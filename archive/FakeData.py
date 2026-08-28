import random
import sqlite3
from datetime import datetime, timedelta
from faker import Faker

fake_fr = Faker('fr_FR')
random.seed(42)
Faker.seed(42)

TN_FIRST_NAMES_MALE = [
    "Mohamed", "Ahmed", "Ali", "Omar", "Youssef", "Hamza", "Bilel", "Sami",
    "Karim", "Amine", "Mehdi", "Hatem", "Nabil", "Walid", "Khaled", "Tarek",
    "Rami", "Bassem", "Issam", "Slim", "Lotfi", "Farouk", "Mondher", "Hedi",
    "Zied", "Maher", "Adel", "Ridha", "Fethi", "Samir", "Chokri", "Nizar",
    "Anis", "Firas", "Yassine", "Skander", "Aymen", "Wassim", "Oussama", "Aziz",
]
TN_FIRST_NAMES_FEMALE = [
    "Fatma", "Amira", "Sana", "Ines", "Rania", "Mariem", "Salma", "Nour",
    "Hela", "Sirine", "Asma", "Rim", "Lina", "Donia", "Yasmine", "Olfa",
    "Sonia", "Hajer", "Amel", "Nawres", "Rahma", "Imen", "Cyrine", "Wafa",
    "Emna", "Lobna", "Manel", "Nadia", "Insaf", "Houda", "Zeineb", "Bechira",
    "Afef", "Dorsaf", "Nesrine", "Ghofrane", "Chaima", "Oumaima", "Sabrine", "Abir",
]
TN_LAST_NAMES = [
    "Ben Ali", "Ben Salah", "Trabelsi", "Chaabane", "Ayari", "Gharbi", "Hamdi",
    "Mansouri", "Belhaj", "Jebali", "Khelifi", "Maatoug", "Rezgui", "Sfaxi",
    "Tlili", "Bouzid", "Ferchichi", "Guesmi", "Haddad", "Jaziri", "Karray",
    "Laabidi", "Mbarki", "Nasr", "Oueslati", "Saidi", "Turki",
    "Zouari", "Abidi", "Boukadi", "Chebbi", "Dridi", "Elloumi", "Farhat",
    "Garrach", "Hamouda", "Idoudi", "Jouini", "Khemiri", "Limem", "Mhenni",
    "Nefzi", "Omri", "Rahmani", "Sghaier", "Toumi", "Yahyaoui", "Zribi",
]

def fake_name():
    gender = random.choice(["M", "F"])
    first = random.choice(TN_FIRST_NAMES_MALE if gender == "M" else TN_FIRST_NAMES_FEMALE)
    last  = random.choice(TN_LAST_NAMES)
    return f"{first} {last}"

def fake_email():
    first = random.choice(TN_FIRST_NAMES_MALE + TN_FIRST_NAMES_FEMALE).lower().replace(" ", "")
    last  = random.choice(TN_LAST_NAMES).lower().replace(" ", "")
    return f"{first}.{last}{random.randint(1,999)}@{random.choice(['gmail.com','yahoo.fr','topnet.tn','hexabyte.tn'])}"

def fake_address():
    return fake_fr.address().replace("\n", ", ")

def fake_sentence():
    return fake_fr.sentence()

DB_PATH = "NetworkAnalyzer.db"
NUM_CUSTOMERS = 100_000
BATCH_SIZE = 2000  # insert in batches for speed

# ─────────────────────────────────────────────
# TUNISIA CONSTANTS
# ─────────────────────────────────────────────

REGIONS = [
    "Tunis", "Ariana", "Ben Arous", "Manouba",
    "Sousse", "Monastir", "Nabeul", "Sfax",
    "Bizerte", "Gabes", "Kairouan", "Gafsa",
    "Zaghouan", "Siliana", "Le Kef"
]

CITIES = {
    "Tunis":     ["Tunis", "La Marsa", "Carthage", "Le Bardo", "Megrine", "Cite El Khadra"],
    "Ariana":    ["Ariana", "Raoued", "Soukra", "Ettadhamen", "Cite Ennasr"],
    "Ben Arous": ["Ben Arous", "Rades", "Hammam Lif", "Ezzahra", "Mourouj"],
    "Manouba":   ["Manouba", "Denden", "Oued Ellil", "Tebourba", "Borj El Amri"],
    "Sousse":    ["Sousse", "Hammam Sousse", "Kantaoui", "Msaken", "Akouda"],
    "Monastir":  ["Monastir", "Skanes", "Ksar Hellal", "Moknine", "Teboulba"],
    "Nabeul":    ["Nabeul", "Hammamet", "Kelibia", "Korba", "Grombalia"],
    "Sfax":      ["Sfax", "Sakiet Eddaier", "Thyna", "Kerkenah", "El Ain"],
    "Bizerte":   ["Bizerte", "Menzel Bourguiba", "Mateur", "Sejnane", "Ras Jebel"],
    "Gabes":     ["Gabes", "El Hamma", "Mareth", "Matmata", "Nouvelle Matmata"],
    "Kairouan":  ["Kairouan", "Sbikha", "Haffouz", "Oueslatia", "Chebika"],
    "Gafsa":     ["Gafsa", "Metlaoui", "Redeyef", "El Ksar", "Om Larayes"],
    "Zaghouan":  ["Zaghouan", "Fahs", "Bir Mcherga", "Nadhour", "Saouaf"],
    "Siliana":   ["Siliana", "Makthar", "Rouhia", "Bou Arada", "Gaafour"],
    "Le Kef":    ["Le Kef", "Dahmani", "Sakiet Sidi Youssef", "Tajerouine", "Nebeur"],
}

REGION_CENTERS = {
    "Tunis":     (36.8065, 10.1815, 0.12, 28),
    "Ariana":    (36.8663, 10.1647, 0.09, 13),
    "Ben Arous": (36.7531, 10.2282, 0.09, 11),
    "Manouba":   (36.8081, 10.0983, 0.09, 7),
    "Sousse":    (35.8245, 10.6346, 0.18, 8),
    "Monastir":  (35.7643, 10.8113, 0.14, 6),
    "Nabeul":    (36.4561, 10.7376, 0.18, 6),
    "Sfax":      (34.7406, 10.7603, 0.22, 7),
    "Bizerte":   (37.2744, 9.8739,  0.18, 5),
    "Gabes":     (33.8814, 10.0982, 0.18, 4),
    "Kairouan":  (35.6781, 10.0963, 0.22, 3),
    "Gafsa":     (34.4250, 8.7842,  0.22, 2),
    "Zaghouan":  (36.4029, 10.1429, 0.18, 2),
    "Siliana":   (36.0853, 9.3708,  0.22, 2),
    "Le Kef":    (36.1822, 8.7147,  0.22, 1),
}

REGION_WEIGHTS = [v[3] for v in REGION_CENTERS.values()]
REGION_NAMES_LIST = list(REGION_CENTERS.keys())

