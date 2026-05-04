"""Chart Modification Pipeline — modify individual charts via natural language.

Supports:
- Changing chart type (bar → line, etc.)
- Changing axes / data dimensions
- Filtering data (WHERE conditions)
- Top-N / sorting
- Grouping by region, category, time period
- Smart prompt rewriting for vague instructions
- Modification history (context memory across turns)

Two-path design:
  1. Clear instruction  → LLM rewrites SQL / type / labels → execute → return updated chart
  2. Vague instruction  → PromptRewriter generates options → return 'clarify' response
     (user picks an option → that becomes the next clear instruction)
"""

import json
import logging
import re
from typing import Any

import dspy

from ai.groq_setup import get_lm
from ai.chart_modification_signatures import ChartModification, PromptRewriter
from ai.validator import validate_sql
from ai.report_generator import _fix_report_sql
from db.schema import format_schema
from db.executor import execute_sql

logger = logging.getLogger(__name__)


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


# Patterns that indicate a vague, non-actionable instruction
_VAGUE_PATTERNS = [
    r"^make\s+(it\s+)?(better|good|nice|great|cleaner|clearer|nicer|cool|pretty)\s*[.!]*$",
    r"^(improve|fix|enhance|update)\s+(it|this)\s*[.!]*$",
    r"^do\s+something\s*(else|different|new)\s*[.!]*$",
    r"^(looks?\s+bad|not\s+good|wrong|ugly)\s*[.!]*$",
    r"^change\s+it\s*[.!]*$",
    r"^(help|idk|dunno|i\s+don'?t\s+know)\s*[.!]*$",
    r"^(yes|no|ok|okay|sure|fine|great)\s*[.!]*$",
]


