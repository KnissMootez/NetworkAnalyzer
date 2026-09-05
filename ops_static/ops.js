const API = 'http://localhost:8001';

// ── Tab navigation ────────────────────────────────────────────────────────────
document.querySelectorAll('.nav-item').forEach(link => {
  link.addEventListener('click', e => {
    e.preventDefault();
    const tab = link.dataset.tab;
    document.querySelectorAll('.nav-item').forEach(l => l.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    link.classList.add('active');
    document.getElementById(`tab-${tab}`).classList.add('active');
    loadTab(tab);
  });
});

function loadTab(tab) {
  if (tab === 'campaigns')    loadCampaigns();
  if (tab === 'sms')          loadSMS();
  if (tab === 'subscribers')  loadSubscribers();
  if (tab === 'network')      loadNetwork();
  if (tab === 'reports')      loadReports();
  if (tab === 'audit')        loadAudit();
}

// ── Action store (avoids inline JSON in onclick) ──────────────────────────────
const _actionStore = {};

document.addEventListener('click', e => {
  const btn = e.target.closest('[data-action-id]');
  if (btn) {
    const id = btn.dataset.actionId;
    if (_actionStore[id]) openModal(_actionStore[id]);
  }
});

// ── Badge counts ──────────────────────────────────────────────────────────────
async function refreshBadges() {
  try {
    const stats = await fetch(`${API}/api/actions/stats`).then(r => r.json());
    const pending = {};
    stats.forEach(r => {
      if (r.status === 'pending') pending[r.type] = (pending[r.type] || 0) + r.count;
    });
    ['campaigns', 'sms', 'network_flag'].forEach(type => {
      const key  = type === 'campaigns' ? 'campaign' : type;
      const el   = document.getElementById(`badge-${type === 'campaigns' ? 'campaigns' : type}`);
      if (!el) return;
      const count = pending[key] || 0;
      el.textContent = count;
      el.classList.toggle('visible', count > 0);
    });
  } catch {}
}

// ── Modal ─────────────────────────────────────────────────────────────────────
let _currentActionId = null;

function openModal(action) {
  _currentActionId = action.id;
  document.getElementById('modal-title').textContent   = action.title;
  document.getElementById('modal-summary').textContent = action.summary || '—';
  document.getElementById('modal-note').value          = '';

  let payload = {};
  try { payload = JSON.parse(action.payload || '{}'); } catch {}

  // Download button for saved CSV files
  const dlBtn = document.getElementById('modal-download');
  if (payload.filename) {
    dlBtn.style.display = 'inline-block';
    dlBtn.onclick = () => window.open(`http://localhost:8000/exports/${payload.filename}`, '_blank');
  } else {
    dlBtn.style.display = 'none';
  }

  // This used to flatten every payload into one monospace block, which suited the
  // CSV exports it was written for and made a campaign proposal — the thing a
  // reviewer actually has to make a decision about — look like a debug dump.
  // The decision facts lead; the SQL is evidence, so it collapses.
  const esc = s => String(s).replace(/[&<>"]/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const html = [];

  if (payload.msisdns) {
    html.push(`<div class="op-headline"><b>${payload.msisdns.length.toLocaleString()}</b>
               <span>${action.type === 'sms' ? 'recipients' : 'subscribers targeted'}</span></div>`);
  }

  // Commercial context: what this costs and who it reaches. Without it the card
  // showed "Offer #2", which tells a reviewer nothing about what is being given away.
  const a = payload.audience || {};
  if (a.avg_arpu != null || a.hvc_count != null) {
    const sub = [];
    if (a.hvc_count != null) sub.push(`${Number(a.hvc_count).toLocaleString()} high-value`);
    if (a.avg_arpu != null)  sub.push(`avg ARPU ${a.avg_arpu} Yuan`);
    if (a.monthly_revenue != null)
      sub.push(`${Number(a.monthly_revenue).toLocaleString()} Yuan/month at stake`);
    html.push(`<div class="op-sub">${esc(sub.join('  ·  '))}</div>`);
  }

  const facts = [];
  if (payload.campaign_type) facts.push(['Campaign type', payload.campaign_type]);
  const o = payload.offer || {};
  if (o.offer_name) {
    const terms = [];
    if (o.discount_pct)   terms.push(`${o.discount_pct}% discount`);
    if (o.bonus_data_gb)  terms.push(`${o.bonus_data_gb} GB bonus`);
    if (o.validity_days)  terms.push(`${o.validity_days} days`);
    facts.push(['Offer', o.offer_name + (terms.length ? ` — ${terms.join(', ')}` : '')]);
  } else if (payload.offer_id) {
    facts.push(['Offer', `#${payload.offer_id}`]);
  }
  if (payload.region)        facts.push(['Region', payload.region]);
  if (payload.cell_ids)      facts.push(['Cells', payload.cell_ids.join(', ')]);
  if (payload.row_count != null) facts.push(['Rows', payload.row_count]);
  if (payload.filename)      facts.push(['File', payload.filename]);
  if (payload.columns)       facts.push(['Columns', payload.columns.join(', ')]);
  if (facts.length) {
    html.push('<dl class="op-facts">' +
      facts.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('') + '</dl>');
  }

  if (payload.message && action.type !== 'sms') {
    html.push(`<div class="op-block"><span class="op-block-h">Message</span>
               <p>${esc(payload.message)}</p></div>`);
  }

  if (payload.recommendations && payload.recommendations.length) {
    html.push('<div class="op-block"><span class="op-block-h">Recommendations</span><ol>' +
      payload.recommendations.map(r =>
        `<li>${esc(typeof r === 'string' ? r : (r.text || JSON.stringify(r)))}</li>`).join('') +
      '</ol></div>');
  }

  if (payload.sql) {
    html.push(`<details class="op-sql"><summary>Audience query</summary>
               <pre>${esc(payload.sql)}</pre></details>`);
  }

  document.getElementById('modal-payload').innerHTML =
    html.join('') || '<span class="op-empty">No further detail supplied.</span>';

  // Show editable SMS field if type is sms
  const smsWrap = document.getElementById('modal-sms-wrap');
  const smsMsg  = document.getElementById('modal-sms-message');
  if (action.type === 'sms') {
    smsWrap.style.display = 'block';
    smsMsg.value = payload.message || '';
  } else {
    smsWrap.style.display = 'none';
    smsMsg.value = '';
  }
  document.getElementById('modal-overlay').classList.add('open');
}

function closeModal() {
  document.getElementById('modal-overlay').classList.remove('open');
  _currentActionId = null;
}

async function resolveAction(status) {
  if (!_currentActionId) return;
  const note       = document.getElementById('modal-note').value.trim();
  const smsMessage = document.getElementById('modal-sms-message').value.trim();

  // If SMS action, patch the payload with the edited message before resolving
  const action = _actionStore[_currentActionId];
  if (action && action.type === 'sms' && smsMessage) {
    try {
      const payload = JSON.parse(action.payload || '{}');
      payload.message = smsMessage;
      await fetch(`${API}/api/actions/${_currentActionId}/patch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ payload: JSON.stringify(payload) }),
      });
    } catch {}
  }

  await fetch(`${API}/api/actions/${_currentActionId}/resolve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status, note }),
  });
  closeModal();
  loadTab(document.querySelector('.nav-item.active').dataset.tab);
  refreshBadges();
}

// ── Shared card renderer ──────────────────────────────────────────────────────
function renderActionCards(containerId, actions, emptyMsg) {
  const el = document.getElementById(containerId);
  if (!actions.length) { el.innerHTML = `<div class="empty">${emptyMsg}</div>`; return; }

  el.innerHTML = actions.map(a => {
    _actionStore[a.id] = a;
    const statusPill = `<span class="pill pill-${a.status}">${a.status}</span>`;
    const typeTag    = `<span class="type-tag">${a.type.replace('_', ' ')}</span>`;
    const created    = a.created_at ? a.created_at.slice(0, 16).replace('T', ' ') : '—';
    const resolved   = a.resolved_at ? a.resolved_at.slice(0, 16).replace('T', ' ') : '';

    const safeId = a.id;
    let reviewBtn = '';
    if (a.status === 'pending') {
      reviewBtn = `<button class="btn btn-review" data-action-id="${safeId}">Review</button>`;
    }

    let payload = {};
    try { payload = JSON.parse(a.payload || '{}'); } catch {}
    const csvBadge    = payload.filename  ? `<span class="card-csv-badge">📎 CSV · ${payload.row_count ?? '?'} rows</span>` : '';
    const targetBadge = payload.msisdns   ? `<span class="card-csv-badge">👥 ${payload.msisdns.length} targets</span>` : '';

    return `
    <div class="card">
      <div class="card-top">
        <div>
          <div class="card-title">${a.title.slice(0, 100)}${a.title.length > 100 ? '…' : ''}</div>
          <div class="card-summary">${a.summary || ''}</div>
        </div>
        <div class="card-actions">
          ${typeTag} ${statusPill} ${reviewBtn}
        </div>
      </div>
      <div class="card-meta">
        <span>Created: ${created}</span>
        ${resolved ? `<span>Resolved: ${resolved}</span>` : ''}
        ${a.note ? `<span>Note: ${a.note}</span>` : ''}
        ${csvBadge} ${targetBadge}
      </div>
    </div>`;
  }).join('');
}

// ── Campaigns ─────────────────────────────────────────────────────────────────
async function loadCampaigns() {
  const status = document.getElementById('camp-status-filter').value;
  const params = new URLSearchParams({ type: 'campaign' });
  if (status) params.append('status', status);
  const data = await fetch(`${API}/api/actions?${params}`).then(r => r.json());
  renderActionCards('campaigns-list', data, 'No campaigns found.');
}

// ── SMS Queue ─────────────────────────────────────────────────────────────────
async function loadSMS() {
  const status = document.getElementById('sms-status-filter').value;
  const params = new URLSearchParams({ type: 'sms' });
  if (status) params.append('status', status);
  const data = await fetch(`${API}/api/actions?${params}`).then(r => r.json());
  renderActionCards('sms-list', data, 'No SMS actions found.');
}

// ── Subscribers ───────────────────────────────────────────────────────────────
let _subDebounce = null;
function debounceSubscribers() {
  clearTimeout(_subDebounce);
  _subDebounce = setTimeout(loadSubscribers, 350);
}

async function loadSubscribers() {
  const region  = document.getElementById('sub-region').value.trim();
  const tech    = document.getElementById('sub-tech').value;
  const segment = document.getElementById('sub-segment').value;
  const params  = new URLSearchParams({ limit: 100 });
  if (region)  params.append('region',   region);
  if (tech)    params.append('technology', tech);
  if (segment) params.append('segment',  segment);
  const data = await fetch(`${API}/api/subscribers?${params}`).then(r => r.json());

  const tbody = document.getElementById('subscribers-body');
  if (!data.length) { tbody.innerHTML = '<tr><td colspan="7" class="empty">No subscribers found.</td></tr>'; return; }

  tbody.innerHTML = data.map(s => {
    const seg       = s.value_segment || '—';
    const risk      = s.churn_risk_score != null ? s.churn_risk_score.toFixed(2) : '—';
    const riskClass = s.churn_risk_score > 0.7 ? 'churn-high' : s.churn_risk_score > 0.4 ? 'churn-medium' : 'churn-low';
    const active    = s.is_active ? 'Active' : 'Inactive';
    return `<tr>
      <td>${s.msisdn}</td>
      <td>${s.region || '—'}</td>
      <td>${s.technology || '—'}</td>
      <td class="seg-${seg}">${seg}</td>
      <td>${s.arpu != null ? s.arpu.toFixed(1) + ' ¥' : '—'}</td>
      <td class="${riskClass}">${risk}</td>
      <td>${active}</td>
    </tr>`;
  }).join('');
}

// ── Network ───────────────────────────────────────────────────────────────────
async function loadNetwork() {
  const [flags, kpis] = await Promise.all([
    fetch(`${API}/api/actions?type=network_flag`).then(r => r.json()),
    fetch(`${API}/api/network/kpis?limit=20`).then(r => r.json()),
  ]);

  renderActionCards('network-flags-list', flags, 'No network flags from agent.');

  const tbody = document.getElementById('kpi-body');
  if (!kpis.length) { tbody.innerHTML = '<tr><td colspan="5" class="empty">No KPI data.</td></tr>'; return; }
  tbody.innerHTML = kpis.map(k => {
    const drop      = k.dropped_call_rate;
    const dropClass = drop > 2 ? 'churn-high' : drop > 1 ? 'churn-medium' : 'churn-low';
    return `<tr>
      <td>${k.cell_id}</td>
      <td>${k.region || '—'}</td>
      <td class="${dropClass}">${drop != null ? drop.toFixed(2) + '%' : '—'}</td>
      <td>${k.dl_throughput_mbps != null ? k.dl_throughput_mbps.toFixed(1) + ' Mbps' : '—'}</td>
      <td>${k.latency_ms != null ? k.latency_ms.toFixed(0) + ' ms' : '—'}</td>
    </tr>`;
  }).join('');
}

// ── Reports ───────────────────────────────────────────────────────────────────
async function loadReports() {
  const data = await fetch(`${API}/api/actions?type=report`).then(r => r.json());
  renderActionCards('reports-list', data, 'No reports from agent yet.');
}

// ── Audit Log ─────────────────────────────────────────────────────────────────
async function loadAudit() {
  const data = await fetch(`${API}/api/actions`).then(r => r.json());
  const tbody = document.getElementById('audit-body');
  if (!data.length) { tbody.innerHTML = '<tr><td colspan="7" class="empty">No actions yet.</td></tr>'; return; }
  tbody.innerHTML = data.map(a => `<tr>
    <td>${a.id}</td>
    <td><span class="type-tag">${a.type.replace('_', ' ')}</span></td>
    <td>${a.title}</td>
    <td><span class="pill pill-${a.status}">${a.status}</span></td>
    <td>${(a.created_at || '').slice(0, 16).replace('T', ' ')}</td>
    <td>${(a.resolved_at || '').slice(0, 16).replace('T', ' ')}</td>
    <td>${a.note || '—'}</td>
  </tr>`).join('');
}

// ── Compose (human origination) ─────────────────────────────────────────────
let _composeMode = 'sms';

function openCompose() {
  document.getElementById('compose-msg').textContent = '';
  document.getElementById('compose-overlay').classList.add('open');
  previewAudience();
}
function closeCompose() {
  document.getElementById('compose-overlay').classList.remove('open');
}
function setComposeMode(mode) {
  _composeMode = mode;
  document.querySelectorAll('.cmode').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
  document.getElementById('c-sms-fields').style.display  = mode === 'sms' ? 'block' : 'none';
  document.getElementById('c-camp-fields').style.display = mode === 'campaign' ? 'block' : 'none';
  document.getElementById('compose-send').textContent = mode === 'sms' ? 'Send SMS' : 'Launch Campaign';
}

let _previewT = null;
function previewAudience() {
  clearTimeout(_previewT);
  _previewT = setTimeout(async () => {
    const params = new URLSearchParams();
    const region = document.getElementById('c-region').value.trim();
    const tech   = document.getElementById('c-tech').value;
    const seg    = document.getElementById('c-segment').value;
    if (region) params.append('region', region);
    if (tech)   params.append('technology', tech);
    if (seg)    params.append('segment', seg);
    try {
      const r = await fetch(`${API}/api/audience/preview?${params}`).then(x => x.json());
      const limit = parseInt(document.getElementById('c-limit').value) || 0;
      const reach = limit && limit < r.count ? `${limit} of ${r.count.toLocaleString()}` : r.count.toLocaleString();
      document.getElementById('c-preview').textContent = `${reach} subscribers match`;
    } catch { document.getElementById('c-preview').textContent = '—'; }
  }, 350);
}

async function submitCompose() {
  const btn   = document.getElementById('compose-send');
  const msgEl = document.getElementById('compose-msg');
  const body  = {
    region:     document.getElementById('c-region').value.trim() || null,
    technology: document.getElementById('c-tech').value || null,
    segment:    document.getElementById('c-segment').value || null,
    limit:      parseInt(document.getElementById('c-limit').value) || 500,
  };
  let url;
  if (_composeMode === 'sms') {
    body.message = document.getElementById('c-message').value.trim();
    if (!body.message) { msgEl.textContent = 'Enter a message first.'; return; }
    url = '/api/originate/sms';
  } else {
    body.campaign_name = document.getElementById('c-camp-name').value.trim();
    body.campaign_type = document.getElementById('c-camp-type').value;
    if (!body.campaign_name) { msgEl.textContent = 'Enter a campaign name first.'; return; }
    url = '/api/originate/campaign';
  }
  btn.disabled = true; msgEl.textContent = 'Sending…';
  try {
    const res  = await fetch(API + url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) { msgEl.textContent = data.detail || 'Failed.'; btn.disabled = false; return; }
    const n = _composeMode === 'sms' ? data.sent : data.targeted;
    msgEl.textContent = `✓ ${_composeMode === 'sms' ? 'SMS queued to' : 'Campaign launched for'} ${n} subscribers.`;
    setTimeout(() => { closeCompose(); loadTab(document.querySelector('.nav-item.active').dataset.tab); refreshBadges(); }, 1100);
  } catch {
    msgEl.textContent = 'Network error.'; btn.disabled = false;
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────
refreshBadges();
loadCampaigns();
setInterval(refreshBadges, 15000);
