"""Regression tests: LLM-generated SQL must not be able to read files or non-pricing tables.

Run from the repo root:  python -m pytest tests
"""

import pytest

from app.duckdb_client import execute_query
from app.sql_validator import SQLValidationError, validate_and_sanitize

FILE_AND_CATALOG_ATTACKS = [
    "SELECT * FROM read_csv('requirements.txt')",
    "SELECT * FROM read_text('requirements.txt')",
    "SELECT * FROM read_parquet('data/x.parquet')",
    "SELECT * FROM read_json_auto('https://example.com/x.json')",
    "SELECT * FROM 'requirements.txt'",
    "SELECT * FROM glob('*')",
    "SELECT * FROM query('SELECT 1')",
    "SELECT * FROM duckdb_settings()",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM other_db.main.azure_web_storage_rates_germany",
    # File read hidden in a subquery behind a legitimate table
    "SELECT tier FROM main.azure_web_storage_rates_germany "
    "WHERE tier IN (SELECT content FROM read_text('requirements.txt'))",
    # File read hidden in a CTE
    "WITH f AS (SELECT * FROM read_csv('requirements.txt')) SELECT * FROM f",
    # File read in a JOIN
    "SELECT * FROM main.azure_web_storage_rates_germany s JOIN glob('*') g ON true",
]

LEGITIMATE_QUERIES = [
    "SELECT tier, price_usd FROM main.azure_web_storage_rates_germany "
    "WHERE meter = 'Data storage' AND unit = 'per GB'",
    "SELECT sku_name, CAST(json_extract_string(pricing, '$.default') AS DOUBLE) AS price "
    "FROM main.azure_dbx_system_price_rates WHERE sku_name ILIKE '%JOBS_COMPUTE%'",
    'SELECT instance, "1_year_savings_plan_savings_total_price_hourly_usd" '
    "FROM main.azure_web_price_rates_germany WHERE instance IS NOT NULL",
    "SELECT tier FROM azure_web_storage_rates_germany",
    "WITH s AS (SELECT tier, price_usd FROM main.azure_web_storage_rates_germany) "
    "SELECT tier, MIN(price_usd) FROM s GROUP BY tier",
    "SELECT r.model FROM main.azure_dbx_foundation_model_rates r "
    "JOIN main.azure_dbx_foundation_model_overview o ON o.selected_model = r.model",
]


@pytest.mark.parametrize("sql", FILE_AND_CATALOG_ATTACKS)
def test_validator_rejects_file_and_catalog_access(sql):
    with pytest.raises(SQLValidationError):
        validate_and_sanitize(sql)


@pytest.mark.parametrize("sql", LEGITIMATE_QUERIES)
def test_legitimate_pricing_queries_still_run(sql):
    execute_query(validate_and_sanitize(sql))


@pytest.mark.parametrize("sql", [
    "SELECT * FROM read_csv('requirements.txt')",
    "SELECT * FROM glob('*')",
])
def test_connection_blocks_file_access_even_without_validator(sql):
    with pytest.raises(Exception, match="(?i)external access|disabled"):
        execute_query(sql)


def test_connection_config_cannot_be_reenabled():
    with pytest.raises(Exception, match="(?i)lock|configuration"):
        execute_query("SET enable_external_access = true")
