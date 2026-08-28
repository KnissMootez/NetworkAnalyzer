"""
update_real_coordinates.py
Rebuild site coordinates and 5G sites using real OpenCelliD data + synthetic
city-based fill for inland regions that are under-represented in crowdsourced data.

Strategy:
  1. Extract all Tunisia (MCC=605) towers from cell_towers.csv.gz by radio type
     (GSM=2G, UMTS=3G, LTE=4G) grouped by region.
  2. Supplement each region/radio with synthetic towers at known city coordinates
     so inland regions get realistic coverage spread, not just coastal dots.
  3. Clean up any previously generated 5G-* sites from the DB.
  4. Re-assign coordinates to every existing site, matching tower radio to cell tech:
       sites with 4G cells  → LTE pool
       sites with 3G cells  → UMTS pool
       sites with 2G cells  → GSM pool  (fallback chain if pool empty)
  5. Generate fresh 5G sites at realistic urban-only counts near LTE anchor points.
"""

import csv
import gzip
import sqlite3
import random
import os
from collections import defaultdict

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
TOWERS_GZ   = os.path.join(BASE_DIR, "cell_towers.csv.gz")
SC_DB       = os.path.join(BASE_DIR, "NetworkAnalyzer_new.db")

random.seed(42)

# ── Bounding boxes per governorate ──────────────────────────────────────────
REGION_BOXES = {
    "Tunis":       (36.70, 36.95, 10.05, 10.35),
    "Ariana":      (36.70, 37.10, 9.95,  10.30),
    "Ben Arous":   (36.50, 36.80, 10.10, 10.60),
    "Manouba":     (36.65, 37.05, 9.65,  10.15),
    "Nabeul":      (36.10, 36.90, 10.40, 11.15),
    "Zaghouan":    (35.85, 36.60, 9.80,  10.40),
    "Bizerte":     (36.65, 37.45, 9.05,  10.25),
    "Beja":        (36.35, 37.05, 8.50,  9.60),
    "Jendouba":    (36.25, 37.05, 8.25,  9.10),
    "Le Kef":      (35.65, 36.60, 8.30,  9.25),
    "Siliana":     (35.60, 36.50, 9.05,  9.80),
    "Kairouan":    (35.15, 36.35, 9.45,  10.35),
    "Kasserine":   (34.65, 35.80, 8.05,  9.40),
    "Sidi Bouzid": (34.40, 35.40, 9.00,  10.10),
    "Sfax":        (33.90, 35.15, 9.85,  11.20),
    "Mahdia":      (35.05, 35.80, 10.25, 11.10),
    "Monastir":    (35.45, 35.95, 10.35, 11.05),
    "Sousse":      (35.70, 36.30, 10.15, 10.90),
    "Gabes":       (33.15, 34.30, 9.45,  10.75),
    "Medenine":    (32.75, 33.65, 9.95,  11.60),
    "Tataouine":   (30.20, 32.90, 8.95,  11.10),
    "Gafsa":       (33.75, 34.90, 7.85,  9.05),
    "Tozeur":      (33.25, 34.30, 7.45,  9.35),
    "Kebili":      (32.95, 34.05, 8.45,  10.05),
}

def lat_lon_to_region(lat, lon):
    for region, (la_min, la_max, lo_min, lo_max) in REGION_BOXES.items():
        if la_min <= lat <= la_max and lo_min <= lon <= lo_max:
            return region
    return None

