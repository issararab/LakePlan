# LakePlan

**A Databricks architecture planning and cost estimation agent.**

LakePlan proposes a Databricks solution architecture for your use case, then estimates what it will cost on Azure Germany West Central. Implemented as a FastAPI backend with a React + Vite frontend.

## What it does

The agent guides users through a structured flow:

1. **Path selection** — user picks one of 5 use-case paths (DWH Migration, Cloud Platform Migration, Real-Time, ML/GenAI, Custom)
2. **Context collection** — conversational Q&A to gather source type, data volume, latency, BI users, etc.
3. **Architecture proposal** — rule engine + LLM generates a Databricks solution architecture; user approves or requests changes
4. **Pricing** — planner decides what to query, SQL generator queries DuckDB, LLM synthesises a cost estimate grounded in real Azure Germany pricing data

## Architecture

![System Architecture](docs/architecture.svg)

## Running with Docker (recommended)

The easiest way to run the agent — no Python or npm required.

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running
- A Databricks personal access token

### Pull and run

**Step 1 — Authenticate with GitHub Container Registry**

You need a GitHub personal access token with `read:packages` scope:
1. Go to **GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)**
2. Generate new token → check **`read:packages`** and **`repo`**
3. Copy the token and login:

```bash
echo YOUR_GITHUB_TOKEN | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
```

**Step 2 — Pull and run**

```bash
docker pull ghcr.io/ruhragency/databricks-pricing-agent:latest
```

**Option A** — use pre-configured workspace and model:

```bash
docker run -p 8000:8000 \
  -e DATABRICKS_TOKEN=<your_databricks_token> \
  ghcr.io/ruhragency/databricks-pricing-agent:latest
```

**Option B** — override the workspace or model:

```bash
docker run -p 8000:8000 \
  -e DATABRICKS_TOKEN=<your_databricks_token> \
  -e DATABRICKS_HOST=https://your-workspace.azuredatabricks.net \
  -e DATABRICKS_LLM_ENDPOINT=your-model-endpoint \
  ghcr.io/ruhragency/databricks-pricing-agent:latest
```

Open http://localhost:8000

### Stop the container

```bash
docker ps                          # find the container ID
docker stop <container_id>
```

---

## Local development setup

**Prerequisites:** Python 3.11+ and Node 18+ (required by Vite 5).

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Build the frontend

```bash
cd frontend
npm install
npm run build
cd ..
```

Compiled React app outputs to `app/static/`.

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env and fill in credentials
```

### 4. Run

```bash
python -m uvicorn app.main:app --reload
```

Open http://localhost:8000

> **Note**: `--reload` auto-restarts on file changes, which clears all in-memory sessions. Start without `--reload` for stable testing sessions.

## 🦙 Run it on your own models

LakePlan isn't tied to a hosted LLM. Set `LLM_PROVIDER=openai_compatible` and it talks to any server that speaks the OpenAI Chat Completions API, so the whole agent can run on Llama, Qwen, Mistral or Gemma on your own machine or GPU box. No cloud account needed, and no prompt or customer detail leaves your network.

| `LLM_PROVIDER` | Backend | Settings |
|---|---|---|
| `databricks` *(default)* | Databricks model serving | `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `DATABRICKS_LLM_ENDPOINT` |
| `anthropic` | Anthropic API | `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` |
| `openai_compatible` | Ollama · vLLM · LM Studio · llama.cpp · OpenAI | `OPENAI_BASE_URL`, `OPENAI_MODEL`, `OPENAI_API_KEY` *(optional)* |

### ⚡ One command: LakePlan + Ollama in Docker Compose

```bash
docker compose -f compose.ollama.yaml up --build
```

This starts Ollama, downloads `qwen2.5:7b` (4.7 GB, first run only), then starts LakePlan on http://localhost:8000. To pick another model, set `LAKEPLAN_MODEL`, for example `LAKEPLAN_MODEL=llama3.1:8b docker compose -f compose.ollama.yaml up --build`. The model weights live in a Docker volume, not in the LakePlan image, so switching models doesn't rebuild anything. GPU users can uncomment the `deploy:` block in [`compose.ollama.yaml`](compose.ollama.yaml).

### 🖥️ Ollama installed on your machine

```bash
OLLAMA_CONTEXT_LENGTH=16384 ollama serve
ollama pull qwen2.5:7b
```

```dotenv
# .env
LLM_PROVIDER=openai_compatible
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_MODEL=qwen2.5:7b
```

### 🚀 vLLM on a GPU server

```bash
vllm serve Qwen/Qwen2.5-7B-Instruct --port 8001 --max-model-len 32768
```

```dotenv
LLM_PROVIDER=openai_compatible
OPENAI_BASE_URL=http://<gpu-host>:8001/v1
OPENAI_MODEL=Qwen/Qwen2.5-7B-Instruct
```

vLLM listens on port 8001 here because LakePlan already uses 8000. If you start vLLM with `--api-key`, set the same value in `OPENAI_API_KEY`.

> [!IMPORTANT]
> **Give the model at least a 16k-token context window.** The SQL-generation prompt carries the whole pricing schema (about 4,400 tokens), and the final answer prompt can pass 10k. Ollama sizes its default window from GPU memory. In testing on a 4 GB GPU it cut that SQL prompt down to 2,050 tokens **without returning an error**, which shows up as wrong SQL or broken JSON. Set `OLLAMA_CONTEXT_LENGTH=16384` (Compose does this for you). On vLLM, use `--max-model-len`.

> [!TIP]
> LakePlan cleans up the usual quirks of local models before parsing a reply: the `<think>…</think>` block from reasoning models such as DeepSeek-R1 and Qwen3, `//` comments inside JSON, and a list where a single text field was expected.

### 🎯 Which model?

LakePlan asks the model for strict JSON plans and DuckDB SQL against a six-table schema, so model size matters more than in a plain chatbot. Measured with Ollama 0.40 on a laptop GPU:

| Model | Download | What happened |
|---|---|---|
| `qwen2.5:3b` | 1.9 GB | Full request flow works, but the SQL used columns that don't exist |
| `qwen2.5:7b` | 4.7 GB | 1 of 3 test lookups returned the right price. Good for trying LakePlan out |

For estimates you'll share, serve a larger model, for example Qwen 2.5 32B or Llama 3.3 70B on a vLLM GPU server.



## UI modes

**User mode** — clean chat with plain-language answers; no SQL, no table names, no confidence labels.

**Debug mode** — each response shows a collapsible step panel (context extraction, rules fired, LLM calls, SQL + raw data). Pricing output includes full retrieved data tables, confidence label, and missing data section.

Mode is selected on the onboarding screen as a toggle and locks for the session.

## Documentation

- [How It Works & Project Structure](../../wiki/How-It-Works)
- [API Reference](../../wiki/API-Reference)
- [LLM Configuration](../../wiki/LLM-Configuration)
- [Pricing Database](../../wiki/Pricing-Database)
- [Building and Publishing the Docker Image](../../wiki/Building-and-Publishing-Docker-Image)
