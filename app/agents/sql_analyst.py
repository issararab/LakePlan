"""SQL analyst agent: writes, validates and runs read-only SQL against the pricing database."""

from __future__ import annotations

import logging

from app import debug_collector as dbg
from app.agents.base import Agent, Handoff, Reply, Session
from app.duckdb_client import execute_query
from app.llm import LLMProvider
from app.planner import QuerySpec
from app.schemas import AgentPhase, ChatResponse, QueryResult
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


class SQLAnalystAgent(Agent):
    """Runs each planned lookup: LLM writes the SQL, the validator checks it, DuckDB runs it.

    Hands off to:
        synthesizer: with the results. Payload: question, followup, query_results.

    Accepts handoffs with payload:
        question, followup: Passed through to the synthesizer.
        queries: The QuerySpecs to run.
    """

    name = "sql_analyst"
    role = "Writes, validates and runs read-only DuckDB SQL for each planned lookup."
    phase = AgentPhase.PRICING

    def run(self, message: str, session: Session, handoff: Handoff | None) -> Handoff | Reply:
        if handoff is None:
            raise ValueError("The SQL analyst only works on a handoff from the planner")

        queries: list[QuerySpec] = handoff.payload["queries"]
        results: list[QueryResult] = []
        for spec in queries:
            result = run_query_spec(spec, self.llm)
            if result is not None:
                results.append(result)
                dbg.add(f"SQL: {spec.name}", f"{len(result.rows)} row(s) returned", "sql")
            else:
                dbg.add(f"SQL: {spec.name}", "Query failed or returned no data", "error")

        if not results:
            # Never let the synthesizer answer without data: it must not invent prices.
            return Reply(ChatResponse(answer=(
                "I wasn't able to retrieve pricing data. "
                "Please try rephrasing, or be more specific about the workload type."
            )))

        rows = sum(len(r.rows) for r in results)
        return Handoff(
            to="synthesizer",
            reason=f"{len(results)} of {len(queries)} lookups ran, {rows} row(s) of pricing data",
            payload={
                "question": handoff.payload["question"],
                "followup": handoff.payload["followup"],
                "query_results": results,
            },
        )
