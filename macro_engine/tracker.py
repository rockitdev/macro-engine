"""Logging, targets, and the headline question: what's left today.

All quantities resolve to grams, macros scale from per-100g values, and every
log row carries denormalized macros so history survives food-table changes.
"""

import datetime as _dt
import math
import sqlite3

from . import resolve

MACROS = ("kcal", "protein_g", "carb_g", "fat_g", "fiber_g")

UNIT_SYNONYMS = {
    "tbsp": "tablespoon", "tbs": "tablespoon", "tablespoons": "tablespoon",
    "tsp": "teaspoon", "teaspoons": "teaspoon",
    "oz": "ounce", "ozs": "ounce", "ounces": "ounce",
    "lb": "pound", "lbs": "pound", "pounds": "pound",
    "milligrams": "milligram", "kilograms": "kilogram", "kgs": "kilogram",
    "cups": "cup", "slices": "slice",
}

# Mass units convert straight to grams — no portion/density needed. Volume units
# (cup, tbsp, tsp) are deliberately absent: without a density they only mean
# something as a named portion, so they must match a portion label instead.
MASS_GRAMS = {
    "gram": 1.0, "grams": 1.0, "g": 1.0,
    "milligram": 0.001, "mg": 0.001,
    "kilogram": 1000.0, "kilograms": 1000.0, "kg": 1000.0,
    "ounce": 28.3495, "pound": 453.592,
}


class UnresolvedUnit(ValueError):
    """A unit was given that is neither a known mass unit nor any portion of the
    food. Carries the food's portions so the caller can offer valid choices."""

    def __init__(self, unit: str, portions: list[dict]):
        self.unit = unit
        self.portions = portions
        super().__init__(
            f"unit {unit!r} not recognized for this food — use grams, a mass unit "
            f"(oz, lb, g), or one of its portions"
        )


def _today() -> str:
    return _dt.date.today().isoformat()


def _num(value, field: str, *, allow_none: bool = False,
         positive: bool = False, nonneg: bool = False):
    """Validate a numeric boundary value. Rejects NaN/inf, non-numbers, and
    (optionally) non-positive / negative values. Returns a float, or None when
    allow_none and the value is None."""
    if value is None:
        if allow_none:
            return None
        raise ValueError(f"{field} is required")
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a number, got {value!r}")
    if not math.isfinite(v):
        raise ValueError(f"{field} must be a finite number, got {value!r}")
    if positive and v <= 0:
        raise ValueError(f"{field} must be greater than 0, got {v:g}")
    if nonneg and v < 0:
        raise ValueError(f"{field} must not be negative, got {v:g}")
    return v


def _text(value, field: str) -> str:
    s = (value or "").strip() if isinstance(value, str) else ""
    if not s:
        raise ValueError(f"{field} must be a non-empty string")
    return s


def _valid_date(date) -> str:
    """Normalize/validate a YYYY-MM-DD date string."""
    try:
        return _dt.date.fromisoformat(date).isoformat()
    except (TypeError, ValueError):
        raise ValueError(f"date must be YYYY-MM-DD, got {date!r}")


def _default_portion_rank(label: str) -> int:
    """USDA foods carry many portions; when the user gives no unit, prefer the
    everyday one ('1 medium' banana, '1 large' egg) over '1 cup, mashed'."""
    l = label.lower()
    if "medium" in l:
        return 0
    if "large" in l:
        return 1
    if "small" in l:
        return 2
    if l.startswith("1 "):
        return 3
    return 4


