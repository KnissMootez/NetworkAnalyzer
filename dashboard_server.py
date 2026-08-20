"""
NetworkAnalyzer — Analytics Dashboard (FastAPI)
A standalone, non-LLM analytics portal that replaces the old Streamlit dashboard.
Serves a custom HTML/JS/CSS UI (dashboard_static/) + JSON endpoints for
Network / Subscribers / Commercial / Campaigns analytics.

Run: python -m uvicorn dashboard_server:app --host 0.0.0.0 --port 8002
"""

import os
import sqlite3

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# ── Config ──────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SC_DB    = os.path.join(BASE_DIR, "NetworkAnalyzer_new.db")
OP_DB    = os.path.join(BASE_DIR, "operator_new.db")
STATIC   = os.path.join(BASE_DIR, "dashboard_static")


# ── DB helpers (dict rows) ───────────────────────────────────────────────────
def _query(db_path, sql, params=()):
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        return [{"error": str(e)}]


def sc(sql, params=()):  return _query(SC_DB, sql, params)
def op(sql, params=()):  return _query(OP_DB, sql, params)


def _one(rows, default=None):
    return rows[0] if rows and "error" not in rows[0] else (default or {})


def _scalar(rows, key, default=0):
    r = _one(rows)
    v = r.get(key, default)
    return v if v is not None else default


# Build a SQL filter clause from optional params. Values are whitelisted /
# parameterised where they come from the client (region, segment), and the
# multi-select tech/severity values are constrained to a known set.
def _in_clause(col, values, allowed):
    vals = [v for v in (values or []) if v in allowed]
    if not vals:
        return ""
    quoted = ",".join("'" + v + "'" for v in vals)
    return f" AND {col} IN ({quoted})"


# ── FastAPI ──────────────────────────────────────────────────────────────────
app = FastAPI(title="NetworkAnalyzer Dashboard")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

os.makedirs(STATIC, exist_ok=True)


@app.get("/")
def root():
    return FileResponse(os.path.join(STATIC, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC), name="dashboard_static")


# ══════════════════════════════════════════════════════════════════════════════
#  FILTERS
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/filters")
def filters():
    regions = sc("SELECT DISTINCT region FROM subscribers WHERE region IS NOT NULL ORDER BY region")
    return JSONResponse({
        "regions": [r["region"] for r in regions if "error" not in r],
    })


# ══════════════════════════════════════════════════════════════════════════════
#  NETWORK
# ══════════════════════════════════════════════════════════════════════════════
_TECHS = {"2G", "3G", "4G", "5G"}
_SEVS  = {"critical", "major", "minor", "warning"}


@app.get("/api/network/summary")
def net_summary(region: str = "", tech: str = "4G,5G"):
    rf = " AND s.region=?" if region else ""
    tc = _in_clause("c.technology", tech.split(","), _TECHS) or " AND c.technology IN ('4G','5G')"
    args = [region] if region else []
    rows = sc(f"""
        SELECT ROUND(AVG(k.dl_throughput_mbps),1) AS avg_dl,
               ROUND(AVG(k.availability_pct),2)   AS avg_avail,
               ROUND(AVG(k.dropped_call_rate),3)  AS avg_drop,
               ROUND(AVG(k.latency_ms),1)         AS avg_lat,
               COUNT(DISTINCT k.cell_id)          AS cells,
               COALESCE(SUM(k.active_users_avg),0) AS total_users
        FROM kpis_daily k JOIN cells c ON k.cell_id=c.cell_id JOIN sites s ON c.site_id=s.site_id
        WHERE k.date=(SELECT MAX(date) FROM kpis_daily){tc}{rf}
    """, args)
    return JSONResponse(_one(rows))


