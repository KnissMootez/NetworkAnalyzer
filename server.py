"""
NetworkAnalyzer Copilot — FastAPI server
Serves the frontend SPA and provides:
  - WebSocket /ws/chat?model=bedrock|bedrock-80b|langgraph  (streaming LLM, AWS Bedrock)
  - REST /api/*  (metrics, network, subscribers, commercial, campaigns)
"""

import asyncio
import json
import queue as q_module
import threading
import os, sys
import re

# Load .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sqlite3
import json

BASE    = os.path.dirname(os.path.abspath(__file__))
SC_DB   = os.path.join(BASE, "NetworkAnalyzer_new.db")
OP_DB   = os.path.join(BASE, "operator_new.db")

import NetworkAnalyzer_agent_bedrock  as _agent_bedrock
import NetworkAnalyzer_agent_langgraph as _agent_langgraph
from NetworkAnalyzer_agent_bedrock import query_sc, query_op, get_proactive_alerts, SC_DB

app = FastAPI(title="NetworkAnalyzer Copilot")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Agent routing ─────────────────────────────────────────────────────

def _get_agent(model: str):
    if model == "langgraph":
        os.environ["BEDROCK_MODEL"] = "qwen.qwen3-32b-v1:0"
        return _agent_langgraph
    if model == "bedrock":
        os.environ["BEDROCK_MODEL"] = "qwen.qwen3-32b-v1:0"
        return _agent_bedrock
    if model == "bedrock-80b":
        os.environ["BEDROCK_MODEL"] = "qwen.qwen3-next-80b-a3b"
        return _agent_bedrock
    # default
    os.environ["BEDROCK_MODEL"] = "qwen.qwen3-32b-v1:0"
    return _agent_bedrock

def _set_queue(agent_mod, q, stop_event=None):
    """Wire a streaming queue and stop event into the right module(s)."""
    for mod in (agent_mod, _agent_bedrock):
        mod._streaming_queue = q
    if stop_event is not None:
        for mod in (agent_mod, _agent_bedrock):
            mod._stop_event = stop_event

def _clear_queue(agent_mod):
    for mod in (agent_mod, _agent_bedrock):
        mod._streaming_queue = None
        mod._stop_event = None

# Serialize agent runs across WebSocket connections. The agent wires its
# streaming queue + memory into module globals (_set_queue), so two connections
# running at once would clobber each other. This lock makes runs mutually
# exclusive. NOTE: it does not give per-user memory isolation — _memory is still
# shared; true multi-user needs session-keyed agent state.
_agent_lock = asyncio.Lock()

# ── Static files / SPA ───────────────────────────────────────────────

@app.get("/")
def root():
    return FileResponse(os.path.join(BASE, "static", "index.html"))

app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")

# ── MSISDN Export ─────────────────────────────────────────────────────

class ExportRequest(BaseModel):
    sql: str
    filename: str = "export.csv"

_EXPORT_COL_FIXES = [
    # devices table
    (r'\bd\.device_model\b',            'd.model'),
    (r'\bd\.device_brand\b',            'd.brand'),
    (r'\bd\.device_os\b',               'd.os'),
    (r'\bd\.phone_model\b',             'd.model'),
    (r'\bd\.device_type\b',             'd.model'),
    (r'\bd\.manufacturer\b',            'd.brand'),
    # subscribers table (NetworkAnalyzer_new.db)
    (r'\bs\.subscriber_name\b',         's.full_name'),
    (r'\bs\.name\b',                    's.full_name'),
    (r'\bs\.phone_number\b',            's.msisdn'),
    (r'\bs\.subscriber_type\b',         's.is_active'),
    (r'\bs\.customer_type\b',           's.is_active'),
    (r'\bs\.subscription_type\b',       's.is_active'),
    (r'\bs\.subscription_date\b',       's.activation_date'),
    (r'\bs\.join_date\b',               's.activation_date'),
    (r'\bs\.signup_date\b',             's.activation_date'),
    (r'\bs\.start_date\b',              's.activation_date'),
    (r'\bs\.contract_start\b',          's.activation_date'),
    (r'\bs\.sim_status\b',              's.is_active'),
    (r'\bs\.status\b',                  's.is_active'),
    (r'\bs\.arpu\b',                    "'N/A'"),
    (r'\bs\.plan_name\b',               "'N/A'"),
    (r'\bs\.plan_id\b',                 "'N/A'"),
    (r'\bs\.plan\b',                    "'N/A'"),
    (r'\bs\.contract_type\b',           "'N/A'"),
    (r'\bs\.revenue\b',                 "'N/A'"),
    (r'\bs\.monthly_spend\b',           "'N/A'"),
    # customers table (operator_new.db) — alias c or cu
    (r'\bc\.subscription_date\b',       'c.registration_date'),
    (r'\bc\.join_date\b',               'c.registration_date'),
    (r'\bc\.signup_date\b',             'c.registration_date'),
    (r'\bcu\.subscription_date\b',      'cu.registration_date'),
    (r'\bc\.subscriber_type\b',         'c.segment'),
    (r'\bc\.customer_segment\b',        'c.segment'),
    # subscriptions table — alias sub or subs
    (r'\bsub\.subscription_date\b',     'sub.start_date'),
    (r'\bsubs\.subscription_date\b',    'subs.start_date'),
]

