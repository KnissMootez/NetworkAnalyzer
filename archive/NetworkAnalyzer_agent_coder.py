import os
import re
import json
import sqlite3
import requests
from datetime import datetime, timedelta
from rag_retriever import retrieve

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SC_DB    = os.path.join(BASE_DIR, "NetworkAnalyzer_new.db")
OP_DB    = os.path.join(BASE_DIR, "operator_new.db")
# ═══════════════════════════════════════════════════════════════════════
# MODEL CONFIG — Qwen2.5-Coder 7B (SQL-optimized, same VRAM as base 7B)
# ═══════════════════════════════════════════════════════════════════════
MODEL           = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b")
OLLAMA_BASE     = os.environ.get("OLLAMA_URL",   "http://localhost:11434")
NUM_GPU_LAYERS  = 20
NUM_THREADS     = 8
CTX_SIZE        = 4096


def _print_model_config():
    print(f"╔══ NetworkAnalyzer Agent ══════════════════════")
    print(f"║  Model:       {MODEL}")
    print(f"║  GPU layers:  {NUM_GPU_LAYERS}  (VRAM)")
    print(f"║  CPU threads: {NUM_THREADS}  (RAM)")
    print(f"║  Context:     {CTX_SIZE} tokens")
    print(f"╚═════════════════════════════════════════")

_print_model_config()


def set_gpu_layers(n: int):
    global NUM_GPU_LAYERS
    NUM_GPU_LAYERS = n
    print(f"GPU layers updated to {n} — takes effect on next query")


# ═══════════════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════════════

def query_sc(sql: str) -> list:
    try:
        conn = sqlite3.connect(SC_DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        return [{"error": str(e)}]

def query_op(sql: str) -> list:
    try:
        conn = sqlite3.connect(OP_DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        return [{"error": str(e)}]

def write_op(sql: str, params: tuple = ()) -> bool:
    try:
        conn = sqlite3.connect(OP_DB)
        conn.execute(sql, params)
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"DB write error: {e}")
        return False

def write_sc(sql: str, params: tuple = ()) -> bool:
    try:
        conn = sqlite3.connect(SC_DB)
        conn.execute(sql, params)
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"DB write error: {e}")
        return False

# ═══════════════════════════════════════════════════════════════════════
# SCHEMA
# ═══════════════════════════════════════════════════════════════════════

NetworkAnalyzer_SCHEMA = """
=== NetworkAnalyzer DATABASE (NetworkAnalyzer_new.db) ===
Network and subscriber technical data. Query with query_sc().

TABLE subscribers
  msisdn        TEXT PK   — phone number, links to operator DB
  imsi          TEXT      — SIM identifier
  sim_type      TEXT      — 'SIM' or 'eSIM'
  is_active     INT       — 1=active subscriber
  region        TEXT      — Tunisian region (Tunis, Sfax, Sousse, etc.)
  city          TEXT
  latitude      REAL
  longitude     REAL
  area_code     TEXT      — geographic area: T=Tunis metro, N=North, C=Center, R=Rural interior, S=South
!! subscribers has NO technology columns. For device capability use devices.is_5g_capable / devices.max_technology.
!! For current network tech use subscriber_technology.current_technology. Always JOIN those tables.

TABLE devices
  msisdn        TEXT PK   — links to subscribers
  imei          TEXT      — device hardware ID
  brand         TEXT      — Samsung, Apple, Huawei, Xiaomi, etc.
  model         TEXT      — device model name
  max_technology TEXT     — highest tech the device supports: '2G','3G','4G','5G'
  volte_capable INT       — 1 = device can do Voice over LTE
  vowifi_capable INT      — 1 = device supports WiFi calling
  is_5g_capable INT       — 1 = device has 5G radio hardware
  os            TEXT      — iOS or Android
  os_version    TEXT

TABLE subscriber_technology
  msisdn           TEXT PK — links to subscribers
  current_technology TEXT  — what tech the SUBSCRIBER is on. NOT used for network KPI filtering.
                             Use cells.technology to filter KPIs by technology generation.
  current_cell_id  INT     — which cell they're currently attached to
  volte_active     INT     — 1 = VoLTE is currently enabled for this subscriber
  data_roaming_active INT  — 1 = currently roaming
  last_seen_date   TEXT    — last network activity date

TABLE sites
  site_id    INT PK
  site_name  TEXT
  region     TEXT
  city       TEXT
  latitude   REAL
  longitude  REAL
  site_type  TEXT  — 'macro','micro','indoor','fwa_home_cell'
  zone       TEXT  — geographic zone: 'North','Center','South' (NOT region names — zone is never 'Tunis')
  is_active  INT


TABLE cells
  cell_id         INT PK
  site_id         INT   — FK to sites
  technology      TEXT  — '2G','3G','4G','5G' — FILTER BY TECHNOLOGY HERE, not on kpis_daily
  frequency_band  TEXT  — '700','900','1800','2100','2600','3500'
  is_active       INT
  max_users       INT

TABLE coverage
  msisdn               TEXT  — which subscriber
  cell_id              INT   — which cell covers them
  technology_available TEXT  — what technology is available at their location
  signal_strength_dbm  REAL  — signal strength in dBm (good > -90, poor < -110)
  is_home_coverage     INT   — 1 = this is their primary/home cell coverage

TABLE kpis_daily
  cell_id             INT   — FK to cells (NOT subscribers — join cells to get technology/region)
  date                TEXT  — YYYY-MM-DD
  rsrp_avg            REAL  — signal strength dBm. Good > -90, Poor < -110
  sinr_avg            REAL  — signal quality dB. Good > 13, Poor < 0
  dl_throughput_mbps  REAL  — download speed
  ul_throughput_mbps  REAL  — upload speed
  dropped_call_rate   REAL  — % calls dropped. Good < 1%, Poor > 2%
  availability_pct    REAL  — % uptime. Target > 99%
  latency_ms          REAL  — round trip delay. 4G target < 50ms
  congestion_level    TEXT  — 'low','medium','high'
  active_users_avg    INT

TABLE kpis_hourly
  cell_id           INT
  datetime          TEXT
  rsrp              REAL
  sinr              REAL
  dl_throughput_mbps REAL
  active_users      INT
  congestion_level  TEXT

TABLE network_alarms
  alarm_id     INT PK
  cell_id      INT
  alarm_type   TEXT  — 'High Interference','Coverage Hole','Congestion',
                       'Hardware Fault','Backhaul Failure','Power Issue'
  severity     TEXT  — 'critical','major','minor','warning'
  trigger_time TEXT
  clear_time   TEXT
  is_active    INT   — 1 = alarm still open
  description  TEXT

TABLE network_incidents
  incident_id    INT PK
  cell_id        INT
  incident_type  TEXT
  start_time     TEXT
  end_time       TEXT
  affected_users INT
  severity       TEXT
  root_cause     TEXT
  resolved       INT

TABLE dou_monthly
  msisdn              TEXT  — subscriber
  month               TEXT  — YYYY-MM
  total_data_gb       REAL  — total data used that month
  distinct_cells_used INT   — how many different cells used (mobility indicator)
  primary_cell_id     INT   — cell they used most
  days_active         INT

TABLE ott_monthly
  msisdn        TEXT
  month         TEXT
  streaming_gb  REAL
  gaming_gb     REAL
  web_gb        REAL
  voip_gb       REAL
  social_gb     REAL
  sms_count     INT

TABLE mobility_profile
  msisdn               TEXT
  month                TEXT
  distinct_cells_count INT   — unique cells per month
  avg_daily_cells      REAL
  primary_cell_id      INT
  mobility_class       TEXT  — 'stationary','low','medium','high'
  is_fwa_candidate     INT   — 1 = flagged as potential FWA subscriber
  is_home_cell_user    INT

KEY JOIN PATTERNS:
  Region KPIs:     kpis_daily k JOIN cells c ON k.cell_id=c.cell_id
                              JOIN sites s ON c.site_id=s.site_id
                   WHERE s.region='Tunis'
  Latest KPIs:     WHERE date=(SELECT MAX(date) FROM kpis_daily)
  Sub coverage:    coverage WHERE msisdn=? AND technology_available='5G'
  Latest mobility: WHERE month=(SELECT MAX(month) FROM mobility_profile)
  Latest DOU:      WHERE month=(SELECT MAX(month) FROM dou_monthly)

TABLE qoe_daily
  msisdn              TEXT  — subscriber
  date                TEXT  — YYYY-MM-DD (daily grain)
  app_type            TEXT  — 'gaming', 'video', 'voip', 'web', 'social'
  avg_latency_ms      REAL  — lower is better
  avg_throughput_mbps REAL
  experience_score    REAL  — 1.0 (poor) to 5.0 (excellent)
  experience_label    TEXT  — 'poor', 'fair', 'good', 'excellent'
  PK: (msisdn, date, app_type)
  Yesterday: WHERE date=date('now','-1 day')
  Poor experience: WHERE experience_label='poor'
  Area filtering: JOIN subscribers s ON q.msisdn=s.msisdn WHERE s.area_code='R'
  Southern zone:  JOIN subscribers s ON q.msisdn=s.msisdn JOIN sites si ON ... — or use s.area_code IN ('S')
"""

