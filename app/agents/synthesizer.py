"""Synthesizer agent: writes the cost estimate from the SQL analyst's rows."""

from __future__ import annotations

import json
from typing import Any, Literal

from langgraph.runtime import Runtime
from langgraph.types import Command

from app.agents.state import LakePlanContext, LakePlanState, reply
from app.prompts import SYSTEM_PROMPT, build_synthesis_prompt
from app.schemas import ChatResponse, QueryResult


def _rows_to_compact_json(rows: list[dict[str, Any]], max_chars: int = 6000) -> str:
    """Serialize query rows to JSON, truncating at max_chars to protect the synthesis context budget."""
    text = json.dumps(rows, default=str)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... (truncated)"
    return text


def format_query_results(results: list[QueryResult]) -> str:
    """Format QueryResults into a readable text block for the synthesis prompt."""
    parts = []
    for r in results:
        rows_text = _rows_to_compact_json(r.rows) if r.rows else "(no rows returned)"
        parts.append(f"Query name: {r.name}\nSQL:\n{r.sql}\nRows:\n{rows_text}")
    return "\n\n---\n\n".join(parts)


def synthesizer(state: LakePlanState, runtime: Runtime[LakePlanContext]) -> Command[Literal["respond"]]:
    """Write the customer-facing estimate, grounded only in the rows it was handed."""
    results = state["query_results"]
    architecture = state["context"].proposed_architecture or "(architecture not recorded)"
    prompt = build_synthesis_prompt(
        question=state["question"],
        architecture_context=architecture[:2000],
        query_results=format_query_results(results),
        mode=state.get("mode", "user"),
    )
    answer = runtime.context.llm.complete(
        [{"role": "user", "content": prompt}],
        system=SYSTEM_PROMPT,
        step="pricing-followup" if state.get("followup") else "pricing-synthesis",
    )
    return reply(state, "synthesizer", ChatResponse(answer=answer, response_type="pricing", query_results=results))
