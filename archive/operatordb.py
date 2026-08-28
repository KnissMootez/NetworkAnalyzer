import sqlite3, random
from datetime import datetime, timedelta
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
random.seed(99)

FIRST_NAMES = ["Ahmed","Mohamed","Ali","Omar","Youssef","Hamza","Amine","Bilel","Khalil",
               "Sami","Nawres","Slim","Rami","Tarek","Bassem","Hatem","Nizar","Aymen",
               "Fatma","Mariem","Ines","Sonia","Rania","Leila","Amira","Hanen","Dorra",
               "Salma","Olfa","Rim","Nour","Asma","Sara","Maha","Hela","Wafa","Raja"]
LAST_NAMES  = ["Ben Ali","Ben Salah","Trabelsi","Gharbi","Hamdi","Jebali","Mansouri",
               "Dridi","Chaabane","Belhaj","Mzoughi","Oueslati","Ayari","Bouazizi",
               "Khemiri","Amri","Nasr","Saidi","Ferjani","Riahi","Jerbi","Abidi"]

def rand_name():
    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"

def rand_date(start=1825, end=0):
    d = datetime.now() - timedelta(days=random.randint(end, start))
    return d.strftime("%Y-%m-%d")

print("Building Operator DB...")

# Read MSISDNs from NetworkAnalyzer DB — this is the overlap
sc = sqlite3.connect(os.path.join(BASE_DIR, "NetworkAnalyzer_new.db"))
sc.row_factory = sqlite3.Row
msisdns = [r[0] for r in sc.execute("SELECT msisdn, region FROM subscribers").fetchall()]
sub_regions = {r[0]: r[1] for r in sc.execute("SELECT msisdn, region FROM subscribers").fetchall()}
sub_tech = {r[0]: r[1] for r in sc.execute("SELECT msisdn, current_technology FROM subscriber_technology").fetchall()}
sub_dou = {r[0]: r[1] for r in sc.execute("""
    SELECT msisdn, total_data_gb FROM dou_monthly
    WHERE month=(SELECT MAX(month) FROM dou_monthly)""").fetchall()}
sub_mobility = {r[0]: r[1] for r in sc.execute("""
    SELECT msisdn, mobility_class FROM mobility_profile
    WHERE month=(SELECT MAX(month) FROM mobility_profile)""").fetchall()}
sc.close()

conn = sqlite3.connect(os.path.join(BASE_DIR,"operator_new.db"))
conn.execute("PRAGMA journal_mode=WAL")
c = conn.cursor()

