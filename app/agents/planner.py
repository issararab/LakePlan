"""Planner agent: decides which pricing lookups answer the question."""

from __future__ import annotations

from app.agents.base import Agent, Handoff, Reply, Session
from app.planner import plan
from app.schemas import AgentPhase, ChatResponse

# Phrases that mean the customer wants to change an input rather than ask about the estimate.
_SCENARIO_CHANGE_SIGNALS = (
    "change", "update", "different", "instead", "what if", "what about",
    "switch", "modify", "alter", "new scenario", "start over",
)


class PlannerAgent(Agent):
    """Turns a pricing question into a list of self-contained DuckDB lookups.

    Holds the conversation once a price has been given, so it also handles follow-ups.

    Hands off to:
        sql_analyst: with the planned lookups. Payload: question, followup, queries.
        intake: when a follow-up changes the scenario. Payload: reopen="scenario_change".

    Accepts handoffs with payload:
        question: The pricing question to plan for.
        followup: True for a customer follow-up, False for the first estimate.
    """

    name = "planner"
    role = "Breaks a pricing question into the DuckDB lookups needed to answer it."
    phase = AgentPhase.PRICING

    def run(self, message: str, session: Session, handoff: Handoff | None) -> Handoff | Reply:
        if handoff is not None:
            question, followup = handoff.payload["question"], handoff.payload["followup"]
        else:
            if any(signal in message.lower() for signal in _SCENARIO_CHANGE_SIGNALS):
                return Handoff(
                    to="intake",
                    reason="Customer wants to change the scenario",
                    payload={"reopen": "scenario_change"},
                )
            question, followup = message, True

        pricing_plan = plan(session.history, question, self.llm)

        if pricing_plan.action == "ask":
            return Reply(ChatResponse(answer=pricing_plan.questions or ""))

        count = len(pricing_plan.queries)
        return Handoff(
            to="sql_analyst",
            reason=f"{count} lookup{'s' if count != 1 else ''} planned: "
                   + ", ".join(q.name for q in pricing_plan.queries),
            payload={"question": question, "followup": followup, "queries": pricing_plan.queries},
        )
