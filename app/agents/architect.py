"""Architect agent: designs the Databricks architecture and gets it approved."""

from __future__ import annotations

import logging
import re

from app import debug_collector as dbg
from app.agents.base import Agent, Handoff, Reply, Session
from app.architecture_generator import adjust_architecture, generate_architecture
from app.schemas import AgentPhase, ChatResponse, CollectedContext

logger = logging.getLogger(__name__)

MAX_APPROVAL_REJECTIONS = 3

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


def detect_approval(message: str) -> str | None:
    """Return "approve", "reject", or None (ambiguous) based on a keyword scan.

    Fast check, no LLM needed for clear cases.
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


def pricing_brief(context: CollectedContext) -> str:
    """Write a self-contained pricing question from the collected context.

    This is the handoff note the architect gives the planner: everything needed to
    price the approved architecture, without reading the conversation.
    """
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


class ArchitectAgent(Agent):
    """Proposes an architecture, adjusts it on request, and waits for approval.

    Hands off to:
        planner: once the customer approves. Payload: question (the pricing brief),
            followup=False.
        intake: after MAX_APPROVAL_REJECTIONS change requests in a row, when the
            requirements need revisiting. Payload: reopen="rethink".

    Accepts handoffs from:
        intake: requirements are complete, so design a new architecture.
    """

    name = "architect"
    role = "Designs the Databricks architecture and gets the customer's approval."
    phase = AgentPhase.ARCHITECTURE_APPROVAL

    def run(self, message: str, session: Session, handoff: Handoff | None) -> Handoff | Reply:
        if handoff is not None or not session.state.context.proposed_architecture:
            return self._propose(session)
        return self._review(message, session)

    def _propose(self, session: Session) -> Reply:
        state = session.state
        architecture = generate_architecture(state.context, self.llm, mode=state.mode)
        state.context = state.context.merge({"proposed_architecture": architecture})
        state.consecutive_approval_rejections = 0
        return Reply(ChatResponse(answer=architecture, response_type="architecture_proposal"))

    def _review(self, message: str, session: Session) -> Handoff | Reply:
        """Interpret the customer's answer to a proposal as approval or a change request.

        Uses a keyword scan first and asks the LLM only for ambiguous messages. A clear
        approval always proceeds to pricing, even after the rejection limit is reached.
        """
        state = session.state
        decision = detect_approval(message)

        if decision == "approve":
            dbg.add("Approval", "Architecture approved by user", "transition")
            return self._approved(session)

        # Stuck-loop safety valve — checked after approval so the user can still accept
        if state.consecutive_approval_rejections >= MAX_APPROVAL_REJECTIONS:
            return Handoff(
                to="intake",
                reason=f"{MAX_APPROVAL_REJECTIONS} change requests in a row — requirements need revisiting",
                payload={"reopen": "rethink"},
            )

        if decision == "reject":
            state.consecutive_approval_rejections += 1
            updated = adjust_architecture(
                original_architecture=state.context.proposed_architecture or "",
                user_feedback=message,
                context=state.context,
                llm=self.llm,
            )
            state.context = state.context.merge({"proposed_architecture": updated})
            return Reply(ChatResponse(answer=updated, response_type="architecture_proposal"))

        # Ambiguous — ask the LLM to interpret
        interpretation = self.llm.complete(
            [{"role": "user", "content": (
                f"The user was shown an architecture proposal and responded: \"{message}\"\n\n"
                "Is this an approval, a request for changes, or something else?\n"
                "Reply with ONLY one word: 'approve', 'reject', or 'unclear'."
            )}],
            step="approval-classifier",
        ).strip().lower()

        if "approve" in interpretation:
            return self._approved(session)

        if "reject" in interpretation or "unclear" in interpretation:
            state.consecutive_approval_rejections += 1
            return Reply(ChatResponse(
                answer=(
                    "I want to make sure I understand correctly — would you like to approve this architecture "
                    "and proceed to pricing, or would you like to make some changes first?\n\n"
                    "If you'd like changes, please describe what you'd like to adjust."
                ),
                response_type="architecture_proposal",
            ))

        return Reply(ChatResponse(
            answer="Could you confirm — do you approve this architecture, or would you like to adjust something?",
            response_type="architecture_proposal",
        ))

    @staticmethod
    def _approved(session: Session) -> Handoff:
        state = session.state
        state.context = state.context.merge({"architecture_approved": True})
        return Handoff(
            to="planner",
            reason="Architecture approved — pricing it",
            payload={"question": pricing_brief(state.context), "followup": False},
        )
