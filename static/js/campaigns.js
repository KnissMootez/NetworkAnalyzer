/* ═══════════════════════════════════════════════════════════
   campaigns.js — Campaigns tab
   ═══════════════════════════════════════════════════════════ */

const Camp = (() => {

  const OPP_META = {
    "5G_upsell":    { title:"5G UPSELL",    color:"#818cf8", icon:"📶", desc:"5G-capable device on 4G plan with 5G coverage" },
    "3G_migration": { title:"3G MIGRATION", color:"#fb923c", icon:"📵", desc:"4G/5G device stuck on 3G — sunset risk" },
    "FWA":          { title:"FWA CONVERT",  color:"#38bdf8", icon:"🏠", desc:"Stationary + 30GB/month mobile data users" },
    "VoLTE_sunset": { title:"VOLTE SUNSET", color:"#facc15", icon:"📞", desc:"VoLTE-capable device on 3G, activation pending" },
    "HVC_upsell":   { title:"HVC UPSELL",   color:"#f472b6", icon:"💎", desc:"Gold/Platinum customers not on 5G plan" },
  };

  function init() {
    fetch("/api/campaigns/summary").then(r => r.json()).then(d => {
      _renderOpps(d.opportunities || {});
      _renderCampaigns(d.campaigns || []);
      _renderOffers(d.offers || []);
    });
  }

  function _renderOpps(opps) {
    const grid = document.getElementById("camp-opps-cards");
    grid.innerHTML = Object.entries(OPP_META).map(([key, meta]) => {
      const count = opps[key] || 0;
      const q = encodeURIComponent(`${meta.title.toLowerCase()} opportunity analysis`);
      return `<div class="opp-card" style="border-top-color:${meta.color}">
        <h3>${meta.icon} ${meta.title}</h3>
        <div class="opp-count" style="color:${meta.color}">${count.toLocaleString()}</div>
        <div class="opp-desc">${meta.desc}</div>
        <button class="opp-btn" onclick="App.quickQuery('${meta.title.toLowerCase()} analysis by region')">
          Analyze in Copilot ▸
        </button>
      </div>`;
    }).join("");

    // Chart
    const keys = Object.keys(OPP_META);
    Charts.bar("camp-opps-chart", {
      title: "Addressable Subscribers by Opportunity",
      x: keys.map(k => OPP_META[k].title),
      y: keys.map(k => opps[k] || 0),
      y_label: "Subscribers",
    });
  }

  function _renderCampaigns(camps) {
    const list = document.getElementById("camp-active-list");
    if (!camps.length) {
      list.innerHTML = `<div class="empty-state">No campaigns found</div>`;
      return;
    }
    list.innerHTML = camps.map(c => {
      const rate = c.targeted ? ((c.converted / c.targeted) * 100).toFixed(1) : 0;
      const fill = Math.min(100, rate);
      const badge = c.status === "active" ? "camp-badge-active"
                  : c.status === "completed" ? "camp-badge-completed"
                  : "camp-badge-draft";
      return `<div class="camp-card">
        <div class="camp-card-header">
          <div class="camp-card-name">${c.campaign_name||""}</div>
          <span class="${badge}">${c.status||""}</span>
        </div>
        <div style="font-size:.68rem;color:#6b87a8;margin-bottom:6px">${c.offer_name||""} · ${c.campaign_type||""} · ${c.launch_date||""}</div>
        <div class="camp-progress"><div class="camp-progress-fill" style="width:${fill}%"></div></div>
        <div class="camp-stats">
          <span>🎯 ${(c.targeted||0).toLocaleString()} targeted</span>
          <span>✓ ${(c.converted||0).toLocaleString()} converted</span>
          <span>${rate}% conversion</span>
        </div>
      </div>`;
    }).join("");
  }

  function _renderOffers(offers) {
    const grid = document.getElementById("camp-offers-grid");
    if (!offers.length) {
      grid.innerHTML = `<div class="empty-state">No offers in catalogue</div>`;
      return;
    }
    grid.innerHTML = offers.map(o => {
      const badges = [];
      if (o.discount_pct)  badges.push(`-${o.discount_pct}%`);
      if (o.bonus_gb)      badges.push(`+${o.bonus_gb}GB`);
      if (o.price_override) badges.push(`${o.price_override} Yuan`);
      return `<div class="offer-card ${o.is_active ? "" : "offer-inactive"}">
        <div class="offer-name">${o.offer_name||""}</div>
        <div class="offer-badges">${badges.map(b => `<span class="offer-badge">${b}</span>`).join("")}</div>
        <div class="offer-desc">
          ${o.campaign_type||""} · ${o.target_technology||""} · ${o.validity_days||""} days<br>
          ${o.description||""}
        </div>
        ${o.is_active ? "" : '<div style="font-size:.6rem;color:#ef4444;margin-top:4px">INACTIVE</div>'}
      </div>`;
    }).join("");
  }

  function subtab(btn, panel) {
    document.querySelectorAll(".camp-tab").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".camp-panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("camp-panel-" + panel).classList.add("active");
  }

  return { init, subtab };
})();
