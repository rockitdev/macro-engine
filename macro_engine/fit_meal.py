"""fit_meal — combinatorial macro-budget solver for restaurant orders.

Given a restaurant/chain name and the day's remaining macros, return 2-3
concrete orders (single items or small combos) that fit within the budget,
ranked by how well they use it (closest without exceeding, protein-forward).
Also flags menu items that individually blow one or more macros.

Critical: DB stores ALL macros per 100 g in food_nutrients. A Big Mac row
may show 257 kcal, but the serving is 219 g → 562 kcal actual. The solver
always scales by (portion_grams / 100) before comparing to the budget.
Never read the raw per-100g column as if it were a serving.
"""

import itertools
import sqlite3
from typing import Optional

from . import resolve, tracker

# The four macros we budget against. fiber_g is informational only.
_BUDGET_MACROS = ("kcal", "protein_g", "carb_g", "fat_g")

# Protein is twice as important as carbs/fat when ranking how well an order
# fills the budget. kcal is a tie-breaker.
_WEIGHTS = {"kcal": 0.2, "protein_g": 1.5, "carb_g": 0.4, "fat_g": 0.4}

# Items fetched from the search for the chain before solving. 40 is enough for
# any chain while keeping triple-combo enumeration fast (~10k iterations max).
_SEARCH_LIMIT = 40

# Largest combo size to try (1 item, 2 items, or 3 items).
_MAX_COMBO = 3

# How many orders to return.
_TOP_N = 3


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


def _fits(combo_macros: dict, eff_budget: dict) -> bool:
    """Return True only if every macro in combo_macros is ≤ eff_budget.

    eff_budget values of float('inf') mean unconstrained (no target set).
    A tiny epsilon (0.05 g / kcal) absorbs floating-point drift.
    """
    for k in _BUDGET_MACROS:
        if combo_macros.get(k, 0.0) > eff_budget[k] + 0.05:
            return False
    return True


def _score(combo_macros: dict, eff_budget: dict) -> float:
    """Rank how well a combo fills the budget (higher = better).

    Ratio of each macro used vs. available, weighted so protein wins ties.
    Unconstrained macros (budget = inf) are skipped to avoid zero division.
    """
    s = 0.0
    for k, w in _WEIGHTS.items():
        b = eff_budget[k]
        if b != float("inf") and b > 0:
            s += w * (combo_macros.get(k, 0.0) / b)
    return s


def _sum_macros(macros_list: list[dict]) -> dict:
    return {k: round(sum(m.get(k, 0.0) for m in macros_list), 1) for k in _BUDGET_MACROS}


def fit_meal(
    con: sqlite3.Connection,
    restaurant: str,
    remaining: Optional[dict] = None,
    date: Optional[str] = None,
) -> dict:
    """Find concrete restaurant orders that fit the remaining macro budget.

    Args:
        restaurant: Chain/restaurant name ("McDonald's", "Tim Hortons", …).
        remaining:  Optional explicit remaining macros dict with keys kcal,
                    protein_g, carb_g, fat_g.  When None the function reads
                    today's remaining from the tracker (or the given date).
        date:       YYYY-MM-DD for auto-pulled remaining (default today).

    Returns a dict with keys:
        restaurant  – echoed back
        budget      – {kcal, protein_g, carb_g, fat_g} that was used
        orders      – list of up to 3 ranked orders, each with:
                        rank, items (name/portion/grams/macros), total macros,
                        fit_score
        over_budget – items that individually exceed at least one budget macro,
                      with which macros they blow (useful warning to show user)
        note        – present only when the chain isn't in the DB
    """
    # ── 1. Resolve budget ──────────────────────────────────────────────────
    if remaining is None:
        rem = tracker.remaining(con, date)
        budget = rem.get("remaining") or {}
    else:
        budget = {k: remaining.get(k) for k in _BUDGET_MACROS}

    # None means "no target set for that macro" → treat as unconstrained
    eff_budget: dict[str, float] = {
        k: float(budget.get(k)) if budget.get(k) is not None else float("inf")
        for k in _BUDGET_MACROS
    }

    # ── 2. Search for the chain's foods ───────────────────────────────────
    candidates = resolve.search(con, restaurant, limit=_SEARCH_LIMIT)
    if not candidates:
        return {
            "restaurant": restaurant,
            "budget": {k: budget.get(k) for k in _BUDGET_MACROS},
            "orders": [],
            "over_budget": [],
            "note": (
                f"No foods found for '{restaurant}' — the chain may not be in "
                "the database yet. Ask the recon agent to import its menu."
            ),
        }

    # ── 3. Compute per-serving macros (portion-aware) ──────────────────────
    items: list[dict] = []
    for food in candidates:
        if food.get("kcal") is None:
            continue  # skip rows with no nutrition data
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
            "budget": {k: budget.get(k) for k in _BUDGET_MACROS},
            "orders": [],
            "over_budget": [],
            "note": f"Foods found for '{restaurant}' but none have macro data.",
        }

    # ── 4. Flag items that individually blow a budget macro ────────────────
    over_budget: list[dict] = []
    for item in items:
        blows = [
            k for k in _BUDGET_MACROS
            if eff_budget[k] != float("inf")
            and item["macros"].get(k, 0.0) > eff_budget[k] + 0.05
        ]
        if blows:
            over_budget.append({
                "name": item["name"],
                "portion": item["portion"],
                "grams": item["grams"],
                "macros": item["macros"],
                "blows": blows,
            })

    # ── 5. Combinatorial fit search ────────────────────────────────────────
    # Enumerate singles → pairs → triples and keep those that fit.
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
            if _fits(total, eff_budget):
                score = _score(total, eff_budget)
                fitting.append((score, combo_items, total))

    # Best fit first (highest score = most macros used without exceeding)
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
        "budget": {k: budget.get(k) for k in _BUDGET_MACROS},
        "orders": top_orders,
        "over_budget": over_budget[:10],  # cap to keep response manageable
    }
