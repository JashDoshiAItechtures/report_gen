"""Pre-verified fallback chart SQL templates per report topic.

When the LLM-generated chart SQL fails validation or returns empty data,
these guaranteed-to-work templates are used to fill the chart slots up to 6.

All SQL queries here have been manually verified against the production schema:
  sales_order, sales_order_line, sales_order_line_pricing, product_master,
  customer_master, vendor_master, purchase_order, po_line_items
"""

import re
from typing import Any

# ── Topic keyword mapping ──────────────────────────────────────────────────

_TOPIC_KEYWORDS = {
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
    """Classify a report question into one of the known topics.

    Returns: 'sales' | 'customer' | 'product' | 'vendor' |
             'procurement' | 'inventory' | 'default'
    """
    q = question.lower().strip()

    # Priority order matters — check most specific first
    priority = ["procurement", "vendor", "inventory", "customer", "product", "sales"]
    for topic in priority:
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
               so.status AS status,
               COUNT(so.so_id) AS order_count
        FROM sales_order so
        GROUP BY month, so.status ORDER BY month LIMIT 48""",
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
        "Your highest-value vendors by total purchase order amount.",
    ),
    _chart(
        "fb_v2", "PO Status Distribution", "doughnut",
        """SELECT po.status AS status, COUNT(po.po_id) AS po_count
        FROM purchase_order po
        GROUP BY po.status ORDER BY po_count DESC""",
        "Status", "PO Count", "oranges",
        "Shows open vs closed vs cancelled POs to assess procurement pipeline health.",
    ),
    _chart(
        "fb_v3", "Monthly PO Value Trend", "area",
        """SELECT TO_CHAR(po.po_date, 'YYYY-MM') AS month,
               ROUND(SUM(po.total_amount)::numeric, 0) AS po_value
        FROM purchase_order po
        GROUP BY month ORDER BY month LIMIT 24""",
        "Month", "PO Value (₹)", "greens",
        "Tracks procurement spend over time to identify purchasing seasonality.",
    ),
    _chart(
        "fb_v4", "Top 10 Vendors by Order Count", "bar",
        """SELECT vm.vendor_name AS vendor,
               COUNT(po.po_id) AS po_count
        FROM purchase_order po
        JOIN vendor_master vm ON po.vendor_id = vm.vendor_id
        GROUP BY vm.vendor_name ORDER BY po_count DESC LIMIT 10""",
        "Vendor", "PO Count", "purples",
        "Most frequently used vendors — high PO count signals supply dependency.",
    ),
    _chart(
        "fb_v5", "Average PO Value by Month", "line",
        """SELECT TO_CHAR(po.po_date, 'YYYY-MM') AS month,
               ROUND(AVG(po.total_amount)::numeric, 0) AS avg_po_value
        FROM purchase_order po
        GROUP BY month ORDER BY month LIMIT 24""",
        "Month", "Avg PO Value (₹)", "mixed",
        "Average purchase order value trend — rising values indicate larger bulk orders.",
    ),
    _chart(
        "fb_v6", "PO Value by Status", "bar",
        """SELECT po.status AS status,
               ROUND(SUM(po.total_amount)::numeric, 0) AS total_value,
               COUNT(po.po_id) AS po_count
        FROM purchase_order po
        GROUP BY po.status ORDER BY total_value DESC""",
        "Status", "Total PO Value (₹)", "gradient",
        "Compares total procurement value across different PO statuses.",
    ),
]

# ── DEFAULT fallback charts (sales-based, always works) ───────────────────

_DEFAULT_CHARTS = _SALES_CHARTS.copy()

# ── Public API ─────────────────────────────────────────────────────────────

_TOPIC_CHART_MAP: dict[str, list[dict[str, Any]]] = {
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
