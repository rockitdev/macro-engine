# Apple Health shortcut sync implementation spec

Prepared for Claude Opus on November 9, 2026. Implement in `/home/moore/projects/macro-engine`.

Keep and extend **Sync Macro Engine**, the existing Apple Shortcut. Reliably import the fitness data needed for the app's current check-ins: weight, body fat, daily steps, workouts, and optional resting heart rate. Extend backend storage, web Progress, and MCP check-ins to present that data. Deliver a generated shortcut and ready-to-install Mac bundle so Ryan does not have to build or repair actions on his phone.

This is the selected implementation. It requires no custom native app, paid exporter, Apple Developer membership, App Store release, or recurring app re-signing. It supersedes the native-app proposal. Keep this filename so the existing Claude handoff link remains valid.

## Scope and user decisions

| Data | Capture and use |
| --- | --- |
| Weight | RENPHO samples already in Health; measurement date/unit; latest per local day. Preserve manual overrides. |
| Body fat | RENPHO percentage in percentage points, with sample date; preserve existing behavior. |
| Steps | Daily total with a verified source/aggregation policy, coverage, and today's partial total. |
| Workouts | Type, start/end, duration and its basis, source, and stable identity where exposed. Summarize strength/cardio/other. |
| Resting heart rate | Dedicated resting estimate in bpm with sample date/source, when available. Missing RHR must not block other metrics. |
| Waist | Existing manual measurement; no new automatic collection. |
| Food and subjective check-ins | Existing logging and check-in inputs; do not infer intake, hunger, stress, or strength performance from Watch data. |
| Sleep | Excluded. Ryan does not wear the Watch overnight. No sleep permissions or missing-sleep warnings. |

“All fitness data the app needs” means the measurements above plus the dates, units, sources, and coverage needed to interpret them. It does not mean exporting the whole Health database. Exclude raw pulse streams, HRV, blood oxygen, GPS/routes, active/resting energy, inferred muscle mass, and unrelated medical records. Add other metrics only when there is a concrete app use and an explicit scope change.

Activity is supporting context. Do not automatically change macro targets, calorie allowances, activity multipliers, maintenance equations, phases, or plateau decisions based on imports. Do not add exercise calories to the food budget or interpret RHR as a diagnosis/readiness score.

The profile records seated desk work and strength training for 45 minutes, six days weekly. Treat that as a planned routine; an unrecorded workout is not proof it was skipped. Cardio was discussed as a recommendation, not confirmed as an existing habit.

## Environment and existing work

Read `CLAUDE.md`. Keep the Python backend stdlib-only except its existing MCP dependency. Preserve unrelated uncommitted work, the user's screenshot, runtime databases, and private credential files.

| Area | Existing files and behavior |
| --- | --- |
| Import | `macro_engine/health_import.py`: flat and Health Auto Export body-metric payloads; newest sample per day; manual-edit protection. |
| HTTP | `macro_engine/http_api.py`: Bearer-protected `POST /api/import/apple-health`, using `MACRO_ENGINE_HEALTH_TOKEN`. |
| Storage | `macro_engine/db.py`: `body_metrics`, `health_import_metrics`, `settings`, saved `checkins`. |
| Coaching | `macro_engine/coach.py`: `progress()`, `check_in()`; exposed by `mcp_server.py`. |
| Web | `web/src/components/progress-view.tsx`, `web/src/types.ts`, `web/src/api.ts`. |
| Generator | `scripts/build_health_shortcut.py`: working body-metric loops and authorization import question. |
| Delivery | `web/public/shortcuts/Sync Macro Engine.unsigned.shortcut`, public downloader, `scripts/install-health-shortcut-mac.command`. |
| Tests | `tests/test_health_import.py`, `tests/test_health_shortcut.py`, HTTP, coach, and MCP suites. |

The iPhone currently reaches `https://moore.tailc24dca.ts.net:4325` over Tailscale. Preserve that route. **The Mac keeps Cloudflare WARP and must not require Tailscale or private-backend access.** Transfer the bundle through the existing file-transfer workflow. The local Mac installer signs bundled files with Apple and opens them for import; Health actions run on the iPhone after iCloud Shortcuts sync.

LAN-only syncing was discussed but not selected in this revision. Keep the generator's origin configurable for future use; do not silently switch to a LAN IP, cleartext credentials, public exposure, or different VPN configuration.

