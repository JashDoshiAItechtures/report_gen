"""Report generation pipeline — intent classification + LLM-based report builder.

This module adds report generation capability to the SQL chatbot without
modifying any existing chat behaviour.
"""

import json
import logging
import re
from datetime import date
from typing import Any

import dspy

from ai.groq_setup import get_lm
from ai.report_signatures import ReportGeneration, ReportModification
from ai.validator import validate_sql, check_sql_against_schema
from ai.sql_pattern_checker import check_sql_patterns, format_issues_for_repair
from ai.signatures import SQLRepair
from db.schema import format_schema, get_schema
from db.relationships import format_relationships
from db.profiler import get_data_profile
from db.executor import execute_sql

logger = logging.getLogger(__name__)

MAX_REPAIR_RETRIES = 2

# ── Intent classification (deterministic — no LLM) ─────────────────────────

# ── Modify intent keywords / patterns (checked FIRST — highest priority) ──
_MODIFY_KEYWORDS = [
    "update ", "set price", "change price", "change the price", "modify price",
    "delete record", "delete the record", "remove record", "remove the record",
    "insert record", "add record", "add a new", "add new customer", "add new product",
    "update record", "update the record", "change the status", "set the status",
    "set status to", "mark as", "mark order",
]
_MODIFY_PATTERNS = [
    r"\bupdate\b.{1,60}\bset\b",                        # UPDATE … SET
    r"\bset\b.{1,40}\bto\b.{1,40}\bwhere\b",            # SET x TO y WHERE
    r"\b(?:change|set|update|modify)\b.{1,40}\bprice\b", # change/set … price
    r"\b(?:change|set|update)\b.{1,40}\bto\b\s+\d",      # change X to <number>
    r"\b(?:delete|remove)\b.{0,40}\b(?:record|row|entry|order|customer|product)\b",
    r"\b(?:insert|add)\b.{0,40}\b(?:record|row|entry|new customer|new product)\b",
    r"\bmark\b.{1,40}\b(?:as|to)\b.{1,20}\b(?:complete|paid|cancelled|shipped|delivered)\b",
]

# ── Chart intent keywords / patterns ─────────────────────────────────────
_CHART_KEYWORDS = [
    "bar chart", "pie chart", "line chart", "doughnut chart",
    "area chart", "scatter chart", "show chart", "show graph",
    "plot ", "draw chart", "draw graph", "visualize", "visualization",
]
_CHART_PATTERNS = [
    r"\b(?:show|create|make|draw|plot|give\s+me)\b.{0,30}\b(?:chart|graph|plot|diagram)\b",
    r"\b(?:bar|line|pie|doughnut|scatter|area)\b.{0,15}\b(?:chart|graph)\b",
]

# ── Export intent keywords ────────────────────────────────────────────────
_EXPORT_KEYWORDS = [
    "export as pdf", "export to pdf", "download pdf", "save as pdf",
    "export excel", "download excel", "export csv", "download csv", "export xlsx",
]

# ── Report intent keywords / patterns ────────────────────────────────────
_REPORT_KEYWORDS = [
    # Generic report terms (explicit report-specific, NOT plain queries)
    "report", "dashboard", "analysis", "trend", "trends",
    "summary", "comparison", "compare", "insight", "insights",
    "performance", "overview", "breakdown", "kpi", "kpis",
    "analytics", "metrics", "statistics",
    # Sales / revenue
    "sales report", "revenue report", "revenue analysis",
    # Operations / order status
    "open orders", "open order", "inorder", "in-order",
    "order fulfillment", "active orders", "pending orders",
    # Backorder
    "backorder", "back order", "back-order", "unfulfilled", "outstanding orders",
    # Procurement / purchasing
    "procurement", "vendor report", "vendor analysis",
    "po report", "supplier report",
    # Customer / product
    "customer report", "customer analysis", "product report", "product analysis",
    "inventory report", "stock report",
    # Financial
    "cost report", "margin report", "profitability", "financial report",
]

_REPORT_PATTERNS = [
    r"\b(?:show|give|create|generate|build|make)\b.*\b(?:report|dashboard|analysis|overview)\b",
    r"\b(?:sales|revenue|order|product|customer|vendor)\s+(?:performance|analysis|breakdown|trend|summary)\b",
    r"\b(?:analyze|analyse)\b",
    # Operational / backorder / procurement report patterns
    r"\b(?:backorder|back-order|inorder|in-order)\b",
    r"\b(?:open|pending|active|processing)\s+orders?\b",
    r"\b(?:order|purchase)\s+(?:status|fulfillment|pipeline)\b",
    r"\b(?:procurement|purchasing)\s+(?:report|analysis|overview|summary|dashboard)\b",
    r"\b(?:vendor|supplier)\s+(?:report|analysis|performance|summary)\b",
    r"\bunfulfilled\s+(?:orders?|lines?)\b",
]


def classify_intent(question: str) -> str:
    """Classify user intent: 'query' | 'chart' | 'report' | 'modify' | 'export'.

    Priority order: modify → export → chart → report → query (default).
    All matching is deterministic keyword/regex — no LLM call.
    """
    q = question.lower().strip()

    # 1. Modify — highest priority; must NOT run through the SELECT pipeline
    for pattern in _MODIFY_PATTERNS:
        if re.search(pattern, q):
            return "modify"
    for kw in _MODIFY_KEYWORDS:
        if kw in q:
            return "modify"

    # 2. Export
    for kw in _EXPORT_KEYWORDS:
        if kw in q:
            return "export"

    # 3. Chart — before report (report keywords used to include 'chart' etc.)
    for pattern in _CHART_PATTERNS:
        if re.search(pattern, q):
            return "chart"
    for kw in _CHART_KEYWORDS:
        if kw in q:
            return "chart"

    # 4. Report
    for pattern in _REPORT_PATTERNS:
        if re.search(pattern, q):
            return "report"
    for kw in _REPORT_KEYWORDS:
        if kw in q:
            return "report"

    # 5. Default — plain query (SELECT)
    return "query"


# ── SQL Auto-Correction ────────────────────────────────────────────────────
# The LLM persistently treats sales_order_line_pricing as if it were a
# combined sales_order + sales_order_line table. It references columns like
# status, so_id, product_id on the pricing table, but those columns live on
# sales_order and sales_order_line respectively.
#
# This auto-corrector detects and rewrites these broken queries.

# Columns that belong to sales_order (NOT on line/pricing/gold/diamond)
_SO_ONLY_COLS = {'status', 'so_id', 'customer_id', 'order_date', 'total_amount',
                 'order_number', 'created_at', 'updated_at'}
# Columns that belong to sales_order_line (NOT pricing/gold/diamond)
_SOL_ONLY_COLS = {'product_id', 'variant_sku', 'quantity', 'so_id'}
# Sub-tables that are frequently misused as main FROM tables
_SUB_TABLES = {
    'sales_order_line_pricing': 'solp',
    'sales_order_line_gold': 'solg',
    'sales_order_line_diamond': 'sold',
}