@app.get("/api/network/by-region")
def net_by_region(tech: str = "4G,5G"):
    tc = _in_clause("c.technology", tech.split(","), _TECHS) or " AND c.technology IN ('4G','5G')"
    return JSONResponse(sc(f"""
        SELECT s.region,
               ROUND(AVG(k.dl_throughput_mbps),1) AS dl,
               ROUND(AVG(k.availability_pct),2)   AS avail,
               ROUND(AVG(k.dropped_call_rate),3)  AS drop_rate,
               ROUND(AVG(k.sinr_avg),1)           AS sinr,
               COUNT(DISTINCT a.alarm_id)         AS alarms,
               -- Alarm RATE is computed over every cell in the region, not just the
               -- 4G/5G subset this query averages KPIs over. Using the filtered subset
               -- gave denominators as small as 2 cells and rates above 140 per 100,
               -- which ranks regions by how small they are rather than how healthy.
               (SELECT COUNT(*) FROM cells c2 JOIN sites s2 ON c2.site_id=s2.site_id
                 WHERE s2.region = s.region)      AS all_cells,
               (SELECT COUNT(*) FROM network_alarms a2
                  JOIN cells c3 ON a2.cell_id = c3.cell_id
                  JOIN sites s3 ON c3.site_id = s3.site_id
                 WHERE s3.region = s.region AND a2.is_active = 1) AS all_alarms
        FROM kpis_daily k JOIN cells c ON k.cell_id=c.cell_id JOIN sites s ON c.site_id=s.site_id
        LEFT JOIN network_alarms a ON a.cell_id=c.cell_id AND a.is_active=1
        WHERE k.date=(SELECT MAX(date) FROM kpis_daily){tc}
        GROUP BY s.region ORDER BY dl DESC
    """))


@app.get("/api/network/alarms")
def net_alarms(region: str = "", sev: str = "critical,major", limit: int = 50):
    rf = " AND s.region=?" if region else ""
    sc_ = _in_clause("a.severity", sev.split(","), _SEVS) or " AND a.severity IN ('critical','major')"
    args = [region] if region else []
    rows = sc(f"""
        SELECT a.alarm_id, s.region, s.site_name, c.technology,
               a.alarm_type, a.severity, a.trigger_time, a.description
        FROM network_alarms a JOIN cells c ON a.cell_id=c.cell_id JOIN sites s ON c.site_id=s.site_id
        WHERE a.is_active=1{sc_}{rf}
        ORDER BY CASE a.severity WHEN 'critical' THEN 1 WHEN 'major' THEN 2
                 WHEN 'minor' THEN 3 ELSE 4 END, a.trigger_time DESC
        LIMIT ?
    """, args + [min(limit, 200)])
    return JSONResponse(rows)


_CELL_ORDER = {
    "drop":       "k.dropped_call_rate DESC",
    "throughput": "k.dl_throughput_mbps ASC",
    "avail":      "k.availability_pct ASC",
    "latency":    "k.latency_ms DESC",
    "sinr":       "k.sinr_avg ASC",
}


@app.get("/api/network/worst-cells")
def net_worst_cells(region: str = "", tech: str = "4G,5G", metric: str = "drop", limit: int = 20):
    rf = " AND s.region=?" if region else ""
    tc = _in_clause("c.technology", tech.split(","), _TECHS) or " AND c.technology IN ('4G','5G')"
    order = _CELL_ORDER.get(metric, _CELL_ORDER["drop"])
    args = [region] if region else []
    return JSONResponse(sc(f"""
        SELECT c.cell_id, s.region, s.site_name, c.technology,
               ROUND(k.dl_throughput_mbps,1) AS dl, ROUND(k.availability_pct,2) AS avail,
               ROUND(k.dropped_call_rate,3) AS drop_rate, ROUND(k.sinr_avg,1) AS sinr,
               ROUND(k.rsrp_avg,1) AS rsrp, k.active_users_avg
        FROM kpis_daily k JOIN cells c ON k.cell_id=c.cell_id JOIN sites s ON c.site_id=s.site_id
        WHERE k.date=(SELECT MAX(date) FROM kpis_daily){tc}{rf}
        ORDER BY {order} LIMIT ?
    """, args + [min(limit, 100)]))


@app.get("/api/network/incidents")
def net_incidents(region: str = ""):
    rf = " AND s.region=?" if region else ""
    args = [region] if region else []
    return JSONResponse(sc(f"""
        SELECT i.incident_id, s.region, s.site_name, i.incident_type, i.start_time,
               i.affected_users, i.severity, i.root_cause, i.resolved
        FROM network_incidents i JOIN cells c ON i.cell_id=c.cell_id JOIN sites s ON c.site_id=s.site_id
        WHERE 1=1{rf} ORDER BY i.start_time DESC LIMIT 100
    """, args))


