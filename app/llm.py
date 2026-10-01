"""LLM provider abstraction. Swap Databricks ↔ Anthropic via LLM_PROVIDER env var."""

import logging
import os
from abc import ABC, abstractmethod

from app import debug_collector

logger = logging.getLogger(__name__)


def _truncate(text: str, n: int = 200) -> str:
    return text[:n] + "…" if len(text) > n else text


class LLMProvider(ABC):
    """Abstract base class for LLM inference providers.

    Concrete implementations wrap a vendor SDK and expose a single
    `complete` method so the rest of the codebase stays provider-agnostic.
    """

    @abstractmethod
    def complete(self, messages: list[dict], system: str | None = None, step: str = "") -> str:
        """Send messages and return the assistant reply as a string.

        step: optional label logged at INFO to identify which pipeline stage is calling.
        """


class DatabricksLLMProvider(LLMProvider):
    """LLM provider backed by a Databricks model-serving endpoint.

    Uses the OpenAI-compatible REST API at the workspace's /serving-endpoints base URL.

    Attributes:
        model: The serving endpoint name (from DATABRICKS_LLM_ENDPOINT env var).
    """

    def __init__(self) -> None:
        """Reads DATABRICKS_HOST, DATABRICKS_TOKEN, and DATABRICKS_LLM_ENDPOINT from env."""
        host = os.environ["DATABRICKS_HOST"].rstrip("/")
        token = os.environ["DATABRICKS_TOKEN"]
        self.model = os.environ["DATABRICKS_LLM_ENDPOINT"]

        from openai import OpenAI
        self._client = OpenAI(
            base_url=f"{host}/serving-endpoints",
            api_key=token,
        )

    def complete(self, messages: list[dict], system: str | None = None, step: str = "") -> str:
        """See base class."""
        _log_llm_call(step, messages, system)
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)
        response = self._client.chat.completions.create(
            model=self.model,
            messages=full_messages,
        )
        reply = response.choices[0].message.content or ""
        logger.debug("[%s] LLM reply: %s", step or "llm", _truncate(reply, 300))
        return reply


class AnthropicLLMProvider(LLMProvider):
    """LLM provider backed by the Anthropic Messages API.

    Attributes:
        model: Model ID from ANTHROPIC_MODEL env var; defaults to 'claude-opus-4-5'.
    """

    def __init__(self) -> None:
        """Reads ANTHROPIC_API_KEY and optionally ANTHROPIC_MODEL from env."""
        import anthropic
        self._client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self.model = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-5")

    def complete(self, messages: list[dict], system: str | None = None, step: str = "") -> str:
        """See base class."""
        _log_llm_call(step, messages, system)
        kwargs: dict = dict(model=self.model, max_tokens=4096, messages=messages)
        if system:
            kwargs["system"] = system
        response = self._client.messages.create(**kwargs)
        block = response.content[0]
        reply = block.text if hasattr(block, "text") else str(block)
        logger.debug("[%s] LLM reply: %s", step or "llm", _truncate(reply, 300))
        return reply


def _log_llm_call(step: str, messages: list[dict], system: str | None) -> None:
    """Log an outgoing LLM call to the Python logger and the debug collector."""
    label = step or "llm"
    first_msg = messages[0]["content"] if messages else ""
    logger.info("[%s] → LLM call | prompt: %s", label, _truncate(first_msg, 120))
    if system:
        logger.debug("[%s] system: %s", label, _truncate(system, 200))
    for i, m in enumerate(messages):
        logger.debug("[%s] msg[%d] role=%s: %s", label, i, m["role"], _truncate(m["content"], 300))
    debug_collector.add(
        label=label,
        detail=_truncate(first_msg, 150),
        step_type="llm",
        full_detail=first_msg,
    )


def get_llm_provider() -> LLMProvider:
    provider = os.environ.get("LLM_PROVIDER", "databricks").lower()
    if provider == "anthropic":
        return AnthropicLLMProvider()
    if provider == "databricks":
        return DatabricksLLMProvider()
    raise ValueError(f"Unknown LLM_PROVIDER: {provider!r}. Use 'databricks' or 'anthropic'.")
