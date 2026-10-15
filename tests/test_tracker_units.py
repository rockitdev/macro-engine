"""Dedicated unit tests for tracker.py and db.py helper functions.

These tests cover the 290+ lines added in the week-bank, meal-tracking, and
validation refactor. Each function gets at least one test; edge branches that
are not reachable through the higher-level integration tests in
test_macro_engine.py are targeted explicitly here.
"""

import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from macro_engine import db, tracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _TmpCon:
    """Thin wrapper pairing a db connection with a TemporaryDirectory so tests
    can call self._tc = _make_con(); self.con = self._tc.con and close everything in tearDown."""
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.con = db.connect(Path(self._tmp.name) / "test.db")

    def cleanup(self):
        self.con.close()
        self._tmp.cleanup()


def _make_con():
    return _TmpCon()


def _add_food(con, name="Apple", kcal=52.0, protein_g=0.3, carb_g=14.0,
              fat_g=0.2, fiber_g=2.4, source_id="test-1"):
    fid = con.execute(
        "INSERT INTO foods (name, source, source_id) VALUES (?, 'manual', ?) RETURNING id",
        (name, source_id),
    ).fetchone()[0]
    con.execute(
        "INSERT INTO food_nutrients (food_id, kcal, protein_g, carb_g, fat_g, fiber_g) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (fid, kcal, protein_g, carb_g, fat_g, fiber_g),
    )
    db.rebuild_fts(con)
    con.commit()
    return fid


# ---------------------------------------------------------------------------
# _num()
# ---------------------------------------------------------------------------

class TestNum(unittest.TestCase):
    def test_valid_int(self):
        self.assertEqual(tracker._num(42, "x"), 42.0)
        self.assertIsInstance(tracker._num(1, "x"), float)

    def test_valid_float_string(self):
        self.assertAlmostEqual(tracker._num("3.14", "x"), 3.14)

    def test_none_without_allow_none_raises(self):
        with self.assertRaises(ValueError):
            tracker._num(None, "x")

    def test_none_with_allow_none_returns_none(self):
        self.assertIsNone(tracker._num(None, "x", allow_none=True))

    def test_nan_raises(self):
        with self.assertRaises(ValueError):
            tracker._num(float("nan"), "x")

    def test_inf_raises(self):
        with self.assertRaises(ValueError):
            tracker._num(float("inf"), "x")

    def test_non_numeric_string_raises(self):
        with self.assertRaises(ValueError):
            tracker._num("abc", "x")

    def test_zero_with_positive_raises(self):
        with self.assertRaises(ValueError):
            tracker._num(0, "x", positive=True)

    def test_negative_with_positive_raises(self):
        with self.assertRaises(ValueError):
            tracker._num(-1, "x", positive=True)

    def test_positive_value_with_positive_ok(self):
        self.assertEqual(tracker._num(0.001, "x", positive=True), 0.001)

    def test_negative_with_nonneg_raises(self):
        with self.assertRaises(ValueError):
            tracker._num(-0.1, "x", nonneg=True)

    def test_zero_with_nonneg_ok(self):
        self.assertEqual(tracker._num(0, "x", nonneg=True), 0.0)


# ---------------------------------------------------------------------------
# _text()
# ---------------------------------------------------------------------------

