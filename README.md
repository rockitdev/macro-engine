# Macro Engine

Macro tracking you talk to instead of tap at. A local food database built from
open data (USDA FoodData Central), exposed as an MCP server so any Claude
surface (a local Claude Code session, or any agent that speaks MCP) can log
meals in plain language and answer the only question that matters:
**what's left today**.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m macro_engine.etl_fdc          # download + load USDA data (~15k foods)
.venv/bin/python -m unittest discover tests -v    # run tests
```

Data lands in `~/.local/share/macro-engine/` (`macros.db` + cached `raw/` zips).
Override the DB path with `MACRO_ENGINE_DB`.

## MCP registration

```bash
claude mcp add macro-engine -- \
  /path/to/macro-engine/.venv/bin/python \
  /path/to/macro-engine/mcp_server.py
```

Or in a project `.mcp.json`:

```json
{
  "mcpServers": {
    "macro-engine": {
      "command": "/path/to/macro-engine/.venv/bin/python",
      "args": ["/path/to/macro-engine/mcp_server.py"]
    }
  }
}
```

Tools: `log_meal`, `remaining`, `day_summary`, `week_summary`, `month_summary`,
`search_food`, `set_targets`, `set_week_start`, `add_food`, `add_alias`,
`delete_log_entry`, `fit_meal`, plus the coaching tools below.

## Coaching (Fat Loss Forever)

Food logging tells you what you ate. The coaching layer tells you what to do
next. Its rules come from *Fat Loss Forever* (Norton & Baker), and the code
comments cite page numbers.

| Tool | What it does |
|---|---|
| `set_profile` | Sex, full birth date, height, desk/activity context, strength-training schedule, long-term loss goal, and carb/fat preference |
| `log_weight` | Daily morning weigh-in, plus optional body fat %, waist, and hips (kg/lb, cm/in) |
| `plan_targets` | Maintenance, then deficit from rate (% bodyweight/wk), then protein from lean mass and age, then the carb/fat split, then fiber. Preview first, then `apply` |
| `check_in` | Weekly decision: hold, adjust, fix adherence, diet break, or reverse |
| `progress` | Weekly-average trend, measured vs predicted maintenance, 14-day adherence |
| `start_phase` | cut / maintenance / reverse / diet_break |
| `plan_high_days` | Refeed or high-day distribution of the same weekly calories |

How the engine decides:

- **Weekly averages, not daily weights.** A week counts only with 3 or more
  weigh-ins. Daily swings of 1–2% are treated as noise.
- **Maintenance is measured.** After about 2 weeks it is calculated from
  logged intake and the weight trend, using tissue energy of 7830 kcal/kg fat
  and 1200 kcal/kg lean. Until then it is predicted with the Müller equation,
  or revised Harris–Benedict when there is no body-fat reading. The gap
  between measured and predicted maintenance shows metabolic adaptation.
- **Deficit** = bodyweight × rate × energy factor (Table 2). The rate is
  0.4–0.8% per week, with a hard maximum of 1%.
- **Plateau** = 2 consecutive weeks under 50% of the goal loss. Before it cuts
  anything, the engine rules out poor logging and confounders (period, stress,
  sleep, illness). It then cuts **only carbs and fat**: 5% plus 5% more
  activity, or 10% food-only, escalating to 15%. After a cut works, the next
  one is 2.5% smaller. If 15% fails, it recommends a reverse diet.
- **Reverse diet**: carbs and fat go up 3, 6, or 10% each week (conservative,
  moderate, aggressive) unless the weekly average rose more than 0.2–0.5%, or
  rose three weeks in a row.
- **Diet breaks**: planned in advance (e.g. 2 weeks on, 1 week off), with at
  most 4 diet weeks between breaks. A break runs at maintenance with protein at
  the non-deficit level.
- **Gate**: no new diet when measured maintenance is 75% of predicted or less.

The web app has a **Progress** tab (`#progress`) with the trend chart,
weigh-in form, check-in, and target setup.

