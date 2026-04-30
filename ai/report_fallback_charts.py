"""Pre-verified fallback chart SQL templates per report topic.

When the LLM-generated chart SQL fails validation or returns empty data,
these guaranteed-to-work templates are used to fill the chart slots up to 6.

All SQL queries here have been manually verified against the production schema:
  sales_order, sales_order_line, sales_order_line_pricing, product_master,
  customer_master, vendor_master, purchase_order, po_line_items
"""

import re
from typing import Any

# ── Topic keyword mapping (broad + fine-grained sub-topics) ─────────────────

_TOPIC_KEYWORDS = {
    # Fine-grained sub-topics first (checked with higher priority)
    "aov": [
        "aov", "average order value", "avg order value", "order value",
        "basket size", "basket value", "average basket", "mean order",
    ],
    "fulfilment": [
        "fulfilment", "fulfillment", "fulfilment rate", "cancellation rate",
        "order status", "cancellation", "backlog", "processing rate",
    ],
    "units": [
        "units sold", "quantity sold", "volume sold", "units shipped",
        "number of items", "items sold", "product quantity",
    ],
    "pricing": [
        "selling price", "price analysis", "pricing report", "price trend",
        "price distribution", "margin", "price per unit",
    ],
    "gold": [
        "gold", "gold weight", "gold procurement", "gold analysis",
    ],
    "diamond": [
        "diamond", "diamond carats", "diamond procurement", "carat",
    ],
    # Broad topics
    "customer": [
        "customer", "client", "buyer", "purchaser", "consumer",
        "customer report", "customer analysis", "top customers",
    ],
    "product": [
        "product", "item", "catalogue", "catalog", "sku", "variant",
        "product report", "product analysis", "product performance",
        "top product", "best product", "selling product",
    ],
    "vendor": [
        "vendor", "supplier", "vendor report", "vendor analysis",
        "supplier report", "vendor performance",
    ],
    "procurement": [
        "procurement", "purchase order", "po report", "purchasing",
        "po analysis", "procurement report", "po pipeline",
        "backorder", "back order",
    ],
    "inventory": [
        "inventory", "stock", "warehouse", "instock", "in-stock",
        "inventory report", "stock report",
    ],
    "sales": [
        "sales", "revenue", "order", "gmv", "sales report",
        "revenue report", "sales performance", "monthly sales",
        "sales analysis", "sales trend", "sales dashboard",
    ],
}

_PALETTE_CYCLE = ["blues", "greens", "purples", "oranges", "mixed", "gradient"]


def detect_report_topic(question: str) -> str:
    """Classify a report question into a topic (including fine-grained sub-topics).

    Returns: 'aov' | 'fulfilment' | 'units' | 'pricing' | 'gold' | 'diamond' |
             'sales' | 'customer' | 'product' | 'vendor' |
             'procurement' | 'inventory' | 'default'
    """
    q = question.lower().strip()

    # Fine-grained sub-topics checked first (higher priority)
    fine_grained_priority = ["aov", "fulfilment", "units", "pricing", "gold", "diamond"]
    for topic in fine_grained_priority:
        for kw in _TOPIC_KEYWORDS.get(topic, []):
            if kw in q:
                return topic

    # Broad topic fallback
    broad_priority = ["procurement", "vendor", "inventory", "customer", "product", "sales"]
    for topic in broad_priority:
        for kw in _TOPIC_KEYWORDS.get(topic, []):
            if kw in q:
                return topic

    return "default"


# ── Fallback chart templates ───────────────────────────────────────────────

def _chart(id_, title, type_, sql, x_label, y_label, scheme, insight):
    return {
        "id": id_,
        "title": title,
        "type": type_,
        "sql": sql,
        "x_label": x_label,
        "y_label": y_label,
        "color_scheme": scheme,
        "chart_insight": insight,
        "explanation": {
            "what": title,
            "how": "Aggregated from live database query",
            "insight": insight,
        },
    }


# ── SALES fallback charts ──────────────────────────────────────────────────

