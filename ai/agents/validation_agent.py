"""Validation Agent — cross-check KPI values against DB control scalars.

Runs a fixed set of authoritative SQL queries ("control queries") in
parallel with the other agents.  After all agents have finished and the
report is merged, the orchestrator calls validate() to produce a
structured verification report.

The validation results are:
  - Included in the API response as report["validation"]
  - Logged for observability
  - Used to flag KPIs that deviate significantly from DB ground truth
"""

import logging
from typing import Any

from ai.agents.base import BaseAgent

logger = logging.getLogger("agent.validation")

# ── Control queries — one authoritative scalar per metric ─────────────────────
# These are pre-verified against the production schema.
_CONTROL_QUERIES: list[dict] = [
    {
        "id": "total_closed_revenue",
        "label": "Total Closed Revenue",
        "sql": "SELECT ROUND(SUM(total_amount)::numeric,2) FROM sales_order WHERE status='closed'",
        "format": "currency",
    },
    {
        "id": "total_closed_orders",
        "label": "Total Closed Orders",
        "sql": "SELECT COUNT(*) FROM sales_order WHERE status='closed'",
        "format": "integer",
    },
    {
        "id": "total_open_orders",
        "label": "Total Open Orders",
        "sql": "SELECT COUNT(*) FROM sales_order WHERE status='open'",
        "format": "integer",
    },
    {
        "id": "fulfilment_rate",
        "label": "Fulfilment Rate (%)",
        "sql": (
            "SELECT ROUND(100.0*COUNT(*) FILTER(WHERE status='closed')"
            "/NULLIF(COUNT(*),0),1) FROM sales_order"
        ),
        "format": "percent",
    },
    {
        "id": "cancellation_rate",
        "label": "Cancellation Rate (%)",
        "sql": (
            "SELECT ROUND(100.0*COUNT(*) FILTER(WHERE status='cancelled')"
            "/NULLIF(COUNT(*),0),1) FROM sales_order"
        ),
        "format": "percent",
    },
    {
        "id": "avg_order_value",
        "label": "Average Order Value",
        "sql": "SELECT ROUND(AVG(total_amount)::numeric,2) FROM sales_order WHERE status='closed'",
        "format": "currency",
    },
    {
        "id": "total_purchase_orders",
        "label": "Total Purchase Orders",
        "sql": "SELECT COUNT(*) FROM purchase_order",
        "format": "integer",
    },
    {
        "id": "total_vendors",
        "label": "Total Vendors",
        "sql": "SELECT COUNT(*) FROM vendor_master",
        "format": "integer",
    },
]

# Deviation threshold above which a KPI is flagged
_DEVIATION_THRESHOLD = 0.20   # 20 %


class ValidationAgent(BaseAgent):
    """Run control scalar queries concurrently; cross-check KPI values."""

    name = "validation"

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _run_one_control(self, control: dict) -> dict:
        """Execute a single control query (blocking, called in executor)."""
        from db.executor import execute_sql

        sql = control["sql"]
        try:
            result = execute_sql(sql)
            if result.get("success") and result.get("data"):
                value = list(result["data"][0].values())[0]
                return {**control, "value": value, "ok": True}
        except Exception as exc:
            logger.warning("Validation control '%s' failed: %s", control["id"], exc)
        return {**control, "value": None, "ok": False}

    # ── Public async API ──────────────────────────────────────────────────────

    async def run(self, blueprint: dict, question: str) -> dict:
        """Fetch all control scalars in parallel.

        Returns:
            {
                "_validation_controls": {id: value, ...},
                "agent_timing": {"validation": <seconds>}
            }
        """
        async with self.timed():
            self.logger.info(
                "Validation agent — running %d control queries in parallel",
                len(_CONTROL_QUERIES),
            )
            results: list[dict] = await self.run_many(
                self._run_one_control, _CONTROL_QUERIES
            )

            controls = {r["id"]: r for r in results if r["ok"]}
            self.logger.info(
                "Validation agent — %d of %d control queries succeeded",
                len(controls), len(_CONTROL_QUERIES),
            )

        return {
            "_validation_controls": controls,
            "agent_timing": {"validation": round(self._elapsed, 3)},
        }

    # ── Post-merge cross-check ────────────────────────────────────────────────

    @staticmethod
    def cross_check(kpis: list[dict], controls: dict) -> dict:
        """Compare KPI values against DB control scalars.

        Called by the orchestrator AFTER all agents have merged.

        Args:
            kpis:     List of KPI dicts (each with "value", "label").
            controls: The _validation_controls dict from run().

        Returns:
            {
                "status": "ok" | "warnings",
                "checks": [{"kpi": str, "db_value": any, "reported": any,
                             "deviation_pct": float, "flagged": bool}, ...]
            }
        """
        checks = []

        # Build a quick lookup: control label → value
        label_to_val = {
            c["label"].lower(): c["value"]
            for c in controls.values()
            if c.get("value") is not None
        }

        # KPI "id" → label keyword mapping for fuzzy matching
        _ID_KEYWORDS = {
            "total_closed_revenue": ["revenue", "sales", "closed revenue"],
            "total_closed_orders": ["closed orders", "fulfilled orders", "total orders"],
            "total_open_orders": ["open orders", "pending orders"],
            "fulfilment_rate": ["fulfilment rate", "fulfillment rate"],
            "cancellation_rate": ["cancellation rate", "cancel rate"],
            "avg_order_value": ["average order", "aov"],
        }

        for kpi in kpis:
            kpi_label = (kpi.get("label") or "").lower()
            kpi_value = kpi.get("value")

            # Try to match this KPI to a control
            matched_ctrl_id = None
            for ctrl_id, keywords in _ID_KEYWORDS.items():
                if any(kw in kpi_label for kw in keywords):
                    if ctrl_id in controls:
                        matched_ctrl_id = ctrl_id
                        break

            if matched_ctrl_id is None:
                continue  # no control for this KPI — skip

            ctrl = controls[matched_ctrl_id]
            db_value = ctrl["value"]

            try:
                db_f = float(db_value)
                rpt_f = float(kpi_value)
                if db_f == 0:
                    deviation = 0.0
                else:
                    deviation = abs(rpt_f - db_f) / abs(db_f)
                flagged = deviation > _DEVIATION_THRESHOLD
            except (TypeError, ValueError):
                deviation = 0.0
                flagged = False

            if flagged:
                logger.warning(
                    "Validation flag: KPI '%s' reports %s but DB control is %s (%.1f%% deviation)",
                    kpi.get("label"), kpi_value, db_value, deviation * 100,
                )
                kpi["flagged"] = True   # mark in-place so the frontend can show a warning badge

            checks.append({
                "kpi": kpi.get("label"),
                "db_value": db_value,
                "reported": kpi_value,
                "deviation_pct": round(deviation * 100, 1),
                "flagged": flagged,
            })

        status = "warnings" if any(c["flagged"] for c in checks) else "ok"
        return {"status": status, "checks": checks}
