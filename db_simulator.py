"""
db_simulator.py
On startup: backfills any missing data from the past year up to today.
Then runs a live simulation loop updating KPIs, QoE, ARPU, alarms, offers, churn etc.

Usage:
    python db_simulator.py                  # backfill + live loop
    python db_simulator.py --fast           # 5x faster tick
    python db_simulator.py --verbose        # print every change
    python db_simulator.py --backfill-only  # one-time historical fill then exit
"""

import sqlite3
import random
import time
import argparse
import os
import pickle
import numpy as np
from datetime import datetime, timedelta, date

random.seed()  # fresh seed every run for genuine randomness
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
SC_DB      = os.path.join(BASE_DIR, "NetworkAnalyzer_new.db")
OP_DB      = os.path.join(BASE_DIR, "operator_new.db")
MODEL_PATH = os.path.join(BASE_DIR, "churn_model.pkl")

# Load churn model if available
_churn_model = None
_churn_p90   = 0.60
_churn_p70   = 0.30
try:
    with open(MODEL_PATH, "rb") as _f:
        _bundle    = pickle.load(_f)
        _churn_model = _bundle["model"]
        _churn_p90   = _bundle.get("p90", 0.60)
        _churn_p70   = _bundle.get("p70", 0.30)
    print(f"[SIM] Churn model loaded from {MODEL_PATH} (thresholds: high>={_churn_p90:.3f}, med>={_churn_p70:.3f})")
except FileNotFoundError:
    print(f"[SIM] No churn model found at {MODEL_PATH} — run churn_model.py first")

# ═══════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════

TICK_SECONDS       = 30
FAST_MULTIPLIER    = 5

P_ALARM_TRIGGER    = 0.40
P_ALARM_CLEAR      = 0.20
P_TECH_MIGRATION   = 0.30
P_OFFER_ACCEPT     = 0.20
P_CHURN            = 0.12
P_NEW_SUB          = 0.51

KPI_CELLS_PER_TICK      = 15
MIGRATION_SUBS_PER_TICK = 8
DOU_SUBS_PER_TICK       = 30
OFFER_ACCEPT_PER_TICK   = 5

ALARM_TYPES = [
    "High Interference", "Coverage Hole", "Congestion",
    "Hardware Fault", "Backhaul Failure", "Power Issue"
]
SEVERITIES       = ["critical", "major", "minor", "warning"]
SEVERITY_WEIGHTS = [0.10, 0.25, 0.40, 0.25]

# Area quality bias for QoE (T=metro best, R=rural worst)
QOE_BIAS = {"T": 1.10, "N": 1.00, "C": 0.95, "R": 0.70, "S": 0.80}

# ═══════════════════════════════════════════════════════════════════════
# DB HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _conn(path):
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def sc_conn(): return _conn(SC_DB)
def op_conn(): return _conn(OP_DB)

def sc_query(sql, params=()):
    for attempt in range(3):
        try:
            conn = sc_conn()
            rows = conn.execute(sql, params).fetchall()
            conn.close()
            return rows
        except sqlite3.OperationalError:
            time.sleep(0.5 * (attempt + 1))
    return []

def op_query(sql, params=()):
    for attempt in range(3):
        try:
            conn = op_conn()
            rows = conn.execute(sql, params).fetchall()
            conn.close()
            return rows
        except sqlite3.OperationalError:
            time.sleep(0.5 * (attempt + 1))
    return []

def sc_write(sql, params=()):
    for attempt in range(3):
        try:
            conn = sc_conn()
            conn.execute(sql, params)
            conn.commit()
            conn.close()
            return True
        except sqlite3.OperationalError:
            time.sleep(0.5 * (attempt + 1))
    return False

def op_write(sql, params=()):
    for attempt in range(3):
        try:
            conn = op_conn()
            conn.execute(sql, params)
            conn.commit()
            conn.close()
            return True
        except sqlite3.OperationalError:
            time.sleep(0.5 * (attempt + 1))
    return False

def sc_write_many(sql, rows, batch_size=5000):
    """Batch insert/update with chunking."""
    for i in range(0, len(rows), batch_size):
        chunk = rows[i:i + batch_size]
        for attempt in range(3):
            try:
                conn = sc_conn()
                conn.executemany(sql, chunk)
                conn.commit()
                conn.close()
                break
            except sqlite3.OperationalError:
                time.sleep(0.5 * (attempt + 1))

def op_write_many(sql, rows, batch_size=5000):
    for i in range(0, len(rows), batch_size):
        chunk = rows[i:i + batch_size]
        for attempt in range(3):
            try:
                conn = op_conn()
                conn.executemany(sql, chunk)
                conn.commit()
                conn.close()
                break
            except sqlite3.OperationalError:
                time.sleep(0.5 * (attempt + 1))

# ═══════════════════════════════════════════════════════════════════════
# KPI GENERATION HELPERS
# ═══════════════════════════════════════════════════════════════════════

TECH_BASE = {
    "2G": dict(rsrp=-95, sinr=8,  dl=2,   ul=0.5, drop=1.8, avail=98.5, lat=300),
    "3G": dict(rsrp=-90, sinr=11, dl=8,   ul=2,   drop=1.2, avail=99.0, lat=120),
    "4G": dict(rsrp=-82, sinr=16, dl=45,  ul=12,  drop=0.6, avail=99.5, lat=35),
    "5G": dict(rsrp=-78, sinr=22, dl=180, ul=45,  drop=0.3, avail=99.8, lat=12),
}

def _gen_kpi(cell_id, technology, date_str):
    base = TECH_BASE.get(technology, dict(rsrp=-88, sinr=13, dl=30, ul=8, drop=0.8, avail=99.2, lat=50))
    congestion_spike = random.random() < 0.08
    fault_event      = random.random() < 0.03
    dl  = base["dl"]    * random.uniform(0.6 if congestion_spike else 0.85, 1.15)
    ul  = base["ul"]    * random.uniform(0.7, 1.1)
    drop = base["drop"] * random.uniform(1.5 if congestion_spike else 0.8, 2.5 if congestion_spike else 1.2)
    avail = base["avail"] * random.uniform(0.95 if fault_event else 0.999, 1.0)
    lat  = base["lat"]  * random.uniform(1.0, 2.2 if congestion_spike else 1.1)
    cong = "high" if congestion_spike else ("medium" if dl < base["dl"] * 0.75 else "low")
    return (
        cell_id, date_str,
        round(base["rsrp"] + random.gauss(0, 3), 2),
        round(base["sinr"] + random.gauss(0, 2), 2),
        round(dl, 2), round(ul, 2),
        round(drop, 3), round(avail, 3),
        round(lat, 1), cong,
        random.randint(10, 120)
    )

QOE_APP_PROFILES = {
    # app_type: (base_score, base_latency_ms, base_throughput_mbps)
    "video":     (3.8, 30,  15.0),
    "gaming":    (3.5, 45,   8.0),
    "streaming": (3.7, 35,  12.0),
    "voip":      (3.6, 20,   0.5),
    "web":       (4.0, 80,   5.0),
    "social":    (4.1, 60,   3.0),
}
QOE_APP_TYPES = list(QOE_APP_PROFILES.keys())

def _gen_qoe(msisdn, date_str, area_code, app_type):
    bias = QOE_BIAS.get(area_code, 1.0)
    base_score, base_lat, base_thr = QOE_APP_PROFILES.get(app_type, (3.5, 50, 5.0))
    score      = round(min(5.0, max(1.0, base_score * bias + random.gauss(0, 0.3))), 2)
    latency    = round(base_lat / bias * random.uniform(0.8, 1.3), 1)
    throughput = round(base_thr * bias * random.uniform(0.7, 1.4), 2)
    label = "poor" if score < 2.0 else ("fair" if score < 3.0 else ("good" if score < 4.0 else "excellent"))
    return (msisdn, date_str, app_type, latency, throughput, score, label)

# ═══════════════════════════════════════════════════════════════════════
# BACKFILL — run once on startup to fill historical gaps
# ═══════════════════════════════════════════════════════════════════════

