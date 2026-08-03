/* ═══════════════════════════════════════════════════════════
   subscribers.js — Subscribers tab
   ═══════════════════════════════════════════════════════════ */

const Subs = (() => {
  let _data = null;

  function init() {
    // Populate region filter
    fetch("/api/network/regions").then(r => r.json()).then(regions => {
      const sel = document.getElementById("sub-region");
      regions.forEach(r => {
        const o = document.createElement("option");
        o.value = r; o.textContent = r;
        sel.appendChild(o);
      });
    });
    load();
  }

  function load() {
    const region  = document.getElementById("sub-region").value;
    const segment = document.getElementById("sub-segment").value;
    fetch(`/api/subscribers/summary?region=${encodeURIComponent(region)}&segment=${segment}`)
      .then(r => r.json()).then(data => {
        _data = data;
        _renderCaps(data.caps);
        _renderTech(data.tech_dist);
        _renderDevices(data.devices);
        _renderSunset(data.sunset);
      });
    fetch(`/api/subscribers/mobility?region=${encodeURIComponent(
        document.getElementById("sub-region").value)}`).then(r=>r.json()).then(_renderMobility);
  }

  function _renderCaps(caps) {
    if (!caps) return;
    const total = caps.total || 1;
    document.getElementById("sub-cap-cards").innerHTML = [
      ["5G CAPABLE",   caps.cap_5g,   `${((caps.cap_5g/total)*100).toFixed(1)}%`],
      ["VOLTE CAPABLE",caps.cap_volte, `${((caps.cap_volte/total)*100).toFixed(1)}%`],
      ["ON 3G",        caps.on_3g,    `${((caps.on_3g/total)*100).toFixed(1)}%`],
      ["TOTAL ACTIVE", caps.total,    "subscribers"],
    ].map(([l,v,s]) => `<div class="kpi-card">
      <div class="kpi-val">${(v||0).toLocaleString()}</div>
      <div class="kpi-lbl">${l}</div>
      <div style="font-size:.6rem;color:var(--muted);font-family:'Inter',sans-serif">${s}</div>
    </div>`).join("");
  }

  function _renderTech(rows) {
    if (!rows) return;
    Charts.pie("sub-tech-pie", {
      title: "Technology Distribution",
      x: rows.map(r => r.tech), y: rows.map(r => r.n),
    });
    Charts.bar("sub-tech-bar", {
      title: "Subscribers by Technology",
      x: rows.map(r => r.tech), y: rows.map(r => r.n), y_label: "Count",
    });
  }

  function _renderDevices(rows) {
    if (!rows) return;
    Charts.bar("sub-devices-chart", {
      title: "Subscribers by Device Brand",
      x: rows.map(r => r.brand), y: rows.map(r => r.n), y_label: "Count",
    });
  }

  function _renderSunset(rows) {
    if (!rows) return;
    Charts.multibar("sub-sunset-chart", {
      title: "3G Sunset Risk by Region",
      x: rows.map(r => r.region),
      y: { "At Risk": rows.map(r => r.at_risk), "Easy Fix (VoLTE)": rows.map(r => r.easy_fix) },
    });
    const tbody = document.querySelector("#sub-sunset-table tbody");
    tbody.innerHTML = rows.map(r => {
      const upgrade = (r.at_risk||0) - (r.easy_fix||0);
      return `<tr>
        <td>${r.region}</td>
        <td>${(r.at_risk||0).toLocaleString()}</td>
        <td>${(r.easy_fix||0).toLocaleString()}</td>
        <td>${upgrade.toLocaleString()}</td>
      </tr>`;
    }).join("");
  }

  function _renderMobility(rows) {
    if (!rows) return;
    Charts.bar("sub-mob-count", {
      title: "Subscribers by Mobility Class",
      x: rows.map(r => r.mobility_class), y: rows.map(r => r.n), y_label: "Count",
    });
    Charts.bar("sub-mob-dou", {
      title: "Avg Data Usage by Mobility (GB)",
      x: rows.map(r => r.mobility_class), y: rows.map(r => r.avg_dou_gb), y_label: "GB",
    });
  }

  function subtab(btn, panel) {
    document.querySelectorAll(".sub-tab").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".sub-panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("sub-panel-" + panel).classList.add("active");
  }

  function lookup() {
    const msisdn = document.getElementById("sub-msisdn").value.trim();
    if (!msisdn) return;
    const result = document.getElementById("sub-lookup-result");
    result.innerHTML = `<div class="empty-state">Loading...</div>`;
    fetch(`/api/subscribers/lookup?msisdn=${encodeURIComponent(msisdn)}`)
      .then(r => r.json()).then(d => {
        if (d.error) { result.innerHTML = `<div class="empty-state">${d.error}</div>`; return; }
        const p = d.profile || {};
        const c = d.commercial || {};
        result.innerHTML = `
          <div class="lookup-card">
            <h4>NETWORK PROFILE</h4>
            ${_kv("Region", p.region)} ${_kv("City", p.city)}
            ${_kv("Segment", p.segment)} ${_kv("SIM Type", p.sim_type)}
            ${_kv("Technology", p.current_technology)} ${_kv("VoLTE", p.volte_active ? "Active" : "Inactive")}
            ${_kv("Last Seen", p.last_seen_date)}
          </div>
          <div class="lookup-card">
            <h4>DEVICE</h4>
            ${_kv("Brand", p.brand)} ${_kv("Model", p.device_model)}
            ${_kv("Max Tech", p.max_technology)}
            ${_kv("5G Capable", p.supports_5g ? "Yes" : "No")}
            ${_kv("VoLTE Capable", p.volte_capable ? "Yes" : "No")}
            ${_kv("OS", `${p.os||""} ${p.os_version||""}`)}
          </div>
          <div class="lookup-card">
            <h4>COMMERCIAL</h4>
            ${_kv("Plan", c.plan_name)} ${_kv("Price", c.monthly_price ? c.monthly_price + " Yuan" : "")}
            ${_kv("Data Cap", c.data_cap_gb ? c.data_cap_gb + " GB" : "")}
            ${_kv("5G Plan", c.supports_5g ? "Yes" : "No")}
            ${_kv("Segment", c.segment)} ${_kv("ARPU", c.arpu_monthly ? c.arpu_monthly + " Yuan" : "")}
          </div>`;
        // DOU trend chart
        if (d.dou && d.dou.length) {
          const id = "dou-chart-" + Date.now();
          result.innerHTML += `<div id="${id}" class="chart-box" style="flex:1 1 100%;min-height:200px"></div>`;
          setTimeout(() => Charts.line(id, {
            title: "Monthly Data Usage (GB)",
            x: d.dou.map(r => r.month).reverse(),
            y: d.dou.map(r => r.gb).reverse(),
          }), 50);
        }
      }).catch(() => {
        result.innerHTML = `<div class="empty-state">Error looking up subscriber</div>`;
      });
  }

  function _kv(k, v) {
    if (!v && v !== 0) return "";
    return `<div class="lookup-row-item"><span class="lookup-key">${k}</span><span class="lookup-val">${v}</span></div>`;
  }

  return { init, load, subtab, lookup };
})();
