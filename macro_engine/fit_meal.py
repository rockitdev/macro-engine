"""fit_meal — combinatorial macro-budget solver for restaurant orders.

Given a restaurant/chain name and the day's remaining macros, return 2-3
concrete orders (single items or small combos) that fit within the budget.
Ranked by closest fit to the *per-meal* target (protein-forward tiebreak),
NOT by maximising the full-day budget.

Critical: DB stores ALL macros per 100 g in food_nutrients. A Big Mac row
may show 257 kcal, but the serving is 219 g → 562 kcal actual. The solver
always scales by (portion_grams / 100) before comparing to the budget.
Never read the raw per-100g column as if it were a serving.

Per-meal targeting (2026-10-15 design):
  target = remaining / meals_left_today     ← what ONE meal should eat
  hard ceiling = remaining                  ← order may NEVER exceed the day's remaining

"meals_left_today" defaults to (meals_per_day setting − meals already logged
today), clamped to ≥ 1.  Both values can be overridden by the caller.

Treats filter:
  Dessert/shake/treat items are excluded by default (include_treats=False).
  Classification uses existing food category fields when present, otherwise
  a maintainable keyword list (see _TREAT_KEYWORDS).  The callers can pass
  include_treats=True to see everything.
"""

from __future__ import annotations

import itertools
import sqlite3
from typing import Optional

from . import resolve, tracker

# ── Constants ─────────────────────────────────────────────────────────────────

# The four macros we budget against. fiber_g is informational only.
_BUDGET_MACROS = ("kcal", "protein_g", "carb_g", "fat_g")

# Protein is twice as important as carbs/fat when ranking how close an order
# is to the per-meal target. kcal is a secondary tiebreaker.
_WEIGHTS = {"kcal": 0.2, "protein_g": 1.5, "carb_g": 0.4, "fat_g": 0.4}

# Items fetched from the search for the chain before solving. 40 is enough for
# any chain while keeping triple-combo enumeration fast (~10k iterations max).
_SEARCH_LIMIT = 40

# Largest combo size to try (1 item, 2 items, or 3 items).
_MAX_COMBO = 3

# How many orders to return.
_TOP_N = 3

# Default meals per day. Stored in the settings table as 'meals_per_day'.
_DEFAULT_MEALS_PER_DAY = 3

# Keywords that classify a food as a treat/dessert (case-insensitive substring
# match against the food name).  Used when no category field is present.
# Keep this list maintainable — prefer broad terms over per-item specifics.
_TREAT_KEYWORDS = frozenset([
    "mcflurry", "flurry",
    "milkshake", "milk shake", "shake",
    "sundae",
    "pie",           # apple pie, etc. — broad but catches most fast-food desserts
    "cookie",
    "brownie",
    "donut", "doughnut",
    "ice cream",
    "soft serve",
    "frozen yogurt", "froyo",
    "smoothie",
    "slurpee",
    "blizzard",
    "frosty",        # Wendy's Frosty
    "churro",
    "cinnamon bun", "cinnamon roll",
    "fritter",
    "muffin",
    "waffle cone", "waffle cup",
    "banana split",
])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_treat(food_name: str) -> bool:
    """Return True if the food name contains a treat/dessert keyword.

    Classification approach: the DB foods table has no category column in the
    current schema, so we match against a keyword list.  When a category field
    is added to the schema, update this function to prefer it.
    """
    name_lower = food_name.lower()
    return any(kw in name_lower for kw in _TREAT_KEYWORDS)


def _per_serving(food: dict, portions: list[dict]) -> tuple[dict, str, float]:
    """Scale per-100g DB macros to one real serving.

    Uses the first portion row when available; falls back to 100 g (i.e.
    no scaling) when a food has no portion data.

    Returns:
        (macros_dict, portion_label, grams)
    """
    if portions:
        portion = portions[0]
        grams = float(portion["grams"])
        label = portion["label"]
    else:
        grams = 100.0
        label = "100 g"

    # THE critical scaling step — never skip this.
    scale = grams / 100.0
    macros = {k: round((food.get(k) or 0.0) * scale, 1) for k in _BUDGET_MACROS}
    return macros, label, grams


def _fits(combo_macros: dict, ceiling: dict) -> bool:
    """Return True only if every macro in combo_macros is ≤ ceiling.

    ceiling values of float('inf') mean unconstrained (no target set).
    A tiny epsilon (0.05 g / kcal) absorbs floating-point drift.
    """
    for k in _BUDGET_MACROS:
        if combo_macros.get(k, 0.0) > ceiling[k] + 0.05:
            return False
    return True


