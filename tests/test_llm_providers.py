"""Tests for LLM provider selection and the OpenAI-compatible provider used for open-weight models.

The round-trip test runs the real OpenAI SDK against a stub server on localhost that
stands in for Ollama or vLLM, so no network access or credentials are needed.

Run from the repo root:  python -m pytest tests
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import openai
import pytest

from app.llm import (
    AnthropicLLMProvider,
    DatabricksLLMProvider,
    OpenAICompatibleLLMProvider,
    _strip_reasoning,
    get_llm_provider,
)

_LLM_ENV_VARS = [
    "LLM_PROVIDER",
    "OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL",
    "DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_LLM_ENDPOINT",
    "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start every test with no LLM settings, whatever the developer's shell has."""
    for var in _LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


class _StubChatServer:
    """A minimal /v1/chat/completions endpoint that records requests and returns a fixed reply."""

    def __init__(self, reply: str) -> None:
        self.requests: list[dict] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                server.requests.append({
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "body": body,
                })
                payload = json.dumps({
                    "id": "chatcmpl-stub",
                    "object": "chat.completion",
                    "created": 0,
                    "model": body["model"],
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": reply},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self._httpd.server_port}/v1"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture
def stub_server():
    servers: list[_StubChatServer] = []

    def start(reply: str) -> _StubChatServer:
        servers.append(_StubChatServer(reply))
        return servers[-1]

    yield start
    for s in servers:
        s.close()


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider, env, expected_type", [
    ("databricks", {"DATABRICKS_HOST": "https://adb-1.azuredatabricks.net",
                    "DATABRICKS_TOKEN": "dapi-x", "DATABRICKS_LLM_ENDPOINT": "my-endpoint"},
     DatabricksLLMProvider),
    ("anthropic", {"ANTHROPIC_API_KEY": "sk-ant-x"}, AnthropicLLMProvider),
    ("openai_compatible", {"OPENAI_BASE_URL": "http://localhost:11434/v1", "OPENAI_MODEL": "llama3.1:8b"},
     OpenAICompatibleLLMProvider),
    ("OpenAI_Compatible", {"OPENAI_MODEL": "llama3.1:8b"}, OpenAICompatibleLLMProvider),
])
def test_llm_provider_env_var_selects_provider(monkeypatch, provider, env, expected_type):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    assert type(get_llm_provider()) is expected_type


def test_unknown_provider_lists_the_valid_names(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")

    with pytest.raises(ValueError, match="databricks, anthropic, openai_compatible"):
        get_llm_provider()


def test_openai_compatible_reads_settings_from_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:8001/v1")
    monkeypatch.setenv("OPENAI_MODEL", "Qwen/Qwen2.5-7B-Instruct")

    llm = get_llm_provider()

    assert llm.base_url == "http://localhost:8001/v1"
    assert llm.model == "Qwen/Qwen2.5-7B-Instruct"


def test_openai_compatible_requires_a_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")

    with pytest.raises(ValueError, match="OPENAI_MODEL"):
        get_llm_provider()


def test_databricks_targets_the_serving_endpoints_api(monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", "https://adb-1.azuredatabricks.net/")
    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-x")
    monkeypatch.setenv("DATABRICKS_LLM_ENDPOINT", "my-endpoint")

    llm = DatabricksLLMProvider()

    assert llm.base_url == "https://adb-1.azuredatabricks.net/serving-endpoints"
    assert llm.model == "my-endpoint"


def test_databricks_never_sends_the_openai_key(monkeypatch):
    """An empty Databricks token must not fall back to OPENAI_API_KEY, which would leak it to Databricks."""
    monkeypatch.setenv("DATABRICKS_HOST", "https://adb-1.azuredatabricks.net")
    monkeypatch.setenv("DATABRICKS_TOKEN", "")
    monkeypatch.setenv("DATABRICKS_LLM_ENDPOINT", "my-endpoint")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-stay-local")

    try:
        llm = DatabricksLLMProvider()
    except openai.OpenAIError:
        return  # Recent SDK versions reject an empty key outright, which is also safe
    assert llm._client.api_key == ""


# ---------------------------------------------------------------------------
# Round trip over HTTP
# ---------------------------------------------------------------------------

def test_round_trip_against_an_openai_compatible_server(stub_server):
    server = stub_server('<think>The user wants JSON.</think>\n{"action": "ask"}')
    llm = OpenAICompatibleLLMProvider(base_url=server.base_url, model="qwen2.5:7b")

    reply = llm.complete([{"role": "user", "content": "plan this"}], system="be precise", step="planner")

    assert reply == '{"action": "ask"}'
    [request] = server.requests
    assert request["path"] == "/v1/chat/completions"
    assert request["authorization"] == "Bearer not-needed"
    assert request["body"]["model"] == "qwen2.5:7b"
    assert request["body"]["messages"] == [
        {"role": "system", "content": "be precise"},
        {"role": "user", "content": "plan this"},
    ]


def test_api_key_is_sent_when_set(monkeypatch, stub_server):
    server = stub_server("ok")
    monkeypatch.setenv("OPENAI_API_KEY", "vllm-secret")
    llm = OpenAICompatibleLLMProvider(base_url=server.base_url, model="m")

    llm.complete([{"role": "user", "content": "hi"}])

    assert server.requests[0]["authorization"] == "Bearer vllm-secret"


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
