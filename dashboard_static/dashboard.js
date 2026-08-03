const API = 'http://localhost:8002';
const CCY = '¥';

// ── Plotly dark theme ─────────────────────────────────────────────────────
const PALETTE = ['#4f8ef7', '#7c5cbf', '#3ecf8e', '#f0a844', '#e05c5c', '#54c7ec'];
const SEG_COLORS = { platinum: '#a070f0', gold: '#f0a844', silver: '#909aaa', bronze: '#c08040' };
const LAYOUT = {
  paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
  font: { color: '#e2e6f0', family: 'Segoe UI, system-ui, sans-serif', size: 12 },
  margin: { l: 50, r: 20, t: 40, b: 50 },
  xaxis: { gridcolor: '#242836', zerolinecolor: '#242836' },
  yaxis: { gridcolor: '#242836', zerolinecolor: '#242836' },
  legend: { orientation: 'h', y: -0.2, font: { size: 11 } },
  colorway: PALETTE,
};
const CFG = { displayModeBar: false, responsive: true };

function title(t) { return { text: t, font: { size: 14, color: '#e2e6f0' }, x: 0.02 }; }
function draw(id, traces, layoutOverride = {}) {
  const el = document.getElementById(id);
  if (!el) return;
  Plotly.react(el, traces, { ...LAYOUT, ...layoutOverride, xaxis: { ...LAYOUT.xaxis, ...(layoutOverride.xaxis || {}) }, yaxis: { ...LAYOUT.yaxis, ...(layoutOverride.yaxis || {}) } }, CFG);
}

const get = (path) => fetch(API + path).then(r => r.json()).catch(() => null);
const num = (n) => (n == null ? '—' : Number(n).toLocaleString());
const fix = (n, d = 1) => (n == null ? '—' : Number(n).toFixed(d));

// ── Generic renderers ──────────────────────────────────────────────────────
function kpis(containerId, cards) {
  document.getElementById(containerId).innerHTML = cards.map(c => `
    <div class="kpi" style="border-left-color:${c.color || 'var(--accent)'}">
      <div class="kpi-label">${c.label}</div>
      <div class="kpi-value">${c.value}</div>
      <div class="kpi-sub">${c.sub || ''}</div>
    </div>`).join('');
}

function table(id, cols, rows, fmt = {}) {
  const el = document.getElementById(id);
  if (!rows || !rows.length) { el.innerHTML = `<tr><td class="empty">No data.</td></tr>`; return; }
  const head = '<tr>' + cols.map(c => `<th>${c.label}</th>`).join('') + '</tr>';
  const body = rows.map(r => '<tr>' + cols.map(c => {
    let v = r[c.key];
    if (fmt[c.key]) return `<td>${fmt[c.key](v, r)}</td>`;
    return `<td>${v == null ? '—' : v}</td>`;
  }).join('') + '</tr>').join('');
  el.innerHTML = head + body;
}

const region = () => document.getElementById('f-region').value;

