"""HTTP API over a temp database. Never touches the real macros.db."""

import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from macro_engine import db, http_api, tracker


def seed(con):
    foods = [
        ("Bananas, raw", "fdc_sr_legacy", "1", 89, 1.1, 22.8, 0.3, 2.6),
        ("Egg, whole, raw, fresh", "fdc_sr_legacy", "2", 143, 12.6, 0.7, 9.5, 0),
        ("Peanut butter, smooth style", "fdc_sr_legacy", "3", 598, 22.2, 22.3, 51.1, 5),
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
        (ids["Bananas"],),
    )
    con.execute(
        "INSERT INTO portions (food_id, label, grams) VALUES (?, '1 large', 50)",
        (ids["Egg"],),
    )
    db.rebuild_fts(con)
    con.commit()
    return ids


class HttpApiTest(unittest.TestCase):
    def setUp(self):
        os.environ["MACRO_ENGINE_HTTP_QUIET"] = "1"
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "test.db")
        os.environ["MACRO_ENGINE_DB"] = self.db_path
        con = db.connect(self.db_path)
        self.ids = seed(con)
        con.close()
        self.httpd = http_api.make_server("127.0.0.1", 0)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address
        self.base = f"http://{host}:{port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        os.environ.pop("MACRO_ENGINE_DB", None)
        os.environ.pop("MACRO_ENGINE_HTTP_QUIET", None)
        self.tmp.cleanup()

    def req(self, method, path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request) as res:
                raw = res.read()
                payload = json.loads(raw) if raw else None
                return res.status, payload, res.headers
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            payload = json.loads(raw) if raw else None
            return exc.code, payload, exc.headers

    def test_health(self):
        status, body, _ = self.req("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True})

    def test_profile_captures_birth_date_training_and_loss_goal(self):
        status, profile, _ = self.req("POST", "/api/profile", {
            "sex": "male", "birth_date": "1981-11-10", "height_in": 72,
            "daily_activity": "seated", "activity": "moderate", "resistance_training": True,
            "strength_days_per_week": 6, "strength_minutes_per_session": 45,
            "goal": "cut", "goal_loss_lb": 100, "units": "lb",
        })
        self.assertEqual(status, 200)
        _, saved, _ = self.req("GET", "/api/profile")
        self.assertEqual(saved, profile)
        _, progress, _ = self.req("GET", "/api/progress?date=2026-11-09")
        self.assertEqual(progress["body"]["age"], 44)
        self.assertIsNone(progress["targets"])
        self.assertIsNone(progress["phase"])
        self.assertEqual(progress["profile"]["strength_days_per_week"], 6)
        self.assertEqual(self.req("POST", "/api/profile", {"strength_days_per_week": 8})[0], 400)

    def test_apple_health_import_auth_and_progress(self):
        path = "/api/import/apple-health"
        body = {"data": {"metrics": [{"name": "weight", "units": "kg", "data": [
            {"date": "2026-11-09 07:30:00 -0400", "qty": 80}]}]}}
        with patch.dict(os.environ, {"MACRO_ENGINE_HEALTH_TOKEN": ""}):
            self.assertEqual(self.req("POST", path, body)[0], 503)
        with patch.dict(os.environ, {"MACRO_ENGINE_HEALTH_TOKEN": "test-import-token"}):
            self.assertEqual(self.req("POST", path, body)[0], 401)
            self.assertEqual(self.req("POST", path, body, {"Authorization": "Bearer wrong"})[0], 401)
            headers = {"Authorization": "Bearer test-import-token"}
            self.assertEqual(self.req("POST", path, {}, headers)[0], 400)
            status, result, _ = self.req("POST", path, body, headers)
            self.assertEqual(status, 200)
            self.assertEqual(result["updated_fields"], 1)
            self.assertEqual(self.req("POST", path, body, headers)[1]["skipped_fields"], 1)
            _, progress, _ = self.req("GET", "/api/progress?date=2026-11-09")
            self.assertEqual(progress["body"]["weight_kg"], 80)
            self.assertIsNotNone(progress["apple_health_sync"]["last_sync_at"])
            shortcut = {"metric": "body_fat_percentage", "value": 22.5, "unit": "%",
                        "date": "2026-11-09T07:30:00-04:00"}
            status, result, _ = self.req("POST", path, shortcut, headers)
            self.assertEqual(status, 200)
            self.assertEqual(result["updated_fields"], 1)

    def test_activity_import_auth_limits_status_and_mcp_agreement(self):
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("America/Halifax")).replace(microsecond=0)
        yesterday = now - timedelta(days=1)
        path = "/api/import/apple-health/activity"
        body = {"schema_version": 1, "captured_at": now.isoformat(), "window": "normal",
                "steps_status": "queried",
                "steps_samples": f"4000\t{yesterday.isoformat()}\tRyan's Apple Watch\n"
                                 f"3900\t{yesterday.isoformat()}\tRyan's iPhone",
                "resting_heart_rate_status": "queried", "resting_heart_rate_samples": "",
                "workouts_status": "unsupported"}
        with patch.dict(os.environ, {"MACRO_ENGINE_HEALTH_TOKEN": ""}):
            self.assertEqual(self.req("POST", path, body)[0], 503)
        with patch.dict(os.environ, {"MACRO_ENGINE_HEALTH_TOKEN": "test-import-token"}):
            self.assertEqual(self.req("POST", path, body)[0], 401)
            self.assertEqual(self.req("GET", path + "/status")[0], 401)
            self.assertEqual(self.req("GET", path + "/status", None, {"Authorization": "Bearer x"})[0], 401)
            headers = {"Authorization": "Bearer test-import-token"}
            status, sync, _ = self.req("GET", path + "/status", None, headers)
            self.assertEqual((status, sync["mode"]), (200, "normal"))
            self.assertEqual(self.req("POST", path, {**body, "surprise": 1}, headers)[0], 400)
            status, result, _ = self.req("POST", path, body, headers)
            self.assertEqual(status, 200, result)
            self.assertEqual(result["notice"], "Updated")
            self.assertNotIn("test-import-token", json.dumps(result))
            replay = self.req("POST", path, body, headers)[1]
            self.assertEqual(replay["notice"], "No new readings")
            changed = {**body, "steps_samples": f"1\t{yesterday.isoformat()}\tRyan's Apple Watch"}
            self.assertEqual(self.req("POST", path, changed, headers)[0], 409)
            # Oversized bodies fail cleanly before parsing.
            big = {**body, "steps_samples": "x" * 4_100_000}
            self.assertEqual(self.req("POST", path, big, headers)[0], 400)
            self.assertEqual(self.req("GET", path + "/status", None, headers)[1]["last_request"]["outcome"],
                             "no_new_readings")
        _, progress, _ = self.req("GET", "/api/progress")
        act = progress["activity"]
        self.assertEqual(act["source"]["selected"], "Ryan's Apple Watch")
        day = next(d for d in act["days"] if d["date"] == yesterday.date().isoformat())
        self.assertEqual(day["steps"], 4000)
        import mcp_server
        self.assertEqual(mcp_server.progress()["activity"], act)
        status, created, _ = self.req("POST", "/api/workouts", {
            "date": yesterday.date().isoformat(), "type": "Traditional Strength Training",
            "duration_minutes": 45})
        self.assertEqual((status, created["source"]), (200, "manual"))
        status, edited, _ = self.req("POST", f"/api/workouts/{created['id']}", {"note": "legs"})
        self.assertEqual((status, edited["note"]), (200, "legs"))
        self.assertEqual(self.req("POST", "/api/workouts/9999", {"note": "x"})[0], 404)
        self.assertEqual(self.req("DELETE", f"/api/workouts/{created['id']}")[0], 200)
        self.assertEqual(self.req("DELETE", f"/api/workouts/{created['id']}")[0], 404)
        status, settings, _ = self.req("POST", "/api/activity/settings", {"source": "Ryan's iPhone"})
        self.assertEqual((status, settings["source"]["selected"]), (200, "Ryan's iPhone"))
        self.assertEqual(self.req("POST", "/api/activity/settings", {"source": "Nope"})[0], 400)

    def test_empty_day_has_no_targets(self):
        status, body, _ = self.req("GET", "/api/day")
        self.assertEqual(status, 200)
        self.assertEqual(body["entries"], [])
        self.assertIsNone(body["targets"])
        self.assertIsNone(body["remaining"])
        self.assertIn("note", body)

    def test_set_targets_then_remaining(self):
        status, saved, _ = self.req("POST", "/api/targets", {
            "kcal": 2000, "protein_g": 150, "carb_g": 200, "fat_g": 60, "fiber_g": 30,
        })
        self.assertEqual(status, 200)
        self.assertEqual(saved["kcal"], 2000)
        status, day, _ = self.req("GET", "/api/day")
        self.assertEqual(day["remaining"]["kcal"], 2000)
        self.assertEqual(day["targets"]["protein_g"], 150)

    def test_search_and_log_and_delete(self):
        status, foods, _ = self.req("GET", "/api/foods?q=egg")
        self.assertEqual(status, 200)
        self.assertEqual(foods[0]["name"], "Egg, whole, raw, fresh")
        self.assertTrue(foods[0]["portions"])

        status, meal, _ = self.req("POST", "/api/meals", {
            "items": [{"food_id": self.ids["Egg"], "qty": 2, "unit": "large"}],
            "raw_text": "two eggs",
            "meal": "breakfast",
        })
        self.assertEqual(status, 200)
        self.assertEqual(len(meal["logged"]), 1)
        self.assertEqual(meal["problems"], [])
        self.assertEqual(meal["logged"][0]["kcal"], 143.0)
        self.assertEqual(meal["logged"][0]["meal"], "breakfast")
        log_id = meal["logged"][0]["log_id"]

        status, day, _ = self.req("GET", "/api/day")
        self.assertEqual(len(day["entries"]), 1)
        self.assertEqual(day["entries"][0]["name"], "Egg, whole, raw, fresh")
        self.assertIs(day["entries"][0]["estimated"], False)
        self.assertEqual(day["entries"][0]["meal"], "breakfast")

        status, moved, _ = self.req("POST", f"/api/log/{log_id}", {"meal": "dinner"})
        self.assertEqual(status, 200)
        self.assertEqual(moved["meal"], "dinner")
        status, bad, _ = self.req("POST", "/api/meals", {
            "items": [{"food_id": self.ids["Egg"], "qty": 1}],
            "meal": "brunch",
        })
        self.assertEqual(status, 400)

        status, deleted, _ = self.req("DELETE", f"/api/log/{log_id}")
        self.assertEqual(status, 200)
        self.assertTrue(deleted["deleted"])
        _, day, _ = self.req("GET", "/api/day")
        self.assertEqual(day["entries"], [])

    def test_duplicate_and_change_amount(self):
        _, meal, _ = self.req("POST", "/api/meals", {
            "items": [{"food_id": self.ids["Egg"], "qty": 2, "unit": "large"}],
            "meal": "breakfast",
        })
        log_id = meal["logged"][0]["log_id"]

        status, copy, _ = self.req("POST", f"/api/log/{log_id}/duplicate")
        self.assertEqual(status, 200)
        self.assertNotEqual(copy["log_id"], log_id)
        self.assertEqual((copy["kcal"], copy["meal"]), (143.0, "breakfast"))

        status, entry, _ = self.req("POST", f"/api/log/{copy['log_id']}", {"scale": 1.5})
        self.assertEqual(status, 200)
        self.assertEqual(entry["qty_desc"], "3 x 1 large")
        self.assertEqual(entry["grams"], 150)
        self.assertEqual(entry["kcal"], 214.5)

        status, entry, _ = self.req("POST", f"/api/log/{log_id}", {"grams": 50, "meal": "lunch"})
        self.assertEqual(status, 200)
        self.assertEqual((entry["kcal"], entry["meal"]), (71.5, "lunch"))

        _, day, _ = self.req("GET", "/api/day")
        self.assertEqual(len(day["entries"]), 2)
        self.assertEqual(day["eaten"]["kcal"], 286.0)

        # A bad meal must not half-apply the amount change.
        status, _, _ = self.req("POST", f"/api/log/{log_id}", {"grams": 500, "meal": "brunch"})
        self.assertEqual(status, 400)
        for body in ({"scale": 0}, {"grams": "lots"}, {"grams": 10, "scale": 2}, {"note": "x"}):
            status, _, _ = self.req("POST", f"/api/log/{log_id}", body)
            self.assertEqual(status, 400)
        _, day, _ = self.req("GET", "/api/day")
        self.assertEqual(day["eaten"]["kcal"], 286.0)

        self.assertEqual(self.req("POST", "/api/log/999/duplicate")[0], 404)
        self.assertEqual(self.req("POST", "/api/log/999", {"scale": 2})[0], 404)

    def test_unknown_unit_is_a_problem_not_an_error(self):
        status, meal, _ = self.req("POST", "/api/meals", {
            "items": [{"food_id": self.ids["Bananas"], "qty": 1, "unit": "bucket"}],
        })
        self.assertEqual(status, 200)
        self.assertEqual(meal["logged"], [])
        self.assertIn("available_portions", meal["problems"][0])

    def test_week_start_setting_regroups_the_week(self):
        status, body, _ = self.req("POST", "/api/settings", {"week_starts_on": "sunday"})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"week_starts_on": "sunday"})
        status, week, _ = self.req("GET", "/api/week?date=2026-07-08")
        self.assertEqual(status, 200)
        self.assertEqual(week["start"], "2026-07-05")
        self.assertEqual(week["end"], "2026-07-11")
        self.assertEqual(week["week_starts_on"], "sunday")
        status, bad, _ = self.req("POST", "/api/settings", {"week_starts_on": "brunch"})
        self.assertEqual(status, 400)
        self.assertIn("error", bad)

    def test_month_route_matches_tracker(self):
        status, body, _ = self.req("GET", "/api/month?date=2026-10-12")
        self.assertEqual(status, 200)
        con = db.connect(self.db_path)
        try:
            expected = tracker.month_summary(con, "2026-10-12")
        finally:
            con.close()
        self.assertEqual(body, expected)
        self.assertEqual(body["start"], "2026-10-01")
        self.assertGreaterEqual(len(body["weeks"]), 4)
        status, bad, _ = self.req("GET", "/api/month?date=tomorrow")
        self.assertEqual(status, 400)
        self.assertIn("error", bad)

    def test_week_route_matches_tracker(self):
        status, _, _ = self.req("POST", "/api/targets", {
            "kcal": 2000, "protein_g": 150, "carb_g": 200, "fat_g": 60,
            "fiber_g": None, "effective_date": "2026-07-06",
        })
        self.assertEqual(status, 200)
        status, _, _ = self.req("POST", "/api/meals", {
            "date": "2026-07-06",
            "items": [{"name": "plate", "macros": {
                "kcal": 1500, "protein_g": 100, "carb_g": 150, "fat_g": 40,
            }}],
        })
        self.assertEqual(status, 200)
        status, body, _ = self.req("GET", "/api/week?date=2026-07-08")
        self.assertEqual(status, 200)
        con = db.connect(self.db_path)
        try:
            expected = tracker.week_summary(con, "2026-07-08")
        finally:
            con.close()
        self.assertEqual(body, expected)
        self.assertEqual(body["start"], "2026-07-06")
        self.assertEqual(body["days"][0]["eaten"]["carb_g"], 150)
        status, bad, _ = self.req("GET", "/api/week?date=tomorrow")
        self.assertEqual(status, 400)
        self.assertIn("error", bad)

    def test_bad_date_and_bad_json(self):
        status, body, _ = self.req("GET", "/api/day?date=tomorrow")
        self.assertEqual(status, 400)
        self.assertIn("date", body["error"])

        request = urllib.request.Request(
            self.base + "/api/meals", data=b"not-json", method="POST",
        )
        request.add_header("Content-Type", "application/json")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 400)

    def test_add_food_and_alias_round_trip(self):
        status, food, _ = self.req("POST", "/api/foods", {
            "name": "Costco chicken burger",
            "kcal": 210, "protein_g": 16, "carb_g": 12, "fat_g": 10,
            "store": "Costco",
            "portion_label": "1 burger",
            "portion_grams": 113,
            "macros_are_per_portion": True,
            "alias": "chicken burger",
        })
        self.assertEqual(status, 200)
        self.assertEqual(food["store"], "Costco")
        status, detail, _ = self.req("GET", f"/api/foods/{food['food_id']}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["portions"][0]["label"], "1 burger")
        status, hits, _ = self.req("GET", "/api/foods?q=chicken%20burger")
        self.assertTrue(hits[0]["matched_alias"])

    def test_missing_log_and_unknown_route(self):
        status, body, _ = self.req("DELETE", "/api/log/999")
        self.assertEqual(status, 404)
        status, body, headers = self.req(
            "GET", "/api/nope", headers={"Origin": "http://127.0.0.1:5173"},
        )
        self.assertEqual(status, 404)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "http://127.0.0.1:5173")

    def test_options_preflight(self):
        status, body, headers = self.req(
            "OPTIONS", "/api/meals", headers={"Origin": "http://localhost:5173"},
        )
        self.assertEqual(status, 204)
        self.assertIsNone(body)
        self.assertIn("POST", headers["Access-Control-Allow-Methods"])

    # ── coaching endpoints ──
    def test_coaching_flow(self):
        status, prof, _ = self.req("POST", "/api/profile",
                                   {"sex": "male", "age": 35, "height_cm": 180})
        self.assertEqual(status, 200)
        self.assertEqual(prof["sex"], "male")
        status, body, _ = self.req("POST", "/api/profile", {"nope": 1})
        self.assertEqual(status, 400)
        for i, day in enumerate(("2026-09-07", "2026-09-08", "2026-09-09")):
            status, row, _ = self.req("POST", "/api/metrics",
                                      {"date": day, "weight": 198.4, "unit": "lb"})
            self.assertEqual(status, 200)
        self.assertAlmostEqual(row["weight_kg"], 90.0, places=1)
        status, body, _ = self.req("POST", "/api/metrics", {"weight": 80, "bogus": 1})
        self.assertEqual(status, 400)
        status, plan, _ = self.req("POST", "/api/plan", {"goal": "cut", "date": "2026-09-09"})
        self.assertEqual(status, 200)
        self.assertIn("targets", plan)
        self.assertNotIn("applied", plan)
        status, plan, _ = self.req("POST", "/api/plan",
                                   {"goal": "cut", "date": "2026-09-09", "apply": True})
        self.assertEqual(plan["applied"]["phase"]["kind"], "cut")
        status, prog, _ = self.req("GET", "/api/progress?date=2026-09-09")
        self.assertEqual(status, 200)
        self.assertEqual(prog["phase"]["kind"], "cut")
        status, ci, _ = self.req("POST", "/api/checkin", {"date": "2026-09-09"})
        self.assertEqual(status, 200)
        self.assertEqual(ci["status"], "collecting")
        status, hist, _ = self.req("GET", "/api/checkins")
        self.assertEqual(len(hist), 1)
        status, hd, _ = self.req("POST", "/api/high-days", {"high_days": 1, "date": "2026-09-09"})
        self.assertEqual(status, 200)
        self.assertEqual(hd["high_days"], 1)
        status, ph, _ = self.req("POST", "/api/phase", {"kind": "reverse", "pace": "conservative"})
        self.assertEqual(ph["params"]["pace"], "conservative")
        status, _, _ = self.req("DELETE", "/api/metrics/2026-09-07")
        self.assertEqual(status, 200)
        status, _, _ = self.req("DELETE", "/api/metrics/2026-09-07")
        self.assertEqual(status, 404)


