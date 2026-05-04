
/* ═══════════════════════════════════════════════════════════════════════════
   AI SQL Analyst — Enterprise Report Viewer (New Tab)
   Renders filter bar, KPIs, charts (Chart.js), data tables, insights.
   Features: streaming reveal, interactive charts, compact layout.
   ═══════════════════════════════════════════════════════════════════════════ */

(function () {
    "use strict";

    // ── Extract report ID from URL ────────────────────────────────────────
    const params = new URLSearchParams(window.location.search);
    const reportId = params.get("id");

    if (!reportId) {
        document.getElementById("reportContent").innerHTML =
            '<div class="report-loading"><div class="report-loading-text">No report ID provided.</div></div>';
        return;
    }

    // ── Show a loading screen while the parent window is still generating ─────
    // This tab is opened early (before fetch completes) to bypass popup blockers.
    // The opener navigates us to the real URL once the report is ready.
    if (reportId === "loading") {
        document.getElementById("reportContent").innerHTML = `
            <div class="report-loading">
                <div class="report-loading-spinner"></div>
                <div class="report-loading-text">
                    Generating your analytics report…
                    <span style="font-size:0.75rem;opacity:0.55;margin-top:0.6rem;display:block">
                        This usually takes 30–60 seconds. This tab will update automatically.
                    </span>
                </div>
            </div>`;
        return;
    }

    // ── Retrieve data from localStorage ─────────────────────────────────
    const raw = localStorage.getItem(reportId);
    if (!raw) {
        document.getElementById("reportContent").innerHTML =
            '<div class="report-loading"><div class="report-loading-text">Report data not found. Please generate the report again from the chat.</div></div>';
        return;
    }

    const reportData = JSON.parse(raw);
    const question = sessionStorage.getItem(reportId + "_question") || "Report";
    const theme = sessionStorage.getItem(reportId + "_theme") || "light";

    // Apply the theme from the parent page
    document.documentElement.setAttribute("data-theme", theme);

    let currentReport = reportData.report;
    const applicableFilters = reportData.applicable_filters || {};

    // ── Per-chart filter state ────────────────────────────────────────────
    // Maps keyed by chartIdx (integer)
    const chartOriginalData = {};  // {idx: original data array}
    const chartSpecs = {};         // {idx: full chart spec object incl. .sql}
    const chartFilters = {};       // {idx: {date_from, date_to, category, ...}}
    const chartModHistory = {};   // {idx: ["instruction1", "instruction2", ...]}
    let editMode = false;
    let sortableInstance = null;
    let reportEditHistory = [];  // commands sent to /report/chat-edit
    const chartInstances = {};     // {idx: Chart.js instance}
    let cachedFilterOptions = {};  // from /report/filters, loaded once
    let activePanelIdx = null;     // which panel is currently open



    // ── Color Palettes ────────────────────────────────────────────────────
    const PALETTES = {
        blues: ["#3b82f6", "#2563eb", "#1d4ed8", "#60a5fa", "#93c5fd", "#1e40af"],
        greens: ["#10b981", "#059669", "#047857", "#34d399", "#6ee7b7", "#065f46"],
        purples: ["#8b5cf6", "#7c3aed", "#6d28d9", "#a78bfa", "#c4b5fd", "#5b21b6"],
        oranges: ["#f59e0b", "#d97706", "#b45309", "#fbbf24", "#fcd34d", "#92400e"],
        mixed: ["#10b981", "#3b82f6", "#8b5cf6", "#f59e0b", "#f43f5e", "#06b6d4", "#6366f1", "#ec4899", "#14b8a6", "#a855f7", "#eab308", "#ef4444", "#22c55e", "#0ea5e9", "#d946ef"],
        gradient: ["#6366f1", "#8b5cf6", "#a855f7", "#c084fc", "#d8b4fe", "#7c3aed"],
    };
    const DEFAULT_COLORS = PALETTES.mixed;
    const ALPHA = "33";

    function getColors(scheme, count) {
        const pal = PALETTES[scheme] || DEFAULT_COLORS;
        const result = [];
        for (let i = 0; i < count; i++) result.push(pal[i % pal.length]);
        return result;
    }

    function getChartDefaults() {
        const isDark = document.documentElement.getAttribute("data-theme") === "dark";
        return {
            gridColor: isDark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.05)",
            textColor: isDark ? "#94a3b8" : "#475569",
            bgColor: isDark ? "#161b22" : "#ffffff",
        };
    }

    if (typeof Chart !== "undefined") {
        Chart.defaults.devicePixelRatio = window.devicePixelRatio || 2;
        
        Chart.register({
            id: 'custom_canvas_background_color',
            beforeDraw: (chart) => {
                const {ctx, canvas} = chart;
                ctx.save();
                ctx.globalCompositeOperation = 'destination-over';
                ctx.fillStyle = document.documentElement.getAttribute('data-theme') === 'dark' ? '#161b22' : '#ffffff';
                ctx.fillRect(0, 0, canvas.width, canvas.height);
                ctx.restore();
            }
        });
    }

    // ── Streaming Render ──────────────────────────────────────────────────
    function streamReveal(container) {
        const sections = container.querySelectorAll(".stream-section");
        sections.forEach((el, i) => {
            setTimeout(() => {
                el.classList.add("visible");
            }, 150 + i * 200);
        });
    }

    // ── Render Full Report ────────────────────────────────────────────────
    function renderReport(report) {
        if (!report) {
            document.getElementById("reportContent").innerHTML =
                '<div class="report-loading"><div class="report-loading-text">Report generation failed. Try again.</div></div>';
            return;
        }

        const content = document.getElementById("reportContent");
        const title = report.title || "Analytics Report";
        const summary = report.summary || "";

        document.title = title + " — AI SQL Analyst";
        document.getElementById("reportTitle").textContent = title;

        let html = "";

        // ── Dynamic Filter Bar (only shows applicable filters) ─────────
        const hasAnyFilter = Object.keys(applicableFilters).length > 0;
        if (hasAnyFilter) {
            html += `<div class="report-filter-bar stream-section" id="filterBar">`;

            if (applicableFilters.date_range) {
                html += `<div class="filter-group">
                    <span class="filter-label">Date Range</span>
                    <input type="date" class="filter-input" id="filterDateFrom" />
                    <span style="font-size:0.65rem;color:var(--text-muted)">to</span>
                    <input type="date" class="filter-input" id="filterDateTo" />
                </div>
                <div class="filter-divider"></div>`;
            }
            if (applicableFilters.category) {
                html += `<div class="filter-group">
                    <span class="filter-label">Category</span>
                    <select class="filter-select" id="filterCategory"><option value="">All</option></select>
                </div>`;
            }
            if (applicableFilters.status) {
                html += `<div class="filter-group">
                    <span class="filter-label">Status</span>
                    <select class="filter-select" id="filterStatus"><option value="">All</option></select>
                </div>`;
            }
            if (applicableFilters.customer) {
                html += `<div class="filter-group">
                    <span class="filter-label">Customer</span>
                    <select class="filter-select" id="filterCustomer"><option value="">All</option></select>
                </div>`;
            }
            if (applicableFilters.product) {
                html += `<div class="filter-group">
                    <span class="filter-label">Product</span>
                    <select class="filter-select" id="filterProduct"><option value="">All</option></select>
                </div>`;
            }

            html += `<button class="filter-apply-btn" id="filterApplyBtn">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:12px;height:12px;margin-right:4px"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
                    Apply Filters
                </button>
                <button class="filter-clear-btn" id="filterClearBtn">Clear Filters</button>
            </div>`;
        }



        // ── Summary ───────────────────────────────────────────────────────
        if (summary) {
            html += `<div class="report-summary stream-section">
                <div class="report-summary-text">${escapeHtml(summary)}</div>
            </div>`;
        }

        // ── KPIs ──────────────────────────────────────────────────────────
        const kpis = report.kpis || [];
        if (kpis.length > 0) {
            html += `<div class="report-section-label label-kpi stream-section">
                <div class="report-section-label-icon">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
                </div>
                <span class="report-section-label-text">Key Performance Indicators</span>
            </div>
            <div class="kpi-grid stream-section">`;

            kpis.forEach((kpi, kpiIdx) => {
                const val = formatKPIValue(kpi.value, kpi.format);
                const hasExplanation = kpi.explanation && (kpi.explanation.what || kpi.explanation.how);
                html += `<div class="kpi-card" style="position:relative" data-kpi-idx="${kpiIdx}">
                    <button class="kpi-delete-btn" data-kpi-idx="${kpiIdx}" title="Delete KPI">×</button>
                    <div class="kpi-header">
                        <div class="kpi-label">${escapeHtml(kpi.label || kpi.id || "Metric")}</div>
                        ${hasExplanation ? `<button class="kpi-eye-btn" data-explain='${escapeAttr(JSON.stringify(kpi.explanation))}' data-title="${escapeAttr(kpi.label || kpi.id || "Metric")}">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
                        </button>` : ""}
                    </div>
                    <div class="kpi-value">${kpi.error ? '<span style="font-size:0.75rem;color:var(--accent-rose)">Error</span>' : val}</div>
                    ${kpi.error ? `<div style="font-size:0.62rem;color:var(--text-muted);margin-top:0.15rem;line-height:1.3">${escapeHtml(kpi.error)}</div>` : ""}
                </div>`;
            });
            html += `</div>`;
        }

        // ── Charts (pre-filter: skip empty, errored, or all-zero charts) ──
        const rawCharts = report.charts || [];
        const charts = rawCharts.filter(c => {
            if (c.error) return false;
            if (!c.data || c.data.length === 0) return false;
            // Check if all numeric values are zero
            const keys = Object.keys(c.data[0]);
            if (keys.length < 2) return false; // need label + value
            const valueKeys = keys.slice(1);
            const allZero = c.data.every(row => valueKeys.every(k => {
                const v = Number(row[k]);
                return isNaN(v) || v === 0;
            }));
            if (allZero) return false;
            return true;
        });
        if (charts.length > 0) {
            html += `<div class="report-section-label label-charts stream-section">
                <div class="report-section-label-icon">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg>
                </div>
                <span class="report-section-label-text">Visual Analytics</span>
            </div>
            <div class="edit-toolbar" id="editToolbar">
                <span class="edit-toolbar-label">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                    Edit Mode — drag to reorder
                </span>
                <button class="edit-add-btn" id="editAddChartBtn">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                    Add Chart
                </button>
                <button class="edit-add-btn" id="editAddKpiBtn">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                    Add KPI
                </button>
            </div>
            <div class="charts-grid stream-section" id="chartsGrid">`;

            // Pre-compute which charts are naturally wide (line/area/stackedBar)
            const naturallyWide = charts.map(c => {
                if (c.widthMode === "wide") return true;
                if (c.widthMode === "normal") return false;
                return ["line", "area", "stackedbar"].includes((c.type || "bar").toLowerCase());
            });

            const shouldBeWide = [...naturallyWide];
            let col = 0;
            for (let i = 0; i < charts.length; i++) {
                if (shouldBeWide[i]) {
                    col = 0;
                } else {
                    if (col === 0) {
                        const nextWide = (i + 1 >= charts.length) || shouldBeWide[i + 1];
                        if (nextWide && charts[i].widthMode !== "normal") { shouldBeWide[i] = true; col = 0; }
                        else { col = 1; }
                    } else { col = 0; }
                }
            }

            charts.forEach((chart, idx) => {
                const isWide = shouldBeWide[idx];
                const isCollapsed = chart.isCollapsed === true;
                const chartTypeBadge = (chart.type || "bar")
                    .replace("horizontalBar", "H.BAR").replace("stackedBar", "STACKED")
                    .replace("doughnut", "DONUT").toUpperCase();
                const chartInsight = chart.chart_insight || (chart.explanation && chart.explanation.insight) || "";

                const expl = {
                    what: (chart.explanation && chart.explanation.what) || chart.title || "",
                    how: (chart.explanation && chart.explanation.how) || `${chartTypeBadge} chart — data grouped and aggregated from the database.`,
                    insight: (chart.explanation && chart.explanation.insight) || chart.chart_insight || "",
                    type: chart.type || "bar",
                };

                const funnelSvg = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>`;
                const wandSvg   = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 4V2m0 2v2m0-2h-2m2 0h2M9 20l9.5-9.5-2.5-2.5L6.5 17.5M3 21l3-3"/><path d="M20 7l-1-1"/></svg>`;
                const dlSvg     = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>`;
                const sendSvgTpl = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>`;

                html += `<div class="chart-card${isWide ? " chart-full-width" : ""}" data-chart-idx="${idx}" style="position:relative;">
                    <div class="chart-header" id="chart-header-${idx}">
                        <span class="chart-title">${escapeHtml(chart.title || "Chart " + (idx + 1))}</span>
                        <div class="chart-header-right">
                            <span class="chart-type-badge">${chartTypeBadge}</span>
                            <button class="chart-filter-btn" id="chart-resize-btn-${idx}" title="Minimize/Maximize Width" data-chart-idx="${idx}">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg>
                            </button>
                            <button class="chart-filter-btn" id="chart-collapse-btn-${idx}" title="Collapse/Expand Graph" data-chart-idx="${idx}">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="${isCollapsed ? '6 9 12 15 18 9' : '18 15 12 9 6 15'}"/></svg>
                            </button>
                            <button class="chart-filter-btn" id="chart-filter-btn-${idx}" title="Chart Filters" data-chart-idx="${idx}">
                                ${funnelSvg}
                            </button>
                            <button class="chart-ai-btn" id="chart-ai-btn-${idx}" title="AI Modify (natural language)" data-chart-idx="${idx}">
                                ${wandSvg}
                            </button>
                            <div style="position:relative">
                                <button class="chart-export-btn" id="chart-export-btn-${idx}" title="Export / Download" data-chart-idx="${idx}">
                                    ${dlSvg}
                                </button>
                                <div class="chart-export-menu" id="chart-export-menu-${idx}">
                                    <button class="chart-export-item" data-action="png" data-chart-idx="${idx}">
                                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
                                        Download PNG
                                    </button>
                                    <button class="chart-export-item" data-action="pdf" data-chart-idx="${idx}">
                                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                                        Download PDF
                                    </button>
                                    <button class="chart-export-item" data-action="sql" data-chart-idx="${idx}">
                                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
                                        View SQL
                                    </button>
                                </div>
                            </div>
                            <button class="kpi-eye-btn" data-explain='${escapeAttr(JSON.stringify(expl))}' data-title="${escapeAttr(chart.title || "Chart")}">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
                            </button>
                        </div>
                        <div class="chart-filter-badges" id="chart-badges-${idx}"></div>
                    </div>
                    <div class="chart-body-container" style="display: ${isCollapsed ? 'none' : 'block'}">
                        <div class="chart-body" id="chart-body-${idx}">
                            <canvas id="chart_${idx}"></canvas>
                        </div>
                        ${chartInsight ? `<div class="chart-insight-text">&#x1F4A1; ${escapeHtml(chartInsight)}</div>` : ""}
                    </div>
                    <div class="chart-ai-panel" id="ai-panel-${idx}">
                        <div class="ai-panel-header">
                            ${wandSvg}
                            AI Modify
                            <span style="margin-left:auto;font-size:0.54rem;opacity:0.45;font-weight:600;text-transform:none;letter-spacing:0">context-aware</span>
                        </div>
                        <div class="ai-panel-history" id="ai-history-${idx}"></div>
                        <div class="ai-panel-input">
                            <input type="text" class="ai-panel-input-box" id="ai-input-${idx}"
                                   placeholder="e.g. 'show top 5', 'convert to line', 'filter Mumbai'" />
                            <button class="ai-send-btn" id="ai-send-${idx}" data-chart-idx="${idx}">
                                ${sendSvgTpl}
                            </button>
                        </div>
                    </div>
                    <button class="chart-delete-btn" data-chart-idx="${idx}" title="Remove chart">×</button>
                </div>`;
            });

            html += `</div>`;
        }



        // ── Data Table ────────────────────────────────────────────────────
        const table = report.table;
        if (table && table.data && table.data.length > 0) {
            html += `<div class="report-section-label label-table stream-section">
                <div class="report-section-label-icon">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>
                </div>
                <span class="report-section-label-text">Data Preview</span>
            </div>
            <div class="report-table-section stream-section">
                <div class="report-table-header">
                    <span class="report-table-title">${escapeHtml(table.title || "Detail Data")}</span>
                    <span class="row-badge">${table.data.length} row${table.data.length !== 1 ? "s" : ""}</span>
                    ${table.explanation ? `<button class="kpi-eye-btn" data-explain='${escapeAttr(JSON.stringify(table.explanation))}' data-title="${escapeAttr(table.title || "Data Table")}">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
                    </button>` : ""}
                </div>
                <div class="report-table-body">${buildTable(table.data)}</div>
            </div>`;
        }

        // ── Insights — Rich Cards ─────────────────────────────────────────
        const insights = report.insights || [];
        if (insights.length > 0) {
            const insightIcons = {
                positive: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg>`,
                negative: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 17 13.5 8.5 8.5 13.5 2 7"/><polyline points="16 17 22 17 22 11"/></svg>`,
                warning: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
                opportunity: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`,
                neutral: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>`,
            };
            html += `<div class="report-section-label label-insights stream-section">
                <div class="report-section-label-icon">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a7 7 0 017 7c0 2.38-1.19 4.47-3 5.74V17a1 1 0 01-1 1H9a1 1 0 01-1-1v-2.26C6.19 13.47 5 11.38 5 9a7 7 0 017-7z"/><line x1="9" y1="21" x2="15" y2="21"/></svg>
                </div>
                <span class="report-section-label-text">AI-Generated Insights</span>
                <span style="font-size:0.6rem;font-weight:700;color:var(--accent-indigo);background:rgba(99,102,241,0.1);padding:0.14rem 0.45rem;border-radius:10px;margin-left:auto">${insights.length} insights</span>
            </div>
            <div class="report-insights stream-section">`;

            insights.forEach((ins, idx) => {
                // Support both string and object insights
                if (typeof ins === "string") {
                    html += `<div class="insight-card type-neutral">
                        <div class="insight-body">${escapeHtml(ins)}</div>
                    </div>`;
                } else {
                    const type = ins.type || "neutral";
                    const icon = insightIcons[type] || insightIcons.neutral;
                    html += `<div class="insight-card type-${type}">
                        <div class="insight-card-header">
                            <div class="insight-icon-wrap type-icon-${type}">${icon}</div>
                            <div style="flex:1;min-width:0">
                                <div class="insight-type-badge badge-${type}">${type}</div>
                                ${ins.title ? `<div class="insight-title">${escapeHtml(ins.title)}</div>` : ""}
                            </div>
                            <div class="insight-num">${String(idx + 1).padStart(2, "0")}</div>
                        </div>
                        <div class="insight-body">${escapeHtml(ins.body || ins.title || "")}</div>
                    </div>`;
                }
            });
            html += `</div>`;
        }

        content.innerHTML = html;

        // ── Store per-chart data and spec ─────────────────────────────────
        charts.forEach((chart, idx) => {
            chartOriginalData[idx] = JSON.parse(JSON.stringify(chart.data || []));
            chartSpecs[idx] = chart;  // full spec including .sql, .type, etc.
            chartFilters[idx] = {};   // empty filter state
        });

        // ── Render Charts ─────────────────────────────────────────────────
        charts.forEach((chart, idx) => {
            if (!chart.data || chart.data.length === 0) return;
            const canvas = document.getElementById(`chart_${idx}`);
            if (!canvas) return;
            try {
                const instance = renderChart(canvas, chart);
                if (instance) chartInstances[idx] = instance;
            } catch (err) {
                console.error(`Chart ${idx} render failed:`, err, chart);
                const card = canvas.closest(".chart-card");
                if (card) card.style.display = "none";
            }
        });

        // ── Wire eye buttons ──────────────────────────────────────────────
        content.querySelectorAll(".kpi-eye-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                const explanation = JSON.parse(btn.dataset.explain);
                const title = btn.dataset.title || "Explanation";
                showExplanationModal(title, explanation);
            });
        });

        // ── Wire per-chart filter buttons ─────────────────────────────────
        content.querySelectorAll(".chart-filter-btn").forEach(btn => {
            btn.addEventListener("click", (e) => {
                e.stopPropagation();
                const idx = parseInt(btn.dataset.chartIdx, 10);
                openChartFilterPanel(idx, btn);
            });
        });

        // ── Wire AI modify buttons ────────────────────────────────────────
        content.querySelectorAll(".chart-ai-btn").forEach(btn => {
            btn.addEventListener("click", (e) => {
                e.stopPropagation();
                const idx = parseInt(btn.dataset.chartIdx, 10);
                toggleChartAiPanel(idx);
            });
        });

        // ── Wire resize and collapse buttons ──────────────────────────────
        content.querySelectorAll(".chart-resize-btn, [id^='chart-resize-btn-']").forEach(btn => {
            btn.addEventListener("click", () => {
                const idx = parseInt(btn.dataset.chartIdx, 10);
                const spec = chartSpecs[idx];
                if (!spec) return;
                const isWide = btn.closest(".chart-card").classList.contains("chart-full-width");
                spec.widthMode = isWide ? "normal" : "wide";
                currentReport.charts[idx] = spec;
                reRenderReport(currentReport);
            });
        });
        
        content.querySelectorAll("[id^='chart-collapse-btn-']").forEach(btn => {
            btn.addEventListener("click", () => {
                const idx = parseInt(btn.dataset.chartIdx, 10);
                const spec = chartSpecs[idx];
                if (!spec) return;
                spec.isCollapsed = !spec.isCollapsed;
                currentReport.charts[idx] = spec;
                reRenderReport(currentReport);
            });
        });

        // ── Wire export dropdown buttons ──────────────────────────────────
        content.querySelectorAll(".chart-export-btn").forEach(btn => {
            btn.addEventListener("click", (e) => {
                e.stopPropagation();
                const idx = parseInt(btn.dataset.chartIdx, 10);
                const menu = document.getElementById(`chart-export-menu-${idx}`);
                if (menu) {
                    document.querySelectorAll(".chart-export-menu.open").forEach(m => {
                        if (m !== menu) m.classList.remove("open");
                    });
                    menu.classList.toggle("open");
                }
            });
        });

        // ── Wire export menu items ────────────────────────────────────────
        content.querySelectorAll(".chart-export-item").forEach(item => {
            item.addEventListener("click", (e) => {
                e.stopPropagation();
                const idx    = parseInt(item.dataset.chartIdx, 10);
                const action = item.dataset.action;
                const menu   = document.getElementById(`chart-export-menu-${idx}`);
                if (menu) menu.classList.remove("open");
                if (action === "png") exportChartPNG(idx);
                else if (action === "pdf") exportChartPDF(idx);
                else if (action === "sql") showChartSql(idx);
            });
        });

        // ── Wire AI send buttons ──────────────────────────────────────────
        content.querySelectorAll(".ai-send-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                const idx = parseInt(btn.dataset.chartIdx, 10);
                sendChartModification(idx);
            });
        });

        // ── Wire AI input Enter key ───────────────────────────────────────
        content.querySelectorAll(".ai-panel-input-box").forEach(input => {
            input.addEventListener("keydown", (e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    const idx = parseInt(input.id.replace("ai-input-", ""), 10);
                    sendChartModification(idx);
                }
            });
        });

        // ── Wire chart delete buttons ─────────────────────────────────────
        content.querySelectorAll(".chart-delete-btn").forEach(btn => {
            btn.addEventListener("click", (e) => {
                e.stopPropagation();
                deleteChart(parseInt(btn.dataset.chartIdx, 10));
            });
        });

        // ── Wire KPI delete buttons ───────────────────────────────────────
        content.querySelectorAll(".kpi-delete-btn").forEach(btn => {
            btn.addEventListener("click", (e) => {
                e.stopPropagation();
                deleteKpi(parseInt(btn.dataset.kpiIdx, 10));
            });
        });

        // ── Wire edit toolbar shortcut buttons ────────────────────────────
        const addChartBtn = document.getElementById("editAddChartBtn");
        if (addChartBtn) {
            addChartBtn.addEventListener("click", () => {
                openChatPanel();
                const input = document.getElementById("rcpInput");
                if (input) { input.value = "add bar chart for "; input.focus(); }
            });
        }
        const addKpiBtn = document.getElementById("editAddKpiBtn");
        if (addKpiBtn) {
            addKpiBtn.addEventListener("click", () => {
                openChatPanel();
                const input = document.getElementById("rcpInput");
                if (input) { input.value = "add KPI for "; input.focus(); }
            });
        }

        // ── Re-init sortable if still in edit mode after re-render ────────
        if (editMode) { setTimeout(initSortable, 80); }

        // ── Streaming reveal ──────────────────────────────────────────────
        streamReveal(content);

        // ── Load filter options ───────────────────────────────────────────
        loadFilterOptions();

        // ── Wire filter apply button ──────────────────────────────────────
        const applyBtn = document.getElementById("filterApplyBtn");
        if (applyBtn) {
            applyBtn.addEventListener("click", handleFilterApply);
        }

        // ── Wire clear filters button ─────────────────────────────────────
        const clearBtn = document.getElementById("filterClearBtn");
        if (clearBtn) {
            clearBtn.addEventListener("click", handleClearFilters);
        }
    }


    // ── Load Filter Options ───────────────────────────────────────────────
    async function loadFilterOptions() {
        try {
            const res = await fetch("/report/filters");
            if (!res.ok) return;
            const data = await res.json();

            // Cache for per-chart panels
            cachedFilterOptions = data;

            populateSelect("filterCategory", data.categories || []);
            populateSelect("filterCustomer", data.customers || []);
            populateSelect("filterProduct", data.products || []);
            populateSelect("filterStatus", data.statuses || []);

            if (data.date_range) {
                const fromEl = document.getElementById("filterDateFrom");
                const toEl = document.getElementById("filterDateTo");
                if (fromEl && data.date_range.min_date) fromEl.value = data.date_range.min_date.split("T")[0];
                if (toEl && data.date_range.max_date) toEl.value = data.date_range.max_date.split("T")[0];
            }
        } catch (e) {
            console.warn("Failed to load filter options:", e);
        }
    }


    function populateSelect(id, options) {
        const el = document.getElementById(id);
        if (!el) return;
        options.forEach(opt => {
            const o = document.createElement("option");
            o.value = opt;
            o.textContent = opt;
            el.appendChild(o);
        });
    }

    // ── Handle Filter Apply ───────────────────────────────────────────────
    // Stores the original (unfiltered) report so we can re-apply filters
    // from a clean base each time, avoiding stacking of filter conditions.
    let originalReport = currentReport ? JSON.parse(JSON.stringify(currentReport)) : null;

    async function handleFilterApply() {
        const btn = document.getElementById("filterApplyBtn");
        btn.textContent = "Loading...";
        btn.disabled = true;

        const filters = {
            date_from: document.getElementById("filterDateFrom")?.value || null,
            date_to: document.getElementById("filterDateTo")?.value || null,
            category: document.getElementById("filterCategory")?.value || null,
            customer: document.getElementById("filterCustomer")?.value || null,
            status: document.getElementById("filterStatus")?.value || null,
            product: document.getElementById("filterProduct")?.value || null,
        };

        // Check if any filter is actually set (non-empty)
        const hasActiveFilters = Object.values(filters).some(v => v && v.trim() !== "");

        try {
            let data;

            if (hasActiveFilters && originalReport) {
                // ── Use server-side SQL injection (fast, no LLM) ──────────
                const res = await fetch("/report/apply-filters", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        report: originalReport,
                        provider: localStorage.getItem(reportId + "_provider") || "groq",
                        ...filters,
                    }),
                });

                if (res.ok) {
                    data = await res.json();
                }
            } else if (!hasActiveFilters) {
                // All filters cleared — restore the original report
                data = { report: JSON.parse(JSON.stringify(originalReport)) };
            } else {
                // Fallback: LLM regeneration (only if no original report)
                const res = await fetch("/report", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        question: question,
                        provider: localStorage.getItem(reportId + "_provider") || "groq",
                        ...filters,
                    }),
                });

                if (res.ok) {
                    data = await res.json();
                }
            }

            if (data && data.report) {
                currentReport = data.report;
                // Destroy existing charts
                try {
                    Object.values(Chart.instances).forEach(inst => inst.destroy());
                } catch (_) { }

                // Save current filter values before re-render wipes them
                const savedFilters = { ...filters };

                renderReport(currentReport);

                // Restore filter values after re-render
                await loadFilterOptions();
                if (savedFilters.date_from) {
                    const el = document.getElementById("filterDateFrom");
                    if (el) el.value = savedFilters.date_from;
                }
                if (savedFilters.date_to) {
                    const el = document.getElementById("filterDateTo");
                    if (el) el.value = savedFilters.date_to;
                }
                if (savedFilters.category) {
                    const el = document.getElementById("filterCategory");
                    if (el) el.value = savedFilters.category;
                }
                if (savedFilters.status) {
                    const el = document.getElementById("filterStatus");
                    if (el) el.value = savedFilters.status;
                }
                if (savedFilters.customer) {
                    const el = document.getElementById("filterCustomer");
                    if (el) el.value = savedFilters.customer;
                }
                if (savedFilters.product) {
                    const el = document.getElementById("filterProduct");
                    if (el) el.value = savedFilters.product;
                }
            }
        } catch (e) {
            console.error("Filter apply failed:", e);
            // Show error to user
            const content = document.getElementById("reportContent");
            if (content) {
                const errDiv = document.createElement("div");
                errDiv.className = "chart-error";
                errDiv.style.cssText = "margin:1rem;padding:0.75rem;border-radius:0.5rem;";
                errDiv.textContent = "Filter application failed: " + (e.message || "Unknown error");
                content.prepend(errDiv);
                setTimeout(() => errDiv.remove(), 5000);
            }
        }

        btn.textContent = "Apply Filters";
        btn.disabled = false;
    }

    // ── Handle Clear Filters ──────────────────────────────────────────────
    function handleClearFilters() {
        // Reset all filter selects to "All"
        ["filterCategory", "filterStatus", "filterCustomer", "filterProduct"].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.selectedIndex = 0;
        });

        // Reset date inputs
        const fromEl = document.getElementById("filterDateFrom");
        const toEl = document.getElementById("filterDateTo");
        if (fromEl) fromEl.value = "";
        if (toEl) toEl.value = "";

        // Restore original report
        if (originalReport) {
            currentReport = JSON.parse(JSON.stringify(originalReport));
            try {
                Object.values(Chart.instances).forEach(inst => inst.destroy());
            } catch (_) { }
            renderReport(currentReport);
        }

        // Re-load date range defaults
        loadFilterOptions();
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  PER-CHART LOCAL FILTER SYSTEM  — v2
    // ═══════════════════════════════════════════════════════════════════════

    // ── Date range quick-pill helpers ─────────────────────────────────────
    function _dateQuickRange(preset) {
        const today = new Date();
        const y = today.getFullYear(), m = today.getMonth();
        let from;
        const to = today.toISOString().slice(0, 10);
        if (preset === "MTD") from = new Date(y, m, 1).toISOString().slice(0, 10);
        else if (preset === "QTD") from = new Date(y, Math.floor(m / 3) * 3, 1).toISOString().slice(0, 10);
        else if (preset === "YTD") from = new Date(y, 0, 1).toISOString().slice(0, 10);
        else return null;
        return { from, to };
    }

    // ── Determine which filters are relevant for this chart ───────────────
    // Returns a smart config object — single source of truth for panel builder
    function _getChartFilterConfig(idx) {
        const spec = chartSpecs[idx] || {};
        const sql = (spec.sql || "").toLowerCase();
        const cType = (spec.type || "bar").toLowerCase();
        const data = spec.data || [];
        const colNames = data.length > 0
            ? Object.keys(data[0]).map(c => c.toLowerCase()) : [];

        const isTimeSeries = ["line", "area"].includes(cType);
        const isRanking = ["bar", "horizontalbar"].includes(cType);
        const isShare = ["doughnut", "pie"].includes(cType);
        const isStacked = cType === "stackedbar";

        // ── Extract actual data labels (first column = dimension) ──────────
        const labelKey = data.length > 0 ? Object.keys(data[0])[0] : null;
        const rawLabels = labelKey ? data.map(r => String(r[labelKey] ?? "")) : [];

        // Detect date-like labels — skip dim-chips for time-series charts
        const DATE_PATS = [/^\d{4}-\d{2}/, /^\d{4}$/, /^Q[1-4]/i,
            /^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)/i];
        const labelsAreDates = rawLabels.length > 0 &&
            rawLabels.slice(0, 4).filter(l => DATE_PATS.some(p => p.test(l.trim()))).length >= 2;

        // ── Smart dimension chips — actual data values as interactive filter ─
        // Shown for categorical (non-date) bar/pie/stacked charts with ≤20 labels
        const showDimChips = !labelsAreDates && rawLabels.length >= 2 && rawLabels.length <= 20
            && (isRanking || isShare || isStacked);
        const dimChips = showDimChips ? rawLabels : [];
        const labelDimName = labelKey
            ? labelKey.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase())
            : "Dimension";

        // ── Date range ────────────────────────────────────────────────────
        const hasDateCol = colNames.some(c => /date|month|year|quarter|week|period/.test(c));
        const sqlDate = /order_date|sales_order|created_at/.test(sql);
        const showDate = isTimeSeries || isStacked || hasDateCol || sqlDate;

        // ── Category / Product / Status — server-side, only when dim-chips absent ─
        const sqlCat = /category|product_master/.test(sql);
        const colCat = colNames.some(c => /category|type/.test(c));
        const showCategory = !showDimChips && (sqlCat || colCat) &&
            !!(cachedFilterOptions.categories && cachedFilterOptions.categories.length);

        const sqlProd = /product_name|product_master/.test(sql);
        const colProd = colNames.some(c => /product/.test(c));
        const showProduct = !showDimChips && (sqlProd || colProd) &&
            !!(cachedFilterOptions.products && cachedFilterOptions.products.length);

        const sqlStatus = !isTimeSeries && (/\.status\b/.test(sql) || /\bstatus\b/.test(sql));
        const colStatus = !isTimeSeries && colNames.some(c => c === "status");
        const showStatus = !showDimChips && (sqlStatus || colStatus) &&
            !!(cachedFilterOptions.statuses && cachedFilterOptions.statuses.length);

        // ── Top N: only for non-time-series charts with >4 items ──────────
        const showTopN = !isTimeSeries && (isRanking || isShare || isStacked) && rawLabels.length > 4;
        const topNOpts = showTopN ? [5, 10, 20] : [];

        // ── Sort order: for ranking/share charts ──────────────────────────
        const showSort = (isRanking || isShare) && rawLabels.length > 2;

        // ── Compatible chart types for switcher ───────────────────────────
        const numRows = data.length;
        const numCols = data.length > 0 ? Object.keys(data[0]).length : 0;
        const compatTypes = [
            { type: "bar", label: "Bar" },
            { type: "horizontalBar", label: "H.Bar" },
        ];
        if (numRows <= 12) {
            compatTypes.push({ type: "pie", label: "Pie" });
            compatTypes.push({ type: "doughnut", label: "Donut" });
        }
        compatTypes.push({ type: "line", label: "Line" });
        compatTypes.push({ type: "area", label: "Area" });
        if (numCols >= 3) compatTypes.push({ type: "stackedBar", label: "Stacked" });

        return {
            isTimeSeries, isRanking, isShare, isStacked, labelsAreDates,
            showDate, showDimChips, dimChips, labelKey, labelDimName,
            showCategory, showProduct, showStatus,
            showTopN, topNOpts, showSort, compatTypes,
        };
    }

    // ── Shared helper: destroy old chart, recreate canvas, render or show no-data ─
    function renderChartOrNoData(idx, dataRows) {
        const spec = chartSpecs[idx];
        const chartBody = document.getElementById(`chart-body-${idx}`);
        if (!spec || !chartBody) return;

        // Remove any existing no-data overlay
        chartBody.querySelector(".chart-no-data")?.remove();

        // Destroy old Chart.js instance
        if (chartInstances[idx]) {
            try { chartInstances[idx].destroy(); } catch (_) { }
            delete chartInstances[idx];
        }

        // Remove old canvas
        chartBody.querySelector(`#chart_${idx}`)?.remove();

        if (!dataRows || dataRows.length === 0) {
            const nd = document.createElement("div");
            nd.className = "chart-no-data";
            nd.innerHTML = `
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                    <circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/>
                </svg>
                <div class="chart-no-data-text">No data for selected filters</div>
                <div class="chart-no-data-sub">Try changing or resetting your filters</div>
            `;
            chartBody.appendChild(nd);
            return;
        }

        // Recreate canvas fresh (Chart.js requires this after destroy)
        const canvas = document.createElement("canvas");
        canvas.id = `chart_${idx}`;
        chartBody.appendChild(canvas);

        // Apply chart type override from active filter state
        const activeFilters = chartFilters[idx] || {};
        const effectiveSpec = activeFilters.chart_type
            ? { ...spec, type: activeFilters.chart_type }
            : spec;

        // Sync the type badge in the card header
        const badgeEl = document.querySelector(`[data-chart-idx="${idx}"] .chart-type-badge`);
        if (badgeEl) {
            const dt = (effectiveSpec.type || "bar")
                .replace("horizontalBar", "H.BAR").replace("stackedBar", "STACKED")
                .replace("doughnut", "DONUT").toUpperCase();
            badgeEl.textContent = dt;
        }

        try {
            const inst = renderChart(canvas, { ...effectiveSpec, data: dataRows });
            if (inst) chartInstances[idx] = inst;
        } catch (err) {
            console.error("Chart re-render failed:", err);
        }
    }

    // ── Call /report/apply-chart-filter with current filter state ─────────
    // ── Client-side result cache (keyed on sql+filters hash) ─────────────
    const _filterCache = new Map();         // cacheKey → data[]
    const _CACHE_MAX = 40;                // evict oldest when full
    const _inflightCtrls = {};               // idx → AbortController

    function _filterCacheKey(sql, filters) {
        return JSON.stringify({
            sql,
            df: filters.date_from || "",
            dt: filters.date_to || "",
            ca: filters.category || "",
            pr: filters.product || "",
            st: filters.status || "",
            tn: filters.top_n || 0,
        });
    }

    async function _fetchFilteredData(idx, filters) {
        const spec = chartSpecs[idx];
        if (!spec || !spec.sql) return null;

        const cacheKey = _filterCacheKey(spec.sql, filters);

        // ── Cache hit — return instantly ────────────────────────────────
        if (_filterCache.has(cacheKey)) {
            return _filterCache.get(cacheKey);
        }

        // ── Abort any previous in-flight request for this chart ─────────
        if (_inflightCtrls[idx]) {
            try { _inflightCtrls[idx].abort(); } catch (_) { }
        }
        const ctrl = new AbortController();
        _inflightCtrls[idx] = ctrl;

        try {
            const res = await fetch("/report/apply-chart-filter", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                signal: ctrl.signal,
                body: JSON.stringify({
                    sql: spec.sql,
                    date_from: filters.date_from || null,
                    date_to: filters.date_to || null,
                    category: filters.category || null,
                    product: filters.product || null,
                    status: filters.status || null,
                    top_n: filters.top_n || null,
                    compare_lm: false,
                    compare_ly: false,
                    provider: localStorage.getItem(reportId + "_provider") || "groq",
                }),
            });

            if (!res.ok) return null;
            const result = await res.json();
            if (result.error) return null;

            // ── Store in cache ──────────────────────────────────────────
            if (_filterCache.size >= _CACHE_MAX) {
                // Evict the oldest entry
                _filterCache.delete(_filterCache.keys().next().value);
            }
            _filterCache.set(cacheKey, result.data);
            return result.data;

        } catch (err) {
            if (err.name === "AbortError") return null;   // intentionally cancelled
            console.error("Filter fetch failed:", err);
            return null;
        } finally {
            if (_inflightCtrls[idx] === ctrl) delete _inflightCtrls[idx];
        }
    }


    // ── Show / hide loading spinner on a chart ────────────────────────────
    function _showChartSpinner(idx) {
        const chartBody = document.getElementById(`chart-body-${idx}`);
        if (!chartBody || document.getElementById(`cfp-spinner-${idx}`)) return;
        const ov = document.createElement("div");
        ov.className = "chart-loading-overlay";
        ov.id = `cfp-spinner-${idx}`;
        ov.innerHTML = `<div class="chart-loading-spinner"></div>`;
        chartBody.appendChild(ov);
    }
    function _hideChartSpinner(idx) {
        document.getElementById(`cfp-spinner-${idx}`)?.remove();
    }

    // ── Open (or toggle-close) the filter panel for chart idx ────────────
    // Attaches to document.body with position:fixed to escape overflow:hidden
    const _applyDebounce = {};
    async function openChartFilterPanel(idx, anchorBtn) {
        // Toggle close if already open
        if (activePanelIdx === idx) { closeChartFilterPanel(idx); return; }
        if (activePanelIdx !== null) closeChartFilterPanel(activePanelIdx);
        activePanelIdx = idx;

        // Load filter options once from server
        if (!cachedFilterOptions || !Object.keys(cachedFilterOptions).length) {
            try {
                const res = await fetch("/report/filters");
                if (res.ok) cachedFilterOptions = await res.json();
            } catch (_) { }
        }

        const filters = chartFilters[idx] || {};
        const opts = cachedFilterOptions || {};
        const cfg = _getChartFilterConfig(idx);
        const spec = chartSpecs[idx] || {};
        const activePreset = filters._datePreset || "";

        // ── Build panel DOM ───────────────────────────────────────────────
        const panel = document.createElement("div");
        panel.className = "chart-filter-panel";
        panel.id = `cfp-${idx}`;
        panel.setAttribute("data-chart-idx", idx);
        panel.style.position = "fixed";
        panel.style.zIndex = "99999";

        // Sections builder
        let sections = "";

        // ── DATE RANGE section ────────────────────────────────────────────
        if (cfg.showDate) {
            sections += `
            <div class="cfp-section">
                <div class="cfp-label">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/>
                        <line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>
                    </svg>
                    Date Range
                </div>
                <div class="cfp-pills">
                    <button class="cfp-pill${activePreset === "MTD" ? " active" : ""}" data-preset="MTD">MTD</button>
                    <button class="cfp-pill${activePreset === "QTD" ? " active" : ""}" data-preset="QTD">QTD</button>
                    <button class="cfp-pill${activePreset === "YTD" ? " active" : ""}" data-preset="YTD">YTD</button>
                    <button class="cfp-pill${activePreset === "Custom" ? " active" : ""}" data-preset="Custom">Custom</button>
                </div>
                <div class="cfp-date-row" id="cfp-date-row-${idx}" style="${activePreset !== "Custom" ? "display:none" : ""}">
                    <input type="date" class="cfp-date-input" id="cfp-from-${idx}" value="${filters.date_from || ""}" placeholder="From">
                    <span class="cfp-date-sep">→</span>
                    <input type="date" class="cfp-date-input" id="cfp-to-${idx}" value="${filters.date_to || ""}" placeholder="To">
                </div>
            </div>`;
        }

        // ── CATEGORY section ──────────────────────────────────────────────
        if (cfg.showCategory) {
            sections += `
            <div class="cfp-section">
                <div class="cfp-label">Category</div>
                <select class="cfp-select" id="cfp-cat-${idx}">
                    <option value="">All Categories</option>
                    ${(opts.categories || []).map(c =>
                `<option value="${escapeAttr(c)}"${filters.category === c ? " selected" : ""}>${escapeHtml(c)}</option>`
            ).join("")}
                </select>
            </div>`;
        }

        // ── PRODUCT section ───────────────────────────────────────────────
        if (cfg.showProduct) {
            sections += `
            <div class="cfp-section">
                <div class="cfp-label">Product</div>
                <select class="cfp-select" id="cfp-prod-${idx}">
                    <option value="">All Products</option>
                    ${(opts.products || []).map(p =>
                `<option value="${escapeAttr(p)}"${filters.product === p ? " selected" : ""}>${escapeHtml(p)}</option>`
            ).join("")}
                </select>
            </div>`;
        }

        // ── STATUS section ────────────────────────────────────────────────
        if (cfg.showStatus) {
            sections += `
            <div class="cfp-section">
                <div class="cfp-label">Status</div>
                <select class="cfp-select" id="cfp-status-${idx}">
                    <option value="">All Statuses</option>
                    ${(opts.statuses || []).map(s =>
                `<option value="${escapeAttr(s)}"${filters.status === s ? " selected" : ""}>${escapeHtml(s)}</option>`
            ).join("")}
                </select>
            </div>`;
        }

        // ── TOP N section ─────────────────────────────────────────────────
        if (cfg.showTopN) {
            sections += `
            <div class="cfp-section">
                <div class="cfp-label">Show Top</div>
                <div class="cfp-chips">
                    <button class="cfp-chip${!filters.top_n ? " active" : ""}" data-topn="0">All</button>
                    ${cfg.topNOpts.map(n =>
                `<button class="cfp-chip${filters.top_n === n ? " active" : ""}" data-topn="${n}">Top ${n}</button>`
            ).join("")}
                </div>
            </div>`;
        }

        // Compare section removed — not needed

        panel.innerHTML = `
            <div class="cfp-header">
                <div class="cfp-title">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>
                    </svg>
                    Chart Filters
                    ${spec.title ? `<span style="font-weight:500;opacity:0.55;font-size:0.6rem">— ${escapeHtml(spec.title)}</span>` : ""}
                </div>
                <button class="cfp-close" title="Close">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                        <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                    </svg>
                </button>
            </div>
            <div class="cfp-body">${sections}</div>
            <div class="cfp-footer">
                <button class="cfp-apply-btn" id="cfp-apply-${idx}">
                    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12">
                        <polyline points="13 4 6 11 3 8"/>
                    </svg>
                    Apply Filters
                </button>
                <button class="cfp-reset-btn" id="cfp-reset-${idx}">Reset</button>
            </div>
        `;

        document.body.appendChild(panel);

        // ── Viewport-aware positioning ────────────────────────────────────
        function positionPanel() {
            if (window.innerWidth <= 600) return; // mobile: CSS handles it
            const rect = anchorBtn.getBoundingClientRect();
            const panelW = 320;
            const vw = window.innerWidth, vh = window.innerHeight;
            let left = rect.right - panelW;
            if (left < 8) left = 8;
            if (left + panelW > vw - 8) left = vw - panelW - 8;
            let top = rect.bottom + 8;
            const estH = Math.min(panel.scrollHeight || 480, vh - 80);
            if (top + estH > vh - 8) top = rect.top - estH - 8;
            if (top < 8) top = 8;
            panel.style.top = top + "px";
            panel.style.left = left + "px";
            panel.style.width = panelW + "px";
        }
        // Wait one frame so panel has rendered dimensions
        requestAnimationFrame(positionPanel);

        const _repos = () => positionPanel();
        window.addEventListener("scroll", _repos, true);
        window.addEventListener("resize", _repos);
        panel._cleanup = () => {
            window.removeEventListener("scroll", _repos, true);
            window.removeEventListener("resize", _repos);
        };

        // ── Wire events ───────────────────────────────────────────────────
        panel.querySelector(".cfp-close").addEventListener("click", e => {
            e.stopPropagation();
            closeChartFilterPanel(idx);
        });

        // Preset pills
        panel.querySelectorAll(".cfp-pill").forEach(pill => {
            pill.addEventListener("click", () => {
                panel.querySelectorAll(".cfp-pill").forEach(p => p.classList.remove("active"));
                pill.classList.add("active");
                const preset = pill.dataset.preset;
                const dateRow = document.getElementById(`cfp-date-row-${idx}`);
                if (preset === "Custom") {
                    if (dateRow) dateRow.style.display = "";
                } else {
                    if (dateRow) dateRow.style.display = "none";
                    const range = _dateQuickRange(preset);
                    if (range) {
                        const fromEl = document.getElementById(`cfp-from-${idx}`);
                        const toEl = document.getElementById(`cfp-to-${idx}`);
                        if (fromEl) fromEl.value = range.from;
                        if (toEl) toEl.value = range.to;
                    }
                }
            });
        });

        // Top N chips — single select
        panel.querySelectorAll("[data-topn]").forEach(btn => {
            btn.addEventListener("click", () => {
                panel.querySelectorAll("[data-topn]").forEach(b => b.classList.remove("active"));
                btn.classList.add("active");
            });
        });

        // Compare chips removed

        // Apply & Reset buttons
        document.getElementById(`cfp-apply-${idx}`).addEventListener("click", e => {
            e.stopPropagation();
            // Debounce: ignore if a filter is already being applied
            if (_applyDebounce[idx]) return;
            _applyDebounce[idx] = true;
            applyChartFilter(idx).finally(() => {
                setTimeout(() => { _applyDebounce[idx] = false; }, 500);
            });
        });
        document.getElementById(`cfp-reset-${idx}`).addEventListener("click", e => {
            e.stopPropagation();
            resetChartFilter(idx);
        });

        // Click-outside to close
        setTimeout(() => { document.addEventListener("click", _globalPanelClose); }, 80);
    }

    function _globalPanelClose(e) {
        if (activePanelIdx === null) return;
        const panel = document.getElementById(`cfp-${activePanelIdx}`);
        const btn = document.getElementById(`chart-filter-btn-${activePanelIdx}`);
        if (panel && !panel.contains(e.target) && e.target !== btn && !btn?.contains(e.target)) {
            closeChartFilterPanel(activePanelIdx);
        }
    }

    function closeChartFilterPanel(idx) {
        const panel = document.getElementById(`cfp-${idx}`);
        if (panel) { if (panel._cleanup) panel._cleanup(); panel.remove(); }
        if (activePanelIdx === idx) activePanelIdx = null;
        document.removeEventListener("click", _globalPanelClose);
    }

    // ── Apply the filter panel state to a specific chart ─────────────────
    async function applyChartFilter(idx) {
        const panel = document.getElementById(`cfp-${idx}`);
        if (!panel) return;

        const applyBtn = document.getElementById(`cfp-apply-${idx}`);
        if (applyBtn) { applyBtn.innerHTML = `<div class="chart-loading-spinner" style="width:14px;height:14px;border-width:2px"></div> Applying…`; applyBtn.disabled = true; }

        // ── Read panel state ──────────────────────────────────────────────
        const activePresetEl = panel.querySelector(".cfp-pill.active");
        const activePreset = activePresetEl ? activePresetEl.dataset.preset : "";

        const date_from = document.getElementById(`cfp-from-${idx}`)?.value || null;
        const date_to = document.getElementById(`cfp-to-${idx}`)?.value || null;
        const category = document.getElementById(`cfp-cat-${idx}`)?.value || null;
        const product = document.getElementById(`cfp-prod-${idx}`)?.value || null;
        const status = document.getElementById(`cfp-status-${idx}`)?.value || null;

        const activeTopNEl = panel.querySelector("[data-topn].active");
        const top_n = activeTopNEl ? (parseInt(activeTopNEl.dataset.topn, 10) || null) : null;

        // Save filter state
        chartFilters[idx] = {
            _datePreset: activePreset,
            date_from: activePreset !== "Custom" ? (date_from || null) : date_from,
            date_to: activePreset !== "Custom" ? (date_to || null) : date_to,
            category: category || null,
            product: product || null,
            status: status || null,
            top_n: top_n || null,
        };

        _showChartSpinner(idx);

        try {
            const spec = chartSpecs[idx];
            const filters = chartFilters[idx];
            const hasSQLFilter = !!(filters.date_from || filters.date_to || category || product || status);

            let newData = null;

            if (hasSQLFilter && spec && spec.sql) {
                // ── Server-side SQL + filter injection ────────────────────
                newData = await _fetchFilteredData(idx, filters);
                // Fallback: if fetch failed, keep original
                if (newData === null) newData = JSON.parse(JSON.stringify(chartOriginalData[idx] || []));
            } else {
                // ── Client-side only (Top N slicing) ─────────────────────
                newData = JSON.parse(JSON.stringify(chartOriginalData[idx] || []));
            }

            // Apply Top N client-side (always, after server data arrives too)
            if (top_n && top_n > 0 && newData && newData.length > 0) {
                const keys = Object.keys(newData[0]);
                if (keys.length >= 2) {
                    const valKey = keys[1];
                    try {
                        newData = newData
                            .slice()
                            .sort((a, b) => parseFloat(b[valKey] || 0) - parseFloat(a[valKey] || 0))
                            .slice(0, top_n);
                    } catch (_) { newData = newData.slice(0, top_n); }
                }
            }

            _hideChartSpinner(idx);
            renderChartOrNoData(idx, newData);

        } catch (err) {
            console.error("Chart filter apply failed:", err);
            _hideChartSpinner(idx);
        } finally {
            if (applyBtn) {
                applyBtn.innerHTML = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12"><polyline points="13 4 6 11 3 8"/></svg> Apply Filters`;
                applyBtn.disabled = false;
            }
        }

        updateChartFilterState(idx);
        closeChartFilterPanel(idx);
    }

    // ── Reset a chart's filters back to original data ────────────────────
    function resetChartFilter(idx) {
        chartFilters[idx] = {};
        const originalData = chartOriginalData[idx];
        renderChartOrNoData(idx, originalData ? JSON.parse(JSON.stringify(originalData)) : []);
        updateChartFilterState(idx);
        closeChartFilterPanel(idx);
    }

    // ── Update filter button state and badge strip ────────────────────────
    function updateChartFilterState(idx) {
        const filters = chartFilters[idx] || {};
        const btn = document.getElementById(`chart-filter-btn-${idx}`);
        const badgeContainer = document.getElementById(`chart-badges-${idx}`);

        const BADGE_DEFS = [
            { key: "_datePreset", label: f => f._datePreset ? `📅 ${f._datePreset}` : null },
            { key: "date_from", label: f => (f._datePreset === "Custom" && f.date_from) ? `From: ${f.date_from}` : null },
            { key: "date_to", label: f => (f._datePreset === "Custom" && f.date_to) ? `To: ${f.date_to}` : null },
            { key: "category", label: f => f.category ? `Cat: ${f.category}` : null },
            { key: "product", label: f => f.product ? `Prod: ${f.product.substring(0, 16)}${f.product.length > 16 ? "…" : ""}` : null },
            { key: "status", label: f => f.status ? `Status: ${f.status}` : null },
            { key: "top_n", label: f => f.top_n ? `Top ${f.top_n}` : null },
        ];

        const activeBadges = BADGE_DEFS.map(d => ({ key: d.key, label: d.label(filters) }))
            .filter(b => b.label !== null);
        const hasFilters = activeBadges.length > 0;

        if (btn) {
            btn.classList.toggle("has-filters", hasFilters);
            btn.title = hasFilters ? `${activeBadges.length} filter(s) active — click to edit` : "Chart Filters";
        }

        if (badgeContainer) {
            badgeContainer.innerHTML = activeBadges.map(b => `
                <span class="chart-filter-badge">
                    ${escapeHtml(b.label)}
                    <button class="chart-filter-badge-remove" data-chart-idx="${idx}" data-filter-key="${b.key}" title="Remove">×</button>
                </span>
            `).join("");

            badgeContainer.querySelectorAll(".chart-filter-badge-remove").forEach(rb => {
                rb.addEventListener("click", e => {
                    e.stopPropagation();
                    removeSingleChartFilter(idx, rb.dataset.filterKey);
                });
            });
        }
    }

    // ── Remove a single filter key and re-apply remaining ────────────────
    async function removeSingleChartFilter(idx, filterKey) {
        const filters = { ...(chartFilters[idx] || {}) };

        if (filterKey === "_datePreset") {
            delete filters._datePreset;
            delete filters.date_from;
            delete filters.date_to;
        } else if (filterKey === "date_from") {
            delete filters.date_from;
            if (filters._datePreset === "Custom" && !filters.date_to) delete filters._datePreset;
        } else if (filterKey === "date_to") {
            delete filters.date_to;
            if (filters._datePreset === "Custom" && !filters.date_from) delete filters._datePreset;
        } else {
            delete filters[filterKey];
        }

        chartFilters[idx] = filters;

        const spec = chartSpecs[idx];
        const originalData = chartOriginalData[idx];
        if (!spec || !originalData) { updateChartFilterState(idx); return; }

        const hasAny = Object.keys(filters).some(k => k !== "_datePreset" && filters[k]);
        let newData;

        if (hasAny && spec.sql) {
            _showChartSpinner(idx);
            try {
                newData = await _fetchFilteredData(idx, filters);
                if (newData === null) newData = JSON.parse(JSON.stringify(originalData));
            } catch (_) {
                newData = JSON.parse(JSON.stringify(originalData));
            } finally {
                _hideChartSpinner(idx);
            }
        } else {
            newData = JSON.parse(JSON.stringify(originalData));
        }

        // Re-apply top_n client-side
        if (filters.top_n && newData && newData.length > 0) {
            const keys = Object.keys(newData[0]);
            if (keys.length >= 2) {
                const vk = keys[1];
                try { newData = newData.slice().sort((a, b) => parseFloat(b[vk] || 0) - parseFloat(a[vk] || 0)).slice(0, filters.top_n); }
                catch (_) { newData = newData.slice(0, filters.top_n); }
            }
        }

        renderChartOrNoData(idx, newData);
        updateChartFilterState(idx);
    }
    // ── Chart Rendering ───────────────────────────────────────────────────
    function renderChart(canvas, chartSpec) {

        const data = chartSpec.data;
        if (!data || data.length === 0) return;

        const keys = Object.keys(data[0]);
        if (keys.length === 0) return;

        const labelKey = keys[0];
        const valueKeys = keys.length > 1 ? keys.slice(1) : [keys[0]];
        // Clean labels: replace underscores, truncate long strings for axis
        const MAX_LABEL = 18;
        const rawLabels = data.map(row => String(row[labelKey]));
        const fullLabels = rawLabels.map(l => l.replace(/_/g, ' '));  // clean, not truncated
        const labels = fullLabels.map(l =>
            l.length > MAX_LABEL ? l.slice(0, MAX_LABEL - 1) + '\u2026' : l
        );
        const defaults = getChartDefaults();
        const colors = getColors(chartSpec.color_scheme, Math.max(data.length, valueKeys.length));

        let chartType = (chartSpec.type || "bar").toLowerCase();
        let isHorizontal = false, isStacked = false, isArea = false;

        // Normalize chart type variants
        if (chartType === "horizontalbar") { chartType = "bar"; isHorizontal = true; }
        else if (chartType === "stackedbar") { chartType = "bar"; isStacked = true; }
        else if (chartType === "area") { chartType = "line"; isArea = true; }
        else if (chartType === "polararea") { chartType = "doughnut"; }
        else if (chartType === "radar") { chartType = "bar"; }
        // Default to bar for any unrecognized type
        else if (!["bar", "line", "pie", "doughnut", "scatter"].includes(chartType)) {
            chartType = "bar";
        }

        const datasets = valueKeys.map((key, i) => {
            const values = data.map(row => {
                const v = row[key];
                return v === null || v === undefined ? 0 : Number(v) || 0;
            });

            const color = colors[i % colors.length];
            const cfg = {
                label: formatColumnName(key),
                data: values,
                backgroundColor: color + ALPHA,
                borderColor: color,
                borderWidth: 2,
            };

            if (chartType === "line") {
                cfg.tension = 0.45;
                // Dynamic point size: hide dots when too many points
                const ptRadius = data.length > 50 ? 0 : data.length > 20 ? 2 : 4;
                const ptHover = data.length > 50 ? 5 : 7;
                cfg.pointRadius = ptRadius;
                cfg.pointHoverRadius = ptHover;
                cfg.pointBackgroundColor = color;
                cfg.pointBorderColor = "#fff";
                cfg.pointBorderWidth = ptRadius > 0 ? 2 : 0;
                cfg.borderWidth = data.length > 50 ? 2 : 2.5;
                if (isArea) {
                    cfg.fill = "origin";
                    cfg.backgroundColor = (ctx) => {
                        if (!ctx.chart.chartArea) return color + "18";
                        const { top, bottom } = ctx.chart.chartArea;
                        const gradient = ctx.chart.ctx.createLinearGradient(0, top, 0, bottom);
                        gradient.addColorStop(0, color + "55");
                        gradient.addColorStop(1, color + "04");
                        return gradient;
                    };
                } else {
                    cfg.backgroundColor = color + "18";
                }
            }

            if (chartType === "bar") {
                cfg.borderRadius = isHorizontal ? 5 : 8;
                cfg.borderSkipped = false;
                if (isStacked) {
                    // Stacked bars: solid semi-opaque fill per series
                    cfg.backgroundColor = color + "cc";
                    cfg.borderColor = color;
                    cfg.borderWidth = 0;
                    cfg.hoverBackgroundColor = color + "ee";
                } else {
                    // Single-series bars: clean solid fill, no heavy gradient
                    cfg.backgroundColor = color + "d8";
                    cfg.borderColor = color;
                    cfg.borderWidth = 0;
                    cfg.hoverBackgroundColor = color + "ff";
                }
            }

            if (isPieType(chartType)) {
                // Vivid opaque slices with a clean separator
                cfg.backgroundColor = colors.slice(0, values.length).map(c => c + "ee");
                cfg.borderColor = defaults.bgColor;
                cfg.borderWidth = 3;
                cfg.hoverBackgroundColor = colors.slice(0, values.length).map(c => c + "ff");
                cfg.hoverOffset = 12;
                cfg.hoverBorderWidth = 0;
            }

            if (chartType === "radar") {
                cfg.fill = true;
                cfg.backgroundColor = color + "33";
                cfg.pointBackgroundColor = color;
                cfg.pointBorderColor = "#fff";
                cfg.pointBorderWidth = 2;
            }

            if (chartType === "polararea") {
                chartType = "polarArea";
                cfg.backgroundColor = colors.slice(0, values.length).map(c => c + "88");
                cfg.borderColor = colors.slice(0, values.length);
                cfg.borderWidth = 2;
            }

            if (chartType === "scatter") {
                cfg.data = data.map(row => ({ x: Number(row[labelKey]) || 0, y: Number(row[key]) || 0 }));
                cfg.pointRadius = 5;
                cfg.pointHoverRadius = 8;
                cfg.pointBackgroundColor = color + "cc";
                cfg.pointBorderColor = color;
                cfg.pointBorderWidth = 2;
            }

            return cfg;
        });

        const config = {
            type: chartType,
            data: { labels: chartType === "scatter" ? undefined : labels, datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                devicePixelRatio: window.devicePixelRatio || 2,
                indexAxis: isHorizontal ? "y" : "x",
                interaction: {
                    intersect: (chartType === "line" || chartType === "area") ? false : true,
                    mode: (chartType === "line" || chartType === "area") ? "index" : "nearest",
                },
                plugins: {
                    legend: {
                        display: valueKeys.length > 1 || isPieType(chartType),
                        position: isPieType(chartType) ? "right" : "top",
                        labels: {
                            color: defaults.textColor,
                            font: { family: "'Inter', sans-serif", size: 11, weight: 500 },
                            padding: 10, usePointStyle: true, pointStyleWidth: 8,
                        },
                    },
                    tooltip: {
                        backgroundColor: defaults.bgColor === "#ffffff" ? "rgba(15,23,42,0.95)" : "rgba(255,255,255,0.95)",
                        titleColor: defaults.bgColor === "#ffffff" ? "#fff" : "#0f172a",
                        bodyColor: defaults.bgColor === "#ffffff" ? "#cbd5e1" : "#475569",
                        padding: 10, cornerRadius: 8, caretSize: 5,
                        titleFont: { family: "'Inter', sans-serif", size: 11, weight: 600 },
                        bodyFont: { family: "'Inter', sans-serif", size: 10 },
                        displayColors: true,
                        callbacks: {
                            title: function (items) {
                                if (!items.length) return '';
                                const i = items[0].dataIndex;
                                // Prefer full clean label over truncated axis label
                                return fullLabels[i] || items[0].label || '';
                            },
                            label: function (ctx) {
                                let val;
                                if (isPieType(chartType)) {
                                    // Pie/doughnut: raw value
                                    val = ctx.parsed;
                                } else if (isHorizontal) {
                                    // Horizontal bar: value is on x-axis
                                    val = ctx.parsed.x;
                                } else {
                                    // Standard bar/line/area: value is on y-axis
                                    val = ctx.parsed.y;
                                }
                                if (typeof val === "number") val = val.toLocaleString("en-IN");
                                const dsLabel = ctx.dataset.label || valueKeys[ctx.datasetIndex] || "Value";
                                return `${dsLabel}: ${val}`;
                            },
                        },
                    },
                },
                scales: {},
                animation: { duration: 800, easing: "easeOutQuart" },
                onHover: (event, elements) => {
                    event.native.target.style.cursor = elements.length ? "pointer" : "default";
                },
            },
        };

        if (!isPieType(chartType) && chartType !== "radar" && chartType !== "polarArea") {
            const numFmtCallback = val => {
                if (typeof val !== "number") return val;
                if (Math.abs(val) >= 10000000) return (val / 10000000).toFixed(1) + "Cr";
                if (Math.abs(val) >= 100000) return (val / 100000).toFixed(1) + "L";
                if (Math.abs(val) >= 1000) return (val / 1000).toFixed(1) + "K";
                return val;
            };
            // For horizontal bar: x=values (needs number fmt), y=labels (plain text)
            // For all others:     x=labels (plain text),     y=values (needs number fmt)
            const valueAxisKey = isHorizontal ? "x" : "y";
            const labelAxisKey = isHorizontal ? "y" : "x";
            config.options.scales = {
                [valueAxisKey]: {
                    grid: { color: defaults.gridColor, drawBorder: false },
                    ticks: {
                        color: defaults.textColor,
                        font: { family: "'Inter'", size: 11, weight: 500 },
                        callback: numFmtCallback,
                        maxTicksLimit: 8,
                    },
                    title: (isHorizontal ? chartSpec.x_label : chartSpec.y_label) ? {
                        display: true,
                        text: isHorizontal ? chartSpec.x_label : chartSpec.y_label,
                        color: defaults.textColor,
                        font: { family: "'Inter'", size: 11, weight: 700 },
                    } : undefined,
                    stacked: isStacked,
                    beginAtZero: true,
                },
                [labelAxisKey]: {
                    grid: { color: isHorizontal ? "transparent" : defaults.gridColor, drawBorder: false },
                    ticks: {
                        color: defaults.textColor,
                        font: { family: "'Inter'", size: isHorizontal ? 11 : 10, weight: 500 },
                        maxRotation: isHorizontal ? 0 : 35,
                        minRotation: isHorizontal ? 0 : 0,
                        autoSkip: true,
                        maxTicksLimit: isHorizontal ? 20 : 10,
                        callback: function (val, i) {
                            // Use cleaned label from our labels array
                            const lbl = this.getLabelForValue ? this.getLabelForValue(val) : labels[i] || val;
                            return lbl;
                        },
                    },
                    title: (isHorizontal ? chartSpec.y_label : chartSpec.x_label) ? {
                        display: true,
                        text: isHorizontal ? chartSpec.y_label : chartSpec.x_label,
                        color: defaults.textColor,
                        font: { family: "'Inter'", size: 11, weight: 700 },
                    } : undefined,
                    stacked: isStacked,
                },
            };
        }

        if (chartType === "radar") {
            config.options.scales = {
                r: {
                    angleLines: { color: defaults.gridColor },
                    grid: { color: defaults.gridColor },
                    ticks: { color: defaults.textColor, font: { size: 8 }, backdropColor: "transparent" },
                    pointLabels: { color: defaults.textColor, font: { family: "'Inter'", size: 9, weight: 500 } },
                },
            };
        }

        // Height: pie/donut gets more vertical room; wide cards more than regular
        const isWideCard = canvas.closest(".chart-full-width") !== null;
        const isPieChart = isPieType(chartType);
        canvas.parentElement.style.height = isPieChart
            ? (isWideCard ? "340px" : "300px")
            : (isWideCard ? "310px" : "270px");
        return new Chart(canvas, config);
    }


    function isPieType(type) { return ["pie", "doughnut"].includes(type); }
    function formatColumnName(name) { return name.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase()); }

    // ── KPI Value Formatting ──────────────────────────────────────────────
    function formatKPIValue(value, format) {
        if (value === null || value === undefined || value === "N/A") return "N/A";
        const num = Number(value);
        if (isNaN(num)) return String(value);

        if (format === "percent") return num.toFixed(1) + "%";

        if (format === "currency") {
            if (Math.abs(num) >= 10000000) return "₹" + (num / 10000000).toFixed(2) + " Cr";
            if (Math.abs(num) >= 100000) return "₹" + (num / 100000).toFixed(2) + " L";
            return "₹" + num.toLocaleString("en-IN", { maximumFractionDigits: 0 });
        }

        if (Math.abs(num) >= 10000000) return (num / 10000000).toFixed(2) + " Cr";
        if (Math.abs(num) >= 100000) return (num / 100000).toFixed(2) + " L";
        if (Number.isInteger(num)) return num.toLocaleString("en-IN");
        return num.toLocaleString("en-IN", { maximumFractionDigits: 2 });
    }

    // ── Table Builder ─────────────────────────────────────────────────────
    function buildTable(rows) {
        if (!rows || !rows.length) return '<p style="font-size:0.72rem;color:var(--text-muted);padding:0.5rem">No data.</p>';
        const cols = Object.keys(rows[0]);
        const display = rows.slice(0, 200);
        let html = "<table><thead><tr>";
        cols.forEach(c => { html += `<th>${escapeHtml(formatColumnName(c))}</th>`; });
        html += "</tr></thead><tbody>";
        display.forEach(row => {
            html += "<tr>";
            cols.forEach(c => {
                const v = row[c];
                let dv = v === null || v === undefined ? "—" : String(v);
                const nv = Number(v);
                if (!isNaN(nv) && String(v) === String(nv) && Math.abs(nv) >= 1000) {
                    dv = nv.toLocaleString("en-IN", { maximumFractionDigits: 2 });
                }
                html += `<td>${escapeHtml(dv)}</td>`;
            });
            html += "</tr>";
        });
        html += "</tbody></table>";
        return html;
    }

    // ── Explanation Modal ─────────────────────────────────────────────────
    const explainOverlay = document.getElementById("explainModalOverlay");
    const explainTitle = document.getElementById("explainModalTitle");
    const explainBody = document.getElementById("explainModalBody");
    const explainClose = document.getElementById("explainModalClose");

    function showExplanationModal(title, explanation) {
        explainTitle.textContent = title;

        const icons = {
            what: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="15" height="15"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`,
            how: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="15" height="15"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>`,
            insight: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="15" height="15"><path d="M12 2a7 7 0 017 7c0 2.38-1.19 4.47-3 5.74V17a1 1 0 01-1 1H9a1 1 0 01-1-1v-2.26C6.19 13.47 5 11.38 5 9a7 7 0 017-7z"/><line x1="9" y1="21" x2="15" y2="21"/></svg>`,
            type: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="15" height="15"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg>`,
        };

        const sections = [
            { key: "what", label: "What it shows", color: "#3b82f6" },
            { key: "how", label: "How it's built", color: "#8b5cf6" },
            { key: "insight", label: "Key insight", color: "#10b981" },
            { key: "type", label: "Chart type", color: "#f59e0b" },
        ];

        let bodyHtml = `<div style="display:flex;flex-direction:column;gap:1rem;padding:0.15rem 0">`;
        let hasContent = false;

        sections.forEach(sec => {
            const val = explanation[sec.key];
            if (!val) return;
            hasContent = true;
            bodyHtml += `
                <div style="display:flex;gap:0.8rem;align-items:flex-start">
                    <div style="flex-shrink:0;width:30px;height:30px;border-radius:8px;
                                background:${sec.color}18;color:${sec.color};
                                display:flex;align-items:center;justify-content:center;margin-top:2px">
                        ${icons[sec.key] || ""}
                    </div>
                    <div style="flex:1;min-width:0">
                        <div style="font-size:0.57rem;font-weight:800;text-transform:uppercase;
                                    letter-spacing:0.07em;color:${sec.color};margin-bottom:0.2rem">
                            ${sec.label}
                        </div>
                        <div style="font-size:0.815rem;line-height:1.7;color:var(--text-secondary)">
                            ${escapeHtml(val)}
                        </div>
                    </div>
                </div>`;
        });

        if (!hasContent) {
            // Plain fallback for bare string explanations
            const parts = [explanation.what, explanation.how, explanation.insight]
                .filter(Boolean).join(". ").replace(/\.\. */g, ". ").trim();
            bodyHtml += `<div style="font-size:0.82rem;line-height:1.75;color:var(--text-secondary)">${escapeHtml(parts || "No details available.")}</div>`;
        }

        bodyHtml += `</div>`;
        explainBody.innerHTML = bodyHtml;
        explainOverlay.classList.remove("hidden");
    }

    explainClose.addEventListener("click", () => explainOverlay.classList.add("hidden"));
    explainOverlay.addEventListener("click", e => { if (e.target === explainOverlay) explainOverlay.classList.add("hidden"); });

    // ── Thought Process Modal ─────────────────────────────────────────────
    const thoughtOverlay = document.getElementById("thoughtModalOverlay");
    const thoughtBody = document.getElementById("thoughtModalBody");
    const thoughtClose = document.getElementById("thoughtModalClose");
    const thoughtBtn = document.getElementById("reportThoughtBtn");

    thoughtBtn.addEventListener("click", () => {
        const meta = currentReport && currentReport.meta;
        const steps = (meta && meta.thought_process) || [];

        if (steps.length === 0) {
            thoughtBody.innerHTML = '<p style="color:var(--text-muted);font-size:0.82rem;">No reasoning data available.</p>';
        } else {
            let html = '<ul class="thought-steps">';
            steps.forEach((step, i) => {
                html += `<li class="thought-step"><span class="thought-step-num">${i + 1}</span><span class="thought-step-text">${escapeHtml(step)}</span></li>`;
            });
            html += "</ul>";
            thoughtBody.innerHTML = html;
        }
        thoughtOverlay.classList.remove("hidden");
    });

    thoughtClose.addEventListener("click", () => thoughtOverlay.classList.add("hidden"));
    thoughtOverlay.addEventListener("click", e => { if (e.target === thoughtOverlay) thoughtOverlay.classList.add("hidden"); });

    // ═══════════════════════════════════════════════════════════════════════
    //  EDIT MODE — DRAG & DROP, DELETE, RE-RENDER
    // ═══════════════════════════════════════════════════════════════════════

    function toggleEditMode() {
        editMode = !editMode;
        document.body.classList.toggle("edit-mode", editMode);

        const btn = document.getElementById("reportEditModeBtn");
        if (btn) {
            btn.classList.toggle("active", editMode);
            const editSvg = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>`;
            const doneSvg  = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px"><polyline points="20 6 9 17 4 12"/></svg>`;
            btn.innerHTML  = (editMode ? doneSvg + " Done Editing" : editSvg + " Edit Report");
        }

        const badge = document.getElementById("rcpEditBadge");
        if (badge) badge.style.display = editMode ? "" : "none";

        if (editMode) {
            initSortable();
            openChatPanel();
        } else {
            destroySortable();
        }
    }

    function initSortable() {
        if (!window.Sortable) return;
        const grid = document.getElementById("chartsGrid");
        if (!grid) return;
        destroySortable();
        sortableInstance = new Sortable(grid, {
            animation: 160,
            ghostClass: "sortable-ghost",
            chosenClass: "sortable-chosen",
            handle: ".chart-header",
            onEnd: () => {
                const cards = grid.querySelectorAll("[data-chart-idx]");
                const newOrder = [];
                cards.forEach(card => {
                    const idx = parseInt(card.dataset.chartIdx, 10);
                    const spec = chartSpecs[idx];
                    if (spec) newOrder.push(spec);
                });
                currentReport.charts = newOrder;
                _persistReport(currentReport);
            },
        });
    }

    function destroySortable() {
        if (sortableInstance) { sortableInstance.destroy(); sortableInstance = null; }
    }

    function _persistReport(report) {
        try {
            const stored = JSON.parse(localStorage.getItem(reportId) || "{}");
            stored.report = report;
            localStorage.setItem(reportId, JSON.stringify(stored));
        } catch (_) {}
    }

    function reRenderReport(newReport) {
        // Destroy Chart.js instances to free canvas
        Object.keys(chartInstances).forEach(k => {
            try { chartInstances[k].destroy(); } catch (_) {}
            delete chartInstances[k];
        });
        // Clear all per-chart state
        [chartSpecs, chartFilters, chartOriginalData, chartModHistory].forEach(obj => {
            Object.keys(obj).forEach(k => delete obj[k]);
        });
        destroySortable();

        currentReport = newReport;
        originalReport = JSON.parse(JSON.stringify(newReport));
        _persistReport(newReport);

        renderReport(newReport);
    }

    function deleteChart(idx) {
        const spec = chartSpecs[idx];
        if (!spec) return;
        const title = spec.title || `Chart ${idx + 1}`;
        if (!confirm(`Remove chart "${title}" from the report?`)) return;
        currentReport.charts = (currentReport.charts || []).filter(c => c.title !== spec.title);
        reRenderReport(currentReport);
        _addRcpMsg("assistant", `Removed chart "${title}".`);
    }

    function deleteKpi(kpiIdx) {
        const kpis = currentReport.kpis || [];
        if (kpiIdx < 0 || kpiIdx >= kpis.length) return;
        const kpi = kpis[kpiIdx];
        const title = kpi.label || kpi.title || kpi.id || `KPI ${kpiIdx + 1}`;
        if (!confirm(`Remove KPI "${title}" from the report?`)) return;
        currentReport.kpis = kpis.filter((_, i) => i !== kpiIdx);
        reRenderReport(currentReport);
        _addRcpMsg("assistant", `Removed KPI "${title}".`);
    }

    // ── Wire Edit Mode button ─────────────────────────────────────────────
    const reportEditModeBtn = document.getElementById("reportEditModeBtn");
    if (reportEditModeBtn) reportEditModeBtn.addEventListener("click", toggleEditMode);

    // ═══════════════════════════════════════════════════════════════════════
    //  GLOBAL REPORT CHAT PANEL
    // ═══════════════════════════════════════════════════════════════════════

    function openChatPanel() {
        const panel = document.getElementById("reportChatPanel");
        const fab   = document.getElementById("reportChatFab");
        if (panel) panel.classList.add("open");
        if (fab)   fab.classList.add("hidden");
    }

    function closeChatPanel() {
        const panel = document.getElementById("reportChatPanel");
        const fab   = document.getElementById("reportChatFab");
        if (panel) panel.classList.remove("open");
        if (fab)   fab.classList.remove("hidden");
    }

    function _addRcpMsg(role, text, clarifyOptions) {
        const historyEl = document.getElementById("rcpHistory");
        if (!historyEl) return;
        const welcome = document.getElementById("rcpWelcome");
        if (welcome) welcome.style.display = "none";

        const msgDiv = document.createElement("div");
        msgDiv.className = `rcp-msg ${role}`;
        const bubble = document.createElement("div");
        bubble.className = "rcp-msg-bubble";
        bubble.textContent = text;
        msgDiv.appendChild(bubble);
        historyEl.appendChild(msgDiv);

        if (clarifyOptions && clarifyOptions.length) {
            const wrap = document.createElement("div");
            wrap.className = "rcp-msg assistant";
            const optsDiv = document.createElement("div");
            optsDiv.className = "rcp-clarify-opts";
            clarifyOptions.forEach(opt => {
                const btn = document.createElement("button");
                btn.className = "rcp-clarify-opt";
                btn.textContent = opt;
                btn.addEventListener("click", () => sendReportEdit(opt));
                optsDiv.appendChild(btn);
            });
            wrap.appendChild(optsDiv);
            historyEl.appendChild(wrap);
        }
        historyEl.scrollTop = historyEl.scrollHeight;
    }

    const _RCP_SEND_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>`;

    async function sendReportEdit(overrideCommand) {
        const input   = document.getElementById("rcpInput");
        const sendBtn = document.getElementById("rcpSend");
        const cmd = overrideCommand || (input ? input.value.trim() : "");
        if (!cmd) return;

        if (input && !overrideCommand) input.value = "";
        hideMentionDropdown();
        _addRcpMsg("user", cmd);

        if (sendBtn) { sendBtn.disabled = true; sendBtn.innerHTML = `<div class="rcp-spinner"></div>`; }

        reportEditHistory.push(cmd);

        try {
            const res = await fetch("/report/chat-edit", {
                method:  "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    report:   currentReport,
                    command:  cmd,
                    history:  reportEditHistory.slice(-5),
                    provider: localStorage.getItem(reportId + "_provider") || "groq",
                }),
            });
            if (!res.ok) throw new Error(`Server error ${res.status}`);
            const result = await res.json();

            if (result.mode === "updated") {
                reRenderReport(result.report);
                _addRcpMsg("assistant", `✓ ${result.message || "Report updated."}`);
            } else if (result.mode === "clarify") {
                _addRcpMsg("assistant", result.message || "What did you mean?", result.options || []);
                reportEditHistory.pop();
            } else if (result.mode === "no_change") {
                _addRcpMsg("assistant", result.message || "No changes needed.");
            } else if (result.mode === "error") {
                _addRcpMsg("error", `Error: ${result.error}`);
            }
        } catch (err) {
            console.error("Report chat-edit failed:", err);
            _addRcpMsg("error", `Failed: ${err.message || "Unknown error"}`);
        }

        if (sendBtn) { sendBtn.disabled = false; sendBtn.innerHTML = _RCP_SEND_SVG; }
    }

    // ── @Mention autocomplete ─────────────────────────────────────────────

    function _getMentionTargets() {
        const charts = currentReport.charts || [];
        const kpis   = currentReport.kpis   || [];
        const out = [];
        charts.forEach(c => { if (c.title) out.push({ title: c.title, type: "chart" }); });
        kpis.forEach(k => {
            const t = k.label || k.title || k.id;
            if (t) out.push({ title: t, type: "kpi" });
        });
        return out;
    }

    function showMentionDropdown(query) {
        const dd = document.getElementById("rcpMentionDropdown");
        if (!dd) return;
        const all      = _getMentionTargets();
        const filtered = query
            ? all.filter(t => t.title.toLowerCase().includes(query.toLowerCase()))
            : all;
        if (!filtered.length) { dd.classList.add("hidden"); return; }

        dd.innerHTML = "";
        filtered.slice(0, 8).forEach(t => {
            const btn = document.createElement("button");
            btn.className = "rcp-mention-item";
            btn.innerHTML = `<span>${escapeHtml(t.title)}</span><span class="rcp-mention-item-type">${t.type}</span>`;
            btn.addEventListener("click", () => insertMention(t.title));
            dd.appendChild(btn);
        });
        dd.classList.remove("hidden");
    }

    function hideMentionDropdown() {
        const dd = document.getElementById("rcpMentionDropdown");
        if (dd) dd.classList.add("hidden");
    }

    function insertMention(title) {
        const input = document.getElementById("rcpInput");
        if (!input) return;
        const val    = input.value;
        const atIdx  = val.lastIndexOf("@");
        const slug   = title.replace(/\s+/g, "-");
        input.value  = atIdx !== -1
            ? val.substring(0, atIdx) + `@${slug} `
            : val + `@${slug} `;
        hideMentionDropdown();
        input.focus();
    }

    // ── Wire global chat panel buttons ────────────────────────────────────

    const rcpFab   = document.getElementById("reportChatFab");
    const rcpClose = document.getElementById("rcpClose");
    const rcpSend  = document.getElementById("rcpSend");
    const rcpInput = document.getElementById("rcpInput");

    if (rcpFab)   rcpFab.addEventListener("click", openChatPanel);
    if (rcpClose) rcpClose.addEventListener("click", closeChatPanel);
    if (rcpSend)  rcpSend.addEventListener("click", () => sendReportEdit());

    if (rcpInput) {
        rcpInput.addEventListener("input", () => {
            const m = rcpInput.value.match(/@([\w-]*)$/);
            m ? showMentionDropdown(m[1]) : hideMentionDropdown();
        });
        rcpInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                hideMentionDropdown();
                sendReportEdit();
            }
            if (e.key === "Escape") hideMentionDropdown();
        });
    }

    // Wire example buttons in chat panel
    document.querySelectorAll(".rcp-example").forEach(btn => {
        btn.addEventListener("click", () => {
            const text = btn.textContent.replace(/^"|"$/g, "").trim();
            openChatPanel();
            sendReportEdit(text);
        });
    });

    // ═══════════════════════════════════════════════════════════════════════
    //  AI CHART MODIFICATION
    // ═══════════════════════════════════════════════════════════════════════

    const _SEND_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>`;

    function toggleChartAiPanel(idx) {
        const panel = document.getElementById(`ai-panel-${idx}`);
        const btn   = document.getElementById(`chart-ai-btn-${idx}`);
        if (!panel) return;
        const isOpen = panel.classList.contains("open");
        if (isOpen) {
            panel.classList.remove("open");
            if (btn) btn.classList.remove("active");
        } else {
            panel.classList.add("open");
            if (btn) btn.classList.add("active");
            setTimeout(() => {
                const inp = document.getElementById(`ai-input-${idx}`);
                if (inp) inp.focus();
            }, 60);
        }
    }

    function _addAiMsg(idx, role, text, clarifyOptions) {
        const historyEl = document.getElementById(`ai-history-${idx}`);
        if (!historyEl) return;

        const msgDiv = document.createElement("div");
        msgDiv.className = `ai-msg ${role}`;

        const bubble = document.createElement("div");
        bubble.className = "ai-msg-bubble";
        bubble.textContent = text;
        msgDiv.appendChild(bubble);
        historyEl.appendChild(msgDiv);

        if (clarifyOptions && clarifyOptions.length) {
            const wrapDiv = document.createElement("div");
            wrapDiv.className = "ai-msg assistant";
            const optsDiv = document.createElement("div");
            optsDiv.className = "ai-clarify-options";
            clarifyOptions.forEach(opt => {
                const optBtn = document.createElement("button");
                optBtn.className = "ai-clarify-opt";
                optBtn.textContent = opt;
                optBtn.addEventListener("click", () => sendChartModification(idx, opt));
                optsDiv.appendChild(optBtn);
            });
            wrapDiv.appendChild(optsDiv);
            historyEl.appendChild(wrapDiv);
        }

        historyEl.scrollTop = historyEl.scrollHeight;
    }

    async function sendChartModification(idx, overrideInstruction) {
        const input   = document.getElementById(`ai-input-${idx}`);
        const sendBtn = document.getElementById(`ai-send-${idx}`);

        const instruction = overrideInstruction || (input ? input.value.trim() : "");
        if (!instruction) return;

        if (input && !overrideInstruction) input.value = "";

        _addAiMsg(idx, "user", instruction);

        if (sendBtn) {
            sendBtn.disabled = true;
            sendBtn.innerHTML = `<div class="ai-send-spinner"></div>`;
        }

        const spec = chartSpecs[idx];
        if (!spec) {
            _addAiMsg(idx, "assistant error", "Chart not found.");
            if (sendBtn) { sendBtn.disabled = false; sendBtn.innerHTML = _SEND_SVG; }
            return;
        }

        const chartForRequest = {
            id:      spec.id || "",
            title:   spec.title || "",
            type:    spec.type  || "bar",
            sql:     spec.sql   || "",
            x_label: spec.x_label || "",
            y_label: spec.y_label || "",
            data:    (spec.data || []).slice(0, 5),
        };

        const modHistory = chartModHistory[idx] || [];

        try {
            const res = await fetch("/report/modify-chart", {
                method:  "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    chart:       chartForRequest,
                    instruction: instruction,
                    history:     modHistory,
                    provider:    localStorage.getItem(reportId + "_provider") || "groq",
                }),
            });

            if (!res.ok) throw new Error(`Server error ${res.status}`);
            const result = await res.json();

            if (result.mode === "modified") {
                const updatedChart = result.chart || {};

                // SQL changed → new data returned; type/label-only → keep original
                const renderData = result.sql_changed && updatedChart.data && updatedChart.data.length
                    ? updatedChart.data
                    : (chartOriginalData[idx] || spec.data || []);

                chartSpecs[idx]        = { ...spec, ...updatedChart, data: renderData };
                if (result.sql_changed) chartOriginalData[idx] = renderData;
                chartFilters[idx]      = {};

                // Re-render with correct data
                renderChartOrNoData(idx, renderData);

                // Sync type badge
                const badgeEl = document.querySelector(`[data-chart-idx="${idx}"] .chart-type-badge`);
                if (badgeEl && updatedChart.type) {
                    const dt = (updatedChart.type)
                        .replace("horizontalBar", "H.BAR").replace("stackedBar", "STACKED")
                        .replace("doughnut", "DONUT").toUpperCase();
                    badgeEl.textContent = dt;
                }

                // Sync title in header
                const titleEl = document.querySelector(`[data-chart-idx="${idx}"] .chart-title`);
                if (titleEl && updatedChart.title) titleEl.textContent = updatedChart.title;

                // Record in context memory
                if (!chartModHistory[idx]) chartModHistory[idx] = [];
                chartModHistory[idx].push(instruction);

                _addAiMsg(idx, "assistant", `✓ ${result.explanation || "Chart updated."}`);

            } else if (result.mode === "clarify") {
                _addAiMsg(idx, "assistant", result.message || "What did you mean?", result.options || []);

            } else if (result.mode === "no_change") {
                _addAiMsg(idx, "assistant", result.message || "No changes needed.");

            } else if (result.mode === "error") {
                _addAiMsg(idx, "assistant error", `Error: ${result.error}`);

            } else {
                _addAiMsg(idx, "assistant error", "Unexpected response from server.");
            }

        } catch (err) {
            console.error("Chart modification failed:", err);
            _addAiMsg(idx, "assistant error", `Failed: ${err.message || "Unknown error"}`);
        }

        if (sendBtn) {
            sendBtn.disabled = false;
            sendBtn.innerHTML = _SEND_SVG;
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  EXPORT FUNCTIONS
    // ═══════════════════════════════════════════════════════════════════════

    function exportChartPNG(idx) {
        const canvas = document.getElementById(`chart_${idx}`);
        if (!canvas) { console.warn("Chart canvas not found for idx", idx); return; }
        const spec     = chartSpecs[idx] || {};
        const filename = (spec.title || `chart-${idx}`)
            .replace(/[^a-z0-9]+/gi, "-").toLowerCase() + ".png";
            
        const tempCanvas = document.createElement("canvas");
        tempCanvas.width = canvas.width;
        tempCanvas.height = canvas.height;
        const ctx = tempCanvas.getContext("2d");
        
        const isDark = document.documentElement.getAttribute("data-theme") === "dark";
        ctx.fillStyle = isDark ? "#161b22" : "#ffffff";
        ctx.fillRect(0, 0, tempCanvas.width, tempCanvas.height);
        ctx.drawImage(canvas, 0, 0);

        const link  = document.createElement("a");
        link.download = filename;
        link.href     = tempCanvas.toDataURL("image/png", 1.0);
        link.click();
    }

    function exportChartPDF(idx) {
        const canvas = document.getElementById(`chart_${idx}`);
        if (!canvas) { exportChartPNG(idx); return; }
        const spec = chartSpecs[idx] || {};

        try {
            if (!window.jspdf) throw new Error("jsPDF not loaded");
            const { jsPDF } = window.jspdf;
            const doc   = new jsPDF({ orientation: "landscape", unit: "mm", format: "a4" });
            const pageW = doc.internal.pageSize.getWidth();
            const pageH = doc.internal.pageSize.getHeight();

            // Header bar
            doc.setFillColor(99, 102, 241);
            doc.rect(0, 0, pageW, 10, "F");
            doc.setTextColor(255, 255, 255);
            doc.setFontSize(9); doc.setFont("helvetica", "bold");
            doc.text("AI Analytics Report", 8, 7);
            doc.setFontSize(8); doc.setFont("helvetica", "normal");
            doc.text(new Date().toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }), pageW - 8, 7, { align: "right" });

            // Chart title
            doc.setTextColor(15, 23, 42);
            doc.setFontSize(15); doc.setFont("helvetica", "bold");
            doc.text(spec.title || "Chart", 8, 21);

            // Axis labels
            const xLabel = spec.x_label || "";
            const yLabel = spec.y_label || "";
            if (xLabel || yLabel) {
                doc.setFontSize(8); doc.setFont("helvetica", "normal");
                doc.setTextColor(100, 116, 139);
                const lbl = [xLabel && `X: ${xLabel}`, yLabel && `Y: ${yLabel}`].filter(Boolean).join("  •  ");
                doc.text(lbl, 8, 27);
            }

            // Applied filters
            const filters = chartFilters[idx] || {};
            const fParts = [
                filters._datePreset && `Period: ${filters._datePreset}`,
                (!filters._datePreset && filters.date_from) && `From: ${filters.date_from}`,
                (!filters._datePreset && filters.date_to)   && `To: ${filters.date_to}`,
                filters.category && `Category: ${filters.category}`,
                filters.product  && `Product: ${filters.product}`,
                filters.status   && `Status: ${filters.status}`,
                filters.top_n    && `Top ${filters.top_n}`,
            ].filter(Boolean);
            if (fParts.length) {
                doc.setFontSize(7.5); doc.setFont("helvetica", "italic");
                doc.setTextColor(99, 102, 241);
                doc.text(`Filters: ${fParts.join(" | ")}`, 8, 32);
            }

            // Chart image
            const tempCanvas = document.createElement("canvas");
            tempCanvas.width = canvas.width;
            tempCanvas.height = canvas.height;
            const ctx = tempCanvas.getContext("2d");
            const isDark = document.documentElement.getAttribute("data-theme") === "dark";
            ctx.fillStyle = isDark ? "#161b22" : "#ffffff";
            ctx.fillRect(0, 0, tempCanvas.width, tempCanvas.height);
            ctx.drawImage(canvas, 0, 0);

            const imgData = tempCanvas.toDataURL("image/png", 1.0);
            const imgStartY = 36;
            const imgW  = pageW - 16;
            const maxH  = pageH - imgStartY - 28;
            const imgH  = Math.min(imgW * (canvas.height / canvas.width), maxH);
            doc.addImage(imgData, "PNG", 8, imgStartY, imgW, imgH);

            // Insight
            const insight = spec.chart_insight || (spec.explanation && spec.explanation.insight) || "";
            if (insight) {
                const insY = imgStartY + imgH + 5;
                if (insY < pageH - 16) {
                    doc.setFontSize(8.5); doc.setFont("helvetica", "italic");
                    doc.setTextColor(71, 85, 105);
                    const lines = doc.splitTextToSize(`💡 ${insight}`, pageW - 16);
                    doc.text(lines.slice(0, 2), 8, insY);
                }
            }

            // SQL (tiny, footer)
            if (spec.sql) {
                doc.setFontSize(6); doc.setFont("courier", "normal");
                doc.setTextColor(148, 163, 184);
                const sqlLines = doc.splitTextToSize(spec.sql.replace(/\s+/g, " "), pageW - 16);
                doc.text(sqlLines.slice(0, 2), 8, pageH - 6);
            }

            const filename = (spec.title || `chart-${idx}`)
                .replace(/[^a-z0-9]+/gi, "-").toLowerCase() + ".pdf";
            doc.save(filename);

        } catch (err) {
            console.error("PDF export failed, falling back to PNG:", err);
            exportChartPNG(idx);
        }
    }

    function exportFullReportPDF() {
        window.print();
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  SQL MODAL
    // ═══════════════════════════════════════════════════════════════════════

    function showChartSql(idx) {
        const spec = chartSpecs[idx];
        if (!spec || !spec.sql) { alert("No SQL available for this chart."); return; }
        showSqlModal(spec.title || `Chart ${idx + 1}`, spec.sql);
    }

    function showSqlModal(title, sql) {
        const overlay  = document.getElementById("sqlModalOverlay");
        const titleEl  = document.getElementById("sqlModalTitle");
        const codeEl   = document.getElementById("sqlModalCode");
        if (!overlay || !titleEl || !codeEl) return;
        titleEl.textContent = `SQL — ${title}`;
        codeEl.textContent  = sql;
        overlay.classList.remove("hidden");
    }

    // SQL modal close
    const sqlModalOverlay = document.getElementById("sqlModalOverlay");
    const sqlModalClose   = document.getElementById("sqlModalClose");
    const sqlCopyBtn      = document.getElementById("sqlCopyBtn");
    if (sqlModalClose) {
        sqlModalClose.addEventListener("click", () => sqlModalOverlay.classList.add("hidden"));
    }
    if (sqlModalOverlay) {
        sqlModalOverlay.addEventListener("click", e => {
            if (e.target === sqlModalOverlay) sqlModalOverlay.classList.add("hidden");
        });
    }
    if (sqlCopyBtn) {
        sqlCopyBtn.addEventListener("click", () => {
            const codeEl = document.getElementById("sqlModalCode");
            if (!codeEl) return;
            navigator.clipboard.writeText(codeEl.textContent).then(() => {
                sqlCopyBtn.textContent = "Copied!";
                setTimeout(() => { sqlCopyBtn.textContent = "Copy SQL"; }, 1800);
            });
        });
    }

    // ── Export PDF topbar button ──────────────────────────────────────────
    const exportPdfBtn = document.getElementById("reportExportPdfBtn");
    if (exportPdfBtn) {
        exportPdfBtn.addEventListener("click", exportFullReportPDF);
    }

    // ── Close export menus on outside click ──────────────────────────────
    document.addEventListener("click", () => {
        document.querySelectorAll(".chart-export-menu.open").forEach(m => m.classList.remove("open"));
    });

    // ── Utilities ─────────────────────────────────────────────────────────
    function escapeHtml(str) {
        const d = document.createElement("div");
        d.appendChild(document.createTextNode(String(str)));
        return d.innerHTML;
    }
    function escapeAttr(str) {
        return str.replace(/&/g, "&amp;").replace(/'/g, "&#39;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    // ── Initialize ────────────────────────────────────────────────────────
    renderReport(currentReport);

})();
