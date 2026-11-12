"""My Kitchen, usual picks, and recipes: the fast ways to log without searching.

- Kitchen: foods flagged in_kitchen (things Ryan keeps on hand).
- Usuals: foods ranked from the log itself, weighted toward the current meal.
- Recipes: a food (source='recipe') built from ingredients, logged by serving.

Every pick carries a 'usual' amount: an item ready to send to log_meal, taken
from the last time the food was logged.
"""

import datetime as _dt
import sqlite3

from . import resolve, tracker
from .tracker import MACROS

SERVING = "1 serving"
USUAL_WINDOW_DAYS = 60


def food_detail(con: sqlite3.Connection, food_id: int) -> dict | None:
    food = resolve.get_food(con, food_id)
    if not food:
        return None
    food["portions"] = resolve.get_portions(con, food_id)
    food["matched_alias"] = False
    return food


# ── Usual amounts ────────────────────────────────────────────────────────────

def _usual(food: dict, row) -> dict:
    """The amount to offer for a one-tap log: what was logged last time (row),
    else the food's everyday portion, else 100 g. 'item' merges into a log_meal
    item alongside food_id."""
    portions = food.get("portions") or []
    by_label = {p["label"]: p for p in portions}
    item, desc, grams = None, None, None
    if row is not None and row["grams"] and row["grams"] > 0:
        count = tracker._DESC_COUNT.match(row["qty_desc"] or "")
        if count and count[2] in by_label:
            qty = float(count[1])
            item = {"qty": qty, "unit": count[2]}
            desc = f"{tracker._fmt_qty(qty)} x {count[2]}"
            grams = qty * by_label[count[2]]["grams"]
        else:
            grams = round(row["grams"], 1)
            item = {"grams": grams}
            desc = f"{tracker._fmt_qty(grams)} g"
    elif portions:
        p = min(portions, key=lambda p: tracker._default_portion_rank(p["label"]))
        item, desc, grams = {"qty": 1, "unit": p["label"]}, f"1 x {p['label']}", p["grams"]
    else:
        item, desc, grams = {"grams": 100}, "100 g", 100.0
    out = {"item": item, "desc": desc, "grams": round(grams, 1)}
    for k in MACROS:
        out[k] = round((food.get(k) or 0) * grams / 100.0, 1)
    return out


def _last_row(con: sqlite3.Connection, food_id: int):
    return con.execute(
        "SELECT qty_desc, grams FROM log WHERE food_id = ? ORDER BY id DESC LIMIT 1",
        (food_id,)).fetchone()


def _window_start(date: str) -> str:
    return (_dt.date.fromisoformat(date) - _dt.timedelta(days=USUAL_WINDOW_DAYS)).isoformat()


def usual_picks(con: sqlite3.Connection, meal: str | None = None,
                date: str | None = None, limit: int = 8) -> list[dict]:
    """Foods logged in the 60 days up to date (default today), most useful
    first: logged often, at this meal, and recently. Each is a food (per-100g
    macros, portions) plus 'usual' (the amount last logged, at this meal when
    there is one), 'times' and 'last_date'."""
    date = tracker._valid_date(date) if date else tracker._today()
    meal = tracker._meal(meal)
    rows = con.execute(
        """SELECT l.food_id, COUNT(*) AS times,
                  SUM(CASE WHEN l.meal = ? THEN 1 ELSE 0 END) AS at_meal,
                  MAX(l.id) AS last_id,
                  MAX(CASE WHEN l.meal = ? THEN l.id END) AS last_meal_id,
                  MAX(l.date) AS last_date
           FROM log l
           JOIN foods f ON f.id = l.food_id
           LEFT JOIN recipes r ON r.food_id = l.food_id
           WHERE l.date >= ? AND l.date <= ? AND COALESCE(r.archived, 0) = 0
           GROUP BY l.food_id""",
        (meal, meal, _window_start(date), date),
    ).fetchall()
    today = _dt.date.fromisoformat(date)

    def score(r) -> float:
        age = (today - _dt.date.fromisoformat(r["last_date"])).days
        return r["at_meal"] * 3 + r["times"] + max(0.0, 1 - age / 14) * 2

    picks = []
    for r in sorted(rows, key=lambda r: (-score(r), -r["last_id"]))[:max(0, int(limit))]:
        food = food_detail(con, r["food_id"])
        if not food or food.get("kcal") is None:
            continue
        last = con.execute("SELECT qty_desc, grams FROM log WHERE id = ?",
                           (r["last_meal_id"] or r["last_id"],)).fetchone()
        food.update(usual=_usual(food, last), times=r["times"],
                    at_meal=r["at_meal"], last_date=r["last_date"])
        picks.append(food)
    return picks


