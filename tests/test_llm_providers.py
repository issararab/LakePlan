"""Tests for the LLM layer: provider selection and the LangChain chat models behind it.

The round-trip tests run the real LangChain clients against stub servers on localhost
(one OpenAI-compatible, standing in for Ollama, vLLM or Databricks, and one speaking the
Anthropic Messages API), so no network access or credentials are needed.

Run from the repo root:  python -m pytest tests
"""

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.llm import ChatModelLLM, _strip_reasoning, _to_langchain_messages, get_llm_provider

_LLM_ENV_VARS = [
    "LLM_PROVIDER",
    "OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL",
    "DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_LLM_ENDPOINT",
    "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL",
    "LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start every test with no LLM settings, whatever the developer's shell has."""
    for var in _LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


class _StubServer:
    """A localhost HTTP server that records POST requests and answers with `respond(body)`."""

    def __init__(self, respond: Callable[[dict], dict]) -> None:
        self.requests: list[dict] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                server.requests.append({
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": body,
                })
                payload = json.dumps(respond(body)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_port}"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def _openai_reply(text: str) -> Callable[[dict], dict]:
    return lambda body: {
        "id": "chatcmpl-stub", "object": "chat.completion", "created": 0, "model": body["model"],
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _anthropic_reply(text: str) -> Callable[[dict], dict]:
    return lambda body: {
        "id": "msg_stub", "type": "message", "role": "assistant", "model": body["model"],
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


@pytest.fixture
def stub_server():
    servers: list[_StubServer] = []

    def start(respond: Callable[[dict], dict]) -> _StubServer:
        servers.append(_StubServer(respond))
        return servers[-1]

    yield start
    for s in servers:
        s.close()


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider, env, chat_model_type", [
    ("databricks", {"DATABRICKS_HOST": "https://adb-1.azuredatabricks.net",
                    "DATABRICKS_TOKEN": "dapi-x", "DATABRICKS_LLM_ENDPOINT": "my-endpoint"}, ChatOpenAI),
    ("anthropic", {"ANTHROPIC_API_KEY": "sk-ant-x"}, ChatAnthropic),
    ("openai_compatible", {"OPENAI_BASE_URL": "http://localhost:11434/v1", "OPENAI_MODEL": "llama3.1:8b"}, ChatOpenAI),
    ("OpenAI_Compatible", {"OPENAI_MODEL": "llama3.1:8b"}, ChatOpenAI),
])
def test_llm_provider_env_var_selects_the_chat_model(monkeypatch, provider, env, chat_model_type):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    llm = get_llm_provider()

    assert isinstance(llm, ChatModelLLM)
    assert type(llm.chat_model) is chat_model_type


def test_unknown_provider_lists_the_valid_names(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")

    with pytest.raises(ValueError, match="databricks, anthropic, openai_compatible"):
        get_llm_provider()


def test_openai_compatible_reads_settings_from_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:8001/v1")
    monkeypatch.setenv("OPENAI_MODEL", "Qwen/Qwen2.5-7B-Instruct")

    llm = get_llm_provider()

    assert llm.chat_model.openai_api_base == "http://localhost:8001/v1"
    assert llm.model == llm.chat_model.model_name == "Qwen/Qwen2.5-7B-Instruct"


def test_openai_compatible_requires_a_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")

    with pytest.raises(ValueError, match="OPENAI_MODEL"):
        get_llm_provider()


def test_databricks_targets_the_serving_endpoints_api(monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", "https://adb-1.azuredatabricks.net/")
    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-x")
    monkeypatch.setenv("DATABRICKS_LLM_ENDPOINT", "my-endpoint")

    llm = get_llm_provider()

    assert llm.chat_model.openai_api_base == "https://adb-1.azuredatabricks.net/serving-endpoints"
    assert llm.model == "my-endpoint"


def test_missing_databricks_host_says_what_to_set(monkeypatch):
    """The image has no default workspace, so a missing host must fail at startup with a clear message."""
    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-x")
    monkeypatch.setenv("DATABRICKS_LLM_ENDPOINT", "my-endpoint")

    with pytest.raises(ValueError, match="DATABRICKS_HOST is not set"):
        get_llm_provider()


def test_empty_databricks_token_is_an_error_not_a_fallback(monkeypatch):
    """An empty token must fail clearly, never send OPENAI_API_KEY to Databricks instead."""
    monkeypatch.setenv("DATABRICKS_HOST", "https://adb-1.azuredatabricks.net")
    monkeypatch.setenv("DATABRICKS_TOKEN", "")
    monkeypatch.setenv("DATABRICKS_LLM_ENDPOINT", "my-endpoint")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-stay-local")

    with pytest.raises(ValueError, match="DATABRICKS_TOKEN"):
        get_llm_provider()


def test_anthropic_model_defaults_and_override(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    assert get_llm_provider().model == "claude-opus-4-5"

    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
    llm = get_llm_provider()
    assert llm.model == llm.chat_model.model == "claude-sonnet-4-5"


# ---------------------------------------------------------------------------
# Round trips over HTTP
# ---------------------------------------------------------------------------

def test_round_trip_against_an_openai_compatible_server(stub_server):
    server = stub_server(_openai_reply('<think>The user wants JSON.</think>\n{"action": "ask"}'))
    chat_model = ChatOpenAI(base_url=f"{server.url}/v1", api_key="not-needed", model="qwen2.5:7b", max_retries=0)
    llm = ChatModelLLM(chat_model, "qwen2.5:7b")

    reply = llm.complete([{"role": "user", "content": "plan this"}], system="be precise", step="planner")

    assert reply == '{"action": "ask"}'
    [request] = server.requests
    assert request["path"] == "/v1/chat/completions"
    assert request["headers"]["authorization"] == "Bearer not-needed"
    assert request["body"]["model"] == "qwen2.5:7b"
    assert request["body"]["messages"] == [
        {"role": "system", "content": "be precise"},
        {"role": "user", "content": "plan this"},
    ]
    # Nothing beyond what Databricks, Ollama and vLLM all accept
    assert set(request["body"]) <= {"model", "messages", "stream"}


def test_api_key_is_sent_when_set(monkeypatch, stub_server):
    server = stub_server(_openai_reply("ok"))
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("OPENAI_BASE_URL", f"{server.url}/v1")
    monkeypatch.setenv("OPENAI_MODEL", "m")
    monkeypatch.setenv("OPENAI_API_KEY", "vllm-secret")

    get_llm_provider().complete([{"role": "user", "content": "hi"}])

    assert server.requests[0]["headers"]["authorization"] == "Bearer vllm-secret"


def test_round_trip_against_the_anthropic_api(stub_server):
    server = stub_server(_anthropic_reply("ARCHITECTURE"))
    chat_model = ChatAnthropic(model="claude-x", api_key="sk-ant-x", base_url=server.url, max_tokens=4096, max_retries=0)
    llm = ChatModelLLM(chat_model, "claude-x")

    reply = llm.complete(
        [{"role": "user", "content": "design it"}, {"role": "assistant", "content": "draft"},
         {"role": "user", "content": "again"}],
        system="you are an architect",
        step="architecture-generate",
    )

    assert reply == "ARCHITECTURE"
    [request] = server.requests
    assert request["path"] == "/v1/messages"
    # Header name depends on the anthropic SDK version (x-api-key or a bearer token)
    assert "sk-ant-x" in (request["headers"].get("x-api-key") or request["headers"]["authorization"])
    body = request["body"]
    assert body["model"] == "claude-x"
    assert body["max_tokens"] == 4096
    assert body["system"] == "you are an architect"
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]


def test_messages_are_converted_to_langchain_types():
    converted = _to_langchain_messages(
        [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}], system="s"
    )

    assert [type(m) for m in converted] == [SystemMessage, HumanMessage, AIMessage]
    assert [m.content for m in converted] == ["s", "a", "b"]


@pytest.mark.parametrize("raw, expected", [
    ("<think>step 1, step 2</think>\n\nSELECT 1", "SELECT 1"),
    # Some servers put the opening tag in the prompt template, so only the closing tag comes back
    ("step 1, step 2</think>SELECT 1", "SELECT 1"),
    ("<think>a</think><think>b</think>{}", "{}"),
    ("SELECT 1", "SELECT 1"),
    ("", ""),
])
def test_reasoning_block_is_stripped(raw, expected):
    assert _strip_reasoning(raw) == expected