def _fix_report_sql(sql: str) -> str:
    """Auto-correct common SQL mistakes generated by the LLM.

    Handles multiple error patterns:
    1. Sub-tables (pricing/gold/diamond) used as FROM with wrong column refs
    2. sales_order joined directly to product_master (missing sales_order_line)
    3. so.product_id references (product_id lives on sales_order_line, not sales_order)
    """
    if not sql:
        return sql

    original = sql

    # Normalize whitespace for easier matching
    sql_oneline = ' '.join(sql.split())

    # ══════════════════════════════════════════════════════════════════════
    # PASS 1: Fix sub-table (pricing/gold/diamond) alias issues
    # ══════════════════════════════════════════════════════════════════════
    for sub_tbl, preferred_alias in _SUB_TABLES.items():
        # Find the alias used for this sub-table
        alias_match = re.search(
            rf'\b{sub_tbl}\s+(\w+)\b', sql_oneline, re.IGNORECASE
        )
        if not alias_match:
            continue

        alias = alias_match.group(1)

        # Check if this alias references columns it doesn't own
        has_status_ref = bool(re.search(rf'\b{re.escape(alias)}\.status\b', sql_oneline, re.IGNORECASE))
        has_so_id_ref = bool(re.search(rf'\b{re.escape(alias)}\.so_id\b', sql_oneline, re.IGNORECASE))
        has_product_id_ref = bool(re.search(rf'\b{re.escape(alias)}\.product_id\b', sql_oneline, re.IGNORECASE))
        has_customer_id_ref = bool(re.search(rf'\b{re.escape(alias)}\.customer_id\b', sql_oneline, re.IGNORECASE))
        has_order_date_ref = bool(re.search(rf'\b{re.escape(alias)}\.order_date\b', sql_oneline, re.IGNORECASE))
        has_quantity_ref = bool(re.search(rf'\b{re.escape(alias)}\.quantity\b', sql_oneline, re.IGNORECASE))
        has_variant_sku_ref = bool(re.search(rf'\b{re.escape(alias)}\.variant_sku\b', sql_oneline, re.IGNORECASE))
        has_total_amount_ref = bool(re.search(rf'\b{re.escape(alias)}\.total_amount\b', sql_oneline, re.IGNORECASE))

        needs_sol = has_so_id_ref or has_product_id_ref or has_quantity_ref or has_variant_sku_ref
        needs_so = has_status_ref or has_customer_id_ref or has_order_date_ref or has_total_amount_ref

        if not needs_sol and not needs_so:
            continue

        logger.info(
            "SQL auto-correct: %s alias '%s' references wrong columns "
            "(status=%s, so_id=%s, product_id=%s). Injecting proper joins.",
            sub_tbl, alias, has_status_ref, has_so_id_ref, has_product_id_ref
        )

        # Check what tables are already in the query
        stripped = sql_oneline
        for t in _SUB_TABLES:
            stripped = stripped.replace(t, '')
        has_sol_table = 'sales_order_line' in stripped.replace('sales_order_line_', '')
        has_so_table = bool(re.search(r'\bsales_order\b(?!_)', stripped))

        # Choose alias names that won't conflict
        sol_alias = 'sol' if alias != 'sol' else 'sol2'
        so_alias = 'so' if alias != 'so' else 'so2'

        # Restructure the FROM clause
        from_pattern = re.compile(
            rf'FROM\s+{sub_tbl}\s+{re.escape(alias)}\b',
            re.IGNORECASE
        )
        if from_pattern.search(sql_oneline):
            new_from = f'FROM sales_order {so_alias}'
            new_from += f' JOIN sales_order_line {sol_alias} ON {so_alias}.so_id = {sol_alias}.so_id'
            new_from += f' JOIN {sub_tbl} {alias} ON {sol_alias}.sol_id = {alias}.sol_id'
            sql_oneline = from_pattern.sub(new_from, sql_oneline, count=1)

            # Remove any now-redundant JOIN to sales_order
            sql_oneline = re.sub(
                rf'\bJOIN\s+sales_order\s+{re.escape(so_alias)}\s+ON\s+[^J]*?(?=JOIN|\bWHERE\b|\bGROUP\b|\bORDER\b|\bLIMIT\b|$)',
                '', sql_oneline, flags=re.IGNORECASE
            )
            has_sol_table = True
            has_so_table = True
        else:
            # Sub-table is joined, not FROM — inject missing intermediate tables
            if needs_sol and not has_sol_table:
                sql_oneline = re.sub(
                    rf'JOIN\s+{sub_tbl}\s+{re.escape(alias)}\b',
                    f'JOIN sales_order_line {sol_alias} ON {sol_alias}.so_id = {so_alias}.so_id '
                    f'JOIN {sub_tbl} {alias}',
                    sql_oneline, count=1, flags=re.IGNORECASE
                )
                sql_oneline = re.sub(
                    rf'JOIN\s+{sub_tbl}\s+{re.escape(alias)}\s+ON\s+\w+\.so_id\s*=\s*{re.escape(alias)}\.so_id',
                    f'JOIN {sub_tbl} {alias} ON {sol_alias}.sol_id = {alias}.sol_id',
                    sql_oneline, flags=re.IGNORECASE
                )
                has_sol_table = True

            if needs_so and not has_so_table:
                sql_oneline = re.sub(
                    r'\bFROM\b',
                    f'FROM sales_order {so_alias} JOIN',
                    sql_oneline, count=1, flags=re.IGNORECASE
                )
                has_so_table = True

        # Remap column references to correct aliases
        if has_status_ref:
            sql_oneline = re.sub(
                rf'\b{re.escape(alias)}\.status\b',
                f'{so_alias}.status',
                sql_oneline, flags=re.IGNORECASE
            )
        if has_so_id_ref:
            sql_oneline = re.sub(
                rf'\b{re.escape(alias)}\.so_id\b',
                f'{sol_alias}.so_id',
                sql_oneline, flags=re.IGNORECASE
            )
        if has_product_id_ref:
            sql_oneline = re.sub(
                rf'\b{re.escape(alias)}\.product_id\b',
                f'{sol_alias}.product_id',
                sql_oneline, flags=re.IGNORECASE
            )
        if has_customer_id_ref:
            sql_oneline = re.sub(
                rf'\b{re.escape(alias)}\.customer_id\b',
                f'{so_alias}.customer_id',
                sql_oneline, flags=re.IGNORECASE
            )
        if has_order_date_ref:
            sql_oneline = re.sub(
                rf'\b{re.escape(alias)}\.order_date\b',
                f'{so_alias}.order_date',
                sql_oneline, flags=re.IGNORECASE
            )
        if has_total_amount_ref:
            sql_oneline = re.sub(
                rf'\b{re.escape(alias)}\.total_amount\b',
                f'{so_alias}.total_amount',
                sql_oneline, flags=re.IGNORECASE
            )
        if has_quantity_ref:
            sql_oneline = re.sub(
                rf'\b{re.escape(alias)}\.quantity\b',
                f'{sol_alias}.quantity',
                sql_oneline, flags=re.IGNORECASE
            )
        if has_variant_sku_ref:
            sql_oneline = re.sub(
                rf'\b{re.escape(alias)}\.variant_sku\b',
                f'{sol_alias}.variant_sku',
                sql_oneline, flags=re.IGNORECASE
            )

    # ══════════════════════════════════════════════════════════════════════
    # PASS 2: Fix so.product_id → sol.product_id  (product_id lives on
    #         sales_order_line, NOT sales_order)
    # ══════════════════════════════════════════════════════════════════════

    # Find all aliases used for sales_order (but NOT sales_order_line*)
    so_aliases = set()
    for m in re.finditer(r'\bsales_order\b(?!_)\s+(\w+)', sql_oneline, re.IGNORECASE):
        so_aliases.add(m.group(1))

    for so_alias in so_aliases:
        if not re.search(rf'\b{re.escape(so_alias)}\.product_id\b', sql_oneline, re.IGNORECASE):
            continue

        logger.info("SQL auto-correct: %s.product_id detected — product_id is on sales_order_line, not sales_order", so_alias)

        # Find the alias for sales_order_line if it exists
        sol_match = re.search(r'\bsales_order_line\b(?!_)\s+(\w+)', sql_oneline, re.IGNORECASE)

        if sol_match:
            sol_alias = sol_match.group(1)
        else:
            # Need to inject sales_order_line into the query
            sol_alias = 'sol'
            # Insert JOIN sales_order_line after FROM sales_order so
            sql_oneline = re.sub(
                rf'(FROM\s+sales_order\s+{re.escape(so_alias)})\b',
                rf'\1 JOIN sales_order_line {sol_alias} ON {so_alias}.so_id = {sol_alias}.so_id',
                sql_oneline, count=1, flags=re.IGNORECASE
            )

        # Remap so.product_id → sol.product_id
        sql_oneline = re.sub(
            rf'\b{re.escape(so_alias)}\.product_id\b',
            f'{sol_alias}.product_id',
            sql_oneline, flags=re.IGNORECASE
        )

    # ══════════════════════════════════════════════════════════════════════
    # PASS 3: Fix direct sales_order → product_master JOIN (missing
    #         sales_order_line in between).
    #         Pattern: JOIN product_master pm ON so.product_id = pm.product_id
    #         Fix: inject sales_order_line, rewrite JOIN condition
    # ══════════════════════════════════════════════════════════════════════

    # Detect: JOIN product_master <alias> ON <so_alias>.product_id = <pm_alias>.product_id
    # where <so_alias> is a sales_order alias (already caught above, but joining might
    # still reference the wrong table).  After Pass 2, so.product_id is already fixed,
    # but we still need to ensure the JOIN to product_master goes through sol.
    # This is already handled by Pass 2 remapping, so no additional action needed
    # if Pass 2 ran.  But handle the case where the LLM omits sales_order_line entirely
    # and writes: FROM sales_order so JOIN product_master pm ON so.product_id = pm.product_id
    # (After Pass 2, this becomes sol.product_id, and the JOIN is already injected.)

    # ══════════════════════════════════════════════════════════════════════
    # PASS 4: Fix raw product_id / variant_sku used as a chart label column.
    #
    # The LLM sometimes writes:
    #   SELECT sol.product_id, SUM(...) AS value FROM ... GROUP BY sol.product_id
    # This produces SKU codes (PROD-0484) as labels instead of human-readable names.
    # Fix: inject JOIN to product_master and replace with pm.product_name.
    # ══════════════════════════════════════════════════════════════════════

    # Detect sol.<alias>.product_id or sol.variant_sku as first SELECT token
    _sol_alias_m = re.search(r'\bsales_order_line\b(?!_)\s+(\w+)', sql_oneline, re.IGNORECASE)
    _sol_alias_p4 = _sol_alias_m.group(1) if _sol_alias_m else 'sol'

    # Check: does the SELECT clause start with <sol_alias>.product_id or .variant_sku?
    _select_label_pid = re.search(
        rf'\bSELECT\s+{re.escape(_sol_alias_p4)}\.(product_id|variant_sku)\b',
        sql_oneline, re.IGNORECASE
    )
    if _select_label_pid:
        logger.info(
            "SQL auto-correct PASS 4: %s.%s used as chart label — rewriting to pm.product_name",
            _sol_alias_p4, _select_label_pid.group(1)
        )
        # Check if product_master is already joined
        _has_pm = bool(re.search(r'\bproduct_master\b', sql_oneline, re.IGNORECASE))
        _pm_alias = 'pm'
        if not _has_pm:
            # Find a good place to inject: after the last JOIN or after FROM clause
            # Inject before WHERE / GROUP / ORDER / LIMIT
            _inject_point = re.search(
                r'\b(WHERE|GROUP\s+BY|ORDER\s+BY|LIMIT)\b', sql_oneline, re.IGNORECASE
            )
            if _inject_point:
                pos = _inject_point.start()
                sql_oneline = (
                    sql_oneline[:pos]
                    + f'JOIN product_master {_pm_alias} ON {_sol_alias_p4}.product_id = {_pm_alias}.product_id '
                    + sql_oneline[pos:]
                )
            else:
                sql_oneline += f' JOIN product_master {_pm_alias} ON {_sol_alias_p4}.product_id = {_pm_alias}.product_id'
        else:
            # Find the pm alias already in use
            _pm_alias_m = re.search(r'\bproduct_master\s+(\w+)', sql_oneline, re.IGNORECASE)
            if _pm_alias_m:
                _pm_alias = _pm_alias_m.group(1)

        # Replace the label column in SELECT
        sql_oneline = re.sub(
            rf'\bSELECT\s+{re.escape(_sol_alias_p4)}\.(product_id|variant_sku)\b',
            f'SELECT {_pm_alias}.product_name',
            sql_oneline, count=1, flags=re.IGNORECASE
        )
        # Replace in GROUP BY
        sql_oneline = re.sub(
            rf'\bGROUP\s+BY\s+{re.escape(_sol_alias_p4)}\.(product_id|variant_sku)\b',
            f'GROUP BY {_pm_alias}.product_name',
            sql_oneline, flags=re.IGNORECASE
        )

    sql = sql_oneline

    if sql != original:
        logger.info("SQL auto-corrected:\n  BEFORE: %s\n  AFTER:  %s",
                     original.replace('\n', ' ')[:300],
                     sql[:300])

    return sql