c.executescript("""
DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS plans;
DROP TABLE IF EXISTS subscriptions;
DROP TABLE IF EXISTS offers;
DROP TABLE IF EXISTS offer_assignments;
DROP TABLE IF EXISTS campaigns;
DROP TABLE IF EXISTS campaign_targets;
DROP TABLE IF EXISTS sms_log;
DROP TABLE IF EXISTS billing;
DROP TABLE IF EXISTS customer_value;

CREATE TABLE customers (
    msisdn TEXT PRIMARY KEY,
    full_name TEXT,
    national_id TEXT,
    date_of_birth TEXT,
    gender TEXT,
    email TEXT,
    address TEXT,
    region TEXT,
    city TEXT,
    segment TEXT,
    registration_date TEXT,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE plans (
    plan_id INTEGER PRIMARY KEY,
    plan_name TEXT,
    plan_type TEXT,
    data_cap_gb REAL,
    speed_mbps REAL,
    monthly_price REAL,
    supports_5g INTEGER DEFAULT 0,
    supports_volte INTEGER DEFAULT 0,
    is_fwa_plan INTEGER DEFAULT 0,
    is_mbb_plan INTEGER DEFAULT 0,
    description TEXT
);

CREATE TABLE subscriptions (
    msisdn TEXT,
    plan_id INTEGER,
    start_date TEXT,
    end_date TEXT,
    is_current INTEGER DEFAULT 1,
    contract_type TEXT,
    auto_renewal INTEGER DEFAULT 0,
    PRIMARY KEY(msisdn, plan_id),
    FOREIGN KEY(msisdn) REFERENCES customers(msisdn),
    FOREIGN KEY(plan_id) REFERENCES plans(plan_id)
);

CREATE TABLE offers (
    offer_id INTEGER PRIMARY KEY,
    offer_name TEXT,
    target_campaign TEXT,
    target_technology TEXT,
    discount_pct REAL,
    bonus_data_gb REAL,
    price_override REAL,
    validity_days INTEGER,
    is_active INTEGER DEFAULT 1,
    description TEXT
);

CREATE TABLE offer_assignments (
    msisdn TEXT,
    offer_id INTEGER,
    assigned_date TEXT,
    channel TEXT,
    accepted INTEGER DEFAULT 0,
    accepted_date TEXT,
    PRIMARY KEY(msisdn, offer_id)
);

CREATE TABLE campaigns (
    campaign_id INTEGER PRIMARY KEY,
    campaign_name TEXT,
    campaign_type TEXT,
    target_count INTEGER,
    launched_date TEXT,
    status TEXT DEFAULT 'draft',
    created_by TEXT DEFAULT 'agent',
    offer_id INTEGER,
    FOREIGN KEY(offer_id) REFERENCES offers(offer_id)
);

CREATE TABLE campaign_targets (
    campaign_id INTEGER,
    msisdn TEXT,
    reason TEXT,
    score REAL,
    notified INTEGER DEFAULT 0,
    converted INTEGER DEFAULT 0,
    PRIMARY KEY(campaign_id, msisdn)
);

CREATE TABLE sms_log (
    sms_id INTEGER PRIMARY KEY AUTOINCREMENT,
    msisdn TEXT,
    campaign_id INTEGER,
    sent_date TEXT,
    message_text TEXT,
    status TEXT DEFAULT 'sent',
    response TEXT DEFAULT 'none'
);

CREATE TABLE billing (
    msisdn TEXT,
    billing_month TEXT,
    total_amount REAL,
    data_charges REAL,
    voice_charges REAL,
    payment_status TEXT,
    payment_date TEXT,
    PRIMARY KEY(msisdn, billing_month)
);

CREATE TABLE customer_value (
    msisdn TEXT,
    month TEXT,
    arpu REAL,
    value_segment TEXT,
    is_hvc INTEGER DEFAULT 0,
    PRIMARY KEY(msisdn, month)
);
""")
conn.commit()
print("  Schema created")

# ═══════════════════════════════════════════════════════
# PLANS
# ═══════════════════════════════════════════════════════

