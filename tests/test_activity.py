"""Activity import semantics (steps, resting HR, workouts) over temp databases.

All values are synthetic. Times are fixed so window/DST behavior is exact.
"""

import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from macro_engine import activity, coach, db, health_import, tracker

WATCH = "Ryan's Apple Watch"
PHONE = "Ryan's iPhone"
NOW = datetime(2026, 11, 10, 1, 55, tzinfo=timezone.utc)   # 21:55 AST (-04:00), Nov 9
CAP = "2026-11-09T18:00:00-04:00"


def lines(rows):
    return "\n".join(f"{v}\t{t}\t{s}" for v, t, s in rows)


def body(captured=CAP, steps=None, rhr=None, window="normal", **extra):
    out = {"schema_version": 1, "captured_at": captured, "window": window}
    if steps is not None:
        out.update(steps_status="queried", steps_samples=lines(steps))
    if rhr is not None:
        out.update(resting_heart_rate_status="queried", resting_heart_rate_samples=lines(rhr))
    out.update(extra)
    return out


def workout(start="2026-11-08T12:00:00-04:00", end="2026-11-08T12:50:00-04:00",
            wtype="Traditional Strength Training", seconds=2700, basis="recorded", **extra):
    return {"type": wtype, "start_at": start, "end_at": end, "duration_seconds": seconds,
            "duration_basis": basis, **extra}


def wbody(items, captured=CAP, source="Workout", **extra):
    return body(captured, workouts_status="queried", workouts_source=source, workouts=items, **extra)


class ActivityBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "t.db"
        self.con = db.connect(self.path)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def imp(self, payload, now=NOW):
        return activity.import_activity(self.con, payload, now=now)

    def steps(self, date, source=WATCH):
        row = self.con.execute("SELECT steps FROM health_step_days WHERE date=? AND source=?",
                               (date, source)).fetchone()
        return row[0] if row else None


