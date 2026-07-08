# CLAUDE.md

Macro Engine: local food DB (USDA FDC) + MCP server for conversational macro
tracking. See README.md for architecture and setup.

## Commands

```bash
.venv/bin/python -m unittest discover tests -v     # tests (stdlib unittest only)
.venv/bin/python -m macro_engine.etl_fdc           # reload USDA data (idempotent)
```

## Rules

- Runtime state: `~/.local/share/macro-engine/macros.db`. Never commit DBs.
- Stdlib-only except the `mcp` package (server layer). Keep it that way.
- Log rows keep denormalized macros — never "fix" history by mutating foods.
- Food ids must stay stable across ETL re-runs (upsert on source+source_id);
  aliases and log history depend on it.
- The consumer is an LLM (fleet proxy). Tool docstrings in `mcp_server.py` are
  prompt surface — keep them precise about item shapes and correction flow.
- Daily-note/vault writes are the proxy's job (agent-fleet assistant skill),
  not this server's. Keep the server vault-free.
