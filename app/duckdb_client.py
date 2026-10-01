"""Read-only DuckDB access with schema introspection and safe query execution."""

import os
from decimal import Decimal
from typing import Any

import duckdb


_connection: duckdb.DuckDBPyConnection | None = None
_schema_context: str | None = None


def _get_connection() -> duckdb.DuckDBPyConnection:
    """Return the module-level read-only DuckDB connection, creating it on first call.

    External access is disabled so LLM-generated SQL cannot read files or URLs
    (read_csv, read_text, glob, ...), and the configuration is locked so a query
    cannot re-enable it.
    """
    global _connection
    if _connection is None:
        db_path = os.environ.get("DUCKDB_PATH", "data/pricing.duckdb")
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"DuckDB database not found: {db_path}")
        _connection = duckdb.connect(
            db_path,
            read_only=True,
            config={"enable_external_access": False, "lock_configuration": True},
        )
    return _connection


def _make_json_safe(value: Any) -> Any:
    """Convert a DuckDB result value to a JSON-serialisable Python type."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def get_schema_context() -> str:
    """Return a compact schema summary string, cached after first call."""
    global _schema_context
    if _schema_context is not None:
        return _schema_context

    con = _get_connection()
    tables = con.execute(
        "SELECT table_schema, table_name FROM information_schema.tables ORDER BY 1, 2"
    ).fetchall()

    lines: list[str] = ["Available tables in the pricing database:"]
    for schema, table in tables:
        lines.append(f"\n  {schema}.{table}")
        cols = con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position",
            [schema, table],
        ).fetchall()
        for col, dtype in cols:
            lines.append(f"    - {col}: {dtype}")

    _schema_context = "\n".join(lines)
    return _schema_context


def execute_query(sql: str, max_rows: int | None = None) -> list[dict[str, Any]]:
    """Execute a validated SELECT and return rows as a list of dicts."""
    if max_rows is None:
        max_rows = int(os.environ.get("MAX_ROWS", "100"))

    con = _get_connection()
    rel = con.execute(sql)
    columns = [desc[0] for desc in rel.description]
    raw_rows = rel.fetchmany(max_rows)

    return [
        {col: _make_json_safe(val) for col, val in zip(columns, row)}
        for row in raw_rows
    ]
