"""The LakePlan agent workflow as a LangGraph state graph.

    START ──► (agent holding the conversation)
                intake ──► architect ──► planner ──► sql_analyst ──► synthesizer
                  ▲            │            │             │               │
                  └────────────┴────────────┘             ▼               ▼
                   back to intake when the        respond ──► END   (every agent replies
                   requirements change                               through respond)

Each turn enters at the agent that holds the conversation for the current phase. Agents
route with `Command(goto=...)`: on to another agent, or to `respond`, which closes the
turn. A checkpointer keeps each session's state under its own thread id between turns.
"""

from __future__ import annotations

import logging

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app import debug_collector as dbg
from app.agents.architect import architect
from app.agents.intake import intake
from app.agents.planner import planner
from app.agents.sql_analyst import sql_analyst
from app.agents.state import AgentName, LakePlanContext, LakePlanState
from app.agents.synthesizer import synthesizer
from app.schemas import AgentPhase

logger = logging.getLogger(__name__)

MAX_HISTORY = 20

# Who gets the customer's next message in each phase
PHASE_OWNER: dict[AgentPhase, AgentName] = {
    AgentPhase.CONTEXT_COLLECTION: "intake",
    AgentPhase.ARCHITECTURE_PROPOSAL: "architect",
    AgentPhase.ARCHITECTURE_APPROVAL: "architect",
    AgentPhase.PRICING: "planner",
}

# The phase reported to the UI when an agent answers. The agent that answers holds the conversation.
AGENT_PHASE: dict[AgentName, AgentPhase] = {
    "intake": AgentPhase.CONTEXT_COLLECTION,
    "architect": AgentPhase.ARCHITECTURE_APPROVAL,
    "planner": AgentPhase.PRICING,
    "sql_analyst": AgentPhase.PRICING,
    "synthesizer": AgentPhase.PRICING,
}

def route_turn(state: LakePlanState) -> AgentName:
    """START edge: give the message to the agent that holds the conversation."""
    phase = state.get("phase", AgentPhase.CONTEXT_COLLECTION)
    owner = PHASE_OWNER[phase]
    message = state.get("message", "")
    logger.info("Turn phase=%s agent=%s path=%s mode=%s | message: %s",
                phase.value, owner, state["context"].path_id or "unknown", state.get("mode", "user"),
                (message[:80] + "…") if len(message) > 80 else message)
    dbg.add("Phase", phase.value, "phase")
    return owner


def respond(state: LakePlanState) -> dict:
    """Close the turn: record who answered, set the phase, and add the exchange to history."""
    speaker = state["speaker"]
    phase = AGENT_PHASE[speaker]
    response = state["response"].model_copy(update={"phase": phase, "agent": speaker})
    history = [
        *state.get("history", []),
        {"role": "user", "content": state["message"]},
        {"role": "assistant", "content": response.answer},
    ][-MAX_HISTORY:]
    return {"phase": phase, "response": response, "history": history}


def new_checkpointer() -> InMemorySaver:
    """In-memory session store. Swap in a SQLite or Postgres saver to keep sessions across restarts.

    On compile, LangGraph limits what the checkpointer will restore to the classes named in
    LakePlanState's type hints, so anything stored in the state must appear there.
    """
    return InMemorySaver()


def build_graph(checkpointer: InMemorySaver | None = None) -> CompiledStateGraph:
    """Wire the agents into the LakePlan workflow and compile it."""
    builder = StateGraph(LakePlanState, context_schema=LakePlanContext)

    builder.add_node("intake", intake)
    builder.add_node("architect", architect)
    builder.add_node("planner", planner)
    builder.add_node("sql_analyst", sql_analyst)
    builder.add_node("synthesizer", synthesizer)
    builder.add_node("respond", respond)

    builder.add_conditional_edges(START, route_turn, ["intake", "architect", "planner"])
    builder.add_edge("respond", END)

    return builder.compile(checkpointer=checkpointer or new_checkpointer(), name="lakeplan")
