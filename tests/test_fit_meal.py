"""Tests for macro_engine.fit_meal — combinatorial restaurant order solver.

Key correctness invariants:
1. ALL macros stored in food_nutrients are PER 100 g. The solver MUST scale by
   (portion_grams / 100) before comparing to the budget.
2. The solver targets a PER-MEAL slice (remaining / meals_left) — not the full
   day's budget.  Orders must not exceed the full remaining (hard ceiling).
3. Treats (desserts, shakes) are excluded by default; include_treats=True brings
   them back.
4. Ranking is by proximity to the per-meal target (protein-forward tiebreak).
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from macro_engine import db, fit_meal as fm_mod, tracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_chain(con, *, chain: str = "FakeBurger") -> dict[str, int]:
    """Insert a minimal branded food set with per-100g nutrients + portions.

    Big Mac proxy: 257 kcal/100g, 219g portion → 562 kcal actual
    Small Fries:   300 kcal/100g, 117g portion → 351 kcal actual
    Diet Cola:       0 kcal/100g, 500g portion →   0 kcal actual
    Side Salad:     15 kcal/100g, 150g portion →  23 kcal actual

    Item names intentionally do NOT contain the chain name to avoid false
    substring matches in test assertions.
    """
    foods = [
        # (name,        kcal/100g, prot/100g, carb/100g, fat/100g, portion_label, grams)
        ("Classic Patty",   257.0, 12.6, 24.0, 12.0, "1 burger",  219.0),
        ("Crispy Fries",    300.0,  3.5, 38.0, 15.0, "small",     117.0),
        ("Zero Cola",         0.0,  0.0,  0.1,  0.0, "medium cup", 500.0),
        ("Garden Salad",     15.0,  1.2,  2.5,  0.5, "side",      150.0),
        # A high-calorie item to test over_budget detection
        ("Double Stack",    380.0, 22.0, 26.0, 25.0, "1 burger",  310.0),
    ]
    ids: dict[str, int] = {}
    for name, kcal, prot, carb, fat, plabel, pgrams in foods:
        fid = con.execute(
            "INSERT INTO foods (name, brand, source) VALUES (?, ?, 'manual') RETURNING id",
            (name, chain),
        ).fetchone()[0]
        con.execute(
            "INSERT INTO food_nutrients (food_id, kcal, protein_g, carb_g, fat_g) "
            "VALUES (?, ?, ?, ?, ?)",
            (fid, kcal, prot, carb, fat),
        )
        con.execute(
            "INSERT INTO portions (food_id, label, grams) VALUES (?, ?, ?)",
            (fid, plabel, pgrams),
        )
        ids[name] = fid
    db.rebuild_fts(con)
    con.commit()
    return ids


def _seed_treat_chain(con, *, chain: str = "SweetSpot") -> dict[str, int]:
    """Insert a mix of regular and treat items."""
    foods = [
        # (name,        kcal/100g, prot/100g, carb/100g, fat/100g, portion_label, grams)
        ("Grilled Chicken",  150.0, 30.0,  2.0,  3.0, "1 piece",  180.0),
        ("Caesar Wrap",      200.0, 18.0, 20.0,  8.0, "1 wrap",   220.0),
        # Treat items — must match _TREAT_KEYWORDS
        ("Chocolate Milkshake", 130.0, 4.0, 22.0, 5.0, "medium", 400.0),
        ("Vanilla McFlurry",     140.0, 3.5, 23.0, 4.5, "regular", 250.0),
        ("Soft Serve Sundae",     80.0, 2.0, 15.0, 2.0, "small",  150.0),
    ]
    ids: dict[str, int] = {}
    for name, kcal, prot, carb, fat, plabel, pgrams in foods:
        fid = con.execute(
            "INSERT INTO foods (name, brand, source) VALUES (?, ?, 'manual') RETURNING id",
            (name, chain),
        ).fetchone()[0]
        con.execute(
            "INSERT INTO food_nutrients (food_id, kcal, protein_g, carb_g, fat_g) "
            "VALUES (?, ?, ?, ?, ?)",
            (fid, kcal, prot, carb, fat),
        )
        con.execute(
            "INSERT INTO portions (food_id, label, grams) VALUES (?, ?, ?)",
            (fid, plabel, pgrams),
        )
        ids[name] = fid
    db.rebuild_fts(con)
    con.commit()
    return ids


# ---------------------------------------------------------------------------
# Per-100g scaling tests (must still pass — the scaling logic is unchanged)
# ---------------------------------------------------------------------------

class FitMealScalingTest(unittest.TestCase):
    """Verify that per-100g → per-serving scaling is applied correctly.

    The hard ceiling is the full remaining budget passed in.  meals_left_override=1
    is used to make the per-meal target == the ceiling, so these tests match the
    original 'fill the whole budget' behaviour while still being valid scaling tests.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = db.connect(Path(self.tmp.name) / "test.db")
        self.ids = _seed_chain(self.con)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_per_100g_trap_burger_kcal(self):
        """Classic Patty stores 257 kcal/100g; serving is 219g → ~562 kcal.

        A ceiling of 600 kcal should fit it (562 ≤ 600).
        A ceiling of 300 kcal should NOT fit it (562 > 300).
        A broken solver that forgets to scale would see 257 < 300 and
        incorrectly approve the patty for the 300-kcal ceiling.
        """
        budget_fits = {"kcal": 600.0, "protein_g": 100.0, "carb_g": 100.0, "fat_g": 50.0}
        # meals_left_override=1 makes target == ceiling; confirms ceiling enforcement
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget_fits,
                                 meals_left_override=1)
        patty_fits = any(
            any("Classic Patty" == it["name"] for it in order["items"])
            for order in result["orders"]
        )
        self.assertTrue(patty_fits,
                        "Classic Patty (~562 kcal) should fit in a 600 kcal ceiling")

        budget_too_tight = {"kcal": 300.0, "protein_g": 100.0, "carb_g": 100.0, "fat_g": 50.0}
        result2 = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget_too_tight,
                                  meals_left_override=1)
        patty_in_tight = any(
            any("Classic Patty" == it["name"] for it in order["items"])
            for order in result2["orders"]
        )
        self.assertFalse(patty_in_tight,
                         "Classic Patty (~562 kcal) must NOT fit in a 300 kcal ceiling — "
                         "solver must scale by portion grams, not use raw per-100g value")

    def test_per_100g_trap_diet_cola_fits_any_budget(self):
        """Zero Cola: 0 kcal/100g, 500g portion → 0 kcal.
        It should fit any ceiling (including very tight ones)."""
        budget = {"kcal": 1.0, "protein_g": 1.0, "carb_g": 1.0, "fat_g": 1.0}
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget)
        cola_fits = any(
            any("Zero Cola" == it["name"] for it in order["items"])
            for order in result["orders"]
        )
        self.assertTrue(cola_fits, "Zero Cola (0 kcal per serving) should fit any ceiling")

    def test_item_macros_use_portion_grams(self):
        """Verify that Classic Patty in orders carries properly scaled macros.

        Stored per-100g: 257 kcal. Portion: 219g. Actual serving: 562.8 kcal.
        The item's macros dict must show ~562.8, not 257.
        """
        budget = {"kcal": 700.0, "protein_g": 100.0, "carb_g": 100.0, "fat_g": 60.0}
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget,
                                 meals_left_override=1)
        found_patty = False
        for order in result["orders"]:
            for item in order["items"]:
                if item["name"] == "Classic Patty":
                    found_patty = True
                    # 257 * 219/100 = 562.8 kcal — must NOT be raw 257
                    self.assertAlmostEqual(item["macros"]["kcal"], 562.8, delta=1.0,
                                          msg="Classic Patty kcal must be scaled to portion (219g), not raw per-100g")
        if not found_patty:
            self.fail("Classic Patty should appear in at least one order for a 700 kcal ceiling")

    def test_over_budget_flags_double(self):
        """Double Stack (380 kcal/100g × 310g = 1178 kcal) must appear in
        over_budget when the ceiling is tight, and must NOT appear in orders."""
        budget = {"kcal": 400.0, "protein_g": 100.0, "carb_g": 100.0, "fat_g": 50.0}
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget)
        over_names = [item["name"] for item in result["over_budget"]]
        self.assertIn("Double Stack", over_names,
                      "Double Stack should be flagged over_budget when kcal ceiling is tight")
        # And it must NOT appear in orders
        for order in result["orders"]:
            for it in order["items"]:
                self.assertNotEqual(it["name"], "Double Stack",
                                    "Over-budget item must not appear in orders")