# ── Profession data with regional weights ──
# (profession, sector, typical_segment, income_bracket, regional_bias)
PROFESSIONS = [
    # profession, sector, segment_bias, income, preferred_regions
    ("Student",              "Education",    "prepaid",    "low",    ["Tunis","Sousse","Sfax","Ariana","Monastir"]),
    ("Software Engineer",    "Technology",   "postpaid",   "high",   ["Tunis","Ariana","Ben Arous"]),
    ("Teacher",              "Education",    "postpaid",   "medium", REGIONS),
    ("Doctor",               "Healthcare",   "postpaid",   "high",   ["Tunis","Sfax","Sousse","Ariana"]),
    ("Nurse",                "Healthcare",   "postpaid",   "medium", ["Tunis","Sfax","Sousse","Monastir"]),
    ("Government Employee",  "Public",       "postpaid",   "medium", ["Tunis","Le Kef","Siliana","Kairouan"]),
    ("Business Owner",       "Commerce",     "enterprise", "high",   ["Sfax","Tunis","Sousse","Nabeul"]),
    ("Freelancer",           "Technology",   "postpaid",   "medium", ["Tunis","Ariana","Sousse"]),
    ("Driver",               "Logistics",    "prepaid",    "low",    REGIONS),
    ("Farmer",               "Agriculture",  "prepaid",    "low",    ["Kairouan","Gafsa","Siliana","Le Kef","Zaghouan"]),
    ("Electrician",          "Trades",       "prepaid",    "medium", REGIONS),
    ("Accountant",           "Finance",      "postpaid",   "medium", ["Tunis","Sfax","Sousse","Ariana"]),
    ("Lawyer",               "Legal",        "postpaid",   "high",   ["Tunis","Sfax","Sousse"]),
    ("Retired",              "None",         "prepaid",    "low",    REGIONS),
    ("Shop Owner",           "Commerce",     "postpaid",   "medium", REGIONS),
    ("Engineer",             "Industry",     "postpaid",   "high",   ["Tunis","Sfax","Sousse","Bizerte","Gabes"]),
    ("Journalist",           "Media",        "postpaid",   "medium", ["Tunis","Sfax","Sousse"]),
    ("Security Guard",       "Services",     "prepaid",    "low",    REGIONS),
    ("Chef",                 "Hospitality",  "prepaid",    "medium", ["Tunis","Sousse","Nabeul","Monastir"]),
    ("Tourism Agent",        "Tourism",      "postpaid",   "medium", ["Tunis","Sousse","Nabeul","Monastir","Gabes"]),
    ("University Professor", "Education",    "postpaid",   "high",   ["Tunis","Sfax","Sousse","Monastir"]),
    ("Pharmacist",           "Healthcare",   "postpaid",   "high",   ["Tunis","Sfax","Sousse","Ariana"]),
    ("Delivery Worker",      "Logistics",    "prepaid",    "low",    ["Tunis","Ariana","Ben Arous","Sousse","Sfax"]),
    ("Army/Police",          "Public",       "postpaid",   "medium", REGIONS),
    ("Unemployed",           "None",         "prepaid",    "low",    REGIONS),
]

LIFESTYLE_PATTERNS = [
    "heavy_streamer",
    "social_media_addict",
    "light_user",
    "business_user",
    "gamer",
    "remote_worker",
    "commuter",
    "traveler",
    "basic_caller",
    "content_creator",
]

USAGE_PEAK_TIMES = ["morning", "afternoon", "evening", "late_night", "all_day"]
LOCATION_PATTERNS = ["home_only", "commuter", "frequent_traveler", "regional", "international"]
PAYMENT_RELIABILITY = ["always_on_time", "occasional_late", "frequently_late", "defaulter"]
SPENDING_SENSITIVITY = ["price_conscious", "value_seeker", "quality_focused", "premium_willing"]
EDUCATION_LEVELS = ["no_formal", "primary", "secondary", "vocational", "bachelor", "master", "phd"]
HOUSING_TYPES = ["apartment", "house", "student_dorm", "shared_housing", "family_home"]
HOUSEHOLD_SIZES = [1, 2, 3, 4, 5, 6]

SEGMENTS = ["prepaid", "postpaid", "enterprise"]
TECHNOLOGIES = ["2G", "3G", "4G", "5G"]
BANDS = ["700MHz", "900MHz", "1800MHz", "2100MHz", "2600MHz", "3500MHz", "28GHz"]
SITE_TYPES = ["macro", "small_cell", "micro", "femto", "rooftop"]
COMPLAINT_TYPES = ["no_signal", "slow_data", "call_drop", "billing_issue", "roaming_issue", "app_issue", "network_outage", "poor_voice_quality"]
COMPLAINT_STATUS = ["open", "in_progress", "resolved", "closed"]
INTERACTION_CHANNELS = ["call_center", "app", "store_visit", "web_chat", "email", "social_media"]
INTERACTION_TYPES = ["complaint", "inquiry", "upgrade_request", "cancellation", "payment", "technical_support"]
ALARM_TYPES = ["high_interference", "cell_down", "capacity_exceeded", "power_failure", "backhaul_issue", "software_fault"]
ALARM_SEVERITY = ["critical", "major", "minor", "warning"]
INCIDENT_TYPES = ["outage", "degradation", "maintenance", "congestion"]
ROAMING_COUNTRIES = ["France", "Algeria", "Libya", "Italy", "Germany", "Saudi Arabia", "UAE", "Morocco", "Spain", "Qatar", "Turkey"]

DEVICE_CATALOG = [
    ("Samsung Galaxy S24",     "Samsung",  "Android",    True,  True),
    ("Samsung Galaxy A54",     "Samsung",  "Android",    True,  True),
    ("Samsung Galaxy A14",     "Samsung",  "Android",    False, True),
    ("Samsung Galaxy A04",     "Samsung",  "Android",    False, False),
    ("iPhone 15 Pro",          "Apple",    "iOS",        True,  True),
    ("iPhone 14",              "Apple",    "iOS",        True,  True),
    ("iPhone 13",              "Apple",    "iOS",        False, True),
    ("iPhone 12",              "Apple",    "iOS",        False, True),
    ("iPhone SE",              "Apple",    "iOS",        False, True),
    ("Huawei P60 Pro",         "Huawei",   "HarmonyOS",  True,  True),
    ("Huawei Nova 11",         "Huawei",   "HarmonyOS",  False, True),
    ("Huawei Y9a",             "Huawei",   "HarmonyOS",  False, False),
    ("Xiaomi 14",              "Xiaomi",   "Android",    True,  True),
    ("Xiaomi Redmi Note 13",   "Xiaomi",   "Android",    False, True),
    ("Xiaomi Redmi 12",        "Xiaomi",   "Android",    False, False),
    ("Oppo Reno 11",           "Oppo",     "Android",    True,  True),
    ("Oppo A78",               "Oppo",     "Android",    False, True),
    ("Vivo V29",               "Vivo",     "Android",    False, True),
    ("Google Pixel 8",         "Google",   "Android",    True,  True),
    ("Realme 11 Pro",          "Realme",   "Android",    False, True),
    ("Realme C55",             "Realme",   "Android",    False, False),
    ("Nokia G42",              "Nokia",    "Android",    False, False),
    ("Nokia C32",              "Nokia",    "Android",    False, False),
    ("Motorola Edge 40",       "Motorola", "Android",    True,  True),
    ("OnePlus 12",             "OnePlus",  "Android",    True,  True),
    ("Tecno Spark 20",         "Tecno",    "Android",    False, False),
    ("Infinix Hot 40",         "Infinix",  "Android",    False, False),
]

