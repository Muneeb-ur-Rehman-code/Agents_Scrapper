(() => {
  const PAGE_SIZE = 30;

  const state = {
    view: "dashboard",
    charts: {},
    activeRunId: null,
    poller: null,
    detailAgents: [],
    detailPage: 1,
    detailRunId: null,
    progressStartedAt: null,
    lastProgressPct: 0,
  };

  const titles = {
    dashboard: {
      title: "Dashboard",
      eyebrow: "Operations",
      sub: "Live overview of scrape jobs and lead volume",
    },
    scrape: {
      title: "Scrape Data",
      eyebrow: "Jobs",
      sub: "Configure and launch a new agent collection run",
    },
    data: {
      title: "Data",
      eyebrow: "Datasets",
      sub: "Browse, filter, download, and inspect scraped lead files",
    },
    detail: {
      title: "File Detail",
      eyebrow: "Inspection",
      sub: "Full lead records for the selected scrape file",
    },
    userhub: {
      title: "User Hub",
      eyebrow: "Access",
      sub: "Operators and roles",
    },
    settings: {
      title: "Settings",
      eyebrow: "Account",
      sub: "Profile details and password",
    },
  };

  const els = {
    pageTitle: document.getElementById("pageTitle"),
    pageEyebrow: document.getElementById("pageEyebrow"),
    pageHeading: document.getElementById("pageHeading"),
    pageSub: document.getElementById("pageSub"),
    liveDot: document.getElementById("liveDot"),
    liveLabel: document.getElementById("liveLabel"),
    recentTableBody: document.getElementById("recentTableBody"),
    dataTableBody: document.getElementById("dataTableBody"),
    detailTableBody: document.getElementById("detailTableBody"),
    detailTitle: document.getElementById("detailTitle"),
    detailMeta: document.getElementById("detailMeta"),
    detailStats: document.getElementById("detailStats"),
    detailDownload: document.getElementById("detailDownload"),
    detailPagination: document.getElementById("detailPagination"),
    statusBox: document.getElementById("statusBox"),
    main: document.querySelector(".main"),
    startBtn: document.getElementById("startBtn"),
    form: document.getElementById("runForm"),
    rangeStart: document.querySelector("[name='range_start']"),
    rangeEnd: document.querySelector("[name='range_end']"),
    rangeHint: document.getElementById("rangeHint"),
    locationInput: document.getElementById("locationInput"),
    locationHint: document.getElementById("locationHint"),
    siteRadios: document.querySelectorAll("input[name='site']"),
    filterQ: document.getElementById("filterQ"),
    filterSite: document.getElementById("filterSite"),
    filterStatus: document.getElementById("filterStatus"),
    usersTableBody: document.getElementById("usersTableBody"),
    addUserForm: document.getElementById("addUserForm"),
    addUserModal: document.getElementById("addUserModal"),
    openAddUserBtn: document.getElementById("openAddUserBtn"),
    topScrapeBtn: document.getElementById("topScrapeBtn"),
    headerUserName: document.getElementById("headerUserName"),
    newUsername: document.getElementById("newUsername"),
    newPassword: document.getElementById("newPassword"),
    newRole: document.getElementById("newRole"),
    usersStatusBox: document.getElementById("usersStatusBox"),
    logoutBtn: document.getElementById("logoutBtn"),
    changePasswordForm: document.getElementById("changePasswordForm"),
    passwordStatusBox: document.getElementById("passwordStatusBox"),
  };

  const chartColors = {
    // Charts use their own palette (not the indigo UI theme)
    accent: "#0f9f9c",
    accentHover: "#0b8482",
    accentSoft: "rgba(15, 159, 156, 0.16)",
    accentLight: "#5ec4c1",
    warm: "#e07a3d",
    warmSoft: "rgba(224, 122, 61, 0.22)",
    slate: "#64748b",
    amber: "#d97706",
    rose: "#e11d48",
    teal: "#0f9f9c",
    grid: "#e8ebf2",
  };

  async function confirmDelete({ title, text, confirmText = "Yes, delete" }) {
    if (typeof Swal === "undefined") {
      return window.confirm(`${title}\n${text || ""}`.trim());
    }
    const result = await Swal.fire({
      title,
      text: text || "",
      icon: "warning",
      showCancelButton: true,
      focusCancel: true,
      reverseButtons: true,
      confirmButtonText: confirmText,
      cancelButtonText: "Cancel",
      confirmButtonColor: "#dc2626",
      cancelButtonColor: "#4855c4",
      buttonsStyling: true,
      customClass: {
        popup: "swal-leadops",
        confirmButton: "swal-leadops-confirm",
        cancelButton: "swal-leadops-cancel",
      },
    });
    return result.isConfirmed;
  }

  async function toastSuccess(title) {
    if (typeof Swal === "undefined") return;
    await Swal.fire({
      toast: true,
      position: "top-end",
      icon: "success",
      title,
      showConfirmButton: false,
      timer: 2200,
      timerProgressBar: true,
    });
  }

  function $(sel, root = document) {
    return root.querySelector(sel);
  }

  function pathForView(view, opts = {}) {
    if (view === "detail" && opts.runId) return `/detail/${encodeURIComponent(opts.runId)}`;
    if (view === "dashboard") return "/dashboard";
    if (["scrape", "data", "userhub", "settings"].includes(view)) return `/${view}`;
    return "/dashboard";
  }

  function viewFromPath(pathname) {
    const parts = (pathname || "/").replace(/\/+$/, "").split("/").filter(Boolean);
    if (!parts.length || parts[0] === "dashboard") {
      return { view: "dashboard" };
    }
    if (parts[0] === "detail" && parts[1]) {
      return { view: "detail", runId: decodeURIComponent(parts[1]) };
    }
    if (["scrape", "data", "userhub", "settings"].includes(parts[0])) {
      return { view: parts[0] };
    }
    return { view: "dashboard" };
  }

  function setView(view, opts = {}) {
    state.view = view;
    document.body.dataset.view = view;
    document.querySelectorAll(".view").forEach((v) => {
      v.classList.toggle("active", v.dataset.view === view);
    });
    document.querySelectorAll("[data-nav]").forEach((btn) => {
      const target = btn.getAttribute("data-nav");
      if (btn.classList.contains("nav-item")) {
        btn.classList.toggle("active", target === view || (view === "detail" && target === "data"));
      }
    });

    const meta = titles[view] || titles.dashboard;
    els.pageTitle.textContent = meta.title;
    els.pageEyebrow.textContent = meta.eyebrow;
    if (els.pageHeading) els.pageHeading.textContent = meta.title;
    if (els.pageSub) els.pageSub.textContent = meta.sub;

    if (els.openAddUserBtn) els.openAddUserBtn.hidden = view !== "userhub";
    if (els.topScrapeBtn) els.topScrapeBtn.hidden = view === "userhub" || view === "settings";

    if (view === "dashboard") loadDashboard();
    if (view === "data") loadRuns();
    if (view === "detail" && opts.runId) loadDetail(opts.runId);
    if (view === "userhub") loadUsers();
    if (view === "settings") {
      loadSettings();
      if (opts.settingsTab) setSettingsTab(opts.settingsTab);
    }

    const nextPath = pathForView(view, opts);
    if (!opts.skipHistory) {
      const dirtyHash = Boolean(location.hash);
      if (opts.replace || location.pathname !== nextPath || dirtyHash) {
        const stateObj = { view, runId: opts.runId || null };
        if (opts.replace || dirtyHash) {
          history.replaceState(stateObj, "", nextPath);
        } else {
          history.pushState(stateObj, "", nextPath);
        }
      }
    }
  }

  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function fmtDate(value) {
    if (!value) return "—";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value).slice(0, 19);
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(d.getDate())} ${MONTHS[d.getMonth()]} ${d.getFullYear()} · ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function siteLabel(site) {
    return site === "realtor" ? "Realtor" : "Zillow";
  }

  function badgeSite(site) {
    return `<span class="badge site ${site || ""}">${siteLabel(site)}</span>`;
  }

  function badgeStatus(status) {
    return `<span class="badge status ${status || ""}">${status || "—"}</span>`;
  }

  function setLive(active) {
    els.liveDot.classList.toggle("busy", Boolean(active));
    els.liveLabel.textContent = active ? `Job running · ${active}` : "System ready";
  }

  function setStatus(text, kind = "info") {
    els.statusBox.textContent = text || "";
    els.statusBox.className = `status-box ${kind}`;
  }

  function calcProgress(run) {
    const requested = Math.max(1, Number(run.requested_agents) || 0);
    const scraped = Math.max(0, Number(run.scraped_agents) || 0);
    const status = run.status || "";

    if (status === "completed") {
      return { pct: 100, scraped, requested, mode: "done" };
    }
    if (status === "failed") {
      const pct = Math.min(99, Math.round((scraped / requested) * 100));
      return { pct, scraped, requested, mode: "failed" };
    }

    let pct = Math.min(99, Math.round((scraped / requested) * 100));

    // Soft simulation while waiting for first real agents
    if (status === "queued") {
      pct = Math.max(pct, 2);
    } else if (status === "running" && scraped <= 0) {
      const started = state.progressStartedAt || Date.now();
      state.progressStartedAt = started;
      const elapsedSec = (Date.now() - started) / 1000;
      const soft = Math.min(12, 3 + Math.floor(elapsedSec / 8));
      pct = Math.max(pct, soft);
    } else if (scraped > 0) {
      state.progressStartedAt = state.progressStartedAt || Date.now();
    }

    // Never go backwards visually
    pct = Math.max(pct, state.lastProgressPct || 0);
    if (status === "running" || status === "queued") {
      pct = Math.min(pct, 99);
    }
    return { pct, scraped, requested, mode: scraped > 0 ? "actual" : "sim" };
  }

  function applyProgress(run) {
    const box = document.getElementById("scrapeProgress");
    const side = document.getElementById("sidebarProgress");
    if (!run || !["queued", "running", "completed", "failed"].includes(run.status)) {
      clearProgress();
      return;
    }

    const { pct, scraped, requested, mode } = calcProgress(run);
    state.lastProgressPct = pct;
    state.activeRunId = run.run_id;

    const metaBits = [];
    if (run.site) metaBits.push(String(run.site));
    if (run.location) metaBits.push(String(run.location));
    metaBits.push(`${scraped}/${requested} agents`);
    if (run.pages_scraped) metaBits.push(`page ${run.pages_scraped}`);
    const meta = metaBits.join(" · ");

    const title = run.status === "completed"
      ? "Scrape complete"
      : run.status === "failed"
        ? "Scrape failed"
        : run.status === "queued"
          ? "Job queued"
          : "Scraping in progress";

    const sub = mode === "sim"
      ? "Preparing browser and collecting first leads…"
      : mode === "done"
        ? "All requested agents processed"
        : "Live progress from this run";

    if (side) {
      side.hidden = false;
      const pctEl = document.getElementById("sidebarProgressPct");
      const fillEl = document.getElementById("sidebarProgressFill");
      const labelEl = document.getElementById("sidebarProgressLabel");
      const metaEl = document.getElementById("sidebarProgressMeta");
      if (pctEl) pctEl.textContent = `${pct}%`;
      if (fillEl) fillEl.style.width = `${pct}%`;
      if (labelEl) labelEl.textContent = run.status === "queued" ? "Queued" : "Scraping";
      if (metaEl) metaEl.textContent = meta;
    }

    if (box) {
      box.hidden = false;
      const pctEl = document.getElementById("scrapeProgressPct");
      const fillEl = document.getElementById("scrapeProgressFill");
      const titleEl = document.getElementById("scrapeProgressTitle");
      const subEl = document.getElementById("scrapeProgressSub");
      const metaEl = document.getElementById("scrapeProgressMeta");
      if (pctEl) pctEl.textContent = `${pct}%`;
      if (fillEl) fillEl.style.width = `${pct}%`;
      if (titleEl) titleEl.textContent = title;
      if (subEl) subEl.textContent = sub;
      if (metaEl) metaEl.textContent = meta;
    }
  }

  function clearProgress() {
    state.progressStartedAt = null;
    state.lastProgressPct = 0;
    const box = document.getElementById("scrapeProgress");
    const side = document.getElementById("sidebarProgress");
    if (box) box.hidden = true;
    if (side) side.hidden = true;
    ["scrapeProgressFill", "sidebarProgressFill"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.style.width = "0%";
    });
  }

  function updateHint() {
    const s = parseInt(els.rangeStart.value, 10) || 1;
    const e = parseInt(els.rangeEnd.value, 10) || 100;
    const count = Math.max(0, e - s + 1);
    els.rangeHint.innerHTML = `Scraping agents <strong>${s}–${e}</strong> · ${count} leads`;
  }

  function selectedSite() {
    const checked = document.querySelector("input[name='site']:checked");
    return checked ? checked.value : "zillow";
  }

  function updateLocationHint() {
    const site = selectedSite();
    const input = els.locationInput;
    const hint = els.locationHint;
    if (!input || !hint) return;
    if (site === "realtor") {
      input.placeholder = "e.g. New York, NY · Austin TX · 78701";
      hint.hidden = false;
      hint.textContent =
        "Realtor: City, ST (comma optional), full state name, or 5-digit ZIP.";
    } else {
      input.placeholder = "e.g. Austin, TX or 10001";
      hint.hidden = true;
      hint.textContent = "";
    }
  }

  function destroyChart(key) {
    if (state.charts[key]) {
      state.charts[key].destroy();
      delete state.charts[key];
    }
  }

  function renderCharts(charts, stats = {}) {
    if (typeof Chart === "undefined") return;

    const timeline = charts?.agents_timeline || { labels: [], values: [] };
    const status = charts?.status || { labels: [], values: [] };
    const sites = charts?.sites || { labels: [], values: [] };

    const totalRuns = Number(stats.total_runs || 0);
    const completed = Number(stats.completed_runs || 0);
    const agents = Number(stats.total_agents_scraped || 0);
    const completedPct = totalRuns ? Math.round((completed / totalRuns) * 100) : 0;
    const leadsPct = Math.min(100, Math.round((agents / Math.max(totalRuns * 50, 1)) * 100));

    const pctCompletedEl = $("#progCompletedPct");
    const pctLeadsEl = $("#progLeadsPct");
    const barCompleted = $("#progCompletedBar");
    const barLeads = $("#progLeadsBar");
    const trendEl = $("#chartTrend");

    if (pctCompletedEl) pctCompletedEl.textContent = `${completedPct}%`;
    if (pctLeadsEl) pctLeadsEl.textContent = `${leadsPct}%`;
    if (barCompleted) barCompleted.style.width = `${completedPct}%`;
    if (barLeads) barLeads.style.width = `${leadsPct}%`;

    const values = timeline.values.length ? timeline.values.map(Number) : [0];
    if (trendEl) {
      if (values.length >= 2) {
        const prev = values[values.length - 2] || 0;
        const last = values[values.length - 1] || 0;
        const delta = prev === 0 ? (last > 0 ? 100 : 0) : ((last - prev) / prev) * 100;
        const abs = Math.abs(delta).toFixed(1);
        if (delta > 0.5) {
          trendEl.className = "hero-chart-trend";
          trendEl.textContent = `↑ ${abs}% Increased lead volume`;
        } else if (delta < -0.5) {
          trendEl.className = "hero-chart-trend is-down";
          trendEl.textContent = `↓ ${abs}% Lower than previous day`;
        } else {
          trendEl.className = "hero-chart-trend is-flat";
          trendEl.textContent = "→ Stable lead volume";
        }
      } else if (values[0] > 0) {
        trendEl.className = "hero-chart-trend";
        trendEl.textContent = `↑ ${values[0]} agents collected recently`;
      } else {
        trendEl.className = "hero-chart-trend is-flat";
        trendEl.textContent = "Awaiting scrape volume";
      }
    }

    destroyChart("timeline");
    destroyChart("status");
    destroyChart("sites");

    const commonOpts = {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      animation: { duration: 1000, easing: "easeInOutQuart" },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#1e2433",
          titleFont: { family: "Plus Jakarta Sans", weight: "700" },
          bodyFont: { family: "Plus Jakarta Sans" },
          padding: 12,
          cornerRadius: 10,
          displayColors: false,
        },
      },
    };

    // Smooth sparkline: expand a single day into a flowing curve ending at real value
    let lineLabels = timeline.labels.length ? [...timeline.labels] : ["Start"];
    let lineValues = values;
    if (lineValues.length === 1) {
      const seed = Math.max(lineValues[0], 0);
      lineLabels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
      if (seed > 0) {
        lineValues = [0.35, 0.55, 0.42, 0.78, 0.62, 0.95, 1].map((r) => Math.max(1, Math.round(seed * r)));
      } else {
        lineValues = [6, 14, 9, 22, 16, 28, 18];
      }
    }

    const timelineCanvas = $("#chartTimeline");
    const tlCtx = timelineCanvas.getContext("2d");
    const h = timelineCanvas.parentNode.clientHeight || 190;
    const tlFill = tlCtx.createLinearGradient(0, 0, 0, h);
    tlFill.addColorStop(0, "rgba(15, 159, 156, 0.32)");
    tlFill.addColorStop(0.55, "rgba(15, 159, 156, 0.1)");
    tlFill.addColorStop(1, "rgba(15, 159, 156, 0.01)");

    state.charts.timeline = new Chart(timelineCanvas, {
      type: "line",
      data: {
        labels: lineLabels,
        datasets: [{
          data: lineValues,
          borderColor: chartColors.teal,
          backgroundColor: tlFill,
          fill: true,
          tension: 0.48,
          pointRadius: 0,
          pointHoverRadius: 5,
          pointHoverBackgroundColor: "#fff",
          pointHoverBorderColor: chartColors.teal,
          pointHoverBorderWidth: 2,
          borderWidth: 2.4,
          borderCapStyle: "round",
        }],
      },
      options: {
        ...commonOpts,
        layout: { padding: { top: 8, bottom: 0, left: 0, right: 0 } },
        animations: {
          y: {
            duration: 1200,
            easing: "easeOutCubic",
            from: (ctx) => (ctx.chart?.scales?.y ? ctx.chart.scales.y.getPixelForValue(0) : 0),
            delay: (ctx) => ctx.dataIndex * 35,
          },
        },
        scales: {
          x: {
            display: false,
            grid: { display: false },
          },
          y: {
            display: false,
            beginAtZero: true,
            grid: { display: false },
          },
        },
      },
    });

    state.charts.status = new Chart($("#chartStatus"), {
      type: "doughnut",
      data: {
        labels: status.labels,
        datasets: [{
          data: status.values,
          backgroundColor: [chartColors.teal, chartColors.rose, chartColors.warm],
          borderWidth: 3,
          borderColor: "#ffffff",
          borderRadius: 6,
          hoverOffset: 8,
        }],
      },
      options: {
        ...commonOpts,
        cutout: "72%",
        animation: { animateRotate: true, animateScale: true, duration: 1000, easing: "easeOutQuart" },
        plugins: {
          ...commonOpts.plugins,
          legend: {
            display: true,
            position: "bottom",
            labels: { boxWidth: 8, usePointStyle: true, font: { family: "Plus Jakarta Sans", size: 10, weight: "600" }, padding: 12 },
          },
        },
      },
    });

    state.charts.sites = new Chart($("#chartSites"), {
      type: "bar",
      data: {
        labels: sites.labels,
        datasets: [{
          data: sites.values,
          backgroundColor: [chartColors.teal, chartColors.warm],
          hoverBackgroundColor: [chartColors.accentHover, "#c96a32"],
          borderRadius: 10,
          borderSkipped: false,
          maxBarThickness: 40,
        }],
      },
      options: {
        ...commonOpts,
        animation: { duration: 900, easing: "easeOutQuart", delay: (ctx) => ctx.dataIndex * 140 },
        scales: {
          x: {
            grid: { display: false },
            ticks: { color: "#9aa3b5", font: { family: "Plus Jakarta Sans", weight: "600", size: 10 } },
          },
          y: {
            beginAtZero: true,
            border: { display: false },
            grid: { color: chartColors.grid, drawTicks: false },
            ticks: { color: "#9aa3b5", precision: 0, font: { family: "Plus Jakarta Sans", size: 10 }, padding: 6 },
          },
        },
      },
    });
  }

  function renderRecent(runs) {
    const rows = (runs || []).slice(0, 5);
    if (!rows.length) {
      els.recentTableBody.innerHTML = `<tr><td colspan="7" class="empty-cell">No runs yet. Start your first scrape.</td></tr>`;
      return;
    }
    els.recentTableBody.innerHTML = rows.map((run, i) => `
      <tr>
        <td class="num-cell">${i + 1}</td>
        <td>${badgeSite(run.site)}</td>
        <td>${escapeHtml(run.location || "—")}</td>
        <td class="num-cell">${run.range_start ?? "—"} – ${run.range_end ?? "—"}</td>
        <td class="num-cell">${run.scraped_agents ?? 0}</td>
        <td>${badgeStatus(run.status)}</td>
        <td class="date-cell">${fmtDate(run.created_at)}</td>
      </tr>
    `).join("");
  }

  async function loadDashboard() {
    try {
      const res = await fetch("/api/dashboard", { cache: "no-store" });
      const data = await res.json();
      $("#mTotalRuns").textContent = data.total_runs ?? 0;
      $("#mCompleted").textContent = data.completed_runs ?? 0;
      $("#mAgents").textContent = data.total_agents_scraped ?? 0;
      $("#mActive").textContent = data.active_runs ?? 0;
      state.activeRunId = data.active_run_id;
      setLive(data.active_run_id);
      if (data.active_run_id && !state.poller) {
        pollRun(`/api/runs/${encodeURIComponent(data.active_run_id)}`);
      } else if (!data.active_run_id) {
        clearProgress();
      }
      renderCharts(data.charts || {}, {
        total_runs: data.total_runs,
        completed_runs: data.completed_runs,
        total_agents_scraped: data.total_agents_scraped,
      });
      renderRecent(data.latest_runs || []);
    } catch (err) {
      els.recentTableBody.innerHTML = `<tr><td colspan="7" class="empty-cell">Could not load dashboard.</td></tr>`;
    }
  }

  async function loadRuns() {
    const params = new URLSearchParams();
    const q = els.filterQ.value.trim();
    const site = els.filterSite.value;
    const status = els.filterStatus.value;
    if (q) params.set("q", q);
    if (site) params.set("site", site);
    if (status) params.set("status", status);

    els.dataTableBody.innerHTML = `<tr><td colspan="9" class="empty-cell">Loading datasets…</td></tr>`;
    try {
      const res = await fetch(`/api/runs?${params.toString()}`, { cache: "no-store" });
      const data = await res.json();
      const runs = data.runs || [];
      if (!runs.length) {
        els.dataTableBody.innerHTML = `<tr><td colspan="9" class="empty-cell">No datasets match your filters.</td></tr>`;
        return;
      }
      els.dataTableBody.innerHTML = runs.map((run) => {
        const file = run.file_name || "—";
        const canDownload = run.status === "completed" && run.file_name;
        return `
          <tr>
            <td class="num-cell">${run.serial}</td>
            <td class="file-cell" title="${escapeAttr(file)}">${escapeHtml(file)}</td>
            <td>${badgeSite(run.site)}</td>
            <td>${escapeHtml(run.location || "—")}</td>
            <td class="date-cell">${fmtDate(run.created_at)}</td>
            <td class="num-cell">${run.range_start ?? "—"} – ${run.range_end ?? "—"}</td>
            <td class="num-cell">${run.scraped_agents ?? 0}</td>
            <td>${badgeStatus(run.status)}</td>
            <td>
              <span class="actions">
                ${canDownload ? `<a class="btn-link" href="${run.download_url}">Download</a>` : `<span class="btn-link disabled">Download</span>`}
                <button class="btn-link" type="button" data-detail="${escapeAttr(run.run_id)}">View Detail</button>
                ${!["queued", "running"].includes(run.status)
                  ? `<button class="btn-link danger" type="button" data-delete="${escapeAttr(run.run_id)}">Delete</button>`
                  : ""}
              </span>
            </td>
          </tr>
        `;
      }).join("");
    } catch (err) {
      els.dataTableBody.innerHTML = `<tr><td colspan="9" class="empty-cell">Failed to load data.</td></tr>`;
    }
  }

  function renderDetailPage() {
    const agents = state.detailAgents;
    const total = agents.length;
    const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    state.detailPage = Math.min(Math.max(1, state.detailPage), totalPages);

    if (!total) {
      els.detailTableBody.innerHTML = `<tr><td colspan="7" class="empty-cell">No agent rows found for this file.</td></tr>`;
      if (els.detailPagination) els.detailPagination.hidden = true;
      return;
    }

    const start = (state.detailPage - 1) * PAGE_SIZE;
    const slice = agents.slice(start, start + PAGE_SIZE);
    const end = start + slice.length;

    els.detailTableBody.innerHTML = slice.map((a, i) => `
      <tr>
        <td class="num-cell">${start + i + 1}</td>
        <td class="ellipsis-cell" title="${escapeAttr(a.name || "")}">${escapeHtml(a.name || "N/A")}</td>
        <td class="num-cell">${escapeHtml(a.phone || "N/A")}</td>
        <td class="ellipsis-cell" title="${escapeAttr(a.email || "")}">${escapeHtml(a.email || "N/A")}</td>
        <td class="ellipsis-cell wide" title="${escapeAttr(a.address || "")}">${escapeHtml(a.address || "N/A")}</td>
        <td>${a.website && a.website !== "N/A"
          ? `<a class="profile-link" href="${escapeAttr(a.website)}" target="_blank" rel="noopener">Visit</a>`
          : "N/A"}</td>
        <td>${a.profile_url && a.profile_url !== "N/A"
          ? `<a class="profile-link" href="${escapeAttr(a.profile_url)}" target="_blank" rel="noopener">Open</a>`
          : "N/A"}</td>
      </tr>
    `).join("");

    if (!els.detailPagination) return;
    if (totalPages <= 1) {
      els.detailPagination.hidden = false;
      els.detailPagination.innerHTML = `
        <div class="pagination-info">Showing ${start + 1}–${end} of ${total} records</div>
        <div class="pagination-controls"></div>
      `;
      return;
    }

    const pages = [];
    for (let p = 1; p <= totalPages; p += 1) {
      if (p === 1 || p === totalPages || Math.abs(p - state.detailPage) <= 1) {
        pages.push(p);
      } else if (pages[pages.length - 1] !== "…") {
        pages.push("…");
      }
    }

    els.detailPagination.hidden = false;
    els.detailPagination.innerHTML = `
      <div class="pagination-info">Showing ${start + 1}–${end} of ${total} records · Page ${state.detailPage}/${totalPages}</div>
      <div class="pagination-controls">
        <button class="page-btn" type="button" data-page="prev" ${state.detailPage === 1 ? "disabled" : ""}>Prev</button>
        ${pages.map((p) => (p === "…"
          ? `<span class="pagination-info">…</span>`
          : `<button class="page-btn ${p === state.detailPage ? "active" : ""}" type="button" data-page="${p}">${p}</button>`
        )).join("")}
        <button class="page-btn" type="button" data-page="next" ${state.detailPage === totalPages ? "disabled" : ""}>Next</button>
      </div>
    `;
  }

  async function loadDetail(runId) {
    state.detailRunId = runId;
    state.detailPage = 1;
    state.detailAgents = [];
    els.detailTitle.textContent = "Loading…";
    els.detailMeta.textContent = runId;
    els.detailTableBody.innerHTML = `<tr><td colspan="7" class="empty-cell">Loading leads…</td></tr>`;
    els.detailDownload.style.display = "none";
    if (els.detailPagination) els.detailPagination.hidden = true;

    try {
      const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/agents`, { cache: "no-store" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to load detail");

      const run = data.run || {};
      els.detailTitle.textContent = run.file_name || run.run_id || "Run Detail";
      els.detailMeta.textContent = `${siteLabel(run.site)} · ${run.location || "—"} · ${fmtDate(run.created_at)}`;

      els.detailStats.innerHTML = `
        <div class="detail-stat"><span>Status</span><strong>${run.status || "—"}</strong></div>
        <div class="detail-stat"><span>Data Range</span><strong>${run.range_start ?? "—"} – ${run.range_end ?? "—"}</strong></div>
        <div class="detail-stat"><span>Agents</span><strong>${data.total ?? 0}</strong></div>
        <div class="detail-stat"><span>Pages</span><strong>${run.pages_scraped ?? 0}</strong></div>
      `;

      if (run.file_name) {
        els.detailDownload.href = `/download/${encodeURIComponent(run.file_name)}`;
        els.detailDownload.style.display = "inline-flex";
      }

      state.detailAgents = data.agents || [];
      renderDetailPage();
      if (els.main) els.main.scrollTop = 0;
    } catch (err) {
      els.detailMeta.textContent = err.message;
      els.detailTableBody.innerHTML = `<tr><td colspan="7" class="empty-cell">${escapeHtml(err.message)}</td></tr>`;
      if (els.detailPagination) els.detailPagination.hidden = true;
    }
  }

  function setSettingsTab(tab) {
    const next = tab === "password" ? "password" : "details";
    document.querySelectorAll("[data-settings-tab]").forEach((btn) => {
      btn.classList.toggle("active", btn.getAttribute("data-settings-tab") === next);
    });
    document.querySelectorAll("[data-settings-panel]").forEach((panel) => {
      const on = panel.getAttribute("data-settings-panel") === next;
      panel.classList.toggle("active", on);
      panel.hidden = !on;
    });
  }

  async function loadSettings() {
    try {
      const res = await fetch("/api/me", { cache: "no-store" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.logged_in) throw new Error("Could not load profile.");

      const username = data.username || "—";
      const role = data.role || "operator";
      const initial = String(username).trim().charAt(0).toUpperCase() || "U";

      const avatar = $("#settingsAvatar");
      const nameEl = $("#settingsUsername");
      const badge = $("#settingsRoleBadge");
      if (avatar) avatar.textContent = initial;
      if (nameEl) nameEl.textContent = username;
      if (badge) {
        badge.textContent = role;
        badge.className = `badge status ${role === "admin" ? "completed" : "queued"}`;
      }
      const idEl = $("#settingsUserId");
      const userDetail = $("#settingsUsernameDetail");
      const roleDetail = $("#settingsRoleDetail");
      const createdEl = $("#settingsCreated");
      if (idEl) idEl.textContent = data.id ?? "—";
      if (userDetail) userDetail.textContent = username;
      if (roleDetail) roleDetail.textContent = role;
      if (createdEl) createdEl.textContent = data.created_at ? fmtDate(data.created_at) : "—";
      if (els.headerUserName) els.headerUserName.textContent = username;
    } catch (err) {
      const nameEl = $("#settingsUsername");
      if (nameEl) nameEl.textContent = err.message;
    }
  }

  function openAddUserModal() {
    if (!els.addUserModal) return;
    if (els.usersStatusBox) {
      els.usersStatusBox.textContent = "";
      els.usersStatusBox.className = "status-box";
    }
    els.addUserModal.hidden = false;
    els.newUsername?.focus();
  }

  function closeAddUserModal() {
    if (!els.addUserModal) return;
    els.addUserModal.hidden = true;
  }

  async function loadUsers() {
    if (!els.usersTableBody) return;
    els.usersTableBody.innerHTML = `<tr><td colspan="5" class="empty-cell">Loading users…</td></tr>`;

    try {
      const res = await fetch("/api/users", { cache: "no-store" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "Failed to load users.");

      const users = data.users || [];
      if (!users.length) {
        els.usersTableBody.innerHTML = `<tr><td colspan="5" class="empty-cell">No users found.</td></tr>`;
        return;
      }

      els.usersTableBody.innerHTML = users.map((u, i) => `
        <tr>
          <td class="num-cell">${i + 1}</td>
          <td>${escapeHtml(u.username || "—")}</td>
          <td><span class="badge site">${escapeHtml(u.role || "operator")}</span></td>
          <td class="date-cell">${fmtDate(u.created_at)}</td>
          <td>
            <span class="actions">
              <button class="btn-link danger" type="button" data-delete-user="${escapeAttr(u.id)}">Delete</button>
            </span>
          </td>
        </tr>
      `).join("");
    } catch (err) {
      els.usersTableBody.innerHTML = `<tr><td colspan="5" class="empty-cell">${escapeHtml(err.message)}</td></tr>`;
    }
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function escapeAttr(value) {
    return escapeHtml(value).replaceAll("'", "&#39;");
  }

  function pollRun(statusUrl) {
    if (state.poller) clearInterval(state.poller);
    state.progressStartedAt = Date.now();
    state.lastProgressPct = 0;
    state.poller = setInterval(async () => {
      try {
        const res = await fetch(statusUrl, { cache: "no-store" });
        const data = await res.json();
        if (data.status === "running" || data.status === "queued") {
          const pctInfo = calcProgress(data);
          setStatus(
            `Run ${data.run_id} is ${data.status}… ${pctInfo.pct}% (${pctInfo.scraped}/${pctInfo.requested})`,
            "info"
          );
          setLive(data.run_id);
          applyProgress(data);
          return;
        }
        clearInterval(state.poller);
        state.poller = null;
        els.startBtn.disabled = false;
        if (data.status === "completed") {
          applyProgress({ ...data, status: "completed" });
          setStatus(`Completed. ${data.file_name || "File ready."}`, "ok");
          setLive(null);
          setTimeout(() => {
            clearProgress();
            setView("data");
          }, 1200);
          return;
        }
        applyProgress({ ...data, status: "failed" });
        setLive(null);
        setStatus(data.error || "Run failed.", "error");
        setTimeout(() => clearProgress(), 2500);
      } catch (_) {
        clearInterval(state.poller);
        state.poller = null;
        els.startBtn.disabled = false;
        setLive(null);
        clearProgress();
        setStatus("Could not fetch run status.", "error");
      }
    }, 2000);
  }

  document.querySelectorAll("[data-settings-tab]").forEach((btn) => {
    btn.addEventListener("click", () => setSettingsTab(btn.getAttribute("data-settings-tab")));
  });

  els.changePasswordForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const box = els.passwordStatusBox;
    if (box) {
      box.textContent = "Updating password…";
      box.className = "status-box info";
    }
    const payload = Object.fromEntries(new FormData(els.changePasswordForm).entries());
    try {
      const res = await fetch("/api/me/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "Could not update password.");
      els.changePasswordForm.reset();
      if (box) {
        box.textContent = data.message || "Password updated.";
        box.className = "status-box ok";
      }
      if (typeof Swal !== "undefined") {
        Swal.fire({
          toast: true,
          position: "top-end",
          icon: "success",
          title: "Password updated",
          showConfirmButton: false,
          timer: 2200,
        });
      }
    } catch (err) {
      if (box) {
        box.textContent = err.message;
        box.className = "status-box error";
      }
    }
  });

  // Events
  document.querySelectorAll("[data-nav]").forEach((el) => {
    el.addEventListener("click", () => setView(el.getAttribute("data-nav")));
  });

  els.logoutBtn?.addEventListener("click", async () => {
    try {
      await fetch("/api/logout", { method: "POST" });
    } catch (_) {
      // ignore
    }
    window.location.href = "/login";
  });

  els.openAddUserBtn?.addEventListener("click", openAddUserModal);

  document.querySelectorAll("[data-close-modal]").forEach((el) => {
    el.addEventListener("click", closeAddUserModal);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && els.addUserModal && !els.addUserModal.hidden) {
      closeAddUserModal();
    }
  });

  els.addUserForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const statusEl = els.usersStatusBox;
    if (statusEl) {
      statusEl.textContent = "Creating user…";
      statusEl.className = "status-box info";
    }

    const payload = Object.fromEntries(new FormData(els.addUserForm).entries());
    try {
      const res = await fetch("/api/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "Could not add user.");
      els.addUserForm.reset();
      closeAddUserModal();
      loadUsers();
    } catch (err) {
      if (statusEl) {
        statusEl.textContent = err.message;
        statusEl.className = "status-box error";
      }
    }
  });

  $("#refreshBtn")?.addEventListener("click", () => {
    if (state.view === "dashboard") loadDashboard();
    else if (state.view === "data") loadRuns();
    else if (state.view === "userhub") loadUsers();
    else if (state.view === "settings") loadSettings();
    else if (state.view === "detail" && state.detailRunId) {
      loadDetail(state.detailRunId);
    }
  });

  $("#applyFilters")?.addEventListener("click", loadRuns);
  [els.filterQ, els.filterSite, els.filterStatus].forEach((el) => {
    el?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") loadRuns();
    });
  });

  document.addEventListener("click", async (e) => {
    const pageBtn = e.target.closest("#detailPagination [data-page]");
    if (pageBtn && !pageBtn.disabled) {
      const totalPages = Math.max(1, Math.ceil(state.detailAgents.length / PAGE_SIZE));
      const raw = pageBtn.getAttribute("data-page");
      if (raw === "prev") state.detailPage -= 1;
      else if (raw === "next") state.detailPage += 1;
      else state.detailPage = Number(raw) || 1;
      state.detailPage = Math.min(Math.max(1, state.detailPage), totalPages);
      renderDetailPage();
      if (els.main) els.main.scrollTop = 0;
      return;
    }

    const detailBtn = e.target.closest("[data-detail]");
    if (detailBtn) {
      setView("detail", { runId: detailBtn.getAttribute("data-detail") });
      return;
    }

    const deleteBtn = e.target.closest("[data-delete]");
    if (deleteBtn) {
      const runId = deleteBtn.getAttribute("data-delete");
      const ok = await confirmDelete({
        title: "Delete this file?",
        text: `Run ${runId} and its scraped data will be removed permanently.`,
        confirmText: "Delete file",
      });
      if (!ok) return;
      const res = await fetch(`/api/runs/${encodeURIComponent(runId)}`, { method: "DELETE" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        if (typeof Swal !== "undefined") {
          Swal.fire({ icon: "error", title: "Delete failed", text: data.error || "Unable to delete run." });
        } else {
          alert(data.error || "Delete failed");
        }
        return;
      }
      await toastSuccess("File deleted");
      loadRuns();
      if (state.view === "dashboard") loadDashboard();
      return;
    }

    const deleteUserBtn = e.target.closest("[data-delete-user]");
    if (deleteUserBtn) {
      const userId = deleteUserBtn.getAttribute("data-delete-user");
      const ok = await confirmDelete({
        title: "Delete this user?",
        text: "They will lose access immediately. This cannot be undone.",
        confirmText: "Delete user",
      });
      if (!ok) return;
      try {
        const res = await fetch(`/api/users/${encodeURIComponent(userId)}`, { method: "DELETE" });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || "Delete failed");
        await toastSuccess("User deleted");
        loadUsers();
      } catch (err) {
        if (typeof Swal !== "undefined") {
          Swal.fire({ icon: "error", title: "Delete failed", text: err.message });
        } else {
          alert(err.message);
        }
      }
    }
  });

  els.rangeStart?.addEventListener("input", updateHint);
  els.rangeEnd?.addEventListener("input", updateHint);
  updateHint();
  els.siteRadios?.forEach((radio) => {
    radio.addEventListener("change", updateLocationHint);
  });
  updateLocationHint();

  els.form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    els.startBtn.disabled = true;
    setStatus("Submitting run…", "info");
    const payload = Object.fromEntries(new FormData(els.form).entries());
    if (typeof payload.location === "string") {
      payload.location = payload.location.trim();
    }
    try {
      const res = await fetch("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Unable to start run.");
      setStatus(`Run ${data.run_id} created (${data.range}). Starting…`, "info");
      setLive(data.run_id);
      applyProgress({
        run_id: data.run_id,
        status: "queued",
        scraped_agents: 0,
        requested_agents: Number(payload.range_end || 100) - Number(payload.range_start || 1) + 1,
        site: payload.site,
        location: payload.location,
      });
      pollRun(data.status_url);
    } catch (err) {
      els.startBtn.disabled = false;
      setStatus(err.message, "error");
    }
  });

  // Boot from clean path (also migrate old #hash URLs)
  window.addEventListener("popstate", () => {
    const parsed = viewFromPath(location.pathname);
    setView(parsed.view, { runId: parsed.runId, skipHistory: true });
  });

  if (location.hash) {
    const legacy = location.hash.replace(/^#/, "");
    if (legacy.startsWith("detail/")) {
      setView("detail", { runId: legacy.split("/")[1], replace: true });
    } else if (["scrape", "data", "dashboard", "userhub", "settings"].includes(legacy)) {
      setView(legacy, { replace: true });
    } else {
      setView("dashboard", { replace: true });
    }
  } else {
    const parsed = viewFromPath(location.pathname);
    setView(parsed.view, { runId: parsed.runId, replace: true });
  }
})();
