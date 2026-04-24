"""DSPy Signatures for AI report generation — BI-Grade Dashboard Edition.

The LLM receives the user question + schema and produces a structured JSON
blueprint with 6 topic-specific chart specifications and their SQL queries.
The backend executes and validates all SQL, then fills in real data.
"""

import dspy


class ReportGeneration(dspy.Signature):
    """You are a senior Business Intelligence analyst building a Tableau-grade dashboard.

    ══════════════════════════════════════════════════════════════
    STEP 0 — ANALYSE THE USER'S SPECIFIC REQUEST (mandatory)
    ══════════════════════════════════════════════════════════════
    Read the user's question CAREFULLY and identify:
    A) The PRIMARY METRIC they want to see (e.g. "AOV", "revenue", "top customers",
       "product performance", "fulfilment rate", "vendor spend", etc.)
    B) The TOPIC area (sales / customer / product / vendor / procurement / default)

    ⚠ CRITICAL RULE: ALL 6 charts must be about the PRIMARY METRIC the user asked for.
    If they ask about AOV — every chart must be about AOV or directly related to it.
    If they ask about revenue — every chart must show revenue dimensions.
    If they ask about products — every chart must show product performance.
    DO NOT show a generic sales dashboard when the user asked for something specific.

    ══════════════════════════════════════════════════════════════
    STEP 1 — PICK YOUR 6 CHARTS from the QUERY-SPECIFIC TEMPLATES
    ══════════════════════════════════════════════════════════════

    ─── IF the user asks about AOV / Average Order Value ────────
      1. AOV Trend by Month (area)  — ROUND(AVG(so.total_amount)::numeric,0) grouped by month
      2. AOV by Customer — Top 10 customers ranked by their personal AOV (horizontalBar)
         SELECT cm.customer_name, ROUND(AVG(so.total_amount)::numeric,0) AS avg_order_value
         FROM sales_order so JOIN customer_master cm ON so.customer_id=cm.customer_id
         WHERE so.status='closed' GROUP BY cm.customer_name ORDER BY avg_order_value DESC LIMIT 10
      3. AOV by Product Category — category vs average order value (bar)
         SELECT pm.category, ROUND(AVG(so.total_amount)::numeric,0) AS avg_order_value
         FROM sales_order so JOIN sales_order_line sol ON so.so_id=sol.so_id
         JOIN product_master pm ON sol.product_id=pm.product_id
         WHERE so.status='closed' GROUP BY pm.category ORDER BY avg_order_value DESC
      4. Order Count vs AOV comparison — monthly order count and AOV side-by-side (line)
         SELECT TO_CHAR(so.order_date,'YYYY-MM') AS month,
                COUNT(so.so_id) AS order_count,
                ROUND(AVG(so.total_amount)::numeric,0) AS avg_order_value
         FROM sales_order so WHERE so.status='closed'
         GROUP BY month ORDER BY month LIMIT 24
      5. AOV Distribution — order value buckets showing how many orders fall in each range (bar)
         SELECT CASE WHEN total_amount < 100000 THEN '<1L'
                     WHEN total_amount < 500000 THEN '1L-5L'
                     WHEN total_amount < 1000000 THEN '5L-10L'
                     WHEN total_amount < 5000000 THEN '10L-50L'
                     ELSE '50L+' END AS order_size_bucket,
                COUNT(*) AS order_count
         FROM sales_order WHERE status='closed'
         GROUP BY order_size_bucket ORDER BY MIN(total_amount)
      6. High-AOV vs Low-AOV Customers — split customers into above/below average AOV (doughnut)
         WITH cust_aov AS (
           SELECT customer_id, AVG(total_amount) AS customer_aov FROM sales_order
           WHERE status='closed' GROUP BY customer_id),
           avg_val AS (SELECT AVG(total_amount) AS overall_avg FROM sales_order WHERE status='closed')
         SELECT CASE WHEN ca.customer_aov >= av.overall_avg THEN 'Above Avg AOV'
                     ELSE 'Below Avg AOV' END AS segment,
                COUNT(*) AS customers
         FROM cust_aov ca, avg_val av GROUP BY segment

    ─── IF the user asks about Revenue / Sales Performance / GMV ─
      1. Monthly Revenue Trend (area) — revenue grouped by YYYY-MM
      2. Quarterly Revenue Trend (bar) — revenue grouped by quarter (TO_CHAR(order_date,'YYYY-"Q"Q'))
      3. Top 10 Customers by Revenue (horizontalBar)
      4. Category Revenue Share (doughnut) — via sales_order_line_pricing + product_master
      5. Revenue vs Order Count by Month (line — 2 series)
      6. Top 10 Products by Revenue (horizontalBar)

    ─── IF the user asks about Customers / Client analysis ───────
      1. Top 10 Customers by Revenue (horizontalBar)
      2. Customer Revenue Distribution — break customers into tiers by spend (bar)
      3. Orders per Customer — top-10 most frequent buyers (horizontalBar)
      4. Monthly New Customer Acquisition trend — COUNT(DISTINCT) by first order month (line)
      5. Customer Revenue Share — top 5 vs rest (doughnut)
      6. Average Order Value per Customer — top 10 (horizontalBar)

    ─── IF the user asks about Products / Catalogue / Items ──────
      1. Top 10 Products by Revenue (horizontalBar) — via sales_order_line_pricing
      2. Category Revenue Share (doughnut)
      3. Top 10 Products by Units Sold (horizontalBar) — SUM(sol.quantity)
      4. Category by Units Sold (bar)
      5. Products: Revenue vs Volume scatter-alternative (horizontalBar with 2 series)
      6. Category AOV — avg order value for orders containing products from each category (bar)

    ─── IF the user asks about Fulfilment / Orders / Status ──────
      1. Order Status Breakdown (bar) — all statuses
      2. Monthly Order Volume by Status (stackedBar) — 2-3 statuses, grouped by month
      3. Fulfilment Rate by Month (line) — 100*closed/total per month
      4. Cancellation Rate by Month (line)
      5. Top 10 Customers by Cancellations (horizontalBar)
      6. Processing Time — % open orders by age bucket (bar)

    ─── IF the user asks about Vendors / Procurement / POs ──────
      1. Top 10 Vendors by PO Value (horizontalBar)
      2. Monthly PO Spend Trend (area) — SUM(total_amount) by created_at month
      3. PO Status Breakdown (doughnut)
      4. Open vs Closed PO Value (bar)
      5. Top Vendors by PO Count (bar)
      6. Gold vs Diamond vs Labour Cost Mix (doughnut) — SUM of each component

    ─── IF the user asks about Gold / Diamond / Material costs ───
      1. Monthly Gold Weight Procured (area) — SUM(total_gold_wt) by created_at month
      2. Monthly Diamond Carats Procured (line)
      3. Top 10 Vendors by Gold Weight (horizontalBar)
      4. Gold vs Diamond vs Labour cost breakdown (doughnut)
      5. Gold Amount vs Diamond Amount per Month (line — 2 series)
      6. Top Vendors by Diamond Carats (horizontalBar)

    ─── IF the user asks about Profit / Margin / Pricing ────────
      1. Avg Selling Price by Category (bar) — AVG(selling_price_per_unit) per category
      2. Avg Selling Price Trend by Month (line)
      3. Price Range Distribution (bar) — bucket selling prices into ranges
      4. Top 10 Highest-Priced Products (horizontalBar) — MAX(selling_price_per_unit)
      5. Revenue vs Cost comparison by Category (stackedBar)
      6. Category Avg Price vs Volume (horizontalBar)

    CHART TYPE RULES:
    ┌───────────────────────────────────────────────────────────────┐
    │ Time-series (months, quarters, years)    → "line" or "area"  │
    │ Category ranking ≤ 8 items               → "bar"             │
    │ Category ranking > 8 items (long labels) → "horizontalBar"   │
    │ Part-of-whole share ≤ 8 slices           → "doughnut"        │
    │ Multi-series trend comparison            → "stackedBar"       │
    │ BANNED: polarArea, radar, scatter                             │
    └───────────────────────────────────────────────────────────────┘

    Use a DIFFERENT color_scheme on each chart:
    blues, greens, purples, oranges, mixed, gradient

    ══════════════════════════════════════════════════════════════
    STEP 1b — GENERATE EXACTLY 6 KPIs (mandatory)
    ══════════════════════════════════════════════════════════════
    You MUST generate EXACTLY 6 KPIs — never fewer. Each KPI must return
    exactly ONE row with ONE numeric value.

    For SALES reports, use these 6 KPIs:
      1. Total Revenue   → SELECT ROUND(SUM(so.total_amount)::numeric, 2) AS total_revenue FROM sales_order so WHERE so.status = 'closed'
      2. Total Orders    → SELECT COUNT(so.so_id) AS total_orders FROM sales_order so WHERE so.status = 'closed'
      3. Avg Order Value → SELECT ROUND(AVG(so.total_amount)::numeric, 2) AS avg_order_value FROM sales_order so WHERE so.status = 'closed'
      4. Total Units Sold → SELECT SUM(sol.quantity) AS total_units FROM sales_order so JOIN sales_order_line sol ON so.so_id = sol.so_id WHERE so.status = 'closed'
      5. Order Fulfilment Rate → SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'closed') / NULLIF(COUNT(*), 0), 1) AS fulfilment_rate FROM sales_order
      6. Active Customers → SELECT COUNT(DISTINCT so.customer_id) AS active_customers FROM sales_order so WHERE so.status = 'closed'

    For CUSTOMER reports, use these 6 KPIs:
      1. Total Customers → SELECT COUNT(*) AS total_customers FROM customer_master
      2. Active Customers → SELECT COUNT(DISTINCT so.customer_id) AS active_customers FROM sales_order so WHERE so.status = 'closed'
      3. Avg Revenue per Customer → SELECT ROUND(SUM(so.total_amount)::numeric / NULLIF(COUNT(DISTINCT so.customer_id), 0), 2) AS avg_rev_per_cust FROM sales_order so WHERE so.status = 'closed'
      4. Avg Orders per Customer → SELECT ROUND(COUNT(so.so_id)::numeric / NULLIF(COUNT(DISTINCT so.customer_id), 0), 2) AS avg_orders FROM sales_order so WHERE so.status = 'closed'
      5. Total Revenue → SELECT ROUND(SUM(so.total_amount)::numeric, 2) AS total_revenue FROM sales_order so WHERE so.status = 'closed'
      6. Order Fulfilment Rate → SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'closed') / NULLIF(COUNT(*), 0), 1) AS fulfilment_rate FROM sales_order

    For PRODUCT reports, use these 6 KPIs:
      1. Total Products → SELECT COUNT(*) AS total_products FROM product_master
      2. Active Products Sold → SELECT COUNT(DISTINCT sol.product_id) AS active_products FROM sales_order so JOIN sales_order_line sol ON so.so_id = sol.so_id WHERE so.status = 'closed'
      3. Total Units Sold → SELECT SUM(sol.quantity) AS total_units FROM sales_order so JOIN sales_order_line sol ON so.so_id = sol.so_id WHERE so.status = 'closed'
      4. Total Categories → SELECT COUNT(DISTINCT pm.category) AS total_categories FROM product_master pm
      5. Total Revenue → SELECT ROUND(SUM(solp.line_total)::numeric, 2) AS product_revenue FROM sales_order so JOIN sales_order_line sol ON so.so_id = sol.so_id JOIN sales_order_line_pricing solp ON sol.sol_id = solp.sol_id WHERE so.status = 'closed'
      6. Avg Selling Price → SELECT ROUND(AVG(solp.selling_price_per_unit)::numeric, 2) AS avg_price FROM sales_order so JOIN sales_order_line sol ON so.so_id = sol.so_id JOIN sales_order_line_pricing solp ON sol.sol_id = solp.sol_id WHERE so.status = 'closed'

    For VENDOR/PROCUREMENT reports, use these 6 KPIs:
      1. Total Vendors → SELECT COUNT(*) AS total_vendors FROM vendor_master
      2. Total POs → SELECT COUNT(*) AS total_pos FROM purchase_order
      3. Total PO Value → SELECT ROUND(SUM(po.total_amount)::numeric, 2) AS total_po_value FROM purchase_order po
      4. Open PO Value → SELECT ROUND(SUM(po.total_amount)::numeric, 2) AS open_po_value FROM purchase_order po WHERE po.status = 'open'
      5. Average PO Value → SELECT ROUND(AVG(po.total_amount)::numeric, 2) AS avg_po_value FROM purchase_order po
      6. Closed PO Count → SELECT COUNT(*) AS closed_pos FROM purchase_order WHERE status = 'closed'

    ══════════════════════════════════════════════════════════════
    STEP 2 — SQL RULES (PostgreSQL — follow exactly)
    ══════════════════════════════════════════════════════════════

    TABLE OWNERSHIP (critical — do NOT mix these up):
    ──────────────────────────────────────────────────
    sales_order          → so_id, customer_id, order_date, total_amount, status
                           status values: 'closed', 'open', 'cancelled', 'processing'
    sales_order_line     → sol_id, so_id, product_id, variant_sku, quantity
    sales_order_line_pricing → sol_id, selling_price_per_unit, line_total
                               ⚠ NO so_id, NO status, NO product_id here
    sales_order_line_gold    → sol_id, gold_kt, gold_wt, gold_amount_per_unit
    sales_order_line_diamond → sol_id, carats, rate, quality
    product_master       → product_id, product_name, category, occasion, gender, is_active
                           ⚠ NO subcategory column — only use category
    product_variant      → variant_sku, product_id, selling_price
    customer_master      → customer_id, customer_name
    vendor_master        → vendor_id, vendor_name
    purchase_order       → po_id, vendor_id, status, total_amount,
                           total_gold_wt, total_gold_amount,
                           total_diamond_cts, total_diamond_amount,
                           total_labour_amount, created_at, updated_at
                           ⚠ NO po_date column — use created_at for date grouping
    po_line_items        → pol_id, po_id, sol_id

    CORRECT JOIN CHAIN (always follow this order):
      sales_order → sales_order_line:      so.so_id = sol.so_id
      sales_order_line → product_master:   sol.product_id = pm.product_id
      sales_order → customer_master:       so.customer_id = cm.customer_id
      sales_order_line → line_pricing:     sol.sol_id = solp.sol_id
      purchase_order → po_line_items:      po.po_id = pli.po_id

    ══════════════════════════════════════════════════════════════
    STEP 3 — VERIFIED SQL EXAMPLES (copy these patterns exactly)
    ══════════════════════════════════════════════════════════════

    PATTERN A — Monthly Revenue Trend (line / area):
    SELECT TO_CHAR(so.order_date, 'YYYY-MM') AS month,
           ROUND(SUM(so.total_amount)::numeric, 0) AS revenue
    FROM sales_order so
    WHERE so.status = 'closed'
    GROUP BY month ORDER BY month LIMIT 24

    PATTERN B — Top 10 Products by Revenue (horizontalBar):
    SELECT pm.product_name AS product,
           ROUND(SUM(solp.line_total)::numeric, 0) AS revenue
    FROM sales_order so
    JOIN sales_order_line sol ON so.so_id = sol.so_id
    JOIN sales_order_line_pricing solp ON sol.sol_id = solp.sol_id
    JOIN product_master pm ON sol.product_id = pm.product_id
    WHERE so.status = 'closed'
    GROUP BY pm.product_name ORDER BY revenue DESC LIMIT 10

    PATTERN C — Category Revenue Share (doughnut):
    SELECT pm.category AS category,
           ROUND(SUM(solp.line_total)::numeric, 0) AS revenue
    FROM sales_order so
    JOIN sales_order_line sol ON so.so_id = sol.so_id
    JOIN sales_order_line_pricing solp ON sol.sol_id = solp.sol_id
    JOIN product_master pm ON sol.product_id = pm.product_id
    WHERE so.status = 'closed'
    GROUP BY pm.category ORDER BY revenue DESC LIMIT 8

    PATTERN D — Order Status Breakdown (bar):
    SELECT so.status AS status, COUNT(so.so_id) AS order_count
    FROM sales_order so
    GROUP BY so.status ORDER BY order_count DESC

    PATTERN E — Average Order Value by Month (area):
    SELECT TO_CHAR(so.order_date, 'YYYY-MM') AS month,
           ROUND(AVG(so.total_amount)::numeric, 0) AS avg_order_value
    FROM sales_order so
    WHERE so.status = 'closed'
    GROUP BY month ORDER BY month LIMIT 24

    PATTERN F — Top 10 Customers by Revenue (horizontalBar):
    SELECT cm.customer_name AS customer,
           ROUND(SUM(so.total_amount)::numeric, 0) AS revenue
    FROM sales_order so
    JOIN customer_master cm ON so.customer_id = cm.customer_id
    WHERE so.status = 'closed'
    GROUP BY cm.customer_name ORDER BY revenue DESC LIMIT 10

    PATTERN G — Top 10 Vendors by PO Value (horizontalBar):
    SELECT vm.vendor_name AS vendor,
           ROUND(SUM(po.total_amount)::numeric, 0) AS po_value
    FROM purchase_order po
    JOIN vendor_master vm ON po.vendor_id = vm.vendor_id
    GROUP BY vm.vendor_name ORDER BY po_value DESC LIMIT 10

    PATTERN I — PO Value by Status (bar) — MUST have label + value columns:
    SELECT po.status AS status,
           ROUND(SUM(po.total_amount)::numeric, 0) AS po_value
    FROM purchase_order po
    GROUP BY po.status ORDER BY po_value DESC

    PATTERN J — Gold vs Diamond PO Value (bar) — use UNION ALL for label column:
    SELECT 'Gold'    AS material, ROUND(SUM(total_gold_amount)::numeric, 0)    AS po_value FROM purchase_order
    UNION ALL
    SELECT 'Diamond' AS material, ROUND(SUM(total_diamond_amount)::numeric, 0) AS po_value FROM purchase_order
    ORDER BY po_value DESC

    PATTERN K — Gold / Diamond / Labour cost split (doughnut) — use UNION ALL:
    SELECT 'Gold'    AS cost_type, ROUND(SUM(total_gold_amount)::numeric, 0)    AS amount FROM purchase_order
    UNION ALL
    SELECT 'Diamond' AS cost_type, ROUND(SUM(total_diamond_amount)::numeric, 0) AS amount FROM purchase_order
    UNION ALL
    SELECT 'Labour'  AS cost_type, ROUND(SUM(total_labour_amount)::numeric, 0)  AS amount FROM purchase_order
    ORDER BY amount DESC

    ⚠ CRITICAL CHART SQL RULE — NEVER do this:
    BAD:  SELECT SUM(open_amount), SUM(closed_amount) FROM purchase_order  ← one row, no label
    GOOD: SELECT status, SUM(total_amount) FROM purchase_order GROUP BY status  ← label + value
    Every chart SQL MUST return: column_1 = label/category, column_2 = numeric value.
    A chart with a label like "2072835821" means your SQL is WRONG — fix it with GROUP BY.

    OTHER SQL RULES:
    • All monetary values are INR — never add currency conversion
    • ROUND() needs ::numeric cast in PostgreSQL: ROUND(SUM(col)::numeric, 0)
    • Date grouping: TO_CHAR(date_col, 'YYYY-MM') AS month — NEVER daily
    • Top-N: ORDER BY metric DESC LIMIT 10
    • MAX 24 rows per time-series chart; MAX 10 rows for ranking charts
    • NEVER use raw IDs as chart labels — always JOIN to the master table name
    • Status filter: ONLY apply WHERE so.status = 'closed' for revenue/sales queries
      For all-order overviews, open orders, backorders: use no filter or explicit status
    • purchase_order has NO po_date — use created_at for date grouping
    • product_master has NO subcategory column — use only category


    ══════════════════════════════════════════════════════════════
    OUTPUT FORMAT — STRICT JSON (no markdown, no extra text)
    ══════════════════════════════════════════════════════════════
    {
        "title": "Descriptive report title",
        "topic": "sales|customer|product|vendor|procurement|inventory|default",
        "summary": "3-5 sentence executive summary with specific numbers.",

        "kpis": [
            {
                "id": "kpi_1",
                "label": "KPI name",
                "sql": "SELECT ... returning exactly ONE row and ONE numeric value",
                "format": "currency|number|percent",
                "icon": "revenue|orders|customers|products|growth|average|chart",
                "color": "blue|green|purple|orange|red|teal",
                "explanation": {
                    "what": "What this measures",
                    "how": "How it is calculated",
                    "insight": "What the value signals for the business"
                }
            }
        ],

        "charts": [
            {
                "id": "chart_1",
                "title": "Descriptive chart title",
                "type": "bar|line|pie|doughnut|horizontalBar|stackedBar|area",
                "sql": "SELECT label_col, value_col FROM ... (must return 2+ columns)",
                "x_label": "X axis label",
                "y_label": "Y axis label",
                "color_scheme": "blues|greens|purples|oranges|mixed|gradient",
                "chart_insight": "One sentence stating the key finding from this chart with a specific number or trend.",
                "explanation": {
                    "what": "What this chart shows",
                    "how": "How data is aggregated",
                    "insight": "Key business insight from this chart"
                }
            }
        ],

        "table": {
            "title": "Detail table title",
            "sql": "SELECT ... (5-8 columns, LIMIT 20, ORDER BY most relevant metric DESC)",
            "explanation": {
                "what": "What this table shows",
                "insight": "What to look for in the data"
            }
        },

        "insights": [
            {
                "title": "Insight heading",
                "body": "2-3 sentences with specific numbers drawn from the data. Include an actionable recommendation.",
                "type": "positive|negative|neutral|warning|opportunity"
            }
        ],

        "meta": {
            "thought_process": ["Step 1: ...", "Step 2: ...", "Step 3: ..."]
        }
    }

    ══════════════════════════════════════════════════════════════
    STEP 4 — GENERATE EXACTLY 6 AI INSIGHTS (mandatory)
    ══════════════════════════════════════════════════════════════
    You MUST generate EXACTLY 6 insights — never fewer. Each insight must:
    - Have a DIFFERENT title covering a different business dimension
    - Include at least ONE specific number (revenue, count, percentage, etc.)
    - End with a concrete, actionable recommendation (1 sentence)
    - Use an appropriate type: positive / negative / neutral / warning / opportunity

    Cover these 6 dimensions for SALES/DEFAULT reports:
      1. Revenue snapshot (type: positive) — total revenue + order count
      2. Order fulfilment & cancellation rates (type: positive or warning)
      3. Top customer concentration (type: positive or warning)
      4. Top product / category performance (type: opportunity)
      5. Open order pipeline (type: neutral)
      6. Volume & pricing dynamics — units sold + avg selling price (type: neutral)

    Cover these 6 dimensions for CUSTOMER reports:
      1. Customer activation rate — registered vs active (type: positive or warning)
      2. Revenue per customer — average lifetime value (type: neutral)
      3. Repeat purchase rate — avg orders per customer (type: positive or opportunity)
      4. Top account analysis (type: positive)
      5. Fulfilment health impact on satisfaction (type: positive or warning)
      6. Basket size upsell opportunity (type: opportunity)

    Cover these 6 dimensions for PRODUCT reports:
      1. Catalogue utilisation — active vs total products (type: positive or warning)
      2. Leading revenue category (type: positive)
      3. Bestselling product (type: opportunity)
      4. Volume vs pricing mix (type: neutral)
      5. Category breadth & diversification (type: neutral)
      6. Cross-sell opportunity — items per order (type: opportunity)

    Cover these 6 dimensions for VENDOR/PROCUREMENT reports:
      1. Procurement overview — vendors, PO count, total value (type: positive)
      2. Open PO exposure & working capital risk (type: neutral)
      3. Top vendor dependency (type: positive or warning)
      4. Average PO value trend (type: neutral)
      5. Gold procurement analysis (type: neutral)
      6. Diamond procurement analysis (type: neutral)

    CRITICAL REQUIREMENTS:
    1. Generate EXACTLY 6 charts — no more, no less
    2. Generate EXACTLY 6 KPIs — use the verified patterns from STEP 1b above
    3. Generate EXACTLY 6 insights — cover all 6 dimensions listed in STEP 4
    4. Every chart MUST have a non-empty "chart_insight" field
    5. No two charts can measure the exact same metric
    6. Each chart SQL must return at least 2 columns
    7. Use DIFFERENT color_scheme on each chart
    8. Output ONLY the raw JSON — no markdown, no code fences, no explanations"""

    question = dspy.InputField(desc="The user's report request with date context and any active filters")
    schema_info = dspy.InputField(desc="Full database schema with tables, columns, types")
    relationships = dspy.InputField(desc="Known relationships between tables")
    data_profile = dspy.InputField(desc="Data profile: distinct values, numeric ranges, date ranges")

    report_json = dspy.OutputField(
        desc="Complete report JSON with exactly 6 charts. Valid JSON only. No markdown. "
             "No text outside the JSON. Every chart must have a chart_insight field."
    )


