"""Macro Engine MCP server (stdio).

Register with Claude Code:
  claude mcp add macro-engine -- /home/moore/projects/macro-engine/.venv/bin/python \
      /home/moore/projects/macro-engine/mcp_server.py
or via .mcp.json (see README).
"""

from mcp.server.fastmcp import FastMCP

from macro_engine import db, resolve, tracker

mcp = FastMCP(
    "macro-engine",
    instructions=(
        "Macro tracker backed by USDA FoodData Central. Typical flow: the user "
        "says what they ate in plain language; you split it into items and call "
        "log_meal once. Trust alias matches (matched_alias=true) — they are the "
        "user's learned vocabulary. If a food can't be resolved, log_meal returns "
        "it under 'problems' with candidates; either re-log with a chosen food_id "
        "or supply a manual macros estimate. When the user corrects a wrong "
        "match, fix the log (delete_log_entry + re-log) AND save an alias so it "
        "resolves right next time. Log immediately — never block on ambiguity; "
        "prefer a flagged estimate over asking twice. Store-qualified phrases "
        "('the breaded chicken burgers from Costco') are pantry items: search "
        "first (store names are indexed), and on a miss research the actual "
        "product's label, add_food with store + the user's phrase as alias, then "
        "log. One-time setup, zero-friction forever."
    ),
)


def _con():
    return db.connect()


@mcp.tool()
def search_food(query: str, limit: int = 8) -> list[dict]:
    """Search the food database. Returns ranked candidates with per-100g macros,
    available portions, and matched_alias=true when the phrase is one the user
    has taught us."""
    con = _con()
    try:
        return resolve.search(con, query, limit)
    finally:
        con.close()


@mcp.tool()
def log_meal(items: list[dict], date: str | None = None,
             raw_text: str | None = None) -> dict:
    """Log one or more eaten items and get back the day's remaining macros.

    Each item is a dict, one of two shapes:
    1. Lookup: {"query": "peanut butter", "qty": 2, "unit": "tbsp"} —
       qty defaults to 1; optional "grams" overrides qty/unit; optional
       "food_id" skips search (use after the user picks a candidate). A unit
       must be a mass unit (oz/lb/g, converted directly) or one of the food's
       portions; an unrecognized unit is returned under "problems" with the
       food's available_portions — re-log with grams or a listed portion.
    2. Manual estimate (unresolvable/restaurant guess): {"name": "shawarma plate",
       "macros": {"kcal": 900, "protein_g": 45, "carb_g": 80, "fat_g": 40}} —
       logged with estimated=1 for later reconciliation.

    date is YYYY-MM-DD, default today. raw_text is the user's original phrasing.
    Unresolved lookups come back under "problems" with candidates — never
    silently dropped."""
    con = _con()
    try:
        return tracker.log_meal(con, items, date, raw_text)
    finally:
        con.close()


@mcp.tool()
def remaining(date: str | None = None) -> dict:
    """The headline answer: macros eaten, targets, and what's left for the day
    (YYYY-MM-DD, default today)."""
    con = _con()
    try:
        return tracker.remaining(con, date)
    finally:
        con.close()


@mcp.tool()
def day_summary(date: str | None = None) -> dict:
    """Full day view: every log entry plus totals/targets/remaining. Use for
    'what have I eaten today' and before correcting entries."""
    con = _con()
    try:
        return {"entries": tracker.day_log(con, date), **tracker.remaining(con, date)}
    finally:
        con.close()


@mcp.tool()
def set_targets(kcal: float, protein_g: float, carb_g: float, fat_g: float,
                fiber_g: float | None = None,
                effective_date: str | None = None) -> dict:
    """Set daily macro targets from effective_date (default today) onward.
    fiber_g is optional (daily fibre goal in grams). Append-only; the latest
    row effective on a given date wins, so history stays intact when targets
    change."""
    con = _con()
    try:
        return tracker.set_targets(con, kcal, protein_g, carb_g, fat_g, fiber_g, effective_date)
    finally:
        con.close()


@mcp.tool()
def add_food(name: str, kcal: float, protein_g: float, carb_g: float, fat_g: float,
             fiber_g: float | None = None, brand: str | None = None,
             store: str | None = None, portion_label: str | None = None,
             portion_grams: float | None = None,
             macros_are_per_portion: bool = False,
             alias: str | None = None) -> dict:
    """Add a custom food the database doesn't have — pantry staples the user
    knows by store ('the chicken burgers from Costco'), local products, home
    staples. Macros are per 100 g unless macros_are_per_portion=True (then
    portion_grams is required and values are read as per-portion, e.g. straight
    off the label: 1 burger 113 g, 210 kcal). Set store so future 'from costco'
    phrases match, portion_label/grams for natural quantities ('1 burger'),
    and alias to the user's exact phrasing so next time resolves instantly.
    Use real label data when you can get it; only estimate when you can't,
    and say so."""
    con = _con()
    try:
        return tracker.add_food(con, name, kcal, protein_g, carb_g, fat_g,
                                fiber_g, brand, store, portion_label,
                                portion_grams, macros_are_per_portion, alias)
    finally:
        con.close()


@mcp.tool()
def add_alias(phrase: str, food_id: int, default_grams: float | None = None) -> dict:
    """Teach the resolver the user's vocabulary: map a phrase (e.g. 'toast') to a
    specific food, optionally with their usual amount in grams. Call this whenever
    the user confirms or corrects a food match."""
    con = _con()
    try:
        return tracker.add_alias(con, phrase, food_id, default_grams)
    finally:
        con.close()


@mcp.tool()
def delete_log_entry(log_id: int) -> dict:
    """Remove a log entry (corrections). Get log_id from day_summary."""
    con = _con()
    try:
        return {"deleted": tracker.delete_log_entry(con, log_id), "log_id": log_id}
    finally:
        con.close()


if __name__ == "__main__":
    mcp.run()