The private, gitignored `macro-engine-shortcut-mac.zip` contains a `Macro Engine Shortcut` folder with the unsigned shortcut, installer, `START HERE.txt`, and `Sync header.txt`. Preserve this installation path. Keep the actual header out of committed files, public assets, unsigned artifacts submitted to Apple, logs, fixtures, and screenshots.

## Verify export capabilities first

Produce a capability inventory for each requested field: actual Shortcuts action/property, observed serialized representation, tested OS version, and limitations. Use real exports or checked references. Do not assume native HealthKit APIs, statistics queries, sample UUIDs, workout properties, Keychain storage, persistent background execution, or catchable errors exist in Shortcuts.

Verify workout retrieval early. Finding existing workouts is different from **Log Workout**, which writes new data. Confirm which types, durations, IDs, and source properties can be read. No action may write or delete Apple Health data.

Use these explicit fallback rules:

- **Steps:** use a reconciled daily total only if a supported action is verified to return it. Otherwise select one authoritative Watch source and label it **Watch-recorded steps**. It can miss activity when the Watch is off. Never sum overlapping phone/Watch samples and call them Health's merged total.
- **Workout identity:** prefer a real stable ID if exposed. Otherwise derive an identity on the server from selected source, canonical start instant, and original type. Mark `identity_basis: derived`, reject collisions, and exclude upload time and mutable duration from the key.
- **Duration:** prefer recorded workout duration. If only start/end are accessible, permit `duration_basis: elapsed` with an honest label; never call it pause-adjusted duration.
- **Workout retrieval unavailable:** retain supported collection, disclose the limitation, and provide a minimal manual workout form in the app if needed. Manual entries need stable IDs, source `manual`, and edit/delete support. Do not claim automatic workout integration is complete. Exercise Minutes, if available, may be separately labeled context, but cannot substitute for session type/count/duration.
- **Optional RHR unavailable:** skip it without disabling supported metrics. If an optional action can throw an uncatchable error, provide a verified configuration or generated variant omitting that action.

No manual loop construction, JSON edits, plist repairs, or paid dependencies for Ryan. Do not declare a generated shortcut functional solely because structural tests pass. If device checks are unavailable, finish independent work and identify exactly what remains unverified.

## Dates and collection

Each normal run refreshes today plus the preceding seven local calendar days. Use calendar boundaries, not eight fixed 24-hour periods. Refresh previous days for late Watch data and revised estimates. Query bounded days, disable accidental result limits, and never silently truncate sample lists.

Send local date, window start/end with UTC offsets, and `captured_at` recorded before each query. Send an IANA timezone if exposed reliably; otherwise use a documented configured timezone, initially `America/Halifax`, and disclose its travel limitation. Do not derive an IANA zone from the current offset. Account for daylight-saving boundaries and the server having a different timezone.

Store capture time separately from server receipt time. Older captures cannot replace newer observations. Same capture timestamp plus different contents is a conflict. Today is **so far**. Closed calendar days do not prove full-day Watch wear. Empty/unreadable Health results are unknown, not zero or confirmed permission denial.

## Measurement rules

### Weight and body fat

Keep the original endpoint, date validation, unit conversions, latest-per-day selection, and manual-edit/deletion protection. Prefer RENPHO when sources conflict. Verify actual body-fat units and send 27.1 for 27.1%, not 0.271. Missing days must not create measurements.

### Steps

Upload replacement totals, never increments: replaying 6,000 leaves 6,000; newer 7,000 replaces it; corrected newer 5,800 also replaces it. Observed zero and missing data are distinct.

Store aggregation policy and source with each snapshot. For one-source raw samples, deduplicate exact repeated records and document assignment of samples spanning midnight; do not count the whole sample twice or invent its interval distribution. Compare against that source in Health, not the combined total. A source may identify an app rather than an individual Watch; verify filtering with the user's devices. Changes in source policy must be visible and must not merge incompatible totals.

### Workouts

Store individual records with stable identity, original type, source, timestamps, and duration/basis. Normalize duration to seconds. Retain a native type code only if exposed; otherwise preserve the exported label and map verified labels to `strength`, `cardio`, or `other`. Unknown types remain other. Categories do not establish exercise intensity.

