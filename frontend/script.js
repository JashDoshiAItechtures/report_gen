/* ═══════════════════════════════════════════════════════════════════════════
   AI Data Assistant — Unified Chat Interface
   Handles: query / chart / report / confirm_modify / modify_success / modify_error
   ═══════════════════════════════════════════════════════════════════════════ */

(function () {
    "use strict";

    // ── DOM refs ───────────────────────────────────────────────────────────
    const questionInput  = document.getElementById("questionInput");
    const submitBtn      = document.getElementById("submitBtn");
    const chatThread     = document.getElementById("chatThread");
    const welcomeState   = document.getElementById("welcomeState");
    const sidebar        = document.getElementById("sidebar");
    const sidebarList    = document.getElementById("sidebarList");
    const sidebarToggle  = document.getElementById("sidebarToggle");
    const newChatBtn     = document.getElementById("newChatBtn");
    const modelSwitcher  = document.getElementById("modelSwitcher");
    const topbarTitle    = document.getElementById("topbarTitle");

    let selectedProvider = "groq";
    let isLoading        = false;

    // ── Report modification state (for /report/modify flow) ───────────────
    let latestReportData = null;
    let latestReportId   = null;
    let reportWindow     = null;

    // ── Theme ──────────────────────────────────────────────────────────────
    const themeSwitcher = document.getElementById("themeSwitcher");

    function applyTheme(theme) {
        document.documentElement.setAttribute("data-theme", theme);
        localStorage.setItem("sqlbot_theme", theme);
        if (themeSwitcher) {
            themeSwitcher.querySelectorAll(".switcher-btn").forEach(b => {
                b.classList.toggle("active", b.dataset.theme === theme);
            });
        }
    }

    const savedTheme = localStorage.getItem("sqlbot_theme") ||
        (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    applyTheme(savedTheme);

    if (themeSwitcher) {
        themeSwitcher.addEventListener("click", e => {
            const btn = e.target.closest(".switcher-btn");
            if (btn) applyTheme(btn.dataset.theme);
        });
    }

    // ── Conversation management ────────────────────────────────────────────
    const _CONV_STORAGE_KEY = "sqlbot_conversation_id";

    function getAllConversations() {
        try { return JSON.parse(localStorage.getItem("sqlbot_conversations") || "[]"); }
        catch { return []; }
    }
    function getConversations() {
        return getAllConversations();
    }
    function saveConversations(list) {
        localStorage.setItem("sqlbot_conversations", JSON.stringify(list));
    }

    let currentConvId = localStorage.getItem(_CONV_STORAGE_KEY) || newConvId();

    function newConvId() {
        return (window.crypto && window.crypto.randomUUID)
            ? window.crypto.randomUUID()
            : "conv-" + Date.now().toString(36);
    }

    function setCurrentConv(id) {
        currentConvId = id;
        localStorage.setItem(_CONV_STORAGE_KEY, id);
    }

    function addConversationToList(id, title) {
        const list = getAllConversations();
        if (!list.find(c => c.id === id)) {
            list.unshift({ id, title, created_at: new Date().toISOString() });
            saveConversations(list);
        }
        renderSidebarList();
    }

    function updateConversationTitle(id, title) {
        const list = getAllConversations();
        const conv = list.find(c => c.id === id);
        if (conv && conv.title !== title) {
            conv.title = title;
            saveConversations(list);
            renderSidebarList();
        }
    }

    // ── Sidebar ────────────────────────────────────────────────────────────
    let sidebarOpen = true;

    function setSidebar(open) {
        sidebarOpen = open;
        sidebar.classList.toggle("collapsed", !open);
    }

    sidebarToggle.addEventListener("click", () => setSidebar(!sidebarOpen));

    function renderSidebarList() {
        const list = getConversations();
        if (!list.length) {
            sidebarList.innerHTML = '<p class="sidebar-empty">No conversations yet.</p>';
            return;
        }
        sidebarList.innerHTML = "";
        list.forEach(conv => {
            const item = document.createElement("button");
            item.className = "sidebar-item" + (conv.id === currentConvId ? " active" : "");
            item.innerHTML = `
                <div class="sidebar-item-icon">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>
                    </svg>
                </div>
                <div class="sidebar-item-content">
                    <div class="sidebar-item-question">${escapeHtml(conv.title || "New conversation")}</div>
                    <div class="sidebar-item-meta">${formatDate(conv.created_at)}</div>
                </div>
                <button class="sidebar-delete-btn" title="Delete conversation" data-id="${conv.id}">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="3 6 5 6 21 6"/>
                        <path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/>
                        <path d="M10 11v6M14 11v6"/><path d="M9 6V4h6v2"/>
                    </svg>
                </button>
            `;
            item.addEventListener("click", e => {
                if (e.target.closest(".sidebar-delete-btn")) return;
                loadConversation(conv.id, conv.title);
            });
            item.querySelector(".sidebar-delete-btn").addEventListener("click", async e => {
                e.stopPropagation();
                await deleteConversation(conv.id);
            });
            sidebarList.appendChild(item);
        });
    }

    async function deleteConversation(id) {
        try {
            const res = await fetch(`/history?conversation_id=${encodeURIComponent(id)}`);
            if (res.ok) {
                const turns = await res.json();
                for (const t of turns) {
                    await fetch(`/history/${t.id}`, { method: "DELETE" });
                }
            }
        } catch (_) {}

        const list = getAllConversations().filter(c => c.id !== id);
        saveConversations(list);

        if (id === currentConvId) {
            startNewChat();
        } else {
            renderSidebarList();
        }
    }

    async function loadConversation(id, title) {
        setCurrentConv(id);
        topbarTitle.textContent = title || "Conversation";
        clearChatThread();

        try {
            const res = await fetch(`/history?conversation_id=${encodeURIComponent(id)}`);
            if (!res.ok) return;
            const turns = await res.json();
            if (turns.length > 0) {
                hideWelcome();
                turns.forEach(t => appendTurn(t.question, t.answer, t.sql_query, t.query_result));
            } else {
                showWelcome();
            }
        } catch (_) { showWelcome(); }

        renderSidebarList();
        scrollToBottom();
    }

    // ── New Chat ───────────────────────────────────────────────────────────
    newChatBtn.addEventListener("click", startNewChat);

    function startNewChat() {
        const id = newConvId();
        setCurrentConv(id);
        topbarTitle.textContent = "New Conversation";
        clearChatThread();
        showWelcome();
        questionInput.value = "";
        questionInput.style.height = "";
        renderSidebarList();
        // Clear report state
        latestReportData = null;
        latestReportId = null;
        reportWindow = null;
    }

    // ── Welcome chips ──────────────────────────────────────────────────────
    document.querySelectorAll(".chip").forEach(chip => {
        chip.addEventListener("click", () => {
            questionInput.value = chip.dataset.q;
            questionInput.dispatchEvent(new Event("input"));
            handleSubmit();
        });
    });

    // ── Model switcher ─────────────────────────────────────────────────────
    if (modelSwitcher) {
        modelSwitcher.addEventListener("click", e => {
            const btn = e.target.closest(".switcher-btn");
            if (!btn) return;
            modelSwitcher.querySelectorAll(".switcher-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            selectedProvider = btn.dataset.provider;
        });
    }

    // ── Auto-resize textarea ───────────────────────────────────────────────
    questionInput.addEventListener("input", () => {
        questionInput.style.height = "auto";
        questionInput.style.height = Math.min(questionInput.scrollHeight, 160) + "px";
    });

    // ── Submit ─────────────────────────────────────────────────────────────
    submitBtn.addEventListener("click", handleSubmit);
    questionInput.addEventListener("keydown", e => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleSubmit();
        }
    });

    // ── Unified Submit Handler ─────────────────────────────────────────────
    async function handleSubmit() {
        const question = questionInput.value.trim();
        if (!question || isLoading) return;

        isLoading = true;
        submitBtn.disabled = true;

        hideWelcome();
        appendUserMessage(question);
        questionInput.value = "";
        questionInput.style.height = "";
        scrollToBottom();

        const typingEl = appendTypingIndicator();
        scrollToBottom();

        try {
            const res = await fetch("/chat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    question,
                    provider: selectedProvider,
                    conversation_id: currentConvId,
                }),
            });

            typingEl.remove();

            if (!res.ok) {
                const err = await res.json().catch(() => ({ detail: res.statusText }));
                appendErrorMessage(err.detail || `HTTP ${res.status}`);
            } else {
                const data = await res.json();

                // ── Route by mode ──
                if (data.mode === "report") {
                    // Backend detected report intent — show generate button
                    appendReportMessage(question, data);
                } else if (data.mode === "chart") {
                    // Inline chart in chat
                    appendChartMessage(data);
                } else if (data.mode === "confirm_modify") {
                    // Modification preview — show confirm card
                    appendConfirmModifyMessage(data);
                } else if (data.mode === "modify_success") {
                    appendModifyResultMessage(data.message || "Done!", true);
                } else if (data.mode === "modify_error") {
                    appendModifyResultMessage(data.error || "Modification failed.", false);
                } else {
                    // Default: plain SQL chat response
                    appendAIMessage(data);
                }

                // Update sidebar
                const convs = getConversations();
                if (!convs.find(c => c.id === currentConvId)) {
                    addConversationToList(currentConvId, question);
                    topbarTitle.textContent = question.length > 40 ? question.slice(0, 40) + "…" : question;
                }
            }
        } catch (err) {
            typingEl.remove();
            appendErrorMessage(err.message || "Something went wrong. Please try again.");
        }

        isLoading = false;
        submitBtn.disabled = false;
        scrollToBottom();
    }

    // ── Report card (mode: "report") ───────────────────────────────────────
    // Shown when backend classifies the intent as "report".
    // User clicks "Generate Report" → calls /report → opens in new tab.
    function appendReportMessage(question, chatData) {
        const el = document.createElement("div");
        el.className = "msg msg-ai";
        const reportId = "rpt_" + Date.now();

        el.innerHTML = `
            <div class="ai-avatar">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M12 2L2 7l10 5 10-5-10-5z"/>
                    <path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>
                </svg>
            </div>
            <div class="ai-body">
                <div class="ai-answer">${escapeHtml(chatData.answer || "I'll generate an analytics report for that.")}</div>
                <div class="report-trigger-card">
                    <div class="report-trigger-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                            <line x1="3" y1="9" x2="21" y2="9"/>
                            <line x1="9" y1="21" x2="9" y2="9"/>
                        </svg>
                    </div>
                    <div class="report-trigger-info">
                        <div class="report-trigger-title">Analytics Report</div>
                        <div class="report-trigger-desc">Generate a full dashboard with KPIs, charts, data tables, and AI insights.</div>
                    </div>
                    <button class="report-trigger-btn" data-report-id="${reportId}" data-question="${escapeAttr(question)}">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                            <line x1="3" y1="9" x2="21" y2="9"/>
                            <line x1="9" y1="21" x2="9" y2="9"/>
                        </svg>
                        Generate Report
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="report-trigger-arrow">
                            <polyline points="9 18 15 12 9 6"/>
                        </svg>
                    </button>
                </div>
            </div>`;

        const btn = el.querySelector(".report-trigger-btn");
        btn.addEventListener("click", () => {
            generateAndOpenReport(question, reportId, btn);
        });

        chatThread.appendChild(el);
    }

    // ── Report success message ─────────────────────────────────────────────
    function appendReportSuccessMessage(question, reportId) {
        const el = document.createElement("div");
        el.className = "msg msg-ai";
        el.innerHTML = `
            <div class="ai-avatar">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M12 2L2 7l10 5 10-5-10-5z"/>
                    <path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>
                </svg>
            </div>
            <div class="ai-body">
                <div class="ai-answer">Your analytics report has been generated and opened in a new tab.</div>
                <div class="report-trigger-card">
                    <div class="report-trigger-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                            <line x1="3" y1="9" x2="21" y2="9"/>
                            <line x1="9" y1="21" x2="9" y2="9"/>
                        </svg>
                    </div>
                    <div class="report-trigger-info">
                        <div class="report-trigger-title">Report Ready</div>
                        <div class="report-trigger-desc">${escapeHtml(question)}</div>
                    </div>
                    <button class="report-trigger-btn report-trigger-btn-success" onclick="window.open('/report-view?id=${reportId}', '_blank')">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg>
                        Open Report
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="report-trigger-arrow"><polyline points="9 18 15 12 9 6"/></svg>
                    </button>
                </div>
            </div>`;
        chatThread.appendChild(el);
    }

    // ── Generate and open report (calls /report endpoint) ─────────────────
    async function generateAndOpenReport(question, reportId, btn) {
        btn.disabled = true;
        btn.innerHTML = `<div class="report-btn-spinner"></div>Generating Report…`;

        // Open placeholder tab synchronously to avoid popup blocking
        let reportWin = window.open("/report-view?id=loading", "_blank");

        try {
            const res = await fetch("/report", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ question, provider: selectedProvider }),
            });

            if (!res.ok) {
                const err = await res.json().catch(() => ({ detail: res.statusText }));
                if (reportWin && !reportWin.closed) reportWin.close();
                btn.disabled = false;
                btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg>Retry Report<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="report-trigger-arrow"><polyline points="9 18 15 12 9 6"/></svg>`;
                appendErrorMessage("Report generation failed: " + (err.detail || err.error || "Unknown error"));
                return;
            }

            const reportData = await res.json();

            // Store in localStorage — shared across all tabs
            localStorage.setItem(reportId, JSON.stringify(reportData));
            localStorage.setItem(reportId + "_question", question);
            localStorage.setItem(reportId + "_provider", selectedProvider);
            localStorage.setItem(reportId + "_theme", document.documentElement.getAttribute("data-theme") || "light");

            // Track for /report/modify
            latestReportData = reportData.report;
            latestReportId = reportId;

            btn.disabled = false;
            btn.classList.add("report-trigger-btn-success");
            btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg>Open Report<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="report-trigger-arrow"><polyline points="9 18 15 12 9 6"/></svg>`;
            btn.onclick = () => window.open(`/report-view?id=${reportId}`, "_blank");

            if (reportWin && !reportWin.closed) {
                reportWin.location.href = `/report-view?id=${reportId}`;
            } else {
                window.open(`/report-view?id=${reportId}`, "_blank");
            }

        } catch (err) {
            if (reportWin && !reportWin.closed) reportWin.close();
            btn.disabled = false;
            btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg>Retry Report<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="report-trigger-arrow"><polyline points="9 18 15 12 9 6"/></svg>`;
            appendErrorMessage("Report generation failed: " + err.message);
        }

        scrollToBottom();
    }

    // ── Inline Chart Message (mode: "chart") ───────────────────────────────
    function appendChartMessage(data) {
        const el = document.createElement("div");
        el.className = "msg msg-ai";

        const chartId   = "inline_chart_" + Date.now();
        const chartType = (data.chart_type || "bar");
        const typeLabel = chartType.charAt(0).toUpperCase() + chartType.slice(1);
        const hasSql    = !!data.sql;
        const hasData   = data.data && data.data.length > 0;
        const rowLabel  = hasData ? `${data.data.length} row${data.data.length !== 1 ? "s" : ""}` : "0 rows";
        const sqlId     = "csql_" + Date.now();
        const tblId     = "ctbl_" + Date.now();

        el.innerHTML = `
            <div class="ai-avatar">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M12 2L2 7l10 5 10-5-10-5z"/>
                    <path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>
                </svg>
            </div>
            <div class="ai-body">
                ${data.answer ? `<div class="ai-answer">${escapeHtml(data.answer)}</div>` : ""}
                <div class="inline-chart-card">
                    <div class="inline-chart-header">
                        <span class="inline-chart-title">Chart</span>
                        <span class="inline-chart-type-badge">${typeLabel}</span>
                    </div>
                    <div class="inline-chart-body">
                        <canvas id="${chartId}"></canvas>
                    </div>
                </div>
                ${hasSql ? `
                <div class="ai-section">
                    <button class="section-toggle" data-target="${sqlId}">
                        <span class="section-toggle-icon">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
                        </span>
                        SQL Query
                        <svg class="chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg>
                    </button>
                    <div class="section-body collapsed" id="${sqlId}">
                        <pre class="sql-code"><code>${escapeHtml(data.sql)}</code></pre>
                    </div>
                </div>` : ""}
                ${hasData ? `
                <div class="ai-section">
                    <button class="section-toggle" data-target="${tblId}">
                        <span class="section-toggle-icon">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>
                        </span>
                        Data <span class="row-badge">${rowLabel}</span>
                        <svg class="chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg>
                    </button>
                    <div class="section-body collapsed" id="${tblId}">
                        <div class="table-wrapper">${buildTable(data.data)}</div>
                    </div>
                </div>` : ""}
            </div>`;

        el.querySelectorAll(".section-toggle").forEach(btn => {
            const body = el.querySelector(`#${btn.dataset.target}`);
            if (!body) return;
            btn.addEventListener("click", () => {
                body.classList.toggle("collapsed");
                btn.classList.toggle("open");
            });
        });

        chatThread.appendChild(el);

        // Render chart after DOM insertion
        if (hasData) {
            requestAnimationFrame(() => {
                const canvas = document.getElementById(chartId);
                if (canvas) renderInlineChart(canvas, data);
            });
        }
    }

    // ── Inline Chart Renderer (Chart.js) ───────────────────────────────────
    function renderInlineChart(canvas, data) {
        const rows = data.data;
        if (!rows || rows.length === 0) return;
        const keys = Object.keys(rows[0]);
        if (keys.length < 2) return;

        const labels = rows.map(r => String(r[keys[0]]));
        const values = rows.map(r => Number(r[keys[1]]) || 0);

        const COLORS = [
            "#10b981","#3b82f6","#8b5cf6","#f59e0b","#f43f5e",
            "#06b6d4","#6366f1","#ec4899","#14b8a6","#a855f7",
            "#eab308","#ef4444","#22c55e","#0ea5e9","#d946ef"
        ];
        const isDark    = document.documentElement.getAttribute("data-theme") === "dark";
        const gridColor = isDark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.05)";
        const textColor = isDark ? "#94a3b8" : "#475569";

        let chartType  = (data.chart_type || "bar").toLowerCase();
        let isHoriz    = false;
        if (chartType === "horizontalbar") { chartType = "bar"; isHoriz = true; }
        if (chartType === "stackedbar")    { chartType = "bar"; }
        if (chartType === "area")          { chartType = "line"; }
        const isPie = ["pie","doughnut"].includes(chartType);
        if (!["bar","line","pie","doughnut","scatter"].includes(chartType)) chartType = "bar";

        const color = COLORS[0];
        const ds = {
            label: String(keys[1]).replace(/_/g," ").replace(/\b\w/g, c => c.toUpperCase()),
            data: values,
            backgroundColor: isPie
                ? COLORS.slice(0, values.length).map(c => c + "bb")
                : color + "33",
            borderColor: isPie
                ? COLORS.slice(0, values.length)
                : color,
            borderWidth: 2,
            borderRadius: chartType === "bar" ? 6 : 0,
            tension: chartType === "line" ? 0.45 : 0,
        };

        if (chartType === "line") {
            ds.fill = data.chart_type === "area";
            ds.pointRadius = rows.length > 30 ? 0 : 4;
            ds.pointBackgroundColor = color;
        }

        canvas.parentElement.style.height = "250px";

        new Chart(canvas, {
            type: chartType,
            data: { labels, datasets: [ds] },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                devicePixelRatio: window.devicePixelRatio || 2,
                indexAxis: isHoriz ? "y" : "x",
                plugins: {
                    legend: {
                        display: isPie,
                        labels: { color: textColor, font:{ family:"'Inter',sans-serif", size:11 } },
                    },
                    tooltip: {
                        backgroundColor: isDark ? "rgba(255,255,255,0.92)" : "rgba(15,23,42,0.95)",
                        titleColor: isDark ? "#0f172a" : "#fff",
                        bodyColor:  isDark ? "#475569" : "#cbd5e1",
                        cornerRadius: 8, padding: 9,
                        callbacks: {
                            label: ctx => {
                                const v = isPie ? ctx.parsed : (isHoriz ? ctx.parsed.x : ctx.parsed.y);
                                return `${ctx.dataset.label}: ${typeof v === "number" ? v.toLocaleString("en-IN") : v}`;
                            },
                        },
                    },
                },
                scales: isPie ? {} : {
                    x: {
                        grid: { color: isHoriz ? gridColor : "transparent" },
                        ticks: {
                            color: textColor,
                            font: { family:"'Inter'", size:10 },
                            maxTicksLimit: isHoriz ? 8 : 10,
                            callback: v => typeof v === "number" && Math.abs(v) >= 1000 ? (v/1000).toFixed(1)+"K" : v,
                        },
                        beginAtZero: isHoriz,
                    },
                    y: {
                        grid: { color: isHoriz ? "transparent" : gridColor },
                        ticks: {
                            color: textColor,
                            font: { family:"'Inter'", size:10 },
                            maxTicksLimit: 8,
                            callback: v => typeof v === "number" && Math.abs(v) >= 1000 ? (v/1000).toFixed(1)+"K" : v,
                        },
                        beginAtZero: !isHoriz,
                    },
                },
                animation: { duration: 600, easing: "easeOutQuart" },
            },
        });
    }

    // ── Modification Confirm Card (mode: "confirm_modify") ─────────────────
    function appendConfirmModifyMessage(data) {
        const el = document.createElement("div");
        el.className = "msg msg-ai";

        const riskLevel = (data.risk_level || "medium").toLowerCase();
        const riskLabel = riskLevel.charAt(0).toUpperCase() + riskLevel.slice(1) + " Risk";
        const pendingSql = data.pending_sql || "";

        el.innerHTML = `
            <div class="ai-avatar">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M12 2L2 7l10 5 10-5-10-5z"/>
                    <path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>
                </svg>
            </div>
            <div class="ai-body">
                <div class="ai-answer">${escapeHtml(data.intent_summary || "Ready to execute the following modification:")}</div>
                <div class="confirm-card">
                    <div class="confirm-card-header">
                        <div class="confirm-card-icon">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 013 3L7 19l-4 1 1-4L16.5 3.5z"/>
                            </svg>
                        </div>
                        <div>
                            <div class="confirm-card-title">Data Modification Preview</div>
                            <div class="confirm-card-subtitle">Review the SQL carefully before approving — this will change your database.</div>
                        </div>
                    </div>
                    <div class="confirm-card-body">
                        <span class="risk-badge risk-${riskLevel}">${riskLabel}</span>
                        <div class="confirm-sql-block">
                            <pre>${escapeHtml(pendingSql)}</pre>
                        </div>
                        ${data.rows_affected_estimate && data.rows_affected_estimate !== "unknown" ? `
                        <div class="confirm-rows-hint">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:13px;height:13px;flex-shrink:0"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                            Estimated rows affected: <strong>${escapeHtml(String(data.rows_affected_estimate))}</strong>
                        </div>` : ""}
                        <div class="confirm-actions">
                            <button class="confirm-approve-btn">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
                                Approve &amp; Execute
                            </button>
                            <button class="confirm-cancel-btn">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                                Cancel
                            </button>
                        </div>
                    </div>
                </div>
            </div>`;

        const approveBtn = el.querySelector(".confirm-approve-btn");
        const cancelBtn  = el.querySelector(".confirm-cancel-btn");

        approveBtn.addEventListener("click", async () => {
            approveBtn.disabled = true;
            cancelBtn.disabled  = true;
            approveBtn.innerHTML = `<div class="report-btn-spinner"></div> Executing…`;

            try {
                const res = await fetch("/modify/execute", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        sql: pendingSql,
                        provider: selectedProvider,
                        conversation_id: currentConvId,
                    }),
                });
                const result = await res.json();
                if (result.mode === "modify_success") {
                    appendModifyResultMessage(result.message || "Done!", true);
                } else {
                    appendModifyResultMessage(result.error || "Execution failed.", false);
                }
            } catch (err) {
                appendModifyResultMessage("Execution error: " + err.message, false);
            }

            scrollToBottom();
        });

        cancelBtn.addEventListener("click", () => {
            approveBtn.disabled = true;
            cancelBtn.disabled  = true;
            appendModifyResultMessage("Modification cancelled. No changes were made.", null);
            scrollToBottom();
        });

        chatThread.appendChild(el);
    }

    // ── Modify Result Message ──────────────────────────────────────────────
    // success: true → green success, false → red error, null → neutral (cancelled)
    function appendModifyResultMessage(message, success) {
        const el = document.createElement("div");
        el.className = "msg msg-ai";

        const cardClass = success === true  ? "modify-result-card modify-success-card" :
                          success === false ? "modify-result-card modify-error-card"   :
                                             "modify-result-card";
        const icon = success === true  ? "✓" :
                     success === false ? "✕" : "⊘";

        el.innerHTML = `
            <div class="ai-avatar">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M12 2L2 7l10 5 10-5-10-5z"/>
                    <path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>
                </svg>
            </div>
            <div class="ai-body">
                <div class="${cardClass}">
                    <span class="modify-result-icon">${icon}</span>
                    <span class="modify-result-text">${escapeHtml(message)}</span>
                </div>
            </div>`;

        chatThread.appendChild(el);
    }

    // ── Plain chat response (mode: "chat") ─────────────────────────────────
    function appendAIMessage(data) {
        const el = document.createElement("div");
        el.className = "msg msg-ai";

        const hasData   = data.data && data.data.length > 0;
        const hasSql    = !!data.sql;
        const hasAnswer = !!data.answer;
        const rowLabel  = hasData ? `${data.data.length} row${data.data.length !== 1 ? "s" : ""}` : "0 rows";
        const sqlId     = "sql_" + Date.now();
        const tblId     = "tbl_" + Date.now();
        const insId     = "ins_" + Date.now();

        el.innerHTML = `
            <div class="ai-avatar">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M12 2L2 7l10 5 10-5-10-5z"/>
                    <path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>
                </svg>
            </div>
            <div class="ai-body">
                ${hasAnswer ? `<div class="ai-answer">${escapeHtml(data.answer)}</div>` : ""}
                ${hasSql ? `
                <div class="ai-section">
                    <button class="section-toggle" data-target="${sqlId}">
                        <span class="section-toggle-icon">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
                        </span>
                        SQL Query
                        <svg class="chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg>
                    </button>
                    <div class="section-body" id="${sqlId}">
                        <pre class="sql-code"><code>${escapeHtml(data.sql)}</code></pre>
                    </div>
                </div>` : ""}
                ${hasData ? `
                <div class="ai-section">
                    <button class="section-toggle" data-target="${tblId}">
                        <span class="section-toggle-icon">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>
                        </span>
                        Results <span class="row-badge">${rowLabel}</span>
                        <svg class="chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg>
                    </button>
                    <div class="section-body" id="${tblId}">
                        <div class="table-wrapper">${buildTable(data.data)}</div>
                    </div>
                </div>` : ""}
                ${data.insights ? `
                <div class="ai-section">
                    <button class="section-toggle" data-target="${insId}">
                        <span class="section-toggle-icon">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a7 7 0 017 7c0 2.38-1.19 4.47-3 5.74V17a1 1 0 01-1 1H9a1 1 0 01-1-1v-2.26C6.19 13.47 5 11.38 5 9a7 7 0 017-7z"/><line x1="9" y1="21" x2="15" y2="21"/></svg>
                        </span>
                        Insights
                        <svg class="chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg>
                    </button>
                    <div class="section-body collapsed" id="${insId}">
                        <div class="insights-text">${escapeHtml(data.insights)}</div>
                    </div>
                </div>` : ""}
            </div>`;

        // SQL and Table open by default; Insights collapsed
        el.querySelectorAll(".section-toggle").forEach(btn => {
            const targetId = btn.dataset.target;
            const body = el.querySelector(`#${targetId}`);
            if (!body) return;

            const isInsights = targetId.startsWith("ins_");
            if (!isInsights) btn.classList.add("open");

            btn.addEventListener("click", () => {
                body.classList.toggle("collapsed");
                btn.classList.toggle("open");
            });
        });

        chatThread.appendChild(el);
    }

    function appendErrorMessage(msg) {
        const el = document.createElement("div");
        el.className = "msg msg-ai";
        el.innerHTML = `
            <div class="ai-avatar ai-avatar-error">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>
                </svg>
            </div>
            <div class="ai-body">
                <div class="ai-error">${escapeHtml(msg)}</div>
            </div>`;
        chatThread.appendChild(el);
    }

    function appendAssistantMessage(msg) {
        const el = document.createElement("div");
        el.className = "msg msg-ai";
        el.innerHTML = `
            <div class="ai-avatar">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M12 2L2 7l10 5 10-5-10-5z"/>
                    <path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>
                </svg>
            </div>
            <div class="ai-body">
                <div class="answer-text">${escapeHtml(msg)}</div>
            </div>`;
        chatThread.appendChild(el);
    }

    function appendUserMessage(text) {
        const el = document.createElement("div");
        el.className = "msg msg-user";
        el.innerHTML = `<div class="msg-bubble">${escapeHtml(text)}</div>`;
        chatThread.appendChild(el);
    }

    function appendTypingIndicator() {
        const el = document.createElement("div");
        el.className = "msg msg-ai";
        el.innerHTML = `
            <div class="ai-avatar">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M12 2L2 7l10 5 10-5-10-5z"/>
                    <path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>
                </svg>
            </div>
            <div class="typing-indicator">
                <span></span><span></span><span></span>
            </div>`;
        chatThread.appendChild(el);
        return el;
    }

    // Restore a historical turn in the chat thread (used by loadConversation)
    function appendTurn(question, answer, sql, queryResult) {
        appendUserMessage(question);
        appendAIMessage({
            answer,
            sql: sql || "",
            data: queryResult || [],
            insights: "",
        });
    }

    // ── Table builder ──────────────────────────────────────────────────────
    function buildTable(rows) {
        if (!rows || !rows.length) return '<p class="no-data">No data returned.</p>';
        const cols = Object.keys(rows[0]);
        const display = rows.slice(0, 200);
        let html = "<table><thead><tr>";
        cols.forEach(c => { html += `<th>${escapeHtml(c)}</th>`; });
        html += "</tr></thead><tbody>";
        display.forEach(row => {
            html += "<tr>";
            cols.forEach(c => {
                const v = row[c];
                html += `<td>${escapeHtml(v === null || v === undefined ? "NULL" : String(v))}</td>`;
            });
            html += "</tr>";
        });
        html += "</tbody></table>";
        if (rows.length > 200) {
            html += `<p class="no-data">Showing 200 of ${rows.length} rows</p>`;
        }
        return html;
    }

    // ── Helpers ────────────────────────────────────────────────────────────
    function showWelcome()  { welcomeState.classList.remove("hidden"); }
    function hideWelcome()  { welcomeState.classList.add("hidden"); }
    function clearChatThread() {
        chatThread.querySelectorAll(".msg").forEach(e => e.remove());
    }
    function scrollToBottom() {
        chatThread.scrollTop = chatThread.scrollHeight;
    }
    function formatDate(iso) {
        if (!iso) return "";
        const d = new Date(iso);
        const now = new Date();
        if (d.toDateString() === now.toDateString()) {
            return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        }
        return d.toLocaleDateString([], { month: "short", day: "numeric" });
    }
    function escapeHtml(str) {
        const d = document.createElement("div");
        d.appendChild(document.createTextNode(String(str)));
        return d.innerHTML;
    }
    function escapeAttr(str) {
        return String(str).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    // ── Explanation Modal (for report.html eye buttons — kept for compat) ──
    window.showExplanationModal = function(title, explanation) {
        const overlay = document.getElementById("explainModalOverlay");
        const mTitle  = document.getElementById("explainModalTitle");
        const mBody   = document.getElementById("explainModalBody");
        if (!overlay || !mTitle || !mBody) return;
        mTitle.textContent = title;
        let html = "";
        if (explanation.what) {
            html += `<div class="explain-section"><div class="explain-section-label explain-label-what">What</div><div class="explain-section-text">${escapeHtml(explanation.what)}</div></div>`;
        }
        if (explanation.how) {
            html += `<div class="explain-section"><div class="explain-section-label explain-label-how">How</div><div class="explain-section-text">${escapeHtml(explanation.how)}</div></div>`;
        }
        mBody.innerHTML = html || "<p>No explanation available.</p>";
        overlay.classList.remove("hidden");
    };

    const explainClose   = document.getElementById("explainModalClose");
    const explainOverlay = document.getElementById("explainModalOverlay");
    if (explainClose)   explainClose.addEventListener("click", () => explainOverlay.classList.add("hidden"));
    if (explainOverlay) explainOverlay.addEventListener("click", e => { if (e.target === explainOverlay) explainOverlay.classList.add("hidden"); });

    const thoughtClose   = document.getElementById("thoughtModalClose");
    const thoughtOverlay = document.getElementById("thoughtModalOverlay");
    if (thoughtClose)   thoughtClose.addEventListener("click", () => thoughtOverlay.classList.add("hidden"));
    if (thoughtOverlay) thoughtOverlay.addEventListener("click", e => { if (e.target === thoughtOverlay) thoughtOverlay.classList.add("hidden"); });

    // ── Startup ────────────────────────────────────────────────────────────
    (async function init() {
        renderSidebarList();

        // Try to restore last active conversation
        const savedId = localStorage.getItem(_CONV_STORAGE_KEY);
        if (savedId) {
            const convList = getAllConversations();
            const conv = convList.find(c => c.id === savedId);
            if (conv) {
                await loadConversation(savedId, conv.title);
                return;
            }
        }
        showWelcome();
    })();

})();