# ══════════════════════════════════════════════════════════════════════════════
#  SUBSCRIBERS
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/subs/summary")
def subs_summary(region: str = ""):
    rf = " AND s.region=?" if region else ""
    a = [region] if region else []
    total = _scalar(sc(f"SELECT COUNT(DISTINCT s.msisdn) AS n FROM subscribers s JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE s.is_active=1{rf}", a), "n")
    on5g  = _scalar(sc(f"SELECT COUNT(*) AS n FROM subscriber_technology st JOIN subscribers s ON s.msisdn=st.msisdn WHERE st.current_technology='5G' AND s.is_active=1{rf}", a), "n")
    on3g  = _scalar(sc(f"SELECT COUNT(*) AS n FROM subscriber_technology st JOIN subscribers s ON s.msisdn=st.msisdn WHERE st.current_technology='3G' AND s.is_active=1{rf}", a), "n")
    fwa   = _scalar(sc(f"SELECT COUNT(DISTINCT mp.msisdn) AS n FROM mobility_profile mp JOIN dou_monthly dm ON mp.msisdn=dm.msisdn AND mp.month=dm.month JOIN subscribers s ON mp.msisdn=s.msisdn WHERE mp.mobility_class='stationary' AND dm.total_data_gb>30 AND mp.month=(SELECT MAX(month) FROM mobility_profile) AND s.is_active=1{rf}", a), "n")
    return JSONResponse({"total": total, "on_5g": on5g, "on_3g": on3g, "fwa": fwa})


@app.get("/api/subs/by-tech")
def subs_by_tech(region: str = ""):
    rf = " AND s.region=?" if region else ""
    a = [region] if region else []
    return JSONResponse(sc(f"SELECT st.current_technology AS tech, COUNT(*) AS n FROM subscriber_technology st JOIN subscribers s ON s.msisdn=st.msisdn WHERE s.is_active=1{rf} GROUP BY tech ORDER BY n DESC", a))


@app.get("/api/subs/tech-by-region")
def subs_tech_by_region():
    return JSONResponse(sc("SELECT s.region, st.current_technology AS tech, COUNT(*) AS n FROM subscribers s JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE s.is_active=1 GROUP BY s.region, tech ORDER BY s.region"))


@app.get("/api/subs/devices")
def subs_devices(region: str = ""):
    rf = " AND s.region=?" if region else ""
    a = [region] if region else []
    cap = _one(sc(f"SELECT SUM(d.is_5g_capable) AS five_g_capable, SUM(d.volte_capable) AS volte_capable, SUM(d.vowifi_capable) AS vowifi_capable, SUM(CASE WHEN d.max_technology='3G' THEN 1 ELSE 0 END) AS legacy_3g, COUNT(*) AS total FROM devices d JOIN subscribers s ON d.msisdn=s.msisdn WHERE s.is_active=1{rf}", a))
    brands = sc(f"SELECT d.brand, COUNT(*) AS n, SUM(d.is_5g_capable) AS five_g, SUM(d.volte_capable) AS volte, SUM(CASE WHEN d.max_technology='3G' THEN 1 ELSE 0 END) AS legacy FROM devices d JOIN subscribers s ON d.msisdn=s.msisdn WHERE s.is_active=1{rf} GROUP BY d.brand ORDER BY n DESC LIMIT 15", a)
    return JSONResponse({"capabilities": cap, "brands": brands})


@app.get("/api/subs/mobility")
def subs_mobility(region: str = ""):
    rf = " AND s.region=?" if region else ""
    a = [region] if region else []
    mob = sc(f"SELECT mp.mobility_class, COUNT(*) AS n, ROUND(AVG(dm.total_data_gb),1) AS avg_dou FROM mobility_profile mp JOIN dou_monthly dm ON mp.msisdn=dm.msisdn AND mp.month=dm.month JOIN subscribers s ON mp.msisdn=s.msisdn WHERE mp.month=(SELECT MAX(month) FROM mobility_profile) AND s.is_active=1{rf} GROUP BY mp.mobility_class ORDER BY avg_dou DESC", a)
    dou = sc(f"SELECT CASE WHEN dm.total_data_gb<2 THEN '< 2 GB' WHEN dm.total_data_gb<10 THEN '2-10 GB' WHEN dm.total_data_gb<30 THEN '10-30 GB' WHEN dm.total_data_gb<100 THEN '30-100 GB' ELSE '100+ GB' END AS bucket, COUNT(*) AS n, MIN(dm.total_data_gb) AS ord FROM dou_monthly dm JOIN subscribers s ON dm.msisdn=s.msisdn WHERE dm.month=(SELECT MAX(month) FROM dou_monthly) AND s.is_active=1{rf} GROUP BY bucket ORDER BY ord", a)
    return JSONResponse({"mobility": mob, "dou": dou})


