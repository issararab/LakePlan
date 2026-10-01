from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Agent phase
# ---------------------------------------------------------------------------

class AgentPhase(str, Enum):
    """Ordered phases of a single pricing session."""

    CONTEXT_COLLECTION = "context_collection"
    ARCHITECTURE_PROPOSAL = "architecture_proposal"
    ARCHITECTURE_APPROVAL = "architecture_approval"
    PRICING = "pricing"


# ---------------------------------------------------------------------------
# Collected context — grows as conversation progresses
# ---------------------------------------------------------------------------

class CollectedContext(BaseModel):
    """All structured information collected from the customer during context collection.

    Fields are populated incrementally as the conversation progresses and merged
    into the pricing question and architecture generation prompts.

    Attributes:
        path_id: Use-case path identifier (e.g. 'dwh-migration', 'realtime').
        path_label: Human-readable version of path_id.
        initial_description: The customer's first free-text message.
        source_type: Normalised source category ('operational_db', 'saas', 'sap',
            'file_object_storage').
        source_system: Specific source system name (e.g. 'SQL Server', 'Salesforce').
        num_tables: Approximate number of tables or objects to migrate.
        historical_volume_gb: One-time bulk historical data volume in GB.
        daily_write_volume_gb: Ongoing daily ingestion volume in GB.
        latency_requirement: Required data freshness ('batch', 'near-real-time', 'streaming').
        daily_processed_volume_gb: Total daily processing volume in GB.
        job_frequency: How often the pipeline runs (e.g. 'hourly', 'daily').
        expected_job_duration_hours: Typical single-run duration in hours.
        workload_stability: Pattern of resource demand ('sporadic', 'stable_247', 'mixed').
        utilization_pct: Fraction of time compute is actively used (0.0–1.0).
        num_bi_users: Number of BI/analyst users querying Gold tables.
        peak_concurrent_queries: Peak simultaneous users/queries; takes priority over
            num_bi_users for SQL Warehouse sizing when explicitly stated.
        sql_pct: Fraction of transformation logic written in SQL (0.0–1.0).
        custom_code_pct: Fraction written in Python/Scala (0.0–1.0).
        transformation_complexity: Complexity tier ('low', 'medium', 'high').
        ml_share_pct: Fraction of workload dedicated to ML (0.0–1.0).
        needs_ml_genai: True only when the customer explicitly mentions ML or GenAI.
        ml_components: ML/GenAI components to include (e.g. 'Model Serving', 'Vector Search').
        daily_llm_requests: LLM/chatbot requests per day.
        avg_llm_input_tokens_per_request: Average input token count per LLM call.
        avg_llm_output_tokens_per_request: Average output token count per LLM call.
        num_vectors: Number of vectors/chunks in the Vector Search index.
        proposed_architecture: Architecture text generated after context collection.
        architecture_approved: True once the customer has approved the architecture.
    """

    # Path
    path_id: str = ""                        # dwh-migration | cloud-migration | realtime | ml-genai | custom
    path_label: str = ""
    initial_description: str = ""

    # Ingestion
    source_type: str | None = None           # operational_db | saas | sap | file_object_storage
    source_system: str | None = None         # SQL Server | Oracle | Salesforce | etc.
    num_tables: int | None = None
    historical_volume_gb: float | None = None  # one-time bulk historical data (e.g. 45 TB = 46080)
    daily_write_volume_gb: float | None = None
    latency_requirement: str | None = None   # batch | near-real-time | streaming

    # Processing
    daily_processed_volume_gb: float | None = None
    job_frequency: str | None = None
    expected_job_duration_hours: float | None = None
    workload_stability: str | None = None    # sporadic | stable_247 | mixed
    utilization_pct: float | None = None

    # Serving
    num_bi_users: int | None = None              # number of BI/analyst users querying Gold tables
    peak_concurrent_queries: int | None = None   # peak simultaneous queries/users (use this over num_bi_users for warehouse sizing)

    # Transformation
    sql_pct: float | None = None
    custom_code_pct: float | None = None
    transformation_complexity: str | None = None  # low | medium | high
    ml_share_pct: float | None = None

    # ML/GenAI — only populated when user explicitly mentions ML or GenAI
    needs_ml_genai: bool = False
    ml_components: list[str] = []            # MLflow | Feature Store | Model Serving | Vector Search
    daily_llm_requests: int | None = None    # LLM/chatbot requests per day (e.g. 2000 questions/day)
    avg_llm_input_tokens_per_request: int | None = None   # average input tokens per LLM call
    avg_llm_output_tokens_per_request: int | None = None  # average output tokens per LLM call
    num_vectors: int | None = None           # number of vectors/chunks in the Vector Search index (e.g. 4M = 4000000)

    # Architecture (filled after generation)
    proposed_architecture: str | None = None
    architecture_approved: bool = False

    def merge(self, updates: dict) -> "CollectedContext":
        """Return a new CollectedContext with non-None update values applied."""
        data = self.model_dump()
        for k, v in updates.items():
            if v is not None and k in data:
                data[k] = v
        return CollectedContext(**data)


# ---------------------------------------------------------------------------
# Per-session agent state
# ---------------------------------------------------------------------------

@dataclass
class AgentState:
    """Mutable runtime state for a single pricing session.

    Attributes:
        phase: Current phase in the agent workflow.
        context: All structured information collected from the conversation.
        consecutive_approval_rejections: Number of consecutive architecture rejections;
            resets to context collection at _MAX_APPROVAL_REJECTIONS.
        mode: Session rendering mode ('user' or 'debug'), locked on the first message.
    """

    phase: AgentPhase = AgentPhase.CONTEXT_COLLECTION
    context: CollectedContext = field(default_factory=CollectedContext)
    consecutive_approval_rejections: int = 0
    mode: str = "user"               # user | debug — set once on first message, stays for the session


# ---------------------------------------------------------------------------
# API request / response schemas
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    """Incoming request payload for the /chat endpoint.

    Attributes:
        message: The user's message text.
        session_id: UUID identifying the browser session.
        path_id: Use-case path identifier; sent on the first message only.
        path_label: Human-readable path label; sent on the first message only.
        mode: Session rendering mode ('user' or 'debug').
    """

    message: str
    session_id: str
    path_id: str | None = None       # sent on first message only
    path_label: str | None = None    # sent on first message only
    mode: str = "user"               # user | debug — sent on every message


class ResetRequest(BaseModel):
    """Request payload for the /reset endpoint.

    Attributes:
        session_id: UUID of the session to reset.
    """

    session_id: str


class QueryResult(BaseModel):
    """A single executed SQL query and its results.

    Attributes:
        name: Logical label for the query (e.g. 'compute_pricing').
        sql: The validated DuckDB SQL that was executed.
        rows: Rows returned by the query as a list of dicts.
    """

    name: str
    sql: str
    rows: list[dict[str, Any]]


class ChatResponse(BaseModel):
    """Response payload returned by the /chat endpoint.

    Attributes:
        answer: The agent's text response.
        response_type: Content category ('message', 'architecture_proposal', or 'pricing').
        phase: Current agent phase after processing the request.
        query_results: SQL queries and rows used to produce the answer; non-empty for
            pricing responses.
        debug_steps: Ordered pipeline steps; populated only when mode is 'debug'.
    """

    answer: str
    response_type: str = "message"   # message | architecture_proposal | pricing
    phase: str | None = None
    query_results: list[QueryResult] = []
    debug_steps: list[dict] = []     # populated only when mode == "debug"
