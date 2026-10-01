"""Orchestrates the full LakePlan agent request flow."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app import debug_collector as dbg
from app.architecture_generator import adjust_architecture, generate_architecture
from app.context_extractor import extract_context_updates
from app.duckdb_client import execute_query
from app.llm import LLMProvider, get_llm_provider
from app.planner import QuerySpec, plan
from app.prompts import COMPLETENESS_CHECK_PROMPT, SYSTEM_PROMPT, build_synthesis_prompt
from app.schemas import (
    AgentPhase,
    AgentState,
    ChatResponse,
    CollectedContext,
    QueryResult,
)
from app.sql_generator import generate_sql
from app.sql_validator import SQLValidationError
from app.utils import extract_json

logger = logging.getLogger(__name__)

_MAX_HISTORY = 20
_MAX_APPROVAL_REJECTIONS = 3

# Keywords that signal approval / rejection (checked before any LLM call)
_APPROVE_WORDS = {
    "yes", "approve", "approved", "correct", "perfect", "looks good",
    "proceed", "go ahead", "that's right", "that is right", "confirmed",
    "agree", "accepted", "accept", "fine", "ok", "okay",
}
_REJECT_WORDS = {
    "no", "change", "adjust", "modify", "wrong", "different", "instead",
    "update", "revise", "rethink", "incorrect", "not right", "but",
    "however", "actually", "wait", "hold on",
}


def _whole_word_pattern(phrases: set[str]) -> re.Pattern[str]:
    """Compile a regex matching any phrase as whole words, so 'no' does not match 'now'."""
    alternatives = "|".join(
        re.escape(p).replace(r"\ ", r"\s+")
        for p in sorted(phrases, key=len, reverse=True)
    )
    return re.compile(rf"\b(?:{alternatives})\b")


_APPROVE_RE = _whole_word_pattern(_APPROVE_WORDS)
_REJECT_RE = _whole_word_pattern(_REJECT_WORDS)


# ---------------------------------------------------------------------------
# Human-readable fallback questions for completeness-check field names.
# Used when _check_completeness returns missing fields that need to be shown
# to the customer — raw field names must never be exposed.
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Helpers shared with old pipeline
# ---------------------------------------------------------------------------

def _rows_to_compact_json(rows: list[dict[str, Any]], max_chars: int = 6000) -> str:
    """Serialize query rows to JSON, truncating at max_chars to protect synthesis context budget."""
    text = json.dumps(rows, default=str)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... (truncated)"
    return text


def _format_query_results(results: list[QueryResult]) -> str:
    """Format a list of QueryResults into a readable text block for the synthesis prompt."""
    parts = []
    for r in results:
        rows_text = _rows_to_compact_json(r.rows) if r.rows else "(no rows returned)"
        parts.append(f"Query name: {r.name}\nSQL:\n{r.sql}\nRows:\n{rows_text}")
    return "\n\n---\n\n".join(parts)


def _run_query_spec(spec: QuerySpec, llm: LLMProvider) -> QueryResult | None:
    """Generate and execute SQL for a single QuerySpec.

    Returns None if SQL generation or validation fails; returns a QueryResult with
    empty rows if execution fails after successful SQL generation.
    """
    try:
        sql = generate_sql(spec.description, llm)
    except SQLValidationError as exc:
        logger.warning("SQL validation failed for %r: %s", spec.name, exc)
        return None
    except Exception as exc:
        logger.exception("SQL generation error for %r: %s", spec.name, exc)
        return None
    try:
        rows = execute_query(sql)
    except Exception as exc:
        logger.warning("Query execution failed for %r: %s\nSQL: %s", spec.name, exc, sql)
        return QueryResult(name=spec.name, sql=sql, rows=[])
    return QueryResult(name=spec.name, sql=sql, rows=rows)


# ---------------------------------------------------------------------------
# Approval detection
# ---------------------------------------------------------------------------

def _detect_approval(message: str) -> str | None:
    """
    Returns "approve", "reject", or None (ambiguous) based on keyword scan.
    Fast check — no LLM needed for clear cases.
    """
    # Normalise curly apostrophes so "that’s right" matches "that's right"
    lower = message.lower().strip().replace("’", "'")

    approve_hit = bool(_APPROVE_RE.search(lower))
    reject_hit = bool(_REJECT_RE.search(lower))

    # Clear approval with no reject signals
    if approve_hit and not reject_hit:
        return "approve"

    # Clear rejection
    if reject_hit:
        return "reject"

    # Very short affirmative messages not caught by the word list above
    if lower in {"y", "yep", "yup", "sure", "great"}:
        return "approve"

    return None


# ---------------------------------------------------------------------------
# Completeness check via LLM
# ---------------------------------------------------------------------------

def _check_completeness(context: CollectedContext, llm: LLMProvider) -> tuple[bool, list[str]]:
    """Ask the LLM to validate completeness against the structured JSON fields.

    Secondary check after the context-extractor LLM reports "complete". Strictly
    validates field presence rather than reading conversation text, so it catches
    cases where the extractor inferred completion from tone rather than data.

    Returns:
        A tuple of (is_complete, missing_fields) where missing_fields is a list of
        field names that must still be collected.
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