@app.get("/api/subs/sunset")
def subs_sunset(region: str = ""):
    rf = " AND s.region=?" if region else ""
    a = [region] if region else []
    return JSONResponse(sc(f"""
        SELECT s.region,
               COUNT(DISTINCT CASE WHEN d.volte_capable=0 AND st.current_technology='3G' THEN s.msisdn END) AS no_volte,
               COUNT(DISTINCT CASE WHEN d.volte_capable=1 AND st.current_technology='3G' AND st.volte_active=0 THEN s.msisdn END) AS volte_inactive
        FROM subscribers s JOIN devices d ON s.msisdn=d.msisdn JOIN subscriber_technology st ON s.msisdn=st.msisdn
        WHERE s.is_active=1{rf} GROUP BY s.region ORDER BY (no_volte+volte_inactive) DESC
    """, a))


@app.get("/api/subs/lookup/{msisdn}")
def subs_lookup(msisdn: str):
    sub  = _one(sc("SELECT * FROM subscribers WHERE msisdn=?", [msisdn]))
    if not sub:
        return JSONResponse({"found": False})
    dev  = _one(sc("SELECT * FROM devices WHERE msisdn=?", [msisdn]))
    tech = _one(sc("SELECT * FROM subscriber_technology WHERE msisdn=?", [msisdn]))
    dou  = sc("SELECT month, total_data_gb FROM dou_monthly WHERE msisdn=? ORDER BY month DESC LIMIT 6", [msisdn])
    plan = _one(op("SELECT p.plan_name, p.monthly_price, p.data_cap_gb, p.supports_5g FROM subscriptions s JOIN plans p ON s.plan_id=p.plan_id WHERE s.msisdn=? AND s.is_current=1", [msisdn]))
    val  = _one(op("SELECT month, arpu, value_segment, is_hvc FROM customer_value WHERE msisdn=? ORDER BY month DESC LIMIT 1", [msisdn]))
    return JSONResponse({"found": True, "subscriber": sub, "device": dev, "tech": tech,
                         "dou": dou, "plan": plan, "value": val})


# ══════════════════════════════════════════════════════════════════════════════
#  COMMERCIAL
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/commercial/summary")
def com_summary(region: str = ""):
    rf = " AND c.region=?" if region else ""
    a = [region] if region else []
    latest = "(SELECT MAX(month) FROM customer_value)"
    rev  = _scalar(op(f"SELECT ROUND(SUM(cv.arpu),0) AS n FROM customer_value cv JOIN customers c ON cv.msisdn=c.msisdn WHERE cv.month={latest}{rf}", a), "n")
    arpu = _scalar(op(f"SELECT ROUND(AVG(cv.arpu),2) AS n FROM customer_value cv JOIN customers c ON cv.msisdn=c.msisdn WHERE cv.month={latest}{rf}", a), "n")
    hvc  = _scalar(op(f"SELECT COUNT(*) AS n FROM customer_value cv JOIN customers c ON cv.msisdn=c.msisdn WHERE cv.month={latest} AND cv.is_hvc=1{rf}", a), "n")
    unp  = _scalar(op(f"SELECT COUNT(DISTINCT b.msisdn) AS n FROM billing b JOIN customers c ON b.msisdn=c.msisdn WHERE b.payment_status='unpaid'{rf}", a), "n")
    return JSONResponse({"revenue": rev, "arpu": arpu, "hvc": hvc, "unpaid": unp})


@app.get("/api/commercial/segments")
def com_segments(region: str = ""):
    rf = " AND c.region=?" if region else ""
    a = [region] if region else []
    latest = "(SELECT MAX(month) FROM customer_value)"
    seg = op(f"SELECT cv.value_segment AS segment, COUNT(*) AS n, ROUND(AVG(cv.arpu),2) AS avg_arpu, ROUND(SUM(cv.arpu),0) AS total_rev FROM customer_value cv JOIN customers c ON cv.msisdn=c.msisdn WHERE cv.month={latest}{rf} GROUP BY cv.value_segment ORDER BY avg_arpu DESC", a)
    trend = op(f"SELECT cv.month, cv.value_segment AS segment, ROUND(AVG(cv.arpu),2) AS avg_arpu FROM customer_value cv JOIN customers c ON cv.msisdn=c.msisdn WHERE 1=1{rf} GROUP BY cv.month, cv.value_segment ORDER BY cv.month", a)
    return JSONResponse({"segments": seg, "trend": trend})


