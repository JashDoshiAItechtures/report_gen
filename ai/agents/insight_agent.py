"""Insight Agent — parallel data-backed insight generation.

Fires immediately after topic detection — does NOT wait for KPI/chart SQL.
Runs the topic-specific fallback insight SQL queries in the thread pool
concurrently, producing ≥6 real database-verified insights with correct
sentiment classification based on actual metric thresholds.

LLM-generated insights are ALWAYS discarded because the LLM consistently:
  1. Uses placeholder names ("Customer A", "Product X", "Category X")
  2. Marks everything as "positive" regardless of actual data
  3. Fabricates numbers not from the database
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

        Always uses the data-backed fallback insights because the LLM
        consistently hallucinates placeholder names and incorrect sentiments.

        Returns:
            {"insights": [...], "agent_timing": {"insight": <seconds>}}
        """
        async with self.timed():
            from ai.report_fallback_charts import detect_report_topic
            from ai.report_fallback_insights import get_fallback_insights

            topic: str = blueprint.get("topic") or detect_report_topic(question)
            self.logger.info("Insight agent — topic=%s", topic)

            # ── Run insight SQL queries in thread pool ────────────────────
            def _build_insights():
                return get_fallback_insights(topic, existing_titles=None)

            fallback_insights: list = await self.run_in_executor(_build_insights)

            # Always use fallback insights — they have real data + correct sentiment
            # LLM insights are discarded entirely to prevent hallucinated placeholders
            merged = fallback_insights[:8]  # cap at 8

            self.logger.info(
                "Insight agent — %d data-backed insights generated (topic=%s)",
                len(merged), topic,
            )

        return {
            "insights": merged,
            "agent_timing": {"insight": round(self._elapsed, 3)},
        }