The **Profile and goals** card saves your profile independently of macro
targets. Birth dates give an exact age on the day being evaluated; older
birth-year profiles still work. The strength schedule records days/week and
minutes/session, and daily activity describes life outside workouts. Overall
activity already includes exercise, so recording the schedule does not add
exercise calories a second time. The optional long-term loss goal is an
approximate aim, separate from the weekly loss rate and calorie targets.

## Apple Health weigh-in sync

Once RENPHO readings appear in Apple Health, use Apple's built-in Shortcuts
(no paid exporter required), or Health Auto Export, to send readings. The phone must
be able to reach the app (keep Tailscale connected when using its private URL).

Set `MACRO_ENGINE_HEALTH_TOKEN` to a random secret in the API process environment.
For Docker Compose, put it in the git-ignored `.env` file as
`MACRO_ENGINE_HEALTH_TOKEN=your-secret`, restrict that file to your user, then
recreate the API with `docker compose -f docker-compose.dev.yml up -d --no-deps api`.
Generate a secret with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`.
Imports are disabled until the token is set. Use HTTPS or a private encrypted
connection. This token protects the importer; the rest of this local app still
relies on your private network and must not be exposed publicly.

### Free option: Apple Shortcuts

**Prebuilt Shortcut:** `web/public/shortcuts/Sync Macro Engine.unsigned.shortcut`
uploads weight and body fat (as before), plus Apple Watch **steps** and
**resting heart rate** for activity context. No token is embedded; the import
question asks for the Authorization header. A variant,
`Sync Macro Engine (without resting HR).unsigned.shortcut`, omits resting HR
in case that Health type fails on the phone. Both come from
`scripts/build_health_shortcut.py`; see
[docs/apple-health-capabilities.md](docs/apple-health-capabilities.md) for
what Shortcuts can and cannot read.

**Offline Mac bundle (Mac keeps Cloudflare WARP, no Tailscale):**

```bash
python3 scripts/package_health_shortcut.py           # private ZIP, reuses the existing Sync header.txt
python3 scripts/package_health_shortcut.py --public  # credential-free ZIP
```

Both ZIPs are git-ignored and written with mode 0600. Transfer the private ZIP
through your usual file-transfer route, unzip it, and follow `START HERE.txt`:

1. Run `/bin/bash install-health-shortcut-mac.command`. Add
   `--without-resting-hr` for the variant. It reads only the bundled files and
   asks Apple to sign them. It never contacts Macro Engine or Tailscale.
2. Choose **Replace** for the existing Sync Macro Engine, so the RENPHO
   automation keeps its target. Paste the line from `Sync header.txt`.
3. After iCloud sync, run the shortcut on the iPhone (unlocked, Tailscale on).
   Allow Steps and Resting Heart Rate.
4. Check Progress → Activity. Choose your Apple Watch as the source if asked.

Recommended automations on the iPhone: keep **App → RENPHO → Is Closed**, and
add **Time of Day → 9:00 PM daily → Run Immediately → Sync Macro Engine**.
Activity then uploads even on days without a weigh-in, and each run refreshes
yesterday's late Watch data. Re-runs are always safe. Health can't be read
while the phone is locked. If an automation fails, open the shortcut and run it
manually; the next run catches up, reaching back up to 30 days. A Watch
"workout ended" trigger is not needed, because workouts can't be imported.

On a Mac that *can* reach Tailscale, the downloader still works:

```bash
macro_installer="$(mktemp)" && curl -fsS 'https://moore.tailc24dca.ts.net:4325/shortcuts/install-mac.command' -o "$macro_installer" && /bin/bash "$macro_installer"
```

Automated checks cover structure, references, and payload compatibility only.
Signing, import, Health permissions and run time need an Apple device; the
pending checks are listed in the capability doc. For another server, run
`python3 scripts/build_health_shortcut.py --origin https://YOUR_HOST --output-dir web/public/shortcuts`.

