import datetime
import json
import unittest

from macro_engine import db, meal_planner as planner, tracker


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:")
        self.addCleanup(self.con.close)
        self.date = tracker._today()
        record = {"id": "greg2-106-0", "title": "Chicken Cacciatore", "book": "Test book",
                  "page": "106", "layout": "      PREP TIME       READY IN\n      15 MINUTES      30 MINUTES",
                  "macros": {"calories": "400", "fat": "12", "carbs": "30", "protein": "40"}}
        self.con.execute("INSERT INTO cookbook_entries VALUES(?,?,?,?,?)",
                         (record["id"], record["book"], record["title"], "106", json.dumps(record)))
        self.con.commit()
        self.recipe = record["id"]

    def action(self, kind, key, **kwargs):
        return planner.meal_action(self.con, kind, key, self.recipe, date=self.date, **kwargs)

    def test_prepare_plan_eat_once(self):
        self.action("prep", "prep", servings=3)
        result = self.action("plan", "plan", servings=1.5, days=2)
        self.assertEqual(result, self.action("plan", "plan", servings=1.5, days=2))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM log").fetchone()[0], 0)
        board = planner.next_meal(self.con, self.date, mode="ready")
        self.assertEqual(len(board["plans"]), 2)
        self.assertEqual(board["choices"][0]["ready"], 3)
        self.assertEqual(board["choices"][0]["minutes"], 30)
        action = dict(plan_id=result["plan_id"], use_prepared=True)
        eaten = self.action("eat", "eat", **action)
        self.assertEqual(eaten, self.action("eat", "eat", **action))
        self.assertEqual(self.con.execute("SELECT kcal FROM log").fetchone()[0], 600)
        board = planner.next_meal(self.con, self.date)
        self.assertEqual(board["prepared"][0]["servings"], 1.5)
        self.assertEqual(len(board["plans"]), 1)
        with self.assertRaises(ValueError):
            self.action("eat", "different", **action)
        self.assertEqual(len(self.con.execute("SELECT * FROM log").fetchall()), 1)

    def test_unavailable_stock_rolls_back(self):
        plan = self.action("plan", "p")
        with self.assertRaises(ValueError):
            self.action("eat", "e", plan_id=plan["plan_id"], use_prepared=True)
        self.assertEqual(len(planner.next_meal(self.con)["plans"]), 1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM log").fetchone()[0], 0)
        with self.assertRaises(ValueError):
            self.action("prep", "p")
        self.assertEqual(planner.next_meal(self.con, minutes=15)["count"], 0)

    def test_plans_reserve_budget_and_move_into_log(self):
        tracker.set_targets(self.con, kcal=2000, protein_g=120, carb_g=210, fat_g=60)
        planned = self.action("plan", "a")
        board = planner.next_meal(self.con)
        self.assertEqual(board["available"]["kcal"], 1600)
        self.assertEqual(board["choices"][0]["after"]["kcal"], 1200)
        self.action("eat", "b", plan_id=planned["plan_id"])
        self.assertEqual(planner.next_meal(self.con)["available"]["kcal"], 1600)
        future = (datetime.date.today()+datetime.timedelta(days=2)).isoformat()
        with self.assertRaises(ValueError):
            planner.meal_action(self.con, "eat", "future", self.recipe, date=future)
