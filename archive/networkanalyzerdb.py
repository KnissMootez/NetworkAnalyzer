import sqlite3, random, math
from datetime import datetime, timedelta
import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

random.seed(42)

# ═══════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════

REGIONS = {
    "Tunis":     {"lat": 36.8190, "lon": 10.1658, "weight": 0.22},
    "Ariana":    {"lat": 36.8665, "lon": 10.1647, "weight": 0.10},
    "Ben Arous": {"lat": 36.7530, "lon": 10.2282, "weight": 0.08},
    "Manouba":   {"lat": 36.8100, "lon": 10.0972, "weight": 0.05},
    "Nabeul":    {"lat": 36.4513, "lon": 10.7357, "weight": 0.05},
    "Sfax":      {"lat": 34.7406, "lon": 10.7603, "weight": 0.09},
    "Sousse":    {"lat": 35.8245, "lon": 10.6346, "weight": 0.07},
    "Bizerte":   {"lat": 37.2744, "lon": 9.8739,  "weight": 0.04},
    "Gabes":     {"lat": 33.8814, "lon": 10.0982, "weight": 0.04},
    "Monastir":  {"lat": 35.7643, "lon": 10.8113, "weight": 0.04},
    "Gafsa":     {"lat": 34.4250, "lon": 8.7842,  "weight": 0.03},
    "Kairouan":  {"lat": 35.6781, "lon": 10.0963, "weight": 0.04},
    "Medenine":  {"lat": 33.3549, "lon": 10.5055, "weight": 0.03},
    "Jendouba":  {"lat": 36.5011, "lon": 8.7757,  "weight": 0.03},
    "Le Kef":    {"lat": 36.1676, "lon": 8.7147,  "weight": 0.02},
    "Siliana":   {"lat": 36.0849, "lon": 9.3708,  "weight": 0.02},
    "Kasserine": {"lat": 35.1676, "lon": 8.8365,  "weight": 0.02},
    "Sidi Bouzid":{"lat": 35.0382,"lon": 9.4849,  "weight": 0.02},
    "Tozeur":    {"lat": 33.9197, "lon": 8.1335,  "weight": 0.01},
    "Kebili":    {"lat": 33.7044, "lon": 8.9688,  "weight": 0.01},
    "Tataouine": {"lat": 32.9211, "lon": 10.4509, "weight": 0.01},
    "Mahdia":    {"lat": 35.5047, "lon": 11.0622, "weight": 0.02},
    "Zaghouan":  {"lat": 36.4021, "lon": 10.1428, "weight": 0.01},
    "Beja":      {"lat": 36.7256, "lon": 9.1817,  "weight": 0.01},
}

DEVICE_BRANDS = {
    "Samsung": {"weight": 0.30, "5g_ratio": 0.45, "4g_ratio": 0.40, "3g_ratio": 0.15},
    "Apple":   {"weight": 0.20, "5g_ratio": 0.65, "4g_ratio": 0.33, "3g_ratio": 0.02},
    "Huawei":  {"weight": 0.15, "5g_ratio": 0.35, "4g_ratio": 0.50, "3g_ratio": 0.15},
    "Xiaomi":  {"weight": 0.12, "5g_ratio": 0.30, "4g_ratio": 0.55, "3g_ratio": 0.15},
    "OPPO":    {"weight": 0.08, "5g_ratio": 0.25, "4g_ratio": 0.60, "3g_ratio": 0.15},
    "Nokia":   {"weight": 0.05, "5g_ratio": 0.10, "4g_ratio": 0.50, "3g_ratio": 0.40},
    "Vivo":    {"weight": 0.05, "5g_ratio": 0.20, "4g_ratio": 0.65, "3g_ratio": 0.15},
    "Other":   {"weight": 0.05, "5g_ratio": 0.05, "4g_ratio": 0.40, "3g_ratio": 0.55},
}

def weighted_choice(options: dict) -> str:
    keys = list(options.keys())
    weights = [options[k]["weight"] for k in keys]
    return random.choices(keys, weights=weights)[0]

def rand_coord(lat, lon, radius_km=0.3):
    dlat = random.uniform(-radius_km/111, radius_km/111)
    dlon = random.uniform(-radius_km/111, radius_km/111)
    return round(lat + dlat, 6), round(lon + dlon, 6)

