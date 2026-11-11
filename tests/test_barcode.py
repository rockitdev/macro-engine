"""Tests for the barcode lookup endpoint and the barcode normalisation module.

The Open Food Facts network call is monkey-patched so these tests run fully
offline.
"""

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from macro_engine import db, http_api
from macro_engine import barcode as barcode_mod


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _off_response(name: str = "Test Cracker", brand: str = "TestBrand",
                  kcal: float = 430, protein: float = 9.0,
                  carb: float = 65.0, fat: float = 15.0, fiber: float = 3.0,
                  serving_size: str = "30g", serving_qty: float = 30.0) -> bytes:
    """Minimal Open Food Facts v2 API success response."""
    payload = {
        "status": 1,
        "product": {
            "product_name": name,
            "brands": brand,
            "serving_size": serving_size,
            "serving_quantity": serving_qty,
            "nutriments": {
                "energy-kcal_100g": kcal,
                "proteins_100g": protein,
                "carbohydrates_100g": carb,
                "fat_100g": fat,
                "fiber_100g": fiber,
            },
        },
    }
    return json.dumps(payload).encode()


def _off_miss() -> bytes:
    return json.dumps({"status": 0, "status_verbose": "product not found"}).encode()


# ── barcode.py unit tests ─────────────────────────────────────────────────────

class TestBarcodeModule(unittest.TestCase):
    def _patch(self, raw: bytes):
        """Return a context manager that makes barcode._get return raw."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = raw
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        def fake_urlopen(req, timeout=None):
            return mock_resp

        return patch("urllib.request.urlopen", side_effect=fake_urlopen)

    def test_lookup_success_normalises_fields(self):
        with self._patch(_off_response()):
            result = barcode_mod.lookup("0123456789012")
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Test Cracker")
        self.assertEqual(result["brand"], "TestBrand")
        self.assertAlmostEqual(result["kcal"], 430)
        self.assertAlmostEqual(result["protein_g"], 9.0)
        self.assertAlmostEqual(result["carb_g"], 65.0)
        self.assertAlmostEqual(result["fat_g"], 15.0)
        self.assertAlmostEqual(result["fiber_g"], 3.0)
        self.assertEqual(result["portion_label"], "30g")
        self.assertAlmostEqual(result["portion_grams"], 30.0)
        self.assertEqual(result["barcode"], "0123456789012")
        self.assertTrue(result["off_found"])

    def test_lookup_miss_returns_none(self):
        with self._patch(_off_miss()):
            result = barcode_mod.lookup("0000000000000")
        self.assertIsNone(result)

    def test_lookup_404_returns_none(self):
        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(url="", code=404, msg="Not Found", hdrs=None, fp=None)  # type: ignore

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = barcode_mod.lookup("9999999999999")
        self.assertIsNone(result)

    def test_lookup_strips_brand_to_first_entry(self):
        with self._patch(_off_response(brand="BrandA, BrandB")):
            result = barcode_mod.lookup("111")
        self.assertEqual(result["brand"], "BrandA")

    def test_lookup_empty_barcode_returns_none(self):
        result = barcode_mod.lookup("   ")
        self.assertIsNone(result)

    def test_lookup_no_name_returns_none(self):
        with self._patch(_off_response(name="")):
            result = barcode_mod.lookup("222")
        self.assertIsNone(result)

    def test_lookup_missing_nutriments_returns_none_fields(self):
        payload = json.dumps({
            "status": 1,
            "product": {
                "product_name": "Mystery Food",
                "brands": "",
                "serving_size": "",
                "serving_quantity": None,
                "nutriments": {},
            },
        }).encode()
        with self._patch(payload):
            result = barcode_mod.lookup("333")
        self.assertIsNotNone(result)
        self.assertIsNone(result["kcal"])
        self.assertIsNone(result["protein_g"])


# ── HTTP endpoint tests ───────────────────────────────────────────────────────

class TestBarcodeEndpoint(unittest.TestCase):
    def setUp(self):
        os.environ["MACRO_ENGINE_HTTP_QUIET"] = "1"
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "test.db")
        os.environ["MACRO_ENGINE_DB"] = self.db_path
        con = db.connect(self.db_path)
        db.rebuild_fts(con)
        con.commit()
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

    def req(self, path):
        request = urllib.request.Request(self.base + path)
        try:
            with urllib.request.urlopen(request) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def _patch_lookup(self, return_value):
        return patch.object(barcode_mod, "lookup", return_value=return_value)

    def test_barcode_found(self):
        product = {
            "name": "Test Cracker", "brand": "TestBrand",
            "kcal": 430.0, "protein_g": 9.0, "carb_g": 65.0,
            "fat_g": 15.0, "fiber_g": 3.0,
            "portion_label": "30g", "portion_grams": 30.0,
            "barcode": "0123456789012", "off_found": True,
        }
        with self._patch_lookup(product):
            status, body = self.req("/api/barcode/0123456789012")
        self.assertEqual(status, 200)
        self.assertEqual(body["name"], "Test Cracker")
        self.assertEqual(body["barcode"], "0123456789012")
        self.assertEqual(body["kcal"], 430.0)

    def test_barcode_not_found(self):
        with self._patch_lookup(None):
            status, body = self.req("/api/barcode/0000000000000")
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    def test_barcode_route_requires_code(self):
        # /api/barcode with no segment → 404 (no route matches)
        status, body = self.req("/api/barcode/")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