// ══ NETWORK ════════════════════════════════════════════════════════════════
async function loadNetwork() {
  const r = region();
  const [s, byReg, cells, alarms, incidents] = await Promise.all([
    get(`/api/network/summary?region=${r}`),
    get(`/api/network/by-region`),
    get(`/api/network/worst-cells?region=${r}&metric=${document.getElementById('net-cell-metric').value}`),
    get(`/api/network/alarms?region=${r}`),
    get(`/api/network/incidents?region=${r}`),
  ]);
  kpis('net-kpis', [
    { label: 'Avg Throughput', value: fix(s.avg_dl) + ' Mbps', sub: 'downlink' },
    { label: 'Availability', value: fix(s.avg_avail, 2) + '%', sub: 'uptime', color: 'var(--green)' },
    { label: 'Drop Rate', value: fix(s.avg_drop, 3) + '%', sub: 'call drops', color: 'var(--red)' },
    { label: 'Latency', value: fix(s.avg_lat) + ' ms', sub: 'round-trip', color: 'var(--yellow)' },
    { label: 'Active Cells', value: num(s.cells), sub: 'monitored' },
    { label: 'Active Users', value: num(s.total_users), sub: 'on network' },
  ]);
  const reg = byReg.map(x => x.region);
  draw('net-dl', [{ type: 'bar', x: reg, y: byReg.map(x => x.dl), marker: { color: '#4f8ef7' } }], { title: title('Avg download by region (Mbps)') });
  draw('net-drop', [{ type: 'bar', x: reg, y: byReg.map(x => x.drop_rate), marker: { color: '#e05c5c' } }], { title: title('Drop rate by region (%)') });
  draw('net-sinr', [{ type: 'bar', x: reg, y: byReg.map(x => x.sinr), marker: { color: '#7c5cbf' } }], { title: title('Avg SINR by region (dB)') });
  draw('net-avail', [
    { type: 'bar', name: 'Alarms', x: reg, y: byReg.map(x => x.alarms), marker: { color: '#e05c5c' } },
    { type: 'scatter', name: 'Availability %', x: reg, y: byReg.map(x => x.avail), yaxis: 'y2', mode: 'lines+markers', line: { color: '#3ecf8e' } },
  ], { title: title('Network health by region'), yaxis2: { overlaying: 'y', side: 'right', range: [95, 100], gridcolor: 'transparent' } });

  table('net-cells', [
    { key: 'cell_id', label: 'Cell' }, { key: 'region', label: 'Region' }, { key: 'site_name', label: 'Site' },
    { key: 'technology', label: 'Tech' }, { key: 'dl', label: 'DL Mbps' }, { key: 'drop_rate', label: 'Drop %' },
    { key: 'sinr', label: 'SINR' }, { key: 'avail', label: 'Avail %' },
  ], cells);
  table('net-alarms', [
    { key: 'severity', label: 'Severity' }, { key: 'region', label: 'Region' }, { key: 'site_name', label: 'Site' },
    { key: 'alarm_type', label: 'Type' }, { key: 'trigger_time', label: 'Triggered' },
  ], alarms, { severity: v => `<span class="sev-${v}">${v}</span>`, trigger_time: v => (v || '').slice(0, 16).replace('T', ' ') });
  table('net-incidents', [
    { key: 'incident_type', label: 'Type' }, { key: 'region', label: 'Region' }, { key: 'severity', label: 'Severity' },
    { key: 'affected_users', label: 'Affected' }, { key: 'root_cause', label: 'Cause' }, { key: 'resolved', label: 'Resolved' },
  ], incidents, { resolved: v => v ? '✅' : '⏳', affected_users: num });
}

