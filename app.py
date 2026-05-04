"""FastAPI application — AI SQL Analyst API and frontend server."""

import logging
import threading
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(name)s  %(message)s")
logger = logging.getLogger("api")

app = FastAPI(title="AI SQL Analyst", version="1.0.0")


def _warm_caches():
    """Pre-build schema, relationship, and data-profile caches at startup.

    Runs in a background thread so the server starts instantly.
    Any request that arrives before the profile is ready gets the static
    business rules immediately (non-blocking) and the full profile on the
    next request.
    """
    try:
        logger.info("Cache warm-up — starting background pre-load...")
        from db.schema import format_schema
        from db.relationships import format_relationships
        import db.profiler as _profiler

        format_schema()
        logger.info("Cache warm-up — schema loaded")
        format_relationships()
        logger.info("Cache warm-up — relationships loaded")

        # Try to load from persistent DB cache first (milliseconds)
        loaded = _profiler.load_profile_from_db_cache()
        if loaded:
            logger.info("Cache warm-up — profile loaded from DB cache (instant)")
        else:
            # No DB cache yet (first ever deploy) — build from scratch
            logger.info("Cache warm-up — no DB cache found, building profile...")
            _profiler._do_build()
            logger.info("Cache warm-up — profile built and saved to DB")
    except Exception as exc:
        logger.warning("Cache warm-up failed (non-fatal): %s", exc)


# Kick off cache pre-loading as soon as the module is imported
threading.Thread(target=_warm_caches, daemon=True).start()

# ── CORS ────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response schemas ──────────────────────────────────────────────

class QuestionRequest(BaseModel):
    question: str
    provider: str = "groq"       # "groq" | "openai"
    conversation_id: str | None = None


class GenerateSQLResponse(BaseModel):
    sql: str


class ExecuteSQLRequest(BaseModel):
    sql: str


class ExecuteSQLResponse(BaseModel):
    sql: str
    data: list
    row_count: int
    error: str | None = None


class ChatResponse(BaseModel):
    mode: str = "chat"
    sql: str
    data: list
    row_count: int
    answer: str
    insights: str


# Month-name → number mapping for natural-language month detection
_MONTH_NAMES: dict[str, int] = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}


def _extract_month_from_question(question: str) -> int | None:
    """Return the month number (1–12) if the question mentions a specific month, else None."""
    import re
    q = question.lower()
    # Match whole words only to avoid false positives (e.g. "march" vs "marching")
    for name, num in _MONTH_NAMES.items():
        if re.search(rf"\b{re.escape(name)}\b", q):
            return num
    return None


class ReportRequest(BaseModel):
    question: str
    provider: str = "groq"
    conversation_id: str | None = None
    # Filters
    date_from: str | None = None
    date_to: str | None = None
    aggregation: str | None = None  # daily|weekly|monthly|quarterly|yearly
    category: str | None = None
    customer: str | None = None
    status: str | None = None
    product: str | None = None


class ReportApplyFiltersRequest(BaseModel):
    """Apply filters to an existing report without re-generating via LLM."""
    report: dict  # The current report JSON
    date_from: str | None = None
    date_to: str | None = None
    category: str | None = None
    customer: str | None = None
    status: str | None = None
    product: str | None = None
    provider: str = "groq"


class ReportChartFilterRequest(BaseModel):
    """Apply filters to a single chart's SQL and return updated data."""
    sql: str                         # Original chart SQL
    date_from: str | None = None
    date_to: str | None = None
    category: str | None = None
    customer: str | None = None
    status: str | None = None
    product: str | None = None
    top_n: int | None = None         # 5 or 10 — applied after SQL (client hint)
    compare_lm: bool = False         # last-month comparison series
    compare_ly: bool = False         # last-year comparison series
    provider: str = "groq"


class ReportModifyRequest(BaseModel):
    report_json: str
    modification: str
    provider: str = "groq"


class ChartModifyRequest(BaseModel):
    chart: dict
    instruction: str
    history: list[str] = []
    provider: str = "groq"


class ReportChatEditRequest(BaseModel):
    report: dict
    command: str
    history: list[str] = []
    provider: str = "groq"


class ModifyPreviewRequest(BaseModel):
    question: str
    provider: str = "groq"
    conversation_id: str | None = None


class ModifyExecuteRequest(BaseModel):
    sql: str
    provider: str = "groq"
    conversation_id: str | None = None


# ── Endpoints ───────────────────────────────────────────────────────────────

