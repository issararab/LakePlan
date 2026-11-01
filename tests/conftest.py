"""Shared pytest setup. Loaded before any test module, so it runs before LangGraph is imported."""

import os

# Run graph checkpoints in strict mode, the future LangGraph default: only classes named in
# LakePlanState's type hints are restored. test_checkpoints_only_hold_allowlisted_types
# fails if anything else ends up in the state.
os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
