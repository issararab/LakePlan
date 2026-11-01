"""End-to-end tests for a full LakePlan session, driven through PricingAgent.handle().

A scripted LLM returns canned replies per pipeline step, so no credentials are needed.
SQL runs for real against data/pricing.duckdb.

Run from the repo root:  python -m pytest tests
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Literal

import pytest
from langchain_core.tracers.base import BaseTracer
from langchain_core.tracers.context import register_configure_hook
from langgraph.types import Command

from app.agent import PricingAgent
from app.llm import LLMProvider
from app.schemas import AgentPhase

_STORAGE_SQL = "```sql\nSELECT tier, price_usd FROM main.azure_web_storage_rates_germany LIMIT 3\n```"

_ASK = json.dumps({"action": "ask", "questions": "- How many tables?", "extracted": {"source_system": "SQL Server"}})
_COMPLETE = json.dumps({"action": "complete", "questions": "", "extracted": {"num_tables": 50, "num_bi_users": 10}})


class ScriptedLLM(LLMProvider):
    """Replies by pipeline step label and records every call.

    A list value is consumed one reply per call; a string value is reused.
    """

    def __init__(self, **replies: str | list[str]) -> None:
        self._replies = {k.replace("_", "-"): v for k, v in replies.items()}
        self.steps: list[str] = []

    def complete(self, messages, system=None, step=""):
        self.steps.append(step)
        key = step.split("(")[0]  # "sql-gen(attempt=1)" -> "sql-gen"
        if key not in self._replies:
            raise AssertionError(f"Unexpected LLM call: {step}")
        reply = self._replies[key]
        return reply.pop(0) if isinstance(reply, list) else reply


def _default_llm(**overrides) -> ScriptedLLM:
    replies = dict(
        context_extractor=[_ASK, _COMPLETE],
        completeness_check=json.dumps({"complete": True, "missing": []}),
        architecture_generate="ARCHITECTURE v1",
        architecture_adjust="ARCHITECTURE v2",
        planner=json.dumps({"action": "query", "queries": [
            {"name": "storage_pricing", "description": "Hot tier storage price per GB"},
        ]}),
        sql_gen=_STORAGE_SQL,
        pricing_synthesis="PRICING ANSWER",
        pricing_followup="FOLLOW-UP ANSWER",
    )
    replies.update(overrides)
    return ScriptedLLM(**replies)


def _start(agent: PricingAgent, mode: str = "user"):
    return agent.handle(
        "We are moving a 2 TB SQL Server warehouse to Databricks",
        path_id="dwh-migration",
        path_label="DWH Migration",
        mode=mode,
    )


def _to_approval(agent: PricingAgent):
    _start(agent)
    return agent.handle("About 50 tables, nightly batch, 10 analysts")


def _to_pricing(agent: PricingAgent):
    _to_approval(agent)
    return agent.handle("Yes, approve")


def test_full_session_ends_with_a_price_grounded_in_duckdb_rows():
    llm = _default_llm()
    agent = PricingAgent(llm=llm)

    first = _start(agent)
    assert first.response_type == "message"
    assert first.phase == AgentPhase.CONTEXT_COLLECTION
    assert "How many tables?" in first.answer

    second = agent.handle("About 50 tables, nightly batch, 10 analysts")
    assert second.response_type == "architecture_proposal"
    assert second.phase == AgentPhase.ARCHITECTURE_APPROVAL
    assert second.answer == "ARCHITECTURE v1"

    third = agent.handle("Yes, approve")
    assert third.response_type == "pricing"
    assert third.phase == AgentPhase.PRICING
    assert third.answer == "PRICING ANSWER"
    [result] = third.query_results
    assert result.name == "storage_pricing"
    assert "azure_web_storage_rates_germany" in result.sql
    assert result.rows and {"tier", "price_usd"} <= result.rows[0].keys()

    assert llm.steps == [
        "context-extractor",
        "context-extractor", "completeness-check", "architecture-generate",
        "planner", "sql-gen(attempt=1)", "pricing-synthesis",
    ]
    assert agent.state["context"].source_system == "SQL Server"
    assert agent.state["context"].num_tables == 50
    assert agent.state["context"].architecture_approved is True


def test_missing_fields_are_asked_as_plain_questions():
    llm = _default_llm(completeness_check=json.dumps({"complete": False, "missing": ["num_tables"]}))
    agent = PricingAgent(llm=llm)

    response = _to_approval(agent)

    assert response.phase == AgentPhase.CONTEXT_COLLECTION
    assert "Roughly how many tables" in response.answer
    assert "num_tables" not in response.answer


def test_change_request_adjusts_the_architecture():
    agent = PricingAgent(llm=_default_llm())
    _to_approval(agent)

    response = agent.handle("No, use Fivetran for ingestion instead")

    assert response.response_type == "architecture_proposal"
    assert response.answer == "ARCHITECTURE v2"
    assert agent.state["context"].proposed_architecture == "ARCHITECTURE v2"


def test_ambiguous_reply_is_classified_by_the_llm():
    llm = _default_llm(approval_classifier="approve")
    agent = PricingAgent(llm=llm)
    _to_approval(agent)

    response = agent.handle("hmm, ship it")

    assert response.response_type == "pricing"
    assert "approval-classifier" in llm.steps


def test_follow_up_question_runs_the_pricing_pipeline_again():
    llm = _default_llm()
    agent = PricingAgent(llm=llm)
    _to_pricing(agent)
    llm.steps.clear()

    response = agent.handle("How much of that is storage?")

    assert response.response_type == "pricing"
    assert response.answer == "FOLLOW-UP ANSWER"
    assert llm.steps == ["planner", "sql-gen(attempt=1)", "pricing-followup"]


def test_scenario_change_returns_to_context_collection_and_keeps_inputs():
    llm = _default_llm()
    agent = PricingAgent(llm=llm)
    _to_pricing(agent)
    llm.steps.clear()

    response = agent.handle("What if we switch to streaming?")

    assert response.phase == AgentPhase.CONTEXT_COLLECTION
    assert "What would you like to change?" in response.answer
    assert llm.steps == []
    assert agent.state["context"].proposed_architecture is None
    assert agent.state["context"].architecture_approved is False
    assert agent.state["context"].num_tables == 50


def test_planner_can_ask_for_more_information():
    llm = _default_llm(planner=json.dumps({"action": "ask", "questions": "Which storage tier?"}))
    agent = PricingAgent(llm=llm)

    response = _to_pricing(agent)

    assert response.answer == "Which storage tier?"
    assert response.phase == AgentPhase.PRICING
    assert not any(s.startswith("sql-gen") for s in llm.steps)


def test_unusable_sql_gives_a_clear_message_instead_of_an_invented_price():
    llm = _default_llm(sql_gen="DROP TABLE azure_web_storage_rates_germany")
    agent = PricingAgent(llm=llm)

    response = _to_pricing(agent)

    assert "wasn't able to retrieve pricing data" in response.answer
    assert response.query_results == []
    assert "pricing-synthesis" not in llm.steps


def test_message_before_a_path_is_chosen_asks_for_a_path():
    agent = PricingAgent(llm=_default_llm())

    response = agent.handle("hello")

    assert response.phase == AgentPhase.CONTEXT_COLLECTION
    assert "select a path" in response.answer


@pytest.mark.parametrize("mode, expect_steps", [("user", False), ("debug", True)])
def test_debug_steps_only_in_debug_mode(mode, expect_steps):
    agent = PricingAgent(llm=_default_llm())

    response = _start(agent, mode=mode)

    assert bool(response.debug_steps) is expect_steps


# ---------------------------------------------------------------------------
# Handoffs between agents
# ---------------------------------------------------------------------------

def test_each_turn_is_handled_by_the_expected_chain_of_agents():
    agent = PricingAgent(llm=_default_llm())

    assert _start(agent).agent == "intake"
    assert agent.last_trace == ["intake"]

    assert agent.handle("About 50 tables, nightly batch, 10 analysts").agent == "architect"
    assert agent.last_trace == ["intake", "architect"]

    assert agent.handle("Yes, approve").agent == "synthesizer"
    assert agent.last_trace == ["architect", "planner", "sql_analyst", "synthesizer"]

    assert agent.handle("How much of that is storage?").agent == "synthesizer"
    assert agent.last_trace == ["planner", "sql_analyst", "synthesizer"]

    assert agent.handle("What if we switch to streaming?").agent == "intake"
    assert agent.last_trace == ["planner", "intake"]


def test_repeated_change_requests_hand_back_to_intake():
    agent = PricingAgent(llm=_default_llm())
    _to_approval(agent)
    for _ in range(3):
        agent.handle("No, change the ingestion")

    response = agent.handle("No, change it again")

    assert agent.last_trace == ["architect", "intake"]
    assert response.phase == AgentPhase.CONTEXT_COLLECTION
    assert "significant rethinking" in response.answer


def test_handoffs_with_reasons_appear_in_debug_steps():
    agent = PricingAgent(llm=_default_llm())
    _start(agent, mode="debug")
    agent.handle("About 50 tables, nightly batch, 10 analysts")

    response = agent.handle("Yes, approve")

    rows = len(response.query_results[0].rows)
    handoffs = [(s["label"], s["detail"]) for s in response.debug_steps if s["type"] == "handoff"]
    assert handoffs == [
        ("Handoff: architect → planner", "Architecture approved — pricing it"),
        ("Handoff: planner → sql_analyst", "1 lookup planned: storage_pricing"),
        ("Handoff: sql_analyst → synthesizer", f"1 of 1 lookups ran, {rows} row(s) of pricing data"),
    ]


def test_architect_hands_the_planner_a_self_contained_brief():
    llm = _default_llm()
    planner_prompts: list[str] = []
    complete = llm.complete

    def spy(messages, system=None, step=""):
        if step == "planner":
            planner_prompts.append(messages[0]["content"])
        return complete(messages, system=system, step=step)

    llm.complete = spy
    _to_pricing(PricingAgent(llm=llm))

    [prompt] = planner_prompts
    assert "Calculate Databricks pricing for a DWH Migration deployment" in prompt
    assert "Source: SQL Server." in prompt
    assert "Number of BI/analyst users: 10." in prompt


def test_failed_lookups_stop_before_the_synthesizer():
    agent = PricingAgent(llm=_default_llm(sql_gen="not sql at all"))

    response = _to_pricing(agent)

    assert agent.last_trace == ["architect", "planner", "sql_analyst"]
    assert response.agent == "sql_analyst"
    assert response.phase == AgentPhase.PRICING


def test_a_routing_loop_is_cut_off(monkeypatch):
    import app.graph as graph_module
    from app.agents import hand_off

    def looping_intake(state, runtime) -> Command[Literal["intake"]]:
        return hand_off(state, "intake", "intake", "again")

    monkeypatch.setattr(graph_module, "intake", looping_intake)
    agent = PricingAgent(llm=_default_llm())

    response = agent.handle("hello")

    assert "lost track" in response.answer
    assert agent.last_trace and set(agent.last_trace) == {"intake"}


# ---------------------------------------------------------------------------
# The LangGraph graph and its checkpointer
# ---------------------------------------------------------------------------

def test_graph_edges_match_the_agent_design():
    from app.graph import build_graph

    edges = {(e.source, e.target) for e in build_graph().get_graph().edges}

    assert edges == {
        ("__start__", "intake"), ("__start__", "architect"), ("__start__", "planner"),
        ("intake", "architect"), ("intake", "respond"),
        ("architect", "planner"), ("architect", "intake"), ("architect", "respond"),
        ("planner", "sql_analyst"), ("planner", "intake"), ("planner", "respond"),
        ("sql_analyst", "synthesizer"), ("sql_analyst", "respond"),
        ("synthesizer", "respond"),
        ("respond", "__end__"),
    }


def test_each_session_is_its_own_thread_and_survives_between_handles():
    from app.graph import build_graph

    graph = build_graph()
    llm = _default_llm()
    alice = PricingAgent(llm=llm, session_id="alice", graph=graph)
    _start(alice)

    bob = PricingAgent(llm=llm, session_id="bob", graph=graph)
    assert bob.state == {}

    # A new handle on the same thread picks up where the session left off
    alice_again = PricingAgent(llm=llm, session_id="alice", graph=graph)
    assert alice_again.state["context"].source_system == "SQL Server"
    assert alice_again.handle("About 50 tables, nightly batch, 10 analysts").agent == "architect"


def test_reset_deletes_the_sessions_thread():
    agent = PricingAgent(llm=_default_llm())
    _start(agent)
    assert agent.state

    agent.reset()

    assert agent.state == {}


def test_registry_drops_the_oldest_session_when_full(monkeypatch):
    import app.agent as agent_module

    monkeypatch.setattr(agent_module, "_MAX_SESSIONS", 2)
    monkeypatch.setattr(agent_module, "_sessions", type(agent_module._sessions)())
    monkeypatch.setattr(agent_module, "_shared", {})
    monkeypatch.setattr(agent_module, "get_llm_provider", lambda: _default_llm())

    first = agent_module.get_agent("s1")
    _start(first)
    agent_module.get_agent("s2")
    agent_module.get_agent("s3")

    assert list(agent_module._sessions) == ["s2", "s3"]
    assert first.state == {}


class _RunRecorder(BaseTracer):
    """Records every chain run, the way a LangSmith tracer sees them."""

    def __init__(self) -> None:
        super().__init__()
        self.runs = []

    def _persist_run(self, run) -> None:
        pass

    def _on_chain_start(self, run) -> None:
        self.runs.append(run)


# Hook the recorder in the same way LangChain attaches the LangSmith tracer
_recorder: ContextVar[_RunRecorder | None] = ContextVar("lakeplan_run_recorder", default=None)
register_configure_hook(_recorder, inheritable=True)


def test_each_turn_is_traced_as_one_run_with_a_span_per_agent():
    """What LangSmith shows when tracing is on: one named run per turn, a child run per node."""
    agent = PricingAgent(llm=_default_llm(), session_id="trace-me")
    _to_approval(agent)

    recorder = _RunRecorder()
    token = _recorder.set(recorder)
    try:
        agent.handle("Yes, approve")
    finally:
        _recorder.reset(token)

    [turn] = [run for run in recorder.runs if run.parent_run_id is None]
    assert turn.name == "lakeplan-turn"
    assert turn.extra["metadata"]["session_id"] == "trace-me"
    nodes = [run.name for run in recorder.runs if run.parent_run_id == turn.id]
    for node in ["architect", "planner", "sql_analyst", "synthesizer", "respond"]:
        assert node in nodes


def test_checkpoints_only_hold_allowlisted_types():
    """Every class the graph stores must be on the checkpoint allowlist in app/graph.py.

    LangGraph reports each class it restores from a checkpoint without it being on the
    list (and, in strict mode, refuses to restore it). A full session must report none.
    """
    from langgraph.checkpoint.serde.event_hooks import register_serde_event_listener

    events = []
    unregister = register_serde_event_listener(events.append)
    try:
        agent = PricingAgent(llm=_default_llm())
        _to_pricing(agent)  # stores context, phase, queries, query_results and the response
        agent.handle("How much of that is storage?")
        assert agent.state["context"].architecture_approved is True
    finally:
        unregister()

    assert events == []
