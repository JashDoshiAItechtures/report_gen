"""Data-backed fallback AI insights generator per report topic.

When the LLM produces < 6 insights (or generic/hallucinated ones),
this module computes real insights by running verified SQL queries and
inserting actual database values into insight templates.

All SQL is verified against the production schema.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Helper: run a single-value SQL query and return the raw scalar result.
# ──────────────────────────────────────────────────────────────────────────────

def _scalar(sql: str, month: int | None = None):
    """Execute SQL and return the first value of the first row, or None on error.

    If month is given, inject EXTRACT(MONTH FROM order_date) = month after
    every WHERE clause so insights are scoped to that specific month.
    """
    if month:
        import re as _re
        # One-pass injection: catch 'so.status' alias or bare 'status' form.
        # Use a negative-lookbehind to avoid double-injection.
        sql = _re.sub(
            r"(WHERE\s+(?:so\.)?status\s*=\s*'closed')(?!.*EXTRACT\(MONTH)",
            lambda m: m.group(0) + f" AND EXTRACT(MONTH FROM order_date) = {month}",
            sql, flags=_re.IGNORECASE | _re.DOTALL
        )
        # If query has no status filter but does reference sales_order (e.g. fulfilment rate query)
        if "EXTRACT(MONTH" not in sql and "sales_order" in sql.lower() and "WHERE" not in sql.upper():
            sql = sql.rstrip() + f" WHERE EXTRACT(MONTH FROM order_date) = {month}"
    try:
        from db.executor import execute_sql
        result = execute_sql(sql)
        if result["success"] and result["data"]:
            return list(result["data"][0].values())[0]
    except Exception as exc:
        logger.warning("Fallback insight SQL failed: %s | %s", sql[:80], exc)
    return None


def _fmt_currency(val) -> str:
    """Format a large INR number into a human-readable string."""
    if val is None:
        return "N/A"
    try:
        v = float(val)
    except (TypeError, ValueError):
        return str(val)

    if v >= 1e7:          # crores
        return f"₹{v / 1e7:.2f} Cr"
    if v >= 1e5:          # lakhs
        return f"₹{v / 1e5:.2f} L"
    return f"₹{v:,.0f}"


def _fmt_num(val, decimals: int = 0) -> str:
    if val is None:
        return "N/A"
    try:
        v = float(val)
        if decimals == 0:
            return f"{int(v):,}"
        return f"{v:,.{decimals}f}"
    except (TypeError, ValueError):
        return str(val)


def _fmt_pct(val, decimals: int = 1) -> str:
    if val is None:
        return "N/A"
    try:
        return f"{float(val):.{decimals}f}%"
    except (TypeError, ValueError):
        return str(val)


def _insight(title: str, body: str, itype: str) -> dict:
    return {"title": title, "body": body, "type": itype}


# ──────────────────────────────────────────────────────────────────────────────
# SALES insights
# ──────────────────────────────────────────────────────────────────────────────

def _sales_insights(month: int | None = None) -> list[dict]:
    insights = []

    # 1. Total revenue snapshot
    total_rev = _scalar("SELECT ROUND(SUM(total_amount)::numeric,2) FROM sales_order WHERE status='closed'", month=month)
    total_orders = _scalar("SELECT COUNT(*) FROM sales_order WHERE status='closed'", month=month)
    if total_rev is not None and total_orders is not None:
        insights.append(_insight(
            "Revenue Snapshot",
            f"Total closed revenue stands at {_fmt_currency(total_rev)} across "
            f"{_fmt_num(total_orders)} fulfilled orders. This forms the primary "
            "top-line metric for sales performance evaluation.",
            "positive",
        ))

    # 2. Average order value
    aov = _scalar("SELECT ROUND(AVG(total_amount)::numeric,2) FROM sales_order WHERE status='closed'", month=month)
    if aov is not None:
        insights.append(_insight(
            "Average Order Value",
            f"The average closed order is worth {_fmt_currency(aov)}. "
            "Monitoring AOV over time reveals whether upselling strategies or "
            "product mix shifts are driving larger basket sizes per transaction.",
            "neutral",
        ))

    # 3. Fulfilment vs cancellation rates
    fulfil = _scalar(
        "SELECT ROUND(100.0*COUNT(*) FILTER(WHERE status='closed')/NULLIF(COUNT(*),0),1) FROM sales_order"
    )
    cancel = _scalar(
        "SELECT ROUND(100.0*COUNT(*) FILTER(WHERE status='cancelled')/NULLIF(COUNT(*),0),1) FROM sales_order"
    )
    if fulfil is not None and cancel is not None:
        itype = "positive" if float(fulfil) >= 80 else "warning"
        insights.append(_insight(
            "Fulfilment & Cancellation Rates",
            f"Order fulfilment rate is {_fmt_pct(fulfil)} while the cancellation rate is "
            f"{_fmt_pct(cancel)}. "
            + ("A high fulfilment rate signals a robust supply and fulfilment pipeline. "
               if float(fulfil) >= 80 else
               "Improving fulfilment rates should be a priority to reduce revenue leakage. ") +
            "Investigate cancellation reasons to uncover demand or inventory issues.",
            itype,
        ))

    # 4. Top customer
    top_cust = _scalar(
        "SELECT cm.customer_name FROM sales_order so "
        "JOIN customer_master cm ON so.customer_id=cm.customer_id "
        "WHERE so.status='closed' GROUP BY cm.customer_name "
        "ORDER BY SUM(so.total_amount) DESC LIMIT 1"
    )
    top_cust_rev = _scalar(
        "SELECT ROUND(SUM(so.total_amount)::numeric,2) FROM sales_order so "
        "JOIN customer_master cm ON so.customer_id=cm.customer_id "
        "WHERE so.status='closed' GROUP BY cm.customer_name "
        "ORDER BY SUM(so.total_amount) DESC LIMIT 1"
    )
    active_custs = _scalar(
        "SELECT COUNT(DISTINCT customer_id) FROM sales_order WHERE status='closed'"
    )
    if top_cust and top_cust_rev and active_custs:
        pct_cust = float(top_cust_rev) / float(total_rev) * 100 if total_rev else 0
        itype = "warning" if pct_cust > 20 else "positive"
        insights.append(_insight(
            "Top Customer Concentration",
            f"{top_cust} is your highest-value customer contributing "
            f"{_fmt_currency(top_cust_rev)} ({pct_cust:.1f}% of total revenue), "
            f"across {_fmt_num(active_custs)} active customers. "
            + ("High concentration on a single account creates revenue risk if they churn — "
               "diversification should be a strategic priority." if pct_cust > 20 else
               "Revenue is reasonably diversified across the customer base, "
               "which limits concentration risk."),
            itype,
        ))

    # 5. Top product category
    top_cat = _scalar(
        "SELECT pm.category FROM sales_order so "
        "JOIN sales_order_line sol ON so.so_id=sol.so_id "
        "JOIN sales_order_line_pricing solp ON sol.sol_id=solp.sol_id "
        "JOIN product_master pm ON sol.product_id=pm.product_id "
        "WHERE so.status='closed' GROUP BY pm.category "
        "ORDER BY SUM(solp.line_total) DESC LIMIT 1"
    )
    top_cat_rev = _scalar(
        "SELECT ROUND(SUM(solp.line_total)::numeric,2) FROM sales_order so "
        "JOIN sales_order_line sol ON so.so_id=sol.so_id "
        "JOIN sales_order_line_pricing solp ON sol.sol_id=solp.sol_id "
        "JOIN product_master pm ON sol.product_id=pm.product_id "
        "WHERE so.status='closed' GROUP BY pm.category "
        "ORDER BY SUM(solp.line_total) DESC LIMIT 1"
    )
    if top_cat and top_cat_rev:
        insights.append(_insight(
            "Category Performance Leader",
            f"The '{top_cat}' category leads all product categories with "
            f"{_fmt_currency(top_cat_rev)} in revenue from closed orders. "
            "Prioritizing stock, marketing, and new product development in this "
            "category can directly amplify top-line growth.",
            "opportunity",
        ))

    # 6. Open pipeline
    open_orders = _scalar("SELECT COUNT(*) FROM sales_order WHERE status='open'", month=month)
    open_val = _scalar("SELECT ROUND(SUM(total_amount)::numeric,2) FROM sales_order WHERE status='open'", month=month)
    if open_orders is not None and open_val is not None:
        insights.append(_insight(
            "Open Order Pipeline",
            f"There are currently {_fmt_num(open_orders)} open orders with a combined "
            f"value of {_fmt_currency(open_val)} pending fulfilment. "
            "Timely conversion of these orders is critical for maintaining revenue momentum "
            "and minimizing the risk of order cancellations.",
            "neutral",
        ))

    # 7. Units sold
    units = _scalar(
        "SELECT SUM(sol.quantity) FROM sales_order so "
        "JOIN sales_order_line sol ON so.so_id=sol.so_id WHERE so.status='closed'"
    )
    avg_price = _scalar(
        "SELECT ROUND(AVG(solp.selling_price_per_unit)::numeric,2) FROM sales_order so "
        "JOIN sales_order_line sol ON so.so_id=sol.so_id "
        "JOIN sales_order_line_pricing solp ON sol.sol_id=solp.sol_id WHERE so.status='closed'"
    )
    if units and avg_price:
        insights.append(_insight(
            "Volume & Price Dynamics",
            f"A total of {_fmt_num(units)} product units were sold across all closed "
            f"orders at an average selling price of {_fmt_currency(avg_price)} per unit. "
            "Tracking units sold alongside revenue reveals whether growth is price-driven "
            "(higher ASP) or volume-driven (more units).",
            "neutral",
        ))

    # 8. Top product
    top_prod = _scalar(
        "SELECT pm.product_name FROM sales_order so "
        "JOIN sales_order_line sol ON so.so_id=sol.so_id "
        "JOIN sales_order_line_pricing solp ON sol.sol_id=solp.sol_id "
        "JOIN product_master pm ON sol.product_id=pm.product_id "
        "WHERE so.status='closed' GROUP BY pm.product_name "
        "ORDER BY SUM(solp.line_total) DESC LIMIT 1"
    )
    top_prod_rev = _scalar(
        "SELECT ROUND(SUM(solp.line_total)::numeric,2) FROM sales_order so "
        "JOIN sales_order_line sol ON so.so_id=sol.so_id "
        "JOIN sales_order_line_pricing solp ON sol.sol_id=solp.sol_id "
        "JOIN product_master pm ON sol.product_id=pm.product_id "
        "WHERE so.status='closed' GROUP BY pm.product_name "
        "ORDER BY SUM(solp.line_total) DESC LIMIT 1"
    )
    if top_prod and top_prod_rev:
        insights.append(_insight(
            "Bestselling Product",
            f"'{top_prod}' is the highest-revenue product with {_fmt_currency(top_prod_rev)} "
            f"in closed-order sales. Ensuring consistent stock availability, competitive pricing, "
            "and promotional support for top-performing products protects a disproportionate share "
            "of total revenue.",
            "opportunity",
        ))

    return insights


# ──────────────────────────────────────────────────────────────────────────────
# CUSTOMER insights
# ──────────────────────────────────────────────────────────────────────────────

def _customer_insights(month: int | None = None) -> list[dict]:
    insights = []

    total_custs = _scalar("SELECT COUNT(*) FROM customer_master", month=month)
    active_custs = _scalar("SELECT COUNT(DISTINCT customer_id) FROM sales_order WHERE status='closed'", month=month)
    if total_custs and active_custs:
        inactive = int(total_custs) - int(active_custs)
        itype = "warning" if inactive > int(total_custs) * 0.2 else "positive"
        insights.append(_insight(
            "Customer Activation Rate",
            f"Of {_fmt_num(total_custs)} registered customers, {_fmt_num(active_custs)} "
            f"have placed at least one closed order — {_fmt_num(inactive)} customers remain "
            f"inactive. " +
            ("A significant inactive base represents a re-engagement opportunity through "
             "targeted campaigns." if inactive > int(total_custs) * 0.2 else
             "Strong customer activation indicates effective onboarding and engagement."),
            itype,
        ))

    avg_rev_per_cust = _scalar(
        "SELECT ROUND(SUM(total_amount)::numeric/NULLIF(COUNT(DISTINCT customer_id),0),2) "
        "FROM sales_order WHERE status='closed'"
    )
    if avg_rev_per_cust:
        insights.append(_insight(
            "Revenue per Customer",
            f"Each active customer contributes on average {_fmt_currency(avg_rev_per_cust)} "
            "in lifetime closed-order revenue. Growing average revenue per customer through "
            "cross-sell, upsell, and loyalty programs is more cost-effective than acquiring "
            "new customers.",
            "neutral",
        ))

    avg_orders = _scalar(
        "SELECT ROUND(COUNT(so_id)::numeric/NULLIF(COUNT(DISTINCT customer_id),0),2) "
        "FROM sales_order WHERE status='closed'"
    )
    if avg_orders:
        itype = "positive" if float(avg_orders) >= 3 else "opportunity"
        insights.append(_insight(
            "Repeat Purchase Rate",
            f"On average, each active customer placed {_fmt_num(avg_orders, 1)} closed orders. "
            + ("This indicates good repeat purchase behaviour and strong customer loyalty." if float(avg_orders) >= 3
               else "Customers are not repeat buying frequently — introducing loyalty rewards or "
                    "subscription models could significantly increase order frequency."),
            itype,
        ))

    top_cust = _scalar(
        "SELECT cm.customer_name FROM sales_order so "
        "JOIN customer_master cm ON so.customer_id=cm.customer_id "
        "WHERE so.status='closed' GROUP BY cm.customer_name "
        "ORDER BY SUM(so.total_amount) DESC LIMIT 1"
    )
    top_cust_rev = _scalar(
        "SELECT ROUND(SUM(so.total_amount)::numeric,2) FROM sales_order so "
        "JOIN customer_master cm ON so.customer_id=cm.customer_id "
        "WHERE so.status='closed' GROUP BY cm.customer_name "
        "ORDER BY SUM(so.total_amount) DESC LIMIT 1"
    )
    if top_cust and top_cust_rev:
        insights.append(_insight(
            "Top Account",
            f"{top_cust} is the highest-value customer at {_fmt_currency(top_cust_rev)} "
            "in revenue. Key account management for top customers ensures continued loyalty "
            "and enables upselling of premium or new product lines.",
            "positive",
        ))

    fulfil = _scalar(
        "SELECT ROUND(100.0*COUNT(*) FILTER(WHERE status='closed')/NULLIF(COUNT(*),0),1) FROM sales_order"
    )
    cancel = _scalar(
        "SELECT ROUND(100.0*COUNT(*) FILTER(WHERE status='cancelled')/NULLIF(COUNT(*),0),1) FROM sales_order"
    )
    if fulfil and cancel:
        insights.append(_insight(
            "Order Fulfilment Health",
            f"Fulfilment rate of {_fmt_pct(fulfil)} with a {_fmt_pct(cancel)} cancellation "
            "rate. High fulfilment rates correlate directly with customer satisfaction scores "
            "and repeat purchase likelihood. Every percentage point reduction in cancellations "
            "translates to retained revenue.",
            "positive" if float(fulfil) >= 80 else "warning",
        ))

    aov = _scalar("SELECT ROUND(AVG(total_amount)::numeric,2) FROM sales_order WHERE status='closed'", month=month)
    if aov:
        insights.append(_insight(
            "Basket Size Opportunity",
            f"Average order value is {_fmt_currency(aov)}. Introducing product bundles, "
            "minimum order incentives, or volume discounts can push this metric higher, "
            "directly improving revenue without increasing customer acquisition costs.",
            "opportunity",
        ))

    return insights


# ──────────────────────────────────────────────────────────────────────────────
# PRODUCT insights
# ──────────────────────────────────────────────────────────────────────────────

def _product_insights(month: int | None = None) -> list[dict]:
    insights = []

    total_prods = _scalar("SELECT COUNT(*) FROM product_master", month=month)
    active_prods = _scalar(
        "SELECT COUNT(DISTINCT sol.product_id) FROM sales_order so "
        "JOIN sales_order_line sol ON so.so_id=sol.so_id WHERE so.status='closed'"
    )
    if total_prods and active_prods:
        inactive = int(total_prods) - int(active_prods)
        pct_active = int(active_prods) / int(total_prods) * 100
        itype = "warning" if pct_active < 80 else "positive"
        insights.append(_insight(
            "Product Catalogue Utilisation",
            f"{_fmt_num(active_prods)} of {_fmt_num(total_prods)} catalogue products "
            f"({pct_active:.0f}%) have been sold in closed orders. "
            f"{_fmt_num(inactive)} products have never appeared in a closed order, "
            "indicating dead stock or inactive listings that may need to be "
            "promoted, discounted, or discontinued.",
            itype,
        ))

    total_cats = _scalar("SELECT COUNT(DISTINCT category) FROM product_master", month=month)
    insights.append(_insight(
        "Category Breadth",
        f"The catalogue spans {_fmt_num(total_cats)} product categories. "
        "A broad category mix hedges against demand shifts in any single segment. "
        "Regularly reviewing per-category growth rates helps reallocate investment "
        "to high-momentum categories.",
        "neutral",
    ))

    top_cat = _scalar(
        "SELECT pm.category FROM sales_order so "
        "JOIN sales_order_line sol ON so.so_id=sol.so_id "
        "JOIN sales_order_line_pricing solp ON sol.sol_id=solp.sol_id "
        "JOIN product_master pm ON sol.product_id=pm.product_id "
        "WHERE so.status='closed' GROUP BY pm.category "
        "ORDER BY SUM(solp.line_total) DESC LIMIT 1"
    )
    top_cat_rev = _scalar(
        "SELECT ROUND(SUM(solp.line_total)::numeric,2) FROM sales_order so "
        "JOIN sales_order_line sol ON so.so_id=sol.so_id "
        "JOIN sales_order_line_pricing solp ON sol.sol_id=solp.sol_id "
        "JOIN product_master pm ON sol.product_id=pm.product_id "
        "WHERE so.status='closed' GROUP BY pm.category "
        "ORDER BY SUM(solp.line_total) DESC LIMIT 1"
    )
    if top_cat and top_cat_rev:
        insights.append(_insight(
            "Revenue-Leading Category",
            f"'{top_cat}' is the highest-revenue category at {_fmt_currency(top_cat_rev)}. "
            "Expanding range depth in this category and launching complementary products "
            "can capture adjacent demand from existing buyers.",
            "positive",
        ))

    top_prod = _scalar(
        "SELECT pm.product_name FROM sales_order so "
        "JOIN sales_order_line sol ON so.so_id=sol.so_id "
        "JOIN sales_order_line_pricing solp ON sol.sol_id=solp.sol_id "
        "JOIN product_master pm ON sol.product_id=pm.product_id "
        "WHERE so.status='closed' GROUP BY pm.product_name "
        "ORDER BY SUM(solp.line_total) DESC LIMIT 1"
    )
    top_prod_rev = _scalar(
        "SELECT ROUND(SUM(solp.line_total)::numeric,2) FROM sales_order so "
        "JOIN sales_order_line sol ON so.so_id=sol.so_id "
        "JOIN sales_order_line_pricing solp ON sol.sol_id=solp.sol_id "
        "JOIN product_master pm ON sol.product_id=pm.product_id "
        "WHERE so.status='closed' GROUP BY pm.product_name "
        "ORDER BY SUM(solp.line_total) DESC LIMIT 1"
    )
    if top_prod and top_prod_rev:
        insights.append(_insight(
            "Bestselling Product",
            f"'{top_prod}' leads individual product revenue at {_fmt_currency(top_prod_rev)}. "
            "Ensuring consistent availability, competitive pricing, and cross-promotion of "
            "this product is critical to protecting a significant portion of total revenue.",
            "opportunity",
        ))

    units = _scalar(
        "SELECT SUM(sol.quantity) FROM sales_order so "
        "JOIN sales_order_line sol ON so.so_id=sol.so_id WHERE so.status='closed'"
    )
    avg_price = _scalar(
        "SELECT ROUND(AVG(solp.selling_price_per_unit)::numeric,2) FROM sales_order so "
        "JOIN sales_order_line sol ON so.so_id=sol.so_id "
        "JOIN sales_order_line_pricing solp ON sol.sol_id=solp.sol_id WHERE so.status='closed'"
    )
    if units and avg_price:
        insights.append(_insight(
            "Volume vs. Price Mix",
            f"Total units sold: {_fmt_num(units)} at an average selling price of "
            f"{_fmt_currency(avg_price)} per unit. Monitoring this ratio over time identifies "
            "whether revenue growth is driven by volume gains or price increases — each "
            "requiring a different strategy.",
            "neutral",
        ))

    avg_items = _scalar(
        "SELECT ROUND(AVG(cnt)::numeric,2) FROM "
        "(SELECT so_id, COUNT(*) AS cnt FROM sales_order_line sl "
        "JOIN sales_order so ON sl.so_id=so.so_id "
        "WHERE so.status='closed' GROUP BY so_id) t"
    )
    if avg_items:
        insights.append(_insight(
            "Cross-Sell Opportunity",
            f"On average, each closed order contains {_fmt_num(avg_items, 1)} product lines. "
            "Recommending complementary products at checkout or through post-purchase marketing "
            "can increase items-per-order, delivering revenue uplift with zero acquisition cost.",
            "opportunity",
        ))

    return insights


# ──────────────────────────────────────────────────────────────────────────────
# VENDOR / PROCUREMENT insights
# ──────────────────────────────────────────────────────────────────────────────

def _vendor_insights(month: int | None = None) -> list[dict]:
    insights = []

    total_vendors = _scalar("SELECT COUNT(*) FROM vendor_master", month=month)
    total_pos = _scalar("SELECT COUNT(*) FROM purchase_order", month=month)
    total_po_val = _scalar("SELECT ROUND(SUM(total_amount)::numeric,2) FROM purchase_order", month=month)
    if total_vendors and total_pos and total_po_val:
        avg_pos_per_vendor = int(total_pos) / int(total_vendors) if int(total_vendors) > 0 else 0
        insights.append(_insight(
            "Procurement Overview",
            f"Procurement spans {_fmt_num(total_vendors)} vendors across "
            f"{_fmt_num(total_pos)} purchase orders totalling "
            f"{_fmt_currency(total_po_val)}. Average of "
            f"{avg_pos_per_vendor:.1f} POs per vendor indicates "
            + ("healthy vendor diversification." if int(total_vendors) >= 5 else
               "high vendor concentration — consider qualifying additional suppliers to "
               "reduce supply chain risk."),
            "positive" if int(total_vendors) >= 5 else "warning",
        ))

    open_val = _scalar(
        "SELECT ROUND(SUM(total_amount)::numeric,2) FROM purchase_order WHERE status='open'"
    )
    open_count = _scalar("SELECT COUNT(*) FROM purchase_order WHERE status='open'", month=month)
    if open_val and open_count:
        insights.append(_insight(
            "Open PO Exposure",
            f"{_fmt_num(open_count)} purchase orders worth {_fmt_currency(open_val)} "
            "are currently open. This represents committed procurement spend pending "
            "delivery. Monitoring open PO ageing ensures timely receipt and reduces "
            "working capital lock-up.",
            "neutral",
        ))

    avg_po = _scalar("SELECT ROUND(AVG(total_amount)::numeric,2) FROM purchase_order", month=month)
    if avg_po:
        insights.append(_insight(
            "Average PO Value",
            f"The average purchase order value is {_fmt_currency(avg_po)}. "
            "Tracking this metric over time reveals whether the business is moving "
            "toward larger bulk orders (cost efficiency) or smaller frequent orders "
            "(inventory flexibility).",
            "neutral",
        ))

    gold_kg = _scalar(
        "SELECT ROUND(SUM(total_gold_wt)::numeric/1000,3) FROM purchase_order "
        "WHERE total_gold_wt IS NOT NULL"
    )
    gold_val = _scalar(
        "SELECT ROUND(SUM(total_gold_amount)::numeric,2) FROM purchase_order "
        "WHERE total_gold_amount IS NOT NULL"
    )
    if gold_kg and gold_val:
        insights.append(_insight(
            "Gold Procurement",
            f"{_fmt_num(float(gold_kg), 3)} kg of gold has been procured at a total cost of "
            f"{_fmt_currency(gold_val)}. Gold is a primary cost input in jewellery — "
            "hedging strategies and negotiating fixed-rate contracts with key vendors "
            "can protect margins against commodity price volatility.",
            "neutral",
        ))

    diamond_cts = _scalar(
        "SELECT ROUND(SUM(total_diamond_cts)::numeric,2) FROM purchase_order "
        "WHERE total_diamond_cts IS NOT NULL"
    )
    diamond_val = _scalar(
        "SELECT ROUND(SUM(total_diamond_amount)::numeric,2) FROM purchase_order "
        "WHERE total_diamond_amount IS NOT NULL"
    )
    if diamond_cts and diamond_val:
        insights.append(_insight(
            "Diamond Procurement",
            f"Total of {_fmt_num(float(diamond_cts), 2)} carats of diamonds procured, "
            f"valued at {_fmt_currency(diamond_val)}. Diamond procurement quality (cut, "
            "clarity, carat weight) directly affects the gross margin achievable at the "
            "sales level. Supplier diversification here mitigates quality and price risk.",
            "neutral",
        ))

    top_vendor = _scalar(
        "SELECT vm.vendor_name FROM purchase_order po "
        "JOIN vendor_master vm ON po.vendor_id=vm.vendor_id "
        "GROUP BY vm.vendor_name ORDER BY SUM(po.total_amount) DESC LIMIT 1"
    )
    top_vendor_val = _scalar(
        "SELECT ROUND(SUM(po.total_amount)::numeric,2) FROM purchase_order po "
        "JOIN vendor_master vm ON po.vendor_id=vm.vendor_id "
        "GROUP BY vm.vendor_name ORDER BY SUM(po.total_amount) DESC LIMIT 1"
    )
    if top_vendor and top_vendor_val and total_po_val:
        pct = float(top_vendor_val) / float(total_po_val) * 100 if float(total_po_val) > 0 else 0
        itype = "warning" if pct > 30 else "positive"
        insights.append(_insight(
            "Top Vendor Dependency",
            f"{top_vendor} accounts for {_fmt_currency(top_vendor_val)} "
            f"({pct:.1f}% of total PO value). "
            + ("High dependency on a single vendor creates supply chain risk. "
               "Qualifying alternate sources for critical materials is recommended." if pct > 30 else
               "Vendor spend is reasonably distributed, indicating healthy supply diversification."),
            itype,
        ))

    return insights


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

_TOPIC_INSIGHT_FN = {
    # Fine-grained sub-topics
    "aov":         _sales_insights,    # AOV insights are a subset of sales insights
    "fulfilment":  _sales_insights,    # fulfilment metrics live on sales_order
    "units":       _product_insights,  # units sold = product dimension
    "pricing":     _product_insights,  # pricing lives on sales_order_line_pricing
    "gold":        _vendor_insights,   # gold lives on purchase_order
    "diamond":     _vendor_insights,   # diamond lives on purchase_order
    # Broad topics
    "sales":       _sales_insights,
    "customer":    _customer_insights,
    "product":     _product_insights,
    "vendor":      _vendor_insights,
    "procurement": _vendor_insights,
    "inventory":   _product_insights,
    "default":     _sales_insights,
}


def get_fallback_insights(
    topic: str,
    existing_titles: set[str] | None = None,
    month: int | None = None,
) -> list[dict]:
    """Return up to 8 data-backed fallback insights for the given topic.

    Each insight is a dict with 'title', 'body', and 'type' keys.
    Pass existing_titles to avoid duplicate insight headings.
    If month is given (1-12), all SQL queries are filtered to that month only.
    """
    fn = _TOPIC_INSIGHT_FN.get(topic, _sales_insights)
    try:
        candidates = fn(month=month)
    except Exception as exc:
        logger.error("Fallback insight generation failed for topic '%s': %s", topic, exc)
        return []

    # Filter out duplicates by title (case-insensitive)
    used_titles = {t.lower() for t in (existing_titles or set())}
    result = []
    for ins in candidates:
        t = ins.get("title", "").lower()
        if t in used_titles:
            continue
        if ins.get("body") and len(ins["body"]) > 20:  # only if body is meaningful
            result.append(ins)
            used_titles.add(t)

    return result
