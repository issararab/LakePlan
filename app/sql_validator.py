"""SQLGlot-based validation: SELECT-only, blocklist, table allowlist, auto-LIMIT injection."""

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

# The only tables a query may read. Anything else in a FROM/JOIN — table functions
# such as read_csv() or glob(), file paths like 'x.csv', or catalog views — is rejected.
_ALLOWED_TABLES = frozenset({
    "azure_dbx_system_price_rates",
    "azure_web_price_rates_germany",
    "azure_web_storage_rates_germany",
    "azure_dbx_foundation_model_rates",
    "azure_dbx_proprietary_foundation_model_rates",
    "azure_dbx_foundation_model_overview",
})

_MAX_LIMIT = 100


class SQLValidationError(ValueError):
    """Raised when a SQL query fails the SELECT-only safety check."""


def _check_tables(statement: exp.Expression) -> None:
    """Raise SQLValidationError unless every table source is an allowed pricing table or a CTE."""
    cte_names = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}

    for table in statement.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise SQLValidationError(
                f"Table functions are not allowed: {table.sql(dialect='duckdb')}. "
                "Query the pricing tables in the main schema directly."
            )

        name = table.name.lower()
        db = table.db.lower()
        if not table.catalog and not db and name in cte_names:
            continue
        if table.catalog or db not in ("", "main") or name not in _ALLOWED_TABLES:
            allowed = ", ".join(f"main.{t}" for t in sorted(_ALLOWED_TABLES))
            raise SQLValidationError(
                f"Table not allowed: {table.sql(dialect='duckdb')}. Allowed tables: {allowed}"
            )


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

    _check_tables(statement)

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