class TestText(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(tracker._text("hello", "f"), "hello")

    def test_strips_whitespace(self):
        self.assertEqual(tracker._text("  hi  ", "f"), "hi")

    def test_empty_string_raises(self):
        with self.assertRaises(ValueError):
            tracker._text("", "f")

    def test_whitespace_only_raises(self):
        with self.assertRaises(ValueError):
            tracker._text("   ", "f")

    def test_none_raises(self):
        with self.assertRaises(ValueError):
            tracker._text(None, "f")

    def test_non_string_raises(self):
        with self.assertRaises(ValueError):
            tracker._text(123, "f")


# ---------------------------------------------------------------------------
# _valid_date()
# ---------------------------------------------------------------------------

class TestValidDate(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(tracker._valid_date("2026-07-08"), "2026-07-08")

    def test_normalizes_leading_zeros(self):
        self.assertEqual(tracker._valid_date("2026-01-01"), "2026-01-01")

    def test_invalid_string_raises(self):
        with self.assertRaises(ValueError):
            tracker._valid_date("next-week")

    def test_none_raises(self):
        with self.assertRaises(ValueError):
            tracker._valid_date(None)

    def test_wrong_format_raises(self):
        with self.assertRaises(ValueError):
            tracker._valid_date("07/08/2026")


# ---------------------------------------------------------------------------
# _default_portion_rank()
# ---------------------------------------------------------------------------

class TestDefaultPortionRank(unittest.TestCase):
    def test_medium_is_zero(self):
        self.assertEqual(tracker._default_portion_rank("1 medium banana"), 0)

    def test_large_is_one(self):
        self.assertEqual(tracker._default_portion_rank("1 large egg"), 1)

    def test_small_is_two(self):
        self.assertEqual(tracker._default_portion_rank("1 small"), 2)

    def test_one_prefix_is_three(self):
        self.assertEqual(tracker._default_portion_rank("1 cup"), 3)

    def test_other_is_four(self):
        self.assertEqual(tracker._default_portion_rank("2 cups, mashed"), 4)

    def test_case_insensitive_medium(self):
        self.assertEqual(tracker._default_portion_rank("1 MEDIUM apple"), 0)


# ---------------------------------------------------------------------------
# _meal()
# ---------------------------------------------------------------------------

class TestMeal(unittest.TestCase):
    def test_none_returns_none(self):
        self.assertIsNone(tracker._meal(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(tracker._meal(""))

    def test_snack_alias(self):
        self.assertEqual(tracker._meal("snack"), "snacks")

    def test_breakfast(self):
        self.assertEqual(tracker._meal("breakfast"), "breakfast")

    def test_lunch_uppercase(self):
        self.assertEqual(tracker._meal("LUNCH"), "lunch")

    def test_dinner_with_whitespace(self):
        self.assertEqual(tracker._meal("  dinner  "), "dinner")

    def test_snacks_canonical(self):
        self.assertEqual(tracker._meal("snacks"), "snacks")

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            tracker._meal("brunch")

    def test_non_string_raises(self):
        with self.assertRaises(ValueError):
            tracker._meal(42)


# ---------------------------------------------------------------------------
# _weekday_name()
# ---------------------------------------------------------------------------

class TestWeekdayName(unittest.TestCase):
    def test_monday_full(self):
        self.assertEqual(tracker._weekday_name("monday"), "monday")

    def test_mon_alias(self):
        self.assertEqual(tracker._weekday_name("mon"), "monday")

    def test_sunday_full(self):
        self.assertEqual(tracker._weekday_name("sunday"), "sunday")

    def test_sun_alias(self):
        self.assertEqual(tracker._weekday_name("sun"), "sunday")

    def test_case_insensitive(self):
        self.assertEqual(tracker._weekday_name("FRIDAY"), "friday")

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            tracker._weekday_name("funday")

    def test_non_string_raises(self):
        with self.assertRaises(ValueError):
            tracker._weekday_name(1)

    def test_thu_alias(self):
        self.assertEqual(tracker._weekday_name("thu"), "thursday")

    def test_thur_alias(self):
        self.assertEqual(tracker._weekday_name("thur"), "thursday")

    def test_tues_alias(self):
        self.assertEqual(tracker._weekday_name("tues"), "tuesday")


# ---------------------------------------------------------------------------
# get_week_start() and set_week_start()
# ---------------------------------------------------------------------------

class TestWeekStart(unittest.TestCase):
    def setUp(self):
        self._tc = _make_con(); self.con = self._tc.con

    def tearDown(self):
        self._tc.cleanup()

    def test_default_is_monday(self):
        self.assertEqual(tracker.get_week_start(self.con), "monday")

    def test_set_and_get(self):
        tracker.set_week_start(self.con, "wednesday")
        self.assertEqual(tracker.get_week_start(self.con), "wednesday")

    def test_set_invalid_raises(self):
        with self.assertRaises(ValueError):
            tracker.set_week_start(self.con, "brunch")
        # Should still be monday (unchanged)
        self.assertEqual(tracker.get_week_start(self.con), "monday")

    def test_set_overrides_previous(self):
        tracker.set_week_start(self.con, "friday")
        tracker.set_week_start(self.con, "sunday")
        self.assertEqual(tracker.get_week_start(self.con), "sunday")

    def test_set_returns_dict(self):
        result = tracker.set_week_start(self.con, "saturday")
        self.assertEqual(result, {"week_starts_on": "saturday"})


# ---------------------------------------------------------------------------
# day_totals() and _day_sums()
# ---------------------------------------------------------------------------

class TestDayTotals(unittest.TestCase):
    def setUp(self):
        self._tc = _make_con(); self.con = self._tc.con

    def tearDown(self):
        self._tc.cleanup()

    def _insert(self, date, kcal=0, protein_g=0, carb_g=0, fat_g=0, fiber_g=0):
        self.con.execute(
            "INSERT INTO log (date, qty_desc, kcal, protein_g, carb_g, fat_g, fiber_g) "
            "VALUES (?, 'test', ?, ?, ?, ?, ?)",
            (date, kcal, protein_g, carb_g, fat_g, fiber_g),
        )
        self.con.commit()

    def test_empty_day_returns_zeros(self):
        totals = tracker.day_totals(self.con, "2026-07-08")
        self.assertEqual(totals["kcal"], 0)
        self.assertEqual(totals["entries"], 0)

    def test_sums_multiple_entries(self):
        self._insert("2026-07-08", kcal=300, protein_g=25, carb_g=40, fat_g=10, fiber_g=5)
        self._insert("2026-07-08", kcal=200, protein_g=15, carb_g=20, fat_g=8, fiber_g=3)
        totals = tracker.day_totals(self.con, "2026-07-08")
        self.assertEqual(totals["kcal"], 500.0)
        self.assertEqual(totals["protein_g"], 40.0)
        self.assertEqual(totals["entries"], 2)

    def test_different_dates_isolated(self):
        self._insert("2026-07-08", kcal=500)
        self._insert("2026-07-09", kcal=700)
        self.assertEqual(tracker.day_totals(self.con, "2026-07-08")["kcal"], 500.0)
        self.assertEqual(tracker.day_totals(self.con, "2026-07-09")["kcal"], 700.0)

    def test_day_sums_returns_float_macros(self):
        self._insert("2026-07-08", kcal=100, protein_g=10, carb_g=15, fat_g=3, fiber_g=2)
        raw = tracker._day_sums(self.con, "2026-07-08")
        self.assertIsInstance(raw["kcal"], float)
        self.assertEqual(raw["kcal"], 100.0)
        self.assertEqual(raw["entries"], 1)


# ---------------------------------------------------------------------------
# remaining()
# ---------------------------------------------------------------------------

class TestRemaining(unittest.TestCase):
    def setUp(self):
        self._tc = _make_con(); self.con = self._tc.con

    def tearDown(self):
        self._tc.cleanup()

    def test_no_targets_returns_note(self):
        out = tracker.remaining(self.con, "2026-07-08")
        self.assertIsNone(out["remaining"])
        self.assertIn("set_targets", out["note"])

    def test_with_targets_returns_remaining(self):
        tracker.set_targets(self.con, 2000, 150, 200, 60, effective_date="2026-07-08")
        out = tracker.remaining(self.con, "2026-07-08")
        self.assertEqual(out["remaining"]["kcal"], 2000.0)
        self.assertEqual(out["remaining"]["carb_g"], 200.0)

    def test_fiber_none_when_unset(self):
        tracker.set_targets(self.con, 2000, 150, 200, 60, effective_date="2026-07-08")
        out = tracker.remaining(self.con, "2026-07-08")
        self.assertIsNone(out["remaining"]["fiber_g"])

    def test_fiber_tracked_when_set(self):
        tracker.set_targets(self.con, 2000, 150, 200, 60, fiber_g=30, effective_date="2026-07-08")
        self.con.execute(
            "INSERT INTO log (date, qty_desc, kcal, protein_g, carb_g, fat_g, fiber_g) "
            "VALUES ('2026-07-08', 't', 500, 50, 60, 15, 10)",
        )
        self.con.commit()
        out = tracker.remaining(self.con, "2026-07-08")
        self.assertEqual(out["remaining"]["fiber_g"], 20.0)


# ---------------------------------------------------------------------------
# set_targets() and get_targets()
# ---------------------------------------------------------------------------

class TestTargets(unittest.TestCase):
    def setUp(self):
        self._tc = _make_con(); self.con = self._tc.con

    def tearDown(self):
        self._tc.cleanup()

    def test_set_and_get_round_trip(self):
        tracker.set_targets(self.con, 2000, 150, 200, 60, 30, effective_date="2026-07-01")
        t = tracker.get_targets(self.con, "2026-07-01")
        self.assertEqual(t["kcal"], 2000.0)
        self.assertEqual(t["fiber_g"], 30.0)

    def test_fiber_g_defaults_to_none(self):
        tracker.set_targets(self.con, 2000, 150, 200, 60, effective_date="2026-07-01")
        t = tracker.get_targets(self.con, "2026-07-01")
        self.assertIsNone(t["fiber_g"])

    def test_no_target_before_effective_date(self):
        tracker.set_targets(self.con, 2000, 150, 200, 60, effective_date="2026-07-10")
        self.assertIsNone(tracker.get_targets(self.con, "2026-07-09"))

    def test_rejects_zero_kcal(self):
        with self.assertRaises(ValueError):
            tracker.set_targets(self.con, 0, 150, 200, 60, effective_date="2026-07-01")

    def test_rejects_negative_protein(self):
        with self.assertRaises(ValueError):
            tracker.set_targets(self.con, 2000, -10, 200, 60, effective_date="2026-07-01")

    def test_effective_date_defaults_to_today(self):
        import datetime
        tracker.set_targets(self.con, 2000, 150, 200, 60)
        t = tracker.get_targets(self.con)
        self.assertEqual(t["effective_date"], datetime.date.today().isoformat())


# ---------------------------------------------------------------------------
# week_summary() — partial-target float(None) fix
# ---------------------------------------------------------------------------

class TestWeekSummaryPartialTarget(unittest.TestCase):
    """Verify the float(None) guard for BANK keys when get_targets returns a
    dict with a None BANK value (simulating a DB row with NULL carb_g, e.g.
    from a manual edit or a future schema relaxation). We mock get_targets
    rather than bypassing the NOT NULL schema constraint."""

    def setUp(self):
        self._tc = _make_con(); self.con = self._tc.con

    def tearDown(self):
        self._tc.cleanup()

    def test_null_bank_field_in_saved_does_not_raise(self):
        """saved_raw accumulation skips None BANK keys instead of float(None)."""
        from unittest.mock import patch

        partial_target = {
            "kcal": 2000.0, "protein_g": 150.0,
            "carb_g": None, "fat_g": 60.0, "fiber_g": None,
            "effective_date": "2026-07-06",
        }
        for day in ("2026-07-06", "2026-07-07", "2026-07-08"):
            self.con.execute(
                "INSERT INTO log (date, qty_desc, kcal, protein_g, carb_g, fat_g) "
                "VALUES (?, 't', 1500, 100, 150, 40)", (day,)
            )
        self.con.commit()

        # Patch get_targets so any call returns our partial target (no DB write needed)
        with patch("macro_engine.tracker.get_targets", return_value=partial_target):
            # Should not raise TypeError; carb_g accumulates as 0.0 (skipped)
            out = tracker.week_summary(self.con, "2026-07-09", today="2026-07-09")

        self.assertIsNotNone(out["saved"])
        self.assertIsInstance(out["saved"]["kcal"], float)
        # carb_g was skipped in accumulation (None), stays at 0.0 init value
        self.assertEqual(out["saved"]["carb_g"], 0.0)

    def test_null_bank_field_in_room_returns_none(self):
        """room_today returns None for BANK keys with a NULL target."""
        from unittest.mock import patch

        partial_target = {
            "kcal": 2000.0, "protein_g": 150.0,
            "carb_g": None, "fat_g": 60.0, "fiber_g": None,
            "effective_date": "2026-07-06",
        }
        with patch("macro_engine.tracker.get_targets", return_value=partial_target):
            out = tracker.week_summary(self.con, "2026-07-06", today="2026-07-06")

        self.assertIsNotNone(out["room_today"])
        self.assertIsNone(out["room_today"]["carb_g"])
        self.assertIsNotNone(out["room_today"]["kcal"])

    def test_normal_targets_unaffected(self):
        """The guard does not break the normal (non-None) path."""
        tracker.set_targets(self.con, 2000, 150, 200, 60, effective_date="2026-07-06")
        for day in ("2026-07-06", "2026-07-07"):
            self.con.execute(
                "INSERT INTO log (date, qty_desc, kcal, protein_g, carb_g, fat_g) "
                "VALUES (?, 't', 1500, 100, 150, 40)", (day,)
            )
        self.con.commit()
        out = tracker.week_summary(self.con, "2026-07-08", today="2026-07-08")
        self.assertIsNotNone(out["saved"])
        self.assertEqual(out["saved"]["carb_g"], 100.0)  # 2 * (200-150)
        self.assertEqual(out["saved"]["fat_g"], 40.0)    # 2 * (60-40)


# ---------------------------------------------------------------------------
# _sum_shown_days()
# ---------------------------------------------------------------------------

class TestSumShownDays(unittest.TestCase):
    def _make_day(self, date, kcal, protein_g, carb_g, fat_g, fiber_g,
                  targets=None, under=None):
        return {
            "date": date,
            "eaten": {"kcal": kcal, "protein_g": protein_g, "carb_g": carb_g,
                      "fat_g": fat_g, "fiber_g": fiber_g},
            "targets": targets,
            "under": under,
        }

    def test_empty_list(self):
        result = tracker._sum_shown_days([])
        self.assertEqual(result["eaten"]["kcal"], 0.0)
        self.assertIsNone(result["target"]["kcal"])

    def test_sums_eaten(self):
        days = [
            self._make_day("2026-07-08", 500, 30, 60, 15, 5),
            self._make_day("2026-07-09", 300, 20, 40, 10, 3),
        ]
        result = tracker._sum_shown_days(days)
        self.assertAlmostEqual(result["eaten"]["kcal"], 800.0)
        self.assertAlmostEqual(result["eaten"]["carb_g"], 100.0)

    def test_target_from_days_with_targets(self):
        days = [
            self._make_day("2026-07-08", 500, 30, 60, 15, 5,
                           targets={"kcal": 2000, "protein_g": 150, "carb_g": 200,
                                    "fat_g": 60, "fiber_g": None, "effective_date": "2026-07-01"}),
            self._make_day("2026-07-09", 300, 20, 40, 10, 3),
        ]
        result = tracker._sum_shown_days(days)
        self.assertEqual(result["target"]["kcal"], 2000.0)
        self.assertIsNone(result["target"]["fiber_g"])
        # total eaten = 500 + 300 = 800; target = 2000 (one day only); left = 1200
        self.assertAlmostEqual(result["left"]["kcal"], 1200.0, delta=1.0)

    def test_no_targets_in_any_day(self):
        days = [self._make_day("2026-07-08", 500, 30, 60, 15, 5)]
        result = tracker._sum_shown_days(days)
        self.assertIsNone(result["target"]["kcal"])
        self.assertIsNone(result["left"]["kcal"])


# ---------------------------------------------------------------------------
# add_food() — macros_are_per_portion=True path
# ---------------------------------------------------------------------------

class TestAddFood(unittest.TestCase):
    def setUp(self):
        self._tc = _make_con(); self.con = self._tc.con

    def tearDown(self):
        self._tc.cleanup()

    def test_macros_are_per_portion_scales_to_100g(self):
        # 200 kcal per 100 g portion → 200 kcal / 100g (no scaling needed here;
        # but with portion_grams=50: 200 kcal in 50 g → 400 kcal/100g stored)
        out = tracker.add_food(
            self.con, "Widget Bar", kcal=200, protein_g=10, carb_g=25,
            fat_g=8, portion_label="1 bar", portion_grams=50,
            macros_are_per_portion=True,
        )
        stored = self.con.execute(
            "SELECT kcal FROM food_nutrients WHERE food_id = ?", (out["food_id"],)
        ).fetchone()[0]
        self.assertAlmostEqual(stored, 400.0, delta=0.5)

    def test_macros_are_per_portion_requires_portion_grams(self):
        with self.assertRaises(ValueError):
            tracker.add_food(
                self.con, "NoGrams", kcal=200, protein_g=10, carb_g=25,
                fat_g=8, macros_are_per_portion=True,
            )

    def test_add_food_with_fiber_per_portion(self):
        out = tracker.add_food(
            self.con, "Fibrous Bar", kcal=150, protein_g=5, carb_g=30,
            fat_g=3, fiber_g=6, portion_label="1 bar", portion_grams=60,
            macros_are_per_portion=True,
        )
        stored_fiber = self.con.execute(
            "SELECT fiber_g FROM food_nutrients WHERE food_id = ?", (out["food_id"],)
        ).fetchone()[0]
        # 6 g fiber in 60 g portion → 10 g/100g stored
        self.assertAlmostEqual(stored_fiber, 10.0, delta=0.5)

    def test_add_food_with_alias(self):
        out = tracker.add_food(
            self.con, "Power Bar", kcal=250, protein_g=20, carb_g=30, fat_g=7,
            alias="power bar costco",
        )
        self.assertIn("alias", out)
        self.assertIn("phrase", out["alias"])

    def test_add_food_rejects_empty_name(self):
        with self.assertRaises(ValueError):
            tracker.add_food(self.con, "", kcal=100, protein_g=5, carb_g=10, fat_g=3)

    def test_add_food_rejects_negative_kcal(self):
        with self.assertRaises(ValueError):
            tracker.add_food(self.con, "Bad", kcal=-10, protein_g=5, carb_g=10, fat_g=3)


# ---------------------------------------------------------------------------
# add_alias()
# ---------------------------------------------------------------------------

class TestAddAlias(unittest.TestCase):
    def setUp(self):
        self._tc = _make_con(); self.con = self._tc.con
        self.food_id = _add_food(self.con)

    def tearDown(self):
        self._tc.cleanup()

    def test_basic_alias(self):
        result = tracker.add_alias(self.con, "an apple", self.food_id)
        self.assertEqual(result["food"], "Apple")
        self.assertIsNone(result["default_grams"])

    def test_alias_with_default_grams(self):
        result = tracker.add_alias(self.con, "medium apple", self.food_id,
                                   default_grams=182.0)
        self.assertEqual(result["default_grams"], 182.0)

    def test_alias_food_not_found_raises(self):
        with self.assertRaises(ValueError):
            tracker.add_alias(self.con, "ghost", 99999)

    def test_alias_rejects_empty_phrase(self):
        with self.assertRaises(ValueError):
            tracker.add_alias(self.con, "", self.food_id)

    def test_alias_rejects_negative_default_grams(self):
        with self.assertRaises(ValueError):
            tracker.add_alias(self.con, "bad", self.food_id, default_grams=-10)

    def test_alias_phrase_is_normalized(self):
        result = tracker.add_alias(self.con, "An Apple", self.food_id)
        self.assertEqual(result["phrase"], "an apple")


# ---------------------------------------------------------------------------
# delete_log_entry() and set_log_meal()
# ---------------------------------------------------------------------------

class TestLogEntryOps(unittest.TestCase):
    def setUp(self):
        self._tc = _make_con(); self.con = self._tc.con
        _add_food(self.con, source_id="le-1")

    def tearDown(self):
        self._tc.cleanup()

    def _insert_log(self, date="2026-07-08"):
        r = self.con.execute(
            "INSERT INTO log (date, qty_desc, kcal, protein_g, carb_g, fat_g) "
            "VALUES (?, 'test', 200, 10, 30, 5) RETURNING id",
            (date,),
        ).fetchone()
        self.con.commit()
        return r[0]

    def test_delete_existing_returns_true(self):
        lid = self._insert_log()
        self.assertTrue(tracker.delete_log_entry(self.con, lid))

    def test_delete_nonexistent_returns_false(self):
        self.assertFalse(tracker.delete_log_entry(self.con, 99999))

    def test_set_log_meal_updates(self):
        lid = self._insert_log()
        result = tracker.set_log_meal(self.con, lid, "lunch")
        self.assertEqual(result["meal"], "lunch")
        self.assertEqual(result["log_id"], lid)

    def test_set_log_meal_clear(self):
        lid = self._insert_log()
        tracker.set_log_meal(self.con, lid, "breakfast")
        result = tracker.set_log_meal(self.con, lid, None)
        self.assertIsNone(result["meal"])

    def test_set_log_meal_nonexistent_returns_none(self):
        self.assertIsNone(tracker.set_log_meal(self.con, 99999, "dinner"))

    def test_set_log_meal_invalid_meal_raises(self):
        lid = self._insert_log()
        with self.assertRaises(ValueError):
            tracker.set_log_meal(self.con, lid, "brunch")


# ---------------------------------------------------------------------------
# _resolve_grams() edge cases not covered by integration tests
# ---------------------------------------------------------------------------

class TestResolveGrams(unittest.TestCase):
    def setUp(self):
        self._tc = _make_con(); self.con = self._tc.con
        # Food with no portions at all
        self.no_portion_food_id = _add_food(self.con, name="Mystery Paste",
                                            source_id="mp-1")

    def tearDown(self):
        self._tc.cleanup()

    def test_explicit_grams_field_overrides_unit(self):
        food = {"id": self.no_portion_food_id, "kcal": 100}
        item = {"grams": 75}
        grams, desc = tracker._resolve_grams(self.con, food, item)
        self.assertEqual(grams, 75.0)
        self.assertIn("75", desc)

    def test_no_portions_falls_back_to_100g(self):
        food = {"id": self.no_portion_food_id, "kcal": 100}
        item = {"qty": 1}
        grams, desc = tracker._resolve_grams(self.con, food, item)
        self.assertEqual(grams, 100.0)
        self.assertIn("no portion data", desc)

    def test_alias_grams_used_when_no_unit(self):
        food = {"id": self.no_portion_food_id, "kcal": 100}
        item = {"qty": 2}
        grams, desc = tracker._resolve_grams(self.con, food, item,
                                             alias_grams=50.0)
        self.assertEqual(grams, 100.0)  # 2 * 50
        self.assertIn("usual", desc)

    def test_bad_alias_grams_raises_value_error(self):
        food = {"id": self.no_portion_food_id, "kcal": 100}
        item = {}
        with self.assertRaises(ValueError):
            tracker._resolve_grams(self.con, food, item, alias_grams=-50)

    def test_unknown_unit_raises_unresolved_unit(self):
        food = {"id": self.no_portion_food_id, "kcal": 100}
        item = {"unit": "cups"}  # volume, no matching portions
        with self.assertRaises(tracker.UnresolvedUnit) as ctx:
            tracker._resolve_grams(self.con, food, item)
        self.assertEqual(ctx.exception.unit, "cups")


# ---------------------------------------------------------------------------
# db.py — _migrate() meal column branch
# ---------------------------------------------------------------------------

class TestMigrateMealColumn(unittest.TestCase):
    """_migrate() adds meal column when log table has no meal column.

    The store and fiber_g branches are already covered by test_db_extra.py;
    this targets the third branch (log.meal)."""

    def test_migrate_adds_meal_column_to_log(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nomeal.db"
            # Build a schema that has log WITHOUT meal column
            con = sqlite3.connect(str(path))
            con.row_factory = sqlite3.Row
            con.executescript("""
                CREATE TABLE foods (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                    brand TEXT, store TEXT,
                    source TEXT NOT NULL, source_id TEXT,
                    verified INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(source, source_id)
                );
                CREATE TABLE food_nutrients (
                    food_id INTEGER PRIMARY KEY,
                    kcal REAL, protein_g REAL, carb_g REAL,
                    fat_g REAL, fiber_g REAL
                );
                CREATE TABLE portions (
                    id INTEGER PRIMARY KEY,
                    food_id INTEGER NOT NULL, label TEXT NOT NULL, grams REAL NOT NULL
                );
                CREATE TABLE aliases (
                    id INTEGER PRIMARY KEY, phrase TEXT NOT NULL,
                    food_id INTEGER NOT NULL, default_grams REAL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                );
                CREATE TABLE log (
                    id INTEGER PRIMARY KEY,
                    ts TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                    date TEXT NOT NULL, food_id INTEGER,
                    qty_desc TEXT, grams REAL,
                    kcal REAL NOT NULL DEFAULT 0,
                    protein_g REAL NOT NULL DEFAULT 0,
                    carb_g REAL NOT NULL DEFAULT 0,
                    fat_g REAL NOT NULL DEFAULT 0,
                    fiber_g REAL NOT NULL DEFAULT 0,
                    raw_text TEXT, estimated INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE targets (
                    id INTEGER PRIMARY KEY, effective_date TEXT NOT NULL,
                    kcal REAL NOT NULL, protein_g REAL NOT NULL,
                    carb_g REAL NOT NULL, fat_g REAL NOT NULL,
                    fiber_g REAL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                );
                CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE VIRTUAL TABLE food_fts
                    USING fts5(name, brand, store, content='', tokenize='porter unicode61');
            """)
            con.commit()
            lcols_before = {r["name"] for r in con.execute("PRAGMA table_info(log)")}
            self.assertNotIn("meal", lcols_before)
            con.close()

            # Now open via db.connect() — should migrate and add meal
            migrated = db.connect(path)
            lcols_after = {r["name"] for r in migrated.execute("PRAGMA table_info(log)")}
            self.assertIn("meal", lcols_after)
            migrated.close()


if __name__ == "__main__":
    unittest.main()
