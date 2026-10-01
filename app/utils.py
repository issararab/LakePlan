"""Shared helpers used across multiple agent modules."""

from __future__ import annotations

import re

FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> str:
    """Strip markdown fences and surrounding prose; return the JSON substring."""
    text = text.strip()
    match = FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        return text[start : end + 1]
    return text


def format_history(
    history: list[dict],
    max_messages: int = 10,
    truncate_at: int = 600,
) -> str:
    """Render recent conversation history as a labelled string for LLM prompts."""
    if not history:
        return "(no prior conversation)"
    lines = []
    for m in history[-max_messages:]:
        label = "User" if m["role"] == "user" else "Assistant"
        content = m["content"]
        if m["role"] == "assistant" and len(content) > truncate_at:
            content = content[:truncate_at] + " ... [truncated]"
        lines.append(f"{label}: {content}")
    return "\n".join(lines)