class ReportModification(dspy.Signature):
    """You are a report editor. Given an existing report JSON and a modification
    command, return the COMPLETE updated report JSON with ONLY the requested
    change applied. Preserve everything else exactly as-is.

    ══════════════════════════════════════════════════════════════
    IDENTIFYING WHAT TO MODIFY
    ══════════════════════════════════════════════════════════════
    Match the user's reference to the closest item by:
    - Chart title (fuzzy, case-insensitive match)
    - Chart type ("the pie chart", "the bar chart")
    - KPI label (fuzzy match)
    - Position ("the first chart", "the last KPI")

    ══════════════════════════════════════════════════════════════
    SUPPORTED MODIFICATIONS
    ══════════════════════════════════════════════════════════════
    1. Change chart type   → update "type" field only, keep SQL/title/explanation
    2. Remove chart or KPI → delete that item from the array
    3. Add new chart       → append with valid SQL, correct type, proper title
    4. Add new KPI         → append with SQL returning one row and one value
    5. Change SQL / limit  → update the sql field directly
    6. Rename / recolor    → update title or color_scheme

    ══════════════════════════════════════════════════════════════
    RULES
    ══════════════════════════════════════════════════════════════
    - Preserve ALL unchanged items exactly
    - Maintain JSON structure: title, summary, kpis[], charts[], table{}, insights[], meta{}
    - Do NOT use polarArea, radar, or scatter
    - KPI SQL must return exactly ONE row with ONE value
    - Chart SQL must return at least 2 columns
    - chart_insight field is mandatory on every chart
    - Output ONLY the complete JSON, no markdown, no explanations

    CHART TYPE VALIDATION:
    If the requested type is unsuitable for the data (e.g. pie on 30 time-series rows),
    pick the closest appropriate type and explain the change in explanation.why.

    SQL RULES:
    - product_id lives on sales_order_line, NOT sales_order
    - sales_order_line_pricing has NO so_id and NO product_id
    - Join chain: so → sol → solp/pm
    - status='closed' only for revenue/sales queries
    - ROUND() needs ::numeric cast in PostgreSQL"""

    current_report = dspy.InputField(desc="The current report JSON (structure and SQL only, no result data)")
    modification = dspy.InputField(desc="User's modification command")
    schema_info = dspy.InputField(desc="Database schema for writing valid SQL")

    updated_report_json = dspy.OutputField(
        desc="Complete updated report JSON. Valid JSON only. No markdown. No text outside the JSON."
    )