OPERATOR_SCHEMA = """
=== OPERATOR DATABASE (operator_new.db) ===
Commercial and customer data. Query with query_op().

TABLE customers
  msisdn            TEXT PK  — links to NetworkAnalyzer subscribers
  full_name         TEXT
  national_id       TEXT
  date_of_birth     TEXT
  gender            TEXT
  email             TEXT
  region            TEXT
  segment           TEXT  — 'prepaid','postpaid','enterprise'
  registration_date TEXT
  is_active         INT

TABLE plans
  plan_id        INT PK
  plan_name      TEXT
  plan_type      TEXT   — 'voice','data','bundle','MBB','FWA'
  data_cap_gb    REAL
  speed_mbps     REAL
  monthly_price  REAL   — price in TND
  supports_5g    INT    — 1 = 5G plan
  supports_volte INT
  is_fwa_plan    INT
  is_mbb_plan    INT
  description    TEXT

TABLE subscriptions
  msisdn         TEXT
  plan_id        INT
  start_date     TEXT
  end_date       TEXT
  is_current     INT   — 1 = active subscription
  contract_type  TEXT
  auto_renewal   INT

TABLE offers
  offer_id         INT PK
  offer_name       TEXT
  target_campaign  TEXT  — 'HVC_upsell','3G_migration','5G_upsell','FWA','VoLTE_sunset'
  target_technology TEXT
  discount_pct     REAL
  bonus_data_gb    REAL
  price_override   REAL
  validity_days    INT
  is_active        INT
  description      TEXT

TABLE offer_assignments
  msisdn        TEXT
  offer_id      INT
  assigned_date TEXT
  channel       TEXT
  accepted      INT
  accepted_date TEXT

TABLE campaigns
  campaign_id    INT PK
  campaign_name  TEXT
  campaign_type  TEXT
  target_count   INT
  launched_date  TEXT
  status         TEXT  — 'draft','active','completed'
  created_by     TEXT
  offer_id       INT

TABLE campaign_targets
  campaign_id INT
  msisdn      TEXT
  reason      TEXT
  score       REAL
  notified    INT
  converted   INT

TABLE sms_log
  sms_id       INT PK
  msisdn       TEXT
  campaign_id  INT
  sent_date    TEXT
  message_text TEXT
  status       TEXT
  response     TEXT

TABLE billing
  msisdn         TEXT
  billing_month  TEXT
  total_amount   REAL
  data_charges   REAL
  voice_charges  REAL
  payment_status TEXT
  payment_date   TEXT

TABLE customer_value
  msisdn        TEXT
  month         TEXT  — format 'YYYY-MM', 6 months of history per subscriber
  arpu          REAL  — monthly spend in TND (5-130 TND typical range)
  value_segment TEXT  — 'bronze'(<15 TND),'silver'(15-35),'gold'(35-75),'platinum'(>=75)
  is_hvc        INT   — 1 = High Value Customer (gold or platinum)

!! CRITICAL: customer_value has ONE ROW PER SUBSCRIBER PER MONTH (6 rows per subscriber).
   ALWAYS filter to one month or AVG/SUM will be inflated 6x.
   WRONG:  JOIN customer_value cv ON cv.msisdn=x              <- 6x row explosion, ARPU wrong
   RIGHT:  JOIN customer_value cv ON cv.msisdn=x AND cv.month=(SELECT MAX(month) FROM customer_value)

KEY JOIN PATTERNS:
  Customer plan:   subscriptions s JOIN plans p ON s.plan_id=p.plan_id WHERE s.msisdn=? AND s.is_current=1
  Latest value:    WHERE month=(SELECT MAX(month) FROM customer_value)
  HVC:             WHERE is_hvc=1 AND month=(SELECT MAX(month) FROM customer_value)
  Active offers:   WHERE is_active=1
"""

CROSS_DB_NOTE = """
=== CROSS-DATABASE QUERIES ===
NetworkAnalyzer and Operator DBs share msisdn as the common key.
To join across both databases in a single query use ATTACH:

    ATTACH DATABASE 'operator_new.db' AS op;
    SELECT s.msisdn, d.max_technology, op.customers.full_name
    FROM subscribers s
    JOIN devices d ON s.msisdn=d.msisdn
    JOIN op.customers ON s.msisdn=op.customers.msisdn
    WHERE d.is_5g_capable=1

Always prefix operator tables with 'op.' when using ATTACH.
ATTACH queries run via query_sc() since you attach op DB to sc connection.

EXAMPLE QUERIES — follow these patterns exactly:

5G upsell candidates in Tunis:
QUERY_SC: SELECT COUNT(*) as n FROM subscribers s JOIN devices d ON s.msisdn=d.msisdn JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE s.region='Tunis' AND d.is_5g_capable=1 AND st.current_technology='4G' AND EXISTS(SELECT 1 FROM coverage cv WHERE cv.msisdn=s.msisdn AND cv.technology_available='5G')

VERIFIED 5G upsell by region — NO LIMIT, returns ALL regions:
QUERY_SC: SELECT s.region, COUNT(DISTINCT s.msisdn) as n FROM subscribers s JOIN devices d ON s.msisdn=d.msisdn JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE d.is_5g_capable=1 AND st.current_technology='4G' AND EXISTS(SELECT 1 FROM coverage cv WHERE cv.msisdn=s.msisdn AND cv.technology_available='5G') GROUP BY s.region ORDER BY n DESC

3G sunset risk by region — NO LIMIT, returns ALL regions:
QUERY_SC: SELECT s.region, COUNT(DISTINCT s.msisdn) as n FROM subscribers s JOIN devices d ON s.msisdn=d.msisdn JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE (d.volte_capable=0 AND st.current_technology='3G') OR (d.volte_capable=1 AND st.current_technology='3G' AND st.volte_active=0) GROUP BY s.region ORDER BY n DESC

5G capable not on 5G by region — NO LIMIT, returns ALL regions:
QUERY_SC: SELECT s.region, COUNT(DISTINCT d.msisdn) as n FROM devices d JOIN subscriber_technology st ON d.msisdn=st.msisdn JOIN subscribers s ON d.msisdn=s.msisdn WHERE d.is_5g_capable=1 AND st.current_technology != '5G' GROUP BY s.region ORDER BY n DESC

FWA candidates latest month:
QUERY_SC: SELECT COUNT(*) as n FROM mobility_profile mp JOIN dou_monthly dm ON mp.msisdn=dm.msisdn AND mp.month=dm.month WHERE mp.mobility_class='stationary' AND dm.total_data_gb>30 AND mp.month=(SELECT MAX(month) FROM mobility_profile)

FWA candidates by region — NO LIMIT, returns ALL regions:
QUERY_SC: SELECT s.region, COUNT(DISTINCT mp.msisdn) as n FROM mobility_profile mp JOIN dou_monthly dm ON mp.msisdn=dm.msisdn AND mp.month=dm.month JOIN subscribers s ON mp.msisdn=s.msisdn WHERE mp.mobility_class='stationary' AND dm.total_data_gb>30 AND mp.month=(SELECT MAX(month) FROM mobility_profile) GROUP BY s.region ORDER BY n DESC

VoLTE ready but on 3G:
QUERY_SC: SELECT COUNT(*) as n FROM devices d JOIN subscriber_technology st ON d.msisdn=st.msisdn WHERE d.volte_capable=1 AND st.current_technology='3G' AND st.volte_active=0

HVC not on 5G plan:
QUERY_OP: SELECT COUNT(*) as n FROM customer_value cv JOIN subscriptions s ON cv.msisdn=s.msisdn AND s.is_current=1 JOIN plans p ON s.plan_id=p.plan_id WHERE cv.is_hvc=1 AND p.supports_5g=0 AND cv.month=(SELECT MAX(month) FROM customer_value)

Network KPIs for a region:
QUERY_SC: SELECT ROUND(AVG(k.dl_throughput_mbps),2) as avg_dl, ROUND(AVG(k.availability_pct),2) as availability, ROUND(AVG(k.dropped_call_rate),3) as drop_rate FROM kpis_daily k JOIN cells c ON k.cell_id=c.cell_id JOIN sites s ON c.site_id=s.site_id WHERE s.region='Sfax' AND k.date=(SELECT MAX(date) FROM kpis_daily)

Network KPIs by region — NO LIMIT, returns ALL regions:
QUERY_SC: SELECT s.region, ROUND(AVG(k.dl_throughput_mbps),2) as avg_dl, ROUND(AVG(k.availability_pct),2) as availability, ROUND(AVG(k.dropped_call_rate),3) as drop_rate FROM kpis_daily k JOIN cells c ON k.cell_id=c.cell_id JOIN sites s ON c.site_id=s.site_id WHERE k.date=(SELECT MAX(date) FROM kpis_daily) GROUP BY s.region ORDER BY avg_dl DESC

Average download speed by region AND technology — drilldown/treemap (kpis_daily has NO subscriber_msisdn):
QUERY_SC: SELECT s.region, c.technology, ROUND(AVG(k.dl_throughput_mbps),2) as avg_dl FROM kpis_daily k JOIN cells c ON k.cell_id=c.cell_id JOIN sites s ON c.site_id=s.site_id WHERE k.date=(SELECT MAX(date) FROM kpis_daily) GROUP BY s.region, c.technology ORDER BY s.region, c.technology

Network KPIs filtered by technology (technology is on cells table, NOT on kpis_daily):
QUERY_SC: SELECT ROUND(AVG(k.dl_throughput_mbps),2) as avg_dl FROM kpis_daily k JOIN cells c ON k.cell_id=c.cell_id JOIN sites s ON c.site_id=s.site_id WHERE s.region='Sfax' AND c.technology='4G' AND k.date=(SELECT MAX(date) FROM kpis_daily)

Subscriber breakdown by 5G capability and current technology (correct query):
QUERY_SC: SELECT d.is_5g_capable, st.current_technology, COUNT(*) as n FROM devices d JOIN subscriber_technology st ON d.msisdn=st.msisdn GROUP BY d.is_5g_capable, st.current_technology ORDER BY d.is_5g_capable, st.current_technology

Drilldown: current technology vs device max technology (upgrade gap analysis):
QUERY_SC: SELECT st.current_technology, d.max_technology, COUNT(*) as n FROM subscriber_technology st JOIN devices d ON st.msisdn=d.msisdn GROUP BY st.current_technology, d.max_technology ORDER BY st.current_technology, d.max_technology

Technology distribution by region — NO LIMIT, returns ALL regions:
QUERY_SC: SELECT s.region, st.current_technology, COUNT(*) as n FROM subscribers s JOIN subscriber_technology st ON s.msisdn=st.msisdn GROUP BY s.region, st.current_technology ORDER BY s.region, st.current_technology

Average ARPU of HVC customers for Q4 2025:
QUERY_OP: SELECT ROUND(AVG(cv.arpu),2) as avg_arpu FROM customer_value cv WHERE cv.is_hvc=1 AND cv.month IN ('2025-10','2025-11','2025-12')

Downlink throughput trend in southern regions (zone='South') over last 30 days:
QUERY_SC: SELECT k.date, ROUND(AVG(k.dl_throughput_mbps),2) as avg_dl FROM kpis_daily k JOIN cells c ON k.cell_id=c.cell_id JOIN sites s ON c.site_id=s.site_id WHERE s.zone='South' AND k.date >= date('now','-30 days') GROUP BY k.date ORDER BY k.date

Users in area R with poor gaming or video experience yesterday:
QUERY_SC: SELECT COUNT(DISTINCT q.msisdn) as n FROM qoe_daily q JOIN subscribers s ON q.msisdn=s.msisdn WHERE s.area_code='R' AND q.app_type IN ('gaming','video') AND q.experience_label='poor' AND q.date=date('now','-1 day')

Poor QoE breakdown by area and app type:
QUERY_SC: SELECT s.area_code, q.app_type, COUNT(DISTINCT q.msisdn) as n FROM qoe_daily q JOIN subscribers s ON q.msisdn=s.msisdn WHERE q.experience_label='poor' AND q.date=date('now','-1 day') GROUP BY s.area_code, q.app_type ORDER BY s.area_code, q.app_type

CRITICAL — cells table has NO msisdn column. NEVER join cells ON msisdn. cells joins via cell_id only.
CRITICAL — kpis_daily has NO msisdn/subscriber column. NEVER join kpis_daily ON msisdn. KPI region = kpis_daily JOIN cells ON cell_id JOIN sites ON site_id.
NEVER use cells.technology for subscriber breakdowns. Use subscriber_technology.current_technology instead.
CRITICAL — to get a subscriber's region, use subscribers.region directly. Do NOT join through sites.
CRITICAL — sites.zone values are ONLY 'North','Center','South'. Region names like 'Tunis','Sfax','Sousse' are in sites.region, NOT sites.zone. NEVER filter WHERE zone='Tunis' — use WHERE s.region='Tunis' instead.

IMPORTANT: kpis_daily has NO technology column. NEVER filter kpis_daily by technology directly.
To filter KPIs by technology: JOIN cells c ON k.cell_id=c.cell_id WHERE c.technology='4G'
subscriber_technology is for subscriber current tech — NOT for network KPI filtering.

CRITICAL — REGIONAL BREAKDOWNS: When asked for ANY breakdown by region, distribution by region,
or per-region analysis, NEVER add LIMIT to the query. Always return ALL regions.
Queries with GROUP BY s.region must NOT have a LIMIT clause.

CRITICAL — REGION NAME CASE: Region values in the database are Title Case (first letter uppercase).
  CORRECT:   WHERE s.region='Ariana'   WHERE s.region='Tunis'   WHERE s.region='Sfax'
  WRONG:     WHERE s.region='ariana'   WHERE s.region='tunis'   WHERE s.region='sfax'
  Full list: Tunis, Ariana, Ben Arous, Manouba, Bizerte, Nabeul, Sousse, Monastir, Mahdia,
             Sfax, Kairouan, Kasserine, Sidi Bouzid, Gabes, Mdenine, Tataouine, Gafsa,
             Tozeur, Kebili, Beja, Jendouba, Kef, Siliana, Zaghouan

=== VERIFIED GROUND-TRUTH COUNTS (use these to validate PROPOSE numbers) ===
Total subscribers: 50000
5G upsell candidates (5G device + 4G plan + 5G coverage): 3852
3G sunset at-risk total: 14612
  - No VoLTE device (needs hardware upgrade): 9046
  - VoLTE capable but inactive on 3G: 5566
FWA candidates (stationary + >30GB/month): 916
HVC customers (gold + platinum): 7979
5G capable but not on 5G plan: 16514
Device breakdown:
  Not 5G capable (is_5g_capable=0): 30352 — on 3G: 12314, on 4G: 18038
  5G capable (is_5g_capable=1): 19648 — on 3G: 2299, on 4G: 14215, on 5G: 3134
"""