def rand_date(start_days_ago=1825, end_days_ago=0):
    d = datetime.now() - timedelta(days=random.randint(end_days_ago, start_days_ago))
    return d.strftime("%Y-%m-%d")

def rand_msisdn():
    prefix = random.choice(["2162","2165","2167","2169","2168","2192","2194","2195","2197","2198","2199"])
    return prefix + str(random.randint(100000, 999999))

N_SUBSCRIBERS = 50000
N_SITES = 400
N_MONTHS = 6

print("Building NetworkAnalyzer DB...")
conn = sqlite3.connect(os.path.join(BASE_DIR, "NetworkAnalyzer_new.db"))

conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
c = conn.cursor()

# ═══════════════════════════════════════════════════════
# SCHEMA
# ═══════════════════════════════════════════════════════

c.executescript("""
DROP TABLE IF EXISTS subscribers;
DROP TABLE IF EXISTS devices;
DROP TABLE IF EXISTS subscriber_technology;
DROP TABLE IF EXISTS sites;
DROP TABLE IF EXISTS cells;
DROP TABLE IF EXISTS coverage;
DROP TABLE IF EXISTS kpis_daily;
DROP TABLE IF EXISTS kpis_hourly;
DROP TABLE IF EXISTS network_alarms;
DROP TABLE IF EXISTS network_incidents;
DROP TABLE IF EXISTS dou_monthly;
DROP TABLE IF EXISTS ott_monthly;
DROP TABLE IF EXISTS mobility_profile;
DROP TABLE IF EXISTS cdr_sessions;

CREATE TABLE subscribers (
    msisdn TEXT PRIMARY KEY,
    imsi TEXT UNIQUE,
    sim_type TEXT,
    is_active INTEGER DEFAULT 1,
    activation_date TEXT,
    region TEXT,
    city TEXT,
    latitude REAL,
    longitude REAL
);

CREATE TABLE devices (
    msisdn TEXT PRIMARY KEY,
    imei TEXT UNIQUE,
    brand TEXT,
    model TEXT,
    max_technology TEXT,
    volte_capable INTEGER,
    vowifi_capable INTEGER,
    is_5g_capable INTEGER,
    os TEXT,
    os_version TEXT
);

CREATE TABLE subscriber_technology (
    msisdn TEXT PRIMARY KEY,
    current_technology TEXT,
    current_cell_id INTEGER,
    volte_active INTEGER,
    data_roaming_active INTEGER,
    last_seen_date TEXT
);

CREATE TABLE sites (
    site_id INTEGER PRIMARY KEY,
    site_name TEXT,
    region TEXT,
    city TEXT,
    latitude REAL,
    longitude REAL,
    site_type TEXT,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE cells (
    cell_id INTEGER PRIMARY KEY,
    site_id INTEGER,
    technology TEXT,
    frequency_band TEXT,
    is_active INTEGER DEFAULT 1,
    max_users INTEGER,
    FOREIGN KEY(site_id) REFERENCES sites(site_id)
);

CREATE TABLE coverage (
    coverage_id INTEGER PRIMARY KEY AUTOINCREMENT,
    msisdn TEXT,
    cell_id INTEGER,
    technology_available TEXT,
    signal_strength_dbm REAL,
    is_home_coverage INTEGER DEFAULT 0,
    FOREIGN KEY(msisdn) REFERENCES subscribers(msisdn),
    FOREIGN KEY(cell_id) REFERENCES cells(cell_id)
);

CREATE TABLE kpis_daily (
    kpi_id INTEGER PRIMARY KEY AUTOINCREMENT,
    cell_id INTEGER,
    date TEXT,
    rsrp_avg REAL,
    sinr_avg REAL,
    dl_throughput_mbps REAL,
    ul_throughput_mbps REAL,
    dropped_call_rate REAL,
    availability_pct REAL,
    latency_ms REAL,
    congestion_level TEXT,
    active_users_avg INTEGER,
    FOREIGN KEY(cell_id) REFERENCES cells(cell_id)
);

CREATE TABLE kpis_hourly (
    kpi_id INTEGER PRIMARY KEY AUTOINCREMENT,
    cell_id INTEGER,
    datetime TEXT,
    rsrp REAL,
    sinr REAL,
    dl_throughput_mbps REAL,
    active_users INTEGER,
    congestion_level TEXT,
    FOREIGN KEY(cell_id) REFERENCES cells(cell_id)
);

CREATE TABLE network_alarms (
    alarm_id INTEGER PRIMARY KEY AUTOINCREMENT,
    cell_id INTEGER,
    alarm_type TEXT,
    severity TEXT,
    trigger_time TEXT,
    clear_time TEXT,
    is_active INTEGER DEFAULT 1,
    description TEXT,
    FOREIGN KEY(cell_id) REFERENCES cells(cell_id)
);

CREATE TABLE network_incidents (
    incident_id INTEGER PRIMARY KEY AUTOINCREMENT,
    cell_id INTEGER,
    incident_type TEXT,
    start_time TEXT,
    end_time TEXT,
    affected_users INTEGER,
    severity TEXT,
    root_cause TEXT,
    resolved INTEGER DEFAULT 0,
    FOREIGN KEY(cell_id) REFERENCES cells(cell_id)
);

CREATE TABLE dou_monthly (
    msisdn TEXT,
    month TEXT,
    total_data_gb REAL,
    distinct_cells_used INTEGER,
    primary_cell_id INTEGER,
    days_active INTEGER,
    PRIMARY KEY(msisdn, month)
);

CREATE TABLE ott_monthly (
    msisdn TEXT,
    month TEXT,
    streaming_gb REAL,
    gaming_gb REAL,
    web_gb REAL,
    voip_gb REAL,
    social_gb REAL,
    sms_count INTEGER,
    PRIMARY KEY(msisdn, month)
);

CREATE TABLE mobility_profile (
    msisdn TEXT,
    month TEXT,
    distinct_cells_count INTEGER,
    avg_daily_cells REAL,
    primary_cell_id INTEGER,
    mobility_class TEXT,
    is_fwa_candidate INTEGER DEFAULT 0,
    is_home_cell_user INTEGER DEFAULT 0,
    PRIMARY KEY(msisdn, month)
);
""")
conn.commit()
print("  Schema created")