_SALES_CHARTS = [
    _chart(
        "fb_s1", "Monthly Revenue Trend", "area",
        """SELECT TO_CHAR(so.order_date, 'YYYY-MM') AS month,
               ROUND(SUM(so.total_amount)::numeric, 0) AS revenue
        FROM sales_order so
        WHERE so.status = 'closed'
        GROUP BY month ORDER BY month LIMIT 24""",
        "Month", "Revenue (₹)", "blues",
        "Tracks how total revenue has evolved month-over-month to identify peak seasons.",
    ),
    _chart(
        "fb_s2", "Top 10 Products by Revenue", "horizontalBar",
        """SELECT pm.product_name AS product,
               ROUND(SUM(solp.line_total)::numeric, 0) AS revenue
        FROM sales_order so
        JOIN sales_order_line sol ON so.so_id = sol.so_id
        JOIN sales_order_line_pricing solp ON sol.sol_id = solp.sol_id
        JOIN product_master pm ON sol.product_id = pm.product_id
        WHERE so.status = 'closed'
        GROUP BY pm.product_name ORDER BY revenue DESC LIMIT 10""",
        "Revenue (₹)", "Product", "greens",
        "The top product drives the most revenue — highlights your bestselling items.",
    ),
    _chart(
        "fb_s3", "Revenue by Category", "doughnut",
        """SELECT pm.category AS category,
               ROUND(SUM(solp.line_total)::numeric, 0) AS revenue
        FROM sales_order so
        JOIN sales_order_line sol ON so.so_id = sol.so_id
        JOIN sales_order_line_pricing solp ON sol.sol_id = solp.sol_id
        JOIN product_master pm ON sol.product_id = pm.product_id
        WHERE so.status = 'closed'
        GROUP BY pm.category ORDER BY revenue DESC LIMIT 8""",
        "Category", "Revenue (₹)", "purples",
        "Shows which product categories contribute the most to total sales revenue.",
    ),
    _chart(
        "fb_s4", "Order Status Breakdown", "bar",
        """SELECT so.status AS status, COUNT(so.so_id) AS order_count
        FROM sales_order so
        GROUP BY so.status ORDER BY order_count DESC""",
        "Status", "Order Count", "oranges",
        "Breaks down orders by status to reveal fulfillment efficiency and pipeline health.",
    ),
    _chart(
        "fb_s5", "Average Order Value by Month", "line",
        """SELECT TO_CHAR(so.order_date, 'YYYY-MM') AS month,
               ROUND(AVG(so.total_amount)::numeric, 0) AS avg_order_value
        FROM sales_order so
        WHERE so.status = 'closed'
        GROUP BY month ORDER BY month LIMIT 24""",
        "Month", "Avg Order Value (₹)", "mixed",
        "Tracks basket size over time — rising AOV signals upselling success.",
    ),
    _chart(
        "fb_s6", "Top 10 Customers by Revenue", "horizontalBar",
        """SELECT cm.customer_name AS customer,
               ROUND(SUM(so.total_amount)::numeric, 0) AS revenue
        FROM sales_order so
        JOIN customer_master cm ON so.customer_id = cm.customer_id
        WHERE so.status = 'closed'
        GROUP BY cm.customer_name ORDER BY revenue DESC LIMIT 10""",
        "Revenue (₹)", "Customer", "gradient",
        "Identifies your highest-value customers to prioritize account management.",
    ),
]

# ── CUSTOMER fallback charts ───────────────────────────────────────────────

_CUSTOMER_CHARTS = [
    _chart(
        "fb_c1", "Monthly New Customer Orders", "area",
        """SELECT TO_CHAR(so.order_date, 'YYYY-MM') AS month,
               COUNT(DISTINCT so.customer_id) AS active_customers
        FROM sales_order so
        GROUP BY month ORDER BY month LIMIT 24""",
        "Month", "Active Customers", "blues",
        "Shows customer engagement trend — how many unique customers placed orders each month.",
    ),
    _chart(
        "fb_c2", "Top 10 Customers by Revenue", "horizontalBar",
        """SELECT cm.customer_name AS customer,
               ROUND(SUM(so.total_amount)::numeric, 0) AS revenue
        FROM sales_order so
        JOIN customer_master cm ON so.customer_id = cm.customer_id
        WHERE so.status = 'closed'
        GROUP BY cm.customer_name ORDER BY revenue DESC LIMIT 10""",
        "Revenue (₹)", "Customer", "greens",
        "Your top customers by lifetime revenue — key accounts to protect and grow.",
    ),
    _chart(
        "fb_c3", "Top 10 Customers by Order Count", "horizontalBar",
        """SELECT cm.customer_name AS customer,
               COUNT(so.so_id) AS order_count
        FROM sales_order so
        JOIN customer_master cm ON so.customer_id = cm.customer_id
        GROUP BY cm.customer_name ORDER BY order_count DESC LIMIT 10""",
        "Order Count", "Customer", "purples",
        "Most frequent buyers — high order count customers are your most loyal.",
    ),
    _chart(
        "fb_c4", "Revenue per Customer (Monthly)", "line",
        """SELECT TO_CHAR(so.order_date, 'YYYY-MM') AS month,
               ROUND(SUM(so.total_amount)::numeric / NULLIF(COUNT(DISTINCT so.customer_id), 0), 0) AS revenue_per_customer
        FROM sales_order so
        WHERE so.status = 'closed'
        GROUP BY month ORDER BY month LIMIT 24""",
        "Month", "Revenue per Customer (₹)", "oranges",
        "Tracks spend per customer over time — a rising trend signals increasing wallet share.",
    ),
    _chart(
        "fb_c5", "Orders by Status per Month", "stackedBar",
        """SELECT TO_CHAR(so.order_date, 'YYYY-MM') AS month,
               COUNT(*) FILTER (WHERE so.status = 'closed') AS closed,
               COUNT(*) FILTER (WHERE so.status = 'open') AS open,
               COUNT(*) FILTER (WHERE so.status = 'cancelled') AS cancelled
        FROM sales_order so
        GROUP BY month ORDER BY month LIMIT 24""",
        "Month", "Order Count", "mixed",
        "Stacked view of order statuses by month shows fulfillment patterns over time.",
    ),
    _chart(
        "fb_c6", "Customer Order Value Distribution", "bar",
        """SELECT so.status AS status,
               ROUND(AVG(so.total_amount)::numeric, 0) AS avg_order_value
        FROM sales_order so
        GROUP BY so.status ORDER BY avg_order_value DESC""",
        "Order Status", "Avg Order Value (₹)", "gradient",
        "Compares average order values across different order statuses.",
    ),
]

# ── PRODUCT fallback charts ────────────────────────────────────────────────