# ---------------------------------------------------------------------------
# Solver correctness tests
# ---------------------------------------------------------------------------

class FitMealSolverTest(unittest.TestCase):
    """Solver correctness: combos, ranking, edge cases."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = db.connect(Path(self.tmp.name) / "test.db")
        self.ids = _seed_chain(self.con)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_returns_up_to_three_orders(self):
        budget = {"kcal": 1000.0, "protein_g": 100.0, "carb_g": 200.0, "fat_g": 80.0}
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget,
                                 meals_left_override=1)
        self.assertLessEqual(len(result["orders"]), 3)
        self.assertGreater(len(result["orders"]), 0,
                           "Should find at least one fitting order")

    def test_orders_ranked_by_score(self):
        budget = {"kcal": 1000.0, "protein_g": 100.0, "carb_g": 200.0, "fat_g": 80.0}
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget,
                                 meals_left_override=1)
        scores = [o["fit_score"] for o in result["orders"]]
        self.assertEqual(scores, sorted(scores, reverse=True),
                         "Orders must be sorted descending by fit_score")

    def test_order_ranks_are_sequential(self):
        budget = {"kcal": 1000.0, "protein_g": 100.0, "carb_g": 200.0, "fat_g": 80.0}
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget,
                                 meals_left_override=1)
        for i, order in enumerate(result["orders"], 1):
            self.assertEqual(order["rank"], i)

    def test_combo_totals_match_item_sum(self):
        """Total macros on each order must equal the sum of its items' macros."""
        budget = {"kcal": 1000.0, "protein_g": 100.0, "carb_g": 200.0, "fat_g": 80.0}
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget,
                                 meals_left_override=1)
        for order in result["orders"]:
            for macro in ("kcal", "protein_g", "carb_g", "fat_g"):
                expected = round(sum(it["macros"][macro] for it in order["items"]), 1)
                self.assertAlmostEqual(order["total"][macro], expected, delta=0.2,
                                       msg=f"Order total.{macro} doesn't match item sum")

    def test_no_order_exceeds_ceiling(self):
        """Every returned order must be within the hard ceiling on all macros."""
        budget = {"kcal": 700.0, "protein_g": 50.0, "carb_g": 80.0, "fat_g": 35.0}
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget,
                                 meals_left_override=3)
        for order in result["orders"]:
            for macro in ("kcal", "protein_g", "carb_g", "fat_g"):
                b = budget[macro]
                self.assertLessEqual(
                    order["total"][macro], b + 0.1,
                    msg=f"Order rank {order['rank']} exceeds {macro} ceiling ({b})",
                )

    def test_zero_budget_yields_no_orders(self):
        """A zero calorie ceiling means nothing fits except zero-kcal items."""
        budget = {"kcal": 0.0, "protein_g": 0.0, "carb_g": 0.0, "fat_g": 0.0}
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget)
        for order in result["orders"]:
            self.assertEqual(order["total"]["kcal"], 0.0,
                             "Only zero-kcal items should fit a zero-kcal ceiling")

    def test_unknown_chain_returns_note(self):
        result = fm_mod.fit_meal(self.con, "ChainThatDoesntExist123")
        self.assertEqual(result["orders"], [])
        self.assertIn("note", result)

    def test_budget_echoed_back(self):
        budget = {"kcal": 500.0, "protein_g": 30.0, "carb_g": 60.0, "fat_g": 20.0}
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget)
        self.assertEqual(result["budget"]["kcal"], 500.0)
        self.assertEqual(result["restaurant"], "FakeBurger")

    def test_pulls_remaining_from_tracker(self):
        """When remaining=None, fit_meal reads from the tracker's remaining()."""
        tracker.set_targets(self.con, 2000, 150, 200, 60, effective_date="2026-01-01")
        # No food logged, so remaining == targets
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=None, date="2026-01-01")
        self.assertEqual(result["budget"]["kcal"], 2000.0)

    def test_result_has_new_fields(self):
        """Result dict must include per_meal_target, meals_left, treats_excluded."""
        budget = {"kcal": 900.0, "protein_g": 75.0, "carb_g": 100.0, "fat_g": 30.0}
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget,
                                 meals_left_override=3)
        self.assertIn("per_meal_target", result)
        self.assertIn("meals_left", result)
        self.assertIn("treats_excluded", result)
        self.assertEqual(result["meals_left"], 3)
        self.assertTrue(result["treats_excluded"])  # default: treats excluded


