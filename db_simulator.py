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

# How an ACTIVE alarm degrades its own cell's KPIs. Multipliers apply to the healthy
# baseline: <1 shrinks (throughput, availability), >1 grows (drop rate, latency);
# rsrp_delta is an offset in dBm and sinr is a multiplier. Each fault type degrades
# what it would actually degrade — a backhaul failure kills throughput and latency,
# interference wrecks SINR, a power issue takes availability down.
ALARM_KPI_IMPACT = {
    "Congestion":        dict(dl=0.45, ul=0.55, drop=2.2, lat=2.4, avail=0.995, sinr=0.75, rsrp_delta=0,   cong="high"),
    "Backhaul Failure":  dict(dl=0.30, ul=0.35, drop=2.6, lat=3.2, avail=0.970, sinr=0.95, rsrp_delta=0,   cong="high"),
    "High Interference": dict(dl=0.65, ul=0.70, drop=3.0, lat=1.5, avail=0.990, sinr=0.45, rsrp_delta=-4,  cong="medium"),
    "Coverage Hole":     dict(dl=0.55, ul=0.60, drop=2.8, lat=1.6, avail=0.985, sinr=0.55, rsrp_delta=-12, cong="medium"),
    "Hardware Fault":    dict(dl=0.40, ul=0.45, drop=3.5, lat=2.0, avail=0.920, sinr=0.80, rsrp_delta=-3,  cong="medium"),
    "Power Issue":       dict(dl=0.25, ul=0.30, drop=4.0, lat=2.2, avail=0.850, sinr=0.85, rsrp_delta=-6,  cong="high"),
}
# A warning barely moves the needle; a critical one bites at full strength.
ALARM_SEVERITY_BITE = {"critical": 1.0, "major": 0.6, "minor": 0.3, "warning": 0.15}

# Which customer-facing incident a major/critical alarm provokes. Causes and services
# are reused verbatim from _INCIDENT_SCENARIOS so the agent's existing filters
# ("streaming issues", "voice calls") keep matching whichever path created the row.
ALARM_TO_INCIDENT = {
    "Congestion":        ("Cell congestion",       "video streaming"),
    "Backhaul Failure":  ("Backhaul degradation",  "mobile data"),
    "High Interference": ("Signal interference",   "voice calls"),
    "Coverage Hole":     ("Signal interference",   "voice calls"),
    "Hardware Fault":    ("Site outage",           "all services"),
    "Power Issue":       ("Power failure at site", "all services"),
}

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

def _is_retryable(err):
    """A locked/busy DB is worth retrying; a malformed statement is not — it fails
    identically on all three attempts and then vanishes. That is exactly how
    simulate_kpi_fluctuation ran for months reporting success while writing nothing:
    its ON CONFLICT target had no matching UNIQUE constraint, and the error was
    swallowed here. Real SQL errors now get printed."""
    msg = str(err).lower()
    return "locked" in msg or "busy" in msg


def sc_write(sql, params=()):
    for attempt in range(3):
        try:
            conn = sc_conn()
            conn.execute(sql, params)
            conn.commit()
            conn.close()
            return True
        except sqlite3.OperationalError as e:
            if not _is_retryable(e):
                print(f"  [SQL ERROR] {e} :: {' '.join(sql.split())[:90]}")
                return False
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
        except sqlite3.OperationalError as e:
            if not _is_retryable(e):
                print(f"  [SQL ERROR] {e} :: {' '.join(sql.split())[:90]}")
                return False
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
            except sqlite3.OperationalError as e:
                if not _is_retryable(e):
                    print(f"  [SQL ERROR] {e} :: {' '.join(sql.split())[:90]}")
                    break
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
            except sqlite3.OperationalError as e:
                if not _is_retryable(e):
                    print(f"  [SQL ERROR] {e} :: {' '.join(sql.split())[:90]}")
                    break
                time.sleep(0.5 * (attempt + 1))

# ═══════════════════════════════════════════════════════════════════════
# KPI GENERATION HELPERS
# ═══════════════════════════════════════════════════════════════════════

# ── Customer value segmentation ──────────────────────────────────────────
# Segments are PERCENTILE BANDS of the live ARPU distribution, never fixed Yuan
# amounts. The old hardcoded cutoffs (arpu>=80 -> platinum, >=45 -> gold, both
# flagged HVC) were written for a base whose ARPU sat around 30-50. update_arpu.py
# later rebuilt ARPU on a city-tier model with a median near 107, and nothing
# revisited the cutoffs — so the median customer cleared the platinum bar and
# 88.5% of the base came out "high value", which makes the label mean nothing.
#
# Reading the bands off the live distribution instead means the shape holds no
# matter how ARPU is rescaled later, and a subscriber's status is re-derived from
# their current numbers rather than frozen at whatever it was when they were created.
SEGMENT_BANDS = [            # (segment, percentile floor), richest first
    ("platinum", 90),
    ("gold",     70),
    ("silver",   30),
    ("bronze",    0),
]
HVC_SEGMENTS = frozenset(("platinum",))   # top 10% of the base

_thresholds_cache = {}       # month -> {segment: arpu floor}

def value_thresholds(month=None, refresh=False):
    """ARPU floor per segment, read off the live distribution for `month`.
    Cached per month — this is 4 ordered lookups, not something to run per row."""
    month = month or datetime.now().strftime("%Y-%m")
    if not refresh and month in _thresholds_cache:
        return _thresholds_cache[month]
    total = op_query("SELECT COUNT(*) FROM customer_value WHERE month=?", (month,))
    n = total[0][0] if total else 0
    if not n:
        return {seg: 0.0 for seg, _ in SEGMENT_BANDS}   # no data yet: everyone bronze
    out = {}
    for seg, pct in SEGMENT_BANDS:
        if pct <= 0:
            out[seg] = 0.0
            continue
        row = op_query("SELECT arpu FROM customer_value WHERE month=? ORDER BY arpu "
                       "LIMIT 1 OFFSET ?", (month, min(n - 1, int(n * pct / 100))))
        out[seg] = row[0][0] if row else 0.0
    _thresholds_cache[month] = out
    return out


def classify_value(arpu, month=None, thresholds=None):
    """(value_segment, is_hvc) for an ARPU, against the CURRENT distribution.
    Single source of truth — every place that writes customer_value goes through
    here, so the definition can never drift between call sites again."""
    th = thresholds if thresholds is not None else value_thresholds(month)
    for seg, _pct in SEGMENT_BANDS:
        if arpu >= th.get(seg, 0.0):
            return seg, (1 if seg in HVC_SEGMENTS else 0)
    return "bronze", 0


TECH_BASE = {
    "2G": dict(rsrp=-95, sinr=8,  dl=2,   ul=0.5, drop=1.8, avail=98.5, lat=300),
    "3G": dict(rsrp=-90, sinr=11, dl=8,   ul=2,   drop=1.2, avail=99.0, lat=120),
    "4G": dict(rsrp=-82, sinr=16, dl=45,  ul=12,  drop=0.6, avail=99.5, lat=35),
    "5G": dict(rsrp=-78, sinr=22, dl=180, ul=45,  drop=0.3, avail=99.8, lat=12),
}

