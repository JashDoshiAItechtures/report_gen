"""DSPy signatures for AI-driven full report editing via natural language chat."""

import dspy


class ReportEditIntent(dspy.Signature):
    """Analyse a report editing command and extract a structured intent.

    ══════════════════════════════════════════════════════════
    AVAILABLE INTENTS
    ══════════════════════════════════════════════════════════
    add_chart    → add a brand-new chart to the report
    modify_chart → change an existing chart (type, axes, filters, data)
    remove_chart → delete an existing chart from the report
    add_kpi      → add a new KPI card to the report
    remove_kpi   → delete an existing KPI card
    unknown      → cannot determine intent clearly

    ══════════════════════════════════════════════════════════
    TARGET EXTRACTION RULES
    ══════════════════════════════════════════════════════════
    - If the command contains @Mention (e.g. @Top-Products or @Sales-Chart),
      use the @mention as the target (replacing hyphens with spaces to match titles).
    - Otherwise, match the command to the closest title in chart_titles or kpi_titles.
    - For add operations set target = 'new'.
    - If no match found set target = 'unknown'.

    ══════════════════════════════════════════════════════════
    MODIFICATION_DETAIL
    ══════════════════════════════════════════════════════════
    - add_chart:    precise description, e.g. "bar chart top 10 customers by total revenue DESC"
    - modify_chart: specific change, e.g. "convert to line chart, group by month"
    - add_kpi:      description, e.g. "total revenue for current month in currency format"
    - remove_*:     empty string
    """

    command = dspy.InputField(
        desc="User's natural language editing command (may contain @Mention)"
    )
    report_summary = dspy.InputField(
        desc="Current report — chart_titles: [...], kpi_titles: [...]"
    )
    schema_info = dspy.InputField(
        desc="Database schema for understanding data references"
    )

    intent = dspy.OutputField(
        desc="One of: add_chart | modify_chart | remove_chart | add_kpi | remove_kpi | unknown"
    )
    target = dspy.OutputField(
        desc="Exact chart/KPI title to target (must match report_summary), 'new', or 'unknown'"
    )
    modification_detail = dspy.OutputField(
        desc="Specific instruction for the sub-operation. Empty string for remove operations."
    )
    confidence = dspy.OutputField(desc="high | medium | low")
    clarification = dspy.OutputField(
        desc="Specific question to ask if confidence=low, else exactly NONE"
    )


class SingleChartGeneration(dspy.Signature):
    """Generate ONE chart specification (SQL + metadata) from a plain English description.

    SQL RULES:
    ─────────────────────────────────────────────────────────
    - SELECT only. First column = label/dimension; remaining columns = numeric values.
    - Use ORDER BY for ranked/sorted data. Use DATE_TRUNC for time series.
    - Default LIMIT 20; honour explicit top-N if mentioned.
    - Use clear, readable column aliases.

    CHART TYPE GUIDANCE:
    bar | horizontalBar  → comparisons, rankings
    line | area          → time series, trends
    pie | doughnut       → composition / share (≤ 8 items)
    stackedBar           → multi-series comparison
    """

    description = dspy.InputField(
        desc="Plain English description of the chart to generate"
    )
    schema_info = dspy.InputField(
        desc="Full database schema with tables and columns"
    )
    existing_titles = dspy.InputField(
        desc="Titles of existing charts (generate a different, non-duplicate title)"
    )

    sql        = dspy.OutputField(desc="Complete valid SQL SELECT query")
    chart_type = dspy.OutputField(
        desc="bar | horizontalBar | line | area | pie | doughnut | stackedBar"
    )
    title   = dspy.OutputField(desc="Descriptive, unique chart title")
    x_label = dspy.OutputField(desc="X-axis label (short)")
    y_label = dspy.OutputField(desc="Y-axis label (short)")


class SingleKPIGeneration(dspy.Signature):
    """Generate ONE KPI specification from a plain English description.

    SQL RULES:
    ─────────────────────────────────────────────────────────
    - SELECT only. Must return exactly 1 row with 1 numeric aggregate column.
    - Use SUM, COUNT, AVG, MAX, MIN as appropriate.
    - NEVER use LIMIT — aggregate queries already return 1 row.
    - NEVER use LIMIT 1 — this would return only 1 data row, not the aggregate.
    - Always use a meaningful alias on the aggregate column.
    - Use the correct table and column names from schema_info.
    - Use WHERE only when the description explicitly requires filtering (e.g. by status/date).

    PRIMARY TABLE GUIDANCE (use these, NOT the stub 'orders' / 'order_items' tables):
    ─────────────────────────────────────────────────────────
    - Order counts / total orders   → sales_order  (columns: so_id, order_date, status, total_amount, customer_id)
    - Revenue / order value         → sales_order  (use SUM(total_amount) or AVG(total_amount))
    - Product revenue / line items  → sales_order_line  joined to sales_order
    - Purchase orders               → purchase_order
    - Customers                     → customer_master

    EXAMPLES:
    - "total orders"            → SELECT COUNT(*) AS total_orders FROM sales_order
    - "total revenue"           → SELECT SUM(total_amount) AS total_revenue FROM sales_order
    - "average order value"     → SELECT AVG(total_amount) AS avg_order_value FROM sales_order
    - "closed orders"           → SELECT COUNT(*) AS closed_orders FROM sales_order WHERE status = 'closed'
    - "completed orders"        → SELECT COUNT(*) AS completed_orders FROM sales_order WHERE status = 'completed'
    - "total customers"         → SELECT COUNT(*) AS total_customers FROM customer_master

    FORMAT: currency (monetary ₹ values) | percent (ratio/rate values) | number (plain counts)
    """

    description = dspy.InputField(
        desc="Plain English description of the KPI (e.g. 'total revenue this month')"
    )
    schema_info = dspy.InputField(
        desc="Full database schema with tables and columns"
    )
    existing_titles = dspy.InputField(
        desc="Titles of existing KPIs (generate a different, non-duplicate title)"
    )

    sql         = dspy.OutputField(desc="SQL SELECT query returning 1 row × 1 numeric column. NO LIMIT clause.")
    title       = dspy.OutputField(desc="Short KPI title, e.g. 'Total Revenue', 'Order Count'")
    format      = dspy.OutputField(desc="currency | percent | number")
    explanation = dspy.OutputField(desc="One sentence explaining what this KPI measures and how it is calculated")
