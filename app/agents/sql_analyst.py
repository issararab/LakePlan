"""SQL analyst agent: writes, validates and runs read-only SQL against the pricing database."""

from __future__ import annotations

import logging
from typing import Literal

from langgraph.runtime import Runtime
from langgraph.types import Command

from app import debug_collector as dbg
from app.agents.state import LakePlanContext, LakePlanState, hand_off, reply
from app.duckdb_client import execute_query
from app.llm import LLMProvider
from app.planner import QuerySpec
from app.schemas import ChatResponse, QueryResult
from app.sql_generator import generate_sql
from app.sql_validator import SQLValidationError

logger = logging.getLogger(__name__)


def run_query_spec(spec: QuerySpec, llm: LLMProvider) -> QueryResult | None:
    """Generate and execute SQL for a single QuerySpec.

    Returns None if SQL generation or validation fails; returns a QueryResult with
    empty rows if execution fails after successful SQL generation.
    """
    try:
        sql = generate_sql(spec.description, llm)
    except SQLValidationError as exc:
        logger.warning("SQL validation failed for %r: %s", spec.name, exc)
        return None
    except Exception as exc:
        logger.exception("SQL generation error for %r: %s", spec.name, exc)
        return None
    try:
        rows = execute_query(sql)
    except Exception as exc:
        logger.warning("Query execution failed for %r: %s\nSQL: %s", spec.name, exc, sql)
        return QueryResult(name=spec.name, sql=sql, rows=[])
    return QueryResult(name=spec.name, sql=sql, rows=rows)


def sql_analyst(state: LakePlanState, runtime: Runtime[LakePlanContext]) -> Command[Literal["synthesizer", "respond"]]:
    """Run each planned lookup: the LLM writes the SQL, the validator checks it, DuckDB runs it.

    Hands the results to the synthesizer. If every lookup failed it answers itself, so the
    synthesizer never writes an estimate without data.
    """
    queries = state.get("queries", [])
    results: list[QueryResult] = []
    for spec in queries:
        result = run_query_spec(spec, runtime.context.llm)
        if result is not None:
            results.append(result)
            dbg.add(f"SQL: {spec.name}", f"{len(result.rows)} row(s) returned", "sql")
        else:
            dbg.add(f"SQL: {spec.name}", "Query failed or returned no data", "error")

    if not results:
        return reply(state, "sql_analyst", ChatResponse(answer=(
            "I wasn't able to retrieve pricing data. "
            "Please try rephrasing, or be more specific about the workload type."
        )))

    rows = sum(len(r.rows) for r in results)
    return hand_off(
        state, "sql_analyst", "synthesizer",
        f"{len(results)} of {len(queries)} lookups ran, {rows} row(s) of pricing data",
        query_results=results,
    )