FULL_SCHEMA = NetworkAnalyzer_SCHEMA + OPERATOR_SCHEMA + CROSS_DB_NOTE

COMPACT_SCHEMA = """
=== NetworkAnalyzer DB (query_sc) ===
subscribers: msisdn(PK), region(Title Case!), city, area_code(T/N/C/R/S), is_active — NO tech columns
devices: msisdn(PK), brand, model, max_technology, is_5g_capable, volte_capable, vowifi_capable, os
subscriber_technology: msisdn(PK), current_technology('2G'/'3G'/'4G'/'5G'), current_cell_id, volte_active, last_seen_date
sites: site_id(PK), site_name, region(Title Case!), city, latitude, longitude, site_type, zone('North'/'Center'/'South'), is_active
cells: cell_id(PK), site_id(FK), technology('2G'/'3G'/'4G'/'5G'), frequency_band, is_active, max_users
coverage: msisdn, cell_id, technology_available, signal_strength_dbm, is_home_coverage
kpis_daily: cell_id(FK→cells), date, rsrp_avg, sinr_avg, dl_throughput_mbps, ul_throughput_mbps, dropped_call_rate, availability_pct, latency_ms, congestion_level, active_users_avg — NO msisdn, NO technology
kpis_hourly: cell_id, datetime, rsrp, sinr, dl_throughput_mbps, active_users, congestion_level
network_alarms: alarm_id, cell_id, alarm_type, severity, trigger_time, clear_time, is_active
dou_monthly: msisdn, month(YYYY-MM), total_data_gb, distinct_cells_used, primary_cell_id, days_active
ott_monthly: msisdn, month, streaming_gb, gaming_gb, web_gb, voip_gb, social_gb, sms_count
mobility_profile: msisdn, month, distinct_cells_count, avg_daily_cells, primary_cell_id, mobility_class('stationary'/'low'/'medium'/'high'), is_fwa_candidate
qoe_daily: msisdn, date, app_type('gaming'/'video'/'voip'/'web'/'social'), avg_latency_ms, avg_throughput_mbps, experience_score, experience_label('poor'/'fair'/'good'/'excellent')

=== OPERATOR DB (query_op) ===
customers: msisdn(PK), full_name, region, segment('prepaid'/'postpaid'/'enterprise'), is_active
plans: plan_id(PK), plan_name, plan_type, data_cap_gb, speed_mbps, monthly_price(TND), supports_5g, is_fwa_plan
subscriptions: msisdn, plan_id, start_date, end_date, is_current(1=active), contract_type
offers: offer_id, offer_name, target_campaign, target_technology, discount_pct, bonus_data_gb, is_active
offer_assignments: msisdn, offer_id, assigned_date, channel, accepted
campaigns: campaign_id, campaign_name, campaign_type, target_count, status, offer_id
campaign_targets: campaign_id, msisdn, reason, score, notified, converted
billing: msisdn, billing_month, total_amount, data_charges, voice_charges, payment_status
customer_value: msisdn, month(YYYY-MM), arpu(TND), value_segment('bronze'/'silver'/'gold'/'platinum'), is_hvc — 6 ROWS PER SUBSCRIBER, always filter: cv.month=(SELECT MAX(month) FROM op.customer_value)

=== CRITICAL RULES ===
- region values are Title Case: 'Ariana' not 'ariana', 'Tunis' not 'tunis'
- kpis_daily has NO msisdn and NO technology — filter tech via JOIN cells, filter region via JOIN cells→sites
- cells has NO msisdn — never join cells ON msisdn
- customer_value and billing are in operator DB — use ATTACH or query_op()
- sites.zone is only 'North'/'Center'/'South' — never WHERE zone='Tunis'
- For cross-DB queries: ATTACH DATABASE 'operator_new.db' AS op; then prefix op.table_name
"""

# ═══════════════════════════════════════════════════════════════════════
# OLLAMA
# ═══════════════════════════════════════════════════════════════════════

def _ollama(system: str, prompt: str, max_tokens: int = 800, timeout: int = 300) -> str:
    try:
        r = requests.post(
            f"{OLLAMA_BASE}/api/generate",
            json={
                "model": MODEL,
                "system": system,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "num_predict": max_tokens,
                    "num_gpu":     NUM_GPU_LAYERS,
                    "num_thread":  NUM_THREADS,
                    "num_ctx":     CTX_SIZE,
                    "low_vram":       True,
                    "f16_kv":         False,
                }
            },
            timeout=timeout
        )
        return r.json().get("response", "").strip()
    except Exception as e:
        return f"ERROR: {e}"


def _check_sql(sql: str, db_path: str) -> str | None:
    """Compile-check SQL before executing. Returns None if valid, or an error
    string with the actual column names for every table referenced."""
    try:
        conn = sqlite3.connect(db_path)
        if "ATTACH" in sql.upper() or " op." in sql:
            conn.execute(f"ATTACH DATABASE '{OP_DB}' AS op")
        conn.execute(f"EXPLAIN {sql}")
        conn.close()
        return None
    except sqlite3.OperationalError as e:
        err = str(e)
        tables = re.findall(r'(?:FROM|JOIN)\s+(\w+)', sql, re.I)
        lines = [f"SQL error: {err}"]
        try:
            conn2 = sqlite3.connect(db_path)
            for t in dict.fromkeys(tables):
                rows = conn2.execute(f"PRAGMA table_info({t})").fetchall()
                if rows:
                    lines.append(f"  {t} actual columns: {', '.join(r[1] for r in rows)}")
                else:
                    lines.append(f"  {t}: table not found in this database")
            conn2.close()
        except Exception:
            pass
        lines.append("Rewrite the query using ONLY the columns listed above.")

        # Table fuzzy search — when a table doesn't exist, list all real tables
        tbl_match = re.search(r'no such table[:\s]+(?:\w+\.)?(\w+)', err, re.I)
        if tbl_match:
            unknown_tbl = tbl_match.group(1).lower()
            for db_path_search, db_label in [(SC_DB, "query_sc"), (OP_DB, "query_op")]:
                try:
                    conn3 = sqlite3.connect(db_path_search)
                    all_tbls = [r[0] for r in conn3.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                    ).fetchall()]
                    conn3.close()
                    similar_tbls = [t for t in all_tbls
                                    if unknown_tbl in t.lower() or t.lower() in unknown_tbl]
                    lines.append(
                        f"TABLE '{unknown_tbl}' not found in {db_label}. "
                        f"Available tables: {', '.join(all_tbls)}."
                        + (f" Closest match: {', '.join(similar_tbls)}." if similar_tbls else "")
                    )
                except Exception:
                    pass

        # Column fuzzy search — find where the unknown column actually lives
        col_match = re.search(r'no such column[:\s]+(?:\w+\.)?(\w+)', err, re.I)
        if col_match:
            unknown_col = col_match.group(1).lower()
            similar = []
            for db_path_search, db_label in [(SC_DB, "query_sc"), (OP_DB, "query_op")]:
                try:
                    conn3 = sqlite3.connect(db_path_search)
                    tbl_rows = conn3.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                    for (tbl,) in tbl_rows:
                        col_rows = conn3.execute(f"PRAGMA table_info({tbl})").fetchall()
                        for col_row in col_rows:
                            col_name = col_row[1].lower()
                            if unknown_col in col_name or col_name in unknown_col:
                                similar.append(f"{tbl}.{col_row[1]} ({db_label})")
                    conn3.close()
                except Exception:
                    pass
            if similar:
                lines.append(
                    f"COLUMN '{unknown_col}' not found. Closest matches across all tables: "
                    + ", ".join(similar[:8])
                )

        return "\n".join(lines)