### Activity (steps, resting heart rate, workouts)

`POST /api/import/apple-health/activity` and
`GET /api/import/apple-health/activity/status` use the same Bearer token.

- **Steps** are labelled *Watch-recorded steps*. The shortcut sends raw samples
  from every source. The server keeps per-source daily totals, and summaries use
  only the one source you select (never iPhone + Watch added together).
  Today is shown as "so far" and left out of averages. A day with no samples is
  unknown, not zero.
- **Resting heart rate** uses Health's dedicated resting estimate, latest per
  day. Newer uploads replace revised estimates.
- **Workouts** can't be read by Apple Shortcuts. Add them in Progress →
  Activity; manual entries can be edited or deleted. Unrecorded sessions are
  not treated as skipped.
- Days are grouped in the configured timezone (`America/Halifax`), changeable
  via `POST /api/activity/settings {"timezone": ...}`.
- Activity is shown in Progress and in new check-ins (`activity`, via HTTP and
  MCP). It never changes targets, calorie budgets, maintenance, or phases.

**Manual construction (optional):**

Create a shortcut named **Sync Macro Engine**. Keep Tailscale connected and
run it manually once to grant Health and network permissions.

1. Add **Find Health Samples**: Type is Weight, Start Date is in the last
   7 days, Unit is kg. Filter to RENPHO as the source if available. Leave
   grouping off and do not limit to one result, so missed dates catch up.
2. Add **Repeat with Each** over the found samples. Inside the repeat, use
   **Get Details of Health Sample** on the Repeat Item to get its Value and
   Start Date. Format that sample date with **Format Date**, ISO 8601,
   including time and timezone. Do not use today's date for old samples.
3. Inside the repeat add **Get Contents of URL**, using your app's origin
   followed by `/api/import/apple-health`. Expand its options, choose POST,
   and add header `Authorization: Bearer your-secret`. Set Request Body to
   JSON with these fields (insert the value/date variables, not their names):

   | Key | Type | Value |
   |---|---|---|
   | `metric` | Text | `weight` |
   | `value` | Number | Sample Value variable |
   | `unit` | Text | `kg` |
   | `date` | Text | Formatted Date variable |

4. After End Repeat, repeat these actions for Body Fat Percentage. Use
   `body_fat_percentage` for metric and `%` for unit. Send percentage points
   (e.g. 22.5). Each metric keeps its own sample date. Empty searches simply
   run no iterations.
5. Run the shortcut and check Progress. Then create a personal automation
   for **App → RENPHO → Is Closed → Run Immediately**, running this shortcut.
   Switch from RENPHO while the phone stays unlocked after readings sync.
   If your iOS version uses **Ask Before Running**, turn that off instead.

