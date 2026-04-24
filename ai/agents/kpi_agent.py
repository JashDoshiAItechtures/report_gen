"""KPI Agent — parallel KPI SQL execution.

Fires all KPI SQL queries simultaneously using asyncio.gather(), then
falls back to the pre-verified KPI template library when fewer than 6
KPIs pass validation.
"""

import logging
from typing import Any

from ai.agents.base import BaseAgent

logger = logging.getLogger("agent.kpi")


class KpiAgent(BaseAgent):
    """Execute all KPI SQL queries concurrently and guarantee ≥6 results."""

    name = "kpi"

    def __init__(self, pipeline_ref) -> None:
        """
        Args:
            pipeline_ref: A ReportPipeline instance so we can reuse
                          _execute_kpi_sql, _fix_report_sql, etc.
        """
        super().__init__()
        self._pipe = pipeline_ref

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _exec_one_kpi(self, kpi: dict) -> dict:
        """Blocking wrapper — to be called via run_in_executor."""
        return self._pipe._execute_kpi_sql(kpi)

    def _exec_one_fallback_kpi(self, kpi_copy: dict) -> dict:
        """Execute a fallback KPI in the thread pool."""
        return self._pipe._execute_kpi_sql(kpi_copy)

    # ── Public async API ──────────────────────────────────────────────────────

    async def run(self, blueprint: dict, question: str) -> dict:
        """Run all KPI SQL queries in parallel, fill fallbacks as needed.

        Returns:
            {"kpis": [...], "agent_timing": {"kpi": <seconds>}}
        """
        async with self.timed():
            kpis: list[dict] = list(blueprint.get("kpis", []))
            self.logger.info("KPI agent — executing %d KPI queries in parallel", len(kpis))

            # ── Phase 1: execute all LLM KPIs concurrently ────────────────
            executed: list[dict] = await self.run_many(self._exec_one_kpi, kpis)

            valid_kpis = [
                k for k in executed
                if k.get("value") not in (None, "N/A", "")
                and not k.get("error")
            ]
            self.logger.info(
                "KPI agent — %d of %d LLM KPIs valid",
                len(valid_kpis), len(executed),
            )

            # ── Phase 2: fallback library (if < 6 passed) ─────────────────
            if len(valid_kpis) < 6:
                from ai.report_fallback_charts import detect_report_topic, get_fallback_kpis

                topic = detect_report_topic(question)
                self.logger.info(
                    "KPI agent — only %d KPIs — filling from fallback library (topic=%s)",
                    len(valid_kpis), topic,
                )
                fallback_kpis = get_fallback_kpis(topic)
                existing_ids = {k.get("id", "") for k in valid_kpis}

                # Filter fallbacks we don't already have, cap to needed count
                needed = [
                    dict(fb)  # shallow copy — don't mutate the template
                    for fb in fallback_kpis
                    if fb["id"] not in existing_ids
                ][:max(0, 6 - len(valid_kpis))]

                # Execute all needed fallbacks concurrently
                if needed:
                    fb_results = await self.run_many(self._exec_one_fallback_kpi, needed)
                    for fb_exec in fb_results:
                        if len(valid_kpis) >= 6:
                            break
                        kpi_val = fb_exec.get("value")
                        if kpi_val not in (None, "N/A", "") and not fb_exec.get("error"):
                            valid_kpis.append(fb_exec)
                            self.logger.info(
                                "KPI agent — fallback added: '%s' = %s",
                                fb_exec.get("label", "?"), kpi_val,
                            )

                self.logger.info(
                    "KPI agent — %d KPIs after fallback fill", len(valid_kpis)
                )

        return {
            "kpis": valid_kpis,
            "agent_timing": {"kpi": round(self._elapsed, 3)},
        }
