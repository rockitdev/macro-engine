"""Tests for macro_engine.fit_meal — combinatorial restaurant order solver.

Key correctness: ALL macros stored in food_nutrients are PER 100 g. The solver
MUST scale by (portion_grams / 100) before comparing to budget. The per-100g
trap tests below verify this explicitly — a solver that forgets to scale will
return wrong results (it would approve orders that actually blow the budget, or
reject orders that actually fit).
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class FitMealScalingTest(unittest.TestCase):
    """Verify that per-100g → per-serving scaling is applied correctly."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = db.connect(Path(self.tmp.name) / "test.db")
        self.ids = _seed_chain(self.con)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_per_100g_trap_burger_kcal(self):
        """Classic Patty stores 257 kcal/100g; serving is 219g → ~562 kcal.

        A budget of 600 kcal should fit it (562 ≤ 600).
        A budget of 300 kcal should NOT fit it (562 > 300).
        A broken solver that forgets to scale would see 257 < 300 and
        incorrectly approve the patty for the 300-kcal budget.
        """
        budget_fits = {"kcal": 600.0, "protein_g": 100.0, "carb_g": 100.0, "fat_g": 50.0}
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget_fits)
        patty_fits = any(
            any("Classic Patty" == it["name"] for it in order["items"])
            for order in result["orders"]
        )
        self.assertTrue(patty_fits,
                        "Classic Patty (~562 kcal) should fit in a 600 kcal budget")

        budget_too_tight = {"kcal": 300.0, "protein_g": 100.0, "carb_g": 100.0, "fat_g": 50.0}
        result2 = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget_too_tight)
        patty_in_tight = any(
            any("Classic Patty" == it["name"] for it in order["items"])
            for order in result2["orders"]
        )
        self.assertFalse(patty_in_tight,
                         "Classic Patty (~562 kcal) must NOT fit in a 300 kcal budget — "
                         "solver must scale by portion grams, not use raw per-100g value")

    def test_per_100g_trap_diet_cola_fits_any_budget(self):
        """Zero Cola: 0 kcal/100g, 500g portion → 0 kcal.
        It should fit any budget (including very tight ones)."""
        budget = {"kcal": 1.0, "protein_g": 1.0, "carb_g": 1.0, "fat_g": 1.0}
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget)
        cola_fits = any(
            any("Zero Cola" == it["name"] for it in order["items"])
            for order in result["orders"]
        )
        self.assertTrue(cola_fits, "Zero Cola (0 kcal per serving) should fit any budget")

    def test_item_macros_use_portion_grams(self):
        """Verify that Classic Patty in orders carries properly scaled macros.

        Stored per-100g: 257 kcal. Portion: 219g. Actual serving: 562.8 kcal.
        The item's macros dict must show ~562.8, not 257.
        """
        budget = {"kcal": 700.0, "protein_g": 100.0, "carb_g": 100.0, "fat_g": 60.0}
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget)
        found_patty = False
        for order in result["orders"]:
            for item in order["items"]:
                if item["name"] == "Classic Patty":
                    found_patty = True
                    # 257 * 219/100 = 562.8 kcal — must NOT be raw 257
                    self.assertAlmostEqual(item["macros"]["kcal"], 562.8, delta=1.0,
                                          msg="Classic Patty kcal must be scaled to portion (219g), not raw per-100g")
        if not found_patty:
            # Classic Patty (562 kcal) fits a 700 kcal budget — if not found, solver is broken
            self.fail("Classic Patty should appear in at least one order for a 700 kcal budget")

    def test_over_budget_flags_double(self):
        """Double Stack (380 kcal/100g × 310g = 1178 kcal) must appear in
        over_budget when the budget is tight, and must NOT appear in orders."""
        budget = {"kcal": 400.0, "protein_g": 100.0, "carb_g": 100.0, "fat_g": 50.0}
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget)
        over_names = [item["name"] for item in result["over_budget"]]
        self.assertIn("Double Stack", over_names,
                      "Double Stack should be flagged over_budget when kcal budget is tight")
        # And it must NOT appear in orders
        for order in result["orders"]:
            for it in order["items"]:
                self.assertNotEqual(it["name"], "Double Stack",
                                    "Over-budget item must not appear in orders")


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
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget)
        self.assertLessEqual(len(result["orders"]), 3)
        self.assertGreater(len(result["orders"]), 0,
                           "Should find at least one fitting order")

    def test_orders_ranked_by_score(self):
        budget = {"kcal": 1000.0, "protein_g": 100.0, "carb_g": 200.0, "fat_g": 80.0}
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget)
        scores = [o["fit_score"] for o in result["orders"]]
        self.assertEqual(scores, sorted(scores, reverse=True),
                         "Orders must be sorted descending by fit_score")

    def test_order_ranks_are_sequential(self):
        budget = {"kcal": 1000.0, "protein_g": 100.0, "carb_g": 200.0, "fat_g": 80.0}
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget)
        for i, order in enumerate(result["orders"], 1):
            self.assertEqual(order["rank"], i)

    def test_combo_totals_match_item_sum(self):
        """Total macros on each order must equal the sum of its items' macros."""
        budget = {"kcal": 1000.0, "protein_g": 100.0, "carb_g": 200.0, "fat_g": 80.0}
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget)
        for order in result["orders"]:
            for macro in ("kcal", "protein_g", "carb_g", "fat_g"):
                expected = round(sum(it["macros"][macro] for it in order["items"]), 1)
                self.assertAlmostEqual(order["total"][macro], expected, delta=0.2,
                                       msg=f"Order total.{macro} doesn't match item sum")

    def test_no_order_exceeds_budget(self):
        """Every returned order must be within budget on all macros."""
        budget = {"kcal": 700.0, "protein_g": 50.0, "carb_g": 80.0, "fat_g": 35.0}
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget)
        for order in result["orders"]:
            for macro in ("kcal", "protein_g", "carb_g", "fat_g"):
                b = budget[macro]
                self.assertLessEqual(
                    order["total"][macro], b + 0.1,
                    msg=f"Order rank {order['rank']} exceeds {macro} budget ({b}g)",
                )

    def test_zero_budget_yields_no_orders(self):
        """A zero calorie budget means nothing fits except zero-kcal items."""
        budget = {"kcal": 0.0, "protein_g": 0.0, "carb_g": 0.0, "fat_g": 0.0}
        result = fm_mod.fit_meal(self.con, "FakeBurger", remaining=budget)
        for order in result["orders"]:
            self.assertEqual(order["total"]["kcal"], 0.0,
                             "Only zero-kcal items should fit a zero-kcal budget")

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
        result = fm_mod.fit_meal(self.con, "Chain", remaining=budget)
        # 280 kcal/100g * (100g/100) = 280 kcal — fits 300 budget
        burger_in_orders = any(
            any("NoPortionBurger" in it["name"] for it in order["items"])
            for order in result["orders"]
        )
        self.assertTrue(burger_in_orders,
                        "Food with no portion should use 100g serving (280 kcal fits 300 budget)")

    def test_no_portion_item_macro_is_100g_scale(self):
        """Item macros returned for a no-portion food must equal per-100g values."""
        budget = {"kcal": 300.0, "protein_g": 100.0, "carb_g": 100.0, "fat_g": 50.0}
        result = fm_mod.fit_meal(self.con, "Chain", remaining=budget)
        for order in result["orders"]:
            for item in order["items"]:
                if "NoPortionBurger" in item["name"]:
                    self.assertAlmostEqual(item["macros"]["kcal"], 280.0, delta=0.5,
                                          msg="No-portion food: macros should equal per-100g (100g serving)")
                    self.assertAlmostEqual(item["grams"], 100.0, delta=0.1)


