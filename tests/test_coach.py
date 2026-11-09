"""Coaching layer: the Fat Loss Forever rules, checked against the book's own
worked examples and against synthetic multi-week scenarios."""

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from macro_engine import coach, db, tracker

MON = "2026-09-07"  # a Monday; default week start is monday


def d(offset: int, base: str = MON) -> str:
    return (dt.date.fromisoformat(base) + dt.timedelta(days=offset)).isoformat()


class BookMathTest(unittest.TestCase):
    """Pure functions reproduce the book's examples."""

    def test_harris_benedict_examples(self):
        self.assertAlmostEqual(coach.bmr_harris_benedict(100, 182, "male", 25), 2159.555, places=3)
        self.assertAlmostEqual(coach.bmr_harris_benedict(60, 152.4, "female", 25), 1366.29, delta=0.01)

    def test_muller_examples(self):
        self.assertAlmostEqual(coach.bmr_muller(85, 15, "male", 25), 2087.315, places=3)
        self.assertAlmostEqual(coach.bmr_muller(51, 9, "female", 25), 1369.679, places=3)

    def test_energy_factor_table(self):
        # Table 2 (p.136): normal BF, high protein + RT = 930; obese NP no RT = 930;
        # overweight HP+RT = 1024; lean HP+RT = 834; lean NP no RT ~645
        self.assertEqual(round(coach.energy_factor("normal", True, True)), 929)
        self.assertEqual(round(coach.energy_factor("overweight", True, True)), 1024)
        self.assertEqual(round(coach.energy_factor("lean", True, True)), 834)
        self.assertEqual(round(coach.energy_factor("lean", False, False)), 645)
        self.assertEqual(round(coach.energy_factor("obese", False, False)), 929)

    def test_deficit_example(self):
        # 100 kg, 0.6%/wk, factor 930 -> ~558 kcal/day (p.137)
        deficit = 100 * 0.6 / 100 * coach.energy_factor("normal", True, True)
        self.assertAlmostEqual(deficit, 558, delta=1)

    def test_macro_split_examples(self):
        # Man: 2242 kcal, 190 P, 60/40 -> 190/220/65 (p.186)
        m = coach.split_macros(2242, 190, 0.6)
        self.assertEqual((m["protein_g"], m["carb_g"], m["fat_g"]), (190, 220, 65))
        # Woman: 1428 kcal, 175 P, 55/45 -> 175/100/35
        w = coach.split_macros(1428, 175, 0.55)
        self.assertEqual((w["protein_g"], w["carb_g"], w["fat_g"]), (175, 100, 35))
        # kcal always agrees with the rounded grams
        self.assertEqual(m["kcal"], 4 * 190 + 4 * 220 + 9 * 65)

    def test_fat_floor(self):
        # 90/10 carb share would push fat under 20% of kcal; the floor holds it.
        m = coach.split_macros(2000, 150, 0.8)
        self.assertGreaterEqual(m["fat_g"] * 9, 0.2 * 2000 - 25)

    def test_adjust_only_touches_carbs_and_fat(self):
        # -5% on 175/100/35 -> about -5 g C, -2 g F (p.227)
        out = coach.adjust_carb_fat({"kcal": 1415, "protein_g": 175, "carb_g": 100, "fat_g": 35}, -5)
        self.assertEqual(out["protein_g"], 175)
        self.assertEqual(out["carb_g"], 95)
        self.assertEqual(out["fat_g"], 33)
        self.assertEqual(out["kcal"], 4 * 175 + 4 * 95 + 9 * 33)

    def test_protein_table(self):
        g, basis = coach.protein_target(100, 80, 30, True)
        self.assertEqual(g, 185)  # 2.3 g/kg LBM midpoint
        self.assertIn("lean mass", basis)
        g2, _ = coach.protein_target(100, 80, 55, True)
        self.assertGreater(g2, g)  # older -> more protein
        g3, _ = coach.protein_target(100, 80, 30, False)
        self.assertLess(g3, g)     # not dieting -> less
        g4, basis4 = coach.protein_target(80, None, 30, True)
        self.assertEqual(g4, 175)  # 2.2 g/kg BW
        g5, basis5 = coach.protein_target(136, None, 30, True, bmi=40)
        self.assertLess(g5, 2.2 * 136)
        self.assertIn("BMI", basis5)

    def test_bf_category(self):
        self.assertEqual(coach.bf_category("male", 30), "obese")
        self.assertEqual(coach.bf_category("male", 15), "normal")
        self.assertEqual(coach.bf_category("male", 8), "lean")
        self.assertEqual(coach.bf_category("female", 35), "overweight")
        self.assertEqual(coach.bf_category("female", None, 31), "obese")


class CoachDBTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = db.connect(Path(self.tmp.name) / "t.db")
        coach.set_profile(self.con, sex="male", birth_year=1990, height_cm=180,
                          activity="moderate")

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    # ── helpers ──
    def weigh(self, start: str, kgs: list[float]):
        for i, kg in enumerate(kgs):
            coach.log_metrics(self.con, d(i, start), weight=kg)

    def eat(self, start: str, days: int, kcal: float, protein=180):
        for i in range(days):
            tracker.log_meal(self.con, [{"name": "day", "macros": {
                "kcal": kcal, "protein_g": protein, "carb_g": 200, "fat_g": 60}}], d(i, start))

    def weeks_of(self, start: str, weekly_avgs: list[float], kcal: float | None = None):
        """Seven daily weigh-ins per week around each average (+/- noise)."""
        noise = [0.4, -0.3, 0.1, -0.2, 0.3, -0.4, 0.1]
        for w, avg in enumerate(weekly_avgs):
            self.weigh(d(7 * w, start), [round(avg + n, 2) for n in noise])
            if kcal:
                self.eat(d(7 * w, start), 7, kcal)

    # ── metrics ──
    def test_log_metrics_converts_and_upserts(self):
        row = coach.log_metrics(self.con, MON, weight=200, unit="lb")
        self.assertAlmostEqual(row["weight_kg"], 90.72, places=2)
        row = coach.log_metrics(self.con, MON, waist=34, length_unit="in")
        self.assertAlmostEqual(row["weight_kg"], 90.72, places=2)  # untouched
        self.assertAlmostEqual(row["waist_cm"], 86.4, places=1)

    def test_log_metrics_rejects_garbage(self):
        with self.assertRaises(ValueError):
            coach.log_metrics(self.con, MON, weight=900)
        with self.assertRaises(ValueError):
            coach.log_metrics(self.con, MON)
        with self.assertRaises(ValueError):
            coach.log_metrics(self.con, MON, weight=80, unit="stone")

    def test_weekly_average_book_example(self):
        # p.216: weekly averages show a 1.3 lb LOSS where single days show a gain.
        w1 = [216.9, 216.5, 214.1, 214.5, 217.5, 215.9, 215.3]
        w2 = [215.1, 214.7, 213.1, 214.0, 213.5, 214.1, 216.8]
        for i, lb in enumerate(w1 + w2):
            coach.log_metrics(self.con, d(i), weight=lb, unit="lb")
        trend = coach.weekly_trend(self.con, d(13), weeks=2)
        self.assertAlmostEqual(trend[0]["avg_lb"], 215.8, delta=0.05)
        self.assertAlmostEqual(trend[1]["avg_lb"], 214.5, delta=0.05)
        self.assertLess(trend[1]["change_kg"], 0)

    def test_week_needs_three_weighins(self):
        self.weigh(MON, [80, 80])
        self.assertIsNone(coach.weekly_trend(self.con, MON, weeks=1)[0]["avg_kg"])

    def test_profile_validation(self):
        with self.assertRaises(ValueError):
            coach.set_profile(self.con, sex="x")
        with self.assertRaises(ValueError):
            coach.set_profile(self.con, activity="couch")
        with self.assertRaises(ValueError):
            coach.set_profile(self.con, bogus=1)
        p = coach.set_profile(self.con, height_in=70, carb_share=55)
        self.assertAlmostEqual(p["height_cm"], 177.8)
        self.assertAlmostEqual(p["carb_share"], 0.55)

    def test_full_birth_date_tracks_birthday_and_preserves_legacy_profiles(self):
        self.assertEqual(coach._age({"birth_year": 1981}, "2026-11-09"), 45)
        p = coach.set_profile(self.con, birth_date="1981-11-10")
        self.assertEqual(p["birth_year"], 1981)
        self.assertEqual(coach._age(p, "2026-11-09"), 44)
        self.assertEqual(coach._age(p, "2026-11-10"), 45)
        self.assertEqual(coach._age(p, "2027-11-09"), 45)
        with self.assertRaisesRegex(ValueError, "conflicts"):
            coach.set_profile(self.con, birth_year=1982)
        self.assertEqual(coach.get_profile(self.con)["birth_date"], "1981-11-10")
        with self.assertRaises(ValueError):
            coach.set_profile(self.con, birth_date="1981-02-30")
        with self.assertRaises(ValueError):
            coach.set_profile(self.con, birth_date="2999-01-01")
        # Correcting a complete date updates the approximate legacy year too.
        p = coach.set_profile(self.con, birth_date="1982-11-10")
        self.assertEqual(p["birth_year"], 1982)

    def test_profile_schedule_and_goal_merge_without_changing_targets(self):
        tracker.set_targets(self.con, 3000, 240, 300, 93, effective_date="2026-01-01")
        before = tracker.get_targets(self.con, "2026-11-09")
        p = coach.set_profile(self.con, birth_date="1981-11-10", height_in=72,
                              daily_activity="seated", strength_days_per_week=6,
                              strength_minutes_per_session=45, goal="cut", goal_loss_lb=100)
        self.assertAlmostEqual(p["height_cm"], 182.9)
        self.assertAlmostEqual(p["goal_loss_kg"] * coach.LB_PER_KG, 100, places=2)
        p = coach.set_profile(self.con, units="lb")
        self.assertEqual(p["strength_days_per_week"], 6)
        self.assertEqual(p["strength_minutes_per_session"], 45)
        self.assertEqual(p["daily_activity"], "seated")
        self.assertEqual(tracker.get_targets(self.con, "2026-11-09"), before)
        self.assertIsNone(coach.current_phase(self.con, "2026-11-09"))
        p = coach.set_profile(self.con, goal_loss_kg="", strength_minutes_per_session="")
        self.assertIsNone(p["goal_loss_kg"])
        self.assertIsNone(p["strength_minutes_per_session"])
        self.assertEqual(p["strength_days_per_week"], 6)

    def test_profile_rejects_invalid_schedule_and_goals_atomically(self):
        before = coach.get_profile(self.con)
        for fields in [dict(strength_days_per_week=8), dict(strength_days_per_week=2.5),
                       dict(strength_days_per_week=True), dict(strength_minutes_per_session=-1),
                       dict(strength_minutes_per_session=481), dict(goal_loss_kg=-10),
                       dict(goal_loss_kg=float("nan")), dict(goal_loss_kg=301),
                       dict(goal_loss_lb=100, goal_loss_kg=45), dict(daily_activity="unknown"),
                       dict(goal="crash")]:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                coach.set_profile(self.con, height_cm=190, **fields)
            self.assertEqual(coach.get_profile(self.con), before)

    # ── maintenance ──
    def test_empirical_maintenance_from_loss(self):
        # 2400 kcal/day, losing 0.5 kg/week -> maintenance ~ 2400 + 0.5 * factor
        self.weeks_of(MON, [90.0, 89.5, 89.0, 88.5], kcal=2400)
        m = coach.empirical_maintenance(self.con, d(27))
        self.assertIsNotNone(m["kcal"])
        self.assertAlmostEqual(m["rate_kg_per_week"], 0.5, places=2)
        factor = coach.energy_factor("overweight", True, True)  # BMI 27.8, no BF
        self.assertAlmostEqual(m["kcal"], 2400 + 0.5 * factor, delta=2)
        self.assertEqual(m["confidence"], "high")

    def test_empirical_maintenance_skips_incomplete_days(self):
        tracker.set_targets(self.con, 2400, 180, 200, 60, effective_date=MON)
        self.weeks_of(MON, [90.0, 90.0, 90.0], kcal=2400)
        tracker.log_meal(self.con, [{"name": "x", "macros": {"kcal": 300}}], d(21))
        coach.log_metrics(self.con, d(21), weight=90)
        m = coach.empirical_maintenance(self.con, d(21))
        self.assertGreaterEqual(m["days_skipped_incomplete"], 0)
        self.assertAlmostEqual(m["kcal"], 2400, delta=5)

    def test_empirical_maintenance_needs_data(self):
        m = coach.empirical_maintenance(self.con, MON)
        self.assertIsNone(m["kcal"])
        self.assertEqual(m["confidence"], "none")

    def test_predicted_maintenance_switches_equation(self):
        self.weigh(MON, [90, 90, 90])
        p = coach.predicted_maintenance(self.con, d(2))
        self.assertEqual(p["equation"], "harris_benedict_revised")
        coach.log_metrics(self.con, d(2), body_fat_pct=20)
        p = coach.predicted_maintenance(self.con, d(2))
        self.assertEqual(p["equation"], "muller")
        self.assertAlmostEqual(p["kcal"], p["bmr"] * 1.55, delta=1)

    # ── planning ──
    def test_plan_cut_preview_and_apply(self):
        self.weigh(MON, [90, 90, 90])
        coach.log_metrics(self.con, d(2), body_fat_pct=20)
        out = coach.plan_targets(self.con, "cut", rate_pct=0.6, date=d(2))
        self.assertIn("predicted", out["maintenance"]["source"])
        self.assertLess(out["targets"]["kcal"], out["maintenance"]["kcal"])
        self.assertAlmostEqual(out["deficit"]["kg_per_week"], 0.54, places=2)
        self.assertIsNone(tracker.get_targets(self.con, d(2)))  # preview only
        out = coach.plan_targets(self.con, "cut", rate_pct=0.6, date=d(2), apply=True,
                                 target_body_fat_pct=12)
        self.assertEqual(tracker.get_targets(self.con, d(2))["kcal"], out["targets"]["kcal"])
        self.assertEqual(coach.current_phase(self.con, d(2))["kind"], "cut")
        self.assertGreater(out["timeline"]["diet_weeks"], 10)

    def test_plan_rejects_over_one_percent(self):
        self.weigh(MON, [90, 90, 90])
        with self.assertRaises(ValueError):
            coach.plan_targets(self.con, "cut", rate_pct=1.5, date=d(2))

    def test_plan_requires_weight(self):
        with self.assertRaises(ValueError):
            coach.plan_targets(self.con, "cut", date=MON)

    def test_plan_gate_on_suppressed_metabolism(self):
        # Maintaining weight on 1500 kcal when ~2900 is predicted -> don't diet.
        self.weeks_of(MON, [90.0, 90.0, 90.0, 90.0], kcal=1500)
        out = coach.plan_targets(self.con, "cut", date=d(27))
        self.assertTrue(any("reverse diet first" in w for w in out["warnings"]))

    # ── check-ins ──
    def cut_setup(self, targets=(2200, 180, 220, 60)):
        tracker.set_targets(self.con, *targets, effective_date=MON)
        coach.start_phase(self.con, "cut", MON, rate_pct=0.6)

    def test_checkin_collecting(self):
        self.cut_setup()
        self.weigh(MON, [90, 90, 90])
        out = coach.check_in(self.con, d(2))
        self.assertEqual(out["status"], "collecting")

    def test_checkin_first_week_holds(self):
        self.cut_setup()
        self.weeks_of(d(-7), [91.5, 90.0], kcal=2200)
        out = coach.check_in(self.con, d(6))
        self.assertEqual(out["status"], "first_week")
        self.assertEqual(out["action"], "hold")

    def test_checkin_on_track(self):
        self.cut_setup()
        self.weeks_of(MON, [90.0, 89.5, 89.0], kcal=2200)
        out = coach.check_in(self.con, d(20))
        self.assertEqual(out["status"], "on_track")
        self.assertIsNone(out["proposed_targets"])

    def test_single_slow_week_is_noise(self):
        self.cut_setup()
        self.weeks_of(MON, [90.0, 89.5, 89.45], kcal=2200)
        out = coach.check_in(self.con, d(20))
        self.assertEqual(out["status"], "slow")
        self.assertEqual(out["action"], "hold")

    def test_plateau_triggers_five_percent_plus_activity(self):
        self.cut_setup()
        self.weeks_of(MON, [90.0, 89.5, 89.45, 89.4], kcal=2200)
        out = coach.check_in(self.con, d(27))
        self.assertEqual(out["status"], "plateau")
        self.assertEqual(out["adjust_pct"], -5)
        self.assertEqual(out["activity_pct"], 5)
        self.assertEqual(out["proposed_targets"]["protein_g"], 180)
        self.assertLess(out["proposed_targets"]["carb_g"], 220)
        self.assertFalse(out["applied"])
        self.assertEqual(tracker.get_targets(self.con, d(27))["kcal"], 2200)

    def test_plateau_food_only_is_ten_percent(self):
        coach.set_profile(self.con, adjust_with_activity=False)
        self.cut_setup()
        self.weeks_of(MON, [90.0, 89.5, 89.45, 89.4], kcal=2200)
        out = coach.check_in(self.con, d(27))
        self.assertEqual(out["adjust_pct"], -10)
        self.assertIsNone(out["activity_pct"])

    def test_plateau_escalates_after_failed_cut(self):
        self.cut_setup()
        self.weeks_of(MON, [90.0, 89.5, 89.45, 89.4], kcal=2200)
        first = coach.check_in(self.con, d(27), apply=True, effective_date=d(28))
        self.assertTrue(first["applied"])
        # Two more stalled weeks after the cut
        self.weeks_of(d(28), [89.38, 89.36])
        self.eat(d(28), 14, 2100)
        out = coach.check_in(self.con, d(41))
        self.assertEqual(out["status"], "plateau")
        self.assertEqual(out["adjust_pct"], -10)

    def test_plateau_steps_down_after_successful_cut(self):
        self.cut_setup()
        self.weeks_of(MON, [90.0, 89.5, 89.45, 89.4], kcal=2200)
        coach.check_in(self.con, d(27), apply=True, effective_date=d(28))
        # Cut worked for a week, then two stalled weeks
        self.weeks_of(d(28), [88.9, 88.88, 88.86])
        self.eat(d(28), 21, 2100)
        out = coach.check_in(self.con, d(48))
        self.assertEqual(out["status"], "plateau")
        self.assertEqual(out["adjust_pct"], -5)  # max(5, 5 - 2.5)

    def test_adherence_gate_blocks_cut(self):
        self.cut_setup()
        self.weeks_of(MON, [90.0, 89.5])
        self.eat(MON, 14, 2200)
        self.weeks_of(d(14), [89.45, 89.4])
        self.eat(d(14), 5, 2200)  # 9 of 14 days unlogged
        out = coach.check_in(self.con, d(27))
        self.assertEqual(out["action"], "fix_adherence")
        self.assertIsNone(out["proposed_targets"])

    def test_confounders_wait_one_more_week(self):
        self.cut_setup()
        self.weeks_of(MON, [90.0, 89.5, 89.45, 89.4], kcal=2200)
        out = coach.check_in(self.con, d(27), flags={"period": True})
        self.assertEqual(out["status"], "plateau_confounded")

    def test_too_fast_two_weeks_adds_food(self):
        self.cut_setup()
        self.weeks_of(MON, [90.0, 89.0, 88.0, 87.0], kcal=2200)
        out = coach.check_in(self.con, d(27))
        self.assertEqual(out["status"], "too_fast")
        self.assertEqual(out["adjust_pct"], 5)

    def test_checkin_apply_writes_targets(self):
        self.cut_setup()
        self.weeks_of(MON, [90.0, 89.5, 89.45, 89.4], kcal=2200)
        out = coach.check_in(self.con, d(27), apply=True, scores={"hunger": 6})
        self.assertTrue(out["applied"])
        self.assertEqual(tracker.get_targets(self.con, d(27))["kcal"], out["proposed_targets"]["kcal"])
        hist = coach.checkin_history(self.con)
        self.assertEqual(hist[0]["adjust_pct"], -5)
        self.assertEqual(hist[0]["payload"]["scores"], {"hunger": 6})

    def test_scores_validated(self):
        self.cut_setup()
        with self.assertRaises(ValueError):
            coach.check_in(self.con, MON, scores={"mood": 11})

    def test_reverse_adds_when_stable(self):
        tracker.set_targets(self.con, 1800, 180, 150, 50, effective_date=MON)
        coach.start_phase(self.con, "reverse", MON, pace="moderate")
        self.weeks_of(MON, [80.0, 80.05, 80.1], kcal=1800)
        out = coach.check_in(self.con, d(20))
        self.assertEqual(out["status"], "reverse_step")
        self.assertEqual(out["adjust_pct"], 6.0)
        self.assertGreater(out["proposed_targets"]["kcal"], 1800)

    def test_reverse_holds_on_fast_gain(self):
        tracker.set_targets(self.con, 1800, 180, 150, 50, effective_date=MON)
        coach.start_phase(self.con, "reverse", MON, pace="conservative")
        self.weeks_of(MON, [80.0, 80.0, 80.5], kcal=1800)
        out = coach.check_in(self.con, d(20))
        self.assertEqual(out["status"], "gaining_fast")
        self.assertEqual(out["action"], "hold")

    def test_reverse_holds_after_three_rising_weeks(self):
        tracker.set_targets(self.con, 1800, 180, 150, 50, effective_date=MON)
        coach.start_phase(self.con, "reverse", MON, pace="aggressive")
        self.weeks_of(MON, [80.0, 80.1, 80.2, 80.3], kcal=1800)
        out = coach.check_in(self.con, d(27))
        self.assertEqual(out["status"], "rising_streak")

    def test_diet_break_schedule(self):
        tracker.set_targets(self.con, 2200, 180, 220, 60, effective_date=MON)
        coach.start_phase(self.con, "cut", MON, rate_pct=0.6, diet_weeks=2, break_weeks=1,
                          maintenance_kcal=2800)
        self.weeks_of(MON, [90.0, 89.5], kcal=2200)
        out = coach.check_in(self.con, d(13), apply=True, effective_date=d(14))
        self.assertEqual(out["action"], "start_break")
        self.assertGreater(out["proposed_targets"]["kcal"], 2200)
        self.weeks_of(d(14), [89.6])
        self.eat(d(14), 7, out["proposed_targets"]["kcal"])
        mid = coach.check_in(self.con, d(16))
        self.assertEqual(mid["status"], "on_break")
        end = coach.check_in(self.con, d(20), apply=True, effective_date=d(21))
        self.assertEqual(end["action"], "end_break")
        self.assertEqual(tracker.get_targets(self.con, d(21))["kcal"], 2200)

    def test_diet_weeks_capped_at_four(self):
        with self.assertRaises(ValueError):
            coach.start_phase(self.con, "cut", MON, diet_weeks=6, break_weeks=1)

    def test_maintenance_drift(self):
        tracker.set_targets(self.con, 2600, 180, 280, 75, effective_date=MON)
        self.weeks_of(d(-7), [80.0])
        coach.start_phase(self.con, "maintenance", MON)
        self.weeks_of(MON, [81.3, 81.4], kcal=2600)
        out = coach.check_in(self.con, d(13))
        self.assertEqual(out["status"], "drifting_up")
        self.assertEqual(out["adjust_pct"], -5)

    # ── high days / adherence / progress ──
    def test_high_days_book_algorithm(self):
        tracker.set_targets(self.con, 2242, 190, 220, 65, effective_date=MON)
        self.weigh(MON, [100, 100, 100])
        out = coach.plan_high_days(self.con, 2, 2800, d(2))
        self.assertEqual(out["weekly_kcal"], 15694)
        self.assertAlmostEqual(out["low"]["kcal"], 2019, delta=25)
        self.assertEqual(out["high"]["fat_g"], out["low"]["fat_g"])
        self.assertLessEqual(out["high"]["protein_g"], 190)
        self.assertAlmostEqual(out["check_weekly_kcal"], 15694, delta=150)

    def test_high_days_cap(self):
        tracker.set_targets(self.con, 1500, 150, 150, 40, effective_date=MON)
        with self.assertRaises(ValueError):
            coach.plan_high_days(self.con, 2, 3500, MON)

    def test_adherence_weekend_drift(self):
        tracker.set_targets(self.con, 2000, 150, 200, 60, effective_date=MON)
        for i in range(7):
            kcal = 2000 if i < 5 else 3000
            tracker.log_meal(self.con, [{"name": "x", "macros": {"kcal": kcal, "protein_g": 150}}], d(i))
        a = coach.adherence(self.con, MON, d(6))
        self.assertEqual(a["kcal_on_target_days"], 5)
        self.assertEqual(a["weekend_drift_kcal"], 1000)
        self.assertEqual(a["logging_coverage"], 1.0)

    def test_progress_overview(self):
        self.cut_setup()
        self.weeks_of(MON, [90.0, 89.5, 89.0, 88.5], kcal=2200)
        p = coach.progress(self.con, d(27))
        self.assertEqual(p["phase"]["kind"], "cut")
        self.assertEqual(p["phase"]["week_number"], 4)
        self.assertIsNotNone(p["maintenance"]["measured"]["kcal"])
        self.assertIsNotNone(p["maintenance"]["vs_predicted_pct"])
        self.assertEqual(len(p["weeks"]), 12)


if __name__ == "__main__":
    unittest.main()
