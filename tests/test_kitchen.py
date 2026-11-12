"""Quick logging: bulk log edits + undo, copy day, usuals, My Kitchen, recipes.

Core functions, the HTTP routes, and the MCP tools, all over temp databases.
"""

import datetime as dt
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from macro_engine import db, http_api, kitchen, resolve, tracker

TODAY = dt.date.today().isoformat()
YESTERDAY = (dt.date.today() - dt.timedelta(days=1)).isoformat()


def seed(con):
    foods = [
        ("Bananas, raw", "1", 89, 1.1, 22.8, 0.3, 2.6),
        ("Egg, whole, raw, fresh", "2", 143, 12.6, 0.7, 9.5, 0),
        ("Peanut butter, smooth style", "3", 598, 22.2, 22.3, 51.1, 5),
        ("Bread, whole-wheat", "4", 254, 12.3, 43.1, 3.6, 6),
    ]
    ids = {}
    for name, sid, kcal, p, c, f, fib in foods:
        fid = con.execute(
            "INSERT INTO foods (name, source, source_id) VALUES (?, 'fdc_sr_legacy', ?) RETURNING id",
            (name, sid)).fetchone()[0]
        con.execute(
            "INSERT INTO food_nutrients (food_id, kcal, protein_g, carb_g, fat_g, fiber_g) "
            "VALUES (?, ?, ?, ?, ?, ?)", (fid, kcal, p, c, f, fib))
        ids[name.split(",")[0]] = fid
    for food, label, grams in [("Bananas", "1 medium", 118), ("Egg", "1 large", 50),
                               ("Peanut butter", "1 tbsp", 16),
                               # the longer label comes first on purpose
                               ("Bread", "1 slice, thick", 60), ("Bread", "1 slice", 43)]:
        con.execute("INSERT INTO portions (food_id, label, grams) VALUES (?, ?, ?)",
                    (ids[food], label, grams))
    db.rebuild_fts(con)
    con.commit()
    return ids


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "test.db")
        self.con = db.connect(self.db_path)
        self.ids = seed(self.con)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def log(self, items, date=TODAY, meal=None):
        out = tracker.log_meal(self.con, items, date, meal=meal)
        self.assertEqual(out["problems"], [])
        return [e["log_id"] for e in out["logged"]]

    def names(self, date=TODAY):
        return [(e["name"].split(",")[0], e["meal"]) for e in tracker.day_log(self.con, date)]


