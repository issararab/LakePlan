# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

The official product name is **LakePlan** (always written with that capitalisation). Use it in all user-facing text, docs, and prompts. Don't put "Databricks" in the product name.

## Running the app

```bash
# Install Python dependencies
pip install -r requirements.txt

# Build the React frontend (first time, or after frontend changes)
cd frontend && npm install && npm run build && cd ..

# Copy and fill in credentials, then start the server
cp .env.example .env
python -m uvicorn app.main:app --reload
```

The UI is at http://localhost:8000. The API is at `POST /chat`, `POST /reset`, and `GET /health`.

## Frontend

The UI is a React + Vite app in `frontend/`. It builds into `app/static/`, which FastAPI serves as static files.

**Key files:**
- `frontend/src/App.jsx` — root component, manages mode state
- `frontend/src/components/ModePicker.jsx` — launch screen (User / Debug mode selection)
- `frontend/src/components/Onboarding.jsx` — two-step onboarding (category → problem description)
- `frontend/src/components/ChatView.jsx` — main chat interface, session management
- `frontend/src/components/Message.jsx` — renders user and agent messages
- `frontend/src/components/QueryPanel.jsx` — collapsible SQL + data panel (Debug mode only)
- `frontend/vite.config.js` — sets `base: '/static/'` and `outDir: '../app/static'`

**Rebuilding after changes:**
```bash
cd frontend && npm run build
```

The built assets in `app/static/` should be committed alongside source changes.

## Smoke-testing without an LLM

The validator and DuckDB layer can be tested independently (no credentials needed):

```bash
python -c "
import sys; sys.path.insert(0, '.')
from app.duckdb_client import get_schema_context, execute_query
from app.sql_validator import validate_and_sanitize, SQLValidationError
print(get_schema_context())
print(execute_query(\"SELECT tier, price_usd FROM main.azure_web_storage_rates_germany LIMIT 3\"))
"
```

## Architecture

The request pipeline in `app/agent.py` has four stages:

1. **Intent check** — the LLM decides if it needs follow-up questions. If yes, return early with no SQL.
2. **SQL generation + validation** — `sql_generator.py` asks the LLM for a DuckDB SELECT (schema context embedded in the prompt), extracts SQL from the response, then `sql_validator.py` parses it with SQLGlot. On failure, one repair retry feeds the error back to the LLM.
3. **Execution** — `duckdb_client.py` opens the DB `read_only=True` and fetches ≤ `MAX_ROWS` rows.
4. **Summarization** — the LLM receives the question + SQL + rows and produces a grounded pricing answer.

Sessions are per-tab (client generates a UUID stored in `sessionStorage`). The server keeps up to 200 concurrent sessions in memory.

## LLM abstraction

`app/llm.py` exposes a single `LLMProvider` ABC with one method: `complete(messages, system)`. Two implementations exist: `DatabricksLLMProvider` (OpenAI-compatible endpoint) and `AnthropicLLMProvider`. The factory `get_llm_provider()` reads the `LLM_PROVIDER` env var (`databricks` default, `anthropic` to switch). The rest of the code only ever calls `llm.complete()`.

## DuckDB schema — critical gotchas

The database (`data/pricing.duckdb`) has six tables in `main`, all Azure Germany / USD:

- **`azure_dbx_system_price_rates`** — The `pricing` column is **JSON stored as VARCHAR**. Always extract with `CAST(json_extract_string(pricing, '$.default') AS DOUBLE)`, never read it as a number directly.
- **`azure_web_price_rates_germany`** — Several column names **start with a digit** (`1_year_savings_plan_savings_total_price_hourly_usd`, `3_year_...`). These must be **double-quoted** in SQL. Many numeric-looking columns (`dbu_count`, `vcpu_s`, `ram`) are `VARCHAR` — cast before arithmetic.
- **`azure_web_storage_rates_germany`** — `price_usd` is `DOUBLE`; `capacity` and `tier` are text bands.
- **`azure_dbx_foundation_model_rates`** — open-source model pricing (Llama, Gemma, etc.) for Germany West Central. DBU → USD: $0.084/DBU. Filter: `selected_region = 'Germany West Central'`.
- **`azure_dbx_proprietary_foundation_model_rates`** — Anthropic/Google/OpenAI model pricing. Region-agnostic. DBU → USD: $0.11/DBU Anthropic, $0.07/DBU Google/OpenAI.
- **`azure_dbx_foundation_model_overview`** — combined model catalogue with `pricing_mode`, `is_proprietary`, and `price_usd_per_dbu`.