# ═══════════════════════════════════════════════════════
# SITES & CELLS
# ═══════════════════════════════════════════════════════

region_names = list(REGIONS.keys())
region_weights = [REGIONS[r]["weight"] for r in region_names]

sites_data = []
for i in range(1, N_SITES + 1):
    region = random.choices(region_names, weights=region_weights)[0]
    info = REGIONS[region]
    lat, lon = rand_coord(info["lat"], info["lon"], radius_km=15)
    site_type = random.choices(
        ["macro", "micro", "indoor", "fwa_home_cell"],
        weights=[0.60, 0.25, 0.10, 0.05])[0]
    sites_data.append((i, f"SITE_{i:04d}", region, region, lat, lon, site_type, 1))

c.executemany("INSERT INTO sites VALUES(?,?,?,?,?,?,?,?)", sites_data)
conn.commit()
print(f"  {len(sites_data)} sites created")

# cells per site — mix of technologies based on region urban/rural
cells_data = []
cell_id = 1
cell_map = {}  # site_id -> list of (cell_id, technology)
tech_by_region = {
    "Tunis": ["4G","4G","5G","5G","3G"],
    "Ariana": ["4G","4G","5G","3G"],
    "Ben Arous": ["4G","4G","5G","3G"],
    "Sfax": ["4G","4G","3G","5G"],
    "Sousse": ["4G","4G","3G","5G"],
    "Monastir": ["4G","4G","3G"],
    "Bizerte": ["4G","3G","4G"],
    "Nabeul": ["4G","3G","4G"],
    "Gabes": ["4G","3G","3G"],
    "Kairouan": ["4G","3G","3G"],
    "Manouba": ["4G","4G","3G"],
}
default_techs = ["4G","3G","3G"]

for site in sites_data:
    site_id, _, region = site[0], site[1], site[2]
    techs = tech_by_region.get(region, default_techs)
    n_cells = random.randint(2, 4)
    cell_map[site_id] = []
    for t in techs[:n_cells]:
        band = {"2G":"900","3G":"2100","4G":"1800","5G":"3500"}.get(t,"1800")
        cells_data.append((cell_id, site_id, t, band, 1, random.randint(200, 800)))
        cell_map[site_id].append((cell_id, t))
        cell_id += 1

c.executemany("INSERT INTO cells VALUES(?,?,?,?,?,?)", cells_data)
conn.commit()
print(f"  {len(cells_data)} cells created")
all_cell_ids = [row[0] for row in cells_data]
all_cells = {row[0]: row for row in cells_data}  # cell_id -> row

