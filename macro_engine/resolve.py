"""Food resolution: phrase -> candidate foods.

Order of attack: exact alias (Ryan's learned vocabulary) -> FTS5 AND match ->
FTS5 prefix match. Candidates are re-ranked by bm25 + source quality +
name length, so 'banana' surfaces 'Bananas, raw' before obscure composites.
"""

import re
import sqlite3

SOURCE_RANK = {
    "manual": 0,
    "recipe": 0,
    "fdc_foundation": 1,
    "fdc_sr_legacy": 2,
    "fdc_survey": 3,
    "off": 4,
}


def _normalize(phrase: str) -> str:
    return re.sub(r"\s+", " ", phrase.strip().lower())


def _fts_words(query: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", query.lower())


def lookup_alias(con: sqlite3.Connection, phrase: str):
    """Return (food row dict, default_grams) for an exact learned phrase, or None."""
    row = con.execute(
        """SELECT a.food_id, a.default_grams FROM aliases a
           WHERE a.phrase = ? ORDER BY a.id DESC LIMIT 1""",
        (_normalize(phrase),),
    ).fetchone()
    if not row:
        return None
    food = get_food(con, row["food_id"])
    if not food:
        return None
    return food, row["default_grams"]


def get_food(con: sqlite3.Connection, food_id: int):
    row = con.execute(
        """SELECT f.id, f.name, f.brand, f.store, f.source, f.verified,
                  n.kcal, n.protein_g, n.carb_g, n.fat_g, n.fiber_g
           FROM foods f LEFT JOIN food_nutrients n ON n.food_id = f.id
           WHERE f.id = ?""",
        (food_id,),
    ).fetchone()
    return dict(row) if row else None


def get_portions(con: sqlite3.Connection, food_id: int) -> list[dict]:
    rows = con.execute(
        "SELECT label, grams FROM portions WHERE food_id = ? ORDER BY id LIMIT 12",
        (food_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _fts_query(con: sqlite3.Connection, match_expr: str, limit: int) -> list[sqlite3.Row]:
    return con.execute(
        """SELECT f.id, f.name, f.brand, f.store, f.source, f.verified,
                  n.kcal, n.protein_g, n.carb_g, n.fat_g, n.fiber_g,
                  bm25(food_fts) AS rank
           FROM food_fts
           JOIN foods f ON f.id = food_fts.rowid
           LEFT JOIN food_nutrients n ON n.food_id = f.id
           WHERE food_fts MATCH ?
           ORDER BY rank LIMIT ?""",
        (match_expr, limit),
    ).fetchall()


def search(con: sqlite3.Connection, query: str, limit: int = 8) -> list[dict]:
    """Ranked candidates for a food phrase. Alias hits come back first with
    matched_alias set; each candidate carries per-100g macros and portions."""
    results: list[dict] = []
    seen: set[int] = set()

    alias_hit = lookup_alias(con, query)
    if alias_hit:
        food, default_grams = alias_hit
        food["matched_alias"] = True
        food["alias_default_grams"] = default_grams
        food["portions"] = get_portions(con, food["id"])
        results.append(food)
        seen.add(food["id"])

    words = _fts_words(query)
    rows: list[sqlite3.Row] = []
    if words:
        rows = _fts_query(con, " ".join(words), 50)
        if not rows:
            rows = _fts_query(con, " OR ".join(f"{w}*" for w in words), 50)

    qwords = {w.rstrip("s") for w in words}

    def score(r: sqlite3.Row) -> float:
        s = r["rank"]  # bm25: more negative = better match
        s += SOURCE_RANK.get(r["source"], 5) * 0.3
        s += len(r["name"]) / 30.0  # short-name preference: plain foods beat composites
        # USDA names lead with the food itself ("Apples, raw", "Egg, whole") —
        # a first-word hit beats composites like "Strudel, apple"
        first = _fts_words(r["name"])
        if first and first[0].rstrip("s") in qwords:
            s -= 2.0
        if r["kcal"] is None:
            s += 5.0  # useless without macros
        return s

    for r in sorted(rows, key=score):
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        d = dict(r)
        d.pop("rank", None)
        d["matched_alias"] = False
        d["portions"] = get_portions(con, d["id"])
        results.append(d)
        if len(results) >= limit:
            break
    return results