def _check_sql_semantics(sql: str) -> str | None:
    """Check for semantic mistakes that EXPLAIN won't catch:
    - customer_value joined without a month filter (causes 6x row inflation)
    - customer_value queried without ATTACH when targeting op DB tables
    - referencing columns that don't exist on a table (e.g. cv.region)
    Returns None if clean, or a correction message."""
    sql_up = sql.upper()
    issues = []

    # customer_value joined without month filter
    if "CUSTOMER_VALUE" in sql_up:
        has_month_filter = (
            "CV.MONTH" in sql_up or
            "CUSTOMER_VALUE.MONTH" in sql_up or
            "AND MONTH" in sql_up
        )
        if not has_month_filter:
            issues.append(
                "SEMANTIC ERROR: customer_value joined without a month filter. "
                "This table has 6 rows per subscriber (one per month), so without "
                "AND cv.month=(SELECT MAX(month) FROM op.customer_value) "
                "your AVG/COUNT will be inflated 6x and ARPU values will be wrong. "
                "Add: AND cv.month=(SELECT MAX(month) FROM op.customer_value) to the JOIN or WHERE clause."
            )

    # customer_value referenced without ATTACH (it lives in operator DB, not NetworkAnalyzer)
    if "CUSTOMER_VALUE" in sql_up and "ATTACH" not in sql_up and " OP." not in sql_up.upper():
        issues.append(
            "SEMANTIC ERROR: customer_value is in operator_new.db, NOT in NetworkAnalyzer_new.db. "
            "You must use ATTACH DATABASE 'operator_new.db' AS op; and prefix it as op.customer_value. "
            "Same applies to: op.customers, op.plans, op.subscriptions, op.billing, op.offers."
        )

    # customer_value has no region column
    if "CUSTOMER_VALUE" in sql_up and "CV.REGION" in sql_up:
        issues.append(
            "SEMANTIC ERROR: customer_value has no region column. "
            "Region is on the subscribers table in NetworkAnalyzer DB. "
            "Join subscribers s and use s.region instead of cv.region."
        )

    # kpis_daily without date filter
    if "KPIS_DAILY" in sql_up:
        has_date_filter = "K.DATE" in sql_up or "KPIS_DAILY.DATE" in sql_up
        if not has_date_filter:
            issues.append(
                "SEMANTIC ERROR: kpis_daily queried without a date filter — this averages ALL historical data, "
                "not current values. Add: AND k.date=(SELECT MAX(date) FROM kpis_daily)"
            )
        elif "DATE('NOW')" in sql_up or 'DATE("NOW")' in sql_up:
            issues.append(
                "SEMANTIC ERROR: DATE('now') may not exist in kpis_daily if the simulator hasn't run today. "
                "Use: k.date=(SELECT MAX(date) FROM kpis_daily) instead."
            )

    # billing same issues
    if "BILLING" in sql_up and "ATTACH" not in sql_up and " OP." not in sql_up:
        issues.append(
            "SEMANTIC ERROR: billing table is in operator_new.db, not NetworkAnalyzer. "
            "Use ATTACH DATABASE 'operator_new.db' AS op; and prefix as op.billing."
        )

    return "\n".join(issues) if issues else None


def _probe_empty_result(sql: str, db_path: str) -> str:
    """When a query returns 0 rows, probe each filtered column to show
    what values actually exist in the DB, so the model can self-correct."""
    where_match = re.search(
        r'\bWHERE\b(.*?)(?:\bGROUP\s+BY\b|\bORDER\s+BY\b|\bHAVING\b|\bLIMIT\b|$)',
        sql, re.I | re.DOTALL
    )
    if not where_match:
        return ""

    where_clause = where_match.group(1)
    filters = re.findall(r"(?:(\w+)\.)?(\w+)\s*=\s*'([^']+)'", where_clause)
    if not filters:
        return ""

    alias_map: dict = {}
    for m in re.finditer(r'(?:FROM|JOIN)\s+(\w+)(?:\s+(?:AS\s+)?(\w+))?', sql, re.I):
        table = m.group(1)
        alias = (m.group(2) or table).lower()
        alias_map[alias] = table
        alias_map[table.lower()] = table

    hints = []
    probed: set = set()
    try:
        conn = sqlite3.connect(db_path)
        if "ATTACH" in sql.upper() or " op." in sql:
            conn.execute(f"ATTACH DATABASE '{OP_DB}' AS op")
        for alias, col, used_val in filters:
            table = alias_map.get(alias.lower(), alias) if alias else None
            if not table:
                bare_match = re.search(r'FROM\s+(\w+)', sql, re.I)
                table = bare_match.group(1) if bare_match else None
            if not table or not col:
                continue
            probe_key = f"{table}.{col}"
            if probe_key in probed:
                continue
            probed.add(probe_key)
            try:
                rows = conn.execute(
                    f"SELECT DISTINCT {col} FROM {table} ORDER BY {col} LIMIT 25"
                ).fetchall()
                vals = [str(r[0]) for r in rows if r[0] is not None]
                if vals:
                    hints.append(
                        f"  {table}.{col}: actual values → {vals}  (query used '{used_val}')"
                    )
            except Exception:
                pass
        conn.close()
    except Exception:
        pass

    if not hints:
        return ""
    return (
        "ZERO ROWS returned — the filter value(s) may not match what is stored:\n"
        + "\n".join(hints)
        + "\nRewrite the query using a value from the list above."
    )


# ═══════════════════════════════════════════════════════════════════════
# CHART SPEC FORMATTER
# FIX 2: tighter prompt, lower max_tokens (200), lower timeout (40s)
# ═══════════════════════════════════════════════════════════════════════

_LABEL_MAPS = {
    "is_5g_capable":    {"0": "Not 5G Capable",  "1": "5G Capable"},
    "volte_capable":    {"0": "No VoLTE",         "1": "VoLTE Capable"},
    "vowifi_capable":   {"0": "No WiFi Calling",  "1": "WiFi Calling"},
    "volte_active":     {"0": "VoLTE Off",        "1": "VoLTE On"},
    "is_active":        {"0": "Inactive",         "1": "Active"},
    "is_fwa_candidate": {"0": "Not FWA",          "1": "FWA Candidate"},
    "is_home_cell_user":{"0": "Non-Home Cell",    "1": "Home Cell User"},
    "area_code":        {"T": "Tunis Metro", "N": "North", "C": "Center",
                         "R": "Rural Interior", "S": "South"},
    "zone":             {"North": "North Zone", "Center": "Central Zone", "South": "Southern Zone"},
}

def _map_label(col: str, val) -> str:
    return _LABEL_MAPS.get(col, {}).get(str(val), str(val))


_UNIT_MAP = {
    r'throughput|mbps|dl|ul|speed':        'Mbps',
    r'arpu|revenue|price|tnd|cost':        'TND',
    r'score|rating':                       '/5',
    r'latency|ms|delay':                   'ms',
    r'pct|rate|percent|availability':      '%',
    r'gb|data|dou|usage':                  'GB',
    r'count|cnt|^n$|total|num|users|subs': '',
}

def _unit_for(col: str) -> str:
    import re as _re
    c = col.lower()
    for pattern, unit in _UNIT_MAP.items():
        if _re.search(pattern, c):
            return unit
    return ''

def _build_treemap_from_context(context: str) -> dict | None:
    """Parse the last GROUP BY result from context and build a treemap.
    Handles any number of dimension columns generically.
    Falls back to ast.literal_eval when compact Python-dict format is used."""
    import ast

    blocks = re.findall(r'Results \(\d+ rows\):\s*(\[.*?\])', context, re.DOTALL)
    if not blocks:
        return None

    raw_block = blocks[-1]
    try:
        rows = json.loads(raw_block)
    except json.JSONDecodeError:
        try:
            rows = ast.literal_eval(raw_block)
        except Exception:
            return None

    if not rows:
        return None

    keys = list(rows[0].keys())

    # Identify metric column: prefer count/total names, then largest numeric column
    count_key = next(
        (k for k in keys if re.search(r'count|cnt|total|num|^n$', k, re.I)), None
    )
    if not count_key:
        count_key = next(
            (k for k in keys
             if isinstance(rows[0][k], (int, float)) and max(r[k] for r in rows) > 10),
            None
        )
    if not count_key:
        return None

    dim_keys = [k for k in keys if k != count_key]
    if not dim_keys:
        return None

    unit = _unit_for(count_key)

    def _to_num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    def _build_nested(rows, dims):
        if len(dims) == 1:
            node: dict = {}
            for row in rows:
                label = _map_label(dims[0], row[dims[0]])
                node[label] = node.get(label, 0.0) + _to_num(row[count_key])
            return node
        node = {}
        grouped: dict = {}
        for row in rows:
            label = _map_label(dims[0], row[dims[0]])
            grouped.setdefault(label, []).append(row)
        for label, sub_rows in grouped.items():
            node[label] = _build_nested(sub_rows, dims[1:])
        return node

    def _sum_tree(tree):
        if isinstance(tree, (int, float)):
            return tree
        return sum(_sum_tree(v) for v in tree.values())

    def _flatten(tree, parent_label, labels, parents, values):
        for key, subtree in tree.items():
            node_label = f"{parent_label} \u2013 {key}" if parent_label != "Total" else key
            total = _sum_tree(subtree)
            labels.append(node_label)
            parents.append(parent_label)
            values.append(round(total, 2))
            if isinstance(subtree, dict):
                _flatten(subtree, node_label, labels, parents, values)

    nested      = _build_nested(rows, dim_keys)
    grand_total = round(_sum_tree(nested), 2)

    labels, parents, values = ["Total"], [""], [grand_total]
    _flatten(nested, "Total", labels, parents, values)

    return {"type": "treemap", "title": "Breakdown",
            "labels": labels, "parents": parents, "values": values,
            "value_unit": unit, "value_label": count_key.replace("_", " ").title()}

def _to_chart_spec(text: str, question: str) -> dict | None:
    """
    Fallback chart formatter — runs only when CONCLUDE JSON has no chart key.
    FIX 2: max_tokens reduced to 200, timeout to 40s, prompt tightened.
    """
    q_lower = question.lower()

    prompt = f"""Convert this answer to a JSON chart spec. Output ONLY valid JSON, nothing else.

Rules:
- Numbers never use commas (16514 not 16,514)
- No chart possible: {{"type":"none"}}
- by region/category → bar: {{"type":"bar","title":"...","x":[...],"y":[...],"x_label":"...","y_label":"..."}}
- proportions → pie: {{"type":"pie","title":"...","x":[...],"y":[...]}}
- trend → line: {{"type":"line","title":"...","x":[...],"y":[...]}}
- drilldown/tree → treemap: {{"type":"treemap","title":"...","labels":[...],"parents":[...],"values":[...]}}
  treemap root node always has parent ""

Question: {question}
Answer: {text}
JSON:"""

    try:
        raw = _ollama("Output only valid JSON. No explanation.", prompt, max_tokens=200, timeout=40)
    except Exception:
        return None

    try:
        raw = raw.strip().strip("```json").strip("```").strip()
        raw = re.sub(r'(\d),(\d{3})', r'\1\2', raw)
        parsed = json.loads(raw)
        if parsed.get("type") == "none":
            return None
        return parsed
    except Exception:
        return None

