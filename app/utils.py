"""Shared helpers used across multiple agent modules."""

from __future__ import annotations

import re

FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> str:
    """Strip markdown fences, surrounding prose and // comments; return the JSON substring."""
    text = text.strip()
    match = FENCE_RE.search(text)
    if match:
        return _strip_line_comments(match.group(1).strip())
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        return _strip_line_comments(text[start : end + 1])
    return text


def _strip_line_comments(text: str) -> str:
    """Remove // comments, which smaller models sometimes add to JSON. Strings are left alone."""
    if "//" not in text:
        return text
    out: list[str] = []
    in_string = escaped = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
            out.append(ch)
        elif text.startswith("//", i):
            newline = text.find("\n", i)
            if newline == -1:
                break
            i = newline
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


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


def as_text(value: object) -> str:
    """Return an LLM JSON field that should be text as a string.

    Smaller models sometimes answer with a list of questions where the prompt asks
    for one string. A list becomes one bullet per line.
    """
    if value is None:
        return ""
    if isinstance(value, list):
        items = (str(item).strip().lstrip("-• ").strip() for item in value)
        return "\n".join(f"- {item}" for item in items if item)
    return str(value)