class ChainHeuristicTest(unittest.TestCase):
    """Unit tests for _titlecase and _looks_like_chain heuristics."""

    # ── _titlecase ───────────────────────────────────────────────────────────

    def test_titlecase_basic(self):
        self.assertEqual(fm_mod._titlecase("tim hortons"), "Tim Hortons")

    def test_titlecase_preserves_apostrophe_case(self):
        """str.title() turns "McDonald's" into "Mcdonald'S"; we fix the 'S."""
        result = fm_mod._titlecase("mcdonald's")
        self.assertNotIn("'S", result,
                         "_titlecase must not capitalise the letter after an apostrophe")
        self.assertTrue(result[0].isupper(), "first letter must be upper-case")

    def test_titlecase_already_correct(self):
        self.assertEqual(fm_mod._titlecase("A&W"), "A&W")

    def test_titlecase_all_upper(self):
        result = fm_mod._titlecase("BURGER KING")
        self.assertEqual(result, "Burger King")

    def test_titlecase_single_word(self):
        self.assertEqual(fm_mod._titlecase("subway"), "Subway")

    # ── _looks_like_chain ────────────────────────────────────────────────────

    def test_accepts_typical_names(self):
        for name in ["McDonald's", "Tim Hortons", "Subway", "7-Eleven", "A&W",
                     "Five Guys", "Popeyes"]:
            with self.subTest(name=name):
                self.assertTrue(fm_mod._looks_like_chain(name),
                                f"Expected {name!r} to look like a chain")

    def test_rejects_empty_string(self):
        self.assertFalse(fm_mod._looks_like_chain(""))

    def test_rejects_whitespace_only(self):
        self.assertFalse(fm_mod._looks_like_chain("   "))

    def test_rejects_url(self):
        self.assertFalse(fm_mod._looks_like_chain("http://example.com/menu"))

    def test_rejects_overlong_name(self):
        self.assertFalse(fm_mod._looks_like_chain("A" * 65))

    def test_accepts_name_with_dot(self):
        self.assertTrue(fm_mod._looks_like_chain("St. Hubert"))

    def test_accepts_name_with_ampersand(self):
        self.assertTrue(fm_mod._looks_like_chain("Fish & Chips Co."))


