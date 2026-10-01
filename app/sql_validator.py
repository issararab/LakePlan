"""SQLGlot-based validation: SELECT-only, blocklist, auto-LIMIT injection."""

import sqlglot
import sqlglot.expressions as exp

_BLOCKED_STATEMENT_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Command,  # catches COPY, ATTACH, INSTALL, LOAD, PRAGMA, EXPORT, TRUNCATE
)

_BLOCKED_KEYWORDS = {
    "insert", "update", "delete", "drop", "create", "alter",
    "copy", "attach", "install", "load", "truncate", "pragma", "export",
}

_MAX_LIMIT = 100


class SQLValidationError(ValueError):
    """Raised when a SQL query fails the SELECT-only safety check."""


def validate_and_sanitize(sql: str) -> str:
    """
    Parse, validate, and sanitize SQL.
    Returns a clean DuckDB SQL string with LIMIT injected if missing.
    Raises SQLValidationError with a description on any policy violation.
    """
    sql = sql.strip().rstrip(";")

    # Keyword blocklist scan (fast pre-check)
    lower = sql.lower()
    for kw in _BLOCKED_KEYWORDS:
        if f" {kw} " in f" {lower} ":
            raise SQLValidationError(f"Blocked keyword in query: {kw.upper()}")

    # Parse
    try:
        statements = sqlglot.parse(sql, dialect="duckdb")
    except sqlglot.errors.ParseError as exc:
        raise SQLValidationError(f"SQL parse error: {exc}") from exc

    if not statements or len(statements) > 1:
        raise SQLValidationError(
            f"Expected exactly one SQL statement, got {len(statements or [])}."
        )

    statement = statements[0]

    # Must be a SELECT (or a CTE whose root is a SELECT)
    if not isinstance(statement, exp.Select):
        raise SQLValidationError(
            f"Only SELECT queries are allowed. Got: {type(statement).__name__}"
        )

    # AST-level blocklist
    for blocked_type in _BLOCKED_STATEMENT_TYPES:
        if statement.find(blocked_type):
            raise SQLValidationError(
                f"Blocked statement type found in query: {blocked_type.__name__}"
            )

    # Inject LIMIT if absent or too large
    existing_limit = statement.args.get("limit")
    if existing_limit is None:
        statement = statement.limit(_MAX_LIMIT)
    else:
        try:
            limit_val = int(existing_limit.this.this)
            if limit_val > _MAX_LIMIT:
                statement = statement.limit(_MAX_LIMIT)
        except (AttributeError, ValueError):
            statement = statement.limit(_MAX_LIMIT)

    return statement.sql(dialect="duckdb")