class StepsTest(ActivityBase):
    def test_replay_increase_decrease_stale_and_conflict(self):
        day = [(6000, "2026-11-08T10:00:00-04:00", WATCH)]
        first = self.imp(body(steps=day))
        self.assertEqual(first["steps"]["updated"], 1)
        replay = self.imp(body(steps=day))  # lost response, then replay
        self.assertEqual((replay["steps"]["updated"], replay["steps"]["unchanged"]), (0, 1))
        self.assertEqual(self.steps("2026-11-08"), 6000)
        self.imp(body("2026-11-09T19:00:00-04:00", steps=[(7000, "2026-11-08T10:00:00-04:00", WATCH)]))
        self.assertEqual(self.steps("2026-11-08"), 7000)
        self.imp(body("2026-11-09T20:00:00-04:00", steps=[(5800, "2026-11-08T10:00:00-04:00", WATCH)]))
        self.assertEqual(self.steps("2026-11-08"), 5800)
        stale = self.imp(body("2026-11-09T19:30:00-04:00", steps=[(9000, "2026-11-08T10:00:00-04:00", WATCH)]))
        self.assertEqual(stale["steps"]["stale"], 1)
        self.assertEqual(self.steps("2026-11-08"), 5800)
        with self.assertRaises(activity.ConflictError):
            self.imp(body("2026-11-09T20:00:00-04:00", steps=[(1, "2026-11-08T10:00:00-04:00", WATCH)]))
        self.assertEqual(self.steps("2026-11-08"), 5800)

    def test_conflict_rolls_back_whole_request(self):
        self.imp(body(steps=[(500, "2026-11-08T10:00:00-04:00", WATCH)]))
        mixed = [(100, "2026-11-07T10:00:00-04:00", WATCH), (900, "2026-11-08T10:00:00-04:00", WATCH)]
        with self.assertRaises(activity.ConflictError):
            self.imp(body(steps=mixed))
        self.assertIsNone(self.steps("2026-11-07"))

    def test_overlapping_phone_and_watch_are_not_summed(self):
        rows = [(3000, "2026-11-08T09:00:00-04:00", WATCH), (2900, "2026-11-08T09:00:00-04:00", PHONE),
                (1000, "2026-11-08T15:00:00-04:00", WATCH), (1000, "2026-11-08T15:00:00-04:00", WATCH)]
        res = self.imp(body(steps=rows))
        self.assertEqual(res["steps"]["duplicates"], 1)
        self.assertEqual(self.steps("2026-11-08"), 4000)
        self.assertEqual(self.steps("2026-11-08", PHONE), 2900)
        self.assertEqual(activity.source_setting(self.con)["selected"], WATCH)
        self.assertEqual(activity.source_setting(self.con)["selection"], "auto")
        s = activity.summary(self.con, "2026-11-09", now=NOW)
        day = next(d for d in s["days"] if d["date"] == "2026-11-08")
        self.assertEqual(day["steps"], 4000)
        self.assertEqual(s["source"]["label"], "Watch-recorded steps")

    def test_two_watches_need_a_choice_and_source_change_is_explicit(self):
        res = self.imp(body(steps=[(10, "2026-11-08T09:00:00-04:00", "Old Apple Watch"),
                                   (20, "2026-11-08T09:00:00-04:00", WATCH)]))
        self.assertEqual(res["steps"]["availability"], "source_needed")
        self.assertEqual(res["outcome"], "partial")
        self.assertTrue(res["notice"].startswith("Partial sync"))
        self.assertIsNone(activity.source_setting(self.con)["selected"])
        activity.update_settings(self.con, {"source": WATCH}, now=NOW)
        activity.update_settings(self.con, {"source": "Old Apple Watch"}, now=NOW)
        src = activity.source_setting(self.con)
        self.assertEqual(src["selected"], "Old Apple Watch")
        self.assertEqual([h["source"] for h in src["history"]], [WATCH, "Old Apple Watch"])
        day = next(d for d in activity.summary(self.con, "2026-11-09", now=NOW)["days"]
                   if d["date"] == "2026-11-08")
        self.assertEqual(day["steps"], 10)  # one source only, never merged
        with self.assertRaisesRegex(ValueError, "already received"):
            activity.update_settings(self.con, {"source": "Unknown"}, now=NOW)

    def test_observed_zero_versus_unknown_day(self):
        self.imp(body(steps=[(0, "2026-11-07T10:00:00-04:00", WATCH)]))
        s = activity.summary(self.con, "2026-11-09", now=NOW)
        days = {d["date"]: d for d in s["days"]}
        self.assertEqual(days["2026-11-07"]["steps"], 0)
        self.assertIsNone(days["2026-11-06"]["steps"])
        self.assertTrue(days["2026-11-06"]["steps_queried"])   # accepted query, no data
        self.assertFalse(days["2026-10-30"]["steps_queried"])  # never queried

    def test_samples_are_assigned_to_their_local_start_date(self):
        rows = [(50, "2026-11-07T23:58:00-04:00", WATCH),     # spans midnight -> Nov 7 only
                (5, "2026-11-08T00:00:00-04:00", WATCH),      # exact midnight -> Nov 8
                (7, "2026-11-09T02:30:00Z", WATCH),           # UTC input -> Nov 8 22:30 local
                (9, "2026-11-01T10:00:00-04:00", WATCH)]      # before the 8-day window
        res = self.imp(body(steps=rows))
        self.assertEqual(self.steps("2026-11-07"), 50)
        self.assertEqual(self.steps("2026-11-08"), 12)
        self.assertEqual(res["steps"]["ignored_outside_window"], 1)
        self.assertIsNone(self.steps("2026-11-01"))

    def test_eight_calendar_day_window_and_today_so_far(self):
        res = self.imp(body(steps=[(1200, "2026-11-09T08:00:00-04:00", WATCH),
                                   (8000, "2026-11-04T08:00:00-04:00", WATCH),
                                   (6000, "2026-11-03T08:00:00-04:00", WATCH)]))
        self.assertEqual((res["window"]["start_date"], res["window"]["end_date"]), ("2026-11-02", "2026-11-09"))
        self.assertEqual(res["window"]["start"], "2026-11-02T00:00:00-04:00")
        self.assertEqual(res["window"]["end"], "2026-11-10T00:00:00-04:00")
        covered = self.con.execute("SELECT COUNT(*) FROM health_activity_coverage WHERE category='steps' "
                                   "AND queried_ts IS NOT NULL").fetchone()[0]
        self.assertEqual(covered, 8)
        s = activity.summary(self.con, "2026-11-09", now=NOW)
        self.assertEqual(s["today_steps"]["value"], 1200)
        self.assertTrue(s["today_steps"]["so_far"])
        week = s["weeks"][-1]   # Mon Nov 9 week: today only, no closed days
        self.assertEqual((week["closed_days"], week["steps_observed_days"], week["steps_avg"]), (0, 0, None))
        prev = s["weeks"][-2]   # Nov 2-8
        self.assertEqual((prev["closed_days"], prev["steps_observed_days"], prev["steps_avg"]), (7, 2, 7000))

    def test_dst_days_and_window_offsets(self):
        fall_now = datetime(2026, 11, 2, 16, 0, tzinfo=timezone.utc)
        res = self.imp(body("2026-11-02T12:00:00-04:00", steps=[
            (1, "2026-11-01T01:30:00-03:00", WATCH), (2, "2026-11-01T01:30:00-04:00", WATCH),
            (4, "2026-11-01T23:30:00-04:00", WATCH)]), now=fall_now)
        self.assertEqual(res["window"]["start"], "2026-10-26T00:00:00-03:00")
        self.assertEqual(res["window"]["end"], "2026-11-03T00:00:00-04:00")
        self.assertEqual(self.steps("2026-11-01"), 7)   # 25-hour day
        spring_now = datetime(2026, 3, 9, 16, 0, tzinfo=timezone.utc)
        self.imp(body("2026-03-09T13:00:00-03:00", steps=[
            (3, "2026-03-08T01:30:00-04:00", WATCH), (5, "2026-03-08T23:30:00-03:00", WATCH)]), now=spring_now)
        self.assertEqual(self.steps("2026-03-08"), 8)   # 23-hour day

    def test_configured_timezone_and_mismatch_warning(self):
        activity.update_settings(self.con, {"timezone": "America/Vancouver"}, now=NOW)
        res = self.imp(body(steps=[(5, "2026-11-08T23:30:00-04:00", WATCH)]))
        self.assertIn("timezone_mismatch", res["warnings"])
        self.assertEqual(res["outcome"], "partial")
        self.assertEqual(self.steps("2026-11-08"), 5)   # 19:30 in Vancouver
        with self.assertRaisesRegex(ValueError, "unknown timezone"):
            activity.update_settings(self.con, {"timezone": "Mars/Base"}, now=NOW)


