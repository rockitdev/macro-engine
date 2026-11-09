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
| `set_profile` | Sex, age, height, activity level, and carb/fat preference |
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