# Prices based on real 2024-2025 Tunisian operator tariffs
# (Tunisie Telecom / Ooredoo / Orange Tunisia — THD comparisons)
plans = [
    # id  name               type     data_gb  spd_mbps  price_TND  5g  volte fwa mbb  description
    (1,  "Basic 2G/3G",     "voice",   1,    0.5,    5.0, 0, 0, 0, 0, "Basic prepaid voice + 1 GB — ~5 TND recharge"),
    (2,  "Starter 3G",      "data",    5,    7.2,   10.0, 0, 0, 0, 0, "Entry 3G — Ooredoo/Orange 2-4 GB @ 10 TND/month"),
    (3,  "Essential 4G",    "bundle",  15,   50,    15.0, 0, 1, 0, 0, "4G everyday — Orange 6 GB @ 15 TND"),
    (4,  "Smart 4G",        "bundle",  25,   100,   30.0, 0, 1, 0, 0, "4G mid-range — all operators 25 GB @ 30 TND"),
    (5,  "Pro 4G",          "bundle",  42,   150,   46.0, 0, 1, 0, 0, "4G premium — Orange 42 GB @ 46.20 TND"),
    (6,  "Unlimited 4G",    "bundle",  999,  150,   50.0, 0, 1, 0, 0, "4G unlimited — Ooredoo 55 GB @ 50 TND"),
    (7,  "5G Starter",      "bundle",  25,   500,   30.0, 1, 1, 0, 0, "5G entry — all operators 25 GB 5G @ 30 TND"),
    (8,  "5G Pro",          "bundle",  100,  1000,  72.0, 1, 1, 0, 0, "5G pro — Orange 100 GB @ 72 TND / TT 110 GB @ 80 TND"),
    (9,  "5G Unlimited",    "bundle",  200,  1000, 100.0, 1, 1, 0, 0, "5G unlimited — Orange 200 GB @ 100 TND"),
    (10, "MBB Basic",       "MBB",     20,   50,   20.0, 0, 0, 0, 1, "Mobile broadband basic dongle"),
    (11, "MBB Pro",         "MBB",     100,  100,  42.0, 0, 0, 0, 1, "Mobile broadband pro — ~42 TND/month"),
    (12, "FWA Home 30Mbps", "FWA",     999,  30,   59.9, 1, 1, 1, 0, "5G FWA home 30 Mbps — Ooredoo/TT @ 59.9 TND/month"),
    (13, "FWA Home 100Mbps","FWA",     999,  100,  99.9, 1, 1, 1, 0, "5G FWA home 100 Mbps — Ooredoo/TT @ 99.9 TND/month"),
    (14, "Enterprise 5G",   "bundle",  999,  2000, 130.0, 1, 1, 0, 0, "Enterprise 5G dedicated — Ooredoo Business ~130 TND"),
    (15, "VoLTE Ready 4G",  "bundle",  30,   100,  36.0, 0, 1, 0, 0, "4G postpaid VoLTE — Orange 30 GB @ 36 TND"),
]
c.executemany("INSERT INTO plans VALUES(?,?,?,?,?,?,?,?,?,?,?)", plans)
conn.commit()
print(f"  {len(plans)} plans created")

# ═══════════════════════════════════════════════════════
# CUSTOMERS — one per MSISDN from NetworkAnalyzer
# ═══════════════════════════════════════════════════════

print(f"  Generating {len(msisdns)} customers...")
customers_data = []
sub_segment = {}  # msisdn -> segment (used later for plan assignment)
segments = ["prepaid","postpaid","enterprise"]
seg_weights = [0.55, 0.40, 0.05]

for msisdn in msisdns:
    region = sub_regions.get(msisdn, "Tunis")
    gender = random.choice(["M","F"])
    name = rand_name()
    dob = rand_date(start=25550, end=6570)
    email = f"{name.split()[0].lower()}.{name.split()[1].lower()}{random.randint(1,999)}@{'gmail' if random.random()<0.6 else 'yahoo'}.com"
    segment = random.choices(segments, weights=seg_weights)[0]
    sub_segment[msisdn] = segment
    reg_date = rand_date(start=2555, end=30)
    customers_data.append((msisdn, name, f"TN{random.randint(10000000,99999999)}",
                           dob, gender, email, f"{region} Street {random.randint(1,999)}",
                           region, region, segment, reg_date, 1))

c.executemany("INSERT OR IGNORE INTO customers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", customers_data)
conn.commit()
print(f"  {len(customers_data)} customers created")

# ═══════════════════════════════════════════════════════
# SUBSCRIPTIONS — based on current technology
# ═══════════════════════════════════════════════════════

mbb_msisdns = set()
subs_data = []
sub_plan_map = {}