# ── My Kitchen ───────────────────────────────────────────────────────────────

def set_kitchen(con: sqlite3.Connection, food_id: int, in_kitchen: bool = True) -> dict | None:
    """Add a food to My Kitchen or take it out. Returns the food, or None when
    the id is missing."""
    if not isinstance(in_kitchen, bool):
        raise ValueError("in_kitchen must be true or false")
    cur = con.execute("UPDATE foods SET in_kitchen = ? WHERE id = ?",
                      (1 if in_kitchen else 0, int(food_id)))
    con.commit()
    if cur.rowcount == 0:
        return None
    return food_detail(con, int(food_id))


def kitchen_list(con: sqlite3.Connection, date: str | None = None) -> list[dict]:
    """Foods in My Kitchen, most-logged first. Each is a food plus 'usual' (the
    amount last logged, or its everyday portion), 'times' (logs in the last 60
    days) and 'last_date'."""
    date = tracker._valid_date(date) if date else tracker._today()
    rows = con.execute(
        """SELECT f.id,
                  (SELECT COUNT(*) FROM log l WHERE l.food_id = f.id AND l.date >= ?) AS times,
                  (SELECT MAX(l.date) FROM log l WHERE l.food_id = f.id) AS last_date
           FROM foods f LEFT JOIN recipes r ON r.food_id = f.id
           WHERE f.in_kitchen = 1 AND COALESCE(r.archived, 0) = 0
           ORDER BY times DESC, f.name COLLATE NOCASE""",
        (_window_start(date),),
    ).fetchall()
    foods = []
    for r in rows:
        food = food_detail(con, r["id"])
        food.update(usual=_usual(food, _last_row(con, r["id"])),
                    times=r["times"], last_date=r["last_date"])
        foods.append(food)
    return foods


# ── Recipes ──────────────────────────────────────────────────────────────────

def _fts_remove(con: sqlite3.Connection, food_id: int, name: str, brand, store) -> None:
    # food_fts is contentless: a row can only be removed by naming its old text.
    con.execute(
        "INSERT INTO food_fts(food_fts, rowid, name, brand, store) VALUES('delete', ?, ?, ?, ?)",
        (food_id, name, brand or "", store or ""))


def _servings(value) -> float:
    return tracker._num(value, "servings", positive=True)


def _log_rows(con: sqlite3.Connection, log_ids) -> list[dict]:
    rows = []
    for log_id in tracker._ids(log_ids):
        r = con.execute(
            """SELECT l.food_id, COALESCE(f.name, l.qty_desc, 'estimate') AS name,
                      CASE WHEN l.food_id IS NULL THEN NULL ELSE l.qty_desc END AS qty_desc,
                      l.grams, l.kcal, l.protein_g, l.carb_g, l.fat_g, l.fiber_g
               FROM log l LEFT JOIN foods f ON f.id = l.food_id WHERE l.id = ?""",
            (log_id,)).fetchone()
        if r is None:
            raise ValueError(f"no log entry {log_id}")
        rows.append(dict(r))
    return rows


def _ingredients(con: sqlite3.Connection, items, log_ids) -> tuple[list[dict], list[dict]]:
    rows, problems = [], []
    if log_ids:
        rows += _log_rows(con, log_ids)
    if items:
        if not isinstance(items, list):
            raise ValueError("items must be a list")
        resolved, problems = tracker.resolve_items(con, items)
        rows += resolved
    return rows, problems