class RestingHeartRateTest(ActivityBase):
    def test_latest_per_day_revisions_and_optional(self):
        self.imp(body(steps=[], rhr=[(62, "2026-11-08T08:00:00-04:00", WATCH),
                                     (60, "2026-11-08T16:00:00-04:00", WATCH)]))
        row = self.con.execute("SELECT * FROM health_rhr_days").fetchone()
        self.assertEqual((row["bpm"], row["sample_at"]), (60, "2026-11-08T16:00:00-04:00"))
        # Apple revised the estimate: same sample time, newer capture replaces it.
        self.imp(body("2026-11-09T19:00:00-04:00", rhr=[(61, "2026-11-08T16:00:00-04:00", WATCH)]))
        self.assertEqual(self.con.execute("SELECT bpm FROM health_rhr_days").fetchone()[0], 61)
        # Later estimate removed: the newer capture's remaining reading wins.
        self.imp(body("2026-11-09T20:00:00-04:00", rhr=[(63, "2026-11-08T08:00:00-04:00", WATCH)]))
        self.assertEqual(self.con.execute("SELECT bpm FROM health_rhr_days").fetchone()[0], 63)
        # No RHR samples, omitted RHR, unsupported RHR: prior value kept and dated.
        self.imp(body("2026-11-09T21:00:00-04:00", rhr=[]))
        self.imp(body("2026-11-09T21:30:00-04:00", steps=[]))
        res = self.imp(body("2026-11-09T21:40:00-04:00", steps=[], resting_heart_rate_status="unsupported"))
        self.assertEqual(res["resting_heart_rate"]["availability"], "unsupported")
        row = self.con.execute("SELECT * FROM health_rhr_days").fetchone()
        self.assertEqual((row["bpm"], row["captured_at"]), (63, "2026-11-09T20:00:00-04:00"))

    def test_rhr_units_and_omission_do_not_advance_version(self):
        with self.assertRaisesRegex(ValueError, "out of range"):
            self.imp(body(rhr=[(1.1, "2026-11-08T08:00:00-04:00", WATCH)]))
        res = self.imp(body(steps=[(5, "2026-11-08T08:00:00-04:00", WATCH)]))
        self.assertEqual(res["resting_heart_rate"], {"availability": "not_sent"})
        fresh = activity.summary(self.con, "2026-11-09", now=NOW)["freshness"]["resting_heart_rate"]
        self.assertIsNone(fresh["last_query_captured_at"])