_PRODUCT_CHARTS = [
    _chart(
        "fb_p1", "Top 10 Products by Revenue", "horizontalBar",
        """SELECT pm.product_name AS product,
               ROUND(SUM(solp.line_total)::numeric, 0) AS revenue
        FROM sales_order so
        JOIN sales_order_line sol ON so.so_id = sol.so_id
        JOIN sales_order_line_pricing solp ON sol.sol_id = solp.sol_id
        JOIN product_master pm ON sol.product_id = pm.product_id
        WHERE so.status = 'closed'
        GROUP BY pm.product_name ORDER BY revenue DESC LIMIT 10""",
        "Revenue (₹)", "Product", "greens",
        "Highlights your top 10 revenue-generating products.",
    ),
    _chart(
        "fb_p2", "Revenue by Category", "doughnut",
        """SELECT pm.category AS category,
               ROUND(SUM(solp.line_total)::numeric, 0) AS revenue
        FROM sales_order so
        JOIN sales_order_line sol ON so.so_id = sol.so_id
        JOIN sales_order_line_pricing solp ON sol.sol_id = solp.sol_id
        JOIN product_master pm ON sol.product_id = pm.product_id
        WHERE so.status = 'closed'
        GROUP BY pm.category ORDER BY revenue DESC LIMIT 8""",
        "Category", "Revenue (₹)", "blues",
        "Shows the revenue share of each product category.",
    ),
    _chart(
        "fb_p3", "Top 10 Products by Units Sold", "horizontalBar",
        """SELECT pm.product_name AS product,
               SUM(sol.quantity) AS units_sold
        FROM sales_order so
        JOIN sales_order_line sol ON so.so_id = sol.so_id
        JOIN product_master pm ON sol.product_id = pm.product_id
        WHERE so.status = 'closed'
        GROUP BY pm.product_name ORDER BY units_sold DESC LIMIT 10""",
        "Units Sold", "Product", "purples",
        "Products with the highest unit sales volume — not always the same as highest revenue.",
    ),
    _chart(
        "fb_p4", "Category Sales Trend by Month", "line",
        """SELECT TO_CHAR(so.order_date, 'YYYY-MM') AS month,
               ROUND(SUM(solp.line_total)::numeric, 0) AS revenue
        FROM sales_order so
        JOIN sales_order_line sol ON so.so_id = sol.so_id
        JOIN sales_order_line_pricing solp ON sol.sol_id = solp.sol_id
        WHERE so.status = 'closed'
        GROUP BY month ORDER BY month LIMIT 24""",
        "Month", "Revenue (₹)", "oranges",
        "Monthly revenue trend for all products combined.",
    ),
    _chart(
        "fb_p5", "Products by Category Count", "bar",
        """SELECT pm.category AS category,
               COUNT(DISTINCT pm.product_id) AS product_count
        FROM product_master pm
        GROUP BY pm.category ORDER BY product_count DESC LIMIT 10""",
        "Category", "Product Count", "mixed",
        "Shows how many products exist in each category — indicates catalogue breadth.",
    ),
    _chart(
        "fb_p6", "Revenue: Category Rank", "bar",
        """SELECT pm.category AS category,
               ROUND(SUM(solp.line_total)::numeric, 0) AS revenue,
               COUNT(DISTINCT pm.product_id) AS products
        FROM sales_order so
        JOIN sales_order_line sol ON so.so_id = sol.so_id
        JOIN sales_order_line_pricing solp ON sol.sol_id = solp.sol_id
        JOIN product_master pm ON sol.product_id = pm.product_id
        WHERE so.status = 'closed'
        GROUP BY pm.category ORDER BY revenue DESC LIMIT 10""",
        "Category", "Revenue (₹)", "gradient",
        "Ranks product categories by total revenue contribution.",
    ),
]

# ── VENDOR fallback charts ─────────────────────────────────────────────────

_VENDOR_CHARTS = [
    _chart(
        "fb_v1", "Top 10 Vendors by PO Value", "horizontalBar",
        """SELECT vm.vendor_name AS vendor,
               ROUND(SUM(po.total_amount)::numeric, 0) AS po_value
        FROM purchase_order po
        JOIN vendor_master vm ON po.vendor_id = vm.vendor_id
        GROUP BY vm.vendor_name ORDER BY po_value DESC LIMIT 10""",
        "PO Value (₹)", "Vendor", "blues",
        "Your highest-value vendors by total purchase order amount — these are your key supply partners.",
    ),
    _chart(
        "fb_v2", "Material Spend Mix", "doughnut",
        """SELECT 'Gold' AS material, ROUND(SUM(total_gold_amount)::numeric, 0) AS amount FROM purchase_order
        UNION ALL
        SELECT 'Diamond' AS material, ROUND(SUM(total_diamond_amount)::numeric, 0) AS amount FROM purchase_order
        UNION ALL
        SELECT 'Labour' AS material, ROUND(SUM(total_labour_amount)::numeric, 0) AS amount FROM purchase_order
        ORDER BY amount DESC""",
        "Material", "Spend (₹)", "mixed",
        "Breaks down total procurement spend into raw materials and labour costs.",
    ),
    _chart(
        "fb_v3", "Monthly Spend Trend", "area",
        """SELECT TO_CHAR(po.created_at, 'YYYY-MM') AS month,
               ROUND(SUM(po.total_amount)::numeric, 0) AS po_value
        FROM purchase_order po
        GROUP BY month ORDER BY month LIMIT 24""",
        "Month", "Spend (₹)", "greens",
        "Tracks month-over-month procurement spend to identify purchasing cycles and budget trends.",
    ),
    _chart(
        "fb_v4", "Open vs Closed PO Value", "bar",
        """SELECT po.status AS status,
               ROUND(SUM(po.total_amount)::numeric, 0) AS total_value
        FROM purchase_order po
        WHERE po.status IN ('open', 'closed')
        GROUP BY po.status ORDER BY total_value DESC""",
        "Status", "PO Value (₹)", "purples",
        "Compares value of completed deliveries (closed) vs pending commitments (open).",
    ),
    _chart(
        "fb_v5", "Monthly Material Weight Trend", "line",
        """SELECT TO_CHAR(po.created_at, 'YYYY-MM') AS month,
               ROUND(SUM(po.total_gold_wt)::numeric, 1) AS gold_wt_gm
        FROM purchase_order po
        WHERE po.total_gold_wt IS NOT NULL
        GROUP BY month ORDER BY month LIMIT 24""",
        "Month", "Gold Weight (gm)", "oranges",
        "Tracks the volume of gold raw material procured each month.",
    ),
    _chart(
        "fb_v6", "PO Status Breakdown", "bar",
        """SELECT po.status AS status, COUNT(po.po_id) AS po_count
        FROM purchase_order po
        GROUP BY po.status ORDER BY po_count DESC""",
        "Status", "PO Count", "gradient",
        "Volume of purchase orders by status — measures operational procurement throughput.",
    ),
]


