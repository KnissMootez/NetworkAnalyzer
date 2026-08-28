---
name: NetworkAnalyzer Current Status & Open Issues
description: What was done recently, what's broken, what to tackle next — updated 2026-06-09
type: project
originSessionId: 3b53e3e6-83c7-4d5f-ba3b-892da0bd4243
modified: 2026-08-17T15:43:39.680Z
---

## Git (2026-06-11)
- Repo initialized in D:\Dev\SmartCareLLM, branch main, initial commit 0e48755 (72 files).
- User is a git beginner — walked through init/add/commit; explain git steps when using them.
- .gitignore excludes: .env (AWS keys), *.db/-shm/-wal, networkanalyzer-env/, archive/ (old DBs), exports/export*.csv, *.zip, *.pdf (REAL Huawei internal docs — never commit/push), media/ (Manim output), odysseus/ (external project), .data_profile_cache.txt
- Remote: https://github.com/KnissMootez/NetworkAnalyzer (PRIVATE — keep it that way, Huawei work). Pushed 2026-06-11, origin/main tracks main.
- GitHub username: KnissMootez. Identity set globally: Mootez Kniss / knissmootez@gmail.com (GitHub account may use a different email — harmless, only affects contribution graph).

## Anti-hallucination: grounding guard + composite-score fix (2026-06-17)
Root cause of the "top data users" fabrication (99.47/98.72/97.54 GB when real max was ~81): the SCORING path. "top N"/ranking questions route to the text chain's composite-merge (`[Tools] falling back: scoring → chain path`), and `_compute_composite_score` (~L2050) used to ask an LLM to "merge datasets and return a JSON array of subscribers with msisdn/region/arpu/..." → the LLM INVENTED the whole table (clean ~1GB descending steps).
- **FIX (root)**: rewrote `_compute_composite_score` — deterministic merge on msisdn in Python; the LLM ONLY returns a ranking ({msisdn, composite_score, retention_offer}); every RAW value is reattached from the real merged rows keyed by msisdn; fabricated msisdns dropped; fallback to real merged rows if LLM output unusable. The model can no longer emit data values. Verified offline (real 81.1 kept, fake 99.47 ignored, fake msisdn dropped).
- **FIX (safety net)**: `_ground_text(text, results_blob, question)` — if the answer cites ≥2 numbers absent from the query rows (≥50% of significant numbers), regenerate once from the real rows. `_grounding_issues` extracts significant numbers (decimals + ints≥1000), skips years and ID-like ints≥7 digits (msisdn-dilution bug — fixed), rounding-tolerant. Now applied to BOTH paths: `_finalize_tools` (tools) AND the text-chain finalize (where this bug lived; grounds `text_only` against `context`). results_blob accumulated in tools loop.
- Residual: `composite_score` is still LLM-derived (not a raw value) — raw data values are now always real. Detection verified offline; live regen unverified (Bedrock import flaky locally). RESTART server to load.

