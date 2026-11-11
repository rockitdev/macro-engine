# Apple Health capability inventory (Sync Macro Engine shortcut)

Checked November 9, 2026, without Apple hardware. "Verified" below means a
real iOS export, a checked reference built from iOS exports/binaries, or this
project's own shortcut that already ran on Ryan's iPhone. Nothing here was
run on a device for this release. See *Pending device checks*.

## Sources

| Ref | What it is | Used for |
| --- | --- | --- |
| A | This repo's body-metric section, which already uploaded weight and body fat from Ryan's iPhone | `filter.health.quantity` + `properties.health.quantity` (Value, Unit, Start Date), ISO 8601 `format.date`, JSON `downloadurl`, error check |
| B | `viticci/shortcuts-playground-plugin` HEALTHKIT.md + `healthkit-ios26.2-reference.json` (anonymized iOS 26.2 exports, iPhoneOS 26.2 headers, ActionKit constants) | `Type is <label>` rows, observed `Steps` label, `Start Date is in the last N days` (Operator 1001, Unit 16 = days), Get Details property list |
| C | `danielhopkins/apple-tools` docs/apple-health.md (strings from the iOS 27 simulator ActionKit binary, the full Find Health Samples picker list) | `Resting Heart Rate` label; `WFHKSampleContentItem` wraps only quantity/category samples; no Workouts row |
| D | WorkflowKit `Localizable.loctable` (iOS 27.2/27.3, via `blacktop/ipsw-diffs`) | Workout actions are Log/Start/End only; "Apple Watch workout ended" automation trigger exists |
| E | `electrikmilk/cherri` compiler source | conditional codes (is = 4, has any value = 100), `setvariable`/`appendvariable`, `text.combine` (`WFTextSeparator = "New Lines"`), `date` (`WFDateActionMode = "Current Date"`) |

## Inventory

| Field | Shortcuts action / property | Serialized as | Evidence | Limitations |
| --- | --- | --- | --- | --- |
| Weight, body fat | Find Health Samples `Weight` / `Body Fat Percentage`; Get Details Value, Unit, Start Date | unchanged from the working shortcut | A | Body fat arrives as percentage points; validated 2–70. |
| Steps | Find Health Samples `Type is Steps`, `Start Date is in the last 8 (or 31) days`, no limit; Get Details `Value`, `Start Date`, `Source` per sample | one text line per sample: `value⇥ISO start⇥source` | A, B (label observed), C (Source detail) | Raw samples from every source. **No reconciled total is verified**, see below. Number text follows the phone locale: grouping commas are accepted, but a decimal comma is rejected. |
| Resting heart rate | Find Health Samples `Type is Resting Heart Rate`; same details | same line format | C (picker label read from the binary), B (SDK label) | Label not yet seen in a real export. If it fails on the phone, install the *without resting HR* variant. |
| Workouts | **None.** No built-in action reads workouts. | `workouts_status: "unsupported"` | C, D | Automatic workout import is **not available**. Fallback: manual entry in Progress → Activity (stable IDs, source `manual`, edit/delete). The API accepts workout items from other clients with derived identities. |
| Sample UUID | Not exposed (Get Details: Type, Value, Unit, Start Date, End Date, Duration, Source, Name) | – | B, C | Steps and RHR are keyed per day and source; workouts would use derived identity. |
| Timezone (IANA) | Not exposed | – | – | Server uses the configured zone (default `America/Halifax`). It warns when `captured_at`'s offset disagrees. While travelling, days near midnight can be misdated. |
| Errors | Uncatchable in Shortcuts | – | – | A Health or network failure stops the run before the final notice. Server responses with `error` are shown and stop the run. |

### Steps: why "Watch-recorded steps"

Find Health Samples can **Group by Day**, and ActionKit's help text says grouping returns
Health's daily totals (C). That has not been compared against the Health app on
a day with both iPhone and Watch samples. Per the spec's fallback, the shortcut
sends raw samples with their source. The server keeps per-source daily totals
and summarizes **only the selected source**. It auto-selects when exactly one
source name contains "Watch"; otherwise Ryan picks once in Progress. iPhone
and Watch samples are never added together. The cost is that days when the
Watch isn't worn read low or show "No data received".

Sample assignment: each sample counts once, on the **local date of its start**.
A sample crossing midnight counts entirely on its start day and is never split.
Exact repeated lines (same value, start and source) are counted once.

### Window semantics

`Start Date is in the last N days` covers the oldest day of an `N`-day
calendar window whether iOS treats it as rolling 24-hour periods or calendar
days. The server ignores samples before the window's first local midnight and
acknowledges only the `N` calendar days ending on `captured_at`'s local date.

## Contract and recovery (server)

- `GET /api/import/apple-health/activity/status` (Bearer) returns `mode`:
  `normal` (8 days) or `catch_up` (31 days). Catch-up is used when an
  accepted-query gap exists after the first acknowledged day and within 30
  days. Older gaps are reported as `backlog_beyond_limit`.
- `POST /api/import/apple-health/activity` (Bearer, 4 MB, ≤ 20,000 lines per
  category). The whole request is validated, then committed atomically. Rules:
  - Snapshots replace earlier ones and are never incremented.
  - An older `captured_at` is reported as stale.
  - The same `captured_at` with different contents returns 409.
  - Coverage is recorded per category and day. `unavailable` attempts are not
    acknowledgements, and `unsupported` never blocks catch-up.
- The response carries `notice`: `Updated`, `No new readings` or
  `Partial sync: …`. The shortcut shows only that notice, never readings.

## Pending device checks (not done)

1. Sign/import on the Mac with WARP on; choose **Replace**; RENPHO automation still targets it.
2. iPhone run over Tailscale. Confirm in the editor that the types show
   **Steps** and **Resting Heart Rate**, not empty pickers. Confirm the Health
   permission prompt lists them, and that Source lines contain the Watch's name.
3. Compare one closed day: Macro Engine "Watch-recorded steps" against Health → Steps → Show All
   Data filtered to the Watch, then RHR against Health.
4. Run time with about 8 days of iPhone and Watch step samples (one loop per
   sample), and whether a 31-day catch-up run finishes. If too slow, reduce
   catch-up days.
5. Second run: no inflated totals. Bad token, then Tailscale off, then rerun:
   no false success, and data catches up.
6. Optional: test whether `Group by Day` totals match Health. If they do, a
   reconciled-total policy could replace the single-source fallback later.