class WorkoutsTest(ActivityBase):
    def count(self):
        return self.con.execute("SELECT COUNT(*) FROM health_workouts").fetchone()[0]

    def test_retry_correction_distinct_and_cross_midnight(self):
        self.imp(wbody([workout()]))
        self.imp(wbody([workout()]))
        self.assertEqual(self.count(), 1)
        self.imp(wbody([workout(seconds=2600)], captured="2026-11-09T19:00:00-04:00"))
        self.assertEqual(self.con.execute("SELECT duration_seconds FROM health_workouts").fetchone()[0], 2600)
        self.imp(wbody([workout(start="2026-11-08T18:00:00-04:00", end="2026-11-08T18:40:00-04:00"),
                        workout(start="2026-11-07T23:40:00-04:00", end="2026-11-08T00:30:00-04:00",
                                wtype="Walking", seconds=3000, basis="elapsed"),
                        workout(start="2026-11-06T07:00:00-04:00", end="2026-11-06T07:30:00-04:00",
                                wtype="Pickleball", seconds=1800)],
                       captured="2026-11-09T20:00:00-04:00"))
        self.assertEqual(self.count(), 4)
        rows = {r["type"]: dict(r) for r in self.con.execute("SELECT * FROM health_workouts")}
        self.assertEqual(rows["Walking"]["local_date"], "2026-11-07")
        self.assertEqual((rows["Walking"]["category"], rows["Walking"]["duration_basis"]), ("cardio", "elapsed"))
        self.assertEqual(rows["Pickleball"]["category"], "other")
        self.assertEqual(rows["Traditional Strength Training"]["identity_basis"], "derived")
        # A later query without these workouts never deletes them.
        self.imp(wbody([], captured="2026-11-09T21:00:00-04:00"))
        self.assertEqual(self.count(), 4)
        week = activity.summary(self.con, "2026-11-09", now=NOW)["weeks"][-2]
        self.assertEqual(week["workouts"]["strength"], {"count": 2, "minutes": 88})
        self.assertEqual(week["workouts"]["cardio"]["count"], 1)
        self.assertEqual(week["duration_bases"], ["elapsed", "recorded"])

    def test_collisions_and_reserved_manual_source(self):
        with self.assertRaises(activity.ConflictError):
            self.imp(wbody([workout(), workout(seconds=100)]))
        self.assertEqual(self.count(), 0)
        with self.assertRaisesRegex(ValueError, "reserved"):
            self.imp(wbody([workout()], source="manual"))
        for bad in (workout(seconds=0), workout(seconds=True), workout(end="2026-11-08T11:00:00-04:00"),
                    workout(basis="paused"), {**workout(), "calories": 300}):
            with self.assertRaises(ValueError):
                self.imp(wbody([bad]))

    def test_external_ids_preferred(self):
        self.imp(wbody([workout(external_id="abc")]))
        self.imp(wbody([workout(external_id="abc", start="2026-11-08T12:01:00-04:00")],
                       captured="2026-11-09T19:00:00-04:00"))
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.con.execute("SELECT identity_basis FROM health_workouts").fetchone()[0], "external")

    def test_manual_fallback_crud(self):
        made = activity.add_manual_workout(self.con, {"date": "2026-11-08", "type": "Traditional Strength Training",
                                                      "duration_minutes": 45, "start_time": "06:30"}, now=NOW)
        self.assertEqual((made["source"], made["identity_basis"], made["category"]), ("manual", "manual", "strength"))
        self.assertEqual(made["start_at"], "2026-11-08T06:30:00-04:00")
        edited = activity.update_manual_workout(self.con, made["id"], {"duration_minutes": 50}, now=NOW)
        self.assertEqual((edited["duration_seconds"], edited["start_at"]), (3000, made["start_at"]))
        self.imp(wbody([workout()]))
        imported = self.con.execute("SELECT id FROM health_workouts WHERE source != 'manual'").fetchone()[0]
        with self.assertRaisesRegex(ValueError, "imported"):
            activity.update_manual_workout(self.con, imported, {"duration_minutes": 5})
        with self.assertRaisesRegex(ValueError, "imported"):
            activity.delete_manual_workout(self.con, imported)
        self.assertTrue(activity.delete_manual_workout(self.con, made["id"]))
        self.assertFalse(activity.delete_manual_workout(self.con, made["id"]))
        with self.assertRaises(ValueError):
            activity.add_manual_workout(self.con, {"date": "2026-11-08", "type": "Run", "duration_minutes": 0})


