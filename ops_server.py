"""
Ops Portal Server — FastAPI backend
- Subscribes to MQTT topics from the agent
- Stores agent actions in operator_new.db
- Exposes REST endpoints for the Ops Portal frontend
"""

import json
import os
import sqlite3
import threading
from datetime import datetime

import paho.mqtt.client as mqtt
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OP_DB    = os.path.join(BASE_DIR, "operator_new.db")
SC_DB    = os.path.join(BASE_DIR, "NetworkAnalyzer_new.db")

MQTT_HOST   = "localhost"
MQTT_PORT   = 1883
MQTT_TOPIC  = "networkanalyzer/actions/#"

# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_conn():
    conn = sqlite3.connect(OP_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _insert_action(type_: str, title: str, summary: str, payload: dict) -> int:
    conn = _get_conn()
    cur = conn.execute(
        """INSERT INTO agent_actions (type, title, summary, payload, status, created_at)
           VALUES (?, ?, ?, ?, 'pending', ?)""",
        (type_, title, summary, json.dumps(payload), datetime.utcnow().isoformat()),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id

# ── MQTT subscriber ───────────────────────────────────────────────────────────

def _on_connect(client, userdata, flags, rc, properties=None):
    print(f"[MQTT] Connected (rc={rc}), subscribing to {MQTT_TOPIC}")
    client.subscribe(MQTT_TOPIC)


def _on_message(client, userdata, msg):
    try:
        data     = json.loads(msg.payload.decode())
        topic    = msg.topic                          # e.g. networkanalyzer/actions/campaign
        type_    = topic.split("/")[-1]               # campaign | sms | report | network_flag
        title    = data.get("title",   f"Agent action: {type_}")
        summary  = data.get("summary", "")
        payload  = data.get("payload", {})
        row_id   = _insert_action(type_, title, summary, payload)
        print(f"[MQTT] Received '{type_}' action → saved as id={row_id}")
    except Exception as e:
        print(f"[MQTT] Failed to process message: {e}")


def _start_mqtt():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = _on_connect
    client.on_message = _on_message
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_forever()


threading.Thread(target=_start_mqtt, daemon=True, name="MQTTSubscriber").start()

# ── FastAPI ───────────────────────────────────────────────────────────────────

app = FastAPI(title="Ops Portal")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Static files ──────────────────────────────────────────────────────────────

OPS_STATIC = os.path.join(BASE_DIR, "ops_static")
os.makedirs(OPS_STATIC, exist_ok=True)

@app.get("/")
def root():
    return FileResponse(os.path.join(OPS_STATIC, "index.html"))

app.mount("/static", StaticFiles(directory=OPS_STATIC), name="ops_static")

# ── Actions endpoints ─────────────────────────────────────────────────────────

@app.get("/api/actions/stats")
def get_stats():
    conn = _get_conn()
    rows = conn.execute("""
        SELECT type, status, COUNT(*) as count
        FROM agent_actions
        GROUP BY type, status
    """).fetchall()
    conn.close()
    return JSONResponse([dict(r) for r in rows])


@app.get("/api/actions")
def get_actions(type: str = None, status: str = None):
    conn  = _get_conn()
    query = "SELECT * FROM agent_actions WHERE 1=1"
    args  = []
    if type:
        query += " AND type=?"
        args.append(type)
    if status:
        query += " AND status=?"
        args.append(status)
    query += " ORDER BY created_at DESC"
    rows  = conn.execute(query, args).fetchall()
    conn.close()
    return JSONResponse([dict(r) for r in rows])


class PatchRequest(BaseModel):
    payload: str

@app.post("/api/actions/{action_id}/patch")
def patch_action(action_id: int, body: PatchRequest):
    conn = _get_conn()
    conn.execute("UPDATE agent_actions SET payload=? WHERE id=?", (body.payload, action_id))
    conn.commit()
    conn.close()
    return {"ok": True}

class ResolveRequest(BaseModel):
    status: str   # 'approved' | 'denied'
    note:   str = ""


@app.post("/api/actions/{action_id}/resolve")
def resolve_action(action_id: int, body: ResolveRequest):
    if body.status not in ("approved", "denied"):
        raise HTTPException(400, "status must be 'approved' or 'denied'")
    conn = _get_conn()
    row  = conn.execute("SELECT * FROM agent_actions WHERE id=?", (action_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Action not found")
    if row["status"] != "pending":
        conn.close()
        raise HTTPException(400, f"Action is already {row['status']}")

    conn.execute(
        "UPDATE agent_actions SET status=?, resolved_at=?, note=? WHERE id=?",
        (body.status, datetime.utcnow().isoformat(), body.note, action_id),
    )

    # On approve, execute the action against the DB
    if body.status == "approved":
        _execute_action(conn, dict(row))

    conn.commit()
    conn.close()
    return {"ok": True, "status": body.status}


def _execute_action(conn: sqlite3.Connection, action: dict):
    """Write approved actions to the DB."""
    type_   = action["type"]
    payload = json.loads(action["payload"] or "{}")

    if type_ == "campaign":
        campaign_name = action["title"]
        msisdns       = payload.get("msisdns", [])
        now           = datetime.utcnow().isoformat()
        cur = conn.execute(
            "INSERT INTO campaigns (campaign_name, campaign_type, target_count, launched_date, status, created_by) VALUES (?,?,?,?,?,?)",
            (campaign_name, payload.get("campaign_type", "retention"), len(msisdns), now[:10], "active", "agent"),
        )
        campaign_id = cur.lastrowid
        for msisdn in msisdns:
            conn.execute(
                "INSERT OR IGNORE INTO campaign_targets (campaign_id, msisdn, notified) VALUES (?,?,0)",
                (campaign_id, msisdn),
            )
        print(f"[Execute] Campaign '{campaign_name}' created with {len(msisdns)} targets.")

    elif type_ == "sms":
        msisdns = payload.get("msisdns", [])
        message = payload.get("message", "")
        now     = datetime.utcnow().isoformat()
        for msisdn in msisdns:
            conn.execute(
                "INSERT INTO sms_log (msisdn, sent_date, message_text, status) VALUES (?,?,?,?)",
                (msisdn, now, message, "sent"),
            )
        print(f"[Execute] SMS sent to {len(msisdns)} subscribers.")

    elif type_ == "network_flag":
        # Just logged — no DB write needed beyond the agent_actions record
        print(f"[Execute] Network flag acknowledged: {action['title']}")

    elif type_ == "report":
        # Report is already saved as a file — nothing to write
        print(f"[Execute] Report approved: {action['title']}")


# ── Subscriber data endpoints (for human browsing) ────────────────────────────

@app.get("/api/subscribers")
def get_subscribers(region: str = None, technology: str = None, segment: str = None, limit: int = 100):
    sc   = sqlite3.connect(SC_DB)
    sc.row_factory = sqlite3.Row
    sc.execute(f"ATTACH DATABASE '{OP_DB}' AS op")
    query = """
        SELECT s.msisdn, s.region, s.city, s.nation,
               st.current_technology AS technology, st.volte_active,
               cv.arpu, cv.value_segment, cv.churn_risk_score, cv.churn_label,
               s.is_active
        FROM subscribers s
        LEFT JOIN subscriber_technology st ON s.msisdn = st.msisdn
        LEFT JOIN op.customer_value cv ON s.msisdn = cv.msisdn
            AND cv.month = (SELECT MAX(month) FROM op.customer_value)
        WHERE 1=1
    """
    args = []
    if region:
        query += " AND s.region=?"; args.append(region)
    if technology:
        query += " AND st.current_technology=?"; args.append(technology)
    if segment:
        query += " AND cv.value_segment=?"; args.append(segment)
    query += f" LIMIT {min(limit, 500)}"
    rows = sc.execute(query, args).fetchall()
    sc.close()
    return JSONResponse([dict(r) for r in rows])


@app.get("/api/subscribers/{msisdn}")
def get_subscriber(msisdn: str):
    sc = sqlite3.connect(SC_DB)
    sc.row_factory = sqlite3.Row
    sc.execute(f"ATTACH DATABASE '{OP_DB}' AS op")
    row = sc.execute("""
        SELECT s.*, st.current_technology, st.volte_active,
               cv.arpu, cv.value_segment, cv.churn_risk_score, cv.churn_label,
               d.model as device_model, d.is_5g_capable,
               sub.plan_id, p.plan_name, p.monthly_price
        FROM subscribers s
        LEFT JOIN subscriber_technology st ON s.msisdn=st.msisdn
        LEFT JOIN op.customer_value cv ON s.msisdn=cv.msisdn
            AND cv.month=(SELECT MAX(month) FROM op.customer_value)
        LEFT JOIN op.devices d ON s.msisdn=d.msisdn
        LEFT JOIN op.subscriptions sub ON s.msisdn=sub.msisdn AND sub.is_current=1
        LEFT JOIN op.plans p ON sub.plan_id=p.plan_id
        WHERE s.msisdn=?
    """, (msisdn,)).fetchone()
    sc.close()
    if not row:
        raise HTTPException(404, "Subscriber not found")
    return JSONResponse(dict(row))


# ── Network KPI endpoints ─────────────────────────────────────────────────────

@app.get("/api/network/kpis")
def get_kpis(region: str = None, limit: int = 50):
    sc = sqlite3.connect(SC_DB)
    sc.row_factory = sqlite3.Row
    query = """
        SELECT k.cell_id, k.date, k.dl_throughput_mbps, k.dropped_call_rate,
               k.latency_ms, k.rsrp_avg, k.availability_pct, s.region
        FROM kpis_daily k
        LEFT JOIN sites s ON k.cell_id = s.site_id
        WHERE k.date = (SELECT MAX(date) FROM kpis_daily)
    """
    args = []
    if region:
        query += " AND s.region=?"; args.append(region)
    query += f" ORDER BY k.dropped_call_rate DESC LIMIT {min(limit, 200)}"
    rows = sc.execute(query, args).fetchall()
    sc.close()
    return JSONResponse([dict(r) for r in rows])


@app.get("/api/network/flags")
def get_network_flags():
    return get_actions(type="network_flag")
