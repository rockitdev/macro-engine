"""Local JSON API for the macro tracker.

Stdlib only. Defaults to 127.0.0.1. During development the Vite app in
``web/`` proxies ``/api`` here. When ``web/dist`` exists, this process also
serves the built app.

    python -m macro_engine.http_api
"""

from __future__ import annotations

import argparse
import json
import os
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import db, fit_meal as _fit_meal_mod, resolve, tracker

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATIC = ROOT / "web" / "dist"
MAX_BODY = 1_000_000

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".map": "application/json",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
    ".webmanifest": "application/manifest+json",
}


def _food_detail(con, food_id: int) -> dict | None:
    food = resolve.get_food(con, food_id)
    if not food:
        return None
    food["portions"] = resolve.get_portions(con, food_id)
    food["matched_alias"] = False
    return food


def _day(con, date: str | None) -> dict:
    summary = tracker.remaining(con, date)
    entries = tracker.day_log(con, summary["date"])
    for entry in entries:
        entry["estimated"] = bool(entry["estimated"])
    return {"entries": entries, **summary}


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


def _query_date(qs: dict) -> str | None:
    raw = (qs.get("date") or [None])[0]
    if raw is None or raw == "":
        return None
    return tracker._valid_date(raw)


def _query_limit(qs: dict) -> int:
    raw = (qs.get("limit") or [None])[0]
    if raw is None:
        return 8
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        raise ValueError("limit must be an integer")
    if not 1 <= limit <= 25:
        raise ValueError("limit must be between 1 and 25")
    return limit


def _as_int(value, field: str) -> int:
    try:
        if isinstance(value, bool) or value is None:
            raise ValueError
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be an integer")


def _read_body(handler: BaseHTTPRequestHandler) -> dict:
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        raise ValueError("Content-Length must be an integer")
    if length < 0 or length > MAX_BODY:
        raise ValueError("body too large")
    raw = handler.rfile.read(length) if length else b""
    if not raw:
        raise ValueError("JSON body required")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("body must be JSON")
    if not isinstance(data, dict):
        raise ValueError("body must be a JSON object")
    return data


def _missing(data: dict, *keys: str) -> None:
    missing = [key for key in keys if key not in data or data[key] is None]
    if missing:
        raise ValueError("missing " + ", ".join(missing))


def handle_request(handler: BaseHTTPRequestHandler, method: str) -> None:
    parsed = urlparse(handler.path)
    path = parsed.path.rstrip("/") or "/"
    qs = parse_qs(parsed.query)
    parts = [p for p in path.split("/") if p]

    if method == "OPTIONS" and path.startswith("/api"):
        _send_empty(handler, 204)
        return

    if method == "GET" and path == "/api/health":
        _send_json(handler, 200, {"ok": True})
        return

    if method == "GET" and path == "/api/day":
        with _connect() as con:
            _send_json(handler, 200, _day(con, _query_date(qs)))
        return

    if method == "GET" and path == "/api/week":
        with _connect() as con:
            _send_json(handler, 200, tracker.week_summary(con, _query_date(qs)))
        return

    if method == "GET" and path == "/api/month":
        with _connect() as con:
            _send_json(handler, 200, tracker.month_summary(con, _query_date(qs)))
        return

    if method == "POST" and path == "/api/settings":
        body = _read_body(handler)
        if "week_starts_on" not in body:
            raise ValueError("missing week_starts_on")
        with _connect() as con:
            _send_json(handler, 200, tracker.set_week_start(con, body["week_starts_on"]))
        return

    if method == "POST" and path == "/api/meals":
        body = _read_body(handler)
        items = body.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("items must be a non-empty list")
        date = body.get("date")
        if date is not None:
            date = tracker._valid_date(date)
        raw_text = body.get("raw_text")
        if raw_text is not None and not isinstance(raw_text, str):
            raise ValueError("raw_text must be a string")
        with _connect() as con:
            result = tracker.log_meal(con, items, date, raw_text)
        _send_json(handler, 200, result)
        return

    if method == "DELETE" and len(parts) == 3 and parts[:2] == ["api", "log"]:
        log_id = _as_int(parts[2], "log_id")
        with _connect() as con:
            deleted = tracker.delete_log_entry(con, log_id)
        if not deleted:
            raise ApiError(404, f"no log entry {log_id}")
        _send_json(handler, 200, {"deleted": True, "log_id": log_id})
        return

    if method == "GET" and path == "/api/foods":
        query = (qs.get("q") or [""])[0]
        with _connect() as con:
            _send_json(handler, 200, resolve.search(con, query, _query_limit(qs)))
        return

    if method == "GET" and len(parts) == 3 and parts[:2] == ["api", "foods"]:
        food_id = _as_int(parts[2], "food_id")
        with _connect() as con:
            food = _food_detail(con, food_id)
        if not food:
            raise ApiError(404, f"no food {food_id}")
        _send_json(handler, 200, food)
        return

    if method == "POST" and path == "/api/foods":
        body = _read_body(handler)
        _missing(body, "name", "kcal", "protein_g", "carb_g", "fat_g")
        per_portion = body.get("macros_are_per_portion", False)
        if not isinstance(per_portion, bool):
            raise ValueError("macros_are_per_portion must be true or false")
        with _connect() as con:
            created = tracker.add_food(
                con,
                body["name"],
                body["kcal"],
                body["protein_g"],
                body["carb_g"],
                body["fat_g"],
                body.get("fiber_g"),
                body.get("brand"),
                body.get("store"),
                body.get("portion_label"),
                body.get("portion_grams"),
                per_portion,
                body.get("alias"),
            )
        _send_json(handler, 200, created)
        return

    if method == "POST" and path == "/api/aliases":
        body = _read_body(handler)
        _missing(body, "phrase", "food_id")
        with _connect() as con:
            saved = tracker.add_alias(
                con, body["phrase"], _as_int(body["food_id"], "food_id"),
                body.get("default_grams"),
            )
        _send_json(handler, 200, saved)
        return

    if method == "POST" and path == "/api/targets":
        body = _read_body(handler)
        _missing(body, "kcal", "protein_g", "carb_g", "fat_g")
        with _connect() as con:
            saved = tracker.set_targets(
                con,
                body["kcal"],
                body["protein_g"],
                body["carb_g"],
                body["fat_g"],
                body.get("fiber_g"),
                body.get("effective_date"),
            )
        _send_json(handler, 200, saved)
        return

    if method == "GET" and path == "/api/chains":
        with _connect() as con:
            result = _fit_meal_mod.chains(con)
        _send_json(handler, 200, result)
        return

    if method == "POST" and path == "/api/fit-meal":
        body = _read_body(handler)
        _missing(body, "restaurant")
        restaurant = body["restaurant"]
        if not isinstance(restaurant, str) or not restaurant.strip():
            raise ValueError("restaurant must be a non-empty string")
        override = body.get("remaining")
        if override is not None and not isinstance(override, dict):
            raise ValueError("remaining must be an object or null")
        date = body.get("date")
        if date is not None:
            date = tracker._valid_date(date)
        with _connect() as con:
            result = _fit_meal_mod.fit_meal(con, restaurant, override, date)
        _send_json(handler, 200, result)
        return

    if method == "GET" and not path.startswith("/api"):
        if _serve_static(handler, path):
            return

    raise ApiError(404, "not found")