# ── AOV-specific fallback charts ──────────────────────────────────────────

_AOV_CHARTS = [
    _chart(
        "fb_aov1", "AOV Trend by Month", "area",
        """SELECT TO_CHAR(so.order_date, 'YYYY-MM') AS month,
               ROUND(AVG(so.total_amount)::numeric, 0) AS avg_order_value
        FROM sales_order so
        WHERE so.status = 'closed'
        GROUP BY month ORDER BY month LIMIT 24""",
        "Month", "Avg Order Value (₹)", "greens",
        "Monthly AOV trend — rising values signal larger transactions and better upselling.",
    ),
    _chart(
        "fb_aov2", "AOV by Customer (Top 10)", "horizontalBar",
        """SELECT cm.customer_name AS customer,
               ROUND(AVG(so.total_amount)::numeric, 0) AS avg_order_value
        FROM sales_order so
        JOIN customer_master cm ON so.customer_id = cm.customer_id
        WHERE so.status = 'closed'
        GROUP BY cm.customer_name
        ORDER BY avg_order_value DESC LIMIT 10""",
        "Avg Order Value (₹)", "Customer", "blues",
        "Top 10 customers by personal AOV — high-AOV customers are ideal upsell targets.",
    ),
    _chart(
        "fb_aov3", "AOV by Product Category", "bar",
        """WITH order_cats AS (
               SELECT DISTINCT so.so_id, so.total_amount, pm.category
               FROM sales_order so
               JOIN sales_order_line sol ON so.so_id = sol.so_id
               JOIN product_master pm ON sol.product_id = pm.product_id
               WHERE so.status = 'closed'
           )
        SELECT category,
               ROUND(AVG(total_amount)::numeric, 0) AS avg_order_value
        FROM order_cats
        GROUP BY category
        ORDER BY avg_order_value DESC""",
        "Category", "Avg Order Value (₹)", "purples",
        "Categories commanding higher AOV are where premium SKU investment pays off most.",
    ),
    _chart(
        "fb_aov4", "Monthly Order Count vs AOV", "line",
        """SELECT TO_CHAR(so.order_date, 'YYYY-MM') AS month,
               COUNT(so.so_id) AS order_count,
               ROUND(AVG(so.total_amount)::numeric, 0) AS avg_order_value
        FROM sales_order so
        WHERE so.status = 'closed'
        GROUP BY month ORDER BY month LIMIT 24""",
        "Month", "Value", "mixed",
        "When order count rises but AOV falls, growth is driven by volume not value — watch for basket dilution.",
    ),
    _chart(
        "fb_aov5", "Order Value Distribution (Buckets)", "bar",
        """SELECT CASE
               WHEN total_amount < 100000  THEN 'Under 1L'
               WHEN total_amount < 500000  THEN '1L – 5L'
               WHEN total_amount < 1000000 THEN '5L – 10L'
               WHEN total_amount < 5000000 THEN '10L – 50L'
               ELSE 'Above 50L'
           END AS order_bucket,
           COUNT(*) AS order_count
        FROM sales_order
        WHERE status = 'closed'
        GROUP BY order_bucket
        ORDER BY MIN(total_amount)""",
        "Order Value Range", "Order Count", "oranges",
        "Distribution of orders by value bucket — identifies where most orders cluster.",
    ),
    _chart(
        "fb_aov6", "High-AOV vs Low-AOV Customer Segments", "doughnut",
        """WITH cust_aov AS (
               SELECT customer_id, AVG(total_amount) AS customer_aov
               FROM sales_order WHERE status = 'closed' GROUP BY customer_id
           ),
           avg_val AS (
               SELECT AVG(total_amount) AS overall_avg
               FROM sales_order WHERE status = 'closed'
           )
        SELECT CASE WHEN ca.customer_aov >= av.overall_avg
                    THEN 'Above Average AOV'
                    ELSE 'Below Average AOV' END AS segment,
               COUNT(*) AS customers
        FROM cust_aov ca, avg_val av
        GROUP BY segment""",
        "Segment", "Customers", "gradient",
        "Proportion of customers above vs below average AOV — large below-average pool signals upsell headroom.",
    ),
]