for msisdn in msisdns:
    current_tech = sub_tech.get(msisdn, "4G")
    seg   = sub_segment.get(msisdn, "prepaid")
    dou   = sub_dou.get(msisdn, 5)
    mob   = sub_mobility.get(msisdn, "medium")

    # MBB if high DOU and stationary/low mobility
    is_mbb = (dou > 25 and mob in ["stationary","low"] and random.random() < 0.3)
    if is_mbb:
        plan_id = random.choice([10, 11])
        mbb_msisdns.add(msisdn)
    else:
        # Plan selection by technology + customer segment
        # 3G subscribers: prepaid stay cheap, postpaid/enterprise can be on mid-range plans
        # (real scenario: postpaid contract, old device, or rural area without 4G upgrade)
        if current_tech == "2G":
            plan_id = 1
        elif current_tech == "3G":
            if seg == "enterprise":
                plan_id = random.choices([3, 4, 15],   weights=[0.4, 0.4, 0.2])[0]
            elif seg == "postpaid":
                plan_id = random.choices([2, 3, 4],    weights=[0.4, 0.4, 0.2])[0]
            else:  # prepaid
                plan_id = random.choices([1, 2],        weights=[0.3, 0.7])[0]
        elif current_tech == "4G":
            if seg == "enterprise":
                plan_id = random.choices([5, 6, 14, 15], weights=[0.25, 0.25, 0.25, 0.25])[0]
            elif seg == "postpaid":
                plan_id = random.choices([3, 4, 5, 6, 15], weights=[0.15, 0.30, 0.25, 0.15, 0.15])[0]
            else:  # prepaid
                plan_id = random.choices([3, 4, 5],     weights=[0.50, 0.35, 0.15])[0]
        elif current_tech == "5G":
            if seg == "enterprise":
                plan_id = random.choices([8, 9, 14],    weights=[0.3, 0.3, 0.4])[0]
            elif seg == "postpaid":
                plan_id = random.choices([7, 8, 9],     weights=[0.4, 0.4, 0.2])[0]
            else:  # prepaid 5G
                plan_id = random.choices([7, 8],        weights=[0.7, 0.3])[0]
        else:
            plan_id = 4  # fallback
    
    contract = random.choices(["monthly","annual"], weights=[0.6,0.4])[0]
    start = rand_date(start=730, end=30)
    end_days = 30 if contract == "monthly" else 365
    end = (datetime.strptime(start, "%Y-%m-%d") + timedelta(days=end_days)).strftime("%Y-%m-%d")
    auto = random.choices([1,0], weights=[0.4,0.6])[0]
    
    subs_data.append((msisdn, plan_id, start, end, 1, contract, auto))
    sub_plan_map[msisdn] = plan_id

c.executemany("INSERT OR IGNORE INTO subscriptions VALUES(?,?,?,?,?,?,?)", subs_data)
conn.commit()
print(f"  {len(subs_data)} subscriptions created")

# ═══════════════════════════════════════════════════════
# BILLING & CUSTOMER VALUE — last 6 months
# ═══════════════════════════════════════════════════════

plan_prices = {p[0]: p[5] for p in plans}
months = [(datetime.now() - timedelta(days=30*i)).strftime("%Y-%m") for i in range(5,-1,-1)]

billing_data = []; value_data = []
for msisdn in msisdns:
    plan_id = sub_plan_map.get(msisdn, 4)
    base_price = plan_prices.get(plan_id, 29)
    
    for month in months:
        total = max(0, base_price + random.gauss(0, base_price*0.1))
        data_c = total * random.uniform(0.5, 0.8)
        voice_c = total - data_c
        paid = random.choices(["paid","unpaid","overdue"], weights=[0.85,0.10,0.05])[0]
        pay_date = rand_date(start=25, end=0) if paid == "paid" else None
        billing_data.append((msisdn, month, round(total,2), round(data_c,2),
                             round(voice_c,2), paid, pay_date))
        
        arpu = round(total, 2)
        # Thresholds calibrated to real Tunisian tariffs (2024-2025):
        #   platinum >= 75 TND : FWA 100 Mbps / 5G Unlimited / Enterprise
        #   gold     >= 35 TND : 5G Starter / Pro 4G / FWA 30 Mbps / premium postpaid
        #   silver   >= 15 TND : Smart 4G / mid-range prepaid
        #   bronze    < 15 TND : Basic/Starter 3G prepaid
        if arpu >= 75:    seg = "platinum"; hvc = 1
        elif arpu >= 35:  seg = "gold";     hvc = 1
        elif arpu >= 15:  seg = "silver";   hvc = 0
        else:             seg = "bronze";   hvc = 0
        value_data.append((msisdn, month, arpu, seg, hvc))