def _serve_static(handler: BaseHTTPRequestHandler, path: str) -> bool:
    static_dir = getattr(handler, "static_dir", None)
    if static_dir is None:
        return False
    root = Path(static_dir).resolve()
    rel = path.lstrip("/") or "index.html"
    candidate = (root / rel).resolve()
    try:
        inside = os.path.commonpath([root, candidate]) == str(root)
    except ValueError:
        inside = False
    if inside and candidate.is_file():
        _send_file(handler, candidate)
        return True
    # Hashed assets and other real files should 404, not return the shell.
    if "." in Path(rel).name:
        return False
    index = root / "index.html"
    if index.is_file():
        _send_file(handler, index)
        return True
    return False


class _Closing:
    def __init__(self, con):
        self.con = con

    def __enter__(self):
        return self.con

    def __exit__(self, *exc):
        self.con.close()


def _connect():
    return _Closing(db.connect())


def _cors(handler: BaseHTTPRequestHandler) -> None:
    origin = handler.headers.get("Origin")
    if origin:
        handler.send_header("Access-Control-Allow-Origin", origin)
        handler.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        handler.send_header("Access-Control-Allow-Headers", "Content-Type")
        handler.send_header("Vary", "Origin")


def _send_json(handler: BaseHTTPRequestHandler, status: int, payload) -> None:
    if getattr(handler, "_sent", False):
        return
    handler._sent = True  # type: ignore[attr-defined]
    body = json.dumps(payload, allow_nan=False).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    _cors(handler)
    handler.end_headers()
    handler.wfile.write(body)


def _send_empty(handler: BaseHTTPRequestHandler, status: int) -> None:
    if getattr(handler, "_sent", False):
        return
    handler._sent = True  # type: ignore[attr-defined]
    handler.send_response(status)
    handler.send_header("Content-Length", "0")
    _cors(handler)
    handler.end_headers()


def _send_file(handler: BaseHTTPRequestHandler, path: Path) -> None:
    if getattr(handler, "_sent", False):
        return
    handler._sent = True  # type: ignore[attr-defined]
    data = path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", MIME.get(path.suffix.lower(), "application/octet-stream"))
    handler.send_header("Content-Length", str(len(data)))
    cache = "no-cache" if path.suffix.lower() == ".html" else "public, max-age=3600"
    handler.send_header("Cache-Control", cache)
    handler.end_headers()
    handler.wfile.write(data)


def _dispatch(handler: BaseHTTPRequestHandler, method: str) -> None:
    try:
        handle_request(handler, method)
    except ApiError as exc:
        _send_json(handler, exc.status, {"error": exc.message})
    except ValueError as exc:
        _send_json(handler, 400, {"error": str(exc)})
    except Exception:
        traceback.print_exc()
        _send_json(handler, 500, {"error": "internal error"})


class Handler(BaseHTTPRequestHandler):
    static_dir: Path | None = None

    def do_GET(self):
        _dispatch(self, "GET")

    def do_POST(self):
        _dispatch(self, "POST")

    def do_DELETE(self):
        _dispatch(self, "DELETE")

    def do_OPTIONS(self):
        _dispatch(self, "OPTIONS")

    def log_message(self, fmt, *args):
        if os.environ.get("MACRO_ENGINE_HTTP_QUIET"):
            return
        super().log_message(fmt, *args)


def make_server(host: str, port: int, static_dir: Path | None = None) -> ThreadingHTTPServer:
    class Bound(Handler):
        pass

    Bound.static_dir = static_dir
    httpd = ThreadingHTTPServer((host, port), Bound)
    httpd.daemon_threads = True
    return httpd


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Macro Engine HTTP API and web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--static", default=None, help="built web app directory (default: web/dist if present)")
    args = parser.parse_args(argv)
    static = Path(args.static) if args.static else DEFAULT_STATIC
    static_dir = static if static.is_dir() else None
    httpd = make_server(args.host, args.port, static_dir)
    url = f"http://{args.host}:{args.port}"
    print(f"macro-engine API on {url}", flush=True)
    if static_dir:
        print(f"serving {static_dir}", flush=True)
    else:
        print("no web/dist — API only (run the Vite dev server for the app)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
