"""Build the unsigned iPhone sync Shortcut; signing happens on the user's Mac.

No credentials are read or embedded. The import question supplies the header.
Only Apple actions are used, and nothing writes to Health.

Serialization references (see docs/apple-health-capabilities.md):
- viticci/shortcuts-playground-plugin HEALTHKIT.md (anonymized iOS 26.2 exports):
  Find Health Samples `Type is <label>` rows, Get Details property names.
- electrikmilk/cherri (compiler that produces signed shortcuts): dictionary
  type codes text=0, dictionary=1, array=2, number=3, boolean=4; conditional
  codes (is=4, has any value=100); setvariable/appendvariable/text.combine.
- The working body-metric section of this file, verified on Ryan's iPhone.

Workouts are not readable by any built-in Shortcuts action, so the activity
upload reports them as unsupported; the app has a manual workout fallback.
"""

import argparse
import plistlib
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

WINDOW_DAYS = {"normal": 8, "catch_up": 31}
VARIANTS = {
    "Sync Macro Engine.unsigned.shortcut": {"include_rhr": True},
    "Sync Macro Engine (without resting HR).unsigned.shortcut": {"include_rhr": False},
}


def token(value):
    return {"WFSerializationType": "WFTextTokenAttachment", "Value": value}


def text(*parts):
    """Text with literal strings and variable attachments, in order."""
    string, attachments = "", {}
    for part in parts:
        if isinstance(part, dict):
            # Positions count UTF-16 units; every literal used here is BMP text.
            attachments[f"{{{len(string.encode('utf-16-le')) // 2}, 1}}"] = part
            string += "￼"
        else:
            string += part
    inner = {"string": string}
    if attachments:
        inner["attachmentsByRange"] = attachments
    return {"WFSerializationType": "WFTextTokenString", "Value": inner}


def variable(name):
    return {"Type": "Variable", "VariableName": name}


def dictionary(items):
    # This builder only emits scalar fields. Marking a text-backed number as
    # type 1 makes WorkflowKit expect a dictionary state and abort during sign.
    if any(kind not in (0, 3) for _, _, kind in items):
        raise ValueError("scalar dictionary fields must use text=0 or number=3")
    return {"WFSerializationType": "WFDictionaryFieldValue", "Value": {
        "WFDictionaryFieldValueItems": [
            {"WFItemType": kind, "WFKey": text(key), "WFValue": text(value)}
            for key, value, kind in items]}}


def health_filter(label, number, unit):
    return {"WFSerializationType": "WFContentPredicateTableTemplate", "Value": {
        "WFActionParameterFilterPrefix": 1, "WFContentPredicateBoundedDate": False,
        "WFActionParameterFilterTemplates": [
            {"Property": "Type", "Operator": 4, "Bounded": True, "Removable": False,
             "Values": {"Enumeration": {"WFSerializationType": "WFStringSubstitutableState", "Value": label}}},
            {"Property": "Start Date", "Operator": 1001, "Bounded": True, "Removable": True,
             "Values": {"Number": str(number), "Unit": unit}}]}}