PLANS = [
    ("Mobi 5Go",           "prepaid",    5,   0,    8.0,   False, False),
    ("Mobi 10Go",          "prepaid",    10,  0,    14.0,  False, False),
    ("Mobi 20Go",          "prepaid",    20,  0,    20.0,  False, True),
    ("Standard 20Go",      "postpaid",   20,  500,  22.0,  False, True),
    ("Standard 30Go",      "postpaid",   30,  1000, 30.0,  False, True),
    ("Premium 50Go",       "postpaid",   50,  2000, 45.0,  True,  True),
    ("Premium 100Go",      "postpaid",   100, 5000, 60.0,  True,  True),
    ("Illimite Basic",     "postpaid",   -1,  1000, 39.0,  False, True),
    ("Illimite Premium",   "postpaid",   -1,  -1,   70.0,  True,  True),
    ("Enterprise 200Go",   "enterprise", 200, -1,   150.0, True,  True),
    ("Enterprise Illimite","enterprise", -1,  -1,   250.0, True,  True),
    ("Touriste 3Jours 3Go","prepaid",    3,   0,    6.0,   False, False),
    ("5G Starter 20Go",    "postpaid",   20,  1000, 35.0,  True,  True),
    ("5G Max Illimite",    "postpaid",   -1,  -1,   80.0,  True,  True),
    ("Maison 5G Illimite", "postpaid",   -1,  -1,   55.0,  True,  False),
    ("Etudiant 15Go",      "prepaid",    15,  0,    12.0,  False, True),
    ("Senior Confort",     "postpaid",   10,  2000, 18.0,  False, True),
]

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def random_date(start_days_ago=730, end_days_ago=0):
    start = datetime.now() - timedelta(days=start_days_ago)
    end   = datetime.now() - timedelta(days=end_days_ago)
    return start + timedelta(seconds=random.randint(0, int((end - start).total_seconds())))

def random_coords_tunisia(region=None):
    if region is None or region not in REGION_CENTERS:
        region = random.choices(REGION_NAMES_LIST, weights=REGION_WEIGHTS, k=1)[0]
    center_lat, center_lon, spread, _ = REGION_CENTERS[region]
    lat = round(random.gauss(center_lat, spread), 6)
    lon = round(random.gauss(center_lon, spread * 1.2), 6)
    lat = max(30.2, min(37.5, lat))
    lon = max(7.5,  min(11.6, lon))
    return lat, lon

def pick_profession_for_region(region):
    """Pick a profession weighted toward what's common in that region."""
    eligible = [(i, p) for i, p in enumerate(PROFESSIONS) if region in p[4] or len(p[4]) == len(REGIONS)]
    if not eligible:
        eligible = list(enumerate(PROFESSIONS))
    idx, prof = random.choice(eligible)
    return prof

def lifestyle_for_profession(profession_name):
    """Return a realistic lifestyle pattern based on profession."""
    mapping = {
        "Student":              random.choice(["social_media_addict", "gamer", "heavy_streamer"]),
        "Software Engineer":    random.choice(["remote_worker", "heavy_streamer", "business_user"]),
        "Freelancer":           random.choice(["remote_worker", "content_creator", "business_user"]),
        "Business Owner":       random.choice(["business_user", "traveler", "commuter"]),
        "Driver":               random.choice(["commuter", "basic_caller", "light_user"]),
        "Farmer":               random.choice(["basic_caller", "light_user"]),
        "Retired":              random.choice(["basic_caller", "light_user", "social_media_addict"]),
        "Tourism Agent":        random.choice(["traveler", "business_user", "commuter"]),
        "Delivery Worker":      random.choice(["commuter", "basic_caller"]),
    }
    return mapping.get(profession_name, random.choice(LIFESTYLE_PATTERNS))

def batch_insert(conn, sql, rows):
    conn.executemany(sql, rows)
    conn.commit()

# ─────────────────────────────────────────────
# CREATE TABLES
# ─────────────────────────────────────────────

