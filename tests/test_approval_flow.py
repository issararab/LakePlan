"""Tests for the architecture approval phase and its rejection-limit safety valve.

Run from the repo root:  python -m pytest tests
"""

from typing import Literal

import pytest
from langgraph.types import Command

import app.agents.architect as architect_module
import app.graph as graph_module
from app.agent import PricingAgent
from app.agents import reply
from app.agents.architect import MAX_APPROVAL_REJECTIONS
from app.llm import LLMProvider
from app.schemas import AgentPhase, ChatResponse, CollectedContext


class _NoLLM(LLMProvider):
    """These paths are decided by keywords alone, so any LLM call is a test failure."""

    def complete(self, messages, system=None, step=""):
        raise AssertionError(f"Unexpected LLM call: {step}")


def _priced(state, runtime) -> Command[Literal["respond"]]:
    """Stands in for the planner, so approval tests don't run the pricing agents."""
    return reply(state, "planner", ChatResponse(answer="PRICING", response_type="pricing"))


def _seed(agent: PricingAgent, **values) -> None:
    """Write values into the session's graph state, as if a previous turn had left them."""
    agent._graph.update_state(agent._config, values, as_node="respond")


@pytest.fixture
def agent(monkeypatch):
    """A session waiting for architecture approval, with pricing and adjustment stubbed out."""
    monkeypatch.setattr(graph_module, "planner", _priced)
    monkeypatch.setattr(
        architect_module, "adjust_architecture",
        lambda original_architecture, user_feedback, context, llm: "adjusted",
    )
    a = PricingAgent(llm=_NoLLM())
    _seed(
        a,
        phase=AgentPhase.ARCHITECTURE_APPROVAL,
        context=CollectedContext(path_id="dwh-migration", path_label="DWH Migration",
                                 proposed_architecture="original"),
        history=[], rejections=0, mode="user",
    )
    return a


def test_approval_after_rejection_limit_proceeds_to_pricing(agent):
    for _ in range(MAX_APPROVAL_REJECTIONS):
        assert agent.handle("No, change the ingestion").response_type == "architecture_proposal"
    assert agent.state["rejections"] == MAX_APPROVAL_REJECTIONS

    response = agent.handle("Yes, approve")

    assert response.answer == "PRICING"
    assert agent.state["phase"] == AgentPhase.PRICING
    assert agent.state["context"].architecture_approved is True


def test_rejection_after_limit_resets_to_context_collection(agent):
    _seed(agent, rejections=MAX_APPROVAL_REJECTIONS)

    response = agent.handle("No, change it again")

    assert response.phase == AgentPhase.CONTEXT_COLLECTION
    assert agent.state["phase"] == AgentPhase.CONTEXT_COLLECTION
    assert agent.state["rejections"] == 0


def test_rejection_below_limit_adjusts_architecture(agent):
    response = agent.handle("No, change the ingestion")

    assert response.answer == "adjusted"
    assert agent.state["phase"] == AgentPhase.ARCHITECTURE_APPROVAL
    assert agent.state["rejections"] == 1