def _resolve_grams(con: sqlite3.Connection, food: dict, item: dict, alias_grams=None) -> tuple[float, str]:
    """Return (grams, human description) for a logged item.

    A given unit is honoured only if it is a known mass unit (converted directly)
    or matches one of the food's portions; anything else raises UnresolvedUnit
    rather than silently falling back to a default portion (which would log the
    wrong amount under an authoritative-looking number)."""
    qty = _num(item.get("qty"), "qty", allow_none=True, positive=True)
    qty = 1.0 if qty is None else qty
    if item.get("grams") is not None:
        g = _num(item["grams"], "grams", positive=True)
        return g, f"{g:g} g"
    portions = resolve.get_portions(con, food["id"])
    unit = (item.get("unit") or "").strip().lower()
    if unit:
        canon = UNIT_SYNONYMS.get(unit, unit)
        if canon in MASS_GRAMS:
            g = qty * MASS_GRAMS[canon]
            return g, f"{qty:g} {canon} ({g:g} g)"
        variants = {unit, canon}
        for p in portions:
            if any(v in p["label"].lower() for v in variants):
                return qty * p["grams"], f"{qty:g} x {p['label']}"
        raise UnresolvedUnit(unit, portions)
    if alias_grams is not None:
        # Guard old/hand-edited rows: a bad stored default becomes a problem
        # rather than logging a wrong (or negative) amount.
        ag = _num(alias_grams, "alias default_grams", positive=True)
        return qty * ag, f"{qty:g} x usual ({ag:g} g)"
    if portions:
        p = min(portions, key=lambda p: _default_portion_rank(p["label"]))
        return qty * p["grams"], f"{qty:g} x {p['label']}"
    return qty * 100.0, f"{qty * 100:g} g (no portion data)"


def _log_item(con: sqlite3.Connection, item: dict, date: str, raw_text: str | None) -> dict:
    """Process one meal item into {'logged': entry} or {'problem': info}.

    Raises ValueError (incl. UnresolvedUnit) for invalid input so the caller can
    route it to 'problems' without aborting the rest of the meal."""
    if item.get("macros") is not None:
        m = item["macros"]
        if not isinstance(m, dict):
            raise ValueError("macros must be an object of macro name -> number")
        vals = {k: (_num(m.get(k), f"macros.{k}", allow_none=True, nonneg=True) or 0.0)
                for k in MACROS}
        name = _text(item.get("name") or "estimate", "name")
        grams = _num(item.get("grams"), "grams", allow_none=True, positive=True)
        cur = con.execute(
            """INSERT INTO log (date, food_id, qty_desc, grams, kcal, protein_g,
                                carb_g, fat_g, fiber_g, raw_text, estimated)
               VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (date, name, grams, vals["kcal"], vals["protein_g"], vals["carb_g"],
             vals["fat_g"], vals["fiber_g"], raw_text or name),
        )
        return {"logged": {"log_id": cur.lastrowid, "name": name,
                           "estimated": True, **vals}}

    food, alias_grams, candidates = None, None, []
    if item.get("food_id"):
        food = resolve.get_food(con, int(item["food_id"]))
    else:
        query = (item.get("query") or "").strip()
        if not query:
            return {"problem": {"item": item, "reason": "no query, food_id, or macros"}}
        candidates = resolve.search(con, query, limit=5)
        if candidates:
            food = candidates[0]
            alias_grams = food.get("alias_default_grams")
    if not food or food.get("kcal") is None:
        return {"problem": {
            "item": item,
            "reason": "no match with nutrition data — log again with a manual "
                      "macros estimate, or pick a food_id from candidates",
            "candidates": [{"food_id": c["id"], "name": c["name"],
                            "source": c["source"]} for c in candidates[:5]],
        }}

    grams, qty_desc = _resolve_grams(con, food, item, alias_grams)
    scale = grams / 100.0
    vals = {k: round((food.get(k) or 0) * scale, 1) for k in MACROS}
    cur = con.execute(
        """INSERT INTO log (date, food_id, qty_desc, grams, kcal, protein_g,
                            carb_g, fat_g, fiber_g, raw_text, estimated)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
        (date, food["id"], qty_desc, grams, vals["kcal"], vals["protein_g"],
         vals["carb_g"], vals["fat_g"], vals["fiber_g"],
         raw_text or item.get("query")),
    )
    return {"logged": {
        "log_id": cur.lastrowid, "food_id": food["id"], "name": food["name"],
        "matched_alias": bool(food.get("matched_alias")), "qty": qty_desc,
        "grams": round(grams, 1), "estimated": False, **vals,
        "runner_up": candidates[1]["name"] if len(candidates) > 1 else None,
    }}


