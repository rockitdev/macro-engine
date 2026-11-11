"""Macro Engine MCP server (stdio).

Register with Claude Code:
  claude mcp add macro-engine -- /home/moore/projects/macro-engine/.venv/bin/python \
      /home/moore/projects/macro-engine/mcp_server.py
or via .mcp.json (see README).
"""

from mcp.server.fastmcp import FastMCP

from macro_engine import coach, db, fit_meal as _fit_meal_mod, resolve, tracker

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
        "log. One-time setup, zero-friction forever.\n\n"
        "Coaching (evidence-based, from Fat Loss Forever): the user weighs in "
        "daily -> log_weight. Judge progress only by weekly averages, never a "
        "single weigh-in (1-2% daily swings are water; up to 4% premenstrually). "
        "Setup once: set_profile, then plan_targets(apply=true). Once a week "
        "run check_in and relay its reasons; only apply a change after the "
        "user agrees. Coach tone: no good/bad/cheat-food language, praise "
        "consistency, focus on the next choice after a slip. A known overeat "
        "is paid back over the following days (e.g. +1000 kcal = -250 x 4 "
        "days) via the week bank — never by crash restriction, and never after "
        "an emotional binge (just resume the plan)."
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

    Alcohol (7 kcal/g) has no macro slot: log drinks as a manual estimate with
    the drink's kcal assigned to carbs (kcal/4) and/or fat (kcal/9) so that
    4*carb + 9*fat = kcal, e.g. a 99 kcal light beer -> carb_g 25. Fiber is
    counted inside carbs — never log "net carbs".

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
def week_summary(date: str | None = None) -> dict:
    """The seven-day week containing date (YYYY-MM-DD, default today), starting
    on the user's week_starts_on day. 'saved' is kcal/carb/fat left unused on
    earlier days this week (negative = over); 'room_today' is today's remaining
    plus that bank. Protein and fiber are weekly totals, not banked. Use for
    'how's my week going' and 'can I afford a bigger dinner tonight'."""
    con = _con()
    try:
        return tracker.week_summary(con, date)
    finally:
        con.close()


@mcp.tool()
def month_summary(date: str | None = None) -> dict:
    """The calendar month containing date (default today), grouped into the
    user's weeks. 'finished' sums completed days, 'through_today' includes
    today; both null before the month starts."""
    con = _con()
    try:
        return tracker.month_summary(con, date)
    finally:
        con.close()


@mcp.tool()
def set_week_start(day: str) -> dict:
    """Set the weekday a week begins on ('sunday', 'mon', ...). Past weeks
    regroup; log rows are untouched."""
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
def delete_log_entry(log_id: int) -> dict:
    """Remove a log entry (corrections). Get log_id from day_summary."""
    con = _con()
    try:
        return {"deleted": tracker.delete_log_entry(con, log_id), "log_id": log_id}
    finally:
        con.close()


@mcp.tool()
def fit_meal(restaurant: str, remaining: dict | None = None,
             date: str | None = None) -> dict:
    """Find concrete fast-food orders that fit the day's remaining macro budget.

    Given a restaurant or chain name, returns 2-3 ranked orders (single items
    or small combos of up to 3 items) that land INSIDE the remaining budget,
    plus a list of popular items that would blow a macro so Ryan knows what to
    avoid.

    IMPORTANT — portion-aware math: the database stores macros per 100 g.
    fit_meal scales every item by its actual serving weight before comparing
    to the budget, so a Big Mac (219 g) contributes ~562 kcal, not the raw
    257 kcal/100g figure.

    Args:
        restaurant: Chain name as Ryan says it — "McDonald's", "Tim Hortons",
                    "Subway", etc. The resolver searches name, brand, and store
                    columns with FTS, so partial names work.
        remaining:  Optional explicit override of the remaining macros dict:
                    {"kcal": 600, "protein_g": 40, "carb_g": 50, "fat_g": 20}.
                    When null (default), reads today's remaining automatically.
                    Useful when Ryan says "I have 600 kcal left" without matching
                    what the tracker currently shows.
        date:       YYYY-MM-DD — the date whose remaining macros to read when
                    remaining is null (default today).

    Returns:
        restaurant  — echoed back
        budget      — {kcal, protein_g, carb_g, fat_g} used as the ceiling
        orders      — ranked list of fitting orders, each with:
                        rank, items (name/portion/grams/macros per item),
                        total (combined macros), fit_score
        over_budget — items that alone exceed at least one budget macro, with
                        which macros they blow — show these as warnings
        note        — present only when the chain is not in the database
    """
    con = _con()
    try:
        return _fit_meal_mod.fit_meal(con, restaurant, remaining, date)
    finally:
        con.close()