def _fix_export_sql(sql: str) -> str:
    import re as _re
    for pat, rep in _EXPORT_COL_FIXES:
        sql = _re.sub(pat, rep, sql, flags=_re.IGNORECASE)
    return sql

_SCHEMA_HINT = """
NetworkAnalyzer_new.db tables and EXACT column names:
  subscribers:        msisdn, imsi, sim_type, full_name, is_active, activation_date, age, gender, region, city, latitude, longitude, area_code
  devices:            msisdn, imei, brand, model, max_technology, volte_capable, vowifi_capable, is_5g_capable, os, os_version
  subscriber_technology: msisdn, current_technology, current_cell_id, volte_active, data_roaming_active, last_seen_date
  sites:              site_id, site_name, region, city, latitude, longitude, site_type, zone, is_active
  cells:              cell_id, site_id, technology, frequency_band, is_active, max_users
  coverage:           msisdn, cell_id, technology_available, signal_strength_dbm, is_home_coverage
  kpis_daily:         cell_id, date, rsrp_avg, sinr_avg, throughput_dl_mbps, throughput_ul_mbps, prb_utilization, active_users, drop_call_rate, packet_loss_rate, latency_ms, handover_success_rate, experience_label
  quality_of_experience: msisdn, date, experience_score, experience_label, avg_throughput_mbps, avg_latency_ms, avg_packet_loss, video_score, voice_score, gaming_score
operator_new.db (alias: op) tables:
  customers:          msisdn, full_name, national_id, date_of_birth, gender, email, segment, registration_date, is_active
  plans:              plan_id, plan_name, plan_type, data_cap_gb, speed_mbps, monthly_price, supports_5g, supports_volte, is_fwa_plan, is_mbb_plan, description
  subscriptions:      msisdn, plan_id, start_date, end_date, is_current, contract_type, auto_renewal
  customer_value:     msisdn, month, arpu, data_usage_gb, voice_minutes, sms_count, roaming_revenue, total_revenue, churn_risk_score, clv_score
  billing:            msisdn, bill_date, amount_due, amount_paid, due_date, is_paid, payment_method
  complaints:         complaint_id, msisdn, complaint_date, category, description, status, resolution_date, satisfaction_score
  campaigns:          campaign_id, name, type, start_date, end_date, target_segment, budget, is_active
"""

def _llm_repair_sql(bad_sql: str, error_msg: str) -> str:
    """Ask the LLM to fix a broken SQL query using the real schema."""
    import re as _re
    # Extract the bad column name from the error if possible
    col_match = _re.search(r'no such column:\s*(\S+)', error_msg, _re.I)
    bad_col = col_match.group(1) if col_match else "unknown column"
    system = (
        "You are a SQL repair assistant. Fix the broken SQL query by replacing wrong column names "
        "with the correct ones from the schema. Return ONLY the corrected SQL — no explanation, no markdown fences."
    )
    prompt = (
        f"The following SQL failed with error: {error_msg}\n\n"
        f"Bad column referenced: {bad_col}\n\n"
        f"EXACT SCHEMA (use ONLY these column names):\n{_SCHEMA_HINT}\n\n"
        f"Broken SQL:\n{bad_sql}\n\n"
        f"Return the corrected SQL only."
    )
    try:
        fixed = _agent_bedrock._llm(system, prompt, max_tokens=800, allow_thinking=False)
        # Strip markdown fences if model wrapped it
        fixed = _re.sub(r'^```[a-z]*\n?', '', fixed.strip(), flags=_re.I)
        fixed = _re.sub(r'\n?```$', '', fixed.strip())
        return fixed.strip()
    except Exception:
        return bad_sql  # if LLM call itself fails, return original so caller gets real error

def _run_sql(sql: str, conn) -> list[dict]:
    """Execute SQL, on column error attempt LLM repair then retry once."""
    try:
        rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        if "no such column" in str(e).lower():
            fixed_sql = _llm_repair_sql(sql, str(e))
            if fixed_sql and fixed_sql != sql:
                try:
                    rows = conn.execute(fixed_sql).fetchall()
                    return [dict(r) for r in rows]
                except Exception as e2:
                    raise e2
        raise

def _query_export(sql: str) -> list[dict]:
    """Run export SQL with both DBs attached so cross-DB joins work."""
    import sqlite3
    sql = _fix_export_sql(sql)
    SC_DB = os.path.join(BASE, "NetworkAnalyzer_new.db")
    OP_DB = os.path.join(BASE, "operator_new.db")
    conn = sqlite3.connect(SC_DB, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"ATTACH DATABASE '{OP_DB}' AS op")
    try:
        return _run_sql(sql, conn)
    finally:
        conn.close()

EXPORTS_DIR = os.path.join(BASE, "exports")
os.makedirs(EXPORTS_DIR, exist_ok=True)

class MsisdnListRequest(BaseModel):
    sql: str

