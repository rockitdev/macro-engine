"""Tests for every MCP tool in mcp_server.py.

Each tool creates its own SQLite connection via _con(), which reads the
MACRO_ENGINE_DB env var.  We set that var in setUp to a temp path so the
real ~/.local/share/macro-engine/macros.db is never touched.

The @mcp.tool() decorator (FastMCP) returns the original function unchanged,
so every tool is callable directly as a regular Python function.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from macro_engine import db, tracker


def _seed(con):
    """Insert a small food fixture and rebuild FTS."""
    foods = [
        ("Bananas, raw",                         "fdc_sr_legacy", "101", 89,  1.1, 22.8,  0.3, 2.6),
        ("Egg, whole, raw, fresh",               "fdc_sr_legacy", "102", 143, 12.6, 0.7,  9.5, 0.0),
        ("Peanut butter, smooth style",          "fdc_sr_legacy", "103", 598, 22.2, 22.3, 51.1, 5.0),
        ("Bread, whole-wheat, commercially prep","fdc_sr_legacy", "104", 254, 12.3, 43.1,  3.6, 6.0),
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

    con.execute(
        "INSERT INTO portions (food_id, label, grams) VALUES (?, '1 medium', 118)",
        (ids["Bananas"],))
    con.execute(
        "INSERT INTO portions (food_id, label, grams) VALUES (?, '1 large', 50)",
        (ids["Egg"],))
    con.execute(
        "INSERT INTO portions (food_id, label, grams) VALUES (?, '1 tbsp', 16)",
        (ids["Peanut butter"],))
    con.execute(
        "INSERT INTO portions (food_id, label, grams) VALUES (?, '1 slice', 43)",
        (ids["Bread"],))
    db.rebuild_fts(con)
    con.commit()
    return ids


class TestMCPTools(unittest.TestCase):
    """Happy-path and edge-case coverage for every mcp_server tool."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "test.db")
        os.environ["MACRO_ENGINE_DB"] = self.db_path
        # Initialise schema + seed via a direct connection that we close before
        # the tools open their own connections.
        con = db.connect(self.db_path)
        self.ids = _seed(con)
        con.close()
        # Import here so MACRO_ENGINE_DB is set when the module attribute _con
        # is first used (module-level FastMCP init is fine; connection is lazy).
        import mcp_server as _ms
        self.ms = _ms

    def tearDown(self):
        os.environ.pop("MACRO_ENGINE_DB", None)
        self.tmp.cleanup()

    # ------------------------------------------------------------------ #
    #  search_food                                                         #
    # ------------------------------------------------------------------ #

    def test_search_food_happy_path(self):
        results = self.ms.search_food("banana")
        self.assertIsInstance(results, list)
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["name"], "Bananas, raw")

    def test_search_food_returns_macros(self):
        results = self.ms.search_food("egg")
        hit = results[0]
        self.assertIn("kcal", hit)
        self.assertIn("protein_g", hit)

    def test_search_food_no_match_returns_empty(self):
        results = self.ms.search_food("xylophone smoothie extraordinaire")
        self.assertIsInstance(results, list)
        self.assertEqual(len(results), 0)

    def test_search_food_alias_wins(self):
        tracker.add_alias(db.connect(self.db_path), "pb",
                          self.ids["Peanut butter"], 32)
        db.connect(self.db_path).close()          # ensure commit flushed
        results = self.ms.search_food("pb")
        self.assertTrue(results[0]["matched_alias"])

    # ------------------------------------------------------------------ #
    #  log_meal — lookup path                                              #
    # ------------------------------------------------------------------ #

    def test_log_meal_lookup_happy_path(self):
        out = self.ms.log_meal([{"query": "banana", "qty": 1}],
                               date="2026-07-17")
        self.assertEqual(out["problems"], [])
        self.assertEqual(len(out["logged"]), 1)
        self.assertEqual(out["logged"][0]["name"], "Bananas, raw")
        self.assertFalse(out["logged"][0]["estimated"])

    def test_log_meal_with_food_id_skips_search(self):
        fid = self.ids["Bananas"]
        out = self.ms.log_meal([{"food_id": fid, "grams": 100}],
                               date="2026-07-17")
        self.assertEqual(len(out["logged"]), 1)
        self.assertAlmostEqual(out["logged"][0]["kcal"], 89.0, delta=1)

    def test_log_meal_with_unit_portion(self):
        out = self.ms.log_meal(
            [{"query": "peanut butter", "qty": 2, "unit": "tbsp"}],
            date="2026-07-17")
        self.assertEqual(out["problems"], [])
        self.assertAlmostEqual(out["logged"][0]["grams"], 32.0, delta=0.1)

    def test_log_meal_with_mass_unit(self):
        out = self.ms.log_meal(
            [{"query": "bread", "qty": 1, "unit": "oz"}],
            date="2026-07-17")
        self.assertEqual(out["problems"], [])
        self.assertAlmostEqual(out["logged"][0]["grams"], 28.3, delta=0.1)

    def test_log_meal_with_grams_override(self):
        out = self.ms.log_meal(
            [{"query": "banana", "grams": 75}],
            date="2026-07-17")
        self.assertEqual(out["logged"][0]["grams"], 75.0)

    def test_log_meal_with_raw_text(self):
        out = self.ms.log_meal([{"query": "egg", "qty": 2}],
                               date="2026-07-17",
                               raw_text="had two eggs for breakfast")
        self.assertEqual(out["problems"], [])
        self.assertEqual(len(out["logged"]), 1)

    # ------------------------------------------------------------------ #
    #  log_meal — manual estimate path                                     #
    # ------------------------------------------------------------------ #

    def test_log_meal_manual_estimate_flagged(self):
        out = self.ms.log_meal(
            [{"name": "pizza slice",
              "macros": {"kcal": 285, "protein_g": 12, "carb_g": 36, "fat_g": 10}}],
            date="2026-07-17")
        self.assertEqual(len(out["logged"]), 1)
        self.assertTrue(out["logged"][0]["estimated"])
        self.assertEqual(out["logged"][0]["kcal"], 285)

    def test_log_meal_manual_with_fiber(self):
        out = self.ms.log_meal(
            [{"name": "oatmeal",
              "macros": {"kcal": 300, "protein_g": 10, "carb_g": 55,
                         "fat_g": 5, "fiber_g": 8}}],
            date="2026-07-17")
        self.assertEqual(out["logged"][0]["fiber_g"], 8)

    # ------------------------------------------------------------------ #
    #  log_meal — problems / unresolved shapes                             #
    # ------------------------------------------------------------------ #

    def test_log_meal_unresolved_item_shape(self):
        """Unresolved lookup → problems list with item, reason, candidates."""
        out = self.ms.log_meal([{"query": "unobtanium burger"}],
                               date="2026-07-17")
        self.assertEqual(out["logged"], [])
        self.assertEqual(len(out["problems"]), 1)
        prob = out["problems"][0]
        self.assertIn("item", prob)
        self.assertIn("reason", prob)
        self.assertIn("candidates", prob)

    def test_log_meal_bad_unit_returns_available_portions(self):
        """Unrecognised volume unit → problem with available_portions."""
        out = self.ms.log_meal(
            [{"query": "egg", "qty": 2, "unit": "cup"}],
            date="2026-07-17")
        self.assertEqual(out["logged"], [])
        self.assertEqual(len(out["problems"]), 1)
        self.assertIn("available_portions", out["problems"][0])
        self.assertIn("1 large", out["problems"][0]["available_portions"])

    def test_log_meal_mixed_good_and_bad(self):
        """One bad item doesn't drop the good one."""
        out = self.ms.log_meal(
            [{"query": "banana"}, {"query": "xylophone smoothie"}],
            date="2026-07-17")
        self.assertEqual(len(out["logged"]), 1)
        self.assertEqual(len(out["problems"]), 1)

    def test_log_meal_no_query_no_macros(self):
        """Item with neither query nor macros → problem."""
        out = self.ms.log_meal([{}], date="2026-07-17")
        self.assertEqual(out["logged"], [])
        self.assertEqual(len(out["problems"]), 1)

    # ------------------------------------------------------------------ #
    #  remaining                                                           #
    # ------------------------------------------------------------------ #

    def test_remaining_no_targets(self):
        out = self.ms.remaining(date="2026-07-17")
        self.assertIn("eaten", out)
        self.assertIsNone(out["remaining"])
        self.assertIn("note", out)

    def test_remaining_with_targets_and_food(self):
        self.ms.set_targets(2100, 180, 190, 60, effective_date="2026-07-17")
        self.ms.log_meal([{"query": "banana"}], date="2026-07-17")
        out = self.ms.remaining(date="2026-07-17")
        self.assertIsNotNone(out["remaining"])
        self.assertLess(out["remaining"]["kcal"], 2100)

    # ------------------------------------------------------------------ #
    #  day_summary                                                         #
    # ------------------------------------------------------------------ #

    def test_day_summary_empty_day(self):
        out = self.ms.day_summary(date="2026-07-17")
        self.assertIn("entries", out)
        self.assertIsInstance(out["entries"], list)
        self.assertEqual(len(out["entries"]), 0)
        self.assertIn("eaten", out)

    def test_month_summary_returns_the_calendar_month(self):
        out = self.ms.month_summary(date="2026-10-12")
        self.assertEqual(out["start"], "2026-10-01")
        self.assertEqual(out["end"], "2026-10-31")
        self.assertGreaterEqual(len(out["weeks"]), 4)
        self.assertIn("finished", out)
        self.assertIn("through_today", out)

    def test_set_week_start_changes_the_week(self):
        out = self.ms.set_week_start("sunday")
        self.assertEqual(out["week_starts_on"], "sunday")
        week = self.ms.week_summary(date="2026-07-08")
        self.assertEqual(week["start"], "2026-07-05")
        self.assertEqual(week["end"], "2026-07-11")
        self.assertEqual(week["week_starts_on"], "sunday")

    def test_week_summary_returns_the_monday_week(self):
        self.ms.set_targets(2000, 150, 200, 60, effective_date="2026-07-06")
        self.ms.log_meal(
            [{"name": "plate", "macros": {"kcal": 1500, "protein_g": 100,
                                          "carb_g": 150, "fat_g": 40}}],
            date="2026-07-06",
        )
        out = self.ms.week_summary(date="2026-07-08")
        self.assertEqual(out["start"], "2026-07-06")
        self.assertEqual(out["end"], "2026-07-12")
        self.assertEqual(len(out["days"]), 7)
        self.assertEqual(out["days"][0]["eaten"]["carb_g"], 150)
        self.assertIn("room_today", out)
        if out["saved"] is not None:
            self.assertEqual(set(out["saved"]), {"kcal", "carb_g", "fat_g"})

    def test_day_summary_with_logged_food(self):
        self.ms.log_meal([{"query": "banana"}, {"query": "egg"}],
                         date="2026-07-17")
        out = self.ms.day_summary(date="2026-07-17")
        self.assertEqual(len(out["entries"]), 2)
        self.assertGreater(out["eaten"]["kcal"], 0)

    # ------------------------------------------------------------------ #
    #  set_targets                                                         #
    # ------------------------------------------------------------------ #

    def test_set_targets_happy_path(self):
        out = self.ms.set_targets(2200, 185, 200, 65, effective_date="2026-07-17")
        self.assertEqual(out["kcal"], 2200)
        self.assertEqual(out["protein_g"], 185)
        self.assertEqual(out["carb_g"], 200)
        self.assertEqual(out["fat_g"], 65)

    def test_set_targets_with_fiber(self):
        out = self.ms.set_targets(2100, 180, 190, 60, fiber_g=30,
                                  effective_date="2026-07-17")
        self.assertEqual(out["fiber_g"], 30)

    def test_set_targets_without_fiber_is_none(self):
        out = self.ms.set_targets(2100, 180, 190, 60, effective_date="2026-07-17")
        self.assertIsNone(out["fiber_g"])

    # ------------------------------------------------------------------ #
    #  add_food                                                            #
    # ------------------------------------------------------------------ #

    def test_add_food_happy_path(self):
        out = self.ms.add_food(
            "Greek Yogurt 2%", kcal=73, protein_g=10, carb_g=4, fat_g=2,
            brand="Fage", store="Costco",
            portion_label="1 cup", portion_grams=227)
        self.assertIn("food_id", out)
        self.assertEqual(out["name"], "Greek Yogurt 2%")
        self.assertEqual(out["store"], "Costco")

    def test_add_food_searchable_by_store(self):
        self.ms.add_food(
            "Breaded Chicken Burgers", kcal=186, protein_g=12,
            carb_g=15, fat_g=8, store="Costco",
            portion_label="1 burger", portion_grams=113,
            macros_are_per_portion=True)
        results = self.ms.search_food("chicken burgers costco")
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["name"], "Breaded Chicken Burgers")

    def test_add_food_with_alias(self):
        out = self.ms.add_food(
            "Protein Bar", kcal=200, protein_g=20, carb_g=22, fat_g=7,
            alias="my protein bar")
        self.assertIn("alias", out)
        # alias should now resolve
        results = self.ms.search_food("my protein bar")
        self.assertTrue(results[0]["matched_alias"])

    # ------------------------------------------------------------------ #
    #  add_alias                                                           #
    # ------------------------------------------------------------------ #

    def test_add_alias_happy_path(self):
        fid = self.ids["Bread"]
        out = self.ms.add_alias("toast", fid, default_grams=43)
        self.assertEqual(out["phrase"], "toast")
        self.assertIn("food", out)

    def test_add_alias_resolves_in_search(self):
        fid = self.ids["Bananas"]
        self.ms.add_alias("nanner", fid)
        results = self.ms.search_food("nanner")
        self.assertTrue(results[0]["matched_alias"])
        self.assertEqual(results[0]["id"], fid)

    def test_add_alias_default_grams_used_in_log(self):
        fid = self.ids["Bananas"]
        self.ms.add_alias("banana", fid, default_grams=80)
        out = self.ms.log_meal([{"query": "banana"}], date="2026-07-17")
        self.assertEqual(out["logged"][0]["grams"], 80.0)

    # ------------------------------------------------------------------ #
    #  delete_log_entry                                                    #
    # ------------------------------------------------------------------ #

    def test_delete_log_entry_happy_path(self):
        logged = self.ms.log_meal([{"query": "banana"}], date="2026-07-17")
        log_id = logged["logged"][0]["log_id"]
        out = self.ms.delete_log_entry(log_id)
        self.assertTrue(out["deleted"])
        self.assertEqual(out["log_id"], log_id)

    def test_delete_nonexistent_returns_false(self):
        out = self.ms.delete_log_entry(99999)
        self.assertFalse(out["deleted"])

    def test_delete_removes_from_day_summary(self):
        logged = self.ms.log_meal([{"query": "banana"}], date="2026-07-17")
        log_id = logged["logged"][0]["log_id"]
        self.ms.delete_log_entry(log_id)
        summary = self.ms.day_summary(date="2026-07-17")
        self.assertEqual(len(summary["entries"]), 0)
        self.assertEqual(summary["eaten"]["kcal"], 0)

    # ------------------------------------------------------------------ #
    #  Correction flow: delete + re-log + add_alias                       #
    # ------------------------------------------------------------------ #

    def test_correction_flow_delete_relog_alias(self):
        """Full correction: log wrong item → delete → add alias → re-log."""
        # Step 1: log banana (search resolves to Bananas, raw)
        step1 = self.ms.log_meal([{"query": "banana"}], date="2026-07-17")
        log_id = step1["logged"][0]["log_id"]
        food_id = self.ids["Bananas"]

        # Step 2: delete the wrong entry
        deleted = self.ms.delete_log_entry(log_id)
        self.assertTrue(deleted["deleted"])

        # Step 3: teach the resolver — banana → Bananas, raw, 118 g per unit
        alias_out = self.ms.add_alias("banana", food_id, default_grams=118)
        self.assertEqual(alias_out["phrase"], "banana")

        # Step 4: re-log; alias should fire and default grams should apply
        step4 = self.ms.log_meal([{"query": "banana"}], date="2026-07-17")
        self.assertEqual(len(step4["logged"]), 1)
        self.assertTrue(step4["logged"][0]["matched_alias"])
        self.assertEqual(step4["logged"][0]["grams"], 118.0)

        # Day should now show exactly the re-logged entry
        summary = self.ms.day_summary(date="2026-07-17")
        self.assertEqual(len(summary["entries"]), 1)


if __name__ == "__main__":
    unittest.main()
