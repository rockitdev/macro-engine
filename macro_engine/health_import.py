"""Import Apple Shortcuts or Health Auto Export JSON into daily coaching metrics."""

import json
import math
from datetime import datetime, timezone

from .coach import LB_PER_KG


FIELDS = {"weight": "weight_kg", "body_mass": "weight_kg",
          "body_fat_percentage": "body_fat_pct"}


def _samples(payload):
    # A flat JSON body is easier to assemble in Shortcuts' Get Contents of URL.
    # Send one dated measurement at a time, reusing the same validation and
    # replay protection as the bulk exporter.
    if "metric" in payload:
        if set(payload) != {"metric", "value", "unit", "date"}:
            raise ValueError("Shortcut imports require metric, value, unit, and date")
        if not isinstance(payload["metric"], str) or payload["metric"] not in FIELDS:
            raise ValueError("metric must be weight, body_mass, or body_fat_percentage")
        payload = {"data": {"metrics": [{"name": payload["metric"], "units": payload["unit"],
                   "data": [{"qty": payload["value"], "date": payload["date"]}]}]}}
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("metrics"), list):
        raise ValueError("expected Health Auto Export JSON: data.metrics array")
    latest = {}
    ignored = 0
    for metric in data["metrics"]:
        if not isinstance(metric, dict) or not isinstance(metric.get("name"), str):
            raise ValueError("each metric must have a name")
        field = FIELDS.get(metric["name"])
        if field is None:
            ignored += 1
            continue
        unit = metric.get("units")
        allowed = ("kg", "lb", "lbs") if field == "weight_kg" else ("%",)
        if unit not in allowed:
            raise ValueError(f"unsupported unit for {metric['name']}; expected {allowed}")
        if not isinstance(metric.get("data"), list):
            raise ValueError("metric data must be an array")
        for sample in metric["data"]:
            if not isinstance(sample, dict):
                raise ValueError("each sample must be an object")
            raw = sample.get("date")
            if not isinstance(raw, str):
                raise ValueError("sample date must include time and UTC offset")
            try:
                try:
                    dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S %z")
                except ValueError:
                    dt = datetime.fromisoformat(raw)
                if dt.utcoffset() is None:
                    raise ValueError
            except ValueError:
                raise ValueError("sample date must include time and UTC offset") from None
            qty = sample.get("qty")
            if isinstance(qty, bool) or not isinstance(qty, (int, float)) or not math.isfinite(qty):
                raise ValueError("sample qty must be a finite number")
            value = qty / LB_PER_KG if unit in ("lb", "lbs") else qty
            low, high = (25, 400) if field == "weight_kg" else (2, 70)
            if not low <= value <= high:
                raise ValueError(f"{metric['name']} out of range; check units")
            value = round(value, 2 if field == "weight_kg" else 1)
            # Use the phone's calendar date, independent of server timezone.
            key = (dt.date().isoformat(), field)
            candidate = (dt.timestamp(), value)
            previous = latest.get(key)
            if previous and previous[0] == candidate[0] and previous[1] != value:
                raise ValueError("conflicting samples at the same time; select RENPHO as the preferred source")
            if previous is None or candidate[0] > previous[0]:
                latest[key] = candidate
    return latest, ignored


def import_metrics(con, payload):
    # Validate the whole batch before writing, then commit it atomically.
    samples, ignored = _samples(payload)
    result = {"updated_fields": 0, "skipped_fields": 0, "preserved_manual_fields": 0,
              "ignored_metrics": ignored, "dates": []}
    dates = set()
    with con:
        con.execute("BEGIN IMMEDIATE")
        for (date, field), (ts, value) in sorted(samples.items()):
            previous = con.execute(
                "SELECT * FROM health_import_metrics WHERE date=? AND field=?", (date, field)
            ).fetchone()
            current = con.execute(f"SELECT {field} FROM body_metrics WHERE date=?", (date,)).fetchone()
            current_value = current[field] if current else None
            # Existing manual values and subsequent manual edits take precedence.
            if ((previous is None and current_value is not None)
                    or (previous is not None and current_value != previous["value"])):
                result["preserved_manual_fields"] += 1
                continue
            if previous is not None and ts <= previous["sample_ts"]:
                result["skipped_fields"] += 1
                continue
            con.execute(
                f"INSERT INTO body_metrics (date, {field}) VALUES (?, ?) "
                f"ON CONFLICT(date) DO UPDATE SET {field}=excluded.{field}, "
                "updated_at=datetime('now','localtime')", (date, value))
            con.execute(
                "INSERT INTO health_import_metrics VALUES (?, ?, ?, ?) "
                "ON CONFLICT(date, field) DO UPDATE SET sample_ts=excluded.sample_ts, value=excluded.value",
                (date, field, ts, value))
            dates.add(date)
            result["updated_fields"] += 1
        result["dates"] = sorted(dates)
        result["last_sync_at"] = datetime.now(timezone.utc).isoformat()
        con.execute("INSERT INTO settings (key,value) VALUES ('apple_health_sync',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(result),))
    return result


def status(con):
    row = con.execute("SELECT value FROM settings WHERE key='apple_health_sync'").fetchone()
    return json.loads(row[0]) if row else None