def log_meal(con: sqlite3.Connection, items: list[dict], date: str | None = None,
             raw_text: str | None = None) -> dict:
    """Log a list of items. Each item is either:
      - a food lookup: {"query": str, "qty": 2, "unit": "slice", "grams": 40,
                        "food_id": 123}  (query required unless food_id given;
                        qty/unit/grams optional). qty/grams must be > 0; a unit
                        must be a mass unit (oz/lb/g) or one of the food's
                        portions, else the item is flagged under 'problems'.
      - a manual estimate: {"name": str, "macros": {"kcal":..., "protein_g":...,
                        "carb_g":..., "fat_g":..., "fiber_g":...}} -> estimated=1
    Returns per-item resolutions plus the day's remaining macros. Items with
    invalid values are surfaced under 'problems', never silently logged.
    """
    date = _valid_date(date) if date else _today()
    logged, problems = [], []

    for item in items:
        if not isinstance(item, dict):
            problems.append({"item": item, "reason": "item must be an object"})
            continue
        try:
            result = _log_item(con, item, date, raw_text)
        except UnresolvedUnit as e:
            problems.append({"item": item, "reason": str(e),
                             "available_portions": [p["label"] for p in e.portions]})
        except ValueError as e:
            problems.append({"item": item, "reason": str(e)})
        else:
            if "problem" in result:
                problems.append(result["problem"])
            else:
                logged.append(result["logged"])

    con.commit()
    return {"date": date, "logged": logged, "problems": problems,
            **remaining(con, date)}


def day_totals(con: sqlite3.Connection, date: str | None = None) -> dict:
    date = date or _today()
    row = con.execute(
        """SELECT COUNT(*) AS entries, COALESCE(SUM(kcal),0) AS kcal,
                  COALESCE(SUM(protein_g),0) AS protein_g,
                  COALESCE(SUM(carb_g),0) AS carb_g,
                  COALESCE(SUM(fat_g),0) AS fat_g,
                  COALESCE(SUM(fiber_g),0) AS fiber_g
           FROM log WHERE date = ?""",
        (date,),
    ).fetchone()
    return {k: (round(row[k], 1) if k != "entries" else row[k]) for k in row.keys()}


def get_targets(con: sqlite3.Connection, date: str | None = None):
    date = date or _today()
    row = con.execute(
        """SELECT kcal, protein_g, carb_g, fat_g, fiber_g, effective_date FROM targets
           WHERE effective_date <= ? ORDER BY effective_date DESC, id DESC LIMIT 1""",
        (date,),
    ).fetchone()
    return dict(row) if row else None


def set_targets(con: sqlite3.Connection, kcal: float, protein_g: float,
                carb_g: float, fat_g: float, fiber_g: float | None = None,
                effective_date: str | None = None) -> dict:
    effective_date = _valid_date(effective_date) if effective_date else _today()
    kcal = _num(kcal, "kcal", positive=True)
    protein_g = _num(protein_g, "protein_g", nonneg=True)
    carb_g = _num(carb_g, "carb_g", nonneg=True)
    fat_g = _num(fat_g, "fat_g", nonneg=True)
    fiber_g = _num(fiber_g, "fiber_g", allow_none=True, nonneg=True)
    con.execute(
        "INSERT INTO targets (effective_date, kcal, protein_g, carb_g, fat_g, fiber_g) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (effective_date, kcal, protein_g, carb_g, fat_g, fiber_g),
    )
    con.commit()
    return get_targets(con, effective_date)


def remaining(con: sqlite3.Connection, date: str | None = None) -> dict:
    date = date or _today()
    totals = day_totals(con, date)
    targets = get_targets(con, date)
    out = {"date": date, "eaten": totals, "targets": targets}
    if targets:
        # An unset target (e.g. no fibre goal) yields None, not a spurious deficit.
        out["remaining"] = {
            k: (round(targets[k] - totals[k], 1) if targets[k] is not None else None)
            for k in MACROS
        }
    else:
        out["remaining"] = None
        out["note"] = "No targets set — call set_targets first."
    return out