@app.post("/export/msisdns")
def export_msisdns(req: MsisdnListRequest):
    """Run a SELECT and return the msisdn column as a JSON list."""
    import re
    sql = req.sql.strip()
    if not re.match(r'^\s*SELECT\b', sql, re.IGNORECASE):
        raise HTTPException(400, "Only SELECT queries allowed.")
    try:
        conn = sqlite3.connect(SC_DB, timeout=15)
        conn.execute(f"ATTACH DATABASE '{OP_DB}' AS op")
        cur  = conn.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        raise HTTPException(500, f"Query failed: {e}")

    # Try to find msisdn column, fall back to first column
    msisdn_idx = next((i for i, c in enumerate(cols) if 'msisdn' in c.lower()), 0)
    msisdns = [str(r[msisdn_idx]) for r in rows if r[msisdn_idx] is not None]
    return {"msisdns": msisdns, "count": len(msisdns), "columns": cols}

class SaveExportRequest(BaseModel):
    sql:      str
    title:    str = "Agent Export"
    summary:  str = ""
    send_type: str = "report"   # report | campaign

@app.post("/export/save")
def export_save(req: SaveExportRequest):
    """Generate CSV, save to disk, push to Ops Portal via MQTT."""
    import csv, io, re, paho.mqtt.publish as mqtt_publish
    from datetime import datetime

    sql = req.sql.strip()
    if not re.match(r'^\s*SELECT\b', sql, re.IGNORECASE):
        raise HTTPException(400, "Only SELECT queries allowed.")

    # Run the query and write CSV
    try:
        sc = sqlite3.connect(SC_DB)
        sc.execute(f"ATTACH DATABASE '{OP_DB}' AS op")
        cur = sc.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        sc.close()
    except Exception as e:
        raise HTTPException(500, f"Query failed: {e}")

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename  = f"export_{timestamp}.csv"
    filepath  = os.path.join(EXPORTS_DIR, filename)

    buf = io.StringIO()
    w   = csv.writer(buf)
    w.writerow(cols)
    w.writerows(rows)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(buf.getvalue())

    # Push to Ops Portal via MQTT
    payload = {
        "title":   req.title,
        "summary": req.summary or f"{len(rows)} rows exported.",
        "payload": {
            "filename": filename,
            "row_count": len(rows),
            "columns":   cols,
            "sql":       sql[:200],
        }
    }
    try:
        mqtt_publish.single(
            f"networkanalyzer/actions/{req.send_type}",
            json.dumps(payload),
            hostname="localhost", port=1883
        )
    except Exception:
        pass  # MQTT failure shouldn't block the response

    return {"ok": True, "filename": filename, "rows": len(rows)}

@app.get("/exports/{filename}")
def download_export(filename: str):
    path = os.path.join(EXPORTS_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(404, "File not found")
    return FileResponse(path, media_type="text/csv", filename=filename)

@app.post("/export/msisdn")
def export_msisdn(req: ExportRequest):
    import csv, io, re
    sql = req.sql.strip()
    if not re.match(r'^\s*SELECT\b', sql, re.IGNORECASE):
        raise HTTPException(status_code=400, detail="Only SELECT queries are allowed for export.")
    for forbidden in ("INSERT","UPDATE","DELETE","DROP","ALTER","CREATE","ATTACH"):
        if re.search(rf'\b{forbidden}\b', sql, re.IGNORECASE):
            raise HTTPException(status_code=400, detail=f"Forbidden keyword: {forbidden}")
    # Strip trailing incomplete tokens (truncated LLM output)
    sql = re.sub(r'[,\s]+$', '', sql.rstrip())
    if not sql.endswith(')') and not sql.upper().rstrip().endswith(('LIMIT', 'DESC', 'ASC')) and not re.search(r'\d\s*$', sql):
        pass  # allow through, SQLite will give a clean error
    try:
        rows = _query_export(sql)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Query error: {e}")
    if not rows:
        raise HTTPException(status_code=404, detail="No data found for this query.")
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)
    fname = req.filename if req.filename.endswith(".csv") else req.filename + ".csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'}
    )

# ── WebSocket ─────────────────────────────────────────────────────────