def create_tables(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS device_catalog (
        device_id INTEGER PRIMARY KEY,
        model_name TEXT, brand TEXT, os TEXT,
        supports_5g INTEGER, supports_volte INTEGER
    );

    CREATE TABLE IF NOT EXISTS plans (
        plan_id INTEGER PRIMARY KEY,
        plan_name TEXT, plan_type TEXT,
        data_cap_gb INTEGER, call_minutes INTEGER,
        monthly_price REAL, supports_5g INTEGER, supports_volte INTEGER
    );

    CREATE TABLE IF NOT EXISTS customers (
        customer_id INTEGER PRIMARY KEY,
        full_name TEXT, email TEXT, phone_number TEXT,
        national_id TEXT, date_of_birth TEXT, gender TEXT,
        region TEXT, city TEXT, address TEXT,
        segment TEXT, registration_date TEXT,
        is_active INTEGER, latitude REAL, longitude REAL
    );

    CREATE TABLE IF NOT EXISTS customer_lifestyle (
        lifestyle_id INTEGER PRIMARY KEY,
        customer_id INTEGER UNIQUE,
        profession TEXT,
        job_sector TEXT,
        income_bracket TEXT,
        education_level TEXT,
        housing_type TEXT,
        household_size INTEGER,
        lifestyle_pattern TEXT,
        peak_usage_time TEXT,
        location_pattern TEXT,
        payment_reliability TEXT,
        spending_sensitivity TEXT,
        is_student INTEGER,
        has_children INTEGER,
        roaming_frequency TEXT,
        preferred_language TEXT,
        FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
    );

    CREATE TABLE IF NOT EXISTS subscriptions (
        subscription_id INTEGER PRIMARY KEY,
        customer_id INTEGER, plan_id INTEGER,
        start_date TEXT, end_date TEXT,
        is_current INTEGER, auto_renewal INTEGER,
        FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
        FOREIGN KEY (plan_id) REFERENCES plans(plan_id)
    );

    CREATE TABLE IF NOT EXISTS customer_devices (
        customer_device_id INTEGER PRIMARY KEY,
        customer_id INTEGER, device_id INTEGER,
        imei TEXT, assigned_date TEXT, is_primary INTEGER,
        FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
        FOREIGN KEY (device_id) REFERENCES device_catalog(device_id)
    );

    CREATE TABLE IF NOT EXISTS churn_scores (
        churn_score_id INTEGER PRIMARY KEY,
        customer_id INTEGER, score_date TEXT,
        churn_probability REAL, risk_level TEXT,
        contributing_factors TEXT,
        FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
    );

    CREATE TABLE IF NOT EXISTS complaints (
        complaint_id INTEGER PRIMARY KEY,
        customer_id INTEGER, complaint_type TEXT,
        description TEXT, submission_date TEXT,
        resolution_date TEXT, status TEXT,
        severity TEXT, channel TEXT, cell_id INTEGER,
        FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
    );

    CREATE TABLE IF NOT EXISTS customer_interactions (
        interaction_id INTEGER PRIMARY KEY,
        customer_id INTEGER, interaction_date TEXT,
        channel TEXT, interaction_type TEXT,
        duration_seconds INTEGER, resolved INTEGER,
        agent_id INTEGER, notes TEXT,
        FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
    );

    CREATE TABLE IF NOT EXISTS sites (
        site_id INTEGER PRIMARY KEY,
        site_name TEXT, region TEXT, city TEXT,
        latitude REAL, longitude REAL,
        site_type TEXT, installation_date TEXT,
        is_active INTEGER, owner TEXT, power_source TEXT
    );

    CREATE TABLE IF NOT EXISTS cells (
        cell_id INTEGER PRIMARY KEY,
        site_id INTEGER, cell_name TEXT,
        technology TEXT, frequency_band TEXT,
        azimuth INTEGER, height_meters REAL,
        max_capacity_users INTEGER,
        is_5g INTEGER, is_volte_enabled INTEGER, is_active INTEGER,
        FOREIGN KEY (site_id) REFERENCES sites(site_id)
    );

    CREATE TABLE IF NOT EXISTS kpis_daily (
        kpi_id INTEGER PRIMARY KEY,
        cell_id INTEGER, date TEXT,
        rsrp_avg REAL, rsrq_avg REAL, sinr_avg REAL,
        dl_throughput_mbps REAL, ul_throughput_mbps REAL,
        dropped_call_rate REAL, call_setup_success_rate REAL,
        handover_success_rate REAL, active_users_avg INTEGER,
        congestion_level TEXT, availability_pct REAL,
        packet_loss_pct REAL, latency_ms REAL,
        FOREIGN KEY (cell_id) REFERENCES cells(cell_id)
    );

    CREATE TABLE IF NOT EXISTS kpis_hourly (
        kpi_hourly_id INTEGER PRIMARY KEY,
        cell_id INTEGER, datetime TEXT,
        rsrp REAL, sinr REAL,
        dl_throughput_mbps REAL, active_users INTEGER,
        congestion_level TEXT,
        FOREIGN KEY (cell_id) REFERENCES cells(cell_id)
    );

    CREATE TABLE IF NOT EXISTS coverage_grid (
        grid_id INTEGER PRIMARY KEY,
        grid_name TEXT, region TEXT,
        center_lat REAL, center_lon REAL,
        grid_size_km REAL, coverage_type TEXT,
        avg_rsrp REAL, avg_sinr REAL,
        technology TEXT, population_density TEXT,
        last_updated TEXT
    );

    CREATE TABLE IF NOT EXISTS coverage_5g_zones (
        zone_id INTEGER PRIMARY KEY,
        zone_name TEXT, region TEXT,
        center_lat REAL, center_lon REAL,
        radius_km REAL, band TEXT,
        avg_dl_speed_mbps REAL,
        is_indoor_coverage INTEGER, launch_date TEXT
    );

    CREATE TABLE IF NOT EXISTS network_incidents (
        incident_id INTEGER PRIMARY KEY,
        cell_id INTEGER, incident_type TEXT,
        start_time TEXT, end_time TEXT,
        duration_minutes INTEGER, affected_users INTEGER,
        severity TEXT, root_cause TEXT, resolved INTEGER,
        FOREIGN KEY (cell_id) REFERENCES cells(cell_id)
    );

    CREATE TABLE IF NOT EXISTS network_alarms (
        alarm_id INTEGER PRIMARY KEY,
        cell_id INTEGER, alarm_type TEXT,
        severity TEXT, trigger_time TEXT,
        clear_time TEXT, is_active INTEGER, description TEXT,
        FOREIGN KEY (cell_id) REFERENCES cells(cell_id)
    );

    CREATE TABLE IF NOT EXISTS offers (
        offer_id INTEGER PRIMARY KEY,
        offer_name TEXT, target_segment TEXT,
        discount_pct REAL, bonus_data_gb INTEGER,
        validity_days INTEGER, start_date TEXT,
        end_date TEXT, is_active INTEGER,
        created_by TEXT, min_churn_risk REAL,
        description TEXT,
        target_profession TEXT,
        target_lifestyle TEXT,
        target_income TEXT
    );

    CREATE TABLE IF NOT EXISTS offer_assignments (
        assignment_id INTEGER PRIMARY KEY,
        offer_id INTEGER, customer_id INTEGER,
        assigned_date TEXT, accepted INTEGER, accepted_date TEXT,
        FOREIGN KEY (offer_id) REFERENCES offers(offer_id),
        FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
    );

    CREATE TABLE IF NOT EXISTS data_usage_daily (
        usage_id INTEGER PRIMARY KEY,
        customer_id INTEGER, date TEXT,
        data_used_gb REAL, cell_id INTEGER,
        peak_hour_usage_gb REAL, off_peak_usage_gb REAL,
        FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
    );

    CREATE TABLE IF NOT EXISTS cdr_transactions (
        cdr_id INTEGER PRIMARY KEY,
        customer_id INTEGER, cell_id INTEGER,
        start_time TEXT, end_time TEXT,
        duration_seconds INTEGER, transaction_type TEXT,
        data_used_mb REAL, call_result TEXT,
        technology_used TEXT, latitude REAL, longitude REAL,
        FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
    );

    CREATE TABLE IF NOT EXISTS billing (
        bill_id INTEGER PRIMARY KEY,
        customer_id INTEGER, billing_month TEXT,
        base_amount REAL, data_overage REAL,
        roaming_charges REAL, discounts REAL,
        total_amount REAL, due_date TEXT,
        paid_date TEXT, payment_status TEXT,
        FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
    );

    CREATE TABLE IF NOT EXISTS roaming_usage (
        roaming_id INTEGER PRIMARY KEY,
        customer_id INTEGER, country TEXT,
        start_date TEXT, end_date TEXT,
        data_used_gb REAL, calls_minutes INTEGER,
        total_charge REAL,
        FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
    );

    CREATE TABLE IF NOT EXISTS device_kpis (
        device_kpi_id INTEGER PRIMARY KEY,
        customer_id INTEGER, date TEXT,
        avg_signal_strength REAL,
        avg_battery_during_call REAL,
        volte_call_attempts INTEGER,
        volte_call_success INTEGER,
        app_crashes INTEGER,
        FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
    );

    CREATE TABLE IF NOT EXISTS cei_scores (
        cei_id INTEGER PRIMARY KEY,
        customer_id INTEGER, score_date TEXT,
        cei_score REAL, network_score REAL,
        service_score REAL, billing_score REAL, device_score REAL,
        FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
    );

    CREATE TABLE IF NOT EXISTS nps_surveys (
        nps_id INTEGER PRIMARY KEY,
        customer_id INTEGER, survey_date TEXT,
        score INTEGER, category TEXT, feedback TEXT,
        FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
    );

    -- INDEXES for query performance at 100k scale
    CREATE INDEX IF NOT EXISTS idx_customers_region   ON customers(region);
    CREATE INDEX IF NOT EXISTS idx_customers_segment  ON customers(segment);
    CREATE INDEX IF NOT EXISTS idx_churn_customer     ON churn_scores(customer_id);
    CREATE INDEX IF NOT EXISTS idx_churn_risk         ON churn_scores(risk_level);
    CREATE INDEX IF NOT EXISTS idx_lifestyle_customer ON customer_lifestyle(customer_id);
    CREATE INDEX IF NOT EXISTS idx_lifestyle_prof     ON customer_lifestyle(profession);
    CREATE INDEX IF NOT EXISTS idx_lifestyle_income   ON customer_lifestyle(income_bracket);
    CREATE INDEX IF NOT EXISTS idx_lifestyle_pattern  ON customer_lifestyle(lifestyle_pattern);
    CREATE INDEX IF NOT EXISTS idx_usage_customer     ON data_usage_daily(customer_id);
    CREATE INDEX IF NOT EXISTS idx_usage_date         ON data_usage_daily(date);
    CREATE INDEX IF NOT EXISTS idx_kpis_cell          ON kpis_daily(cell_id);
    CREATE INDEX IF NOT EXISTS idx_kpis_date          ON kpis_daily(date);
    CREATE INDEX IF NOT EXISTS idx_complaints_cust    ON complaints(customer_id);
    CREATE INDEX IF NOT EXISTS idx_billing_cust       ON billing(customer_id);
    CREATE INDEX IF NOT EXISTS idx_cei_customer       ON cei_scores(customer_id);
    CREATE INDEX IF NOT EXISTS idx_alarms_active      ON network_alarms(is_active);
    CREATE INDEX IF NOT EXISTS idx_subs_customer      ON subscriptions(customer_id);
    """)
    conn.commit()
    print("✅ Tables + indexes created.")

# ─────────────────────────────────────────────
# POPULATE
# ─────────────────────────────────────────────

def populate(conn):
    c = conn.cursor()

    # ── Device Catalog ──
    batch_insert(conn,
        "INSERT INTO device_catalog VALUES (?,?,?,?,?,?)",
        [(i+1, m, b, o, int(g), int(v)) for i,(m,b,o,g,v) in enumerate(DEVICE_CATALOG)]
    )
    print("✅ device_catalog")

    # ── Plans ──
    batch_insert(conn,
        "INSERT INTO plans VALUES (?,?,?,?,?,?,?,?)",
        [(i+1, n, pt, d, m, pr, int(g), int(v)) for i,(n,pt,d,m,pr,g,v) in enumerate(PLANS)]
    )
    print("✅ plans")

    # ── Customers + Lifestyle (batched) ──
    print(f"⏳ Generating {NUM_CUSTOMERS:,} customers...")
    cust_rows = []
    life_rows = []
    region_cache = {}  # cache region->profession pools

    for i in range(1, NUM_CUSTOMERS + 1):
        region = random.choices(REGION_NAMES_LIST, weights=REGION_WEIGHTS, k=1)[0]
        city   = random.choice(CITIES.get(region, [region]))
        lat, lon = random_coords_tunisia(region)
        prof   = pick_profession_for_region(region)

        profession_name  = prof[0]
        job_sector       = prof[1]
        segment_bias     = prof[2]
        income_bracket   = prof[3]

        # segment mostly follows profession bias
        segment = random.choices(
            [segment_bias, "prepaid", "postpaid"],
            weights=[70, 15, 15]
        )[0]

        is_student   = int(profession_name == "Student")
        has_children = random.choices([0, 1], weights=[40, 60])[0] if income_bracket in ["medium","high"] else random.choices([0,1], weights=[60,40])[0]
        roaming_freq = random.choices(
            ["never", "rare", "occasional", "frequent"],
            weights=[50, 25, 15, 10] if income_bracket == "low" else [20, 30, 30, 20]
        )[0]

        cust_rows.append((
            i,
            fake_name(),
            fake_email(),
            f"+216{random.randint(20000000, 99999999)}",
            fake_fr.bothify("??######"),
            fake_fr.date_of_birth(minimum_age=16, maximum_age=75).isoformat(),
            random.choice(["Male", "Female"]),
            region, city,
            fake_address(),
            segment,
            random_date(1825, 30).isoformat(),
            random.choices([1, 0], weights=[95, 5])[0],
            lat, lon
        ))

        life_rows.append((
            i, i,
            profession_name,
            job_sector,
            income_bracket,
            random.choice(EDUCATION_LEVELS),
            random.choice(HOUSING_TYPES),
            random.choice(HOUSEHOLD_SIZES),
            lifestyle_for_profession(profession_name),
            random.choice(USAGE_PEAK_TIMES),
            random.choice(LOCATION_PATTERNS),
            random.choice(PAYMENT_RELIABILITY),
            random.choice(SPENDING_SENSITIVITY),
            is_student,
            has_children,
            roaming_freq,
            random.choice(["Arabic", "French", "Both"])
        ))

        if i % BATCH_SIZE == 0 or i == NUM_CUSTOMERS:
            batch_insert(conn, "INSERT INTO customers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cust_rows)
            batch_insert(conn,
                "INSERT INTO customer_lifestyle VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                life_rows
            )
            cust_rows.clear()
            life_rows.clear()
            if i % 10000 == 0:
                print(f"   → {i:,} customers done")

    print(f"✅ customers ({NUM_CUSTOMERS:,})")
    print("✅ customer_lifestyle")

    # ── Subscriptions ──
    print("⏳ Subscriptions...")
    sub_rows = []
    all_segments = {r[0]: r[1] for r in conn.execute("SELECT customer_id, segment FROM customers")}
    for i in range(1, NUM_CUSTOMERS + 1):
        seg = all_segments[i]
        eligible = [j+1 for j,(n,pt,d,m,pr,g,v) in enumerate(PLANS)
                    if pt == seg or pt == "postpaid"]
        plan_id = random.choice(eligible) if eligible else random.randint(1, len(PLANS))
        start = random_date(730, 30)
        sub_rows.append((i, i, plan_id, start.isoformat(), (start+timedelta(days=365)).isoformat(), 1, random.randint(0,1)))
        if len(sub_rows) >= BATCH_SIZE:
            batch_insert(conn, "INSERT INTO subscriptions VALUES (?,?,?,?,?,?,?)", sub_rows)
            sub_rows.clear()
    if sub_rows:
        batch_insert(conn, "INSERT INTO subscriptions VALUES (?,?,?,?,?,?,?)", sub_rows)
    print("✅ subscriptions")

    # ── Customer Devices ──
    print("⏳ Devices...")
    dev_rows = []
    for i in range(1, NUM_CUSTOMERS + 1):
        dev_rows.append((i, i, random.randint(1, len(DEVICE_CATALOG)),
                         fake_fr.bothify("##############??"),
                         random_date(730, 0).isoformat(), 1))
        if len(dev_rows) >= BATCH_SIZE:
            batch_insert(conn, "INSERT INTO customer_devices VALUES (?,?,?,?,?,?)", dev_rows)
            dev_rows.clear()
    if dev_rows:
        batch_insert(conn, "INSERT INTO customer_devices VALUES (?,?,?,?,?,?)", dev_rows)
    print("✅ customer_devices")

    # ── Churn Scores ──
    print("⏳ Churn scores...")
    churn_rows = []
    churn_id = 1
    for cust_id in range(1, NUM_CUSTOMERS + 1):
        for _ in range(random.randint(1, 3)):
            prob = round(random.uniform(0.0, 1.0), 3)
            risk = "high" if prob > 0.7 else "medium" if prob > 0.4 else "low"
            factors = random.sample(["late_payment","low_usage","many_complaints",
                                     "competitor_offers","poor_signal","plan_mismatch"], k=random.randint(1,3))
            churn_rows.append((churn_id, cust_id, random_date(180,0).isoformat(),
                                prob, risk, ", ".join(factors)))
            churn_id += 1
        if len(churn_rows) >= BATCH_SIZE:
            batch_insert(conn, "INSERT INTO churn_scores VALUES (?,?,?,?,?,?)", churn_rows)
            churn_rows.clear()
    if churn_rows:
        batch_insert(conn, "INSERT INTO churn_scores VALUES (?,?,?,?,?,?)", churn_rows)
    print("✅ churn_scores")

    # ── Sites ──
    NUM_SITES = 200
    site_rows = []
    for i in range(1, NUM_SITES + 1):
        region = random.choices(REGION_NAMES_LIST, weights=REGION_WEIGHTS, k=1)[0]
        city   = random.choice(CITIES.get(region, [region]))
        lat, lon = random_coords_tunisia(region)
        site_rows.append((i, f"SITE-{str(i).zfill(4)}", region, city, lat, lon,
                          random.choice(SITE_TYPES), random_date(3650,365).isoformat(),
                          random.choices([1,0], weights=[95,5])[0],
                          random.choice(["Operator","Tower Company","Shared"]),
                          random.choice(["Grid","Solar+Grid","Generator"])))
    batch_insert(conn, "INSERT INTO sites VALUES (?,?,?,?,?,?,?,?,?,?,?)", site_rows)
    print(f"✅ sites ({NUM_SITES})")

    # ── Cells ──
    cell_rows = []
    cell_id = 1
    for site_id in range(1, NUM_SITES + 1):
        for _ in range(random.randint(2, 6)):
            tech = random.choice(TECHNOLOGIES)
            cell_rows.append((cell_id, site_id, f"CELL-{str(cell_id).zfill(5)}",
                               tech, random.choice(BANDS),
                               random.choice([0,60,120,180,240,300]),
                               round(random.uniform(15,60),1),
                               random.randint(50,500),
                               int(tech=="5G"), random.randint(0,1),
                               random.choices([1,0], weights=[96,4])[0]))
            cell_id += 1
    NUM_CELLS = cell_id - 1
    batch_insert(conn, "INSERT INTO cells VALUES (?,?,?,?,?,?,?,?,?,?,?)", cell_rows)
    print(f"✅ cells ({NUM_CELLS})")

    # ── KPIs Daily ──
    print("⏳ KPIs daily (90 days, all cells)...")
    kpi_rows = []
    kpi_id = 1
    for cid in range(1, NUM_CELLS + 1):
        for day_offset in range(90):
            date = (datetime.now() - timedelta(days=day_offset)).strftime("%Y-%m-%d")
            kpi_rows.append((kpi_id, cid, date,
                round(random.uniform(-120,-70),2), round(random.uniform(-20,-3),2),
                round(random.uniform(-5,30),2), round(random.uniform(5,300),2),
                round(random.uniform(1,50),2), round(random.uniform(0,5),3),
                round(random.uniform(85,100),2), round(random.uniform(88,100),2),
                random.randint(10,480), random.choice(["low","medium","high","critical"]),
                round(random.uniform(95,100),3), round(random.uniform(0,3),3),
                round(random.uniform(5,80),2)))
            kpi_id += 1
            if len(kpi_rows) >= BATCH_SIZE:
                batch_insert(conn, "INSERT INTO kpis_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", kpi_rows)
                kpi_rows.clear()
    if kpi_rows:
        batch_insert(conn, "INSERT INTO kpis_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", kpi_rows)
    print("✅ kpis_daily")

    # ── KPIs Hourly (7 days, sampled cells) ──
    print("⏳ KPIs hourly...")
    kpi_h_rows = []
    kpi_h_id = 1
    sampled_cells_h = random.sample(range(1, NUM_CELLS+1), min(50, NUM_CELLS))
    for cid in sampled_cells_h:
        for day_offset in range(7):
            for hour in range(24):
                dt = (datetime.now() - timedelta(days=day_offset, hours=hour)).strftime("%Y-%m-%d %H:00:00")
                kpi_h_rows.append((kpi_h_id, cid, dt,
                    round(random.uniform(-115,-70),2), round(random.uniform(-5,30),2),
                    round(random.uniform(5,300),2), random.randint(5,500),
                    random.choice(["low","medium","high","critical"])))
                kpi_h_id += 1
    batch_insert(conn, "INSERT INTO kpis_hourly VALUES (?,?,?,?,?,?,?,?)", kpi_h_rows)
    print("✅ kpis_hourly")

    # ── Coverage Grid ──
    grid_rows = []
    for i in range(1, 201):
        region = random.choices(REGION_NAMES_LIST, weights=REGION_WEIGHTS, k=1)[0]
        lat, lon = random_coords_tunisia(region)
        grid_rows.append((i, f"GRID-{str(i).zfill(4)}", region, lat, lon,
            round(random.uniform(0.5,5.0),2), random.choice(["urban","suburban","rural","highway"]),
            round(random.uniform(-120,-70),2), round(random.uniform(-5,30),2),
            random.choice(TECHNOLOGIES), random.choice(["high","medium","low","sparse"]),
            random_date(30,0).isoformat()))
    batch_insert(conn, "INSERT INTO coverage_grid VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", grid_rows)
    print("✅ coverage_grid")

    # ── 5G Coverage Zones ──
    URBAN_REGIONS  = ["Tunis","Ariana","Ben Arous","Sousse","Sfax","Monastir","Nabeul"]
    URBAN_WEIGHTS  = [25,15,12,10,8,8,7]
    zone_rows = []
    for i in range(1, 61):
        region = random.choices(URBAN_REGIONS, weights=URBAN_WEIGHTS, k=1)[0]
        lat, lon = random_coords_tunisia(region)
        zone_rows.append((i, f"5G-ZONE-{str(i).zfill(3)}", region, lat, lon,
            round(random.uniform(0.2,3.0),2), random.choice(["3500MHz","28GHz","700MHz"]),
            round(random.uniform(100,1000),2), random.randint(0,1),
            random_date(730,30).isoformat()))
    batch_insert(conn, "INSERT INTO coverage_5g_zones VALUES (?,?,?,?,?,?,?,?,?,?)", zone_rows)
    print("✅ coverage_5g_zones")

    # ── Network Incidents ──
    inc_rows = []
    for i in range(1, 501):
        cid   = random.randint(1, NUM_CELLS)
        start = random_date(365,1)
        dur   = random.randint(10,480)
        inc_rows.append((i, cid, random.choice(INCIDENT_TYPES), start.isoformat(),
            (start+timedelta(minutes=dur)).isoformat(), dur,
            random.randint(5,2000), random.choice(["low","medium","high","critical"]),
            random.choice(["hardware_fault","software_bug","power_issue","backhaul_failure","planned_maintenance","weather"]),
            random.randint(0,1)))
    batch_insert(conn, "INSERT INTO network_incidents VALUES (?,?,?,?,?,?,?,?,?,?)", inc_rows)
    print("✅ network_incidents")

    # ── Network Alarms ──
    alarm_rows = []
    for i in range(1, 801):
        cid     = random.randint(1, NUM_CELLS)
        trigger = random_date(90,0)
        active  = random.randint(0,1)
        alarm_rows.append((i, cid, random.choice(ALARM_TYPES), random.choice(ALARM_SEVERITY),
            trigger.isoformat(),
            (trigger+timedelta(hours=random.randint(1,48))).isoformat() if not active else None,
            active, fake_sentence()))
    batch_insert(conn, "INSERT INTO network_alarms VALUES (?,?,?,?,?,?,?,?)", alarm_rows)
    print("✅ network_alarms")

    # ── Complaints (sampled 30% of customers) ──
    print("⏳ Complaints...")
    comp_rows = []
    comp_id = 1
    sampled_complainers = random.sample(range(1, NUM_CUSTOMERS+1), int(NUM_CUSTOMERS*0.3))
    for cust_id in sampled_complainers:
        for _ in range(random.choices([1,2,3,5], weights=[50,25,15,10])[0]):
            submitted = random_date(365,0)
            status    = random.choice(COMPLAINT_STATUS)
            resolved  = (submitted+timedelta(days=random.randint(1,14))).isoformat() if status in ["resolved","closed"] else None
            comp_rows.append((comp_id, cust_id, random.choice(COMPLAINT_TYPES), fake_sentence(),
                submitted.isoformat(), resolved, status,
                random.choice(["low","medium","high"]),
                random.choice(INTERACTION_CHANNELS), random.randint(1, NUM_CELLS)))
            comp_id += 1
            if len(comp_rows) >= BATCH_SIZE:
                batch_insert(conn, "INSERT INTO complaints VALUES (?,?,?,?,?,?,?,?,?,?)", comp_rows)
                comp_rows.clear()
    if comp_rows:
        batch_insert(conn, "INSERT INTO complaints VALUES (?,?,?,?,?,?,?,?,?,?)", comp_rows)
    print("✅ complaints")

    # ── Customer Interactions (sampled 40%) ──
    print("⏳ Interactions...")
    inter_rows = []
    inter_id = 1
    for cust_id in random.sample(range(1, NUM_CUSTOMERS+1), int(NUM_CUSTOMERS*0.4)):
        for _ in range(random.randint(1,5)):
            inter_rows.append((inter_id, cust_id, random_date(365,0).isoformat(),
                random.choice(INTERACTION_CHANNELS), random.choice(INTERACTION_TYPES),
                random.randint(30,1800), random.randint(0,1),
                random.randint(1,100), fake_sentence()))
            inter_id += 1
            if len(inter_rows) >= BATCH_SIZE:
                batch_insert(conn, "INSERT INTO customer_interactions VALUES (?,?,?,?,?,?,?,?,?)", inter_rows)
                inter_rows.clear()
    if inter_rows:
        batch_insert(conn, "INSERT INTO customer_interactions VALUES (?,?,?,?,?,?,?,?,?)", inter_rows)
    print("✅ customer_interactions")

    # ── Offers (with lifestyle targeting) ──
    offers_data = [
        ("Offre Fidelite 20%",      "postpaid",   20.0, 10,  30, "any",        "any",              "any",    0.3),
        ("Retour Client Premium",   "postpaid",   30.0, 20,  60, "any",        "any",              "any",    0.7),
        ("5G Pionnier",             "postpaid",   15.0, 50,  30, "any",        "any",              "high",   0.2),
        ("Offre Ramadan",           "prepaid",    25.0, 15,  30, "any",        "any",              "any",    0.0),
        ("Remise Etudiant",         "prepaid",    35.0, 20,  90, "Student",    "social_media_addict","low",  0.0),
        ("Pack Gamer",              "postpaid",   20.0, 30,  30, "any",        "gamer",            "any",    0.0),
        ("Remote Worker Pro",       "postpaid",   10.0, 50,  60, "any",        "remote_worker",    "medium", 0.0),
        ("Business Elite",          "enterprise", 15.0, 100, 90, "Business Owner","business_user", "high",  0.2),
        ("Tarif Senior",            "prepaid",    30.0, 5,   60, "Retired",    "basic_caller",     "low",   0.0),
        ("Pack Roaming Europe",     "postpaid",   20.0, 10,  30, "any",        "traveler",         "any",   0.0),
        ("Blitz Data Weekend",      "prepaid",    15.0, 25,  7,  "any",        "heavy_streamer",   "any",   0.0),
        ("Offre Agriculteur",       "prepaid",    20.0, 5,   30, "Farmer",     "basic_caller",     "low",   0.0),
        ("Pack Freelance",          "postpaid",   10.0, 30,  30, "Freelancer", "remote_worker",    "medium",0.3),
        ("Bundle Anti-Churn",       "postpaid",   40.0, 20,  90, "any",        "any",              "any",   0.6),
        ("Promotion Nouvel An",     "postpaid",   25.0, 30,  14, "any",        "any",              "any",   0.0),
    ]
    offer_rows = []
    for i, (name, seg, disc, bonus, validity, prof, lifestyle, income, min_churn) in enumerate(offers_data, 1):
        start = random_date(180,30)
        offer_rows.append((i, name, seg, disc, bonus, validity,
            start.isoformat(), (start+timedelta(days=90)).isoformat(),
            random.randint(0,1), random.choice(["system","agent","marketing_team"]),
            min_churn, fake_sentence(),
            None if prof == "any" else prof,
            None if lifestyle == "any" else lifestyle,
            None if income == "any" else income))
    batch_insert(conn,
        "INSERT INTO offers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        offer_rows)
    print("✅ offers")

    # ── Offer Assignments (5% of customers) ──
    assign_rows = []
    for assign_id, cust_id in enumerate(random.sample(range(1, NUM_CUSTOMERS+1), int(NUM_CUSTOMERS*0.05)), 1):
        offer_id = random.randint(1, len(offers_data))
        assigned = random_date(180,0)
        accepted = random.randint(0,1)
        assign_rows.append((assign_id, offer_id, cust_id, assigned.isoformat(), accepted,
            (assigned+timedelta(days=random.randint(0,7))).isoformat() if accepted else None))
    batch_insert(conn, "INSERT INTO offer_assignments VALUES (?,?,?,?,?,?)", assign_rows)
    print("✅ offer_assignments")

    # ── Data Usage Daily (all customers, 30 days) ──
    print("⏳ Data usage (30 days × 100k)...")
    usage_rows = []
    usage_id = 1
    for cust_id in range(1, NUM_CUSTOMERS+1):
        for day_offset in range(30):
            date  = (datetime.now()-timedelta(days=day_offset)).strftime("%Y-%m-%d")
            total = round(random.uniform(0.01,15.0),3)
            peak  = round(total*random.uniform(0.3,0.7),3)
            usage_rows.append((usage_id, cust_id, date, total,
                random.randint(1, NUM_CELLS), peak, round(total-peak,3)))
            usage_id += 1
            if len(usage_rows) >= BATCH_SIZE:
                batch_insert(conn, "INSERT INTO data_usage_daily VALUES (?,?,?,?,?,?,?)", usage_rows)
                usage_rows.clear()
        if cust_id % 10000 == 0:
            print(f"   → usage: {cust_id:,}")
    if usage_rows:
        batch_insert(conn, "INSERT INTO data_usage_daily VALUES (?,?,?,?,?,?,?)", usage_rows)
    print("✅ data_usage_daily")

    # ── CDR Transactions (10% of customers) ──
    print("⏳ CDR transactions...")
    cdr_rows = []
    cdr_id = 1
    for cust_id in random.sample(range(1, NUM_CUSTOMERS+1), int(NUM_CUSTOMERS*0.1)):
        for _ in range(random.randint(5,20)):
            start = random_date(90,0)
            dur   = random.randint(0,3600)
            lat, lon = random_coords_tunisia(random.choices(REGION_NAMES_LIST, weights=REGION_WEIGHTS, k=1)[0])
            cdr_rows.append((cdr_id, cust_id, random.randint(1,NUM_CELLS),
                start.isoformat(), (start+timedelta(seconds=dur)).isoformat(), dur,
                random.choice(["voice_call","video_call","data","sms","volte_call"]),
                round(random.uniform(0,500),2),
                random.choice(["completed","dropped","failed","busy"]),
                random.choice(TECHNOLOGIES), lat, lon))
            cdr_id += 1
            if len(cdr_rows) >= BATCH_SIZE:
                batch_insert(conn, "INSERT INTO cdr_transactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", cdr_rows)
                cdr_rows.clear()
    if cdr_rows:
        batch_insert(conn, "INSERT INTO cdr_transactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", cdr_rows)
    print("✅ cdr_transactions")

    # ── Billing (6 months, all customers) ──
    print("⏳ Billing...")
    bill_rows = []
    bill_id = 1
    for cust_id in range(1, NUM_CUSTOMERS+1):
        for month_offset in range(6):
            bm    = (datetime.now()-timedelta(days=30*month_offset)).strftime("%Y-%m")
            base  = round(random.uniform(8,250),2)
            over  = round(random.uniform(0,30),2)
            roam  = round(random.uniform(0,80),2)
            disc  = round(random.uniform(0,20),2)
            total = round(base+over+roam-disc,2)
            status= random.choice(["paid","paid","paid","unpaid","overdue"])
            due   = (datetime.now()-timedelta(days=30*month_offset-14)).strftime("%Y-%m-%d")
            paid  = (datetime.now()-timedelta(days=random.randint(0,14))).strftime("%Y-%m-%d") if status=="paid" else None
            bill_rows.append((bill_id, cust_id, bm, base, over, roam, disc, total, due, paid, status))
            bill_id += 1
            if len(bill_rows) >= BATCH_SIZE:
                batch_insert(conn, "INSERT INTO billing VALUES (?,?,?,?,?,?,?,?,?,?,?)", bill_rows)
                bill_rows.clear()
        if cust_id % 10000 == 0:
            print(f"   → billing: {cust_id:,}")
    if bill_rows:
        batch_insert(conn, "INSERT INTO billing VALUES (?,?,?,?,?,?,?,?,?,?,?)", bill_rows)
    print("✅ billing")

    # ── Roaming (5% of customers) ──
    roam_rows = []
    roam_id = 1
    for cust_id in random.sample(range(1, NUM_CUSTOMERS+1), int(NUM_CUSTOMERS*0.05)):
        for _ in range(random.randint(1,3)):
            start = random_date(365,30)
            end   = start+timedelta(days=random.randint(3,21))
            roam_rows.append((roam_id, cust_id, random.choice(ROAMING_COUNTRIES),
                start.isoformat(), end.isoformat(),
                round(random.uniform(0.1,10.0),3), random.randint(0,300),
                round(random.uniform(10,300),2)))
            roam_id += 1
    batch_insert(conn, "INSERT INTO roaming_usage VALUES (?,?,?,?,?,?,?,?)", roam_rows)
    print("✅ roaming_usage")

    # ── Device KPIs (sampled 20% customers, 14 days) ──
    print("⏳ Device KPIs...")
    dkpi_rows = []
    dkpi_id = 1
    for cust_id in random.sample(range(1, NUM_CUSTOMERS+1), int(NUM_CUSTOMERS*0.2)):
        for day_offset in range(14):
            date = (datetime.now()-timedelta(days=day_offset)).strftime("%Y-%m-%d")
            dkpi_rows.append((dkpi_id, cust_id, date,
                round(random.uniform(-115,-70),2), round(random.uniform(20,100),1),
                random.randint(0,20), random.randint(0,20), random.randint(0,5)))
            dkpi_id += 1
            if len(dkpi_rows) >= BATCH_SIZE:
                batch_insert(conn, "INSERT INTO device_kpis VALUES (?,?,?,?,?,?,?,?)", dkpi_rows)
                dkpi_rows.clear()
    if dkpi_rows:
        batch_insert(conn, "INSERT INTO device_kpis VALUES (?,?,?,?,?,?,?,?)", dkpi_rows)
    print("✅ device_kpis")

    # ── CEI Scores (sampled 30% customers, 8 weeks) ──
    print("⏳ CEI scores...")
    cei_rows = []
    cei_id = 1
    for cust_id in random.sample(range(1, NUM_CUSTOMERS+1), int(NUM_CUSTOMERS*0.3)):
        for week_offset in range(8):
            date    = (datetime.now()-timedelta(weeks=week_offset)).strftime("%Y-%m-%d")
            network = round(random.uniform(40,100),2)
            service = round(random.uniform(40,100),2)
            billing = round(random.uniform(40,100),2)
            device  = round(random.uniform(40,100),2)
            cei     = round(network*0.4+service*0.3+billing*0.2+device*0.1, 2)
            cei_rows.append((cei_id, cust_id, date, cei, network, service, billing, device))
            cei_id += 1
            if len(cei_rows) >= BATCH_SIZE:
                batch_insert(conn, "INSERT INTO cei_scores VALUES (?,?,?,?,?,?,?,?)", cei_rows)
                cei_rows.clear()
    if cei_rows:
        batch_insert(conn, "INSERT INTO cei_scores VALUES (?,?,?,?,?,?,?,?)", cei_rows)
    print("✅ cei_scores")

    # ── NPS Surveys (10% of customers) ──
    nps_rows = []
    for nps_id, cust_id in enumerate(random.sample(range(1, NUM_CUSTOMERS+1), int(NUM_CUSTOMERS*0.1)), 1):
        score    = random.randint(0,10)
        category = "promoter" if score>=9 else "passive" if score>=7 else "detractor"
        nps_rows.append((nps_id, cust_id, random_date(180,0).isoformat(), score, category, fake_sentence()))
    batch_insert(conn, "INSERT INTO nps_surveys VALUES (?,?,?,?,?,?)", nps_rows)
    print("✅ nps_surveys")

    print(f"\n🎉 Done! Database ready at: {DB_PATH}")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import time
    import os
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print(f"🗑️  Removed old {DB_PATH}")

    start_time = time.time()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=100000")
    conn.execute("PRAGMA temp_store=MEMORY")
    create_tables(conn)
    populate(conn)
    conn.close()
    elapsed = time.time() - start_time
    print(f"⏱️  Total time: {elapsed/60:.1f} minutes")