## Verbatim context window for current conversation (2026-06-17)
User wanted a PROPER context window for the CURRENT conversation only — NOT cross-session persistence, NOT picking up previous sessions. Built Part 1 of the scoped memory fix (NOT the last_result store — explicitly out of scope).
- `_run_chain_tools` (the PRIMARY tools path) previously seeded its messages with the lossy `_memory_context()` SUMMARY + question. Now it seeds with the VERBATIM prior turns: `_hist=_conv_history()` (last `_MAX_CONV_TURNS*2`=20 msgs), drop the trailing current user msg (run_agent already appended it), re-add `{"text":"Question: "+question}` (resolved form). So the agent sees the real turn-by-turn conversation, like a normal chatbot.
- `_conv_messages` is lean (only user question + assistant answer text per turn — NO raw SQL row-dumps), so verbatim context doesn't bloat. Cap stays 10 turns.
- Verified: prior answer text retained verbatim in context, current question not duplicated, valid user/assistant alternation, first-turn edge OK. (Couldn't run the full function — RAG/embeddings import was hanging on network; logic validated standalone + py_compile.)
- STILL using summary: the TEXT-chain fallback (`_run_chain`) still uses `_memory_context()` + history only on conclude — left as-is (it's the fallback path). Cross-session persistence NOT added (globals reset per session). The `last_result` queryable store for table-only data (the "show their names" case) was NOT built — out of scope per user.

### Upgraded to HYBRID memory (2026-07-03)
User asked (mentor question) to match how production chat works — recent verbatim + summarized older. Built:
- `_rolling_summary` global (gist of turns older than the verbatim window). `_evict_to_summary()` runs when `_conv_messages` exceeds `_MAX_CONV_TURNS*2` (20 msgs / 10 turns): pops the overflow, folds it (merged with prior summary) into `_rolling_summary` via `_llm_quiet` (only fires past 10 turns → short chats pay nothing). Replaced the two hard-trim sites in run_agent + reset_memory clears it.
- Tools path (`_run_chain_tools`) prepends `[Summary of earlier conversation: ...]` to the earliest context message → model gets old-gist + recent-verbatim + current Q. Bounded token cost, no hard-forgetting.
- Verified offline (mocked summarizer): 14 turns → 20 msgs kept verbatim, oldest = turn 5, summary populated + prepended. Live Bedrock summary quality unverified. Mental model for mentor: LLMs are stateless → memory = re-feeding text each call; hybrid = verbatim recent + rolling summary of old (same pattern as this Claude harness). Ephemeral/in-RAM ("like incognito") but GLOBAL not per-user (known limit).

## Real-time self-healing experience incidents (2026-07-13)
User wanted to ask the agent live "at this moment, do we have any HVCs having streaming issues?" AND have those issues auto-fix over time (no manual DB edits) while still being able to say "today at 14:32 this HVC had this issue." Built a full incident LIFECYCLE.
- **NEW table `experience_incidents`** (SC_DB, in `_ensure_new_tables`): incident_id, msisdn, value_segment, is_hvc, region (all SNAPSHOTTED at open → no join needed for HVC filters), affected_service ('video streaming'/'mobile data'/'voice calls'/'all services'), root_cause, severity (minor/major/critical), started_at + expected_resolution_at + resolved_at (full 'YYYY-MM-DD HH:MM:SS' timestamps), status (active/resolved), duration_min, complaint_id. Indexed on status/msisdn/started_at.
- **`simulate_experience_incident` REWRITTEN**: opens timestamped ACTIVE incidents for a few gold/platinum HVCs (skips anyone already active), from `_INCIDENT_SCENARIOS` (cause,service,severity) with demo-fast heal windows `_INCIDENT_DURATION_MIN` (minor 5-12m / major 10-20m / critical 18-30m). Lays coherent evidence: poor qoe_daily rows for the service's apps (`_SERVICE_APPS`), a service_interruptions row, and a complaint (linked via complaint_id) for major/critical. Rescores churn → RISES.
- **`simulate_incident_resolution` NEW** (runs EVERY tick): closes any active incident past expected_resolution_at → status='resolved', resolved_at stamped, linked complaint marked resolved, churn rescored → RELAXES. Row survives forever so the timeline query still works.
- **Churn penalty rebased** in `_rescore_churn`: now driven by ACTIVE incidents (sev weights critical .30/major .18/minor .10) + still-OPEN complaints (.05), clipped .40 — NOT the persistent daily qoe/interruption evidence (which never clears). So churn tracks the live issue: verified 0.32(med)→0.62(high) on critical active→back to 0.32(med) on heal, exactly +0.30.
- **Backfill** seeds ~historical incidents (mostly resolved, a few live) so the table's never empty.
- **Agent wired**: added to `_SC_TABLES` + the `_sc_tables` routing set (+ complaints); schema doc + BEDROCK_CRITICAL_RULES describe it ("right now"=status='active', "today at X"=started_at, "streaming"=affected_service='video streaming'); schema_graph.json node+edge; 3 RAG examples in rag_sql_examples.txt (active HVC streaming issues / today's timeline with times / active summary by service+severity).
- **GOTCHA hit during testing**: `_rescore_churn` UPDATEs `customer_value WHERE month=now()` but MAX(month) in the DB is 2026-04 (sim not running). `ensure_current_month()` at sim startup creates the current-month rows so the UPDATE lands — churn coupling only visibly moves once the sim has run (or you rescore against 2026-04). Not a bug.
- All offline/DB-verified; live Bedrock narration UNVERIFIED (restart server to load new RAG examples + schema).

### Map flashing: light up the offending cell site when asked about issues (2026-07-13)
User idea: when you ask the agent about customers having issues, the coverage map should FLASH the cell site(s) with the issue. Built deterministically (data-driven, not LLM-driven).
- **experience_incidents gained a `cell_id` column** (snapshotted from subscriber_technology.current_cell_id at open; guarded ALTER upgrades the already-created live table; backfill + simulate_experience_incident both populate it). This is what ties an incident to a site (cell_id → cells.site_id → sites.latitude/longitude).
- **server.py**: `_active_incident_sites(region=None)` joins active experience_incidents → cells → sites, groups by site, returns site_name/region/lat/lon/incidents/hvc/sev_rank(3=crit,2=maj,1=min)/services. Endpoint `GET /api/incidents/active-sites?region=`. `_compute_map_flash(question)` = `_ISSUE_MARKERS` regex (issue/problem/outage/streaming issue/right now/at this moment/currently/…) AND optional region match (`_known_regions` from sites) → returns sites, else []. Injected as `map_flash` into the WS `done` payload (added `import re`). Verified: streaming-issue Q → all sites; "problems in Ba Sing Se right now" → 7 BSS sites only; "avg ARPU by region" → [] (no false trigger).
- **Frontend**: sites are Plotly scatter over atla_map.jpg in pixel space IMG_W=4096×IMG_H=3072, plotted x=longitude, y=IMG_H−latitude (site lat/lon ARE pixel coords, no geo projection). `App.flashCoverage(sites)` in main.js (exported) adds a "⚠ Live issue" trace (circle-open ring, color by severity red/orange/yellow), pulses it via setInterval restyle (size 22↔52), auto-expands the snapshot panel + switches to the Coverage tab (snap-map), toasts. `_clearFlash` removes the prior ring by trace name. chat.js finalize calls `App.flashCoverage(result.map_flash || [])` — empty list clears stale flashes. Cache-bust: chat.js v6, main.js v5 in index.html.
- Frontend runtime UNVERIFIED (no browser); backend query + detection DB-verified. All active incidents now carry cell_id.
- **BUG FOUND + FIXED on first live run (2026-07-13)**: the map NEVER flashed. `query_sc(sql)` takes SQL ONLY (no params) but `_active_incident_sites` called `query_sc(sql, tuple(params))` → TypeError → swallowed by `_compute_map_flash`'s bare `except: return []` → map_flash always []. FIX: added `_query_sc_params(sql, params)` in server.py (own sqlite3 conn, row_factory=Row, imports SC_DB from the agent) and made the except PRINT the error. LESSON: query_sc/query_op take NO params — bind on your own connection. Don't let a defensive except hide the failure it catches.

## Incident BLAST RADIUS / scope model (2026-07-16)
User caught it via click-to-diagnose: "critical Power failure at site" affecting **1 subscriber out of the 53 on that cell** — physically nonsense. Root cause: incidents were per-customer, sampled only from gold/platinum, so infrastructure faults had no blast radius. (All the agent's FACTS were verified correct — the DATA was wrong, not the agent.)
- **`_INCIDENT_SCENARIOS` is now (cause, service, severity, scope, weight)**. `scope`: 'site' (Site outage, Power failure at site) → every active sub across all cells of the site; 'cell' (Cell congestion, Backhaul degradation, Signal interference) → every active sub on the cell; 'subscriber' (Throughput throttling, CDN peering, DNS failure, Packet loss, VoLTE bearer drop) → 1 customer, biased gold/platinum. `weight` makes site outages ~3% of events (subscriber 48 / cell 15 / site 2).
- **`_incident_targets(scope, active_now)`** NEW. GOTCHA: picking a random cell from `cells` returned 0 subscribers (many cells serve nobody) → cell events silently no-op'd. FIX: seed off a random ACTIVE SUBSCRIBER and use their cell/site — guarantees a populated cell AND weights by population (busy cells congest, which is realistic). `_subscriber_snapshot(msisdns)` batches region+cell (SC) and value_segment+is_hvc (OP) for everyone hit — verified exactly against ground truth.
- **Budget counts EVENTS not rows**: `_MAX_NEW_EVENTS=3`, `_MAX_AFFECTED_PER_EVENT=500`. Site sizes measured: **411 sites, max 472, median 116, only 1 over 400** — so 500 covers EVERY site outright and a power failure = 100% of its site. The earlier 400 cap truncated Sunset City (472) to 120/400 and the agent then narrated "roughly 25% of its user base is offline" — a TRUNCATION ARTIFACT reported as fact. Lesson: a cap that bites shows up in the narrative. SQLite's real param limit is **32766** (verified via getlimit), not the 999 I'd assumed — the cap was never param-constrained. `_COMPLAINT_RATE_BULK=0.12` — a mass outage doesn't generate 400 complaints.
- **ALL writes batched** (`sc_write_many`/`op_write_many`) — `sc_write` opens a connection PER CALL, so a 400-sub outage would have been 400+ connections. A 156-sub site outage takes 1.3s incl. churn rescore.
- `simulate_incident_resolution` also batched; complaints now close by (msisdn, category, status='open') since bulk rows carry no complaint_id link (column kept, now always NULL).
- **Verified**: site outage 156 served → 156 knocked out (147 HVC, rich area); Sunset City site → 472 served, 389 bronze + 83 silver, **0 HVC** — that's the city-tier wealth model, NOT a bug (truncation is unbiased: first 120 mirrored the full 472). Same fault, opposite commercial urgency — exactly the realism that was missing. Map still draws ONE ring per site regardless of row count.
- Offers/remedies: agent invented "100% data bonus + 50% discount" though a REAL `offers` catalog exists (HVC Platinum Pack 25%+100GB/90d, HVC Gold Upgrade 15%+50GB/60d). User DEPRIORITIZED ("it needs a reference no?" — yes, offers is that reference; grounding remedies there is a small RAG example whenever wanted). NOTE: offers.description still says **TND** (stale pre-Yuan currency) — agent will quote it.

## "Issues only" map mode + incident RATE bug (2026-07-16)
User: the only way to hide the rings was clicking the Plotly legend entry — wanted a real toggle, an issues-ONLY view, hover to see the issue, and click-to-diagnose.
- **INCIDENT RATE BUG (mine)**: original `simulate_experience_incident` picked `LIMIT 4` customers; I widened it to `LIMIT 12` so I could skip already-active ones — but then OPENED all 12 → 12 incidents every ~4 min → **49 active at once** (projection for cap=12 was ~54 ✓). That's why the map looked flooded. FIX: `_MAX_NEW_INCIDENTS = 3` + `if len(affected) >= _MAX_NEW_INCIDENTS: break` — wide candidate pool, capped open rate. Steady state ≈ open_rate × avg heal (~15.8 min) → **~14 active**. THE DIAL for how busy the map looks is `_MAX_NEW_INCIDENTS`.
- **Backend**: `_active_incident_sites` also returns `causes` (GROUP_CONCAT DISTINCT root_cause) + `since` (MIN started_at) for hover.
- **Frontend (main.js)**: rings refactored into `_renderIssueRings(sites)` shared by BOTH entry points (agent `flashCoverage` = answer-scoped; `toggleIssuesOnly` = all active). One trace PER SEVERITY named "⚠ Critical/Major/Minor" (legend reads well + per-severity legend toggle); single interval pulses each at its own period (4/7/11 → worse = faster blink) via `Plotly.restyle({marker.size:[s1,s2,s3]}, _ringIdx)`. `_isRing(t)` = name starts with "⚠". `_clearFlash` deletes all ring traces. Hover shows site/region/service/root cause/severity/count/HVC/since. Click a ring → `App.quickQuery` asking the agent to explain the issue + how to fix it (branches before the old generic "Investigate cell site" handler). `_applySiteLayerVisibility()` sets healthy tech traces to `legendonly` in issues-only mode. `_loadCoverage` re-applies the mode after a theme re-plot.
- **UI**: `.map-toolbar` + `#issues-toggle` chip (red pulsing dot when on) + `#issues-count` badge + hint text, above #coverage-map in index.html; CSS in style.css. Cache-bust: style.css v7, main.js v7, chat.js v6.
- Frontend runtime still UNVERIFIED (no browser). JS syntax-checked via `node --check`.

## Map flash scoping — flash must match the ANSWER (2026-07-13)
2nd live run: flashing WORKED but lit up ~25 rings all over the map for an answer about 9 HVC streaming incidents — because `_compute_map_flash` flashed ALL active incidents regardless of service/segment. FIX: `_flash_filters(question, result)` in server.py MIRRORS THE AGENT'S OWN SQL — `result["steps"]` entries carry `{"tag","sql"(:200 chars),"rows"}`, so it regexes the executed experience_incidents SQL for `affected_service='..'`, `is_hvc=1`, `severity='..'`, `region='..'` and applies the same filters to the site query (trusting the agent's WHERE = exact answer scope). Only if NO incident SQL ran does it fall back to parsing the question (`_SERVICE_FROM_Q` / `_HVC_FROM_Q` / region). `_active_incident_sites(region, service, hvc, severity)` now takes filters; endpoint too; LIMIT 40→25. Verified vs the real agent SQL: 25 sites → 10 (== the 10 matching incidents), regions match the answer. Also toned the pulse down (26↔52px → 16↔28, line 4→2.5) since big rings merge into blobs where sites cluster (Ba Sing Se). main.js v6.
- Design rule for this feature: the map must never light up a site the agent didn't talk about. Mirror the SQL, don't guess from the question.

## Live-run findings on the incident feature (2026-07-13)
First real Bedrock run of "at this moment do we have any HVCs having issues with their streaming?" — agent used the RIGHT SQL unprompted (`experience_incidents WHERE status='active' AND is_hvc=1 AND affected_service='video streaming'`) and every number was DB-verified correct (6 incidents, causes 3/2/1, regions BSS 4/Gaoling 1/FNC 1). Two real problems fixed:
- **Fabricated negatives**: agent claimed "not linked to low RSRP or high latency", "no active alarms on the cells" after running only 2 queries (COUNT + SELECT) — it never queried RSRP/latency/alarms. `_ground_text` only validates NUMBERS, so unverified qualitative/causal claims slip through. FIX (general principle per the no-whack-a-mole rule, added after the RANKING rule in BOTH AGENT_SYSTEM and AGENT_SYSTEM_TOOLS — the two lines are identical so one replace_all hits both): "ONLY CLAIM WHAT YOU QUERIED" — negatives/causal claims need a query that looked and got zero rows; if root_cause exists, report it, don't speculate about mechanisms never measured.
- **Clarify fired with schema-inventing options**: `_AMBIGUOUS_MARKERS` contains "issues"/"problems" so EVERY real-time issue question tripped `_llm_clarify_gate`; and `_CLARIFY_SYS`'s hardcoded "Available data:" inventory never mentioned experience_incidents → model invented options (low throughput / high latency / dropped calls) that don't exist in the data model. FIXES: (1) refreshed `_CLARIFY_SYS` inventory to include the live issue log + "NEVER ask the user to define a term the data already defines (HVC=is_hvc, issue=a row in the log)"; (2) NEW `_is_live_incident_q(q)` (`_LIVE_NOW_RE` AND `_ISSUE_WORD_RE`) short-circuits `_maybe_clarify` → live-issue questions skip the whole gate. Verified: 4 live-issue Qs skip, "what issues should we focus on"/"biggest problems"/ARPU still clarify. Principle: when the data model already defines the terms, don't ask.

## Open follow-ups from the de-theming session (2026-07-13)
- Ops portal (:8001) + analytics dashboard (:8002) still have the old navy look — user may want the same neutral light/dark treatment there (offered, not yet requested).
- Unused Avatar assets still in static/ (nation bgs, logos, Avatar Airbender font) — can be archived.
- Strategy diagram quality: model produced 14 segments all "blue"/same strategy on the 5G-upsell question — model laziness, not a bug. Per the no-whack-a-mole rule, only add a prompt nudge (distinct colors/strategies per tier) if it recurs.
- Live Bedrock verification of the conclude-leak fix pending (offline-verified only; server needs restart to load).

## Conclude text-leak fix #2 — labeled sections inside text field (2026-07-13)
Tools-path leak the old guard missed: model calls the conclude TOOL but writes legacy labeled sections INSIDE the text field as plain text ("...answer. Recommendations: 1. ... strategy_diagram: {json}") — not whole-JSON, so the `text.startswith("{")` unwrap never fired and raw diagram JSON rendered in the chat bubble (seen live on "5G upsell opportunities by region"). FIX in `_finalize_tools`: (1) lift embedded `strategy_diagram:/mindmap:/chart: {...}` blobs via `_first_json_obj` into their result fields (only if the structured field was empty), stripping them from text; (2) parse inline "Recommendations: 1. …" numbered list into the recs list (guarded: only when followed by "1."); (3) hard `re.sub(r'\{.*\}','')` on the final text — same brace-strip the text-chain path always had. Verified offline vs the exact leaked output + 3 regressions. RESTART server to load.

## Per-customer experience incidents -> churn (2026-06-17)
Feature: agent can report "customer X had these service issues today, churn risk rising" for retention/upsell. Built in db_simulator.py:
- **Churn coupling (chosen: penalty, not retrain)**: `_rescore_churn` now adds an EXPERIENCE PENALTY on top of the usage-based model score: `min(0.40, 0.05*poor_qoe_days_7d + 0.10*complaints_7d + 0.07*interruptions_7d)`, clipped to 1.0. So recent poor QoE / complaints / service interruptions genuinely raise churn_risk_score. (Usage churn model itself ignores QoE — this is the link.)
- **`simulate_experience_incident`** (NEW, runs every 3 ticks): picks ~4 gold/platinum customers, injects a COHERENT episode for today — poor `qoe_daily` (video+gaming, label='poor'), a `service_interruptions` row, sometimes a `complaint` — all from one root cause (`_INCIDENT_CAUSES`). Then rescleores those msisdns so churn rises immediately.
- **Tables/grain**: qoe_daily (SC DB; PK msisdn,date,app_type; experience_label poor/fair/good/excellent; ~200 random subs/tick organically via simulate_qoe_update). complaints (SC). service_interruptions (OP). churn_risk_score/churn_label in op.customer_value.
- **RAG examples** (chosen: assemble-on-query, no new table) in rag_sql_examples.txt: (1) per-customer issue digest (poor_qoe_days/complaints/interruptions + churn), (2) "high-value customers at risk from recent service issues" retention list. Anchor "recent" to `date((SELECT MAX(date) FROM qoe_daily),'-7 days')`.
- **Verified**: injected issues on a platinum sub -> churn 0.459 -> 0.766 (high), +0.31; both RAG queries execute; retention list already returns 20 organic targets. Test data cleaned up. UNVERIFIED: live Bedrock narration.
- Ties into retention/upsell -> ops-portal origination + campaign loop ([[Ops Portal & Action Pipeline]]).

## Baseline / Threshold Clarify — human inputs the number (2026-06-17)
- User rule: whenever an answer hinges on a NUMERIC THRESHOLD/baseline, the human must get to type their own number (with a "let the agent choose" fallback). Motivation: "ARPU > average" is misleading on skewed data — don't silently use the mean.
- `_maybe_clarify_baseline(q)` (NetworkAnalyzer_agent_bedrock.py): pre-filtered by `_BASELINE_MARKERS` (high arpu/above average/heavy data/high churn/etc), then a quiet LLM call with `_BASELINE_SYS` → JSON {clarify, question, metric, unit, suggested, template (MUST contain {value}), options}. Returns a clarify dict with a NEW `input` block {metric, unit, template, placeholder} + a guaranteed "let the agent choose a sensible cutoff" option.
- `_maybe_clarify` order is now: BASELINE → dimensions → general gate (so "high-ARPU subscribers by region" gets the number input, not the dim chooser).
- Payload: result["input"] forwarded by server.py (done payload). chat.js renders a number field + unit + Apply button (Enter submits) ABOVE the option buttons; Apply fills {value}→typed number and App.sendSilent(filled). CSS .clarify-input* in style.css. Cache-bust bumped chat.js/style.css v=4→v=5.
- BYPASS: run_agent registers the template's static prefix (text before {value}) in `_recent_clarify`, so a human-typed number bypasses the chooser and runs directly (substring match). Verified offline (mocked LLM): gate fires, template fills, prefix matches, ordering correct, plain drills unaffected. UNVERIFIED: live Bedrock JSON quality + browser rendering.

## General Curiosity / Clarify (2026-06-09, updated)
- User wants the agent to ALWAYS offer choices on drills (even when dims named) AND to be generally curious "if it must" on any vague question.
- _maybe_clarify(q) = _maybe_clarify_baseline(q) OR _maybe_clarify_dimensions(q) OR _llm_clarify_gate(q):
  - _maybe_clarify_dimensions: ANY subscriber drill/breakdown (gate no longer excludes named dims). If dims named, first option = "Run exactly what I asked" (query=original). Else presets.
  - _llm_clarify_gate: for vague questions only (gated by _AMBIGUOUS_MARKERS: best/worst/improve/analyze/what should/focus on/biggest/priorit/etc — specific Qs skip, no latency). Runs _llm_quiet (non-streaming so no token leak) with _CLARIFY_SYS → JSON {clarify, question, options[]}; prefers clarify:false. Options are full standalone queries + a "Just decide for me" default.
- LOOP SAFETY: _recent_clarify set stores offered option queries. run_agent checks bypass (substring match) BEFORE calling _maybe_clarify → a clicked option runs directly, never re-asks, no wasted LLM call. reset_memory clears it.
- run_agent order: chitchat → clarify(bypass-or-ask) → intent → fast-path → tools/chain. clarify returns type "clarify" w/ options; chat.js renders .clarify-btn; click=App.quickQuery(option.query).
- Backend-only change since last frontend bump → just restart server (no Ctrl+F5 needed, but harmless).

## Dimension Clarify Feature (2026-06-09)
- NEW response type "clarify": when a drill/breakdown request about subscribers names NO dimension, agent returns options (clickable drill-down paths) instead of guessing. _maybe_clarify_dimensions() in agent: trigger = drill verb + subscriber entity + NO named dim/" by ". Options = _SUBS_DRILL_PRESETS (self-contained queries like "drilldown of subscriber counts by region, technology, and value segment"). Clicking an option = App.quickQuery(option.query) → runs normal drilldown → tree. NO pending/resume state (options are full queries; the query has named dims so it won't re-trigger clarify).
- Wired: agent run_agent checks clarify right after chitchat (before fast-path/tools/chain); server forwards result.options in done payload; chat.js finalize renders .clarify-options buttons (response_type==="clarify"); CSS .clarify-btn. Gate verified offline (8 cases). Frontend cache-bust: chat.js v=3, style.css v=4.

## Native Tools Prompt (2026-06-11) — VERIFIED WORKING
- AGENT_SYSTEM_TOOLS: dedicated tools-native system prompt (6.6K chars, no legacy CONCLUDE:/QUERY_SC: tags) used by _run_chain_tools; the "OUTPUT MODE OVERRIDE" patch block is deleted. Legacy AGENT_SYSTEM (10.4K) untouched for text-chain fallback.
- Runtime-verified: server logs show "[Tools v2] conclude tool called" (native tool use, no salvage) on a real question.
- Commit a3307a2, pushed to GitHub.
- Stale cosmetic: startup banner still prints "Model: qwen3:8b / YuanU layers: 25" from local-LLM days — not fixed yet.

## Agent Tool-Calling Refactor (2026-06-09)
- **_run_chain_tools()** NEW: structured Bedrock converse `toolConfig` loop replacing regex tag/JSON parsing. Tools: run_sql(database network|operator|both, sql), inspect_schema(table), conclude(text/recommendations/chart/strategy_diagram/mindmap), propose_action(description). Reuses ALL guards (_check_sql, _check_sql_semantics, _diagnose_zero_rows, query_sc/op, _build_treemap_from_context) + _finalize_tools() for conclude post-processing (currency/markdown clean, treemap build, export SQL). Eliminates the raw "CONCLUDE: {...}" leak class.
- **Auto-fallback**: run_agent tries _run_chain_tools; on _ToolsUnsupported (model/region rejects toolConfig) or any exception → falls back to _run_chain (text-tag path, fully preserved). Zero regression risk. `_tools_ok` flips False permanently if model lacks tool support; env `AGENT_TOOLS=0` disables.
- **UNVERIFIED at runtime**: whether Bedrock qwen.qwen3-32b-v1:0 actually accepts toolConfig (couldn't test w/o $). If logs show "[Tools] falling back to text chain" → not supported, behavior = old. Offline-tested the loop logic with a mocked client: run_sql→conclude, propose, and unsupported-fallback all pass.
- Scoring questions (score/rank/composite/weighted/prioriti/top N) deliberately raise _ToolsUnsupported → use chain's composite-merge path.
- **CONFIRMED at runtime: Bedrock qwen3-32b DOES support toolConfig** (think panel populated = tool path ran). BUT model often ignores the conclude tool and writes the answer as JSON TEXT (because AGENT_SYSTEM still describes the legacy CONCLUDE:{json} format) → old code looped → "couldn't finish" + JSON dumped 4x in think panel + 156s latency. FIXED: (1) system override block telling it tools are mandatory + legacy CONCLUDE/QUERY tags obsolete; (2) _try_text_conclude + _first_json_obj salvage — if model emits conclude as text, parse first balanced JSON obj and _finalize_tools it (returns on that step, fast); (3) _finalize_tools unwraps JSON crammed into the text field; (4) answer-JSON text not streamed to think panel. Verified offline: text-conclude (even repeated) → clean result w/ chart+recs.
- Tool path latency: huge reused AGENT_SYSTEM prompt every converse step is slow (~saw 156s on a looping run). With salvage it finalizes in ~2 calls. If Qwen keeps fighting tools, set AGENT_TOOLS=0 to use proven text chain (now also has fast-path + retry).

## Agent Performance/Robustness Pass (2026-06-09)
- **Bedrock retry/backoff** (_bedrock): retries transient errors (throttle/timeout/5xx/429) up to 4x with exponential backoff; only retries when 0 tokens streamed (avoids dup tokens); keeps partial output on mid-stream failure. Added `import time, random`.
- **Fast path RE-WIRED** (was dead code): run_agent now routes simple single-metric questions to _fast_query (single-shot, skips 8-step loop) when `_is_simple_metric()` AND not treemap/drilldown/breakdown. Big latency win on "how many 5G subs"/"avg throughput" type Qs.
- **Concurrency lock** (server.py `_agent_lock = asyncio.Lock()`): wraps the per-message run block so two WS connections can't clobber the shared module-global _streaming_queue/_memory. NOTE: still NOT per-user memory isolation (true multi-user needs session-keyed state) — flagged, not done (too invasive for demo).
- **eval_agent.py** NEW: golden-set harness. `--refs` computes ground-truth from DBs (free, no LLM); `--list`; `--only <cat>`; full run calls run_agent (needs Bedrock $). `--refs` is the source of truth for current counts — run it instead of trusting any number written down (DBs update live via simulator). Resets memory between cases. Categories: metric/categorical/distribution/hallucination/safety/route/empty.
- **Known agent issues flagged but NOT changed** (risk): action/JSON parsing is regex-based (greedy `\{.*\}`) → tool-use/structured-output via Bedrock toolConfig would be the real fix; think_budget/allow_thinking are no-ops on Bedrock Qwen3; CONCLUDE fires many serial LLM calls (export-sql/sanitizer/recs/self-check) — could parallelize; anti-hallucination is a small keyword allowlist; step budget can be eaten by rejections.

## Recently Completed (this session)

### RAG & Retrieval Upgrades
- BM25 + FAISS hybrid retrieval with RRF fusion — both knowledge and SQL indexes (rank_bm25 package)
- Schema Knowledge Graph: schema_graph.json (15 tables, named join paths, constraints, wrong-column warnings)
- schema_graph_retriever.py: subgraph retriever injects only relevant join paths per query; includes intent classifier
- Intent Classifier: classifies chitchat/network_kpi/alarm/commercial/subscriber_tech/cross_domain/geography; adjusts DB routing hints
- Semantic Cache: cosine sim ≥ 0.92 returns cached result instantly (30-entry LRU, cleared on reset_memory)

### Agent Bug Fixes
- ZeroRowDiag ATTACH fix: strips ATTACH statement before running bare diagnostic query
- UniformityCheck false positive: skips columns whose uniform value matches a WHERE filter value (e.g. current_technology='3G')
- CONCLUDE raw JSON display: collapses multi-line JSON + hard prefix strip for action tags
- SCHEMA step row count: was hardcoded 0, now 1 if found / 0 if not found
- Markdown essay sanitizer: detects headers/bullets/tables in text field, rewrites via LLM

### Chart Fixes
- _chart_from_context: prefers categorical label cols (region/technology/segment) over msisdn/IDs; aggregates rows by label; caps at 20 bars
- _build_treemap_from_context: detects subscriber-level data (msisdn present) → builds bar chart grouped by region instead of plotting 97 phone numbers; filters ID cols from treemap dimensions; caps dimensions at 3 levels

### Settings Panel & UI
- Settings panel CSS + HTML complete: nation theme (2×2 logo grid), model selector, portals link
- Nation logos: logo_water.jpg, logo_earth.jpg, logo_fire.jpg, logo_air.jpg in static/
- Thinking mode removed (not exposed by Bedrock Qwen3)
- GPU layers / RAG mode removed from settings (Bedrock only now)

### ARPU Update
- update_arpu.py: city-tier wealth model, range 2–250 Yuan, avg ~105 Yuan
- Realistic distribution: Ba Sing Se Inner Ring ~155 Yuan avg, HeiBai's Forest ~22 Yuan avg

## Open Issues

### Known Model Behavior
- Agent sometimes tries QUERY_SC with op.customer_value before QUERY_BOTH — self-corrects but wastes a step
- Scoring/ranking queries (composite multi-metric) sometimes produce fewer steps than ideal
- Export SQL occasionally truncates on very complex queries

### Report Status (rewritten 2026-08-17 — supersedes the "Not Started"/chapter-order entries above)
Report source lives at `D:\Dev\report\ENIT TIC Report Template\ENIT TIC Report Template\` (LaTeX Workshop + MiKTeX, compiles clean with latexmk) and is now ALSO backed up in the SmartCareLLM repo under `report/` (source .tex/.bib/figures only — build junk gitignored via `report/.gitignore`, main.pdf NOT tracked, caught by the root `*.pdf` blanket rule same as everywhere else). Pushed to origin/main 2026-08-17.

**Real chapter order now (renumbered from the old 7-chapter plan — no churn chapter, no separate confidentiality/ops-portal chapters, user explicitly rejected those as too granular):**
1. Host Organization and Project Context — DONE
2. Building the Foundation — Data and First Prototype — DONE (schema TikZ diagram, fictional-geography subsection, local Ollama prototype)
3. Moving to Cloud Inference — DONE (rewritten in first person per [[Report Writing Tone]] tone-feedback update)
4. **A Living Database** — DONE, NEW chapter, db_simulator.py as its own chapter (tick loop, incident lifecycle + blast radius, self-healing, churn coupling, map flashing, partial-month gotcha) — placed BEFORE the agent chapter per user's explicit reasoning ("the agent depends on the db, not the other way around")
5. **How the Agent Works** (renamed from "Agent Capabilities" — user wanted a title covering both the loop mechanism AND what it can do) — DONE, contains the reasoning loop + grounding + mistake-catching + actions/visuals (charts/mindmap/strategy diagram/CSV export) + ops portal as a SUBSECTION (not its own chapter) + fast path/cache + hybrid memory. The old "Proving It Works: Evaluation Harness" section was CUT — git history showed eval_agent.py has one commit ever (initial commit) and nothing imports it, no evidence it's actually been run, so citing it as proof was unverifiable and got removed.
6. General Conclusion — still placeholder text, NOT STARTED

Churn prediction (XGBoost+SMOTE) intentionally stays a small mention inside Ch4, not its own chapter — user: "idc about churn prediction tbh it was never a big task."

**Bibliography**: was previously bare in Ch2-5 (only Ch1 had real citations). Added real sources: aws_bedrock, react (Yao et al. ReAct — cited for the agent loop), rag (Lewis et al. — cited for the grounding/retrieval section), bm25, faiss (cited for hybrid retrieval), mqtt (cited for the ops-portal MQTT hop), xgboost, smote (cited for the churn model mention in Ch4). All cross-checked against references.bib, no dangling \cite keys.

**Figures**: 13 screenshot slots total across the report, each with an exact filename + LaTeX comment block telling the user exactly where/what to capture (established workflow: I mark the exact spot + instructions, user takes and drops in the actual screenshot — I never launch the live app myself). 2 already filled in by the user: `ch2-fictional-data.png` (dashboard Subscribers section, technology-mix-by-region chart — repurposed from an original "raw DB rows" ask to match what the user actually screenshotted, since the aggregate-chart version is honestly the stronger proof of "one consistent invented world at scale") and `ch3-bedrock-console.png` (AWS Bedrock model catalog entry for qwen3-next-80b-a3b — had to rewrite the capture instructions mid-session because AWS retired the old manual "Model access" page; models now auto-enable on first invoke). Remaining 11 still need to be captured by the user.

Figure placement: user is manually converting `[htbp]` → `[H]` (float package, already loaded in main.tex) across all figures themselves — I should expect `[H]` next time I read these files and not treat it as something to "fix" back to htbp.

## Architecture Reminders
- Primary agent: NetworkAnalyzer_agent_bedrock.py (Bedrock qwen3-32b)
- Bedrock model ID: qwen.qwen3-32b-v1:0 (BEDROCK_MODEL env var)
- DATA_PROFILE rebuilt at server startup — restart after schema changes
- coverage table: mandatory EXISTS check for upsell queries
- dou_monthly: monthly data usage (total_data_gb, month YYYY-MM) — in SC DB, NOT op DB
- subscriber_technology: current_technology, current_cell_id, volte_active — NO usage columns
- kpis_daily: dropped_call_rate (NOT drop_rate), NO technology column, NO region column
- Cross-DB: ATTACH DATABASE 'operator_new.db' AS op — always relative path

## Simulator Causality + Silent-Failure Bug Class (2026-08-17)

**Root cause found:** db_simulator's retry helpers (sc_write/op_write/*_many) caught
`sqlite3.OperationalError`, retried 3x, returned quietly. Malformed SQL failed forever
while printing success. Two long-running bugs hid behind it:
- `kpis_daily` had NO `UNIQUE(cell_id,date)`, so every `ON CONFLICT(cell_id,date) DO UPDATE`
  failed to prepare — **`simulate_kpi_fluctuation` never wrote a row, ever**. Fixed by
  `idx_kpi_cell_date` created in `_ensure_new_tables`.
- `customer_value` gained churn_risk_score/churn_label but 3 INSERTs still passed 5 values
  positionally → table froze at 2026-04; `billing` froze at 2026-05 because
  `backfill_monthly_op` anchors its window on `MAX(month) FROM customer_value`.
Helpers now retry only on locked/busy and PRINT real SQL errors (`_is_retryable`).

**Alarms are now causal, both directions** (were pure decoration — critical-alarm cells
measured *better* KPIs than clean ones, and 62% of alarmed cells served zero subscribers):
- `simulate_alarm_trigger` seeds its cell via an active subscriber (like `_incident_targets`),
  degrades that cell's KPIs via `_gen_kpi(alarm=(type,severity))` using `ALARM_KPI_IMPACT` +
  `ALARM_SEVERITY_BITE`, and for major/critical opens real incidents via `ALARM_TO_INCIDENT`.
- NEW reverse path `simulate_kpi_threshold_alarms` (TCA): `_detect_threshold_alarm` raises
  alarms FROM degraded counters. Thresholds are RELATIVE to `TECH_BASE` (a flat `drop>2`
  rule only ever flags 2G/3G). Check order is SIGNATURE-FIRST (rsrp→sinr→lat→avail→dl) —
  availability-first misattributed nearly everything as "Power Issue".
- Known limit: at *major* severity Backhaul↔Congestion and Power↔Hardware blur (overlapping
  signatures). Critical attribution is clean for all 6 types.
- `_emit_incident_event` extracted from `simulate_experience_incident`; both paths share it.

**`repair_data_gaps.py`** (new, idempotent, `--dry-run`): filled customer_value 2026-05..08
(201k rows, ARPU carried forward with drift — avg 104.81→108.97), unfroze billing (+151k),
retired 549 phantom + 309 uncoupled legacy alarms, synthesised 1,952 coupled historical
alarms over 30 days. Result: 155 active alarms, ALL on cells serving customers; alarmed
cells now ~2x worse drop rate than clean WITHIN each technology.

**2G resolved itself** — no 2G cell serves an active subscriber, so 2G now raises zero
alarms without deleting anything. The 2G-as-IoT idea (sim_type='M2M', ~3-5k fleet, needs a
DATA FACTS filter rule so consumer ARPU/churn denominators aren't contaminated) is still
OPEN but no longer urgent.

**Ch4 implication:** the report can now honestly describe a two-way coupling (faults that
announce themselves vs faults detected by threshold crossing). Before this it could not.
Ch4 §4.4 "Alarms That Actually Mean Something" written (kept to ~56 lines deliberately —
user pushed back on a first 110-line draft: "it should just be a good addition, that is
all, it shouldn't change much"). Report still 49 pages, General Conclusion still the only
chapter outstanding.

### HVC made dynamic + 3rd freeze instance (2026-08-17, later)
- **HVC was meaningless**: cutoffs (arpu>=80 platinum, >=45 gold, both HVC) predated
  update_arpu.py's rescaling (median ARPU now ~107) → 88.5% of the base was "high value"
  and 67.6% platinum (inverted pyramid). Also silently neutered `_incident_targets`' bias.
- Now PERCENTILE BANDS of the live distribution: `SEGMENT_BANDS` (platinum p90, gold p70,
  silver p30), `HVC_SEGMENTS={'platinum'}` = top 10%. `value_thresholds(month)` (cached) +
  `classify_value(arpu)` are the SINGLE source of truth — the 4 hardcoded sites + the repair
  script all call it. `simulate_arpu_update` refreshes bands and RE-TESTS status on every
  ARPU change (logs `HVC +n/-n`) — user explicitly wanted it dynamic, not static.
  The two `value_segment IN ('gold','platinum')` queries now use `is_hvc=1`.
- **Freeze pattern, 3rd instance — self-anchoring**: `backfill_new_tables` anchored its
  12-month window on `MAX(month) FROM nps_scores`, a table IT FILLS → froze permanently.
  Same shape in `backfill_monthly_op` vs customer_value. Both now anchor `datetime.now()`.
  Unfroze streaming_quality, web_quality, voip_quality, nps_scores, roaming_usage.
- **`check_data_freshness()`** NEW, runs at sim startup (1.2s): scans every date/month column
  in both DBs, prints anything >40 days behind today (skips birth/activation/registration/
  start/end/payment — historical by nature). 40d chosen so a normal monthly lag never trips
  (month cols parse to the 1st = up to 31d) but a missed cycle does.

### Two "agent-side" bugs that weren't the agent (2026-08-17)
- **Short data questions were answered from memory, no SQL.** `_is_conversational` treated
  ANY message <=6 words as a follow-up unless it contained show/list/count/average/...
  "ARPU trend last 6 months" (5 words) → replayed the PREVIOUS answer verbatim in 0.4s.
  FIX: `_build_domain_terms()` (398 terms from LIVE_SCHEMA tables/columns + arpu/nps/churn/
  hvc/5g...); a short message naming any of them is a QUERY, only one naming none
  ("why?", "hold on") stays conversational. **I first blamed the semantic cache — wrong,
  measured sim was 0.430 vs the 0.92 threshold. Verify before asserting.**
- **Questions typed mid-run were silently dropped** (server.py): the mid-run listener that
  catches "stop" discarded every other message, so type-ahead vanished and the UI paired the
  in-flight answer with the new bubble. Now queued in `pending_msgs`.

### Open (2026-08-17 EOD)
- Superseded-step leak: a rejected/retried query's results stay in the conclude context
  (produced the "44,312, not 98" incoherent answer). HIGHEST priority — visible in demos.
- `coverage` needs a multi-row warning in DATA_PROFILE: agent joined through it and got
  628 HVCs instead of 204 (it's many-to-many; served-by is `subscriber_technology.current_cell_id`).
- "urgent" is undefined in the prompt — agent silently alternates critical vs critical+major.
- Ungrounded NEGATIVES ("no churn risk detected" when 113 med + 1 high) — `_ground_text`
  only checks numbers, and only the final answer, not the reasoning text.
- **nps_scores is two flat plateaus with a step at 2026-05** — an artifact of MY segment
  recalibration (`_gen_nps` derives from value_segment). Any NPS trend question narrates it
  as "growing dissatisfaction". Regenerate all months under current segmentation; optionally
  skew within-band scores (currently uniform 0-6/7-8/9-10) and couple drift to incidents.
- qoe_daily missing 2026-08-14/15/16 (sim was off); `ensure_today_qoe` only ever fills today.
- 6 commits on main, **NOT pushed**.
- Model note: all the above failures occurred on the **80B** (qwen3-next-80b), not the 32B —
  so they're structural (schema facts + context plumbing), not capability. Good report line.

## Report endgame + machine wipe (2026-08-28)

**Defense: 5 September 2026.** PC being formatted — everything pushed to
branch `report-cleanup-and-insert-fix` (repo is PRIVATE, verified 404 anon).

### Compilatio / AI detection
- Scored **34% AI against a 25% institutional maximum**. Report is 19,233 words.
- To reach 25% needs ~1,700 words genuinely de-flagged (not just deleted —
  deleting shrinks numerator and denominator together).
- ToC/LoF are scored as **"unknown language"** = excluded from analysis, so
  removing them does nothing. Abstract removal was tried then reverted; the
  supervisor says abstracts are always flagged, which matches the register.
- **Method that works: Mootez rewrites, Claude critiques.** Do NOT write prose
  in his voice even when asked — that reinstates exactly what he's removing and
  he signs off on it at the defense. Give facts as notes instead. See
  [[feedback_report_tone]].
- Rewrite priority by (impact x how well his voice fits): Conclusion (836w) >
  Ch7 Limitations (1,629w) > Ch6 (2,921w) > Ch2 > Abstract last.

### Known-wrong claims still in the report
- **Chapter3.tex:143-145** — says every table is tagged with its owning database
  and that tag routes questions. FALSE. Routing is keyword intent classification
  over the question (`_INTENT_PRIORITY`, schema_graph_retriever.py:207).
- **Chapter 6 orphan explanation** — blames "an older identifier migration that
  never cascaded". FALSE. They were being created live by the insert bug below.
- **Chapter 5 intro** promises the three Ch1 gaps get closed one at a time;
  nothing closes it. No use-case section exists. Screenshots for one already
  exist; agreed to leave new use-case captures for the presentation.

### Fixed this session
- **db_simulator.py:2174** — new-subscriber INSERT supplied 10 values into an
  11-column `subscribers` table (`nation` computed, never passed). Every insert
  failed while subscriber_technology/devices/dou_monthly succeeded → orphans,
  371 → 378. Now an explicit column list. **Simulator needs restarting.**
- All 29 captions now <=22 words (worst were 61 and 67).
- Ch3 schema catalogues → longtables; added `experience_incidents` and
  `agent_actions` (both missing); "five groups" → six.
- Ch1 SmartCare passage 180 → 125 words; fixed a mangled `\ref` rendering as
  literal `ef{ch:eval}`.

### Churn state (verified live)
- Current month (2026-08) is healthy: 50,406 rows, avg 0.108, bands land exactly
  10/20/70 on p90/p70.
- **Historical months are all zeros** — rescoring only ever writes MAX(month),
  and `backfill_monthly_op` inserts customer_value rows without churn columns.
  755,786 of 855,055 rows are 0. A "churn trend over 6 months" question would
  get a wall of zeros reported as real. Not fixed — deadline call.

### Not in git, must be copied manually before the wipe
- `NetworkAnalyzer_new.db` 8.3 GB, `operator_new.db` 234 MB, `NetworkAnalyzer.db`
  470 MB. Report numbers come from these exact files; regenerating changes them.
  Try `VACUUM` first.
- `.env` (AWS keys) — never push.
- `C:\Users\<user>\.claude\` (108 MB) — transcripts + this memory.