@app.get("/api/commercial/plans")
def com_plans():
    return JSONResponse(op("SELECT p.plan_name, p.plan_type, p.monthly_price, p.data_cap_gb, p.supports_5g, p.supports_volte, COUNT(s.msisdn) AS subscribers FROM plans p LEFT JOIN subscriptions s ON p.plan_id=s.plan_id AND s.is_current=1 GROUP BY p.plan_id ORDER BY subscribers DESC"))


@app.get("/api/commercial/billing")
def com_billing(region: str = ""):
    rf = " AND c.region=?" if region else ""
    a = [region] if region else []
    return JSONResponse(op(f"""
        SELECT b.billing_month AS month, ROUND(SUM(b.total_amount),0) AS billed,
               ROUND(SUM(CASE WHEN b.payment_status='paid' THEN b.total_amount ELSE 0 END),0) AS paid,
               ROUND(SUM(CASE WHEN b.payment_status='unpaid' THEN b.total_amount ELSE 0 END),0) AS unpaid
        FROM billing b JOIN customers c ON b.msisdn=c.msisdn WHERE 1=1{rf}
        GROUP BY b.billing_month ORDER BY b.billing_month DESC LIMIT 12
    """, a))


@app.get("/api/commercial/hvc")
def com_hvc(region: str = "", limit: int = 50):
    rf = " AND c.region=?" if region else ""
    a = [region] if region else []
    latest = "(SELECT MAX(month) FROM customer_value)"
    return JSONResponse(op(f"""
        SELECT c.msisdn, c.full_name, c.region, c.segment, cv.value_segment, cv.arpu,
               p.plan_name, p.supports_5g
        FROM customer_value cv JOIN customers c ON cv.msisdn=c.msisdn
        JOIN subscriptions s ON cv.msisdn=s.msisdn AND s.is_current=1
        JOIN plans p ON s.plan_id=p.plan_id
        WHERE cv.month={latest} AND cv.is_hvc=1{rf} ORDER BY cv.arpu DESC LIMIT ?
    """, a + [min(limit, 200)]))


# ══════════════════════════════════════════════════════════════════════════════
#  CAMPAIGNS  (incl. the SMS feedback loop)
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/campaigns/summary")
def camp_summary():
    active    = _scalar(op("SELECT COUNT(*) AS n FROM campaigns WHERE status='active'"), "n")
    completed = _scalar(op("SELECT COUNT(*) AS n FROM campaigns WHERE status='completed'"), "n")
    targets   = _scalar(op("SELECT COUNT(*) AS n FROM campaign_targets"), "n")
    converted = _scalar(op("SELECT COALESCE(SUM(converted),0) AS n FROM campaign_targets"), "n")
    sms_sent  = _scalar(op("SELECT COUNT(*) AS n FROM sms_log"), "n")
    replies   = _scalar(op("SELECT COUNT(*) AS n FROM sms_log WHERE response!='none'"), "n")
    return JSONResponse({"active": active, "completed": completed, "targets": targets,
                         "converted": converted, "sms_sent": sms_sent, "replies": replies,
                         "conv_rate": round(converted * 100 / targets, 1) if targets else 0})


@app.get("/api/campaigns/list")
def camp_list():
    return JSONResponse(op("""
        SELECT c.campaign_id, c.campaign_name, c.campaign_type, c.launched_date, c.status,
               COALESCE(o.offer_name,'—') AS offer_name,
               COUNT(ct.msisdn) AS targeted, COALESCE(SUM(ct.converted),0) AS converted
        FROM campaigns c
        LEFT JOIN offers o ON c.offer_id=o.offer_id
        LEFT JOIN campaign_targets ct ON c.campaign_id=ct.campaign_id
        GROUP BY c.campaign_id ORDER BY c.launched_date DESC
    """))


