/* ═══════════════════════════════════════════════════════════
   main.js — App bootstrap, tab routing, WS event wiring
   ═══════════════════════════════════════════════════════════ */

const App = (() => {
  let activeModel = "qwen3";
  let t0 = null;

  function init() {
    _restoreFastPath();
    // Tab switching
    document.querySelectorAll(".tab-btn").forEach(btn => {
      btn.addEventListener("click", () => _switchTab(btn.dataset.tab));
    });

    // All current models are Bedrock — thinking always available via 🧠 button

    // WS status indicator
    WS.on("_connected",    () => _setStatus(true));
    WS.on("_disconnected", () => _setStatus(false));

    // Chat streaming events
    WS.on("token",       ev => Chat.appendToken(ev.token));
    WS.on("think_token", ev => Chat.appendThinkToken(ev.token));
    WS.on("think_done",  ev => Chat.appendThinkToken(""));   // think block complete
    WS.on("step_sql",    ev => Chat.addStep(ev));

    WS.on("done", ev => {
      const elapsed = t0 ? ((Date.now() - t0) / 1000).toFixed(1) : null;
      console.log("[chart spec]", ev.result.chart);
      Chat.finalize({ ...ev.result, elapsed });
      document.getElementById("chat-input").disabled = false;
      document.querySelector(".btn-send").style.display = "";
      document.getElementById("btn-stop").style.display = "none";
      t0 = null;

      const btnContinue = document.getElementById("btn-continue");
      if (ev.result && ev.result.truncated) {
        btnContinue.style.display = "";
      } else {
        btnContinue.style.display = "none";
      }
    });

    // Load KPIs
    _loadMetrics();
    setInterval(_loadMetrics, 30000);

    // Coverage map
    _loadCoverage();

    // Quick snapshot charts
    _loadSnapCharts();

    // Connect WS
    WS.connect(activeModel);
  }

  // ── Tab routing ───────────────────────────────────────────
  const tabLoaded = {};
  function _switchTab(tab) {
    document.querySelectorAll(".tab-panel").forEach(p =>
      p.classList.toggle("active", p.dataset.tab === tab));
    document.querySelectorAll(".tab-btn").forEach(b =>
      b.classList.toggle("active", b.dataset.tab === tab));

    if (!tabLoaded[tab]) {
      tabLoaded[tab] = true;
      if (tab === "network")     Net.init();
      if (tab === "subscribers") Subs.init();
      if (tab === "commercial")  Com.init();
      if (tab === "campaigns")   Camp.init();
    }
  }

  // ── Send message ──────────────────────────────────────────
  function send() {
    const inp = document.getElementById("chat-input");
    const text = inp.value.trim();
    if (!text || Chat.isStreaming()) return;
    inp.value = "";
    inp.style.height = "auto";
    inp.disabled = true;
    document.querySelector(".btn-send").style.display = "none";
    document.getElementById("btn-stop").style.display = "";
    Chat.addUser(text);
    Chat.startStream();
    t0 = Date.now();
    WS.send({ type: "message", text });
  }

  function quickQuery(text) {
    _switchTab("copilot");
    const inp = document.getElementById("chat-input");
    inp.value = text;
    send();
  }

  // Send without showing a user bubble — for diagram/mindmap extensions
  function sendSilent(text) {
    if (Chat.isStreaming()) return;
    _switchTab("copilot");
    document.getElementById("chat-input").disabled = true;
    document.querySelector(".btn-send").style.display = "none";
    document.getElementById("btn-stop").style.display = "";
    Chat.startStream();
    t0 = Date.now();
    WS.send({ type: "message", text });
  }

  function confirm() {
    if (!Chat.hasPending()) return;
    Chat.clearPending();
    Chat.startStream();
    t0 = Date.now();
    WS.send({ type: "confirm" });
  }

  function cancel() {
    Chat.clearPending();
    WS.send({ type: "cancel" });
  }

  // ── Snapshot tab switching ────────────────────────────────
  function snapTab(btn, panel) {
    document.querySelectorAll(".snap-tab").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".snap-panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("snap-" + panel).classList.add("active");
  }

  // ── Collapse / expand the right-hand snapshot column (map etc.) ──
  function toggleSnapshot() {
    const col = document.getElementById("snapshot-col");
    if (!col) return;
    const collapsed = col.classList.toggle("collapsed");
    // when re-opening, the panel changed width — let Plotly re-fit any chart in it
    if (!collapsed) {
      setTimeout(() => {
        col.querySelectorAll(".js-plotly-plot").forEach(p => { try { Plotly.Plots.resize(p); } catch (e) {} });
      }, 260);
    }
  }

  // ── Model apply ───────────────────────────────────────────
  function applyModel() {
    const sel   = document.getElementById("model-select");
    activeModel = sel.value;
    fetch("/api/model", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: activeModel })
    }).then(() => {
      WS.connect(activeModel);
      closeSettings();
    });
  }

  // ── Fast path toggle ──────────────────────────────────────
  // Diagnostic switch. Off routes every question through the full reasoning
  // loop instead of the single-shot path, so the steps panel shows the chain.
  function setFastPath(on) {
    try { localStorage.setItem("na-fastpath", on ? "1" : "0"); } catch (e) {}
    fetch("/api/fastpath", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !!on })
    }).catch(() => {});
  }

  function _restoreFastPath() {
    let on = true;
    try { on = localStorage.getItem("na-fastpath") !== "0"; } catch (e) {}
    const box = document.getElementById("fastpath-toggle");
    if (box) box.checked = on;
    if (!on) setFastPath(false);   // re-apply across a server restart
  }

  function stop() {
    WS.send({ type: "stop" });
    Chat.finalize({ text: Chat.currentText() || "— stopped —", elapsed: t0 ? ((Date.now()-t0)/1000).toFixed(1) : null });
    document.getElementById("chat-input").disabled = false;
    document.getElementById("btn-stop").style.display = "none";
    document.querySelector(".btn-send").style.display = "";
    t0 = null;
  }

  function continueResponse() {
    if (Chat.isStreaming()) return;
    document.getElementById("btn-continue").style.display = "none";
    document.getElementById("chat-input").disabled = true;
    document.querySelector(".btn-send").style.display = "none";
    document.getElementById("btn-stop").style.display = "";
    Chat.addUser("Continue");
    Chat.startStream();
    t0 = Date.now();
    WS.send({ type: "continue" });
  }

  function forgetMemory() {
    fetch("/api/reset", { method: "POST" }).then(r => r.json()).then(() => {
      Chat.clear();
      document.getElementById("chat-input").disabled = false;
      document.querySelector(".btn-send").style.display = "";
      document.getElementById("btn-stop").style.display = "none";
      _toast("Memory cleared");
    });
  }

  function _toast(msg) {
    const t = document.createElement("div");
    t.textContent = msg;
    t.style.cssText = "position:fixed;bottom:24px;left:50%;transform:translateX(-50%);" +
      "background:var(--surface);color:var(--text);border:1px solid var(--border);" +
      "font-family:'Inter',sans-serif;" +
      "font-size:.75rem;padding:6px 18px;border-radius:6px;z-index:9999;pointer-events:none;";
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 2000);
  }

  // ── Metrics ───────────────────────────────────────────────
  function _loadMetrics() {
    fetch("/api/metrics").then(r => r.json()).then(d => {
      document.getElementById("k-subs").textContent   = d.subscribers ? d.subscribers.toLocaleString() : "—";
      document.getElementById("k-alarms").textContent = d.alarms       ? d.alarms.toLocaleString()      : "—";
      document.getElementById("k-hvc").textContent    = d.hvc          ? d.hvc.toLocaleString()         : "—";
      document.getElementById("k-fwa").textContent    = d.fwa          ? d.fwa.toLocaleString()         : "—";
    }).catch(() => {});
  }

  // ── Coverage map (Avatar world map) ──────────────────────────────────────────
  function _loadCoverage() {
    fetch("/api/coverage?t=" + Date.now()).then(r => r.json()).then(rows => {
      if (!rows || !rows.length) return;
      const IMG_W = 4096, IMG_H = 3072;

      // High contrast colors that pop on any map background
      const TECH_COLORS = { "5G":"#ffffff", "4G":"#facc15", "3G":"#f97316", "2G":"#94a3b8" };
      const techColor = TECH_COLORS;
      const techSize  = { "5G": 6, "4G": 6, "3G": 6, "2G": 6 };

      // Pre-seed all tech tiers so legend always shows all three
      const byTech = { "5G":{x:[],y:[],text:[],ids:[]}, "4G":{x:[],y:[],text:[],ids:[]}, "3G":{x:[],y:[],text:[],ids:[]} };
      rows.forEach(r => {
        const t = r.technology || "3G";
        if (!byTech[t]) byTech[t] = { x:[], y:[], text:[], ids:[] };
        byTech[t].x.push(r.longitude);
        byTech[t].y.push(IMG_H - r.latitude);
        byTech[t].text.push(`<b>${r.site_name}</b><br>${r.region} — ${t}<br><i>Click to investigate</i>`);
        byTech[t].ids.push(r.site_name + '|' + r.region);
      });

      const scatterTraces = Object.entries(byTech).map(([t, d]) => ({
        type: "scatter",
        mode: "markers",
        x: d.x, y: d.y,
        text: d.text,
        ids: d.ids,
        name: t,
        marker: {
          size: 7,
          color: techColor[t] || "#94a3b8",
          opacity: 0.95,
          line: { width: 1.5, color: "rgba(0,0,0,0.7)" },
          symbol: "circle",
        },
        hoverinfo: "text",
        hoverlabel: {
          bgcolor: "rgba(0,0,0,0.8)",
          bordercolor: techColor[t] || "#fff",
          font: { color: "#fff", size: 11 },
        },
      }));

      const mapEl = document.getElementById("coverage-map");
      if (mapEl) { Plotly.purge(mapEl); mapEl.style.background = "transparent"; }

      Plotly.newPlot("coverage-map", scatterTraces, {
        xaxis: {
          range: [0, IMG_W], visible: false,
          fixedrange: false, minallowed: 0, maxallowed: IMG_W,
        },
        yaxis: {
          range: [0, IMG_H], visible: false, autorange: "reversed",
          fixedrange: false, minallowed: 0, maxallowed: IMG_H,
        },
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor:  "rgba(0,0,0,0)",
        margin: { t:0, b:0, l:0, r:0 },
        showlegend: true,
        legend: {
          bgcolor: "rgba(0,0,0,0.6)",
          bordercolor: "rgba(255,255,255,0.25)",
          borderwidth: 1,
          font: { color: "#fff", size: 11, family: "Inter, sans-serif" },
          x: 1, xanchor: "right", y: 0.98,
        },
        dragmode: "pan",
        images: [{
          source: "/static/atla_map.jpg",
          xref: "x", yref: "y",
          x: 0, y: 0,
          sizex: IMG_W, sizey: IMG_H,
          sizing: "stretch",
          layer: "below",
          opacity: 1,
        }],
      }, {
        responsive: true,
        scrollZoom: true,
        displayModeBar: true,
        displaylogo: false,
        modeBarButtonsToRemove: ["select2d","lasso2d","hoverCompareCartesian","hoverClosestCartesian"],
        toImageButtonOptions: { format: "png", scale: 2 },
      });

      // Click a cell site → trigger agent investigation
      mapEl.on("plotly_click", function(data) {
        const pt = data.points[0];
        if (!pt) return;

        // A flashing issue ring → ask the agent to explain it and how to fix it
        if (_isRing(pt.data)) {
          const cd = pt.customdata || [];
          const site = cd[0], region = cd[1];
          if (site) {
            _toast(`Diagnosing ${site}...`);
            App.quickQuery(
              `Cell site ${site} in ${region || "unknown region"} has active service issues right now. ` +
              `Explain exactly what is going on: the affected services, root cause, severity, when it started, ` +
              `which subscribers are affected and how many are high-value — then tell me what to do to fix it ` +
              `and protect those customers.`
            );
          }
          return;
        }

        const id = pt.id || "";
        const [site, region] = id.split("|");
        const tech = pt.data.name;
        if (site) {
          _toast(`Investigating ${site}...`);
          App.quickQuery(`Investigate cell site ${site} in ${region || "unknown region"} — check KPIs, drop rate, throughput and any active alarms`);
        }
      });

      // Hover cursor
      mapEl.on("plotly_hover",   () => { mapEl.style.cursor = "pointer"; });
      mapEl.on("plotly_unhover", () => { mapEl.style.cursor = "default"; });

      // A re-plot (e.g. theme switch) wipes the rings — restore Issues-only if it was on
      if (_issuesOnly) {
        _applySiteLayerVisibility();
        fetch("/api/incidents/active-sites?t=" + Date.now())
          .then(r => r.json()).then(s => _renderIssueRings(s)).catch(() => {});
      }

    }).catch(() => {});
  }

  // ── Snapshot charts ───────────────────────────────────────
  function _loadSnapCharts() {
    // Tech distribution pie
    fetch("/api/charts/tech-dist").then(r => r.json()).then(rows => {
      Charts.pie("snap-tech-chart", {
        title: "Technology Distribution",
        x: rows.map(r => r.tech),
        y: rows.map(r => r.n),
      });
    });
    // Alarms by region bar
    fetch("/api/charts/alarms-region").then(r => r.json()).then(rows => {
      Charts.bar("snap-alarms-chart", {
        title: "Active Alarms by Region",
        x: rows.map(r => r.region),
        y: rows.map(r => r.n),
        y_label: "Alarms",
      });
    });
  }

  // ── Alerts scan ───────────────────────────────────────────
  function scanAlerts() {
    const btn = document.getElementById("scan-btn");
    btn.disabled = true;
    btn.textContent = "Scanning...";
    fetch("/api/alerts").then(r => r.json()).then(alerts => {
      const list = document.getElementById("alerts-list");
      list.innerHTML = "";
      if (!alerts || !alerts.length) {
        list.innerHTML = `<div class="empty-state">No active alerts</div>`;
        return;
      }
      alerts.forEach(a => {
        const div = document.createElement("div");
        div.className = `alert-item ${a.level || "info"}`;
        div.innerHTML = `<div class="alert-title">${a.message || ""}</div>`
          + `<div class="alert-action">${a.action || ""}</div>`
          + `<button class="alert-investigate" onclick="App.quickQuery('${(a.message||"").replace(/'/g,"\\'")}')">▸ Investigate</button>`;
        list.appendChild(div);
      });
    }).finally(() => {
      btn.disabled = false;
      btn.textContent = "Scan network";
    });
  }

  function _setStatus(connected) {
    const dot = document.getElementById("ws-status");
    dot.className = "status-dot " + (connected ? "connected" : "disconnected");
  }

  let _theme = null;
  function setTheme(mode) {
    const changed = _theme !== null && _theme !== mode;
    _theme = mode;
    document.body.classList.toggle('light', mode === 'light');
    localStorage.setItem('na-theme', mode);
    document.querySelectorAll('.theme-opt').forEach(b => {
      b.classList.toggle('active', b.dataset.theme === mode);
    });
    const tgl = document.getElementById('theme-toggle');
    if (tgl) tgl.textContent = mode === 'light' ? '☀' : '🌙';
    // Re-render charts so Plotly picks up the new palette
    if (changed) setTimeout(() => {
      if (document.getElementById('coverage-map')?.children.length) _loadCoverage();
      document.querySelectorAll('.chart-box[id]').forEach(el => {
        if (el.id && el.children.length) Plotly.purge(el.id);
      });
      _loadSnapCharts();
    }, 260);
  }

  function toggleTheme() {
    setTheme(_theme === 'light' ? 'dark' : 'light');
  }

  // apply saved theme immediately, before the first chart renders
  setTheme(localStorage.getItem('na-theme') || 'dark');

  function openSettings() {
    document.getElementById('settings-panel').classList.add('open');
    document.getElementById('settings-overlay').classList.add('open');
  }
  function closeSettings() {
    document.getElementById('settings-panel').classList.remove('open');
    document.getElementById('settings-overlay').classList.remove('open');
  }

  // ── Live-issue rings on the coverage map ────────────────────────────────────
  // Two ways in: the agent answering an "issues" question (flashCoverage, scoped to
  // that answer), or the "Issues only" toggle (every live issue). Both render the
  // same rings — one trace per severity, so the legend reads Critical/Major/Minor
  // and each pulses at its own rate: the worse it is, the faster it blinks.
  const SEV_META = {
    3: { name: "⚠ Critical", color: "#ef4444", period: 4  },   // fastest
    2: { name: "⚠ Major",    color: "#f97316", period: 7  },
    1: { name: "⚠ Minor",    color: "#facc15", period: 11 },   // slowest
  };
  let _flashTimer = null, _ringIdx = [], _ringMeta = [], _issuesOnly = false;

  const _isRing = t => !!(t && t.name && t.name.indexOf("⚠") === 0);

  function _clearFlash() {
    if (_flashTimer) { clearInterval(_flashTimer); _flashTimer = null; }
    _ringIdx = []; _ringMeta = [];
    const mapEl = document.getElementById("coverage-map");
    if (!mapEl || !mapEl.data) return;
    const idx = [];
    mapEl.data.forEach((t, i) => { if (_isRing(t)) idx.push(i); });
    if (idx.length) { try { Plotly.deleteTraces(mapEl, idx); } catch (e) {} }
  }

  function _issueHover(s) {
    const sev   = s.sev_rank === 3 ? "critical" : s.sev_rank === 2 ? "major" : "minor";
    const since = (s.since || "").slice(11, 16);
    return `<b>⚠ ${s.site_name}</b> — ${s.region}<br>` +
           `<b>${(s.services || "service issue").replace(/,/g, ", ")}</b><br>` +
           `Cause: ${(s.causes || "unknown").replace(/,/g, ", ")}<br>` +
           `${sev} · ${s.incidents} active` + (s.hvc ? ` · ${s.hvc} HVC` : "") +
           (since ? `<br>Since ${since}` : "") +
           `<br><i>Click to diagnose</i>`;
  }

  function _updateIssueCount(n) {
    const el = document.getElementById("issues-count");
    if (el) el.textContent = n ? String(n) : "";
  }

  // Healthy sites go 'legendonly' in Issues-only mode — still one click away in the legend.
  function _applySiteLayerVisibility() {
    const mapEl = document.getElementById("coverage-map");
    if (!mapEl || !mapEl.data) return;
    const idx = [];
    mapEl.data.forEach((t, i) => { if (!_isRing(t)) idx.push(i); });
    if (idx.length) {
      try { Plotly.restyle(mapEl, { visible: _issuesOnly ? "legendonly" : true }, idx); } catch (e) {}
    }
  }

  function _renderIssueRings(sites) {
    const mapEl = document.getElementById("coverage-map");
    if (!mapEl || !mapEl.data) return 0;    // map not drawn yet
    _clearFlash();
    _updateIssueCount(sites ? sites.length : 0);
    if (!sites || !sites.length) return 0;  // empty list just clears stale rings

    const IMG_H = 3072;
    const groups = { 3: [], 2: [], 1: [] };
    sites.forEach(s => {
      if (s.latitude == null || s.longitude == null) return;
      (groups[s.sev_rank] || groups[1]).push(s);
    });

    const traces = [], metas = [];
    [3, 2, 1].forEach(rank => {
      const g = groups[rank];
      if (!g.length) return;
      const meta = SEV_META[rank];
      traces.push({
        type: "scatter", mode: "markers", name: meta.name,
        x: g.map(s => s.longitude),
        y: g.map(s => IMG_H - s.latitude),
        text: g.map(_issueHover),
        customdata: g.map(s => [s.site_name, s.region]),
        hoverinfo: "text",
        marker: { size: 16, color: meta.color, symbol: "circle-open",
                  line: { width: 2.5, color: meta.color } },
        hoverlabel: { bgcolor: "rgba(0,0,0,0.88)", bordercolor: meta.color,
                      font: { color: "#fff", size: 11 } },
      });
      metas.push(meta);
    });
    if (!traces.length) return 0;

    const base = mapEl.data.length;
    Plotly.addTraces(mapEl, traces);
    _ringIdx  = traces.map((_, i) => base + i);
    _ringMeta = metas;

    // one timer drives every severity — size follows a sine, period set by severity
    let tick = 0;
    _flashTimer = setInterval(() => {
      tick++;
      const sizes = _ringMeta.map(m => 16 + 6 * (1 + Math.sin((tick / m.period) * Math.PI)));
      try { Plotly.restyle(mapEl, { "marker.size": sizes }, _ringIdx); }
      catch (e) { clearInterval(_flashTimer); _flashTimer = null; }
    }, 90);

    return sites.length;
  }

  function _revealMap() {
    const col = document.getElementById("snapshot-col");
    if (col && col.classList.contains("collapsed")) toggleSnapshot();
    const covTab = Array.from(document.querySelectorAll(".snap-tab"))
      .find(b => (b.getAttribute("onclick") || "").includes("'map'"));
    if (covTab) snapTab(covTab, "map");
    const mapEl = document.getElementById("coverage-map");
    if (mapEl) { try { Plotly.Plots.resize(mapEl); } catch (e) {} }
  }

  // Agent-driven: flash exactly the sites the answer was about.
  function flashCoverage(sites) {
    const n = _renderIssueRings(sites);
    if (!n) return;
    _revealMap();
    _toast(`⚠ ${n} site${n > 1 ? "s" : ""} with live issues on the map`);
  }

  // User-driven: strip the map down to only the sites that are hurting.
  function toggleIssuesOnly() {
    _issuesOnly = !_issuesOnly;
    const btn = document.getElementById("issues-toggle");
    if (btn) btn.classList.toggle("on", _issuesOnly);
    _applySiteLayerVisibility();
    if (!_issuesOnly) return;               // leaving the mode keeps whatever rings are up
    _revealMap();
    fetch("/api/incidents/active-sites?t=" + Date.now())
      .then(r => r.json())
      .then(sites => {
        const n = _renderIssueRings(sites);
        _toast(n ? `${n} site${n > 1 ? "s" : ""} with live issues` : "No live issues right now");
      })
      .catch(() => {});
  }

  return { init, send, stop, setFastPath, continueResponse, quickQuery, sendSilent, confirm, cancel, snapTab, toggleSnapshot, applyModel, forgetMemory, scanAlerts, setTheme, toggleTheme, openSettings, closeSettings, flashCoverage, toggleIssuesOnly };
})();

document.addEventListener("DOMContentLoaded", App.init);
