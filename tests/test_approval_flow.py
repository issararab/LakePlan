"""Tests for the architecture approval phase and its rejection-limit safety valve.

Run from the repo root:  python -m pytest tests
"""

import pytest

import app.agent as agent_module
from app.agent import _MAX_APPROVAL_REJECTIONS, PricingAgent
from app.llm import LLMProvider
from app.schemas import AgentPhase, ChatResponse


class _NoLLM(LLMProvider):
    """These paths are decided by keywords alone, so any LLM call is a test failure."""

    def complete(self, messages, system=None, step=""):
        raise AssertionError(f"Unexpected LLM call: {step}")


@pytest.fixture
def agent(monkeypatch):
    """An agent waiting for architecture approval, with pricing and adjustment stubbed out."""
    a = PricingAgent(llm=_NoLLM())
    a._state.phase = AgentPhase.ARCHITECTURE_APPROVAL
    a._state.context = a._state.context.merge({"proposed_architecture": "original"})

    monkeypatch.setattr(
        a, "_calculate_pricing",
        lambda: ChatResponse(answer="PRICING", response_type="pricing", phase=AgentPhase.PRICING),
    )
    monkeypatch.setattr(
        agent_module, "adjust_architecture",
        lambda original_architecture, user_feedback, context, llm: "adjusted",
    )
    return a


def test_approval_after_rejection_limit_proceeds_to_pricing(agent):
    for _ in range(_MAX_APPROVAL_REJECTIONS):
        assert agent.handle("No, change the ingestion").response_type == "architecture_proposal"
    assert agent._state.consecutive_approval_rejections == _MAX_APPROVAL_REJECTIONS

    response = agent.handle("Yes, approve")

    assert response.answer == "PRICING"
    assert agent._state.phase == AgentPhase.PRICING
    assert agent._state.context.architecture_approved is True


def test_rejection_after_limit_resets_to_context_collection(agent):
    agent._state.consecutive_approval_rejections = _MAX_APPROVAL_REJECTIONS

    response = agent.handle("No, change it again")

    assert response.phase == AgentPhase.CONTEXT_COLLECTION
    assert agent._state.phase == AgentPhase.CONTEXT_COLLECTION
    assert agent._state.consecutive_approval_rejections == 0


def test_rejection_below_limit_adjusts_architecture(agent):
    response = agent.handle("No, change the ingestion")

    assert response.answer == "adjusted"
    assert agent._state.phase == AgentPhase.ARCHITECTURE_APPROVAL
    assert agent._state.consecutive_approval_rejections == 1