// ══ SUBSCRIBERS ════════════════════════════════════════════════════════════
async function loadSubscribers() {
  const r = region();
  const [s, tech, techReg, dev, mob, sunset] = await Promise.all([
    get(`/api/subs/summary?region=${r}`),
    get(`/api/subs/by-tech?region=${r}`),
    get(`/api/subs/tech-by-region`),
    get(`/api/subs/devices?region=${r}`),
    get(`/api/subs/mobility?region=${r}`),
    get(`/api/subs/sunset?region=${r}`),
  ]);
  kpis('subs-kpis', [
    { label: 'Total Active', value: num(s.total), sub: 'filtered base' },
    { label: 'On 5G', value: num(s.on_5g), sub: 'connected', color: 'var(--green)' },
    { label: 'On 3G', value: num(s.on_3g), sub: 'sunset risk', color: 'var(--red)' },
    { label: 'FWA Candidates', value: num(s.fwa), sub: 'stationary high-DOU', color: 'var(--accent2)' },
  ]);
  const techColors = { '2G': '#6b7490', '3G': '#e05c5c', '4G': '#4f8ef7', '5G': '#3ecf8e' };
  draw('subs-techpie', [{ type: 'pie', hole: 0.55, labels: tech.map(x => x.tech), values: tech.map(x => x.n), marker: { colors: tech.map(x => techColors[x.tech] || '#888') } }], { title: title('Technology distribution') });
  draw('subs-techbar', [{ type: 'bar', x: tech.map(x => x.tech), y: tech.map(x => x.n), marker: { color: tech.map(x => techColors[x.tech] || '#888') } }], { title: title('Subscribers by technology') });

  // stacked tech mix by region
  const regions = [...new Set(techReg.map(x => x.region))];
  const techs = ['2G', '3G', '4G', '5G'];
  draw('subs-techregion', techs.map(t => ({
    type: 'bar', name: t, x: regions,
    y: regions.map(rg => { const row = techReg.find(x => x.region === rg && x.tech === t); return row ? row.n : 0; }),
    marker: { color: techColors[t] },
  })), { title: title('Technology mix by region'), barmode: 'stack' });

  const br = dev.brands || [];
  draw('subs-devices', [
    { type: 'bar', name: '5G', x: br.map(x => x.brand), y: br.map(x => x.five_g), marker: { color: '#3ecf8e' } },
    { type: 'bar', name: 'VoLTE', x: br.map(x => x.brand), y: br.map(x => x.volte), marker: { color: '#4f8ef7' } },
    { type: 'bar', name: 'Legacy 3G', x: br.map(x => x.brand), y: br.map(x => x.legacy), marker: { color: '#6b7490' } },
  ], { title: title('Device capabilities by brand'), barmode: 'group' });

  const m = mob.mobility || [];
  draw('subs-mobility', [{ type: 'bar', x: m.map(x => x.mobility_class), y: m.map(x => x.avg_dou), marker: { color: '#7c5cbf' } }], { title: title('Avg DOU by mobility class (GB)') });
  const d = mob.dou || [];
  draw('subs-dou', [{ type: 'bar', x: d.map(x => x.bucket), y: d.map(x => x.n), marker: { color: '#4f8ef7' } }], { title: title('Subscribers by monthly data usage') });

  draw('subs-sunset', [
    { type: 'bar', name: 'No VoLTE (upgrade)', x: sunset.map(x => x.region), y: sunset.map(x => x.no_volte), marker: { color: '#e05c5c' } },
    { type: 'bar', name: 'VoLTE inactive (easy fix)', x: sunset.map(x => x.region), y: sunset.map(x => x.volte_inactive), marker: { color: '#f0a844' } },
  ], { title: title('3G sunset risk by region'), barmode: 'stack' });
}

async function doLookup() {
  const m = document.getElementById('subs-lookup-input').value.trim();
  const el = document.getElementById('subs-lookup-result');
  if (!m) { el.innerHTML = ''; return; }
  const d = await get(`/api/subs/lookup/${encodeURIComponent(m)}`);
  if (!d || !d.found) { el.innerHTML = `<div class="empty">MSISDN ${m} not found.</div>`; return; }
  const s = d.subscriber, t = d.tech || {}, dev = d.device || {}, p = d.plan || {}, v = d.value || {};
  el.innerHTML = `<div class="lookup-grid">
    <div class="lookup-col"><h4>Network</h4>
      <div>Region: <b>${s.region || '—'}</b></div>
      <div>City: <b>${s.city || '—'}</b></div>
      <div>Technology: <b>${t.current_technology || '—'}</b></div>
      <div>VoLTE: <b>${t.volte_active ? '✅' : '❌'}</b></div>
      <div>Active: <b>${s.is_active ? '✅' : '❌'}</b></div>
    </div>
    <div class="lookup-col"><h4>Device</h4>
      <div><b>${dev.brand || '—'} ${dev.model || ''}</b></div>
      <div>Max tech: <b>${dev.max_technology || '—'}</b></div>
      <div>5G capable: <b>${dev.is_5g_capable ? '✅' : '❌'}</b></div>
      <div>VoLTE capable: <b>${dev.volte_capable ? '✅' : '❌'}</b></div>
    </div>
    <div class="lookup-col"><h4>Commercial</h4>
      <div>Plan: <b>${p.plan_name || '—'}</b></div>
      <div>Price: <b>${p.monthly_price != null ? p.monthly_price + ' ' + CCY + '/mo' : '—'}</b></div>
      <div>Segment: <b class="seg-${(v.value_segment || '')}">${v.value_segment || '—'}</b> ${v.is_hvc ? '⭐' : ''}</div>
      <div>ARPU: <b>${v.arpu != null ? v.arpu + ' ' + CCY : '—'}</b></div>
    </div>
  </div>`;
}

