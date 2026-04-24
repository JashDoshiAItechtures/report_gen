"""Insight Agent — parallel data-backed insight generation.

Fires immediately after topic detection — does NOT wait for KPI/chart SQL.
Runs the topic-specific fallback insight SQL queries in the thread pool
concurrently, producing ≥6 real database-verified insights with zero
sequential blocking.
"""

import logging

from ai.agents.base import BaseAgent

logger = logging.getLogger("agent.insight")


class InsightAgent(BaseAgent):
    """Build data-backed insights from DB in parallel with KPI/Chart agents."""

    name = "insight"

    # ── Public async API ──────────────────────────────────────────────────────

    async def run(self, blueprint: dict, question: str) -> dict:
        """Compute insights using the fallback library (SQL-driven, not LLM).

        The fallback library runs verified SQL queries internally.  We wrap
        the entire call in run_in_executor so all those DB round-trips don't
        block the event loop.

        Returns:
            {"insights": [...], "agent_timing": {"insight": <seconds>}}
        """
        async with self.timed():
            # ── Determine topic (pure Python, instant) ────────────────────
            from ai.report_fallback_charts import detect_report_topic
            from ai.report_fallback_insights import get_fallback_insights

            topic: str = blueprint.get("topic") or detect_report_topic(question)
            self.logger.info("Insight agent — topic=%s", topic)

            # LLM-provided insights (may be empty or hallucinated)
            llm_insights: list = blueprint.get("insights") or []
            existing_titles = {
                ins.get("title", "") if isinstance(ins, dict) else str(ins)
                for ins in llm_insights
            }

            # ── Run insight SQL queries in thread pool ────────────────────
            # get_fallback_insights() makes multiple DB calls internally;
            # we run the whole thing in the executor to avoid blocking.
            def _build_insights():
                return get_fallback_insights(topic, existing_titles)

            fallback_insights: list = await self.run_in_executor(_build_insights)

            # Merge: keep LLM insights first, then fill to ≥6 from fallback
            merged = list(llm_insights)
            for fb_ins in fallback_insights:
                if len(merged) >= 6:
                    break
                merged.append(fb_ins)

            self.logger.info(
                "Insight agent — %d insights total (%d LLM + %d fallback)",
                len(merged), len(llm_insights), len(fallback_insights),
            )

        return {
            "insights": merged,
            "agent_timing": {"insight": round(self._elapsed, 3)},
        }
