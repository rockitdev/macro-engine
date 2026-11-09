"""Evidence-based coaching layer: body metrics, trend, maintenance, targets,
weekly check-ins, diet phases.

Every rule here is from *Fat Loss Forever* (Norton & Baker, 2019); page
numbers in comments are the book's running page numbers. The short version:

- Weigh daily, judge by WEEKLY AVERAGES compared week over week (p.120, 216).
  Day-to-day swings of 1-2% are noise (p.119, 222); premenstrually up to 4% (p.284).
- Maintenance is best measured, not predicted: intake +/- tissue energy change
  (p.125-127). Equations (Muller preferred, revised Harris-Benedict without a
  body-fat reading) are only a starting point (p.121-123).
- Deficit = bodyweight x rate% x energy factor, where the factor depends on how
  much of the loss is fat vs lean (Tables 1-2, p.130, 136). Rate 0.4-0.8%/wk,
  never above 1% (p.127-128).
- Protein is set in grams from lean mass and age (Table, p.173), then carbs and
  fat split the remainder by preference (default 60/40, p.186), fiber 10-15 g
  per 1000 kcal (p.191).
- Plateau = two consecutive weekly check-ins under 50% of the goal loss (p.222).
  Before cutting, rule out poor adherence and false plateaus (p.225-233). Then
  cut ONLY carbs/fat, starting at 5% (+activity) or 10%, escalating to 15%; once
  a cut works, try 2.5% less next time (p.226-229). If 15% fails: reverse (p.229).
- Reverse diet: +2-10% carbs/fat a week unless the weekly average rose more
  than 0.2-0.5% or rose three weeks running (p.249-252).
- Don't start a diet if maintenance is <= 75% of predicted (p.257-258).

Stdlib only. Storage lives in db.py (body_metrics, phases, checkins, settings).
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import sqlite3

from . import activity as activity_context, tracker

# ── Constants ────────────────────────────────────────────────────────────────

KCAL_PER_KG_FAT_TISSUE = 7830.0   # 87% lipid x 9 kcal/g (p.127)
KCAL_PER_KG_LEAN_TISSUE = 1200.0  # 30% protein x 4 kcal/g (p.127)
LB_PER_KG = 2.20462
CM_PER_IN = 2.54

ACTIVITY = {  # p.124
    "sedentary": 1.2,    # desk job, no exercise
    "light": 1.375,      # desk job + some exercise, or on-feet job
    "moderate": 1.55,    # desk job + hard training ("most of you")
    "very": 1.725,       # hard training most days + on-feet job
    "extra": 1.9,        # hard training + physically intense job
}

# Protein, g per kg lean body mass, by age bracket (p.173). (low, high)
PROTEIN_LBM = {
    "deficit":     [(30, 2.2, 2.4), (40, 2.4, 2.8), (50, 2.8, 3.1), (60, 3.1, 3.5), (200, 3.5, 3.8)],
    "non_deficit": [(30, 1.8, 2.0), (40, 2.0, 2.3), (50, 2.3, 2.6), (60, 2.6, 2.9), (200, 2.9, 3.2)],
}

# Fraction of weight lost as fat mass (Table 1, p.130), by body-fat category and
# [normal protein no RT, high protein no RT, normal protein + RT, high protein + RT].
FAT_FRACTION = {
    "obese":      [0.80, 0.90, 0.90, 0.95],
    "overweight": [0.70, 0.80, 0.80, 0.90],
    "normal":     [0.60, 0.70, 0.70, 0.80],
    "lean":       [0.50, 0.60, 0.60, 0.70],
}
# Body-fat cut-offs from Table 2 (p.136): (obese >, overweight >, normal >=)
BF_CUTOFFS = {"male": (27, 20, 10), "female": (38, 33, 21)}

PHASE_KINDS = ("cut", "maintenance", "reverse", "diet_break")
REVERSE_PACE = {  # (+% carbs/fat per week, max acceptable weekly gain %) p.250-251
    "conservative": (3.0, 0.2),
    "moderate": (6.0, 0.35),
    "aggressive": (10.0, 0.5),
}
MIN_WEIGHINS_PER_WEEK = 3   # fallback minimum the book accepts (p.217)
ADHERENCE_TOLERANCE = 0.05  # within 5% of each macro = 100% compliance (p.192)


# ── Small helpers ────────────────────────────────────────────────────────────

def _r5(x: float) -> int:
    """Round grams to the nearest 5 (the book's convention in every example)."""
    return int(5 * round(x / 5.0))


def _date(value, default: str | None = None) -> str:
    if value in (None, ""):
        return default or tracker._today()
    return tracker._valid_date(value)


def _d(s: str) -> _dt.date:
    return _dt.date.fromisoformat(s)


def _add(s: str, days: int) -> str:
    return (_d(s) + _dt.timedelta(days=days)).isoformat()


def _week_start(con: sqlite3.Connection, date: str) -> str:
    starts_on = tracker.get_week_start(con)
    d = _d(date)
    delta = (d.weekday() - tracker.WEEKDAYS.index(starts_on)) % 7
    return (d - _dt.timedelta(days=delta)).isoformat()


def _kcal_of(p: float, c: float, f: float) -> int:
    return int(round(4 * p + 4 * c + 9 * f))


def _get_setting(con, key: str):
    row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else None


def _put_setting(con, key: str, value) -> None:
    con.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


# ── Profile ──────────────────────────────────────────────────────────────────

PROFILE_DEFAULTS = {
    "sex": None,                 # "male" | "female"
    "birth_year": None,
    "birth_date": None,
    "height_cm": None,
    "activity": "moderate",
    "resistance_training": True,
    "daily_activity": None,     # seated | on_feet | physical; outside workouts
    "strength_days_per_week": None,
    "strength_minutes_per_session": None,
    "goal": None,               # cut | maintain | reverse
    "goal_loss_kg": None,       # approximate long-term aim, not a weekly rate
    "carb_share": 0.6,           # of non-protein kcal (p.186: 55/45-60/40 default)
    "adjust_with_activity": True,  # first plateau move: -5% C/F + 5% activity (p.229)
    "units": "kg",
}


def get_profile(con: sqlite3.Connection) -> dict:
    stored = _get_setting(con, "profile") or {}
    return {**PROFILE_DEFAULTS, **stored}


def set_profile(con: sqlite3.Connection, **fields) -> dict:
    """Merge profile fields without changing targets or starting a phase.

    Accepts height_in, age and goal_loss_lb as unit/age conveniences.
    A full birth_date takes precedence over the legacy birth_year.
    """
    prof = get_profile(con)
    requested_age = fields.get("age")
    if fields.get("goal_loss_lb") is not None:
        if isinstance(fields["goal_loss_lb"], bool):
            raise ValueError("goal_loss_lb must be a number")
        if fields.get("goal_loss_kg") is not None:
            raise ValueError("give goal_loss_lb or goal_loss_kg, not both")
        fields["goal_loss_kg"] = tracker._num(fields.pop("goal_loss_lb"), "goal_loss_lb", positive=True) / LB_PER_KG
    else:
        fields.pop("goal_loss_lb", None)
    if fields.get("height_in") is not None:
        fields["height_cm"] = tracker._num(fields.pop("height_in"), "height_in", positive=True) * CM_PER_IN
    else:
        fields.pop("height_in", None)
    if fields.get("age") is not None:
        age = tracker._num(fields.pop("age"), "age", positive=True)
        fields["birth_year"] = _dt.date.today().year - int(round(age))
    else:
        fields.pop("age", None)
    for key, value in fields.items():
        if value is None:
            continue
        if key not in PROFILE_DEFAULTS:
            raise ValueError(f"unknown profile field {key!r}")
        if value == "" and key in ("birth_date", "daily_activity", "strength_days_per_week",
                                   "strength_minutes_per_session", "goal", "goal_loss_kg"):
            prof[key] = None
            continue
        if key == "sex":
            v = str(value).strip().lower()
            v = {"m": "male", "f": "female", "man": "male", "woman": "female"}.get(v, v)
            if v not in ("male", "female"):
                raise ValueError("sex must be male or female (used by the BMR equations)")
            value = v
        elif key == "birth_date":
            value = tracker._valid_date(value)
            born = _d(value)
            today = _dt.date.today()
            age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
            if born.year < 1900 or age < 10:
                raise ValueError("birth_date out of range")
        elif key == "birth_year":
            value = int(tracker._num(value, "birth_year", positive=True))
            if not 1900 <= value <= _dt.date.today().year - 10:
                raise ValueError("birth_year out of range")
        elif key == "height_cm":
            value = round(tracker._num(value, "height_cm", positive=True), 1)
            if not 100 <= value <= 250:
                raise ValueError("height_cm must be 100-250")
        elif key == "activity":
            value = str(value).strip().lower()
            if value not in ACTIVITY:
                raise ValueError("activity must be one of " + ", ".join(ACTIVITY))
        elif key == "daily_activity":
            if value not in ("seated", "on_feet", "physical"):
                raise ValueError("daily_activity must be seated, on_feet, or physical")
        elif key in ("strength_days_per_week", "strength_minutes_per_session"):
            if isinstance(value, bool):
                raise ValueError(f"{key} must be a number")
            n = tracker._num(value, key)
            maximum = 7 if key == "strength_days_per_week" else 480
            if n != int(n) or not 0 <= n <= maximum:
                raise ValueError(f"{key} must be an integer from 0 to {maximum}")
            value = int(n)
        elif key == "goal":
            if value not in ("cut", "maintain", "reverse"):
                raise ValueError("goal must be cut, maintain, or reverse")
        elif key == "goal_loss_kg":
            if isinstance(value, bool):
                raise ValueError("goal_loss_kg must be a number")
            value = round(tracker._num(value, key, positive=True), 3)
            if not 0 < value <= 300:
                raise ValueError("goal_loss_kg must be greater than 0 and at most 300")
        elif key in ("resistance_training", "adjust_with_activity"):
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be true or false")
        elif key == "carb_share":
            value = tracker._num(value, "carb_share")
            if value > 1:
                value = value / 100.0
            if not 0.2 <= value <= 0.8:
                raise ValueError("carb_share must be 0.2-0.8 of non-protein kcal "
                                 "(below 0.2 is ketogenic territory, p.188)")
        elif key == "units":
            value = str(value).strip().lower()
            if value not in ("kg", "lb"):
                raise ValueError("units must be kg or lb")
        prof[key] = value
    if prof.get("birth_date"):
        born_year = _d(prof["birth_date"]).year
        if requested_age is not None:
            if requested_age != _age(prof):
                raise ValueError("age conflicts with birth_date; update the birth date instead")
        elif fields.get("birth_year") is not None and prof["birth_year"] != born_year:
            raise ValueError("birth_year conflicts with birth_date")
        prof["birth_year"] = born_year
    _put_setting(con, "profile", {k: prof[k] for k in PROFILE_DEFAULTS})
    con.commit()
    return prof


def _age(prof: dict, date: str | None = None) -> int | None:
    as_of = _d(date) if date else _dt.date.today()
    if prof.get("birth_date"):
        born = _d(prof["birth_date"])
        return as_of.year - born.year - ((as_of.month, as_of.day) < (born.month, born.day))
    if not prof.get("birth_year"):
        return None
    return as_of.year - int(prof["birth_year"])


# ── Body metrics ─────────────────────────────────────────────────────────────

def log_metrics(con: sqlite3.Connection, date: str | None = None, *,
                weight: float | None = None, unit: str = "kg",
                body_fat_pct: float | None = None,
                waist: float | None = None, hips: float | None = None,
                length_unit: str = "cm", note: str | None = None) -> dict:
    """Upsert one day's body metrics. Only fields given are changed."""
    date = _date(date)
    unit = (unit or "kg").strip().lower()
    if unit in ("lbs", "pound", "pounds"):
        unit = "lb"
    if unit not in ("kg", "lb"):
        raise ValueError("unit must be kg or lb")
    length_unit = (length_unit or "cm").strip().lower()
    if length_unit in ("inch", "inches"):
        length_unit = "in"
    if length_unit not in ("cm", "in"):
        raise ValueError("length_unit must be cm or in")
    fields: dict = {}
    if weight is not None:
        w = tracker._num(weight, "weight", positive=True)
        fields["weight_kg"] = round(w / LB_PER_KG if unit == "lb" else w, 2)
        if not 25 <= fields["weight_kg"] <= 400:
            raise ValueError("weight out of range — check the unit (kg or lb)")
    if body_fat_pct is not None:
        bf = tracker._num(body_fat_pct, "body_fat_pct", positive=True)
        if not 2 <= bf <= 70:
            raise ValueError("body_fat_pct must be 2-70")
        fields["body_fat_pct"] = round(bf, 1)
    for key, val in (("waist_cm", waist), ("hips_cm", hips)):
        if val is not None:
            v = tracker._num(val, key.split("_")[0], positive=True)
            fields[key] = round(v * CM_PER_IN if length_unit == "in" else v, 1)
    if note is not None:
        fields["note"] = str(note)
    if not fields:
        raise ValueError("give at least one of weight, body_fat_pct, waist, hips, note")
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{k} = excluded.{k}" for k in fields)
    con.execute(
        f"INSERT INTO body_metrics (date, {cols}) VALUES (?, {marks}) "
        f"ON CONFLICT(date) DO UPDATE SET {updates}, updated_at = datetime('now','localtime')",
        (date, *fields.values()),
    )
    con.commit()
    row = con.execute("SELECT * FROM body_metrics WHERE date = ?", (date,)).fetchone()
    out = dict(row)
    if out.get("weight_kg") is not None:
        out["weight_lb"] = round(out["weight_kg"] * LB_PER_KG, 1)
    return out


def delete_metrics(con: sqlite3.Connection, date: str) -> bool:
    cur = con.execute("DELETE FROM body_metrics WHERE date = ?", (_date(date),))
    con.commit()
    return cur.rowcount > 0


def _metrics_between(con, start: str, end: str) -> list[dict]:
    rows = con.execute(
        "SELECT * FROM body_metrics WHERE date BETWEEN ? AND ? ORDER BY date",
        (start, end)).fetchall()
    return [dict(r) for r in rows]


def latest_body_fat(con, date: str, max_age_days: int = 60) -> float | None:
    """Mean of body-fat readings in the most recent week that has any, within
    max_age_days (single readings are noisy; the trend matters, p.119)."""
    row = con.execute(
        "SELECT date FROM body_metrics WHERE body_fat_pct IS NOT NULL AND date <= ? "
        "AND date >= ? ORDER BY date DESC LIMIT 1",
        (date, _add(date, -max_age_days))).fetchone()
    if not row:
        return None
    r = con.execute(
        "SELECT AVG(body_fat_pct) AS bf FROM body_metrics WHERE body_fat_pct IS NOT NULL "
        "AND date BETWEEN ? AND ?", (_add(row["date"], -6), row["date"])).fetchone()
    return round(r["bf"], 1)


def weekly_trend(con: sqlite3.Connection, date: str | None = None, weeks: int = 12) -> list[dict]:
    """Weekly averages for the `weeks` weeks ending with the week containing
    date. Each week: start, end, n weigh-ins, avg_kg (None if fewer than
    MIN_WEIGHINS_PER_WEEK), change vs the previous valid week, and averages of
    waist / hips / body fat when present."""
    date = _date(date)
    last_start = _week_start(con, date)
    first_start = _add(last_start, -7 * (weeks - 1))
    rows = _metrics_between(con, first_start, _add(last_start, 6))
    out = []
    prev = None
    for i in range(weeks):
        ws = _add(first_start, 7 * i)
        we = _add(ws, 6)
        wk = [r for r in rows if ws <= r["date"] <= we]
        ws_vals = [r["weight_kg"] for r in wk if r["weight_kg"] is not None]
        entry = {"start": ws, "end": we, "n": len(ws_vals), "avg_kg": None, "avg_lb": None,
                 "change_kg": None, "change_pct": None}
        if len(ws_vals) >= MIN_WEIGHINS_PER_WEEK:
            avg = sum(ws_vals) / len(ws_vals)
            entry["avg_kg"] = round(avg, 2)
            entry["avg_lb"] = round(avg * LB_PER_KG, 1)
            entry["min_kg"] = min(ws_vals)
            entry["max_kg"] = max(ws_vals)
            if prev is not None:
                entry["change_kg"] = round(avg - prev, 2)
                entry["change_pct"] = round((avg - prev) / prev * 100, 2)
            prev = avg
        for key in ("waist_cm", "hips_cm", "body_fat_pct"):
            vals = [r[key] for r in wk if r[key] is not None]
            entry[key] = round(sum(vals) / len(vals), 1) if vals else None
        out.append(entry)
    return out


def current_weight(con, date: str) -> float | None:
    """Best current bodyweight: latest valid weekly average, else latest weigh-in."""
    for w in reversed(weekly_trend(con, date, weeks=3)):
        if w["avg_kg"] is not None:
            return w["avg_kg"]
    row = con.execute("SELECT weight_kg FROM body_metrics WHERE weight_kg IS NOT NULL "
                      "AND date <= ? ORDER BY date DESC LIMIT 1", (date,)).fetchone()
    return row["weight_kg"] if row else None


# ── Energy math (pure) ───────────────────────────────────────────────────────

def bmr_muller(lbm_kg: float, fm_kg: float, sex: str, age: float) -> float:
    """Muller equation — preferred when body fat is known (p.122)."""
    return 13.587 * lbm_kg + 9.613 * fm_kg + 198 * (1 if sex == "male" else 0) - 3.351 * age + 674


def bmr_harris_benedict(weight_kg: float, height_cm: float, sex: str, age: float) -> float:
    """Revised Harris-Benedict (Roza 1984) — use without a body-fat reading (p.121)."""
    if sex == "male":
        return 88.362 + 13.397 * weight_kg + 4.799 * height_cm - 5.677 * age
    return 447.593 + 9.247 * weight_kg + 3.098 * height_cm - 4.330 * age


def bf_category(sex: str, bf_pct: float | None, bmi: float | None = None) -> str:
    """Obese / overweight / normal / lean per Table 2 cut-offs (p.136). Without
    a body-fat reading, fall back to BMI bands (an approximation, flagged)."""
    if bf_pct is None:
        if bmi is None:
            return "normal"
        return "obese" if bmi >= 30 else "overweight" if bmi >= 25 else "normal"
    obese, over, normal = BF_CUTOFFS.get(sex or "male", BF_CUTOFFS["male"])
    if bf_pct > obese:
        return "obese"
    if bf_pct > over:
        return "overweight"
    if bf_pct >= normal:
        return "normal"
    return "lean"


def energy_factor(category: str, high_protein: bool = True, resistance_training: bool = True) -> float:
    """kcal/day of deficit per kg/week of loss (Table 2, p.136), derived from
    the fat fraction of the loss: (f*7830 + (1-f)*1200) / 7."""
    col = (2 if resistance_training else 0) + (1 if high_protein else 0)
    f = FAT_FRACTION[category][col]
    return (f * KCAL_PER_KG_FAT_TISSUE + (1 - f) * KCAL_PER_KG_LEAN_TISSUE) / 7.0


def protein_target(weight_kg: float, lbm_kg: float | None, age: int | None,
                   in_deficit: bool, bmi: float | None = None) -> tuple[int, str]:
    """Protein grams (rounded to 5) and the basis used.

    With lean mass: the age-bracket table midpoint x LBM (p.173). Without it:
    2.2 g/kg bodyweight (p.174), except for BMI >= 30 where bodyweight
    overshoots badly (p.175) — there, 2.2 g/kg of the weight at BMI 27."""
    if lbm_kg:
        table = PROTEIN_LBM["deficit" if in_deficit else "non_deficit"]
        a = age if age is not None else 30
        lo, hi = next((lo, hi) for top, lo, hi in table if a <= top)
        per = (lo + hi) / 2
        return _r5(lbm_kg * per), f"{per:g} g/kg lean mass (age {a}, {'deficit' if in_deficit else 'maintenance/surplus'})"
    basis_w = weight_kg
    note = "2.2 g/kg bodyweight (no body-fat reading)"
    if bmi and bmi >= 30:
        basis_w = weight_kg * 27 / bmi
        note = "2.2 g/kg of BMI-27 bodyweight (no body-fat reading; BMI >= 30)"
    return _r5(2.2 * basis_w), note


def split_macros(kcal: float, protein_g: float, carb_share: float = 0.6) -> dict:
    """Protein first, then carbs/fat split the remainder (p.171, 186). Fat is
    floored at 20% of total kcal (p.164) and 30% of non-protein kcal (p.188).
    Grams round to 5; kcal is recomputed from the rounded grams so the numbers
    always agree. Fiber: 12.5 g per 1000 kcal (10-15 range, p.191)."""
    rest = max(0.0, kcal - 4 * protein_g)
    fat_share = 1 - carb_share
    fat_kcal = max(rest * fat_share, rest * 0.30, kcal * 0.20)
    fat_kcal = min(fat_kcal, rest)
    carb_kcal = rest - fat_kcal
    p, c, f = _r5(protein_g), _r5(carb_kcal / 4), _r5(fat_kcal / 9)
    total = _kcal_of(p, c, f)
    return {"kcal": total, "protein_g": p, "carb_g": c, "fat_g": f,
            "fiber_g": _r5(12.5 * total / 1000)}


def adjust_carb_fat(targets: dict, pct: float) -> dict:
    """Change ONLY carbs and fat by pct (negative = cut), protein untouched
    (p.227). Whole grams; kcal recomputed. A non-zero change moves each macro by
    at least 1 g."""
    def move(v):
        v = float(v)
        delta = v * pct / 100.0
        if pct and abs(delta) < 1 and v > 0:
            delta = math.copysign(1, pct)
        return max(0, int(round(v + delta)))
    p = targets["protein_g"]
    p = int(p) if float(p).is_integer() else float(p)
    c, f = move(targets["carb_g"]), move(targets["fat_g"])
    kcal = _kcal_of(p, c, f)
    out = {"kcal": kcal, "protein_g": p, "carb_g": c, "fat_g": f}
    out["fiber_g"] = targets.get("fiber_g") if targets.get("fiber_g") is not None else _r5(12.5 * kcal / 1000)
    return out


# ── Maintenance ──────────────────────────────────────────────────────────────

def _body(con, prof: dict, date: str) -> dict:
    """Everything known about the body right now, plus what's missing."""
    weight = current_weight(con, date)
    bf = latest_body_fat(con, date)
    age = _age(prof, date)
    height = prof.get("height_cm")
    bmi = weight / (height / 100) ** 2 if weight and height else None
    lbm = fm = None
    if weight and bf is not None:
        fm = weight * bf / 100
        lbm = weight - fm
    missing = [k for k, v in (("sex", prof.get("sex")), ("age", age), ("height", height),
                              ("weight", weight)) if not v]
    return {"weight_kg": weight, "body_fat_pct": bf, "age": age, "height_cm": height,
            "bmi": round(bmi, 1) if bmi else None, "lbm_kg": round(lbm, 1) if lbm else None,
            "fm_kg": round(fm, 1) if fm else None, "missing": missing}


def predicted_maintenance(con, date: str | None = None, prof: dict | None = None,
                          body: dict | None = None) -> dict | None:
    date = _date(date)
    prof = prof or get_profile(con)
    body = body or _body(con, prof, date)
    sex, age, w = prof.get("sex"), body["age"], body["weight_kg"]
    if not (sex and age and w):
        return None
    if body["lbm_kg"] is not None:
        bmr, eq = bmr_muller(body["lbm_kg"], body["fm_kg"], sex, age), "muller"
    elif body["height_cm"]:
        bmr, eq = bmr_harris_benedict(w, body["height_cm"], sex, age), "harris_benedict_revised"
    else:
        return None
    factor = ACTIVITY[prof.get("activity") or "moderate"]
    return {"bmr": round(bmr), "equation": eq, "activity": prof.get("activity"),
            "activity_factor": factor, "kcal": round(bmr * factor)}


def _day_intakes(con, start: str, end: str) -> dict[str, dict]:
    rows = con.execute(
        "SELECT date, COUNT(*) n, SUM(kcal) kcal, SUM(protein_g) protein_g, "
        "SUM(carb_g) carb_g, SUM(fat_g) fat_g FROM log WHERE date BETWEEN ? AND ? "
        "GROUP BY date", (start, end)).fetchall()
    return {r["date"]: dict(r) for r in rows}


def empirical_maintenance(con: sqlite3.Connection, date: str | None = None,
                          weeks: int = 4) -> dict:
    """Measured maintenance (the book's 'best method', p.125-127):
    average logged intake +/- the energy of the weekly-average weight change.

    Uses the last `weeks` weeks ending with the week containing date. Days
    with no log rows are excluded; so are days under 50% of that day's kcal
    target (almost certainly incomplete logging). Confidence reflects span,
    logging coverage, and weigh-in density."""
    date = _date(date)
    prof = get_profile(con)
    trend = [w for w in weekly_trend(con, date, weeks) if w["avg_kg"] is not None]
    if len(trend) < 2:
        return {"kcal": None, "confidence": "none",
                "reason": f"need weekly averages (>= {MIN_WEIGHINS_PER_WEEK} weigh-ins) "
                          f"in at least 2 weeks; have {len(trend)}"}
    first, last = trend[0], trend[-1]
    span_weeks = (_d(last["start"]) - _d(first["start"])).days / 7
    start, end = first["start"], min(last["end"], date)
    intakes = _day_intakes(con, start, end)
    n_days = (_d(end) - _d(start)).days + 1
    used, skipped = [], 0
    for day, row in intakes.items():
        t = tracker.get_targets(con, day)
        if t and row["kcal"] < 0.5 * t["kcal"]:
            skipped += 1
            continue
        used.append(row)
    if len(used) < 7:
        return {"kcal": None, "confidence": "none",
                "reason": f"only {len(used)} fully-logged days between {start} and {end}"}
    avg_intake = sum(r["kcal"] for r in used) / len(used)
    avg_protein = sum(r["protein_g"] for r in used) / len(used)
    rate = (first["avg_kg"] - last["avg_kg"]) / span_weeks  # kg/week, + = losing
    body = _body(con, prof, date)
    cat = bf_category(prof.get("sex"), body["body_fat_pct"], body["bmi"])
    high_protein = body["weight_kg"] and avg_protein / body["weight_kg"] >= 1.6
    if rate >= 0:
        per_kg_week = energy_factor(cat, bool(high_protein), bool(prof.get("resistance_training")))
    else:  # gaining: book's example assumes 75% fat / 25% lean (p.127)
        per_kg_week = (0.75 * KCAL_PER_KG_FAT_TISSUE + 0.25 * KCAL_PER_KG_LEAN_TISSUE) / 7
    kcal = avg_intake + rate * per_kg_week
    coverage = len(used) / n_days
    avg_n = sum(w["n"] for w in trend) / len(trend)
    if span_weeks >= 3 and coverage >= 0.85 and avg_n >= 5:
        conf = "high"
    elif span_weeks >= 2 and coverage >= 0.7:
        conf = "medium"
    else:
        conf = "low"
    return {
        "kcal": int(round(kcal)), "confidence": conf,
        "avg_intake_kcal": int(round(avg_intake)), "rate_kg_per_week": round(rate, 2),
        "rate_pct_per_week": round(rate / first["avg_kg"] * 100, 2),
        "window": {"start": start, "end": end, "weeks": round(span_weeks, 1)},
        "days_used": len(used), "days_skipped_incomplete": skipped,
        "logging_coverage": round(coverage, 2),
        "note": "Maintenance is a 100-300 kcal range, not a point (p.138).",
    }


# ── Adherence ────────────────────────────────────────────────────────────────

def adherence(con: sqlite3.Connection, start: str, end: str) -> dict:
    """How closely intake matched targets. Within +/-5% of a macro target
    counts as fully compliant (p.192). Also reports weekend vs weekday intake
    because weekend drift silently erases weekday deficits (p.94)."""
    intakes = _day_intakes(con, start, end)
    n = (_d(end) - _d(start)).days + 1
    on_kcal = on_protein = targeted = 0
    over = []
    wkday, wkend = [], []
    for i in range(n):
        day = _add(start, i)
        row = intakes.get(day)
        if not row:
            continue
        (wkend if _d(day).weekday() >= 5 else wkday).append(row["kcal"])
        t = tracker.get_targets(con, day)
        if not t:
            continue
        targeted += 1
        if abs(row["kcal"] - t["kcal"]) <= ADHERENCE_TOLERANCE * t["kcal"]:
            on_kcal += 1
        elif row["kcal"] > t["kcal"]:
            over.append(row["kcal"] - t["kcal"])
        if row["protein_g"] >= (1 - ADHERENCE_TOLERANCE) * t["protein_g"]:
            on_protein += 1
    logged = len(intakes)
    out = {
        "days": n, "logged_days": logged, "logging_coverage": round(logged / n, 2) if n else 0,
        "kcal_on_target_days": on_kcal, "protein_hit_days": on_protein,
        "kcal_compliance": round(on_kcal / targeted, 2) if targeted else None,
        "protein_compliance": round(on_protein / targeted, 2) if targeted else None,
        "avg_overage_kcal": int(round(sum(over) / len(over))) if over else 0,
    }
    if wkday and wkend:
        out["weekday_avg_kcal"] = int(round(sum(wkday) / len(wkday)))
        out["weekend_avg_kcal"] = int(round(sum(wkend) / len(wkend)))
        out["weekend_drift_kcal"] = out["weekend_avg_kcal"] - out["weekday_avg_kcal"]
    return out


# ── Phases ───────────────────────────────────────────────────────────────────

def current_phase(con: sqlite3.Connection, date: str | None = None) -> dict | None:
    date = _date(date)
    row = con.execute("SELECT * FROM phases WHERE start_date <= ? "
                      "ORDER BY start_date DESC, id DESC LIMIT 1", (date,)).fetchone()
    if not row:
        return None
    out = dict(row)
    out["params"] = json.loads(out["params"] or "{}")
    out["week_number"] = (_d(date) - _d(out["start_date"])).days // 7 + 1
    return out


def start_phase(con: sqlite3.Connection, kind: str, start_date: str | None = None,
                **params) -> dict:
    """Begin a diet phase. cut: rate_pct (0.4-1.0, default 0.6),
    diet_weeks/break_weeks for planned diet breaks (e.g. 2 and 1; p.207).
    reverse: pace conservative|moderate|aggressive. maintenance/diet_break:
    maintenance_kcal optional."""
    kind = (kind or "").strip().lower().replace(" ", "_")
    if kind not in PHASE_KINDS:
        raise ValueError("kind must be one of " + ", ".join(PHASE_KINDS))
    start_date = _date(start_date)
    clean = {}
    if kind == "cut":
        rate = tracker._num(params.get("rate_pct", 0.6), "rate_pct", positive=True)
        if rate > 1.0:
            raise ValueError("rate_pct above 1%/week is past the book's absolute max (p.128)")
        clean["rate_pct"] = rate
        dw, bw = params.get("diet_weeks"), params.get("break_weeks")
        if dw or bw:
            dw = int(tracker._num(dw or 2, "diet_weeks", positive=True))
            bw = int(tracker._num(bw or 1, "break_weeks", positive=True))
            if dw > 4:
                raise ValueError("never more than 4 deficit weeks without a break (p.208)")
            clean.update(diet_weeks=dw, break_weeks=bw)
    elif kind == "reverse":
        pace = str(params.get("pace") or "moderate").lower()
        if pace not in REVERSE_PACE:
            raise ValueError("pace must be conservative, moderate, or aggressive")
        clean["pace"] = pace
    if params.get("maintenance_kcal") is not None:
        clean["maintenance_kcal"] = tracker._num(params["maintenance_kcal"], "maintenance_kcal", positive=True)
    if params.get("goal_weight_kg") is not None:
        clean["goal_weight_kg"] = tracker._num(params["goal_weight_kg"], "goal_weight_kg", positive=True)
    t = tracker.get_targets(con, start_date)
    if t:
        clean["start_targets"] = {k: t[k] for k in ("kcal", "protein_g", "carb_g", "fat_g", "fiber_g")}
    w = current_weight(con, start_date)
    if w:
        clean["start_weight_kg"] = w
    cur = con.execute("INSERT INTO phases (kind, start_date, params) VALUES (?, ?, ?)",
                      (kind, start_date, json.dumps(clean)))
    con.commit()
    return {"phase_id": cur.lastrowid, "kind": kind, "start_date": start_date, "params": clean}


# ── Target planning ──────────────────────────────────────────────────────────

def plan_targets(con: sqlite3.Connection, goal: str = "cut", *,
                 rate_pct: float | None = None, carb_share: float | None = None,
                 maintenance_kcal: float | None = None,
                 target_body_fat_pct: float | None = None,
                 date: str | None = None, apply: bool = False,
                 diet_weeks: int | None = None, break_weeks: int | None = None,
                 pace: str | None = None) -> dict:
    """Compute targets the book's way (p.197): maintenance -> deficit from
    rate -> protein -> carbs/fat split -> fiber. goal: cut | maintain | reverse.
    With apply=True, saves the targets (effective date) and starts the
    matching phase."""
    date = _date(date)
    goal = (goal or "cut").strip().lower()
    if goal not in ("cut", "maintain", "maintenance", "reverse"):
        raise ValueError("goal must be cut, maintain, or reverse")
    goal = "maintain" if goal == "maintenance" else goal
    prof = get_profile(con)
    body = _body(con, prof, date)
    warnings, notes = [], []
    if not body["weight_kg"]:
        raise ValueError("no bodyweight logged — log_weight first (daily weigh-ins work best)")

    predicted = predicted_maintenance(con, date, prof, body)
    measured = empirical_maintenance(con, date)
    if maintenance_kcal is not None:
        maint, source = tracker._num(maintenance_kcal, "maintenance_kcal", positive=True), "given"
    elif measured["kcal"] and measured["confidence"] in ("medium", "high"):
        maint, source = measured["kcal"], f"measured ({measured['confidence']} confidence)"
    elif predicted:
        maint, source = predicted["kcal"], f"predicted ({predicted['equation']})"
        notes.append("Equations fit ~60% of people well (p.123). After 2-4 weeks of "
                     "daily weigh-ins and logging, maintenance will be measured instead.")
    else:
        raise ValueError("can't estimate maintenance: set_profile needs "
                         + ", ".join(body["missing"] or ["sex/age/height"])
                         + " (or pass maintenance_kcal)")
    if body["body_fat_pct"] is None:
        notes.append("No body-fat reading: using bodyweight-based protein and the "
                     "Harris-Benedict equation. A weekly body-fat check (same method "
                     "each time) unlocks the more accurate Muller equation (p.122).")

    w = body["weight_kg"]
    cat = bf_category(prof.get("sex"), body["body_fat_pct"], body["bmi"])
    out: dict = {"goal": goal, "date": date, "weight_kg": w, "body": body,
                 "body_fat_category": cat, "maintenance": {"kcal": int(round(maint)), "source": source,
                                                           "predicted": predicted, "measured": measured}}

    # Gate: don't diet from a suppressed metabolism (p.257-258).
    if predicted and measured["kcal"] and measured["confidence"] != "low":
        ratio = measured["kcal"] / predicted["kcal"]
        out["maintenance"]["vs_predicted"] = round(ratio, 2)
        if goal == "cut" and ratio <= 0.75:
            warnings.append(f"Measured maintenance is {ratio:.0%} of predicted. The book's rule "
                            "is not to start a diet at <= 75% (p.257): reverse diet first.")

    carb_share = prof["carb_share"] if carb_share is None else tracker._num(carb_share, "carb_share")
    if carb_share > 1:
        carb_share /= 100
    in_deficit = goal == "cut"
    protein, protein_basis = protein_target(w, body["lbm_kg"], body["age"], in_deficit, body["bmi"])
    if goal == "cut":
        rate = 0.6 if rate_pct is None else tracker._num(rate_pct, "rate_pct", positive=True)
        if rate > 1.0:
            raise ValueError("rate_pct above 1%/week is past the book's absolute max (p.128)")
        if rate < 0.4:
            warnings.append("Below 0.4%/wk the deficit may sit inside the maintenance range "
                            "and produce no loss at all (p.138).")
        if rate > 0.8:
            warnings.append("0.8-1.0%/wk is the aggressive end; expect more lean-mass loss "
                            "and hunger (p.128).")
        factor = energy_factor(cat, True, bool(prof.get("resistance_training")))
        weekly_kg = w * rate / 100
        deficit = weekly_kg * factor
        kcal = maint - deficit
        out["deficit"] = {"rate_pct": rate, "kg_per_week": round(weekly_kg, 2),
                          "lb_per_week": round(weekly_kg * LB_PER_KG, 2),
                          "energy_factor": round(factor), "kcal_per_day": int(round(deficit))}
        if target_body_fat_pct is not None and body["body_fat_pct"] is not None:
            tbf = tracker._num(target_body_fat_pct, "target_body_fat_pct", positive=True)
            if tbf < body["body_fat_pct"]:
                col = 3 if prof.get("resistance_training") else 1
                f = FAT_FRACTION[cat][col]
                total_loss = w * (body["body_fat_pct"] - tbf) / 100 / f  # p.131-132
                out["timeline"] = {"target_body_fat_pct": tbf,
                                   "total_loss_kg": round(total_loss, 1),
                                   "end_weight_kg": round(w - total_loss, 1),
                                   "diet_weeks": round(total_loss / weekly_kg, 1)}
                if diet_weeks and break_weeks:
                    dw = round(total_loss / weekly_kg, 1)
                    out["timeline"]["calendar_weeks_with_breaks"] = round(dw + math.ceil(dw / diet_weeks - 1) * break_weeks, 1)
    else:
        kcal = maint
    macros = split_macros(kcal, protein, carb_share)
    if macros["kcal"] <= 1200:
        warnings.append("<= 1200 kcal is VLCD territory; the book advises against it (p.128).")
    if protein / w > 2.8:
        notes.append("Protein above 2.8 g/kg bodyweight hasn't been assessed for long-term "
                     "safety (p.172); fine to lower toward 1.6-2.2 g/kg if it's hard to hit.")
    out["targets"] = macros
    out["protein_basis"] = protein_basis
    out["carb_share"] = round(carb_share, 2)
    out["warnings"] = warnings
    out["notes"] = notes
    if apply:
        saved = tracker.set_targets(con, macros["kcal"], macros["protein_g"], macros["carb_g"],
                                    macros["fat_g"], macros["fiber_g"], date)
        kind = {"cut": "cut", "maintain": "maintenance", "reverse": "reverse"}[goal]
        params = {"maintenance_kcal": int(round(maint))}
        if goal == "cut":
            params["rate_pct"] = out["deficit"]["rate_pct"]
            if diet_weeks or break_weeks:
                params.update(diet_weeks=diet_weeks or 2, break_weeks=break_weeks or 1)
        if goal == "reverse":
            params["pace"] = pace or "moderate"
        out["applied"] = {"targets": saved, "phase": start_phase(con, kind, date, **params)}
    return out


# ── Weekly check-in ──────────────────────────────────────────────────────────

FLAG_KEYS = ("period", "high_stress", "poor_sleep", "illness", "injury",
             "new_medication", "novice_lifter")


def _checkins(con, phase_id) -> list[dict]:
    rows = con.execute("SELECT * FROM checkins WHERE phase_id IS ? ORDER BY date, id",
                       (phase_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d["payload"] or "{}")
        out.append(d)
    return out


def _last_target_change(con, date: str) -> str | None:
    row = con.execute("SELECT effective_date FROM targets WHERE effective_date <= ? "
                      "ORDER BY effective_date DESC, id DESC LIMIT 1", (date,)).fetchone()
    return row["effective_date"] if row else None


def _next_cut_pct(history: list[dict], trend: list[dict], rate_pct: float) -> tuple[float | None, str]:
    """Pick the carb/fat cut using the book's threshold-learning rule (p.226-229).

    No earlier cut: 5% (with +5% activity) or 10% (food only) — caller decides.
    Earlier cut that FAILED (no on-track week since it): +5%, max 15%.
    Earlier cut that WORKED: 2.5% smaller than it, min 5%.
    15% already failed: None -> recommend a reverse diet / diet break."""
    cuts = [c for c in history if c["applied"] and (c["adjust_pct"] or 0) < 0]
    if not cuts:
        return 0.0, "first"
    last = cuts[-1]
    last_pct = -last["adjust_pct"]
    worked = False
    for w in trend:
        if w["start"] > last["date"] and w["change_kg"] is not None and w.get("prev_avg"):
            target = w["prev_avg"] * rate_pct / 100
            if -w["change_kg"] >= 0.5 * target:
                worked = True
                break
    if worked:
        return max(5.0, last_pct - 2.5), f"last cut ({last_pct:g}%) worked; trying 2.5% less (p.228)"
    if last_pct >= 15:
        return None, "a 15% carb/fat cut already failed (p.229)"
    return min(15.0, last_pct + 5), f"last cut ({last_pct:g}%) didn't break the stall; escalating (p.227)"


def check_in(con: sqlite3.Connection, date: str | None = None, *,
             flags: dict | None = None, scores: dict | None = None,
             apply: bool = False, effective_date: str | None = None) -> dict:
    """Weekly check-in: compare this week's average weight to last week's,
    read the active phase, and recommend hold / adjust / break / reverse.

    flags: any of FLAG_KEYS set true (false-plateau causes, p.225-226).
    scores: optional 1-10 self-ratings (adherence, energy, hunger, mood,
    stress, sleep, strength; p.221) — stored for the record.
    apply=True writes the proposed targets (effective_date, default date)."""
    date = _date(date)
    flags = {k: bool(v) for k, v in (flags or {}).items() if k in FLAG_KEYS}
    prof = get_profile(con)
    phase = current_phase(con, date)
    targets = tracker.get_targets(con, date)
    trend = weekly_trend(con, date, weeks=8)
    prev = None
    for w in trend:
        w["prev_avg"] = prev
        if w["avg_kg"] is not None:
            prev = w["avg_kg"]
    valid = [w for w in trend if w["avg_kg"] is not None]
    res: dict = {"date": date, "phase": phase, "targets": targets, "flags": flags,
                 "weeks": [{k: w[k] for k in ("start", "n", "avg_kg", "avg_lb", "change_kg",
                                              "change_pct", "waist_cm", "body_fat_pct")}
                           for w in trend[-4:]],
                 "proposed_targets": None, "adjust_pct": None, "activity_pct": None,
                 "reasons": [], "advice": []}
    # Descriptive Watch context, attached before any early return. It never
    # feeds the rules below (no targets/maintenance/plateau changes).
    res["activity"] = activity_context.checkin_context(con, date, prof)
    if scores:
        if not isinstance(scores, dict):
            raise ValueError("scores must be an object of name -> 1-10")
        res["scores"] = {str(k): int(tracker._num(v, f"scores.{k}")) for k, v in scores.items()
                         if v is not None}
        if any(not 1 <= v <= 10 for v in res["scores"].values()):
            raise ValueError("scores are 1-10")

    def done(status: str, action: str):
        res["status"], res["action"] = status, action
        return _finish(con, res, apply, effective_date or date)

    if not targets:
        res["reasons"].append("No targets yet — run plan_targets first.")
        return done("no_targets", "setup")
    if len(valid) < 2 or valid[-1]["change_kg"] is None:
        res["reasons"].append(f"Need two weeks with >= {MIN_WEIGHINS_PER_WEEK} weigh-ins to compare "
                              "averages. Keep weighing daily: morning, after the bathroom, "
                              "same scale (p.216).")
        return done("collecting", "hold")
    cur = valid[-1]
    res["this_week"] = {k: cur[k] for k in ("start", "avg_kg", "avg_lb", "change_kg", "change_pct", "n")}
    kind = phase["kind"] if phase else "cut"
    params = phase["params"] if phase else {"rate_pct": 0.6}
    history = _checkins(con, phase["id"] if phase else None)

    # ── Diet-break schedule (planned in advance, p.208) ──
    if phase and kind == "cut" and params.get("diet_weeks"):
        # Is TOMORROW a break day, and is a break currently applied? State comes
        # from applied check-ins, so a missed or previewed check-in self-heals.
        cycle = params["diet_weeks"] + params["break_weeks"]
        tomorrow_idx = ((_d(date) - _d(phase["start_date"])).days + 1) // 7 % cycle
        break_tomorrow = tomorrow_idx >= params["diet_weeks"]
        last_break_action = next((c["action"] for c in reversed(history) if c["applied"]
                                  and c["action"] in ("start_break", "end_break")), None)
        on_break_now = last_break_action == "start_break"
        if break_tomorrow and not on_break_now:
            maint = empirical_maintenance(con, date)
            mk = maint["kcal"] if maint["kcal"] and maint["confidence"] != "low" else params.get("maintenance_kcal")
            body = _body(con, prof, date)
            p, _ = protein_target(body["weight_kg"], body["lbm_kg"], body["age"], False, body["bmi"])
            if mk:
                res["proposed_targets"] = split_macros(mk, p, prof["carb_share"])
            res["reasons"].append(f"Planned diet break next week: {params['break_weeks']} week(s) at "
                                  "maintenance with protein at the non-deficit level (p.208-210). "
                                  "Maintenance is re-measured each break.")
            res["diet_targets_to_restore"] = {k: targets[k] for k in ("kcal", "protein_g", "carb_g", "fat_g", "fiber_g")}
            return done("diet_break_due", "start_break")
        if on_break_now and not break_tomorrow:
            restore = next((c["payload"].get("diet_targets_to_restore") for c in reversed(history)
                            if c["action"] == "start_break" and c["applied"]), None)
            res["proposed_targets"] = restore
            res["reasons"].append("Break ends: back to diet targets. Expect a 1-2 week water drop "
                                  "and don't judge the deficit off the first week back.")
            return done("diet_break_ending", "end_break")
        if on_break_now:
            res["reasons"].append("Diet-break week: eat at maintenance; no adjustments.")
            return done("on_break", "hold")

    if kind == "diet_break":
        res["reasons"].append("Diet break: hold at maintenance; adjustments resume when the cut does.")
        return done("on_break", "hold")

    if kind == "reverse":
        return _reverse_check(con, res, params, valid, cur, targets, prof, date, done)

    if kind == "maintenance":
        anchor = params.get("start_weight_kg") or valid[0]["avg_kg"]
        drift = (cur["avg_kg"] - anchor) / anchor * 100
        prev_drift = (valid[-2]["avg_kg"] - anchor) / anchor * 100
        res["drift_pct"] = round(drift, 2)
        if drift > 1.5 and prev_drift > 1.5:
            res["proposed_targets"] = adjust_carb_fat(targets, -5)
            res["adjust_pct"] = -5
            res["reasons"].append(f"Weekly average has held {drift:.1f}% above the maintenance anchor "
                                  "for two weeks; trimming carbs/fat 5%.")
            return done("drifting_up", "adjust")
        if drift < -1.5 and prev_drift < -1.5:
            res["proposed_targets"] = adjust_carb_fat(targets, +5)
            res["adjust_pct"] = 5
            res["reasons"].append(f"Weekly average has held {abs(drift):.1f}% below the anchor for two "
                                  "weeks — maintenance is higher than you're eating; adding 5% carbs/fat.")
            return done("drifting_down", "adjust")
        res["reasons"].append("Within the maintenance range. Maintenance is a 100-300 kcal band, "
                              "so small swings need no action (p.138).")
        return done("on_track", "hold")

    # ── Cut ──
    rate = float(params.get("rate_pct", 0.6))
    goal_kg = cur["prev_avg"] * rate / 100
    loss = -cur["change_kg"]
    ratio = loss / goal_kg if goal_kg else 0
    res["goal_loss_kg"] = round(goal_kg, 2)
    res["loss_kg"] = round(loss, 2)
    res["pct_of_goal"] = round(ratio * 100)
    first_week = phase is not None and cur["start"] <= _add(phase["start_date"], 6)

    if first_week:
        res["reasons"].append("First week of the phase: early loss is mostly water and glycogen, "
                              "so nothing changes off week one (p.222).")
        return done("first_week", "hold")
    if -cur["change_pct"] > 1.0:
        prev_fast = valid[-2]["change_pct"] is not None and -valid[-2]["change_pct"] > 1.0
        if prev_fast:
            res["proposed_targets"] = adjust_carb_fat(targets, +5)
            res["adjust_pct"] = 5
            res["reasons"].append("Losing more than 1% of bodyweight a week for two weeks — past the "
                                  "book's absolute max (p.128). Adding 5% carbs/fat to protect lean mass.")
            return done("too_fast", "adjust")
        res["reasons"].append("Faster than 1%/wk this week; watching one more week before slowing it down.")
        return done("fast", "hold")
    if ratio >= 0.5:
        res["reasons"].append(f"Lost {loss:.2f} kg vs a {goal_kg:.2f} kg goal ({ratio:.0%}). "
                              "At or above half the goal is on track; don't change anything off one week (p.222).")
        return done("on_track", "hold")

    # Slow week. Count consecutive slow weeks since the last target change.
    last_change = _last_target_change(con, date) or "0000-00-00"
    streak = 0
    for w in reversed(valid):
        if w["change_kg"] is None or w["start"] < last_change:
            break
        if -w["change_kg"] >= 0.5 * w["prev_avg"] * rate / 100:
            break
        streak += 1
    res["slow_weeks"] = streak
    if streak < 2:
        res["reasons"].append(f"Under half the goal this week ({ratio:.0%}). One slow week is noise — "
                              "a plateau is two consecutive check-ins under 50% (p.222).")
        sec = _secondary_progress(valid)
        if sec:
            res["reasons"].append(sec + " — progress is happening off the scale (p.222).")
        return done("slow", "hold")

    # Two+ slow weeks: rule out false plateaus before cutting.
    span_start = valid[-streak]["start"] if streak <= len(valid) else valid[0]["start"]
    adh = adherence(con, span_start, min(cur["end"], date))
    res["adherence"] = adh
    if adh["logging_coverage"] < 0.85 or (adh["kcal_compliance"] is not None and adh["kcal_compliance"] < 0.6
                                          and adh["avg_overage_kcal"] > 100):
        res["reasons"].append("Before cutting food: logging covered "
                              f"{adh['logging_coverage']:.0%} of days and kcal was on target "
                              f"{(adh['kcal_compliance'] or 0):.0%} of the time. Poor adherence is the #1 cause "
                              "of false plateaus; people under-report by up to ~50% (p.232-233). "
                              "Tighten logging (bites, oils, drinks, weekends) for one more week.")
        if adh.get("weekend_drift_kcal", 0) > 300:
            res["advice"].append(f"Weekends run ~{adh['weekend_drift_kcal']} kcal above weekdays.")
        return done("plateau_adherence", "fix_adherence")
    confounders = [k for k in ("period", "high_stress", "poor_sleep", "illness", "injury", "new_medication")
                   if flags.get(k)]
    if confounders and streak < 3:
        res["reasons"].append("Possible false plateau (" + ", ".join(confounders).replace("_", " ") +
                              "): wait one extra check-in before changing anything (p.225-226).")
        return done("plateau_confounded", "hold")
    sec = _secondary_progress(valid)
    if flags.get("novice_lifter") and sec:
        res["reasons"].append(sec + ". New lifters can recompose — no change while measurements keep "
                              "dropping (p.223).")
        return done("recomposing", "hold")

    pct, why = _next_cut_pct(history, trend, rate)
    if pct is None:
        res["reasons"].append(why + ". The book's read: metabolism isn't in a good place. "
                              "Recommend a diet break or a reverse diet rather than cutting further.")
        return done("plateau_exhausted", "recommend_reverse")
    activity = None
    if pct == 0.0:
        if prof.get("adjust_with_activity"):
            pct, activity = 5.0, 5.0
            why = "first plateau: -5% carbs/fat plus +5% weekly exercise time (p.229)"
        else:
            pct = 10.0
            why = "first plateau, food-only: -10% carbs/fat (p.229)"
    proposed = adjust_carb_fat(targets, -pct)
    res["proposed_targets"] = proposed
    res["adjust_pct"] = -pct
    res["activity_pct"] = activity
    res["reasons"].append(f"Plateau: {streak} consecutive check-ins under 50% of goal. Cut carbs/fat "
                          f"{pct:g}% — protein stays put ({why}).")
    if activity:
        res["advice"].append("Add ~5% to weekly exercise time (e.g. 90 -> 95 min). Measure activity "
                             "as time, not machine calories (p.229-230).")
    if proposed["kcal"] <= 1200:
        res["advice"].append("New intake is <= 1200 kcal. Consider a diet break or reverse instead (p.128).")
    return done("plateau", "adjust")


def _secondary_progress(valid: list[dict]) -> str | None:
    """Waist or body-fat trending down across the last two valid weeks."""
    if len(valid) < 2:
        return None
    a, b = valid[-2], valid[-1]
    bits = []
    if a["waist_cm"] and b["waist_cm"] and b["waist_cm"] < a["waist_cm"] - 0.2:
        bits.append(f"waist down {a['waist_cm'] - b['waist_cm']:.1f} cm")
    if a["body_fat_pct"] and b["body_fat_pct"] and b["body_fat_pct"] < a["body_fat_pct"] - 0.2:
        bits.append(f"body fat down {a['body_fat_pct'] - b['body_fat_pct']:.1f} pts")
    return ", ".join(bits).capitalize() if bits else None


def _reverse_check(con, res, params, valid, cur, targets, prof, date, done):
    pace = params.get("pace", "moderate")
    inc, max_gain = REVERSE_PACE[pace]
    res["pace"] = pace
    gain = cur["change_pct"]
    rising = [w["change_pct"] is not None and w["change_pct"] > 0 for w in valid[-3:]]
    if gain > max_gain:
        res["reasons"].append(f"Weekly average rose {gain:.2f}% (> {max_gain}% for a {pace} reverse). "
                              "Hold this week; if it's a known water cause (period, travel, sodium) "
                              "an increase is still allowed (p.250).")
        return done("gaining_fast", "hold")
    if len(rising) == 3 and all(rising):
        res["reasons"].append("Weight has risen three weeks running, even if each was in range. "
                              "Hold 1-2 weeks (p.252).")
        return done("rising_streak", "hold")
    res["proposed_targets"] = adjust_carb_fat(targets, inc)
    res["adjust_pct"] = inc
    res["reasons"].append(f"Weekly change {gain:+.2f}% is within range: +{inc:g}% carbs/fat ({pace} pace, p.250-251).")
    pred = predicted_maintenance(con, date, prof)
    if pred:
        ratio = res["proposed_targets"]["kcal"] / pred["kcal"]
        res["vs_predicted_maintenance"] = round(ratio, 2)
        if ratio >= 1.1:
            res["advice"].append(f"Intake is {ratio:.0%} of predicted maintenance — 10-20% above predicted "
                                 "is the book's ideal launch point for the next diet. If you're happy "
                                 "here, cut ~5% as a cushion and hold (p.253-255).")
    if res["proposed_targets"]["carb_g"] >= 300:
        res["advice"].append("Food volume is getting hard to eat; it's fine to stop increasing here (p.252).")
    return done("reverse_step", "adjust")


def _finish(con, res: dict, apply: bool, effective_date: str) -> dict:
    proposed = res.get("proposed_targets")
    applied = False
    if apply and proposed:
        saved = tracker.set_targets(con, proposed["kcal"], proposed["protein_g"], proposed["carb_g"],
                                    proposed["fat_g"], proposed.get("fiber_g"), effective_date)
        res["applied_targets"] = saved
        applied = True
    phase = res.get("phase")
    week_start = (res.get("this_week") or {}).get("start")
    payload = {k: res.get(k) for k in ("reasons", "advice", "proposed_targets", "flags",
                                       "diet_targets_to_restore", "loss_kg", "goal_loss_kg",
                                       "activity")}
    if res.get("scores"):
        payload["scores"] = res["scores"]
    con.execute(
        "INSERT INTO checkins (date, week_start, phase_id, status, action, adjust_pct, "
        "activity_pct, applied, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (res["date"], week_start, phase["id"] if phase else None, res["status"], res["action"],
         res.get("adjust_pct"), res.get("activity_pct"), int(applied), json.dumps(payload)))
    con.commit()
    res["applied"] = applied
    return res


def checkin_history(con: sqlite3.Connection, limit: int = 12) -> list[dict]:
    rows = con.execute("SELECT * FROM checkins ORDER BY date DESC, id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d["payload"] or "{}")
        d["applied"] = bool(d["applied"])
        out.append(d)
    return out


# ── High / low day planner (p.204) ───────────────────────────────────────────

def plan_high_days(con: sqlite3.Connection, high_days: int = 2, high_kcal: float | None = None,
                   date: str | None = None) -> dict:
    """Spread the same weekly calories over low days and higher (refeed) days,
    per the book's algorithm (p.204): weekly kcal -> high days x high kcal ->
    remainder / low days. High days default to maintenance; protein drops to the
    non-deficit level on high days and fat is held, carbs fill (p.202-203).
    A high day may not exceed 2x a low day (p.201). Returns a plan; the
    week-bank view already lets any day run over if other days come in under."""
    date = _date(date)
    t = tracker.get_targets(con, date)
    if not t:
        raise ValueError("no targets set")
    n = int(tracker._num(high_days, "high_days", positive=True))
    if n > 3:
        raise ValueError("1-3 high days a week")
    prof = get_profile(con)
    body = _body(con, prof, date)
    weekly = t["kcal"] * 7
    if high_kcal is None:
        m = empirical_maintenance(con, date)
        if m["kcal"] and m["confidence"] != "low":
            high_kcal = m["kcal"]
        else:
            pred = predicted_maintenance(con, date, prof, body)
            high_kcal = pred["kcal"] if pred else t["kcal"] * 1.4
    high_kcal = tracker._num(high_kcal, "high_kcal", positive=True)
    low_kcal = (weekly - n * high_kcal) / (7 - n)
    if high_kcal > 2 * low_kcal:
        raise ValueError(f"high days ({high_kcal:.0f}) would be more than double the low days "
                         f"({low_kcal:.0f}); the book caps refeeds at 2x (p.201). Use fewer or smaller high days.")
    low = split_macros(low_kcal, t["protein_g"], prof["carb_share"])
    hp = t["protein_g"]
    if body["weight_kg"]:
        hp, _ = protein_target(body["weight_kg"], body["lbm_kg"], body["age"], False, body["bmi"])
        hp = min(hp, t["protein_g"])
    fat = low["fat_g"]
    carbs = _r5(max(0.0, high_kcal - 4 * hp - 9 * fat) / 4)
    high = {"kcal": _kcal_of(hp, carbs, fat), "protein_g": hp, "carb_g": carbs, "fat_g": fat,
            "fiber_g": _r5(12.5 * high_kcal / 1000)}
    return {"weekly_kcal": int(round(weekly)), "high_days": n, "low_days": 7 - n,
            "high": high, "low": low,
            "check_weekly_kcal": high["kcal"] * n + low["kcal"] * (7 - n),
            "note": "Same weekly deficit, different distribution — a preference/adherence tool, "
                    "not a fat-loss accelerator (p.200)."}


# ── Progress overview ────────────────────────────────────────────────────────

def progress(con: sqlite3.Connection, date: str | None = None, weeks: int = 12) -> dict:
    date = _date(date)
    prof = get_profile(con)
    body = _body(con, prof, date)
    trend = weekly_trend(con, date, weeks)
    phase = current_phase(con, date)
    measured = empirical_maintenance(con, date)
    predicted = predicted_maintenance(con, date, prof, body)
    adaptation = None
    if measured["kcal"] and predicted and measured["confidence"] != "low":
        adaptation = round((measured["kcal"] - predicted["kcal"]) / predicted["kcal"] * 100, 1)
    recent = _metrics_between(con, _add(date, -27), date)
    out = {
        "date": date, "profile": prof, "body": body, "phase": phase,
        "targets": tracker.get_targets(con, date),
        "weeks": trend,
        "daily": [{"date": r["date"], "weight_kg": r["weight_kg"], "body_fat_pct": r["body_fat_pct"],
                   "waist_cm": r["waist_cm"]} for r in recent],
        "maintenance": {"measured": measured, "predicted": predicted,
                        "vs_predicted_pct": adaptation},
        "adherence_14d": adherence(con, _add(date, -13), date),
        "last_checkin": (checkin_history(con, 1) or [None])[0],
        "activity": activity_context.summary(con, date, plan=prof),
    }
    if phase and phase["params"].get("start_weight_kg") and body["weight_kg"]:
        out["phase_change_kg"] = round(body["weight_kg"] - phase["params"]["start_weight_kg"], 2)
    if adaptation is not None and adaptation <= -25:
        out["maintenance"]["gate"] = ("Measured maintenance is >= 25% under predicted: don't start a new "
                                      "diet from here — reverse first (p.257).")
    return out
