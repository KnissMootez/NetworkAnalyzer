"""Rebuild nation themes — full background image, light glass panels, outlined text"""
with open('static/style.css', 'r', encoding='utf-8') as f:
    content = f.read()

# Cut everything from the avatar themes section (and any additions before it)
cut_markers = [
    '/* ===============================================================\n   AVATAR NATION THEMES',
    '\n/* Right panel',
    '\n/* Chat column',
]
cut_at = len(content)
for marker in cut_markers:
    idx = content.find(marker)
    if idx != -1 and idx < cut_at:
        cut_at = idx

# Also remove the small addition at very end
end_additions = content.rfind('\n/* Chat column bg')
if end_additions != -1:
    cut_at = min(cut_at, end_additions)
end_additions2 = content.rfind('\n/* Tab panels transparent')
if end_additions2 != -1:
    cut_at = min(cut_at, end_additions2)

base = content[:cut_at]

nation_css = r"""/* ===============================================================
   AVATAR NATION THEMES — Full background, light glass panels
   =============================================================== */

/* Nation Switcher — 2x2 grid */
.nation-switcher {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
  padding: 10px 0 4px;
  border-top: 1px solid rgba(255,255,255,0.15);
  margin-top: 4px;
}

.nation-btn {
  position: relative;
  height: 56px;
  border: 1.5px solid rgba(255,255,255,0.15);
  border-radius: 8px;
  cursor: pointer;
  background: rgba(0,0,0,0.3);
  overflow: hidden;
  padding: 0;
  transition: border-color .25s, transform .15s, box-shadow .25s;
}

.nation-btn img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  filter: grayscale(50%) brightness(0.5);
  transition: filter .3s, transform .3s;
}

.nation-btn-label {
  position: absolute;
  bottom: 4px;
  left: 0; right: 0;
  text-align: center;
  font-family: 'AvatarFont', 'Bebas Neue', sans-serif;
  font-size: .7rem;
  letter-spacing: .06em;
  color: rgba(255,255,255,0.7);
  z-index: 2;
  text-shadow: 0 1px 4px rgba(0,0,0,0.9);
}

.nation-btn:hover img, .nation-btn.active img {
  filter: grayscale(0%) brightness(0.8);
  transform: scale(1.08);
}
.nation-btn:hover { transform: translateY(-2px); }
.nation-btn:hover .nation-btn-label,
.nation-btn.active .nation-btn-label { color: #fff; }
.nation-btn.active {
  border-color: var(--nation-accent, #fff);
  box-shadow: 0 0 14px var(--nation-glow, rgba(255,255,255,0.4));
}

.nation-switcher-label {
  font-family: 'Share Tech Mono', monospace;
  font-size: .55rem;
  color: var(--muted);
  letter-spacing: .12em;
  margin-bottom: 2px;
}

/* ── Full-screen background — the ONLY background ── */
#nation-bg {
  position: fixed;
  inset: 0;
  z-index: -1;
  background-size: cover;
  background-position: center;
  opacity: 0;
  transition: opacity .7s;
  pointer-events: none;
}

/* ── When a nation is active: kill all dark backgrounds ── */
body[class*="theme-"] {
  background: transparent;
}

body[class*="theme-"] #sidebar,
body[class*="theme-"] #main,
body[class*="theme-"] #kpi-bar,
body[class*="theme-"] #tab-bar,
body[class*="theme-"] .tab-panel,
body[class*="theme-"] .two-col,
body[class*="theme-"] .chat-col,
body[class*="theme-"] .snapshot-col,
body[class*="theme-"] #chat-messages,
body[class*="theme-"] #chat-input-row {
  background: transparent;
}

/* ── Glass panels ── */
body[class*="theme-"] #sidebar {
  background: rgba(0,0,0,0.45) !important;
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  border-right: 1px solid rgba(255,255,255,0.12);
}

body[class*="theme-"] #kpi-bar {
  background: rgba(0,0,0,0.4) !important;
  backdrop-filter: blur(12px);
  border-bottom: 1px solid rgba(255,255,255,0.1);
}

body[class*="theme-"] #tab-bar {
  background: rgba(0,0,0,0.35) !important;
  backdrop-filter: blur(10px);
  border-bottom: 1px solid rgba(255,255,255,0.1);
}

body[class*="theme-"] .kpi-card {
  background: rgba(0,0,0,0.35) !important;
  backdrop-filter: blur(8px);
  border: 1px solid rgba(255,255,255,0.12);
}

body[class*="theme-"] .msg-agent {
  background: rgba(0,0,0,0.5) !important;
  backdrop-filter: blur(8px);
  border: 1px solid rgba(255,255,255,0.12);
}

body[class*="theme-"] .msg-user {
  background: rgba(255,255,255,0.15) !important;
  backdrop-filter: blur(6px);
}

body[class*="theme-"] #chat-input-row {
  background: rgba(0,0,0,0.4) !important;
  backdrop-filter: blur(12px);
  border-top: 1px solid rgba(255,255,255,0.1);
}

body[class*="theme-"] #chat-input {
  background: rgba(255,255,255,0.1) !important;
  border: 1px solid rgba(255,255,255,0.2);
}

body[class*="theme-"] .snap-tabs {
  background: rgba(0,0,0,0.4) !important;
  backdrop-filter: blur(10px);
  border-bottom: 1px solid rgba(255,255,255,0.1);
}

body[class*="theme-"] #coverage-map,
body[class*="theme-"] .chart-box {
  background: rgba(0,0,0,0.3) !important;
  backdrop-filter: blur(4px);
  border: 1px solid rgba(255,255,255,0.1);
}

body[class*="theme-"] .btn-quick {
  background: rgba(255,255,255,0.08) !important;
  border-color: rgba(255,255,255,0.15);
}

/* ── Text outline for readability on any bg ── */
body[class*="theme-"] .sb-logo,
body[class*="theme-"] .kpi-val,
body[class*="theme-"] .kpi-lbl,
body[class*="theme-"] .tab-btn,
body[class*="theme-"] .snap-tab,
body[class*="theme-"] .sb-label,
body[class*="theme-"] .sb-tiny,
body[class*="theme-"] .welcome-title,
body[class*="theme-"] .welcome-sub {
  text-shadow: 0 1px 3px rgba(0,0,0,0.9), 0 0 8px rgba(0,0,0,0.6);
}

body[class*="theme-"] .msg-text,
body[class*="theme-"] .msg-stream,
body[class*="theme-"] .msg-body {
  text-shadow: 0 1px 2px rgba(0,0,0,0.7);
}

/* ── Avatar font on major labels ── */
body[class*="theme-"] .sb-logo,
body[class*="theme-"] .kpi-val,
body[class*="theme-"] .tab-btn,
body[class*="theme-"] .snap-tab,
body[class*="theme-"] .sb-label,
body[class*="theme-"] .section-label,
body[class*="theme-"] .welcome-title {
  font-family: 'AvatarFont', 'Bebas Neue', sans-serif;
  letter-spacing: .05em;
}

/* ── Smooth transitions ── */
body, #sidebar, #main, #kpi-bar, #tab-bar,
.kpi-card, .msg-agent, .tab-btn, .btn-quick,
.btn-primary, .btn-outline, .kpi-val, .sb-logo,
.chart-box, #chat-input-row, .snap-tabs {
  transition: background .5s, border-color .4s, color .3s, box-shadow .3s;
}

/* ================================================================
   FIRE NATION
   ================================================================ */
body.theme-fire {
  --text:   #fff4ee;
  --muted:  #ffcfb0;
  --p300:   #fb923c;
  --p400:   #f97316;
  --p500:   #ea580c;
  --p600:   #c2410c;
  --nation-accent: #f97316;
  --nation-glow: rgba(249,115,22,0.8);
}
body.theme-fire #nation-bg {
  background-image: url("/static/fire_nation_bg.jpg");
  opacity: .9;
}
body.theme-fire .kpi-val  { color: #ffb380; }
body.theme-fire .kpi-lbl  { color: #ffcfb0; }
body.theme-fire .sb-logo  { color: #ffb380; }
body.theme-fire .sb-label { color: #fb923c; }
body.theme-fire .tab-btn  { color: #ffcfb0; }
body.theme-fire .tab-btn.active { color: #f97316; border-bottom-color: #f97316; }
body.theme-fire .snap-tab { color: #ffcfb0; }
body.theme-fire .snap-tab.active { color: #f97316; border-bottom-color: #f97316; }
body.theme-fire .btn-primary { background: linear-gradient(135deg,#c2410c,#9a3412); border:1px solid rgba(249,115,22,.5); color:#fff; box-shadow:0 2px 10px rgba(249,115,22,.4); }
body.theme-fire .btn-primary:hover { background: linear-gradient(135deg,#ea580c,#c2410c); }
body.theme-fire .btn-outline { border-color: rgba(249,115,22,.5); color: #fb923c; }
body.theme-fire .btn-quick:hover { border-color: #f97316; color: #f97316; }
body.theme-fire .btn-danger { background: rgba(239,68,68,.2); border-color: rgba(239,68,68,.4); color:#fca5a5; }
body.theme-fire .status-dot.connected { background:#f97316; box-shadow:0 0 10px #f97316; }
body.theme-fire .msg-label { color: #f97316; }
body.theme-fire #send-btn { background:linear-gradient(135deg,#c2410c,#9a3412); box-shadow:0 2px 10px rgba(249,115,22,.4); }
body.theme-fire #send-btn:hover { background:linear-gradient(135deg,#ea580c,#c2410c); }
body.theme-fire #chat-input { color: #fff4ee; }
body.theme-fire #chat-input::placeholder { color: rgba(255,200,170,.6); }
body.theme-fire .sb-brand { border-bottom-color: rgba(249,115,22,.3); }
body.theme-fire .msg-action-btn:hover { color:#f97316; border-color:#f97316; }

/* ================================================================
   EARTH KINGDOM
   ================================================================ */
body.theme-earth {
  --text:   #f0ffe0;
  --muted:  #c0e080;
  --p300:   #a3e635;
  --p400:   #84cc16;
  --p500:   #65a30d;
  --p600:   #4d7c0f;
  --nation-accent: #84cc16;
  --nation-glow: rgba(132,204,22,0.7);
}
body.theme-earth #nation-bg {
  background-image: url("/static/earth_kingdom_bg.jpg");
  opacity: .85;
}
body.theme-earth .kpi-val  { color: #d0ff80; }
body.theme-earth .kpi-lbl  { color: #c0e080; }
body.theme-earth .sb-logo  { color: #a3e635; }
body.theme-earth .sb-label { color: #84cc16; }
body.theme-earth .tab-btn  { color: #c0e080; }
body.theme-earth .tab-btn.active { color: #a3e635; border-bottom-color: #84cc16; }
body.theme-earth .snap-tab { color: #c0e080; }
body.theme-earth .snap-tab.active { color: #a3e635; border-bottom-color: #84cc16; }
body.theme-earth .btn-primary { background:linear-gradient(135deg,#4d7c0f,#3a5c09); border:1px solid rgba(132,204,22,.5); color:#e0ffe0; box-shadow:0 2px 10px rgba(132,204,22,.3); }
body.theme-earth .btn-primary:hover { background:linear-gradient(135deg,#65a30d,#4d7c0f); }
body.theme-earth .btn-outline { border-color:rgba(132,204,22,.5); color:#a3e635; }
body.theme-earth .btn-quick:hover { border-color:#84cc16; color:#a3e635; }
body.theme-earth .btn-danger { background:rgba(239,68,68,.2); border-color:rgba(239,68,68,.4); color:#fca5a5; }
body.theme-earth .status-dot.connected { background:#84cc16; box-shadow:0 0 10px #84cc16; }
body.theme-earth .msg-label { color:#84cc16; }
body.theme-earth #send-btn { background:linear-gradient(135deg,#4d7c0f,#3a5c09); box-shadow:0 2px 10px rgba(132,204,22,.3); }
body.theme-earth #send-btn:hover { background:linear-gradient(135deg,#65a30d,#4d7c0f); }
body.theme-earth #chat-input { color:#f0ffe0; }
body.theme-earth #chat-input::placeholder { color:rgba(180,230,130,.6); }
body.theme-earth .sb-brand { border-bottom-color:rgba(132,204,22,.3); }
body.theme-earth .msg-action-btn:hover { color:#84cc16; border-color:#84cc16; }

/* ================================================================
   WATER TRIBE
   ================================================================ */
body.theme-water {
  --text:   #eaf8ff;
  --muted:  #a0d8f0;
  --p300:   #7dd3fc;
  --p400:   #38bdf8;
  --p500:   #0ea5e9;
  --p600:   #0284c7;
  --nation-accent: #38bdf8;
  --nation-glow: rgba(56,189,248,0.7);
}
body.theme-water #nation-bg {
  background-image: url("/static/water_tribe_bg.webp");
  opacity: .88;
  filter: saturate(1.1);
}
body.theme-water .kpi-val  { color: #baeeff; }
body.theme-water .kpi-lbl  { color: #a0d8f0; }
body.theme-water .sb-logo  { color: #7dd3fc; }
body.theme-water .sb-label { color: #38bdf8; }
body.theme-water .tab-btn  { color: #a0d8f0; }
body.theme-water .tab-btn.active { color: #7dd3fc; border-bottom-color: #38bdf8; }
body.theme-water .snap-tab { color: #a0d8f0; }
body.theme-water .snap-tab.active { color: #7dd3fc; border-bottom-color: #38bdf8; }
body.theme-water .btn-primary { background:linear-gradient(135deg,#0284c7,#0369a1); border:1px solid rgba(56,189,248,.5); color:#e0f8ff; box-shadow:0 2px 10px rgba(56,189,248,.3); }
body.theme-water .btn-primary:hover { background:linear-gradient(135deg,#0ea5e9,#0284c7); }
body.theme-water .btn-outline { border-color:rgba(56,189,248,.5); color:#7dd3fc; }
body.theme-water .btn-quick:hover { border-color:#38bdf8; color:#7dd3fc; }
body.theme-water .btn-danger { background:rgba(239,68,68,.2); border-color:rgba(239,68,68,.4); color:#fca5a5; }
body.theme-water .status-dot.connected { background:#38bdf8; box-shadow:0 0 10px #38bdf8; }
body.theme-water .msg-label { color:#38bdf8; }
body.theme-water #send-btn { background:linear-gradient(135deg,#0284c7,#0369a1); box-shadow:0 2px 10px rgba(56,189,248,.3); }
body.theme-water #send-btn:hover { background:linear-gradient(135deg,#0ea5e9,#0284c7); }
body.theme-water #chat-input { color:#eaf8ff; }
body.theme-water #chat-input::placeholder { color:rgba(160,216,240,.6); }
body.theme-water .sb-brand { border-bottom-color:rgba(56,189,248,.3); }
body.theme-water .msg-action-btn:hover { color:#38bdf8; border-color:#38bdf8; }

/* ================================================================
   AIR NOMADS
   ================================================================ */
body.theme-air {
  --text:   #fffbf0;
  --muted:  #f0d890;
  --p300:   #fcd34d;
  --p400:   #fbbf24;
  --p500:   #f59e0b;
  --p600:   #d97706;
  --nation-accent: #fbbf24;
  --nation-glow: rgba(251,191,36,0.7);
}
body.theme-air #nation-bg {
  background-image: url("/static/air_nomads_bg.webp");
  opacity: .88;
}
body.theme-air .kpi-val  { color: #ffe88a; }
body.theme-air .kpi-lbl  { color: #f0d890; }
body.theme-air .sb-logo  { color: #fcd34d; }
body.theme-air .sb-label { color: #f59e0b; }
body.theme-air .tab-btn  { color: #f0d890; }
body.theme-air .tab-btn.active { color: #fcd34d; border-bottom-color: #fbbf24; }
body.theme-air .snap-tab { color: #f0d890; }
body.theme-air .snap-tab.active { color: #fcd34d; border-bottom-color: #fbbf24; }
body.theme-air .btn-primary { background:linear-gradient(135deg,#d97706,#b45309); border:1px solid rgba(251,191,36,.5); color:#fff8e0; box-shadow:0 2px 10px rgba(251,191,36,.3); }
body.theme-air .btn-primary:hover { background:linear-gradient(135deg,#f59e0b,#d97706); }
body.theme-air .btn-outline { border-color:rgba(251,191,36,.5); color:#fcd34d; }
body.theme-air .btn-quick:hover { border-color:#fbbf24; color:#fcd34d; }
body.theme-air .btn-danger { background:rgba(239,68,68,.2); border-color:rgba(239,68,68,.4); color:#fca5a5; }
body.theme-air .status-dot.connected { background:#fbbf24; box-shadow:0 0 10px #fbbf24; }
body.theme-air .msg-label { color:#f59e0b; }
body.theme-air #send-btn { background:linear-gradient(135deg,#d97706,#b45309); box-shadow:0 2px 10px rgba(251,191,36,.3); }
body.theme-air #send-btn:hover { background:linear-gradient(135deg,#f59e0b,#d97706); }
body.theme-air #chat-input { color:#fffbf0; }
body.theme-air #chat-input::placeholder { color:rgba(240,216,144,.6); }
body.theme-air .sb-brand { border-bottom-color:rgba(251,191,36,.3); }
body.theme-air .msg-action-btn:hover { color:#fbbf24; border-color:#fbbf24; }
"""

with open('static/style.css', 'w', encoding='utf-8') as f:
    f.write(base + nation_css)

print(f'Done. Lines: {(base + nation_css).count(chr(10))}')