class LogEditsTest(Base):
    def test_exact_portion_label_beats_substring(self):
        (log_id,) = self.log([{"food_id": self.ids["Bread"], "qty": 2, "unit": "1 slice"}])
        entry = tracker._entry(self.con, log_id)
        self.assertEqual((entry["qty_desc"], entry["grams"]), ("2 x 1 slice", 86))
        # a bare word still falls back to the first portion containing it
        (log_id,) = self.log([{"food_id": self.ids["Bread"], "unit": "slice"}])
        self.assertEqual(tracker._entry(self.con, log_id)["qty_desc"], "1 x 1 slice, thick")

    def test_copy_entries_to_another_day_and_meal(self):
        ids = self.log([{"food_id": self.ids["Egg"], "qty": 2, "unit": "large"},
                        {"name": "latte", "macros": {"kcal": 120, "carb_g": 12}}],
                       YESTERDAY, "breakfast")
        copies = tracker.copy_log_entries(self.con, ids + [99999], TODAY, "lunch")
        self.assertEqual(len(copies), 2)  # unknown id skipped
        self.assertEqual(self.names(), [("Egg", "lunch"), ("latte", "lunch")])
        self.assertEqual(copies[0]["kcal"], 143.0)
        self.assertEqual(len(tracker.day_log(self.con, YESTERDAY)), 2)
        # defaults keep the row's own day and meal
        (dup,) = tracker.copy_log_entries(self.con, ids[:1])
        self.assertEqual(dup["meal"], "breakfast")
        self.assertEqual(len(tracker.day_log(self.con, YESTERDAY)), 3)

    def test_copy_day_all_and_by_meal(self):
        self.log([{"food_id": self.ids["Egg"]}], YESTERDAY, "breakfast")
        self.log([{"food_id": self.ids["Bananas"]}], YESTERDAY, "lunch")
        self.log([{"food_id": self.ids["Bread"], "grams": 40}], YESTERDAY)  # no meal
        out = tracker.copy_day(self.con, YESTERDAY, TODAY, ["breakfast", None])
        self.assertEqual(out["date"], TODAY)
        self.assertEqual(self.names(), [("Egg", "breakfast"), ("Bread", None)])
        out = tracker.copy_day(self.con, YESTERDAY, TODAY)
        self.assertEqual(len(out["entries"]), 3)
        self.assertEqual(tracker.copy_day(self.con, "2001-01-01", TODAY)["entries"], [])
        with self.assertRaises(ValueError):
            tracker.copy_day(self.con, TODAY, TODAY)
        with self.assertRaises(ValueError):
            tracker.copy_day(self.con, YESTERDAY, TODAY, ["brunch"])

    def test_move_entries(self):
        ids = self.log([{"food_id": self.ids["Egg"]}, {"food_id": self.ids["Bananas"]}],
                       meal="breakfast")
        self.assertEqual(tracker.move_log_entries(self.con, ids, "snack"), 2)
        self.assertEqual({m for _, m in self.names()}, {"snacks"})
        with self.assertRaises(ValueError):
            tracker.move_log_entries(self.con, [], "lunch")
        with self.assertRaises(ValueError):
            tracker.move_log_entries(self.con, ["1"], "lunch")

    def test_delete_then_restore_keeps_the_row(self):
        ids = self.log([{"food_id": self.ids["Egg"], "qty": 2, "unit": "large"},
                        {"food_id": self.ids["Bananas"]}], meal="breakfast")
        before = tracker.day_log(self.con, TODAY)
        self.assertEqual(tracker.delete_log_entries(self.con, ids + [4242]), ids)
        self.assertEqual(tracker.day_log(self.con, TODAY), [])
        restored = tracker.restore_log_entries(self.con, ids)
        self.assertEqual(restored, before)  # same ids, ts, amounts, meal
        self.assertEqual(tracker.restore_log_entries(self.con, ids), [])  # only once

    def test_restore_when_the_id_was_reused(self):
        (old,) = self.log([{"food_id": self.ids["Egg"]}])
        self.assertTrue(tracker.delete_log_entry(self.con, old))
        (new,) = self.log([{"food_id": self.ids["Bananas"]}])
        self.assertEqual(new, old)  # sqlite hands the freed id out again
        (back,) = tracker.restore_log_entries(self.con, [old])
        self.assertNotEqual(back["log_id"], old)
        self.assertEqual(sorted(n for n, _ in self.names()), ["Bananas", "Egg"])

    def test_old_trash_is_purged(self):
        (a,) = self.log([{"food_id": self.ids["Egg"]}])
        tracker.delete_log_entry(self.con, a)
        self.con.execute("UPDATE log_trash SET deleted_at = datetime('now', 'localtime', '-31 days')")
        (b,) = self.log([{"food_id": self.ids["Bananas"]}])
        tracker.delete_log_entry(self.con, b)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM log_trash").fetchone()[0], 1)

    def test_day_log_marks_kitchen_foods(self):
        self.log([{"food_id": self.ids["Egg"]}, {"name": "latte", "macros": {"kcal": 1}}])
        kitchen.set_kitchen(self.con, self.ids["Egg"])
        self.assertEqual([e["in_kitchen"] for e in tracker.day_log(self.con, TODAY)], [1, 0])