def day_log(con: sqlite3.Connection, date: str | None = None) -> list[dict]:
    date = date or _today()
    rows = con.execute(
        """SELECT l.id AS log_id, l.ts, l.food_id, l.qty_desc, l.grams, l.kcal,
                  l.protein_g, l.carb_g, l.fat_g, l.fiber_g, l.estimated,
                  COALESCE(f.name, l.qty_desc) AS name
           FROM log l LEFT JOIN foods f ON f.id = l.food_id
           WHERE l.date = ? ORDER BY l.id""",
        (date,),
    ).fetchall()
    return [dict(r) for r in rows]


def delete_log_entry(con: sqlite3.Connection, log_id: int) -> bool:
    cur = con.execute("DELETE FROM log WHERE id = ?", (log_id,))
    con.commit()
    return cur.rowcount > 0


def add_food(con: sqlite3.Connection, name: str, kcal: float, protein_g: float,
             carb_g: float, fat_g: float, fiber_g: float | None = None,
             brand: str | None = None, store: str | None = None,
             portion_label: str | None = None, portion_grams: float | None = None,
             macros_are_per_portion: bool = False,
             alias: str | None = None) -> dict:
    """Add a custom food (source='manual'). Macros are per 100 g unless
    macros_are_per_portion=True, in which case portion_grams is required and
    values are scaled to 100 g for storage."""
    name = _text(name, "name")
    kcal = _num(kcal, "kcal", nonneg=True)
    protein_g = _num(protein_g, "protein_g", nonneg=True)
    carb_g = _num(carb_g, "carb_g", nonneg=True)
    fat_g = _num(fat_g, "fat_g", nonneg=True)
    fiber_g = _num(fiber_g, "fiber_g", allow_none=True, nonneg=True)
    portion_grams = _num(portion_grams, "portion_grams", allow_none=True, positive=True)
    if macros_are_per_portion:
        if not portion_grams:
            raise ValueError("macros_are_per_portion requires portion_grams")
        f = 100.0 / float(portion_grams)
        kcal, protein_g, carb_g, fat_g = (round(v * f, 1) for v in
                                          (kcal, protein_g, carb_g, fat_g))
        fiber_g = round(fiber_g * f, 1) if fiber_g is not None else None
    food_id = con.execute(
        "INSERT INTO foods (name, brand, store, source) VALUES (?, ?, ?, 'manual') "
        "RETURNING id",
        (name, brand, store)).fetchone()[0]
    con.execute(
        "INSERT INTO food_nutrients (food_id, kcal, protein_g, carb_g, fat_g, fiber_g) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (food_id, kcal, protein_g, carb_g, fat_g, fiber_g))
    if portion_label and portion_grams:
        con.execute("INSERT INTO portions (food_id, label, grams) VALUES (?, ?, ?)",
                    (food_id, portion_label, portion_grams))
    con.execute("INSERT INTO food_fts (rowid, name, brand, store) VALUES (?, ?, ?, ?)",
                (food_id, name, brand or "", store or ""))
    con.commit()
    out = {"food_id": food_id, "name": name, "brand": brand, "store": store,
           "kcal_per_100g": kcal}
    if alias:
        out["alias"] = add_alias(con, alias, food_id,
                                 portion_grams if portion_label else None)
    return out


def add_alias(con: sqlite3.Connection, phrase: str, food_id: int,
              default_grams: float | None = None) -> dict:
    phrase = _text(phrase, "phrase")
    default_grams = _num(default_grams, "default_grams", allow_none=True, positive=True)
    food = resolve.get_food(con, food_id)
    if not food:
        raise ValueError(f"no food with id {food_id}")
    con.execute(
        "INSERT INTO aliases (phrase, food_id, default_grams) VALUES (?, ?, ?)",
        (resolve._normalize(phrase), food_id, default_grams),
    )
    con.commit()
    return {"phrase": resolve._normalize(phrase), "food": food["name"],
            "default_grams": default_grams}