# ── Fulfilment-specific fallback charts ───────────────────────────────────

_FULFILMENT_CHARTS = [
    _chart(
        "fb_f1", "Order Status Breakdown", "bar",
        """SELECT so.status AS status, COUNT(so.so_id) AS order_count
        FROM sales_order so
        GROUP BY so.status ORDER BY order_count DESC""",
        "Status", "Order Count", "blues",
        "Overall order status distribution — closed ratio is your fulfilment rate.",
    ),
    _chart(
        "fb_f2", "Monthly Fulfilment Rate", "line",
        """SELECT TO_CHAR(so.order_date, 'YYYY-MM') AS month,
               ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'closed')
               / NULLIF(COUNT(*), 0), 1) AS fulfilment_rate
        FROM sales_order so
        GROUP BY month ORDER BY month LIMIT 24""",
        "Month", "Fulfilment Rate (%)", "greens",
        "Monthly fulfilment rate trend — dips indicate supply or operational issues.",
    ),
    _chart(
        "fb_f3", "Monthly Cancellation Rate", "area",
        """SELECT TO_CHAR(so.order_date, 'YYYY-MM') AS month,
               ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'cancelled')
               / NULLIF(COUNT(*), 0), 1) AS cancellation_rate
        FROM sales_order so
        GROUP BY month ORDER BY month LIMIT 24""",
        "Month", "Cancellation Rate (%)", "oranges",
        "Rising cancellation rates flag customer satisfaction or demand-supply mismatches.",
    ),
    _chart(
        "fb_f4", "Order Volume by Status by Month", "stackedBar",
        """SELECT TO_CHAR(so.order_date, 'YYYY-MM') AS month,
               COUNT(*) FILTER (WHERE status = 'closed') AS closed,
               COUNT(*) FILTER (WHERE status = 'open') AS open,
               COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelled
        FROM sales_order so
        GROUP BY month ORDER BY month LIMIT 24""",
        "Month", "Orders", "mixed",
        "Stack shows closed vs open vs cancelled order volumes month-by-month.",
    ),
    _chart(
        "fb_f5", "Top 10 Customers by Cancellations", "horizontalBar",
        """SELECT cm.customer_name AS customer, COUNT(so.so_id) AS cancellations
        FROM sales_order so
        JOIN customer_master cm ON so.customer_id = cm.customer_id
        WHERE so.status = 'cancelled'
        GROUP BY cm.customer_name ORDER BY cancellations DESC LIMIT 10""",
        "Cancellations", "Customer", "purples",
        "Customers with the most cancellations — targeted outreach can recover this revenue.",
    ),
    _chart(
        "fb_f6", "Open Order Pipeline by Month", "bar",
        """SELECT TO_CHAR(so.order_date, 'YYYY-MM') AS month,
               COUNT(so.so_id) AS open_orders
        FROM sales_order so
        WHERE so.status = 'open'
        GROUP BY month ORDER BY month LIMIT 24""",
        "Month", "Open Orders", "gradient",
        "Volume of open orders by origination month — older open orders risk cancellation.",
    ),
]

# ── DEFAULT fallback charts (sales-based, always works) ───────────────────

_DEFAULT_CHARTS = _SALES_CHARTS.copy()

# ── Public API ─────────────────────────────────────────────────────────────

_TOPIC_CHART_MAP: dict[str, list[dict[str, Any]]] = {
    # Fine-grained sub-topics
    "aov":         _AOV_CHARTS,
    "fulfilment":  _FULFILMENT_CHARTS,
    "units":       _PRODUCT_CHARTS,   # best proxy — units come from product lines
    "pricing":     _PRODUCT_CHARTS,   # pricing lives in sales_order_line_pricing
    "gold":        _VENDOR_CHARTS,    # gold data lives in purchase_order
    "diamond":     _VENDOR_CHARTS,    # diamond data lives in purchase_order
    # Broad topics
    "sales":       _SALES_CHARTS,
    "customer":    _CUSTOMER_CHARTS,
    "product":     _PRODUCT_CHARTS,
    "vendor":      _VENDOR_CHARTS,
    "procurement": _VENDOR_CHARTS,   # same SQL — both use purchase_order
    "inventory":   _PRODUCT_CHARTS,  # best proxy — uses product_master
    "default":     _DEFAULT_CHARTS,
}


def get_fallback_charts(topic: str) -> list[dict[str, Any]]:
    """Return the 6 fallback chart templates for the given topic.

    Each template has id, title, type, sql, x_label, y_label,
    color_scheme, chart_insight, and explanation fields.
    The 'data' field is NOT populated here — the caller must execute the SQL.
    """
    return _TOPIC_CHART_MAP.get(topic, _DEFAULT_CHARTS)



# ══════════════════════════════════════════════════════════════════════════════
# FALLBACK KPI TEMPLATES — Pre-verified SQL per topic
# These are injected when the LLM produces < 6 valid KPIs.
# All SQL has been manually verified against the production schema.
# ══════════════════════════════════════════════════════════════════════════════