# ── Known city centres per region for synthetic fill ────────────────────────
# Each entry: (lat, lon, spread_km)
# spread_km controls how much random scatter towers get around the centre.
# Multiple towns per governorate give realistic geographic spread.
REGION_CITIES = {
    "Tunis": [
        (36.8190, 10.1658, 1.5), (36.8065, 10.1815, 1.0), (36.8433, 10.2056, 1.2),
        (36.8817, 10.3230, 1.0), (36.7880, 10.1340, 0.8), (36.8320, 10.2890, 1.0),
    ],
    "Ariana": [
        (36.8614, 10.1939, 1.2), (36.9531, 10.1924, 1.0), (36.8973, 10.1428, 0.8),
        (36.8280, 10.1650, 0.7),
    ],
    "Ben Arous": [
        (36.7533, 10.2289, 1.0), (36.6762, 10.3309, 1.0), (36.6236, 10.4215, 0.8),
        (36.7011, 10.2867, 0.7), (36.5456, 10.5123, 0.8),
    ],
    "Manouba": [
        (36.8080, 9.9848, 1.0), (36.7842, 10.0532, 0.8), (36.7320, 9.9215, 0.8),
        (36.8623, 10.0127, 0.7), (36.6789, 9.8567, 0.7),
    ],
    "Nabeul": [
        (36.4527, 10.7356, 1.2), (36.3978, 10.5827, 1.0), (36.5892, 10.9213, 0.9),
        (36.6781, 10.9012, 0.8), (36.2134, 10.9876, 0.8), (36.7234, 10.7890, 0.7),
    ],
    "Zaghouan": [
        (36.4025, 10.1437, 1.0), (36.1234, 10.1678, 0.8), (36.3456, 10.0234, 0.7),
        (36.2345, 10.2345, 0.7), (35.9234, 10.0567, 0.6),
    ],
    "Bizerte": [
        (37.2744, 9.8731, 1.5), (37.0815, 9.6012, 1.0), (37.1523, 9.9876, 0.9),
        (37.0234, 10.0123, 0.8), (37.3456, 9.7234, 0.8),
    ],
    "Beja": [
        (36.7269, 9.1823, 1.2), (36.4956, 9.3456, 0.9), (36.8754, 9.2134, 0.8),
        (36.6012, 8.9876, 0.8), (36.5678, 9.4567, 0.7),
    ],
    "Jendouba": [
        (36.5011, 8.7773, 1.2), (36.6215, 8.9779, 1.0), (36.4731, 8.6953, 0.8),
        (36.8012, 9.0345, 0.8), (36.3456, 8.8123, 0.7), (36.7234, 8.5678, 0.6),
    ],
    "Le Kef": [
        (36.1824, 8.7146, 1.2), (36.4679, 8.8445, 0.9), (36.0123, 8.5234, 0.8),
        (35.8765, 8.8012, 0.8), (36.3012, 9.0234, 0.7), (35.9678, 9.1234, 0.6),
    ],
    "Siliana": [
        (36.0840, 9.3706, 1.2), (35.8589, 9.2014, 0.9), (36.2012, 9.5701, 0.8),
        (35.9234, 9.4567, 0.7), (36.1456, 9.1234, 0.7), (35.7234, 9.3456, 0.6),
    ],
    "Kairouan": [
        (35.6752, 10.1004, 1.5), (35.2789, 9.7823, 1.0), (35.9012, 9.8765, 0.9),
        (35.4567, 9.9876, 0.8), (35.5678, 10.2345, 0.8), (35.1234, 9.6789, 0.7),
        (35.7890, 10.3456, 0.7),
    ],
    "Kasserine": [
        (35.1673, 8.8365, 1.2), (35.2301, 9.1089, 1.0), (34.8765, 8.7234, 0.8),
        (35.0123, 9.0456, 0.8), (34.9456, 8.5678, 0.7), (35.3456, 8.9012, 0.7),
        (34.7890, 9.2345, 0.6),
    ],
    "Sidi Bouzid": [
        (35.0383, 9.4844, 1.2), (34.8159, 9.7934, 1.0), (35.2234, 9.6789, 0.8),
        (34.9678, 9.2345, 0.8), (35.1012, 9.8901, 0.7), (34.6789, 9.5678, 0.6),
    ],
    "Sfax": [
        (34.7406, 10.7603, 1.5), (34.4234, 10.5789, 1.0), (35.0123, 10.8901, 0.9),
        (34.5678, 10.2345, 0.8), (34.8901, 11.0234, 0.8), (34.2345, 10.4567, 0.7),
        (34.9678, 10.5678, 0.7),
    ],
    "Mahdia": [
        (35.5047, 11.0627, 1.2), (35.2789, 11.0432, 0.9), (35.6543, 10.9876, 0.8),
        (35.1234, 10.8765, 0.8), (35.7890, 11.1234, 0.7),
    ],
    "Monastir": [
        (35.7773, 10.8262, 1.2), (35.6234, 10.7345, 0.9), (35.9012, 10.9234, 0.8),
        (35.5678, 10.6789, 0.8), (35.8456, 10.6234, 0.7),
    ],
    "Sousse": [
        (35.8333, 10.6367, 1.5), (35.9234, 10.5234, 1.0), (35.7456, 10.7456, 0.9),
        (36.0678, 10.4567, 0.8), (35.6789, 10.8901, 0.8), (36.1234, 10.3456, 0.7),
    ],
    "Gabes": [
        (33.8818, 10.0982, 1.3), (33.5289, 10.1789, 1.0), (34.1012, 9.7234, 0.9),
        (33.3456, 10.3456, 0.8), (33.7234, 10.2345, 0.8), (33.1234, 9.9012, 0.7),
    ],
    "Medenine": [
        (33.3541, 10.5021, 1.2), (33.1012, 11.1234, 1.0), (33.5678, 11.3456, 0.8),
        (32.9012, 10.8765, 0.8), (33.4321, 10.9012, 0.7), (32.7890, 11.2345, 0.7),
    ],
    "Tataouine": [
        (32.9292, 10.4518, 1.2), (32.3167, 10.3956, 1.0), (31.5789, 10.5678, 0.9),
        (31.0234, 10.1234, 0.8), (30.5678, 9.8901, 0.7), (32.1234, 10.6789, 0.7),
    ],
    "Gafsa": [
        (34.4250, 8.7842, 1.2), (34.3323, 8.4032, 1.0), (34.6234, 8.9456, 0.8),
        (34.1456, 8.5678, 0.8), (34.7890, 8.2345, 0.7), (34.0123, 8.8901, 0.6),
    ],
    "Tozeur": [
        (33.9192, 8.1335, 1.2), (33.8694, 7.8774, 1.0), (33.5678, 8.0234, 0.8),
        (34.1012, 8.3456, 0.7), (33.7234, 7.6789, 0.6),
    ],
    "Kebili": [
        (33.7050, 8.9714, 1.2), (33.4612, 9.0202, 1.0), (33.9012, 9.2345, 0.8),
        (33.2345, 8.7890, 0.8), (33.5678, 9.4567, 0.7),
    ],
}

