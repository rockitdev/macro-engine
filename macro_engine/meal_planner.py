"""Cookbook decisions, planned meals and prepared portions shared by the app and MCP."""
import datetime
import json
import re
import secrets

from . import fit_meal, tracker

NUTRIENTS = {"kcal": "calories", "protein_g": "protein", "carb_g": "carbs",
             "fat_g": "fat", "fiber_g": "fiber"}


def _ready_minutes(layout):
    direct = re.search(r"Ready[ \t]+in[ \t]*:?[ \t]*(\d+)[ \t]*min", layout, re.I)
    if direct:
        return int(direct[1])
    lines = layout.splitlines()
    for index, line in enumerate(lines[:20]):
        if "READY IN" in line:
            column = line.index("READY IN")
            for following in lines[index+1:index+4]:
                for match in re.finditer(r"(\d+) MINUTES?\b", following):
                    if abs(match.start()-column) <= 6:
                        return int(match[1])
    return None


def _dish(record):
    published = record["macros"]
    if not all(key in published for key in ("calories", "protein", "carbs", "fat")):
        return None
    title = record["title"]
    page = int(record["page"])
    if record["id"].startswith("kory"):
        section = "breakfast" if page <= 53 else "main" if page <= 111 else "snacks"
    elif record["id"].startswith("greg2"):
        section = "breakfast" if page < 60 else "main" if page < 123 else "snacks"
    else:
        section = "breakfast" if page < 21 else "main" if page < 52 else "snacks"
    return {"id": record["id"], "title": title, "book": record["book"], "section": section,
            "minutes": _ready_minutes(record["layout"]),
            "macros": {k: float(published[v]) if v in published else None for k, v in NUTRIENTS.items()}}


def _servings(value):
    number = tracker._num(value, "servings", positive=True)
    if number > 100 or number * 4 != int(number * 4):
        raise ValueError("Use quarter servings, up to 100.")
    return number


def _meal(value):
    meal = tracker._meal(value)
    if not meal:
        raise ValueError("Choose a meal.")
    return meal


def _scaled(dish, servings):
    return {k: round(v * servings, 2) if v is not None else None for k, v in dish["macros"].items()}


def next_meal(con, date=None, meal="dinner", mode="cook", query="", offset=0, minutes=None, servings=1):
    date = tracker._valid_date(date) if date else tracker._today()
    meal = _meal(meal)
    if mode not in ("cook", "ready"):
        raise ValueError("Choose cook or ready.")
    portions = _servings(servings)
    offset = int(offset)
    records = [json.loads(r[0]) for r in con.execute("SELECT record_json FROM cookbook_entries")]
    dishes = {d["id"]: d for r in records if (d := _dish(r))}
    stock = dict(con.execute("SELECT recipe_id, servings FROM prepared_meals WHERE servings > 0"))
    end = (datetime.date.fromisoformat(date) + datetime.timedelta(days=6)).isoformat()
    plans = []
    for row in con.execute("SELECT * FROM meal_plans WHERE date BETWEEN ? AND ? ORDER BY date,id", (date, end)):
        dish = dishes.get(row["recipe_id"])
        if dish:
            plans.append({**dict(row), "title": dish["title"], "macros": _scaled(dish, row["servings"])})
    required = {}
    for plan in plans:
        key = plan["recipe_id"]
        required[key] = required.get(key, 0) + plan["servings"]
    prep_list = [{"id": key, "title": dishes[key]["title"], "needed": total - stock.get(key, 0)}
                 for key, total in required.items() if total > stock.get(key, 0)]
    today_plans = [p for p in plans if p["date"] == date]
    remaining = tracker.remaining(con, date)["remaining"] or {}
    available = {k: round(v - sum(p["macros"][k] or 0 for p in today_plans), 1)
                 for k, v in remaining.items() if v is not None}
    taken = {r[0] for r in con.execute("SELECT DISTINCT meal FROM log WHERE date=? AND meal IS NOT NULL", (date,))}
    taken.update(p["meal"] for p in today_plans)
    slots = max(1, fit_meal.get_meals_per_day(con) - len(taken))
    budget = available.get("kcal")
    target = max(0, budget / slots) if budget is not None else None
    protein = max(0, available.get("protein_g", 0) / slots)
    section = "main" if meal in ("lunch", "dinner") else meal
    words = query.casefold().split()
    pantry = [name.casefold().strip() for name, in con.execute("SELECT name FROM foods WHERE in_kitchen=1")
              if name and len(name.strip()) >= 3]
    ranked = []
    for r in records:
        dish = dishes.get(r["id"])
        if not dish:
            continue
        ready = stock.get(dish["id"], 0)
        if mode == "ready" and ready < portions:
            continue
        if mode == "cook" and minutes is not None and (dish["minutes"] is None or dish["minutes"] > int(minutes)):
            continue
        search_text = (dish["title"] + " " + r["layout"]).casefold()
        if words and query != dish["id"] and not all(w in search_text for w in words):
            continue
        nutrition = _scaled(dish, portions)
        kcal = nutrition["kcal"]
        score = abs(kcal-target) / max(target, 100) if target is not None else 0
        score += max(0, protein-nutrition["protein_g"]) / max(protein, 1) * 0.5
        matches = [name for name in pantry if re.search(r"\b"+re.escape(name)+r"\b", search_text)]
        score -= 0.1 * min(3, len(matches))
        score += 0.9 if dish["section"] != section else 0
        if words and all(w in dish["title"].casefold() for w in words):
            score -= 1
        after = {k: round(v-nutrition[k], 1) for k, v in available.items() if nutrition[k] is not None}
        ranked.append((score, {**dish, "ready": ready, "kitchen_matches": matches[:3], "portion": nutrition, "after": after}))
    ranked.sort(key=lambda pair: (pair[0], pair[1]["title"], pair[1]["id"]))
    start = max(0, offset) % len(ranked) if ranked else 0
    return {"choices": [d for _, d in (ranked[start:]+ranked[:start])[:3]], "count": len(ranked),
            "available": available, "meal_kcal": round(target) if target is not None else None,
            "slots": slots, "plans": plans, "prep_list": prep_list, "prepared": [{"id": k, "title": dishes[k]["title"], "servings": v}
            for k, v in stock.items() if k in dishes], "date": date}


