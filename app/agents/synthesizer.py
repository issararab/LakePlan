"""Synthesizer agent: writes the cost estimate from the SQL analyst's rows."""

from __future__ import annotations

import json
from typing import Any

from app.agents.base import Agent, Handoff, Reply, Session
from app.prompts import SYSTEM_PROMPT, build_synthesis_prompt
from app.schemas import AgentPhase, ChatResponse, QueryResult


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


class SynthesizerAgent(Agent):
    """Writes the customer-facing estimate, grounded only in the rows it was handed.

    Always replies; never hands off.

    Accepts handoffs with payload:
        question: The pricing question.
        followup: True for a customer follow-up (changes the LLM step label only).
        query_results: The SQL analyst's QueryResults.
    """

    name = "synthesizer"
    role = "Writes the cost estimate strictly from the rows the SQL analyst returned."
    phase = AgentPhase.PRICING

    def run(self, message: str, session: Session, handoff: Handoff | None) -> Handoff | Reply:
        if handoff is None:
            raise ValueError("The synthesizer only works on a handoff from the SQL analyst")

        results: list[QueryResult] = handoff.payload["query_results"]
        architecture = session.state.context.proposed_architecture or "(architecture not recorded)"
        prompt = build_synthesis_prompt(
            question=handoff.payload["question"],
            architecture_context=architecture[:2000],
            query_results=format_query_results(results),
            mode=session.state.mode,
        )
        answer = self.llm.complete(
            [{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPT,
            step="pricing-followup" if handoff.payload["followup"] else "pricing-synthesis",
        )
        return Reply(ChatResponse(answer=answer, response_type="pricing", query_results=results))