# How many synthetic towers to generate per region per radio type.
# Tuned so inland regions get realistic spread without swamping urban real data.
SYNTHETIC_COUNTS = {
    # region:          (GSM, UMTS, LTE)
    "Tunis":           ( 0,   0,   0),   # already dense in OpenCelliD
    "Ariana":          (10,   8,   5),
    "Ben Arous":       ( 8,   6,   4),
    "Manouba":         (15,  12,   8),   # nearly zero in OpenCelliD
    "Nabeul":          ( 8,   6,   4),
    "Zaghouan":        (18,  15,  10),
    "Bizerte":         (10,   8,   5),
    "Beja":            (20,  16,  12),
    "Jendouba":        (22,  18,  14),
    "Le Kef":          (22,  18,  14),
    "Siliana":         (22,  18,  14),
    "Kairouan":        (18,  14,  10),
    "Kasserine":       (22,  18,  14),
    "Sidi Bouzid":     (20,  16,  12),
    "Sfax":            ( 6,   4,   2),
    "Mahdia":          ( 8,   6,   4),
    "Monastir":        ( 8,   6,   4),
    "Sousse":          ( 8,   6,   4),
    "Gabes":           ( 8,   6,   4),
    "Medenine":        (12,  10,   8),
    "Tataouine":       (20,  16,  12),
    "Gafsa":           (18,  14,  10),
    "Tozeur":          (18,  14,  10),
    "Kebili":          (18,  14,  10),
}

def km_to_deg(km):
    return km / 111.0

