"""Structural checks, not a substitute for importing and running on an iPhone."""
import importlib.util
import plistlib
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from macro_engine import activity, db

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_health_shortcut", ROOT / "scripts/build_health_shortcut.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

ORIGIN = "https://example.test"
LIVE_ORIGIN = "https://moore.tailc24dca.ts.net:4325"


def actions_of(workflow):
    return workflow["WFWorkflowActions"]


def ident(action):
    return action["WFWorkflowActionIdentifier"].removeprefix("is.workflow.actions.")


def json_fields(action):
    items = action["WFWorkflowActionParameters"]["WFJSONValues"]["Value"]["WFDictionaryFieldValueItems"]
    return {i["WFKey"]["Value"]["string"]: i for i in items}


def requests(workflow):
    return [a for a in actions_of(workflow) if ident(a) == "downloadurl"]


def activity_request(workflow):
    return next(a for a in requests(workflow)
                if a["WFWorkflowActionParameters"]["WFURL"].endswith("/apple-health/activity"))


def finds(workflow):
    out = []
    for a in actions_of(workflow):
        if ident(a) == "filter.health.quantity":
            rows = a["WFWorkflowActionParameters"]["WFContentItemFilter"]["Value"]["WFActionParameterFilterTemplates"]
            out.append((a, rows[0]["Values"]["Enumeration"]["Value"], rows[1]))
    return out