def _kpi(id_, label, sql, fmt, icon, color, what, how, insight):
    """Build a KPI dict matching the report JSON schema."""
    return {
        "id": id_,
        "label": label,
        "sql": sql,
        "format": fmt,
        "icon": icon,
        "color": color,
        "explanation": {"what": what, "how": how, "insight": insight},
    }


# ── SALES KPIs ────────────────────────────────────────────────────────────

_SALES_KPIS = [
    _kpi(
        "fb_kpi_s1", "Total Revenue",
        "SELECT ROUND(SUM(so.total_amount)::numeric, 2) AS total_revenue "
        "FROM sales_order so WHERE so.status = 'closed'",
        "currency", "revenue", "green",
        "Total revenue from all closed sales orders.",
        "Sum of total_amount on sales_order where status = 'closed'.",
        "Core top-line metric reflecting actual realized sales value.",
    ),
    _kpi(
        "fb_kpi_s2", "Total Orders",
        "SELECT COUNT(so.so_id) AS total_orders "
        "FROM sales_order so WHERE so.status = 'closed'",
        "number", "orders", "blue",
        "Count of successfully closed (fulfilled) sales orders.",
        "Count of rows in sales_order with status = 'closed'.",
        "A high order count with stable revenue indicates consistent deal sizes.",
    ),
    _kpi(
        "fb_kpi_s3", "Average Order Value (AOV)",
        "SELECT ROUND(AVG(so.total_amount)::numeric, 2) AS avg_order_value "
        "FROM sales_order so WHERE so.status = 'closed'",
        "currency", "average", "purple",
        "Mean value of a single closed sales order.",
        "AVG(total_amount) from closed sales_order rows.",
        "Rising AOV signals successful upselling or higher-value product mix shifts.",
    ),
    _kpi(
        "fb_kpi_s4", "Total Units Sold",
        "SELECT SUM(sol.quantity) AS total_units "
        "FROM sales_order so "
        "JOIN sales_order_line sol ON so.so_id = sol.so_id "
        "WHERE so.status = 'closed'",
        "number", "products", "orange",
        "Total number of product units shipped across all closed orders.",
        "SUM(quantity) from sales_order_line joined to closed sales_order.",
        "High unit volume with stable revenue could indicate a move toward lower-price items.",
    ),
    _kpi(
        "fb_kpi_s5", "Order Fulfilment Rate",
        "SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'closed') "
        "/ NULLIF(COUNT(*), 0), 1) AS fulfilment_rate "
        "FROM sales_order",
        "percent", "growth", "teal",
        "Percentage of all orders that have been successfully closed/fulfilled.",
        "Closed orders ÷ total orders × 100.",
        "Rates above 85% indicate a healthy fulfilment pipeline.",
    ),
    _kpi(
        "fb_kpi_s6", "Active Customers",
        "SELECT COUNT(DISTINCT so.customer_id) AS active_customers "
        "FROM sales_order so WHERE so.status = 'closed'",
        "number", "customers", "blue",
        "Number of unique customers who placed at least one closed order.",
        "COUNT DISTINCT customer_id from closed sales_order rows.",
        "A growing active customer base indicates healthy demand and acquisition.",
    ),
    _kpi(
        "fb_kpi_s7", "Open Orders",
        "SELECT COUNT(so.so_id) AS open_orders "
        "FROM sales_order so WHERE so.status = 'open'",
        "number", "orders", "orange",
        "Number of orders currently in open / pending state.",
        "Count of sales_order rows where status = 'open'.",
        "A large open order count signals healthy pipeline but possible fulfilment backlog.",
    ),
    _kpi(
        "fb_kpi_s8", "Cancellation Rate",
        "SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'cancelled') "
        "/ NULLIF(COUNT(*), 0), 1) AS cancellation_rate "
        "FROM sales_order",
        "percent", "chart", "red",
        "Percentage of all orders that were cancelled.",
        "Cancelled orders ÷ total orders × 100.",
        "Rising cancellation rates flag customer satisfaction or inventory fulfilment issues.",
    ),
]


# ── CUSTOMER KPIs ─────────────────────────────────────────────────────────

