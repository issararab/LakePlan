"""LLM access for LakePlan, built on LangChain chat models.

LLM_PROVIDER picks the backend:

- databricks         ChatOpenAI on the workspace's /serving-endpoints API (default)
- anthropic          ChatAnthropic
- openai_compatible  ChatOpenAI on any OpenAI-compatible server: self-hosted models
                     (Ollama, vLLM, LM Studio, llama.cpp) or OpenAI itself

The rest of the app calls `LLMProvider.complete()`. `ChatModelLLM` implements it on top
of any LangChain chat model, so adding a backend means adding one builder function.
"""

import logging
import os
from abc import ABC, abstractmethod
from collections.abc import Callable

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from app import debug_collector

logger = logging.getLogger(__name__)

# Placeholder key for local servers that ignore auth. The OpenAI client refuses an empty key.
_NO_API_KEY = "not-needed"

_ROLE_TO_MESSAGE: dict[str, type[BaseMessage]] = {
    "system": SystemMessage,
    "user": HumanMessage,
    "assistant": AIMessage,
}


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


def _to_langchain_messages(messages: list[dict], system: str | None) -> list[BaseMessage]:
    """Turn {"role", "content"} dicts (plus an optional system prompt) into LangChain messages."""
    converted: list[BaseMessage] = [SystemMessage(system)] if system else []
    converted.extend(_ROLE_TO_MESSAGE[m["role"]](m["content"]) for m in messages)
    return converted


class LLMProvider(ABC):
    """What the rest of LakePlan needs from an LLM: one call per pipeline step.

    Attributes:
        model: Model or endpoint name, for logs.
    """

    model: str = ""

    @abstractmethod
    def complete(self, messages: list[dict], system: str | None = None, step: str = "") -> str:
        """Send messages and return the assistant reply as a string.

        step: label for the pipeline stage making the call (e.g. 'planner'). Used in logs,
            Debug mode and as the LangChain run name.
        """


class ChatModelLLM(LLMProvider):
    """Runs `complete()` on a LangChain chat model.

    Each call carries its pipeline step as the LangChain run name, so callbacks and
    LangSmith traces show `planner`, `sql-gen(attempt=1)` and so on instead of a generic
    model call.

    Attributes:
        chat_model: The LangChain chat model that does the work.
        model: Model or endpoint name, for logs.
    """

    def __init__(self, chat_model: BaseChatModel, model: str) -> None:
        self.chat_model = chat_model
        self.model = model

    def complete(self, messages: list[dict], system: str | None = None, step: str = "") -> str:
        """See base class."""
        _log_llm_call(step, messages, system)
        reply = self.chat_model.invoke(
            _to_langchain_messages(messages, system),
            config={"run_name": step or "llm", "tags": ["lakeplan"]},
        )
        text = _strip_reasoning(reply.text)
        logger.debug("[%s] LLM reply: %s", step or "llm", _truncate(text, 300))
        return text


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


# ---------------------------------------------------------------------------
# Chat model per provider
# ---------------------------------------------------------------------------

def _databricks() -> tuple[BaseChatModel, str]:
    """Databricks model serving speaks the OpenAI Chat Completions API under /serving-endpoints."""
    from langchain_openai import ChatOpenAI

    host = os.environ.get("DATABRICKS_HOST", "").rstrip("/")
    token = os.environ.get("DATABRICKS_TOKEN", "")
    endpoint = os.environ["DATABRICKS_LLM_ENDPOINT"]
    if not host:
        raise ValueError(
            "DATABRICKS_HOST is not set. Set it to your workspace URL, "
            "e.g. https://adb-1234567890.12.azuredatabricks.net, or pick another LLM_PROVIDER"
        )
    if not token:
        raise ValueError("DATABRICKS_TOKEN is not set")
    return ChatOpenAI(base_url=f"{host}/serving-endpoints", api_key=token, model=endpoint), endpoint


def _anthropic() -> tuple[BaseChatModel, str]:
    from langchain_anthropic import ChatAnthropic

    model = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-5")
    return ChatAnthropic(model=model, api_key=os.environ["ANTHROPIC_API_KEY"], max_tokens=4096), model


def _openai_compatible() -> tuple[BaseChatModel, str]:
    """Ollama, vLLM, LM Studio, llama.cpp or OpenAI. No base URL means api.openai.com."""
    from langchain_openai import ChatOpenAI

    model = os.environ.get("OPENAI_MODEL", "")
    if not model:
        raise ValueError("OPENAI_MODEL must be set when LLM_PROVIDER=openai_compatible")
    chat_model = ChatOpenAI(
        base_url=os.environ.get("OPENAI_BASE_URL") or None,
        api_key=os.environ.get("OPENAI_API_KEY") or _NO_API_KEY,
        model=model,
    )
    return chat_model, model


_PROVIDERS: dict[str, Callable[[], tuple[BaseChatModel, str]]] = {
    "databricks": _databricks,
    "anthropic": _anthropic,
    "openai_compatible": _openai_compatible,
}


def get_llm_provider() -> LLMProvider:
    """Build the LLM named by LLM_PROVIDER (default 'databricks')."""
    provider = os.environ.get("LLM_PROVIDER", "databricks").lower()
    if provider not in _PROVIDERS:
        raise ValueError(f"Unknown LLM_PROVIDER: {provider!r}. Use one of: {', '.join(_PROVIDERS)}.")
    chat_model, model = _PROVIDERS[provider]()
    return ChatModelLLM(chat_model, model)