These gotchas are baked into both the SQL-generation prompt (`app/prompts.py: SQL_GENERATION_PROMPT`) and the system prompt.

## All prompts live in `app/prompts.py`

- `SYSTEM_PROMPT` — agent persona and the "never invent prices" rule. Passed as the `system` parameter to the synthesis LLM call.
- `PLANNING_PROMPT` — instructs the LLM to return a JSON plan: either `{"action":"ask","questions":"..."}` or `{"action":"query","queries":[...]}`.
- `SQL_GENERATION_PROMPT` — full schema with per-column notes and dialect rules. Formatted with `{question}`.
- `SYNTHESIS_PROMPT` — instructs the LLM to produce the final answer strictly from query rows. Formatted with `{question}` and `{query_results}`.

## API schemas (`app/schemas.py`)

```python
# Request
ChatRequest(message: str, session_id: str)
ResetRequest(session_id: str)

# Response
QueryResult(name: str, sql: str, rows: list[dict])
ChatResponse(answer: str, query_results: list[QueryResult])
```

`context_extractor.py` protected fields: `path_id`, `path_label`, `initial_description`, `proposed_architecture`, `architecture_approved` are stripped from the LLM-returned extracted dict before merging. This prevents the LLM from overwriting API-set path labels.

Notable `CollectedContext` fields:
- `historical_volume_gb: float | None` — one-time bulk historical data volume (e.g. 45 TB = 46080). Included in `_build_pricing_question` so storage math starts from the right baseline.
- `num_bi_users: int | None` — named BI users; midpoint used for ranges.
- `peak_concurrent_queries: int | None` — explicitly stated peak concurrency. Takes priority over `num_bi_users × 15%` heuristic for SQL Warehouse sizing. Always label `[user-stated]` when present.

## SQL validator rules (`app/sql_validator.py`)

Only `SELECT` is allowed. Blocked at two levels: keyword scan (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `CREATE`, `ALTER`, `COPY`, `ATTACH`, `INSTALL`, `LOAD`, `TRUNCATE`, `PRAGMA`, `EXPORT`) and SQLGlot AST node check. A `LIMIT` clause is auto-injected (capped at 100) if absent or above the cap.

Every table a query reads must be one of the six pricing tables in `_ALLOWED_TABLES` (with or without the `main.` prefix) or a CTE defined in the same query. Table functions (`read_csv`, `read_text`, `glob`, `query`, `duckdb_settings`, ...), file paths in `FROM`, and `information_schema` are rejected. **When you add a table to the DuckDB file, add it to `_ALLOWED_TABLES` too.**

As a second layer, `duckdb_client.py` opens the connection with `enable_external_access=false` and `lock_configuration=true`, so file and URL reads fail inside DuckDB even if a query got past the validator. Regression tests are in `tests/test_sql_safety.py` (`python -m pytest tests`).

## Environment variables

| Variable | Purpose |
|---|---|
| `LLM_PROVIDER` | `databricks` (default) or `anthropic` |
| `DATABRICKS_HOST` | Workspace URL |
| `DATABRICKS_TOKEN` | PAT |
| `DATABRICKS_LLM_ENDPOINT` | Model serving endpoint name |
| `ANTHROPIC_API_KEY` | Required when `LLM_PROVIDER=anthropic` |
| `ANTHROPIC_MODEL` | Defaults to `claude-opus-4-5` |
| `DUCKDB_PATH` | Defaults to `data/pricing.duckdb` |
| `MAX_ROWS` | Defaults to `100` |
| `LOG_LEVEL` | Defaults to `INFO`; set to `DEBUG` for full prompt logging |

## `create_pricing_agent.py`

Kept for reference only — it is the original Databricks notebook export (uses `WorkspaceClient`, `dbutils`, MLflow, Genie MCP). It is not imported anywhere.