# ── Coaching ────────────────────────────────────────────────────────────────


@mcp.tool()
def set_profile(sex: str | None = None, age: float | None = None,
                birth_year: int | None = None, birth_date: str | None = None, height_cm: float | None = None,
                height_in: float | None = None, activity: str | None = None,
                resistance_training: bool | None = None,
                daily_activity: str | None = None,
                strength_days_per_week: int | None = None,
                strength_minutes_per_session: int | None = None,
                goal: str | None = None, goal_loss_kg: float | None = None,
                goal_loss_lb: float | None = None,
                carb_share: float | None = None,
                adjust_with_activity: bool | None = None,
                units: str | None = None) -> dict:
    """Store the body facts the target math needs. Partial updates merge.
    birth_date: YYYY-MM-DD; preferred over approximate age/birth_year.
    daily_activity: seated | on_feet | physical, excluding workouts.
    Strength schedule: days/week (0-7), minutes/session (0-480).
    goal: cut | maintain | reverse. goal_loss_kg OR goal_loss_lb stores an
    approximate long-term loss goal, not a rate or an automatic calorie change.
    Overall activity below includes training; schedule fields don't add calories.
    activity: sedentary (desk, no exercise) | light (desk + some exercise, or
    on-feet job) | moderate (desk + hard training — most lifters) | very (hard
    training + on-feet job) | extra (hard training + physical labour).
    carb_share: fraction of NON-protein calories from carbs (default 0.6;
    0.2-0.8 — pure preference, fat loss is equal). adjust_with_activity: on a
    plateau, prefer -5% carbs/fat + 5% more exercise time over -10% food only.
    units: kg|lb for how weights are shown."""
    con = _con()
    try:
        return coach.set_profile(con, sex=sex, age=age, birth_year=birth_year, birth_date=birth_date,
                                 height_cm=height_cm, height_in=height_in,
                                 activity=activity, resistance_training=resistance_training,
                                 daily_activity=daily_activity, strength_days_per_week=strength_days_per_week,
                                 strength_minutes_per_session=strength_minutes_per_session,
                                 goal=goal, goal_loss_kg=goal_loss_kg, goal_loss_lb=goal_loss_lb,
                                 carb_share=carb_share, adjust_with_activity=adjust_with_activity,
                                 units=units)
    finally:
        con.close()


@mcp.tool()
def log_weight(weight: float | None = None, unit: str = "kg", date: str | None = None,
               body_fat_pct: float | None = None, waist: float | None = None,
               hips: float | None = None, length_unit: str = "cm",
               note: str | None = None) -> dict:
    """Record a day's morning weigh-in (unit kg|lb) and optional body-fat %,
    waist (at the navel) and hips (widest point) in cm|in. One row per date;
    re-logging a date updates only the fields given. Returns the row plus this
    week's running average. Never react to a single weigh-in — daily swings of
    1-2% are water/glycogen/sodium, not fat."""
    con = _con()
    try:
        row = coach.log_metrics(con, date, weight=weight, unit=unit,
                                body_fat_pct=body_fat_pct, waist=waist, hips=hips,
                                length_unit=length_unit, note=note)
        week = coach.weekly_trend(con, row["date"], weeks=2)
        return {"logged": row, "this_week": week[-1], "last_week": week[0]}
    finally:
        con.close()


@mcp.tool()
def progress(date: str | None = None, weeks: int = 12) -> dict:
    """Coaching dashboard: weekly-average weight trend (with waist/body-fat),
    measured vs predicted maintenance (a big negative gap = metabolic
    adaptation), current phase and week number, 14-day adherence (logging
    coverage, days within 5% of kcal, protein hits, weekend drift), and the
    last check-in. Use for 'how am I doing' questions.

    `activity` holds Apple Watch context: Watch-recorded steps (one selected
    source; today is 'so far' and excluded from averages), resting heart rate,
    workouts (manual entries only: Shortcuts can't read workouts), weekly
    counts with denominators, freshness, and sync/catch-up state. Unknown days
    are not zero. Describe it ("steps recorded on 5 of 7 days"); never use it
    to change targets or draw medical/recovery conclusions."""
    con = _con()
    try:
        return coach.progress(con, date, weeks)
    finally:
        con.close()


