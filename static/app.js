(() => {
  const PAGE_SIZE = 30;

  const state = {
    view: "dashboard",
    charts: {},
    activeRunId: null,
    focusRunId: null,          // progress bar tracks this run only
    activeRunIds: [],          // all currently running run IDs
    pollers: {},               // { run_id: intervalId }
    poller: null,              // legacy single-poller compat
    progressByRun: {},         // { run_id: lastPct }
    detailAgents: [],
    detailPage: 1,
    detailRunId: null,
    progressStartedAt: null,
    progressStartedAtByRun: {},
    lastProgressPct: 0,
    coveredRanges: [],         // ranges already scraped for current site+location
    runMeta: {},               // { run_id: {site, location, range_start, range_end, ...} }
    pollerFailures: {},        // { run_id: consecutive fetch failures }
    agentsSite: "zillow",
    agentsPage: 1,
    agentsLimit: 50,
    agentsSearch: "",
    agentsTotal: 0,
    agentsRows: [],
    agentsSelected: new Set(),
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
    agents: {
      title: "Agents",
      eyebrow: "Directory",
      sub: "Global repository of scraped agents, grouped by site",
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
    agentsSiteSelect: document.getElementById("agentsSiteSelect"),
    agentsSearchInput: document.getElementById("agentsSearchInput"),
    agentsSearchBtn: document.getElementById("agentsSearchBtn"),
    agentsExportBtn: document.getElementById("agentsExportBtn"),
    agentsExportAllBtn: document.getElementById("agentsExportAllBtn"),
    agentsTableBody: document.getElementById("agentsTableBody"),
    agentsSelectAll: document.getElementById("agentsSelectAll"),
    agentsPagination: document.getElementById("agentsPagination"),
    agentsPrevBtn: document.getElementById("agentsPrevBtn"),
    agentsPageInfo: document.getElementById("agentsPageInfo"),
    agentsNextBtn: document.getElementById("agentsNextBtn"),
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
    if (["scrape", "data", "userhub", "settings", "agents"].includes(view)) return `/${view}`;
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
    if (["scrape", "data", "userhub", "settings", "agents"].includes(parts[0])) {
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
    if (view === "agents") loadAgents();
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
    const count = (state.activeRunIds || []).length;
    const busy = Boolean(active) || count > 0;
    els.liveDot.classList.toggle("busy", busy);
    if (count > 1) {
      els.liveLabel.textContent = `${count} jobs running`;
    } else if (count === 1 || active) {
      els.liveLabel.textContent = `Job running · ${active || state.activeRunIds[0]}`;
    } else {
      els.liveLabel.textContent = "System ready";
    }
  }

  function setStatus(text, kind = "info") {
    els.statusBox.textContent = text || "";
    els.statusBox.className = `status-box ${kind}`;
  }

  function calcProgress(run) {
    const requested = Math.max(1, Number(run.requested_agents) || 0);
    const scraped = Math.max(0, Number(run.scraped_agents) || 0);
    const pages = Math.max(0, Number(run.pages_scraped) || 0);
    const rangeStart = Math.max(1, Number(run.range_start) || 1);
    const rangeEnd = Math.max(rangeStart, Number(run.range_end) || requested);
    const status = run.status || "";
    const runId = run.run_id || "";
    const span = Math.max(1, rangeEnd - rangeStart + 1);
    const targetPage = Math.max(1, Math.ceil(rangeStart / 16));
    const endPage = Math.max(targetPage, Math.ceil(rangeEnd / 16));

    if (status === "completed") {
      return { pct: 100, scraped, requested, mode: "done" };
    }
    if (status === "cancelled") {
      const pct = Math.min(99, Math.round((scraped / span) * 100));
      return { pct, scraped, requested: span, mode: "failed" };
    }
    if (status === "failed") {
      const pct = Math.min(99, Math.round((scraped / span) * 100));
      return { pct, scraped, requested: span, mode: "failed" };
    }

    // Real scrape progress inside the requested range
    let pct = Math.min(99, Math.round((scraped / span) * 100));

    if (status === "queued") {
      pct = Math.max(pct, 2);
    } else if (status === "running" && scraped <= 0) {
      // Soft bar only — 18% means "still starting", NOT real progress.
      // High ranges: use pages_scraped while skipping toward range_start.
      if (pages > 0 && rangeStart > 1) {
        const skipPct = Math.min(45, Math.round((pages / endPage) * 45));
        pct = Math.max(pct, Math.max(8, skipPct));
      } else if (pages > 0) {
        pct = Math.max(pct, Math.min(12, 4 + pages * 2));
      } else {
        const startedMap = state.progressStartedAtByRun || {};
        const started = runId
          ? (startedMap[runId] || (startedMap[runId] = Date.now()))
          : Date.now();
        const elapsedSec = (Date.now() - started) / 1000;
        const soft = Math.min(18, 3 + Math.floor(elapsedSec / 8));
        pct = Math.max(pct, soft);
      }
    } else if (scraped > 0) {
      if (runId && state.progressStartedAtByRun) {
        state.progressStartedAtByRun[runId] = state.progressStartedAtByRun[runId] || Date.now();
      }
      state.progressStartedAt = state.progressStartedAt || Date.now();
    }

    // Never go backwards for THIS run only (parallel-safe)
    const prev = (runId && state.progressByRun[runId]) || 0;
    pct = Math.max(pct, prev);
    if (status === "running" || status === "queued") {
      pct = Math.min(pct, 99);
    }
    return { pct, scraped, requested: span, mode: scraped > 0 ? "actual" : "sim" };
  }

  function applyProgress(run) {
    // Keep per-run pct cache updated, then paint the AVERAGE of all active runs
    if (run && run.run_id && ["queued", "running", "completed", "failed", "cancelled"].includes(run.status)) {
      const { pct } = calcProgress(run);
      state.progressByRun[run.run_id] = pct;
      rememberRunMeta(run);
    }
    paintAverageProgress(run);
  }

  function paintAverageProgress(hintRun) {
    const box = document.getElementById("scrapeProgress");
    const side = document.getElementById("sidebarProgress");

    const activeIds = (state.activeRunIds || []).filter((id) => state.pollers[id]);
    if (!activeIds.length) {
      // Single finished run paint (completion flash)
      if (hintRun && ["completed", "failed", "cancelled"].includes(hintRun.status)) {
        const { pct, scraped, requested } = calcProgress(hintRun);
        state.lastProgressPct = pct;
        const meta = [
          hintRun.site,
          hintRun.location,
          `${scraped}/${requested} agents`,
        ].filter(Boolean).join(" · ");
        const title = hintRun.status === "completed"
          ? "Scrape complete"
          : hintRun.status === "failed"
            ? "Scrape failed"
            : "Scrape cancelled";
        if (side) {
          side.hidden = false;
          const pctEl = document.getElementById("sidebarProgressPct");
          const fillEl = document.getElementById("sidebarProgressFill");
          const labelEl = document.getElementById("sidebarProgressLabel");
          const metaEl = document.getElementById("sidebarProgressMeta");
          if (pctEl) pctEl.textContent = `${pct}%`;
          if (fillEl) fillEl.style.width = `${pct}%`;
          if (labelEl) labelEl.textContent = title;
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
          if (subEl) subEl.textContent = hintRun.error || (hintRun.status === "completed" ? "All requested agents processed" : "");
          if (metaEl) metaEl.textContent = meta;
        }
      }
      return;
    }

    let scrapedSum = 0;
    let requestedSum = 0;
    let pctSum = 0;
    const sites = new Set();
    const locs = new Set();

    activeIds.forEach((id) => {
      const meta = state.runMeta[id] || { run_id: id, status: "running" };
      const info = calcProgress({
        ...meta,
        run_id: id,
        status: meta.status || "running",
        scraped_agents: meta.scraped_agents,
        pages_scraped: meta.pages_scraped,
        range_start: meta.range_start,
        range_end: meta.range_end,
        requested_agents: meta.requested_agents,
      });
      state.progressByRun[id] = info.pct;
      pctSum += info.pct;
      scrapedSum += info.scraped;
      requestedSum += info.requested;
      if (meta.site) sites.add(meta.site);
      if (meta.location) locs.add(meta.location);
    });

    const avgPct = Math.round(pctSum / activeIds.length);
    state.lastProgressPct = avgPct;

    const metaBits = [];
    if (sites.size === 1) metaBits.push([...sites][0]);
    if (locs.size === 1) metaBits.push([...locs][0]);
    metaBits.push(`${activeIds.length} sessions`);
    metaBits.push(`${scrapedSum}/${requestedSum} agents`);
    metaBits.push(`avg ${avgPct}%`);
    const waiting = activeIds.filter((id) => {
      const m = state.runMeta[id] || {};
      return (Number(m.scraped_agents) || 0) <= 0 && (Number(m.pages_scraped) || 0) <= 0;
    });
    if (waiting.length && activeIds.length > 1) {
      metaBits.push(`${waiting.length} waiting for 1st agent on other job`);
    } else if (scrapedSum === 0 && avgPct >= 15) {
      metaBits.push("still starting / captcha");
    }
    const meta = metaBits.join(" · ");

    const title = "Scraping in progress";
    const sub = `Average progress across ${activeIds.length} running session${activeIds.length > 1 ? "s" : ""}`;

    if (side) {
      side.hidden = false;
      const pctEl = document.getElementById("sidebarProgressPct");
      const fillEl = document.getElementById("sidebarProgressFill");
      const labelEl = document.getElementById("sidebarProgressLabel");
      const metaEl = document.getElementById("sidebarProgressMeta");
      if (pctEl) pctEl.textContent = `${avgPct}%`;
      if (fillEl) fillEl.style.width = `${avgPct}%`;
      if (labelEl) labelEl.textContent = `${activeIds.length} scraping`;
      if (metaEl) metaEl.textContent = meta;
    }

    if (box) {
      box.hidden = false;
      const pctEl = document.getElementById("scrapeProgressPct");
      const fillEl = document.getElementById("scrapeProgressFill");
      const titleEl = document.getElementById("scrapeProgressTitle");
      const subEl = document.getElementById("scrapeProgressSub");
      const metaEl = document.getElementById("scrapeProgressMeta");
      if (pctEl) pctEl.textContent = `${avgPct}%`;
      if (fillEl) fillEl.style.width = `${avgPct}%`;
      if (titleEl) titleEl.textContent = title;
      if (subEl) subEl.textContent = sub;
      if (metaEl) metaEl.textContent = meta;
    }
  }

  function clearProgress() {
    state.progressStartedAt = null;
    state.lastProgressPct = 0;
    if (!Object.keys(state.pollers || {}).length) {
      state.focusRunId = null;
    }
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
      const activeIds = data.active_run_ids || (data.active_run_id ? [data.active_run_id] : []);
      state.activeRunIds = activeIds;
      state.activeRunId  = activeIds[0] || null;
      if (!state.focusRunId || !activeIds.includes(state.focusRunId)) {
        state.focusRunId = activeIds[0] || null;
      }
      setLive(activeIds.length ? (state.focusRunId || activeIds[0]) : null);
      // Start pollers for any active runs not already being tracked
      activeIds.forEach((id) => {
        if (!state.pollers[id]) {
          startRunPoller(id, `/api/runs/${encodeURIComponent(id)}`);
        }
      });
      // Hydrate site/range meta for the sessions panel
      if (activeIds.length) {
        try {
          const ar = await fetch("/api/runs/active", { cache: "no-store" });
          const ad = await ar.json();
          (ad.runs || []).forEach((run) => rememberRunMeta(run));
        } catch (_) { /* ignore */ }
        renderActiveSessions();
      } else {
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

    els.dataTableBody.innerHTML = `<tr><td colspan="10" class="empty-cell">Loading datasets…</td></tr>`;
    try {
      const res = await fetch(`/api/runs?${params.toString()}`, { cache: "no-store" });
      const data = await res.json();
      const runs = data.runs || [];
      if (!runs.length) {
        els.dataTableBody.innerHTML = `<tr><td colspan="10" class="empty-cell">No datasets match your filters.</td></tr>`;
        return;
      }
      els.dataTableBody.innerHTML = runs.map((run) => {
        const file = run.file_name || "—";
        const canDownload = run.status === "completed" && run.file_name;
        const canLog = !!run.debug_file_name;
        const err = (run.error || "").trim();
        const reason = (run.status === "failed" || run.status === "cancelled")
          ? (err || (run.status === "cancelled" ? "Cancelled by user" : "Unknown error"))
          : (run.status === "completed" && err ? err : "—");
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
            <td class="reason-cell" title="${escapeAttr(reason)}">${escapeHtml(reason)}</td>
            <td>
              <span class="actions">
                ${canDownload ? `<a class="btn-link" href="${run.download_url}">Download</a>` : `<span class="btn-link disabled">Download</span>`}
                ${canLog ? `<a class="btn-link" href="/api/runs/${encodeURIComponent(run.run_id)}/log" title="Download debug log">Log</a>` : ""}
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
      els.dataTableBody.innerHTML = `<tr><td colspan="10" class="empty-cell">Failed to load data.</td></tr>`;
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

    // Remove any previous log viewer
    const oldLog = document.getElementById("detailLogViewer");
    if (oldLog) oldLog.remove();

    try {
      const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/agents`, { cache: "no-store" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to load detail");

      const run = data.run || {};
      els.detailTitle.textContent = run.file_name || run.run_id || "Run Detail";
      els.detailMeta.textContent = `${siteLabel(run.site)} · ${run.location || "—"} · ${fmtDate(run.created_at)}`;

      els.detailStats.innerHTML = `
        <div class="detail-stat"><span>Status</span><strong>${escapeHtml(run.status || "—")}</strong></div>
        <div class="detail-stat"><span>Data Range</span><strong>${run.range_start ?? "—"} – ${run.range_end ?? "—"}</strong></div>
        <div class="detail-stat"><span>Agents</span><strong>${data.total ?? 0}</strong></div>
        <div class="detail-stat"><span>Pages</span><strong>${run.pages_scraped ?? 0}</strong></div>
        ${(run.status === "failed" || run.status === "cancelled") && (run.error || "").trim()
          ? `<div class="detail-stat detail-stat-wide"><span>Failure reason</span><strong>${escapeHtml(run.error)}</strong></div>`
          : ""}
      `;

      if (run.file_name) {
        els.detailDownload.href = `/download/${encodeURIComponent(run.file_name)}`;
        els.detailDownload.style.display = "inline-flex";
      }

      // ── Log viewer (if this run has a log file) ──────────────
      if (run.debug_file_name) {
        const logSection = document.createElement("div");
        logSection.id = "detailLogViewer";
        logSection.style.cssText = "margin-top:24px";
        logSection.innerHTML = `
          <div class="panel-head" style="margin-bottom:10px">
            <div>
              <h2 style="font-size:1rem">Run Log</h2>
              <p style="font-size:.8rem;color:var(--text-muted)">${escapeHtml(run.debug_file_name)}</p>
            </div>
            <div style="display:flex;gap:8px;align-items:center">
              <button class="btn btn-ghost btn-sm" id="refreshLogBtn" type="button">Refresh</button>
              <a class="btn btn-ghost btn-sm" href="/api/runs/${encodeURIComponent(runId)}/log" id="downloadLogBtn">Download Log</a>
              <button class="btn btn-ghost btn-sm" id="deleteLogBtn" type="button" style="color:#dc2626">Delete Log</button>
            </div>
          </div>
          <pre id="logContent" style="
            background:var(--bg-page,#0f1117);
            color:var(--text-secondary,#b0b8cc);
            font-size:.72rem;
            line-height:1.5;
            padding:14px 16px;
            border-radius:10px;
            border:1px solid var(--border,rgba(255,255,255,.08));
            max-height:420px;
            overflow-y:auto;
            white-space:pre-wrap;
            word-break:break-all;
          ">Loading log…</pre>
        `;
        // Insert before the table-wrap
        const tableWrap = document.querySelector("#view-detail .table-wrap");
        if (tableWrap) tableWrap.parentNode.insertBefore(logSection, tableWrap);

        let activeLogPoller = null;

        async function refreshLog() {
          try {
            const lr = await fetch(`/api/runs/${encodeURIComponent(runId)}/log/tail?lines=300`, { cache: "no-store" });
            const ld = await lr.json();
            const pre = document.getElementById("logContent");
            if (!pre) return;
            if (ld.error) {
              pre.textContent = `[Error] ${ld.error}`;
            } else {
              pre.textContent = ld.content || "(empty log)";
              // Auto-scroll to bottom
              pre.scrollTop = pre.scrollHeight;
            }
          } catch (e) {
            const pre = document.getElementById("logContent");
            if (pre) pre.textContent = `[Network error] ${e.message}`;
          }
        }

        refreshLog();
        const refreshBtn = document.getElementById("refreshLogBtn");
        if (refreshBtn) refreshBtn.addEventListener("click", refreshLog);

        const deleteLogBtn = document.getElementById("deleteLogBtn");
        if (deleteLogBtn) {
          deleteLogBtn.addEventListener("click", async () => {
            const ok = await confirmDelete({
              title: "Delete this log file?",
              text: "The debug log will be permanently removed from disk to free up space.",
              confirmText: "Delete log",
            });
            if (!ok) return;
            try {
              const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/log`, { method: "DELETE" });
              const data = await res.json().catch(() => ({}));
              if (!res.ok) throw new Error(data.error || "Failed to delete log.");
              if (activeLogPoller) clearInterval(activeLogPoller);
              const section = document.getElementById("detailLogViewer");
              if (section) section.remove();
              await toastSuccess("Log file deleted");
            } catch (err) {
              if (typeof Swal !== "undefined") {
                Swal.fire({ icon: "error", title: "Delete failed", text: err.message });
              } else {
                alert(err.message);
              }
            }
          });
        }

        // Auto-refresh while run is active
        if (["queued", "running"].includes(run.status)) {
          activeLogPoller = setInterval(async () => {
            await refreshLog();
            // Stop auto-refresh once run is done
            try {
              const sr = await fetch(`/api/runs/${encodeURIComponent(runId)}`, { cache: "no-store" });
              const sd = await sr.json();
              if (!sd.status || !["queued", "running"].includes(sd.status)) {
                clearInterval(activeLogPoller);
              }
            } catch (_) { /* ignore */ }
          }, 5000);
          // Clean up if user navigates away
          const obs = new MutationObserver(() => {
            if (!document.getElementById("detailLogViewer")) {
              clearInterval(activeLogPoller);
              obs.disconnect();
            }
          });
          obs.observe(document.body, { childList: true, subtree: true });
        }
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

  async function loadAgents() {
    if (!els.agentsTableBody) return;
    const site = els.agentsSiteSelect ? els.agentsSiteSelect.value : "zillow";
    state.agentsSite = site;

    const params = new URLSearchParams();
    params.set("site", site);
    params.set("page", state.agentsPage);
    params.set("limit", state.agentsLimit);
    if (state.agentsSearch) params.set("search", state.agentsSearch);

    els.agentsTableBody.innerHTML = `<tr><td colspan="6" class="empty-cell">Loading agents…</td></tr>`;
    if (els.agentsPagination) els.agentsPagination.hidden = true;

    try {
      const res = await fetch(`/api/agents?${params.toString()}`, { cache: "no-store" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "Failed to load agents.");

      state.agentsRows = data.agents || [];
      state.agentsTotal = data.total || 0;

      if (!state.agentsRows.length) {
        els.agentsTableBody.innerHTML = `<tr><td colspan="6" class="empty-cell">No agents found.</td></tr>`;
      } else {
        els.agentsTableBody.innerHTML = state.agentsRows.map((a) => `
          <tr>
            <td><input type="checkbox" class="agent-row-check" data-agent-id="${escapeAttr(a.id)}" ${state.agentsSelected.has(String(a.id)) ? "checked" : ""}></td>
            <td class="ellipsis-cell" title="${escapeAttr(a.name || "")}">${escapeHtml(a.name || "N/A")}</td>
            <td class="num-cell">${escapeHtml(a.phone || "N/A")}</td>
            <td class="ellipsis-cell" title="${escapeAttr(a.email || "")}">${escapeHtml(a.email || "N/A")}</td>
            <td class="ellipsis-cell wide" title="${escapeAttr(a.address || "")}">${escapeHtml(a.address || "N/A")}</td>
            <td class="date-cell">${fmtDate(a.scraped_at)}</td>
          </tr>
        `).join("");
      }

      updateAgentsSelectAllState();

      const totalPages = Math.max(1, Math.ceil(state.agentsTotal / state.agentsLimit));
      if (els.agentsPagination) {
        els.agentsPagination.hidden = false;
        if (els.agentsPageInfo) {
          els.agentsPageInfo.textContent = `Page ${state.agentsPage}/${totalPages} · ${state.agentsTotal} total`;
        }
        if (els.agentsPrevBtn) els.agentsPrevBtn.disabled = state.agentsPage <= 1;
        if (els.agentsNextBtn) els.agentsNextBtn.disabled = state.agentsPage >= totalPages;
      }
    } catch (err) {
      els.agentsTableBody.innerHTML = `<tr><td colspan="6" class="empty-cell">${escapeHtml(err.message)}</td></tr>`;
    }
  }

  function updateAgentsSelectAllState() {
    if (!els.agentsSelectAll) return;
    const rows = state.agentsRows || [];
    if (!rows.length) {
      els.agentsSelectAll.checked = false;
      els.agentsSelectAll.indeterminate = false;
      return;
    }
    const selectedCount = rows.filter((a) => state.agentsSelected.has(String(a.id))).length;
    els.agentsSelectAll.checked = selectedCount === rows.length;
    els.agentsSelectAll.indeterminate = selectedCount > 0 && selectedCount < rows.length;
  }

  async function exportAgents(all) {
    const ids = Array.from(state.agentsSelected);
    if (!all && !ids.length) {
      if (typeof Swal !== "undefined") {
        Swal.fire({ icon: "info", title: "No agents selected", text: "Select at least one agent, or use Export All Filtered." });
      } else {
        alert("Select at least one agent first.");
      }
      return;
    }
    try {
      const res = await fetch("/api/agents/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          site: state.agentsSite,
          all: Boolean(all),
          ids: all ? [] : ids,
          search: state.agentsSearch || "",
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "Export failed.");
      window.location.href = data.download_url;
      await toastSuccess("Export ready — download starting");
    } catch (err) {
      if (typeof Swal !== "undefined") {
        Swal.fire({ icon: "error", title: "Export failed", text: err.message });
      } else {
        alert(err.message);
      }
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

  function refreshViewAfterRunUpdate() {
    if (state.view === "data") loadRuns();
    else if (state.view === "dashboard") loadDashboard();
    else if (state.view === "detail" && state.detailRunId) loadDetail(state.detailRunId);
  }

  // ── Per-run poller (supports multiple concurrent runs) ──────
  function startRunPoller(runId, statusUrl) {
    if (state.pollers[runId]) return; // already polling
    state.progressStartedAt = state.progressStartedAt || Date.now();
    if (!state.activeRunIds.includes(runId)) {
      state.activeRunIds = [...state.activeRunIds, runId];
    }
    // Prefer an explicitly set focus (new submit); otherwise pick this run
    if (!state.focusRunId) state.focusRunId = runId;
    state.pollerFailures[runId] = 0;
    state.pollers[runId] = setInterval(async () => {
      try {
        const res  = await fetch(statusUrl, { cache: "no-store" });
        let data;
        try {
          data = await res.json();
        } catch (_) {
          data = {};
        }

        if (!res.ok) {
          state.pollerFailures[runId] = (state.pollerFailures[runId] || 0) + 1;
          if (state.pollerFailures[runId] >= 8) {
            clearInterval(state.pollers[runId]);
            delete state.pollers[runId];
            setStatus(res.status === 404 ? `Run ${runId} not found.` : "Could not fetch run status.", "error");
            refreshViewAfterRunUpdate();
          }
          return;
        }

        if (data.error && !data.status) {
          state.pollerFailures[runId] = (state.pollerFailures[runId] || 0) + 1;
          if (state.pollerFailures[runId] >= 5) {
            setStatus(data.error || "Could not fetch run status.", "error");
            refreshViewAfterRunUpdate();
          }
          return;
        }

        state.pollerFailures[runId] = 0;

        if (data.status === "running" || data.status === "queued") {
          updateSessionRow(data);
          applyProgress(data);
          const activeCount = Object.keys(state.pollers).length;
          const avg = state.lastProgressPct || 0;
          setStatus(
            activeCount > 1
              ? `${activeCount} sessions running · average ${avg}%`
              : `Run ${data.run_id} is ${data.status}… ${avg}%`,
            "info"
          );
          setLive(state.focusRunId || data.run_id);
          return;
        }

        // Run finished
        clearInterval(state.pollers[runId]);
        delete state.pollers[runId];
        delete state.pollerFailures[runId];
        delete state.progressByRun[runId];
        state.activeRunIds = state.activeRunIds.filter((id) => id !== runId);

        const stillRunning = Object.keys(state.pollers);
        if (state.focusRunId === runId) {
          state.focusRunId = stillRunning[0] || null;
          state.lastProgressPct = state.focusRunId
            ? (state.progressByRun[state.focusRunId] || 0)
            : 0;
        }

        updateSessionRow(data);
        if (!stillRunning.length) {
          renderActiveSessions();
        }

        if (data.status === "completed") {
          if (!stillRunning.length) {
            applyProgress({ ...data, status: "completed" });
            setStatus(`Run ${runId} complete — ${data.file_name || "file ready"}`, "ok");
            setLive(null);
            setTimeout(() => { clearProgress(); setView("data"); }, 1200);
          } else {
            paintAverageProgress(null);
            setStatus(
              `Run ${runId} complete. ${stillRunning.length} other job(s) still running…`,
              "ok"
            );
            setLive(state.focusRunId);
          }
          updateRangeCoverage();
        } else if (data.status === "cancelled") {
          if (!stillRunning.length) {
            setLive(null);
            setStatus(`Run ${runId} cancelled.`, "info");
            setTimeout(() => { clearProgress(); }, 1200);
          } else {
            paintAverageProgress(null);
            setStatus(
              `Run ${runId} cancelled. ${stillRunning.length} other job(s) still running…`,
              "info"
            );
            setLive(state.focusRunId);
          }
          updateRangeCoverage();
        } else {
          if (!stillRunning.length) {
            applyProgress({ ...data, status: "failed" });
            setLive(null);
            setStatus(data.error || `Run ${runId} failed.`, "error");
            setTimeout(() => { clearProgress(); }, 2500);
          } else {
            paintAverageProgress(null);
            setStatus(
              (data.error || `Run ${runId} failed.`) +
              ` ${stillRunning.length} other job(s) still running…`,
              "error"
            );
            setLive(state.focusRunId);
          }
          updateRangeCoverage();
        }
        refreshViewAfterRunUpdate();
      } catch (_) {
        state.pollerFailures[runId] = (state.pollerFailures[runId] || 0) + 1;
        if (state.pollerFailures[runId] < 6) {
          return;
        }
        clearInterval(state.pollers[runId]);
        delete state.pollers[runId];
        delete state.pollerFailures[runId];
        delete state.progressByRun[runId];
        state.activeRunIds = state.activeRunIds.filter((id) => id !== runId);
        if (state.focusRunId === runId) {
          const remaining = Object.keys(state.pollers);
          state.focusRunId = remaining[0] || null;
        }
        if (!Object.keys(state.pollers).length) {
          setLive(null);
          clearProgress();
          renderActiveSessions();
        }
        setStatus("Could not fetch run status.", "error");
        refreshViewAfterRunUpdate();
      }
    }, 2000);
  }

  // Legacy compat wrapper
  function pollRun(statusUrl) {
    // Extract run_id from URL like /api/runs/<run_id>
    const runId = statusUrl.split("/").pop();
    startRunPoller(runId, statusUrl);
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
    else if (state.view === "agents") loadAgents();
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

  els.agentsSiteSelect?.addEventListener("change", () => {
    state.agentsPage = 1;
    state.agentsSelected.clear();
    loadAgents();
  });

  function triggerAgentsSearch() {
    state.agentsSearch = els.agentsSearchInput ? els.agentsSearchInput.value.trim() : "";
    state.agentsPage = 1;
    loadAgents();
  }
  els.agentsSearchBtn?.addEventListener("click", triggerAgentsSearch);
  els.agentsSearchInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") triggerAgentsSearch();
  });

  els.agentsSelectAll?.addEventListener("change", () => {
    const checked = els.agentsSelectAll.checked;
    (state.agentsRows || []).forEach((a) => {
      const id = String(a.id);
      if (checked) state.agentsSelected.add(id);
      else state.agentsSelected.delete(id);
    });
    document.querySelectorAll(".agent-row-check").forEach((cb) => { cb.checked = checked; });
  });

  document.addEventListener("change", (e) => {
    const rowCheck = e.target.closest(".agent-row-check");
    if (rowCheck) {
      const id = rowCheck.getAttribute("data-agent-id");
      if (rowCheck.checked) state.agentsSelected.add(id);
      else state.agentsSelected.delete(id);
      updateAgentsSelectAllState();
    }
  });

  els.agentsPrevBtn?.addEventListener("click", () => {
    if (state.agentsPage > 1) {
      state.agentsPage -= 1;
      loadAgents();
    }
  });
  els.agentsNextBtn?.addEventListener("click", () => {
    const totalPages = Math.max(1, Math.ceil(state.agentsTotal / state.agentsLimit));
    if (state.agentsPage < totalPages) {
      state.agentsPage += 1;
      loadAgents();
    }
  });

  els.agentsExportBtn?.addEventListener("click", () => exportAgents(false));
  els.agentsExportAllBtn?.addEventListener("click", () => exportAgents(true));

  document.addEventListener("click", async (e) => {
    const cancelBtn = e.target.closest("[data-cancel-run]");
    if (cancelBtn) {
      e.preventDefault();
      await cancelSession(cancelBtn.getAttribute("data-cancel-run"));
      return;
    }

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

  // ── Covered-ranges helpers ───────────────────────────────────
  async function fetchCoveredRanges(site, location) {
    if (!site || !location) return [];
    try {
      const params = new URLSearchParams({ site, location });
      const res = await fetch(`/api/runs/covered-ranges?${params}`, { cache: "no-store" });
      const data = await res.json();
      return data.ranges || [];
    } catch (_) { return []; }
  }

  function rangesOverlap(s1, e1, s2, e2) {
    return s1 <= e2 && e1 >= s2;
  }

  function buildCoverageHtml(last) {
    if (!last) return "";
    const when = last.completed_at || last.created_at || "";
    const whenShort = when ? fmtDate(when) : "";
    return `<div class="covered-ranges-hint"><span class="covered-label">Last scraped:</span>` +
      `<span class="range-chip-small chip-done">${last.range_start}–${last.range_end} <em>completed</em></span>` +
      (whenShort ? `<span class="covered-when">${escapeHtml(whenShort)}</span>` : "") +
      `</div>`;
  }

  async function updateRangeCoverage() {
    const site     = selectedSite();
    const location = (els.locationInput?.value || "").trim();
    const hint     = document.getElementById("coverageHint");
    if (!hint) return;
    if (!location) { hint.innerHTML = ""; return; }
    try {
      const params = new URLSearchParams({ site, location });
      const res = await fetch(`/api/runs/covered-ranges?${params}`, { cache: "no-store" });
      const data = await res.json();
      state.coveredRanges = data.ranges || [];
      // Only the latest COMPLETED scrape for this site + location
      hint.innerHTML = buildCoverageHtml(data.last || null);
    } catch (_) {
      hint.innerHTML = "";
    }
  }

  function rememberRunMeta(run) {
    if (!run || !run.run_id) return;
    state.runMeta[run.run_id] = {
      site: run.site || state.runMeta[run.run_id]?.site || "",
      location: run.location || state.runMeta[run.run_id]?.location || "",
      range_start: run.range_start ?? state.runMeta[run.run_id]?.range_start,
      range_end: run.range_end ?? state.runMeta[run.run_id]?.range_end,
      status: run.status,
      scraped_agents: run.scraped_agents,
      pages_scraped: run.pages_scraped ?? state.runMeta[run.run_id]?.pages_scraped ?? 0,
      requested_agents: run.requested_agents,
    };
  }

  // ── Active sessions panel ────────────────────────────────────
  function renderActiveSessions() {
    const panel = document.getElementById("activeSessionsPanel");
    if (!panel) return;
    const ids = state.activeRunIds;
    if (!ids.length) { panel.hidden = true; panel.innerHTML = ""; return; }
    panel.hidden = false;
    panel.innerHTML = `
      <div class="active-sessions-head">
        <span class="pulse-dot busy" style="margin-right:6px"></span>
        <strong>${ids.length} session${ids.length > 1 ? "s" : ""} running</strong>
      </div>
      <div class="active-sessions-list">
        ${ids.map((id) => {
          const meta = state.runMeta[id] || {};
          const site = meta.site || "…";
          const loc = meta.location || "";
          const rs = meta.range_start ?? "?";
          const re = meta.range_end ?? "?";
          return `
          <div class="active-session-row" id="session-${escapeAttr(id)}">
            <div class="session-main">
              <div class="session-top">
                <span class="badge site ${escapeAttr(site)}">${escapeHtml(siteLabel(site))}</span>
                <span class="session-range">${escapeHtml(String(rs))}–${escapeHtml(String(re))}</span>
                <span class="session-badge" id="session-badge-${escapeAttr(id)}">${escapeHtml(meta.status || "queued")}</span>
              </div>
              <div class="session-sub">
                <span class="session-loc" title="${escapeAttr(loc)}">${escapeHtml(loc || "—")}</span>
                <span class="session-id">${escapeHtml(id)}</span>
              </div>
            </div>
            <span class="session-pct" id="session-pct-${escapeAttr(id)}">0%</span>
            <span class="session-scraped" id="session-scraped-${escapeAttr(id)}">0 agents</span>
            <button type="button" class="btn-cancel-session" data-cancel-run="${escapeAttr(id)}" title="Cancel this scrape">Cancel</button>
          </div>`;
        }).join("")}
      </div>
    `;
  }

  // Update a single session row when polled (no full panel rebuild)
  function updateSessionRow(run) {
    if (!run || !run.run_id) return;
    rememberRunMeta(run);
    const row = document.getElementById(`session-${run.run_id}`);
    if (!row) {
      if (state.activeRunIds.includes(run.run_id) || ["queued", "running"].includes(run.status)) {
        if (!state.activeRunIds.includes(run.run_id) && ["queued", "running"].includes(run.status)) {
          state.activeRunIds = [...state.activeRunIds, run.run_id];
        }
        renderActiveSessions();
      } else {
        return;
      }
    }
    const pctInfo = calcProgress(run);
    const badge   = document.getElementById(`session-badge-${run.run_id}`);
    const pctEl   = document.getElementById(`session-pct-${run.run_id}`);
    const scraped = document.getElementById(`session-scraped-${run.run_id}`);
    if (badge) {
      const sa = Number(run.scraped_agents) || 0;
      const pg = Number(run.pages_scraped) || 0;
      if (run.status === "running" && sa <= 0 && pg <= 0) {
        badge.textContent = "waiting for peer";
      } else if (run.status === "running" && sa <= 0 && pg > 0) {
        badge.textContent = "starting";
      } else {
        badge.textContent = run.status;
      }
    }
    if (pctEl)   pctEl.textContent   = `${pctInfo.pct}%`;
    if (scraped) scraped.textContent = `${pctInfo.scraped} agents`;

    if (["completed", "failed", "cancelled"].includes(run.status)) {
      setTimeout(() => {
        if (!state.activeRunIds.includes(run.run_id)) {
          renderActiveSessions();
          updateRangeCoverage();
        }
      }, 800);
    }
  }

  async function cancelSession(runId) {
    let ok = true;
    if (typeof Swal !== "undefined") {
      const result = await Swal.fire({
        icon: "warning",
        title: "Cancel this scrape?",
        text: `Stop run ${runId}? Partial data already saved will be kept.`,
        showCancelButton: true,
        confirmButtonText: "Yes, cancel",
        cancelButtonText: "Keep running",
        confirmButtonColor: "#b91c1c",
        cancelButtonColor: "#4855c4",
      });
      ok = result.isConfirmed;
    } else {
      ok = window.confirm(`Cancel scrape ${runId}?`);
    }
    if (!ok) return;

    try {
      const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "Cancel failed");

      if (state.pollers[runId]) {
        clearInterval(state.pollers[runId]);
        delete state.pollers[runId];
      }
      state.activeRunIds = state.activeRunIds.filter((id) => id !== runId);
      delete state.progressByRun[runId];
      if (state.focusRunId === runId) {
        state.focusRunId = state.activeRunIds[0] || null;
        state.lastProgressPct = state.focusRunId ? (state.progressByRun[state.focusRunId] || 0) : 0;
      }
      renderActiveSessions();
      setStatus(`Run ${runId} cancelled.`, "info");
      setLive(state.focusRunId);
      if (!state.activeRunIds.length) {
        setTimeout(() => clearProgress(), 800);
      }
      updateRangeCoverage();
      await toastSuccess("Scrape cancelled");
    } catch (err) {
      if (typeof Swal !== "undefined") {
        Swal.fire({ icon: "error", title: "Cancel failed", text: err.message });
      } else {
        alert(err.message);
      }
    }
  }

  els.rangeStart?.addEventListener("input", updateHint);
  els.rangeEnd?.addEventListener("input", updateHint);
  updateHint();
  els.siteRadios?.forEach((radio) => {
    radio.addEventListener("change", () => { updateLocationHint(); updateRangeCoverage(); });
  });
  updateLocationHint();

  // Debounced coverage refresh when location changes
  let _coverageTimer = null;
  els.locationInput?.addEventListener("input", () => {
    clearTimeout(_coverageTimer);
    _coverageTimer = setTimeout(updateRangeCoverage, 600);
  });

  els.form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    setStatus("Checking range…", "info");

    const payload = Object.fromEntries(new FormData(els.form).entries());
    if (typeof payload.location === "string") payload.location = payload.location.trim();

    const rangeS = Number(payload.range_start || 1);
    const rangeE = Number(payload.range_end   || 100);

    // ── Overlap check ───────────────────────────────────────
    const covered = await fetchCoveredRanges(payload.site, payload.location);
    state.coveredRanges = covered;
    const overlapping = covered.filter((r) => rangesOverlap(rangeS, rangeE, r.range_start, r.range_end));

    if (overlapping.length) {
      const lines = overlapping.map((r) =>
        `  • ${r.range_start}–${r.range_end} (${r.status}, ${r.scraped_agents} agents scraped)`
      ).join("\n");
      let proceed = false;
      if (typeof Swal !== "undefined") {
        const result = await Swal.fire({
          icon: "warning",
          title: "Range already scraped",
          html: `<p>The range <strong>${rangeS}–${rangeE}</strong> overlaps with existing runs:</p><pre class="swal-range-list">${escapeHtml(lines)}</pre><p>Do you want to scrape it again anyway?</p>`,
          showCancelButton: true,
          confirmButtonText: "Yes, scrape again",
          cancelButtonText: "Cancel",
          confirmButtonColor: "#d97706",
          cancelButtonColor: "#4855c4",
        });
        proceed = result.isConfirmed;
      } else {
        proceed = window.confirm(`Range ${rangeS}–${rangeE} overlaps with existing runs:\n${lines}\n\nScrape again?`);
      }
      if (!proceed) { setStatus("", "info"); return; }
    }

    setStatus("Submitting run…", "info");
    try {
      const res = await fetch("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Unable to start run.");

      // Track new run
      const newActiveIds = data.active_run_ids || [data.run_id];
      state.activeRunIds = newActiveIds;
      state.activeRunId  = data.run_id;
      state.focusRunId   = data.run_id;
      state.lastProgressPct = 0;
      state.progressByRun[data.run_id] = 0;
      rememberRunMeta({
        run_id: data.run_id,
        site: payload.site,
        location: payload.location,
        range_start: rangeS,
        range_end: rangeE,
        status: "queued",
        scraped_agents: 0,
        requested_agents: rangeE - rangeS + 1,
      });

      setStatus(`Run ${data.run_id} created (${data.range}). Starting…`, "info");
      setLive(data.run_id);
      applyProgress({
        run_id:           data.run_id,
        status:           "queued",
        scraped_agents:   0,
        requested_agents: rangeE - rangeS + 1,
        site:             payload.site,
        location:         payload.location,
      });
      renderActiveSessions();
      newActiveIds.forEach((id) => {
        startRunPoller(id, `/api/runs/${encodeURIComponent(id)}`);
      });

      // Refresh coverage hint
      updateRangeCoverage();
    } catch (err) {
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
