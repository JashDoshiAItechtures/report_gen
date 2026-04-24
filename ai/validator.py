"""SQL safety validation.

Two validators:
  validate_sql()              — SELECT-only (existing chat/report pipeline)
  validate_modification_sql() — UPDATE/INSERT/DELETE (modification pipeline)
"""

import re

_FORBIDDEN_KEYWORDS = [
    r"\bDROP\b",
    r"\bDELETE\b",
    r"\bUPDATE\b",
    r"\bALTER\b",
    r"\bTRUNCATE\b",
    r"\bINSERT\b",
    r"\bCREATE\b",
    r"\bGRANT\b",
    r"\bREVOKE\b",
    r"\bEXEC\b",
    r"\bEXECUTE\b",
]

_FORBIDDEN_PATTERN = re.compile("|".join(_FORBIDDEN_KEYWORDS), re.IGNORECASE)

# DDL / privilege keywords that must ALWAYS be blocked (even in modification path)
_ALWAYS_FORBIDDEN = [
    r"\bDROP\b",
    r"\bTRUNCATE\b",
    r"\bALTER\b",
    r"\bCREATE\b",
    r"\bGRANT\b",
    r"\bREVOKE\b",
    r"\bEXEC\b",
    r"\bEXECUTE\b",
]
_ALWAYS_FORBIDDEN_PATTERN = re.compile("|".join(_ALWAYS_FORBIDDEN), re.IGNORECASE)

# Pattern that allows only DML modification statements
_MODIFICATION_START = re.compile(r"^\s*(UPDATE|INSERT|DELETE)\b", re.IGNORECASE)


def validate_sql(sql: str) -> tuple[bool, str]:
    """Check if a SQL string is safe to execute as a SELECT query.

    Returns
    -------
    (is_safe, reason)
    """
    stripped = sql.strip().rstrip(";").strip()

    if not stripped:
        return False, "Empty query."

    # Must start with SELECT or WITH (CTE)
    if not re.match(r"^\s*(SELECT|WITH)\b", stripped, re.IGNORECASE):
        return False, "Only SELECT queries are allowed."

    # Check for forbidden keywords
    match = _FORBIDDEN_PATTERN.search(stripped)
    if match:
        return False, f"Forbidden keyword detected: {match.group().upper()}"

    return True, ""


def validate_modification_sql(sql: str) -> tuple[bool, str]:
    """Check if a SQL string is safe to execute as a data modification.

    Allows UPDATE / INSERT / DELETE with restrictions:
      - Must start with UPDATE, INSERT, or DELETE
      - UPDATE and DELETE must have a WHERE clause (prevents mass-modification)
      - DDL and privilege keywords are always blocked

    Returns
    -------
    (is_valid, reason)
    """
    stripped = sql.strip().rstrip(";").strip()

    if not stripped:
        return False, "Empty query."

    # Must start with a DML keyword
    if not _MODIFICATION_START.match(stripped):
        return False, (
            "Only UPDATE, INSERT, or DELETE are allowed for data modifications. "
            "SELECT queries should use the chat mode."
        )

    # Always-forbidden DDL / privilege keywords
    match = _ALWAYS_FORBIDDEN_PATTERN.search(stripped)
    if match:
        return False, f"Forbidden keyword: {match.group().upper()}"

    upper = stripped.upper()

    # UPDATE without WHERE → would modify every row in the table
    if upper.startswith("UPDATE") and "WHERE" not in upper:
        return False, (
            "UPDATE without a WHERE clause is not allowed — "
            "it would modify every row in the table."
        )

    # DELETE without WHERE → would delete every row
    if upper.startswith("DELETE") and "WHERE" not in upper:
        return False, (
            "DELETE without a WHERE clause is not allowed — "
            "it would delete every row in the table."
        )

    return True, ""


def check_sql_against_schema(sql: str, schema: dict[str, list]) -> tuple[bool, list[str]]:
    """Programmatically check that tables/columns in SQL exist in the schema.

    Returns (is_valid, list_of_issues).
    Much faster and more accurate than LLM-based critique.
    """
    issues: list[str] = []

    # Build lookup sets
    all_tables = {t.lower() for t in schema}
    table_columns: dict[str, set[str]] = {}
    for t, cols in schema.items():
        table_columns[t.lower()] = {c["column_name"].lower() for c in cols}
    all_columns = set()
    for cols in table_columns.values():
        all_columns |= cols

    sql_upper = sql.upper()

    # Extract table references (FROM / JOIN)
    table_refs = re.findall(
        r'(?:FROM|JOIN)\s+"?(\w+)"?', sql, re.IGNORECASE
    )
    for tref in table_refs:
        if tref.lower() not in all_tables:
            issues.append(f"Table '{tref}' not found in schema")

    # Basic check: if GROUP BY is present, verify SELECT has aggregation or is in GROUP BY
    if "GROUP BY" in sql_upper and "SELECT" in sql_upper:
        if not any(fn in sql_upper for fn in ["SUM(", "COUNT(", "AVG(", "MIN(", "MAX("]):
            issues.append("GROUP BY present but no aggregation function found")

    return (len(issues) == 0, issues)