class StaticServingTest(unittest.TestCase):
    def setUp(self):
        os.environ["MACRO_ENGINE_HTTP_QUIET"] = "1"
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MACRO_ENGINE_DB"] = str(Path(self.tmp.name) / "empty.db")
        static = Path(self.tmp.name) / "dist"
        static.mkdir()
        (static / "index.html").write_text("<!doctype html><title>app</title>", encoding="utf-8")
        (static / "assets").mkdir()
        (static / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
        secret = Path(self.tmp.name) / "secret.txt"
        secret.write_text("nope", encoding="utf-8")
        self.httpd = http_api.make_server("127.0.0.1", 0, static)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address
        self.base = f"http://{host}:{port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        os.environ.pop("MACRO_ENGINE_DB", None)
        os.environ.pop("MACRO_ENGINE_HTTP_QUIET", None)
        self.tmp.cleanup()

    def _get(self, path):
        try:
            with urllib.request.urlopen(self.base + path) as res:
                return res.status, res.read().decode(), res.headers["Content-Type"]
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode(), exc.headers.get("Content-Type")

    def test_serves_app_and_refuses_escape(self):
        status, body, ctype = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("app", body)
        self.assertIn("text/html", ctype)

        status, body, ctype = self._get("/today")
        self.assertEqual(status, 200)
        self.assertIn("<!doctype html>", body)

        status, body, ctype = self._get("/assets/app.js")
        self.assertEqual(status, 200)
        self.assertIn("console.log", body)
        self.assertIn("javascript", ctype)

        status, body, _ = self._get("/assets/missing.js")
        self.assertEqual(status, 404)

        status, body, _ = self._get("/../secret.txt")
        self.assertNotIn("nope", body)
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