class ValidationTest(ActivityBase):
    def test_strict_schema(self):
        bad = [
            {**body(steps=[]), "schema_version": True},
            {**body(steps=[]), "schema_version": 2},
            {**body(steps=[]), "extra": 1},
            body(window="weekly", steps=[]),
            body(captured="2026-11-09T18:00:00", steps=[]),
            body(captured="2026-11-10T18:00:00-04:00", steps=[]),
            body(steps=[(-1, "2026-11-08T10:00:00-04:00", WATCH)]),
            body(steps=[(1.5, "2026-11-08T10:00:00-04:00", WATCH)]),
            body(steps=[("1,5", "2026-11-08T10:00:00-04:00", WATCH)]),
            body(steps=[("NaN", "2026-11-08T10:00:00-04:00", WATCH)]),
            body(steps=[(5, "2026-11-09T23:00:00-04:00", WATCH)]),   # after captured_at
            body(steps=[(5, "2026-11-08T10:00:00-04:00", "")]),
            {**body(), "steps_samples": "1\t2026-11-08T10:00:00-04:00\tW"},   # samples without status
            {**body(), "steps_status": "observed"},
            {**body(), "steps_status": "unavailable", "steps_samples": ""},
            {**body(), "workouts_status": "queried", "workouts_source": "W", "workouts": [workout()] * 201},
        ]
        for payload in bad:
            with self.assertRaises(ValueError, msg=json.dumps(payload)[:200]):
                self.imp(payload)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM health_activity_coverage").fetchone()[0], 0)

    def test_grouping_commas_and_line_limits(self):
        self.imp(body(steps=[("1,234", "2026-11-08T10:00:00-04:00", WATCH)]))
        self.assertEqual(self.steps("2026-11-08"), 1234)
        too_many = "\n".join(["1\t2026-11-08T10:00:00-04:00\tW"] * (activity.MAX_LINES + 1))
        with self.assertRaisesRegex(ValueError, "too many"):
            self.imp({**body(), "steps_status": "queried", "steps_samples": too_many})

    def test_valid_empty_upload_reports_no_new_readings(self):
        res = self.imp(body(steps=[], rhr=[], workouts_status="unsupported"))
        self.assertEqual(res["outcome"], "no_new_readings")
        self.assertEqual(res["notice"], "No new readings")
        self.assertEqual(res["steps"]["availability"], "no_samples")
        self.assertEqual(res["workouts"], {"availability": "unsupported"})


class CatchUpTest(ActivityBase):
    def at(self, day, hour=18):
        return datetime.fromisoformat(f"{day}T{hour:02d}:00:00-04:00").astimezone(timezone.utc)

    def test_gap_triggers_bounded_catch_up_and_resumes(self):
        self.imp(body("2026-10-20T18:00:00-04:00", steps=[]), now=self.at("2026-10-20"))
        status = activity.sync_status(self.con, NOW)
        self.assertEqual(status["mode"], "catch_up")
        self.assertEqual(status["window_days"], 31)
        self.assertEqual(status["catch_up"], [{"start": "2026-10-21", "end": "2026-11-01", "days": 12}])
        # A normal run doesn't clear the gap, and says so.
        res = self.imp(body(steps=[]))
        self.assertEqual(res["outcome"], "partial")
        self.assertEqual(res["next_mode"], "catch_up")
        # Unavailable attempts are not acknowledgements.
        self.imp(body(window="catch_up", steps_status="unavailable"))
        self.assertEqual(activity.sync_status(self.con, NOW)["mode"], "catch_up")
        res = self.imp(body(window="catch_up", steps=[], workouts_status="unsupported"))
        self.assertEqual(res["window"]["start_date"], "2026-10-10")
        self.assertEqual(activity.sync_status(self.con, NOW)["mode"], "normal")
        self.assertEqual(res["outcome"], "no_new_readings")

    def test_backlog_beyond_limit_is_reported_and_first_run_is_normal(self):
        self.assertEqual(activity.sync_status(self.con, NOW)["mode"], "normal")
        self.imp(body("2026-09-20T18:00:00-04:00", steps=[]), now=self.at("2026-09-20"))
        status = activity.sync_status(self.con, NOW)
        self.assertEqual(status["backlog_beyond_limit"][0]["start"], "2026-09-21")
        self.assertEqual(status["backlog_beyond_limit"][0]["end"], "2026-10-09")
        self.assertIsNotNone(status["backlog_note"])


