"""Safe SQL execution against PostgreSQL.

Supports both SELECT queries and approved modification statements
(UPDATE / INSERT / DELETE) via the allow_modification flag.
Modification queries are only executed after user confirmation in the UI.
"""

from typing import Any

from sqlalchemy import text

from db.connection import get_engine


def execute_sql(sql: str, allow_modification: bool = False) -> dict[str, Any]:
    """Execute a SQL query and return results or error.

    Parameters
    ----------
    sql : str
        The SQL statement to execute.
    allow_modification : bool
        When True, executes UPDATE/INSERT/DELETE statements.
        When False (default), only SELECT queries are allowed.

    Returns
    -------
    dict with keys:
        success       : bool
        data          : list[dict]  (SELECT results)
        columns       : list[str]   (SELECT results)
        rows_affected : int         (modification results)
        error         : str         (on failure)
    """
    if allow_modification:
        # Use the modification validator (allows DML, blocks DDL)
        from ai.validator import validate_modification_sql
        is_valid, reason = validate_modification_sql(sql)
        if not is_valid:
            return {
                "success": False, "data": [], "columns": [],
                "rows_affected": 0, "error": reason,
            }

        try:
            with get_engine().begin() as conn:   # .begin() auto-commits on success
                result = conn.execute(text(sql))
                return {
                    "success": True, "data": [], "columns": [],
                    "rows_affected": result.rowcount, "error": "",
                }
        except Exception as exc:
            return {
                "success": False, "data": [], "columns": [],
                "rows_affected": 0, "error": str(exc),
            }
    else:
        # SELECT-only path (original behaviour)
        from ai.validator import validate_sql
        is_safe, reason = validate_sql(sql)
        if not is_safe:
            return {"success": False, "data": [], "columns": [], "rows_affected": 0, "error": reason}

        try:
            with get_engine().connect() as conn:
                result = conn.execute(text(sql))
                columns = list(result.keys())
                rows = [dict(zip(columns, row)) for row in result.fetchall()]
                return {"success": True, "data": rows, "columns": columns, "rows_affected": 0, "error": ""}
        except Exception as exc:
            return {"success": False, "data": [], "columns": [], "rows_affected": 0, "error": str(exc)}