Use one authoritative source or a verified cross-source deduplication strategy. Retries update the same record; distinct legitimate sessions cannot merge merely because they share a date/type. Assign a cross-midnight workout wholly to its local start date and count it once. Weekly duration sums sessions; it is not unique elapsed time when distinct sessions overlap.

Health-side workout deletion reconciliation is deferred unless a safe supported mechanism is demonstrated. Absence from a query is neither a tombstone nor proof of a rest day. Disclose that deleted Health workouts may remain imported. Protect manual entries from silent import overwrite.

### Resting heart rate

Use the dedicated resting metric, not the minimum or average of ordinary pulse readings. Select the latest available estimate per local day from the chosen source and retain sample time/source. A newer capture can replace an estimate even when its measurement timestamp is unchanged or a previous estimate was removed. Apple can revise current/previous-day estimates. Missing RHR must not block other categories.

## API and storage contract

Keep the old `POST /api/import/apple-health` request/response contracts working. Add Bearer-protected `POST /api/import/apple-health/activity` with the same authentication, errors, and body-size limits. Reuse helpers without weakening validation.

Prefer one bounded activity request per day. Do not send one HTTP request for every step/pulse sample. Construct payloads using verified dictionary/list actions. If a day exceeds safe request limits, chunk it with individual acknowledgements and never mark all chunks received prematurely. Finalize the contract against actual generator capabilities.

Proposed example with synthetic data and the single-source fallback:

```json
{
  "schema_version": 1,
  "date": "2026-11-08",
  "timezone": "America/Halifax",
  "window_start": "2026-11-08T00:00:00-04:00",
  "window_end": "2026-11-09T00:00:00-04:00",
  "captured_at": "2026-11-09T18:00:00-04:00",
  "steps": {
    "status": "observed",
    "value": 6400,
    "source_policy": "single_watch",
    "source_id": "example-selected-source"
  },
  "resting_heart_rate": {
    "status": "observed",
    "bpm": 68,
    "sample_at": "2026-11-08T16:00:00-04:00",
    "source_id": "example-selected-source"
  },
  "workouts": {
    "status": "observed",
    "source_id": "example-workout-source",
    "items": [
      {
        "identity_basis": "derived",
        "type": "Traditional Strength Training",
        "start_at": "2026-11-08T12:00:00-04:00",
        "end_at": "2026-11-08T12:50:00-04:00",
        "duration_seconds": 2700,
        "duration_basis": "recorded"
      }
    ]
  }
}
```

Support an external ID when genuinely exposed; the example deliberately assumes no UUID access. Per metric, use strictly validated `observed`, `no_samples`, `unavailable`, and `unsupported` shapes. An omitted optional field means no update. Empty queries do not establish permission state or inactivity. Use unavailable for detectable failure and unsupported for a verified capability/configuration limitation. Preserve last good observations and their dates when queries fail or return nothing.

Validate the full request before an atomic commit. Reject malformed units/types, booleans as numbers, NaN/infinity, negative/fractional step counts, nonpositive durations, impossible timestamp ordering, unsupported versions, unknown fields, conflicts, and oversized collections. Quantity bounds are generous documented input validation, not medical classification. Unique constraints and transactions must handle overlapping runs.

Suggested additive tables are `health_activity_daily` and `health_workouts`, with metric-specific capture/source provenance and availability metadata. Keep last good values separate from last attempted/empty queries. Omitting a metric cannot advance its version. Activity imports must not mutate body records, food logs, targets, phases, profiles, or historical check-ins.

Return updated/unchanged/stale counts and per-metric availability. Keep measurement date, last observation saved, last query accepted, and last request received distinct. Weight arriving does not make activity fresh. A valid empty upload reports no observations. Body and activity requests are separate transactions; expose partial success.

## Recovery and catch-up

Use repeatable Health queries and idempotent server upserts as the primary recovery mechanism. Do not copy native-app queue, Keychain, observer, or background retry requirements into Shortcuts without evidence. A failed network action may abort execution; the next run must safely replay the affected range. Earlier successful requests stay committed.

Implement durable per-category/day acknowledgements, preferably server-side behind an authenticated sync-status endpoint. Distinguish accepted query coverage from observed data coverage. Only confirmed accepted requests advance progress; incomplete chunks/categories stay pending. Unavailable days remain eligible for retry; unsupported categories must not trap catch-up indefinitely. An empty query can be acknowledged while data coverage remains unknown. A current-day acknowledgement must not prevent further refreshes that day.