// ══ COMMERCIAL ═════════════════════════════════════════════════════════════
async function loadCommercial() {
  const r = region();
  const [s, seg, plans, billing, hvc] = await Promise.all([
    get(`/api/commercial/summary?region=${r}`),
    get(`/api/commercial/segments?region=${r}`),
    get(`/api/commercial/plans`),
    get(`/api/commercial/billing?region=${r}`),
    get(`/api/commercial/hvc?region=${r}`),
  ]);
  kpis('com-kpis', [
    { label: 'Total Revenue', value: num(s.revenue) + ' ' + CCY, sub: 'this month' },
    { label: 'Avg ARPU', value: fix(s.arpu, 2) + ' ' + CCY, sub: 'per subscriber', color: 'var(--yellow)' },
    { label: 'HVC Customers', value: num(s.hvc), sub: 'gold + platinum', color: 'var(--accent2)' },
    { label: 'Unpaid Bills', value: num(s.unpaid), sub: 'outstanding', color: 'var(--red)' },
  ]);
  const sg = seg.segments || [];
  draw('com-segbar', [{ type: 'bar', x: sg.map(x => x.segment), y: sg.map(x => x.n), marker: { color: sg.map(x => SEG_COLORS[x.segment] || '#4f8ef7') }, text: sg.map(x => x.avg_arpu + ' ' + CCY), textposition: 'outside' }], { title: title('Customers by value segment') });
  draw('com-segpie', [{ type: 'pie', hole: 0.55, labels: sg.map(x => x.segment), values: sg.map(x => x.total_rev), marker: { colors: sg.map(x => SEG_COLORS[x.segment] || '#4f8ef7') } }], { title: title('Revenue share by segment') });

  const tr = seg.trend || [];
  const segs = [...new Set(tr.map(x => x.segment))];
  draw('com-trend', segs.map(sv => ({
    type: 'scatter', mode: 'lines+markers', name: sv,
    x: tr.filter(x => x.segment === sv).map(x => x.month),
    y: tr.filter(x => x.segment === sv).map(x => x.avg_arpu),
    line: { color: SEG_COLORS[sv] || '#4f8ef7' },
  })), { title: title('ARPU trend by segment') });

  const pl = (plans || []).filter(p => p.subscribers > 0);
  draw('com-plans', [{ type: 'bar', x: pl.map(x => x.plan_name), y: pl.map(x => x.subscribers), marker: { color: '#4f8ef7' } }], { title: title('Subscribers by plan'), xaxis: { tickangle: -25 } });
  draw('com-billing', [
    { type: 'bar', name: 'Paid', x: billing.map(x => x.month), y: billing.map(x => x.paid), marker: { color: '#3ecf8e' } },
    { type: 'bar', name: 'Unpaid', x: billing.map(x => x.month), y: billing.map(x => x.unpaid), marker: { color: '#e05c5c' } },
  ], { title: title('Monthly billing — paid vs unpaid'), barmode: 'stack' });

  table('com-hvc', [
    { key: 'msisdn', label: 'MSISDN' }, { key: 'full_name', label: 'Name' }, { key: 'region', label: 'Region' },
    { key: 'value_segment', label: 'Segment' }, { key: 'arpu', label: 'ARPU' }, { key: 'plan_name', label: 'Plan' },
    { key: 'supports_5g', label: '5G plan' },
  ], hvc, {
    value_segment: v => `<span class="seg-${v}">${v}</span>`,
    arpu: v => fix(v, 1) + ' ' + CCY, supports_5g: v => v ? '✅' : '❌',
  });
}

