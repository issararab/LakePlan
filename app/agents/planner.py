"""Planner agent: decides which pricing lookups answer the question."""

from __future__ import annotations

from typing import Literal

from langgraph.runtime import Runtime
from langgraph.types import Command

from app.agents.state import LakePlanContext, LakePlanState, hand_off, reply
from app.planner import plan
from app.schemas import ChatResponse

# Phrases that mean the customer wants to change an input rather than ask about the estimate.
_SCENARIO_CHANGE_SIGNALS = (
    "change", "update", "different", "instead", "what if", "what about",
    "switch", "modify", "alter", "new scenario", "start over",
)


def planner(state: LakePlanState, runtime: Runtime[LakePlanContext]) -> Command[Literal["sql_analyst", "intake", "respond"]]:
    """Turn a pricing question into a list of self-contained DuckDB lookups.

    Prices the architect's brief when the architect hands over. Once a price has been
    given it holds the conversation, so it also answers follow-ups, and hands back to
    intake when a follow-up changes the scenario (`reopen="scenario_change"`).
    """
    if state.get("sender"):
        question, followup = state["question"], state["followup"]
    else:
        message = state["message"]
        if any(signal in message.lower() for signal in _SCENARIO_CHANGE_SIGNALS):
            return hand_off(state, "planner", "intake", "Customer wants to change the scenario", reopen="scenario_change")
        question, followup = message, True

    pricing_plan = plan(state.get("history", []), question, runtime.context.llm)

    if pricing_plan.action == "ask":
        return reply(state, "planner", ChatResponse(answer=pricing_plan.questions or ""))

    count = len(pricing_plan.queries)
    return hand_off(
        state, "planner", "sql_analyst",
        f"{count} lookup{'s' if count != 1 else ''} planned: " + ", ".join(q.name for q in pricing_plan.queries),
        question=question, followup=followup, queries=pricing_plan.queries,
    )