class HealthShortcutTest(unittest.TestCase):
    def variants(self):
        return [builder.build(ORIGIN), builder.build(ORIGIN, include_rhr=False)]

    def test_control_flow_references_and_variables(self):
        for workflow in self.variants():
            self.assertEqual(plistlib.loads(builder.serialize(workflow)), workflow)
            seen, groups, named = set(), [], {"Repeat Item"}
            loop_depth = 0

            def check_refs(value):
                if isinstance(value, dict):
                    if "OutputUUID" in value:
                        self.assertIn(value["OutputUUID"], seen)
                    if value.get("Type") == "Variable" and "VariableName" in value:
                        self.assertIn(value["VariableName"], named)
                        if value["VariableName"] == "Repeat Item":
                            self.assertGreater(loop_depth, 0)
                    for child in value.values():
                        check_refs(child)
                elif isinstance(value, list):
                    for child in value:
                        check_refs(child)

            for action in actions_of(workflow):
                params = action["WFWorkflowActionParameters"]
                check_refs(params)
                if "WFControlFlowMode" in params:
                    mode = params["WFControlFlowMode"]
                    if mode == 0:
                        groups.append(params["GroupingIdentifier"])
                        loop_depth += ident(action) == "repeat.each"
                    elif mode == 1:
                        self.assertEqual(groups[-1], params["GroupingIdentifier"])
                    else:
                        self.assertEqual(groups.pop(), params["GroupingIdentifier"])
                        loop_depth -= ident(action) == "repeat.each"
                if ident(action) in ("setvariable", "appendvariable"):
                    named.add(params["WFVariableName"])
                self.assertNotIn(params["UUID"], seen)
                seen.add(params["UUID"])
            self.assertEqual(groups, [])

    def test_import_question_points_at_authorization_action(self):
        for workflow in self.variants():
            questions = workflow["WFWorkflowImportQuestions"]
            self.assertEqual(len(questions), 1)
            action = actions_of(workflow)[questions[0]["ActionIndex"]]
            self.assertEqual(ident(action), "gettext")
            params = action["WFWorkflowActionParameters"]
            self.assertEqual(params[questions[0]["ParameterKey"]], "Bearer PASTE_YOUR_SYNC_TOKEN")
            for request in requests(workflow):
                header = request["WFWorkflowActionParameters"]["WFHTTPHeaders"]["Value"]["WFDictionaryFieldValueItems"][0]
                ref = header["WFValue"]["Value"]["attachmentsByRange"]["{0, 1}"]
                self.assertEqual(ref["OutputUUID"], params["UUID"])

    def test_read_only_actions_and_expected_health_types(self):
        allowed = {"comment", "gettext", "date", "format.date", "downloadurl", "getvalueforkey",
                   "conditional", "showresult", "exit", "filter.health.quantity", "repeat.each",
                   "properties.health.quantity", "setvariable", "appendvariable", "nothing",
                   "text.combine", "notification"}
        full, without = self.variants()
        for workflow in (full, without):
            self.assertLessEqual({ident(a) for a in actions_of(workflow)}, allowed)
            for action, _, date_row in finds(workflow):
                self.assertFalse(action["WFWorkflowActionParameters"]["WFContentItemLimitEnabled"])
                self.assertEqual(date_row["Operator"], 1001)
        self.assertEqual(sorted({t for _, t, _ in finds(full)}),
                         ["Body Fat Percentage", "Resting Heart Rate", "Steps", "Weight"])
        self.assertEqual(sorted({t for _, t, _ in finds(without)}), ["Body Fat Percentage", "Steps", "Weight"])
        windows = {(t, row["Values"]["Number"], row["Values"]["Unit"]) for _, t, row in finds(full)}
        self.assertEqual(windows, {("Weight", "1", 8192), ("Body Fat Percentage", "1", 8192),
                                   ("Steps", "8", 16), ("Steps", "31", 16),
                                   ("Resting Heart Rate", "8", 16), ("Resting Heart Rate", "31", 16)})
        details = {a["WFWorkflowActionParameters"]["WFContentItemPropertyName"]
                   for a in actions_of(full) if ident(a) == "properties.health.quantity"}
        self.assertEqual(details, {"Value", "Unit", "Start Date", "Source"})

    def test_destinations(self):
        workflow = builder.build(ORIGIN)
        calls = [(r["WFWorkflowActionParameters"]["WFURL"], r["WFWorkflowActionParameters"]["WFHTTPMethod"])
                 for r in requests(workflow)]
        self.assertEqual(calls, [
            (ORIGIN + "/api/import/apple-health/activity/status", "GET"),
            (ORIGIN + "/api/import/apple-health", "POST"),
            (ORIGIN + "/api/import/apple-health", "POST"),
            (ORIGIN + "/api/import/apple-health/activity", "POST")])
        with self.assertRaises(ValueError):
            builder.build("http://192.168.1.60:4325")

    def test_body_metric_requests_unchanged(self):
        metrics = []
        for request in requests(builder.build(ORIGIN))[1:3]:
            fields = json_fields(request)
            self.assertEqual(set(fields), {"metric", "value", "unit", "date"})
            # WorkflowKit's number code is 3; 1 requires a nested dictionary
            # state and crashes Apple's signer with our scalar token string.
            self.assertEqual(fields["value"]["WFItemType"], 3)
            metrics.append(fields["metric"]["WFValue"]["Value"]["string"])
        self.assertEqual(metrics, ["weight", "body_fat_percentage"])

    def test_scalar_dictionary_rejects_nested_dictionary_type_code(self):
        with self.assertRaisesRegex(ValueError, "number=3"):
            builder.dictionary([("value", {"Type": "Variable", "VariableName": "Weight"}, 1)])

    def test_every_date_is_iso8601_with_time(self):
        workflow = builder.build(ORIGIN)
        by_id = {a["WFWorkflowActionParameters"]["UUID"]: a for a in actions_of(workflow)}
        formatters = [a for a in actions_of(workflow) if ident(a) == "format.date"]
        self.assertEqual(len(formatters), 5)   # captured_at, weight, body fat, steps, RHR
        for formatter in formatters:
            params = formatter["WFWorkflowActionParameters"]
            self.assertEqual(params["WFDateFormatStyle"], "ISO 8601")
            self.assertIs(params["WFISO8601IncludeTime"], True)
            self.assertNotIn("WFDateFormat", params)
            self.assertNotIn("WFDateFormatString", params)
            source = by_id[params["WFDate"]["Value"]["attachmentsByRange"]["{0, 1}"]["OutputUUID"]]
            source_params = source["WFWorkflowActionParameters"]
            if ident(source) == "date":
                self.assertEqual(source_params["WFDateActionMode"], "Current Date")
            else:
                self.assertEqual(source_params["WFContentItemPropertyName"], "Start Date")
                self.assertEqual(source_params["WFInput"]["Value"]["VariableName"], "Repeat Item")

    def test_captured_at_is_taken_before_any_health_query(self):
        names = [ident(a) for a in actions_of(builder.build(ORIGIN))]
        self.assertLess(names.index("date"), names.index("filter.health.quantity"))

    def test_sample_lines_match_server_parser(self):
        workflow = builder.build(ORIGIN)
        by_id = {a["WFWorkflowActionParameters"]["UUID"]: a for a in actions_of(workflow)}
        lines = [a for a in actions_of(workflow) if ident(a) == "gettext"
                 and a["WFWorkflowActionParameters"]["UUID"] != actions_of(workflow)[
                     workflow["WFWorkflowImportQuestions"][0]["ActionIndex"]]["WFWorkflowActionParameters"]["UUID"]]
        self.assertEqual(len(lines), 2)
        for line in lines:
            value = line["WFWorkflowActionParameters"]["WFTextActionText"]["Value"]
            self.assertEqual(value["string"], "￼\t￼\t￼")
            order = []
            for rng in ("{0, 1}", "{2, 1}", "{4, 1}"):
                src = by_id[value["attachmentsByRange"][rng]["OutputUUID"]]
                p = src["WFWorkflowActionParameters"]
                order.append(p.get("WFContentItemPropertyName") or ident(src))
            self.assertEqual(order, ["Value", "format.date", "Source"])

    def test_activity_payload_matches_server_contract(self):
        for include_rhr in (True, False):
            fields = json_fields(activity_request(builder.build(ORIGIN, include_rhr=include_rhr)))
            for item in fields.values():
                self.assertIn(item["WFItemType"], (0, 3))
            self.assertEqual(fields["schema_version"]["WFItemType"], 3)
            # Replace each variable with a representative value and run the real importer.
            sample = {"captured_at": "2026-11-09T18:00:00-04:00", "window": "normal",
                      "steps_samples": "1,234\t2026-11-08T10:00:00-04:00\tRyan's Apple Watch",
                      "resting_heart_rate_samples": "61\t2026-11-08T08:00:00-04:00\tRyan's Apple Watch"}
            payload = {}
            for key, item in fields.items():
                value = item["WFValue"]["Value"]
                literal = value["string"] if "attachmentsByRange" not in value else sample[key]
                payload[key] = int(literal) if item["WFItemType"] == 3 else literal
            with tempfile.TemporaryDirectory() as tmp:
                con = db.connect(Path(tmp) / "t.db")
                try:
                    result = activity.import_activity(
                        con, payload, now=datetime(2026, 11, 9, 23, tzinfo=timezone.utc))
                finally:
                    con.close()
            self.assertEqual(result["steps"]["availability"], "observed")
            self.assertEqual(result["workouts"], {"availability": "unsupported"})
            self.assertEqual(result["resting_heart_rate"]["availability"],
                             "observed" if include_rhr else "unsupported")

    def test_catch_up_branch_uses_server_mode(self):
        workflow = builder.build(ORIGIN)
        check = next(a for a in actions_of(workflow) if ident(a) == "conditional"
                     and a["WFWorkflowActionParameters"].get("WFCondition") == 4)
        params = check["WFWorkflowActionParameters"]
        self.assertEqual(params["WFConditionalActionString"], "catch_up")
        mode = next(a for a in actions_of(workflow)
                    if a["WFWorkflowActionParameters"]["UUID"] == params["WFInput"]["Variable"]["Value"]["OutputUUID"])
        self.assertEqual(mode["WFWorkflowActionParameters"]["WFDictionaryKey"], "mode")
        self.assertEqual(set(activity.WINDOW_DAYS.items()), set(builder.WINDOW_DAYS.items()))

    def test_failures_stop_before_the_final_notice(self):
        workflow = builder.build(ORIGIN)
        names = [ident(a) for a in actions_of(workflow)]
        self.assertEqual(names.count("exit"), 4)            # status, weight, body fat, activity
        self.assertEqual(names[-1], "notification")
        body = actions_of(workflow)[-1]["WFWorkflowActionParameters"]["WFNotificationActionBody"]["Value"]
        self.assertEqual(body["string"], "￼")           # server-provided notice only
        for action in actions_of(workflow):
            if ident(action) == "showresult":
                self.assertTrue(action["WFWorkflowActionParameters"]["Text"]["Value"]["string"]
                                .startswith("Sync failed ("))

    def test_no_credentials_or_excluded_categories(self):
        for name in builder.VARIANTS:
            data = (ROOT / "web/public/shortcuts" / name).read_bytes()
            text = data.decode("latin-1")
            # Never echo matches: a failure here must not print a credential.
            found = re.findall(r"Bearer [A-Za-z0-9_\-]{8,}", text)
            self.assertTrue(found == ["Bearer PASTE_YOUR_SYNC_TOKEN"], f"credential-like text in {name}")
            for word in ("Sleep", "Active Calories", "Heart Rate Variability", "health.quantity.log",
                         "health.workout.log", "runscript", "Log Workout"):
                self.assertFalse(word in text, f"{word} in {name}")

    def test_download_matches_builder(self):
        for name, options in builder.VARIANTS.items():
            actual = (ROOT / "web/public/shortcuts" / name).read_bytes()
            self.assertEqual(actual, builder.serialize(builder.build(LIVE_ORIGIN, **options)))


if __name__ == "__main__":
    unittest.main()