// ══ CAMPAIGNS ══════════════════════════════════════════════════════════════
async function loadCampaigns() {
  const [s, opp, list, fb, offers] = await Promise.all([
    get(`/api/campaigns/summary`),
    get(`/api/campaigns/opportunities`),
    get(`/api/campaigns/list`),
    get(`/api/campaigns/feedback`),
    get(`/api/campaigns/offers`),
  ]);
  kpis('camp-kpis', [
    { label: 'Active', value: num(s.active), sub: 'running', color: 'var(--green)' },
    { label: 'Completed', value: num(s.completed), sub: 'finished' },
    { label: 'Targeted', value: num(s.targets), sub: 'subscribers' },
    { label: 'Converted', value: num(s.converted), sub: s.conv_rate + '% rate', color: 'var(--yellow)' },
    { label: 'SMS Sent', value: num(s.sms_sent), sub: 'outbound' },
    { label: 'Replies', value: num(s.replies), sub: 'inbound', color: 'var(--accent2)' },
  ]);
  draw('camp-opp', [{
    type: 'bar', x: opp.map(o => o.title), y: opp.map(o => o.count),
    marker: { color: PALETTE }, text: opp.map(o => num(o.count)), textposition: 'outside',
  }], { title: title('Addressable subscribers by campaign type') });
  document.getElementById('camp-opp-cards').innerHTML = opp.map((o, i) => `
    <div class="opp-card" style="border-left-color:${PALETTE[i % PALETTE.length]}">
      <div class="opp-title">${o.title}</div>
      <div class="opp-count" style="color:${PALETTE[i % PALETTE.length]}">${num(o.count)}</div>
      <div class="opp-desc">${o.desc}</div>
    </div>`).join('');

  // campaigns list
  const cl = document.getElementById('camp-list');
  if (!list || !list.length) { cl.innerHTML = '<div class="empty">No campaigns yet. They appear when a proposal is approved in the Ops Portal.</div>'; }
  else cl.innerHTML = list.map(c => {
    const pct = c.targeted > 0 ? Math.round(c.converted * 100 / c.targeted) : 0;
    return `<div class="camp-card">
      <div class="camp-top">
        <div><div class="camp-name">${c.campaign_name}</div>
          <div class="camp-meta">${c.campaign_type} · ${c.offer_name} · ${(c.launched_date || '').slice(0, 10)}</div></div>
        <span class="pill pill-${c.status}">${c.status}</span>
      </div>
      <div class="camp-stats"><span>Targeted <b>${num(c.targeted)}</b></span><span>Converted <b>${num(c.converted)}</b></span><span>Rate <b>${pct}%</b></span></div>
      <div class="progress-bar"><div class="progress-fill" style="width:${Math.min(pct, 100)}%"></div></div>
    </div>`;
  }).join('');

  // feedback funnel
  table('camp-funnel', [
    { key: 'campaign_name', label: 'Campaign' }, { key: 'status', label: 'Status' }, { key: 'sms_sent', label: 'Sent' },
    { key: 'delivered', label: 'Delivered' }, { key: 'failed', label: 'Failed' }, { key: 'opt_in', label: 'Opt-in' },
    { key: 'opt_out', label: 'Opt-out' }, { key: 'queries', label: 'Queries' }, { key: 'conversion_pct', label: 'Conv %' },
  ], fb.funnel, {
    status: v => `<span class="pill pill-${v}">${v}</span>`,
    conversion_pct: v => v == null ? '—' : v + '%',
  });

  // replies feed
  const rep = document.getElementById('camp-replies');
  if (!fb.replies || !fb.replies.length) rep.innerHTML = '<div class="empty">No replies yet. Run the simulator with an active campaign to see inbound SMS.</div>';
  else rep.innerHTML = fb.replies.map(r => `
    <div class="reply">
      <span class="reply-intent intent-${r.intent}">${(r.intent || '').replace('_', '-')}</span>
      <span class="reply-text">${r.inbound_text || ''}</span>
      <span class="reply-meta">${r.msisdn} · ${r.campaign_name}</span>
    </div>`).join('');

  // offers
  document.getElementById('camp-offers').innerHTML = (offers || []).map(o => {
    const perks = [];
    if (o.discount_pct > 0) perks.push(`${Math.round(o.discount_pct)}% OFF`);
    if (o.bonus_data_gb > 0) perks.push(`${Math.round(o.bonus_data_gb)}GB BONUS`);
    if (o.price_override) perks.push(`${Math.round(o.price_override)} ${CCY}/mo`);
    return `<div class="offer-card">
      <div style="display:flex;justify-content:space-between">
        <div class="offer-name">${o.offer_name}</div>
        <div class="${o.is_active ? 'badge-off' : 'badge-on'}" style="font-size:11px">${o.is_active ? '● ACTIVE' : '○ INACTIVE'}</div>
      </div>
      <div style="margin:6px 0">${perks.map(p => `<span class="offer-badge">${p}</span>`).join('')}</div>
      <div class="offer-meta">${o.target_campaign} · ${o.target_technology || 'all'} · ${o.validity_days} days<br>${o.description || ''}</div>
    </div>`;
  }).join('');
}