# ═══════════════════════════════════════════════════════════════════════
# REASONING CHAIN
# ═══════════════════════════════════════════════════════════════════════

AGENT_SYSTEM = """{rag_context}

{schema}

You are an intelligent telecom analyst for a Tunisian operator.
You reason step by step using SQL queries against two databases.

CRITICAL SCHEMA RULES — NEVER VIOLATE:
- kpis_daily has NO technology column. NEVER filter kpis_daily by technology directly.
- To filter KPIs by technology: JOIN cells c ON k.cell_id=c.cell_id WHERE c.technology='4G'
- subscriber_technology is for subscriber current tech — NOT for network KPI filtering.
- REGIONAL BREAKDOWNS: Never add LIMIT to GROUP BY region queries. Return ALL regions.

At each step output EXACTLY ONE of:

QUERY_SC: <sql>
  Query NetworkAnalyzer DB (network, devices, coverage, KPIs, DOU, mobility)
  SQL must be valid SQLite. No markdown. SELECT only.

QUERY_OP: <sql>
  Query Operator DB (customers, plans, subscriptions, offers, billing)
  SQL must be valid SQLite. No markdown. SELECT only.

QUERY_BOTH: <sql>
  Cross-database query using ATTACH. Use op. prefix for operator tables.

CONCLUDE: <your full analysis as JSON>
  When you have enough data to answer completely.
  Use CONCLUDE for: analysis, breakdowns, trends, comparisons, recommendations,
  "how can we", "how do we", "why is", "what should we" questions.
  CONCLUDE answers questions. It does NOT create campaigns or send messages.

PROPOSE: <action description>
  ONLY when the user explicitly asks to CREATE, LAUNCH, ASSIGN, SEND, or ENROLL.
  e.g. "create a 5G upsell campaign", "send offers to these users", "assign plan X".
  Describe EXACTLY what you will do with exact counts and TND impact.
  Validate counts against VERIFIED GROUND-TRUTH COUNTS before proposing.
  Wait for confirmation before executing.
  NEVER PROPOSE for analysis-only questions — use CONCLUDE instead.

Rules:
- Keep answers concise — 2-4 sentences maximum for CONCLUDE.
- Always check coverage before recommending any technology upgrade.
- Never guess numbers — always query first.
- All prices must be in TND. Never use USD.
- Write SQL on a single line with no line breaks.
- Do not use markdown backticks around SQL.
- NEVER output QUERY_SC or QUERY_OP tags inside a CONCLUDE or PROPOSE block.
- NEVER write PROPOSE before you have run at least 2 actual queries.
- NEVER use PROPOSE for "how can we", "how do we", "why", "should we", "recommend" questions — use CONCLUDE for those.
- Use correct campaign types: 5G_upsell, 3G_migration, FWA, VoLTE_sunset, HVC_upsell.
- In JSON output, NEVER use commas in numbers. Write 13532 not 13,532.
- For any breakdown by region: query WITHOUT LIMIT to get ALL regions.
- NEVER SELECT raw msisdn lists. Always aggregate: use COUNT(*), GROUP BY, or AVG(). Fetching individual msisdns is useless for analysis.
- For 3G sunset analysis use: SELECT s.region, COUNT(DISTINCT s.msisdn) as n FROM subscribers s JOIN devices d ON s.msisdn=d.msisdn JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE (d.volte_capable=0 AND st.current_technology='3G') OR (d.volte_capable=1 AND st.current_technology='3G' AND st.volte_active=0) GROUP BY s.region ORDER BY n DESC

OUTPUT FORMAT — When writing CONCLUDE, output valid JSON on a single line:
{"text": "your analysis here", "chart": {"type": "bar", "title": "Chart Title", "x": ["A","B","C"], "y": [1,2,3], "x_label": "Category", "y_label": "Value"}}

Chart types:
- "bar"       — single series comparison
- "multibar"  — multiple series, y must be a dict {"series_name": [values]}
- "pie"       — proportions/shares/percentages
- "line"      — trends over time
- "area"      — cumulative trends, filled line
- "scatter"   — correlation, add "labels" array
- "histogram" — frequency distribution, x is raw values array
- "heatmap"   — 2D matrix, x=columns, y=rows, add "z" as 2D array
- "treemap"   — hierarchical drill-down, requires "labels", "parents", "values" (NOT x/y)
                root node has empty parent "". Example:
                labels=["Total","5G Capable","Not 5G Capable"]
                parents=["","Total","Total"]
                values=[50000,19648,30352]

Omit "chart" entirely if there is nothing visual to show.
Only include chart data from actual query results. Never invent chart data.
"""

