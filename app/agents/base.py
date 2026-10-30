"""Building blocks shared by LakePlan's agents: the Agent interface, handoffs and replies.

Each agent does one job and ends its turn in one of two ways:

- Reply: answer the customer. The agent that replies holds the conversation, so the
  customer's next message goes back to it.
- Handoff: pass control to another agent, with a reason and the work that agent needs
  (the payload). The coordinator in app/agent.py runs the receiving agent next.

Agents are stateless. Everything they know about the conversation lives in the Session.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

from app.llm import LLMProvider
from app.schemas import AgentPhase, AgentState, ChatResponse

AgentName = Literal["intake", "architect", "planner", "sql_analyst", "synthesizer"]


@dataclass
class Session:
    """The conversation state that agents read and update. Owned by the coordinator.

    Attributes:
        state: Phase, collected context, rejection counter and UI mode.
        history: Recent user/assistant messages, oldest first.
    """

    state: AgentState
    history: list[dict]


@dataclass(frozen=True)
class Handoff:
    """Pass control to another agent.

    Attributes:
        to: Name of the agent that should act next.
        reason: Why the work is moving on. Logged and shown in Debug mode.
        payload: What the receiving agent needs. Each agent documents the keys it reads.
    """

    to: AgentName
    reason: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Reply:
    """End the turn and answer the customer."""

    response: ChatResponse


class Agent(ABC):
    """One member of the LakePlan agent team.

    Class attributes:
        name: Identifier used as a handoff target.
        role: One-line description of the agent's job.
        phase: Conversation phase reported to the UI when this agent replies.
    """

    name: ClassVar[AgentName]
    role: ClassVar[str]
    phase: ClassVar[AgentPhase]

    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    @abstractmethod
    def run(self, message: str, session: Session, handoff: Handoff | None) -> Handoff | Reply:
        """Do this agent's part of the turn.

        Args:
            message: The customer's message for this turn.
            session: Shared conversation state; agents may update it.
            handoff: The handoff that activated this agent, or None when the agent
                holds the conversation and is handling the customer's message directly.
        """
