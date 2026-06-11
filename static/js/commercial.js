/* ═══════════════════════════════════════════════════════════
   commercial.js — Commercial tab
   ═══════════════════════════════════════════════════════════ */

const Com = (() => {

  function init() { load(); }

  function load() {
    const segment = document.getElementById("com-segment").value;
    fetch(`/api/commercial/summary?segment=${segment}`)
      .then(r => r.json()).then(d => {
        _renderArpu(d.value_segs);
        _renderPlans(d.plans);
        _renderBilling(d.billing);
        _renderHvc(d.hvc);
      });
    fetch("/api/commercial/arpu-trend").then(r => r.json()).then(rows => {
      Charts.line("com-arpu-trend", {
        title: "ARPU Trend (Yuan)",
        x: rows.map(r => r.month), y: rows.map(r => r.avg_arpu), y_label: "Yuan",
      });
    });
  }

  function _renderArpu(rows) {
    if (!rows) return;
    Charts.bar("com-seg-chart", {
      title: "Customer Value Segments — Avg ARPU",
      x: rows.map(r => r.segment),
      y: rows.map(r => r.avg_arpu),
      y_label: "Yuan",
    });
  }

  function _renderPlans(rows) {
    if (!rows) return;
    Charts.bar("com-plans-bar", {
      title: "Subscribers by Plan",
      x: rows.map(r => r.plan_name), y: rows.map(r => r.subscribers), y_label: "Subs",
    });
    // Group by plan_type for pie
    const byType = {};
    rows.forEach(r => { byType[r.plan_type] = (byType[r.plan_type]||0) + r.subscribers; });
    Charts.pie("com-plans-pie", {
      title: "Subscribers by Plan Type",
      x: Object.keys(byType), y: Object.values(byType),
    });
    const tbody = document.querySelector("#com-plans-table tbody");
    tbody.innerHTML = rows.map(r => `<tr>
      <td>${r.plan_name||""}</td>
      <td>${r.plan_type||""}</td>
      <td>${r.monthly_price??""}</td>
      <td>${r.data_cap_gb??""}</td>
      <td>${r.supports_5g ? "✓" : ""}</td>
      <td>${r.volte_support ? "✓" : ""}</td>
      <td>${(r.subscribers||0).toLocaleString()}</td>
    </tr>`).join("");
  }

  function _renderBilling(rows) {
    if (!rows) return;
    Charts.multibar("com-billing-chart", {
      title: "Monthly Billing (Yuan)",
      x: rows.map(r => r.month).reverse(),
      y: {
        "Paid":   rows.map(r => r.paid).reverse(),
        "Unpaid": rows.map(r => r.unpaid).reverse(),
      },
    });
    const tbody = document.querySelector("#com-billing-table tbody");
    tbody.innerHTML = rows.map(r => `<tr>
      <td>${r.month}</td>
      <td>${(r.billed||0).toLocaleString()}</td>
      <td>${(r.paid||0).toLocaleString()}</td>
      <td style="color:#fca5a5">${(r.unpaid||0).toLocaleString()}</td>
      <td>${(r.unpaid_subs||0).toLocaleString()}</td>
    </tr>`).join("");
  }

  function _renderHvc(rows) {
    if (!rows) return;
    const tbody = document.querySelector("#com-hvc-table tbody");
    tbody.innerHTML = rows.slice(0,100).map(r => `<tr>
      <td>${r.msisdn||""}</td>
      <td>${r.segment||""}</td>
      <td>${r.arpu??""}</td>
      <td>${r.plan_name||""}</td>
      <td>${r.supports_5g ? "✓" : "—"}</td>
    </tr>`).join("");
  }

  function subtab(btn, panel) {
    document.querySelectorAll(".com-tab").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".com-panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("com-panel-" + panel).classList.add("active");
  }

  return { init, load, subtab };
})();
