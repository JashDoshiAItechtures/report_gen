"""Report Edit Agent — process full report editing commands via natural language.

Routing table:
  add_chart    → generate SQL via SingleChartGeneration → execute → append to charts
  modify_chart → delegate to ChartModificationPipeline (preserves context memory)
  remove_chart → remove matching chart by title
  add_kpi      → generate SQL via SingleKPIGeneration → execute → prepend to kpis
  remove_kpi   → remove matching KPI by title/label

Design rule: never mutate the original report dict; always work on a deep copy.
"""

import copy
import logging
import re
import uuid
from typing import Any

import dspy

from ai.groq_setup import get_lm
from ai.report_edit_signatures import (
    ReportEditIntent,
    SingleChartGeneration,
    SingleKPIGeneration,
)
from ai.chart_modification_pipeline import ChartModificationPipeline
from ai.validator import validate_sql
from ai.report_generator import _fix_report_sql, ReportPipeline
from db.schema import format_schema
from db.executor import execute_sql

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

_TOP_N_RE = re.compile(
    r"\b(?:top|bottom|least|lowest|best|worst)\s+(\d+)\b"
    r"|\blimit\s+(\d+)\b",
    re.IGNORECASE,
)


def _extract_top_n(command: str) -> int | None:
    """Return the first explicit N from 'top N', 'bottom N', 'limit N' etc., or None."""
    m = _TOP_N_RE.search(command)
    if m:
        raw = m.group(1) or m.group(2)
        return int(raw)
    return None


def _enforce_limit(sql: str, n: int) -> str:
    """Replace or append LIMIT N in a SQL query."""
    sql = re.sub(r"\bLIMIT\s+\d+\b", f"LIMIT {n}", sql, flags=re.IGNORECASE)
    if not re.search(r"\bLIMIT\b", sql, re.IGNORECASE):
        sql = sql.rstrip(";,").rstrip() + f" LIMIT {n}"
    return sql


def _clean_sql(raw: str) -> str:
    """Strip markdown fences and trailing prose from LLM SQL output."""
    sql = raw.strip()
    if sql.startswith("```"):
        lines = sql.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        sql = "\n".join(lines).strip()
    import re as _re
    match = _re.search(
        r"((?:SELECT|WITH)\b[\s\S]*?)(;|\n\n(?=[A-Z][a-z])|$)",
        sql, _re.IGNORECASE,
    )
    if match:
        sql = match.group(1).strip()
    sql = sql.rstrip(";")
    return sql


def _extract_mention(command: str) -> tuple[str | None, str]:
    """Extract @Mention from command.  Returns (mention_raw, original_command)."""
    m = re.search(r'@([\w-]+)', command)
    if m:
        mention = m.group(1).replace("-", " ").strip()
        return mention, command
    return None, command


def _build_summary(report: dict) -> str:
    charts = report.get("charts") or []
    kpis   = report.get("kpis")   or []
    ctitles = [c.get("title", f"Chart {i+1}") for i, c in enumerate(charts)]
    ktitles = [
        k.get("label") or k.get("title") or k.get("id") or f"KPI {i+1}"
        for i, k in enumerate(kpis)
    ]
    return f"chart_titles: {ctitles}\nkpi_titles: {ktitles}"


def _find(items: list[dict], target: str, *fields: str) -> int:
    """Find the index of the best-matching item by checking multiple fields."""
    if not fields:
        fields = ("title", "label", "id")
        
    import re
    def normalize(s: str) -> str:
        return re.sub(r'[^a-z0-9]', '', (s or "").lower())
        
    t_norm = normalize(target)
    if not t_norm:
        return -1
        
    # Exact match after normalization
    for i, item in enumerate(items):
        for f in fields:
            if normalize(item.get(f, "")) == t_norm:
                return i
                
    # Partial match
    for i, item in enumerate(items):
        for f in fields:
            v_norm = normalize(item.get(f, ""))
            if v_norm and (t_norm in v_norm or v_norm in t_norm):
                return i
                
    return -1


# ── Agent ─────────────────────────────────────────────────────────────────────