@mcp.tool()
def plan_targets(goal: str = "cut", rate_pct: float | None = None,
                 carb_share: float | None = None,
                 maintenance_kcal: float | None = None,
                 target_body_fat_pct: float | None = None,
                 diet_weeks: int | None = None, break_weeks: int | None = None,
                 pace: str | None = None, date: str | None = None,
                 apply: bool = False) -> dict:
    """Compute daily targets the Fat Loss Forever way and (apply=true) save
    them and start the matching phase. Preview first, show the user, then
    apply.

    goal: cut | maintain | reverse.
    rate_pct: % bodyweight lost per week for a cut (default 0.6; 0.4-0.8
      recommended, 1.0 hard max).
    maintenance_kcal: override; otherwise measured from logs + weight trend
      when there are 2+ weeks of data, else predicted from the profile.
    target_body_fat_pct: adds a timeline (weeks to goal).
    diet_weeks/break_weeks: planned diet breaks, e.g. 2 and 1 (max 4 diet
      weeks between breaks). pace (reverse): conservative|moderate|aggressive.
    Returns targets, the maintenance used and its source, deficit, protein
    basis, warnings (VLCD, metabolism-too-suppressed-to-diet gate)."""
    con = _con()
    try:
        return coach.plan_targets(con, goal, rate_pct=rate_pct, carb_share=carb_share,
                                  maintenance_kcal=maintenance_kcal,
                                  target_body_fat_pct=target_body_fat_pct, date=date,
                                  apply=apply, diet_weeks=diet_weeks, break_weeks=break_weeks,
                                  pace=pace)
    finally:
        con.close()


@mcp.tool()
def check_in(date: str | None = None, period: bool = False, high_stress: bool = False,
             poor_sleep: bool = False, illness: bool = False, injury: bool = False,
             new_medication: bool = False, novice_lifter: bool = False,
             scores: dict | None = None, apply: bool = False,
             effective_date: str | None = None) -> dict:
    """Weekly check-in (same weekday each week). Compares this week's average
    weight to last week's against the active phase and returns status, action
    (hold | adjust | fix_adherence | start_break | end_break |
    recommend_reverse | setup), reasons (relay these), and proposed_targets.

    Ask the user about the flags first — period, stress, sleep, illness,
    injury, new medication all cause false plateaus. scores: optional 1-10
    self-ratings {adherence, energy, hunger, mood, stress, sleep, strength}.

    Rules it applies: one slow week is noise; a plateau is 2 consecutive weeks
    under 50% of goal loss; poor logging is ruled out before any cut; cuts
    touch ONLY carbs/fat (5 -> 10 -> 15%, learning the user's threshold);
    reverse diets add 3-10%/week unless weight rose too fast.

    Run with apply=false, explain, and re-run with apply=true only once the
    user agrees. Each call is recorded in check-in history.

    The result includes `activity`: descriptive Watch context (steps coverage,
    resting HR, recorded workouts vs the planned strength routine). It does
    not affect the recommendation; an unrecorded workout is not a skipped one."""
    con = _con()
    try:
        flags = {"period": period, "high_stress": high_stress, "poor_sleep": poor_sleep,
                 "illness": illness, "injury": injury, "new_medication": new_medication,
                 "novice_lifter": novice_lifter}
        return coach.check_in(con, date, flags=flags, scores=scores, apply=apply,
                              effective_date=effective_date)
    finally:
        con.close()


@mcp.tool()
def start_phase(kind: str, start_date: str | None = None, rate_pct: float | None = None,
                pace: str | None = None, diet_weeks: int | None = None,
                break_weeks: int | None = None,
                maintenance_kcal: float | None = None) -> dict:
    """Begin a diet phase without recomputing targets: cut (rate_pct, optional
    diet_weeks/break_weeks), maintenance, reverse (pace conservative|moderate|
    aggressive), or diet_break. After a cut, go straight to maintenance or a
    reverse — never a free-for-all 'cheat week'; post-diet bodies regain fat
    fast. plan_targets(apply=true) starts phases too."""
    con = _con()
    try:
        params = {k: v for k, v in (("rate_pct", rate_pct), ("pace", pace),
                                    ("diet_weeks", diet_weeks), ("break_weeks", break_weeks),
                                    ("maintenance_kcal", maintenance_kcal)) if v is not None}
        return coach.start_phase(con, kind, start_date, **params)
    finally:
        con.close()


@mcp.tool()
def plan_high_days(high_days: int = 2, high_kcal: float | None = None,
                   date: str | None = None) -> dict:
    """Split the same weekly calories into low days and 1-3 higher (refeed)
    days — e.g. bigger weekends. High days default to maintenance, carbs fill
    the extra, fat held, protein at the non-deficit level. Capped at 2x a low
    day. Returns a plan to show the user; it doesn't change targets (the week
    bank already carries unused low-day calories forward)."""
    con = _con()
    try:
        return coach.plan_high_days(con, high_days, high_kcal, date)
    finally:
        con.close()


if __name__ == "__main__":
    mcp.run()