# site lookup by cell
cell_to_site = {row[0]: row[1] for row in cells_data}
site_to_region = {row[0]: row[2] for row in sites_data}

# ═══════════════════════════════════════════════════════
# SUBSCRIBERS
# ═══════════════════════════════════════════════════════

print(f"  Generating {N_SUBSCRIBERS} subscribers...")
msisdns = set()
while len(msisdns) < N_SUBSCRIBERS:
    msisdns.add(rand_msisdn())
msisdns = list(msisdns)

subscribers_data = []
for msisdn in msisdns:
    region = random.choices(region_names, weights=region_weights)[0]
    info = REGIONS[region]
    lat, lon = rand_coord(info["lat"], info["lon"], radius_km=20)
    sim_type = random.choices(["SIM","eSIM"], weights=[0.88, 0.12])[0]
    act_date = rand_date(start_days_ago=2555, end_days_ago=30)
    subscribers_data.append((msisdn, f"60600{random.randint(10000000,99999999)}",
                              sim_type, 1, act_date, region, region, lat, lon))

c.executemany("INSERT OR IGNORE INTO subscribers VALUES(?,?,?,?,?,?,?,?,?)", subscribers_data)
conn.commit()
print(f"  {len(subscribers_data)} subscribers created")

# ═══════════════════════════════════════════════════════
# DEVICES
# ═══════════════════════════════════════════════════════

models = {
    "Samsung": ["Galaxy S24 Ultra","Galaxy A54","Galaxy A34","Galaxy S23","Galaxy M34","Galaxy A14"],
    "Apple":   ["iPhone 15 Pro","iPhone 14","iPhone 13","iPhone 12","iPhone SE"],
    "Huawei":  ["P60 Pro","Nova 11","Mate 50","P40","Y9a"],
    "Xiaomi":  ["14 Pro","Redmi Note 13","POCO X5","Redmi 12","Mi 11"],
    "OPPO":    ["Find X6","Reno 10","A78","A58","Find N3"],
    "Nokia":   ["G60","X30","G21","3310 4G","105"],
    "Vivo":    ["X90 Pro","V29","Y36","Y22","T2x"],
    "Other":   ["Generic 4G","Basic Phone","MBB Router","Tablet 4G","MBB Dongle"],
}

devices_data = []
sub_device_map = {}  # msisdn -> (max_tech, volte)
imeis = set()

for msisdn in msisdns:
    brand = weighted_choice(DEVICE_BRANDS)
    info = DEVICE_BRANDS[brand]
    r = random.random()
    if r < info["5g_ratio"]:
        max_tech = "5G"
        volte = 1
        is_5g = 1
    elif r < info["5g_ratio"] + info["4g_ratio"]:
        max_tech = "4G"
        volte = random.choices([1,0], weights=[0.75,0.25])[0]
        is_5g = 0
    else:
        max_tech = "3G"
        volte = 0
        is_5g = 0
    vowifi = 1 if (max_tech in ["4G","5G"] and random.random() < 0.6) else 0
    model = random.choice(models[brand])
    os = "iOS" if brand == "Apple" else "Android"
    os_ver = str(random.randint(12,17)) if brand=="Apple" else str(random.randint(10,14))
    imei = f"{random.randint(100000000000000,999999999999999)}"
    while imei in imeis:
        imei = f"{random.randint(100000000000000,999999999999999)}"
    imeis.add(imei)
    devices_data.append((msisdn, imei, brand, model, max_tech, volte, vowifi, is_5g, os, os_ver))
    sub_device_map[msisdn] = (max_tech, volte)

c.executemany("INSERT INTO devices VALUES(?,?,?,?,?,?,?,?,?,?)", devices_data)
conn.commit()
print(f"  {len(devices_data)} devices created")

# ═══════════════════════════════════════════════════════
# COVERAGE — per subscriber based on their region's cells
# ═══════════════════════════════════════════════════════

print("  Generating coverage...")
# build region -> cells map
from collections import defaultdict
region_cells = defaultdict(list)
for cell_row in cells_data:
    cell_id_val = cell_row[0]
    site_id_val = cell_row[1]
    tech = cell_row[2]
    region_val = site_to_region[site_id_val]
    region_cells[region_val].append((cell_id_val, tech))

coverage_data = []
sub_coverage_map = {}  # msisdn -> {tech: bool}
sub_home_cell = {}     # msisdn -> cell_id