class ReportEditAgent:
    """Routes natural-language report editing commands to the correct handler."""

    def __init__(self, provider: str = "groq") -> None:
        self.provider   = provider
        self._lm        = get_lm(provider)
        self._intent    = dspy.Predict(ReportEditIntent)
        self._gen_chart = dspy.Predict(SingleChartGeneration)
        self._gen_kpi   = dspy.Predict(SingleKPIGeneration)

    # ── Public API ────────────────────────────────────────────────────────────

    def chat_edit(
        self,
        report: dict,
        command: str,
        history: list[str] | None = None,
    ) -> dict[str, Any]:
        """Process one editing command against the current report.

        Returns:
            mode='updated'   → { report, message }
            mode='clarify'   → { message, options }
            mode='no_change' → { message }
            mode='error'     → { error }
        """
        history      = history or []
        schema_str   = format_schema()
        report_summ  = _build_summary(report)
        mention_tgt, _ = _extract_mention(command)

        logger.info("ReportEditAgent — command=%s mention=%s", command[:80], mention_tgt or "–")

        with dspy.context(lm=self._lm):
            parsed = self._intent(
                command=command,
                report_summary=report_summ,
                schema_info=schema_str,
            )

        intent        = (parsed.intent        or "unknown").strip().lower()
        target        = (parsed.target        or "unknown").strip()
        detail        = (parsed.modification_detail or "").strip()
        confidence    = (parsed.confidence    or "high").strip().lower()
        clarification = (parsed.clarification or "NONE").strip()

        # @mention overrides LLM target
        if mention_tgt:
            target = mention_tgt

        logger.info("ReportEditAgent — intent=%s target=%s conf=%s", intent, target, confidence)

        if confidence == "low" and clarification.upper() != "NONE" and clarification:
            return {
                "mode": "clarify",
                "message": clarification,
                "options": self._fallback_opts(report),
            }

        updated = copy.deepcopy(report)

        if   intent == "add_chart":    return self._add_chart(updated, detail or command, schema_str)
        elif intent == "modify_chart": return self._modify_chart(updated, target, detail or command, history)
        elif intent == "remove_chart": return self._remove_chart(updated, target)
        elif intent == "add_kpi":      return self._add_kpi(updated, detail or command, schema_str)
        elif intent == "remove_kpi":   return self._remove_kpi(updated, target)
        else:
            return {
                "mode": "clarify",
                "message": (
                    "I'm not sure what to do. Try: "
                    "\"add bar chart for top 10 products\", "
                    "\"@Sales-Chart convert to line\", or "
                    "\"remove Revenue KPI\"."
                ),
                "options": self._fallback_opts(report),
            }

    # ── Chart handlers ────────────────────────────────────────────────────────

    def _add_chart(self, report: dict, description: str, schema_str: str) -> dict:
        charts   = report.get("charts") or []
        existing = [c.get("title", "") for c in charts]

        with dspy.context(lm=self._lm):
            r = self._gen_chart(
                description=description,
                schema_info=schema_str,
                existing_titles=str(existing),
            )

        sql        = _clean_sql(r.sql or "")
        sql        = _fix_report_sql(sql)
        chart_type = (r.chart_type or "bar").strip()
        title      = (r.title      or description[:40]).strip()
        x_label    = (r.x_label   or "").strip()
        y_label    = (r.y_label   or "").strip()

        if not sql:
            return {"mode": "error", "error": "LLM failed to generate SQL for the new chart."}

        # Enforce explicit top-N from the user's command
        top_n = _extract_top_n(description)
        if top_n:
            sql = _enforce_limit(sql, top_n)

        is_safe, reason = validate_sql(sql)
        if not is_safe:
            return {"mode": "error", "error": f"Generated SQL is invalid: {reason}"}

        res = execute_sql(sql)
        if not res["success"]:
            return {"mode": "error", "error": f"Chart query failed: {res['error']}"}

        data = res.get("data") or []
        if not data:
            return {"mode": "error", "error": "The new chart query returned no data."}

        new_chart = {
            "id":           f"chart_edit_{uuid.uuid4().hex[:8]}",
            "title":        title,
            "type":         chart_type,
            "sql":          sql,
            "x_label":      x_label,
            "y_label":      y_label,
            "color_scheme": "blues",
            "data":         data,
            "chart_insight": "",
            "explanation":  {"what": title, "how": "", "insight": "", "type": chart_type},
        }

        report["charts"] = charts + [new_chart]
        logger.info("ReportEditAgent — added chart '%s' (%d rows)", title, len(data))
        return {
            "mode":    "updated",
            "report":  report,
            "message": f"Added chart \"{title}\" ({len(data)} rows).",
        }

    def _modify_chart(
        self, report: dict, target: str, instruction: str, history: list
    ) -> dict:
        charts = report.get("charts") or []
        idx    = _find(charts, target)

        if idx == -1:
            avail = [c.get("title") for c in charts]
            return {"mode": "error", "error": f"Chart '{target}' not found. Available: {avail}"}

        pipeline = ChartModificationPipeline(provider=self.provider)
        result   = pipeline.modify(charts[idx], instruction, history)

        if result["mode"] == "modified":
            updated_spec = {**charts[idx], **result["chart"]}
            if result.get("sql_changed") and result["chart"].get("data"):
                updated_spec["data"] = result["chart"]["data"]
            report["charts"][idx] = updated_spec
            return {"mode": "updated", "report": report, "message": result.get("explanation", "Chart updated.")}

        elif result["mode"] == "clarify":
            return result

        elif result["mode"] == "no_change":
            return {"mode": "no_change", "message": result.get("message", "No changes needed.")}

        return result  # propagate error

    def _remove_chart(self, report: dict, target: str) -> dict:
        charts = report.get("charts") or []
        idx    = _find(charts, target)

        if idx == -1:
            return {"mode": "error", "error": f"Chart '{target}' not found."}

        removed = charts[idx].get("title", "")
        report["charts"] = [c for i, c in enumerate(charts) if i != idx]
        return {"mode": "updated", "report": report, "message": f"Removed chart \"{removed}\"."}

    # ── KPI handlers ──────────────────────────────────────────────────────────

    def _add_kpi(self, report: dict, description: str, schema_str: str) -> dict:
        kpis     = report.get("kpis") or []
        existing = [k.get("label") or k.get("title") or k.get("id") or "" for k in kpis]

        with dspy.context(lm=self._lm):
            r = self._gen_kpi(
                description=description,
                schema_info=schema_str,
                existing_titles=str(existing),
            )

        sql   = _clean_sql(r.sql or "")
        logger.info("KPI raw SQL from LLM: %s", sql)
        sql   = _fix_report_sql(sql)
        # KPI queries must never have LIMIT — strip it as a safety net
        sql   = re.sub(r"\bLIMIT\s+\d+\b", "", sql, flags=re.IGNORECASE).strip()
        # Safety: replace stub 'orders' table with real 'sales_order'
        sql   = re.sub(r"\bFROM\s+orders\b", "FROM sales_order", sql, flags=re.IGNORECASE)
        sql   = re.sub(r"\bJOIN\s+orders\b", "JOIN sales_order", sql, flags=re.IGNORECASE)
        # Safety: status values in DB are lowercase — normalise any quoted status literals
        sql   = re.sub(
            r"(status\s*=\s*)'([^']+)'",
            lambda m: m.group(1) + "'" + m.group(2).lower() + "'",
            sql, flags=re.IGNORECASE,
        )
        logger.info("KPI final SQL: %s", sql)
        title = (r.title  or description[:30]).strip()
        fmt   = (r.format or "number").strip().lower()
        expl_text = (r.explanation or "").strip()
        if fmt not in ("currency", "percent", "number"):
            fmt = "number"

        if not sql:
            return {"mode": "error", "error": "LLM failed to generate SQL for the new KPI."}

        is_safe, reason = validate_sql(sql)
        if not is_safe:
            return {"mode": "error", "error": f"Generated SQL is invalid: {reason}"}

        res = execute_sql(sql)
        if not res["success"]:
            return {"mode": "error", "error": f"KPI query failed: {res['error']}"}

        data = res.get("data") or []
        if not data:
            return {"mode": "error", "error": "KPI query returned no data."}

        value = list(data[0].values())[0]

        new_kpi = {
            "id":          f"kpi_edit_{uuid.uuid4().hex[:6]}",
            "label":       title,
            "title":       title,
            "value":       value,
            "format":      fmt,
            "change":      None,
            "change_type": None,
            "sql":         sql,
            "explanation": {
                "what":    title,
                "how":     expl_text or f"Calculated using SQL: {sql}",
                "insight": "",
            },
        }

        report["kpis"] = [new_kpi] + kpis
        logger.info("ReportEditAgent — added KPI '%s' = %s", title, value)
        return {
            "mode":    "updated",
            "report":  report,
            "message": f"Added KPI \"{title}\" = {value}.",
        }

    def _remove_kpi(self, report: dict, target: str) -> dict:
        kpis = report.get("kpis") or []
        idx  = _find(kpis, target)

        if idx == -1:
            return {"mode": "error", "error": f"KPI '{target}' not found."}

        removed = kpis[idx].get("label") or kpis[idx].get("title") or ""
        report["kpis"] = [k for i, k in enumerate(kpis) if i != idx]
        return {"mode": "updated", "report": report, "message": f"Removed KPI \"{removed}\"."}

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _fallback_opts(report: dict) -> list[str]:
        charts = report.get("charts") or []
        kpis   = report.get("kpis")   or []
        opts   = ["Add a bar chart for top 10 products by revenue"]
        if charts:
            slug = charts[0].get("title", "Chart 1").replace(" ", "-")
            opts.append(f"@{slug} convert to line chart")
        if charts:
            opts.append(f"Remove \"{charts[-1].get('title', 'last chart')}\" chart")
        if kpis:
            lbl = kpis[0].get("label") or kpis[0].get("title") or "KPI"
            opts.append(f"Remove \"{lbl}\" KPI")
        else:
            opts.append("Add total revenue KPI")
        return opts[:4]