# ── Server-side filter injection ───────────────────────────────────────────
# Instead of asking the LLM to regenerate SQL with filters, we inject
# WHERE clauses programmatically into the existing working SQL.

def _inject_filters(sql: str, filters: dict) -> str:
    """Inject WHERE conditions into an existing SQL query for applied filters.

    This is the reliable alternative to asking the LLM to rewrite queries.
    It modifies the existing (working) SQL by adding/extending WHERE clauses
    and injecting required JOINs if needed.

    Args:
        sql: Original SQL query string
        filters: Dict with keys: date_from, date_to, category, status, customer, product
    """
    if not sql or not filters:
        return sql

    sql = ' '.join(sql.split())  # normalize whitespace
    conditions = []

    # ── Date filters ──────────────────────────────────────────────────
    # Only apply if the query references sales_order
    if filters.get('date_from') and re.search(r'\bsales_order\b(?!_)', sql, re.IGNORECASE):
        # Find the alias for sales_order
        so_alias_m = re.search(r'\bsales_order\b(?!_)\s+(\w+)', sql, re.IGNORECASE)
        so_alias = so_alias_m.group(1) if so_alias_m else 'so'
        conditions.append(f"{so_alias}.order_date >= '{filters['date_from']}'")

    if filters.get('date_to') and re.search(r'\bsales_order\b(?!_)', sql, re.IGNORECASE):
        so_alias_m = re.search(r'\bsales_order\b(?!_)\s+(\w+)', sql, re.IGNORECASE)
        so_alias = so_alias_m.group(1) if so_alias_m else 'so'
        conditions.append(f"{so_alias}.order_date <= '{filters['date_to']}'")

    # ── Status filter ─────────────────────────────────────────────────
    if filters.get('status') and re.search(r'\bsales_order\b(?!_)', sql, re.IGNORECASE):
        so_alias_m = re.search(r'\bsales_order\b(?!_)\s+(\w+)', sql, re.IGNORECASE)
        so_alias = so_alias_m.group(1) if so_alias_m else 'so'
        status_val = filters['status'].replace("'", "''")
        # Remove any existing status condition and replace
        sql = re.sub(
            rf"\b{re.escape(so_alias)}\.status\s*=\s*'[^']*'",
            f"{so_alias}.status = '{status_val}'",
            sql, flags=re.IGNORECASE
        )
        # If no existing status condition was replaced, add one
        if not re.search(rf"\b{re.escape(so_alias)}\.status\s*=", sql, re.IGNORECASE):
            conditions.append(f"{so_alias}.status = '{status_val}'")

    # ── Category filter ───────────────────────────────────────────────
    if filters.get('category'):
        cat_val = filters['category'].replace("'", "''")
        # Check if product_master is already in the query
        pm_match = re.search(r'\bproduct_master\s+(\w+)', sql, re.IGNORECASE)
        if pm_match:
            pm_alias = pm_match.group(1)
        else:
            # Need to inject the join chain: sales_order_line + product_master
            pm_alias = 'pm'
            sol_match = re.search(r'\bsales_order_line\b(?!_)\s+(\w+)', sql, re.IGNORECASE)
            so_alias_m = re.search(r'\bsales_order\b(?!_)\s+(\w+)', sql, re.IGNORECASE)

            if sol_match:
                sol_alias = sol_match.group(1)
                # sales_order_line exists, just add product_master join
                sql = re.sub(
                    r'(\bWHERE\b)',
                    f'JOIN product_master {pm_alias} ON {sol_alias}.product_id = {pm_alias}.product_id WHERE',
                    sql, count=1, flags=re.IGNORECASE
                )
                if 'WHERE' not in sql.upper():
                    sql += f' JOIN product_master {pm_alias} ON {sol_alias}.product_id = {pm_alias}.product_id'
            elif so_alias_m:
                so_alias = so_alias_m.group(1)
                sol_alias = 'sol'
                # Need both sales_order_line and product_master
                join_clause = (f'JOIN sales_order_line {sol_alias} ON {so_alias}.so_id = {sol_alias}.so_id '
                               f'JOIN product_master {pm_alias} ON {sol_alias}.product_id = {pm_alias}.product_id')
                if 'WHERE' in sql.upper():
                    sql = re.sub(r'(\bWHERE\b)', f'{join_clause} WHERE', sql, count=1, flags=re.IGNORECASE)
                else:
                    sql += f' {join_clause}'

        conditions.append(f"{pm_alias}.category = '{cat_val}'")

    # ── Product filter ────────────────────────────────────────────────
    if filters.get('product'):
        prod_val = filters['product'].replace("'", "''")
        pm_match = re.search(r'\bproduct_master\s+(\w+)', sql, re.IGNORECASE)
        if pm_match:
            pm_alias = pm_match.group(1)
        else:
            # Inject join chain (same logic as category)
            pm_alias = 'pm'
            sol_match = re.search(r'\bsales_order_line\b(?!_)\s+(\w+)', sql, re.IGNORECASE)
            so_alias_m = re.search(r'\bsales_order\b(?!_)\s+(\w+)', sql, re.IGNORECASE)

            if sol_match:
                sol_alias = sol_match.group(1)
                if 'WHERE' in sql.upper():
                    sql = re.sub(
                        r'(\bWHERE\b)',
                        f'JOIN product_master {pm_alias} ON {sol_alias}.product_id = {pm_alias}.product_id WHERE',
                        sql, count=1, flags=re.IGNORECASE
                    )
                else:
                    sql += f' JOIN product_master {pm_alias} ON {sol_alias}.product_id = {pm_alias}.product_id'
            elif so_alias_m:
                so_alias = so_alias_m.group(1)
                sol_alias = 'sol'
                join_clause = (f'JOIN sales_order_line {sol_alias} ON {so_alias}.so_id = {sol_alias}.so_id '
                               f'JOIN product_master {pm_alias} ON {sol_alias}.product_id = {pm_alias}.product_id')
                if 'WHERE' in sql.upper():
                    sql = re.sub(r'(\bWHERE\b)', f'{join_clause} WHERE', sql, count=1, flags=re.IGNORECASE)
                else:
                    sql += f' {join_clause}'

        conditions.append(f"{pm_alias}.product_name = '{prod_val}'")

    # ── Customer filter ───────────────────────────────────────────────
    if filters.get('customer'):
        cust_val = filters['customer'].replace("'", "''")
        cm_match = re.search(r'\bcustomer_master\s+(\w+)', sql, re.IGNORECASE)
        if cm_match:
            cm_alias = cm_match.group(1)
        else:
            cm_alias = 'cm'
            so_alias_m = re.search(r'\bsales_order\b(?!_)\s+(\w+)', sql, re.IGNORECASE)
            if so_alias_m:
                so_alias = so_alias_m.group(1)
                if 'WHERE' in sql.upper():
                    sql = re.sub(
                        r'(\bWHERE\b)',
                        f'JOIN customer_master {cm_alias} ON {so_alias}.customer_id = {cm_alias}.customer_id WHERE',
                        sql, count=1, flags=re.IGNORECASE
                    )
                else:
                    sql += f' JOIN customer_master {cm_alias} ON {so_alias}.customer_id = {cm_alias}.customer_id'

        conditions.append(f"{cm_alias}.customer_name = '{cust_val}'")

    # ── Apply collected conditions ────────────────────────────────────
    if conditions:
        cond_str = ' AND '.join(conditions)
        if re.search(r'\bWHERE\b', sql, re.IGNORECASE):
            # Find the position right after WHERE and its existing conditions
            # Insert before GROUP BY / ORDER BY / LIMIT if present
            for keyword in ['GROUP BY', 'ORDER BY', 'LIMIT', 'HAVING']:
                pattern = re.compile(rf'\b{keyword}\b', re.IGNORECASE)
                match = pattern.search(sql)
                if match:
                    insert_pos = match.start()
                    sql = sql[:insert_pos] + f'AND {cond_str} ' + sql[insert_pos:]
                    break
            else:
                # No GROUP BY/ORDER BY/LIMIT — just append
                sql += f' AND {cond_str}'
        else:
            # No WHERE clause at all — insert before GROUP BY etc. or append
            for keyword in ['GROUP BY', 'ORDER BY', 'LIMIT', 'HAVING']:
                pattern = re.compile(rf'\b{keyword}\b', re.IGNORECASE)
                match = pattern.search(sql)
                if match:
                    insert_pos = match.start()
                    sql = sql[:insert_pos] + f'WHERE {cond_str} ' + sql[insert_pos:]
                    break
            else:
                sql += f' WHERE {cond_str}'

    return sql