@app.post("/generate-sql", response_model=GenerateSQLResponse)
def generate_sql_endpoint(req: QuestionRequest):
    """Generate SQL for a question without executing it."""
    from ai.pipeline import SQLAnalystPipeline

    pipeline = SQLAnalystPipeline(provider=req.provider)
    sql = pipeline.generate_sql_only(req.question)
    return GenerateSQLResponse(sql=sql)


@app.post("/execute-sql", response_model=ExecuteSQLResponse)
def execute_sql_endpoint(req: ExecuteSQLRequest):
    """Execute a raw SQL SELECT query and return the results."""
    from ai.validator import validate_sql
    from db.executor import execute_sql

    is_safe, reason = validate_sql(req.sql)
    if not is_safe:
        return ExecuteSQLResponse(
            sql=req.sql,
            data=[],
            row_count=0,
            error=f"Query rejected: {reason}",
        )

    result = execute_sql(req.sql)
    if not result["success"]:
        return ExecuteSQLResponse(
            sql=req.sql,
            data=[],
            row_count=0,
            error=result["error"],
        )

    data = result["data"]
    return ExecuteSQLResponse(
        sql=req.sql,
        data=data,
        row_count=len(data),
    )


@app.post("/chat")
def chat_endpoint(req: QuestionRequest):
    from ai.report_generator import classify_intent
    from db.memory import get_recent_history, add_turn

    logger.info(
        "CHAT request | provider=%s | conv=%s | question=%s",
        req.provider,
        req.conversation_id or "default",
        req.question[:100],
    )

    # Deterministic intent classification — no LLM call
    intent = classify_intent(req.question)
    logger.info("CHAT intent: %s", intent)

    conversation_id = req.conversation_id or "default"

    # ── MODIFY: generate SQL preview, don't execute ───────────────────────
    if intent == "modify":
        from ai.modification_pipeline import ModificationPipeline
        mod_pipeline = ModificationPipeline(provider=req.provider)
        result = mod_pipeline.preview(req.question)
        # Store a summary turn so context is preserved
        add_turn(
            conversation_id, req.question,
            f"[Modification request] {result.get('intent_summary', req.question)}",
            "", None,
        )
        return result

    # ── REPORT / EXPORT: skip SQL pipeline, tell frontend to open report ───
    if intent in ("report", "export"):
        add_turn(conversation_id, req.question, "Report generation requested.", "", None)
        return {
            "mode": "report",
            "sql": "",
            "data": [],
            "row_count": 0,
            "answer": (
                "I'll generate a comprehensive analytics report for that. "
                "Click the button below to open your report dashboard."
            ),
            "insights": "",
        }

    # ── QUERY / CHART: run SQL pipeline ──────────────────────────────
    history = get_recent_history(conversation_id, limit=5)
    if history:
        history_lines = ["You are in a multi-turn conversation. Here are the recent exchanges:"]
        for turn in history:
            history_lines.append(f"User: {turn['question']}")
            history_lines.append(f"Assistant: {turn['answer']}")
        history_lines.append(f"Now the user asks: {req.question}")
        question_with_context = "\n".join(history_lines)
    else:
        question_with_context = req.question

    from ai.pipeline import SQLAnalystPipeline
    pipeline = SQLAnalystPipeline(provider=req.provider)
    result = pipeline.run(question_with_context)

    logger.info(
        "CHAT result | conv=%s | sql=%s",
        conversation_id,
        (result.get("sql") or "").replace("\n", " ")[:200],
    )

    # Persist turn for context memory
    add_turn(
        conversation_id,
        req.question,
        result["answer"],
        result["sql"],
        query_result=(result["data"][:200] if result.get("data") else None),
    )

    data = result.get("data") or []

    # Decide whether to surface as a chart
    chart_type = None
    if intent == "chart" or _should_suggest_chart(data):
        chart_type = _suggest_chart_type(data)

    mode = "chart" if chart_type else "chat"

    response = {
        "mode": mode,
        "sql": result["sql"],
        "data": data,
        "row_count": len(data),
        "answer": result["answer"],
        "insights": result["insights"],
    }
    if chart_type:
        response["chart_type"] = chart_type
    return response


def _should_suggest_chart(data: list) -> bool:
    """Return True when data is small and numeric — naturally chart-able."""
    if not data or len(data) < 2 or len(data) > 20:
        return False
    keys = list(data[0].keys())
    if len(keys) < 2:
        return False
    try:
        float(data[0][keys[1]])
        return True
    except (TypeError, ValueError):
        return False


