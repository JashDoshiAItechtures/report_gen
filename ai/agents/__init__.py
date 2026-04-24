"""Parallel report-generation agents package.

Each agent is an autonomous async unit responsible for one slice of
the report pipeline.  They are launched simultaneously via asyncio.gather()
by ReportPipeline._generate_parallel() and their results are merged into
the final dashboard report.

Agents:
  KpiAgent        — execute all KPI SQL queries concurrently
  ChartAgent      — execute all chart SQL queries concurrently
  InsightAgent    — build data-backed insights in parallel
  SqlAgent        — execute detail table SQL
  ValidationAgent — cross-check KPI values against DB control scalars
"""

from ai.agents.kpi_agent import KpiAgent
from ai.agents.chart_agent import ChartAgent
from ai.agents.insight_agent import InsightAgent
from ai.agents.sql_agent import SqlAgent
from ai.agents.validation_agent import ValidationAgent

__all__ = [
    "KpiAgent",
    "ChartAgent",
    "InsightAgent",
    "SqlAgent",
    "ValidationAgent",
]