class ChainsDbTest(unittest.TestCase):
    """Tests for chains() — fetching distinct chain names from the DB."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = db.connect(Path(self.tmp.name) / "test.db")
        # Insert two chains; one food has no macros (should be excluded)
        for name, brand, kcal in [
            ("Classic Patty", "FakeBurger", 257.0),
            ("Crispy Fries", "FakeBurger", 300.0),
            ("Signature Wrap", "SandwichCo", 210.0),
            ("Mystery Item", "GhostChain", None),  # no macros — must be excluded
        ]:
            fid = self.con.execute(
                "INSERT INTO foods (name, brand, source) VALUES (?, ?, 'manual') RETURNING id",
                (name, brand),
            ).fetchone()[0]
            if kcal is not None:
                self.con.execute(
                    "INSERT INTO food_nutrients (food_id, kcal, protein_g, carb_g, fat_g) "
                    "VALUES (?, ?, 10, 20, 5)",
                    (fid, kcal),
                )
        db.rebuild_fts(self.con)
        self.con.commit()

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_returns_chains_with_macro_data(self):
        result = fm_mod.chains(self.con)
        # Brands are returned as stored in the DB (not title-cased)
        self.assertIn("FakeBurger", result)
        self.assertIn("SandwichCo", result)

    def test_excludes_chains_without_macro_data(self):
        result = fm_mod.chains(self.con)
        self.assertNotIn("GhostChain", result,
                         "Chains with no macro data must be excluded")

    def test_no_duplicates(self):
        result = fm_mod.chains(self.con)
        self.assertEqual(result, sorted(set(result)))

    def test_sorted_alphabetically(self):
        result = fm_mod.chains(self.con)
        self.assertEqual(result, sorted(result))

    def test_empty_db_returns_empty_list(self):
        tmp = tempfile.TemporaryDirectory()
        empty_con = db.connect(Path(tmp.name) / "empty.db")
        try:
            result = fm_mod.chains(empty_con)
            self.assertEqual(result, [])
        finally:
            empty_con.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