def _insert_items(con: sqlite3.Connection, recipe_id: int, rows: list[dict]) -> None:
    for r in rows:
        con.execute(
            """INSERT INTO recipe_items (recipe_id, food_id, name, qty_desc, grams, kcal,
                                         protein_g, carb_g, fat_g, fiber_g)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (recipe_id, r["food_id"], r["name"], r["qty_desc"], r["grams"],
             *(r[k] or 0 for k in MACROS)))


def _recompute(con: sqlite3.Connection, recipe_id: int) -> None:
    """Rewrite the recipe food's per-100 g macros and its '1 serving' portion
    from the ingredients. When any ingredient has no weight the recipe has no
    real weight either, so a serving is given a nominal 100 g."""
    servings = con.execute("SELECT servings FROM recipes WHERE food_id = ?",
                           (recipe_id,)).fetchone()["servings"]
    items = con.execute("SELECT * FROM recipe_items WHERE recipe_id = ?",
                        (recipe_id,)).fetchall()
    totals = {k: sum(i[k] for i in items) for k in MACROS}
    weighed = all(i["grams"] and i["grams"] > 0 for i in items)
    total_grams = sum(i["grams"] for i in items) if weighed else 100.0 * servings
    per100 = {k: round(totals[k] / total_grams * 100.0, 3) for k in MACROS}
    con.execute(
        """INSERT OR REPLACE INTO food_nutrients (food_id, kcal, protein_g, carb_g, fat_g, fiber_g)
           VALUES (?, ?, ?, ?, ?, ?)""", (recipe_id, *(per100[k] for k in MACROS)))
    con.execute("DELETE FROM portions WHERE food_id = ?", (recipe_id,))
    con.execute("INSERT INTO portions (food_id, label, grams) VALUES (?, ?, ?)",
                (recipe_id, SERVING, total_grams / servings))
    con.execute("UPDATE recipes SET updated_at = datetime('now', 'localtime') WHERE food_id = ?",
                (recipe_id,))


def get_recipe(con: sqlite3.Connection, recipe_id: int) -> dict | None:
    """One recipe: name, servings, per-serving and whole-recipe macros, its
    ingredients, and 'food' (the food it logs as). None when missing or deleted."""
    rec = con.execute("SELECT * FROM recipes WHERE food_id = ? AND archived = 0",
                      (int(recipe_id),)).fetchone()
    if rec is None:
        return None
    food = food_detail(con, rec["food_id"])
    items = [dict(r) for r in con.execute(
        """SELECT id, food_id, name, qty_desc, grams, kcal, protein_g, carb_g, fat_g, fiber_g
           FROM recipe_items WHERE recipe_id = ? ORDER BY id""", (rec["food_id"],))]
    total = {k: round(sum(i[k] for i in items), 1) for k in MACROS}
    serving_grams = next((p["grams"] for p in food["portions"] if p["label"] == SERVING), None)
    return {
        "food_id": rec["food_id"], "name": food["name"], "servings": rec["servings"],
        "serving_grams": round(serving_grams, 1) if serving_grams else None,
        "weighed": all(i["grams"] and i["grams"] > 0 for i in items),
        "in_kitchen": food["in_kitchen"],
        "per_serving": {k: round(total[k] / rec["servings"], 1) for k in MACROS},
        "total": total, "items": items, "food": food, "updated_at": rec["updated_at"],
    }


def list_recipes(con: sqlite3.Connection) -> list[dict]:
    ids = [r["food_id"] for r in con.execute(
        """SELECT r.food_id FROM recipes r JOIN foods f ON f.id = r.food_id
           WHERE r.archived = 0 ORDER BY f.name COLLATE NOCASE""")]
    return [get_recipe(con, i) for i in ids]


def create_recipe(con: sqlite3.Connection, name: str, servings=1, items=None,
                  log_ids=None) -> dict:
    """Save a recipe from ingredients. Give log_ids (existing log entries, taken
    with the amounts and macros they were logged with) and/or items (the same
    shapes log_meal takes; nothing is logged). servings is how many the whole
    recipe makes. Returns {'saved': True, ...recipe}; when an item can't be
    resolved nothing is saved and {'saved': False, 'problems': [...]} comes back."""
    name = tracker._text(name, "name")
    servings = _servings(servings)
    rows, problems = _ingredients(con, items, log_ids)
    if problems:
        return {"saved": False, "problems": problems}
    if not rows:
        raise ValueError("a recipe needs at least one ingredient (items or log_ids)")
    food_id = con.execute(
        "INSERT INTO foods (name, source) VALUES (?, 'recipe') RETURNING id", (name,)).fetchone()[0]
    con.execute("INSERT INTO food_fts (rowid, name, brand, store) VALUES (?, ?, '', '')",
                (food_id, name))
    con.execute("INSERT INTO recipes (food_id, servings) VALUES (?, ?)", (food_id, servings))
    _insert_items(con, food_id, rows)
    _recompute(con, food_id)
    con.commit()
    return {"saved": True, **get_recipe(con, food_id)}


def update_recipe(con: sqlite3.Connection, recipe_id: int, name=None, servings=None,
                  remove_item_ids=None, items=None, log_ids=None) -> dict | None:
    """Change a recipe: rename it, change how many servings it makes, remove
    ingredients (remove_item_ids), or add some (items / log_ids, as in
    create_recipe). Entries already logged from the recipe keep the macros they
    were logged with. Returns like create_recipe, or None when the id is missing."""
    recipe_id = int(recipe_id)
    if get_recipe(con, recipe_id) is None:
        return None
    rows, problems = _ingredients(con, items, log_ids)
    if problems:
        return {"saved": False, "problems": problems}
    if name is not None:
        name = tracker._text(name, "name")
    if servings is not None:
        servings = _servings(servings)
    removing = tracker._ids(remove_item_ids) if remove_item_ids else []
    kept = sum(1 for r in con.execute(
        "SELECT id FROM recipe_items WHERE recipe_id = ?", (recipe_id,))
        if r["id"] not in removing)
    if kept + len(rows) == 0:
        raise ValueError("a recipe needs at least one ingredient")
    if name is not None:
        old = con.execute("SELECT name, brand, store FROM foods WHERE id = ?",
                          (recipe_id,)).fetchone()
        if name != old["name"]:
            _fts_remove(con, recipe_id, old["name"], old["brand"], old["store"])
            con.execute("UPDATE foods SET name = ? WHERE id = ?", (name, recipe_id))
            con.execute("INSERT INTO food_fts (rowid, name, brand, store) VALUES (?, ?, ?, ?)",
                        (recipe_id, name, old["brand"] or "", old["store"] or ""))
    if servings is not None:
        con.execute("UPDATE recipes SET servings = ? WHERE food_id = ?", (servings, recipe_id))
    for item_id in removing:
        con.execute("DELETE FROM recipe_items WHERE id = ? AND recipe_id = ?",
                    (item_id, recipe_id))
    _insert_items(con, recipe_id, rows)
    _recompute(con, recipe_id)
    con.commit()
    return {"saved": True, **get_recipe(con, recipe_id)}


def delete_recipe(con: sqlite3.Connection, recipe_id: int) -> bool:
    """Delete a recipe. It stops showing up in search, usuals and the kitchen;
    days it was already logged on keep their entries."""
    recipe_id = int(recipe_id)
    if get_recipe(con, recipe_id) is None:
        return False
    old = con.execute("SELECT name, brand, store FROM foods WHERE id = ?",
                      (recipe_id,)).fetchone()
    _fts_remove(con, recipe_id, old["name"], old["brand"], old["store"])
    con.execute("UPDATE recipes SET archived = 1, updated_at = datetime('now', 'localtime') "
                "WHERE food_id = ?", (recipe_id,))
    con.execute("UPDATE foods SET in_kitchen = 0 WHERE id = ?", (recipe_id,))
    con.execute("DELETE FROM aliases WHERE food_id = ?", (recipe_id,))
    con.commit()
    return True