Apple documents [HTTP requests in Shortcuts](https://support.apple.com/guide/shortcuts/request-your-first-api-apd58d46713f/ios)
and [app automation triggers](https://support.apple.com/guide/shortcuts/setting-triggers-apde31e9638b/ios).
The automation needs an unlocked phone for Health access, a reachable server,
and readings already written to Apple Health. Running the shortcut again is
safe; its seven-day window catches up on missed uploads. This does not run
directly when the scale records a reading. A scheduled trigger can also be
used, but may fail if the phone is locked.

### Optional paid exporter: Health Auto Export

In Health Auto Export, create an enabled **REST API** automation (requires its
paid automation feature):

- URL: your app's HTTPS origin followed by `/api/import/apple-health`.
- HTTP header: `Authorization` with value `Bearer your-secret`.
- Data type: Health Metrics; select Weight and Body Fat Percentage only,
  with RENPHO as the preferred source for both.
- Format: JSON, Version 2, **Summarize Data off**. Weight units: kg or lb;
  body fat: percentage points (`22.5`, not `0.225`).
- Date range: Default (yesterday and today); desired sync frequency: hourly.
  Use Manual Upload for an initial history import or missed dates. Enable
  batching or shorten the date range if the request exceeds 1 MB.

Run Manual Upload once while the phone is unlocked. Refresh Progress to check
the measurements and “Apple Health last received” time beneath the weigh-in form.
Background App Refresh must be enabled; iOS can delay exports and cannot read
Health data while locked. See the provider's
[REST API setup](https://help.healthyapps.dev/en/health-auto-export/automations/rest-api/)
and [JSON format](https://help.healthyapps.dev/en/health-auto-export/export-format/health-metrics/).

The importer uses the sample's local date and the latest timestamp per metric
per day. Repeated or older samples are skipped. Existing manual values, subsequent
manual changes, and deletion of imported days take precedence over imports.
Other measurements and notes are preserved. Corrected values with an identical
timestamp are not automatically reapplied; edit that day's measurement manually.
This feeds the existing weekly check-in; it does not apply target changes.

## Web app on iPhone (Home Screen install)

The web UI is an installable PWA: full-screen, bottom tab bar, dark mode, and it
refreshes itself whenever it returns to the foreground.

The installed app always runs `main`, from its own checkout, so it is unaffected
by whatever branch is checked out for development:

| Endpoint | What | Runs |
|---|---|---|
| `:4325` | Vite dev server + dev API (`docker-compose.dev.yml`) | your working tree, hot reload |
| `:4332` | Built `web/dist` + API (`docker-compose.app.yml`) | `origin/main`, in `~/deploy/macro-engine` |

`scripts/deploy-app.sh` fetches `origin/main` and, when it has moved, rebuilds
the web app and restarts the service. A systemd user timer runs it every two
minutes, so merging to `main` is the deploy:

```bash
scripts/deploy-app.sh                      # first run clones ~/deploy/macro-engine
cp .env ~/deploy/macro-engine/.env         # MACRO_ENGINE_HEALTH_TOKEN for Apple Health sync
FORCE=1 ~/deploy/macro-engine/scripts/deploy-app.sh
cp scripts/systemd/macro-engine-app-deploy.* ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now macro-engine-app-deploy.timer
journalctl --user -u macro-engine-app-deploy -n 20    # deploy log
```

Install from the `:4332` endpoint (Safari → Share → Add to Home Screen); the
icon is tied to the host it was installed from. Both stacks share the same
database, so a branch that changes the schema affects the installed app too.

No service worker yet, so the app needs to reach the server to load.

## Architecture

- `macro_engine/db.py` — SQLite schema (foods, per-100g nutrients, portions,
  aliases, log, targets, FTS5 index)
- `macro_engine/etl_fdc.py` — USDA FDC bulk-CSV loader (Foundation + SR Legacy
  + FNDDS Survey). Re-runnable; food ids stable across reloads.
- `macro_engine/resolve.py` — phrase → food: learned aliases first, then FTS5
  with source-quality re-ranking
- `macro_engine/tracker.py` — logging, targets (append-only, latest wins),
  day totals, remaining, week bank
- `macro_engine/coach.py` — body metrics, weekly trend, measured/predicted
  maintenance, target planning, weekly check-ins, phases (Fat Loss Forever)
- `macro_engine/health_import.py` / `macro_engine/activity.py` — Apple Health
  body-metric import; Watch steps/resting HR/workout snapshots, sync status,
  read-only activity summaries
- `mcp_server.py` — FastMCP stdio server

Design rules: log rows carry denormalized macros (history survives data
reloads); unresolvable items are returned as `problems` or logged as flagged
estimates, never silently dropped; aliases are the product — every correction
teaches the resolver.

## Roadmap

1. ~~Core loop: USDA data + MCP server + fleet wiring~~ (this)
2. Vault recipe indexer (`Wiki/Reference/Recipes/` → per-serving macros)
3. Open Food Facts import (Canadian packaged goods), miss-queue → recon
   sourcing, chain-restaurant adapters
4. Optional UI (standalone PWA or Training Engine feature)

## License

MIT. See [LICENSE](LICENSE).
