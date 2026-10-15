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

# Base per-macro weights for the distance-based fit score.
# Protein gets a higher weight so under-hitting the protein target is penalised
# more than missing kcal/carb/fat — this implements the "protein-forward tiebreak"
# WITHOUT allowing it to override kcal/fat overshoot (see _OVERSHOOT_FACTORS).
_MACRO_WEIGHTS = {"kcal": 0.5, "protein_g": 2.0, "carb_g": 0.5, "fat_g": 0.5}

# Extra multiplier applied on top of _MACRO_WEIGHTS when a combo OVERSHOOTS the
# per-meal target on a given macro.  Values > 1 mean "going over is worse than
# going under by the same fraction."
#   • kcal/fat: ×4 → overshoot is 4× more costly than undershoot.
#     A combo 50 % over on kcal/fat scores far worse than one that's 50 % under.
#   • protein: ×0.3 → overshooting protein is FORGIVEN (more protein is acceptable).
#     Effective overshoot cost = 2.0 × 0.3 = 0.6, less than the undershoot cost of 2.0.
#   • carb: ×2 → mild overshoot penalty (carbs are not as sensitive as fat).
_OVERSHOOT_FACTORS = {"kcal": 4.0, "protein_g": 0.3, "carb_g": 2.0, "fat_g": 4.0}

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

# ── Item role classification (main / side / drink) ────────────────────────────
# Used to enforce realistic combo composition (≤1 main, ≤1 side per order).
# Prefer broad terms over per-item specifics — same philosophy as _TREAT_KEYWORDS.
# Drinks that are also treats (shake) are excluded before we reach this classifier.

_MAIN_KEYWORDS = frozenset([
    "burger", "cheeseburger", "hamburger",
    "big mac",
    "quarter pounder",
    "filet-o-fish", "filet o fish",
    "mcchicken",
    "mcnugget", "nugget",
    "mcmuffin",
    "mcgriddle",
    "biscuit",       # biscuit sandwiches (biscuit alone is breakfast item, safe catch)
    "wrap",
    "sandwich",
    "burrito",
    "big breakfast", "deluxe breakfast",
])

_SIDE_KEYWORDS = frozenset([
    "fries", "french fries",
    "hash brown",
    "salad",
    "fruit n yogurt parfait", "yogurt parfait",
    "apple slice",
    "hotcake",       # standalone hotcakes (not as part of big breakfast)
    "yogurt",
])

_DRINK_KEYWORDS = frozenset([
    "coffee",
    "latte",
    "soft drink", "soda",
    "juice",
    " milk",         # leading space avoids "milkshake" (already a treat)
    "smoothie",      # smoothie is also a treat; included for completeness
    "cola", "sprite", "fanta", "orange drink",
])

# Role literal type (string for simplicity — no enum dependency)
_ROLE_MAIN  = "main"
_ROLE_SIDE  = "side"
_ROLE_DRINK = "drink"
_ROLE_OTHER = "other"  # uncategorised — treated as side for composition purposes


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_treat(food_name: str) -> bool:
    """Return True if the food name contains a treat/dessert keyword.

    Classification approach: the DB foods table has no category column in the
    current schema, so we match against a keyword list.  When a category field
    is added to the schema, update this function to prefer it.
    """
    name_lower = food_name.lower()
    return any(kw in name_lower for kw in _TREAT_KEYWORDS)


def classify_role(food_name: str) -> str:
    """Classify a food item as 'main', 'side', 'drink', or 'other'.

    Classification is keyword-based (case-insensitive substring match), mirroring
    the treats classifier pattern.  'other' is treated as a side for the purpose
    of combo composition (it can only appear once in an order alongside a main).

    Priority: main > side > drink > other.  A 'Big Mac Wrap' matches main first.
    """
    name_lower = food_name.lower()
    if any(kw in name_lower for kw in _MAIN_KEYWORDS):
        return _ROLE_MAIN
    if any(kw in name_lower for kw in _SIDE_KEYWORDS):
        return _ROLE_SIDE
    if any(kw in name_lower for kw in _DRINK_KEYWORDS):
        return _ROLE_DRINK
    return _ROLE_OTHER


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
    """Rank how close a combo is to the per-meal target (higher score = better fit).

    Returns a score ∈ (0, 1] where 1.0 is a perfect match.  The score is based
    on an asymmetric weighted distance:

        penalty_k = relative_error_k × _MACRO_WEIGHTS[k]
                    × (_OVERSHOOT_FACTORS[k] if overshooting else 1.0)

        relative_error_k = |used_k − target_k| / target_k

        score = 1 / (1 + Σ penalty_k)

    Key properties:
    • Overshooting kcal or fat is penalised 4× harder than undershooting by the
      same fraction.  A combo 50 % over on kcal/fat scores far below a combo that
      is 50 % under on those macros.
    • Undershooting protein (not hitting the target) is penalised with weight 2.0,
      making protein the primary tiebreaker among otherwise similar combos.
    • Overshooting protein is forgiven (effective factor 0.6), because having more
      protein than targeted is acceptable for this use-case.
    • Unconstrained macros (target = inf or 0) are skipped to avoid division by zero.

    This replaces the old clamped-ratio approach, which gave every over-target
    combo a perfect 1.0 similarity on that macro (because min(used/target, 1) == 1
    for any overshoot).  The old approach let a 3-burger stack score identically
    to an exact match on kcal/fat.
    """
    total_penalty = 0.0
    for k in _BUDGET_MACROS:
        t = target.get(k, float("inf"))
        if t == float("inf") or t <= 0:
            continue
        used = combo_macros.get(k, 0.0)
        relative_error = abs(used - t) / t
        base_w = _MACRO_WEIGHTS[k]
        if used > t:
            # Overshoot: apply extra factor (kcal/fat overshoot costs much more)
            total_penalty += relative_error * base_w * _OVERSHOOT_FACTORS[k]
        else:
            # Undershoot: normal weight
            total_penalty += relative_error * base_w
    return 1.0 / (1.0 + total_penalty)


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
            "role": classify_role(food["name"]),
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

            # ── Realistic combo composition ──────────────────────────────────
            # At most ONE main (burger/sandwich/wrap/etc.) per order.
            # At most ONE side (fries/salad/etc. and 'other' items) per order.
            # Drinks are zero/low-calorie and may appear once alongside.
            # This prevents stacking 2-3 mains (the original bug that prompted
            # the overshoot scoring fix).  Treat items are already excluded above.
            roles = [it["role"] for it in combo_items]
            main_count  = roles.count(_ROLE_MAIN)
            # 'other' items are treated as sides for composition purposes
            side_count  = roles.count(_ROLE_SIDE) + roles.count(_ROLE_OTHER)
            drink_count = roles.count(_ROLE_DRINK)
            if main_count > 1 or side_count > 1 or drink_count > 1:
                continue  # skip unrealistic stacks

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
                    "role": it["role"],
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
