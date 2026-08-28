---
name: NetworkAnalyzer Project Overview
description: AI-powered telecom operator intelligence platform — full technical summary of everything built
type: project
originSessionId: 3b53e3e6-83c7-4d5f-ba3b-892da0bd4243
modified: 2026-07-24T13:58:44.185Z
---

**NetworkAnalyzer** (formerly SmartCareLLM) is an LLM-powered copilot for a mobile network operator, built as a new use case on top of Huawei's SmartCare platform. Developed during an internship at Huawei's Sales and Solutions Department.

## Stack Summary
- **LLM (only):** AWS Bedrock — `qwen.qwen3-32b-v1:0` (NetworkAnalyzer_agent_bedrock.py). NO local LLM — Ollama/local qwen3:8b/RTX-GPU inference fully removed.
- **Databases:** 2 SQLite DBs linked by `msisdn`
- **API:** FastAPI + WebSocket streaming (server.py)
- **Frontend:** Custom HTML/JS/CSS chat UI (static/)
- **Dashboard:** FastAPI + custom HTML/JS/CSS (dashboard_server.py :8002 + dashboard_static/) — replaced Streamlit 2026-06-17. Old Streamlit (dashboard.py + pages/) moved to archive/streamlit_dashboard/. User hates Streamlit's generic look; all 3 web surfaces now FastAPI custom: chat :8000, ops :8001, dashboard :8002.
- **Data Simulator:** db_simulator.py
- **ML:** XGBoost churn model (churn_model.py) + pkl thresholds

---

## Databases

### NetworkAnalyzer_new.db (network/subscriber side)
- subscribers: msisdn, region, technology, status, tenure
- kpis_daily: date, cell_id, throughput, drop_rate, latency — latest date 2026-03-30
- alarms: cell alarms by severity
- dou_monthly: total_data_gb per subscriber per month (YYYY-MM)
- ott_monthly: OTT app usage + voice_minutes (added via migrate_voice.py)
- subscriber_technology: current_technology, current_cell_id, volte_active (NO usage columns)
- coverage: 5G coverage data — mandatory for upsell checks (EXISTS required)
- qoe_scores, mobility_events, sites
- experience_incidents: LIVE self-healing service-issue log (added 2026-07-16). msisdn, value_segment/is_hvc/region/cell_id (snapshotted → no join for HVC/site filters), affected_service (video streaming/mobile data/voice calls/all services), root_cause, severity, started_at/expected_resolution_at/resolved_at (datetime), status (active/resolved). "right now"=status='active', "today at X"=started_at, "streaming"=affected_service='video streaming'. Faults have a BLAST RADIUS (scope): site outage/power failure→whole site, cell congestion/backhaul→whole cell, per-customer faults→1 sub. Drives the coverage-map flashing + "Issues only" mode + click-to-diagnose. See [[NetworkAnalyzer Current Status & Open Issues]].

