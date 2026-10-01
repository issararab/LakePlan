"""
Business rules for ingestion, processing, and transformation decisions.
All functions are pure Python — no LLM calls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.schemas import CollectedContext


# ---------------------------------------------------------------------------
# Ingestion rules
# ---------------------------------------------------------------------------

INGESTION_RULES = [
    {
        "source_type": "operational_db",
        "latency": ["near-real-time", "streaming"],
        "connector": "Zerobus",
        "pattern": "CDC",
        "rationale": (
            "Operational DB with real-time latency requirement → "
            "Zerobus as the CDC ingest layer for low-latency change capture."
        ),
    },
    {
        "source_type": "operational_db",
        "latency": ["batch"],
        "connector": "Lakeflow Connect or JDBC batch",
        "pattern": "Incremental extraction",
        "rationale": (
            "Operational DB with batch latency → incremental JDBC extraction using a high-watermark "
            "column (e.g. MODIFIED_DATE or sequence ID). Lakeflow Connect manages scheduling and "
            "schema evolution; JDBC is the fallback for unsupported sources. "
            "Historical migration uses a one-time bulk extract (parallel JDBC or source-side export to ADLS)."
        ),
    },
    {
        "source_type": "saas",
        "latency": None,  # applies to any latency
        "connector": "Lakeflow Connect",
        "fallback_connector": "Fivetran",
        "pattern": "Managed connector",
        "rationale": (
            "SaaS sources (Salesforce, HubSpot, Workday, etc.) → "
            "Lakeflow Connect by default; Fivetran if the connector is not yet available in Lakeflow."
        ),
    },
    {
        "source_type": "sap",
        "latency": None,
        "connector": "BDC or Datasphere + Fivetran/Lakeflow",
        "pattern": "SAP extraction",
        "rationale": (
            "SAP requires a dedicated extraction path: "
            "SAP BDC or Datasphere for extraction, then Fivetran or Lakeflow for ingestion into Delta."
        ),
    },
    {
        "source_type": "file_object_storage",
        "latency": None,
        "connector": "Auto Loader",
        "pattern": "Auto Loader with Delta sink",
        "rationale": (
            "File or object storage sources → "
            "Auto Loader with Delta sink for incremental ingestion with schema evolution."
        ),
    },
]

# Map common source descriptions to normalised source_type
SOURCE_TYPE_ALIASES: dict[str, str] = {
    "sql server": "operational_db",
    "oracle": "operational_db",
    "teradata": "operational_db",
    "synapse": "operational_db",
    "redshift": "operational_db",
    "postgres": "operational_db",
    "postgresql": "operational_db",
    "mysql": "operational_db",
    "db2": "operational_db",
    "salesforce": "saas",
    "hubspot": "saas",
    "workday": "saas",
    "servicenow": "saas",
    "sap": "sap",
    "s3": "file_object_storage",
    "adls": "file_object_storage",
    "blob": "file_object_storage",
    "gcs": "file_object_storage",
    "csv": "file_object_storage",
    "parquet": "file_object_storage",
}


# ---------------------------------------------------------------------------
# Processing thresholds
# ---------------------------------------------------------------------------

SERVERLESS_MAX_UTILIZATION = 0.30    # below 30% utilisation → Serverless preferred


# ---------------------------------------------------------------------------
# Transformation thresholds
# ---------------------------------------------------------------------------

SQL_HEAVY_THRESHOLD = 0.70   # >70% SQL → Serverless SQL Warehouse
MIXED_THRESHOLD_LOW = 0.50   # 50–70% SQL → Lakeflow Jobs on Job Compute (mixed-workload cluster)
                             # <50% SQL → Classic Spark cluster (dedicated sizing)


# ---------------------------------------------------------------------------
# Cloud/region scope
# ---------------------------------------------------------------------------

SUPPORTED_CLOUDS = ["azure"]
SUPPORTED_REGIONS = ["germany", "germany west central", "germany-west-central"]


# ---------------------------------------------------------------------------
# apply_rules — pure function, no LLM
# ---------------------------------------------------------------------------

def apply_rules(context: "CollectedContext") -> dict:
    """
    Apply ingestion, processing, and transformation rules to the collected context.
    Returns a recommendations dict used by the architecture generator.
    """
    recommendations: dict = {}

    # --- Ingestion ---
    ingestion_rec = _ingestion_recommendation(context)
    recommendations["ingestion"] = ingestion_rec

    # --- Processing ---
    processing_rec = _processing_recommendation(context)
    recommendations["processing"] = processing_rec

    # --- Transformation ---
    transformation_rec = _transformation_recommendation(context)
    recommendations["transformation"] = transformation_rec

    # --- ML/GenAI ---
    recommendations["ml_components"] = context.ml_components if context.needs_ml_genai else []

    return recommendations


def _ingestion_recommendation(context: "CollectedContext") -> dict:
    """Select the ingestion connector and pattern for the given source and latency.

    Attempts to infer source_type from source_system if not explicitly set.
    Falls back to a managed-connector default when no rule matches.
    """
    source_type = context.source_type
    latency = context.latency_requirement

    # Try to infer source_type from source_system if not explicitly set
    if not source_type and context.source_system:
        key = context.source_system.lower().strip()
        for alias, stype in SOURCE_TYPE_ALIASES.items():
            if alias in key:
                source_type = stype
                break

    for rule in INGESTION_RULES:
        if rule["source_type"] != source_type:
            continue
        if rule["latency"] is not None and latency not in rule["latency"]:
            continue
        result = {
            "connector": rule["connector"],
            "pattern": rule["pattern"],
            "rationale": rule["rationale"],
        }
        if "fallback_connector" in rule:
            result["fallback_connector"] = rule["fallback_connector"]
        return result

    return {
        "connector": "Lakeflow Connect or Fivetran",
        "pattern": "Managed ingestion",
        "rationale": "Source type not fully specified — defaulting to managed connector. Refine after source is confirmed.",
    }


def _processing_recommendation(context: "CollectedContext") -> dict:
    """Choose the compute type for ETL processing based on utilisation and workload stability.

    When utilization_pct is not stated, infers from workload_stability but does NOT
    cite the 30% threshold in the returned rationale to avoid presenting inferred
    values as measured facts.
    """
    utilisation = context.utilization_pct
    stability = context.workload_stability
    utilisation_stated = utilisation is not None

    # Infer from stability only when utilisation was not explicitly provided
    if not utilisation_stated:
        if stability == "sporadic":
            utilisation = 0.15
        elif stability == "stable_247":
            utilisation = 0.80
        elif stability == "mixed":
            utilisation = 0.50

    if utilisation is not None and utilisation_stated and utilisation < SERVERLESS_MAX_UTILIZATION:
        return {
            "compute_type": "Serverless Jobs Compute",
            "utilisation_stated": utilisation_stated,
            "utilisation_pct": utilisation,
            "rationale": (
                f"Stated utilisation ~{int(utilisation * 100)}% — below the 30% threshold. "
                "Serverless Jobs Compute is preferred for sporadic workloads: no cluster management, "
                "pay only for active compute time."
            ),
            "cost_optimisation": (
                "Classic Compute with spot workers can reduce costs if job frequency increases."
            ),
        }

    if not utilisation_stated:
        rationale = (
            "Utilisation not stated by customer — Classic Job Compute (on-demand) chosen as the "
            "safe default for production ETL with SLAs. "
            "Actual compute hours and cluster runtime must be confirmed in the workshop before sizing."
        )
    else:
        rationale = (
            f"Stated utilisation ~{int((utilisation or 50) * 100)}% — above 30% threshold. "
            "Classic Job Compute on-demand provides predictable performance and no eviction risk "
            "for production ETL with SLAs."
        )

    return {
        "compute_type": "Classic Job Compute (on-demand)",
        "utilisation_stated": utilisation_stated,
        "utilisation_pct": utilisation,
        "rationale": rationale,
        "cost_optimisation": (
            "Spot worker nodes (with on-demand driver) can reduce worker compute costs by 60–80% "
            "once jobs are stable, checkpointed, and retry-tested. Phase 2 cost lever only."
        ),
    }


def _transformation_recommendation(context: "CollectedContext") -> dict:
    """
    Returns two separate recommendations:
    - etl_compute:   the compute tier for pipeline ETL jobs (writes Bronze→Silver→Gold)
    - serving_layer: the compute tier for analyst queries, reporting, BI access on Gold tables

    These are intentionally different tiers — the SQL % affects BOTH, but in different ways.
    """
    sql_pct = context.sql_pct

    # ETL compute (the job cluster running transformation pipelines)
    if sql_pct is None or sql_pct >= MIXED_THRESHOLD_LOW:
        etl_compute = "Job Compute (SQL tasks in Lakeflow Jobs / Workflows)"
        etl_rationale = (
            "ETL transformation pipelines (Bronze→Silver→Gold writes) run as SQL + Python tasks "
            "on Job Compute — not on a SQL Warehouse. Job clusters are ephemeral and cost-efficient for scheduled batch."
        )
    else:
        etl_compute = "Classic Spark cluster (dedicated sizing)"
        etl_rationale = (
            f"Python/PySpark-heavy workload ({int((1 - (sql_pct or 0)) * 100)}% custom code) — "
            "dedicated Spark cluster for complex transformations."
        )

    # Serving layer — ML/GenAI paths use Model Serving, not a SQL Warehouse
    if context.needs_ml_genai:
        return {
            "etl_compute": etl_compute,
            "etl_rationale": etl_rationale,
            "serving_layer": "Databricks Model Serving (LLM endpoint + Vector Search)",
            "serving_rationale": (
                "ML/GenAI path — the primary serving tier is Model Serving (RAG chain + LLM endpoint) "
                "and Vector Search, not a SQL Warehouse. A small SQL Warehouse may be added separately "
                "for metadata/monitoring queries if needed."
            ),
        }

    if sql_pct is None or sql_pct >= SQL_HEAVY_THRESHOLD:
        serving = "Serverless SQL Warehouse"
        serving_rationale = (
            f"{'SQL share not specified' if sql_pct is None else f'{int(sql_pct * 100)}% SQL'} — "
            "Serverless SQL Warehouse for analyst queries, reporting, and BI tool access on Gold tables. "
            "Elastic, fast-start, consumption-based. Separate from ETL compute."
        )
    elif sql_pct >= MIXED_THRESHOLD_LOW:
        serving = "Serverless SQL Warehouse or SQL Pro Compute"
        serving_rationale = (
            f"Mixed workload ({int(sql_pct * 100)}% SQL) — Serverless SQL Warehouse for most queries; "
            "SQL Pro if advanced features (row-level security, larger cluster) are needed."
        )
    else:
        serving = "Classic SQL Compute"
        serving_rationale = (
            "Python-heavy workload — standard SQL Compute for the smaller SQL serving footprint."
        )

    return {
        "etl_compute": etl_compute,
        "etl_rationale": etl_rationale,
        "serving_layer": serving,
        "serving_rationale": serving_rationale,
    }