@app.get("/api/campaigns/feedback")
def camp_feedback():
    """The SMS feedback loop: per-campaign delivery + response funnel, plus replies."""
    funnel = op("""
        SELECT c.campaign_id, c.campaign_name, c.campaign_type, c.status,
               COUNT(sl.sms_id) AS sms_sent,
               SUM(CASE WHEN sl.status='delivered' THEN 1 ELSE 0 END) AS delivered,
               SUM(CASE WHEN sl.status='failed'    THEN 1 ELSE 0 END) AS failed,
               SUM(CASE WHEN sl.response='opt_in'  THEN 1 ELSE 0 END) AS opt_in,
               SUM(CASE WHEN sl.response='opt_out' THEN 1 ELSE 0 END) AS opt_out,
               SUM(CASE WHEN sl.response='query'   THEN 1 ELSE 0 END) AS queries,
               ROUND(100.0*SUM(CASE WHEN sl.response='opt_in' THEN 1 ELSE 0 END)
                     /NULLIF(SUM(CASE WHEN sl.status='delivered' THEN 1 ELSE 0 END),0),1) AS conversion_pct
        FROM campaigns c LEFT JOIN sms_log sl ON c.campaign_id=sl.campaign_id
        GROUP BY c.campaign_id ORDER BY c.launched_date DESC
    """)
    replies = op("""
        SELECT COALESCE(c.campaign_name, 'Direct SMS') AS campaign_name,
               sl.response AS intent, sl.inbound_text, sl.msisdn
        FROM sms_log sl LEFT JOIN campaigns c ON sl.campaign_id=c.campaign_id
        WHERE sl.inbound_text IS NOT NULL ORDER BY sl.sent_date DESC LIMIT 30
    """)
    return JSONResponse({"funnel": funnel, "replies": replies})


@app.get("/api/campaigns/offers")
def camp_offers():
    return JSONResponse(op("""
        SELECT offer_id, offer_name, target_campaign, target_technology, discount_pct,
               bonus_data_gb, price_override, validity_days, is_active, description
        FROM offers ORDER BY is_active DESC, offer_id
    """))


@app.get("/api/campaigns/opportunities")
def camp_opportunities():
    five_g    = _scalar(sc("SELECT COUNT(DISTINCT s.msisdn) AS n FROM subscribers s JOIN devices d ON s.msisdn=d.msisdn JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE d.is_5g_capable=1 AND st.current_technology='4G' AND EXISTS(SELECT 1 FROM coverage cv WHERE cv.msisdn=s.msisdn AND cv.technology_available='5G')"), "n")
    migration = _scalar(sc("SELECT COUNT(DISTINCT s.msisdn) AS n FROM subscribers s JOIN devices d ON s.msisdn=d.msisdn JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE d.max_technology IN ('4G','5G') AND st.current_technology='3G'"), "n")
    fwa       = _scalar(sc("SELECT COUNT(DISTINCT mp.msisdn) AS n FROM mobility_profile mp JOIN dou_monthly dm ON mp.msisdn=dm.msisdn AND mp.month=dm.month WHERE mp.mobility_class='stationary' AND dm.total_data_gb>30 AND mp.month=(SELECT MAX(month) FROM mobility_profile)"), "n")
    volte     = _scalar(sc("SELECT COUNT(DISTINCT s.msisdn) AS n FROM subscribers s JOIN devices d ON s.msisdn=d.msisdn JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE d.volte_capable=1 AND st.current_technology='3G' AND st.volte_active=0"), "n")
    hvc       = _scalar(op("SELECT COUNT(*) AS n FROM customer_value cv JOIN subscriptions s ON cv.msisdn=s.msisdn AND s.is_current=1 JOIN plans p ON s.plan_id=p.plan_id WHERE cv.is_hvc=1 AND p.supports_5g=0 AND cv.month=(SELECT MAX(month) FROM customer_value)"), "n")
    return JSONResponse([
        {"key": "5g_upsell",    "title": "5G Upsell",      "count": five_g,    "desc": "5G device on a 4G plan, with 5G coverage available"},
        {"key": "3g_migration", "title": "3G Migration",   "count": migration, "desc": "4G/5G device stuck on 3G — at risk on 3G sunset"},
        {"key": "fwa",          "title": "FWA Conversion",  "count": fwa,       "desc": "Stationary + 30GB/mo — using mobile as home internet"},
        {"key": "volte_sunset", "title": "VoLTE Sunset",   "count": volte,     "desc": "VoLTE-capable device on 3G, VoLTE not yet activated"},
        {"key": "hvc_upsell",   "title": "HVC Upsell",     "count": hvc,       "desc": "Gold/platinum customers not on a 5G plan"},
    ])


# ══════════════════════════════════════════════════════════════════════════════
#  QUERY BUILDER  (no-SQL, dropdown-driven SELECT tester)
# ══════════════════════════════════════════════════════════════════════════════
from fastapi import HTTPException

