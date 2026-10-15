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
        "log. One-time setup, zero-friction forever. For the week as a budget "
        "(carbs and fat saved on earlier days and spendable later, such as a "
        "bigger Saturday), call week_summary. Protein is a weekly total, not "
        "a bank. Weeks run from the weekday set by set_week_start (Monday "
        "unless changed) through the day before the next one. month_summary "
        "is the calendar month, with those weeks inside it. Do not move log "
        "rows between days to create that bank."
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
             raw_text: str | None = None, meal: str | None = None) -> dict:
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
    meal is breakfast, lunch, dinner, or snacks and applies to every item in
    this call. An item may set its own "meal" to override. Omit it when the
    user did not name a meal. Unresolved lookups come back under "problems"
    with candidates — never silently dropped."""
    con = _con()
    try:
        return tracker.log_meal(con, items, date, raw_text, meal)
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
    'what have I eaten today' and before correcting entries. Each entry has
    meal: breakfast, lunch, dinner, snacks, or null when it was never set."""
    con = _con()
    try:
        return {"entries": tracker.day_log(con, date), **tracker.remaining(con, date)}
    finally:
        con.close()


@mcp.tool()
def week_summary(date: str | None = None) -> dict:
    """The seven-day week containing date (YYYY-MM-DD, default today).

    start and end are inclusive. The week begins on week_starts_on (monday
    unless set_week_start changed it) and ends the day before the next one.
    Name those dates when you tell the user which week you mean.

    Weekly compliance. Carbs, fat, and kcal left unused on earlier days of
    this week are banked in "saved" and can be eaten on a later day while the
    week still finishes on target. Today is not included in "saved" because
    the day is not finished.

    saved is {kcal, carb_g, fat_g}. Positive means those earlier days came in
    under target. Negative means they ran over. Null when today is outside
    this week, or when no earlier day had a target (the first day of the
    week, or no targets yet).

    room_today is how much kcal, carb_g, and fat_g can still be eaten today
    while later days stay at their own targets: today's remaining plus saved.
    It describes the "today" field, not "focus", when the caller asked about
    a different day. Null when today is outside this week or today has no
    target. A negative room means that macro is already past the week's pace
    even if nothing more is eaten today; later days have to come in under.

    Protein is not banked. Read it from week.target, week.eaten, and
    week.left, as "protein eaten of protein target this week". Fiber is not
    banked either. week.left is the whole week, later days included, which is
    a different number from room_today.

    days has the seven dates in order. A day with no target has targets and
    under null; food eaten that day still counts in week.eaten and week.left.
    Do not invent a target for it. A finished week has saved and room_today
    null; use week.left for how it came out (positive left is under target).
    """
    con = _con()
    try:
        return tracker.week_summary(con, date)
    finally:
        con.close()


@mcp.tool()
def month_summary(date: str | None = None) -> dict:
    """Calendar month containing date (YYYY-MM-DD, default today).

    start and end are the first and last calendar days. weeks is every
    week that overlaps the month, using week_starts_on. A week that begins
    in the previous month or ends in the next one is included whole. Say the
    week's own start and end when you talk about that week's bank.

    month sums only days inside the calendar month. finished sums the days
    in the month before today, and is null when the month has not started or
    no day in it has finished. through_today includes today. Positive left
    is under target. Future days are in month but not in finished, so do not
    describe them as missed. Protein is a total in these sums, not a bank.
    """
    con = _con()
    try:
        return tracker.month_summary(con, date)
    finally:
        con.close()


@mcp.tool()
def set_week_start(day: str) -> dict:
    """Set the weekday a week starts on: sunday, monday, tuesday, wednesday,
    thursday, friday, or saturday. Monday is the default. This regroups every
    week, including past ones. It does not move log rows. After changing it,
    call week_summary again and tell the user the new start and end dates."""
    con = _con()
    try:
        return tracker.set_week_start(con, day)
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
def set_log_meal(log_id: int, meal: str | None = None) -> dict:
    """Set or clear the meal on one log entry. meal is breakfast, lunch,
    dinner, or snacks; pass null to clear it. Get log_id from day_summary."""
    con = _con()
    try:
        updated = tracker.set_log_meal(con, log_id, meal)
    finally:
        con.close()
    if not updated:
        return {"updated": False, "log_id": log_id}
    return {"updated": True, **updated}


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
