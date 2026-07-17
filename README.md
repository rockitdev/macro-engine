# Macro Engine

Macro tracking you talk to instead of tap at. A local food database built from
open data (USDA FoodData Central), exposed as an MCP server so any Claude
surface (a local Claude Code session, or any agent that speaks MCP) can log
meals in plain language and answer the only question that matters:
**what's left today**.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install mcp
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

Tools: `log_meal`, `remaining`, `day_summary`, `search_food`, `set_targets`,
`add_alias`, `delete_log_entry`.

## Architecture

- `macro_engine/db.py` — SQLite schema (foods, per-100g nutrients, portions,
  aliases, log, targets, FTS5 index)
- `macro_engine/etl_fdc.py` — USDA FDC bulk-CSV loader (Foundation + SR Legacy
  + FNDDS Survey). Re-runnable; food ids stable across reloads.
- `macro_engine/resolve.py` — phrase → food: learned aliases first, then FTS5
  with source-quality re-ranking
- `macro_engine/tracker.py` — logging, targets (append-only, latest wins),
  day totals, remaining
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