def _gen_kpi(cell_id, technology, date_str, alarm=None):
    """Generate one cell-day of KPIs. `alarm` is an (alarm_type, severity) tuple for an
    ACTIVE alarm on this cell; when present the fault is the cause of the numbers rather
    than an unrelated row in another table, so a cell carrying a critical Power Issue
    actually reads as broken."""
    base = TECH_BASE.get(technology, dict(rsrp=-88, sinr=13, dl=30, ul=8, drop=0.8, avail=99.2, lat=50))
    congestion_spike = random.random() < 0.08
    fault_event      = random.random() < 0.03
    dl  = base["dl"]    * random.uniform(0.6 if congestion_spike else 0.85, 1.15)
    ul  = base["ul"]    * random.uniform(0.7, 1.1)
    drop = base["drop"] * random.uniform(1.5 if congestion_spike else 0.8, 2.5 if congestion_spike else 1.2)
    avail = base["avail"] * random.uniform(0.95 if fault_event else 0.999, 1.0)
    lat  = base["lat"]  * random.uniform(1.0, 2.2 if congestion_spike else 1.1)
    cong = "high" if congestion_spike else ("medium" if dl < base["dl"] * 0.75 else "low")
    rsrp = base["rsrp"] + random.gauss(0, 3)
    sinr = base["sinr"] + random.gauss(0, 2)

    impact = ALARM_KPI_IMPACT.get(alarm[0]) if alarm else None
    if impact:
        # Blend toward the fault's full impact by severity: 1 + (mult-1)*bite.
        bite = ALARM_SEVERITY_BITE.get(alarm[1], 0.3)
        scale = lambda key: 1 + (impact[key] - 1) * bite
        dl    *= scale("dl")
        ul    *= scale("ul")
        drop  *= scale("drop")
        lat   *= scale("lat")
        avail *= scale("avail")
        sinr  *= scale("sinr")
        rsrp  += impact["rsrp_delta"] * bite
        if bite >= 0.5:                      # major/critical dominate the congestion label
            cong = impact["cong"]

    return (
        cell_id, date_str,
        round(rsrp, 2),
        round(sinr, 2),
        round(max(dl, 0.05), 2), round(max(ul, 0.02), 2),
        round(min(drop, 100.0), 3), round(max(min(avail, 100.0), 0.0), 3),
        round(lat, 1), cong,
        random.randint(10, 120)
    )


_KPI_UPSERT = """
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
"""

_TCA_FALLBACK_BASE = dict(rsrp=-88, sinr=13, dl=30, ul=8, drop=0.8, avail=99.2, lat=50)

def _detect_threshold_alarm(technology, row):
    """The REVERSE coupling: read a degraded cell-day and decide which alarm it should
    raise. Equipment faults (power, hardware, backhaul) announce themselves and then
    degrade the cell — that is simulate_alarm_trigger. Performance faults have no
    equipment to raise a flag; they are DETECTED by watching counters cross a threshold,
    which is what a real NMS calls a threshold-crossing alert.

    Every threshold is RELATIVE to the technology baseline, never absolute: a 1.9% drop
    rate is a broken 5G cell and a completely ordinary 2G one. Checks run worst-first and
    return at most one alarm, so a badly degraded cell raises the fault that explains it
    rather than one of each. Returns (alarm_type, severity) or None."""
    base = TECH_BASE.get(technology, _TCA_FALLBACK_BASE)
    _cid, _date, rsrp, sinr, dl, _ul, _drop, avail, lat, cong, _users = row

    def sev(excess):
        """How far past the threshold, as a ratio — 45% over is critical."""
        return "critical" if excess >= 0.45 else ("major" if excess >= 0.25 else "minor")

    # Order matters, and it is SIGNATURE-FIRST, not severity-first. Every fault dents
    # availability a little, so checking availability early misattributes almost
    # everything as a power issue. Each metric below is checked by the fault that owns
    # it: only a coverage hole moves RSRP that far, only interference collapses SINR
    # while RSRP holds, only a backhaul problem triples latency, and only an equipment
    # failure takes real availability out.
    if rsrp < base["rsrp"] - 10:
        return "Coverage Hole", sev((base["rsrp"] - 10 - rsrp) / 8)
    if sinr < base["sinr"] * 0.55:
        return "High Interference", sev(1 - sinr / (base["sinr"] * 0.55))
    # 3.0x, not 2.5x: congestion itself pushes latency to ~2.4x, so a tighter bar here
    # relabels every congested cell as a transport fault.
    if lat > base["lat"] * 3.0:
        return "Backhaul Failure", sev(lat / (base["lat"] * 3.0) - 1)
    if avail < base["avail"] * 0.90:
        return "Power Issue", sev((base["avail"] * 0.90 - avail) / 6)
    if avail < base["avail"] * 0.95:
        return "Hardware Fault", sev((base["avail"] * 0.95 - avail) / 6)
    if dl < base["dl"] * 0.55:
        return "Congestion", sev(1 - dl / (base["dl"] * 0.55))
    if cong == "high":
        return "Congestion", "minor"
    return None


