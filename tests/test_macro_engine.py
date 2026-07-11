import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from macro_engine import db, resolve, tracker


def seed(con):
    foods = [
        # name, source, source_id, kcal, p, c, f, fiber
        ("Bananas, raw", "fdc_sr_legacy", "1", 89, 1.1, 22.8, 0.3, 2.6),
        ("Egg, whole, raw, fresh", "fdc_sr_legacy", "2", 143, 12.6, 0.7, 9.5, 0),
        ("Peanut butter, smooth style", "fdc_sr_legacy", "3", 598, 22.2, 22.3, 51.1, 5),
        ("Bread, whole-wheat, commercially prepared", "fdc_sr_legacy", "4",
         254, 12.3, 43.1, 3.6, 6),
        ("Banana bread, prepared from recipe", "fdc_survey", "5", 326, 4.3, 54.6, 10.5, 1.1),
    ]
    ids = {}
    for name, source, sid, kcal, p, c, f, fib in foods:
        fid = con.execute(
            "INSERT INTO foods (name, source, source_id) VALUES (?, ?, ?) RETURNING id",
            (name, source, sid),
        ).fetchone()[0]
        con.execute(
            "INSERT INTO food_nutrients (food_id, kcal, protein_g, carb_g, fat_g, fiber_g) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (fid, kcal, p, c, f, fib),
        )
        ids[name.split(",")[0]] = fid
    con.execute("INSERT INTO portions (food_id, label, grams) VALUES (?, '1 large', 50)",
                (ids["Egg"],))
    con.execute("INSERT INTO portions (food_id, label, grams) VALUES (?, '1 tbsp', 16)",
                (ids["Peanut butter"],))
    con.execute("INSERT INTO portions (food_id, label, grams) VALUES (?, '1 slice', 43)",
                (ids["Bread"],))
    con.execute("INSERT INTO portions (food_id, label, grams) VALUES (?, '1 medium', 118)",
                (ids["Bananas"],))
    db.rebuild_fts(con)
    con.commit()
    return ids


class MacroEngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = db.connect(Path(self.tmp.name) / "test.db")
        self.ids = seed(self.con)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_search_ranks_plain_food_first(self):
        results = resolve.search(self.con, "banana")
        self.assertEqual(results[0]["name"], "Bananas, raw")

    def test_alias_wins_over_fts(self):
        tracker.add_alias(self.con, "toast", self.ids["Bread"], default_grams=43)
        results = resolve.search(self.con, "toast")
        self.assertTrue(results[0]["matched_alias"])
        self.assertEqual(results[0]["id"], self.ids["Bread"])

    def test_log_meal_and_remaining(self):
        tracker.set_targets(self.con, 2100, 180, 190, 60, effective_date="2026-07-08")
        out = tracker.log_meal(
            self.con,
            [{"query": "egg", "qty": 2},
             {"query": "peanut butter", "qty": 2, "unit": "tbsp"},
             {"query": "banana", "qty": 1}],
            date="2026-07-08",
        )
        self.assertEqual(out["problems"], [])
        self.assertEqual(len(out["logged"]), 3)
        # 2 eggs = 100g -> 143 kcal; 2 tbsp pb = 32g -> 191.4; banana 118g -> 105
        self.assertAlmostEqual(out["eaten"]["kcal"], 143 + 191.4 + 105.0, delta=0.5)
        # 2 eggs = 100 g -> 12.6 g protein; 32 g pb -> 7.1; 118 g banana -> 1.3
        self.assertAlmostEqual(out["remaining"]["protein_g"],
                               180 - (12.6 + 7.1 + 1.3), delta=0.3)

    def test_unit_synonym_tbsp(self):
        out = tracker.log_meal(self.con, [{"query": "peanut butter", "qty": 2,
                                           "unit": "tbsp"}], date="2026-07-08")
        self.assertEqual(out["logged"][0]["grams"], 32.0)  # 2 x '1 tbsp' 16 g

    def test_default_portion_prefers_medium(self):
        self.con.execute(
            "INSERT INTO portions (food_id, label, grams) VALUES (?, '1 cup, mashed', 225)",
            (self.ids["Bananas"],))
        # '1 medium' (118 g) must win over the cup portion regardless of order
        out = tracker.log_meal(self.con, [{"query": "banana"}], date="2026-07-08")
        self.assertEqual(out["logged"][0]["grams"], 118.0)

    def test_manual_estimate_flagged(self):
        out = tracker.log_meal(
            self.con,
            [{"name": "shawarma plate",
              "macros": {"kcal": 900, "protein_g": 45, "carb_g": 80, "fat_g": 40}}],
            date="2026-07-08",
        )
        self.assertTrue(out["logged"][0]["estimated"])
        self.assertEqual(out["eaten"]["kcal"], 900)

    def test_unresolvable_reported_not_dropped(self):
        out = tracker.log_meal(self.con, [{"query": "xylophone smoothie"}],
                               date="2026-07-08")
        self.assertEqual(len(out["problems"]), 1)
        self.assertEqual(out["eaten"]["entries"], 0)

    def test_targets_latest_wins(self):
        tracker.set_targets(self.con, 2100, 180, 190, 60, effective_date="2026-07-01")
        tracker.set_targets(self.con, 1900, 185, 160, 55, effective_date="2026-07-08")
        self.assertEqual(tracker.get_targets(self.con, "2026-07-05")["kcal"], 2100)
        self.assertEqual(tracker.get_targets(self.con, "2026-07-08")["kcal"], 1900)
        self.assertIsNone(tracker.get_targets(self.con, "2026-06-01"))

    def test_no_targets_note(self):
        out = tracker.remaining(self.con, "2026-07-08")
        self.assertIsNone(out["remaining"])
        self.assertIn("set_targets", out["note"])

    def test_delete_and_day_log(self):
        out = tracker.log_meal(self.con, [{"query": "banana"}], date="2026-07-08")
        log_id = out["logged"][0]["log_id"]
        self.assertEqual(len(tracker.day_log(self.con, "2026-07-08")), 1)
        self.assertTrue(tracker.delete_log_entry(self.con, log_id))
        self.assertEqual(tracker.day_totals(self.con, "2026-07-08")["kcal"], 0)

    def test_add_food_store_searchable(self):
        out = tracker.add_food(
            self.con, "Breaded Chicken Burgers", kcal=210, protein_g=14,
            carb_g=17, fat_g=9, brand="Janes", store="Costco",
            portion_label="1 burger", portion_grams=113,
            macros_are_per_portion=True,
            alias="breaded chicken burgers from costco")
        # store name matches even though it's not in the food name
        hit = resolve.search(self.con, "chicken burgers costco")[0]
        self.assertEqual(hit["id"], out["food_id"])
        # alias resolves and logs one burger by default
        logged = tracker.log_meal(
            self.con, [{"query": "breaded chicken burgers from costco"}],
            date="2026-07-08")
        self.assertEqual(logged["logged"][0]["grams"], 113.0)
        self.assertAlmostEqual(logged["logged"][0]["kcal"], 210, delta=1)

    # --- Fix #1: unit resolution correctness ---
    def test_mass_unit_converts_directly(self):
        # 2 oz of bread (portion is '1 slice' 43 g) -> 2 * 28.3495 g, not 2 slices
        out = tracker.log_meal(self.con, [{"query": "bread", "qty": 2, "unit": "oz"}],
                               date="2026-07-08")
        self.assertEqual(out["problems"], [])
        self.assertAlmostEqual(out["logged"][0]["grams"], 56.7, delta=0.1)

    def test_grams_unit_converts_directly(self):
        out = tracker.log_meal(self.con, [{"query": "banana", "qty": 150, "unit": "g"}],
                               date="2026-07-08")
        self.assertEqual(out["logged"][0]["grams"], 150.0)

    def test_unknown_unit_becomes_problem_not_silent_fallback(self):
        # egg's only portion is '1 large'; 'ounce' is a mass unit so it converts,
        # but 'cup' (volume, no matching portion) must be flagged, not defaulted.
        out = tracker.log_meal(self.con, [{"query": "egg", "qty": 2, "unit": "cup"}],
                               date="2026-07-08")
        self.assertEqual(out["logged"], [])
        self.assertEqual(len(out["problems"]), 1)
        self.assertIn("cup", out["problems"][0]["reason"])
        self.assertIn("1 large", out["problems"][0]["available_portions"])
        self.assertEqual(tracker.day_totals(self.con, "2026-07-08")["entries"], 0)

    # --- Fix #2: unset fibre target is not treated as zero ---
    def test_unset_fibre_target_reports_none_remaining(self):
        tracker.set_targets(self.con, 2100, 180, 190, 60, effective_date="2026-07-08")
        tracker.log_meal(self.con, [{"query": "peanut butter", "qty": 2, "unit": "tbsp"}],
                         date="2026-07-08")
        out = tracker.remaining(self.con, "2026-07-08")
        self.assertIsNone(out["remaining"]["fiber_g"])
        self.assertIsNotNone(out["remaining"]["protein_g"])

    def test_set_fibre_target_reports_deficit(self):
        tracker.set_targets(self.con, 2100, 180, 190, 60, fiber_g=30,
                            effective_date="2026-07-08")
        out = tracker.remaining(self.con, "2026-07-08")
        self.assertEqual(out["remaining"]["fiber_g"], 30)

    # --- Fix #3: boundary validation on write paths ---
    def test_negative_qty_flagged(self):
        out = tracker.log_meal(self.con, [{"query": "banana", "qty": -1}],
                               date="2026-07-08")
        self.assertEqual(out["logged"], [])
        self.assertEqual(len(out["problems"]), 1)

    def test_nan_estimate_flagged(self):
        out = tracker.log_meal(
            self.con,
            [{"name": "bad", "macros": {"kcal": float("nan"), "protein_g": 1,
                                        "carb_g": 1, "fat_g": 1}}],
            date="2026-07-08")
        self.assertEqual(out["logged"], [])
        self.assertEqual(len(out["problems"]), 1)

    def test_negative_estimate_macro_flagged(self):
        out = tracker.log_meal(
            self.con,
            [{"name": "bad", "macros": {"kcal": -100, "protein_g": 1,
                                        "carb_g": 1, "fat_g": 1}}],
            date="2026-07-08")
        self.assertEqual(len(out["problems"]), 1)

    def test_one_bad_item_does_not_sink_the_meal(self):
        out = tracker.log_meal(
            self.con,
            [{"query": "banana"}, {"query": "egg", "qty": -2}],
            date="2026-07-08")
        self.assertEqual(len(out["logged"]), 1)
        self.assertEqual(len(out["problems"]), 1)

    def test_malformed_date_rejected(self):
        with self.assertRaises(ValueError):
            tracker.log_meal(self.con, [{"query": "banana"}], date="July 8")

    def test_set_targets_rejects_nonpositive_kcal(self):
        with self.assertRaises(ValueError):
            tracker.set_targets(self.con, 0, 180, 190, 60, effective_date="2026-07-08")

    def test_add_food_rejects_empty_name(self):
        with self.assertRaises(ValueError):
            tracker.add_food(self.con, "  ", kcal=10, protein_g=1, carb_g=1, fat_g=1)

    def test_add_food_rejects_negative_portion(self):
        with self.assertRaises(ValueError):
            tracker.add_food(self.con, "Weird", kcal=10, protein_g=1, carb_g=1,
                             fat_g=1, portion_grams=-5, portion_label="1 blob")

    # --- Fix #4: fibre appears in per-entry day_log ---
    def test_day_log_includes_fibre(self):
        tracker.log_meal(self.con, [{"query": "peanut butter", "qty": 2, "unit": "tbsp"}],
                         date="2026-07-08")
        entries = tracker.day_log(self.con, "2026-07-08")
        self.assertIn("fiber_g", entries[0])
        # 32 g PB at 5 g fibre/100 g -> 1.6 g
        self.assertAlmostEqual(entries[0]["fiber_g"], 1.6, delta=0.1)

    # --- Follow-up: plural mass units + alias-grams validation ---
    def test_plural_mass_unit_converts(self):
        out = tracker.log_meal(self.con, [{"query": "bread", "qty": 2, "unit": "ounces"}],
                               date="2026-07-08")
        self.assertEqual(out["problems"], [])
        self.assertAlmostEqual(out["logged"][0]["grams"], 56.7, delta=0.1)

    def test_add_alias_rejects_negative_default_grams(self):
        with self.assertRaises(ValueError):
            tracker.add_alias(self.con, "toast", self.ids["Bread"], default_grams=-43)

    def test_bad_stored_alias_grams_becomes_problem(self):
        # Simulate an old/hand-edited row that predates write validation.
        self.con.execute(
            "INSERT INTO aliases (phrase, food_id, default_grams) VALUES ('shake', ?, -100)",
            (self.ids["Peanut butter"],))
        self.con.commit()
        out = tracker.log_meal(self.con, [{"query": "shake"}], date="2026-07-08")
        self.assertEqual(out["logged"], [])
        self.assertEqual(len(out["problems"]), 1)

    def test_etl_reruns_keep_food_ids_stable(self):
        fid = self.con.execute(
            """INSERT INTO foods (name, source, source_id) VALUES ('Bananas, raw v2',
               'fdc_sr_legacy', '1')
               ON CONFLICT(source, source_id) DO UPDATE SET name = excluded.name
               RETURNING id""").fetchone()[0]
        self.assertEqual(fid, self.ids["Bananas"])


if __name__ == "__main__":
    unittest.main()