def synthetic_towers(region, radio, count):
    """Generate `count` synthetic (lat, lon) points scattered around the
    known cities of `region`. Each city gets proportional share of points."""
    cities = REGION_CITIES.get(region, [])
    if not cities:
        return []
    results = []
    per_city = max(1, count // len(cities))
    for lat_c, lon_c, spread_km in cities:
        spread = km_to_deg(spread_km)
        for _ in range(per_city):
            lat = round(lat_c + random.uniform(-spread, spread), 6)
            lon = round(lon_c + random.uniform(-spread, spread), 6)
            results.append((lat, lon))
        if len(results) >= count:
            break
    # top up if rounding left us short
    while len(results) < count:
        lat_c, lon_c, spread_km = random.choice(cities)
        spread = km_to_deg(spread_km)
        results.append((
            round(lat_c + random.uniform(-spread, spread), 6),
            round(lon_c + random.uniform(-spread, spread), 6),
        ))
    return results[:count]


# ════════════════════════════════════════════════════════════════════════════
# STEP 1 — Build tower pools from real OpenCelliD data
# ════════════════════════════════════════════════════════════════════════════
RADIO_TO_TECH = {"GSM": "2G", "UMTS": "3G", "LTE": "4G"}

print("Reading cell_towers.csv.gz for MCC=605 (Tunisia) …")
real_towers = defaultdict(lambda: defaultdict(list))  # [region][radio] = [(lat,lon)]
total_real = 0

with gzip.open(TOWERS_GZ, "rt", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        if row.get("mcc") != "605":
            continue
        radio = row.get("radio", "")
        if radio not in RADIO_TO_TECH:
            continue
        try:
            lat = float(row["lat"])
            lon = float(row["lon"])
        except ValueError:
            continue
        region = lat_lon_to_region(lat, lon)
        if region:
            real_towers[region][radio].append((lat, lon))
            total_real += 1

print(f"  Real towers loaded: {total_real}")
for region in sorted(REGION_BOXES):
    gsm  = len(real_towers[region]["GSM"])
    umts = len(real_towers[region]["UMTS"])
    lte  = len(real_towers[region]["LTE"])
    if gsm + umts + lte:
        print(f"  {region:<15} GSM={gsm:<4} UMTS={umts:<4} LTE={lte}")


# ════════════════════════════════════════════════════════════════════════════
# STEP 2 — Combine real towers + synthetic fill
# ════════════════════════════════════════════════════════════════════════════
print("\nAdding synthetic city-based towers for under-represented regions …")
towers = defaultdict(lambda: defaultdict(list))  # [region][radio]
synth_added = 0

for region in REGION_BOXES:
    gsm_n, umts_n, lte_n = SYNTHETIC_COUNTS.get(region, (15, 12, 8))
    for radio, synth_count in [("GSM", gsm_n), ("UMTS", umts_n), ("LTE", lte_n)]:
        real = real_towers[region][radio]
        towers[region][radio].extend(real)
        synth = synthetic_towers(region, radio, synth_count)
        towers[region][radio].extend(synth)
        synth_added += len(synth)

print(f"  Synthetic towers added: {synth_added}")
print("\nFinal tower pool by region:")
for region in sorted(REGION_BOXES):
    gsm  = len(towers[region]["GSM"])
    umts = len(towers[region]["UMTS"])
    lte  = len(towers[region]["LTE"])
    print(f"  {region:<15} GSM={gsm:<4} UMTS={umts:<4} LTE={lte}")

all_towers_flat = [(lat, lon)
                   for r in towers.values()
                   for radio_pools in r.values()
                   for lat, lon in radio_pools]


# ════════════════════════════════════════════════════════════════════════════
# STEP 3 — Open DB, clean up old 5G-* sites
# ════════════════════════════════════════════════════════════════════════════
print("\nConnecting to DB …")
conn = sqlite3.connect(SC_DB)
conn.execute("PRAGMA journal_mode=WAL")

old_5g_sites = conn.execute(
    "SELECT site_id FROM sites WHERE site_name LIKE '5G-%'"
).fetchall()
old_5g_ids = [r[0] for r in old_5g_sites]

if old_5g_ids:
    placeholders = ",".join("?" * len(old_5g_ids))
    old_5g_cells = conn.execute(
        f"SELECT cell_id FROM cells WHERE site_id IN ({placeholders})", old_5g_ids
    ).fetchall()
    old_cell_ids = [r[0] for r in old_5g_cells]

    if old_cell_ids:
        cell_ph = ",".join("?" * len(old_cell_ids))
        conn.execute(f"DELETE FROM coverage WHERE cell_id IN ({cell_ph})", old_cell_ids)
        conn.execute(f"DELETE FROM cells WHERE cell_id IN ({cell_ph})", old_cell_ids)

    conn.execute(f"DELETE FROM sites WHERE site_id IN ({placeholders})", old_5g_ids)
    print(f"  Removed {len(old_5g_ids)} old 5G-* sites and {len(old_cell_ids)} cells")
else:
    print("  No old 5G-* sites to remove")
    old_cell_ids = []


# ════════════════════════════════════════════════════════════════════════════
# STEP 4 — Re-assign coordinates to existing sites by technology
# ════════════════════════════════════════════════════════════════════════════
# For each site, look at what technologies its cells use to pick the right pool.
# Priority: 4G → LTE pool, 3G → UMTS pool, 2G → GSM pool
# If preferred pool is empty for that region, fall back down the chain.

print("\nRe-assigning coordinates to existing sites …")

site_rows = conn.execute("SELECT site_id, region FROM sites").fetchall()

# Build site → primary tech map
site_techs = {}
for site_id, in conn.execute("SELECT DISTINCT site_id FROM sites"):
    techs = [r[0] for r in conn.execute(
        "SELECT DISTINCT technology FROM cells WHERE site_id=?", (site_id,)
    )]
    if "4G" in techs:
        site_techs[site_id] = "4G"
    elif "3G" in techs:
        site_techs[site_id] = "3G"
    elif "2G" in techs:
        site_techs[site_id] = "2G"
    else:
        site_techs[site_id] = "4G"  # fallback

TECH_TO_RADIO = {"4G": "LTE", "3G": "UMTS", "2G": "GSM"}
FALLBACK_RADIO = {"LTE": ["LTE", "UMTS", "GSM"],
                  "UMTS": ["UMTS", "GSM", "LTE"],
                  "GSM":  ["GSM", "UMTS", "LTE"]}

updated = 0
for site_id, region in site_rows:
    tech  = site_techs.get(site_id, "4G")
    radio = TECH_TO_RADIO[tech]

    pool = None
    for r in FALLBACK_RADIO[radio]:
        candidate = towers.get(region, {}).get(r)
        if candidate:
            pool = candidate
            break

    if pool:
        lat, lon = random.choice(pool)
    else:
        lat, lon = random.choice(all_towers_flat)

    conn.execute(
        "UPDATE sites SET latitude=?, longitude=? WHERE site_id=?",
        (lat, lon, site_id)
    )
    updated += 1

print(f"  {updated} sites updated")


# ════════════════════════════════════════════════════════════════════════════
# STEP 5 — Generate fresh 5G sites (realistic urban-focused counts)
# ════════════════════════════════════════════════════════════════════════════
# Tunisia 5G is early-stage: concentrate on major cities only.
FIVE_G_DISTRIBUTION = {
    "Tunis": 40, "Ariana": 18, "Ben Arous": 16, "Sousse": 16,
    "Sfax": 16, "Monastir": 10, "Nabeul": 8, "Manouba": 8,
    "Bizerte": 6, "Gabes": 5, "Mahdia": 5, "Kairouan": 4,
    "Gafsa": 3, "Kasserine": 3, "Jendouba": 3, "Le Kef": 2,
    "Zaghouan": 2, "Siliana": 2, "Sidi Bouzid": 2, "Medenine": 2,
    "Beja": 2, "Kebili": 1, "Tataouine": 1, "Tozeur": 1,
}

OFFSET     = 0.005   # ±500 m from 4G anchor
COV_RADIUS = 0.03    # ~3 km subscriber coverage radius

print("\nGenerating 5G sites …")

next_site_id = conn.execute("SELECT MAX(site_id) FROM sites").fetchone()[0] + 1
next_cell_id = conn.execute("SELECT MAX(cell_id) FROM cells").fetchone()[0] + 1

# Load 4G site positions per region as anchors for 5G placement
lte_sites_by_region = defaultdict(list)
for region, city, zone, lat, lon in conn.execute(
    "SELECT s.region, s.city, s.zone, s.latitude, s.longitude "
    "FROM sites s "
    "JOIN cells c ON c.site_id = s.site_id "
    "WHERE c.technology='4G' AND s.is_active=1 "
    "  AND s.latitude IS NOT NULL"
):
    lte_sites_by_region[region].append((city, zone, lat, lon))

subscribers = conn.execute(
    "SELECT msisdn, latitude, longitude FROM subscribers "
    "WHERE is_active=1 AND latitude IS NOT NULL AND longitude IS NOT NULL"
).fetchall()

total_5g_sites = total_5g_cells = total_5g_cov = 0

for region, count in FIVE_G_DISTRIBUTION.items():
    pool = lte_sites_by_region.get(region, [])
    if not pool:
        pool = [(c, z, la, lo)
                for r_sites in lte_sites_by_region.values()
                for c, z, la, lo in r_sites]

    new_sites = []
    for _ in range(count):
        city, zone, base_lat, base_lon = random.choice(pool)
        lat = round(base_lat + random.uniform(-OFFSET, OFFSET), 6)
        lon = round(base_lon + random.uniform(-OFFSET, OFFSET), 6)
        new_sites.append((
            next_site_id,
            f"5G-{region[:3].upper()}-{next_site_id}",
            region, city, lat, lon, "macro", 1, zone,
        ))
        next_site_id += 1

    conn.executemany(
        "INSERT OR IGNORE INTO sites "
        "(site_id, site_name, region, city, latitude, longitude, site_type, is_active, zone) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        new_sites,
    )

    new_cells = []
    site_positions = []
    for site_row in new_sites:
        sid, _, _, _, lat, lon, _, _, _ = site_row
        new_cells.append((next_cell_id, sid, "5G", "3500", 1, 150))
        site_positions.append((next_cell_id, lat, lon))
        next_cell_id += 1

    conn.executemany(
        "INSERT OR IGNORE INTO cells "
        "(cell_id, site_id, technology, frequency_band, is_active, max_users) "
        "VALUES (?,?,?,?,?,?)",
        new_cells,
    )

    new_cov = []
    for cell_id, site_lat, site_lon in site_positions:
        lat_lo = site_lat - COV_RADIUS
        lat_hi = site_lat + COV_RADIUS
        lon_lo = site_lon - COV_RADIUS
        lon_hi = site_lon + COV_RADIUS
        for msisdn, sub_lat, sub_lon in subscribers:
            if lat_lo <= sub_lat <= lat_hi and lon_lo <= sub_lon <= lon_hi:
                new_cov.append((
                    msisdn, cell_id, "5G",
                    round(random.uniform(-105, -75), 1), 0,
                ))

    if new_cov:
        conn.executemany(
            "INSERT OR IGNORE INTO coverage "
            "(msisdn, cell_id, technology_available, signal_strength_dbm, is_home_coverage) "
            "VALUES (?,?,?,?,?)",
            new_cov,
        )

    total_5g_sites += len(new_sites)
    total_5g_cells += len(new_cells)
    total_5g_cov   += len(new_cov)
    print(f"  {region:<15} {len(new_sites):>3} sites  {len(new_cov):>6} cov links")

conn.commit()
conn.close()

print(f"\n{'='*50}")
print(f"Done.")
print(f"  Existing sites re-coordinated : {updated}")
print(f"  5G sites added                : {total_5g_sites}")
print(f"  5G cells added                : {total_5g_cells}")
print(f"  5G coverage links added       : {total_5g_cov:,}")

# ── Final DB summary ─────────────────────────────────────────────────────────
conn2 = sqlite3.connect(SC_DB)
print("\nFinal cell counts by technology:")
for tech, cnt in conn2.execute(
    "SELECT technology, COUNT(*) FROM cells GROUP BY technology ORDER BY technology"
):
    print(f"  {tech}: {cnt}")
conn2.close()
