---
name: ops-portal-design
description: Ops Portal — standalone human operations platform with MQTT integration for agent actions
metadata: 
  node_type: memory
  type: project
  originSessionId: 3f332db3-edb9-4a21-aab7-e053e09898d5
---

## Ops Portal — Design Decisions

**What it is:** A standalone HTML/CSS/JS + FastAPI operations platform for human operators. Primary tool for day-to-day operations. The agent is a minor contributor, not the main character.

**Why separate:** Agent and portal don't necessarily live in the same environment (supervisor's requirement). Decoupled via MQTT.

**Protocol:** MQTT (local Mosquitto broker for dev). Agent publishes, portal subscribes. Fire-and-forget — agent doesn't know or care if portal is running.

**Why MQTT over REST:** REST is request/response and couples the two systems. MQTT is pub/sub — true decoupling. Used in telecom OSS/BSS event streaming. Huawei platforms use it internally.

**Frontend:** HTML/CSS/JS (same dark theme family as chat UI). No login required.

**Backend:** New FastAPI app (ops_server.py) separate from server.py.

**Storage:** New `agent_actions` table in operator_new.db.

## DB Schema

```sql
agent_actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT,    -- 'campaign' | 'sms' | 'report' | 'network_flag'
    title       TEXT,
    summary     TEXT,
    payload     TEXT,    -- JSON blob
    status      TEXT DEFAULT 'pending',  -- 'pending' | 'approved' | 'denied'
    created_at  TEXT,
    resolved_at TEXT,
    note        TEXT
)
```

## MQTT Topics
- `networkanalyzer/actions/campaign`
- `networkanalyzer/actions/sms`
- `networkanalyzer/actions/report`
- `networkanalyzer/actions/network_flag`

## Portal Tabs (build order)
1. **Campaigns** — first, most tied to agent output
2. **SMS Queue** — draft messages + recipient lists
3. **Reports** — agent CSVs and summaries
4. **Network Flags** — cells/regions flagged for field team
5. **Audit Log** — everything ever pushed, status, timestamp

## Chat UI Dropdown Actions (agent side — minor detail, build after portal)
- Export CSV (already exists)
- Send to Campaign Manager → publishes to `networkanalyzer/actions/campaign`
- Draft SMS to segment → publishes to `networkanalyzer/actions/sms`
- Save as Report → publishes to `networkanalyzer/actions/report`
- Flag for Network Team → publishes to `networkanalyzer/actions/network_flag`

## Build Order
1. Install Mosquitto + paho-mqtt
2. Create agent_actions table in operator_new.db
3. Build ops_server.py (FastAPI, MQTT subscriber, REST endpoints)
4. Build ops portal frontend (HTML/CSS/JS)
5. Wire agent dropdown → MQTT publisher
