"""Tests for the keyword-based architecture approval check in app/agent.py.

Run from the repo root:  python -m pytest tests
"""

import pytest

from app.agent import _detect_approval


@pytest.mark.parametrize("message", [
    # Reject words hidden inside other words: "no" in "now"/"know"/"nothing", "but" in "button"
    "Looks good, now proceed",
    "I know it's right, go ahead",
    "Fine, nothing to add",
    "Clicked the button, approved",
    # Message sent by the UI's "Approve architecture" button
    "Yes, the architecture looks correct. Please proceed to pricing.",
    # Curly apostrophe and extra whitespace inside a phrase
    "That’s right",
    "Looks  good",
    "OK",
    "yep",
])
def test_approval(message):
    assert _detect_approval(message) == "approve"


@pytest.mark.parametrize("message", [
    "No, change the ingestion to Fivetran",
    "Yes but use Fivetran",
    "This is incorrect",
    "That's not right",
    "Hold on, let me check with the team",
])
def test_rejection(message):
    assert _detect_approval(message) == "reject"


@pytest.mark.parametrize("message", [
    "hmm",
    "Can you explain the ingestion layer?",
    # "agree" used to match inside "disagree" and count as approval
    "I disagree",
])
def test_ambiguous_falls_through_to_llm(message):
    assert _detect_approval(message) is None
