"""Customer sessions on the LakePlan graph. Each browser session is one LangGraph thread."""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any

from langgraph.errors import GraphRecursionError
from langgraph.graph.state import CompiledStateGraph

from app import debug_collector as dbg
from app.agents.state import TURN_DEFAULTS, LakePlanContext
from app.graph import build_graph
from app.llm import LLMProvider, get_llm_provider
from app.schemas import AgentPhase, ChatResponse, CollectedContext

logger = logging.getLogger(__name__)

# Graph steps per turn. The longest real turn is architect → planner → sql_analyst →
# synthesizer → respond (5 steps), so 10 leaves room and still stops a routing loop.
RECURSION_LIMIT = 10


class PricingAgent:
    """One customer session. Turns run on the LakePlan graph; the graph's checkpointer
    keeps the session's state under `session_id`.

    Attributes:
        session_id: The LangGraph thread id for this session.
    """

    def __init__(
        self,
        llm: LLMProvider | None = None,
        session_id: str = "local",
        graph: CompiledStateGraph | None = None,
    ) -> None:
        """Uses a private graph and checkpointer unless a shared graph is passed in."""
        self.session_id = session_id
        self._llm = llm or get_llm_provider()
        self._graph = graph or build_graph()

    @property
    def _config(self) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": self.session_id},
            "recursion_limit": RECURSION_LIMIT,
            "run_name": "lakeplan-turn",
            "metadata": {"session_id": self.session_id},
        }

    @property
    def state(self) -> dict[str, Any]:
        """The session's current graph state (empty before the first message)."""
        return self._graph.get_state(self._config).values

    @property
    def last_trace(self) -> list[str]:
        """Agents that worked on the most recent turn, in order."""
        return self.state.get("trace", [])

    def reset(self) -> None:
        """Forget the session: delete its thread from the checkpointer."""
        self._graph.checkpointer.delete_thread(self.session_id)

    def handle(
        self,
        message: str,
        path_id: str | None = None,
        path_label: str | None = None,
        mode: str = "user",
    ) -> ChatResponse:
        """Run one turn of the conversation and return the answer.

        Starts a fresh session when path_id is provided (first message of a session),
        which also covers a page refresh that keeps the same browser session id.

        Args:
            message: The user's message text.
            path_id: Use-case path identifier; sent on the first message only.
            path_label: Human-readable path label; sent on the first message only.
            mode: Session rendering mode ('user' or 'debug'). Locked once set to debug.

        Returns:
            ChatResponse with the answer, the phase and agent, and debug steps in debug mode.
        """
        current = self.state
        turn: dict[str, Any] = {**TURN_DEFAULTS, "message": message}

        if path_id or not current:
            turn.update(
                phase=AgentPhase.CONTEXT_COLLECTION,
                context=CollectedContext(
                    path_id=path_id or "",
                    path_label=(path_label or path_id or ""),
                    initial_description=message if path_id else "",
                ),
                history=[],
                rejections=0,
            )
            previous_mode = "user"
        else:
            previous_mode = current.get("mode", "user")
        turn["mode"] = "debug" if "debug" in (previous_mode, mode) else "user"

        steps = dbg.start() if turn["mode"] == "debug" else None
        try:
            result = self._graph.invoke(turn, self._config, context=LakePlanContext(llm=self._llm))
            response: ChatResponse = result["response"]
        except GraphRecursionError:
            logger.error("Turn stopped after %d graph steps (%s)", RECURSION_LIMIT, " → ".join(self.last_trace))
            response = ChatResponse(
                answer="Sorry, I lost track of that request. Could you rephrase it?",
                phase=self.state.get("phase", AgentPhase.CONTEXT_COLLECTION),
            )
        finally:
            if steps is not None:
                dbg.stop()

        if steps:
            response.debug_steps = steps
        return response


# ---------------------------------------------------------------------------
# Session registry
# ---------------------------------------------------------------------------

_MAX_SESSIONS = 200
_sessions: OrderedDict[str, PricingAgent] = OrderedDict()
_shared: dict[str, Any] = {}


def get_agent(session_id: str) -> PricingAgent:
    """Return the PricingAgent for the session, creating one on the shared graph if needed.

    When the registry is full, the oldest session is dropped and its thread deleted.
    """
    if session_id not in _sessions:
        if "graph" not in _shared:
            _shared["graph"] = build_graph()
            _shared["llm"] = get_llm_provider()
        if len(_sessions) >= _MAX_SESSIONS:
            _, oldest = _sessions.popitem(last=False)
            oldest.reset()
        _sessions[session_id] = PricingAgent(llm=_shared["llm"], session_id=session_id, graph=_shared["graph"])
    return _sessions[session_id]


def reset_agent(session_id: str) -> None:
    """Reset the agent for the given session if it exists; otherwise no-op."""
    if session_id in _sessions:
        _sessions[session_id].reset()