for sub in subscribers_data:
    msisdn = sub[0]
    region = sub[5]
    available_cells = region_cells.get(region, [])
    if not available_cells:
        available_cells = random.sample(all_cell_ids, min(3, len(all_cell_ids)))
        available_cells = [(c, all_cells[c][2]) for c in available_cells]
    
    techs_available = set(t for _, t in available_cells)
    sub_coverage_map[msisdn] = techs_available
    
    # pick home cell — prefer 4G or 5G
    preferred = [(c,t) for c,t in available_cells if t in ["4G","5G"]]
    home_cell = random.choice(preferred if preferred else available_cells)
    sub_home_cell[msisdn] = home_cell[0]
    
    # add 1-3 coverage entries
    sample_cells = random.sample(available_cells, min(3, len(available_cells)))
    for cell_id_val, tech in sample_cells:
        signal = round(random.uniform(-110, -70), 1)
        is_home = 1 if cell_id_val == home_cell[0] else 0
        coverage_data.append((msisdn, cell_id_val, tech, signal, is_home))

c.executemany("INSERT INTO coverage(msisdn,cell_id,technology_available,signal_strength_dbm,is_home_coverage) VALUES(?,?,?,?,?)", coverage_data)
conn.commit()
print(f"  {len(coverage_data)} coverage records created")

# ═══════════════════════════════════════════════════════
# SUBSCRIBER TECHNOLOGY — what they're actually on
# ═══════════════════════════════════════════════════════

sub_tech_data = []
sub_current_tech = {}

for sub in subscribers_data:
    msisdn = sub[0]
    max_tech, volte_cap = sub_device_map[msisdn]
    coverage_techs = sub_coverage_map[msisdn]
    home_cell = sub_home_cell[msisdn]
    
    # Current technology — limited by device AND coverage
    possible = []
    tech_order = ["5G","4G","3G","2G"]
    for t in tech_order:
        if t in coverage_techs:
            possible.append(t)
    
    # Device cap
    device_order = tech_order.index(max_tech)
    possible = [t for t in possible if tech_order.index(t) >= device_order or tech_order.index(t) <= device_order]
    possible = [t for t in possible if tech_order.index(t) >= device_order]
    
    if not possible:
        possible = ["3G"]
    
    # Some customers don't use best available tech (plan limitation)
    if max_tech == "5G" and "5G" in possible:
        current = random.choices(["5G","4G","4G","4G"], weights=[0.30,0.45,0.15,0.10])[0]
        if current not in possible: current = possible[0]
    elif max_tech in ["4G","5G"] and "4G" in possible:
        current = random.choices(["4G","3G"], weights=[0.75,0.25])[0]
        if current not in possible: current = possible[0]
    else:
        current = possible[0]
    
    sub_current_tech[msisdn] = current
    volte_active = 1 if (volte_cap and current == "4G" and random.random() < 0.65) else 0
    roaming = 1 if random.random() < 0.05 else 0
    last_seen = rand_date(start_days_ago=7, end_days_ago=0)
    
    sub_tech_data.append((msisdn, current, home_cell, volte_active, roaming, last_seen))

c.executemany("INSERT INTO subscriber_technology VALUES(?,?,?,?,?,?)", sub_tech_data)
conn.commit()
print(f"  {len(sub_tech_data)} subscriber_technology records")

# ═══════════════════════════════════════════════════════
# DOU, OTT, MOBILITY
# ═══════════════════════════════════════════════════════

print("  Generating DOU / OTT / Mobility profiles...")
dou_data = []; ott_data = []; mobility_data = []
months = [(datetime.now() - timedelta(days=30*i)).strftime("%Y-%m") for i in range(N_MONTHS-1, -1, -1)]

