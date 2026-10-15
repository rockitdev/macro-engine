"""HTTP API over a temp database. Never touches the real macros.db.

Covers all routes available in this branch's tracker, plus the two new routes
added in the fit_meal pass:
    GET  /api/chains    — list of distinct restaurant brands
    POST /api/fit-meal  — combinatorial restaurant order solver
"""

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


# ---------------------------------------------------------------------------
# Shared seed helpers
# ---------------------------------------------------------------------------

def seed_generic(con):
    """Generic USDA foods without a brand (used by most existing tests)."""
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


def seed_chains(con):
    """Insert two restaurant chains with portion data, for chains/fit-meal tests."""
    chain_data = [
        ("Classic Patty",  "FakeBurger", 257.0, 12.6, 24.0, 12.0, "1 burger", 219.0),
        ("Crispy Fries",   "FakeBurger", 300.0,  3.5, 38.0, 15.0, "small",    117.0),
        ("Zero Cola",      "FakeBurger",   0.0,  0.0,  0.1,  0.0, "medium",   500.0),
        # A second chain to verify /api/chains returns multiple entries
        ("Signature Wrap", "SandwichCo", 210.0, 18.0, 22.0,  8.0, "1 wrap",   200.0),
    ]
    for name, brand, kcal, prot, carb, fat, plabel, pgrams in chain_data:
        fid = con.execute(
            "INSERT INTO foods (name, brand, source) VALUES (?, ?, 'manual') RETURNING id",
            (name, brand),
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
    db.rebuild_fts(con)
    con.commit()


# ---------------------------------------------------------------------------
# Base test class with a live HTTP server over a temp DB
# ---------------------------------------------------------------------------

class _ApiTestBase(unittest.TestCase):
    """Starts a ThreadingHTTPServer on a random port; subclasses seed the DB."""

    def _make_server(self, db_path: str):
        os.environ["MACRO_ENGINE_HTTP_QUIET"] = "1"
        os.environ["MACRO_ENGINE_DB"] = db_path
        httpd = http_api.make_server("127.0.0.1", 0)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        host, port = httpd.server_address
        return httpd, thread, f"http://{host}:{port}"

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


# ---------------------------------------------------------------------------
# Core API routes (tracker features present in this branch)
# ---------------------------------------------------------------------------

class HttpApiTest(_ApiTestBase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "test.db")
        con = db.connect(self.db_path)
        self.ids = seed_generic(con)
        con.close()
        self.httpd, self.thread, self.base = self._make_server(self.db_path)

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

        # log_meal in this branch does not have the `meal` parameter
        status, meal, _ = self.req("POST", "/api/meals", {
            "items": [{"food_id": self.ids["Egg"], "qty": 2, "unit": "large"}],
            "raw_text": "two eggs",
        })
        self.assertEqual(status, 200)
        self.assertEqual(len(meal["logged"]), 1)
        self.assertEqual(meal["problems"], [])
        self.assertEqual(meal["logged"][0]["kcal"], 143.0)
        log_id = meal["logged"][0]["log_id"]

        status, day, _ = self.req("GET", "/api/day")
        self.assertEqual(len(day["entries"]), 1)
        self.assertEqual(day["entries"][0]["name"], "Egg, whole, raw, fresh")
        self.assertIs(day["entries"][0]["estimated"], False)

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

    def test_bad_date_returns_400(self):
        status, body, _ = self.req("GET", "/api/day?date=tomorrow")
        self.assertEqual(status, 400)
        self.assertIn("date", body["error"])

    def test_bad_json_returns_400(self):
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


# ---------------------------------------------------------------------------
# GET /api/chains
# ---------------------------------------------------------------------------

class ChainsRouteTest(_ApiTestBase):
    """HTTP-layer tests for GET /api/chains."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "test.db")
        con = db.connect(self.db_path)
        seed_chains(con)
        con.close()
        self.httpd, self.thread, self.base = self._make_server(self.db_path)

    def test_returns_200_and_list(self):
        status, body, _ = self.req("GET", "/api/chains")
        self.assertEqual(status, 200)
        self.assertIsInstance(body, list)

    def test_contains_seeded_chains(self):
        _, body, _ = self.req("GET", "/api/chains")
        self.assertIn("FakeBurger", body)
        self.assertIn("SandwichCo", body)

    def test_sorted_alphabetically(self):
        _, body, _ = self.req("GET", "/api/chains")
        self.assertEqual(body, sorted(body))

    def test_empty_db_returns_empty_list(self):
        """With no branded foods, /api/chains must return [], not 500."""
        tmp = tempfile.TemporaryDirectory()
        db_path = str(Path(tmp.name) / "empty.db")
        os.environ["MACRO_ENGINE_DB"] = db_path
        db.connect(db_path).close()  # ensure schema is initialised
        status, body, _ = self.req("GET", "/api/chains")
        self.assertEqual(status, 200)
        self.assertEqual(body, [])
        tmp.cleanup()

    def test_no_duplicates(self):
        """Multiple foods with the same brand must not produce duplicate entries."""
        _, body, _ = self.req("GET", "/api/chains")
        self.assertEqual(len(body), len(set(body)))

    def test_cors_header_present(self):
        _, _, headers = self.req(
            "GET", "/api/chains", headers={"Origin": "http://localhost:5173"},
        )
        self.assertIn("Access-Control-Allow-Origin", headers)


# ---------------------------------------------------------------------------
# POST /api/fit-meal
# ---------------------------------------------------------------------------

class FitMealRouteTest(_ApiTestBase):
    """HTTP-layer tests for POST /api/fit-meal."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "test.db")
        con = db.connect(self.db_path)
        seed_chains(con)
        con.close()
        self.httpd, self.thread, self.base = self._make_server(self.db_path)

    def test_basic_fit_meal_200(self):
        """POST with a known chain and explicit remaining returns 200 with orders."""
        status, body, _ = self.req("POST", "/api/fit-meal", {
            "restaurant": "FakeBurger",
            "remaining": {"kcal": 800, "protein_g": 50, "carb_g": 100, "fat_g": 40},
        })
        self.assertEqual(status, 200)
        self.assertIn("orders", body)
        self.assertIn("budget", body)
        self.assertIn("restaurant", body)
        self.assertEqual(body["restaurant"], "FakeBurger")

    def test_orders_is_list(self):
        _, body, _ = self.req("POST", "/api/fit-meal", {
            "restaurant": "FakeBurger",
            "remaining": {"kcal": 1000, "protein_g": 80, "carb_g": 120, "fat_g": 50},
        })
        self.assertIsInstance(body["orders"], list)

    def test_no_order_exceeds_budget(self):
        """Every returned order must stay within the requested budget."""
        budget = {"kcal": 700, "protein_g": 40, "carb_g": 80, "fat_g": 35}
        _, body, _ = self.req("POST", "/api/fit-meal", {
            "restaurant": "FakeBurger",
            "remaining": budget,
        })
        for order in body["orders"]:
            for macro, ceiling in budget.items():
                self.assertLessEqual(
                    order["total"][macro], ceiling + 0.1,
                    msg=f"order rank {order['rank']} exceeds {macro} budget",
                )

    def test_unknown_restaurant_returns_note(self):
        """A chain not in the DB should return empty orders and a note."""
        status, body, _ = self.req("POST", "/api/fit-meal", {
            "restaurant": "ChainThatDoesntExist999",
            "remaining": {"kcal": 600, "protein_g": 30, "carb_g": 60, "fat_g": 20},
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["orders"], [])
        self.assertIn("note", body)

    def test_missing_restaurant_field_is_400(self):
        status, body, _ = self.req("POST", "/api/fit-meal", {
            "remaining": {"kcal": 600},
        })
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_empty_restaurant_is_400(self):
        status, body, _ = self.req("POST", "/api/fit-meal", {
            "restaurant": "   ",
            "remaining": {"kcal": 600, "protein_g": 40, "carb_g": 60, "fat_g": 20},
        })
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_bad_remaining_type_is_400(self):
        """remaining must be an object or null — a string is invalid."""
        status, body, _ = self.req("POST", "/api/fit-meal", {
            "restaurant": "FakeBurger",
            "remaining": "600 kcal",
        })
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_invalid_date_is_400(self):
        status, body, _ = self.req("POST", "/api/fit-meal", {
            "restaurant": "FakeBurger",
            "date": "not-a-date",
        })
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_no_body_is_400(self):
        """POST with no JSON body must return 400."""
        request = urllib.request.Request(
            self.base + "/api/fit-meal", data=b"", method="POST",
        )
        request.add_header("Content-Type", "application/json")
        try:
            urllib.request.urlopen(request)
            self.fail("expected HTTPError 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)

    def test_over_budget_field_present(self):
        """Response must always include over_budget (even if empty)."""
        _, body, _ = self.req("POST", "/api/fit-meal", {
            "restaurant": "FakeBurger",
            "remaining": {"kcal": 2000, "protein_g": 200, "carb_g": 300, "fat_g": 100},
        })
        self.assertIn("over_budget", body)

    def test_cors_header_present(self):
        _, _, headers = self.req(
            "POST", "/api/fit-meal",
            body={
                "restaurant": "FakeBurger",
                "remaining": {"kcal": 800, "protein_g": 50, "carb_g": 100, "fat_g": 40},
            },
            headers={"Origin": "http://localhost:5173"},
        )
        self.assertIn("Access-Control-Allow-Origin", headers)

    def test_order_ranks_sequential_and_scores_descending(self):
        """Orders must have sequential rank numbers and descending fit_scores."""
        _, body, _ = self.req("POST", "/api/fit-meal", {
            "restaurant": "FakeBurger",
            "remaining": {"kcal": 1200, "protein_g": 80, "carb_g": 150, "fat_g": 60},
        })
        orders = body["orders"]
        if len(orders) > 1:
            for i, order in enumerate(orders, 1):
                self.assertEqual(order["rank"], i)
            scores = [o["fit_score"] for o in orders]
            self.assertEqual(scores, sorted(scores, reverse=True))

    def test_budget_echoed_back(self):
        budget = {"kcal": 500, "protein_g": 30, "carb_g": 60, "fat_g": 20}
        _, body, _ = self.req("POST", "/api/fit-meal", {
            "restaurant": "FakeBurger",
            "remaining": budget,
        })
        self.assertEqual(body["budget"]["kcal"], 500.0)
        self.assertEqual(body["restaurant"], "FakeBurger")


# ---------------------------------------------------------------------------
# Static serving
# ---------------------------------------------------------------------------

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
