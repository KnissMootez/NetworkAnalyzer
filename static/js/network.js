/* ═══════════════════════════════════════════════════════════
   network.js — Network tab
   ═══════════════════════════════════════════════════════════ */

const Net = (() => {

  function init() {
    // Load region options
    fetch("/api/network/regions").then(r => r.json()).then(regions => {
      const sel = document.getElementById("net-region");
      regions.forEach(r => {
        const o = document.createElement("option");
        o.value = r; o.textContent = r;
        sel.appendChild(o);
      });
    });
    load();
    loadAlarms();
    loadCells();
  }

  function load() {
    const region = document.getElementById("net-region").value;
    const tech   = document.getElementById("net-tech").value;
    fetch(`/api/network/kpis?region=${encodeURIComponent(region)}&tech=${tech}`)
      .then(r => r.json()).then(rows => {
        // KPI summary cards
        if (rows.length) {
          const agg = rows.reduce((a, r) => {
            a.dl += r.avg_dl || 0; a.avail += r.availability || 0;
            a.drop += r.drop_rate || 0; a.lat += r.latency || 0;
            a.cells += r.cells || 0; a.users += r.active_users || 0;
            a.n++;
            return a;
          }, { dl:0, avail:0, drop:0, lat:0, cells:0, users:0, n:0 });
          const n = agg.n || 1;
          document.getElementById("net-kpi-cards").innerHTML = [
            ["AVG DL MBPS",    (agg.dl/n).toFixed(2)],
            ["AVAILABILITY",   (agg.avail/n).toFixed(1) + "%"],
            ["DROP RATE",      (agg.drop/n).toFixed(3) + "%"],
            ["LATENCY MS",     (agg.lat/n).toFixed(1)],
            ["ACTIVE CELLS",   agg.cells.toLocaleString()],
            ["ACTIVE USERS",   agg.users.toLocaleString()],
          ].map(([l,v]) => `<div class="kpi-card"><div class="kpi-val">${v}</div><div class="kpi-lbl">${l}</div></div>`).join("");
        }
        // Charts
        Charts.bar("net-chart-dl", {
          title: "Avg DL Throughput by Region (Mbps)",
          x: rows.map(r => r.region), y: rows.map(r => r.avg_dl),
          y_label: "Mbps",
        });
        Charts.bar("net-chart-drop", {
          title: "Drop Rate by Region (%)",
          x: rows.map(r => r.region), y: rows.map(r => r.drop_rate),
          y_label: "%",
        });
      });
  }

  function loadAlarms() {
    const region = document.getElementById("net-region").value;
    const sev    = document.getElementById("net-sev").value;
    fetch(`/api/network/alarms?region=${encodeURIComponent(region)}&severity=${sev}&limit=100`)
      .then(r => r.json()).then(rows => {
        // Alarms by type bar
        const byType = {};
        rows.forEach(r => { byType[r.alarm_type] = (byType[r.alarm_type]||0)+1; });
        const types  = Object.keys(byType);
        const counts = types.map(t => byType[t]);
        Charts.bar("net-alarms-chart", {
          title: "Alarms by Type",
          x: types, y: counts, y_label: "Count",
        });
        // Table
        const tbody = document.querySelector("#net-alarms-table tbody");
        tbody.innerHTML = rows.map(r => `<tr>
          <td>${r.alarm_type||""}</td>
          <td class="badge-${r.severity}">${r.severity||""}</td>
          <td>${r.site_name||""}</td>
          <td>${r.region||""}</td>
          <td>${r.description||""}</td>
        </tr>`).join("");
      });
  }

  function loadCells() {
    const sort = document.getElementById("net-cell-sort").value;
    fetch(`/api/network/top-cells?sort=${sort}&limit=20`)
      .then(r => r.json()).then(rows => {
        const tbody = document.querySelector("#net-cells-table tbody");
        tbody.innerHTML = rows.map(r => `<tr>
          <td>${r.cell_id}</td>
          <td>${r.site_name||""}</td>
          <td>${r.region||""}</td>
          <td>${r.technology||""}</td>
          <td>${r.dl_mbps??""}</td>
          <td>${r.availability??""}</td>
          <td>${r.drop_rate??""}</td>
          <td>${r.latency??""}</td>
        </tr>`).join("");
      });
  }

  return { init, load, loadAlarms, loadCells };
})();
