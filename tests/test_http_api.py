"""HTTP API over a temp database. Never touches the real macros.db."""

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