def _run_chain(question: str, max_steps: int = 12) -> dict:
    # Only use RAG if needed
    rag_context = ""
    if _should_use_rag(question):
        rag_context = retrieve(question, top_k=3)
        rag_block = f"\n\nTELECOM CONTEXT (use for recommendations):\n{rag_context}"
    else:
        rag_block = ""

    system = AGENT_SYSTEM\
        .replace("{rag_context}", rag_block)\
        .replace("{schema}", FULL_SCHEMA)

    # Force treemap output for drilldown/tree questions
    q_lower = question.lower()
    is_treemap = any(w in q_lower for w in ("drilldown", "drill down", "drill-down", "tree", "treemap", "hierarchy"))
    if is_treemap:
        system += (
            "\n\nCRITICAL: This question requires a TREEMAP. One query, then CONCLUDE.\n"
            "Use 2 GROUP BY columns for simple hierarchies, up to 4 for deep drilldowns.\n"
            "QUERY RULES:\n"
            "- To get subscriber region: use subscribers.region — do NOT join through sites\n"
            "- For subscriber technology: use subscriber_technology.current_technology\n"
            "- For device capability: use devices.max_technology or devices.is_5g_capable\n"
            "- cells has NO msisdn column — never join cells ON msisdn\n"
            "CORRECT PATTERNS (copy the one that matches the question):\n"
            "  Subscriber count by region+technology:  SELECT s.region, st.current_technology, COUNT(*) as n FROM subscribers s JOIN subscriber_technology st ON s.msisdn=st.msisdn GROUP BY s.region, st.current_technology\n"
            "  KPI speed by region+technology:         SELECT si.region, c.technology, ROUND(AVG(k.dl_throughput_mbps),2) as avg_dl FROM kpis_daily k JOIN cells c ON k.cell_id=c.cell_id JOIN sites si ON c.site_id=si.site_id WHERE k.date=(SELECT MAX(date) FROM kpis_daily) GROUP BY si.region, c.technology ORDER BY si.region, c.technology\n"
            "  Tech vs device capability:              SELECT st.current_technology, d.max_technology, COUNT(*) as n FROM subscriber_technology st JOIN devices d ON st.msisdn=d.msisdn GROUP BY st.current_technology, d.max_technology\n"
            "  Device drilldown (os/brand/tech/volte):  SELECT d.os, d.brand, d.max_technology, d.volte_capable, COUNT(*) as n FROM devices d JOIN subscriber_technology st ON d.msisdn=st.msisdn GROUP BY d.os, d.brand, d.max_technology, d.volte_capable ORDER BY n DESC\n"
            "  Utilization gap (current vs max):        SELECT d.max_technology, st.current_technology, COUNT(*) as n FROM devices d JOIN subscriber_technology st ON d.msisdn=st.msisdn GROUP BY d.max_technology, st.current_technology ORDER BY d.max_technology, n DESC\n"
            "After running ONE query, output: CONCLUDE: {\"text\": \"your analysis here\"}\n"
            "The chart is built automatically — do NOT include chart JSON in your CONCLUDE."
        )
    context = ""
    steps_log = []
    _seen_sqls: set = set()

    treemap_remind = (
        "\nYou have run your query. Now output: CONCLUDE: {\"text\": \"your analysis here\"}"
    ) if is_treemap else ""

    for step in range(max_steps):
        if step > 0:
            system = AGENT_SYSTEM\
                .replace("{rag_context}", rag_block)\
                .replace("{schema}", COMPACT_SCHEMA)
        if context:
            queries_done = context.count("Step ")
            if queries_done >= 6:
                if is_treemap:
                    prompt = (
                        f"Question: {question}\n\n"
                        f"Data gathered:\n{context}\n\n"
                        f"You have sufficient data. Output: CONCLUDE: {{\"text\": \"your analysis\"}}"
                    )
                else:
                    prompt = (
                        f"Question: {question}\n\n"
                        f"Data gathered:\n{context}\n\n"
                        f"You have sufficient data. "
                        f"Output CONCLUDE: with your full analysis as JSON, "
                        f"or PROPOSE: if an action should be taken."
                        f"{rag_block}"
                    )
            else:
                prompt = (
                    f"Question: {question}\n\n"
                    f"Data gathered so far:\n{context}\n\n"
                    f"Next step — output QUERY_SC, QUERY_OP, "
                    f"QUERY_BOTH, CONCLUDE, or PROPOSE:"
                    f"{treemap_remind}"
                )
        else:
            prompt = (
                f"Question: {question}\n\n"
                f"You MUST run a QUERY_SC or QUERY_OP to retrieve data before concluding. "
                f"Do NOT answer from memory. What SQL query do you need first?\n"
                f"Output only: QUERY_SC: <sql>"
            )

        _is_conclude_step = context and context.count("Step ") >= 1
        _max_tok = 400 if (is_treemap and _is_conclude_step) else (800 if _is_conclude_step else 300)
        response = _ollama(system, prompt, max_tokens=_max_tok, timeout=420)

        if response.startswith("ERROR:"):
            if context:
                break  # use whatever data we gathered so far
            return {"type": "error", "text": response}

        steps_log.append(response[:500])

        # ── CONCLUDE ──────────────────────────────────────────────
        if "CONCLUDE:" in response:
            # Reject premature CONCLUDE — model must have run at least 1 real query.
            # This prevents the 7B model from hallucinating answers from _memory context.
            queries_executed = context.count("Step ")
            if queries_executed == 0:
                # Force the model to query first
                context += f"\nStep {step+1}: CONCLUDE rejected — no data queried yet. Run a QUERY_SC or QUERY_OP first.\n"
                continue

            conclusion = response.split("CONCLUDE:", 1)[1].strip()
            for tag in ["QUERY_SC:", "QUERY_OP:", "QUERY_BOTH:", "Step "]:
                if tag in conclusion:
                    conclusion = conclusion.split(tag)[0].strip()
            conclusion = conclusion.rstrip(".,: \n")

            chart_spec = None
            text_only  = conclusion
            try:
                json_match = re.search(r'\{.*\}', conclusion, re.DOTALL)
                if json_match:
                    raw = json_match.group()
                    raw = re.sub(r'(\d),(\d{3})', r'\1\2', raw)
                    try:
                        parsed    = json.loads(raw)
                        text_only = parsed.get("text", conclusion)
                        if not is_treemap:
                            chart_spec = parsed.get("chart", None)
                            # handle chart keys at root level (common LLM mistake)
                            if chart_spec is None and "labels" in parsed:
                                chart_spec = {
                                    "type":    parsed.get("type", "treemap"),
                                    "title":   parsed.get("title", ""),
                                    "labels":  parsed.get("labels", []),
                                    "parents": parsed.get("parents", []),
                                    "values":  parsed.get("values", []),
                                }
                            # ensure treemap type is set correctly
                            if chart_spec and "labels" in chart_spec and chart_spec.get("type") != "treemap":
                                chart_spec["type"] = "treemap"
                    except json.JSONDecodeError:
                        # JSON truncated — salvage just the text field
                        text_match = re.search(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
                        if text_match:
                            text_only = text_match.group(1)
                        else:
                            text_only = re.sub(r'\{.*', '', conclusion, flags=re.DOTALL).strip()
                    text_only = re.sub(r'\{.*\}', '', text_only, flags=re.DOTALL).strip()
                    for tag in ["PROPOSE:", "QUERY_SC:", "QUERY_OP:"]:
                        if tag in text_only:
                            text_only = text_only.split(tag)[0].strip()
            except Exception:
                text_only = re.sub(r'\{.*\}', '', conclusion, flags=re.DOTALL).strip()

            # For treemap questions, ALWAYS build chart from query results — never rely on model JSON
            if is_treemap and context:
                chart_spec = _build_treemap_from_context(context)

            # For non-treemap, fall back to LLM chart formatter
            if chart_spec is None:
                full_context = text_only
                if context:
                    full_context = f"{text_only}\n\nRaw data:\n{context[-1500:]}"
                chart_spec = _to_chart_spec(full_context, question)

            return {
                "type":  "analysis",
                "text":  text_only,
                "chart": chart_spec,
                "steps": steps_log
            }

        # ── PROPOSE ───────────────────────────────────────────────
        if "PROPOSE:" in response:
            proposal = response.split("PROPOSE:", 1)[1].strip()
            for tag in ["QUERY_SC:", "QUERY_OP:", "QUERY_BOTH:", "Step "]:
                if tag in proposal:
                    proposal = proposal.split(tag)[0].strip()
            proposal = proposal.rstrip(".,: \n")
            return {
                "type":                 "proposal",
                "text":                 proposal,
                "steps":                steps_log,
                "pending_confirmation": True
            }

        # ── QUERY_SC / QUERY_OP / QUERY_BOTH ─────────────────────
        executed = False
        for tag, runner in [
            ("QUERY_BOTH:", query_sc),
            ("QUERY_SC:",   query_sc),
            ("QUERY_OP:",   query_op),
        ]:
            if tag in response:
                for line in response.split("\n"):
                    stripped = line.strip()
                    if stripped.startswith(tag):
                        sql_raw = stripped.split(tag, 1)[1].strip()
                        sql = re.sub(r'```sql|```', '', sql_raw).strip()

                        if tag == "QUERY_BOTH:" and "ATTACH" not in sql.upper():
                            attach = f"ATTACH DATABASE '{OP_DB}' AS op; "
                            sql = attach + sql

                        if not sql.upper().startswith("SELECT") and \
                           not sql.upper().startswith("ATTACH"):
                            context += f"\nStep {step+1}: Invalid SQL skipped.\n"
                            executed = True
                            break

                        db_path = OP_DB if tag == "QUERY_OP:" else SC_DB

                        # Duplicate query guard
                        sql_key = re.sub(r'\s+', ' ', sql.strip().upper())
                        if sql_key in _seen_sqls:
                            context += f"\nStep {step+1}: Duplicate query skipped — already ran this. Use the previous result or try a different query.\n"
                            executed = True
                            break
                        _seen_sqls.add(sql_key)

                        # Semantic checks first (catches issues EXPLAIN won't see)
                        sem_err = _check_sql_semantics(sql)
                        if sem_err:
                            context += f"\nStep {step+1} semantic error:\n{sem_err}\n"
                            executed = True
                            break

                        sql_err = _check_sql(sql, db_path)
                        if sql_err:
                            context += f"\nStep {step+1}: {sql_err}\n"
                            executed = True
                            break

                        results = runner(sql)
                        if results and "error" not in results[0]:
                            n = len(results)
                            # For multi-row categorical results (e.g. by region),
                            # pass ALL rows compactly so the LLM sees complete data.
                            # Only truncate for very large non-categorical results (>30 rows).
                            if n <= 30:
                                snippet = json.dumps(results, indent=2)
                            else:
                                # Compact format: one line per row, all rows
                                compact = ", ".join(
                                    str(dict(r)) for r in results
                                )
                                snippet = f"[{compact}]"
                            context += (
                                f"\nStep {step+1} [{tag.rstrip(':')}]:\n"
                                f"SQL: {sql[:120]}\n"
                                f"Results ({n} rows): {snippet}\n"
                            )
                        elif results and "error" in results[0]:
                            context += f"\nStep {step+1}: Query error: {results[0]['error']}\n"
                        else:
                            # 0 rows — probe the filtered columns so model can self-correct
                            probe = _probe_empty_result(sql, db_path)
                            if probe:
                                context += f"\nStep {step+1}: {probe}\n"
                            else:
                                context += f"\nStep {step+1}: Query returned 0 rows.\n"
                        executed = True
                        break
                if executed:
                    break

        if not executed:
            if context:
                return {
                    "type": "analysis",
                    "text": _ollama(
                        "You are a telecom analyst. Summarize findings concisely.",
                        f"Question: {question}\n\nData:\n{context}\n\nSummary:"
                    ),
                    "chart": None,
                    "steps": steps_log
                }
            # Model output plain text with no query — force it to start
            context += f"\nStep {step+1}: No query detected. Stop explaining. Output ONLY: QUERY_SC: <corrected sql on one line>\n"

    # Fallback
    if context:
        summary = _ollama(
            "You are a telecom analyst. Summarize findings and recommend actions.",
            f"Question: {question}\n\nData gathered:\n{context}\n\nAnalysis:"
        )
        chart_spec = _build_treemap_from_context(context) if is_treemap else None
        if chart_spec is None:
            chart_spec = _to_chart_spec(f"{summary}\n\nRaw data:\n{context[-1500:]}", question)
        return {"type": "analysis", "text": summary, "chart": chart_spec, "steps": steps_log}

    return {
        "type": "error",
        "text": "Could not gather enough data to answer this question."
    }

# ═══════════════════════════════════════════════════════════════════════
# FAST PATH — simple metric questions
# FIX 1: Added regional breakdown patterns to COMPLEX_PATTERNS
# FIX 4: ATTACH queries always routed to query_sc()
# ═══════════════════════════════════════════════════════════════════════

SIMPLE_KPI_PATTERNS = [
    "throughput", "availability", "drop rate", "latency", "sinr", "rsrp",
    "average", "avg", "how many", "count", "total", "number of",
    "what is the", "show me the", "what's the", "what are they using",
    "which technology", "distribution", "split", "of those", "among those",
]

# FIX 1: Regional breakdown patterns force full chain so LIMIT is never added
COMPLEX_PATTERNS = [
    "campaign", "propose", "upsell", "migrate", "recommend", "suggest",
    "should we", "how do we", "which subscribers", "find candidates",
    "create", "launch", "assign", "compare",
    "what do you propose", "what would you suggest", "what should we do",
    "how can we", "how could we", "how would we", "how to increase",
    "how to improve", "how to grow", "what can we do", "what should we",
    "increase", "improve", "grow", "boost", "expand", "why is",
    "drilldown", "drill down", "drill-down", "tree", "treemap",
    "breakdown", "hierarchy",
    # FIX 1 additions — force full chain for all regional breakdowns
    "by region", "per region", "distribution by region",
    "regional breakdown", "each region", "all regions",
    "region by region", "regions",
    # Additional regional phrasings the LLM prompt may produce
    "based on region", "by area", "per area", "across regions",
    "for each region", "region-wise", "region wise",
    # Multi-step / multi-metric questions — fast path can only run ONE query
    "first.*then", "and also", "as well as", "in addition",
    "both.*and", "furthermore", "step 1", "step 2",
    "then we", "then count", "then find", "then show",
]

def _is_simple_metric(question: str) -> bool:
    import re as _re
    q = question.lower()
    has_simple  = any(p in q for p in SIMPLE_KPI_PATTERNS)
    has_complex = any(p in q for p in COMPLEX_PATTERNS)

    # Multi-step check: if the question contains 2+ sentences with action verbs,
    # it needs multiple queries — _fast_query can only run one. Force full chain.
    action_verbs = ["count", "find", "show", "list", "get", "how many",
                    "what is", "calculate", "identify", "give me"]
    sentences = [s.strip() for s in _re.split(r'[.!?]', question) if s.strip()]
    multi_ask = sum(1 for s in sentences
                    if any(v in s.lower() for v in action_verbs)) >= 2

    return has_simple and not has_complex and not multi_ask


def _should_use_rag(question: str) -> bool:
    """Determine if RAG context is needed for this query."""
    q_lower = question.lower()
    
    # RAG is needed for these types of questions
    rag_keywords = [
        # Analysis and conclusions
        "analyze", "analysis", "recommend", "suggest", "propose",
        "what should", "how can we", "what do you think", "insight",
        "strategy", "opportunity", "trend", "pattern", "correlation",
        
        # Campaign and actions
        "campaign", "upsell", "migrate", "convert", "launch", "target",
        "which subscribers", "find candidates", "identify",
        
        # Comparative analysis
        "compare", "versus", "vs", "why", "reason", "cause",
        "impact", "effect", "improve", "optimize",
        
        # Multi-step analysis (needs context)
        "drilldown", "drill down", "hierarchy", "breakdown", "distribution",
        "first then", "step by step", "how to", "approach"
    ]
    
    # Simple SQL keywords - RAG NOT needed
    simple_keywords = [
        "count", "how many", "total", "number of",
        "what is the", "show me", "list", "get", "average", "avg",
        "throughput", "availability", "drop rate", "sinr", "rsrp",
        "region", "by region", "per region"
    ]
    
    # If question is purely about getting numbers/data, skip RAG
    is_simple = any(kw in q_lower for kw in simple_keywords)
    needs_rag = any(kw in q_lower for kw in rag_keywords)
    
    # If simple query, no RAG needed
    if is_simple and not needs_rag:
        return False
    
    # For complex or ambiguous, use RAG
    return needs_rag or len(q_lower.split()) > 10  # Long questions often need context


def _fast_query(question: str) -> dict:
    """Single-shot query — no reasoning chain."""
    # Only use RAG if needed
    rag_context = ""
    if _should_use_rag(question):
        rag_context = retrieve(question, top_k=2)
        rag_block = rag_context
    else:
        rag_block = ""
    
    sql_system = f"""You are a telecom SQL analyst.
{rag_block}

{FULL_SCHEMA}

CRITICAL: kpis_daily has NO technology column.
Filter by technology using: JOIN cells c ON k.cell_id=c.cell_id WHERE c.technology='4G'
NEVER add LIMIT to GROUP BY region queries — always return ALL regions.

Output ONLY a single SQL query. No explanation. No markdown. Just the SQL.

Examples:
Average 4G throughput in Sfax:
SELECT ROUND(AVG(k.dl_throughput_mbps),2) as avg_dl FROM kpis_daily k JOIN cells c ON k.cell_id=c.cell_id JOIN sites s ON c.site_id=s.site_id WHERE s.region='Sfax' AND c.technology='4G' AND k.date=(SELECT MAX(date) FROM kpis_daily)

Active critical alarms:
SELECT COUNT(*) as n FROM network_alarms WHERE severity='critical' AND is_active=1

Subscribers on 3G in Tunis:
SELECT COUNT(*) as n FROM subscribers s JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE st.current_technology='3G' AND s.region='Tunis'
"""

    sql_raw = _ollama(sql_system, f"Question: {question}\nSQL:", max_tokens=200)
    sql = sql_raw.strip().strip("```sql").strip("```").strip()

    # FIX 4: ATTACH queries must always go to query_sc(), check this FIRST
    if "ATTACH" in sql.upper() or "op." in sql:
        results = query_sc(sql)
    else:
        op_keywords = ["plan", "customer", "billing", "arpu", "offer", "campaign",
                       "segment", "subscription", "revenue", "payment"]
        use_op = any(k in question.lower() for k in op_keywords)
        if use_op:
            results = query_op(sql)
        else:
            results = query_sc(sql)

    if not results or "error" in results[0]:
        return _run_chain(question)

    # ... rest of _fast_query remains the same ...

    # Build chart spec directly from full results — avoids LLM token truncation
    # for large result sets (e.g. 24 regions). Works when result is 2-column shape.
    chart_spec = None
    keys = list(results[0].keys())
    if len(keys) == 2:
        cat_key = keys[0]
        val_key = keys[1]
        try:
            x_vals = [str(r[cat_key]) for r in results]
            y_vals = [float(r[val_key]) if isinstance(r[val_key], float)
                      else int(r[val_key]) for r in results]
            q_lower = question.lower()
            total = sum(y_vals)

            if any(w in q_lower for w in ["treemap","tree","drill","drilldown","drill down","drill-down","hierarchy"]):
                # Build proper treemap: Total root → each region
                labels  = ["Total"] + x_vals
                parents = [""]      + ["Total"] * len(x_vals)
                values  = [total]   + y_vals
                chart_spec = {
                    "type":    "treemap",
                    "title":   question[:60],
                    "labels":  labels,
                    "parents": parents,
                    "values":  values,
                }
            elif any(w in q_lower for w in ["proportion","share","split","pie"]):
                chart_spec = {
                    "type":    "pie",
                    "title":   question[:60],
                    "x":       x_vals,
                    "y":       y_vals,
                    "x_label": cat_key.replace("_", " ").title(),
                    "y_label": val_key.replace("_", " ").title(),
                }
            else:
                chart_spec = {
                    "type":    "bar",
                    "title":   question[:60],
                    "x":       x_vals,
                    "y":       y_vals,
                    "x_label": cat_key.replace("_", " ").title(),
                    "y_label": val_key.replace("_", " ").title(),
                }
        except (ValueError, TypeError, KeyError):
            chart_spec = None

    # LLM for text summary only — concise, no JSON marshalling of all rows
    total_rows = len(results)
    snippet = results[:12]
    row_note = f" ({total_rows} regions total)" if total_rows > 12 else ""

    text_only = _ollama(
        "You are a telecom analyst. Write a concise 1-2 sentence summary of ONLY the data shown. "
        "NEVER mention or invent metrics that are not in the query result. "
        "NEVER add numbers you did not see in the data. Only report what is explicitly in the result below. "
        "Output only plain text — no JSON, no markdown.",
        f"Question: {question}\nData{row_note}: {snippet}\nSummary:",
        max_tokens=150
    ).strip()

    # Fallback chart if direct build failed (e.g. multi-column result)
    if chart_spec is None:
        chart_spec = _to_chart_spec(json.dumps(results[:24]), question)

    return {"type": "analysis", "text": text_only, "chart": chart_spec}

# ═══════════════════════════════════════════════════════════════════════
# ACTION EXECUTION
# FIX 3: Structured JSON action format replaces brittle regex parsing
# ═══════════════════════════════════════════════════════════════════════

def _execute_action(proposal_text: str, original_question: str) -> dict:
    ACT_SYSTEM = f"""You are executing a confirmed telecom action.
Parse the proposal and output the ACTION as a single JSON object.

{FULL_SCHEMA}

Available actions — output exactly one JSON object:

{{"action": "create_campaign", "name": "...", "type": "5G_upsell|3G_migration|FWA|VoLTE_sunset|HVC_upsell", "offer_id": 1, "msisdn_filter_sql": "SELECT msisdn FROM ..."}}
{{"action": "assign_offer", "offer_id": 1, "msisdn_filter_sql": "SELECT msisdn FROM ..."}}
{{"action": "send_sms", "campaign_id": 1, "message": "..."}}
{{"action": "acknowledge_alarm", "alarm_id": 1}}
{{"action": "bulk_acknowledge", "region": "Tunis", "severity": "critical"}}
{{"action": "create_offer", "name": "...", "campaign_type": "5G_upsell", "target_technology": "5G", "discount_pct": 20, "bonus_data_gb": 10, "price_override": null, "validity_days": 30, "description": "..."}}

Output ONLY the JSON object. No explanation. No markdown.

Examples:
{{"action": "assign_offer", "offer_id": 1, "msisdn_filter_sql": "SELECT msisdn FROM subscribers s JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE s.region='Tunis' AND st.current_technology='3G'"}}
{{"action": "create_campaign", "name": "FWA Conversion Tunis", "type": "FWA", "offer_id": 5, "msisdn_filter_sql": "SELECT mp.msisdn FROM mobility_profile mp JOIN dou_monthly dm ON mp.msisdn=dm.msisdn AND mp.month=dm.month WHERE mp.mobility_class='stationary' AND dm.total_data_gb>30 AND mp.month=(SELECT MAX(month) FROM mobility_profile)"}}
"""
    action_raw = _ollama(
        ACT_SYSTEM,
        f"Proposal: {proposal_text}\nOriginal question: {original_question}\nJSON:",
        max_tokens=400
    )

    # FIX 3: parse as JSON instead of regex
    try:
        action_raw = action_raw.strip().strip("```json").strip("```").strip()
        action_json = json.loads(action_raw)
    except (json.JSONDecodeError, Exception):
        # fallback: try to find JSON block in response
        match = re.search(r'\{.*\}', action_raw, re.DOTALL)
        if match:
            try:
                action_json = json.loads(match.group())
            except Exception:
                return {"type": "error", "text": f"Could not parse action JSON: {action_raw[:200]}"}
        else:
            return {"type": "error", "text": f"No valid action JSON found: {action_raw[:200]}"}

    return _dispatch_action_json(action_json, original_question)


def _dispatch_action_json(action: dict, context: str = "") -> dict:
    """
    FIX 3: JSON-based action dispatcher — no regex, no brittle string parsing.
    """
    now = datetime.now().isoformat()
    atype = action.get("action", "")

    # ── create_offer ──────────────────────────────────────────────
    if atype == "create_offer":
        name     = action.get("name", "Unnamed Offer")
        ctype    = action.get("campaign_type", "")
        tech     = action.get("target_technology", "")
        disc     = float(action.get("discount_pct", 0))
        bonus    = int(float(action.get("bonus_data_gb", 0)))
        price    = action.get("price_override")
        price    = float(price) if price else None
        validity = int(action.get("validity_days", 30))
        desc     = action.get("description", "")
        ok = write_op(
            "INSERT INTO offers (offer_name,target_campaign,target_technology,discount_pct,"
            "bonus_data_gb,price_override,validity_days,is_active,description) VALUES(?,?,?,?,?,?,?,1,?)",
            (name, ctype, tech, disc, bonus, price, validity, desc)
        )
        if ok:
            row = query_op("SELECT offer_id FROM offers ORDER BY offer_id DESC LIMIT 1")
            oid = row[0]["offer_id"] if row else "?"
            return {
                "type": "success",
                "text": f"Offer **{name}** created (ID {oid}) — {ctype}, {disc}% off, {bonus}GB bonus"
            }
        return {"type": "error", "text": "Failed to create offer."}

    # ── assign_offer ──────────────────────────────────────────────
    if atype == "assign_offer":
        offer_id   = int(action.get("offer_id", 0))
        filter_sql = action.get("msisdn_filter_sql", "")
        if not offer_id or not filter_sql:
            return {"type": "error", "text": "Missing offer_id or msisdn_filter_sql."}

        sc_tables = ["subscribers","mobility","devices","coverage","kpis","cells","sites"]
        use_sc = any(t in filter_sql.lower() for t in sc_tables)
        targets = query_sc(filter_sql) if use_sc else query_op(filter_sql)

        if not targets or "error" in targets[0]:
            return {"type": "error", "text": "Filter query returned no results."}
        count = 0
        conn  = sqlite3.connect(OP_DB)
        for row in targets:
            msisdn = row.get("msisdn")
            if msisdn:
                conn.execute(
                    "INSERT OR IGNORE INTO offer_assignments (msisdn,offer_id,assigned_date,channel,accepted) VALUES(?,?,?,'agent',0)",
                    (msisdn, offer_id, now)
                )
                count += 1
        conn.commit()
        conn.close()
        offer_name = query_op(f"SELECT offer_name FROM offers WHERE offer_id={offer_id}")
        name = offer_name[0]["offer_name"] if offer_name else f"Offer {offer_id}"
        return {"type": "success", "text": f"Offer **{name}** assigned to **{count:,}** subscribers."}

    # ── create_campaign ───────────────────────────────────────────
    if atype == "create_campaign":
        name       = action.get("name", "Unnamed Campaign")
        ctype      = action.get("type", "")
        offer_id   = action.get("offer_id")
        filter_sql = action.get("msisdn_filter_sql", "")

        ok = write_op(
            "INSERT INTO campaigns (campaign_name,campaign_type,target_count,launched_date,status,created_by,offer_id) VALUES(?,?,0,?,'active','agent',?)",
            (name, ctype, now, offer_id)
        )
        if not ok:
            return {"type": "error", "text": "Failed to create campaign."}

        camp        = query_op("SELECT campaign_id FROM campaigns ORDER BY campaign_id DESC LIMIT 1")
        campaign_id = camp[0]["campaign_id"] if camp else None
        count       = 0

        if filter_sql and campaign_id:
            sc_tables = ["subscribers","mobility","devices","coverage","kpis","cells","sites"]
            use_sc = any(t in filter_sql.lower() for t in sc_tables)
            targets = query_sc(filter_sql) if use_sc else query_op(filter_sql)
            if targets and "error" not in targets[0]:
                conn = sqlite3.connect(OP_DB)
                for row in targets:
                    msisdn = row.get("msisdn")
                    if msisdn:
                        conn.execute(
                            "INSERT OR IGNORE INTO campaign_targets (campaign_id,msisdn,reason,score,notified,converted) VALUES(?,?,?,0.8,0,0)",
                            (campaign_id, msisdn, ctype)
                        )
                        count += 1
                conn.execute("UPDATE campaigns SET target_count=? WHERE campaign_id=?", (count, campaign_id))
                conn.commit()
                conn.close()

        return {
            "type": "success",
            "text": f"Campaign **{name}** created (ID {campaign_id}) — {ctype}, {count:,} targets, status: active"
        }

    # ── acknowledge_alarm ─────────────────────────────────────────
    if atype == "acknowledge_alarm":
        alarm_id = action.get("alarm_id")
        if alarm_id:
            write_sc("UPDATE network_alarms SET is_active=0 WHERE alarm_id=?", (int(alarm_id),))
            return {"type": "success", "text": f"Alarm {alarm_id} acknowledged."}
        return {"type": "error", "text": "Missing alarm_id."}

    # ── bulk_acknowledge ──────────────────────────────────────────
    if atype == "bulk_acknowledge":
        region   = action.get("region")
        severity = action.get("severity")
        wh = ["a.is_active=1"]; p = []
        if severity: wh.append("a.severity=?"); p.append(severity)
        if region:   wh.append("s.region=?");   p.append(region)
        join = "JOIN cells c ON a.cell_id=c.cell_id JOIN sites s ON c.site_id=s.site_id" if region else ""
        count_r = query_sc(f"SELECT COUNT(*) as n FROM network_alarms a {join} WHERE {' AND '.join(wh)}")
        conn = sqlite3.connect(SC_DB)
        conn.execute(
            f"UPDATE network_alarms SET is_active=0 WHERE alarm_id IN "
            f"(SELECT a.alarm_id FROM network_alarms a {join} WHERE {' AND '.join(wh)})",
            tuple(p)
        )
        conn.commit()
        conn.close()
        n = count_r[0]["n"] if count_r else "?"
        return {"type": "success", "text": f"{n} alarms acknowledged." +
                (f" Region: {region}" if region else "") +
                (f" Severity: {severity}" if severity else "")}

    return {"type": "error", "text": f"Unknown action type: {atype}"}


# Keep old _dispatch_action as thin wrapper for backward compatibility
def _dispatch_action(action_str: str, context: str = "") -> dict:
    return {"type": "error", "text": "Use _dispatch_action_json() instead."}

# ═══════════════════════════════════════════════════════════════════════
# MEMORY
# FIX 5: Skip LLM compression for short texts — use truncation instead
# ═══════════════════════════════════════════════════════════════════════

_memory  = []
_pending = None

def _compress(text: str) -> str:
    # FIX 5: Only call LLM if text is long enough to benefit from compression
    if len(text) <= 200:
        return text
    if len(text) <= 400:
        # Simple truncation for medium texts — no LLM call needed
        return text[:300] + "…"
    # Only invoke LLM for genuinely long texts
    return _ollama(
        "Summarize in 2-3 sentences. Keep key numbers and recommendations.",
        text, max_tokens=80
    )

def _memory_context() -> str:
    if not _memory:
        return ""
    lines = [f"{m['role'].upper()}: {m['summary']}" for m in _memory[-4:]]
    return "Recent context:\n" + "\n".join(lines) + "\n\n"

# ═══════════════════════════════════════════════════════════════════════
# CONFIRMATION DETECTION
# ═══════════════════════════════════════════════════════════════════════

CONFIRM_WORDS = {
    "yes", "confirm", "go ahead", "do it", "proceed",
    "execute", "apply", "launch it", "run it", "ok", "okay",
    "approved", "validate", "confirm it"
}
DENY_WORDS = {
    "no", "cancel", "stop", "abort", "don't", "do not",
    "nevermind", "never mind", "skip"
}

def _is_confirmation(text: str) -> bool:
    t = text.lower().strip().rstrip(".,!")
    return t in CONFIRM_WORDS or any(w in t for w in CONFIRM_WORDS)

def _is_denial(text: str) -> bool:
    t = text.lower().strip()
    return t in DENY_WORDS or any(w in t for w in DENY_WORDS)

# ═══════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════

def run_agent(user_input: str) -> dict:
    global _pending, _memory

    # confirmation of pending proposal
    if _pending and _is_confirmation(user_input):
        proposal = _pending
        _pending = None
        result = _execute_action(proposal["text"], proposal["question"])
        _memory.append({"role": "agent", "summary": _compress(result["text"])})
        return result

    # denial of pending proposal
    if _pending and _is_denial(user_input):
        _pending = None
        return {"type": "cancelled", "text": "Action cancelled. No changes were made."}

    mem_ctx  = _memory_context()
    enriched = f"{mem_ctx}{user_input}" if mem_ctx else user_input
    _memory.append({"role": "user", "summary": user_input})

    # resolve short follow-ups, otherwise pass full enriched context
    resolved_input = enriched
    if len(user_input.split()) <= 4 and mem_ctx:
        resolved_input = _ollama(
            "Rewrite the follow-up question as a complete standalone question using the context. Output only the rewritten question, nothing else.",
            f"Context: {mem_ctx}\nFollow-up: {user_input}\nComplete question:",
            max_tokens=60
        )
        resolved_input = resolved_input.strip().strip('"')

    # fast path or full chain
    if _is_simple_metric(resolved_input):
        result = _fast_query(resolved_input)
    else:
        result = _run_chain(resolved_input)

    # store proposal for confirmation
    if result.get("type") == "proposal":
        _pending = {"text": result["text"], "question": user_input}
        result["text"] = (
            result["text"] +
            "\n\n---\n**Type 'confirm' to proceed or 'cancel' to abort.**"
        )

    _memory.append({"role": "agent", "summary": _compress(result.get("text", ""))})
    return result


def get_proactive_alerts() -> list:
    alerts = []

    critical = query_sc("SELECT COUNT(*) as n FROM network_alarms WHERE severity='critical' AND is_active=1")
    if critical and critical[0].get("n", 0) > 30:
        worst = query_sc("""
            SELECT s.region, COUNT(*) as n FROM network_alarms a
            JOIN cells c ON a.cell_id=c.cell_id JOIN sites s ON c.site_id=s.site_id
            WHERE a.severity='critical' AND a.is_active=1
            GROUP BY s.region ORDER BY n DESC LIMIT 1
        """)
        region = worst[0]["region"] if worst else "unknown"
        alerts.append({
            "type": "critical_alarms", "severity": "critical",
            "message": f"⚠️ {critical[0]['n']} critical alarms active. Worst region: {region}",
            "action": f"Investigate network issues in {region}"
        })

    sunset_risk = query_sc("""
        SELECT COUNT(DISTINCT s.msisdn) as n FROM subscribers s
        JOIN devices d ON s.msisdn=d.msisdn
        JOIN subscriber_technology st ON s.msisdn=st.msisdn
        WHERE (d.volte_capable=0 AND st.current_technology='3G')
        OR (d.volte_capable=1 AND st.current_technology='3G' AND st.volte_active=0)
    """)
    if sunset_risk and sunset_risk[0].get("n", 0) > 100:
        alerts.append({
            "type": "sunset_risk", "severity": "warning",
            "message": f"📵 {sunset_risk[0]['n']:,} subscribers at risk from 3G sunset.",
            "action": "Run 3G sunset migration analysis"
        })

    fwa = query_sc("""
        SELECT COUNT(DISTINCT mp.msisdn) as n FROM mobility_profile mp
        JOIN dou_monthly dm ON mp.msisdn=dm.msisdn AND mp.month=dm.month
        WHERE mp.mobility_class='stationary' AND dm.total_data_gb > 30
        AND mp.month=(SELECT MAX(month) FROM mobility_profile)
    """)
    if fwa and fwa[0].get("n", 0) > 50:
        alerts.append({
            "type": "fwa_opportunity", "severity": "info",
            "message": f"🏠 {fwa[0]['n']:,} stationary high-DOU subscribers are FWA candidates.",
            "action": "Analyze FWA conversion opportunity"
        })

    five_g = query_sc("""
        SELECT COUNT(*) as n FROM devices d
        JOIN subscriber_technology st ON d.msisdn=st.msisdn
        WHERE d.is_5g_capable=1 AND st.current_technology='4G'
        AND EXISTS(SELECT 1 FROM coverage cv WHERE cv.msisdn=d.msisdn AND cv.technology_available='5G')
    """)
    if five_g and five_g[0].get("n", 0) > 100:
        alerts.append({
            "type": "5g_upsell", "severity": "info",
            "message": f"📶 {five_g[0]['n']:,} subscribers have 5G devices and coverage but are on 4G plans.",
            "action": "Launch 5G upsell campaign"
        })

    return alerts


def reset_memory():
    global _memory, _pending
    _memory  = []
    _pending = None