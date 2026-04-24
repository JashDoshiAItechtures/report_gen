"""Chart Agent — parallel chart SQL execution.

Fires all chart SQL queries simultaneously using asyncio.gather(), then
falls back to the pre-verified chart template library when fewer than 5
charts have valid data.  Also applies smart type fixing and diversity
enforcement after all SQL has resolved.
"""

import logging

from ai.agents.base import BaseAgent

logger = logging.getLogger("agent.chart")


class ChartAgent(BaseAgent):
    """Execute all chart SQL queries concurrently and guarantee ≥5 charts."""

    name = "chart"

    def __init__(self, pipeline_ref) -> None:
        """
        Args:
            pipeline_ref: A ReportPipeline instance so we can reuse
                          _execute_chart_sql, _smart_fix_chart_type etc.
        """
        super().__init__()
        self._pipe = pipeline_ref

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _exec_one_chart(self, chart: dict) -> dict:
        """Blocking wrapper — to be called via run_in_executor."""
        return self._pipe._execute_chart_sql(chart)

    # ── Public async API ──────────────────────────────────────────────────────

    async def run(self, blueprint: dict, question: str) -> dict:
        """Run all chart SQL queries in parallel, fill fallbacks, fix types.

        Returns:
            {"charts": [...], "agent_timing": {"chart": <seconds>}}
        """
        async with self.timed():
            charts: list[dict] = list(blueprint.get("charts", []))
            self.logger.info(
                "Chart agent — executing %d chart queries in parallel", len(charts)
            )

            # ── Phase 1: execute all LLM charts concurrently ──────────────
            executed: list[dict] = await self.run_many(self._exec_one_chart, charts)

            valid_charts: list[dict] = []
            for chart in executed:
                if chart.get("error"):
                    self.logger.info(
                        "Chart agent — removing '%s' (error: %s)",
                        chart.get("title", "?"), chart.get("error"),
                    )
                    continue
                data = chart.get("data") or []
                if not data:
                    self.logger.info(
                        "Chart agent — removing '%s' (empty data)", chart.get("title", "?")
                    )
                    continue
                row_keys = list(data[0].keys()) if data else []
                if len(row_keys) < 2:
                    self.logger.info(
                        "Chart agent — removing '%s' (only 1 column)", chart.get("title", "?")
                    )
                    continue
                value_keys = row_keys[1:]
                all_zero = all(
                    all((v := row.get(k)) is None or v == 0 or v == "" for k in value_keys)
                    for row in data
                )
                if all_zero:
                    self.logger.info(
                        "Chart agent — removing '%s' (all values zero)", chart.get("title", "?")
                    )
                    continue
                valid_charts.append(chart)

            self.logger.info(
                "Chart agent — %d of %d LLM charts valid",
                len(valid_charts), len(executed),
            )

            # ── Phase 2: fallback library (if < 5 passed) ─────────────────
            if len(valid_charts) < 5:
                from ai.report_fallback_charts import detect_report_topic, get_fallback_charts

                topic = detect_report_topic(question)
                self.logger.info(
                    "Chart agent — only %d charts — filling from fallback library (topic=%s)",
                    len(valid_charts), topic,
                )
                fallback_charts = get_fallback_charts(topic)
                existing_ids = {c.get("id", "") for c in valid_charts}

                needed = [
                    dict(fb)  # shallow copy
                    for fb in fallback_charts
                    if fb["id"] not in existing_ids
                ][:max(0, 6 - len(valid_charts))]

                if needed:
                    fb_results = await self.run_many(self._exec_one_chart, needed)
                    for fb_exec in fb_results:
                        if len(valid_charts) >= 6:
                            break
                        fb_data = fb_exec.get("data") or []
                        if (
                            len(fb_data) >= 2
                            and not fb_exec.get("error")
                        ):
                            self._pipe._smart_fix_chart_type(fb_exec)
                            valid_charts.append(fb_exec)
                            self.logger.info(
                                "Chart agent — fallback added: '%s' (%d rows)",
                                fb_exec.get("title", "?"), len(fb_data),
                            )

                self.logger.info(
                    "Chart agent — %d charts after fallback fill", len(valid_charts)
                )

            # ── Phase 3: smart type fixes (data shape corrections) ─────────
            for chart in valid_charts:
                self._pipe._smart_fix_chart_type(chart)

            # ── Phase 4: enforce chart type diversity ──────────────────────
            valid_charts = self._pipe._enforce_chart_diversity(valid_charts)

        return {
            "charts": valid_charts,
            "agent_timing": {"chart": round(self._elapsed, 3)},
        }