for msisdn in msisdns:
    max_tech, _ = sub_device_map[msisdn]
    current_tech = sub_current_tech[msisdn]
    home_cell = sub_home_cell[msisdn]
    
    # Base DOU by tech
    base_dou = {"5G": random.gauss(35,15), "4G": random.gauss(18,10),
                "3G": random.gauss(5,3), "2G": random.gauss(0.5,0.3)}
    base = max(0.1, base_dou[current_tech])
    
    # Mobility class — affects FWA candidacy
    mob_class = random.choices(
        ["stationary","low","medium","high"],
        weights=[0.15, 0.25, 0.35, 0.25])[0]
    distinct_cells = {"stationary": random.randint(1,2),
                      "low": random.randint(2,5),
                      "medium": random.randint(5,15),
                      "high": random.randint(15,40)}[mob_class]
    
    is_fwa = 1 if (mob_class == "stationary" and base > 30) else 0
    is_home = 1 if mob_class in ["stationary","low"] else 0
    
    for month in months:
        dou = max(0.01, base + random.gauss(0, base*0.2))
        days_active = random.randint(20, 30)
        
        dou_data.append((msisdn, month, round(dou,3), distinct_cells,
                         home_cell, days_active))
        
        # OTT breakdown — must sum to roughly dou
        streaming = max(0, dou * random.uniform(0.10, 0.45))
        gaming    = max(0, dou * random.uniform(0.05, 0.25))
        web       = max(0, dou * random.uniform(0.10, 0.30))
        voip      = max(0, dou * random.uniform(0.01, 0.10))
        social    = max(0, dou - streaming - gaming - web - voip)
        sms       = random.randint(0, 200)
        
        ott_data.append((msisdn, month,
                         round(streaming,3), round(gaming,3),
                         round(web,3), round(voip,3),
                         round(social,3), sms))
        
        mobility_data.append((msisdn, month, distinct_cells,
                               round(distinct_cells/days_active,2),
                               home_cell, mob_class, is_fwa, is_home))

c.executemany("INSERT INTO dou_monthly VALUES(?,?,?,?,?,?)", dou_data)
c.executemany("INSERT INTO ott_monthly VALUES(?,?,?,?,?,?,?,?)", ott_data)
c.executemany("INSERT INTO mobility_profile VALUES(?,?,?,?,?,?,?,?)", mobility_data)
conn.commit()
print(f"  {len(dou_data)} DOU records, {len(ott_data)} OTT records, {len(mobility_data)} mobility records")

# ═══════════════════════════════════════════════════════
# KPIs DAILY — last 90 days
# ═══════════════════════════════════════════════════════

print("  Generating KPIs...")
kpi_data = []
base_date = datetime.now()
tech_kpi = {
    "5G": {"dl": (200,50), "ul": (80,20), "latency": (5,2), "avail": (99.5,0.3), "drop": (0.2,0.1)},
    "4G": {"dl": (80,20),  "ul": (30,10), "latency": (20,5), "avail": (99.0,0.5), "drop": (0.5,0.2)},
    "3G": {"dl": (8,3),    "ul": (3,1),   "latency": (60,15), "avail": (98.0,1.0), "drop": (1.5,0.5)},
    "2G": {"dl": (0.3,0.1),"ul": (0.1,0.05),"latency": (200,50),"avail": (97.0,1.5),"drop": (3.0,1.0)},
}
for cell_row in cells_data:
    cell_id_val = cell_row[0]
    tech = cell_row[2]
    kp = tech_kpi.get(tech, tech_kpi["4G"])
    for d in range(90):
        date_str = (base_date - timedelta(days=d)).strftime("%Y-%m-%d")
        dl = max(0.1, random.gauss(*kp["dl"]))
        ul = max(0.05, random.gauss(*kp["ul"]))
        lat = max(1, random.gauss(*kp["latency"]))
        avail = min(100, max(80, random.gauss(*kp["avail"])))
        drop = max(0, random.gauss(*kp["drop"]))
        rsrp = round(random.uniform(-115, -70), 1)
        sinr = round(random.uniform(-3, 25), 1)
        congestion = random.choices(["low","medium","high"], weights=[0.6,0.3,0.1])[0]
        users = random.randint(10, cell_row[5])
        kpi_data.append((cell_id_val, date_str, rsrp, sinr, round(dl,2), round(ul,2),
                         round(drop,3), round(avail,2), round(lat,1), congestion, users))

c.executemany("INSERT INTO kpis_daily(cell_id,date,rsrp_avg,sinr_avg,dl_throughput_mbps,ul_throughput_mbps,dropped_call_rate,availability_pct,latency_ms,congestion_level,active_users_avg) VALUES(?,?,?,?,?,?,?,?,?,?,?)", kpi_data)
conn.commit()
print(f"  {len(kpi_data)} KPI daily records")

# ═══════════════════════════════════════════════════════
# NETWORK ALARMS
# ═══════════════════════════════════════════════════════

alarm_types = ["High Interference","Coverage Hole","Congestion","Hardware Fault",
               "Backhaul Failure","Power Issue","Software Fault","High Drop Rate"]