_CUSTOMER_KPIS = [
    _kpi(
        "fb_kpi_c1", "Total Customers",
        "SELECT COUNT(*) AS total_customers FROM customer_master",
        "number", "customers", "blue",
        "Total number of registered customers in the database.",
        "COUNT(*) from customer_master table.",
        "Reflects the breadth of the customer base.",
    ),
    _kpi(
        "fb_kpi_c2", "Active Customers",
        "SELECT COUNT(DISTINCT so.customer_id) AS active_customers "
        "FROM sales_order so WHERE so.status = 'closed'",
        "number", "customers", "green",
        "Customers who have at least one closed order.",
        "COUNT DISTINCT customer_id from closed sales_order.",
        "Compares engaged buyers to total registered customers.",
    ),
    _kpi(
        "fb_kpi_c3", "Avg Revenue per Customer",
        "SELECT ROUND(SUM(so.total_amount)::numeric "
        "/ NULLIF(COUNT(DISTINCT so.customer_id), 0), 2) AS avg_revenue_per_customer "
        "FROM sales_order so WHERE so.status = 'closed'",
        "currency", "average", "purple",
        "Mean lifetime revenue contributed by each active customer.",
        "Total closed revenue ÷ distinct active customers.",
        "Higher values indicate strong wallet share per account.",
    ),
    _kpi(
        "fb_kpi_c4", "Avg Orders per Customer",
        "SELECT ROUND(COUNT(so.so_id)::numeric "
        "/ NULLIF(COUNT(DISTINCT so.customer_id), 0), 2) AS avg_orders_per_customer "
        "FROM sales_order so WHERE so.status = 'closed'",
        "number", "orders", "orange",
        "Average number of closed orders per active customer.",
        "Total closed orders ÷ distinct customers.",
        "Higher repeat-order rates indicate strong loyalty and retention.",
    ),
    _kpi(
        "fb_kpi_c5", "Total Closed Revenue",
        "SELECT ROUND(SUM(so.total_amount)::numeric, 2) AS total_revenue "
        "FROM sales_order so WHERE so.status = 'closed'",
        "currency", "revenue", "teal",
        "Total revenue generated from all closed orders.",
        "SUM(total_amount) from closed sales_order rows.",
        "Primary top-line metric for customer revenue performance.",
    ),
    _kpi(
        "fb_kpi_c6", "Total Orders",
        "SELECT COUNT(so.so_id) AS total_orders FROM sales_order so",
        "number", "orders", "blue",
        "Total number of orders across all statuses.",
        "COUNT(*) from sales_order.",
        "Reflects overall order volume regardless of fulfilment status.",
    ),
    _kpi(
        "fb_kpi_c7", "Average Order Value",
        "SELECT ROUND(AVG(so.total_amount)::numeric, 2) AS avg_order_value "
        "FROM sales_order so WHERE so.status = 'closed'",
        "currency", "average", "purple",
        "Mean value per closed order.",
        "AVG(total_amount) from closed sales_order.",
        "Tracks basket size — higher AOV points to premium buying behaviour.",
    ),
    _kpi(
        "fb_kpi_c8", "Order Fulfilment Rate",
        "SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'closed') "
        "/ NULLIF(COUNT(*), 0), 1) AS fulfilment_rate FROM sales_order",
        "percent", "growth", "green",
        "Percentage of orders that have been fulfilled (closed).",
        "Closed ÷ total orders × 100.",
        "A strong fulfilment rate builds customer trust and reduces cancellations.",
    ),
]


# ── PRODUCT KPIs ─────────────────────────────────────────────────────────

_PRODUCT_KPIS = [
    _kpi(
        "fb_kpi_p1", "Total Products",
        "SELECT COUNT(*) AS total_products FROM product_master",
        "number", "products", "blue",
        "Total number of products in the product catalogue.",
        "COUNT(*) from product_master.",
        "Reflects the breadth and depth of the product catalogue.",
    ),
    _kpi(
        "fb_kpi_p2", "Active Products Sold",
        "SELECT COUNT(DISTINCT sol.product_id) AS active_products_sold "
        "FROM sales_order so "
        "JOIN sales_order_line sol ON so.so_id = sol.so_id "
        "WHERE so.status = 'closed'",
        "number", "products", "green",
        "Number of unique products that appear in at least one closed order.",
        "COUNT DISTINCT product_id from closed-order lines.",
        "Low ratio of active/total products indicates dead stock risk.",
    ),
    _kpi(
        "fb_kpi_p3", "Total Units Sold",
        "SELECT SUM(sol.quantity) AS total_units "
        "FROM sales_order so "
        "JOIN sales_order_line sol ON so.so_id = sol.so_id "
        "WHERE so.status = 'closed'",
        "number", "chart", "purple",
        "Total product units shipped in closed orders.",
        "SUM(quantity) from sales_order_line joined to closed sales_order.",
        "High unit volume with diversified product mix reduces single-SKU dependency.",
    ),
    _kpi(
        "fb_kpi_p4", "Total Product Categories",
        "SELECT COUNT(DISTINCT pm.category) AS total_categories "
        "FROM product_master pm",
        "number", "chart", "orange",
        "Number of distinct product categories in the catalogue.",
        "COUNT DISTINCT category from product_master.",
        "More categories means broader market coverage.",
    ),
    _kpi(
        "fb_kpi_p5", "Total Revenue from Products",
        "SELECT ROUND(SUM(solp.line_total)::numeric, 2) AS product_revenue "
        "FROM sales_order so "
        "JOIN sales_order_line sol ON so.so_id = sol.so_id "
        "JOIN sales_order_line_pricing solp ON sol.sol_id = solp.sol_id "
        "WHERE so.status = 'closed'",
        "currency", "revenue", "teal",
        "Total revenue calculated from line-level pricing across all closed orders.",
        "SUM(line_total) from sales_order_line_pricing joined via closed orders.",
        "Line-level revenue is the most granular and accurate revenue metric.",
    ),
    _kpi(
        "fb_kpi_p6", "Avg Selling Price per Unit",
        "SELECT ROUND(AVG(solp.selling_price_per_unit)::numeric, 2) AS avg_selling_price "
        "FROM sales_order so "
        "JOIN sales_order_line sol ON so.so_id = sol.so_id "
        "JOIN sales_order_line_pricing solp ON sol.sol_id = solp.sol_id "
        "WHERE so.status = 'closed'",
        "currency", "average", "purple",
        "Average selling price per product unit across all closed order lines.",
        "AVG(selling_price_per_unit) from sales_order_line_pricing on closed orders.",
        "Tracks average price realization — a declining trend may signal discounting pressure.",
    ),
    _kpi(
        "fb_kpi_p7", "Avg Items per Order",
        "SELECT ROUND(AVG(cnt)::numeric, 2) AS avg_items_per_order "
        "FROM (SELECT sol.so_id, COUNT(*) AS cnt "
        "FROM sales_order so "
        "JOIN sales_order_line sol ON so.so_id = sol.so_id "
        "WHERE so.status = 'closed' "
        "GROUP BY sol.so_id) t",
        "number", "orders", "blue",
        "Average number of product lines per closed order.",
        "AVG of per-order line counts from closed sales_order_line.",
        "Higher line items per order indicate customers are buying across categories.",
    ),
    _kpi(
        "fb_kpi_p8", "Order Fulfilment Rate",
        "SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'closed') "
        "/ NULLIF(COUNT(*), 0), 1) AS fulfilment_rate FROM sales_order",
        "percent", "growth", "green",
        "Percentage of orders successfully fulfilled.",
        "Closed orders ÷ total orders × 100.",
        "Strong fulfilment performance drives product trust and repeat purchases.",
    ),
]


