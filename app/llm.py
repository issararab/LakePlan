"""LLM provider abstraction. Pick a backend with the LLM_PROVIDER env var.

- databricks         Databricks model serving (default)
- anthropic          Anthropic Messages API
- openai_compatible  Any OpenAI-compatible server: self-hosted open-weight models
                     (Ollama, vLLM, LM Studio, llama.cpp) or a hosted API
"""

import logging
import os
from abc import ABC, abstractmethod

from app import debug_collector

logger = logging.getLogger(__name__)

# Placeholder key for local servers that ignore auth. The OpenAI SDK refuses an empty key.
_NO_API_KEY = "not-needed"


def _truncate(text: str, n: int = 200) -> str:
    return text[:n] + "…" if len(text) > n else text


def _strip_reasoning(text: str) -> str:
    """Drop the <think>…</think> block that reasoning models (DeepSeek-R1, Qwen3) emit before the answer.

    Callers parse replies as JSON or SQL, so leftover reasoning would break them.
    Some servers drop the opening tag, so everything up to the last closing tag is removed.
    """
    if "</think>" in text:
        return text.rsplit("</think>", 1)[1].lstrip()
    return text


class LLMProvider(ABC):
    """Abstract base class for LLM inference providers.

    Concrete implementations wrap a vendor SDK and expose a single
    `complete` method so the rest of the codebase stays provider-agnostic.
    """

    model: str = ""

    @abstractmethod
    def complete(self, messages: list[dict], system: str | None = None, step: str = "") -> str:
        """Send messages and return the assistant reply as a string.

        step: optional label logged at INFO to identify which pipeline stage is calling.
        """


class OpenAICompatibleLLMProvider(LLMProvider):
    """LLM provider for any server that implements the OpenAI Chat Completions API.

    This is how LakePlan runs on self-hosted open-weight models: point it at Ollama,
    vLLM, LM Studio or a llama.cpp server. It also works with OpenAI itself.

    Attributes:
        base_url: API root including the version path, e.g. 'http://localhost:11434/v1'.
            None means the OpenAI SDK default (api.openai.com).
        model: Model name as the server knows it, e.g. 'qwen2.5:7b' on Ollama.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        """Reads OPENAI_BASE_URL, OPENAI_API_KEY and OPENAI_MODEL from env for any argument not passed."""
        if model is None:
            model = os.environ.get("OPENAI_MODEL", "")
            if not model:
                raise ValueError("OPENAI_MODEL must be set when LLM_PROVIDER=openai_compatible")
        if base_url is None:
            base_url = os.environ.get("OPENAI_BASE_URL") or None
        if api_key is None:
            api_key = os.environ.get("OPENAI_API_KEY") or _NO_API_KEY
        self.model = model
        self.base_url = base_url

        from openai import OpenAI
        self._client = OpenAI(base_url=base_url, api_key=api_key)

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
        reply = _strip_reasoning(response.choices[0].message.content or "")
        logger.debug("[%s] LLM reply: %s", step or "llm", _truncate(reply, 300))
        return reply


class DatabricksLLMProvider(OpenAICompatibleLLMProvider):
    """LLM provider backed by a Databricks model-serving endpoint.

    Databricks serves an OpenAI-compatible API at the workspace's /serving-endpoints path,
    so this is the OpenAI-compatible provider with Databricks settings.

    Attributes:
        model: The serving endpoint name (from DATABRICKS_LLM_ENDPOINT env var).
    """

    def __init__(self) -> None:
        """Reads DATABRICKS_HOST, DATABRICKS_TOKEN, and DATABRICKS_LLM_ENDPOINT from env."""
        host = os.environ["DATABRICKS_HOST"].rstrip("/")
        super().__init__(
            base_url=f"{host}/serving-endpoints",
            api_key=os.environ["DATABRICKS_TOKEN"],
            model=os.environ["DATABRICKS_LLM_ENDPOINT"],
        )


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


_PROVIDERS: dict[str, type[LLMProvider]] = {
    "databricks": DatabricksLLMProvider,
    "anthropic": AnthropicLLMProvider,
    "openai_compatible": OpenAICompatibleLLMProvider,
}


def get_llm_provider() -> LLMProvider:
    """Build the provider named by LLM_PROVIDER (default 'databricks')."""
    provider = os.environ.get("LLM_PROVIDER", "databricks").lower()
    if provider not in _PROVIDERS:
        raise ValueError(f"Unknown LLM_PROVIDER: {provider!r}. Use one of: {', '.join(_PROVIDERS)}.")
    return _PROVIDERS[provider]()