class PicksTest(Base):
    def test_usuals_rank_by_meal_and_carry_last_amount(self):
        self.log([{"food_id": self.ids["Bread"], "qty": 2, "unit": "1 slice"}], YESTERDAY, "breakfast")
        self.log([{"food_id": self.ids["Bread"], "qty": 2, "unit": "1 slice"}], TODAY, "breakfast")
        self.log([{"food_id": self.ids["Bananas"], "grams": 130}], TODAY, "lunch")
        self.log([{"food_id": self.ids["Bananas"]}], TODAY, "snacks")
        self.log([{"food_id": self.ids["Bananas"]}], TODAY, "snacks")
        self.log([{"name": "latte", "macros": {"kcal": 120}}], TODAY, "breakfast")

        picks = kitchen.usual_picks(self.con, "breakfast")
        self.assertEqual([p["name"].split(",")[0] for p in picks], ["Bread", "Bananas"])
        bread = picks[0]
        self.assertEqual(bread["usual"]["item"], {"qty": 2.0, "unit": "1 slice"})
        self.assertEqual(bread["usual"]["desc"], "2 x 1 slice")
        self.assertEqual(bread["usual"]["kcal"], round(254 * 0.86, 1))
        self.assertEqual((bread["times"], bread["at_meal"]), (2, 2))
        self.assertTrue(bread["portions"])

        lunch = kitchen.usual_picks(self.con, "lunch")
        self.assertEqual(lunch[0]["name"], "Bananas, raw")
        # the amount comes from the last time at *this* meal, not the latest log
        self.assertEqual(lunch[0]["usual"]["item"], {"grams": 130.0})
        self.assertEqual(lunch[0]["usual"]["desc"], "130 g")

    def test_usual_item_logs_the_same_amount(self):
        self.log([{"food_id": self.ids["Bread"], "qty": 2, "unit": "1 slice"}], meal="lunch")
        pick = kitchen.usual_picks(self.con, "lunch")[0]
        (log_id,) = self.log([{"food_id": pick["id"], **pick["usual"]["item"]}], meal="lunch")
        entry = tracker._entry(self.con, log_id)
        self.assertEqual((entry["qty_desc"], entry["kcal"]), ("2 x 1 slice", pick["usual"]["kcal"]))

    def test_usuals_window_and_limit(self):
        old = (dt.date.today() - dt.timedelta(days=90)).isoformat()
        self.log([{"food_id": self.ids["Egg"]}], old, "breakfast")
        self.log([{"food_id": self.ids["Bananas"]}], TODAY)
        self.log([{"food_id": self.ids["Bread"]}], TODAY)
        self.assertEqual(len(kitchen.usual_picks(self.con, "breakfast")), 2)
        self.assertEqual(len(kitchen.usual_picks(self.con, "breakfast", limit=1)), 1)
        self.assertEqual(kitchen.usual_picks(self.con, None, "2000-01-01"), [])
        with self.assertRaises(ValueError):
            kitchen.usual_picks(self.con, "brunch")

    def test_kitchen_flag_and_list(self):
        self.assertEqual(kitchen.kitchen_list(self.con), [])
        self.assertIsNone(kitchen.set_kitchen(self.con, 99999))
        with self.assertRaises(ValueError):
            kitchen.set_kitchen(self.con, self.ids["Egg"], "yes")
        self.assertEqual(kitchen.set_kitchen(self.con, self.ids["Egg"])["in_kitchen"], 1)
        kitchen.set_kitchen(self.con, self.ids["Peanut butter"])
        self.log([{"food_id": self.ids["Peanut butter"], "qty": 2, "unit": "tbsp"}])

        foods = kitchen.kitchen_list(self.con)
        self.assertEqual([f["name"].split(",")[0] for f in foods], ["Peanut butter", "Egg"])
        self.assertEqual(foods[0]["usual"]["item"], {"qty": 2.0, "unit": "1 tbsp"})
        self.assertEqual(foods[0]["times"], 1)
        # never logged: its everyday portion
        self.assertEqual(foods[1]["usual"]["item"], {"qty": 1, "unit": "1 large"})
        self.assertEqual(foods[1]["usual"]["kcal"], 71.5)
        self.assertEqual(resolve.search(self.con, "egg")[0]["in_kitchen"], 1)

        kitchen.set_kitchen(self.con, self.ids["Egg"], False)
        self.assertEqual(len(kitchen.kitchen_list(self.con)), 1)

    def test_kitchen_survives_schema_reopen(self):
        kitchen.set_kitchen(self.con, self.ids["Egg"])
        self.con.close()
        self.con = db.connect(self.db_path)
        self.assertEqual(len(kitchen.kitchen_list(self.con)), 1)

    def test_migration_adds_kitchen_column_to_an_old_database(self):
        path = Path(self.tmp.name) / "old.db"
        import sqlite3
        raw = sqlite3.connect(path)
        raw.execute("CREATE TABLE foods (id INTEGER PRIMARY KEY, name TEXT NOT NULL, brand TEXT, "
                    "store TEXT, source TEXT NOT NULL, source_id TEXT, "
                    "verified INTEGER NOT NULL DEFAULT 1, UNIQUE(source, source_id))")
        raw.execute("INSERT INTO foods (name, source) VALUES ('Old food', 'manual')")
        raw.commit()
        raw.close()
        con = db.connect(path)
        self.assertEqual(con.execute("SELECT in_kitchen FROM foods").fetchone()[0], 0)
        con.close()


