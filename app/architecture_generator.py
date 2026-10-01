"""
Generate and adjust Databricks solution architectures.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app import debug_collector
from app.llm import LLMProvider
from app.prompts import ARCHITECTURE_ADJUSTMENT_PROMPT, build_architecture_prompt
from app.rules import apply_rules

if TYPE_CHECKING:
    from app.schemas import CollectedContext

logger = logging.getLogger(__name__)


def _context_to_json(context: "CollectedContext") -> str:
    """Serialize a CollectedContext to compact JSON, omitting null and empty fields."""
    data = context.model_dump()
    # Drop empty/None to keep the prompt concise
    clean = {k: v for k, v in data.items() if v is not None and v != "" and v != []}
    return json.dumps(clean, indent=2)


def generate_architecture(context: "CollectedContext", llm: LLMProvider, mode: str = "user") -> str:
    """
    Apply rules and generate a solution architecture proposal.
    Returns the architecture as a formatted string.
    """
    recommendations = apply_rules(context)

    ing = recommendations.get("ingestion", {})
    proc = recommendations.get("processing", {})
    trans = recommendations.get("transformation", {})
    debug_collector.add("Rules: ingestion",       f"{ing.get('pattern','?')} → {ing.get('connector','?')}", "rule")
    debug_collector.add("Rules: ETL compute",     proc.get("compute_type", "?"), "rule")
    debug_collector.add("Rules: ETL transform",   trans.get("etl_compute", "?"), "rule")
    debug_collector.add("Rules: serving layer",   trans.get("serving_layer", "?"), "rule")
    ml = recommendations.get("ml_components", [])
    debug_collector.add("Rules: ML/GenAI", f"components: {ml}" if ml else "not included", "rule")

    prompt = build_architecture_prompt(
        path_label=context.path_label or "Custom Mode",
        collected_context_json=_context_to_json(context),
        recommendations_json=json.dumps(recommendations, indent=2),
        mode=mode,
    )

    architecture = llm.complete([{"role": "user", "content": prompt}], step="architecture-generate")
    logger.debug("Architecture generated (%d chars)", len(architecture))
    return architecture


def adjust_architecture(
    original_architecture: str,
    user_feedback: str,
    context: "CollectedContext",
    llm: LLMProvider,
) -> str:
    """
    Adjust an existing architecture based on user feedback.
    Returns the updated architecture as a formatted string.
    """
    recommendations = apply_rules(context)

    prompt = ARCHITECTURE_ADJUSTMENT_PROMPT.format(
        path_label=context.path_label or "Custom Mode",
        original_architecture=original_architecture,
        user_feedback=user_feedback,
        collected_context_json=_context_to_json(context),
        recommendations_json=json.dumps(recommendations, indent=2),
    )

    updated = llm.complete([{"role": "user", "content": prompt}], step="architecture-adjust")
    logger.debug("Architecture adjusted (%d chars)", len(updated))
    return updated