def meal_action(con, action, request_id, recipe_id=None, servings=1, date=None, meal="dinner", plan_id=None,
                use_prepared=False, days=1):
    """Commit inventory, plan and log changes as one retry-safe operation."""
    if not isinstance(request_id, str) or not 1 <= len(request_id.strip()) <= 128:
        raise ValueError("Supply a stable request_id for this action.")
    if not isinstance(use_prepared, bool):
        raise ValueError("use_prepared must be true or false")
    if type(days) is not int or not 1 <= days <= 7 or (days != 1 and action != "plan"):
        raise ValueError("Plan one to seven consecutive days.")
    payload = json.dumps([action, recipe_id, servings, date, meal, plan_id, use_prepared, days])
    with con:
        con.execute("BEGIN IMMEDIATE")
        previous = con.execute("SELECT * FROM meal_action_receipts WHERE request_id=?", (request_id,)).fetchone()
        if previous:
            if previous["payload"] != payload:
                raise ValueError("This request_id was already used for a different action.")
            return json.loads(previous["result"])
        if action not in ("plan", "prep", "eat", "remove_plan", "discard"):
            raise ValueError("Unknown meal action.")
        if plan_id is not None:
            if action not in ("eat", "remove_plan"):
                raise ValueError("Only eat or remove a planned meal.")
            plan = con.execute("SELECT * FROM meal_plans WHERE id=?", (plan_id,)).fetchone()
            if not plan:
                raise ValueError("This meal is no longer planned.")
            recipe_id, servings, date, meal = (plan[k] for k in ("recipe_id", "servings", "date", "meal"))
        elif action == "remove_plan":
            raise ValueError("Choose a planned meal.")
        date = tracker._valid_date(date) if date else tracker._today()
        if action == "eat" and date > tracker._today():
            raise ValueError("A future meal can be planned and logged on the day you eat it.")
        meal = _meal(meal)
        servings = _servings(servings)
        row = con.execute("SELECT record_json FROM cookbook_entries WHERE id=?", (recipe_id,)).fetchone()
        dish = _dish(json.loads(row[0])) if row else None
        if not dish:
            raise ValueError("Choose a recipe with a published per-serving breakdown.")
        result = {"action": action, "recipe_id": recipe_id, "servings": servings}
        if action == "plan":
            ids = []
            for day in range(days):
                planned_date = datetime.date.fromisoformat(date) + datetime.timedelta(days=day)
                plan_key = secrets.randbits(52)
                con.execute("INSERT INTO meal_plans(id,recipe_id,date,meal,servings) VALUES(?,?,?,?,?)",
                            (plan_key, recipe_id, planned_date.isoformat(), meal, servings))
                ids.append(plan_key)
            result.update(plan_id=ids[0], plan_ids=ids)
        if action == "prep":
            con.execute("""INSERT INTO prepared_meals VALUES(?,?) ON CONFLICT(recipe_id)
                        DO UPDATE SET servings=servings+excluded.servings""", (recipe_id, servings))
        if action == "discard" or (action == "eat" and use_prepared):
            changed = con.execute("""UPDATE prepared_meals SET servings=servings-?
                                  WHERE recipe_id=? AND servings>=?""", (servings, recipe_id, servings))
            if not changed.rowcount:
                raise ValueError("There are fewer prepared servings left. Refresh and choose another portion.")
        if action == "eat":
            item = {"name": f"{dish['title']} · {servings:g} serving(s)", "macros": _scaled(dish, servings)}
            result["log_id"] = tracker._log_item(con, item, date, dish["book"], meal)["logged"]["log_id"]
        if plan_id is not None:
            con.execute("DELETE FROM meal_plans WHERE id=?", (plan_id,))
        con.execute("INSERT INTO meal_action_receipts VALUES(?,?,?)", (request_id, payload, json.dumps(result)))
        return result
