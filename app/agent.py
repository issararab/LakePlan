"""Runs a LakePlan session as a team of agents that hand work to each other.

    intake ──► architect ──► planner ──► sql_analyst ──► synthesizer
      ▲            │            │
      └────────────┴────────────┘   hand back when the requirements change

Each turn starts with the agent that holds the conversation for the current phase.
An agent either replies to the customer or hands off to another agent with a reason
and a payload. The coordinator below follows the handoffs, records them, and stamps the
reply with the phase and name of the agent that wrote it.
"""

from __future__ import annotations

import logging

from app import debug_collector as dbg
from app.agents import (
    Agent,
    AgentName,
    ArchitectAgent,
    Handoff,
    IntakeAgent,
    PlannerAgent,
    Reply,
    Session,
    SQLAnalystAgent,
    SynthesizerAgent,
)
from app.llm import LLMProvider, get_llm_provider
from app.schemas import AgentPhase, AgentState, ChatResponse, CollectedContext

logger = logging.getLogger(__name__)

_MAX_HISTORY = 20

# The longest legitimate chain is architect → planner → sql_analyst → synthesizer (3 handoffs).
_MAX_HANDOFFS_PER_TURN = 5

# Which agent holds the conversation in each phase, i.e. who gets the customer's next message.
_PHASE_OWNER: dict[AgentPhase, AgentName] = {
    AgentPhase.CONTEXT_COLLECTION: "intake",
    AgentPhase.ARCHITECTURE_PROPOSAL: "architect",
    AgentPhase.ARCHITECTURE_APPROVAL: "architect",
    AgentPhase.PRICING: "planner",
}


class PricingAgent:
    """Coordinates one customer session across the agent team. One instance per browser session.

    Attributes:
        last_trace: Names of the agents that worked on the most recent turn, in order.
        _llm: LLM provider shared by all agents in the session.
        _agents: The agent team, keyed by name.
        _history: Sliding window of conversation messages (capped at _MAX_HISTORY).
        _state: Current phase, collected context, and session mode.
    """

    def __init__(self, llm: LLMProvider | None = None) -> None:
        """Builds the agent team around a shared LLM provider instance."""
        self._llm = llm or get_llm_provider()
        team: tuple[Agent, ...] = (
            IntakeAgent(self._llm),
            ArchitectAgent(self._llm),
            PlannerAgent(self._llm),
            SQLAnalystAgent(self._llm),
            SynthesizerAgent(self._llm),
        )
        self._agents: dict[str, Agent] = {agent.name: agent for agent in team}
        self._history: list[dict] = []
        self._state = AgentState()
        self.last_trace: list[str] = []

    def reset(self) -> None:
        """Reset conversation history and agent state, returning to context collection."""
        self._history.clear()
        self._state = AgentState()

    def _append_history(self, role: str, content: str) -> None:
        """Append a message to the conversation history, evicting the oldest when over the limit."""
        self._history.append({"role": role, "content": content})
        if len(self._history) > _MAX_HISTORY:
            self._history = self._history[-_MAX_HISTORY:]

    def handle(
        self,
        message: str,
        path_id: str | None = None,
        path_label: str | None = None,
        mode: str = "user",
    ) -> ChatResponse:
        """Process an incoming user message and return the agent response.

        Reinitialises state when path_id is provided (first message of a session).
        This handles page-refresh scenarios where the browser UUID persists but the
        agent should start fresh.

        Args:
            message: The user's message text.
            path_id: Use-case path identifier; sent on the first message only.
            path_label: Human-readable path label; sent on the first message only.
            mode: Session rendering mode ('user' or 'debug'). Locked on the first message.

        Returns:
            ChatResponse containing the agent's answer and optional debug steps.
        """
        # Reinitialise whenever path_id is provided (onboarding first message).
        # This handles page-refresh without /reset: sessionStorage keeps the same
        # UUID, so the server session may still be in PRICING from a previous run.
        if path_id:
            self._history.clear()
            self._state = AgentState()
            self._state.context = CollectedContext(
                path_id=path_id,
                path_label=path_label or path_id,
                initial_description=message,
            )

        # Lock mode on the first message — it stays for the session
        if self._state.mode == "user" and mode == "debug":
            self._state.mode = "debug"

        # Start debug collection for this request (noop if not debug)
        steps = dbg.start() if self._state.mode == "debug" else None

        response = self._run_turn(message)
        self._append_history("user", message)
        self._append_history("assistant", response.answer)

        if steps:
            response.debug_steps = steps
            dbg.stop()

        return response

    def _run_turn(self, message: str) -> ChatResponse:
        """Give the message to the agent that holds the conversation and follow its handoffs."""
        phase = self._state.phase
        owner = _PHASE_OWNER[phase]
        logger.info("Turn phase=%s agent=%s path=%s mode=%s | message: %s",
                    phase.value, owner, self._state.context.path_id or "unknown", self._state.mode,
                    (message[:80] + "…") if len(message) > 80 else message)
        dbg.add("Phase", phase.value, "phase")

        session = Session(state=self._state, history=self._history)
        agent = self._agents[owner]
        handoff: Handoff | None = None
        self.last_trace = []

        for _ in range(_MAX_HANDOFFS_PER_TURN + 1):
            self.last_trace.append(agent.name)
            outcome = agent.run(message, session, handoff)

            if isinstance(outcome, Reply):
                # Whoever replies holds the conversation, so the next message goes back to them
                self._state.phase = agent.phase
                response = outcome.response
                response.phase = agent.phase
                response.agent = agent.name
                return response

            handoff = outcome
            logger.info("Handoff %s → %s: %s", agent.name, handoff.to, handoff.reason)
            dbg.add(f"Handoff: {agent.name} → {handoff.to}", handoff.reason, "handoff")
            agent = self._agents[handoff.to]

        logger.error("No reply after %d handoffs (%s)", _MAX_HANDOFFS_PER_TURN, " → ".join(self.last_trace))
        return ChatResponse(
            answer="Sorry, I lost track of that request. Could you rephrase it?",
            phase=self._state.phase,
        )


# ---------------------------------------------------------------------------
# Session registry
# ---------------------------------------------------------------------------

_MAX_SESSIONS = 200
_sessions: dict[str, PricingAgent] = {}


def get_agent(session_id: str) -> PricingAgent:
    """Return the PricingAgent for the session, creating and caching one if needed.

    Evicts the oldest session when the registry reaches _MAX_SESSIONS.
    """
    if session_id not in _sessions:
        if len(_sessions) >= _MAX_SESSIONS:
            oldest = next(iter(_sessions))
            del _sessions[oldest]
        _sessions[session_id] = PricingAgent()
    return _sessions[session_id]


def reset_agent(session_id: str) -> None:
    """Reset the agent for the given session if it exists; otherwise no-op."""
    if session_id in _sessions:
        _sessions[session_id].reset()