def _score(combo_macros: dict, target: dict) -> float:
    """Rank how close a combo is to the per-meal target (lower distance = higher score).

    Returns a score ∈ [0, ∞) where HIGHER means a CLOSER fit.  We compute a
    weighted similarity: each macro's ratio (used / target) is clamped to [0, 1]
    and weighted.  A combo that exactly matches the target on all macros scores
    the maximum.  Over-target combos are already excluded by _fits().

    Protein gets the highest weight so it wins ties (Ryan eats protein-forward).
    Unconstrained macros (target = inf) are skipped to avoid zero-division.
    """
    s = 0.0
    for k, w in _WEIGHTS.items():
        t = target[k]
        if t != float("inf") and t > 0:
            ratio = min(combo_macros.get(k, 0.0) / t, 1.0)
            s += w * ratio
    return s


def _sum_macros(macros_list: list[dict]) -> dict:
    return {k: round(sum(m.get(k, 0.0) for m in macros_list), 1) for k in _BUDGET_MACROS}


def get_meals_per_day(con: sqlite3.Connection) -> int:
    """Read the user's meals-per-day preference (default 3)."""
    try:
        row = con.execute(
            "SELECT value FROM settings WHERE key = 'meals_per_day'"
        ).fetchone()
        if row is not None:
            v = int(row["value"])
            if v >= 1:
                return v
    except Exception:
        pass
    return _DEFAULT_MEALS_PER_DAY


def set_meals_per_day(con: sqlite3.Connection, n: int) -> dict:
    """Persist the user's meals-per-day preference. n must be 1–10."""
    if not isinstance(n, int) or not (1 <= n <= 10):
        raise ValueError("meals_per_day must be an integer between 1 and 10")
    con.execute(
        "INSERT INTO settings (key, value) VALUES ('meals_per_day', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(n),),
    )
    con.commit()
    return {"meals_per_day": n}


def _count_meals_logged_today(con: sqlite3.Connection, date: str) -> int:
    """Count how many distinct meal-types have been logged for date.

    A meal-type counts once regardless of how many items it contains.
    Rows with meal=NULL are ignored (they don't consume a named meal slot).
    """
    row = con.execute(
        "SELECT COUNT(DISTINCT meal) FROM log WHERE date = ? AND meal IS NOT NULL",
        (date,),
    ).fetchone()
    return row[0] if row else 0


# ── Main solver ───────────────────────────────────────────────────────────────