def _suggest_chart_type(data: list) -> str | None:
    """Pick the most suitable Chart.js chart type for the result set."""
    if not data:
        return None
    keys = list(data[0].keys())
    if len(keys) < 2:
        return None
    n = len(data)
    first_label = str(data[0][keys[0]]).lower()
    # Date-like labels → line chart
    if any(x in first_label for x in ["-", "/", "jan", "feb", "mar", "q1", "q2", "2023", "2024", "2025"]):
        return "line"
    # 2–8 categories → doughnut
    if 2 <= n <= 8:
        return "doughnut"
    # Many categories → horizontal bar
    return "bar"


@app.post("/report")
def report_endpoint(req: ReportRequest):
    """Generate a full analytics report from a natural-language question."""
    from ai.report_generator import ReportPipeline

    # ── Auto-detect month from question (e.g. "february", "feb") ─────────
    detected_month = _extract_month_from_question(req.question)

    # Build filter context string for the LLM
    filters = []
    if req.date_from:
        filters.append(f"Date range: from {req.date_from}")
    if req.date_to:
        filters.append(f"to {req.date_to}")
    if req.aggregation:
        filters.append(f"Time aggregation: {req.aggregation}")
    if req.category:
        filters.append(f"Category filter: {req.category}")
    if req.customer:
        filters.append(f"Customer filter: {req.customer}")
    if req.status:
        filters.append(f"Order status filter: {req.status}")
    if req.product:
        filters.append(f"Product filter: {req.product}")
    if detected_month:
        import calendar
        month_name = calendar.month_name[detected_month]  # e.g. "February"
        filters.append(
            f"Month filter: {month_name} only (month number {detected_month}). "
            f"Add EXTRACT(MONTH FROM order_date) = {detected_month} "
            f"(or EXTRACT(MONTH FROM created_at) = {detected_month} for purchase_order) "
            f"to EVERY SQL WHERE clause. This compares {month_name} across ALL years."
        )

    filter_ctx = ""
    if filters:
        filter_ctx = "\n[ACTIVE FILTERS: " + ", ".join(filters) + ". Apply these filters in ALL SQL WHERE clauses.]"

    question_with_filters = req.question + filter_ctx

    logger.info(
        "REPORT request | question=%s | filters=%s | detected_month=%s",
        req.question, filter_ctx or "none", detected_month,
    )
    pipeline = ReportPipeline(provider=req.provider)
    result = pipeline.generate(question_with_filters)

    # ── Attach detected month so frontend / apply-filters can propagate it ─
    if detected_month and isinstance(result, dict):
        result["detected_month"] = detected_month

    return result


@app.post("/report/apply-filters")
def report_apply_filters_endpoint(req: ReportApplyFiltersRequest):
    """Apply filters to an existing report by injecting WHERE clauses into SQL.

    This does NOT call the LLM — it modifies the existing SQL queries directly,
    making it much faster and more reliable than regenerating the entire report.
    """
    from ai.report_generator import ReportPipeline

    filters = {}
    if req.date_from:
        filters["date_from"] = req.date_from
    if req.date_to:
        filters["date_to"] = req.date_to
    if req.category:
        filters["category"] = req.category
    if req.customer:
        filters["customer"] = req.customer
    if req.status:
        filters["status"] = req.status
    if req.product:
        filters["product"] = req.product

    logger.info("REPORT APPLY-FILTERS | filters=%s", filters)
    pipeline = ReportPipeline(provider=req.provider)
    return pipeline.apply_filters(req.report, filters)


