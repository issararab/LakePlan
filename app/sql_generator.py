"""Generate DuckDB SQL from a natural-language pricing question."""

import re

from app.llm import LLMProvider
from app.prompts import SQL_GENERATION_PROMPT
from app.sql_validator import SQLValidationError, validate_and_sanitize


_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _extract_sql(text: str) -> str:
    """Strip markdown fences and prose; return the raw SQL."""
    text = text.strip()

    # Prefer an explicit fenced block
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()

    # No fence: find the first SELECT and take everything from there,
    # stopping at the first blank line (which typically separates SQL from prose).
    lower = text.lower()
    idx = lower.find("select")
    if idx != -1:
        candidate = text[idx:]
        sql_lines: list[str] = []
        for line in candidate.splitlines():
            if not line.strip() and sql_lines:
                break  # blank line signals end of SQL block
            sql_lines.append(line)
        return "\n".join(sql_lines).strip()

    return text


def generate_sql(question: str, llm: LLMProvider, max_retries: int = 2) -> str:
    """
    Ask the LLM for a DuckDB SELECT, validate it, and retry on validation failure.
    Returns a validated SQL string.
    Raises SQLValidationError if all retries fail.
    """
    prompt = SQL_GENERATION_PROMPT.format(question=question)
    messages = [{"role": "user", "content": prompt}]
    last_error: SQLValidationError | None = None

    for attempt in range(max_retries):
        raw = llm.complete(messages, step=f"sql-gen(attempt={attempt+1})")
        sql = _extract_sql(raw)
        try:
            return validate_and_sanitize(sql)
        except SQLValidationError as exc:
            last_error = exc
            # Feed the error back so the LLM can self-correct
            messages.append({"role": "assistant", "content": raw})
            messages.append({
                "role": "user",
                "content": (
                    f"That SQL failed validation: {exc}\n"
                    "Please fix it and return only the corrected SELECT query."
                ),
            })

    raise last_error  # type: ignore[misc]
