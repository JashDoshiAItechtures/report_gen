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
    const chartInstances = {};     // {idx: Chart.js instance}
    let cachedFilterOptions = {};  // from /report/filters, loaded once
    let activePanelIdx = null;     // which panel is currently open



    // ── Color Palettes ────────────────────────────────────────────────────
    const PALETTES = {
        blues:   ["#3b82f6","#2563eb","#1d4ed8","#60a5fa","#93c5fd","#1e40af"],
        greens:  ["#10b981","#059669","#047857","#34d399","#6ee7b7","#065f46"],
        purples: ["#8b5cf6","#7c3aed","#6d28d9","#a78bfa","#c4b5fd","#5b21b6"],
        oranges: ["#f59e0b","#d97706","#b45309","#fbbf24","#fcd34d","#92400e"],
        mixed:   ["#10b981","#3b82f6","#8b5cf6","#f59e0b","#f43f5e","#06b6d4","#6366f1","#ec4899","#14b8a6","#a855f7","#eab308","#ef4444","#22c55e","#0ea5e9","#d946ef"],
        gradient:["#6366f1","#8b5cf6","#a855f7","#c084fc","#d8b4fe","#7c3aed"],
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

    // Force crisp rendering on Retina / high-DPI screens
    if (typeof Chart !== "undefined") {
        Chart.defaults.devicePixelRatio = window.devicePixelRatio || 2;
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

            kpis.forEach(kpi => {
                const val = formatKPIValue(kpi.value, kpi.format);
                const hasExplanation = kpi.explanation && (kpi.explanation.what || kpi.explanation.how);
                html += `<div class="kpi-card">
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
            <div class="charts-grid stream-section">`;

            // Pre-compute which charts are naturally wide (line/area/stackedBar)
            const naturallyWide = charts.map(c =>
                ["line", "area", "stackedbar"].includes((c.type || "bar").toLowerCase())
            );

            const shouldBeWide = [...naturallyWide];
            let col = 0;
            for (let i = 0; i < charts.length; i++) {
                if (shouldBeWide[i]) {
                    col = 0;
                } else {
                    if (col === 0) {
                        const nextWide = (i + 1 >= charts.length) || shouldBeWide[i + 1];
                        if (nextWide) { shouldBeWide[i] = true; col = 0; }
                        else { col = 1; }
                    } else { col = 0; }
                }
            }

            charts.forEach((chart, idx) => {
                const isWide = shouldBeWide[idx];
                const chartTypeBadge = (chart.type || "bar")
                    .replace("horizontalBar","H.BAR").replace("stackedBar","STACKED")
                    .replace("doughnut","DONUT").toUpperCase();
                const chartInsight = chart.chart_insight || (chart.explanation && chart.explanation.insight) || "";

                const expl = {
                    what:    (chart.explanation && chart.explanation.what)    || chart.title || "",
                    how:     (chart.explanation && chart.explanation.how)     || `${chartTypeBadge} chart — data grouped and aggregated from the database.`,
                    insight: (chart.explanation && chart.explanation.insight) || chart.chart_insight || "",
                    type:    chart.type || "bar",
                };

                const funnelSvg = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>`;

                html += `<div class="chart-card${isWide ? " chart-full-width" : ""}" data-chart-idx="${idx}" style="position:relative;">
                    <div class="chart-header" id="chart-header-${idx}">
                        <span class="chart-title">${escapeHtml(chart.title || "Chart " + (idx + 1))}</span>
                        <div class="chart-header-right">
                            <span class="chart-type-badge">${chartTypeBadge}</span>
                            <button class="chart-filter-btn" id="chart-filter-btn-${idx}" title="Chart Filters" data-chart-idx="${idx}">
                                ${funnelSvg}
                            </button>
                            <button class="kpi-eye-btn" data-explain='${escapeAttr(JSON.stringify(expl))}' data-title="${escapeAttr(chart.title || "Chart")}">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
                            </button>
                        </div>
                        <div class="chart-filter-badges" id="chart-badges-${idx}"></div>
                    </div>
                    <div class="chart-body" id="chart-body-${idx}">
                        <canvas id="chart_${idx}"></canvas>
                    </div>
                    ${chartInsight ? `<div class="chart-insight-text">&#x1F4A1; ${escapeHtml(chartInsight)}</div>` : ""}
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
                positive:    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg>`,
                negative:    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 17 13.5 8.5 8.5 13.5 2 7"/><polyline points="16 17 22 17 22 11"/></svg>`,
                warning:     `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
                opportunity: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`,
                neutral:     `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>`,
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
                } catch (_) {}

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
            } catch (_) {}
            renderReport(currentReport);
        }

        // Re-load date range defaults
        loadFilterOptions();
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  PER-CHART LOCAL FILTER SYSTEM
    // ═══════════════════════════════════════════════════════════════════════

    // ── Date range quick-pill helpers ─────────────────────────────────────
    function _dateQuickRange(preset) {
        const today = new Date();
        const y = today.getFullYear();
        const m = today.getMonth(); // 0-based
        const d = today.getDate();

        let from, to;
        to = today.toISOString().slice(0, 10);

        if (preset === "MTD") {
            from = new Date(y, m, 1).toISOString().slice(0, 10);
        } else if (preset === "QTD") {
            const qStart = Math.floor(m / 3) * 3;
            from = new Date(y, qStart, 1).toISOString().slice(0, 10);
        } else if (preset === "YTD") {
            from = new Date(y, 0, 1).toISOString().slice(0, 10);
        } else {
            return null; // Custom — don't auto-fill
        }
        return { from, to };
    }

    // ── Open (or close) the filter panel for a chart ──────────────────────
    // Panel attaches to document.body with position:fixed to escape
    // .charts-grid's overflow:hidden clipping.
    async function openChartFilterPanel(idx, anchorBtn) {
        // Close any other open panel
        if (activePanelIdx !== null && activePanelIdx !== idx) {
            closeChartFilterPanel(activePanelIdx);
        }

        const existingPanel = document.getElementById(`cfp-${idx}`);
        if (existingPanel) {
            closeChartFilterPanel(idx);
            return;
        }

        activePanelIdx = idx;

        // ── Ensure filter options are loaded ──────────────────────────────
        if (!cachedFilterOptions || !Object.keys(cachedFilterOptions).length) {
            try {
                const res = await fetch("/report/filters");
                if (res.ok) cachedFilterOptions = await res.json();
            } catch (_) {}
        }

        const filters = chartFilters[idx] || {};
        const opts = cachedFilterOptions || {};
        const activePreset = filters._datePreset || "";

        // ── Build panel ───────────────────────────────────────────────────
        const panel = document.createElement("div");
        panel.className = "chart-filter-panel";
        panel.id = `cfp-${idx}`;
        panel.setAttribute("data-chart-idx", idx);
        panel.style.position = "fixed";
        panel.style.zIndex   = "99999";

        // ── Determine which filters are relevant for THIS chart ──────────
        const spec = chartSpecs[idx] || {};
        const sql  = (spec.sql || "").toLowerCase();
        const cType = (spec.type || "bar").toLowerCase();
        const colNames = (spec.data && spec.data[0]) ? Object.keys(spec.data[0]).map(c => c.toLowerCase()) : [];
        const chartTitle = (spec.title || "").toLowerCase();

        // Date Range: show for time-series OR queries touching sales_order / order_date
        const isTimeSeries = ["line", "area"].includes(cType);
        const hasDateCol = colNames.some(c => /date|month|year|quarter|week|period/.test(c));
        const sqlHasDate = /order_date|sales_order/.test(sql);
        const showDate = isTimeSeries || hasDateCol || sqlHasDate;

        // Category: show only if SQL or columns reference category
        const sqlHasCategory = /category|product_master/.test(sql);
        const colHasCategory = colNames.some(c => /category|type/.test(c));
        const showCategory = (sqlHasCategory || colHasCategory) && opts.categories && opts.categories.length;

        // Product: show only if SQL or columns reference product
        const sqlHasProduct = /product_name|product_master/.test(sql);
        const colHasProduct = colNames.some(c => /product/.test(c));
        const showProduct = (sqlHasProduct || colHasProduct) && opts.products && opts.products.length;

        // Status: show only if SQL or columns reference status
        const sqlHasStatus = /\.status\b/.test(sql);
        const colHasStatus = colNames.some(c => /status/.test(c));
        const showStatus = (sqlHasStatus || colHasStatus) && opts.statuses && opts.statuses.length;

        // Top N: show for ranking charts (bar, horizontalBar, doughnut, pie), NOT for time-series
        const showTopN = !isTimeSeries && ["bar", "horizontalbar", "doughnut", "pie", "radar"].includes(cType);

        // Compare (vs LM/LY): show for time-series or date-based charts
        const showCompare = showDate;

        // ── Build only the relevant filter sections ──────────────────────
        let panelBody = "";

        // Date Range
        if (showDate) {
            panelBody += `
            <div class="cfp-row">
                <div class="cfp-row-label">Date Range</div>
                <div class="cfp-pills">
                    <button class="cfp-pill${activePreset==="MTD"?" active":""}" data-preset="MTD">MTD</button>
                    <button class="cfp-pill${activePreset==="QTD"?" active":""}" data-preset="QTD">QTD</button>
                    <button class="cfp-pill${activePreset==="YTD"?" active":""}" data-preset="YTD">YTD</button>
                    <button class="cfp-pill${activePreset==="Custom"?" active":""}" data-preset="Custom">Custom</button>
                </div>
                <div class="cfp-date-range" id="cfp-custom-date-${idx}" style="${activePreset!=="Custom"?"display:none":"display:flex"}">
                    <input type="date" class="cfp-date-input" id="cfp-from-${idx}" value="${filters.date_from||""}" placeholder="From">
                    <span class="cfp-date-sep">–</span>
                    <input type="date" class="cfp-date-input" id="cfp-to-${idx}" value="${filters.date_to||""}" placeholder="To">
                </div>
            </div>`;
        }

        // Category
        if (showCategory) {
            panelBody += `
            <div class="cfp-row">
                <div class="cfp-row-label">Category</div>
                <select class="cfp-select" id="cfp-cat-${idx}">
                    <option value="">All Categories</option>
                    ${(opts.categories||[]).map(c => `<option value="${escapeAttr(c)}"${filters.category===c?" selected":""}>${escapeHtml(c)}</option>`).join("")}
                </select>
            </div>`;
        }

        // Product
        if (showProduct) {
            panelBody += `
            <div class="cfp-row">
                <div class="cfp-row-label">Product</div>
                <select class="cfp-select" id="cfp-prod-${idx}">
                    <option value="">All Products</option>
                    ${(opts.products||[]).map(p => `<option value="${escapeAttr(p)}"${filters.product===p?" selected":""}>${escapeHtml(p)}</option>`).join("")}
                </select>
            </div>`;
        }

        // Status
        if (showStatus) {
            panelBody += `
            <div class="cfp-row">
                <div class="cfp-row-label">Status</div>
                <select class="cfp-select" id="cfp-status-${idx}">
                    <option value="">All Statuses</option>
                    ${(opts.statuses||[]).map(s => `<option value="${escapeAttr(s)}"${filters.status===s?" selected":""}>${escapeHtml(s)}</option>`).join("")}
                </select>
            </div>`;
        }

        // Top N
        if (showTopN) {
            panelBody += `
            <div class="cfp-row">
                <div class="cfp-row-label">Show Top</div>
                <div class="cfp-toggle-row">
                    <button class="cfp-toggle${!filters.top_n?" active":""}" data-topn="0">All</button>
                    <button class="cfp-toggle${filters.top_n===5?" active":""}" data-topn="5">Top 5</button>
                    <button class="cfp-toggle${filters.top_n===10?" active":""}" data-topn="10">Top 10</button>
                </div>
            </div>`;
        }

        // Compare
        if (showCompare) {
            panelBody += `
            <div class="cfp-row">
                <div class="cfp-row-label">Compare</div>
                <div class="cfp-toggle-row">
                    <button class="cfp-toggle${filters.compare_lm?" active":""}" data-compare="lm">vs Last Month</button>
                    <button class="cfp-toggle${filters.compare_ly?" active":""}" data-compare="ly">vs Last Year</button>
                </div>
            </div>`;
        }

        panel.innerHTML = `
            <div class="cfp-header">
                <div class="cfp-title">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>
                    Chart Filters
                </div>
                <button class="cfp-close" title="Close">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
            </div>
            ${panelBody}
            <div class="cfp-footer">
                <button class="cfp-apply-btn" id="cfp-apply-${idx}">Apply</button>
                <button class="cfp-reset-btn" id="cfp-reset-${idx}">Reset</button>
            </div>
        `;


        // Append to body — escapes overflow:hidden on .charts-grid
        document.body.appendChild(panel);

        // ── Position: anchor below the filter button (viewport-relative) ──
        function positionPanel() {
            const rect   = anchorBtn.getBoundingClientRect();
            const panelW = 292;
            const vw     = window.innerWidth;
            const vh     = window.innerHeight;

            let left = rect.right - panelW;
            if (left < 8)       left = 8;
            if (left + panelW > vw - 8) left = vw - panelW - 8;

            let top = rect.bottom + 6;
            // If the panel would go off the bottom, show above the button
            const estPanelH = 420;  // approximate panel height
            if (top + estPanelH > vh - 8) top = rect.top - estPanelH - 6;
            if (top < 8) top = 8;

            panel.style.top   = top     + "px";
            panel.style.left  = left    + "px";
            panel.style.right = "auto";
            panel.style.width = panelW  + "px";
        }
        positionPanel();

        // Reposition on scroll/resize
        const _reposition = () => positionPanel();
        window.addEventListener("scroll", _reposition, true);
        window.addEventListener("resize", _reposition);
        panel._cleanup = () => {
            window.removeEventListener("scroll", _reposition, true);
            window.removeEventListener("resize", _reposition);
        };

        // ── Wire panel events ─────────────────────────────────────────────

        // Close
        panel.querySelector(".cfp-close").addEventListener("click", (e) => {
            e.stopPropagation();
            closeChartFilterPanel(idx);
        });

        // Date preset pills
        panel.querySelectorAll(".cfp-pill").forEach(pill => {
            pill.addEventListener("click", () => {
                panel.querySelectorAll(".cfp-pill").forEach(p => p.classList.remove("active"));
                pill.classList.add("active");
                const preset = pill.dataset.preset;
                const customRow = document.getElementById(`cfp-custom-date-${idx}`);
                if (preset === "Custom") {
                    if (customRow) customRow.style.display = "flex";
                } else {
                    if (customRow) customRow.style.display = "none";
                    const range = _dateQuickRange(preset);
                    if (range) {
                        const fromEl = document.getElementById(`cfp-from-${idx}`);
                        const toEl   = document.getElementById(`cfp-to-${idx}`);
                        if (fromEl) fromEl.value = range.from;
                        if (toEl)   toEl.value   = range.to;
                    }
                }
            });
        });

        // Top-N toggles (single-select)
        panel.querySelectorAll("[data-topn]").forEach(btn => {
            btn.addEventListener("click", () => {
                panel.querySelectorAll("[data-topn]").forEach(b => b.classList.remove("active"));
                btn.classList.add("active");
            });
        });

        // Compare toggles (multi-select)
        panel.querySelectorAll("[data-compare]").forEach(btn => {
            btn.addEventListener("click", () => btn.classList.toggle("active"));
        });

        // Apply & Reset
        document.getElementById(`cfp-apply-${idx}`).addEventListener("click", (e) => { e.stopPropagation(); applyChartFilter(idx); });
        document.getElementById(`cfp-reset-${idx}`).addEventListener("click", (e) => { e.stopPropagation(); resetChartFilter(idx); });

        // Click-outside to close
        setTimeout(() => {
            document.addEventListener("click", _globalPanelClose);
        }, 60);
    }

    function _globalPanelClose(e) {
        if (activePanelIdx === null) return;
        const panel = document.getElementById(`cfp-${activePanelIdx}`);
        const btn   = document.getElementById(`chart-filter-btn-${activePanelIdx}`);
        if (panel && !panel.contains(e.target) && e.target !== btn && !btn?.contains(e.target)) {
            closeChartFilterPanel(activePanelIdx);
        }
    }

    function closeChartFilterPanel(idx) {
        const panel = document.getElementById(`cfp-${idx}`);
        if (panel) {
            if (panel._cleanup) panel._cleanup();
            panel.remove();
        }
        activePanelIdx = null;
        document.removeEventListener("click", _globalPanelClose);
    }




    // ── Apply the filter panel state to a specific chart ─────────────────
    async function applyChartFilter(idx) {

        const panel = document.getElementById(`cfp-${idx}`);
        if (!panel) return;

        const applyBtn = document.getElementById(`cfp-apply-${idx}`);
        if (applyBtn) { applyBtn.textContent = "Loading…"; applyBtn.disabled = true; }

        // Read current panel values
        const activePresetEl = panel.querySelector(".cfp-pill.active");
        const activePreset = activePresetEl ? activePresetEl.dataset.preset : "";

        const date_from = document.getElementById(`cfp-from-${idx}`)?.value || null;
        const date_to   = document.getElementById(`cfp-to-${idx}`)?.value   || null;
        const category  = document.getElementById(`cfp-cat-${idx}`)?.value   || null;
        const product   = document.getElementById(`cfp-prod-${idx}`)?.value  || null;
        const status    = document.getElementById(`cfp-status-${idx}`)?.value || null;

        const activeTopNEl = panel.querySelector("[data-topn].active");
        const top_n = activeTopNEl ? parseInt(activeTopNEl.dataset.topn, 10) || null : null;

        const compare_lm = panel.querySelector("[data-compare='lm']")?.classList.contains("active") || false;
        const compare_ly = panel.querySelector("[data-compare='ly']")?.classList.contains("active") || false;

        // Save filter state for badge rendering
        chartFilters[idx] = {
            _datePreset: activePreset,
            date_from: activePreset !== "Custom" ? (date_from || null) : date_from,
            date_to:   activePreset !== "Custom" ? (date_to   || null) : date_to,
            category:  category  || null,
            product:   product   || null,
            status:    status    || null,
            top_n:     top_n     || null,
            compare_lm,
            compare_ly,
        };

        const spec = chartSpecs[idx];
        const hasSQLFilter = date_from || date_to || category || product || status || compare_lm || compare_ly;

        // Show spinner in chart body
        const chartBody = document.getElementById(`chart-body-${idx}`);
        if (chartBody) {
            const overlay = document.createElement("div");
            overlay.className = "chart-loading-overlay";
            overlay.id = `cfp-spinner-${idx}`;
            overlay.innerHTML = `<div class="chart-loading-spinner"></div>`;
            chartBody.appendChild(overlay);
        }

        try {
            let newData = null;

            if (hasSQLFilter && spec && spec.sql) {
                // ── Server-side SQL filter ────────────────────────────────
                const res = await fetch("/report/apply-chart-filter", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        sql: spec.sql,
                        date_from: date_from || null,
                        date_to:   date_to   || null,
                        category:  category  || null,
                        product:   product   || null,
                        status:    status    || null,
                        top_n:     top_n     || null,
                        compare_lm,
                        compare_ly,
                        provider: localStorage.getItem(reportId + "_provider") || "groq",
                    }),
                });
                if (res.ok) {
                    const result = await res.json();
                    if (!result.error) {
                        newData = result.data;
                    } else {
                        console.warn(`Chart ${idx} filter error:`, result.error);
                    }
                }
            } else {
                // ── Client-side only (Top N) ──────────────────────────────
                newData = JSON.parse(JSON.stringify(chartOriginalData[idx] || []));
                if (top_n && newData.length) {
                    const keys = Object.keys(newData[0]);
                    if (keys.length >= 2) {
                        const valKey = keys[1];
                        try {
                            newData = newData.sort((a, b) => (parseFloat(b[valKey]||0) - parseFloat(a[valKey]||0))).slice(0, top_n);
                        } catch(_) { newData = newData.slice(0, top_n); }
                    }
                }
            }

            if (newData !== null) {
                // Destroy old chart instance
                if (chartInstances[idx]) {
                    try { chartInstances[idx].destroy(); } catch(_) {}
                    delete chartInstances[idx];
                }
                // Re-create canvas (chart.js needs a fresh canvas after destroy)
                if (chartBody) {
                    const oldCanvas = document.getElementById(`chart_${idx}`);
                    if (oldCanvas) oldCanvas.remove();
                    const newCanvas = document.createElement("canvas");
                    newCanvas.id = `chart_${idx}`;
                    chartBody.appendChild(newCanvas);

                    const updatedSpec = { ...spec, data: newData };
                    try {
                        const inst = renderChart(newCanvas, updatedSpec);
                        if (inst) chartInstances[idx] = inst;
                    } catch (err) {
                        console.error("Chart re-render failed:", err);
                    }
                }
            }
        } catch (err) {
            console.error("Chart filter apply failed:", err);
        }

        // Remove spinner
        const spinner = document.getElementById(`cfp-spinner-${idx}`);
        if (spinner) spinner.remove();

        if (applyBtn) { applyBtn.textContent = "Apply"; applyBtn.disabled = false; }

        // Update filter button appearance and badges
        updateChartFilterState(idx);

        // Close panel
        closeChartFilterPanel(idx);
    }

    // ── Reset a chart's filters to original data ──────────────────────────
    function resetChartFilter(idx) {
        chartFilters[idx] = {};

        const spec = chartSpecs[idx];
        const originalData = chartOriginalData[idx];
        if (!spec || !originalData) { closeChartFilterPanel(idx); return; }

        // Destroy old instance
        if (chartInstances[idx]) {
            try { chartInstances[idx].destroy(); } catch(_) {}
            delete chartInstances[idx];
        }

        // Re-create canvas
        const chartBody = document.getElementById(`chart-body-${idx}`);
        if (chartBody) {
            const oldCanvas = document.getElementById(`chart_${idx}`);
            if (oldCanvas) oldCanvas.remove();
            const newCanvas = document.createElement("canvas");
            newCanvas.id = `chart_${idx}`;
            chartBody.appendChild(newCanvas);

            const resetSpec = { ...spec, data: JSON.parse(JSON.stringify(originalData)) };
            try {
                const inst = renderChart(newCanvas, resetSpec);
                if (inst) chartInstances[idx] = inst;
            } catch (err) {
                console.error("Chart reset re-render failed:", err);
            }
        }

        updateChartFilterState(idx);
        closeChartFilterPanel(idx);
    }

    // ── Update filter button highlight and badge strip ────────────────────
    function updateChartFilterState(idx) {
        const filters = chartFilters[idx] || {};
        const btn = document.getElementById(`chart-filter-btn-${idx}`);
        const badgeContainer = document.getElementById(`chart-badges-${idx}`);

        const BADGE_DEFS = [
            { key: "_datePreset", label: f => f._datePreset ? `Date: ${f._datePreset}` : null },
            { key: "date_from",   label: f => (f._datePreset === "Custom" && f.date_from) ? `From: ${f.date_from}` : null },
            { key: "date_to",     label: f => (f._datePreset === "Custom" && f.date_to) ? `To: ${f.date_to}` : null },
            { key: "category",    label: f => f.category ? `Category: ${f.category}` : null },
            { key: "product",     label: f => f.product   ? `Product: ${f.product.substring(0,18)}${f.product.length>18?"…":""}` : null },
            { key: "status",      label: f => f.status    ? `Status: ${f.status}` : null },
            { key: "top_n",       label: f => f.top_n     ? `Top ${f.top_n}` : null },
            { key: "compare_lm",  label: f => f.compare_lm ? "vs Last Month" : null },
            { key: "compare_ly",  label: f => f.compare_ly ? "vs Last Year"  : null },
        ];

        const activeBadges = BADGE_DEFS.map(def => ({
            key: def.key,
            label: def.label(filters),
        })).filter(b => b.label !== null);

        const hasFilters = activeBadges.length > 0;

        // Update button state
        if (btn) {
            btn.classList.toggle("has-filters", hasFilters);
            btn.title = hasFilters ? `${activeBadges.length} filter(s) active` : "Chart Filters";
        }

        // Render badges
        if (badgeContainer) {
            badgeContainer.innerHTML = activeBadges.map(b => `
                <span class="chart-filter-badge">
                    ${escapeHtml(b.label)}
                    <button class="chart-filter-badge-remove" data-chart-idx="${idx}" data-filter-key="${b.key}" title="Remove filter">×</button>
                </span>
            `).join("");

            // Wire badge remove buttons
            badgeContainer.querySelectorAll(".chart-filter-badge-remove").forEach(removeBtn => {
                removeBtn.addEventListener("click", (e) => {
                    e.stopPropagation();
                    const filterKey = removeBtn.dataset.filterKey;
                    removeSingleChartFilter(idx, filterKey);
                });
            });
        }
    }

    // ── Remove one specific filter from a chart ───────────────────────────
    async function removeSingleChartFilter(idx, filterKey) {
        const filters = chartFilters[idx] || {};

        // Clear the relevant filter keys
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

        // Re-apply remaining filters
        const spec = chartSpecs[idx];
        const originalData = chartOriginalData[idx];
        if (!spec || !originalData) { updateChartFilterState(idx); return; }

        const hasAny = Object.keys(filters).some(k => k !== "_datePreset" && filters[k]);
        let newData;

        if (hasAny && spec.sql) {
            const showSpinner = () => {
                const chartBody = document.getElementById(`chart-body-${idx}`);
                if (chartBody && !document.getElementById(`cfp-spinner-${idx}`)) {
                    const overlay = document.createElement("div");
                    overlay.className = "chart-loading-overlay";
                    overlay.id = `cfp-spinner-${idx}`;
                    overlay.innerHTML = `<div class="chart-loading-spinner"></div>`;
                    chartBody.appendChild(overlay);
                }
            };
            showSpinner();

            try {
                const res = await fetch("/report/apply-chart-filter", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        sql: spec.sql,
                        date_from: filters.date_from || null,
                        date_to:   filters.date_to   || null,
                        category:  filters.category  || null,
                        product:   filters.product   || null,
                        status:    filters.status    || null,
                        top_n:     filters.top_n     || null,
                        compare_lm: !!filters.compare_lm,
                        compare_ly: !!filters.compare_ly,
                        provider: localStorage.getItem(reportId + "_provider") || "groq",
                    }),
                });
                if (res.ok) {
                    const result = await res.json();
                    newData = result.error ? JSON.parse(JSON.stringify(originalData)) : result.data;
                }
            } catch (_) {
                newData = JSON.parse(JSON.stringify(originalData));
            }

            const spinner = document.getElementById(`cfp-spinner-${idx}`);
            if (spinner) spinner.remove();
        } else {
            // No active filters — restore original, apply only client-side
            newData = JSON.parse(JSON.stringify(originalData));
            if (filters.top_n && newData.length) {
                const keys = Object.keys(newData[0]);
                if (keys.length >= 2) {
                    const valKey = keys[1];
                    try { newData = newData.sort((a,b)=>parseFloat(b[valKey]||0)-parseFloat(a[valKey]||0)).slice(0, filters.top_n); }
                    catch(_) { newData = newData.slice(0, filters.top_n); }
                }
            }
        }

        if (newData !== null && newData !== undefined) {
            if (chartInstances[idx]) {
                try { chartInstances[idx].destroy(); } catch(_) {}
                delete chartInstances[idx];
            }
            const chartBody = document.getElementById(`chart-body-${idx}`);
            if (chartBody) {
                const oldCanvas = document.getElementById(`chart_${idx}`);
                if (oldCanvas) oldCanvas.remove();
                const newCanvas = document.createElement("canvas");
                newCanvas.id = `chart_${idx}`;
                chartBody.appendChild(newCanvas);
                const updatedSpec = { ...spec, data: newData };
                try {
                    const inst = renderChart(newCanvas, updatedSpec);
                    if (inst) chartInstances[idx] = inst;
                } catch (_) {}
            }
        }

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
        const labels = data.map(row => String(row[labelKey]));
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
                    // Single-series bars: gradient fill from solid to lighter
                    cfg.backgroundColor = (ctx) => {
                        if (!ctx || !ctx.chart || !ctx.chart.chartArea) return color + "cc";
                        const { top, bottom, left, right } = ctx.chart.chartArea;
                        const gradient = ctx.chart.ctx.createLinearGradient(
                            isHorizontal ? left : 0,
                            isHorizontal ? 0 : top,
                            isHorizontal ? right : 0,
                            isHorizontal ? 0 : bottom
                        );
                        gradient.addColorStop(0, color + "ee");
                        gradient.addColorStop(1, color + "55");
                        return gradient;
                    };
                    cfg.borderColor = color;
                    cfg.borderWidth = 0;
                    cfg.hoverBackgroundColor = color + "ff";
                }
            }

            if (isPieType(chartType)) {
                // Semi-transparent slices with a clean white/dark separator
                cfg.backgroundColor = colors.slice(0, values.length).map(c => c + "bb");
                cfg.borderColor = defaults.bgColor;
                cfg.borderWidth = 2;
                cfg.hoverBackgroundColor = colors.slice(0, values.length).map(c => c + "ee");
                cfg.hoverOffset = 10;
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
                            title: function(items) {
                                if (!items.length) return "";
                                // For pie/doughnut, show the slice label
                                if (isPieType(chartType)) return items[0].label || "";
                                // For bar/line/area, show the x-axis label
                                return items[0].label || "";
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
                if (Math.abs(val) >= 100000)   return (val / 100000).toFixed(1) + "L";
                if (Math.abs(val) >= 1000)     return (val / 1000).toFixed(1) + "K";
                return val;
            };
            // For horizontal bar: x=values (needs number fmt), y=labels (plain text)
            // For all others:     x=labels (plain text),     y=values (needs number fmt)
            const valueAxisKey  = isHorizontal ? "x" : "y";
            const labelAxisKey  = isHorizontal ? "y" : "x";
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
                        maxRotation: isHorizontal ? 0 : 40,
                        autoSkip: true,
                        maxTicksLimit: isHorizontal ? 20 : 12,
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

        // Sync height with CSS: wide cards = 310px, regular = 270px
        const isWideCard = canvas.closest(".chart-full-width") !== null;
        canvas.parentElement.style.height = isWideCard ? "310px" : "270px";
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
            what:    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="15" height="15"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`,
            how:     `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="15" height="15"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>`,
            insight: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="15" height="15"><path d="M12 2a7 7 0 017 7c0 2.38-1.19 4.47-3 5.74V17a1 1 0 01-1 1H9a1 1 0 01-1-1v-2.26C6.19 13.47 5 11.38 5 9a7 7 0 017-7z"/><line x1="9" y1="21" x2="15" y2="21"/></svg>`,
            type:    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="15" height="15"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg>`,
        };

        const sections = [
            { key: "what",    label: "What it shows",  color: "#3b82f6" },
            { key: "how",     label: "How it's built", color: "#8b5cf6" },
            { key: "insight", label: "Key insight",    color: "#10b981" },
            { key: "type",    label: "Chart type",     color: "#f59e0b" },
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
