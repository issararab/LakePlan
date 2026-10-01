"""
Confirmed pricing constants for Azure Germany West Central.

Update this file when Databricks publishes new rates — prompts.py reads from here
and injects the formatted blocks into the synthesis prompt automatically.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Serverless SQL Warehouse — DBU/hr by size
# ---------------------------------------------------------------------------

SERVERLESS_SQL_WAREHOUSE_DBU_PER_HOUR: dict[str, int] = {
    "2X-Small": 4,
    "X-Small": 6,
    "Small": 12,
    "Medium": 24,
    "Large": 40,
    "X-Large": 80,
    "2X-Large": 144,
    "3X-Large": 272,
    "4X-Large": 528,
}

# ---------------------------------------------------------------------------
# Model Serving — GPU endpoint sizes
# ---------------------------------------------------------------------------

MODEL_SERVING_DBU_RATE_USD: float = 0.084  # $/DBU — same rate for CPU and GPU serving

# CPU Serving: 1 concurrent request = 1 DBU/hr (no size tiers)
# Cost/hr = provisioned_concurrency × MODEL_SERVING_DBU_RATE_USD
# Sizing: provisioned_concurrency ≈ QPS × model_runtime_seconds
CPU_SERVING_DBU_PER_CONCURRENT_REQUEST: int = 1

GPU_SERVING_SIZES: list[dict] = [
    {"name": "Small",         "gpu": "T4 or equivalent",  "dbu_per_hour": 10.48},
    {"name": "Medium",        "gpu": "A10G x1 GPU",        "dbu_per_hour": 20.00},
    {"name": "Medium 4X",     "gpu": "A10G x4 GPU",        "dbu_per_hour": 112.00},
    {"name": "Medium 8X",     "gpu": "A10G x8 GPU",        "dbu_per_hour": 290.80},
    {"name": "Large 8X 40GB", "gpu": "A100 40GB x8 GPU",  "dbu_per_hour": 538.40},
    {"name": "Large 8X 80GB", "gpu": "A100 80GB x8 GPU",  "dbu_per_hour": 628.00},
]

# ---------------------------------------------------------------------------
# Vector Search (AI Search) — tiers
# DBU rate: $0.084/DBU (same PREMIUM_SERVERLESS_REAL_TIME_INFERENCE SKU as Model Serving)
# Billed per unit per hour; storage billed separately per GB/month
# ---------------------------------------------------------------------------

VECTOR_SEARCH_DBU_RATE_USD: float = 0.084  # $/DBU

VECTOR_SEARCH_TIERS: list[dict] = [
    {
        "name": "Standard",
        "vector_capacity": "2M vectors (768 dim) per unit",
        "dbu_per_hour": 4.00,
        "compute_usd_per_hour": 0.34,       # Germany West Central, includes cloud instance
        "storage_usd_per_gb_month": 0.250,
        "storage_free_gb": 30,
    },
    {
        "name": "Storage Optimized",
        "vector_capacity": "64M vectors (768 dim) per unit",
        "dbu_per_hour": 18.29,
        "compute_usd_per_hour": 1.54,       # Germany West Central, includes cloud instance
        "storage_usd_per_gb_month": 0.050,
        "storage_free_gb": 0,
    },
]

# ---------------------------------------------------------------------------
# Serverless DBU rates — known price_item_raw values from azure_web_price_rates_germany
# (Germany West Central, Premium tier, Pay As You Go)
# ---------------------------------------------------------------------------

SERVERLESS_DBU_RATES: dict[str, float] = {
    "Serverless SQL ***":                 0.91,
    "Automated Serverless Compute ***":   0.50,
    "Interactive Serverless Compute ***": 1.00,
    "SQL Compute":                        0.22,
    "SQL Pro Compute":                    0.72,
    "Jobs Compute **":                    0.30,
    "Model Training ***":                 0.78,
    "Serverless Real-Time Inference ***": 0.084,
    "Model Serving for Anthropic":        0.105,
}

# ---------------------------------------------------------------------------
# Prompt-ready formatted blocks — imported by prompts.py
# ---------------------------------------------------------------------------

def _build_sql_warehouse_block() -> str:
    items = list(SERVERLESS_SQL_WAREHOUSE_DBU_PER_HOUR.items())
    rows = []
    for i in range(0, len(items), 3):
        rows.append("  |  ".join(f"{name}: {dbu} DBU/hr" for name, dbu in items[i:i+3]))
    return "\n".join(f"     {row}" for row in rows)


def _build_cpu_serving_block() -> str:
    rate = MODEL_SERVING_DBU_RATE_USD * CPU_SERVING_DBU_PER_CONCURRENT_REQUEST
    return (
        f"     Pricing unit: {CPU_SERVING_DBU_PER_CONCURRENT_REQUEST} concurrent request = "
        f"{CPU_SERVING_DBU_PER_CONCURRENT_REQUEST} DBU/hr -> ${rate:.3f}/hr per concurrent request\n"
        f"     Formula: provisioned_concurrency x ${MODEL_SERVING_DBU_RATE_USD}/DBU\n"
        f"     Sizing:  provisioned_concurrency ~= QPS x model_runtime_seconds\n"
        f"     Example: 5 concurrent requests x ${MODEL_SERVING_DBU_RATE_USD} = ${5 * rate:.2f}/hr"
    )


def _build_gpu_serving_block() -> str:
    lines = []
    for s in GPU_SERVING_SIZES:
        cost = s["dbu_per_hour"] * MODEL_SERVING_DBU_RATE_USD
        lines.append(
            f"     {s['name']} ({s['gpu']}): {s['dbu_per_hour']} DBU/hr  -> ${cost:.3f}/hr"
        )
    return "\n".join(lines)


def _build_vector_search_block() -> str:
    lines = []
    for t in VECTOR_SEARCH_TIERS:
        cost = t["dbu_per_hour"] * VECTOR_SEARCH_DBU_RATE_USD
        storage_note = (
            f"storage ${t['storage_usd_per_gb_month']}/GB/mo (first {t['storage_free_gb']} GB free)"
            if t["storage_free_gb"]
            else f"storage ${t['storage_usd_per_gb_month']}/GB/mo"
        )
        lines.append(
            f"     {t['name']}: {t['dbu_per_hour']} DBU/hr -> ${cost:.3f}/hr compute"
            f"  |  {t['vector_capacity']}  |  {storage_note}"
        )
    return "\n".join(lines)


SQL_WAREHOUSE_PROMPT_BLOCK: str = _build_sql_warehouse_block()
CPU_SERVING_PROMPT_BLOCK: str = _build_cpu_serving_block()
GPU_SERVING_PROMPT_BLOCK: str = _build_gpu_serving_block()
VECTOR_SEARCH_PROMPT_BLOCK: str = _build_vector_search_block()
