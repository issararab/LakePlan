"""Tests for LLM replies that are valid JSON but not quite the shape the prompt asked for.

Seen with qwen2.5:7b on Ollama: "questions" comes back as a list instead of a string
(which crashed /chat with a TypeError), and JSON comes back with // comments (which
failed to parse, so the raw JSON was shown to the customer).

Run from the repo root:  python -m pytest tests
"""

import json

import pytest

from app.context_extractor import extract_context_updates
from app.llm import LLMProvider
from app.planner import _parse_plan
from app.schemas import CollectedContext
from app.utils import as_text, extract_json


class _FixedReply(LLMProvider):
    def __init__(self, reply: str) -> None:
        self._reply = reply

    def complete(self, messages, system=None, step=""):
        return self._reply


def test_context_extractor_turns_a_list_of_questions_into_text():
    llm = _FixedReply(json.dumps({
        "action": "ask",
        "questions": ["How many tables?", "- What is the daily volume?"],
        "extracted": {"source_system": "SQL Server"},
    }))
    context = CollectedContext(path_id="dwh-migration", path_label="DWH Migration")

    action, data = extract_context_updates("We use SQL Server", [], context, llm)

    assert action == "ask"
    assert data["questions"] == "- How many tables?\n- What is the daily volume?"
    assert data["extracted"] == {"source_system": "SQL Server"}


def test_planner_turns_a_list_of_questions_into_text():
    plan = _parse_plan(json.dumps({"action": "ask", "questions": ["Which tier?", "Which region?"]}))

    assert plan.questions == "- Which tier?\n- Which region?"


@pytest.mark.parametrize("value, expected", [
    ("Already a string", "Already a string"),
    (None, ""),
    (["one", "", "  • two "], "- one\n- two"),
    (3, "3"),
])
def test_as_text(value, expected):
    assert as_text(value) == expected


def test_json_with_line_comments_still_parses():
    """Seen with qwen2.5:7b: `"historical_volume_gb": 2048,  // 2 TB = 2048 GB`."""
    llm = _FixedReply(
        '{\n'
        '  "action": "complete",\n'
        '  "extracted": {\n'
        '    "historical_volume_gb": 2048,  // 2 TB = 2048 GB\n'
        '    "source_system": "SQL Server" // from the first message\n'
        '  },\n'
        '  "questions": ""\n'
        '}'
    )
    context = CollectedContext(path_id="dwh-migration", path_label="DWH Migration")

    action, data = extract_context_updates("2 TB from SQL Server", [], context, llm)

    assert action == "complete"
    assert data["extracted"] == {"historical_volume_gb": 2048, "source_system": "SQL Server"}


@pytest.mark.parametrize("raw, expected", [
    ('{"url": "https://adb-1.azuredatabricks.net"}', {"url": "https://adb-1.azuredatabricks.net"}),
    ('{"a": "say \\"//hi\\"", // note\n "b": 1}', {"a": 'say "//hi"', "b": 1}),
    ('```json\n{"a": 1 // one\n}\n```', {"a": 1}),
])
def test_comment_stripping_leaves_strings_alone(raw, expected):
    assert json.loads(extract_json(raw)) == expected