c.executemany("INSERT OR IGNORE INTO billing VALUES(?,?,?,?,?,?,?)", billing_data)
c.executemany("INSERT OR IGNORE INTO customer_value VALUES(?,?,?,?,?)", value_data)
conn.commit()
print(f"  {len(billing_data)} billing records, {len(value_data)} value records")

# ═══════════════════════════════════════════════════════
# OFFERS
# ═══════════════════════════════════════════════════════

offers = [
    (1, "Pack 5G Discovery",     "5G_upsell",    "5G", 20, 0,   30.0, 30, 1, "5G Starter 25 GB at 20% off — 24 TND first month"),
    (2, "5G Premium Upgrade",    "5G_upsell",    "5G", 0,  50,  72.0, 60, 1, "5G Pro 100 GB with 50 GB bonus"),
    (3, "4G Migration Pack",     "3G_migration", "4G", 15, 10,  None, 30, 1, "Migrate from 3G to 4G Smart with bonus data"),
    (4, "VoLTE Ready Bundle",    "VoLTE_sunset", "4G", 10, 20,  None, 30, 1, "VoLTE activation with 20 GB bonus"),
    (5, "FWA Home 30Mbps Promo", "FWA",          "5G", 0,  0,   55.0, 30, 1, "5G FWA 30 Mbps — promo price 55 TND/month"),
    (6, "FWA Home 100Mbps Pro",  "FWA",          "5G", 0,  0,   99.9, 30, 1, "5G FWA 100 Mbps — 99.9 TND/month"),
    (7, "HVC Platinum Pack",     "HVC_upsell",   "5G", 25, 100, None, 90, 1, "Exclusive offer for platinum customers"),
    (8, "HVC Gold Upgrade",      "HVC_upsell",   "4G", 15, 50,  None, 60, 1, "Gold customer loyalty upgrade"),
    (9, "3G Sunset Rescue",      "VoLTE_sunset", "4G", 20, 15,  None, 30, 1, "Migrate before 3G sunset — Smart 4G 25 GB with bonus"),
    (10,"MBB to FWA Conversion", "FWA",          "5G", 10, 0,   None, 30, 1, "Convert MBB to FWA 5G for better home broadband"),
]
c.executemany("INSERT INTO offers VALUES(?,?,?,?,?,?,?,?,?,?)", offers)
conn.commit()
print(f"  {len(offers)} offers created")

# ═══════════════════════════════════════════════════════
# INDEXES
# ═══════════════════════════════════════════════════════

c.executescript("""
CREATE INDEX IF NOT EXISTS idx_cust_region ON customers(region);
CREATE INDEX IF NOT EXISTS idx_cust_segment ON customers(segment);
CREATE INDEX IF NOT EXISTS idx_sub_plan ON subscriptions(plan_id);
CREATE INDEX IF NOT EXISTS idx_billing_month ON billing(billing_month);
CREATE INDEX IF NOT EXISTS idx_val_hvc ON customer_value(is_hvc);
CREATE INDEX IF NOT EXISTS idx_val_seg ON customer_value(value_segment);
""")
conn.commit()

# ═══════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════

print("\n=== Operator DB Summary ===")
tables = ["customers","plans","subscriptions","offers","billing","customer_value"]
for t in tables:
    count = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  {t}: {count:,}")

print("\n=== Value Segments ===")
for seg in ["platinum","gold","silver","bronze"]:
    count = c.execute("""SELECT COUNT(DISTINCT msisdn) FROM customer_value
        WHERE value_segment=? AND month=(SELECT MAX(month) FROM customer_value)""", (seg,)).fetchone()[0]
    print(f"  {seg}: {count:,}")

hvc = c.execute("""SELECT COUNT(DISTINCT msisdn) FROM customer_value
    WHERE is_hvc=1 AND month=(SELECT MAX(month) FROM customer_value)""").fetchone()[0]
print(f"\n  HVC (High Value Customers): {hvc:,}")

conn.close()
print("\nOperator DB built successfully.")