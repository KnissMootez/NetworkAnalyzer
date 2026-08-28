---
name: ops-portal-action-pipeline
description: "The second server — human-in-the-loop ops portal, MQTT action bus, agent_actions approve/edit/execute flow"
metadata: 
  node_type: memory
  type: project
  originSessionId: b7a3ae0b-3111-423b-884d-b509095e7ac6
---

NetworkAnalyzer has a SECOND server: the **Ops Portal** (`ops_server.py`, port 8001) — a non-LLM, pure FastAPI/REST human-in-the-loop console. The chat-portal LLM only *proposes* actions; a human approves/edits them here; approval is what writes to the DB. See [[NetworkAnalyzer Project Overview]].

## End-to-end action pipeline
1. **Chat portal** (`server.py`, :8000): agent's `propose_action` tool fires → server publishes to MQTT topic `networkanalyzer/actions/{type}` (type = campaign|sms|report|network_flag). Publish points: server.py ~L830 (proposal endpoint) and ~L253 (export/send path). Agent itself executes nothing.
2. **MQTT broker** localhost:1883 (paho-mqtt). Bus between the two servers.
3. **Ops portal** (`ops_server.py`): MQTT subscriber on `networkanalyzer/actions/#` → `_insert_action` writes row to **`agent_actions`** table (cols: id, type, title, summary, payload JSON, status, created_at, resolved_at, note) as `status='pending'`.
4. **Human review** (`ops_static/ops.js` + index.html + ops.css): tabs Campaigns/SMS/Subscribers/Network/Reports/Audit. Review modal → edit SMS text, add note, Approve/Deny. REST: GET `/api/actions[/stats]`, POST `/api/actions/{id}/patch` (edit payload), POST `/api/actions/{id}/resolve` (status approved|denied + note).
5. **On approve** → `_execute_action(conn, action)` writes real rows by type:
   - campaign → INSERT `campaigns` (campaign_name, campaign_type, target_count, launched_date, status, created_by='agent') + `campaign_targets` (campaign_id, msisdn, notified)
   - sms → INSERT `sms_log` (msisdn, campaign_id, sent_date, message_text, status='sent')
   - network_flag / report → logged only, no DB write
6. **Simulator** (`db_simulator.py`): `simulate_campaign_progress` (~L1497) drives the full SMS feedback loop for active campaigns (see below).

## Ops portal role (user's vision, 2026-06-17)
The ops portal is the HUMAN ACTION LAYER / human-interaction platform. It both (a) RECEIVES proposals from the chat/agent (via MQTT) for approve/edit, AND (b) lets the operator ORIGINATE sends directly. (User first said receive-only, then corrected: a human-interaction platform should let humans initiate too.) So "approve" must actually SEND, and there's a Compose tool to start sends from scratch.

## Origination tool (built 2026-06-17)
"＋ New Action" button in the ops portal sidebar opens a Compose modal (ops_static: index.html + ops.js + ops.css). Two modes: Send SMS / Launch Campaign. Audience builder = region + technology + value-segment filters + max-recipients limit, with a live count preview. Backend (ops_server.py):
- `_resolve_audience(region, technology, segment, limit, count_only)` — resolves matching active subscribers via SC_DB + ATTACH op (joins subscriber_technology + op.customer_value latest month). Returns count or msisdn list.
- `GET /api/audience/preview` → {count}.
- `POST /api/originate/sms` {message, region, technology, segment, limit} → inserts sms_log rows (status='sent', campaign_id NULL) → simulate_sms_lifecycle delivers + collects replies. Also logs an agent_actions row (type='sms', status='approved', note='originated by operator') for audit/SMS-tab visibility.
- `POST /api/originate/campaign` {campaign_name, campaign_type, region, technology, segment, limit} → creates campaigns(status='active', created_by='operator') + campaign_targets → simulate_campaign_progress + lifecycle drive it. Also logs an agent_actions row.
- Verified end-to-end (preview/sms/campaign/lifecycle, 0 residual). NOTE: an MQTT broker IS running on localhost:1883 (confirmed 2026-06-17).
- Deliberately NOT built: bulk-segment was folded into the audience filter; no separate per-msisdn picker.

## SMS feedback loop (built 2026-06-17, lifecycle unified same day)
Two simulator functions (db_simulator.py):
- `simulate_campaign_progress` (~L1497): for active campaigns, notifies fresh targets — flips `campaign_targets.notified=1` and INSERTs a `sms_log` row with status `'sent'` (queued); completes a campaign past 80% conversion. (No longer does delivery/replies itself.)
- `simulate_sms_lifecycle` (NEW): advances EVERY queued `sms_log` row regardless of source — `sent`→`delivered`(~92%)/`failed`(~8%), then delivered+`response='none'`→ rolls an inbound reply (opt_in 25% / query 15% / opt_out 10% / silent 50%), writing `response` + `inbound_text`; on opt_in for a campaign row, sets `campaign_targets.converted=1`. Runs every 2 ticks. Operates by `sms_id` (PK).
- WHY unified: a human approving a standalone SMS in the ops portal (`_execute_action` type 'sms' → INSERT sms_log status='sent', campaign_id NULL) used to die as a dead 'sent' row. Now the SAME lifecycle delivers it + collects replies, so approve = real send. NO ops_server.py change needed — it already inserts status='sent'; the simulator (the "world clock") processes it.
- Copy/reply text from `_CAMPAIGN_MSG` / `_INBOUND_TEXT` dicts. Dashboard replies feed LEFT JOINs campaigns → standalone SMS show as 'Direct SMS'.
- `sms_log` schema: sms_id, msisdn, campaign_id, sent_date, message_text, status (sent/delivered/failed), response (none/opt_in/opt_out/query), **inbound_text** (added 2026-06-17 via guarded ALTER in `_ensure_new_tables`).
- Closed loop: agent can analyze its own campaigns via RAG SQL examples in `rag_sql_examples.txt` (campaign performance funnel, inbound replies, query=follow-up targets). All on operator DB. Added per the "no whack-a-mole system prompt" rule — see [[agent-behavior-feedback]].

## Table creation
agent_actions / campaigns / campaign_targets / sms_log are NOT created by any current project .py — they live in operator_new.db, built by a one-off/archived script. New columns are added via guarded `ALTER TABLE ... ` in `_ensure_new_tables` (db_simulator.py).

## Other ops-side bits
- Ops portal also has read endpoints for human browsing: `/api/subscribers[/{msisdn}]`, `/api/network/kpis`, `/api/network/flags`.
- Streamlit side mirrors campaigns: `pages/4_Campaigns.py`, `static/js/campaigns.js`.
