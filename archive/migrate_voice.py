"""
One-time migration: add voice_minutes column to ott_monthly and populate
with reasonable values derived from technology, voip_gb, and mobility class.

Voice minutes logic:
  - Base by technology: 2G=400, 3G=280, 4G=160, 5G=90  (2G users rely more on voice)
  - Mobility boost: high_mobility +30%, stationary -20%
  - voip_gb correlation: each 0.1 GB voip ~ -15 min (VoIP replaces voice calls)
  - Random ±25% noise per subscriber-month
  - Minimum 10 minutes
"""

import random
import sqlite3

random.seed(42)

NET_DB = "NetworkAnalyzer_new.db"

BASE_BY_TECH = {"2G": 400, "3G": 280, "4G": 160, "5G": 90}
MOBILITY_MULT = {
    "high": 1.30, "high_mobility": 1.30,
    "medium": 1.0, "medium_mobility": 1.0,
    "low": 0.85,  "low_mobility": 0.85,
    "stationary": 0.70,
}

conn = sqlite3.connect(NET_DB)

# Add column if missing
existing = {r[1] for r in conn.execute("PRAGMA table_info(ott_monthly)").fetchall()}
if "voice_minutes" not in existing:
    conn.execute("ALTER TABLE ott_monthly ADD COLUMN voice_minutes INTEGER DEFAULT 0")
    print("Added voice_minutes column to ott_monthly")
else:
    print("voice_minutes column already exists")

# Build technology map per msisdn
tech_map = dict(conn.execute(
    "SELECT msisdn, current_technology FROM subscriber_technology"
).fetchall())

# Build latest mobility class per msisdn
mob_map = dict(conn.execute("""
    SELECT msisdn, mobility_class FROM mobility_profile
    WHERE month = (SELECT MAX(month) FROM mobility_profile mp2 WHERE mp2.msisdn = mobility_profile.msisdn)
""").fetchall())

# Fetch all ott_monthly rows that need populating
rows = conn.execute(
    "SELECT msisdn, month, voip_gb FROM ott_monthly WHERE voice_minutes = 0 OR voice_minutes IS NULL"
).fetchall()
print(f"Populating {len(rows):,} ott_monthly rows...")

updates = []
for msisdn, month, voip_gb in rows:
    tech     = tech_map.get(msisdn, "3G")
    mob      = mob_map.get(msisdn, "medium")
    base     = BASE_BY_TECH.get(tech, 280)
    mob_mult = MOBILITY_MULT.get(mob, 1.0)
    voip_sub = (voip_gb or 0) * 150          # each GB voip ~ 150 min offset (VoIP substitution)
    noise    = random.uniform(0.75, 1.25)
    minutes  = max(10, int((base * mob_mult - voip_sub) * noise))
    updates.append((minutes, msisdn, month))

conn.executemany(
    "UPDATE ott_monthly SET voice_minutes=? WHERE msisdn=? AND month=?",
    updates
)
conn.commit()
conn.close()
print(f"Done. Updated {len(updates):,} rows.")