def fit_meal(
    con: sqlite3.Connection,
    restaurant: str,
    remaining: Optional[dict] = None,
    date: Optional[str] = None,
    meals_per_day: Optional[int] = None,
    meals_left_override: Optional[int] = None,
    include_treats: bool = False,
) -> dict:
    """Find concrete restaurant orders that fit the remaining macro budget.

    Args:
        restaurant:         Chain/restaurant name ("McDonald's", "Tim Hortons", …).
        remaining:          Optional explicit remaining macros dict with keys kcal,
                            protein_g, carb_g, fat_g.  When None the function reads
                            today's remaining from the tracker (or the given date).
        date:               YYYY-MM-DD for auto-pulled remaining (default today).
        meals_per_day:      Override the stored meals_per_day setting for this call.
        meals_left_override: Directly set how many meals are left today (skips the
                            (meals_per_day − meals_logged) calculation).
        include_treats:     Include dessert/shake/treat items in suggestions.
                            Default False (treats excluded).

    Returns a dict with keys:
        restaurant      – echoed back
        budget          – full remaining macros used as hard ceiling
        per_meal_target – the per-meal slice budgeted (budget / meals_left)
        meals_left      – how many meals are left today (informational)
        orders          – list of up to 3 ranked orders, each with:
                            rank, items (name/portion/grams/macros), total macros,
                            fit_score
        over_budget     – items that individually exceed at least one ceiling macro,
                          with which macros they blow (useful warning to show user)
        treats_excluded – True when treats were filtered out (includes_treats=False)
        note            – present only when the chain isn't in the DB
    """
    # ── 1. Resolve full-day remaining (hard ceiling) ──────────────────────────
    if remaining is None:
        rem = tracker.remaining(con, date)
        full_remaining = rem.get("remaining") or {}
        _date = rem["date"]
    else:
        full_remaining = {k: remaining.get(k) for k in _BUDGET_MACROS}
        _date = date or tracker._today()

    # None means "no target set" → unconstrained
    ceiling: dict[str, float] = {
        k: float(full_remaining.get(k)) if full_remaining.get(k) is not None else float("inf")
        for k in _BUDGET_MACROS
    }

    # ── 2. Compute per-meal target ────────────────────────────────────────────
    if meals_left_override is not None:
        meals_left = max(1, int(meals_left_override))
    else:
        _meals_per_day = meals_per_day if meals_per_day is not None else get_meals_per_day(con)
        meals_logged = _count_meals_logged_today(con, _date)
        meals_left = max(1, _meals_per_day - meals_logged)

    # Per-meal target = ceiling / meals_left (unconstrained stays unconstrained)
    target: dict[str, float] = {
        k: (v / meals_left if v != float("inf") else float("inf"))
        for k, v in ceiling.items()
    }

    # ── 3. Search for the chain's foods ──────────────────────────────────────
    candidates = resolve.search(con, restaurant, limit=_SEARCH_LIMIT)
    if not candidates:
        return {
            "restaurant": restaurant,
            "budget": {k: full_remaining.get(k) for k in _BUDGET_MACROS},
            "per_meal_target": {k: (round(v, 1) if v != float("inf") else None) for k, v in target.items()},
            "meals_left": meals_left,
            "orders": [],
            "over_budget": [],
            "treats_excluded": not include_treats,
            "note": (
                f"No foods found for '{restaurant}' — the chain may not be in "
                "the database yet. Ask the recon agent to import its menu."
            ),
        }

    # ── 4. Compute per-serving macros (portion-aware) ─────────────────────────
    items: list[dict] = []
    treats_present = False
    for food in candidates:
        if food.get("kcal") is None:
            continue  # skip rows with no nutrition data
        if not include_treats and _is_treat(food["name"]):
            treats_present = True
            continue  # exclude treats by default
        portions = food.get("portions") or resolve.get_portions(con, food["id"])
        macros, label, grams = _per_serving(food, portions)
        items.append({
            "food_id": food["id"],
            "name": food["name"],
            "portion": label,
            "grams": grams,
            "macros": macros,
        })

    if not items:
        return {
            "restaurant": restaurant,
            "budget": {k: full_remaining.get(k) for k in _BUDGET_MACROS},
            "per_meal_target": {k: (round(v, 1) if v != float("inf") else None) for k, v in target.items()},
            "meals_left": meals_left,
            "orders": [],
            "over_budget": [],
            "treats_excluded": not include_treats,
            "note": f"Foods found for '{restaurant}' but none have macro data.",
        }

    # ── 5. Flag items that individually exceed the CEILING (full remaining) ──
    over_budget: list[dict] = []
    for item in items:
        blows = [
            k for k in _BUDGET_MACROS
            if ceiling[k] != float("inf")
            and item["macros"].get(k, 0.0) > ceiling[k] + 0.05
        ]
        if blows:
            over_budget.append({
                "name": item["name"],
                "portion": item["portion"],
                "grams": item["grams"],
                "macros": item["macros"],
                "blows": blows,
            })

    # ── 6. Combinatorial fit search ───────────────────────────────────────────
    # Enumerate singles → pairs → triples; keep combos that:
    #   (a) fit within the CEILING (hard constraint — never exceed day's remaining)
    #   (b) are scored against the TARGET (per-meal slice)
    fitting: list[tuple[float, list[dict], dict]] = []
    seen: set[frozenset[int]] = set()

    for size in range(1, _MAX_COMBO + 1):
        for indices in itertools.combinations(range(len(items)), size):
            key: frozenset[int] = frozenset(indices)
            if key in seen:
                continue
            seen.add(key)
            combo_items = [items[i] for i in indices]
            total = _sum_macros([it["macros"] for it in combo_items])
            # HARD CEILING: never exceed the day's full remaining
            if not _fits(total, ceiling):
                continue
            # Score by proximity to the per-meal target
            score = _score(total, target)
            fitting.append((score, combo_items, total))

    # Best fit first (highest score = closest to per-meal target, protein-forward)
    fitting.sort(key=lambda x: -x[0])

    top_orders: list[dict] = []
    for score, combo_items, total in fitting:
        if len(top_orders) >= _TOP_N:
            break
        top_orders.append({
            "rank": len(top_orders) + 1,
            "items": [
                {
                    "name": it["name"],
                    "portion": it["portion"],
                    "grams": it["grams"],
                    "macros": it["macros"],
                }
                for it in combo_items
            ],
            "total": total,
            "fit_score": round(score, 3),
        })

    return {
        "restaurant": restaurant,
        "budget": {k: full_remaining.get(k) for k in _BUDGET_MACROS},
        "per_meal_target": {k: (round(v, 1) if v != float("inf") else None) for k, v in target.items()},
        "meals_left": meals_left,
        "orders": top_orders,
        "over_budget": over_budget[:10],  # cap to keep response manageable
        "treats_excluded": not include_treats,
    }