class IsolationTest(ActivityBase):
    def test_activity_never_touches_body_food_targets_or_checkins(self):
        coach.log_metrics(self.con, "2026-11-08", weight=80, unit="kg")
        tracker.set_targets(self.con, 2000, 150, 200, 60, None, "2026-11-01")
        coach.check_in(self.con, "2026-11-08")
        tables = ["body_metrics", "targets", "checkins", "phases", "log", "health_import_metrics"]
        before = {t: [tuple(r) for r in self.con.execute(f"SELECT * FROM {t}")] for t in tables}
        profile = coach.get_profile(self.con)
        self.imp(body(steps=[(5000, "2026-11-08T10:00:00-04:00", WATCH)],
                      rhr=[(60, "2026-11-08T10:00:00-04:00", WATCH)]))
        self.imp(wbody([workout()], captured="2026-11-09T19:00:00-04:00"))
        activity.summary(self.con, "2026-11-09", now=NOW)
        coach.progress(self.con, "2026-11-09")
        after = {t: [tuple(r) for r in self.con.execute(f"SELECT * FROM {t}")] for t in tables}
        self.assertEqual(before, after)
        self.assertEqual(profile, coach.get_profile(self.con))

    def test_new_checkin_includes_activity_even_when_collecting(self):
        tracker.set_targets(self.con, 2000, 150, 200, 60, None, "2026-11-01")
        self.imp(body(steps=[(5000, "2026-11-08T10:00:00-04:00", WATCH)]), now=NOW)
        out = coach.check_in(self.con, "2026-11-09")
        self.assertEqual(out["status"], "collecting")
        self.assertEqual(out["activity"]["source"]["label"], "Watch-recorded steps")
        saved = coach.checkin_history(self.con, 1)[0]["payload"]["activity"]
        self.assertEqual(saved["weeks"], json.loads(json.dumps(out["activity"]["weeks"])))
        before = coach.checkin_history(self.con, 1)[0]["payload"]
        self.imp(body("2026-11-09T19:00:00-04:00", steps=[(9000, "2026-11-08T10:00:00-04:00", WATCH)]))
        self.assertEqual(coach.checkin_history(self.con, 1)[0]["payload"], before)   # no backfill

    def test_body_and_activity_are_separate_transactions(self):
        health_import.import_metrics(self.con, {"metric": "weight", "value": 80, "unit": "kg",
                                                "date": "2026-11-08T07:00:00-04:00"})
        with self.assertRaises(ValueError):
            self.imp(body(steps=[(-5, "2026-11-08T10:00:00-04:00", WATCH)]))
        self.assertEqual(self.con.execute("SELECT weight_kg FROM body_metrics").fetchone()[0], 80)
        self.assertIsNone(activity.summary(self.con, "2026-11-09", now=NOW)["freshness"]["steps"]
                          ["last_query_captured_at"])

    def test_concurrent_runs_keep_newest_capture(self):
        def run(captured, value):
            con = db.connect(self.path)
            try:
                activity.import_activity(con, body(captured, steps=[(value, "2026-11-08T10:00:00-04:00", WATCH)]),
                                         now=NOW)
            finally:
                con.close()
        threads = [threading.Thread(target=run, args=(f"2026-11-09T1{i}:00:00-04:00", 1000 + i))
                   for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(self.steps("2026-11-08"), 1005)

    def test_legacy_database_migrates(self):
        legacy = Path(self.tmp.name) / "legacy.db"
        raw = sqlite3.connect(legacy)
        raw.executescript("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
                          "CREATE TABLE body_metrics (date TEXT PRIMARY KEY, weight_kg REAL, body_fat_pct REAL,"
                          " waist_cm REAL, hips_cm REAL, note TEXT, updated_at TEXT NOT NULL DEFAULT '');")
        raw.close()
        con = db.connect(legacy)
        try:
            names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"health_step_days", "health_rhr_days", "health_workouts",
                             "health_activity_coverage"} <= names)
            empty = activity.summary(con, "2026-11-09", now=NOW)
            self.assertIn("No activity data received yet.", empty["notes"])
            db.connect(legacy).close()   # idempotent
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