// ── Query Builder (no-SQL, dropdown-driven) ─────────────────────────────────
let _qbSchema = null, _qbBuilt = false;

async function loadQuery() {
  if (_qbBuilt) return;
  _qbSchema = await get('/api/qb/schema');
  if (!_qbSchema) return;
  const dbSel = document.getElementById('qb-db');
  dbSel.innerHTML = Object.keys(_qbSchema).map(d => `<option value="${d}">${d}</option>`).join('');
  dbSel.onchange = qbPopulateTables;
  document.getElementById('qb-table').onchange = qbPopulateColumns;
  document.getElementById('qb-add-filter').onclick = qbAddFilter;
  document.getElementById('qb-run').onclick = qbRun;
  document.getElementById('qb-agg-func').onchange = qbRefreshOrder;
  document.getElementById('qb-agg-col').onchange = qbRefreshOrder;
  qbPopulateTables();
  _qbBuilt = true;
}

const qbTables = () => (_qbSchema[document.getElementById('qb-db').value] || {});
const qbCols   = () => (qbTables()[document.getElementById('qb-table').value] || []);

function qbPopulateTables() {
  document.getElementById('qb-table').innerHTML = Object.keys(qbTables()).map(n => `<option>${n}</option>`).join('');
  qbPopulateColumns();
}

function qbChip(container, col) {
  const el = document.createElement('span');
  el.className = 'qb-chip'; el.textContent = col; el.dataset.col = col;
  el.onclick = () => el.classList.toggle('on');
  container.appendChild(el);
}

function qbPopulateColumns() {
  const cols = qbCols();
  const colsBox = document.getElementById('qb-cols'); colsBox.innerHTML = '';
  const grpBox  = document.getElementById('qb-group'); grpBox.innerHTML = '';
  cols.forEach(c => { qbChip(colsBox, c); qbChip(grpBox, c); });
  document.getElementById('qb-agg-col').innerHTML =
    '<option value="*">* (all rows)</option>' + cols.map(c => `<option>${c}</option>`).join('');
  document.getElementById('qb-filters').innerHTML = '';
  qbRefreshOrder();
}

function qbAggAlias() {
  const f = document.getElementById('qb-agg-func').value;
  if (!f) return null;
  const c = document.getElementById('qb-agg-col').value;
  return f.toLowerCase() + '_' + (c === '*' ? 'all' : c);
}

function qbRefreshOrder() {
  const cols = qbCols(), alias = qbAggAlias(), sel = document.getElementById('qb-order-col'), cur = sel.value;
  const opts = ['<option value="">(none)</option>'].concat(cols.map(c => `<option>${c}</option>`));
  if (alias) opts.push(`<option value="${alias}">${alias} (aggregate)</option>`);
  sel.innerHTML = opts.join(''); sel.value = cur;
}

const qbChosen = id => [...document.getElementById(id).querySelectorAll('.qb-chip.on')].map(e => e.dataset.col);

