"""Apple Watch activity context: steps, resting heart rate, and workouts.

Imports come from the generated "Sync Macro Engine" shortcut (see
docs/apple-health-capabilities.md for what Shortcuts can and cannot read).
Activity is descriptive context only: nothing here changes targets, phases,
maintenance estimates, body metrics, food logs, or saved check-ins.

Request contract (schema_version 1). The shortcut sends one bounded request
per run covering a calendar-day window in the configured timezone:

    {"schema_version": 1,
     "captured_at": "2026-11-09T18:00:00-04:00",   # recorded before the queries
     "window": "normal" | "catch_up",              # 8 or 31 local calendar days
     "steps_status": "queried" | "unavailable" | "unsupported",
     "steps_samples": "<value>\\t<start ISO 8601>\\t<source>\\n...",
     "resting_heart_rate_status": ..., "resting_heart_rate_samples": "...",
     "workouts_status": ..., "workouts_source": "...", "workouts": [{...}]}

Sample lists are newline-separated text because that is what Shortcuts can
assemble reliably (Combine Text over an appended variable). Omitting a
category means "no update" for it.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import uuid
from datetime import date as Date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import tracker

SCHEMA_VERSION = 1
DEFAULT_TIMEZONE = "America/Halifax"
WINDOW_DAYS = {"normal": 8, "catch_up": 31}
CATCH_UP_LIMIT = 30          # calendar days before today that catch-up can reach
BACKLOG_SCAN_DAYS = 120      # how far back an out-of-range backlog is reported
MAX_BODY = 4_000_000
MAX_LINES = 20_000           # per category; larger uploads are rejected, never truncated
MAX_WORKOUTS = 200
MAX_SOURCE_LEN = 120
CLOCK_SKEW = timedelta(minutes=10)
# Input validation bounds, not clinical judgements.
STEP_SAMPLE_MAX = 100_000
STEP_DAY_MAX = 250_000
RHR_MIN, RHR_MAX = 20, 250
WORKOUT_MAX_SECONDS = 24 * 3600

CATEGORIES = ("steps", "resting_heart_rate", "workouts")
STATUSES = ("queried", "unavailable", "unsupported")
STEP_POLICY = "single_source"
STEP_LABEL = "Watch-recorded steps"

# Organizational allowlist (Shortcuts/HealthKit workout labels). Categories do
# not imply intensity. Anything not listed stays "other".
STRENGTH_TYPES = frozenset({"Traditional Strength Training", "Functional Strength Training"})
CARDIO_TYPES = frozenset({"Walking", "Running", "Cycling", "Swimming", "Rowing", "Elliptical",
                          "Stair Climbing", "Stairs"})
MANUAL_TYPES = ["Traditional Strength Training", "Functional Strength Training", "Walking",
                "Running", "Cycling", "Elliptical", "Rowing", "Swimming", "Stair Climbing", "Other"]
DURATION_BASES = ("recorded", "elapsed")

SYNC_KEY = "apple_health_activity_sync"
SOURCE_KEY = "activity_source"
TIMEZONE_KEY = "activity_timezone"

TIMEZONE_NOTE = ("Days are grouped in the configured timezone. Shortcuts does not expose the phone's "
                 "timezone name, so while travelling, days near midnight can land on the wrong date.")
WORKOUT_NOTE = ("Apple Shortcuts cannot read workouts from Health, so they are not imported "
                "automatically. Add sessions manually; unrecorded sessions are not treated as skipped.")


class ConflictError(Exception):
    """Same capture time with different contents, or colliding workout identities."""


def category_of(workout_type: str) -> str:
    if workout_type in STRENGTH_TYPES:
        return "strength"
    if workout_type in CARDIO_TYPES:
        return "cardio"
    return "other"


# ── Settings ─────────────────────────────────────────────────────────────────

def _get(con, key, default=None):
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def _put(con, key, value) -> None:
    con.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))


def timezone_name(con) -> str:
    return _get(con, TIMEZONE_KEY) or DEFAULT_TIMEZONE


def _tz(con) -> ZoneInfo:
    return ZoneInfo(timezone_name(con))


def source_setting(con) -> dict:
    return _get(con, SOURCE_KEY) or {"selected": None, "selection": None, "history": []}


def _select_source(con, name: str, how: str, now: datetime) -> dict:
    current = source_setting(con)
    if current.get("selected") == name:
        return current
    history = list(current.get("history") or [])
    history.append({"source": name, "selection": how, "at": now.isoformat(timespec="seconds"),
                    "previous": current.get("selected")})
    value = {"selected": name, "selection": how, "history": history[-20:]}
    _put(con, SOURCE_KEY, value)
    return value


def update_settings(con, body: dict, now: datetime | None = None) -> dict:
    """Web settings: {"source": "<name seen in imports>"} and/or {"timezone": "Area/City"}."""
    now = now or datetime.now(timezone.utc)
    extra = set(body) - {"source", "timezone"}
    if extra or not body:
        raise ValueError("provide source and/or timezone")
    with con:
        if "timezone" in body:
            name = body["timezone"]
            if not isinstance(name, str) or "/" not in name:
                raise ValueError("timezone must be an IANA name such as America/Halifax")
            try:
                ZoneInfo(name)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError(f"unknown timezone {name}") from None
            _put(con, TIMEZONE_KEY, name)
        if "source" in body:
            name = body["source"]
            seen = {r[0] for r in con.execute(
                "SELECT source FROM health_step_days UNION SELECT source FROM health_rhr_days")}
            if not isinstance(name, str) or name not in seen:
                raise ValueError("source must be one of the sources already received from the shortcut")
            _select_source(con, name, "user", now)
    return {"timezone": timezone_name(con), "source": source_setting(con)}


# ── Parsing ──────────────────────────────────────────────────────────────────

_NUMBER = re.compile(r"^(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _instant(raw, what: str) -> datetime:
    if not isinstance(raw, str):
        raise ValueError(f"{what} must be an ISO 8601 timestamp with a UTC offset")
    try:
        value = datetime.fromisoformat(raw.strip())
    except ValueError:
        raise ValueError(f"{what} must be an ISO 8601 timestamp with a UTC offset") from None
    if value.utcoffset() is None:
        raise ValueError(f"{what} must include a UTC offset")
    return value


def _plain_number(raw: str, what: str) -> float:
    # Shortcuts turns numbers into text in the phone's locale. Grouping commas
    # (en locales) are accepted; a decimal comma is rejected rather than guessed.
    text = raw.strip().replace(" ", "").replace(" ", "")
    if not _NUMBER.match(text):
        raise ValueError(f"{what} must be a plain number, got {raw[:20]!r}")
    return float(text.replace(",", ""))


def _source_name(raw: str, what: str) -> str:
    name = raw.strip()
    if not name or len(name) > MAX_SOURCE_LEN or _CONTROL.search(name):
        raise ValueError(f"{what} source name must be 1-{MAX_SOURCE_LEN} printable characters")
    return name


def _lines(text, what: str) -> list[tuple[float, datetime, str]]:
    if text is None:   # an empty Shortcuts variable may serialize as null
        text = ""
    if not isinstance(text, str):
        raise ValueError(f"{what}_samples must be text")
    rows = [line.rstrip("\r") for line in text.split("\n")]
    rows = [line for line in rows if line.strip()]
    if len(rows) > MAX_LINES:
        raise ValueError(f"too many {what} samples ({len(rows)} > {MAX_LINES}); nothing was saved")
    out = []
    for n, line in enumerate(rows, 1):
        parts = line.split("\t")
        if len(parts) != 3:
            raise ValueError(f"{what} sample line {n} must be value<TAB>start<TAB>source")
        value = _plain_number(parts[0], f"{what} sample {n} value")
        out.append((value, _instant(parts[1], f"{what} sample {n} start"),
                    _source_name(parts[2], f"{what} sample {n}")))
    return out


def _window(captured_local: Date, days: int) -> list[Date]:
    return [captured_local - timedelta(days=i) for i in range(days - 1, -1, -1)]


def _local_midnight(day: Date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time(), tzinfo=tz)


def _validate(con, payload: dict, now: datetime) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("body must be a JSON object")
    allowed = {"schema_version", "captured_at", "window", "workouts_source", "workouts"}
    for cat in CATEGORIES:
        allowed.add(f"{cat}_status")
    allowed |= {"steps_samples", "resting_heart_rate_samples"}
    extra = set(payload) - allowed
    if extra:
        raise ValueError("unknown fields: " + ", ".join(sorted(extra)))
    version = payload.get("schema_version")
    if isinstance(version, bool) or version != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version; expected {SCHEMA_VERSION}")
    captured = _instant(payload.get("captured_at"), "captured_at")
    if captured - now > CLOCK_SKEW:
        raise ValueError("captured_at is in the future; check the phone's clock")
    mode = payload.get("window")
    if mode not in WINDOW_DAYS:
        raise ValueError("window must be normal or catch_up")
    tz = _tz(con)
    captured_local = captured.astimezone(tz)
    days = _window(captured_local.date(), WINDOW_DAYS[mode])
    warnings = []
    if captured.utcoffset() != captured_local.utcoffset():
        warnings.append("timezone_mismatch")

    parsed = {"captured": captured, "mode": mode, "tz": tz, "days": days, "warnings": warnings,
              "categories": {}}
    for cat in CATEGORIES:
        status = payload.get(f"{cat}_status")
        data_keys = ([f"{cat}_samples"] if cat != "workouts" else ["workouts", "workouts_source"])
        present = [k for k in data_keys if k in payload]
        if status is None:
            if present:
                raise ValueError(f"{cat}_status is required with {present[0]}")
            continue
        if status not in STATUSES:
            raise ValueError(f"{cat}_status must be queried, unavailable, or unsupported")
        if status != "queried":
            if present:
                raise ValueError(f"{present[0]} is only allowed when {cat}_status is queried")
            parsed["categories"][cat] = {"status": status}
            continue
        if cat == "workouts":
            parsed["categories"][cat] = {"status": status, **_workouts(payload, days, tz, captured)}
            continue
        if f"{cat}_samples" not in payload:
            raise ValueError(f"{cat}_samples is required when {cat}_status is queried")
        samples = _lines(payload[f"{cat}_samples"], cat)
        kept, ignored, duplicates, seen = [], 0, 0, set()
        for value, start, source in samples:
            if start - captured > CLOCK_SKEW:
                raise ValueError(f"{cat} sample starts after captured_at")
            if cat == "steps" and not 0 <= value <= STEP_SAMPLE_MAX:
                raise ValueError(f"steps sample out of range (0-{STEP_SAMPLE_MAX})")
            if cat == "steps" and value != int(value):
                raise ValueError("steps samples must be whole numbers")
            if cat == "resting_heart_rate" and not RHR_MIN <= value <= RHR_MAX:
                raise ValueError(f"resting heart rate out of range ({RHR_MIN}-{RHR_MAX} bpm); check units")
            key = (value, start.timestamp(), source)
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            local = start.astimezone(tz).date()
            if not days[0] <= local <= days[-1]:
                ignored += 1
                continue
            kept.append((value, start, source, local))
        parsed["categories"][cat] = {"status": status, "samples": kept, "received": len(samples),
                                     "ignored_outside_window": ignored, "duplicates": duplicates}
    return parsed


def _workouts(payload, days, tz, captured) -> dict:
    items = payload.get("workouts")
    source = payload.get("workouts_source")
    if not isinstance(items, list):
        raise ValueError("workouts must be a list when workouts_status is queried")
    if len(items) > MAX_WORKOUTS:
        raise ValueError(f"too many workouts ({len(items)} > {MAX_WORKOUTS}); nothing was saved")
    if not isinstance(source, str):
        raise ValueError("workouts_source is required when workouts_status is queried")
    source = _source_name(source, "workouts")
    if source.casefold() == "manual":
        raise ValueError("workouts_source 'manual' is reserved for entries made in the app")
    out, by_key, ignored = [], {}, 0
    for n, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError(f"workout {n} must be an object")
        extra = set(item) - {"external_id", "type", "start_at", "end_at", "duration_seconds",
                             "duration_basis"}
        if extra:
            raise ValueError(f"workout {n} has unknown fields: " + ", ".join(sorted(extra)))
        wtype = item.get("type")
        if not isinstance(wtype, str) or not wtype.strip() or len(wtype) > 80:
            raise ValueError(f"workout {n} type must be 1-80 characters")
        start = _instant(item.get("start_at"), f"workout {n} start_at")
        end = _instant(item.get("end_at"), f"workout {n} end_at")
        if end <= start:
            raise ValueError(f"workout {n} must end after it starts")
        if start - captured > CLOCK_SKEW:
            raise ValueError(f"workout {n} starts after captured_at")
        duration = item.get("duration_seconds")
        if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                or not math.isfinite(duration) or not 0 < duration <= WORKOUT_MAX_SECONDS):
            raise ValueError(f"workout {n} duration_seconds must be positive and at most 24 hours")
        basis = item.get("duration_basis")
        if basis not in DURATION_BASES:
            raise ValueError(f"workout {n} duration_basis must be recorded or elapsed")
        external = item.get("external_id")
        if external is not None and (not isinstance(external, str) or not 1 <= len(external) <= 200):
            raise ValueError(f"workout {n} external_id must be 1-200 characters")
        utc_start = start.astimezone(timezone.utc).isoformat(timespec="seconds")
        if external:
            key, basis_id = "ext:" + hashlib.sha256(f"{source}\x1f{external}".encode()).hexdigest(), "external"
        else:
            # Derived identity: source + canonical start instant + original type.
            # Never upload time, list position, or mutable duration.
            key = "derived:" + hashlib.sha256(f"{source}\x1f{utc_start}\x1f{wtype}".encode()).hexdigest()
            basis_id = "derived"
        record = {"identity_key": key, "identity_basis": basis_id, "external_id": external,
                  "source": source, "type": wtype, "category": category_of(wtype),
                  "start_at": start.isoformat(), "end_at": end.isoformat(),
                  "local_date": start.astimezone(tz).date().isoformat(),
                  "duration_seconds": int(round(duration)), "duration_basis": basis}
        if key in by_key:
            if by_key[key] != record:
                raise ConflictError(f"workout {n} collides with another workout in this upload "
                                    "(same source, start, and type)")
            continue
        by_key[key] = record
        if not days[0].isoformat() <= record["local_date"] <= days[-1].isoformat():
            ignored += 1
            continue
        out.append(record)
    return {"source": source, "items": out, "received": len(items), "ignored_outside_window": ignored}


# ── Import ───────────────────────────────────────────────────────────────────

def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _counts():
    return {"updated": 0, "unchanged": 0, "stale": 0}


def _upsert_snapshot(con, table, keys: dict, values: dict, captured, received, counts, what):
    where = " AND ".join(f"{k}=?" for k in keys)
    row = con.execute(f"SELECT * FROM {table} WHERE {where}", tuple(keys.values())).fetchone()
    ts = captured.timestamp()
    if row is not None:
        same = all(row[k] == v for k, v in values.items())
        if ts < row["captured_ts"]:
            counts["stale"] += 1
            return
        if ts == row["captured_ts"]:
            if not same:
                raise ConflictError(f"{what} for {keys.get('date', '')} has different contents "
                                    "with the same captured_at; nothing was saved")
            counts["unchanged"] += 1
            return
        if same:
            counts["unchanged"] += 1
            con.execute(f"UPDATE {table} SET captured_at=?, captured_ts=? WHERE {where}",
                        (_iso(captured), ts, *keys.values()))
            return
    cols = {**keys, **values, "captured_at": _iso(captured), "captured_ts": ts, "received_at": received}
    con.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
                f"ON CONFLICT({', '.join(keys)}) DO UPDATE SET "
                + ", ".join(f"{k}=excluded.{k}" for k in cols if k not in keys),
                tuple(cols.values()))
    counts["updated"] += 1


def _cover(con, cat, days, status, captured, received):
    ts = captured.timestamp()
    for day in days:
        d = day.isoformat()
        con.execute("INSERT OR IGNORE INTO health_activity_coverage (category, date) VALUES (?, ?)", (cat, d))
        # Older captures can never move acknowledgements backwards.
        con.execute("UPDATE health_activity_coverage SET attempt_status=?, attempt_captured_at=?, "
                    "attempt_ts=?, attempt_received_at=? WHERE category=? AND date=? "
                    "AND (attempt_ts IS NULL OR attempt_ts <= ?)",
                    (status, _iso(captured), ts, received, cat, d, ts))
        if status == "queried":
            con.execute("UPDATE health_activity_coverage SET queried_captured_at=?, queried_ts=?, "
                        "queried_received_at=? WHERE category=? AND date=? "
                        "AND (queried_ts IS NULL OR queried_ts <= ?)",
                        (_iso(captured), ts, received, cat, d, ts))


def import_activity(con: sqlite3.Connection, payload: dict, now: datetime | None = None) -> dict:
    """Validate the whole request, then commit it atomically (all or nothing)."""
    now = now or datetime.now(timezone.utc)
    parsed = _validate(con, payload, now)
    captured, days, tz = parsed["captured"], parsed["days"], parsed["tz"]
    received = _iso(now)
    result = {"schema_version": SCHEMA_VERSION, "received_at": received, "captured_at": _iso(captured),
              "timezone": timezone_name(con),
              "window": {"mode": parsed["mode"], "start_date": days[0].isoformat(),
                         "end_date": days[-1].isoformat(), "days": len(days),
                         "start": _iso(_local_midnight(days[0], tz)),
                         "end": _iso(_local_midnight(days[-1] + timedelta(days=1), tz))},
              "warnings": list(parsed["warnings"])}
    with con:
        con.execute("BEGIN IMMEDIATE")
        for cat in CATEGORIES:
            info = parsed["categories"].get(cat)
            if info is None:
                result[cat] = {"availability": "not_sent"}
                continue
            _cover(con, cat, days, info["status"], captured, received)
            if info["status"] != "queried":
                result[cat] = {"availability": info["status"]}
                continue
            counts = _counts()
            if cat == "steps":
                totals: dict = {}
                for value, _start, source, local in info["samples"]:
                    total, n = totals.get((local, source), (0.0, 0))
                    totals[(local, source)] = (total + value, n + 1)
                for (local, source), (total, n) in sorted(totals.items()):
                    steps = int(round(total))
                    if steps > STEP_DAY_MAX:
                        raise ValueError(f"steps total for {local} out of range; check the source")
                    _upsert_snapshot(con, "health_step_days",
                                     {"date": local.isoformat(), "source": source, "policy": STEP_POLICY},
                                     {"steps": steps, "sample_count": n}, captured, received, counts, "steps")
                sources = sorted({s for _, _, s, _ in info["samples"]})
            elif cat == "resting_heart_rate":
                latest: dict = {}
                for value, start, source, local in info["samples"]:
                    key = (local, source)
                    if key not in latest or start > latest[key][1]:
                        latest[key] = (value, start)
                for (local, source), (bpm, start) in sorted(latest.items()):
                    _upsert_snapshot(con, "health_rhr_days", {"date": local.isoformat(), "source": source},
                                     {"bpm": round(bpm, 1), "sample_at": start.isoformat()},
                                     captured, received, counts, "resting heart rate")
                sources = sorted({s for _, _, s, _ in info["samples"]})
            else:
                for record in info["items"]:
                    _upsert_workout(con, record, captured, received, counts)
                sources = [info["source"]]
            result[cat] = {"availability": "observed" if (info.get("samples") or info.get("items"))
                           else "no_samples", **counts, "received": info["received"],
                           "ignored_outside_window": info["ignored_outside_window"],
                           "sources": sources}
            if "duplicates" in info:
                result[cat]["duplicates"] = info["duplicates"]
        _auto_select_source(con, now)
        selected = source_setting(con).get("selected")
        for cat in ("steps", "resting_heart_rate"):
            entry = result[cat]
            if entry.get("availability") in ("observed", "no_samples"):
                entry["selected_source"] = selected
                if entry["availability"] == "observed" and selected not in entry["sources"]:
                    entry["availability"] = "source_needed" if selected is None else "other_sources_only"
        status_after = sync_status(con, now)
        reasons = []
        if result["steps"].get("availability") == "source_needed":
            reasons.append("choose your step source in Progress")
        for cat in CATEGORIES:
            if result[cat].get("availability") == "unavailable":
                reasons.append(f"{cat.replace('_', ' ')} unavailable")
        if status_after["mode"] == "catch_up":
            reasons.append("older days still need catch-up; run again")
        if "timezone_mismatch" in parsed["warnings"]:
            reasons.append("phone timezone differs from the configured timezone")
        updated = sum(result[c].get("updated", 0) for c in CATEGORIES)
        if reasons:
            outcome, notice = "partial", "Partial sync: " + reasons[0]
        elif updated:
            outcome, notice = "updated", "Updated"
        else:
            outcome, notice = "no_new_readings", "No new readings"
        result.update(outcome=outcome, notice=notice, partial_reasons=reasons,
                      observations_saved=updated, next_mode=status_after["mode"])
        _put(con, SYNC_KEY, {k: result[k] for k in ("received_at", "captured_at", "outcome", "notice",
                                                     "partial_reasons", "observations_saved", "warnings")}
             | {"window": result["window"],
                "availability": {c: result[c].get("availability") for c in CATEGORIES}})
    return result


def _upsert_workout(con, record, captured, received, counts):
    row = con.execute("SELECT * FROM health_workouts WHERE identity_key=?", (record["identity_key"],)).fetchone()
    mutable = ("end_at", "duration_seconds", "duration_basis", "local_date", "category", "start_at", "type")
    ts = captured.timestamp()
    if row is not None:
        if row["source"] != record["source"]:
            raise ConflictError("workout identity collision across sources")
        same = all(row[k] == record[k] for k in mutable)
        if ts < (row["captured_ts"] or 0):
            counts["stale"] += 1
            return
        if ts == row["captured_ts"] and not same:
            raise ConflictError("workout changed with the same captured_at; nothing was saved")
        if same:
            counts["unchanged"] += 1
            con.execute("UPDATE health_workouts SET captured_at=?, captured_ts=? WHERE id=?",
                        (_iso(captured), ts, row["id"]))
            return
        con.execute("UPDATE health_workouts SET " + ", ".join(f"{k}=?" for k in mutable)
                    + ", captured_at=?, captured_ts=?, received_at=?, updated_at=? WHERE id=?",
                    (*[record[k] for k in mutable], _iso(captured), ts, received, received, row["id"]))
        counts["updated"] += 1
        return
    cols = {**record, "captured_at": _iso(captured), "captured_ts": ts, "received_at": received,
            "updated_at": received}
    con.execute(f"INSERT INTO health_workouts ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                tuple(cols.values()))
    counts["updated"] += 1


def _auto_select_source(con, now):
    if source_setting(con).get("selected"):
        return
    names = {r[0] for r in con.execute("SELECT DISTINCT source FROM health_step_days")}
    watches = sorted(n for n in names if "watch" in n.casefold())
    if len(watches) == 1:
        _select_source(con, watches[0], "auto", now)


# ── Acknowledgements / catch-up ──────────────────────────────────────────────

def _today(con, now: datetime | None) -> Date:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(_tz(con)).date()


def _ranges(dates: list[Date]) -> list[dict]:
    out = []
    for d in sorted(dates):
        if out and (d - Date.fromisoformat(out[-1]["end"])).days == 1:
            out[-1]["end"] = d.isoformat()
            out[-1]["days"] += 1
        else:
            out.append({"start": d.isoformat(), "end": d.isoformat(), "days": 1})
    return out


def sync_status(con: sqlite3.Connection, now: datetime | None = None) -> dict:
    """What the next shortcut run should query. Only accepted step queries count
    as coverage; unavailable attempts stay eligible for retry, and unsupported
    categories never hold catch-up open."""
    today = _today(con, now)
    covered = {Date.fromisoformat(r[0]) for r in con.execute(
        "SELECT date FROM health_activity_coverage WHERE category='steps' AND queried_ts IS NOT NULL")}
    normal_start = today - timedelta(days=WINDOW_DAYS["normal"] - 1)
    limit_start = today - timedelta(days=CATCH_UP_LIMIT)
    gap, beyond = [], []
    if covered:
        d = max(min(covered), today - timedelta(days=BACKLOG_SCAN_DAYS))
        while d < normal_start:
            if d not in covered:
                (gap if d >= limit_start else beyond).append(d)
            d += timedelta(days=1)
    return {"mode": "catch_up" if gap else "normal",
            "window_days": WINDOW_DAYS["catch_up" if gap else "normal"],
            "today": today.isoformat(), "timezone": timezone_name(con),
            "catch_up": _ranges(gap), "backlog_beyond_limit": _ranges(beyond),
            "backlog_note": ("These days are older than the 30-day catch-up limit and can't be "
                             "re-sent by the shortcut.") if beyond else None,
            "last_request": _get(con, SYNC_KEY)}


# ── Manual workouts (fallback: Shortcuts cannot read workouts) ───────────────

def _manual_fields(con, body: dict, partial: bool) -> dict:
    allowed = {"date", "type", "duration_minutes", "start_time", "note"}
    extra = set(body) - allowed
    if extra:
        raise ValueError("unknown fields: " + ", ".join(sorted(extra)))
    if not partial:
        missing = [k for k in ("date", "type", "duration_minutes") if body.get(k) in (None, "")]
        if missing:
            raise ValueError("missing " + ", ".join(missing))
    out = {}
    if "date" in body:
        out["local_date"] = tracker._valid_date(body["date"])
    if "type" in body:
        wtype = body["type"]
        if not isinstance(wtype, str) or not wtype.strip() or len(wtype) > 80:
            raise ValueError("type must be 1-80 characters")
        out["type"] = wtype.strip()
        out["category"] = category_of(out["type"])
    if "duration_minutes" in body:
        minutes = body["duration_minutes"]
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not math.isfinite(minutes) \
                or not 0 < minutes <= 24 * 60:
            raise ValueError("duration_minutes must be between 1 and 1440")
        out["duration_seconds"] = int(round(minutes * 60))
    if "start_time" in body:
        st = body["start_time"]
        if st in (None, ""):
            out["start_time"] = None
        else:
            if not isinstance(st, str) or not re.fullmatch(r"\d{2}:\d{2}", st):
                raise ValueError("start_time must be HH:MM")
            hh, mm = map(int, st.split(":"))
            if hh > 23 or mm > 59:
                raise ValueError("start_time must be HH:MM")
            out["start_time"] = st
    if "note" in body:
        note = body["note"]
        if note is not None and (not isinstance(note, str) or len(note) > 500):
            raise ValueError("note must be text up to 500 characters")
        out["note"] = note or None
    return out


def _manual_start(con, local_date: str, start_time: str | None) -> str | None:
    if not start_time:
        return None
    hh, mm = map(int, start_time.split(":"))
    return datetime.combine(Date.fromisoformat(local_date), time(hh, mm), tzinfo=_tz(con)).isoformat()


def _workout_row(row) -> dict:
    d = dict(row)
    for k in ("captured_ts", "identity_key"):
        d.pop(k, None)
    d["minutes"] = round(d["duration_seconds"] / 60)
    return d


def add_manual_workout(con, body: dict, now: datetime | None = None) -> dict:
    fields = _manual_fields(con, body, partial=False)
    stamp = _iso(now or datetime.now(timezone.utc))
    start_at = _manual_start(con, fields["local_date"], fields.get("start_time"))
    with con:
        cur = con.execute(
            "INSERT INTO health_workouts (identity_key, identity_basis, source, type, category, local_date, "
            "start_at, duration_seconds, duration_basis, note, received_at, updated_at) "
            "VALUES (?, 'manual', 'manual', ?, ?, ?, ?, ?, 'manual', ?, ?, ?)",
            ("manual:" + uuid.uuid4().hex, fields["type"], fields["category"], fields["local_date"], start_at,
             fields["duration_seconds"], fields.get("note"), stamp, stamp))
    return get_workout(con, cur.lastrowid)


def get_workout(con, workout_id: int) -> dict | None:
    row = con.execute("SELECT * FROM health_workouts WHERE id=?", (workout_id,)).fetchone()
    return _workout_row(row) if row else None


def update_manual_workout(con, workout_id: int, body: dict, now: datetime | None = None) -> dict | None:
    row = con.execute("SELECT * FROM health_workouts WHERE id=?", (workout_id,)).fetchone()
    if row is None:
        return None
    if row["source"] != "manual":
        raise ValueError("imported workouts can't be edited here; correct them in Health")
    fields = _manual_fields(con, body, partial=True)
    if not fields:
        raise ValueError("nothing to update")
    local_date = fields.get("local_date", row["local_date"])
    if "start_time" in fields or "local_date" in fields:
        start_time = fields.pop("start_time", None) if "start_time" in fields else (
            row["start_at"][11:16] if row["start_at"] else None)
        fields["start_at"] = _manual_start(con, local_date, start_time)
    fields.pop("start_time", None)
    fields["updated_at"] = _iso(now or datetime.now(timezone.utc))
    with con:
        con.execute("UPDATE health_workouts SET " + ", ".join(f"{k}=?" for k in fields) + " WHERE id=?",
                    (*fields.values(), workout_id))
    return get_workout(con, workout_id)


def delete_manual_workout(con, workout_id: int) -> bool:
    row = con.execute("SELECT source FROM health_workouts WHERE id=?", (workout_id,)).fetchone()
    if row is None:
        return False
    if row["source"] != "manual":
        raise ValueError("imported workouts can't be deleted here")
    with con:
        con.execute("DELETE FROM health_workouts WHERE id=?", (workout_id,))
    return True


# ── Read-only summaries (shared by progress() and check_in()) ────────────────

def _avg(values):
    return round(sum(values) / len(values), 1) if values else None


def _week_start(con, d: Date) -> Date:
    starts_on = tracker.get_week_start(con)
    return d - timedelta(days=(d.weekday() - tracker.WEEKDAYS.index(starts_on)) % 7)


def summary(con: sqlite3.Connection, date: str, *, plan: dict | None = None, weeks: int = 4,
            days: int = 14, now: datetime | None = None) -> dict:
    """Activity context for `date` (YYYY-MM-DD). Read-only. Unknown days are
    omitted from averages (never zero-filled); today's steps are 'so far'."""
    ref = Date.fromisoformat(date)
    today = _today(con, now)
    src = source_setting(con)
    selected = src.get("selected")
    first_week = _week_start(con, ref) - timedelta(days=7 * (weeks - 1))
    lo = min(first_week, ref - timedelta(days=days - 1)).isoformat()
    hi = ref.isoformat()

    steps = {r["date"]: dict(r) for r in con.execute(
        "SELECT * FROM health_step_days WHERE source=? AND policy=? AND date BETWEEN ? AND ?",
        (selected, STEP_POLICY, lo, hi))} if selected else {}
    rhr = {r["date"]: dict(r) for r in con.execute(
        "SELECT * FROM health_rhr_days WHERE source=? AND date BETWEEN ? AND ?", (selected, lo, hi))} \
        if selected else {}
    coverage = {(r["category"], r["date"]): dict(r) for r in con.execute(
        "SELECT * FROM health_activity_coverage WHERE date BETWEEN ? AND ?", (lo, hi))}
    workouts = [_workout_row(r) for r in con.execute(
        "SELECT * FROM health_workouts WHERE local_date BETWEEN ? AND ? "
        "ORDER BY local_date, COALESCE(start_at, ''), id", (lo, hi))]

    def day_info(d: Date) -> dict:
        iso = d.isoformat()
        s, h = steps.get(iso), rhr.get(iso)
        cov = coverage.get(("steps", iso)) or {}
        return {"date": iso, "partial": d == today, "future": d > today,
                "steps": s["steps"] if s else None,
                "steps_captured_at": s["captured_at"] if s else None,
                "steps_queried": cov.get("queried_ts") is not None,
                "resting_hr_bpm": h["bpm"] if h else None,
                "resting_hr_sample_at": h["sample_at"] if h else None,
                "workouts": sum(1 for w in workouts if w["local_date"] == iso)}

    week_rows = []
    for i in range(weeks):
        start = first_week + timedelta(days=7 * i)
        end = start + timedelta(days=6)
        span = [start + timedelta(days=k) for k in range(7)]
        closed = [d for d in span if d <= ref and d < today]
        step_vals = [steps[d.isoformat()]["steps"] for d in closed if d.isoformat() in steps]
        rhr_vals = [rhr[d.isoformat()]["bpm"] for d in span if d <= ref and d.isoformat() in rhr]
        ws = [w for w in workouts if start.isoformat() <= w["local_date"] <= min(end, ref).isoformat()]
        by_cat = {c: {"count": sum(1 for w in ws if w["category"] == c),
                      "minutes": round(sum(w["duration_seconds"] for w in ws if w["category"] == c) / 60)}
                  for c in ("strength", "cardio", "other")}
        week_rows.append({
            "start": start.isoformat(), "end": end.isoformat(), "closed_days": len(closed),
            "steps_observed_days": len(step_vals), "steps_avg": _avg(step_vals) and round(_avg(step_vals)),
            "steps_queried_days": sum(1 for d in closed if (coverage.get(("steps", d.isoformat())) or {})
                                      .get("queried_ts") is not None),
            "resting_hr_avg": _avg(rhr_vals), "resting_hr_days": len(rhr_vals),
            "workouts": by_cat, "workout_days": len({w["local_date"] for w in ws}),
            "duration_bases": sorted({w["duration_basis"] for w in ws}),
            "manual_workouts": sum(1 for w in ws if w["source"] == "manual"),
        })

    today_steps = steps.get(today.isoformat()) if today <= ref else None
    seen = [dict(r) for r in con.execute(
        "SELECT source, COUNT(*) AS days, MAX(date) AS last_date FROM health_step_days "
        "GROUP BY source ORDER BY days DESC, source")]

    def freshness(cat, table):
        q = con.execute("SELECT MAX(queried_captured_at), MAX(queried_received_at) "
                        "FROM health_activity_coverage WHERE category=?", (cat,)).fetchone()
        a = con.execute("SELECT attempt_status FROM health_activity_coverage WHERE category=? "
                        "ORDER BY attempt_ts DESC LIMIT 1", (cat,)).fetchone()
        info = {"last_query_captured_at": q[0], "last_query_received_at": q[1],
                "last_attempt_status": a[0] if a else None,
                "last_observation_date": None, "last_observation_saved_at": None}
        if table == "health_workouts":
            o = con.execute("SELECT MAX(local_date), MAX(received_at) FROM health_workouts").fetchone()
            info["last_observation_date"], info["last_observation_saved_at"] = o[0], o[1]
        elif selected:
            o = con.execute(f"SELECT MAX(date), MAX(received_at) FROM {table} WHERE source=?",
                            (selected,)).fetchone()
            info["last_observation_date"], info["last_observation_saved_at"] = o[0], o[1]
        return info

    status = sync_status(con, now)
    current = week_rows[-1]
    notes = []
    if current["closed_days"]:
        if selected:
            notes.append(f"{STEP_LABEL} on {current['steps_observed_days']} of {current['closed_days']} "
                         "closed days this week.")
        if current["resting_hr_days"]:
            notes.append(f"Resting heart rate available on {current['resting_hr_days']} day(s) this week.")
    strength = current["workouts"]["strength"]["count"]
    planned = (plan or {}).get("strength_days_per_week")
    if strength or planned:
        line = f"{strength} strength session(s) recorded this week"
        if planned:
            line += f" (plan: {planned}/week)"
        notes.append(line + ". Unrecorded sessions aren't counted as skipped.")
    if not seen:
        notes.append("No activity data received yet.")
    elif not selected:
        notes.append("Choose which source counts as Watch-recorded steps.")

    return {
        "date": date, "today": today.isoformat(), "timezone": timezone_name(con),
        "timezone_note": TIMEZONE_NOTE,
        "source": {"selected": selected, "selection": src.get("selection"), "policy": STEP_POLICY,
                   "label": STEP_LABEL, "seen": seen, "history": src.get("history") or []},
        "today_steps": ({"date": today.isoformat(), "value": today_steps["steps"], "so_far": True,
                         "captured_at": today_steps["captured_at"]} if today_steps else None),
        "days": [day_info(ref - timedelta(days=i)) for i in range(days - 1, -1, -1)],
        "weeks": week_rows,
        "workouts": [w for w in workouts if w["local_date"] >= (ref - timedelta(days=days - 1)).isoformat()],
        "plan": {"strength_days_per_week": planned,
                 "strength_minutes_per_session": (plan or {}).get("strength_minutes_per_session")},
        "freshness": {"steps": freshness("steps", "health_step_days"),
                      "resting_heart_rate": freshness("resting_heart_rate", "health_rhr_days"),
                      "workouts": freshness("workouts", "health_workouts")},
        "sync": status,
        "capabilities": {"steps": "single_source_raw_samples", "resting_heart_rate": "dedicated_metric",
                         "workouts": "manual_only", "workouts_note": WORKOUT_NOTE},
        "manual_workout_types": MANUAL_TYPES,
        "notes": notes,
    }


def checkin_context(con: sqlite3.Connection, date: str, plan: dict | None = None,
                    now: datetime | None = None) -> dict:
    """Compact, descriptive activity context saved with a new check-in."""
    full = summary(con, date, plan=plan, weeks=2, days=7, now=now)
    return {k: full[k] for k in ("timezone", "today_steps", "weeks", "plan", "notes", "capabilities")} | {
        "source": {k: full["source"][k] for k in ("selected", "label", "policy")},
        "sync": {k: full["sync"][k] for k in ("mode", "catch_up", "backlog_beyond_limit", "last_request")},
        "freshness": full["freshness"],
        "note": "Context only; activity does not change targets or recommendations."}