# ---------------------------------------------------------------------------
# Per-meal targeting tests (new 2026-10-15)
# ---------------------------------------------------------------------------

class FitMealPerMealTargetTest(unittest.TestCase):
    """Verify the per-meal division and hard-ceiling guard."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = db.connect(Path(self.tmp.name) / "test.db")
        self.ids = _seed_chain(self.con)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_per_meal_target_is_ceiling_divided_by_meals_left(self):
        """per_meal_target must equal ceiling / meals_left for each macro."""
        budget = {"kcal": 900.0, "protein_g": 90.0, "carb_g": 120.0, "fat_g": 30.0}
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget,
                                 meals_left_override=3)
        self.assertEqual(result["meals_left"], 3)
        target = result["per_meal_target"]
        self.assertAlmostEqual(target["kcal"], 300.0, delta=0.5,
                               msg="per_meal_target.kcal = 900 / 3 = 300")
        self.assertAlmostEqual(target["protein_g"], 30.0, delta=0.5,
                               msg="per_meal_target.protein_g = 90 / 3 = 30")
        self.assertAlmostEqual(target["carb_g"], 40.0, delta=0.5,
                               msg="per_meal_target.carb_g = 120 / 3 = 40")
        self.assertAlmostEqual(target["fat_g"], 10.0, delta=0.5,
                               msg="per_meal_target.fat_g = 30 / 3 = 10")

    def test_meals_left_one_target_equals_ceiling(self):
        """With meals_left=1, per-meal target == ceiling (whole remaining for one meal)."""
        budget = {"kcal": 600.0, "protein_g": 50.0, "carb_g": 70.0, "fat_g": 20.0}
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget,
                                 meals_left_override=1)
        self.assertEqual(result["meals_left"], 1)
        target = result["per_meal_target"]
        self.assertAlmostEqual(target["kcal"], 600.0, delta=0.5)
        self.assertAlmostEqual(target["protein_g"], 50.0, delta=0.5)

    def test_hard_ceiling_never_exceeded(self):
        """Orders must NEVER exceed the ceiling even when the per-meal target is larger.

        Scenario: large ceiling (3000 kcal), 1 meal left → target = 3000 kcal.
        Classic Patty (562 kcal) should fit under the 3000 kcal ceiling.
        Double Stack (1178 kcal) also fits under 3000 kcal.
        Neither should appear in any order that totals > 3000 kcal.
        """
        budget = {"kcal": 3000.0, "protein_g": 240.0, "carb_g": 300.0, "fat_g": 93.0}
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget,
                                 meals_left_override=1)
        for order in result["orders"]:
            self.assertLessEqual(order["total"]["kcal"], 3000.0 + 0.1,
                                 "No order should exceed the kcal ceiling")
            self.assertLessEqual(order["total"]["protein_g"], 240.0 + 0.1)
            self.assertLessEqual(order["total"]["carb_g"], 300.0 + 0.1)
            self.assertLessEqual(order["total"]["fat_g"], 93.0 + 0.1)

    def test_hard_ceiling_applied_with_multiple_meals_left(self):
        """With meals_left=3 and a tight ceiling, orders must not exceed ceiling.

        This is the key regression test for the original bug: the solver was
        targeting the full day's budget (ceiling) rather than a per-meal slice.
        With meals_left=3 and kcal=600, the per-meal target = 200 kcal; orders
        may still find items up to 600 kcal (the ceiling), but they are scored
        by proximity to 200 kcal.  Either way, they must not exceed 600 kcal.
        """
        budget = {"kcal": 600.0, "protein_g": 60.0, "carb_g": 80.0, "fat_g": 25.0}
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget,
                                 meals_left_override=3)
        # Verify the target is 1/3 of the ceiling
        self.assertAlmostEqual(result["per_meal_target"]["kcal"], 200.0, delta=0.5)
        # Verify no order exceeds the hard ceiling
        for order in result["orders"]:
            self.assertLessEqual(order["total"]["kcal"], 600.0 + 0.1,
                                 "Orders must not exceed the hard ceiling even with target < ceiling")

    def test_meals_per_day_setting_persisted(self):
        """set_meals_per_day persists and get_meals_per_day reads it back."""
        fm_mod.set_meals_per_day(self.con, 4)
        self.assertEqual(fm_mod.get_meals_per_day(self.con), 4)
        fm_mod.set_meals_per_day(self.con, 2)
        self.assertEqual(fm_mod.get_meals_per_day(self.con), 2)

    def test_meals_per_day_default_is_three(self):
        """Default meals_per_day is 3 when no setting has been stored."""
        self.assertEqual(fm_mod.get_meals_per_day(self.con), 3)

    def test_meals_per_day_used_when_no_override(self):
        """meals_per_day setting is used when meals_left_override is not passed."""
        fm_mod.set_meals_per_day(self.con, 2)
        budget = {"kcal": 1000.0, "protein_g": 100.0, "carb_g": 120.0, "fat_g": 40.0}
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget,
                                 date="2026-01-01")
        # With nothing logged, meals_logged = 0, so meals_left = max(1, 2 - 0) = 2
        self.assertEqual(result["meals_left"], 2)
        self.assertAlmostEqual(result["per_meal_target"]["kcal"], 500.0, delta=0.5)


# ---------------------------------------------------------------------------
# Treats filter tests (new 2026-10-15)
# ---------------------------------------------------------------------------

class FitMealTreatsFilterTest(unittest.TestCase):
    """Treats (desserts, shakes) are excluded by default; include_treats brings them back."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = db.connect(Path(self.tmp.name) / "test.db")
        self.ids = _seed_treat_chain(self.con)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    TREAT_NAMES = {"Chocolate Milkshake", "Vanilla McFlurry", "Soft Serve Sundae"}
    REGULAR_NAMES = {"Grilled Chicken", "Caesar Wrap"}

    def _item_names_in_orders(self, result: dict) -> set:
        names = set()
        for order in result["orders"]:
            for item in order["items"]:
                names.add(item["name"])
        return names

    def test_treats_excluded_by_default(self):
        """Default call must not suggest any treat items in orders."""
        budget = {"kcal": 2000.0, "protein_g": 200.0, "carb_g": 250.0, "fat_g": 100.0}
        result = fm_mod.fit_meal(self.con, "SweetSpot", remaining=budget,
                                 meals_left_override=1)
        names = self._item_names_in_orders(result)
        for treat in self.TREAT_NAMES:
            self.assertNotIn(treat, names,
                             f"Treat item '{treat}' must be excluded from orders by default")

    def test_treats_excluded_flag_set_by_default(self):
        """treats_excluded field is True when include_treats=False (default)."""
        budget = {"kcal": 2000.0, "protein_g": 200.0, "carb_g": 250.0, "fat_g": 100.0}
        result = fm_mod.fit_meal(self.con, "SweetSpot", remaining=budget)
        self.assertTrue(result["treats_excluded"])

    def test_include_treats_flag_false_when_opted_in(self):
        """treats_excluded field is False when include_treats=True."""
        budget = {"kcal": 2000.0, "protein_g": 200.0, "carb_g": 250.0, "fat_g": 100.0}
        result = fm_mod.fit_meal(self.con, "SweetSpot", remaining=budget,
                                 include_treats=True)
        self.assertFalse(result["treats_excluded"])

    def test_treats_included_when_opted_in(self):
        """With include_treats=True, treat items appear in suggestions."""
        budget = {"kcal": 2000.0, "protein_g": 200.0, "carb_g": 250.0, "fat_g": 100.0}
        result = fm_mod.fit_meal(self.con, "SweetSpot", remaining=budget,
                                 include_treats=True, meals_left_override=1)
        names = self._item_names_in_orders(result)
        # At least one treat must appear when inclusion is enabled
        found_treat = bool(names & self.TREAT_NAMES)
        self.assertTrue(found_treat,
                        "At least one treat item should appear when include_treats=True")

    def test_regular_items_always_present(self):
        """Non-treat items always appear regardless of the treats toggle."""
        budget = {"kcal": 2000.0, "protein_g": 200.0, "carb_g": 250.0, "fat_g": 100.0}
        for include in (False, True):
            with self.subTest(include_treats=include):
                result = fm_mod.fit_meal(self.con, "SweetSpot", remaining=budget,
                                         include_treats=include, meals_left_override=1)
                names = self._item_names_in_orders(result)
                found_regular = bool(names & self.REGULAR_NAMES)
                self.assertTrue(found_regular,
                                f"Regular items should appear with include_treats={include}")

    def test_mcflurry_keyword_is_treat(self):
        """The _is_treat helper classifies McFlurry correctly."""
        self.assertTrue(fm_mod._is_treat("Vanilla McFlurry"))
        self.assertTrue(fm_mod._is_treat("Chocolate McFlurry"))

    def test_milkshake_keyword_is_treat(self):
        self.assertTrue(fm_mod._is_treat("Strawberry Milkshake"))
        self.assertTrue(fm_mod._is_treat("Vanilla Milk Shake"))

    def test_sundae_keyword_is_treat(self):
        self.assertTrue(fm_mod._is_treat("Hot Fudge Sundae"))

    def test_regular_item_not_treat(self):
        self.assertFalse(fm_mod._is_treat("Grilled Chicken Sandwich"))
        self.assertFalse(fm_mod._is_treat("Crispy Fries"))
        self.assertFalse(fm_mod._is_treat("Zero Cola"))
        self.assertFalse(fm_mod._is_treat("Garden Salad"))


