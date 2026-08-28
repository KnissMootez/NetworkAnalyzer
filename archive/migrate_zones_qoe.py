"""
Migration: add zone to sites, area_code to subscribers, create qoe_daily.
Safe to re-run — uses ALTER TABLE IF NOT EXISTS guards.
"""
import sqlite3, random, os
from datetime import datetime, timedelta

random.seed(42)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SC_DB = os.path.join(BASE_DIR, "NetworkAnalyzer_new.db")

# ── Geographic zone & area_code mappings ─────────────────────────────────────
ZONE_MAP = {
    "Tunis": "North",    "Ariana": "North",   "Ben Arous": "North",
    "Manouba": "North",  "Bizerte": "North",  "Beja": "North",
    "Jendouba": "North", "Zaghouan": "North",
    "Nabeul": "Center",  "Sousse": "Center",  "Monastir": "Center",
    "Mahdia": "Center",  "Kairouan": "Center","Le Kef": "Center",
    "Kasserine": "Center","Sidi Bouzid": "Center","Siliana": "Center",
    "Sfax": "South",     "Gabes": "South",    "Medenine": "South",
    "Tataouine": "South","Gafsa": "South",    "Kebili": "South",
    "Tozeur": "South",
}

# T=Tunis metro, N=North, C=Center, R=Rural interior, S=South coastal
AREA_CODE_MAP = {
    "Tunis": "T",  "Ariana": "T",   "Ben Arous": "T", "Manouba": "T",
    "Bizerte": "N","Beja": "N",     "Jendouba": "N",  "Zaghouan": "N",
    "Nabeul": "C", "Sousse": "C",   "Monastir": "C",  "Mahdia": "C",
    "Kairouan": "C",
    "Le Kef": "R", "Siliana": "R",  "Kasserine": "R", "Sidi Bouzid": "R",
    "Gafsa": "R",  "Kebili": "R",   "Tozeur": "R",
    "Sfax": "S",   "Gabes": "S",    "Medenine": "S",  "Tataouine": "S",
}

# Experience quality bias per zone/area (multiplier on base score)
QUALITY_BIAS = {
    "T": 1.10, "N": 1.00, "C": 0.95, "R": 0.70, "S": 0.80,
}

conn = sqlite3.connect(SC_DB)
conn.execute("PRAGMA journal_mode=WAL")
c = conn.cursor()

# ── 1. Add zone to sites ──────────────────────────────────────────────────────
existing = {r[1] for r in c.execute("PRAGMA table_info(sites)").fetchall()}
if "zone" not in existing:
    c.execute("ALTER TABLE sites ADD COLUMN zone TEXT")
    print("Added zone column to sites")

for region, zone in ZONE_MAP.items():
    c.execute("UPDATE sites SET zone=? WHERE region=?", (zone, region))
print(f"  Updated sites.zone — {c.rowcount} rows for last region")

# ── 2. Add area_code to subscribers ──────────────────────────────────────────
existing = {r[1] for r in c.execute("PRAGMA table_info(subscribers)").fetchall()}
if "area_code" not in existing:
    c.execute("ALTER TABLE subscribers ADD COLUMN area_code TEXT")
    print("Added area_code column to subscribers")

for region, code in AREA_CODE_MAP.items():
    c.execute("UPDATE subscribers SET area_code=? WHERE region=?", (code, region))
total_updated = c.execute("SELECT COUNT(*) FROM subscribers WHERE area_code IS NOT NULL").fetchone()[0]
print(f"  Updated subscribers.area_code — {total_updated} subscribers")

conn.commit()

# ── 3. Create qoe_daily ───────────────────────────────────────────────────────
c.execute("""
CREATE TABLE IF NOT EXISTS qoe_daily (
    msisdn         TEXT,
    date           TEXT,
    app_type       TEXT,  -- 'gaming', 'video', 'voip', 'web', 'social'
    avg_latency_ms REAL,
    avg_throughput_mbps REAL,
    experience_score    REAL,   -- 1.0 (poor) to 5.0 (excellent)
    experience_label    TEXT,   -- 'poor', 'fair', 'good', 'excellent'
    PRIMARY KEY(msisdn, date, app_type)
)
""")
c.execute("CREATE INDEX IF NOT EXISTS idx_qoe_date ON qoe_daily(date)")
c.execute("CREATE INDEX IF NOT EXISTS idx_qoe_area ON qoe_daily(msisdn, app_type)")
print("qoe_daily table ready")

# ── 4. Populate qoe_daily ─────────────────────────────────────────────────────
today = datetime.now()
dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, 31)]  # last 30 days

# Get all active subscribers with their area_code
subscribers = c.execute(
    "SELECT msisdn, area_code FROM subscribers WHERE is_active=1 AND area_code IS NOT NULL"
).fetchall()
print(f"  Generating QoE for {len(subscribers)} active subscribers × 30 days × 2 app types …")

def score_to_label(s):
    if s < 2.0:   return "poor"
    if s < 3.0:   return "fair"
    if s < 4.0:   return "good"
    return "excellent"

APP_TYPES = ["gaming", "video"]

# Base parameters per app type
APP_BASE = {
    "gaming": {"latency": 45,  "throughput": 8,   "score": 3.5},
    "video":  {"latency": 30,  "throughput": 15,  "score": 3.8},
}

batch = []
BATCH_SIZE = 50_000

existing_count = c.execute("SELECT COUNT(*) FROM qoe_daily").fetchone()[0]
if existing_count > 0:
    print(f"  qoe_daily already has {existing_count} rows — skipping insert")
else:
    for msisdn, area_code in subscribers:
        bias = QUALITY_BIAS.get(area_code, 1.0)
        for date in dates:
            for app in APP_TYPES:
                base = APP_BASE[app]
                # Add daily/subscriber noise
                noise = random.gauss(0, 0.3)
                score = round(min(5.0, max(1.0, base["score"] * bias + noise)), 2)
                latency = round(base["latency"] / bias * random.uniform(0.8, 1.3), 1)
                throughput = round(base["throughput"] * bias * random.uniform(0.7, 1.4), 2)
                label = score_to_label(score)
                batch.append((msisdn, date, app, latency, throughput, score, label))

                if len(batch) >= BATCH_SIZE:
                    c.executemany(
                        "INSERT OR IGNORE INTO qoe_daily VALUES(?,?,?,?,?,?,?)", batch
                    )
                    conn.commit()
                    batch = []

    if batch:
        c.executemany("INSERT OR IGNORE INTO qoe_daily VALUES(?,?,?,?,?,?,?)", batch)
        conn.commit()

total_qoe = c.execute("SELECT COUNT(*) FROM qoe_daily").fetchone()[0]
print(f"  qoe_daily total rows: {total_qoe:,}")

# Quick sanity checks
area_r = c.execute("""
    SELECT COUNT(DISTINCT q.msisdn) FROM qoe_daily q
    JOIN subscribers s ON q.msisdn=s.msisdn
    WHERE s.area_code='R' AND q.app_type IN ('gaming','video')
    AND q.experience_label='poor'
    AND q.date=?
""", [dates[0]]).fetchone()[0]
print(f"  Area R poor gaming/video yesterday: {area_r} users")

south_zones = c.execute(
    "SELECT DISTINCT zone FROM sites WHERE zone IS NOT NULL"
).fetchall()
print(f"  Site zones: {[z[0] for z in south_zones]}")

conn.close()
print("\nMigration complete.")