# ── VENDOR / PROCUREMENT KPIs ─────────────────────────────────────────────

_VENDOR_KPIS = [
    _kpi(
        "fb_kpi_v1", "Total Vendors",
        "SELECT COUNT(*) AS total_vendors FROM vendor_master",
        "number", "customers", "blue",
        "Total number of registered vendors in the system.",
        "COUNT(*) from vendor_master.",
        "More vendors increase supply redundancy and negotiation leverage.",
    ),
    _kpi(
        "fb_kpi_v2", "Total Purchase Orders",
        "SELECT COUNT(*) AS total_pos FROM purchase_order",
        "number", "orders", "purple",
        "Total number of purchase orders placed.",
        "COUNT(*) from purchase_order.",
        "Reflects procurement activity volume across all vendors.",
    ),
    _kpi(
        "fb_kpi_v3", "Total PO Value",
        "SELECT ROUND(SUM(po.total_amount)::numeric, 2) AS total_po_value "
        "FROM purchase_order po",
        "currency", "revenue", "green",
        "Total value of all purchase orders placed.",
        "SUM(total_amount) from purchase_order.",
        "Reflects total procurement spend — compare to revenue for margin insight.",
    ),
    _kpi(
        "fb_kpi_v4", "Open PO Value",
        "SELECT ROUND(SUM(po.total_amount)::numeric, 2) AS open_po_value "
        "FROM purchase_order po WHERE po.status = 'open'",
        "currency", "chart", "orange",
        "Total value of purchase orders currently in open status.",
        "SUM(total_amount) from purchase_order where status = 'open'.",
        "High open PO value indicates pending supply commitments not yet fulfilled.",
    ),
    _kpi(
        "fb_kpi_v5", "Average PO Value",
        "SELECT ROUND(AVG(po.total_amount)::numeric, 2) AS avg_po_value "
        "FROM purchase_order po",
        "currency", "average", "teal",
        "Mean value per purchase order.",
        "AVG(total_amount) from purchase_order.",
        "Tracks typical order size — useful for benchmarking vendor relationships.",
    ),
    _kpi(
        "fb_kpi_v6", "Total Gold Weight (kg)",
        "SELECT ROUND(SUM(po.total_gold_wt)::numeric / 1000, 3) AS total_gold_kg "
        "FROM purchase_order po WHERE po.total_gold_wt IS NOT NULL",
        "number", "chart", "orange",
        "Total gold weight across all purchase orders (in kilograms).",
        "SUM(total_gold_wt) from purchase_order ÷ 1000 to convert to kg.",
        "Key raw material procurement metric for jewellery businesses.",
    ),
    _kpi(
        "fb_kpi_v7", "Total Diamond Weight (cts)",
        "SELECT ROUND(SUM(po.total_diamond_cts)::numeric, 3) AS total_diamond_cts "
        "FROM purchase_order po WHERE po.total_diamond_cts IS NOT NULL",
        "number", "chart", "purple",
        "Total diamond carats procured across all purchase orders.",
        "SUM(total_diamond_cts) from purchase_order.",
        "Tracks key gemstone procurement volume for planning and cost control.",
    ),
    _kpi(
        "fb_kpi_v8", "Closed PO Count",
        "SELECT COUNT(*) AS closed_pos FROM purchase_order WHERE status = 'closed'",
        "number", "orders", "green",
        "Number of purchase orders that have been fully closed/received.",
        "COUNT(*) from purchase_order where status = 'closed'.",
        "High closed PO count indicates effective vendor delivery performance.",
    ),
]


# ── DEFAULT KPIs (sales-based, always works) ──────────────────────────────

_DEFAULT_KPIS = _SALES_KPIS.copy()

_TOPIC_KPI_MAP: dict[str, list[dict[str, Any]]] = {
    "sales":       _SALES_KPIS,
    "customer":    _CUSTOMER_KPIS,
    "product":     _PRODUCT_KPIS,
    "vendor":      _VENDOR_KPIS,
    "procurement": _VENDOR_KPIS,
    "inventory":   _PRODUCT_KPIS,
    "default":     _DEFAULT_KPIS,
}


def get_fallback_kpis(topic: str) -> list[dict[str, Any]]:
    """Return up to 8 pre-verified KPI templates for the given topic.

    Each template has id, label, sql, format, icon, color, and explanation
    fields.  The 'value' field is NOT populated here — the caller must
    execute the SQL via _execute_kpi_sql().
    """
    return _TOPIC_KPI_MAP.get(topic, _DEFAULT_KPIS)
