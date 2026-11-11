"""Barcode lookup via Open Food Facts.

Queries https://world.openfoodfacts.org/api/v2/product/<barcode>.json
and normalises the result into the same food-like shape that resolve.get_food
returns (without a food_id, since it may not yet be in the local DB).

Public surface
--------------
  lookup(barcode) -> dict | None
      Returns a ``FoodLookup`` dict or None when the product is not found.

      FoodLookup keys
      ~~~~~~~~~~~~~~~
      name          str
      brand         str | None
      kcal          float | None   — per 100 g
      protein_g     float | None   — per 100 g
      carb_g        float | None   — per 100 g
      fat_g         float | None   — per 100 g
      fiber_g       float | None   — per 100 g
      portion_label str | None
      portion_grams float | None
      barcode       str
      off_found     True           — always True when a dict is returned
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Any

_OFF_URL = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json?fields=product_name,brands,nutriments,serving_size,serving_quantity"
_TIMEOUT = 8  # seconds


def _get(url: str) -> dict | None:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "macro-engine/1 (barcode scanner; contact ryan.moore@verbinteractive.com)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
        return f if f >= 0 else None
    except (TypeError, ValueError):
        return None


def lookup(barcode: str) -> dict | None:
    """Query Open Food Facts for *barcode*.

    Returns a normalised dict on hit, None on miss (product not in OFF or
    missing required nutriment data).
    """
    barcode = barcode.strip()
    if not barcode:
        return None

    data = _get(_OFF_URL.format(barcode=barcode))
    if data is None:
        return None

    status = data.get("status")
    if status != 1:
        # OFF returns status=0 when product not found
        return None

    product: dict = data.get("product") or {}
    name = (product.get("product_name") or "").strip()
    if not name:
        return None

    brand = (product.get("brands") or "").strip() or None
    # brands can be comma-separated; use the first one
    if brand:
        brand = brand.split(",")[0].strip() or None

    nutriments: dict = product.get("nutriments") or {}

    # OFF stores per-100g values with suffix _100g
    kcal = _float_or_none(nutriments.get("energy-kcal_100g"))
    protein_g = _float_or_none(nutriments.get("proteins_100g"))
    carb_g = _float_or_none(nutriments.get("carbohydrates_100g"))
    fat_g = _float_or_none(nutriments.get("fat_100g"))
    fiber_g = _float_or_none(nutriments.get("fiber_100g"))

    # Serving info
    serving_label: str | None = (product.get("serving_size") or "").strip() or None
    serving_grams = _float_or_none(product.get("serving_quantity"))

    return {
        "name": name,
        "brand": brand,
        "kcal": kcal,
        "protein_g": protein_g,
        "carb_g": carb_g,
        "fat_g": fat_g,
        "fiber_g": fiber_g,
        "portion_label": serving_label,
        "portion_grams": serving_grams,
        "barcode": barcode,
        "off_found": True,
    }