class RecipeTest(Base):
    def chili(self, **kw):
        return kitchen.create_recipe(self.con, "Weeknight chili", 4, items=[
            {"food_id": self.ids["Egg"], "grams": 400},        # 572 kcal
            {"food_id": self.ids["Bananas"], "grams": 600},    # 534 kcal
        ], **kw)

    def test_create_from_items_and_log_by_serving(self):
        rec = self.chili()
        self.assertTrue(rec["saved"])
        self.assertEqual(rec["servings"], 4)
        self.assertEqual(rec["total"]["kcal"], 1106.0)
        self.assertEqual(rec["per_serving"]["kcal"], 276.5)
        self.assertEqual(rec["serving_grams"], 250.0)
        self.assertTrue(rec["weighed"])
        self.assertEqual(rec["food"]["source"], "recipe")
        self.assertEqual([i["name"].split(",")[0] for i in rec["items"]], ["Egg", "Bananas"])
        self.assertEqual(tracker.day_log(self.con, TODAY), [])  # saving logs nothing

        (log_id,) = self.log([{"food_id": rec["food_id"], "qty": 1.5, "unit": "1 serving"}])
        entry = tracker._entry(self.con, log_id)
        self.assertEqual(entry["qty_desc"], "1.5 x 1 serving")
        self.assertAlmostEqual(entry["kcal"], 276.5 * 1.5, delta=0.2)
        self.assertEqual(resolve.search(self.con, "chili")[0]["id"], rec["food_id"])
        # and by plain text, defaulting to one serving
        out = tracker.log_meal(self.con, [{"query": "weeknight chili"}])
        self.assertAlmostEqual(out["logged"][0]["kcal"], 276.5, delta=0.1)

    def test_create_from_log_entries_including_unweighed_estimate(self):
        ids = self.log([{"food_id": self.ids["Bread"], "qty": 2, "unit": "1 slice"},
                        {"name": "sauce", "macros": {"kcal": 100, "fat_g": 10}}], meal="dinner")
        rec = kitchen.create_recipe(self.con, "Toast thing", 2, log_ids=ids)
        self.assertFalse(rec["weighed"])
        self.assertEqual(rec["serving_grams"], 100.0)  # nominal
        self.assertEqual([(i["name"], i["qty_desc"]) for i in rec["items"]],
                         [("Bread, whole-wheat", "2 x 1 slice"), ("sauce", None)])
        self.assertEqual(rec["per_serving"]["kcal"], round((254 * 0.86 + 100) / 2, 1))
        (log_id,) = self.log([{"food_id": rec["food_id"]}])
        self.assertAlmostEqual(tracker._entry(self.con, log_id)["kcal"],
                               rec["per_serving"]["kcal"], delta=0.1)
        self.assertEqual(len(tracker.day_log(self.con, TODAY)), 3)  # source rows untouched

    def test_unresolvable_item_saves_nothing(self):
        out = kitchen.create_recipe(self.con, "Mystery", 1, items=[
            {"food_id": self.ids["Egg"]}, {"query": "zzzxqv"},
            {"food_id": self.ids["Egg"], "unit": "bucket"}])
        self.assertFalse(out["saved"])
        self.assertEqual(len(out["problems"]), 2)
        self.assertIn("available_portions", out["problems"][1])
        self.assertEqual(kitchen.list_recipes(self.con), [])
        self.assertEqual(resolve.search(self.con, "mystery"), [])

    def test_validation(self):
        with self.assertRaises(ValueError):
            kitchen.create_recipe(self.con, "Empty", 1)
        with self.assertRaises(ValueError):
            kitchen.create_recipe(self.con, "", 1, items=[{"food_id": self.ids["Egg"]}])
        with self.assertRaises(ValueError):
            kitchen.create_recipe(self.con, "Zero", 0, items=[{"food_id": self.ids["Egg"]}])
        with self.assertRaises(ValueError):
            kitchen.create_recipe(self.con, "Ghost", 1, log_ids=[12345])

    def test_update_keeps_logged_history_and_food_id(self):
        rec = self.chili()
        (log_id,) = self.log([{"food_id": rec["food_id"], "unit": "1 serving"}])
        logged = tracker._entry(self.con, log_id)["kcal"]

        out = kitchen.update_recipe(self.con, rec["food_id"], name="Sunday chili", servings=2,
                                    remove_item_ids=[rec["items"][1]["id"]],
                                    items=[{"food_id": self.ids["Peanut butter"], "grams": 100}])
        self.assertTrue(out["saved"])
        self.assertEqual(out["food_id"], rec["food_id"])
        self.assertEqual(out["total"]["kcal"], 572 + 598)
        self.assertEqual(out["per_serving"]["kcal"], 585.0)
        self.assertEqual(out["serving_grams"], 250.0)
        self.assertEqual(len(out["food"]["portions"]), 1)
        entry = tracker._entry(self.con, log_id)
        self.assertEqual((entry["kcal"], entry["name"]), (logged, "Sunday chili"))
        self.assertEqual(resolve.search(self.con, "weeknight"), [])
        self.assertEqual(resolve.search(self.con, "sunday")[0]["id"], rec["food_id"])

        with self.assertRaises(ValueError):  # can't remove every ingredient
            kitchen.update_recipe(self.con, rec["food_id"],
                                  remove_item_ids=[i["id"] for i in out["items"]])
        bad = kitchen.update_recipe(self.con, rec["food_id"], name="Nope",
                                    items=[{"query": "zzzxqv"}])
        self.assertFalse(bad["saved"])
        self.assertEqual(kitchen.get_recipe(self.con, rec["food_id"])["name"], "Sunday chili")
        self.assertIsNone(kitchen.update_recipe(self.con, 99999, name="x"))

    def test_delete_hides_the_recipe_but_keeps_history(self):
        rec = self.chili()
        kitchen.set_kitchen(self.con, rec["food_id"])
        tracker.add_alias(self.con, "the chili", rec["food_id"])
        self.log([{"food_id": rec["food_id"]}], meal="dinner")

        self.assertTrue(kitchen.delete_recipe(self.con, rec["food_id"]))
        self.assertFalse(kitchen.delete_recipe(self.con, rec["food_id"]))
        self.assertEqual(kitchen.list_recipes(self.con), [])
        self.assertIsNone(kitchen.get_recipe(self.con, rec["food_id"]))
        self.assertEqual(resolve.search(self.con, "chili"), [])
        self.assertEqual(resolve.search(self.con, "the chili"), [])
        self.assertEqual(kitchen.kitchen_list(self.con), [])
        self.assertEqual(kitchen.usual_picks(self.con, "dinner"), [])
        self.assertEqual(self.names(), [("Weeknight chili", "dinner")])
        db.rebuild_fts(self.con)  # an ETL re-run must not bring it back
        self.assertEqual(resolve.search(self.con, "chili"), [])
        # the name is free for a new recipe
        self.assertTrue(self.chili()["saved"])
        self.assertEqual(len(resolve.search(self.con, "chili")), 1)