alarm_data = []
for _ in range(500):
    cell_id_val = random.choice(all_cell_ids)
    severity = random.choices(["critical","major","minor","warning"],weights=[0.10,0.25,0.35,0.30])[0]
    t = datetime.now() - timedelta(hours=random.randint(1,720))
    is_active = random.choices([1,0],weights=[0.4,0.6])[0]
    alarm_data.append((cell_id_val, random.choice(alarm_types), severity,
                       t.isoformat(), None if is_active else t.isoformat(), is_active,
                       f"{random.choice(alarm_types)} detected on cell {cell_id_val}"))

c.executemany("INSERT INTO network_alarms(cell_id,alarm_type,severity,trigger_time,clear_time,is_active,description) VALUES(?,?,?,?,?,?,?)", alarm_data)
conn.commit()
print(f"  {len(alarm_data)} alarms created")

# ═══════════════════════════════════════════════════════
# INDEXES
# ═══════════════════════════════════════════════════════

c.executescript("""
CREATE INDEX IF NOT EXISTS idx_sub_region ON subscribers(region);
CREATE INDEX IF NOT EXISTS idx_dev_maxtech ON devices(max_technology);
CREATE INDEX IF NOT EXISTS idx_subtech_tech ON subscriber_technology(current_technology);
CREATE INDEX IF NOT EXISTS idx_cov_msisdn ON coverage(msisdn);
CREATE INDEX IF NOT EXISTS idx_dou_month ON dou_monthly(month);
CREATE INDEX IF NOT EXISTS idx_ott_month ON ott_monthly(month);
CREATE INDEX IF NOT EXISTS idx_mob_class ON mobility_profile(mobility_class);
CREATE INDEX IF NOT EXISTS idx_kpi_date ON kpis_daily(date);
CREATE INDEX IF NOT EXISTS idx_alarm_active ON network_alarms(is_active);
""")
conn.commit()

# ═══════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════

print("\n=== NetworkAnalyzer DB Summary ===")
tables = ["subscribers","devices","subscriber_technology","sites","cells",
          "coverage","kpis_daily","network_alarms","dou_monthly","ott_monthly","mobility_profile"]
for t in tables:
    count = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  {t}: {count:,}")

# Sanity checks
print("\n=== Campaign Candidate Counts ===")
five_g_upsell = c.execute("""
    SELECT COUNT(*) FROM subscribers s
    JOIN devices d ON s.msisdn=d.msisdn
    JOIN subscriber_technology st ON s.msisdn=st.msisdn
    WHERE d.is_5g_capable=1 AND st.current_technology='4G'
    AND EXISTS(SELECT 1 FROM coverage cv WHERE cv.msisdn=s.msisdn AND cv.technology_available='5G')
""").fetchone()[0]
print(f"  5G Upsell candidates: {five_g_upsell:,}")

migration_3g_4g = c.execute("""
    SELECT COUNT(*) FROM subscribers s
    JOIN devices d ON s.msisdn=d.msisdn
    JOIN subscriber_technology st ON s.msisdn=st.msisdn
    WHERE d.max_technology IN ('4G','5G') AND st.current_technology='3G'
    AND EXISTS(SELECT 1 FROM coverage cv WHERE cv.msisdn=s.msisdn AND cv.technology_available='4G')
""").fetchone()[0]
print(f"  3G→4G Migration candidates: {migration_3g_4g:,}")

volte_sunset = c.execute("""
    SELECT COUNT(*) FROM subscribers s
    JOIN devices d ON s.msisdn=d.msisdn
    JOIN subscriber_technology st ON s.msisdn=st.msisdn
    WHERE d.volte_capable=1 AND st.current_technology='3G' AND st.volte_active=0
""").fetchone()[0]
print(f"  VoLTE/3G Sunset candidates: {volte_sunset:,}")

fwa_candidates = c.execute("""
    SELECT COUNT(*) FROM mobility_profile mp
    JOIN dou_monthly dm ON mp.msisdn=dm.msisdn AND mp.month=dm.month
    WHERE mp.mobility_class='stationary' AND dm.total_data_gb > 30
    AND mp.month=(SELECT MAX(month) FROM mobility_profile)
""").fetchone()[0]
print(f"  FWA candidates: {fwa_candidates:,}")

conn.close()
print("\n✅ NetworkAnalyzer DB built: /home/claude/NetworkAnalyzer_new.db")