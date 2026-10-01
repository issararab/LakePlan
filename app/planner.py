"""
Planning step: extract the pricing scenario from conversation, decide whether
to ask follow-up questions or run one or more DuckDB queries.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Literal

from app import debug_collector
from app.llm import LLMProvider
from app.prompts import PLANNING_PROMPT
from app.utils import extract_json, format_history

logger = logging.getLogger(__name__)


@dataclass
class QuerySpec:
    """A self-contained description of a single pricing query to execute.

    Attributes:
        name: Short identifier used to label the result (e.g. 'compute_pricing').
        description: Natural-language question passed to the SQL generator.
    """

    name: str
    description: str


@dataclass
class Plan:
    """Output of the planner LLM call: either ask for more information or run queries.

    Attributes:
        action: 'ask' to request more information from the user, 'query' to run DuckDB queries.
        questions: Follow-up text to show the user; populated when action is 'ask'.
        queries: Query specs to execute; populated when action is 'query'.
    """

    action: Literal["ask", "query"]
    questions: str | None = None
    queries: list[QuerySpec] = field(default_factory=list)


def _parse_plan(text: str) -> Plan:
    """Parse the planner LLM response into a Plan.

    Args:
        text: Raw LLM response expected to contain a JSON object with 'action' and
            either 'questions' or 'queries'.

    Returns:
        A Plan instance with action and the relevant payload populated.

    Raises:
        ValueError: If the JSON is malformed, the action is unrecognised, or action
            is 'query' but no valid query specs are present.
    """
    raw = extract_json(text)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Planner returned invalid JSON: {exc}\nRaw: {raw[:300]}") from exc

    action = data.get("action")
    if action == "ask":
        return Plan(action="ask", questions=str(data.get("questions", "")))
    if action == "query":
        specs = [
            QuerySpec(name=q["name"], description=q["description"])
            for q in data.get("queries", [])
            if q.get("name") and q.get("description")
        ]
        if not specs:
            raise ValueError("Planner returned 'query' action with no queries")
        return Plan(action="query", queries=specs)
    raise ValueError(f"Unknown planner action: {action!r}")


def plan(history: list[dict], message: str, llm: LLMProvider) -> Plan:
    """
    Ask the LLM to analyse the conversation and decide:
      - ask:   return clarifying questions
      - query: return list of self-contained query specs to run
    Falls back to a single query on parse failure.
    """
    prompt = PLANNING_PROMPT.format(
        history=format_history(history),
        message=message,
    )
    response = llm.complete([{"role": "user", "content": prompt}], step="planner")
    logger.debug("Planner raw response: %s", response[:500])

    try:
        result = _parse_plan(response)
    except ValueError as exc:
        logger.warning("Planner parse failed (%s) — falling back to single query", exc)
        result = Plan(
            action="query",
            queries=[QuerySpec(name="pricing_lookup", description=message)],
        )

    if result.action == "ask":
        debug_collector.add("Planner", f"Needs more info: {(result.questions or '')[:150]}", "plan")
    else:
        names = ", ".join(q.name for q in result.queries)
        debug_collector.add("Planner", f"{len(result.queries)} quer{'y' if len(result.queries)==1 else 'ies'}: {names}", "plan")

    return result
