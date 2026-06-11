"""
Reassign ARPU in customer_value with city-tier-based distributions.
Recomputes value_segment and is_hvc. Re-runs churn scoring.
"""
import sqlite3, random, math, os

random.seed(99)

SC_DB = "NetworkAnalyzer_new.db"
OP_DB = "operator_new.db"

# ── City wealth tiers ─────────────────────────────────────────────────
# (base_arpu, std_dev)  — individual subscriber ARPU drawn from normal dist
CITY_TIERS = {
    # Platinum — major capitals, densely populated
    "Ba Sing Se (Inner Ring)":        (155, 35),
    "Ba Sing Se (Outer Ring)":        (130, 30),
    "Fire Nation Capital":            (145, 38),
    "Omashu":                         (135, 32),

    # Gold — large prosperous cities
    "Gaoling":                        (105, 28),
    "Ember Island":                   (110, 25),
    "Kyoshi Island":                  (95,  24),
    "Agna Qel'a":                     (100, 26),
    "Gaoling North District":         (98,  25),

    # Silver — mid-size cities
    "Crescent Island":                (72,  20),
    "Misty Palms Oasis":              (68,  18),
    "Gaipan Village":                 (62,  17),
    "FumaBu Town":                    (65,  18),
    "Southern Water Tribe Capital":   (75,  20),
    "Si Wong Rock Town":              (58,  16),
    "Serpent's Pass":                 (55,  15),

    # Bronze — remote, small, underdeveloped
    "HeiBai's Forest":                (22,  10),
    "Sunset City":                    (28,  12),
    "The Boiling Rock":               (25,  11),
    "Island of the Sun Warriors":     (32,  13),
    "Eastern Air Temple":             (38,  14),
    "Western Air Temple":             (36,  13),
    "Northern Air Temple":            (35,  12),
    "Southern Air Temple":            (40,  15),
}

DEFAULT_TIER = (60, 18)  # fallback for any unlisted city

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def gen_arpu(city):
    base, std = CITY_TIERS.get(city, DEFAULT_TIER)
    val = random.gauss(base, std)
    return round(clamp(val, 2.0, 250.0), 2)

def segment(arpu):
    if arpu >= 120: return "platinum"
    if arpu >= 80:  return "gold"
    if arpu >= 40:  return "silver"
    return "bronze"

def is_hvc(arpu):
    return 1 if arpu >= 90 else 0

# ── Load city per msisdn from SC DB ──────────────────────────────────
print("Loading subscriber cities...")
sc = sqlite3.connect(SC_DB)
city_map = dict(sc.execute("SELECT msisdn, city FROM subscribers").fetchall())
sc.close()
print(f"  {len(city_map):,} subscribers loaded")

# ── Update customer_value ─────────────────────────────────────────────
print("Updating customer_value ARPU...")
op = sqlite3.connect(OP_DB)
rows = op.execute("SELECT msisdn, month FROM customer_value").fetchall()
print(f"  {len(rows):,} rows to update")

updates = []
for msisdn, month in rows:
    city = city_map.get(msisdn, "")
    arpu = gen_arpu(city)
    seg  = segment(arpu)
    hvc  = is_hvc(arpu)
    updates.append((arpu, seg, hvc, msisdn, month))

op.executemany(
    "UPDATE customer_value SET arpu=?, value_segment=?, is_hvc=? WHERE msisdn=? AND month=?",
    updates
)
op.commit()
print("  Done.")

# ── Verify ────────────────────────────────────────────────────────────
stats = op.execute("""
    SELECT MIN(arpu), MAX(arpu), ROUND(AVG(arpu),2),
           SUM(CASE WHEN value_segment='platinum' THEN 1 ELSE 0 END),
           SUM(CASE WHEN value_segment='gold' THEN 1 ELSE 0 END),
           SUM(CASE WHEN value_segment='silver' THEN 1 ELSE 0 END),
           SUM(CASE WHEN value_segment='bronze' THEN 1 ELSE 0 END),
           SUM(is_hvc)
    FROM customer_value
""").fetchone()
print(f"\nARPU range:  {stats[0]:.2f} – {stats[1]:.2f} Yuan  (avg {stats[2]:.2f})")
print(f"Segments:    platinum={stats[3]:,}  gold={stats[4]:,}  silver={stats[5]:,}  bronze={stats[6]:,}")
print(f"HVC total:   {stats[7]:,}")

# ── City averages (latest month) ──────────────────────────────────────
op.execute(f"ATTACH DATABASE '{SC_DB}' AS sc")
city_avgs = op.execute("""
    SELECT s.city, ROUND(AVG(cv.arpu),1) as avg_arpu, COUNT(DISTINCT cv.msisdn) as subs
    FROM customer_value cv
    JOIN sc.subscribers s ON cv.msisdn=s.msisdn
    WHERE cv.month=(SELECT MAX(month) FROM customer_value)
    GROUP BY s.city ORDER BY avg_arpu DESC
""").fetchall()
print("\nCity ARPU (latest month):")
for city, avg, subs in city_avgs:
    print(f"  {city:<40} {avg:>7} Yuan  ({subs:,} subs)")

op.close()
print("\nDone. Restart server to rebuild data profile.")