@app.post("/report/apply-chart-filter")
def report_apply_chart_filter_endpoint(req: ReportChartFilterRequest):
    """Re-execute a single chart's SQL with injected filters.

    Only the filter types that are applicable to this chart's specific SQL
    are injected (detected via _detect_item_filters).  Irrelevant filters
    are silently ignored so vendor-only charts don't get status injections
    and sales-only charts don't get vendor filters.

    Much faster than applying filters to the whole report — only one SQL
    query is run, so the response is nearly instant.

    Returns:
        { "data": [...], "error": null | "..." }
    """
    from ai.report_generator import _inject_filters, _fix_report_sql, ReportPipeline

    all_filters = {}
    if req.date_from:
        all_filters["date_from"] = req.date_from
    if req.date_to:
        all_filters["date_to"] = req.date_to
    if req.category:
        all_filters["category"] = req.category
    if req.customer:
        all_filters["customer"] = req.customer
    if req.status:
        all_filters["status"] = req.status
    if req.product:
        all_filters["product"] = req.product

    # Detect which filter types are applicable to this chart's SQL
    applicable = set(ReportPipeline._detect_item_filters(req.sql))
    smart_filters: dict = {}
    if "date_range" in applicable:
        if req.date_from:
            smart_filters["date_from"] = req.date_from
        if req.date_to:
            smart_filters["date_to"] = req.date_to
    for key in ("status", "category", "product", "customer"):
        if key in applicable and key in all_filters:
            smart_filters[key] = all_filters[key]

    logger.info(
        "CHART APPLY-FILTER | top_n=%s applicable=%s injected=%s",
        req.top_n, sorted(applicable), list(smart_filters.keys()),
    )

    try:
        sql = _inject_filters(_fix_report_sql(req.sql), smart_filters)

        # Direct DB execution — skip validate_sql overhead (SQL comes from our
        # own system, not user input; safety check not needed on filter path)
        from db.connection import get_engine
        from sqlalchemy import text as _text
        with get_engine().connect() as conn:
            result = conn.execute(_text(sql))
            columns = list(result.keys())
            data = [dict(zip(columns, row)) for row in result.fetchall()]

        # ── Top-N slicing (server-side for response size) ─────────────────
        if req.top_n and data:
            keys = list(data[0].keys())
            if len(keys) >= 2:
                val_key = keys[1]
                try:
                    data = sorted(
                        data,
                        key=lambda r: float(r.get(val_key) or 0),
                        reverse=True,
                    )[:req.top_n]
                except (TypeError, ValueError):
                    data = data[:req.top_n]

        return {"data": data, "error": None}

    except Exception as exc:
        logger.error("CHART APPLY-FILTER error: %s", exc)
        return {"data": [], "error": str(exc)}




@app.post("/report/modify")
def report_modify_endpoint(req: ReportModifyRequest):
    """Modify an existing report based on a natural-language command."""
    from ai.report_generator import ReportPipeline

    logger.info("REPORT MODIFY | command=%s", req.modification)
    pipeline = ReportPipeline(provider=req.provider)
    return pipeline.modify(req.report_json, req.modification)


@app.post("/report/modify-chart")
def report_modify_chart_endpoint(req: ChartModifyRequest):
    """Modify a single chart via a natural language instruction.

    Supports: axis changes, chart type conversion, top-N, filters,
    grouping, sorting, and smart prompt rewriting for vague inputs.

    Returns:
        mode='modified'  → { chart, explanation, sql }
        mode='clarify'   → { message, options[] }  (vague / low-confidence)
        mode='no_change' → { message, chart }
        mode='error'     → { error }
    """
    from ai.chart_modification_pipeline import ChartModificationPipeline

    logger.info(
        "CHART MODIFY | chart=%s | instruction=%s",
        req.chart.get("title", "?")[:40],
        req.instruction[:80],
    )
    pipeline = ChartModificationPipeline(provider=req.provider)
    return pipeline.modify(req.chart, req.instruction, req.history)


@app.post("/report/chat-edit")
def report_chat_edit_endpoint(req: ReportChatEditRequest):
    """Full report editing via natural language chat.

    Supports: add chart, modify chart (@mention), remove chart,
              add KPI, remove KPI.

    Returns:
        mode='updated'   → { report, message }     ← full updated report JSON
        mode='clarify'   → { message, options[] }
        mode='no_change' → { message }
        mode='error'     → { error }
    """
    from ai.report_edit_agent import ReportEditAgent

    logger.info("REPORT CHAT EDIT | command=%s", req.command[:80])
    agent = ReportEditAgent(provider=req.provider)
    return agent.chat_edit(req.report, req.command, req.history)


# ── Data modification endpoints (two-phase: preview then execute) ────────────

@app.post("/modify/preview")
def modify_preview_endpoint(req: ModifyPreviewRequest):
    """Generate a modification SQL for user review — does NOT execute it."""
    from ai.modification_pipeline import ModificationPipeline

    logger.info("MODIFY PREVIEW | question=%s", req.question[:120])
    pipeline = ModificationPipeline(provider=req.provider)
    return pipeline.preview(req.question)