def build(origin, include_rhr=True):
    if not origin.startswith("https://"):
        raise ValueError("Use an HTTPS app origin")
    origin = origin.rstrip("/")
    actions = []

    def uid(label):
        return str(uuid5(NAMESPACE_URL, "macro-engine-health/" + label)).upper()

    def action(name, label, **params):
        actions.append({"WFWorkflowActionIdentifier": "is.workflow.actions." + name,
                        "WFWorkflowActionParameters": {"UUID": uid(label), **params}})
        return {"Type": "ActionOutput", "OutputUUID": uid(label), "OutputName": label}

    def fail_if_error(response, label, what, group=None):
        error = action("getvalueforkey", label + " error", WFDictionaryKey="error", WFInput=token(response))
        group = group or uid("error-group/" + label)
        action("conditional", label + " error check", GroupingIdentifier=group, WFControlFlowMode=0,
               WFCondition=100, WFInput={"Type": "Variable", "Variable": token(error)})
        action("showresult", label + " show error", Text=text(f"Sync failed ({what}): ", error))
        action("exit", label + " stop on error")
        action("conditional", label + " end error check", GroupingIdentifier=group, WFControlFlowMode=2)

    action("comment", "About", WFCommentActionText=(
        "Reads weight and body fat (last week), plus daily steps and resting heart rate "
        "(last 8 calendar days, or 31 when the server asks for catch-up), and sends them to "
        "your Macro Engine over Tailscale. Run on iPhone while unlocked. Repeat runs are safe. "
        "No Health records are changed. Workouts can't be read by Shortcuts; add them in the app."))
    auth = action("gettext", "Sync authorization", WFTextActionText="Bearer PASTE_YOUR_SYNC_TOKEN")
    headers = dictionary([("Authorization", auth, 0)])

    # captured_at is recorded before any Health query; the server rejects older
    # captures that arrive late so they can't replace newer snapshots.
    now = action("date", "Captured date", WFDateActionMode="Current Date")
    captured = action("format.date", "Captured at", WFDate=text(now),
                      WFDateFormatStyle="ISO 8601", WFISO8601IncludeTime=True)
    status = action("downloadurl", "Sync status",
                    WFURL=origin + "/api/import/apple-health/activity/status", WFHTTPMethod="GET",
                    WFHTTPHeaders=headers)
    fail_if_error(status, "Sync status", "checking the token")
    mode = action("getvalueforkey", "Sync mode", WFDictionaryKey="mode", WFInput=token(status))

    for label, metric in [("Weight", "weight"), ("Body Fat Percentage", "body_fat_percentage")]:
        action("comment", label + " section", WFCommentActionText=(
            f"Send {label.lower()} readings from the last week. "
            "Each reading keeps its measurement date and its unit. "
            "If several sources record this metric, select RENPHO in the Find action."))
        samples = action("filter.health.quantity", label + " samples", WFContentItemLimitEnabled=False,
                         WFContentItemFilter=health_filter(label, 1, 8192))
        group = str(uuid5(NAMESPACE_URL, "macro-engine-health/loop/" + metric)).upper()
        action("repeat.each", label + " loop", GroupingIdentifier=group,
               WFControlFlowMode=0, WFInput=token(samples))
        item = token(variable("Repeat Item"))
        value = action("properties.health.quantity", label + " value", WFContentItemPropertyName="Value", WFInput=item)
        unit = action("properties.health.quantity", label + " unit", WFContentItemPropertyName="Unit", WFInput=item)
        date = action("properties.health.quantity", label + " date", WFContentItemPropertyName="Start Date", WFInput=item)
        formatted = action("format.date", label + " formatted date", WFDate=text(date),
                           WFDateFormatStyle="ISO 8601", WFISO8601IncludeTime=True)
        response = action("downloadurl", label + " response",
            WFURL=origin + "/api/import/apple-health", WFHTTPMethod="POST", WFHTTPBodyType="JSON",
            WFHTTPHeaders=headers,
            WFJSONValues=dictionary([("metric", metric, 0), ("value", value, 3),
                                     ("unit", unit, 0), ("date", formatted, 0)]))
        fail_if_error(response, label, label.lower(),
                      str(uuid5(NAMESPACE_URL, "macro-engine-health/error/" + metric)).upper())
        action("repeat.each", label + " end loop", GroupingIdentifier=group, WFControlFlowMode=2)

    # Activity: raw samples, one line each, uploaded in one request. The server
    # groups them into local calendar days and keeps one selected source.
    categories = [("Steps", "Steps", "Step")]
    if include_rhr:
        categories.append(("Resting Heart Rate", "Resting Heart Rate", "Resting HR"))
    action("comment", "Activity section", WFCommentActionText=(
        "Steps and resting heart rate from every source are sent with their source name. "
        "Macro Engine counts only the source you select in Progress (your Apple Watch), so "
        "iPhone and Watch steps are never added together. Normal runs read 8 days; when "
        "the server reports a gap it asks for 31 days so missed days catch up."))
    branch = uid("activity-window")
    action("conditional", "Catch-up check", GroupingIdentifier=branch, WFControlFlowMode=0,
           WFCondition=4, WFConditionalActionString="catch_up",
           WFInput={"Type": "Variable", "Variable": token(mode)})
    for window in ("catch_up", "normal"):
        if window == "normal":
            action("conditional", "Normal window", GroupingIdentifier=branch, WFControlFlowMode=1)
        for label, health_type, short in categories:
            found = action("filter.health.quantity", f"{label} samples ({window})",
                           WFContentItemLimitEnabled=False,
                           WFContentItemFilter=health_filter(health_type, WINDOW_DAYS[window], 16))
            action("setvariable", f"{label} samples variable ({window})",
                   WFVariableName=f"{short} samples", WFInput=token(found))
    action("conditional", "End window", GroupingIdentifier=branch, WFControlFlowMode=2)

    combined = {}
    for label, _, short in categories:
        action("nothing", f"{label} lines reset")
        action("setvariable", f"{label} lines start", WFVariableName=f"{short} lines")
        group = uid("activity-loop/" + label)
        action("repeat.each", f"{label} activity loop", GroupingIdentifier=group, WFControlFlowMode=0,
               WFInput=token(variable(f"{short} samples")))
        item = token(variable("Repeat Item"))
        value = action("properties.health.quantity", f"{label} sample value",
                       WFContentItemPropertyName="Value", WFInput=item)
        start = action("properties.health.quantity", f"{label} sample start",
                       WFContentItemPropertyName="Start Date", WFInput=item)
        source = action("properties.health.quantity", f"{label} sample source",
                        WFContentItemPropertyName="Source", WFInput=item)
        start_iso = action("format.date", f"{label} sample start ISO", WFDate=text(start),
                           WFDateFormatStyle="ISO 8601", WFISO8601IncludeTime=True)
        line = action("gettext", f"{label} sample line",
                      WFTextActionText=text(value, "\t", start_iso, "\t", source))
        action("appendvariable", f"{label} sample append", WFVariableName=f"{short} lines", WFInput=token(line))
        action("repeat.each", f"{label} activity end loop", GroupingIdentifier=group, WFControlFlowMode=2)
        combined[label] = action("text.combine", f"{label} upload text",
                                 text=token(variable(f"{short} lines")), WFTextSeparator="New Lines")

    fields = [("schema_version", "1", 3), ("captured_at", captured, 0), ("window", mode, 0),
              ("steps_status", "queried", 0), ("steps_samples", combined["Steps"], 0)]
    if include_rhr:
        fields += [("resting_heart_rate_status", "queried", 0),
                   ("resting_heart_rate_samples", combined["Resting Heart Rate"], 0)]
    else:
        fields += [("resting_heart_rate_status", "unsupported", 0)]
    fields += [("workouts_status", "unsupported", 0)]
    upload = action("downloadurl", "Activity response",
                    WFURL=origin + "/api/import/apple-health/activity", WFHTTPMethod="POST",
                    WFHTTPBodyType="JSON", WFHTTPHeaders=headers, WFJSONValues=dictionary(fields))
    fail_if_error(upload, "Activity", "activity")
    notice = action("getvalueforkey", "Sync notice", WFDictionaryKey="notice", WFInput=token(upload))
    action("notification", "Finished", WFNotificationActionTitle=text("Macro Engine"),
           WFNotificationActionBody=text(notice))

    header_index = next(i for i, a in enumerate(actions)
                        if a["WFWorkflowActionParameters"]["UUID"] == auth["OutputUUID"])
    return {
        "WFWorkflowName": "Sync Macro Engine",
        "WFWorkflowClientVersion": "3036.0.4.2",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {"WFWorkflowIconStartColor": 4282601983, "WFWorkflowIconGlyphNumber": 59446},
        "WFWorkflowTypes": [], "WFWorkflowInputContentItemClasses": [],
        "WFWorkflowActions": actions,
        "WFWorkflowImportQuestions": [{"ActionIndex": header_index, "Category": "Parameter",
            "ParameterKey": "WFTextActionText", "DefaultValue": "Bearer ",
            "Text": "Paste your Macro Engine Authorization header, including Bearer and the space. "
                    "This is saved in the Shortcut for automatic uploads."}],
    }


def serialize(workflow):
    return plistlib.dumps(workflow, fmt=plistlib.FMT_BINARY, sort_keys=False)


def write_all(origin, directory):
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, options in VARIANTS.items():
        path = directory / name
        path.write_bytes(serialize(build(origin, **options)))
        paths.append(path)
    return paths


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="writes every variant (with and without resting HR) here")
    args = parser.parse_args()
    for path in write_all(args.origin, args.output_dir):
        count = len(plistlib.loads(path.read_bytes())["WFWorkflowActions"])
        print(f"Wrote {count} actions to {path} (unsigned; no credentials)")