# ---------------------------------------------------------------------------
# PricingAgent
# ---------------------------------------------------------------------------

class PricingAgent:
    """Stateful agent managing a single customer pricing session.

    Advances through four phases: context collection → architecture proposal →
    architecture approval → pricing. One instance per browser session.

    Attributes:
        _llm: LLM provider used for all inference calls.
        _history: Sliding window of conversation messages (capped at _MAX_HISTORY).
        _state: Current phase, collected context, and session mode.
    """

    def __init__(self, llm: LLMProvider | None = None) -> None:
        """Initialises the agent with a shared LLM provider instance."""
        self._llm = llm or get_llm_provider()
        self._history: list[dict] = []
        self._state = AgentState()

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

        response = self._route(message)
        self._append_history("user", message)
        self._append_history("assistant", response.answer)

        if steps:
            response.debug_steps = steps
            dbg.stop()

        return response

    # ------------------------------------------------------------------
    # Phase router
    # ------------------------------------------------------------------

    def _route(self, message: str) -> ChatResponse:
        """Dispatch the message to the handler for the current phase."""
        phase = self._state.phase
        path = self._state.context.path_id or "unknown"
        logger.info("Agent phase=%s path=%s mode=%s | message: %s",
                    phase.value, path, self._state.mode,
                    (message[:80] + "…") if len(message) > 80 else message)
        dbg.add("Phase", phase.value, "phase")

        if phase == AgentPhase.CONTEXT_COLLECTION:
            return self._handle_context_collection(message)

        if phase == AgentPhase.ARCHITECTURE_PROPOSAL:
            return self._generate_and_present_architecture()

        if phase == AgentPhase.ARCHITECTURE_APPROVAL:
            return self._handle_approval(message)

        if phase == AgentPhase.PRICING:
            return self._handle_pricing(message)

        # Fallback
        return ChatResponse(answer="I'm not sure where we are. Let's start over — please describe your use case.")

    # ------------------------------------------------------------------
    # Phase: context collection
    # ------------------------------------------------------------------

    def _handle_context_collection(self, message: str) -> ChatResponse:
        """Extract context from the message and ask follow-up questions or advance to architecture.

        Runs two completeness checks: the context-extractor LLM (conversation-level) and
        a secondary structured check against the JSON fields. Both must agree before
        transitioning to architecture proposal.
        """
        # If path not set yet (edge case: message before path selected)
        if not self._state.context.path_id:
            return ChatResponse(
                answer=(
                    "Please select a path first — DWH Migration, Cloud Platform Migration, "
                    "Real-Time Use Case, ML or GenAI Use Case, or Custom Mode."
                ),
                phase=AgentPhase.CONTEXT_COLLECTION,
            )

        action, data = extract_context_updates(
            message=message,
            history=self._history,
            context=self._state.context,
            llm=self._llm,
        )

        # Merge extracted fields into context
        extracted = data.get("extracted", {})
        if extracted:
            self._state.context = self._state.context.merge(extracted)

        if action == "ask":
            questions = data.get("questions", "Could you tell me more about your requirements?")
            answer = (
                "To build an accurate architecture and pricing estimate, "
                "we need a few more details — please answer whichever you can:\n\n"
                + questions
            )
            return ChatResponse(
                answer=answer,
                response_type="message",
                phase=AgentPhase.CONTEXT_COLLECTION,
            )

        # action == "complete" — run a secondary completeness check
        complete, missing = _check_completeness(self._state.context, self._llm)
        if complete:
            dbg.add("Completeness check", "Passed — enough information to propose architecture", "extraction")
        else:
            dbg.add("Completeness check", f"Missing: {', '.join(missing)}", "extraction")

        if not complete and missing:
            def _to_question(field: str) -> str:
                fallback = f"Could you tell me more about your {field.replace('_', ' ')}?"
                return _MISSING_FIELD_QUESTIONS.get(field, fallback)

            questions = "\n".join(f"- {_to_question(f)}" for f in missing)
            return ChatResponse(
                answer=(
                    "To build an accurate architecture and pricing estimate, "
                    "we need a few more details — please answer whichever you can:\n\n"
                    + questions
                ),
                response_type="message",
                phase=AgentPhase.CONTEXT_COLLECTION,
            )

        # Enough info — move to architecture generation
        logger.info("Context complete — transitioning to architecture proposal")
        dbg.add("Transition", "context_collection → architecture_proposal", "transition")
        self._state.phase = AgentPhase.ARCHITECTURE_PROPOSAL
        return self._generate_and_present_architecture()

    # ------------------------------------------------------------------
    # Phase: architecture proposal (transient — always generates and moves on)
    # ------------------------------------------------------------------

    def _generate_and_present_architecture(self) -> ChatResponse:
        """Generate an architecture proposal and transition to the approval phase."""
        architecture = generate_architecture(self._state.context, self._llm, mode=self._state.mode)
        self._state.context = self._state.context.merge({"proposed_architecture": architecture})
        self._state.phase = AgentPhase.ARCHITECTURE_APPROVAL
        self._state.consecutive_approval_rejections = 0
        dbg.add("Transition", "architecture_proposal → architecture_approval (waiting for user)", "transition")

        return ChatResponse(
            answer=architecture,
            response_type="architecture_proposal",
            phase=AgentPhase.ARCHITECTURE_APPROVAL,
        )

    # ------------------------------------------------------------------
    # Phase: architecture approval loop
    # ------------------------------------------------------------------

    def _handle_approval(self, message: str) -> ChatResponse:
        """Interpret user response as approval or change request and act accordingly.

        Uses a keyword scan first; falls back to LLM classification for ambiguous messages.
        Tracks consecutive rejections and resets to context collection after
        _MAX_APPROVAL_REJECTIONS to avoid an infinite loop. A clear approval always
        proceeds to pricing, even after the rejection limit is reached.
        """
        decision = _detect_approval(message)

        if decision == "approve":
            dbg.add("Approval", "Architecture approved by user", "transition")
            dbg.add("Transition", "architecture_approval → pricing", "transition")
            self._state.context = self._state.context.merge({"architecture_approved": True})
            self._state.phase = AgentPhase.PRICING
            return self._calculate_pricing()

        # Stuck-loop safety valve — checked after approval so the user can still accept
        if self._state.consecutive_approval_rejections >= _MAX_APPROVAL_REJECTIONS:
            self._state.phase = AgentPhase.CONTEXT_COLLECTION
            self._state.consecutive_approval_rejections = 0
            return ChatResponse(
                answer=(
                    "It seems like the architecture needs significant rethinking. "
                    "Let me ask a few more targeted questions to better understand your requirements.\n\n"
                    "What is the most important aspect that the current proposal doesn't address correctly?"
                ),
                response_type="message",
                phase=AgentPhase.CONTEXT_COLLECTION,
            )

        if decision == "reject":
            self._state.consecutive_approval_rejections += 1
            updated = adjust_architecture(
                original_architecture=self._state.context.proposed_architecture or "",
                user_feedback=message,
                context=self._state.context,
                llm=self._llm,
            )
            self._state.context = self._state.context.merge({"proposed_architecture": updated})
            return ChatResponse(
                answer=updated,
                response_type="architecture_proposal",
                phase=AgentPhase.ARCHITECTURE_APPROVAL,
            )

        # Ambiguous — ask the LLM to interpret
        interpretation = self._llm.complete(
            [{"role": "user", "content": (
                f"The user was shown an architecture proposal and responded: \"{message}\"\n\n"
                "Is this an approval, a request for changes, or something else?\n"
                "Reply with ONLY one word: 'approve', 'reject', or 'unclear'."
            )}],
            step="approval-classifier",
        ).strip().lower()

        if "approve" in interpretation:
            self._state.context = self._state.context.merge({"architecture_approved": True})
            self._state.phase = AgentPhase.PRICING
            return self._calculate_pricing()

        if "reject" in interpretation or "unclear" in interpretation:
            self._state.consecutive_approval_rejections += 1
            return ChatResponse(
                answer=(
                    "I want to make sure I understand correctly — would you like to approve this architecture "
                    "and proceed to pricing, or would you like to make some changes first?\n\n"
                    "If you'd like changes, please describe what you'd like to adjust."
                ),
                response_type="architecture_proposal",
                phase=AgentPhase.ARCHITECTURE_APPROVAL,
            )

        return ChatResponse(
            answer="Could you confirm — do you approve this architecture, or would you like to adjust something?",
            response_type="architecture_proposal",
            phase=AgentPhase.ARCHITECTURE_APPROVAL,
        )

    # ------------------------------------------------------------------
    # Phase: pricing
    # ------------------------------------------------------------------

    def _execute_pricing(self, question: str, step: str) -> ChatResponse:
        """Plan queries, execute them, and synthesise a pricing response."""
        pricing_plan = plan(self._history, question, self._llm)

        if pricing_plan.action == "ask":
            return ChatResponse(
                answer=pricing_plan.questions or "",
                response_type="message",
                phase=AgentPhase.PRICING,
            )

        query_results: list[QueryResult] = []
        for spec in pricing_plan.queries:
            result = _run_query_spec(spec, self._llm)
            if result is not None:
                query_results.append(result)
                dbg.add(f"SQL: {spec.name}", f"{len(result.rows)} row(s) returned", "sql")
            else:
                dbg.add(f"SQL: {spec.name}", "Query failed or returned no data", "error")

        if not query_results:
            return ChatResponse(
                answer=(
                    "I wasn't able to retrieve pricing data. "
                    "Please try rephrasing, or be more specific about the workload type."
                ),
                response_type="message",
                phase=AgentPhase.PRICING,
            )

        architecture_context = self._state.context.proposed_architecture or "(architecture not recorded)"
        synthesis_prompt = build_synthesis_prompt(
            question=question,
            architecture_context=architecture_context[:2000],
            query_results=_format_query_results(query_results),
            mode=self._state.mode,
        )
        answer = self._llm.complete(
            [{"role": "user", "content": synthesis_prompt}],
            system=SYSTEM_PROMPT,
            step=step,
        )

        return ChatResponse(
            answer=answer,
            response_type="pricing",
            phase=AgentPhase.PRICING,
            query_results=query_results,
        )

    def _calculate_pricing(self) -> ChatResponse:
        """Build the pricing question from collected context and run the pricing pipeline."""
        return self._execute_pricing(
            self._build_pricing_question(self._state.context),
            step="pricing-synthesis",
        )

    def _handle_pricing(self, message: str) -> ChatResponse:
        """Handle follow-up questions or input changes while in PRICING phase."""
        lower = message.lower()
        change_signals = [
            "change", "update", "different", "instead", "what if", "what about",
            "switch", "modify", "alter", "new scenario", "start over",
        ]

        if any(s in lower for s in change_signals):
            # Partial reset — keep volume/source info, clear architecture.
            # Use model_copy so that None is applied (merge() silently drops None values).
            self._state.context = self._state.context.model_copy(
                update={"proposed_architecture": None, "architecture_approved": False}
            )
            self._state.phase = AgentPhase.CONTEXT_COLLECTION
            self._state.consecutive_approval_rejections = 0
            return ChatResponse(
                answer=(
                    "Sure — I've kept your existing configuration details. "
                    "What would you like to change?"
                ),
                response_type="message",
                phase=AgentPhase.CONTEXT_COLLECTION,
            )

        return self._execute_pricing(message, step="pricing-followup")

    @staticmethod
    def _build_pricing_question(context: CollectedContext) -> str:
        """Construct a self-contained pricing question from the collected context."""
        parts = [f"Calculate Databricks pricing for a {context.path_label} deployment on Azure Germany West Central."]

        if context.source_system:
            parts.append(f"Source: {context.source_system}.")
        if context.historical_volume_gb:
            parts.append(f"Historical bulk data volume: {context.historical_volume_gb / 1024:.1f} TB.")
        if context.daily_processed_volume_gb:
            parts.append(f"Daily processed volume: {context.daily_processed_volume_gb} GB.")
        if context.daily_write_volume_gb:
            parts.append(f"Daily write volume: {context.daily_write_volume_gb} GB.")
        if context.latency_requirement:
            parts.append(f"Latency: {context.latency_requirement}.")
        if context.job_frequency:
            parts.append(f"Job frequency: {context.job_frequency}.")
        if context.expected_job_duration_hours:
            parts.append(f"Expected job duration: {context.expected_job_duration_hours} hours.")
        if context.workload_stability:
            parts.append(f"Workload stability: {context.workload_stability}.")
        if context.sql_pct is not None:
            parts.append(f"SQL share: {int(context.sql_pct * 100)}%.")
        if context.num_bi_users:
            parts.append(f"Number of BI/analyst users: {context.num_bi_users}.")
        if context.peak_concurrent_queries:
            parts.append(f"Peak concurrent queries (user-stated): {context.peak_concurrent_queries}.")
        if context.needs_ml_genai and context.ml_components:
            parts.append(f"ML/GenAI components: {', '.join(context.ml_components)}.")
        if context.daily_llm_requests:
            monthly = context.daily_llm_requests * 30
            parts.append(f"LLM requests: {context.daily_llm_requests}/day ({monthly:,}/month) [user-stated].")
        if context.avg_llm_input_tokens_per_request:
            parts.append(f"Average LLM input tokens per request: {context.avg_llm_input_tokens_per_request} [user-stated].")
        if context.avg_llm_output_tokens_per_request:
            parts.append(f"Average LLM output tokens per request: {context.avg_llm_output_tokens_per_request} [user-stated].")
        if context.num_vectors:
            parts.append(f"Vector Search index size: {context.num_vectors:,} vectors [user-stated].")

        parts.append(
            "Include: DBU estimate, VM compute cost range, storage estimate, "
            "Serverless vs Classic trade-off, CUD options, and rough TCO comparison."
        )

        return " ".join(parts)


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