def _active_alarms_by_cell(cell_ids=None):
    """{cell_id: (alarm_type, severity)} for active alarms, worst severity per cell."""
    sql = ("SELECT cell_id, alarm_type, severity FROM network_alarms WHERE is_active=1")
    params = ()
    if cell_ids:
        sql += f" AND cell_id IN ({','.join('?' * len(cell_ids))})"
        params = tuple(cell_ids)
    rank = {"critical": 3, "major": 2, "minor": 1, "warning": 0}
    worst = {}
    for cid, atype, sev in sc_query(sql, params):
        if cid not in worst or rank.get(sev, 0) > rank.get(worst[cid][1], 0):
            worst[cid] = (atype, sev)
    return worst

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
    # Anchored on today, not on MAX(month) FROM customer_value: when that table froze,
    # this window froze with it and billing stopped getting new months as a side effect.
    _ref = datetime.now()
    months = [(_ref - timedelta(days=30 * i)).strftime("%Y-%m") for i in range(11, -1, -1)]

    msisdns = [r[0] for r in op_query("SELECT msisdn FROM customers WHERE is_active=1")]

    # ── customer_value ───────────────────────────────────────────────────
    existing_cv = {(r[0], r[1]) for r in op_query("SELECT msisdn, month FROM customer_value")}
    cv_rows = []
    for m in months:
        for msisdn in msisdns:
            if (msisdn, m) not in existing_cv:
                arpu = round(random.uniform(5, 150), 2)
                seg, hvc = classify_value(arpu, m)
                cv_rows.append((msisdn, m, arpu, seg, hvc))
    if cv_rows:
        print(f"  [BACKFILL] customer_value: inserting {len(cv_rows):,} rows …")
        op_write_many("INSERT OR IGNORE INTO customer_value "
                      "(msisdn, month, arpu, value_segment, is_hvc) VALUES(?,?,?,?,?)", cv_rows)

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

        -- Live service-experience incidents with a full lifecycle: each row is
        -- one issue that OPENS (status='active', started_at stamped to the minute)
        -- and later SELF-HEALS (status='resolved', resolved_at stamped). "Right
        -- now" = WHERE status='active'; the historical timeline survives forever
        -- via started_at/resolved_at. Segment/region/is_hvc are snapshotted at
        -- open time so "which HVCs have an active streaming issue" is single-table.
        CREATE TABLE IF NOT EXISTS experience_incidents (
            incident_id            INTEGER PRIMARY KEY AUTOINCREMENT,
            msisdn                 TEXT    NOT NULL,
            value_segment          TEXT,
            is_hvc                 INTEGER DEFAULT 0,
            region                 TEXT,
            affected_service       TEXT,
            root_cause             TEXT,
            severity               TEXT,
            started_at             TEXT    NOT NULL,
            expected_resolution_at TEXT,
            resolved_at            TEXT,
            status                 TEXT    DEFAULT 'active',
            duration_min           INTEGER,
            complaint_id           INTEGER,
            cell_id                TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_exp_inc_status  ON experience_incidents(status);
        CREATE INDEX IF NOT EXISTS idx_exp_inc_msisdn  ON experience_incidents(msisdn);
        CREATE INDEX IF NOT EXISTS idx_exp_inc_started ON experience_incidents(started_at);
    """)
    # cell_id — the offending cell (for flashing the site on the coverage map).
    # Guarded ALTER so DBs that already have the table (created before this column) upgrade.
    try:
        sc.execute("ALTER TABLE experience_incidents ADD COLUMN cell_id TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists

    # kpis_daily was created without a UNIQUE constraint on (cell_id, date), so every
    # "INSERT ... ON CONFLICT(cell_id, date) DO UPDATE" in the live loop failed to
    # prepare and was swallowed by the retry helpers — simulate_kpi_fluctuation reported
    # updated cells for months while writing nothing. The index makes the upserts work.
    try:
        sc.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_kpi_cell_date ON kpis_daily(cell_id, date)")
    except sqlite3.OperationalError as e:
        print(f"  [INIT] could not create idx_kpi_cell_date ({e}) — "
              f"duplicate (cell_id, date) rows must be removed first")
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
    # sms_log.inbound_text — captures the subscriber's reply to a campaign SMS (Phase 2)
    try:
        op.execute("ALTER TABLE sms_log ADD COLUMN inbound_text TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
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


# Real NPS answers pile up at the ends — people rate 0 or 10, rarely 3. Drawing a
# uniform score inside each band (which is what this used to do) produced a dead-flat
# histogram: every detractor score 0-6 landed within ~5% of the others, which no
# survey has ever looked like.
_NPS_SCORE_WEIGHTS = {
    "detractor": ([0, 1, 2, 3, 4, 5, 6], [30, 10, 10, 12, 13, 12, 13]),
    "passive":   ([7, 8],                [45, 55]),
    "promoter":  ([9, 10],               [35, 65]),
}

def _gen_nps(msisdn, month, segment, rng=None):
    """`rng` lets a caller pass a seeded Random so regeneration is reproducible."""
    rnd = rng or random
    profile = _NPS_PROFILE.get(segment, _NPS_PROFILE["bronze"])
    r = rnd.random()
    if r < profile["detractor_p"]:
        cat = "detractor"
    elif r < 1 - profile["promoter_p"]:
        cat = "passive"
    else:
        cat = "promoter"
    vals, wts = _NPS_SCORE_WEIGHTS[cat]
    return (msisdn, month, rnd.choices(vals, weights=wts)[0], cat)


def backfill_new_tables(verbose=False):
    """Fill signal_quality (daily, 30 days), and monthly tables for 12 months."""
    if verbose: print("  [BACKFILL] Starting new tables …")
    # Anchor the window on TODAY, never on a table's own MAX(month). This used to read
    # MAX(month) FROM nps_scores -- one of the tables it fills -- so once nps_scores
    # stopped advancing the window stopped with it, and it could never advance again.
    # A self-anchoring backfill freezes permanently and silently; these five monthly
    # tables sat four months stale that way.
    _ref  = datetime.now()
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

    # ── Seed historical experience incidents (mostly resolved, a few live) ──
    existing_inc = sc_query("SELECT COUNT(*) FROM experience_incidents")[0][0]
    if existing_inc < 30:
        now = datetime.now()
        # HVC pool: gold/platinum with their region snapshot
        hvc_pool = op_query("""
            SELECT msisdn, value_segment, is_hvc FROM customer_value
            WHERE month=(SELECT MAX(month) FROM customer_value)
              AND is_hvc=1
            ORDER BY RANDOM() LIMIT 200
        """)
        region_map = dict(sc_query("SELECT msisdn, region FROM subscribers"))
        cell_map   = dict(sc_query("SELECT msisdn, current_cell_id FROM subscriber_technology"))
        inc_rows = []
        for msisdn, segment, is_hvc in hvc_pool:
            if random.random() > 0.35:      # ~35% of the pool had a past incident
                continue
            cause, service, severity = random.choice(_INCIDENT_SCENARIOS)
            lo, hi  = _INCIDENT_DURATION_MIN[severity]
            dur_min = random.randint(lo, hi)
            # started sometime in the past 14 days (bias a couple to today)
            mins_ago = random.choice([random.randint(20, 240)] * 3 + [random.randint(240, 14 * 1440)])
            start    = now - timedelta(minutes=mins_ago)
            eta      = start + timedelta(minutes=dur_min)
            started_at = start.strftime("%Y-%m-%d %H:%M:%S")
            eta_str    = eta.strftime("%Y-%m-%d %H:%M:%S")
            # if the heal window already passed it's resolved; else still active
            if eta <= now:
                status, resolved_at = "resolved", eta_str
            else:
                status, resolved_at = "active", None
            inc_rows.append((msisdn, segment, is_hvc, region_map.get(msisdn), service,
                             cause, severity, started_at, eta_str, resolved_at, status, dur_min,
                             None, cell_map.get(msisdn)))
        if inc_rows:
            print(f"  [BACKFILL] experience_incidents: seeding {len(inc_rows):,} rows …")
            sc_write_many(
                """INSERT INTO experience_incidents
                   (msisdn, value_segment, is_hvc, region, affected_service, root_cause,
                    severity, started_at, expected_resolution_at, resolved_at, status,
                    duration_min, complaint_id, cell_id)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                inc_rows
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
    # No small LIMIT here: at 500 per call every 10 ticks a fresh month took ~100 ticks
    # to fill, and for four months it never caught up at all. One pass, whole month.
    missing_dou = sc_query("""
        SELECT msisdn FROM subscribers WHERE is_active=1
        AND msisdn NOT IN (SELECT msisdn FROM dou_monthly WHERE month=?)
        LIMIT 60000
    """, (month,))
    if missing_dou:
        rows = [(m[0], month, 0.0, 0, None, 0) for m in missing_dou]
        sc_write_many("INSERT OR IGNORE INTO dou_monthly VALUES(?,?,?,?,?,?)", rows)
        if verbose: print(f"  [INIT] Created {len(rows)} dou_monthly records for {month}")

    # customer_value
    missing_cv = op_query("""
        SELECT msisdn FROM customers WHERE is_active=1
        AND msisdn NOT IN (SELECT msisdn FROM customer_value WHERE month=?)
        LIMIT 60000
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
            else:
                arpu = round(random.uniform(5, 150), 2)
            # re-derived from the new ARPU, not carried over from last month:
            # a subscriber who drifts out of the top decile stops being an HVC
            seg, hvc = classify_value(arpu, month)
            cv_rows.append((msisdn, month, arpu, seg, hvc))
        op_write_many("INSERT OR IGNORE INTO customer_value "
                      "(msisdn, month, arpu, value_segment, is_hvc) VALUES(?,?,?,?,?)", cv_rows)
        if verbose: print(f"  [INIT] Created {len(cv_rows)} customer_value records for {month}")


# ═══════════════════════════════════════════════════════════════════════
# LIVE SIMULATION FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════

def simulate_kpi_fluctuation(verbose=False):
    """Update KPIs for random cells — writes today's row. Every cell currently carrying
    an active alarm is refreshed too, so a fault keeps showing in the numbers for as long
    as it is open instead of drifting back to healthy on the next random redraw."""
    cells = sc_query(
        "SELECT cell_id, technology FROM cells WHERE is_active=1 ORDER BY RANDOM() LIMIT ?",
        (KPI_CELLS_PER_TICK,)
    )
    alarmed = sc_query("""
        SELECT DISTINCT c.cell_id, c.technology FROM cells c
        JOIN network_alarms na ON na.cell_id=c.cell_id
        WHERE na.is_active=1 AND c.is_active=1
        ORDER BY RANDOM() LIMIT ?
    """, (KPI_CELLS_PER_TICK,))
    seen = set()
    todo = []
    for cid, tech in list(cells) + list(alarmed):
        if cid not in seen:
            seen.add(cid)
            todo.append((cid, tech))

    today  = datetime.now().strftime("%Y-%m-%d")
    alarms = _active_alarms_by_cell([cid for cid, _ in todo])
    for cid, tech in todo:
        sc_write(_KPI_UPSERT, _gen_kpi(cid, tech, today, alarm=alarms.get(cid)))
    if verbose:
        print(f"  [KPI] Updated {len(todo)} cells ({len(alarms)} degraded by an active alarm)")


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

    # ── Experience penalty ──────────────────────────────────────────────
    # The usage model above ignores network experience. This penalty tracks the
    # customer's LIVE experience so churn RISES while an incident is open and
    # RELAXES once it self-heals (see simulate_experience_incident /
    # simulate_incident_resolution). It's driven by ACTIVE incidents + still-OPEN
    # complaints — not by the persistent daily QoE evidence, which never clears.
    # So "service issues raised this customer's churn" is real, and so is the
    # recovery. Clipped to 1.0.
    _SEV_WEIGHT = {"critical": 0.30, "major": 0.18, "minor": 0.10}
    active_pen, open_compl = defaultdict(float), defaultdict(int)
    try:
        for m, sev, cnt in sc_conn.execute(
            f"SELECT msisdn, severity, COUNT(*) FROM experience_incidents "
            f"WHERE msisdn IN ({placeholders}) AND status='active' "
            f"GROUP BY msisdn, severity", msisdn_list):
            active_pen[m] += _SEV_WEIGHT.get(sev, 0.10) * cnt
        for m, cnt in sc_conn.execute(
            f"SELECT msisdn, COUNT(*) FROM complaints WHERE msisdn IN ({placeholders}) "
            f"AND status IN ('open','in_progress') GROUP BY msisdn", msisdn_list):
            open_compl[m] = cnt
    except Exception:
        pass

    for i, msisdn in enumerate(msisdn_list):
        s = float(scores[i])
        penalty = min(0.40, active_pen.get(msisdn, 0.0)
                            + 0.05 * open_compl.get(msisdn, 0))
        s = min(1.0, s + penalty)
        label = "high" if s >= _churn_p90 else "medium" if s >= _churn_p70 else "low"
        op_conn.execute("""
            UPDATE customer_value SET churn_risk_score=?, churn_label=?
            WHERE msisdn=? AND month=?
        """, (round(s, 4), label, msisdn, month))
    op_conn.commit()


# (root_cause, affected_service, severity, scope, weight) — the experience-issue catalog.
# affected_service is what the agent filters on ("streaming issues" -> 'video streaming').
#
# SCOPE = blast radius, and it matters: a power failure at a site does NOT take out one
# unlucky platinum customer, it takes out EVERYONE on that site. Infrastructure faults are
# 'cell'/'site' scoped and hit every active subscriber served there; only genuinely
# per-customer faults (a policy throttle, their CDN route, their VoLTE bearer) are
# 'subscriber' scoped. WEIGHT keeps site-wide outages rare, as they are in real life
# (~3% of events) while per-subscriber niggles are the common case.
_INCIDENT_SCENARIOS = [
    ("Cell congestion",         "video streaming", "major",    "cell",        6),
    ("Throughput throttling",   "video streaming", "minor",    "subscriber", 12),
    ("CDN peering degradation", "video streaming", "major",    "subscriber", 10),
    ("Backhaul degradation",    "mobile data",     "major",    "cell",        5),
    ("Packet loss spike",       "mobile data",     "minor",    "subscriber", 10),
    ("DNS resolver failure",    "mobile data",     "minor",    "subscriber",  8),
    ("Signal interference",     "voice calls",     "minor",    "cell",        4),
    ("VoLTE bearer drop",       "voice calls",     "major",    "subscriber",  8),
    ("Site outage",             "all services",    "critical", "site",        1),
    ("Power failure at site",   "all services",    "critical", "site",        1),
]

# A cell/site event writes one incident row PER AFFECTED SUBSCRIBER, so the budget counts
# EVENTS, not rows. The map draws one ring per site either way.
_MAX_NEW_EVENTS         = 3
# Cap on blast radius — a backstop, not a modelling choice. The biggest site here serves
# 472 subscribers (411 sites, median 116), so this covers every one of them OUTRIGHT: a
# power failure takes out 100% of its site, not some fraction. That matters, because the
# agent reports the ratio — a truncating cap made it narrate "25% of the site is offline",
# which is an artifact, not a fact. Safe on params: SQLite allows 32766 and
# _rescore_churn / _subscriber_snapshot bind one per affected msisdn.
_MAX_AFFECTED_PER_EVENT = 500
_COMPLAINT_RATE_BULK    = 0.12   # in a mass outage only some people actually complain

# Demo-fast self-heal windows (real wall-clock minutes) by severity. Small
# glitches clear fast; a site outage lingers — but everything resolves inside a
# session so you can watch the loop close.
_INCIDENT_DURATION_MIN = {
    "minor":    (5, 12),
    "major":    (10, 20),
    "critical": (18, 30),
}

# Which qoe_daily app rows a service degrades — keeps the QoE evidence coherent.
_SERVICE_APPS = {
    "video streaming": ("video",),
    "mobile data":     ("web", "gaming"),
    "voice calls":     ("voip",),
    "all services":    ("video", "gaming", "web", "voip"),
}

def _incident_targets(scope, active_now):
    """Who does this fault ACTUALLY hit? Infrastructure faults take out every active
    subscriber served by the cell/site; a per-subscriber fault hits one customer.
    Anyone already carrying an active incident is skipped."""
    if scope == "subscriber":
        # per-customer faults: bias to gold/platinum (that's the retention story)
        for (m,) in op_query("""
            SELECT msisdn FROM customer_value
            WHERE month=(SELECT MAX(month) FROM customer_value)
              AND is_hvc=1
            ORDER BY RANDOM() LIMIT 8
        """):
            if m not in active_now:
                return [m]
        return []

    # Pick the cell/site via a random ACTIVE SUBSCRIBER rather than at random from the
    # cell table: plenty of cells serve nobody (picking those would no-op the event), and
    # going through subscribers weights selection by population — the busy cells are the
    # ones that congest, which is what we want anyway.
    seed = sc_query("""
        SELECT st.current_cell_id FROM subscriber_technology st
        JOIN subscribers s ON st.msisdn=s.msisdn
        WHERE s.is_active=1 AND st.current_cell_id IS NOT NULL
        ORDER BY RANDOM() LIMIT 1
    """)
    if not seed:
        return []
    seed_cell = seed[0][0]

    if scope == "cell":
        rows = sc_query("""
            SELECT st.msisdn FROM subscriber_technology st
            JOIN subscribers s ON st.msisdn=s.msisdn
            WHERE st.current_cell_id=? AND s.is_active=1 LIMIT ?
        """, (seed_cell, _MAX_AFFECTED_PER_EVENT))
        return [m for (m,) in rows if m not in active_now]

    # site: everyone across every cell on that site
    rows = sc_query("""
        SELECT st.msisdn FROM subscriber_technology st
        JOIN subscribers s ON st.msisdn=s.msisdn
        JOIN cells c ON st.current_cell_id=c.cell_id
        WHERE c.site_id=(SELECT site_id FROM cells WHERE cell_id=?)
          AND s.is_active=1 LIMIT ?
    """, (seed_cell, _MAX_AFFECTED_PER_EVENT))
    return [m for (m,) in rows if m not in active_now]


def _subscriber_snapshot(msisdns):
    """region + current cell (network DB) and value_segment / is_hvc (operator DB) for
    everyone an incident hits — snapshotted onto each incident row so the UI and the
    agent can filter by HVC/region/site without a cross-DB join."""
    out = {}
    if not msisdns:
        return out
    ph = ",".join("?" * len(msisdns))
    for m, reg, cell in sc_query(
        f"SELECT s.msisdn, s.region, st.current_cell_id FROM subscribers s "
        f"JOIN subscriber_technology st ON s.msisdn=st.msisdn "
        f"WHERE s.msisdn IN ({ph})", list(msisdns)):
        out[m] = [reg, cell, None, 0]
    for m, seg, hvc in op_query(
        f"SELECT msisdn, value_segment, is_hvc FROM customer_value "
        f"WHERE month=(SELECT MAX(month) FROM customer_value) AND msisdn IN ({ph})",
        list(msisdns)):
        if m in out:
            out[m][2] = seg
            out[m][3] = hvc or 0
    return {k: tuple(v) for k, v in out.items()}


def _emit_incident_event(targets, cause, service, severity, now, scope="cell"):
    """Write ONE incident event: an incident row per affected subscriber plus the coherent
    evidence trail — poor qoe_daily rows for the affected apps, a service interruption, and
    a complaint from some of them. All writes batched. Returns (targets, dur_min, info);
    the caller re-scores churn so it can batch that across several events."""
    if not targets:
        return [], 0, {}
    today   = now.strftime("%Y-%m-%d")
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    lo, hi  = _INCIDENT_DURATION_MIN[severity]
    dur_min = random.randint(lo, hi)
    eta     = (now + timedelta(minutes=dur_min)).strftime("%Y-%m-%d %H:%M:%S")
    info    = _subscriber_snapshot(targets)
    apps    = _SERVICE_APPS.get(service, ("video",))
    # a single customer's own fault gets their attention; a mass outage doesn't
    # generate 120 complaints
    compl_rate = 0.7 if (scope == "subscriber" and severity in ("major", "critical")) \
                     else (0.3 if scope == "subscriber" else _COMPLAINT_RATE_BULK)

    inc_rows, qoe_rows, si_rows, comp_rows = [], [], [], []
    for m in targets:
        region, cell_id, segment, is_hvc = info.get(m, (None, None, None, 0))
        inc_rows.append((m, segment, is_hvc, region, service, cause, severity,
                         now_str, eta, dur_min, cell_id))
        for app in apps:
            qoe_rows.append((m, today, app, random.randint(150, 260),
                             round(random.uniform(0.5, 2.5), 2),
                             round(random.uniform(1.2, 2.2), 2), "poor"))
        si_rows.append((m, today, dur_min, cause, service))
        if random.random() < compl_rate:
            comp_rows.append((m, today, service, f"{cause} affecting {service}"))

    sc_write_many("""INSERT INTO experience_incidents
                     (msisdn, value_segment, is_hvc, region, affected_service, root_cause,
                      severity, started_at, expected_resolution_at, status, duration_min, cell_id)
                     VALUES(?,?,?,?,?,?,?,?,?, 'active', ?,?)""", inc_rows)
    sc_write_many("""INSERT INTO qoe_daily VALUES(?,?,?,?,?,?,?)
                     ON CONFLICT(msisdn, date, app_type) DO UPDATE SET
                         avg_latency_ms=excluded.avg_latency_ms,
                         avg_throughput_mbps=excluded.avg_throughput_mbps,
                         experience_score=excluded.experience_score,
                         experience_label=excluded.experience_label""", qoe_rows)
    op_write_many("""INSERT INTO service_interruptions
                     (msisdn, date, duration_min, cause, affected_service)
                     VALUES(?,?,?,?,?)""", si_rows)
    if comp_rows:
        sc_write_many("""INSERT INTO complaints (msisdn, date, category, description, status)
                         VALUES(?,?,?,?, 'open')""", comp_rows)
    return targets, dur_min, info


def simulate_experience_incident(verbose=False):
    """OPEN coherent, timestamped service-degradation EVENTS.

    Each event picks a fault (weighted) and hits everyone in its blast radius: a site
    outage takes out the whole site, a cell fault the whole cell, a per-customer fault
    one subscriber. Every affected subscriber gets an incident row (status='active',
    started_at stamped to the second, expected_resolution_at in the near future) plus
    coherent evidence — poor qoe_daily rows for the affected apps, a service
    interruption, and a complaint from some of them — and their churn RISES while it's
    open. simulate_incident_resolution() heals it later and relaxes churn. All writes
    are batched: a site outage is ~120 subscribers, not 120 connections."""
    now     = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    active_now = {r[0] for r in sc_query(
        "SELECT msisdn FROM experience_incidents WHERE status='active'")}

    scenarios = [s[:4] for s in _INCIDENT_SCENARIOS]
    weights   = [s[4]  for s in _INCIDENT_SCENARIOS]

    all_affected = []
    for _ in range(_MAX_NEW_EVENTS):
        cause, service, severity, scope = random.choices(scenarios, weights=weights)[0]
        targets = _incident_targets(scope, active_now)
        if not targets:
            continue

        targets, dur_min, info = _emit_incident_event(targets, cause, service, severity,
                                                      now, scope)
        active_now.update(targets)
        all_affected.extend(targets)
        if verbose:
            hvc_n = sum(1 for m in targets if info.get(m, (None, None, None, 0))[3])
            print(f"  [INCIDENT+] {now_str[11:]} {severity} {cause} -> {service} [{scope}] "
                  f"hit {len(targets)} subscriber(s) ({hvc_n} HVC), heals in ~{dur_min}m")

    # rescore the affected customers now so churn reflects the new issues immediately
    if _churn_model and all_affected:
        month = now.strftime("%Y-%m")
        oc = sqlite3.connect(OP_DB); sc = sqlite3.connect(SC_DB)
        _rescore_churn(list(set(all_affected)), month, oc, sc)
        oc.close(); sc.close()


def simulate_incident_resolution(verbose=False):
    """SELF-HEAL: close every active incident whose expected_resolution_at has
    passed. Stamps resolved_at, flips status, resolves the linked complaint, and
    re-scores churn so risk RELAXES now that the issue is over. The row stays in
    the table forever, so the agent can still report 'today at 14:32 this HVC had
    a streaming issue (resolved 14:49)'."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    due = sc_query("""
        SELECT incident_id, msisdn, affected_service
        FROM experience_incidents
        WHERE status='active' AND expected_resolution_at <= ?
    """, (now,))
    if not due:
        return

    # batched: healing a site outage is ~120 rows, not 120 connections
    sc_write_many("""UPDATE experience_incidents SET status='resolved', resolved_at=?
                     WHERE incident_id=?""", [(now, i) for i, _, _ in due])
    # close the complaint the fault provoked (bulk rows carry no complaint_id link)
    sc_write_many("""UPDATE complaints SET status='resolved', resolution_days=0
                     WHERE msisdn=? AND category=? AND status='open'""",
                  [(m, svc) for _, m, svc in due])

    healed = sorted({m for _, m, _ in due})
    if verbose:
        print(f"  [INCIDENT-] {now[11:]} resolved {len(due)} incident(s) "
              f"across {len(healed)} subscriber(s)")

    # churn relaxes now that the incidents are no longer active
    if _churn_model and healed:
        month = datetime.now().strftime("%Y-%m")
        oc = sqlite3.connect(OP_DB); sc = sqlite3.connect(SC_DB)
        _rescore_churn(healed, month, oc, sc)
        oc.close(); sc.close()


def simulate_arpu_update(verbose=False):
    """Update ARPU for a random subset of subscribers this month."""
    month = datetime.now().strftime("%Y-%m")
    subs  = op_query("""
        SELECT msisdn, arpu FROM customer_value WHERE month=?
        ORDER BY RANDOM() LIMIT 100
    """, (month,))
    # Refresh the bands once per call: ARPU has just moved for a batch of people, and
    # the bands are percentiles of that same distribution. This is where HVC status is
    # actually re-tested — a subscriber whose ARPU drifts below the top decile loses it,
    # and one who climbs into it gains it, without anyone editing a threshold.
    th = value_thresholds(month, refresh=True)
    updated = 0
    promoted = demoted = 0
    for msisdn, old_arpu in subs:
        new_arpu = round(max(5.0, old_arpu * random.uniform(0.97, 1.05)), 2)
        was_hvc = classify_value(old_arpu, thresholds=th)[1]
        seg, hvc = classify_value(new_arpu, thresholds=th)
        promoted += (hvc and not was_hvc)
        demoted  += (was_hvc and not hvc)
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

    if verbose:
        churn = f", HVC +{promoted}/-{demoted}" if (promoted or demoted) else ""
        print(f"  [ARPU] Updated {updated} customer value records{churn}")


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
    """Raise an alarm and make it MEAN something: degrade the cell it sits on, and for
    major/critical faults open experience incidents for the customers that cell serves.

    The cell is seeded through an active subscriber rather than picked at random from the
    cells table — the same reasoning as _incident_targets: most cells serve nobody, so a
    randomly placed alarm is decoration that never reaches a customer."""
    if random.random() > P_ALARM_TRIGGER:
        return
    seed = sc_query("""
        SELECT st.current_cell_id, c.technology
        FROM subscriber_technology st
        JOIN subscribers s ON st.msisdn=s.msisdn
        JOIN cells c ON st.current_cell_id=c.cell_id
        WHERE s.is_active=1 AND c.is_active=1
        ORDER BY RANDOM() LIMIT 1
    """)
    if not seed:
        return
    cell_id, technology = seed[0]
    atype    = random.choice(ALARM_TYPES)
    severity = random.choices(SEVERITIES, weights=SEVERITY_WEIGHTS)[0]
    now      = datetime.now()
    now_iso  = now.isoformat()
    sc_write("""
        INSERT INTO network_alarms
            (cell_id, alarm_type, severity, trigger_time, is_active, description)
        VALUES (?,?,?,?,1,?)
    """, (cell_id, atype, severity, now_iso, f"{atype} on cell {cell_id} at {now_iso[:16]}"))

    # The fault is the CAUSE of the cell's numbers, not a sibling row in another table.
    sc_write(_KPI_UPSERT, _gen_kpi(cell_id, technology, now.strftime("%Y-%m-%d"),
                                   alarm=(atype, severity)))

    # Minor/warning faults stay a network-side concern; major and critical ones reach
    # the customers on that cell and flow through to complaints and churn.
    affected = []
    if severity in ("critical", "major"):
        cause, service = ALARM_TO_INCIDENT.get(atype, ("Signal interference", "voice calls"))
        active_now = {r[0] for r in sc_query(
            "SELECT msisdn FROM experience_incidents WHERE status='active'")}
        targets = [m for (m,) in sc_query("""
            SELECT st.msisdn FROM subscriber_technology st
            JOIN subscribers s ON st.msisdn=s.msisdn
            WHERE st.current_cell_id=? AND s.is_active=1 LIMIT ?
        """, (cell_id, _MAX_AFFECTED_PER_EVENT)) if m not in active_now]
        affected, _dur, _info = _emit_incident_event(targets, cause, service, severity, now)
        if _churn_model and affected:
            oc = sqlite3.connect(OP_DB); sc = sqlite3.connect(SC_DB)
            _rescore_churn(affected, now.strftime("%Y-%m"), oc, sc)
            oc.close(); sc.close()

    if verbose:
        tail = f", hit {len(affected)} subscriber(s)" if affected else ""
        print(f"  [ALARM] {severity.upper()} — {atype} on cell {cell_id}{tail}")


_MAX_TCA_PER_TICK = 3

def simulate_kpi_threshold_alarms(verbose=False):
    """Raise alarms FROM degraded counters — the reverse of simulate_alarm_trigger.

    Two guards keep this from turning into a siren. Cells that already carry an active
    alarm are skipped, so an alarm-induced degradation cannot spawn a second alarm about
    itself (no feedback loop, no duplicate per fault). And only cells that actually serve
    subscribers are eligible — alarming an empty cell would put back exactly the phantom
    rows this coupling exists to remove."""
    today = datetime.now()
    day   = today.strftime("%Y-%m-%d")
    rows  = sc_query("""
        SELECT k.cell_id, k.date, k.rsrp_avg, k.sinr_avg, k.dl_throughput_mbps,
               k.ul_throughput_mbps, k.dropped_call_rate, k.availability_pct,
               k.latency_ms, k.congestion_level, k.active_users_avg, c.technology
        FROM kpis_daily k
        JOIN cells c ON k.cell_id = c.cell_id
        WHERE k.date = ? AND c.is_active = 1
          AND k.cell_id NOT IN (SELECT cell_id FROM network_alarms WHERE is_active=1)
          AND EXISTS (SELECT 1 FROM subscriber_technology st
                      JOIN subscribers s ON st.msisdn = s.msisdn
                      WHERE st.current_cell_id = k.cell_id AND s.is_active = 1)
        ORDER BY RANDOM() LIMIT 200
    """, (day,))

    raised = []
    for row in rows:
        hit = _detect_threshold_alarm(row[11], row[:11])
        if hit:
            raised.append((row[0], hit[0], hit[1]))
        if len(raised) >= _MAX_TCA_PER_TICK:
            break
    if not raised:
        return

    now_iso = today.isoformat()
    sc_write_many("""
        INSERT INTO network_alarms
            (cell_id, alarm_type, severity, trigger_time, is_active, description)
        VALUES (?,?,?,?,1,?)
    """, [(cid, atype, sev, now_iso,
           f"{atype} detected on cell {cid} by threshold crossing at {now_iso[:16]}")
          for cid, atype, sev in raised])

    # a detected fault reaches customers exactly like a raised one does
    affected = []
    active_now = {r[0] for r in sc_query(
        "SELECT msisdn FROM experience_incidents WHERE status='active'")}
    for cid, atype, sev in raised:
        if sev not in ("critical", "major"):
            continue
        cause, service = ALARM_TO_INCIDENT.get(atype, ("Signal interference", "voice calls"))
        targets = [m for (m,) in sc_query("""
            SELECT st.msisdn FROM subscriber_technology st
            JOIN subscribers s ON st.msisdn=s.msisdn
            WHERE st.current_cell_id=? AND s.is_active=1 LIMIT ?
        """, (cid, _MAX_AFFECTED_PER_EVENT)) if m not in active_now]
        hit, _dur, _info = _emit_incident_event(targets, cause, service, sev, today)
        active_now.update(hit)
        affected.extend(hit)

    if _churn_model and affected:
        oc = sqlite3.connect(OP_DB); sc = sqlite3.connect(SC_DB)
        _rescore_churn(list(set(affected)), today.strftime("%Y-%m"), oc, sc)
        oc.close(); sc.close()

    if verbose:
        for cid, atype, sev in raised:
            print(f"  [TCA] {sev.upper()} — {atype} detected on cell {cid} from KPI thresholds")


def simulate_alarm_clear(verbose=False):
    """Clear alarms and let the cell recover — the KPI row is rewritten without the fault
    (or with whatever weaker alarm is still open), so healing shows up in the numbers."""
    if random.random() > P_ALARM_CLEAR:
        return
    alarms  = sc_query("""
        SELECT na.alarm_id, na.severity, na.cell_id, c.technology
        FROM network_alarms na JOIN cells c ON na.cell_id=c.cell_id
        WHERE na.is_active=1 ORDER BY RANDOM() LIMIT 5
    """)
    cleared = []
    now     = datetime.now()
    now_iso = now.isoformat()
    for alarm_id, severity, cell_id, technology in alarms:
        prob = {"warning": 0.5, "minor": 0.3, "major": 0.1, "critical": 0.02}.get(severity, 0.2)
        if random.random() < prob:
            sc_write("UPDATE network_alarms SET is_active=0, clear_time=? WHERE alarm_id=?", (now_iso, alarm_id))
            cleared.append((cell_id, technology))

    # recompute each healed cell against whatever alarms remain open on it
    if cleared:
        remaining = _active_alarms_by_cell([cid for cid, _ in cleared])
        today = now.strftime("%Y-%m-%d")
        for cell_id, technology in cleared:
            sc_write(_KPI_UPSERT, _gen_kpi(cell_id, technology, today,
                                           alarm=remaining.get(cell_id)))
    if verbose and cleared:
        print(f"  [ALARM] Cleared {len(cleared)} alarms (cells recovered)")


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
    vseg, hvc = classify_value(arpu, month)
    # Insert current month + 11 historical months so backfill never sees gaps for this subscriber
    _hist_months = [(datetime.strptime(month, "%Y-%m") - timedelta(days=30 * i)).strftime("%Y-%m")
                    for i in range(12)]
    op_write_many("INSERT OR IGNORE INTO customer_value "
                  "(msisdn, month, arpu, value_segment, is_hvc) VALUES (?,?,?,?,?)",
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


# Outbound campaign copy by type, and realistic inbound replies by intent.
_CAMPAIGN_MSG = {
    "retention":    "We value you! Enjoy 20% off your next 3 months. Reply YES to claim.",
    "winback":      "We miss you! Come back for 50% off for 2 months. Reply YES.",
    "upsell":       "Upgrade your plan and get 10GB bonus data. Reply YES to upgrade.",
    "5g_upsell":    "Your device supports 5G! Activate now + 10GB free. Reply YES.",
    "3g_migration": "3G is retiring soon. Switch to 4G free, keep your number. Reply YES.",
}
_INBOUND_TEXT = {
    "opt_in":  ["YES", "Yes please activate", "Sounds good, sign me up", "YES interested"],
    "opt_out": ["STOP", "Not interested", "Unsubscribe", "Stop texting me"],
    "query":   ["How much does it cost?", "Does this work with my plan?",
                "What's the catch?", "Until when is this valid?"],
}


def simulate_campaign_progress(verbose=False):
    """Queue SMS for active campaigns (mark targets notified, drop a 'sent' sms_log
    row), then let active campaigns complete once most targets have converted.
    Delivery + replies are handled by simulate_sms_lifecycle (shared with standalone
    SMS that humans approve in the Ops Portal)."""
    campaigns = op_query("SELECT campaign_id, campaign_type FROM campaigns WHERE status='active' ORDER BY RANDOM() LIMIT 3")
    now = datetime.now().isoformat()

    for campaign_id, ctype in campaigns:
        msg = _CAMPAIGN_MSG.get(ctype or "retention", _CAMPAIGN_MSG["retention"])

        # ── Notify a fresh batch of targets: queue the SMS (status='sent') ──
        to_notify = op_query(
            "SELECT msisdn FROM campaign_targets WHERE campaign_id=? AND notified=0 ORDER BY RANDOM() LIMIT 25",
            (campaign_id,))
        for (msisdn,) in to_notify:
            op_write(
                "INSERT INTO sms_log (msisdn, campaign_id, sent_date, message_text, status, response) "
                "VALUES (?,?,?,?, 'sent', 'none')",
                (msisdn, campaign_id, now, msg))
            op_write("UPDATE campaign_targets SET notified=1 WHERE campaign_id=? AND msisdn=?",
                     (campaign_id, msisdn))
        if to_notify and verbose:
            print(f"  [CAMPAIGN {campaign_id}] queued {len(to_notify)} SMS ({ctype})")

        # ── Complete the campaign once most targets have converted ─────────
        counts = op_query("SELECT COUNT(*), SUM(converted) FROM campaign_targets WHERE campaign_id=?", (campaign_id,))
        if counts and counts[0][0] > 0 and (counts[0][1] or 0) / counts[0][0] > 0.8:
            op_write("UPDATE campaigns SET status='completed' WHERE campaign_id=?", (campaign_id,))
            if verbose: print(f"  [CAMPAIGN] Campaign {campaign_id} completed")


def simulate_sms_lifecycle(verbose=False):
    """Advance EVERY queued SMS through its lifecycle — works for campaign SMS and
    for standalone SMS a human approves in the Ops Portal (campaign_id NULL).
    sent -> delivered/failed, then delivered -> an inbound reply (opt_in/opt_out/query).
    On opt_in for a campaign SMS, mark the campaign target converted."""
    # 1. sent -> delivered (~92%) / failed (~8%)
    queued = op_query("SELECT sms_id FROM sms_log WHERE status='sent' ORDER BY RANDOM() LIMIT 60")
    for (sms_id,) in queued:
        op_write("UPDATE sms_log SET status=? WHERE sms_id=?",
                 ("delivered" if random.random() > 0.08 else "failed", sms_id))

    # 2. delivered + no reply yet -> roll an inbound response
    pending = op_query(
        "SELECT sms_id, campaign_id, msisdn FROM sms_log "
        "WHERE status='delivered' AND response='none' ORDER BY RANDOM() LIMIT 40")
    replied = 0
    for sms_id, campaign_id, msisdn in pending:
        roll = random.random()
        if   roll < 0.25: intent = "opt_in"    # converts
        elif roll < 0.40: intent = "query"     # asks a question
        elif roll < 0.50: intent = "opt_out"   # unsubscribes
        else:             continue             # stays silent this tick
        reply = random.choice(_INBOUND_TEXT[intent])
        op_write("UPDATE sms_log SET response=?, inbound_text=? WHERE sms_id=?", (intent, reply, sms_id))
        if intent == "opt_in" and campaign_id is not None:
            op_write("UPDATE campaign_targets SET converted=1 WHERE campaign_id=? AND msisdn=?",
                     (campaign_id, msisdn))
        replied += 1
    if verbose and (queued or replied):
        print(f"  [SMS] delivered {len(queued)} queued, {replied} new replies")


# ═══════════════════════════════════════════════════════════════════════
# STATUS
# ═══════════════════════════════════════════════════════════════════════

# Columns that are historical by nature — a birth date or an activation date is
# SUPPOSED to be years old. Everything else with a date is a living series and
# should be tracking "today".
_STATIC_DATE_HINTS = ("birth", "activation", "registration", "start", "end",
                      "launched", "resolved", "clear", "created", "payment", "trigger")
_STALE_AFTER_DAYS = 40


def check_data_freshness(verbose=True):
    """Print any living series whose newest row is well behind today.

    Three separate tables froze silently before this existed, each because a backfill
    anchored its window on data instead of on the clock — and each stayed frozen for
    months because everything kept reporting success. A stale table cannot announce
    itself, so something has to go looking."""
    today = datetime.now().date()
    conn = sc_conn()
    try:
        conn.execute(f"ATTACH DATABASE '{OP_DB}' AS op")
    except sqlite3.OperationalError:
        pass
    stale = []
    for master, prefix in (("sqlite_master", ""), ("op.sqlite_master", "op.")):
        for (tbl,) in conn.execute(
                f"SELECT name FROM {master} WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            for col in conn.execute(f"PRAGMA table_info({tbl})"):
                name = col[1].lower()
                if not any(k in name for k in ("date", "month")):
                    continue
                if any(h in name for h in _STATIC_DATE_HINTS):
                    continue
                try:
                    mx = conn.execute(f"SELECT MAX({col[1]}) FROM {prefix}{tbl}").fetchone()[0]
                except sqlite3.OperationalError:
                    continue
                if not mx or not isinstance(mx, str):
                    continue
                try:                                   # 'YYYY-MM' or 'YYYY-MM-DD'
                    parts = mx[:10].split("-")
                    newest = date(int(parts[0]), int(parts[1]),
                                  int(parts[2]) if len(parts) > 2 else 1)
                except (ValueError, IndexError):
                    continue
                behind = (today - newest).days
                if behind > _STALE_AFTER_DAYS:
                    stale.append((behind, f"{prefix}{tbl}.{col[1]}", mx[:10]))
    conn.close()
    if stale and verbose:
        print(f"\n  [STALE DATA] these series have stopped advancing (> {_STALE_AFTER_DAYS} days behind):")
        for behind, col, mx in sorted(stale, reverse=True):
            print(f"    {behind:5}d behind — {col} (newest {mx})")
        print("    A backfill window is probably anchored on data instead of the clock.\n")
    elif verbose:
        print("  [FRESHNESS] all living series are current.")
    return stale


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
    active_inc   = sc_query("SELECT COUNT(*) FROM experience_incidents WHERE status='active'")[0][0]
    active_hvc   = sc_query("SELECT COUNT(*) FROM experience_incidents WHERE status='active' AND is_hvc=1")[0][0]

    print(f"\n  [{now}] ── LIVE STATUS ──────────────────────────────")
    print(f"  Subscribers : {active_subs:,} active")
    print(f"  Technology  : 3G={on_3g:,}  4G={on_4g:,}  5G={on_5g:,}")
    print(f"  Alarms      : {total_alarms} active ({critical} critical)")
    print(f"  Campaigns   : {active_camps} active")
    print(f"  Avg ARPU    : {avg_arpu} Yuan")
    print(f"  Poor QoE    : {poor_qoe:,} users yesterday")
    print(f"  Live issues : {active_inc} active incidents ({active_hvc} on HVCs)")
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

    check_data_freshness()
    print_status()

    tick_count = 0
    while True:
        try:
            tick_count += 1

            # Every tick
            simulate_kpi_fluctuation(args.verbose)
            simulate_alarm_trigger(args.verbose)        # equipment fault -> degrades its cell
            simulate_kpi_threshold_alarms(args.verbose) # degraded counters -> raise the alarm
            simulate_alarm_clear(args.verbose)
            simulate_incident_resolution(args.verbose)   # self-heal any due experience incidents

            # Every 2 ticks
            if tick_count % 2 == 0:
                simulate_dou_update(args.verbose)
                simulate_offer_acceptance(args.verbose)
                simulate_qoe_update(args.verbose)
                simulate_new_table_tick(args.verbose)
                simulate_sms_lifecycle(args.verbose)   # deliver + collect replies for any queued SMS

            # Every 3 ticks
            if tick_count % 3 == 0:
                simulate_technology_migration(args.verbose)
                simulate_volte_activation(args.verbose)
                simulate_congestion_incident(args.verbose)
                simulate_experience_incident(args.verbose)   # HVC service issues -> churn up

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
