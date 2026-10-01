"""
Extract structured context fields from a user message.
Returns only fields the user explicitly stated — no hallucination.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app import debug_collector
from app.llm import LLMProvider
from app.prompts import CONTEXT_COLLECTION_PROMPT, PATH_CONTEXTS
from app.utils import extract_json, format_history

if TYPE_CHECKING:
    from app.schemas import CollectedContext

logger = logging.getLogger(__name__)


def _format_collected(context: "CollectedContext") -> str:
    """Render non-null, non-empty fields of a CollectedContext as human-readable text for the prompt."""
    data = context.model_dump()
    lines = []
    for k, v in data.items():
        if v is not None and v != "" and v != [] and v is not False:
            lines.append(f"  {k}: {v}")
    if not lines:
        return "  (nothing collected yet)"
    return "\n".join(lines)


def extract_context_updates(
    message: str,
    history: list[dict],
    context: "CollectedContext",
    llm: LLMProvider,
) -> tuple[str, dict]:
    """
    Ask the LLM to extract context fields from the message and decide if more
    questions are needed.

    Returns:
        (action, data) where action is "ask" or "complete", and data is a dict
        with keys:
          - "questions": str (when action == "ask")
          - "extracted": dict of CollectedContext field updates (always present)
    """
    path_id = context.path_id or "custom"
    path_label = context.path_label or "Custom Mode"
    path_context = PATH_CONTEXTS.get(path_id, PATH_CONTEXTS["custom"])

    prompt = CONTEXT_COLLECTION_PROMPT.format(
        path_label=path_label,
        path_context=path_context,
        collected_so_far=_format_collected(context),
        history=format_history(history, max_messages=8, truncate_at=400),
        message=message,
    )

    raw = llm.complete([{"role": "user", "content": prompt}], step="context-extractor")
    logger.debug("Context extractor raw response: %s", raw[:500])

    try:
        parsed = json.loads(extract_json(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Context extractor JSON parse failed: %s — treating as ask", exc)
        return "ask", {"questions": raw, "extracted": {}}

    action = parsed.get("action", "ask")
    extracted = parsed.get("extracted", {})
    questions = parsed.get("questions", "")

    # Sanitise extracted: remove null values and protect fields set from the API
    _PROTECTED = {"path_id", "path_label", "initial_description",
                  "proposed_architecture", "architecture_approved"}
    extracted = {k: v for k, v in extracted.items()
                 if v is not None and k not in _PROTECTED}

    if extracted:
        debug_collector.add(
            label="Extracted context",
            detail=", ".join(f"{k}={v}" for k, v in extracted.items()),
            step_type="extraction",
        )
    debug_collector.add(
        label=f"Context decision: {action}",
        detail=questions[:200] if action == "ask" else "Enough information collected",
        step_type="extraction",
    )

    return action, {"questions": questions, "extracted": extracted}