class HttpTest(unittest.TestCase):
    def setUp(self):
        os.environ["MACRO_ENGINE_HTTP_QUIET"] = "1"
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MACRO_ENGINE_DB"] = str(Path(self.tmp.name) / "test.db")
        con = db.connect()
        self.ids = seed(con)
        con.close()
        self.httpd = http_api.make_server("127.0.0.1", 0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def req(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request) as res:
                return res.status, json.loads(res.read() or b"null")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"null")

    def log(self, items, date=TODAY, meal="breakfast"):
        status, out = self.req("POST", "/api/meals", {"items": items, "date": date, "meal": meal})
        self.assertEqual((status, out["problems"]), (200, []))
        return [e["log_id"] for e in out["logged"]]

    def day(self, date=TODAY):
        return self.req("GET", f"/api/day?date={date}")[1]["entries"]

    def test_batch_move_duplicate_delete_restore(self):
        ids = self.log([{"food_id": self.ids["Egg"]}, {"food_id": self.ids["Bananas"]}])
        status, out = self.req("POST", "/api/log/batch", {"ids": ids, "action": "move", "meal": "lunch"})
        self.assertEqual((status, out), (200, {"moved": 2}))
        status, out = self.req("POST", "/api/log/batch", {"ids": ids, "action": "duplicate"})
        self.assertEqual([e["meal"] for e in out["entries"]], ["lunch", "lunch"])
        status, out = self.req("POST", "/api/log/batch", {"ids": ids, "action": "delete"})
        self.assertEqual(out, {"deleted": ids})
        self.assertEqual(len(self.day()), 2)
        status, out = self.req("POST", "/api/log/batch", {"ids": ids, "action": "restore"})
        self.assertEqual([e["log_id"] for e in out["entries"]], ids)
        self.assertEqual(len(self.day()), 4)

        for body in ({"ids": ids, "action": "explode"}, {"ids": ids, "action": "move"},
                     {"ids": [], "action": "delete"}, {"ids": ["a"], "action": "delete"},
                     {"action": "delete"}):
            self.assertEqual(self.req("POST", "/api/log/batch", body)[0], 400, body)

    def test_single_delete_can_be_restored(self):
        (log_id,) = self.log([{"food_id": self.ids["Egg"]}])
        self.assertEqual(self.req("DELETE", f"/api/log/{log_id}")[0], 200)
        status, out = self.req("POST", "/api/log/batch", {"ids": [log_id], "action": "restore"})
        self.assertEqual(out["entries"][0]["log_id"], log_id)

    def test_copy_day(self):
        self.log([{"food_id": self.ids["Egg"]}], YESTERDAY, "breakfast")
        self.log([{"food_id": self.ids["Bananas"]}], YESTERDAY, "dinner")
        status, out = self.req("POST", "/api/log/copy",
                               {"from_date": YESTERDAY, "to_date": TODAY, "meals": ["dinner"]})
        self.assertEqual(status, 200)
        self.assertEqual([(e["name"], e["meal"]) for e in self.day()], [("Bananas, raw", "dinner")])
        self.assertEqual(self.req("POST", "/api/log/copy", {"from_date": TODAY, "to_date": TODAY})[0], 400)
        self.assertEqual(self.req("POST", "/api/log/copy", {"to_date": TODAY})[0], 400)

    def test_picks_and_kitchen(self):
        self.log([{"food_id": self.ids["Egg"], "qty": 2, "unit": "1 large"}])
        status, out = self.req("GET", "/api/picks?meal=breakfast")
        self.assertEqual(status, 200)
        self.assertEqual(out["usual"][0]["usual"]["item"], {"qty": 2.0, "unit": "1 large"})
        self.assertEqual(self.req("GET", "/api/picks?meal=brunch")[0], 400)

        egg = self.ids["Egg"]
        status, food = self.req("POST", f"/api/foods/{egg}/kitchen", {"in_kitchen": True})
        self.assertEqual((status, food["in_kitchen"]), (200, 1))
        self.assertEqual(self.req("GET", f"/api/foods/{egg}")[1]["in_kitchen"], 1)
        self.assertEqual(self.day()[0]["in_kitchen"], 1)
        self.assertEqual([f["id"] for f in self.req("GET", "/api/kitchen")[1]["foods"]], [egg])
        self.assertEqual(self.req("POST", "/api/foods/99999/kitchen", {"in_kitchen": True})[0], 404)
        self.assertEqual(self.req("POST", f"/api/foods/{egg}/kitchen", {"in_kitchen": "y"})[0], 400)
        self.assertEqual(self.req("POST", f"/api/foods/{egg}/kitchen", {})[0], 400)
        self.req("POST", f"/api/foods/{egg}/kitchen", {"in_kitchen": False})
        self.assertEqual(self.req("GET", "/api/kitchen")[1]["foods"], [])

    def test_recipe_lifecycle(self):
        ids = self.log([{"food_id": self.ids["Egg"], "grams": 200},
                        {"food_id": self.ids["Bananas"], "grams": 200}], meal="dinner")
        status, rec = self.req("POST", "/api/recipes", {"name": "Scramble", "servings": 2, "log_ids": ids})
        self.assertEqual((status, rec["saved"]), (200, True))
        rid = rec["food_id"]
        self.assertEqual(rec["per_serving"]["kcal"], 232.0)

        status, out = self.req("GET", "/api/recipes")
        self.assertEqual([r["name"] for r in out["recipes"]], ["Scramble"])
        status, out = self.req("POST", f"/api/recipes/{rid}",
                               {"servings": 4, "remove_item_ids": [rec["items"][1]["id"]]})
        self.assertEqual(out["per_serving"]["kcal"], 71.5)
        self.assertEqual(self.req("GET", f"/api/recipes/{rid}")[1]["servings"], 4)

        (log_id,) = self.log([{"food_id": rid, "qty": 1, "unit": "1 serving"}], meal="lunch")
        self.assertAlmostEqual(self.day()[-1]["kcal"], 71.5, delta=0.1)

        self.assertEqual(self.req("DELETE", f"/api/recipes/{rid}"), (200, {"deleted": True}))
        self.assertEqual(self.req("DELETE", f"/api/recipes/{rid}")[0], 404)
        self.assertEqual(self.req("GET", f"/api/recipes/{rid}")[0], 404)
        self.assertEqual(self.req("POST", f"/api/recipes/{rid}", {"name": "x"})[0], 404)
        self.assertEqual(self.day()[-1]["name"], "Scramble")

        self.assertEqual(self.req("POST", "/api/recipes", {"name": "Nothing"})[0], 400)
        self.assertEqual(self.req("POST", "/api/recipes", {"servings": 2, "log_ids": ids})[0], 400)
        self.assertEqual(self.req("POST", "/api/recipes", {"name": "Bad", "log_ids": "1"})[0], 400)
        status, out = self.req("POST", "/api/recipes", {"name": "Bad", "items": [{"query": "zzzxqv"}]})
        self.assertEqual((status, out["saved"]), (200, False))


class McpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MACRO_ENGINE_DB"] = str(Path(self.tmp.name) / "test.db")
        con = db.connect()
        self.ids = seed(con)
        con.close()
        import mcp_server
        self.ms = mcp_server

    def tearDown(self):
        self.tmp.cleanup()

    def entries(self, date=None):
        return self.ms.day_summary(date)["entries"]

    def test_update_duplicate_restore(self):
        out = self.ms.log_meal([{"food_id": self.ids["Bread"], "qty": 2, "unit": "1 slice"}])
        log_id = out["logged"][0]["log_id"]

        entry = self.ms.update_log_entry(log_id, scale=1.5, meal="snack")
        self.assertEqual((entry["qty_desc"], entry["meal"]), ("3 x 1 slice", "snacks"))
        self.assertEqual(self.ms.update_log_entry(log_id, grams=43)["qty_desc"], "1 x 1 slice")
        self.assertEqual(self.ms.update_log_entry(log_id, meal="lunch")["meal"], "lunch")
        for bad in ({}, {"grams": 10, "scale": 2}, {"meal": "brunch", "scale": 2}):
            with self.assertRaises(ValueError):
                self.ms.update_log_entry(log_id, **bad)
        self.assertEqual(self.entries()[0]["qty_desc"], "1 x 1 slice")  # bad meal changed nothing
        with self.assertRaises(ValueError):
            self.ms.update_log_entry(99999, scale=2)

        copy = self.ms.duplicate_log_entry(log_id)
        self.assertEqual((copy["meal"], copy["kcal"]), ("lunch", entry["kcal"] / 3))
        other = self.ms.duplicate_log_entry(log_id, date=YESTERDAY, meal="dinner")
        self.assertEqual(self.entries(YESTERDAY)[0]["log_id"], other["log_id"])
        with self.assertRaises(ValueError):
            self.ms.duplicate_log_entry(99999)

        self.assertTrue(self.ms.delete_log_entry(log_id)["deleted"])
        back = self.ms.restore_log_entry(log_id)
        self.assertEqual((back["restored"], back["entry"]["log_id"]), (True, log_id))
        self.assertEqual(self.ms.restore_log_entry(log_id), {"restored": False, "entry": None})

    def test_copy_day(self):
        self.ms.log_meal([{"food_id": self.ids["Egg"], "meal": "breakfast"},
                          {"food_id": self.ids["Bananas"], "meal": "lunch"}], date=YESTERDAY)
        out = self.ms.copy_day(YESTERDAY, meals=["breakfast"])
        self.assertEqual((out["date"], len(out["entries"])), (TODAY, 1))
        self.assertEqual(len(self.ms.copy_day(YESTERDAY)["entries"]), 2)
        self.assertEqual(len(self.entries()), 3)

    def test_kitchen_and_recipes(self):
        self.assertEqual(self.ms.set_kitchen(self.ids["Egg"])["in_kitchen"], 1)
        self.assertEqual([f["id"] for f in self.ms.list_kitchen()], [self.ids["Egg"]])
        self.ms.set_kitchen(self.ids["Egg"], False)
        self.assertEqual(self.ms.list_kitchen(), [])
        with self.assertRaises(ValueError):
            self.ms.set_kitchen(99999)

        rec = self.ms.save_recipe("PB toast", 2, items=[
            {"query": "bread", "qty": 2, "unit": "1 slice"},
            {"query": "peanut butter", "qty": 2, "unit": "tbsp"}])
        self.assertTrue(rec["saved"])
        self.assertEqual(self.entries(), [])
        out = self.ms.log_meal([{"food_id": rec["food_id"], "qty": 1, "unit": "1 serving"}])
        self.assertAlmostEqual(out["logged"][0]["kcal"], rec["per_serving"]["kcal"], delta=0.1)
        self.assertEqual([r["name"] for r in self.ms.list_recipes()], ["PB toast"])
        self.assertEqual(self.ms.update_recipe(rec["food_id"], servings=1)["servings"], 1)
        with self.assertRaises(ValueError):
            self.ms.update_recipe(99999, name="x")
        self.assertFalse(self.ms.save_recipe("Bad", 1, items=[{"query": "zzzxqv"}])["saved"])
        self.assertTrue(self.ms.delete_recipe(rec["food_id"])["deleted"])
        self.assertFalse(self.ms.delete_recipe(rec["food_id"])["deleted"])
        self.assertEqual(self.ms.list_recipes(), [])


if __name__ == "__main__":
    unittest.main()
