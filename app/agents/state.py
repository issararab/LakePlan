"""Graph state, runtime context and routing helpers shared by the agent nodes.

Every agent is a LangGraph node: it reads `LakePlanState`, gets the LLM from the run's
`LakePlanContext`, and returns a `Command` that updates the state and names the next node:
either another agent (`hand_off`) or `respond`, which closes the turn (`reply`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from langgraph.types import Command

from app import debug_collector as dbg
from app.llm import LLMProvider
from app.planner import QuerySpec
from app.schemas import AgentPhase, ChatResponse, CollectedContext, QueryResult

logger = logging.getLogger(__name__)

AgentName = Literal["intake", "architect", "planner", "sql_analyst", "synthesizer"]


class LakePlanState(TypedDict, total=False):
    """Everything the agents read and write. The checkpointer keeps it per session thread.

    Kept for the whole session:
        phase: Conversation phase; decides which agent gets the customer's next message.
        context: Requirements collected so far, plus the proposed architecture.
        history: Recent user/assistant messages, oldest first (capped at 20).
        rejections: Change requests in a row on the current architecture.
        mode: 'user' or 'debug', locked on the first message.

    Reset at the start of every turn:
        message: The customer's message for this turn.
        sender: Agent that passed work to the current node this turn. None means the node
            is handling the customer's message directly.
        reopen: Why the conversation came back to intake ('rethink' or 'scenario_change').
        question: Pricing question passed from architect or planner down to the synthesizer.
        followup: True when the pricing question is a customer follow-up.
        queries: Lookups planned by the planner for the SQL analyst.
        query_results: Rows the SQL analyst got back, for the synthesizer.
        trace: Agents that ran this turn, in order.
        speaker: Agent that answered the customer.
        response: The answer.
    """

    phase: AgentPhase
    context: CollectedContext
    history: list[dict]
    rejections: int
    mode: str

    message: str
    sender: AgentName | None
    reopen: str | None
    question: str | None
    followup: bool
    queries: list[QuerySpec]
    query_results: list[QueryResult]
    trace: list[str]
    speaker: AgentName | None
    response: ChatResponse | None


# Values that start every turn; the checkpointer would otherwise carry them over.
TURN_DEFAULTS: dict[str, Any] = {
    "sender": None,
    "reopen": None,
    "question": None,
    "followup": False,
    "queries": [],
    "query_results": [],
    "trace": [],
    "speaker": None,
    "response": None,
}


@dataclass(frozen=True)
class LakePlanContext:
    """Per-run dependencies, given to nodes through LangGraph's runtime context (never checkpointed).

    Attributes:
        llm: The LLM every agent in the run uses.
    """

    llm: LLMProvider


def hand_off(state: LakePlanState, sender: AgentName, to: AgentName, reason: str, **update: Any) -> Command:
    """Pass the turn to another agent node, recording who sent it and why."""
    logger.info("Handoff %s → %s: %s", sender, to, reason)
    dbg.add(f"Handoff: {sender} → {to}", reason, "handoff")
    return Command(goto=to, update={"sender": sender, "trace": [*state.get("trace", []), sender], **update})


def reply(state: LakePlanState, speaker: AgentName, response: ChatResponse, **update: Any) -> Command:
    """Finish the agents' work for this turn with an answer for the customer."""
    return Command(
        goto="respond",
        update={"speaker": speaker, "response": response, "trace": [*state.get("trace", []), speaker], **update},
    )