async function qbAddFilter() {
  const row = document.createElement('div'); row.className = 'qb-filter-row';
  const colSel = document.createElement('select');
  colSel.innerHTML = qbCols().map(c => `<option>${c}</option>`).join('');
  const opSel = document.createElement('select');
  opSel.innerHTML = ['=', '!=', '>', '<', '>=', '<=', 'LIKE'].map(o => `<option>${o}</option>`).join('');
  const valWrap = document.createElement('span'); valWrap.className = 'qb-val';
  const x = document.createElement('span'); x.className = 'qb-x'; x.textContent = '✕'; x.onclick = () => row.remove();
  row.append(colSel, opSel, valWrap, x);
  document.getElementById('qb-filters').appendChild(row);
  const fillVal = async () => {
    const d = await get(`/api/qb/distinct?database=${document.getElementById('qb-db').value}&table=${document.getElementById('qb-table').value}&column=${encodeURIComponent(colSel.value)}`);
    valWrap.innerHTML = '';
    if (d && d.values && d.values.length) {
      const s = document.createElement('select');
      s.innerHTML = d.values.map(v => `<option>${v}</option>`).join('');
      valWrap.appendChild(s);
    } else {
      const i = document.createElement('input'); i.placeholder = 'value'; valWrap.appendChild(i);
    }
  };
  colSel.onchange = fillVal;
  await fillVal();
}

async function qbRun() {
  const filters = [...document.querySelectorAll('#qb-filters .qb-filter-row')].map(r => {
    const sels = r.querySelectorAll('select');
    const valEl = r.querySelector('.qb-val select, .qb-val input');
    return { column: sels[0].value, op: sels[1].value, value: valEl ? valEl.value : '' };
  });
  const func = document.getElementById('qb-agg-func').value;
  const body = {
    database: document.getElementById('qb-db').value,
    table:    document.getElementById('qb-table').value,
    columns:  qbChosen('qb-cols'),
    group_by: qbChosen('qb-group'),
    aggregate: func ? { func, column: document.getElementById('qb-agg-col').value } : null,
    filters,
    order_by: { column: document.getElementById('qb-order-col').value, dir: document.getElementById('qb-order-dir').value },
    limit: parseInt(document.getElementById('qb-limit').value) || 100,
  };
  const res = await fetch(API + '/api/qb/run', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then(r => r.json()).catch(() => ({ error: 'request failed' }));

  document.getElementById('qb-sql').textContent = res.sql || '—';
  const tbl = document.getElementById('qb-results'), cnt = document.getElementById('qb-count');
  if (res.error) { tbl.innerHTML = `<tr><td class="empty">${res.error}</td></tr>`; cnt.textContent = ''; return; }
  const rows = res.rows || [];
  cnt.textContent = `(${rows.length} rows)`;
  if (!rows.length) { tbl.innerHTML = '<tr><td class="empty">No rows.</td></tr>'; return; }
  const cols = Object.keys(rows[0]);
  tbl.innerHTML = '<tr>' + cols.map(c => `<th>${c}</th>`).join('') + '</tr>' +
    rows.map(r => '<tr>' + cols.map(c => `<td>${r[c] == null ? '—' : r[c]}</td>`).join('') + '</tr>').join('');
}

// ── Section nav ─────────────────────────────────────────────────────────────
const LOADERS = { network: loadNetwork, subscribers: loadSubscribers, commercial: loadCommercial, campaigns: loadCampaigns, query: loadQuery };
let current = 'network';

function showSection(sec) {
  current = sec;
  document.querySelectorAll('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.sec === sec));
  document.querySelectorAll('.section').forEach(s => s.classList.toggle('active', s.id === 'sec-' + sec));
  document.getElementById('sec-title').textContent = sec.charAt(0).toUpperCase() + sec.slice(1);
  LOADERS[sec]();
}

document.querySelectorAll('.nav-item').forEach(n => n.addEventListener('click', () => showSection(n.dataset.sec)));
document.getElementById('f-region').addEventListener('change', () => LOADERS[current]());
document.getElementById('net-cell-metric').addEventListener('change', loadNetwork);
let lkT;
document.getElementById('subs-lookup-input').addEventListener('input', () => { clearTimeout(lkT); lkT = setTimeout(doLookup, 400); });

// ── Init ────────────────────────────────────────────────────────────────────
(async function init() {
  const f = await get('/api/filters');
  if (f && f.regions) {
    document.getElementById('f-region').innerHTML =
      '<option value="">All</option>' + f.regions.map(r => `<option>${r}</option>`).join('');
  }
  showSection('network');
})();