_QB_OPS = {"=", "!=", ">", "<", ">=", "<=", "LIKE"}
_QB_AGG = {"COUNT", "AVG", "SUM", "MIN", "MAX"}
_qb_schema_cache: dict = {}


def _qb_schema():
    """Introspect both DBs → {database: {table: [columns]}}. Cached."""
    if _qb_schema_cache:
        return _qb_schema_cache
    for name, path in (("network", SC_DB), ("operator", OP_DB)):
        conn = sqlite3.connect(path)
        tbls = {}
        for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            tbls[t] = [r[1] for r in conn.execute(f'PRAGMA table_info("{t}")')]
        conn.close()
        _qb_schema_cache[name] = tbls
    return _qb_schema_cache


def _qb_resolve(database):
    schema = _qb_schema()
    if database not in schema:
        raise HTTPException(400, "unknown database")
    return (SC_DB if database == "network" else OP_DB), schema[database]


@app.get("/api/qb/schema")
def qb_schema():
    return JSONResponse(_qb_schema())


@app.get("/api/qb/distinct")
def qb_distinct(database: str, table: str, column: str):
    path, schema = _qb_resolve(database)
    if table not in schema or column not in schema[table]:
        raise HTTPException(400, "unknown table/column")
    conn = sqlite3.connect(path)
    n = conn.execute(f'SELECT COUNT(DISTINCT "{column}") FROM "{table}"').fetchone()[0]
    vals = []
    if n and n <= 60:   # only offer a value dropdown for low-cardinality columns
        vals = [r[0] for r in conn.execute(
            f'SELECT DISTINCT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL ORDER BY 1 LIMIT 60')]
    conn.close()
    return JSONResponse({"count": n, "values": vals})


@app.post("/api/qb/run")
def qb_run(body: dict):
    """Build and run a SELECT from dropdown selections. SELECT-only; every table/
    column/operator is validated against the live schema (no injection surface)."""
    path, schema = _qb_resolve(body.get("database", ""))
    table = body.get("table", "")
    if table not in schema:
        raise HTTPException(400, "unknown table")
    cols_ok = set(schema[table])

    def ck(c):
        if c != "*" and c not in cols_ok:
            raise HTTPException(400, f"unknown column: {c}")

    group_by = [c for c in (body.get("group_by") or []) if c]
    agg      = body.get("aggregate") or None
    columns  = [c for c in (body.get("columns") or []) if c]

    sel = []
    for c in group_by:
        ck(c); sel.append(f'"{c}"')
    order_alias = None
    if agg:
        func = str(agg.get("func", "")).upper()
        acol = agg.get("column", "*")
        if func not in _QB_AGG:
            raise HTTPException(400, "bad aggregate")
        if acol != "*":
            ck(acol)
        order_alias = f'{func.lower()}_{"all" if acol == "*" else acol}'
        sel.append(f'{func}({"*" if acol == "*" else chr(34)+acol+chr(34)}) AS {order_alias}')
    if not agg:
        for c in columns:
            ck(c); sel.append(f'"{c}"')
    if not sel:
        sel = ["*"]

    sql = f'SELECT {", ".join(sel)} FROM "{table}"'
    args = []
    for fl in (body.get("filters") or []):
        c, op, v = fl.get("column"), fl.get("op"), fl.get("value")
        if not c:
            continue
        ck(c)
        if op not in _QB_OPS:
            raise HTTPException(400, "bad operator")
        sql += (" WHERE " if "WHERE" not in sql else " AND ") + f'"{c}" {op} ?'
        args.append(f"%{v}%" if op == "LIKE" else v)
    if group_by:
        sql += " GROUP BY " + ", ".join(f'"{c}"' for c in group_by)
    ob = body.get("order_by") or {}
    oc = ob.get("column")
    if oc:
        d = "DESC" if str(ob.get("dir", "")).upper() == "DESC" else "ASC"
        if oc in cols_ok:
            sql += f' ORDER BY "{oc}" {d}'
        elif oc == order_alias:
            sql += f' ORDER BY {oc} {d}'
    limit = max(1, min(int(body.get("limit") or 100), 1000))
    sql += f" LIMIT {limit}"

    conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    except Exception as e:
        conn.close()
        return JSONResponse({"error": str(e), "sql": sql})
    conn.close()
    return JSONResponse({"sql": sql, "rows": rows})
