"""Intake agent: collects the customer's requirements."""

from __future__ import annotations

import json
import logging

from typing import Literal

from langgraph.runtime import Runtime
from langgraph.types import Command

from app import debug_collector as dbg
from app.agents.state import LakePlanContext, LakePlanState, hand_off, reply
from app.context_extractor import extract_context_updates
from app.llm import LLMProvider
from app.prompts import COMPLETENESS_CHECK_PROMPT
from app.schemas import ChatResponse, CollectedContext
from app.utils import extract_json

logger = logging.getLogger(__name__)

# Human-readable questions for completeness-check field names.
# Raw field names must never be shown to the customer.
_MISSING_FIELD_QUESTIONS: dict[str, str] = {
    "source_type": "What type of data source is involved? (e.g. relational database, SaaS application, files/object storage, SAP)",
    "source_system": "What is the specific source system? (e.g. SQL Server, Oracle, Salesforce, ADLS)",
    "num_tables": "Roughly how many tables or datasets are you migrating?",
    "daily_write_volume_gb": "How much new data arrives daily, approximately (in GB)?",
    "daily_processed_volume_gb": "What is the approximate daily data processing volume (in GB)?",
    "historical_volume_gb": "What is the total historical data volume you need to migrate (in GB or TB)?",
    "latency_requirement": "What is your data freshness requirement — batch (hourly/daily), near-real-time (minutes), or streaming (seconds)?",
    "workload_stability": "How consistent is your workload — sporadic (peaks and quiet periods), stable 24/7, or mixed?",
    "job_frequency": "How often do your data jobs run (e.g. hourly, daily, weekly)?",
    "expected_job_duration_hours": "How long does a typical job run take?",
    "sql_pct": "What share of your transformation logic is SQL vs Python/Scala (approximate percentage)?",
    "num_bi_users": "How many BI analysts or data consumers will query the data?",
    "needs_ml_genai": "Does this use case involve machine learning, model training, or GenAI?",
    "ml_components": "Which ML components do you need? (e.g. model training, model serving, feature store, vector search)",
}

_MORE_DETAILS_INTRO = (
    "To build an accurate architecture and pricing estimate, "
    "we need a few more details — please answer whichever you can:\n\n"
)


def _check_completeness(context: CollectedContext, llm: LLMProvider) -> tuple[bool, list[str]]:
    """Ask the LLM to validate completeness against the structured JSON fields.

    Secondary check after the context extractor reports "complete". It validates field
    presence rather than reading the conversation, so it catches cases where the
    extractor inferred completion from tone rather than data.

    Returns:
        (is_complete, missing_fields), where missing_fields lists field names still needed.
    """
    prompt = COMPLETENESS_CHECK_PROMPT.format(
        path_label=context.path_label or "Custom Mode",
        path_id=context.path_id or "custom",
        collected_context_json=json.dumps(
            {k: v for k, v in context.model_dump().items() if v is not None and v != "" and v != []},
            indent=2,
        ),
    )
    raw = llm.complete([{"role": "user", "content": prompt}], step="completeness-check")
    logger.debug("Completeness check response: %s", raw[:300])

    try:
        data = json.loads(extract_json(raw))
        return bool(data.get("complete")), data.get("missing", [])
    except (json.JSONDecodeError, ValueError):
        logger.warning("Completeness check parse failed — treating as incomplete")
        return False, []


def _to_question(field: str) -> str:
    return _MISSING_FIELD_QUESTIONS.get(field, f"Could you tell me more about your {field.replace('_', ' ')}?")


def intake(state: LakePlanState, runtime: Runtime[LakePlanContext]) -> Command[Literal["architect", "respond"]]:
    """Interview the customer until there is enough to design an architecture.

    Hands off to architect when both the context extractor and the completeness check
    agree the requirements are complete. Takes the conversation back from architect
    (`reopen="rethink"`, after repeated rejections) or planner (`reopen="scenario_change"`).
    """
    if state.get("sender"):
        return _reopen(state)

    llm = runtime.context.llm
    context = state["context"]
    if not context.path_id:
        return reply(state, "intake", ChatResponse(answer=(
            "Please select a path first — DWH Migration, Cloud Platform Migration, "
            "Real-Time Use Case, ML or GenAI Use Case, or Custom Mode."
        )))

    action, data = extract_context_updates(
        message=state["message"],
        history=state.get("history", []),
        context=context,
        llm=llm,
    )
    if data.get("extracted"):
        context = context.merge(data["extracted"])

    if action == "ask":
        questions = data.get("questions", "Could you tell me more about your requirements?")
        return reply(state, "intake", ChatResponse(answer=_MORE_DETAILS_INTRO + questions), context=context)

    complete, missing = _check_completeness(context, llm)
    if complete:
        dbg.add("Completeness check", "Passed — enough information to propose architecture", "extraction")
    else:
        dbg.add("Completeness check", f"Missing: {', '.join(missing)}", "extraction")

    if not complete and missing:
        questions = "\n".join(f"- {_to_question(f)}" for f in missing)
        return reply(state, "intake", ChatResponse(answer=_MORE_DETAILS_INTRO + questions), context=context)

    logger.info("Context complete — handing off to the architect")
    return hand_off(state, "intake", "architect", "Requirements complete", context=context)


def _reopen(state: LakePlanState) -> Command:
    """Take the conversation back from a later agent and ask what needs to change."""
    if state.get("reopen") == "scenario_change":
        # Keep volume and source details, drop the architecture.
        # model_copy because merge() ignores None values.
        context = state["context"].model_copy(
            update={"proposed_architecture": None, "architecture_approved": False}
        )
        return reply(state, "intake", ChatResponse(answer=(
            "Sure — I've kept your existing configuration details. "
            "What would you like to change?"
        )), context=context, rejections=0)

    return reply(state, "intake", ChatResponse(answer=(
        "It seems like the architecture needs significant rethinking. "
        "Let me ask a few more targeted questions to better understand your requirements.\n\n"
        "What is the most important aspect that the current proposal doesn't address correctly?"
    )), rejections=0)