# ---------------------------------------------------------------------------
# Portion fallback tests
# ---------------------------------------------------------------------------

class FitMealPortionFallbackTest(unittest.TestCase):
    """When a food has NO portion rows, 100g is used as the serving."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = db.connect(Path(self.tmp.name) / "test.db")
        # Insert a food with no portion row
        fid = self.con.execute(
            "INSERT INTO foods (name, brand, source) VALUES ('NoPortionBurger', 'Chain', 'manual') RETURNING id"
        ).fetchone()[0]
        self.con.execute(
            "INSERT INTO food_nutrients (food_id, kcal, protein_g, carb_g, fat_g) VALUES (?, 280, 14, 30, 12)",
            (fid,),
        )
        db.rebuild_fts(self.con)
        self.con.commit()
        self.fid = fid

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_no_portion_uses_100g_serving(self):
        """With no portion data, serving defaults to 100g, so kcal = 280."""
        budget = {"kcal": 300.0, "protein_g": 100.0, "carb_g": 100.0, "fat_g": 50.0}
        result = fm_mod.fit_meal(self.con, "Chain", remaining=budget,
                                 meals_left_override=1)
        # 280 kcal/100g * (100g/100) = 280 kcal — fits 300 ceiling
        burger_in_orders = any(
            any("NoPortionBurger" in it["name"] for it in order["items"])
            for order in result["orders"]
        )
        self.assertTrue(burger_in_orders,
                        "Food with no portion should use 100g serving (280 kcal fits 300 ceiling)")

    def test_no_portion_item_macro_is_100g_scale(self):
        """Item macros returned for a no-portion food must equal per-100g values."""
        budget = {"kcal": 300.0, "protein_g": 100.0, "carb_g": 100.0, "fat_g": 50.0}
        result = fm_mod.fit_meal(self.con, "Chain", remaining=budget,
                                 meals_left_override=1)
        for order in result["orders"]:
            for item in order["items"]:
                if "NoPortionBurger" in item["name"]:
                    self.assertAlmostEqual(item["macros"]["kcal"], 280.0, delta=0.5,
                                          msg="No-portion food: macros should equal per-100g (100g serving)")
                    self.assertAlmostEqual(item["grams"], 100.0, delta=0.1)


# ---------------------------------------------------------------------------
# Overshoot regression tests (2026-10-15 scoring fix)
# ---------------------------------------------------------------------------

class FitMealOvershootRegressionTest(unittest.TestCase):
    """Regression suite for the scoring bug where combos 2-3× over the per-meal
    kcal/fat target ranked #1 because the old clamped-ratio score gave ANY
    overshoot a perfect 1.0 similarity on that macro.

    Scenario mirrors the real dev report:
      per-meal target  1,001 kcal / 80 g protein / 100 g carbs / 31 g fat
      bad #1 order     three-burger stack → 1,547 kcal / 87.8 g fat (~2.8× fat target)

    We seed three items that reproduce the exact pathological totals, plus a
    "NearTarget Sandwich" that lands within 5 % of the target on every macro.
    The new asymmetric scoring must rank NearTarget first.
    """

    # Per-meal target from the real bug report
    TARGET_KCAL    = 1001.0
    TARGET_PROTEIN =   80.0
    TARGET_CARB    =  100.0
    TARGET_FAT     =   31.0

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = db.connect(Path(self.tmp.name) / "test.db")
        self._seed()

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def _seed(self):
        """Seed items that reproduce the overshoot scenario.

        All items use 100 g portions so per-100g == per-serving (no scaling).

        NearTarget Sandwich: lands just under the per-meal target on all macros.
          960 kcal / 78 g prot / 96 g carb / 29 g fat

        StackA / StackB / StackC: individually fit the per-meal ceiling (used
        as the day ceiling when meals_left_override=1).  Together they reproduce
        the 3-burger stack: ~1,547 kcal / 85.7 g prot / 113 g carb / 87.8 g fat.
        They also all fit under the day ceiling of 3× the per-meal target when
        meals_left_override=3, which is the real regression scenario.
        """
        # (name, kcal, protein_g, carb_g, fat_g) — 100 g portion
        items = [
            ("NearTarget Sandwich", 960.0, 78.0,  96.0,  29.0),
            ("StackBurger A",       500.0, 20.0,  38.0,  29.0),
            ("StackBurger B",       600.0, 30.0,  40.0,  30.0),
            ("StackBurger C",       447.0, 35.7,  35.0,  28.8),
        ]
        for name, kcal, prot, carb, fat in items:
            fid = self.con.execute(
                "INSERT INTO foods (name, brand, source) VALUES (?, 'TestChain', 'manual') RETURNING id",
                (name,),
            ).fetchone()[0]
            self.con.execute(
                "INSERT INTO food_nutrients (food_id, kcal, protein_g, carb_g, fat_g) "
                "VALUES (?, ?, ?, ?, ?)",
                (fid, kcal, prot, carb, fat),
            )
            self.con.execute(
                "INSERT INTO portions (food_id, label, grams) VALUES (?, '1 serving', 100.0)",
                (fid,),
            )
        db.rebuild_fts(self.con)
        self.con.commit()

    # Day ceiling = 3× per-meal target (3 meals left), matching the real scenario.
    _BUDGET_3X = {
        "kcal":      TARGET_KCAL    * 3,
        "protein_g": TARGET_PROTEIN * 3,
        "carb_g":    TARGET_CARB    * 3,
        "fat_g":     TARGET_FAT     * 3,
    }

    def _top_order(self, result: dict) -> dict:
        self.assertGreater(len(result["orders"]), 0, "Solver should return at least one order")
        return result["orders"][0]

    # -- Score unit-level check ------------------------------------------------

    def test_score_near_target_beats_overshoot(self):
        """_score() must rank a near-target single item above the 3-burger stack.

        Computed directly against the solver's _score() function so this test
        catches regressions even before the full solver is wired up.
        """
        target = {
            "kcal": self.TARGET_KCAL,
            "protein_g": self.TARGET_PROTEIN,
            "carb_g": self.TARGET_CARB,
            "fat_g": self.TARGET_FAT,
        }
        near = {"kcal": 960.0, "protein_g": 78.0, "carb_g": 96.0, "fat_g": 29.0}
        stack = {"kcal": 1547.0, "protein_g": 85.7, "carb_g": 113.0, "fat_g": 87.8}

        score_near  = fm_mod._score(near, target)
        score_stack = fm_mod._score(stack, target)

        self.assertGreater(
            score_near, score_stack,
            f"NearTarget score ({score_near:.4f}) should beat 3-stack score "
            f"({score_stack:.4f}); old clamped-ratio bug gave both ~1.0",
        )

    # -- Solver-level regression -----------------------------------------------

    def test_top_order_does_not_massively_overshoot_kcal(self):
        """With 3 meals left, the #1 order must not exceed 115 % of the kcal target.

        Old bug: 3-burger stack (1,547 kcal = 154 % of target) ranked #1.
        """
        result = fm_mod.fit_meal(
            self.con, "TestChain",
            remaining=self._BUDGET_3X,
            meals_left_override=3,
        )
        top = self._top_order(result)
        kcal_limit = self.TARGET_KCAL * 1.15
        self.assertLessEqual(
            top["total"]["kcal"], kcal_limit,
            f"#1 order kcal ({top['total']['kcal']}) exceeds 115 % of target "
            f"({kcal_limit:.0f}); overshoot-blind scoring bug may have regressed",
        )

    def test_top_order_does_not_massively_overshoot_fat(self):
        """With 3 meals left, the #1 order must not exceed 115 % of the fat target.

        Old bug: 3-burger stack (87.8 g fat = 283 % of 31 g target) ranked #1.
        """
        result = fm_mod.fit_meal(
            self.con, "TestChain",
            remaining=self._BUDGET_3X,
            meals_left_override=3,
        )
        top = self._top_order(result)
        fat_limit = self.TARGET_FAT * 1.15
        self.assertLessEqual(
            top["total"]["fat_g"], fat_limit,
            f"#1 order fat ({top['total']['fat_g']} g) exceeds 115 % of target "
            f"({fat_limit:.1f} g); overshoot-blind scoring bug may have regressed",
        )

    def test_near_target_single_ranks_above_3burger_stack(self):
        """NearTarget Sandwich must rank higher than any 3-item combo.

        Verifies that the near-target single item actually wins the ranking,
        not just that the #1 order is below the 115 % threshold.
        """
        result = fm_mod.fit_meal(
            self.con, "TestChain",
            remaining=self._BUDGET_3X,
            meals_left_override=3,
        )
        top = self._top_order(result)
        top_names = {it["name"] for it in top["items"]}
        self.assertIn(
            "NearTarget Sandwich", top_names,
            f"#1 order should be NearTarget Sandwich, got items: {top_names}",
        )
        self.assertEqual(
            len(top["items"]), 1,
            f"#1 order should be a single item, got {len(top['items'])} items: {top_names}",
        )

    def test_3burger_stack_scores_below_near_target_in_isolation(self):
        """The 3-burger stack (1,547/87.8) should have a lower fit_score than
        the NearTarget Sandwich order even if it appears in the results.
        """
        result = fm_mod.fit_meal(
            self.con, "TestChain",
            remaining=self._BUDGET_3X,
            meals_left_override=3,
        )
        # Find NearTarget order and 3-burger stack order (if present)
        near_score = None
        stack_score = None
        for order in result["orders"]:
            names = {it["name"] for it in order["items"]}
            if "NearTarget Sandwich" in names and len(order["items"]) == 1:
                near_score = order["fit_score"]
            if (
                "StackBurger A" in names
                and "StackBurger B" in names
                and "StackBurger C" in names
            ):
                stack_score = order["fit_score"]

        if near_score is None:
            self.fail("NearTarget Sandwich single order not found in results")

        if stack_score is not None:
            self.assertGreater(
                near_score, stack_score,
                f"NearTarget score ({near_score}) should exceed 3-stack score "
                f"({stack_score})",
            )


# ---------------------------------------------------------------------------
# Role classifier tests (2026-10-15)
# ---------------------------------------------------------------------------

class FitMealClassifierTest(unittest.TestCase):
    """Unit tests for classify_role() — keyword-based item classification."""

    def _role(self, name: str) -> str:
        return fm_mod.classify_role(name)

    # Mains
    def test_big_mac_is_main(self):
        self.assertEqual(self._role("Big Mac"), "main")

    def test_quarter_pounder_is_main(self):
        self.assertEqual(self._role("Quarter Pounder with Cheese"), "main")

    def test_hamburger_is_main(self):
        self.assertEqual(self._role("Hamburger"), "main")

    def test_cheeseburger_is_main(self):
        self.assertEqual(self._role("Cheeseburger"), "main")

    def test_filet_o_fish_is_main(self):
        self.assertEqual(self._role("Filet-O-Fish"), "main")

    def test_mcchicken_is_main(self):
        self.assertEqual(self._role("McChicken"), "main")

    def test_nuggets_is_main(self):
        self.assertEqual(self._role("Chicken McNuggets 6pc"), "main")

    def test_mcmuffin_is_main(self):
        self.assertEqual(self._role("Egg McMuffin"), "main")

    def test_wrap_is_main(self):
        self.assertEqual(self._role("Grilled Chicken Wrap"), "main")

    def test_sandwich_is_main(self):
        self.assertEqual(self._role("McChicken Sandwich"), "main")

    def test_big_breakfast_is_main(self):
        self.assertEqual(self._role("Big Breakfast"), "main")

    # Sides
    def test_fries_is_side(self):
        self.assertEqual(self._role("Medium French Fries"), "side")

    def test_hash_brown_is_side(self):
        self.assertEqual(self._role("Hash Brown"), "side")

    def test_salad_is_side(self):
        self.assertEqual(self._role("Garden Side Salad"), "side")

    def test_apple_slices_is_side(self):
        self.assertEqual(self._role("Apple Slices"), "side")

    def test_hotcakes_is_side(self):
        self.assertEqual(self._role("Hotcakes"), "side")

    # Drinks
    def test_coffee_is_drink(self):
        self.assertEqual(self._role("Premium Roast Coffee"), "drink")

    def test_latte_is_drink(self):
        self.assertEqual(self._role("McCafé Latte"), "drink")

    def test_cola_is_drink(self):
        self.assertEqual(self._role("Coca-Cola"), "drink")

    def test_juice_is_drink(self):
        self.assertEqual(self._role("Orange Juice"), "drink")

    # Other
    def test_unknown_item_is_other(self):
        self.assertEqual(self._role("Some Random Item XYZ"), "other")

    def test_classifier_is_case_insensitive(self):
        self.assertEqual(self._role("BIG MAC"), "main")
        self.assertEqual(self._role("FRENCH FRIES"), "side")
        self.assertEqual(self._role("COFFEE"), "drink")


# ---------------------------------------------------------------------------
# Combo composition tests (2026-10-15)
# ---------------------------------------------------------------------------

def _seed_composition_chain(con, *, chain: str = "ComboTest") -> dict[str, int]:
    """Seed a chain with typed items for testing combo composition rules.

    Main:  ClassicBurger    560 kcal / 219g
    Side:  SmallFries       350 kcal / 117g
    Drink: ZeroCola           0 kcal / 500g
    Side2: GardenSalad       22 kcal / 150g
    Main2: ChickenSandwich  480 kcal / 190g   (second main — must not pair with ClassicBurger)
    """
    foods = [
        # (name,              kcal/100g, prot/100g, carb/100g, fat/100g, label, grams)
        ("ClassicBurger",       257.0, 12.6, 24.0, 12.0, "1 burger", 219.0),
        ("SmallFries",          300.0,  3.5, 38.0, 15.0, "small",    117.0),
        ("ZeroCola",              0.0,  0.0,  0.1,  0.0, "medium",   500.0),
        ("GardenSalad",          15.0,  1.2,  2.5,  0.5, "side",     150.0),
        ("ChickenSandwich",     252.0, 14.0, 26.0, 10.0, "1 piece",  190.0),
    ]
    ids: dict[str, int] = {}
    for name, kcal, prot, carb, fat, plabel, pgrams in foods:
        fid = con.execute(
            "INSERT INTO foods (name, brand, source) VALUES (?, ?, 'manual') RETURNING id",
            (name, chain),
        ).fetchone()[0]
        con.execute(
            "INSERT INTO food_nutrients (food_id, kcal, protein_g, carb_g, fat_g) "
            "VALUES (?, ?, ?, ?, ?)",
            (fid, kcal, prot, carb, fat),
        )
        con.execute(
            "INSERT INTO portions (food_id, label, grams) VALUES (?, ?, ?)",
            (fid, plabel, pgrams),
        )
        ids[name] = fid
    from macro_engine import db
    db.rebuild_fts(con)
    con.commit()
    return ids


class FitMealCompositionTest(unittest.TestCase):
    """Verify that returned orders obey ≤1 main and ≤1 side composition rules."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = db.connect(Path(self.tmp.name) / "test.db")
        self.ids = _seed_composition_chain(self.con)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_no_order_contains_two_mains(self):
        """No returned order should contain more than one main item."""
        budget = {"kcal": 2000.0, "protein_g": 200.0, "carb_g": 300.0, "fat_g": 100.0}
        result = fm_mod.fit_meal(self.con, "ComboTest", remaining=budget,
                                 meals_left_override=1)
        for order in result["orders"]:
            main_count = sum(1 for it in order["items"] if it["role"] == "main")
            self.assertLessEqual(main_count, 1,
                                 f"Order rank {order['rank']} has {main_count} mains: "
                                 f"{[it['name'] for it in order['items']]}")

    def test_main_plus_side_can_outrank_single_main(self):
        """A main+side combo that hits the per-meal target can rank above a single main.

        Per-meal target: 600 kcal / 40g protein / 70g carb / 35g fat (meals_left=1).
        ClassicBurger alone:           ~562 kcal / 27.6g prot / 52.6g carb / 26.3g fat.
        ClassicBurger + GardenSalad:   ~585 kcal / 29.4g prot / 56.4g carb / 27.1g fat.
        The combo is closer to the 600 kcal / 40g protein target, so it should rank
        at least as high as the single-main order; either way the combo must appear.
        """
        # Budget chosen so ClassicBurger AND ClassicBurger+GardenSalad both fit
        budget = {"kcal": 600.0, "protein_g": 40.0, "carb_g": 70.0, "fat_g": 35.0}
        result = fm_mod.fit_meal(self.con, "ComboTest", remaining=budget,
                                 meals_left_override=1)
        self.assertGreater(len(result["orders"]), 0, "Should return at least one order")
        # Verify that at least one order is a main+side combo
        combos_found = any(
            len(order["items"]) > 1
            and any(it["role"] == "main" for it in order["items"])
            and any(it["role"] in ("side", "other") for it in order["items"])
            for order in result["orders"]
        )
        # The combo fits the budget and should appear in results
        self.assertTrue(combos_found,
                        "Expected at least one main+side combo in orders; "
                        f"got: {[[(it['name'], it['role']) for it in o['items']] for o in result['orders']]}")

    def test_each_item_carries_role_field(self):
        """Every item in every returned order must have a 'role' field."""
        budget = {"kcal": 1000.0, "protein_g": 80.0, "carb_g": 100.0, "fat_g": 50.0}
        result = fm_mod.fit_meal(self.con, "ComboTest", remaining=budget,
                                 meals_left_override=1)
        for order in result["orders"]:
            for item in order["items"]:
                self.assertIn("role", item,
                              f"Item {item['name']!r} missing 'role' field")
                self.assertIn(item["role"], ("main", "side", "drink", "other"),
                              f"Item {item['name']!r} has invalid role {item['role']!r}")

    def test_no_order_has_two_sides(self):
        """No returned order should have more than one side item."""
        budget = {"kcal": 2000.0, "protein_g": 200.0, "carb_g": 300.0, "fat_g": 100.0}
        result = fm_mod.fit_meal(self.con, "ComboTest", remaining=budget,
                                 meals_left_override=1)
        for order in result["orders"]:
            side_count = sum(
                1 for it in order["items"]
                if it["role"] in ("side", "other")
            )
            self.assertLessEqual(side_count, 1,
                                 f"Order rank {order['rank']} has {side_count} sides: "
                                 f"{[it['name'] for it in order['items']]}")


if __name__ == "__main__":
    unittest.main()