def backfill_kpis_daily(verbose=False):
    """Fill kpis_daily for any missing dates in the past 365 days."""
    today      = datetime.now().date()
    start_date = today - timedelta(days=365)

    existing = {r[0] for r in sc_query("SELECT DISTINCT date FROM kpis_daily")}
    missing  = [
        (start_date + timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range((today - start_date).days)
        if (start_date + timedelta(days=i)).strftime("%Y-%m-%d") not in existing
    ]

    if not missing:
        if verbose: print("  [BACKFILL] kpis_daily already complete")
        return

    print(f"  [BACKFILL] kpis_daily: filling {len(missing)} missing dates …")
    cells = sc_query("SELECT cell_id, technology FROM cells WHERE is_active=1")

    for i, d in enumerate(missing):
        rows = [_gen_kpi(cid, tech, d) for cid, tech in cells]
        sc_write_many("""
            INSERT OR IGNORE INTO kpis_daily
                (cell_id, date, rsrp_avg, sinr_avg, dl_throughput_mbps,
                 ul_throughput_mbps, dropped_call_rate, availability_pct,
                 latency_ms, congestion_level, active_users_avg)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, rows)
        if (i + 1) % 30 == 0 or i == len(missing) - 1:
            print(f"    … {i+1}/{len(missing)} dates done")

    print(f"  [BACKFILL] kpis_daily complete — {len(missing) * len(cells):,} rows inserted")


def backfill_monthly_sc():
    """Fill dou_monthly, ott_monthly, mobility_profile for past 12 months."""
    # Anchor to DB's own latest month so real-world date drift doesn't create new gaps
    _max = sc_query("SELECT MAX(month) FROM dou_monthly")
    _ref = datetime.strptime(_max[0][0], "%Y-%m") if _max and _max[0][0] else datetime(2026, 3, 1)
    months = [(_ref - timedelta(days=30 * i)).strftime("%Y-%m") for i in range(11, -1, -1)]

    subscribers = sc_query("SELECT msisdn FROM subscribers WHERE is_active=1")
    msisdns     = [r[0] for r in subscribers]

    # ── dou_monthly ─────────────────────────────────────────────────────
    existing_dou = {(r[0], r[1]) for r in sc_query("SELECT msisdn, month FROM dou_monthly")}
    dou_rows = []
    for m in months:
        for msisdn in msisdns:
            if (msisdn, m) not in existing_dou:
                dou_rows.append((
                    msisdn, m,
                    round(random.uniform(0.5, 80), 3),
                    random.randint(1, 20),
                    None,
                    random.randint(15, 30)
                ))
    if dou_rows:
        print(f"  [BACKFILL] dou_monthly: inserting {len(dou_rows):,} rows …")
        sc_write_many("INSERT OR IGNORE INTO dou_monthly VALUES(?,?,?,?,?,?)", dou_rows)

    # ── ott_monthly ─────────────────────────────────────────────────────
    existing_ott = {(r[0], r[1]) for r in sc_query("SELECT msisdn, month FROM ott_monthly")}
    # Build tech map for voice_minutes synthesis
    _tech_map = dict(sc_query("SELECT msisdn, current_technology FROM subscriber_technology"))
    _base_voice = {"2G": 400, "3G": 280, "4G": 160, "5G": 90}
    ott_rows = []
    for m in months:
        for msisdn in msisdns:
            if (msisdn, m) not in existing_ott:
                total = random.uniform(0.5, 80)
                parts = sorted([random.random() for _ in range(5)])
                splits = [parts[0], parts[1]-parts[0], parts[2]-parts[1],
                          parts[3]-parts[2], 1-parts[3]]
                voip_gb = round(total * splits[3], 3)
                tech    = _tech_map.get(msisdn, "3G")
                base_v  = _base_voice.get(tech, 280)
                voice_m = max(10, int((base_v - voip_gb * 150) * random.uniform(0.75, 1.25)))
                ott_rows.append((
                    msisdn, m,
                    round(total * splits[0], 3),  # streaming
                    round(total * splits[1], 3),  # gaming
                    round(total * splits[2], 3),  # web
                    voip_gb,                       # voip
                    round(total * splits[4], 3),  # social
                    random.randint(0, 300),        # sms
                    voice_m,                       # voice_minutes
                ))
    if ott_rows:
        print(f"  [BACKFILL] ott_monthly: inserting {len(ott_rows):,} rows …")
        sc_write_many("INSERT OR IGNORE INTO ott_monthly VALUES(?,?,?,?,?,?,?,?,?)", ott_rows)

    # ── mobility_profile ────────────────────────────────────────────────
    existing_mob = {(r[0], r[1]) for r in sc_query("SELECT msisdn, month FROM mobility_profile")}
    mob_rows = []
    mob_classes = ["stationary", "low_mobility", "medium_mobility", "high_mobility"]
    mob_weights = [0.15, 0.30, 0.35, 0.20]
    for m in months:
        for msisdn in msisdns:
            if (msisdn, m) not in existing_mob:
                cells_count = random.randint(1, 50)
                mob_cls = random.choices(mob_classes, weights=mob_weights)[0]
                total_gb = next(
                    (r[0] for r in sc_query(
                        "SELECT total_data_gb FROM dou_monthly WHERE msisdn=? AND month=?",
                        (msisdn, m)
                    )), random.uniform(1, 40)
                )
                is_fwa = 1 if (mob_cls == "stationary" and total_gb > 30) else 0
                mob_rows.append((
                    msisdn, m, cells_count,
                    round(cells_count / 30, 2),
                    None, mob_cls, is_fwa,
                    1 if cells_count <= 3 else 0
                ))
    if mob_rows:
        print(f"  [BACKFILL] mobility_profile: inserting {len(mob_rows):,} rows …")
        sc_write_many("INSERT OR IGNORE INTO mobility_profile VALUES(?,?,?,?,?,?,?,?)", mob_rows)

    print("  [BACKFILL] NetworkAnalyzer monthly tables complete")


def backfill_monthly_op():
    """Fill billing and customer_value for past 12 months."""
    _max = op_query("SELECT MAX(month) FROM customer_value")
    _ref = datetime.strptime(_max[0][0], "%Y-%m") if _max and _max[0][0] else datetime(2026, 3, 1)
    months = [(_ref - timedelta(days=30 * i)).strftime("%Y-%m") for i in range(11, -1, -1)]

    msisdns = [r[0] for r in op_query("SELECT msisdn FROM customers WHERE is_active=1")]

    # ── customer_value ───────────────────────────────────────────────────
    existing_cv = {(r[0], r[1]) for r in op_query("SELECT msisdn, month FROM customer_value")}
    cv_rows = []
    for m in months:
        for msisdn in msisdns:
            if (msisdn, m) not in existing_cv:
                arpu = round(random.uniform(5, 150), 2)
                if arpu >= 80:   seg = "platinum"; hvc = 1
                elif arpu >= 45: seg = "gold";     hvc = 1
                elif arpu >= 25: seg = "silver";   hvc = 0
                else:            seg = "bronze";   hvc = 0
                cv_rows.append((msisdn, m, arpu, seg, hvc))
    if cv_rows:
        print(f"  [BACKFILL] customer_value: inserting {len(cv_rows):,} rows …")
        op_write_many("INSERT OR IGNORE INTO customer_value VALUES(?,?,?,?,?)", cv_rows)

    # ── billing ──────────────────────────────────────────────────────────
    existing_bill = {(r[0], r[1]) for r in op_query("SELECT msisdn, billing_month FROM billing")}
    bill_rows = []
    for m in months:
        for msisdn in msisdns:
            if (msisdn, m) not in existing_bill:
                total   = round(random.uniform(5, 150), 2)
                data_c  = round(total * random.uniform(0.3, 0.7), 2)
                voice_c = round(total - data_c, 2)
                paid    = random.choices(["paid", "unpaid"], weights=[0.85, 0.15])[0]
                pay_date = (datetime.now() - timedelta(days=random.randint(0, 25))).strftime("%Y-%m-%d") \
                           if paid == "paid" else None
                bill_rows.append((msisdn, m, total, data_c, voice_c, paid, pay_date))
    if bill_rows:
        print(f"  [BACKFILL] billing: inserting {len(bill_rows):,} rows …")
        op_write_many("INSERT OR IGNORE INTO billing VALUES(?,?,?,?,?,?,?)", bill_rows)

    print("  [BACKFILL] Operator monthly tables complete")


def backfill_qoe(verbose=False):
    """Fill qoe_daily for the past 30 days (rolling window)."""
    today      = datetime.now().date()
    start_date = today - timedelta(days=30)

    existing = {r[0] for r in sc_query("SELECT DISTINCT date FROM qoe_daily")}
    missing  = [
        (start_date + timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range((today - start_date).days)
        if (start_date + timedelta(days=i)).strftime("%Y-%m-%d") not in existing
    ]

    if not missing:
        if verbose: print("  [BACKFILL] qoe_daily already complete")
        return

    print(f"  [BACKFILL] qoe_daily: filling {len(missing)} missing dates …")
    subscribers = sc_query(
        "SELECT msisdn, area_code FROM subscribers WHERE is_active=1 AND area_code IS NOT NULL"
    )

    for d in missing:
        rows = []
        for msisdn, area_code in subscribers:
            for app in QOE_APP_TYPES:
                rows.append(_gen_qoe(msisdn, d, area_code, app))
        sc_write_many("INSERT OR IGNORE INTO qoe_daily VALUES(?,?,?,?,?,?,?)", rows)

    print(f"  [BACKFILL] qoe_daily complete — {len(missing)} dates filled")


# ═══════════════════════════════════════════════════════════════════════
# NEW TABLES — schema + backfill + live ticks
# ═══════════════════════════════════════════════════════════════════════

# Technology capability matrix — what each tier can produce
_TECH_CAPS = {
    "2G": dict(has_data=False, has_voip=False, has_streaming=False,
               signal_range=(-105, -85), sinr_range=(3, 10)),
    "3G": dict(has_data=True,  has_voip=False, has_streaming=True,
               signal_range=(-100, -78), sinr_range=(6, 14)),
    "4G": dict(has_data=True,  has_voip=None,  has_streaming=True,
               signal_range=(-92,  -65), sinr_range=(10, 22)),
    "5G": dict(has_data=True,  has_voip=True,  has_streaming=True,
               signal_range=(-88,  -58), sinr_range=(15, 30)),
}

# Streaming quality by tech tier (max resolution, buffering probability)
_STREAM_PROFILE = {
    "2G":  None,                              # no streaming
    "3G":  dict(max_res="360p",  buf_p=0.55, score_range=(1.5, 3.0)),
    "4G":  dict(max_res="1080p", buf_p=0.12, score_range=(3.0, 4.5)),
    "5G":  dict(max_res="4K",   buf_p=0.03, score_range=(4.0, 5.0)),
}

# Web quality by tech tier
_WEB_PROFILE = {
    "2G": dict(load_range=(8.0, 20.0), score_range=(1.0, 2.5)),
    "3G": dict(load_range=(3.0, 9.0),  score_range=(2.0, 3.5)),
    "4G": dict(load_range=(0.8, 3.0),  score_range=(3.5, 4.7)),
    "5G": dict(load_range=(0.2, 1.2),  score_range=(4.2, 5.0)),
}

# VoIP/VoLTE quality — only 4G (if volte_active=1) and 5G
_VOIP_PROFILE = {
    "4G_volte": dict(mos_range=(3.5, 4.5), jitter_range=(5, 25),  packet_loss_range=(0.1, 1.5)),
    "5G":       dict(mos_range=(4.0, 4.9), jitter_range=(2, 12),  packet_loss_range=(0.0, 0.8)),
}

# NPS buckets by value segment
_NPS_PROFILE = {
    "platinum": dict(promoter_p=0.65, detractor_p=0.08),
    "gold":     dict(promoter_p=0.50, detractor_p=0.15),
    "silver":   dict(promoter_p=0.35, detractor_p=0.22),
    "bronze":   dict(promoter_p=0.20, detractor_p=0.35),
}

# Complaint categories with tech relevance
_COMPLAINT_CATS = {
    "2G": ["call_drop", "poor_signal", "billing"],
    "3G": ["call_drop", "poor_signal", "slow_data", "billing"],
    "4G": ["slow_data", "billing", "coverage", "app_issues"],
    "5G": ["billing", "app_issues", "device_compatibility"],
}
_COMPLAINT_STATUS = ["open", "in_progress", "resolved", "closed"]
_COMPLAINT_STATUS_W = [0.20, 0.15, 0.40, 0.25]


def _ensure_new_tables():
    """Create new tables if they don't exist yet — safe to call on every startup."""
    sc = sc_conn()
    sc.executescript("""
        CREATE TABLE IF NOT EXISTS signal_quality (
            msisdn          TEXT    NOT NULL,
            date            TEXT    NOT NULL,
            rsrp_dbm        REAL,
            sinr_db         REAL,
            signal_label    TEXT,
            handover_count  INTEGER DEFAULT 0,
            PRIMARY KEY (msisdn, date)
        );

        CREATE TABLE IF NOT EXISTS streaming_quality (
            msisdn              TEXT NOT NULL,
            month               TEXT NOT NULL,
            avg_resolution      TEXT,
            buffering_ratio     REAL,
            avg_bitrate_mbps    REAL,
            stream_score        REAL,
            stream_label        TEXT,
            PRIMARY KEY (msisdn, month)
        );

        CREATE TABLE IF NOT EXISTS web_quality (
            msisdn              TEXT NOT NULL,
            month               TEXT NOT NULL,
            avg_page_load_sec   REAL,
            dns_fail_rate       REAL,
            web_score           REAL,
            web_label           TEXT,
            PRIMARY KEY (msisdn, month)
        );

        CREATE TABLE IF NOT EXISTS voip_quality (
            msisdn          TEXT NOT NULL,
            month           TEXT NOT NULL,
            mos_score       REAL,
            jitter_ms       REAL,
            packet_loss_pct REAL,
            voip_label      TEXT,
            PRIMARY KEY (msisdn, month)
        );

        CREATE TABLE IF NOT EXISTS complaints (
            complaint_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            msisdn          TEXT    NOT NULL,
            date            TEXT    NOT NULL,
            category        TEXT,
            description     TEXT,
            status          TEXT    DEFAULT 'open',
            resolution_days INTEGER
        );

    """)
    sc.commit()
    sc.close()

    op = op_conn()
    op.executescript("""
        CREATE TABLE IF NOT EXISTS nps_scores (
            msisdn          TEXT NOT NULL,
            month           TEXT NOT NULL,
            nps_score       INTEGER,
            nps_category    TEXT,
            PRIMARY KEY (msisdn, month)
        );

        CREATE TABLE IF NOT EXISTS roaming_usage (
            msisdn          TEXT NOT NULL,
            month           TEXT NOT NULL,
            roaming_country TEXT,
            data_gb         REAL DEFAULT 0,
            voice_minutes   INTEGER DEFAULT 0,
            roaming_cost    REAL DEFAULT 0,
            PRIMARY KEY (msisdn, month)
        );

        CREATE TABLE IF NOT EXISTS service_interruptions (
            interruption_id INTEGER PRIMARY KEY AUTOINCREMENT,
            msisdn          TEXT    NOT NULL,
            date            TEXT    NOT NULL,
            duration_min    INTEGER,
            cause           TEXT,
            affected_service TEXT
        );
    """)
    op.commit()
    op.close()
    print("  [INIT] New tables verified/created")


def _gen_signal(msisdn, date_str, tech, area_code):
    caps = _TECH_CAPS.get(tech, _TECH_CAPS["3G"])
    bias = QOE_BIAS.get(area_code, 1.0)
    lo, hi = caps["signal_range"]
    rsrp = round((lo + (hi - lo) * bias * random.uniform(0.7, 1.0)), 2)
    slo, shi = caps["sinr_range"]
    sinr = round(slo + (shi - slo) * bias * random.uniform(0.6, 1.0), 2)
    label = "excellent" if rsrp > -75 else ("good" if rsrp > -85 else ("fair" if rsrp > -95 else "poor"))
    handovers = random.randint(0, 3) if tech in ("4G", "5G") else random.randint(0, 8)
    return (msisdn, date_str, rsrp, sinr, label, handovers)


def _gen_streaming(msisdn, month, tech):
    profile = _STREAM_PROFILE.get(tech)
    if profile is None:
        return None  # 2G — no streaming
    buf = round(random.uniform(0, profile["buf_p"]), 4)
    lo, hi = profile["score_range"]
    score = round(random.uniform(lo, hi), 2)
    label = "poor" if score < 2.0 else ("fair" if score < 3.0 else ("good" if score < 4.0 else "excellent"))
    bitrate = {"360p": random.uniform(0.5, 1.5), "1080p": random.uniform(3.0, 8.0), "4K": random.uniform(15.0, 35.0)}[profile["max_res"]]
    return (msisdn, month, profile["max_res"], round(buf, 4), round(bitrate, 2), score, label)


def _gen_web(msisdn, month, tech, area_code):
    profile = _WEB_PROFILE.get(tech, _WEB_PROFILE["3G"])
    bias = QOE_BIAS.get(area_code, 1.0)
    lo, hi = profile["load_range"]
    load = round(random.uniform(lo, hi) / bias, 2)
    dns_fail = round(random.uniform(0, 0.15) * (2.0 - bias), 4)
    lo2, hi2 = profile["score_range"]
    score = round(random.uniform(lo2, hi2) * bias, 2)
    score = min(5.0, score)
    label = "poor" if score < 2.0 else ("fair" if score < 3.0 else ("good" if score < 4.0 else "excellent"))
    return (msisdn, month, load, dns_fail, score, label)


def _gen_voip(msisdn, month, tech, volte_active):
    if tech == "5G":
        profile = _VOIP_PROFILE["5G"]
    elif tech == "4G" and volte_active:
        profile = _VOIP_PROFILE["4G_volte"]
    else:
        return None  # not eligible
    lo, hi = profile["mos_range"]
    mos = round(random.uniform(lo, hi), 2)
    jitter = round(random.uniform(*profile["jitter_range"]), 1)
    pl = round(random.uniform(*profile["packet_loss_range"]), 3)
    label = "poor" if mos < 3.0 else ("fair" if mos < 3.6 else ("good" if mos < 4.2 else "excellent"))
    return (msisdn, month, mos, jitter, pl, label)


def _gen_nps(msisdn, month, segment):
    profile = _NPS_PROFILE.get(segment, _NPS_PROFILE["bronze"])
    r = random.random()
    if r < profile["detractor_p"]:
        score = random.randint(0, 6); cat = "detractor"
    elif r < profile["detractor_p"] + (1 - profile["detractor_p"] - profile["promoter_p"]):
        score = random.randint(7, 8); cat = "passive"
    else:
        score = random.randint(9, 10); cat = "promoter"
    return (msisdn, month, score, cat)


def backfill_new_tables(verbose=False):
    """Fill signal_quality (daily, 30 days), and monthly tables for 12 months."""
    if verbose: print("  [BACKFILL] Starting new tables …")
    _max = op_query("SELECT MAX(month) FROM nps_scores")
    _max_cv = op_query("SELECT MAX(month) FROM customer_value")
    _ref_str = (_max[0][0] if _max and _max[0][0] else None) or (_max_cv[0][0] if _max_cv and _max_cv[0][0] else None)
    _ref  = datetime.strptime(_ref_str, "%Y-%m") if _ref_str else datetime(2026, 3, 1)
    today = _ref.date()
    months = [(_ref - timedelta(days=30 * i)).strftime("%Y-%m") for i in range(11, -1, -1)]

    # Build tech + volte + area + segment lookup once
    tech_map   = dict(sc_query("SELECT msisdn, current_technology FROM subscriber_technology"))
    volte_map  = dict(sc_query("SELECT msisdn, volte_active FROM subscriber_technology"))
    area_map   = dict(sc_query("SELECT msisdn, area_code FROM subscribers WHERE is_active=1"))
    # segment from latest customer_value
    seg_rows   = op_query("""
        SELECT msisdn, value_segment FROM customer_value
        WHERE month=(SELECT MAX(month) FROM customer_value)
    """)
    seg_map    = dict(seg_rows)

    msisdns_sc = [r[0] for r in sc_query("SELECT msisdn FROM subscribers WHERE is_active=1")]
    msisdns_op = [r[0] for r in op_query("SELECT msisdn FROM customers WHERE is_active=1")]
    msisdn_set = set(msisdns_sc) & set(msisdns_op)
    msisdns    = list(msisdn_set)

    # ── signal_quality — seed once if empty (7 days, 20% sample) ────────
    if sc_query("SELECT COUNT(*) FROM signal_quality")[0][0] == 0:
        sig_sample = random.sample(msisdns, max(1, len(msisdns) // 5))
        _sig_today = datetime.now().date()
        sig_rows = [
            _gen_signal(msisdn, (_sig_today - timedelta(days=6 - i)).strftime("%Y-%m-%d"),
                        tech_map.get(msisdn, "3G"), area_map.get(msisdn, "N"))
            for i in range(7) for msisdn in sig_sample
        ]
        print(f"  [BACKFILL] signal_quality: inserting {len(sig_rows):,} rows …")
        sc_write_many("INSERT OR IGNORE INTO signal_quality VALUES(?,?,?,?,?,?)", sig_rows)

    # ── Monthly tables (12 months) ───────────────────────────────────────
    stream_rows = []; web_rows = []; voip_rows = []; nps_rows = []; roam_rows = []

    roam_countries = ["France", "Germany", "Spain", "Italy", "UAE", "Saudi Arabia", "Turkey", "Morocco"]
    p_roam = 0.04  # 4% of subs roam each month

    # Skip months that already have data entirely — avoids random re-sampling on every restart
    months_with_stream = {r[0] for r in sc_query("SELECT DISTINCT month FROM streaming_quality")}
    months_with_web    = {r[0] for r in sc_query("SELECT DISTINCT month FROM web_quality")}
    months_with_voip   = {r[0] for r in sc_query("SELECT DISTINCT month FROM voip_quality")}
    months_with_nps    = {r[0] for r in op_query("SELECT DISTINCT month FROM nps_scores")}
    months_with_roam   = {r[0] for r in op_query("SELECT DISTINCT month FROM roaming_usage")}

    # Fixed deterministic 30% sample for quality, 20% for NPS — same subs every run
    random.seed(42)
    quality_sample = set(random.sample(msisdns, max(1, len(msisdns) * 3 // 10)))
    nps_sample     = set(random.sample(msisdns, max(1, len(msisdns) // 5)))
    random.seed()  # restore randomness for the rest of the simulator

    for m in months:
        for msisdn in msisdns:
            tech   = tech_map.get(msisdn, "3G")
            volte  = bool(volte_map.get(msisdn, 0))
            area   = area_map.get(msisdn, "N")
            seg    = seg_map.get(msisdn, "bronze")

            if msisdn in quality_sample:
                if m not in months_with_stream:
                    row = _gen_streaming(msisdn, m, tech)
                    if row: stream_rows.append(row)
                if m not in months_with_web:
                    web_rows.append(_gen_web(msisdn, m, tech, area))
                if m not in months_with_voip:
                    row = _gen_voip(msisdn, m, tech, volte)
                    if row: voip_rows.append(row)

            if msisdn in nps_sample and m not in months_with_nps:
                nps_rows.append(_gen_nps(msisdn, m, seg))

            if m not in months_with_roam and random.random() < p_roam:
                country = random.choice(roam_countries)
                data_gb = round(random.uniform(0.1, 5.0), 3)
                voice_m = random.randint(5, 120)
                cost    = round(data_gb * 12 + voice_m * 0.3, 2)
                roam_rows.append((msisdn, m, country, data_gb, voice_m, cost))

    if stream_rows:
        print(f"  [BACKFILL] streaming_quality: inserting {len(stream_rows):,} rows …")
        sc_write_many("INSERT OR IGNORE INTO streaming_quality VALUES(?,?,?,?,?,?,?)", stream_rows)
    if web_rows:
        print(f"  [BACKFILL] web_quality: inserting {len(web_rows):,} rows …")
        sc_write_many("INSERT OR IGNORE INTO web_quality VALUES(?,?,?,?,?,?)", web_rows)
    if voip_rows:
        print(f"  [BACKFILL] voip_quality: inserting {len(voip_rows):,} rows …")
        sc_write_many("INSERT OR IGNORE INTO voip_quality VALUES(?,?,?,?,?,?)", voip_rows)
    if nps_rows:
        print(f"  [BACKFILL] nps_scores: inserting {len(nps_rows):,} rows …")
        op_write_many("INSERT OR IGNORE INTO nps_scores VALUES(?,?,?,?)", nps_rows)
    if roam_rows:
        print(f"  [BACKFILL] roaming_usage: inserting {len(roam_rows):,} rows …")
        op_write_many("INSERT OR IGNORE INTO roaming_usage VALUES(?,?,?,?,?,?)", roam_rows)

    # ── Seed some historical complaints ─────────────────────────────────
    existing_complaints = sc_query("SELECT COUNT(*) FROM complaints")[0][0]
    if existing_complaints < 100:
        comp_rows = []
        sample = random.sample(msisdns, min(2000, len(msisdns)))
        for msisdn in sample:
            if random.random() < 0.15:  # ~15% had a complaint in the past year
                tech = tech_map.get(msisdn, "3G")
                cats = _COMPLAINT_CATS.get(tech, ["billing"])
                cat  = random.choice(cats)
                days_ago = random.randint(1, 365)
                d    = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
                status = random.choices(_COMPLAINT_STATUS, weights=_COMPLAINT_STATUS_W)[0]
                res_days = random.randint(1, 14) if status in ("resolved", "closed") else None
                comp_rows.append((msisdn, d, cat, f"Customer reported {cat.replace('_',' ')} issue", status, res_days))
        if comp_rows:
            print(f"  [BACKFILL] complaints: seeding {len(comp_rows):,} rows …")
            sc_write_many(
                "INSERT OR IGNORE INTO complaints (msisdn,date,category,description,status,resolution_days) VALUES(?,?,?,?,?,?)",
                comp_rows
            )

    # ── Seed some service interruptions ─────────────────────────────────
    existing_si = op_query("SELECT COUNT(*) FROM service_interruptions")[0][0]
    if existing_si < 50:
        si_rows = []
        sample = random.sample(msisdns, min(1000, len(msisdns)))
        causes   = ["network_outage", "maintenance", "congestion", "hardware_fault"]
        services = ["voice", "data", "sms", "all"]
        for msisdn in sample:
            if random.random() < 0.08:
                days_ago = random.randint(1, 180)
                d = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
                si_rows.append((
                    msisdn, d,
                    random.randint(5, 240),
                    random.choice(causes),
                    random.choice(services)
                ))
        if si_rows:
            print(f"  [BACKFILL] service_interruptions: seeding {len(si_rows):,} rows …")
            op_write_many(
                "INSERT OR IGNORE INTO service_interruptions (msisdn,date,duration_min,cause,affected_service) VALUES(?,?,?,?,?)",
                si_rows
            )

    print("  [BACKFILL] New tables complete")


def simulate_new_table_tick(verbose=False):
    """Live tick: update signal quality for a random sub-sample + trickle new complaints."""
    today  = datetime.now().strftime("%Y-%m-%d")

    # Signal quality refresh — 100 random subscribers
    subs = sc_query("""
        SELECT s.msisdn, st.current_technology, s.area_code
        FROM subscribers s
        JOIN subscriber_technology st ON s.msisdn = st.msisdn
        WHERE s.is_active=1
        ORDER BY RANDOM() LIMIT 100
    """)
    sig_rows = [_gen_signal(msisdn, today, tech, area) for msisdn, tech, area in subs]
    for row in sig_rows:
        sc_write("""
            INSERT INTO signal_quality VALUES(?,?,?,?,?,?)
            ON CONFLICT(msisdn, date) DO UPDATE SET
                rsrp_dbm=excluded.rsrp_dbm, sinr_db=excluded.sinr_db,
                signal_label=excluded.signal_label, handover_count=excluded.handover_count
        """, row)

    # Trickle new complaint (rare — ~1 per tick on average across 50k subs)
    if random.random() < 0.30:
        sub = sc_query("""
            SELECT s.msisdn, st.current_technology FROM subscribers s
            JOIN subscriber_technology st ON s.msisdn=st.msisdn
            WHERE s.is_active=1 ORDER BY RANDOM() LIMIT 1
        """)
        if sub:
            msisdn, tech = sub[0]
            cat = random.choice(_COMPLAINT_CATS.get(tech, ["billing"]))
            sc_write(
                "INSERT INTO complaints (msisdn,date,category,description,status) VALUES(?,?,?,?,?)",
                (msisdn, today, cat, f"Customer reported {cat.replace('_',' ')} issue", "open")
            )
            if verbose: print(f"  [COMPLAINT] New {cat} complaint from {msisdn}")

    if verbose: print(f"  [NEW_TABLES] Signal quality refreshed for {len(subs)} subs")


def run_backfill(verbose=False):
    """Run all backfill tasks in sequence."""
    print("\n╔══════════════════════════════════════════════╗")
    print("║  BACKFILLING HISTORICAL DATA …               ║")
    print("╚══════════════════════════════════════════════╝")
    t0 = time.time()
    backfill_kpis_daily(verbose)
    backfill_monthly_sc()
    backfill_monthly_op()
    backfill_qoe(verbose)
    backfill_new_tables(verbose)
    elapsed = time.time() - t0
    print(f"\n  Backfill done in {elapsed:.1f}s\n")


# ═══════════════════════════════════════════════════════════════════════
# ENSURE TODAY / CURRENT MONTH ARE COVERED
# ═══════════════════════════════════════════════════════════════════════

def ensure_today_kpis(verbose=False):
    """Make sure kpis_daily has at least one entry for today."""
    today = datetime.now().strftime("%Y-%m-%d")
    count = sc_query("SELECT COUNT(*) FROM kpis_daily WHERE date=?", (today,))[0][0]
    if count == 0:
        cells = sc_query("SELECT cell_id, technology FROM cells WHERE is_active=1")
        rows  = [_gen_kpi(cid, tech, today) for cid, tech in cells]
        sc_write_many("""
            INSERT OR IGNORE INTO kpis_daily
                (cell_id, date, rsrp_avg, sinr_avg, dl_throughput_mbps,
                 ul_throughput_mbps, dropped_call_rate, availability_pct,
                 latency_ms, congestion_level, active_users_avg)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, rows)
        if verbose: print(f"  [INIT] Created today's kpis_daily ({len(rows)} cells)")


def ensure_today_qoe(verbose=False):
    """Make sure qoe_daily has entries for today."""
    today = datetime.now().strftime("%Y-%m-%d")
    count = sc_query("SELECT COUNT(*) FROM qoe_daily WHERE date=?", (today,))[0][0]
    if count == 0:
        subs = sc_query(
            "SELECT msisdn, area_code FROM subscribers WHERE is_active=1 AND area_code IS NOT NULL"
        )
        rows = []
        for msisdn, area_code in subs:
            for app in QOE_APP_TYPES:
                rows.append(_gen_qoe(msisdn, today, area_code, app))
        sc_write_many("INSERT OR IGNORE INTO qoe_daily VALUES(?,?,?,?,?,?,?)", rows)
        if verbose: print(f"  [INIT] Created today's qoe_daily")


def ensure_current_month(verbose=False):
    """Create current month records in monthly tables if missing."""
    month = datetime.now().strftime("%Y-%m")

    # dou_monthly
    missing_dou = sc_query("""
        SELECT msisdn FROM subscribers WHERE is_active=1
        AND msisdn NOT IN (SELECT msisdn FROM dou_monthly WHERE month=?)
        LIMIT 500
    """, (month,))
    if missing_dou:
        rows = [(m[0], month, 0.0, 0, None, 0) for m in missing_dou]
        sc_write_many("INSERT OR IGNORE INTO dou_monthly VALUES(?,?,?,?,?,?)", rows)
        if verbose: print(f"  [INIT] Created {len(rows)} dou_monthly records for {month}")

    # customer_value
    missing_cv = op_query("""
        SELECT msisdn FROM customers WHERE is_active=1
        AND msisdn NOT IN (SELECT msisdn FROM customer_value WHERE month=?)
        LIMIT 500
    """, (month,))
    if missing_cv:
        cv_rows = []
        for (msisdn,) in missing_cv:
            prev = op_query("""
                SELECT arpu, value_segment, is_hvc FROM customer_value
                WHERE msisdn=? ORDER BY month DESC LIMIT 1
            """, (msisdn,))
            if prev:
                arpu = round(prev[0][0] * random.uniform(0.95, 1.05), 2)
                seg  = prev[0][1]; hvc = prev[0][2]
            else:
                arpu = round(random.uniform(5, 150), 2)
                if arpu >= 80:   seg = "platinum"; hvc = 1
                elif arpu >= 45: seg = "gold";     hvc = 1
                elif arpu >= 25: seg = "silver";   hvc = 0
                else:            seg = "bronze";   hvc = 0
            cv_rows.append((msisdn, month, arpu, seg, hvc))
        op_write_many("INSERT OR IGNORE INTO customer_value VALUES(?,?,?,?,?)", cv_rows)
        if verbose: print(f"  [INIT] Created {len(cv_rows)} customer_value records for {month}")


# ═══════════════════════════════════════════════════════════════════════
# LIVE SIMULATION FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════

def simulate_kpi_fluctuation(verbose=False):
    """Update KPIs for random cells — writes today's row."""
    cells = sc_query(
        "SELECT cell_id, technology FROM cells WHERE is_active=1 ORDER BY RANDOM() LIMIT ?",
        (KPI_CELLS_PER_TICK,)
    )
    today = datetime.now().strftime("%Y-%m-%d")
    rows  = [_gen_kpi(cid, tech, today) for cid, tech in cells]
    for row in rows:
        sc_write("""
            INSERT INTO kpis_daily
                (cell_id, date, rsrp_avg, sinr_avg, dl_throughput_mbps,
                 ul_throughput_mbps, dropped_call_rate, availability_pct,
                 latency_ms, congestion_level, active_users_avg)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(cell_id, date) DO UPDATE SET
                rsrp_avg=excluded.rsrp_avg, sinr_avg=excluded.sinr_avg,
                dl_throughput_mbps=excluded.dl_throughput_mbps,
                ul_throughput_mbps=excluded.ul_throughput_mbps,
                dropped_call_rate=excluded.dropped_call_rate,
                availability_pct=excluded.availability_pct,
                latency_ms=excluded.latency_ms,
                congestion_level=excluded.congestion_level,
                active_users_avg=excluded.active_users_avg
        """, row)
    if verbose: print(f"  [KPI] Updated {len(rows)} cells")


def simulate_qoe_update(verbose=False):
    """Refresh QoE scores for a random subset of subscribers today."""
    today = datetime.now().strftime("%Y-%m-%d")
    subs  = sc_query("""
        SELECT msisdn, area_code FROM subscribers
        WHERE is_active=1 AND area_code IS NOT NULL
        ORDER BY RANDOM() LIMIT 200
    """)
    rows = []
    for msisdn, area_code in subs:
        for app in ("gaming", "video"):
            rows.append(_gen_qoe(msisdn, today, area_code, app))
    for row in rows:
        sc_write("""
            INSERT INTO qoe_daily VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(msisdn, date, app_type) DO UPDATE SET
                avg_latency_ms=excluded.avg_latency_ms,
                avg_throughput_mbps=excluded.avg_throughput_mbps,
                experience_score=excluded.experience_score,
                experience_label=excluded.experience_label
        """, row)
    if verbose: print(f"  [QOE] Refreshed {len(subs)} subscribers")


def _rescore_churn(msisdn_list, month, op_conn, sc_conn):
    """Re-score a list of MSISDNs using the Indian telecom model and update customer_value."""
    if _churn_model is None or not msisdn_list:
        return

    from collections import defaultdict
    placeholders = ",".join("?" * len(msisdn_list))
    today = date.today()

    # tenure in days
    subs = sc_conn.execute(f"""
        SELECT msisdn, activation_date FROM subscribers
        WHERE msisdn IN ({placeholders})
    """, msisdn_list).fetchall()
    tenure_map = {}
    for m, act in subs:
        try:
            tenure_map[m] = float(np.clip((today - date.fromisoformat(act)).days, 0, 3650))
        except Exception:
            tenure_map[m] = 365.0

    # Billing last 3 months: total_amount + data_charges
    billing = op_conn.execute(f"""
        SELECT msisdn, billing_month, total_amount, data_charges FROM billing
        WHERE msisdn IN ({placeholders})
        ORDER BY msisdn, billing_month DESC
    """, msisdn_list).fetchall()
    arpu_hist  = defaultdict(list)
    maxrc_hist = defaultdict(list)
    for m, _, amt, dc in billing:
        arpu_hist[m].append(amt or 0)
        maxrc_hist[m].append(dc or 0)

    # Data usage + days_active last 3 months
    dou = sc_conn.execute(f"""
        SELECT msisdn, month, total_data_gb * 1024, days_active FROM dou_monthly
        WHERE msisdn IN ({placeholders})
        ORDER BY msisdn, month DESC
    """, msisdn_list).fetchall()
    data_hist  = defaultdict(list)
    days_map   = {}
    for m, _, mb, da in dou:
        data_hist[m].append(mb or 0)
        if m not in days_map:
            days_map[m] = da or 0

    # Recharge proxy: paid billing months
    rech = op_conn.execute(f"""
        SELECT msisdn, billing_month,
               CASE WHEN payment_status='paid' THEN 1 ELSE 0 END
        FROM billing WHERE msisdn IN ({placeholders})
        ORDER BY msisdn, billing_month DESC
    """, msisdn_list).fetchall()
    rech_hist = defaultdict(list)
    for m, _, paid in rech:
        rech_hist[m].append(paid or 0)

    # Voice minutes last 3 months
    voice = sc_conn.execute(f"""
        SELECT msisdn, month, voice_minutes FROM ott_monthly
        WHERE msisdn IN ({placeholders})
        ORDER BY msisdn, month DESC
    """, msisdn_list).fetchall()
    voice_hist = defaultdict(list)
    for m, _, vm in voice:
        voice_hist[m].append(vm or 0)

    # Roaming
    roam = sc_conn.execute(f"""
        SELECT msisdn, data_roaming_active FROM subscriber_technology
        WHERE msisdn IN ({placeholders})
    """, msisdn_list).fetchall()
    roam_map = dict(roam)

    def _drop(hist):
        if len(hist) >= 3:
            early = (hist[1] + hist[2]) / 2
        elif len(hist) == 2:
            early = hist[1]
        elif len(hist) == 1:
            early = hist[0]
        else:
            return 0.0
        return float(np.clip((early - hist[0]) / early if early > 0 else 0, -1, 5))

    # Build feature matrix — matches FEATURE_COLS order in churn_model.py
    rows = []
    for msisdn in msisdn_list:
        ah = arpu_hist[msisdn]
        mh = maxrc_hist[msisdn]
        dh = data_hist[msisdn]
        rh = rech_hist[msisdn]
        vh = voice_hist[msisdn]
        rows.append([
            _drop(ah),                         # arpu_drop_pct
            ah[0] if ah else 0,                # arpu_m8
            _drop(ah),                         # rech_amt_drop_pct (same signal)
            ah[0] if ah else 0,                # rech_amt_m8
            _drop(mh),                         # max_rech_drop_pct
            mh[0] if mh else 0,                # max_rech_m8
            _drop(dh),                         # data_drop_pct
            dh[0] if dh else 0,                # data_mb_m8
            float(days_map.get(msisdn, 15)),   # days_active_m8
            _drop(rh),                         # rech_drop_pct
            rh[0] if rh else 0,                # rech_num_m8
            _drop(vh),                         # voice_drop_pct
            vh[0] if vh else 0,                # voice_min_m8
            roam_map.get(msisdn, 0),           # has_roaming
            tenure_map.get(msisdn, 365.0),     # tenure_days
        ])

    X = np.array(rows, dtype=np.float32)
    scores = _churn_model.predict_proba(X)[:, 1]

    for i, msisdn in enumerate(msisdn_list):
        s = float(scores[i])
        label = "high" if s >= _churn_p90 else "medium" if s >= _churn_p70 else "low"
        op_conn.execute("""
            UPDATE customer_value SET churn_risk_score=?, churn_label=?
            WHERE msisdn=? AND month=?
        """, (round(s, 4), label, msisdn, month))
    op_conn.commit()


def simulate_arpu_update(verbose=False):
    """Update ARPU for a random subset of subscribers this month."""
    month = datetime.now().strftime("%Y-%m")
    subs  = op_query("""
        SELECT msisdn, arpu FROM customer_value WHERE month=?
        ORDER BY RANDOM() LIMIT 100
    """, (month,))
    updated = 0
    for msisdn, old_arpu in subs:
        new_arpu = round(max(5.0, old_arpu * random.uniform(0.97, 1.05)), 2)
        if new_arpu >= 80:   seg = "platinum"; hvc = 1
        elif new_arpu >= 45: seg = "gold";     hvc = 1
        elif new_arpu >= 25: seg = "silver";   hvc = 0
        else:                seg = "bronze";   hvc = 0
        op_write("""
            UPDATE customer_value
            SET arpu=?, value_segment=?, is_hvc=?
            WHERE msisdn=? AND month=?
        """, (new_arpu, seg, hvc, msisdn, month))
        updated += 1

    # Re-score churn for the updated batch
    if _churn_model and subs:
        msisdn_list = [r[0] for r in subs]
        op_conn  = sqlite3.connect(OP_DB)
        sc_conn  = sqlite3.connect(SC_DB)
        _rescore_churn(msisdn_list, month, op_conn, sc_conn)
        op_conn.close(); sc_conn.close()

    if verbose: print(f"  [ARPU] Updated {updated} customer value records")


def simulate_voice_update(verbose=False):
    """Drift voice_minutes for a random subset of subscribers this month."""
    month = datetime.now().strftime("%Y-%m")
    rows  = sc_query("""
        SELECT o.msisdn, o.voice_minutes, st.current_technology
        FROM ott_monthly o
        JOIN subscriber_technology st ON o.msisdn=st.msisdn
        WHERE o.month=? ORDER BY RANDOM() LIMIT 150
    """, (month,))
    BASE_BY_TECH = {"2G": 400, "3G": 280, "4G": 160, "5G": 90}
    updated = 0
    for msisdn, cur_min, tech in rows:
        base    = BASE_BY_TECH.get(tech, 280)
        new_min = max(10, int((cur_min or base) * random.uniform(0.95, 1.06)))
        sc_write("UPDATE ott_monthly SET voice_minutes=? WHERE msisdn=? AND month=?",
                 (new_min, msisdn, month))
        updated += 1
    if verbose: print(f"  [VOICE] Updated {updated} voice_minutes records")


def simulate_alarm_trigger(verbose=False):
    if random.random() > P_ALARM_TRIGGER:
        return
    cells = sc_query("SELECT cell_id FROM cells WHERE is_active=1 ORDER BY RANDOM() LIMIT 3")
    if not cells:
        return
    cell_id  = cells[0][0]
    atype    = random.choice(ALARM_TYPES)
    severity = random.choices(SEVERITIES, weights=SEVERITY_WEIGHTS)[0]
    now      = datetime.now().isoformat()
    sc_write("""
        INSERT INTO network_alarms
            (cell_id, alarm_type, severity, trigger_time, is_active, description)
        VALUES (?,?,?,?,1,?)
    """, (cell_id, atype, severity, now, f"{atype} on cell {cell_id} at {now[:16]}"))
    if verbose: print(f"  [ALARM] {severity.upper()} — {atype} on cell {cell_id}")


def simulate_alarm_clear(verbose=False):
    if random.random() > P_ALARM_CLEAR:
        return
    alarms  = sc_query("SELECT alarm_id, severity FROM network_alarms WHERE is_active=1 ORDER BY RANDOM() LIMIT 5")
    cleared = 0
    now     = datetime.now().isoformat()
    for alarm_id, severity in alarms:
        prob = {"warning": 0.5, "minor": 0.3, "major": 0.1, "critical": 0.02}.get(severity, 0.2)
        if random.random() < prob:
            sc_write("UPDATE network_alarms SET is_active=0, clear_time=? WHERE alarm_id=?", (now, alarm_id))
            cleared += 1
    if verbose and cleared: print(f"  [ALARM] Cleared {cleared} alarms")


def simulate_technology_migration(verbose=False):
    if random.random() > P_TECH_MIGRATION:
        return
    subs = sc_query("""
        SELECT st.msisdn FROM subscriber_technology st
        JOIN devices d ON st.msisdn=d.msisdn
        WHERE st.current_technology='3G' AND d.max_technology IN ('4G','5G')
        ORDER BY RANDOM() LIMIT ?
    """, (MIGRATION_SUBS_PER_TICK,))
    migrated = 0
    for (msisdn,) in subs:
        cell = sc_query("""
            SELECT c.cell_id FROM cells c JOIN coverage cv ON c.cell_id=cv.cell_id
            WHERE cv.msisdn=? AND c.technology='4G' AND c.is_active=1 LIMIT 1
        """, (msisdn,))
        sc_write("""
            UPDATE subscriber_technology
            SET current_technology='4G',
                current_cell_id=COALESCE(?,current_cell_id),
                last_seen_date=?
            WHERE msisdn=?
        """, (cell[0][0] if cell else None, datetime.now().strftime("%Y-%m-%d"), msisdn))
        migrated += 1
    if verbose and migrated: print(f"  [MIGRATION] {migrated} subscribers migrated 3G→4G")


def simulate_volte_activation(verbose=False):
    subs = sc_query("""
        SELECT st.msisdn FROM subscriber_technology st JOIN devices d ON st.msisdn=d.msisdn
        WHERE d.volte_capable=1 AND st.volte_active=0 AND st.current_technology='4G'
        ORDER BY RANDOM() LIMIT 5
    """)
    activated = 0
    for (msisdn,) in subs:
        if random.random() < 0.3:
            sc_write("UPDATE subscriber_technology SET volte_active=1 WHERE msisdn=?", (msisdn,))
            activated += 1
    if verbose and activated: print(f"  [VOLTE] {activated} subscribers got VoLTE activated")


def simulate_dou_update(verbose=False):
    month = datetime.now().strftime("%Y-%m")
    subs  = sc_query(
        "SELECT msisdn FROM dou_monthly WHERE month=? ORDER BY RANDOM() LIMIT ?",
        (month, DOU_SUBS_PER_TICK)
    )
    updated = 0
    for (msisdn,) in subs:
        sc_write("""
            UPDATE dou_monthly SET total_data_gb=total_data_gb+?, days_active=MIN(days_active+1,31)
            WHERE msisdn=? AND month=?
        """, (round(random.uniform(0.05, 0.8), 3), msisdn, month))
        updated += 1
    if verbose: print(f"  [DOU] Updated usage for {updated} subscribers")


def simulate_offer_acceptance(verbose=False):
    if random.random() > P_OFFER_ACCEPT:
        return
    pending = op_query("""
        SELECT msisdn, offer_id FROM offer_assignments WHERE accepted=0
        ORDER BY RANDOM() LIMIT ?
    """, (OFFER_ACCEPT_PER_TICK,))
    accepted = rejected = 0
    now = datetime.now().isoformat()
    for msisdn, offer_id in pending:
        offer = op_query("SELECT discount_pct, target_campaign FROM offers WHERE offer_id=?", (offer_id,))
        discount = offer[0][0] if offer else 10
        prob = min(0.15 + (discount / 100) * 0.6, 0.75)
        if random.random() < prob:
            op_write("UPDATE offer_assignments SET accepted=1, accepted_date=? WHERE msisdn=? AND offer_id=?",
                     (now, msisdn, offer_id))
            campaign = offer[0][1] if offer else ""
            if "5G" in campaign.upper():
                _upgrade_plan(msisdn, supports_5g=1)
            elif "FWA" in campaign.upper():
                _upgrade_plan(msisdn, is_fwa=1)
            accepted += 1
        else:
            rejected += 1
    if verbose and accepted: print(f"  [OFFERS] {accepted} accepted, {rejected} rejected")


def _upgrade_plan(msisdn, supports_5g=0, is_fwa=0):
    plan = op_query(
        "SELECT plan_id FROM plans WHERE supports_5g=1 ORDER BY monthly_price LIMIT 1"
    ) if supports_5g else op_query(
        "SELECT plan_id FROM plans WHERE is_fwa_plan=1 ORDER BY monthly_price LIMIT 1"
    ) if is_fwa else []
    if not plan:
        return
    now = datetime.now().strftime("%Y-%m-%d")
    op_write("UPDATE subscriptions SET is_current=0, end_date=? WHERE msisdn=? AND is_current=1", (now, msisdn))
    op_write("INSERT OR IGNORE INTO subscriptions (msisdn, plan_id, start_date, is_current, contract_type, auto_renewal) VALUES (?,?,?,1,'monthly',1)",
             (msisdn, plan[0][0], now))
    if supports_5g:
        sc_write("UPDATE subscriber_technology SET current_technology='5G' WHERE msisdn=?", (msisdn,))


def simulate_churn(verbose=False):
    if random.random() > P_CHURN:
        return
    subs = op_query("""
        SELECT cv.msisdn FROM customer_value cv
        WHERE cv.value_segment='bronze' AND cv.month=(SELECT MAX(month) FROM customer_value)
        AND cv.msisdn IN (SELECT msisdn FROM customers WHERE is_active=1)
        ORDER BY cv.arpu ASC, RANDOM() LIMIT 2
    """)
    now = datetime.now().strftime("%Y-%m-%d")
    for (msisdn,) in subs:
        op_write("UPDATE customers SET is_active=0 WHERE msisdn=?", (msisdn,))
        sc_write("UPDATE subscribers SET is_active=0 WHERE msisdn=?", (msisdn,))
        op_write("UPDATE subscriptions SET is_current=0, end_date=? WHERE msisdn=? AND is_current=1", (now, msisdn))
    if verbose and subs: print(f"  [CHURN] {len(subs)} subscribers deactivated")


_REGIONS = [
    # (region, city, area_code, nation)
    ("Agna Qel'a","Agna Qel'a","N","Water Tribe"),
    ("Sanikiluaq","Sanikiluaq","N","Water Tribe"),
    ("Southern Water Tribe Capital","Patola","N","Water Tribe"),
    ("FumaBu Town","FumaBu","N","Water Tribe"),
    ("Fire Nation Capital","Caldera","S","Fire Nation"),
    ("The Boiling Rock","Boiling Rock","S","Fire Nation"),
    ("Island of the Sun Warriors","Sun Warriors","S","Fire Nation"),
    ("Ember Island","Ember Island","S","Fire Nation"),
    ("Shuhon Island","Shuhon","S","Fire Nation"),
    ("Sunset City","Sunset City","S","Fire Nation"),
    ("Crescent Island","Crescent","S","Fire Nation"),
    ("Ba Sing Se","Ba Sing Se","T","Earth Kingdom"),
    ("Omashu","Omashu","C","Earth Kingdom"),
    ("Gaoling","Gaoling","C","Earth Kingdom"),
    ("Serpent's Pass","Serpent's Pass","C","Earth Kingdom"),
    ("Si Wong Desert","Si Wong","C","Earth Kingdom"),
    ("HeiBai's Forest","HeiBai","C","Earth Kingdom"),
    ("Sandlocke City","Sandlocke","C","Earth Kingdom"),
    ("Gaipan Village","Gaipan","C","Earth Kingdom"),
    ("Kyoshi Island","Kyoshi","C","Earth Kingdom"),
    ("Northern Air Temple","N. Air Temple","N","Air Nomads"),
    ("Western Air Temple","W. Air Temple","C","Air Nomads"),
    ("Eastern Air Temple","E. Air Temple","C","Air Nomads"),
    ("Southern Air Temple","S. Air Temple","N","Air Nomads"),
]

_NAMES_BY_NATION = {
    "Earth Kingdom": (
        ["Toph","Bumi","Haru","Jet","Kuei","Long","Xin","Song","Pao","Yung","Wei","Jin","Lee",
         "Liling","Fong","Shao","Zhen","Bo","Chan","Dao","Fan","Guo","Han","Hua","Jian","Kai",
         "Lan","Min","Ping","Mei","Feng","Hong","Jing","Kang","Lu","Nuo"],
        ["Beifong","Li","Chen","Fong","Zhang","Xin","Long","Yao","Han","Hua","Jian","Kai","Lan",
         "Min","Ping","Qian","Shan","Tan","Wan","Xiao","Yan","Zhi","Bao","Cai","Deng","Fu","Gao"],
    ),
    "Fire Nation": (
        ["Zuko","Azula","Iroh","Mai","Zhao","Jeong","Piandao","Kori","Ryo","Naomi","Kei","Sora",
         "Taro","Yuki","Hiro","Ren","Sae","Taka","Mako","Nori","Koji","Rei","Saki","Tomo","Hana",
         "Ito","Jiro","Kenji","Liko","Masa","Nami","Osamu","Riku","Sho","Toru"],
        ["Zhao","Piandao","Shoji","Ukano","Sato","Tanaka","Yamamoto","Ito","Watanabe","Kobayashi",
         "Nakamura","Suzuki","Kato","Yoshida","Yamada","Sasaki","Abe","Mori","Hayashi","Kimura"],
    ),
    "Water Tribe": (
        ["Katara","Sokka","Hakoda","Pakku","Yue","Arnook","Hama","Bato","Kya","Siku","Nauja",
         "Tulok","Aklaq","Amaruq","Imiq","Kalluk","Nanuq","Taqtu","Ulva","Pinga","Sedna","Iqaluk"],
        ["Hakoda","Arnook","Pakku","Tonraq","Malina","Tulok","Siku","Nauja","Pinga","Kuruk","Nanuq"],
    ),
    "Air Nomads": (
        ["Aang","Gyatso","Tenzin","Pema","Sonam","Karma","Dorje","Pemba","Tashi","Wangmo","Dawa",
         "Jigme","Kelsang","Lobsang","Nyima","Palden","Rigzin","Sherab","Thubten","Tsering","Ugyen"],
        ["Gyatso","Tenzin","Pasang","Sonam","Karma","Dorje","Pemba","Tashi","Kelsang","Lobsang",
         "Wangchuk","Yeshe","Zangpo","Choden","Namgyal","Rinzin"],
    ),
}
_FIRST_NAMES = [
    # Earth Kingdom
    "Toph","Bumi","Haru","Jet","Kuei","Long","Xin","Song","Pao","Yung","Wei","Jin","Lee","Liling",
    "Fong","Shao","Zhen","Bo","Chan","Dao","Fan","Guo","Han","Hua","Jian","Kai","Lan","Min","Ping",
    # Fire Nation
    "Zuko","Azula","Iroh","Mai","Zhao","Jeong","Piandao","Kori","Ryo","Naomi","Kei","Sora","Taro",
    "Yuki","Hiro","Ren","Sae","Taka","Mako","Nori","Koji","Rei","Saki","Tomo","Hana",
    # Water Tribe
    "Katara","Sokka","Hakoda","Pakku","Yue","Arnook","Hama","Bato","Kya","Siku","Nauja","Tulok",
    "Aklaq","Amaruq","Imiq","Kalluk","Nanuq","Taqtu","Ulva",
    # Air Nomads
    "Aang","Gyatso","Tenzin","Pema","Sonam","Karma","Dorje","Pemba","Tashi","Wangmo","Dawa","Jigme",
    "Kelsang","Lobsang","Nyima","Palden","Rigzin","Sherab","Thubten","Tsering",
]
_LAST_NAMES = [
    # Earth Kingdom
    "Beifong","Li","Chen","Fong","Zhang","Xin","Long","Yao","Han","Hua","Jian","Kai","Lan","Min",
    "Ping","Qian","Shan","Tan","Wan","Xiao","Yan","Zhi","Bao","Cai","Deng","Fu","Gao",
    # Fire Nation
    "Zhao","Piandao","Shoji","Ukano","Sato","Tanaka","Yamamoto","Ito","Watanabe","Kobayashi",
    "Nakamura","Suzuki","Kato","Yoshida","Yamada","Sasaki",
    # Water Tribe
    "Hakoda","Arnook","Pakku","Tonraq","Malina","Tulok","Siku","Nauja","Pinga",
    # Air Nomads
    "Gyatso","Tenzin","Pasang","Sonam","Karma","Dorje","Pemba","Tashi","Kelsang","Lobsang",
]
_BRANDS      = ["Samsung","Apple","Huawei","Xiaomi","Oppo","Nokia","Other"]
_MAX_TECH    = ["3G","4G","4G","4G","5G","5G"]

def _gen_msisdn():
    """Generate a unique 10-digit MSISDN not already in DB."""
    for _ in range(20):
        msisdn = f"999{random.randint(20000000,99999999)}"
        exists = sc_query("SELECT 1 FROM subscribers WHERE msisdn=?", (msisdn,))
        if not exists:
            return msisdn
    return None

def _insert_new_subscriber(verbose=False):
    """Create a fully-formed new subscriber across all tables."""
    msisdn = _gen_msisdn()
    if not msisdn:
        return False

    now   = datetime.now().strftime("%Y-%m-%d")
    month = datetime.now().strftime("%Y-%m")
    region, city, area_code, nation = random.choice(_REGIONS)

    # Fictional coordinates (Avatar world pixel space mapped to lat/lon range)
    lat = round(random.uniform(10.0, 60.0), 6)
    lon = round(random.uniform(60.0, 140.0), 6)

    imsi  = str(random.randint(10**12, 10**13 - 1))
    imei  = str(random.randint(10**14, 10**15 - 1))
    brand = random.choice(_BRANDS)
    max_t = random.choice(_MAX_TECH)
    volte = 1 if max_t in ("4G","5G") and random.random() < 0.6 else 0
    is_5g = 1 if max_t == "5G" else 0

    tech  = random.choices(["3G","4G","5G"], weights=[0.20, 0.55, 0.25])[0]
    if tech == "5G" and max_t != "5G": tech = "4G"
    if tech == "4G" and max_t == "3G": tech = "3G"

    # Get a valid cell_id for the subscriber's technology
    cell = sc_query("SELECT cell_id FROM cells WHERE technology=? AND is_active=1 ORDER BY RANDOM() LIMIT 1", (tech,))
    cell_id = cell[0][0] if cell else None

    # ── NetworkAnalyzer DB ─────────────────────────────────────────────────────
    sc_write("INSERT OR IGNORE INTO subscribers VALUES (?,?,?,1,?,?,?,?,?,?)",
             (msisdn, imsi, "SIM", now, region, city, lat, lon, area_code))
    sc_write("INSERT OR IGNORE INTO subscriber_technology VALUES (?,?,?,?,?,?)",
             (msisdn, tech, cell_id, 0, 0, now))
    sc_write("INSERT OR IGNORE INTO devices VALUES (?,?,?,?,?,?,?,?,?,?)",
             (msisdn, imei, brand, "Smartphone", max_t, volte, 0, is_5g, "Android", "13"))
    sc_write("INSERT OR IGNORE INTO dou_monthly VALUES (?,?,?,?,?,?)",
             (msisdn, month, 0.0, 0, None, 0))

    # ── Operator DB ──────────────────────────────────────────────────────
    _npool = _NAMES_BY_NATION.get(nation, (_FIRST_NAMES, _LAST_NAMES))
    fname = random.choice(_npool[0])
    lname = random.choice(_npool[1])
    gender = random.choice(["M","F"])
    dob    = f"{random.randint(1970,2003)}-{random.randint(1,12):02d}-{random.randint(1,28):02d}"
    email  = f"{fname.lower().replace(' ','')}.{lname.lower().replace(' ','')}{random.randint(1,999)}@avatar.net"
    seg    = random.choices(["prepaid","postpaid"], weights=[0.6, 0.4])[0]
    nat_id = f"AV{random.randint(10000000, 99999999)}"

    op_write("INSERT OR IGNORE INTO customers VALUES (?,?,?,?,?,?,?,?,?,?,?,1)",
             (msisdn, f"{fname} {lname}", nat_id, dob, gender, email,
              f"{city} Street {random.randint(1,999)}", region, city, seg, now))

    plan = op_query("SELECT plan_id FROM plans ORDER BY RANDOM() LIMIT 1")
    if plan:
        op_write("INSERT OR IGNORE INTO subscriptions (msisdn, plan_id, start_date, is_current, contract_type, auto_renewal) VALUES (?,?,?,1,'monthly',1)",
                 (msisdn, plan[0][0], now))

    arpu = round(random.uniform(5, 150), 2)
    if arpu >= 80:   vseg = "platinum"; hvc = 1
    elif arpu >= 45: vseg = "gold";     hvc = 1
    elif arpu >= 25: vseg = "silver";   hvc = 0
    else:            vseg = "bronze";   hvc = 0
    # Insert current month + 11 historical months so backfill never sees gaps for this subscriber
    _hist_months = [(datetime.strptime(month, "%Y-%m") - timedelta(days=30 * i)).strftime("%Y-%m")
                    for i in range(12)]
    op_write_many("INSERT OR IGNORE INTO customer_value VALUES (?,?,?,?,?)",
                  [(msisdn, m, round(arpu * random.uniform(0.85, 1.15), 2), vseg, hvc)
                   for m in _hist_months])

    if verbose: print(f"  [NEW SUB] {msisdn} — {region}, {tech}, {brand}, ARPU {arpu}")
    return True


def simulate_new_activation(verbose=False):
    if random.random() > P_NEW_SUB:
        return
    inactive = sc_query("SELECT msisdn FROM subscribers WHERE is_active=0 ORDER BY RANDOM() LIMIT 1")
    if inactive:
        # Reactivate an existing inactive subscriber
        msisdn = inactive[0][0]
        now    = datetime.now().strftime("%Y-%m-%d")
        sc_write("UPDATE subscribers SET is_active=1 WHERE msisdn=?", (msisdn,))
        op_write("UPDATE customers SET is_active=1 WHERE msisdn=?", (msisdn,))
        plan = op_query("SELECT plan_id FROM plans ORDER BY RANDOM() LIMIT 1")
        if plan:
            op_write("UPDATE subscriptions SET is_current=0 WHERE msisdn=?", (msisdn,))
            op_write("INSERT OR IGNORE INTO subscriptions (msisdn, plan_id, start_date, is_current, contract_type, auto_renewal) VALUES (?,?,?,1,'monthly',1)",
                     (msisdn, plan[0][0], now))
        tech = random.choices(["2G","3G","4G","5G"], weights=[0.05, 0.15, 0.60, 0.20])[0]
        sc_write("UPDATE subscriber_technology SET current_technology=?, last_seen_date=? WHERE msisdn=?",
                 (tech, now, msisdn))
        if verbose: print(f"  [ACTIVATION] Reactivated {msisdn} on {tech}")
    else:
        # Pool fully active — insert a brand new subscriber
        _insert_new_subscriber(verbose)


def simulate_congestion_incident(verbose=False):
    if random.random() > 0.04:
        return
    congested = sc_query("""
        SELECT k.cell_id, s.region FROM kpis_daily k
        JOIN cells c ON k.cell_id=c.cell_id JOIN sites s ON c.site_id=s.site_id
        WHERE k.congestion_level='high' AND k.date=(SELECT MAX(date) FROM kpis_daily)
        ORDER BY RANDOM() LIMIT 1
    """)
    if not congested:
        return
    cell_id, region = congested[0]
    affected = random.randint(50, 400)
    sc_write("""
        INSERT INTO network_incidents
            (cell_id, incident_type, start_time, affected_users, severity, root_cause, resolved)
        VALUES (?,?,?,?,'major','High traffic congestion',0)
    """, (cell_id, "Congestion", datetime.now().isoformat(), affected))
    if verbose: print(f"  [INCIDENT] Congestion — {region}, {affected} users")


def simulate_campaign_progress(verbose=False):
    campaigns = op_query("SELECT campaign_id FROM campaigns WHERE status='active' ORDER BY RANDOM() LIMIT 3")
    for (campaign_id,) in campaigns:
        op_write("""
            UPDATE campaign_targets SET converted=1
            WHERE campaign_id=? AND converted=0
            AND msisdn IN (
                SELECT msisdn FROM campaign_targets WHERE campaign_id=? AND converted=0
                ORDER BY RANDOM() LIMIT 3
            )
        """, (campaign_id, campaign_id))
        counts = op_query("SELECT COUNT(*), SUM(converted) FROM campaign_targets WHERE campaign_id=?", (campaign_id,))
        if counts and counts[0][0] > 0 and (counts[0][1] or 0) / counts[0][0] > 0.8:
            op_write("UPDATE campaigns SET status='completed' WHERE campaign_id=?", (campaign_id,))
            if verbose: print(f"  [CAMPAIGN] Campaign {campaign_id} completed")


# ═══════════════════════════════════════════════════════════════════════
# STATUS
# ═══════════════════════════════════════════════════════════════════════

def print_status():
    now          = datetime.now().strftime("%H:%M:%S")
    active_subs  = sc_query("SELECT COUNT(*) FROM subscribers WHERE is_active=1")[0][0]
    critical     = sc_query("SELECT COUNT(*) FROM network_alarms WHERE is_active=1 AND severity='critical'")[0][0]
    total_alarms = sc_query("SELECT COUNT(*) FROM network_alarms WHERE is_active=1")[0][0]
    on_3g        = sc_query("SELECT COUNT(*) FROM subscriber_technology WHERE current_technology='3G'")[0][0]
    on_4g        = sc_query("SELECT COUNT(*) FROM subscriber_technology WHERE current_technology='4G'")[0][0]
    on_5g        = sc_query("SELECT COUNT(*) FROM subscriber_technology WHERE current_technology='5G'")[0][0]
    active_camps = op_query("SELECT COUNT(*) FROM campaigns WHERE status='active'")[0][0]
    avg_arpu     = op_query("SELECT ROUND(AVG(arpu),2) FROM customer_value WHERE month=(SELECT MAX(month) FROM customer_value)")[0][0]
    poor_qoe     = sc_query("SELECT COUNT(DISTINCT msisdn) FROM qoe_daily WHERE experience_label='poor' AND date=date('now','-1 day')")[0][0]

    print(f"\n  [{now}] ── LIVE STATUS ──────────────────────────────")
    print(f"  Subscribers : {active_subs:,} active")
    print(f"  Technology  : 3G={on_3g:,}  4G={on_4g:,}  5G={on_5g:,}")
    print(f"  Alarms      : {total_alarms} active ({critical} critical)")
    print(f"  Campaigns   : {active_camps} active")
    print(f"  Avg ARPU    : {avg_arpu} Yuan")
    print(f"  Poor QoE    : {poor_qoe:,} users yesterday")
    print(f"  ────────────────────────────────────────────────────")


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fast",          action="store_true", help="5x faster simulation")
    parser.add_argument("--verbose",       action="store_true", help="Print every change")
    parser.add_argument("--backfill-only", action="store_true", help="Backfill then exit")
    parser.add_argument("--no-backfill",   action="store_true", help="Skip backfill, jump straight to live loop")
    args = parser.parse_args()

    tick = TICK_SECONDS / FAST_MULTIPLIER if args.fast else TICK_SECONDS
    mode = "FAST MODE" if args.fast else "NORMAL MODE"

    print(f"""
╔══════════════════════════════════════════════╗
║       NetworkAnalyzer DB SIMULATOR                 ║
║  {mode:<44}║
║  Tick: {tick:.0f}s  |  Ctrl+C to stop              ║
╚══════════════════════════════════════════════╝""")

    # ── Ensure new tables exist ─────────────────────────────────────────
    _ensure_new_tables()

    # ── Backfill historical data ────────────────────────────────────────
    if not args.no_backfill:
        run_backfill(args.verbose)

    if args.backfill_only:
        print("\n  --backfill-only: done.")
        return

    # ── Ensure today is covered ─────────────────────────────────────────
    ensure_today_kpis(verbose=True)
    ensure_today_qoe(verbose=True)
    ensure_current_month(verbose=True)

    print_status()

    tick_count = 0
    while True:
        try:
            tick_count += 1

            # Every tick
            simulate_kpi_fluctuation(args.verbose)
            simulate_alarm_trigger(args.verbose)
            simulate_alarm_clear(args.verbose)

            # Every 2 ticks
            if tick_count % 2 == 0:
                simulate_dou_update(args.verbose)
                simulate_offer_acceptance(args.verbose)
                simulate_qoe_update(args.verbose)
                simulate_new_table_tick(args.verbose)

            # Every 3 ticks
            if tick_count % 3 == 0:
                simulate_technology_migration(args.verbose)
                simulate_volte_activation(args.verbose)
                simulate_congestion_incident(args.verbose)

            # Every 5 ticks
            if tick_count % 5 == 0:
                simulate_churn(args.verbose)
                simulate_new_activation(args.verbose)
                simulate_campaign_progress(args.verbose)
                simulate_arpu_update(args.verbose)
                simulate_voice_update(args.verbose)

            # Every 10 ticks — ensure today still covered (handles midnight rollover)
            if tick_count % 10 == 0:
                ensure_today_kpis()
                ensure_today_qoe()
                ensure_current_month()

            # Every 20 ticks — status snapshot
            if tick_count % 20 == 0:
                print_status()

            time.sleep(tick)

        except KeyboardInterrupt:
            print("\n\n  Simulator stopped. DB state preserved.")
            print_status()
            break
        except Exception as e:
            print(f"  [ERROR] {e} — continuing...")
            time.sleep(tick)


if __name__ == "__main__":
    main()