@app.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket, model: str = "qwen3"):
    await websocket.accept()
    agent_mod = _get_agent(model)

    stop_ev = None

    async def _drain_queue(sq: q_module.Queue, stop_ev_ref: threading.Event):
        """Drain agent streaming queue → WebSocket. Returns when sentinel None received."""
        while True:
            try:
                event = sq.get_nowait()
            except q_module.Empty:
                await asyncio.sleep(0.008)
                continue
            if event is None:
                return
            try:
                await websocket.send_text(json.dumps(event))
            except Exception:
                return

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            if msg.get("type") == "pong":
                continue
            if msg.get("type") == "stop":
                if stop_ev:
                    stop_ev.set()
                continue

            user_text = msg.get("text", "")
            if msg.get("type") == "confirm":
                user_text = "confirm"
            elif msg.get("type") == "cancel":
                user_text = "cancel"
            elif msg.get("type") == "continue":
                user_text = "__continue__"

            if not user_text:
                continue

            sq = q_module.Queue()
            result_holder = {}
            stop_ev = threading.Event()

            async with _agent_lock:
                _set_queue(agent_mod, sq, stop_ev)

                def _run():
                    try:
                        result_holder["result"] = agent_mod.run_agent(user_text)
                    except Exception as e:
                        result_holder["result"] = {"type": "error", "text": str(e)}
                    finally:
                        _clear_queue(agent_mod)
                        sq.put(None)  # sentinel

                t = threading.Thread(target=_run, daemon=True)
                t.start()

                # Run drain and receive concurrently so stop/pong messages are
                # processed immediately even while the agent is streaming.
                drain_task = asyncio.ensure_future(_drain_queue(sq, stop_ev))
                recv_task  = asyncio.ensure_future(websocket.receive_text())

                while not drain_task.done():
                    done, _ = await asyncio.wait(
                        [drain_task, recv_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if recv_task in done:
                        try:
                            in_msg = json.loads(recv_task.result())
                            if in_msg.get("type") == "stop" and stop_ev:
                                stop_ev.set()
                            # ignore pong / unknown during streaming
                        except Exception:
                            pass
                        if not drain_task.done():
                            recv_task = asyncio.ensure_future(websocket.receive_text())

                # Cancel the pending recv_task if drain finished first
                if not recv_task.done():
                    recv_task.cancel()
                    try:
                        await recv_task
                    except (asyncio.CancelledError, Exception):
                        pass

                t.join(timeout=5)
                result = result_holder.get("result", {"type": "error", "text": "Agent timed out"})

            try:
                await websocket.send_text(json.dumps({
                    "type": "done",
                    "result": {
                        "response_type":   result.get("type", "analysis"),
                        "text":            result.get("text", ""),
                        "recommendations": result.get("recommendations", []),
                        "extract_sql":      result.get("extract_sql"),
                        "chart":            result.get("chart"),
                        "strategy_diagram": result.get("strategy_diagram"),
                        "mindmap":          result.get("mindmap"),
                        "options":          result.get("options", []),
                        "input":            result.get("input"),
                        "steps":            result.get("steps", []),
                        "think_log":        result.get("think_log", []),
                        "truncated":        result.get("truncated", False),
                        "map_flash":        _compute_map_flash(user_text, result),
                    }
                }))
            except Exception:
                pass  # client disconnected before result arrived

    except WebSocketDisconnect:
        pass
    finally:
        _clear_queue(agent_mod)

# ── Metrics / snapshot ────────────────────────────────────────────────

@app.get("/api/metrics")
def api_metrics():
    subs   = query_sc("SELECT COUNT(*) as n FROM subscribers WHERE is_active=1")
    alarms = query_sc("SELECT COUNT(*) as n FROM network_alarms WHERE severity='critical' AND is_active=1")
    hvc    = query_op("SELECT COUNT(*) as n FROM customer_value WHERE segment IN ('gold','platinum')")
    fwa    = query_sc("""
        SELECT COUNT(DISTINCT s.msisdn) as n
        FROM subscribers s
        JOIN mobility_profile mp ON s.msisdn=mp.msisdn
        JOIN dou_monthly d ON s.msisdn=d.msisdn
        WHERE mp.mobility_class='stationary'
          AND d.month=(SELECT MAX(month) FROM dou_monthly)
          AND d.total_data_gb >= 30
    """)
    return {
        "subscribers": subs[0]["n"]   if subs   and "n" in subs[0]   else 0,
        "alarms":      alarms[0]["n"] if alarms and "n" in alarms[0] else 0,
        "hvc":         hvc[0]["n"]    if hvc    and "n" in hvc[0]    else 0,
        "fwa":         fwa[0]["n"]    if fwa    and "n" in fwa[0]    else 0,
    }

@app.get("/api/alerts")
def api_alerts():
    return get_proactive_alerts()

# ── Coverage map ──────────────────────────────────────────────────────

@app.get("/api/coverage")
def api_coverage():
    rows = query_sc("""
        SELECT s.site_name, s.region, s.zone,
               s.latitude, s.longitude,
               COALESCE(s.display_tech, '3G') as technology
        FROM sites s
        WHERE s.latitude IS NOT NULL AND s.longitude IS NOT NULL
          AND s.is_active = 1
        LIMIT 5000
    """)
    return rows


# query_sc() takes SQL only (no params), so bind parameters on our own connection.
def _query_sc_params(sql: str, params: tuple = ()):
    conn = sqlite3.connect(SC_DB)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


# Cell sites that currently have LIVE experience incidents — used to flash the
# offending site on the coverage map. Filters mirror whatever the answer is about,
# so we never light up sites the agent didn't actually talk about.
def _active_incident_sites(region: str = None, service: str = None,
                           hvc: bool = False, severity: str = None):
    where  = "ei.status='active' AND s.latitude IS NOT NULL AND s.longitude IS NOT NULL"
    params = []
    if region:
        where += " AND LOWER(s.region)=LOWER(?)"
        params.append(region)
    if service:
        where += " AND LOWER(ei.affected_service)=LOWER(?)"
        params.append(service)
    if hvc:
        where += " AND ei.is_hvc=1"
    if severity:
        where += " AND LOWER(ei.severity)=LOWER(?)"
        params.append(severity)
    return _query_sc_params(f"""
        SELECT s.site_name, s.region, s.latitude, s.longitude,
               COUNT(*)        AS incidents,
               SUM(ei.is_hvc)  AS hvc,
               MAX(CASE ei.severity WHEN 'critical' THEN 3 WHEN 'major' THEN 2 ELSE 1 END) AS sev_rank,
               GROUP_CONCAT(DISTINCT ei.affected_service) AS services,
               GROUP_CONCAT(DISTINCT ei.root_cause)       AS causes,
               MIN(ei.started_at)                         AS since
        FROM experience_incidents ei
        JOIN cells c ON ei.cell_id=c.cell_id
        JOIN sites s ON c.site_id=s.site_id
        WHERE {where}
        GROUP BY s.site_id
        ORDER BY sev_rank DESC, incidents DESC
        LIMIT 25
    """, tuple(params))


@app.get("/api/incidents/active-sites")
def api_active_incident_sites(region: str = None, service: str = None,
                              hvc: bool = False, severity: str = None):
    return _active_incident_sites(region, service, hvc, severity)


# Question phrasings that mean "show me who/what is having problems" — when one of
# these fires AND there are live incidents, we flash the sites on the map.
_ISSUE_MARKERS = re.compile(
    r"\b(issue|issues|problem|problems|struggling|degrad|outage|incident|incidents|"
    r"complain|poor experience|bad experience|buffering|streaming issue|"
    r"having (?:trouble|issues|problems)|affected|impacted|experiencing|right now|"
    r"at this moment|currently|live issue)\b", re.I)

# Known region names, so "issues in Ba Sing Se" flashes only that region.
def _known_regions():
    global _REGION_CACHE
    try:
        return _REGION_CACHE
    except NameError:
        pass
    try:
        _REGION_CACHE = [r["region"] for r in query_sc("SELECT DISTINCT region FROM sites")]
    except Exception:
        _REGION_CACHE = []
    return _REGION_CACHE

# Service names as a user would phrase them → the affected_service value in the log.
_SERVICE_FROM_Q = [
    (re.compile(r"\b(stream|streams|streaming|video|buffering)\b", re.I), "video streaming"),
    (re.compile(r"\b(voice|call|calls|calling|volte)\b", re.I),           "voice calls"),
    (re.compile(r"\b(mobile data|browsing|internet)\b", re.I),            "mobile data"),
    (re.compile(r"\b(outage|outages|all services)\b", re.I),             "all services"),
]
_HVC_FROM_Q = re.compile(r"\b(hvc|hvcs|high[- ]value|gold|platinum|premium|vip)\b", re.I)


def _flash_filters(question: str, result: dict = None):
    """What we flash must match what the ANSWER is about, or the map lights up sites
    the agent never mentioned. Prefer the filters the agent ACTUALLY used in its SQL
    (exact match to the answer); only fall back to reading the question when it never
    touched the incident log."""
    f = {"region": None, "service": None, "hvc": False, "severity": None}

    sql_blob = " ".join(
        (s.get("sql") or "") for s in (result or {}).get("steps", [])
        if "experience_incidents" in (s.get("sql") or "").lower()
    )

    if sql_blob:
        # Trust the agent's own WHERE clause — it defines the answer's scope exactly.
        m = re.search(r"affected_service\s*=\s*'([^']+)'", sql_blob, re.I)
        if m: f["service"] = m.group(1)
        m = re.search(r"\bseverity\s*=\s*'([^']+)'", sql_blob, re.I)
        if m: f["severity"] = m.group(1)
        m = re.search(r"\bregion\s*=\s*'([^']+)'", sql_blob, re.I)
        if m: f["region"] = m.group(1)
        if re.search(r"is_hvc\s*=\s*1", sql_blob, re.I):
            f["hvc"] = True
        return f

    # No incident SQL (e.g. answered from context) — infer scope from the question.
    for rx, svc in _SERVICE_FROM_Q:
        if rx.search(question):
            f["service"] = svc
            break
    f["hvc"] = bool(_HVC_FROM_Q.search(question))
    ql = question.lower()
    f["region"] = next((r for r in _known_regions() if r and r.lower() in ql), None)
    return f


def _compute_map_flash(question: str, result: dict = None):
    """Return the list of sites to flash on the coverage map for an issue-style
    question, else []. Deterministic: driven by the experience_incidents table,
    scoped to whatever the agent's answer actually covered."""
    if not question or not _ISSUE_MARKERS.search(question):
        return []
    try:
        f = _flash_filters(question, result)
        return _active_incident_sites(f["region"], f["service"], f["hvc"], f["severity"])
    except Exception as e:
        # Never break the chat over the map — but don't fail silently either:
        # a swallowed TypeError here once hid the flash being broken entirely.
        print(f"[map_flash] failed: {e!r}")
        return []

# ── Charts (snapshot panel) ───────────────────────────────────────────

@app.get("/api/charts/tech-dist")
def api_tech_dist():
    return query_sc("""
        SELECT st.current_technology as tech, COUNT(*) as n
        FROM subscriber_technology st
        JOIN subscribers s ON st.msisdn=s.msisdn
        WHERE s.is_active=1
        GROUP BY st.current_technology ORDER BY n DESC
    """)

@app.get("/api/charts/alarms-region")
def api_alarms_region():
    return query_sc("""
        SELECT s.region, COUNT(*) as n
        FROM network_alarms a
        JOIN cells c ON a.cell_id=c.cell_id
        JOIN sites s ON c.site_id=s.site_id
        WHERE a.is_active=1
        GROUP BY s.region ORDER BY n DESC
    """)

# ── Network tab ───────────────────────────────────────────────────────

@app.get("/api/network/regions")
def api_network_regions():
    rows = query_sc("SELECT DISTINCT region FROM sites ORDER BY region")
    return [r["region"] for r in rows]

@app.get("/api/network/kpis")
def api_network_kpis(region: str = "All", tech: str = "All"):
    r_clause = f"AND s.region='{region}'" if region != "All" else ""
    t_clause = f"AND c.technology='{tech}'" if tech != "All" else ""
    rows = query_sc(f"""
        SELECT s.region,
               ROUND(AVG(k.dl_throughput_mbps),2)  as avg_dl,
               ROUND(AVG(k.availability_pct),2)     as availability,
               ROUND(AVG(k.dropped_call_rate),3)    as drop_rate,
               ROUND(AVG(k.latency_ms),1)           as latency,
               COUNT(DISTINCT c.cell_id)            as cells,
               SUM(k.active_users)                  as active_users
        FROM kpis_daily k
        JOIN cells c ON k.cell_id=c.cell_id
        JOIN sites s ON c.site_id=s.site_id
        WHERE k.date=(SELECT MAX(date) FROM kpis_daily)
          {r_clause} {t_clause}
        GROUP BY s.region ORDER BY avg_dl DESC
    """)
    return rows

@app.get("/api/network/alarms")
def api_network_alarms(region: str = "All", severity: str = "All", limit: int = 100):
    r_clause = f"AND s.region='{region}'" if region != "All" else ""
    sev_clause = f"AND a.severity='{severity}'" if severity != "All" else ""
    return query_sc(f"""
        SELECT a.alarm_id, a.alarm_type, a.severity, a.description,
               s.site_name, s.region, a.started_at, a.is_active
        FROM network_alarms a
        JOIN cells c ON a.cell_id=c.cell_id
        JOIN sites s ON c.site_id=s.site_id
        WHERE a.is_active=1 {r_clause} {sev_clause}
        ORDER BY CASE a.severity
            WHEN 'critical' THEN 1 WHEN 'major' THEN 2
            WHEN 'minor'    THEN 3 ELSE 4 END
        LIMIT {limit}
    """)

@app.get("/api/network/top-cells")
def api_top_cells(sort: str = "drop_rate", limit: int = 20):
    sort_map = {
        "drop_rate":    "k.dropped_call_rate DESC",
        "throughput":   "k.dl_throughput_mbps ASC",
        "availability": "k.availability_pct ASC",
        "latency":      "k.latency_ms DESC",
    }
    order = sort_map.get(sort, "k.dropped_call_rate DESC")
    return query_sc(f"""
        SELECT c.cell_id, s.site_name, s.region, c.technology,
               ROUND(k.dl_throughput_mbps,2) as dl_mbps,
               ROUND(k.availability_pct,2)   as availability,
               ROUND(k.dropped_call_rate,3)  as drop_rate,
               ROUND(k.latency_ms,1)         as latency,
               k.active_users
        FROM kpis_daily k
        JOIN cells c ON k.cell_id=c.cell_id
        JOIN sites s ON c.site_id=s.site_id
        WHERE k.date=(SELECT MAX(date) FROM kpis_daily)
        ORDER BY {order}
        LIMIT {limit}
    """)

# ── Subscribers tab ───────────────────────────────────────────────────

@app.get("/api/subscribers/summary")
def api_subscribers_summary(region: str = "All", segment: str = "All"):
    r_clause = f"AND s.region='{region}'" if region != "All" else ""
    seg_clause = f"AND s.segment='{segment}'" if segment != "All" else ""

    tech_dist = query_sc(f"""
        SELECT st.current_technology as tech, COUNT(*) as n
        FROM subscribers s
        JOIN subscriber_technology st ON s.msisdn=st.msisdn
        WHERE s.is_active=1 {r_clause} {seg_clause}
        GROUP BY st.current_technology ORDER BY n DESC
    """)
    devices = query_sc(f"""
        SELECT d.brand, COUNT(*) as n,
               SUM(CASE WHEN d.supports_5g=1 THEN 1 ELSE 0 END)    as cap_5g,
               SUM(CASE WHEN d.volte_capable=1 THEN 1 ELSE 0 END)  as cap_volte
        FROM subscribers s
        JOIN devices d ON s.device_id=d.device_id
        WHERE s.is_active=1 {r_clause} {seg_clause}
        GROUP BY d.brand ORDER BY n DESC LIMIT 15
    """)
    sunset = query_sc(f"""
        SELECT s.region,
               COUNT(*) FILTER (WHERE st.current_technology='3G') as at_risk,
               COUNT(*) FILTER (WHERE st.current_technology='3G' AND d.volte_capable=1) as easy_fix
        FROM subscribers s
        JOIN subscriber_technology st ON s.msisdn=st.msisdn
        JOIN devices d ON s.device_id=d.device_id
        WHERE s.is_active=1 {r_clause}
        GROUP BY s.region ORDER BY at_risk DESC
    """)
    caps = query_sc(f"""
        SELECT
            COUNT(*) FILTER (WHERE d.supports_5g=1)     as cap_5g,
            COUNT(*) FILTER (WHERE d.volte_capable=1)   as cap_volte,
            COUNT(*) FILTER (WHERE st.current_technology='3G') as on_3g,
            COUNT(*)                                    as total
        FROM subscribers s
        JOIN devices d ON s.device_id=d.device_id
        JOIN subscriber_technology st ON s.msisdn=st.msisdn
        WHERE s.is_active=1 {r_clause} {seg_clause}
    """)
    return {"tech_dist": tech_dist, "devices": devices, "sunset": sunset,
            "caps": caps[0] if caps else {}}

@app.get("/api/subscribers/lookup")
def api_subscriber_lookup(msisdn: str):
    profile = query_sc(f"""
        SELECT s.msisdn, s.full_name, s.region, s.city, s.segment,
               s.sim_type, s.is_active, s.activation_date,
               st.current_technology, st.volte_active, st.last_seen_date,
               d.brand, d.model as device_model, d.max_technology,
               d.supports_5g, d.volte_capable, d.os, d.os_version
        FROM subscribers s
        LEFT JOIN subscriber_technology st ON s.msisdn=st.msisdn
        LEFT JOIN devices d ON s.device_id=d.device_id
        WHERE s.msisdn='{msisdn}'
    """)
    if not profile or "error" in profile[0]:
        return JSONResponse({"error": "Not found"}, status_code=404)
    commercial = query_op(f"""
        SELECT cv.segment, cv.arpu_monthly,
               p.plan_name, p.monthly_price, p.data_cap_gb, p.supports_5g
        FROM customer_value cv
        JOIN subscriptions sub ON cv.msisdn=sub.msisdn
        JOIN plans p ON sub.plan_id=p.plan_id
        WHERE cv.msisdn='{msisdn}' AND sub.status='active'
        LIMIT 1
    """)
    dou = query_sc(f"""
        SELECT month, ROUND(data_usage_gb,2) as gb
        FROM dou_monthly WHERE msisdn='{msisdn}'
        ORDER BY month DESC LIMIT 6
    """)
    return {"profile": profile[0], "commercial": commercial[0] if commercial else {},
            "dou": dou}

@app.get("/api/subscribers/mobility")
def api_mobility(region: str = "All"):
    r_clause = f"AND s.region='{region}'" if region != "All" else ""
    return query_sc(f"""
        SELECT mp.mobility_class,
               COUNT(*) as n,
               ROUND(AVG(d.total_data_gb),2) as avg_dou_gb
        FROM mobility_profile mp
        JOIN subscribers s ON mp.msisdn=s.msisdn
        JOIN dou_monthly d ON mp.msisdn=d.msisdn
        WHERE s.is_active=1 {r_clause}
          AND d.month=(SELECT MAX(month) FROM dou_monthly)
        GROUP BY mp.mobility_class ORDER BY n DESC
    """)

# ── Commercial tab ────────────────────────────────────────────────────

@app.get("/api/commercial/summary")
def api_commercial_summary(segment: str = "All", region: str = "All"):
    seg_clause = f"AND sub.plan_type='{segment}'" if segment != "All" else ""
    r_clause   = f"AND sub.region='{region}'"    if region  != "All" else ""

    value_segs = query_op(f"""
        SELECT cv.segment,
               COUNT(*) as n,
               ROUND(AVG(cv.arpu_monthly),2) as avg_arpu
        FROM customer_value cv
        JOIN subscriptions sub ON cv.msisdn=sub.msisdn
        WHERE sub.status='active' {seg_clause}
        GROUP BY cv.segment ORDER BY avg_arpu DESC
    """)
    billing = query_op("""
        SELECT strftime('%Y-%m', billing_month) as month,
               ROUND(SUM(billed_amount),0)  as billed,
               ROUND(SUM(paid_amount),0)    as paid,
               ROUND(SUM(billed_amount)-SUM(paid_amount),0) as unpaid,
               COUNT(DISTINCT CASE WHEN paid_amount < billed_amount THEN msisdn END) as unpaid_subs
        FROM billing
        GROUP BY billing_month ORDER BY billing_month DESC LIMIT 12
    """)
    plans = query_op("""
        SELECT p.plan_name, p.plan_type, p.monthly_price,
               p.data_cap_gb, p.supports_5g, p.volte_support,
               COUNT(sub.msisdn) as subscribers
        FROM plans p
        LEFT JOIN subscriptions sub ON p.plan_id=sub.plan_id AND sub.status='active'
        GROUP BY p.plan_id ORDER BY subscribers DESC
    """)
    hvc = query_op("""
        SELECT cv.msisdn, cv.segment, ROUND(cv.arpu_monthly,2) as arpu,
               p.plan_name, p.supports_5g
        FROM customer_value cv
        JOIN subscriptions sub ON cv.msisdn=sub.msisdn
        JOIN plans p ON sub.plan_id=p.plan_id
        WHERE cv.segment IN ('gold','platinum') AND sub.status='active'
        ORDER BY cv.arpu_monthly DESC LIMIT 100
    """)
    return {"value_segs": value_segs, "billing": billing, "plans": plans, "hvc": hvc}

@app.get("/api/commercial/arpu-trend")
def api_arpu_trend():
    return query_op("""
        SELECT strftime('%Y-%m', billing_month) as month,
               ROUND(AVG(billed_amount),2) as avg_arpu
        FROM billing
        GROUP BY billing_month ORDER BY billing_month ASC LIMIT 12
    """)

# ── Campaigns tab ─────────────────────────────────────────────────────

@app.get("/api/campaigns/summary")
def api_campaigns_summary():
    campaigns = query_op("""
        SELECT c.campaign_id, c.campaign_name, c.campaign_type,
               c.status, c.launch_date,
               o.offer_name,
               COUNT(DISTINCT ct.msisdn)                                         as targeted,
               COUNT(DISTINCT CASE WHEN ct.converted=1 THEN ct.msisdn END)       as converted
        FROM campaigns c
        LEFT JOIN offers o         ON c.offer_id=o.offer_id
        LEFT JOIN campaign_targets ct ON c.campaign_id=ct.campaign_id
        GROUP BY c.campaign_id ORDER BY c.launch_date DESC
    """)
    offers = query_op("""
        SELECT o.offer_id, o.offer_name, o.campaign_type,
               o.discount_pct, o.bonus_gb, o.price_override,
               o.target_technology, o.validity_days, o.description, o.is_active
        FROM offers o ORDER BY o.campaign_type, o.offer_name
    """)
    # Opportunity counts
    opp_5g = query_sc("""
        SELECT COUNT(DISTINCT s.msisdn) as n FROM subscribers s
        JOIN devices d ON s.device_id=d.device_id
        JOIN subscriber_technology st ON s.msisdn=st.msisdn
        WHERE s.is_active=1 AND d.supports_5g=1 AND st.current_technology='4G'
    """)
    opp_3g = query_sc("""
        SELECT COUNT(DISTINCT s.msisdn) as n FROM subscribers s
        JOIN devices d ON s.device_id=d.device_id
        JOIN subscriber_technology st ON s.msisdn=st.msisdn
        WHERE s.is_active=1 AND d.max_technology IN ('4G','5G') AND st.current_technology='3G'
    """)
    opp_fwa = query_sc("""
        SELECT COUNT(DISTINCT s.msisdn) as n FROM subscribers s
        JOIN mobility_profile mp ON s.msisdn=mp.msisdn
        JOIN dou_monthly d ON s.msisdn=d.msisdn
        WHERE s.is_active=1 AND mp.mobility_class='stationary'
          AND d.month=(SELECT MAX(month) FROM dou_monthly) AND d.total_data_gb >= 30
    """)
    opp_volte = query_sc("""
        SELECT COUNT(DISTINCT s.msisdn) as n FROM subscribers s
        JOIN devices d ON s.device_id=d.device_id
        JOIN subscriber_technology st ON s.msisdn=st.msisdn
        WHERE s.is_active=1 AND d.volte_capable=1 AND st.current_technology='3G'
          AND (st.volte_active=0 OR st.volte_active IS NULL)
    """)
    opp_hvc = query_op("""
        SELECT COUNT(DISTINCT cv.msisdn) as n FROM customer_value cv
        JOIN subscriptions sub ON cv.msisdn=sub.msisdn
        JOIN plans p ON sub.plan_id=p.plan_id
        WHERE cv.segment IN ('gold','platinum') AND p.supports_5g=0 AND sub.status='active'
    """)
    opportunities = {
        "5G_upsell":    opp_5g[0]["n"]    if opp_5g    else 0,
        "3G_migration": opp_3g[0]["n"]    if opp_3g    else 0,
        "FWA":          opp_fwa[0]["n"]   if opp_fwa   else 0,
        "VoLTE_sunset": opp_volte[0]["n"] if opp_volte else 0,
        "HVC_upsell":   opp_hvc[0]["n"]   if opp_hvc   else 0,
    }
    return {"campaigns": campaigns, "offers": offers, "opportunities": opportunities}

# ── Model config ──────────────────────────────────────────────────────

@app.post("/api/model")
async def api_set_model(config: dict):
    model = config.get("model", "bedrock")

    if model == "bedrock-80b":
        os.environ["BEDROCK_MODEL"] = "qwen.qwen3-next-80b-a3b"
    else:
        os.environ["BEDROCK_MODEL"] = "qwen.qwen3-32b-v1:0"

    _agent_bedrock.reset_memory()
    return {"ok": True, "model": model}

# ── Ops Portal — MQTT publish ─────────────────────────────────────────

class OpsSendRequest(BaseModel):
    type: str        # campaign | sms | report | network_flag
    title: str
    summary: str
    payload: dict = {}

@app.post("/ops/send")
def ops_send(body: OpsSendRequest):
    try:
        import paho.mqtt.publish as mqtt_publish
        import json as _json
        topic = f"networkanalyzer/actions/{body.type}"
        msg   = _json.dumps({
            "title":   body.title,
            "summary": body.summary,
            "payload": body.payload,
        })
        mqtt_publish.single(topic, msg, hostname="localhost", port=1883)
        return {"ok": True, "topic": topic}
    except Exception as e:
        raise HTTPException(500, f"MQTT publish failed: {e}")

@app.post("/api/reset")
def api_reset():
    for ag in (_agent_bedrock, _agent_langgraph):
        if hasattr(ag, "reset_memory"):
            ag.reset_memory()
    return {"ok": True}

# ── Dev runner ────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