# ── Report generation ──────────────────────────────────────────────────────

class ReportPipeline:
    """Generates a complete analytics report from a natural-language request."""

    def __init__(self, provider: str = "groq"):
        self.provider = provider
        self._lm = get_lm(provider)
        self.report_gen = dspy.Predict(ReportGeneration)
        self.report_mod = dspy.Predict(ReportModification)
        self.repair = dspy.Predict(SQLRepair)

    # ── Shared SQL validation + execution (mirrors SQLAnalystPipeline) ──

    @staticmethod
    def _clean_sql(raw: str) -> str:
        """Strip markdown fences, trailing prose, and whitespace from LLM SQL."""
        sql = raw.strip()
        if sql.startswith("```"):
            lines = sql.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            sql = "\n".join(lines).strip()
        match = re.search(
            r"((?:SELECT|WITH)\b[\s\S]*?)(;|\n\n(?=[A-Z][a-z])|$)",
            sql, re.IGNORECASE,
        )
        if match:
            sql = match.group(1).strip()
        sql = sql.rstrip(";")
        return sql

    def _validate_and_execute_sql(self, sql: str, context: str = "report") -> tuple:
        """Validate and execute SQL using the same pipeline as SQL Chat.

        Applies the full validation chain:
        1. Auto-correct known LLM mistakes (_fix_report_sql)
        2. Schema validation (check_sql_against_schema)
        3. Structural pattern checker (check_sql_patterns)
        4. Safety validation (validate_sql)
        5. Execute with repair loop (up to 2 retries on DB errors)

        Returns (corrected_sql, result_dict) where result_dict has
        'success', 'data', 'error' keys.
        """
        # Step 1: Auto-correct known regex patterns
        sql = _fix_report_sql(sql)

        # Step 2: Schema validation
        try:
            schema = get_schema()
            schema_valid, schema_issues = check_sql_against_schema(sql, schema)
            if not schema_valid:
                logger.warning("[%s] Schema issues: %s", context, schema_issues)
        except Exception as exc:
            logger.warning("Schema check failed: %s", exc)

        # Step 3: Structural pattern checker
        try:
            pattern_issues = check_sql_patterns(sql)
            if pattern_issues:
                logger.warning(
                    "[%s] Pattern issues: %s",
                    context,
                    [i["pattern_name"] for i in pattern_issues],
                )
        except Exception as exc:
            logger.warning("Pattern check failed: %s", exc)

        # Step 4: Safety validation
        is_safe, reason = validate_sql(sql)
        if not is_safe:
            return sql, {"success": False, "data": [], "error": f"Query rejected: {reason}"}

        # Step 5: Execute with repair loop
        result = execute_sql(sql)

        for attempt in range(MAX_REPAIR_RETRIES):
            if result["success"]:
                break
            logger.warning(
                "[%s] SQL error (attempt %d): %s", context, attempt + 1, result["error"]
            )
            try:
                schema_str = format_schema()
                repair_result = self.repair(
                    sql_query=sql,
                    error_message=result["error"],
                    schema_info=schema_str,
                    question=f"Fix this SQL for a {context} query",
                )
                sql = self._clean_sql(repair_result.corrected_sql)
                sql = _fix_report_sql(sql)  # re-apply regex fixes after repair

                is_safe, reason = validate_sql(sql)
                if not is_safe:
                    return sql, {"success": False, "data": [], "error": f"Repaired query rejected: {reason}"}

                result = execute_sql(sql)
            except Exception as exc:
                logger.error("[%s] Repair attempt %d failed: %s", context, attempt + 1, exc)
                break

        return sql, result

    @staticmethod
    def _build_question_with_context(question: str) -> str:
        today = date.today()
        current_year = today.year
        last_year = current_year - 1
        return (
            f"[CONTEXT: Today is {today.isoformat()}. "
            f"Current year = {current_year}. "
            f"'Last year' = {last_year} ({last_year}-01-01 to {last_year}-12-31). "
            f"'This year' = {current_year} ({current_year}-01-01 to {current_year}-12-31).]\n\n"
            f"{question}"
        )

    @staticmethod
    def _repair_json(text: str) -> str:
        """Best-effort repair of common LLM JSON generation errors.

        Handles:
        1. Literal newlines / tabs / carriage-returns inside string values
        2. Trailing commas before } or ]
        3. Truncated JSON (missing closing braces/brackets)
        """
        # ── Pass 1: escape unescaped control chars inside string literals ──
        result: list[str] = []
        in_string = False
        escape_next = False

        for ch in text:
            if escape_next:
                result.append(ch)
                escape_next = False
                continue
            if ch == "\\":
                escape_next = True
                result.append(ch)
                continue
            if ch == '"':
                in_string = not in_string
                result.append(ch)
                continue
            if in_string:
                if ch == "\n":
                    result.append("\\n")
                elif ch == "\r":
                    result.append("\\r")
                elif ch == "\t":
                    result.append("\\t")
                else:
                    result.append(ch)
            else:
                result.append(ch)

        text = "".join(result)

        # ── Pass 2: remove trailing commas before } or ] ───────────────────
        import re as _re
        text = _re.sub(r",(\s*[}\]])", r"\1", text)

        # ── Pass 3: close any truncated JSON ──────────────────────────────
        # Count unmatched { and [
        depth_brace = 0
        depth_bracket = 0
        in_str = False
        esc = False
        for ch in text:
            if esc:
                esc = False
                continue
            if ch == "\\" and in_str:
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if not in_str:
                if ch == "{":
                    depth_brace += 1
                elif ch == "}":
                    depth_brace -= 1
                elif ch == "[":
                    depth_bracket += 1
                elif ch == "]":
                    depth_bracket -= 1

        # If we ended mid-string, close it first
        if in_str:
            text += '"'
        # Close any open arrays before open objects
        if depth_bracket > 0:
            text += "]" * depth_bracket
        if depth_brace > 0:
            text += "}" * depth_brace

        return text

    @staticmethod
    def _extract_json(raw: str) -> dict:
        """Extract and parse JSON from LLM output with multi-stage repair."""
        text = raw.strip()

        # ── Strip markdown code fences ────────────────────────────────────
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            text = "\n".join(lines).strip()

        # ── Find JSON object boundaries ───────────────────────────────────
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]

        # ── Stage 1: direct parse ─────────────────────────────────────────
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # ── Stage 2: repair then parse ────────────────────────────────────
        repaired = ReportPipeline._repair_json(text)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

        # ── Stage 3: re-extract boundaries after repair and retry ─────────
        start = repaired.find("{")
        end = repaired.rfind("}")
        if start != -1 and end != -1 and end > start:
            repaired = repaired[start:end + 1]

        return json.loads(repaired)  # let the caller handle any final exception

    def _execute_kpi_sql(self, kpi: dict) -> dict:
        """Execute a KPI's SQL and populate its value."""
        sql = kpi.get("sql", "")
        if not sql:
            kpi["value"] = "N/A"
            kpi["error"] = "No SQL provided"
            return kpi

        sql, result = self._validate_and_execute_sql(sql, context=f"KPI:{kpi.get('label', kpi.get('id', '?'))}")
        kpi["sql"] = sql  # store corrected SQL

        if not result["success"]:
            kpi["value"] = "N/A"
            kpi["error"] = result["error"]
            return kpi

        data = result["data"]
        if data and len(data) > 0:
            first_row = data[0]
            if not first_row:
                kpi["value"] = "N/A"
                return kpi

            values = list(first_row.values())
            numeric_val = None
            for v in reversed(values):
                if v is not None and isinstance(v, (int, float)):
                    numeric_val = v
                    break
                try:
                    numeric_val = float(v)
                    break
                except (TypeError, ValueError):
                    continue

            if numeric_val is not None:
                kpi["value"] = numeric_val
            else:
                kpi["value"] = values[0] if values else "N/A"
        else:
            kpi["value"] = "N/A"

        return kpi

    def _execute_chart_sql(self, chart: dict) -> dict:
        """Execute a chart's SQL and populate its data."""
        sql = chart.get("sql", "")
        if not sql:
            chart["data"] = []
            chart["error"] = "No SQL provided"
            return chart

        sql, result = self._validate_and_execute_sql(sql, context=f"Chart:{chart.get('title', chart.get('id', '?'))}")
        chart["sql"] = sql  # store corrected SQL

        if not result["success"]:
            chart["data"] = []
            chart["error"] = result["error"]
            return chart

        chart["data"] = result["data"]

        # ── Rebuild chart_insight if it looks hallucinated ─────────────
        self._rebuild_chart_insight(chart)

        return chart

    @staticmethod
    def _is_hallucinated_insight(text: str) -> bool:
        """Return True if the chart_insight contains known hallucination patterns."""
        if not text:
            return True
        hallmarks = [
            "1234567", "1,234,567", "12345678",      # placeholder numbers
            "123456", "999999",                        # other placeholder rounds
            "Customer A", "Customer B", "Product A",  # placeholder names
            "being 1234", "value being",               # LLM phrasing pattern
        ]
        txt = text.lower()
        return any(h.lower() in txt for h in hallmarks)

    @staticmethod
    def _fmt_crore(val) -> str:
        """Format a raw INR value into Cr / L notation."""
        try:
            v = float(val)
        except (TypeError, ValueError):
            return str(val)
        if v >= 1e7:
            return f"₹{v/1e7:.2f} Cr"
        if v >= 1e5:
            return f"₹{v/1e5:.2f} L"
        return f"₹{v:,.0f}"

    def _rebuild_chart_insight(self, chart: dict) -> None:
        """Compute a factual chart_insight from real data rows.

        Only fires when the LLM's insight looks hallucinated (contains
        placeholder numbers like 1234567.89).
        """
        existing = chart.get("chart_insight", "") or ""
        if not self._is_hallucinated_insight(existing):
            return   # LLM provided a real insight — keep it

        data = chart.get("data") or []
        if not data:
            chart["chart_insight"] = "No data available for this chart."
            return

        rows = data
        title = chart.get("title", "")
        chart_type = chart.get("type", "bar")

        # Get column names of the first row
        first_row = dict(rows[0])
        cols = list(first_row.keys())
        if len(cols) < 2:
            return  # can't build insight without label + value

        label_col = cols[0]
        value_col = cols[1]

        def _fmt(v):
            try:
                fv = float(v)
                if fv > 1e5:
                    return self._fmt_crore(fv)
                if fv == int(fv):
                    return f"{int(fv):,}"
                return f"{fv:,.2f}"
            except (TypeError, ValueError):
                return str(v)

        # Time-series: report trend direction
        if chart_type in ("line", "area") and len(rows) >= 2:
            first_val = float(rows[0].get(value_col, 0) or 0)
            last_val  = float(rows[-1].get(value_col, 0) or 0)
            direction = "increasing" if last_val > first_val else "decreasing"
            first_lbl = rows[0].get(label_col, "")
            last_lbl  = rows[-1].get(label_col, "")
            chart["chart_insight"] = (
                f"{title} shows a {direction} trend from "
                f"{_fmt(first_val)} ({first_lbl}) to {_fmt(last_val)} ({last_lbl})."
            )
            return

        # Ranking / bar / horizontalBar / doughnut: top-N summary
        top = dict(rows[0])
        top_label = top.get(label_col, "")
        top_value = top.get(value_col, 0)
        n = len(rows)

        if n == 1:
            chart["chart_insight"] = (
                f"{top_label} accounts for the full value at {_fmt(top_value)}."
            )
        elif n == 2:
            bot = dict(rows[1])
            bot_label = bot.get(label_col, "")
            bot_value = bot.get(value_col, 0)
            chart["chart_insight"] = (
                f"{top_label}: {_fmt(top_value)}. "
                f"{bot_label}: {_fmt(bot_value)}."
            )
        else:
            # Show top-2 and total
            second = dict(rows[1])
            second_label = second.get(label_col, "")
            second_value = second.get(value_col, 0)
            chart["chart_insight"] = (
                f"Top: {top_label} ({_fmt(top_value)}), "
                f"followed by {second_label} ({_fmt(second_value)}) "
                f"across {n} items."
            )


    def _execute_table_sql(self, table: dict) -> dict:
        """Execute the detail table's SQL and populate data."""
        sql = table.get("sql", "")
        if not sql:
            table["data"] = []
            return table

        sql, result = self._validate_and_execute_sql(sql, context="DetailTable")
        table["sql"] = sql  # store corrected SQL

        if not result["success"]:
            table["data"] = []
            table["error"] = result["error"]
            return table

        table["data"] = result["data"][:200]  # Limit rows for display
        return table

    # ── Parallel async orchestrator ────────────────────────────────────────

    async def _generate_parallel(self, blueprint: dict, question: str) -> dict:
        """Fire all 5 agents simultaneously and merge their outputs.

        Each agent is an independent async coroutine backed by a thread-pool
        executor so that blocking psycopg2 DB calls don't stall the event loop.

        Returns the merged partial report dict ready for post-processing.
        """
        import asyncio
        import time
        from ai.agents.kpi_agent import KpiAgent
        from ai.agents.chart_agent import ChartAgent
        from ai.agents.insight_agent import InsightAgent
        from ai.agents.sql_agent import SqlAgent
        from ai.agents.validation_agent import ValidationAgent

        t0 = time.perf_counter()
        logger.info(
            "Parallel pipeline — launching 5 agents simultaneously "
            "(%d KPIs, %d charts)",
            len(blueprint.get("kpis", [])),
            len(blueprint.get("charts", [])),
        )

        kpi_agent        = KpiAgent(self)
        chart_agent      = ChartAgent(self)
        insight_agent    = InsightAgent()
        sql_agent        = SqlAgent(self)
        validation_agent = ValidationAgent()

        # ── Fire all 5 agents in parallel ─────────────────────────────────
        (
            kpi_result,
            chart_result,
            insight_result,
            sql_result,
            validation_result,
        ) = await asyncio.gather(
            kpi_agent.run(blueprint, question),
            chart_agent.run(blueprint, question),
            insight_agent.run(blueprint, question),
            sql_agent.run(blueprint, question),
            validation_agent.run(blueprint, question),
            return_exceptions=False,
        )

        elapsed = time.perf_counter() - t0
        logger.info("Parallel pipeline — all agents finished in %.3fs total", elapsed)

        # ── Merge agent outputs into the report dict ───────────────────────
        merged = dict(blueprint)   # start from blueprint (preserves title, topic, etc.)

        merged["kpis"]     = kpi_result.get("kpis", [])
        merged["charts"]   = chart_result.get("charts", [])
        merged["insights"] = insight_result.get("insights", [])
        if "table" in sql_result:
            merged["table"] = sql_result["table"]

        # ── Collect per-agent timings for observability ────────────────────
        agent_timings: dict = {}
        for r in (kpi_result, chart_result, insight_result, sql_result, validation_result):
            agent_timings.update(r.get("agent_timing", {}))
        agent_timings["total_parallel"] = round(elapsed, 3)

        # ── Validation cross-check (runs after merge so KPI values exist) ──
        controls = validation_result.get("_validation_controls", {})
        if controls:
            validation_summary = ValidationAgent.cross_check(merged["kpis"], controls)
            merged["validation"] = validation_summary
            flagged = sum(1 for c in validation_summary["checks"] if c["flagged"])
            if flagged:
                logger.warning(
                    "Validation: %d KPI(s) deviate >20%% from DB control values", flagged
                )
            else:
                logger.info("Validation: all KPIs match DB control values ✓")

        return merged, agent_timings

    def generate(self, question: str) -> dict[str, Any]:
        """Generate a complete report with real data (parallel multi-agent pipeline)."""
        import asyncio
        import time

        schema_str = format_schema()
        rels_str = format_relationships()
        profile_str = get_data_profile()
        question_with_date = self._build_question_with_context(question)

        logger.info("Report generation — calling LLM for report blueprint")
        t_start = time.perf_counter()

        # ── Step 1: LLM blueprint call (synchronous — unavoidable) ────────
        result = self.report_gen(
            question=question_with_date,
            schema_info=schema_str,
            relationships=rels_str,
            data_profile=profile_str,
        )

        t_llm = time.perf_counter()
        logger.info("Report blueprint received in %.2fs — launching parallel agents", t_llm - t_start)

        # Parse the JSON output
        try:
            blueprint = self._extract_json(result.report_json)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("Failed to parse report JSON: %s", exc)
            return {
                "mode": "report",
                "error": f"Failed to generate report structure: {str(exc)}",
                "report": None,
            }

        # ── Step 2: Parallel agent execution ──────────────────────────────
        # Run the async orchestrator.  We use asyncio.run() when there is no
        # running event loop (sync context from uvicorn sync worker), or we
        # get the current loop if one already exists (asyncio endpoint).
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're inside an async context — run in thread pool to avoid
                # nested-loop error (e.g. called from async FastAPI endpoint)
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        asyncio.run,
                        self._generate_parallel(blueprint, question),
                    )
                    report, agent_timings = future.result()
            else:
                report, agent_timings = loop.run_until_complete(
                    self._generate_parallel(blueprint, question)
                )
        except RuntimeError:
            # Fallback: create a brand-new event loop
            report, agent_timings = asyncio.run(
                self._generate_parallel(blueprint, question)
            )

        # ── Step 3: Log final counts ──────────────────────────────────────
        final_kpi_count   = len(report.get("kpis", []))
        final_chart_count = len(report.get("charts", []))
        logger.info(
            "Report generation complete — %d KPIs, %d charts survived SQL execution",
            final_kpi_count, final_chart_count,
        )
        logger.info("Agent timings: %s", agent_timings)

        if final_chart_count < 5:
            logger.warning(
                "Only %d charts available after cleanup (expected ≥5).",
                final_chart_count,
            )

        # ── Step 4: Rebuild DB-verified summary ───────────────────────────
        try:
            from ai.report_fallback_charts import detect_report_topic as _detect_topic
            report["summary"] = self._build_db_verified_summary(
                question=question,
                topic=report.get("topic") or _detect_topic(question),
                kpis=report.get("kpis", []),
            )
        except Exception as _sum_err:
            logger.warning("Summary rebuild failed, keeping LLM summary: %s", _sum_err)

        # ── Step 5: Detect applicable filters ─────────────────────────────
        applicable_filters = self._detect_applicable_filters(report)

        return {
            "mode": "report",
            "report": report,
            "applicable_filters": applicable_filters,
            "agent_timings": agent_timings,
            "ui_instructions": {
                "create_new_section": True,
                "open_in_new_tab": True,
                "enable_streaming": True,
                "stream_once": True,
                "include_report_ai": True,
                "report_ai": {
                    "type": "chat_like",
                    "position": "below_report",
                },
                "explanation_feature": {
                    "enabled": True,
                    "trigger": "eye_button",
                },
            },
        }

    # ── LEGACY sequential helpers (kept for apply_filters / modify paths) ─────
    # These are still used by apply_filters() and modify() which are per-filter
    # re-executions, not full report generations.

    def _legacy_execute_kpis_sequential(self, report: dict) -> None:
        """Sequential KPI execution — only used by apply_filters / modify."""
        for kpi in report.get("kpis", []):
            self._execute_kpi_sql(kpi)

    def _legacy_execute_charts_sequential(self, report: dict) -> None:
        """Sequential chart execution — only used by apply_filters / modify."""
        for chart in report.get("charts", []):
            self._execute_chart_sql(chart)

    def _build_db_verified_summary(
        self, question: str, topic: str, kpis: list[dict]
    ) -> str:
        """Build an executive summary using real database values.

        Runs a small set of verified SQL queries for the detected topic,
        then formats them into a concise, accurate summary sentence.
        Replaces the LLM's hallucinated summary.
        """
        from db.executor import execute_sql

        def _q(sql: str):
            """Run SQL and return the first value of the first row, or None."""
            try:
                res = execute_sql(sql)
                if res.get("success") and res.get("data"):
                    return list(res["data"][0].values())[0]
            except Exception:
                pass
            return None

        def _fmt_currency(val) -> str:
            if val is None:
                return "N/A"
            try:
                v = float(val)
            except (TypeError, ValueError):
                return str(val)
            if v >= 1e7:
                return f"₹{v/1e7:.2f} Cr"
            if v >= 1e5:
                return f"₹{v/1e5:.2f} L"
            return f"₹{v:,.0f}"

        def _fmt_num(val) -> str:
            if val is None:
                return "N/A"
            try:
                return f"{int(float(val)):,}"
            except (TypeError, ValueError):
                return str(val)

        def _top3_names(sql: str) -> str:
            """Run a query returning a name column, return 'A, B, and C'."""
            try:
                res = execute_sql(sql)
                if res.get("success") and res.get("data"):
                    names = [list(row.values())[0] for row in res["data"][:3]]
                    if len(names) == 1:
                        return names[0]
                    if len(names) == 2:
                        return f"{names[0]} and {names[1]}"
                    return f"{names[0]}, {names[1]}, and {names[2]}"
            except Exception:
                pass
            return None

        # ── SALES / AOV / FULFILMENT / DEFAULT ────────────────────────
        if topic in ("sales", "aov", "fulfilment", "units", "pricing", "default"):
            total_rev = _q(
                "SELECT ROUND(SUM(total_amount)::numeric,2) FROM sales_order WHERE status='closed'"
            )
            total_orders = _q(
                "SELECT COUNT(*) FROM sales_order WHERE status='closed'"
            )
            aov = _q(
                "SELECT ROUND(AVG(total_amount)::numeric,2) FROM sales_order WHERE status='closed'"
            )
            top_custs = _top3_names(
                "SELECT cm.customer_name FROM sales_order so "
                "JOIN customer_master cm ON so.customer_id=cm.customer_id "
                "WHERE so.status='closed' GROUP BY cm.customer_name "
                "ORDER BY SUM(so.total_amount) DESC LIMIT 3"
            )
            top_cat = _q(
                "SELECT pm.category FROM sales_order so "
                "JOIN sales_order_line sol ON so.so_id=sol.so_id "
                "JOIN sales_order_line_pricing solp ON sol.sol_id=solp.sol_id "
                "JOIN product_master pm ON sol.product_id=pm.product_id "
                "WHERE so.status='closed' GROUP BY pm.category "
                "ORDER BY SUM(solp.line_total) DESC LIMIT 1"
            )
            fulfil = _q(
                "SELECT ROUND(100.0*COUNT(*) FILTER(WHERE status='closed')"
                "/NULLIF(COUNT(*),0),1) FROM sales_order"
            )

            parts = []
            if total_rev and total_orders:
                parts.append(
                    f"Total revenue from {_fmt_num(total_orders)} closed orders "
                    f"stands at {_fmt_currency(total_rev)}."
                )
            if aov:
                parts.append(f"Average order value is {_fmt_currency(aov)}.")
            if top_custs:
                parts.append(
                    f"Top customers by revenue are {top_custs}."
                )
            if top_cat:
                parts.append(
                    f"The '{top_cat}' category leads product revenue."
                )
            if fulfil:
                parts.append(f"Order fulfilment rate is {fulfil}%.")
            return " ".join(parts) if parts else question

        # ── CUSTOMER ──────────────────────────────────────────────────
        if topic == "customer":
            total_custs = _q("SELECT COUNT(*) FROM customer_master")
            active_custs = _q(
                "SELECT COUNT(DISTINCT customer_id) FROM sales_order WHERE status='closed'"
            )
            avg_rev = _q(
                "SELECT ROUND(SUM(total_amount)::numeric/NULLIF(COUNT(DISTINCT customer_id),0),2) "
                "FROM sales_order WHERE status='closed'"
            )
            top_custs = _top3_names(
                "SELECT cm.customer_name FROM sales_order so "
                "JOIN customer_master cm ON so.customer_id=cm.customer_id "
                "WHERE so.status='closed' GROUP BY cm.customer_name "
                "ORDER BY SUM(so.total_amount) DESC LIMIT 3"
            )
            parts = []
            if total_custs and active_custs:
                parts.append(
                    f"Out of {_fmt_num(total_custs)} registered customers, "
                    f"{_fmt_num(active_custs)} are active with closed orders."
                )
            if avg_rev:
                parts.append(f"Average revenue per customer is {_fmt_currency(avg_rev)}.")
            if top_custs:
                parts.append(f"Top customers by revenue are {top_custs}.")
            return " ".join(parts) if parts else question

        # ── PRODUCT ───────────────────────────────────────────────────
        if topic in ("product", "inventory"):
            total_prods = _q("SELECT COUNT(*) FROM product_master")
            total_cats = _q("SELECT COUNT(DISTINCT category) FROM product_master")
            top_prods = _top3_names(
                "SELECT pm.product_name FROM sales_order so "
                "JOIN sales_order_line sol ON so.so_id=sol.so_id "
                "JOIN sales_order_line_pricing solp ON sol.sol_id=solp.sol_id "
                "JOIN product_master pm ON sol.product_id=pm.product_id "
                "WHERE so.status='closed' GROUP BY pm.product_name "
                "ORDER BY SUM(solp.line_total) DESC LIMIT 3"
            )
            top_cat_rev = _q(
                "SELECT pm.category FROM sales_order so "
                "JOIN sales_order_line sol ON so.so_id=sol.so_id "
                "JOIN sales_order_line_pricing solp ON sol.sol_id=solp.sol_id "
                "JOIN product_master pm ON sol.product_id=pm.product_id "
                "WHERE so.status='closed' GROUP BY pm.category "
                "ORDER BY SUM(solp.line_total) DESC LIMIT 1"
            )
            parts = []
            if total_prods and total_cats:
                parts.append(
                    f"The product catalogue contains {_fmt_num(total_prods)} products "
                    f"across {_fmt_num(total_cats)} categories."
                )
            if top_cat_rev:
                parts.append(f"'{top_cat_rev}' is the highest-revenue category.")
            if top_prods:
                parts.append(f"Top products by revenue are {top_prods}.")
            return " ".join(parts) if parts else question

        # ── VENDOR / PROCUREMENT / GOLD / DIAMOND ─────────────────────
        if topic in ("vendor", "procurement", "gold", "diamond"):
            total_vendors = _q("SELECT COUNT(*) FROM vendor_master")
            total_pos = _q("SELECT COUNT(*) FROM purchase_order")
            total_po_val = _q(
                "SELECT ROUND(SUM(total_amount)::numeric,2) FROM purchase_order"
            )
            top_vendors = _top3_names(
                "SELECT vm.vendor_name FROM purchase_order po "
                "JOIN vendor_master vm ON po.vendor_id=vm.vendor_id "
                "GROUP BY vm.vendor_name ORDER BY SUM(po.total_amount) DESC LIMIT 3"
            )
            open_val = _q(
                "SELECT ROUND(SUM(total_amount)::numeric,2) FROM purchase_order WHERE status='open'"
            )
            parts = []
            if total_vendors and total_pos and total_po_val:
                parts.append(
                    f"Procurement spans {_fmt_num(total_vendors)} vendors across "
                    f"{_fmt_num(total_pos)} purchase orders totalling {_fmt_currency(total_po_val)}."
                )
            if open_val:
                parts.append(f"{_fmt_currency(open_val)} is pending in open POs.")
            if top_vendors:
                parts.append(f"Top vendors by PO value are {top_vendors}.")
            return " ".join(parts) if parts else question

        # Fallback — return the question itself as a minimal summary
        return question

    def _smart_fix_chart_type(self, chart: dict) -> None:
        """Auto-correct chart type based on actual data patterns.

        Analyzes the first column (labels) and row count to pick the best
        chart type for the data, overriding the LLM's choice when wrong.
        Also trims excessively large datasets to keep charts readable.
        """
        data = chart.get("data")
        if not data or len(data) == 0:
            return

        chart_type = chart.get("type", "bar").lower()
        row_count = len(data)
        keys = list(data[0].keys())
        label_key = keys[0]
        value_keys = keys[1:]
        labels = [str(row.get(label_key, "")) for row in data]

        # ── Detect time-series labels (dates, months, years) ──────────
        time_patterns = [
            r"^\d{4}-\d{2}$",        # 2024-01
            r"^\d{4}-\d{2}-\d{2}$",  # 2024-01-15
            r"^\d{4}$",              # 2024
            r"^Q[1-4]\s?\d{4}$",     # Q1 2024
            r"^\w{3,9}\s?\d{4}$",    # Jan 2024 / January 2024
        ]
        import re as _re
        is_time_series = False
        if row_count >= 3:
            match_count = sum(
                1 for lbl in labels[:5]
                if any(_re.match(p, lbl.strip()) for p in time_patterns)
            )
            if match_count >= min(3, len(labels[:5])):
                is_time_series = True

        original_type = chart_type

        # Rule 1: Time-series data → line or area (never bar/horizontalBar)
        if is_time_series and chart_type in ("bar", "horizontalBar", "pie", "doughnut"):
            chart["type"] = "line"
            logger.info("Auto-fix chart '%s': %s → line (time-series detected)",
                        chart.get("title", "?"), original_type)

        # Rule 1b: Aggregate daily data → monthly when too many data points
        if is_time_series and row_count > 30:
            # Check if labels are daily (YYYY-MM-DD)
            daily_pattern = r"^\d{4}-\d{2}-\d{2}"
            daily_count = sum(1 for lbl in labels[:10] if _re.match(daily_pattern, lbl.strip()))
            if daily_count >= min(5, len(labels[:10])):
                # Aggregate to monthly
                from collections import OrderedDict
                monthly = OrderedDict()
                for row in data:
                    lbl = str(row.get(label_key, ""))
                    month_key = lbl[:7]  # "2024-01-15" → "2024-01"
                    if month_key not in monthly:
                        monthly[month_key] = {label_key: month_key}
                        for vk in value_keys:
                            monthly[month_key][vk] = 0
                    for vk in value_keys:
                        try:
                            monthly[month_key][vk] += float(row.get(vk, 0) or 0)
                        except (ValueError, TypeError):
                            pass
                chart["data"] = list(monthly.values())
                logger.info("Auto-fix chart '%s': aggregated %d daily → %d monthly data points",
                            chart.get("title", "?"), row_count, len(chart["data"]))
                # Update row_count for subsequent rules
                data = chart["data"]
                row_count = len(data)
                labels = [str(row.get(label_key, "")) for row in data]

        # Rule 2: Too many slices for pie/doughnut → trim to top 6 + "Others"
        elif chart_type in ("pie", "doughnut") and row_count > 8 and value_keys:
            first_val_key = value_keys[0]
            try:
                sorted_data = sorted(
                    data,
                    key=lambda r: float(r.get(first_val_key, 0) or 0),
                    reverse=True
                )
                top_slices = sorted_data[:6]
                others_sum = sum(float(r.get(first_val_key, 0) or 0) for r in sorted_data[6:])
                if others_sum > 0:
                    others_row = {label_key: "Others", first_val_key: others_sum}
                    top_slices.append(others_row)
                chart["data"] = top_slices
                logger.info("Auto-fix chart '%s': trimmed %d → %d slices (kept %s)",
                            chart.get("title", "?"), row_count, len(top_slices), chart_type)
            except (ValueError, TypeError):
                chart["type"] = "bar"  # fallback
                logger.info("Auto-fix chart '%s': %s → bar (trim failed)",
                            chart.get("title", "?"), original_type)

        # Rule 3: Too many categories for bar → horizontalBar
        elif chart_type == "bar" and row_count > 12:
            chart["type"] = "horizontalBar"
            logger.info("Auto-fix chart '%s': bar → horizontalBar (%d categories)",
                        chart.get("title", "?"), row_count)

        # Rule 4: Few categories in horizontalBar → regular bar
        elif chart_type == "horizontalBar" and row_count <= 6:
            chart["type"] = "bar"
            logger.info("Auto-fix chart '%s': horizontalBar → bar (only %d categories)",
                        chart.get("title", "?"), row_count)

        # ── Rule 5: Trim excessive data rows (>15) to Top 10 ─────────
        # For non-time-series, non-pie charts with too many categories
        updated_type = chart.get("type", chart_type).lower()
        updated_count = len(chart.get("data", data))
        if (not is_time_series
                and updated_type not in ("pie", "doughnut")
                and updated_count > 15
                and value_keys):
            first_val_key = value_keys[0]
            try:
                current_data = chart.get("data", data)
                sorted_data = sorted(
                    current_data,
                    key=lambda r: float(r.get(first_val_key, 0) or 0),
                    reverse=True
                )
                chart["data"] = sorted_data[:10]
                chart["type"] = "horizontalBar"
                title = chart.get("title", "")
                if "top" not in title.lower():
                    chart["title"] = f"Top 10 — {title}"
                logger.info("Auto-fix chart '%s': trimmed %d → 10 rows, set horizontalBar",
                            chart.get("title", "?"), updated_count)
            except (ValueError, TypeError):
                pass

    @staticmethod
    def _enforce_chart_diversity(charts: list) -> list:
        """Ensure chart types are appropriate for the data and diverse.

        Rules:
        - Never use polarArea or radar (unreadable with business data)
        - Time-series data (dates in labels) → line or area
        - Proportions/shares (≤8 items) → pie or doughnut
        - Comparisons (>8 items) → horizontalBar
        - Comparisons (≤8 items) → bar
        - Trends with multiple series → stackedBar or area
        - No two charts should use the same type unless necessary
        """
        # Preferred types in order (no polarArea, no radar)
        GOOD_TYPES = ["bar", "line", "pie", "doughnut", "horizontalBar", "stackedBar", "area"]

        TIME_KEYWORDS = ["trend", "growth", "over time", "monthly", "weekly", "daily",
                         "quarterly", "yearly", "timeline", "history", "date", "period"]
        PROPORTION_KEYWORDS = ["distribution", "share", "breakdown", "composition",
                               "by category", "by type", "proportion", "split", "mix"]
        COMPARISON_KEYWORDS = ["top", "ranking", "comparison", "versus", "vs",
                               "best", "worst", "highest", "lowest"]

        def _has_date_labels(chart):
            """Check if the chart's data labels look like dates."""
            data = chart.get("data", [])
            if not data:
                return False
            keys = list(data[0].keys())
            if not keys:
                return False
            label_key = keys[0]
            sample_labels = [str(row.get(label_key, "")) for row in data[:5]]
            date_patterns = [r"\d{4}-\d{2}", r"\d{2}/\d{2}", r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"]
            import re as _re
            for label in sample_labels:
                for pat in date_patterns:
                    if _re.search(pat, label, _re.IGNORECASE):
                        return True
            return False

        def _infer_best_type(chart, used_types):
            """Pick the best chart type based on title, data shape, and what's used."""
            title = (chart.get("title") or "").lower()
            data = chart.get("data", [])
            num_rows = len(data)
            num_cols = len(data[0].keys()) if data else 0

            # Time-series → line or area
            if _has_date_labels(chart) or any(kw in title for kw in TIME_KEYWORDS):
                for t in ["line", "area"]:
                    if t not in used_types:
                        return t
                return "line"

            # Proportions → pie or doughnut
            if any(kw in title for kw in PROPORTION_KEYWORDS) and num_rows <= 10:
                for t in ["pie", "doughnut"]:
                    if t not in used_types:
                        return t
                for t in ["bar", "horizontalBar"]:
                    if t not in used_types:
                        return t
                return "doughnut"

            # Many categories → horizontalBar
            if num_rows > 8:
                if "horizontalBar" not in used_types:
                    return "horizontalBar"
                if "bar" not in used_types:
                    return "bar"

            # Multiple value columns → stackedBar
            if num_cols >= 3:
                if "stackedBar" not in used_types:
                    return "stackedBar"

            # Default: pick first unused good type
            for t in GOOD_TYPES:
                if t not in used_types:
                    return t

            return "bar"

        used_types = set()
        result = []

        for chart in charts:
            original_type = (chart.get("type") or "bar").lower()

            # Force-replace bad chart types
            if original_type in ("polararea", "polarArea", "radar"):
                new_type = _infer_best_type(chart, used_types)
                chart["type"] = new_type
                used_types.add(new_type)
                logger.info(
                    "Chart fix: replaced '%s' with '%s' for '%s'",
                    original_type, new_type, chart.get("title", "?"),
                )
                result.append(chart)
            elif original_type in used_types:
                # Duplicate type — reassign, but protect pie↔doughnut
                if original_type == "pie" and "doughnut" not in used_types:
                    chart["type"] = "doughnut"
                    used_types.add("doughnut")
                    logger.info("Chart diversity: pie → doughnut for '%s'", chart.get("title", "?"))
                elif original_type == "doughnut" and "pie" not in used_types:
                    chart["type"] = "pie"
                    used_types.add("pie")
                    logger.info("Chart diversity: doughnut → pie for '%s'", chart.get("title", "?"))
                else:
                    new_type = _infer_best_type(chart, used_types)
                    chart["type"] = new_type
                    used_types.add(new_type)
                    logger.info(
                        "Chart diversity: changed duplicate '%s' to '%s' for '%s'",
                        original_type, new_type, chart.get("title", "?"),
                    )
                result.append(chart)
            else:
                used_types.add(original_type)
                result.append(chart)

        return result

    @staticmethod
    def _detect_applicable_filters(report: dict) -> dict:
        """Analyze all SQL in the report to determine which filters are applicable.

        Returns a dict like:
        {
            "date_range": True,   # has sales_order with order_date
            "category": True,     # has product_master
            "product": True,      # has product_master
            "customer": True,     # has customer_master
            "status": True,       # has sales_order with status
        }
        """
        # Collect all SQL from KPIs, charts, and table
        all_sql = []
        for kpi in report.get("kpis", []):
            if kpi.get("sql"):
                all_sql.append(kpi["sql"])
        for chart in report.get("charts", []):
            if chart.get("sql"):
                all_sql.append(chart["sql"])
        if report.get("table", {}).get("sql"):
            all_sql.append(report["table"]["sql"])

        combined = " ".join(all_sql).lower()

        has_sales_order = bool(re.search(r'\bsales_order\b(?!_)', combined))
        has_product_master = bool(re.search(r'\bproduct_master\b', combined))
        has_customer_master = bool(re.search(r'\bcustomer_master\b', combined))
        has_order_date = bool(re.search(r'\border_date\b', combined))

        filters = {}

        # Date range filter — applicable if sales_order is referenced
        if has_sales_order and has_order_date:
            filters["date_range"] = True

        # Category & Product — applicable if product_master is referenced
        if has_product_master:
            filters["category"] = True
            filters["product"] = True

        # Customer — applicable if customer_master is referenced
        if has_customer_master:
            filters["customer"] = True

        # Status — applicable if sales_order is referenced
        if has_sales_order:
            filters["status"] = True

        logger.info("Detected applicable filters: %s", filters)
        return filters

    def apply_filters(self, report: dict, filters: dict) -> dict[str, Any]:
        """Apply filters to an existing report by injecting WHERE clauses.

        This does NOT call the LLM — it modifies existing SQL directly.
        Much faster and more reliable than re-generating.
        """
        import copy
        report = copy.deepcopy(report)

        logger.info("Applying filters to existing report: %s", filters)

        # Apply filters to all KPI SQLs and re-execute
        for kpi in report.get("kpis", []):
            original_sql = kpi.get("sql", "")
            if original_sql:
                filtered_sql = _inject_filters(original_sql, filters)
                filtered_sql = _fix_report_sql(filtered_sql)
                kpi["sql"] = filtered_sql
                # Clear previous error/value
                kpi.pop("error", None)
                kpi.pop("value", None)
            self._execute_kpi_sql(kpi)

        # Apply filters to all chart SQLs and re-execute
        for chart in report.get("charts", []):
            original_sql = chart.get("sql", "")
            if original_sql:
                filtered_sql = _inject_filters(original_sql, filters)
                filtered_sql = _fix_report_sql(filtered_sql)
                chart["sql"] = filtered_sql
                # Clear previous error/data
                chart.pop("error", None)
                chart["data"] = []
            self._execute_chart_sql(chart)

        # Apply filters to table SQL and re-execute
        if "table" in report and report["table"]:
            original_sql = report["table"].get("sql", "")
            if original_sql:
                filtered_sql = _inject_filters(original_sql, filters)
                filtered_sql = _fix_report_sql(filtered_sql)
                report["table"]["sql"] = filtered_sql
                report["table"].pop("error", None)
                report["table"]["data"] = []
            self._execute_table_sql(report["table"])

        logger.info("Filter application complete — all SQL re-executed")

        # ── Post-processing: clean up after filter application ────────
        if "kpis" in report:
            all_kpis_f = report["kpis"]
            valid_kpis = [
                kpi for kpi in all_kpis_f
                if kpi.get("value") not in (None, "N/A", "")
                and not kpi.get("error")
            ]
            report["kpis"] = valid_kpis
            logger.info("KPIs after filter cleanup: %d of %d valid", len(valid_kpis), len(all_kpis_f))

            # Guarantee ≥6 KPIs even after filter application
            if len(valid_kpis) < 6:
                from ai.report_fallback_charts import detect_report_topic, get_fallback_kpis
                # Determine topic from the report itself
                topic = report.get("topic", "default")
                existing_ids = {k.get("id", "") for k in valid_kpis}
                fallback_kpis = get_fallback_kpis(topic)
                for fb_kpi in fallback_kpis:
                    if len(valid_kpis) >= 6:
                        break
                    if fb_kpi["id"] in existing_ids:
                        continue
                    fb_copy = dict(fb_kpi)
                    if filters:
                        fb_sql = _inject_filters(fb_copy.get("sql", ""), filters)
                        fb_copy["sql"] = _fix_report_sql(fb_sql)
                    executed = self._execute_kpi_sql(fb_copy)
                    kpi_val = executed.get("value")
                    if kpi_val not in (None, "N/A", "") and not executed.get("error"):
                        valid_kpis.append(executed)
                        existing_ids.add(fb_kpi["id"])
                report["kpis"] = valid_kpis
                logger.info("KPIs after filter fallback fill: %d total", len(valid_kpis))

        if "charts" in report:
            valid_charts = []
            for chart in report["charts"]:
                if chart.get("error"):
                    continue
                if not chart.get("data") or len(chart["data"]) == 0:
                    continue
                row_keys = list(chart["data"][0].keys()) if chart["data"] else []
                if len(row_keys) < 2:
                    continue
                value_keys = row_keys[1:]
                all_zero = all(
                    all((v := row.get(k)) is None or v == 0 or v == "" for k in value_keys)
                    for row in chart["data"]
                )
                if all_zero:
                    continue
                valid_charts.append(chart)
            if valid_charts:
                report["charts"] = valid_charts

        return {
            "mode": "report",
            "report": report,
            "ui_instructions": {
                "create_new_section": True,
                "open_in_new_tab": True,
                "enable_streaming": False,
                "stream_once": False,
            },
        }

    def modify(self, current_report_json: str, modification: str) -> dict[str, Any]:
        """Modify an existing report based on a natural-language command."""
        schema_str = format_schema()

        # ── Step 1: Record original chart types BEFORE the LLM call ──────────
        # Any chart whose type changes in the LLM response was explicitly requested
        # by the user — we must protect those from being overridden by auto-correction.
        _original_types: dict[str, str] = {}
        try:
            _current = (
                json.loads(current_report_json)
                if isinstance(current_report_json, str)
                else current_report_json
            )
            for c in _current.get("charts", []):
                cid = c.get("id") or c.get("title", "")
                if cid:
                    _original_types[cid] = (c.get("type") or "bar").lower()
        except Exception:
            pass

        logger.info("Report modification — command: %s", modification)

        result = self.report_mod(
            current_report=current_report_json,
            modification=modification,
            schema_info=schema_str,
        )

        try:
            report = self._extract_json(result.updated_report_json)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("Failed to parse modified report JSON: %s", exc)
            return {
                "mode": "report",
                "error": f"Failed to modify report: {str(exc)}",
                "report": None,
            }

        # ── Step 2: Detect which chart types were explicitly changed ──────────
        # These are "user-locked" — auto-correction must NOT touch them.
        _user_locked: dict[str, str] = {}   # {chart_id_or_title: new_type_as_given}
        for chart in report.get("charts", []):
            cid = chart.get("id") or chart.get("title", "")
            new_type = (chart.get("type") or "bar").lower()
            if cid and cid in _original_types and _original_types[cid] != new_type:
                _user_locked[cid] = chart.get("type") or new_type
                logger.info(
                    "Modification: chart '%s' type locked as '%s' (changed from '%s' — user intent)",
                    cid, new_type, _original_types[cid],
                )

        # Re-execute all SQL queries on the modified report
        for kpi in report.get("kpis", []):
            self._execute_kpi_sql(kpi)

        for chart in report.get("charts", []):
            self._execute_chart_sql(chart)

        if "table" in report and report["table"]:
            self._execute_table_sql(report["table"])

        # ── Post-processing (same as generate) ──────────────────────────
        if "kpis" in report:
            valid_kpis = [
                kpi for kpi in report["kpis"]
                if kpi.get("value") not in (None, "N/A", "")
                and not kpi.get("error")
            ]
            if valid_kpis:
                report["kpis"] = valid_kpis

        if "charts" in report:
            valid_charts = []
            for chart in report["charts"]:
                if chart.get("error"):
                    continue
                if not chart.get("data") or len(chart["data"]) == 0:
                    continue
                row_keys = list(chart["data"][0].keys()) if chart["data"] else []
                if len(row_keys) < 2:
                    continue
                value_keys = row_keys[1:]
                all_zero = all(
                    all((v := row.get(k)) is None or v == 0 or v == "" for k in value_keys)
                    for row in chart["data"]
                )
                if all_zero:
                    continue
                valid_charts.append(chart)
            if valid_charts:
                report["charts"] = valid_charts

        # ── Smart chart-type auto-correction (data-shape fixes only) ──────────
        # Run _smart_fix_chart_type for data-aggregation benefits (e.g. daily→monthly),
        # but immediately restore any type that was explicitly set by the user.
        if "charts" in report:
            for chart in report["charts"]:
                self._smart_fix_chart_type(chart)

        # ── Step 3: Restore user-locked chart types after auto-correction ─────
        # _smart_fix_chart_type may have re-changed the type — undo that for locked charts.
        # _enforce_chart_diversity is intentionally SKIPPED in modify() — it would override
        # the user's explicit request (e.g. "change pie to bar").
        if _user_locked and "charts" in report:
            for chart in report["charts"]:
                cid = chart.get("id") or chart.get("title", "")
                if cid in _user_locked:
                    chart["type"] = _user_locked[cid]
                    logger.info(
                        "Modification: enforced user-requested type '%s' for chart '%s'",
                        _user_locked[cid], cid,
                    )

        applicable_filters = self._detect_applicable_filters(report)

        return {
            "mode": "report",
            "report": report,
            "applicable_filters": applicable_filters,
            "ui_instructions": {
                "create_new_section": True,
                "open_in_new_tab": False,
                "enable_streaming": False,
                "stream_once": False,
                "include_report_ai": True,
                "report_ai": {
                    "type": "chat_like",
                    "position": "below_report",
                },
                "explanation_feature": {
                    "enabled": True,
                    "trigger": "eye_button",
                },
            },
        }