### operator_new.db (commercial side)
- customers, plans, subscriptions, billing
- customer_value: arpu per subscriber per month — 6 rows per subscriber, always filter by MAX(month)
- devices: model, is_5g_capable (NOT d.device_model — that column doesn't exist)
- churn_risk_score, churn_label added to customer_value

### Numbers — DO NOT store counts (and don't assume magnitudes)
The DBs update live via db_simulator.py, so any subscriber/segment count goes stale immediately. NEVER pin exact counts (active subs, 5G/3G/4G subs, upsell/FWA/HVC candidates, etc.) in memory — query the DBs on demand instead. The sim also ADVANCES THE CALENDAR: as of 2026-07-03 the latest dou_monthly month was 2026-07 (was 2026-03/04 earlier).

**Partial-current-month gotcha:** dou_monthly usage ACCUMULATES over the month, so the current (in-progress) month shows small totals — e.g., top data users were ~2–3 GB early in 2026-07 vs ~80 GB in completed months. "Top by data usage this month" is legitimately small early in the cycle; for full-month figures query a COMPLETED month. Verified an agent answer citing 3.14 GB / msisdn 2165751618 / Western Air Temple was CORRECT — nearly false-flagged it as hallucination by assuming ~80 GB from stale memory. Lesson: verify magnitudes against the live DB + current month; don't assume. (3.14 GB was real, not a fabricated "pi".)

---

## Agent Architecture
- **Primary:** NetworkAnalyzer_agent_bedrock.py — custom agentic RAG loop on AWS Bedrock
- **Parallel:** NetworkAnalyzer_agent_langgraph.py — LangGraph StateGraph implementation, same interface
- Both selectable from the UI; default is Bedrock 32B
- Models: qwen.qwen3-32b-v1:0 (default), qwen.qwen3-next-80b-a3b (80B option)
- Local LLMs (Ollama) fully removed — faster startup
- RAG loop: QUERY → observe results → CONCLUDE (single action per step)
- DB routing: "arpu","billing","churn","revenue" → strong operator signal even if "subscriber" in question
- SQL quality: ColCheck catches hallucinated columns pre-execution, injects real columns of affected table into error
- Auto-tag salvage: if model outputs raw SQL without action tag, agent detects SELECT, infers DB, runs it
- Enrichment rule: if only 1 query step and commercial angle, model must run 1 more before CONCLUDE
- RAG SQL examples in rag_sql_examples.txt — covers FWA, HVC, ARPU, churn, 3G migration, cross-DB
- RAG now uses BM25 + FAISS hybrid retrieval with RRF fusion (rank_bm25 package) — both knowledge and SQL indexes
- Schema Knowledge Graph (schema_graph.json + schema_graph_retriever.py) — JSON nodes/edges/join-paths, retriever injects only relevant subgraph per query
- Intent Classifier (in schema_graph_retriever.py) — classifies: chitchat/network_kpi/alarm/commercial/subscriber_tech/cross_domain/geography; adjusts prompt routing
- Semantic Cache (_sem_cache in agent) — cosine similarity ≥ 0.92 returns cached result, max 30 entries, cleared on reset_memory()

### Critical SQL Rules (from feedback)
- subscribers.region is a direct column — NEVER join subscribers with sites to get region
- kpis_daily date filter: always `(SELECT MAX(date) FROM kpis_daily)` — never `date('now')`
- customer_value: always filter by `WHERE cv.month=(SELECT MAX(month) FROM customer_value)`

---

## Churn Prediction Module (churn_model.py)
- Dataset: Indian telecom (Kaggle) — closer feature match than Telco dataset
- Model: XGBoost + SMOTE for class imbalance (9.1% churn rate)
- AUC-ROC: 0.885 — primary reliability metric (precision ~0.62 is acceptable given imbalance)
- Thresholds: percentile-based (p90 = high risk, p70 = medium risk) saved to churn_thresholds.pkl
- 15 features: arpu_drop_pct, arpu_m8, rech_amt_drop_pct, rech_amt_m8, max_rech_drop_pct, max_rech_m8, data_drop_pct, data_mb_m8, days_active_m8, rech_drop_pct, rech_num_m8, voice_drop_pct, voice_min_m8, has_roaming, tenure_days
- voice_minutes synthesized by technology type (5G>4G>3G>2G) — added to ott_monthly
- INR→Yuan normalization rate: 0.43
- _rescore_churn() and simulate_voice_update() wired into db_simulator.py tick loop

---

## Frontend Features (static/)
- **THEME (2026-07-13): Avatar nation theming REMOVED from the chat UI.** Now a neutral "standard AI" look: dark mode = near-black grays (default), light mode = white, indigo accent, Inter font (JetBrains Mono only for SQL). Toggle = 🌙/☀ button in kpi-bar + Appearance section in settings; `body.light` class + CSS vars, persisted in localStorage `na-theme`. setNation/nation-bg/AvatarFont/Bebas/Share Tech Mono all gone; charts.js has DARK_COLORS/LIGHT_COLORS palettes and reads CSS vars. Mindmap + strategy diagram stay permanently DARK canvas widgets (their node colors are hardcoded dark) via locally re-scoped CSS vars on .mm-root/.strat-diagram. The Avatar world COVERAGE MAP (atla_map.jpg) is KEPT — only the chrome was de-themed. Unused Avatar assets (bgs/logos/font) still on disk. Cache busts: style v6, main.js v4, charts.js v4.
- Streaming chat with thinking expander, steps expander
- Stop/abort button
- Copy / Fullscreen per message
- Export Data button — CSV with LLM-annotated CASE WHEN columns (recommended_action, priority, campaign_type)
- Strategy Diagram — silent button, D3 SVG two-column flow, LLM outputs JSON in CONCLUDE
- Mind Map (mindmap.js) — REBUILT: interactive drill-down tree. Card nodes (label+value, colored accent, glow on root/✦action), edge-anchored curved links color-matched to target. 3 layouts via cycle button: Top-Down (DEFAULT), Left-Right, Radial. Toolbar: Expand all / Collapse / Fit / Layout cycle / PNG / Fullscreen + breadcrumb. Click node = expand/collapse; hover reveals ◎ "solo" button = re-roots view to that branch (siblings vanish, drill into one); breadcrumb (⌂ All › … ›) restores. State held in `view` (current focus root) vs `root` (absolute). Hover tooltip, zoom/pan. Fullscreen targets the map element + ResizeObserver re-fit. chat.js passes ONE container (.mindmap-wrap).
- Mindmap "undefined" bug ROOT CAUSE (was NOT the converter): measure() sets d._label/_w/_h, but `root.each(measure)` ran AFTER default-collapse, and d3 .each() only walks visible .children (not collapsed ._children) — so depth-2/3 nodes were never measured → rendered "undefined" + no card (value was fine since it's set in toH). FIX: update() lazily measures any visible node with _w==null before place(). (toH._name + strip dash guards also added as belt-and-suspenders.)
- Mindmap readability: fit() keeps a MIN_READABLE 0.55 zoom (anchors top so you scroll/pan rather than shrink to a strip); Fit button passes fitAll=true to zoom-to-everything. ResizeObserver re-fits once canvas gets real size (fixes first-fit-stuck-at-bottom). Collapsible panel: .mm-header click toggles .mm-collapsed.
- Snapshot column collapse (THE right-side COVERAGE/Avatar map panel): Copilot tab = .two-col [.chat-col flex:3 | .snapshot-col#snapshot-col flex:3]. snapshot-col has tabs COVERAGE(#coverage-map Plotly Avatar map)/NETWORK/SUBSCRIBERS/COMMERCIAL. App.toggleSnapshot() (main.js) toggles .collapsed → col shrinks to 34px vertical "⟨ PANEL" bar, chat-col grows to fill; Plotly.Plots.resize on re-open. Collapse btn ⟩ in .snap-tabs, expand btn .snap-expand. (User's "make the map collapsable" meant THIS, not the mindmap.)
- Static assets cache-busted via ?v=N in index.html (charts.js/chat.js v=2; style.css/main.js/mindmap.js v=3) — bump N when changing a file.
- Breakdown / treemap rendering — REPLACED: hierarchical treemap charts (result.chart.type==="treemap") now render as the interactive drill-down TREE (NotebookLM-style), not Plotly. chat.js calls Mindmap.fromTreemap(chart) → converts labels/parents/values to hierarchy (strips parent prefix off labels, top-level branches get cycled colors, descendants inherit). Returns null for flat raw-ID grids (≥12 leaves, ≥60% id-like) → falls back to Charts.render. User explicitly wanted drill-down/"go deeper" UX + working fullscreen; Plotly treemap was cramped and its fullscreen was locked to a 280px box.
- charts.js treemap() is now only the FALLBACK for non-tree cases: id-grid guard rerenders as horizontal count bar (rolls up by leaf parent node, else "… – Region" suffix), or top-15 by value, or summary card. Legit treemaps (if ever hit directly) get per-branch colors + rounded tiles.
- SILENT VIZ FOLLOW-UP FIX (agent_bedrock.py): Mind Map / Strategy Diagram buttons send CONCLUDE-only silent requests reusing prior analysis (context empty). The "queries_executed==0 → reject CONCLUDE" guard rejected them → model looped → raw CONCLUDE JSON leaked as message text. Fix: is_viz_followup flag (q has "analysis above" + mind map/strategy_diagram) bypasses the no-query guard. Strategy silent prompt updated to include a "text" field (guard at ~line 2484 rejects CONCLUDE without text).
- Recommendations panel (collapsible)
- Plotly charts: bar, pie, histogram, treemap, heatmap, scatter

---

## Analytics Dashboard (dashboard_server.py :8002 + dashboard_static/) — built 2026-06-17
- Standalone FastAPI app, non-LLM, custom HTML/JS/CSS matching ops portal style (reuses the navy/blue design tokens from ops.css). Plotly via CDN (plotly-2.35.2). Currency = ¥ (Yuan).
- 4 sidebar sections: Network / Subscribers / Commercial / Campaigns. Region filter in topbar applies to active section. Lazy-loads per section.
- JSON endpoints: /api/network/{summary,by-region,worst-cells,alarms,incidents}, /api/subs/{summary,by-tech,tech-by-region,devices,mobility,sunset,lookup/{msisdn}}, /api/commercial/{summary,segments,plans,billing,hvc}, /api/campaigns/{summary,list,feedback,offers,opportunities}. DB helpers sc()/op() return dict rows; query strings copied from the old Streamlit pages (verified against live schema, 0 errors).
- Campaigns section surfaces the SMS feedback loop: per-campaign delivery/response funnel + latest inbound replies feed (ties in the [[Ops Portal & Action Pipeline]] campaign work).
- Replaced the old Streamlit dashboard (dashboard.py + pages/1-4) which imported the dead local-LLM agents and still showed TND — archived to archive/streamlit_dashboard/.
- Run: `python -m uvicorn dashboard_server:app --host 0.0.0.0 --port 8002`

---

## Data Simulator (db_simulator.py)
- Tick-based: runs on a schedule, updates KPIs, alarms, usage, churn scores
- _insert_new_subscriber(): creates full subscriber records across both DBs when pool exhausted
- Subscriber count no longer capped at ~49,990
- simulate_voice_update(): updates voice_minutes each tick
- _rescore_churn(): recomputes churn scores using pkl thresholds each tick
- Avatar world theme: _REGIONS has 4-tuple (region, city, area_code, nation), names generated per nation
- Email domain: @avatar.net, national ID prefix: AV

---

## File Structure (root = D:\Dev\SmartCareLLM)
- server.py — FastAPI app, imports from NetworkAnalyzer_agent_bedrock.py
- NetworkAnalyzer_agent_bedrock.py — THE agent (Bedrock only). Dead Ollama code fully removed 2026-06-17: no more _ollama(), set_gpu_layers(), MODEL/OLLAMA_BASE/NUM_YuanU_LAYERS/NUM_THREADS/CTX_SIZE vars, or `import requests`; startup banner now prints the real Bedrock model id. _llm() → _bedrock() only.
- NetworkAnalyzer_agent_langgraph.py — parallel LangGraph agent, also Bedrock (imports _llm from the bedrock agent); selectable via model='langgraph'.
- archive/legacy_local_llm_agents/ — the 4 Ollama-based agents (NetworkAnalyzer_agent.py base + _14b/_gemma/_qwen3) archived here 2026-06-17 (RTX 3050 4GB can't host a local LLM). Kept for report Chapter 2 (Local LLM First Attempt). Nothing active imports them.
- server.py model selector: only bedrock | bedrock-80b | langgraph (legacy 7b/14b/gemma/qwen3 routing gone; ollama thinking_enabled/think_budget handling removed). ws.js default model now 'bedrock' (was '7b').
- dashboard.py — Streamlit dashboard
- db_simulator.py — data simulator
- churn_model.py — ML model training
- FakeData.py — static DB generation
- archive/ — ~30 unused files archived here
- static/js/ — strategy_diagram.js, mindmap.js, chat UI JS

---

## Startup Slowness (not FastAPI)
Three things run at module import time in NetworkAnalyzer_agent_bedrock.py:
1. _load_schema_columns() — fast, just PRAGMA reads
2. _build_semantic_index() — loads SentenceTransformer all-MiniLM-L6-v2, encodes 259 columns (~3-5s)
3. _build_data_profile() — queries every table for stats at startup (a few more seconds)
RAG index also builds on first retrieve_sql() call (same model, possibly reused).

## Open Technical Issues
- Map: needs replacement with Avatar: The Last Airbender world map (Plotly go.Image + go.Scatter, pixel coords)
- Folder rename: D:\Dev\SmartCareLLM → D:\Dev\NetworkAnalyzer (do with VS Code closed)
- Multi-part questions (overall + breakdown) sometimes only run 1 query — planner step planned but not built yet
- Strategy diagram sometimes shows fabricated segment numbers when model skips enrichment query

---

## Run Commands
- Chat server: `python -m uvicorn server:app --host 0.0.0.0 --port 8000`
- Ops portal: `python -m uvicorn ops_server:app --host 0.0.0.0 --port 8001` (needs MQTT broker on :1883)
- Analytics dashboard: `python -m uvicorn dashboard_server:app --host 0.0.0.0 --port 8002`
- Simulator: run db_simulator.py directly
