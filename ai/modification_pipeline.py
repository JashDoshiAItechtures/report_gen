"""Modification pipeline — generates and executes safe DML SQL (UPDATE/INSERT/DELETE).

Two-phase safety design:
  Phase 1: preview()  — LLM generates SQL + summary, shown to user for approval
  Phase 2: execute()  — Runs the SQL only after user explicitly approves

The preview SQL is NEVER auto-executed.
"""

import json
import logging
import re
from datetime import date
from typing import Any

import dspy

from ai.groq_setup import get_lm
from ai.modification_signatures import ModificationGeneration
from ai.validator import validate_modification_sql
from db.schema import format_schema
from db.executor import execute_sql

logger = logging.getLogger(__name__)


class ModificationPipeline:
    """Pipeline that generates a safe SQL modification for user confirmation,
    then executes it only after explicit approval."""

    def __init__(self, provider: str = "groq"):
        self.provider = provider
        self._lm = get_lm(provider)
        self.generate = dspy.Predict(ModificationGeneration)

    # ── public API ──────────────────────────────────────────────────────────

    def preview(self, question: str) -> dict[str, Any]:
        """Generate a modification SQL for the user to review.

        Returns a dict with mode='confirm_modify' and all preview data.
        Does NOT execute the SQL.
        """
        schema_str = format_schema()

        # Optionally fetch a small sample of current data for the LLM to use
        # in building precise WHERE conditions
        current_data = self._fetch_relevant_sample(question, schema_str)

        logger.info("ModificationPipeline.preview — generating SQL for: %s", question[:80])

        with dspy.context(lm=self._lm):
            result = self.generate(
                question=question,
                schema_info=schema_str,
                current_data=current_data,
            )

        sql = (result.sql or "").strip().rstrip(";").strip()
        intent_summary = (result.intent_summary or "").strip()
        risk_level = (result.risk_level or "medium").strip().lower()
        rows_estimate = (result.rows_affected_estimate or "unknown").strip()

        # Safety check: LLM may refuse to generate if too vague
        if sql.upper() == "UNSAFE" or not sql:
            return {
                "mode": "modify_error",
                "error": intent_summary or (
                    "The request is too vague to execute safely. "
                    "Please specify which record(s) to modify (e.g. by ID or SKU)."
                ),
            }

        # Validate the generated SQL
        is_valid, reason = validate_modification_sql(sql)
        if not is_valid:
            logger.warning("ModificationPipeline: SQL rejected — %s", reason)
            return {
                "mode": "modify_error",
                "error": f"Cannot generate safe SQL for this request: {reason}",
            }

        logger.info(
            "ModificationPipeline.preview — returning confirm_modify | risk=%s | rows=%s",
            risk_level,
            rows_estimate,
        )

        return {
            "mode": "confirm_modify",
            "pending_sql": sql,
            "intent_summary": intent_summary,
            "risk_level": risk_level,
            "rows_affected_estimate": rows_estimate,
        }

    def execute(self, sql: str) -> dict[str, Any]:
        """Execute a previously previewed SQL statement after user approval.

        Always re-validates the SQL before executing — the client cannot
        bypass safety checks by sending raw SQL.
        """
        sql = (sql or "").strip().rstrip(";").strip()

        # Re-validate (defence-in-depth — never trust client SQL blindly)
        is_valid, reason = validate_modification_sql(sql)
        if not is_valid:
            logger.warning("ModificationPipeline.execute: SQL rejected at execution — %s", reason)
            return {
                "mode": "modify_error",
                "error": f"Execution refused: {reason}",
            }

        logger.info("ModificationPipeline.execute — running approved SQL: %s", sql[:120])

        result = execute_sql(sql, allow_modification=True)

        if not result["success"]:
            logger.error("ModificationPipeline.execute — DB error: %s", result["error"])
            return {
                "mode": "modify_error",
                "error": result["error"],
            }

        rows_affected = result.get("rows_affected", 0)
        logger.info("ModificationPipeline.execute — success | rows_affected=%s", rows_affected)

        return {
            "mode": "modify_success",
            "rows_affected": rows_affected,
            "sql": sql,
            "message": (
                f"Done! {rows_affected} row{'s' if rows_affected != 1 else ''} "
                f"updated successfully."
            ),
        }

    # ── private helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _fetch_relevant_sample(question: str, schema_str: str) -> str:
        """Try to fetch a small data sample that is relevant to the modification.

        Looks for product SKUs, customer names, or order IDs in the question
        and runs a quick SELECT to give the LLM precise current values.
        Falls back to empty string if nothing useful can be found.
        """
        q = question.lower()

        # Detect mention of a specific product SKU (e.g. PROD-0266)
        sku_match = re.search(r"[A-Z]{2,6}-\d{3,6}", question, re.IGNORECASE)
        if sku_match:
            sku = sku_match.group(0).upper().replace("'", "''")
            res = execute_sql(
                f"SELECT product_id, product_name, selling_price "
                f"FROM product_variant pv "
                f"JOIN product_master pm ON pv.product_id = pm.product_id "
                f"WHERE pv.variant_sku = '{sku}' LIMIT 5"
            )
            if res["success"] and res["data"]:
                return f"Current data for SKU {sku}: {json.dumps(res['data'], default=str)}"

        # Detect product name mention
        if "product" in q or "price" in q or "selling" in q:
            res = execute_sql(
                "SELECT variant_sku, product_name, selling_price "
                "FROM product_variant pv "
                "JOIN product_master pm ON pv.product_id = pm.product_id "
                "LIMIT 10"
            )
            if res["success"] and res["data"]:
                return f"Sample product data: {json.dumps(res['data'], default=str)}"

        return ""  # No relevant sample found — LLM will work from schema only