Always refresh the normal eight-day window. When acknowledgement history shows a longer gap, support bounded catch-up up to 30 calendar days in small requests/runs fitting measured execution limits. Preserve acknowledged progress and refresh overlaps. A longer outage/backlog must show its date range and a clear resume/backfill option rather than silently dropping it. Lifetime Health export is out of scope.

Check API responses explicitly. Stop on bad credentials or invalid payloads with a concise remedy and failing category/day. Retry transient failures only a bounded number of times if the actual Shortcuts version supports catching them; otherwise defer to the next run. A timeout after server commit is safe to replay. An uncatchable system error must prevent the final success notice. A health-endpoint response alone does not authenticate the token or prove sync success.

Empty optional lists must not stop supported categories. If genuine query exceptions cannot be caught, generate a verified optional-section configuration/helper variant instead of promising continuation through it or making Ryan assemble it. Per-category server status must make partial runs inspectable even after an abrupt client stop.

Use **Updated**, **No new readings**, **Partial sync**, and **Sync failed** based on acknowledgements. Never announce unconditional success after a skip/error. Avoid detailed health readings and tokens in notifications. Simultaneous manual/automation runs must be safe through server idempotency without a fragile local lock.

## Progress and check-ins

Add a shared read-only activity summary to `coach.progress()` and newly generated `check_in()` results so HTTP/MCP agree. Preserve existing response fields and add `activity` plus per-category sync status. Include context before early returns when weigh-in history is insufficient. Do not backfill historical check-ins.

Add a compact **Activity** section with:

- Today's steps **so far**, source label, and observation time.
- Weekly average over observed closed days, with denominator/missing-day coverage. Exclude today from closed-day averages.
- Strength/cardio/other session counts and minutes, duration basis, and a small workout list. Say “No workouts received” rather than declaring a rest day.
- Average available daily RHR, observed-day count, source, and freshness; never zero-fill missing readings.
- Per-category last query/data dates, partial sync, and backlog state. Old retained readings stay visibly dated.

Align weeks with the existing week-start preference. Keep manual waist and subjective inputs. No sleep cards or automated macro/medical conclusions. Use descriptive context such as “Steps recorded on 5 of 7 days.” If the manual workout fallback is necessary, clearly label its provenance.

The existing `check_in(apply=False)` can still save a check-in: use a database copy when previewing during validation, not the live account.

## Generator and installation

Extend `scripts/build_health_shortcut.py` and keep **Sync Macro Engine** as the entry point. Generate every action and any necessary verified helpers, minimizing setup and installed helpers. Preserve the full Bearer-header import question. Configure source choices once where possible; no daily source picker or code edits. No Health writes.

Preserve fixes from actual installation failures:

- Numeric scalar dictionary fields use `WFItemType = 3`; type 1 expects a dictionary and previously crashed signing. Verify nested dictionary/array serialization for the new payload.
- Dates use `WFDateFormatStyle = "ISO 8601"` and `WFISO8601IncludeTime = True`. Previous custom keys emitted dates without time/offset. Verify actual date output.
- Magic-variable references point to prior valid actions and control-flow groups balance. Avoid brittle numeric action indexes. Keep the import question pointed at the authorization action.
- Generated artifacts match the builder deterministically. Unsigned/public/signing input contains no credentials.

Regenerate the offline Mac ZIP reproducibly. Include the unsigned shortcut, local installer, short `START HERE.txt`, and the existing `Sync header.txt` only in the private bundle. Also support a credential-free distributable. Keep the private ZIP gitignored and access-restricted. Do not rotate the live token merely to rebuild.

Instructions: unzip, run the local installer, import/replace, paste the header, wait for iPhone sync, allow added Health/network categories, run once, inspect Progress. The Mac installer reads bundled files and contacts Apple for signing; **no Tailscale/private-server downloads or WARP changes on the Mac**. No paid developer account or native-app signing workflow.

Retain RENPHO-close automation. Recommend one additional evening scheduled automation so activity uploads independently of weighing in; the next run refreshes late data from yesterday. Offer a workout-end trigger only if supported and useful on the installed OS. Personal automation setup may require the user to choose a trigger and the existing shortcut; generate everything else. Do not promise immediate sync on VPN reconnect, permanent background execution, or locked-device Health access. Manual unlocked execution remains the recovery path.

