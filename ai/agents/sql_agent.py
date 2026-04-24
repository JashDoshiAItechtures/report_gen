"""SQL Agent — execute the detail table SQL query.

Responsible for populating the report's detail table (full tabular data).
Runs concurrently with KPI, Chart, and Insight agents.
"""

import logging

from ai.agents.base import BaseAgent

logger = logging.getLogger("agent.sql")


class SqlAgent(BaseAgent):
    """Execute the report's detail table SQL query in the thread pool."""

    name = "sql"

    def __init__(self, pipeline_ref) -> None:
        super().__init__()
        self._pipe = pipeline_ref

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _exec_table(self, table: dict) -> dict:
        """Blocking wrapper invoked in the thread pool."""
        return self._pipe._execute_table_sql(table)

    # ── Public async API ──────────────────────────────────────────────────────

    async def run(self, blueprint: dict, question: str) -> dict:
        """Execute the detail table SQL, return partial report dict.

        Returns:
            {"table": {...}, "agent_timing": {"sql": <seconds>}}
            If no table was in the blueprint, returns {"agent_timing": {...}}.
        """
        async with self.timed():
            table = blueprint.get("table")
            if not table:
                self.logger.info("SQL agent — no detail table in blueprint, skipping")
            else:
                self.logger.info("SQL agent — executing detail table SQL")
                table = await self.run_in_executor(self._exec_table, table)
                self.logger.info(
                    "SQL agent — table has %d rows",
                    len(table.get("data") or []),
                )

        result: dict = {"agent_timing": {"sql": round(self._elapsed, 3)}}
        if table is not None:
            result["table"] = table
        return result