class ChartModificationPipeline:
    """Modifies a chart spec (SQL, type, labels) via a natural language instruction."""

    def __init__(self, provider: str = "groq") -> None:
        self.provider = provider
        self._lm = get_lm(provider)
        self._modify = dspy.Predict(ChartModification)
        self._rewrite = dspy.Predict(PromptRewriter)

    # ── Public API ────────────────────────────────────────────────────────────

    def modify(
        self,
        chart_spec: dict,
        instruction: str,
        history: list[str] | None = None,
    ) -> dict[str, Any]:
        """Apply a natural language modification to a chart.

        Args:
            chart_spec: Current chart spec (title, sql, type, x_label, y_label, data, …).
            instruction: User's NL instruction.
            history: List of previous instructions applied to this chart (context memory).

        Returns:
            dict with 'mode' key:
                'modified'   → chart updated; 'chart' has new spec, 'explanation' and 'sql' keys
                'clarify'    → instruction unclear; 'message' and 'options' keys
                'no_change'  → LLM determined nothing needs to change
                'error'      → failure; 'error' key has message
        """
        history = history or []

        if self._is_vague(instruction):
            return self._clarify_vague(instruction, chart_spec)

        schema_str = format_schema()
        history_str = "; ".join(history[-5:]) if history else ""

        logger.info(
            "ChartModificationPipeline — chart=%s instruction=%s history_len=%d",
            chart_spec.get("title", "?")[:50],
            instruction[:80],
            len(history),
        )

        with dspy.context(lm=self._lm):
            result = self._modify(
                instruction=instruction,
                current_sql=chart_spec.get("sql", ""),
                current_chart_type=chart_spec.get("type", "bar"),
                current_title=chart_spec.get("title", ""),
                current_x_label=chart_spec.get("x_label", "") or "",
                current_y_label=chart_spec.get("y_label", "") or "",
                schema_info=schema_str,
                modification_history=history_str,
            )

        new_sql          = (result.new_sql or "").strip().rstrip(";").strip()
        new_type         = (result.new_chart_type or "").strip()
        new_title        = (result.new_title or "").strip()
        new_x_label      = (result.new_x_label or "").strip()
        new_y_label      = (result.new_y_label or "").strip()
        new_color_scheme = (result.new_color_scheme or "").strip().lower()
        explanation      = (result.explanation or "").strip()
        confidence       = (result.confidence or "high").strip().lower()
        clarification    = (result.clarification_needed or "NONE").strip()

        # Low confidence → ask for clarification
        if confidence == "low" and clarification.upper() != "NONE" and clarification:
            options_list = self._fallback_options(chart_spec)
            return {
                "mode": "clarify",
                "message": clarification,
                "options": options_list,
            }

        # Build updated spec (without carrying old data; we'll update below)
        updated: dict = {k: v for k, v in chart_spec.items()}
        changed = False
        sql_changed_flag = False

        # ── SQL change ──────────────────────────────────────────────────────
        if new_sql and new_sql.upper() != "UNCHANGED":
            # Clean markdown/prose wrappers and apply schema auto-corrections
            new_sql = _clean_sql(new_sql)
            new_sql = _fix_report_sql(new_sql)

            is_safe, reason = validate_sql(new_sql)
            if not is_safe:
                return {"mode": "error", "error": f"Generated SQL was invalid: {reason}"}

            exec_result = execute_sql(new_sql)
            if not exec_result["success"]:
                return {"mode": "error", "error": f"Query failed: {exec_result['error']}"}

            new_data = exec_result.get("data") or []
            if not new_data:
                return {
                    "mode": "error",
                    "error": (
                        "The modified query returned no data. "
                        "Please refine your instruction."
                    ),
                }

            updated["sql"]  = new_sql
            updated["data"] = new_data
            changed = True
            sql_changed_flag = True
            logger.info(
                "ChartModificationPipeline — SQL updated (%d rows)", len(new_data)
            )
        # When SQL hasn't changed, do NOT set updated["data"] — the frontend
        # keeps its own full dataset (chartOriginalData[idx]).

        # ── Type change ─────────────────────────────────────────────────────
        if new_type and new_type.upper() != "UNCHANGED":
            updated["type"] = new_type
            changed = True

        # ── Label changes ───────────────────────────────────────────────────
        if new_title and new_title.upper() != "UNCHANGED":
            updated["title"] = new_title
            changed = True
        if new_x_label and new_x_label.upper() != "UNCHANGED":
            updated["x_label"] = new_x_label
            changed = True
        if new_y_label and new_y_label.upper() != "UNCHANGED":
            updated["y_label"] = new_y_label
            changed = True

        # ── Color scheme change ──────────────────────────────────────────────
        _VALID_SCHEMES = {"blues", "greens", "purples", "oranges", "mixed", "gradient"}
        if new_color_scheme and new_color_scheme.upper() != "UNCHANGED" and new_color_scheme in _VALID_SCHEMES:
            updated["color_scheme"] = new_color_scheme
            changed = True

        if not changed:
            return {
                "mode": "no_change",
                "message": "No changes were needed — the chart already matches your request.",
            }

        # Remove sample data that was echoed back if no new data was fetched
        if not sql_changed_flag:
            updated.pop("data", None)

        return {
            "mode": "modified",
            "chart": updated,
            "sql_changed": sql_changed_flag,
            "explanation": explanation or "Chart updated successfully.",
            "sql": updated.get("sql", chart_spec.get("sql", "")),
        }

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _is_vague(instruction: str) -> bool:
        instr = instruction.strip().lower()
        return any(re.match(p, instr) for p in _VAGUE_PATTERNS)

    def _clarify_vague(self, instruction: str, chart_spec: dict) -> dict:
        """Use PromptRewriter LLM to suggest concrete alternatives."""
        data  = chart_spec.get("data") or []
        cols  = list(data[0].keys()) if data else []
        ctx   = (
            f"Title: '{chart_spec.get('title', 'Chart')}', "
            f"Type: {chart_spec.get('type', 'bar')}, "
            f"Columns: {', '.join(cols)}"
        )

        try:
            with dspy.context(lm=self._lm):
                r = self._rewrite(
                    vague_instruction=instruction,
                    chart_context=ctx,
                )
            raw = (r.options or "[]").strip()
            try:
                options = json.loads(raw)
                if not isinstance(options, list):
                    options = [str(options)]
            except Exception:
                options = [r.rewritten_instruction or instruction]
        except Exception as exc:
            logger.warning("PromptRewriter LLM failed: %s", exc)
            options = self._fallback_options(chart_spec)

        return {
            "mode": "clarify",
            "message": "Your instruction is a bit vague. Did you mean one of these?",
            "options": options[:4],
        }

    @staticmethod
    def _fallback_options(chart_spec: dict) -> list[str]:
        """Return generic options based on current chart type."""
        ctype = (chart_spec.get("type") or "bar").lower()
        if ctype in ("bar", "horizontalbar"):
            return [
                "Show top 5 items by value",
                "Convert to line chart",
                "Convert to pie / donut chart",
                "Group by category",
            ]
        if ctype in ("line", "area"):
            return [
                "Aggregate by month",
                "Aggregate by year",
                "Convert to bar chart",
                "Filter to last 6 months",
            ]
        if ctype in ("pie", "doughnut"):
            return [
                "Show top 5 slices only",
                "Convert to bar chart",
                "Convert to horizontal bar",
                "Filter by date range",
            ]
        return [
            "Change chart type",
            "Show top 10 items",
            "Add date range filter",
            "Group by category",
        ]