## Acceptance tests

1. Existing body imports retain unit/date behavior, manual overrides/deletions, retry safety, and rollback.
2. Steps handle replays, increases/decreases, stale/conflicting captures, mixed-source fixtures, source changes, observed zero, and unknown days correctly.
3. Workouts deduplicate retries, accept corrections, reject derived-ID collisions, preserve distinct/cross-midnight sessions, and label unknown types/duration basis correctly.
4. Missing/unsupported RHR and workout capabilities never fabricate values. Test optional variants and partial failures with good values retained and dated.
5. Date tests cover midnight, offsets, 23/25-hour DST days, today's partial values, timezone configuration, normal refresh, and catch-up windows.
6. Test authentication, limits, strict schemas, atomic writes, concurrent runs, and a lost response after server commit followed by replay.
7. Acknowledgements resume incomplete categories/chunks without skipping gaps; backlog stays discoverable. Empty-query acknowledgement remains distinct from observed coverage.
8. HTTP/MCP/web summaries agree with denominators, including early check-in returns. Activity does not alter targets, maintenance formulas, phases, manual body values, or old check-ins.
9. Generator tests cover references, control flow, nested values, valid ISO timestamps, query limits, import-question placement, allowed destinations, read-only actions, capability variants, and artifact equality.
10. Packaging tests verify offline installation needs no private-server download and public/signing artifacts contain no credentials. Do not expose real secrets in tests.

Run `.venv/bin/python -m unittest discover tests -v`, web build/lint, and `git diff --check`. The preceding implementation passed 305 Python tests and the web build; lint had 11 warnings and no errors. Reassess the current checkout rather than assuming those counts are permanent.

Container web checks: `docker compose -f docker-compose.dev.yml exec -T web npm run build` and the equivalent `npm run lint`. Backend changes require API restart. Back up the actual runtime SQLite DB before deployment; Docker uses a named volume and the host's default DB may not be live. Test with temporary databases; never upload synthetic health readings into the real account.

## Device verification and deliverables

Deliver backend/web/MCP changes, tests, generator/artifacts, offline bundle, capability inventory, and short README/setup instructions. No native app or paid dependency. Clearly separate automated checks from pending Apple-device checks.

Validate on actual devices:

1. Sign/import on the Mac with WARP enabled and no Tailscale/private-server access.
2. Run on the iPhone over Tailscale; compare weight/body fat, closed-day steps under the chosen source policy, workout type/duration, and available RHR against Health/Fitness.
3. Repeat and confirm no inflated totals/duplicate sessions. Re-run after new data and check freshness/partial-day updates. Verify manual override protection.
4. Test missing optional data/permissions, bad token, partial execution, and disconnected Tailscale. Reconnect and rerun; prior acknowledgements remain valid and missing work catches up. Document uncatchable errors without false success.
5. Verify replacement preserves the existing automation's target and test the added schedule. Disclose locked-device limits.

If Apple hardware is unavailable, finish independent code/tests and deliver the bundle with device checks pending. If workout retrieval is unsupported, deliver the disclosed manual fallback and identify automatic workout sync as incomplete. Do not conceal the gap or reintroduce paid/native dependencies.

## References

- [Apple Find and Filter actions](https://support.apple.com/guide/shortcuts/intro-to-find-and-filter-actions-apd3c845e881/ios): general Find Health Samples behavior; verify actual OS fields.
- [HTTP requests in Shortcuts](https://support.apple.com/guide/shortcuts/request-your-first-api-apd58d46713f/ios) and [automation triggers](https://support.apple.com/guide/shortcuts/setting-triggers-apde31e9638b/ios): upload and automation setup.
- [Apple Health source management](https://support.apple.com/en-au/108779): prioritization does not prove arbitrary raw Shortcut samples are already deduplicated.
- [Resting heart rate type](https://developer.apple.com/documentation/healthkit/hkquantitytypeidentifier/restingheartrate): estimate semantics/revisions, not proof a native API is exposed in Shortcuts.
- Repository `README.md`, generator, and health import/shortcut tests: working serialization, installation, and compatibility constraints.