@app.post("/modify/execute")
def modify_execute_endpoint(req: ModifyExecuteRequest):
    """Execute a previously previewed SQL after user explicitly approves it."""
    from ai.modification_pipeline import ModificationPipeline
    from db.memory import add_turn

    logger.info("MODIFY EXECUTE | sql=%s", req.sql[:120])
    pipeline = ModificationPipeline(provider=req.provider)
    result = pipeline.execute(req.sql)

    # Store in conversation memory
    conv_id = req.conversation_id or "default"
    if result.get("mode") == "modify_success":
        add_turn(conv_id, f"[Executed SQL] {req.sql}", result.get("message", ""), req.sql, None)

    return result


# ── Filter values endpoint ──────────────────────────────────────────────────

@app.get("/report/filters")
def report_filters_endpoint():
    """Return distinct filter values for the report filter bar."""
    from db.executor import execute_sql

    result = {}

    # Categories
    cat_res = execute_sql("SELECT DISTINCT category FROM product_master WHERE category IS NOT NULL ORDER BY category")
    result["categories"] = [r["category"] for r in (cat_res["data"] if cat_res["success"] else [])]

    # Customers
    cust_res = execute_sql("SELECT DISTINCT customer_name FROM customer_master WHERE customer_name IS NOT NULL ORDER BY customer_name LIMIT 100")
    result["customers"] = [r["customer_name"] for r in (cust_res["data"] if cust_res["success"] else [])]

    # Products (top 100)
    prod_res = execute_sql("SELECT DISTINCT product_name FROM product_master WHERE product_name IS NOT NULL ORDER BY product_name LIMIT 100")
    result["products"] = [r["product_name"] for r in (prod_res["data"] if prod_res["success"] else [])]

    # Statuses
    stat_res = execute_sql("SELECT DISTINCT status FROM sales_order WHERE status IS NOT NULL ORDER BY status")
    result["statuses"] = [r["status"] for r in (stat_res["data"] if stat_res["success"] else [])]

    # Date range
    date_res = execute_sql("SELECT MIN(order_date)::text AS min_date, MAX(order_date)::text AS max_date FROM sales_order")
    if date_res["success"] and date_res["data"]:
        result["date_range"] = date_res["data"][0]
    else:
        result["date_range"] = {"min_date": None, "max_date": None}

    return result


# ── Schema info endpoint (for debugging / transparency) ─────────────────────

@app.get("/history")
def history_endpoint(conversation_id: str = "default"):
    from db.memory import get_full_history
    return get_full_history(conversation_id)


@app.delete("/history/{turn_id}")
def delete_turn_endpoint(turn_id: int):
    from db.memory import delete_turn
    delete_turn(turn_id)
    return {"ok": True}


@app.get("/history/{turn_id}/sql")
def history_sql_endpoint(turn_id: int):
    """Return just the SQL query for a specific history turn."""
    from db.memory import get_turn_by_id
    turn = get_turn_by_id(turn_id)
    if not turn:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Turn not found")
    return {"turn_id": turn_id, "sql": turn.get("sql_query")}


@app.get("/history/{turn_id}/result")
def history_result_endpoint(turn_id: int):
    """Return just the query result data for a specific history turn."""
    from db.memory import get_turn_by_id
    turn = get_turn_by_id(turn_id)
    if not turn:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Turn not found")
    data = turn.get("query_result") or []
    return {"turn_id": turn_id, "data": data, "row_count": len(data)}


@app.get("/history/{turn_id}/answer")
def history_answer_endpoint(turn_id: int):
    """Return just the AI answer/explanation for a specific history turn."""
    from db.memory import get_turn_by_id
    turn = get_turn_by_id(turn_id)
    if not turn:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Turn not found")
    return {"turn_id": turn_id, "question": turn.get("question"), "answer": turn.get("answer")}


@app.get("/schema")
def schema_endpoint():
    from db.schema import get_schema
    return get_schema()


@app.get("/relationships")
def relationships_endpoint():
    from db.relationships import discover_relationships
    rels = discover_relationships()
    return [
        {
            "table_a": r.table_a, "column_a": r.column_a,
            "table_b": r.table_b, "column_b": r.column_b,
            "confidence": r.confidence, "source": r.source,
        }
        for r in rels
    ]


# ── Frontend static files ──────────────────────────────────────────────────

FRONTEND_DIR = Path(__file__).parent / "frontend"

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
def serve_frontend():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/report-view")
def serve_report_view():
    """Serve the standalone report viewer page (opens in new tab)."""
    return FileResponse(str(FRONTEND_DIR / "report.html"))


# ── Run ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
