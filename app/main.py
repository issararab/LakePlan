"""FastAPI application entry point."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

from app.agent import get_agent, reset_agent
from app.duckdb_client import get_schema_context
from app.llm import get_llm_provider
from app.schemas import ChatRequest, ChatResponse, ResetRequest

_log_level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
logging.basicConfig(level=_log_level, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Verify DuckDB connectivity and LLM provider availability on startup.

    Raises on failure so the process exits with a clear error rather than
    accepting requests it cannot serve.
    """
    try:
        ctx = get_schema_context()
        table_count = ctx.count("main.")
        logger.info("DuckDB connected (%d tables available)", table_count)
        logger.debug("DuckDB schema:\n%s", ctx)
    except Exception as exc:
        logger.error("Failed to connect to DuckDB: %s", exc)
        raise

    try:
        get_llm_provider()
        logger.info("LLM provider initialised (provider=%s)", os.environ.get("LLM_PROVIDER", "databricks"))
    except Exception as exc:
        logger.error("Failed to initialise LLM provider: %s", exc)
        raise

    yield


app = FastAPI(
    title="LakePlan",
    description="A Databricks architecture planning and cost estimation agent.",
    version="1.0.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/")
def root() -> FileResponse:
    return FileResponse(str(_STATIC_DIR / "index.html"))


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty")
    return get_agent(request.session_id).handle(
        request.message.strip(),
        path_id=request.path_id,
        path_label=request.path_label,
        mode=request.mode,
    )


@app.post("/reset")
def reset_chat(request: ResetRequest) -> dict:
    reset_agent(request.session_id)
    return {"status": "ok"}
