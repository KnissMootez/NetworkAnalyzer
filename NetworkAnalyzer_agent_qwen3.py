import os
import re
import json
import sqlite3
import requests
from datetime import datetime, timedelta
from rag_retriever import retrieve, retrieve_sql

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SC_DB    = os.path.join(BASE_DIR, "NetworkAnalyzer_new.db")
OP_DB    = os.path.join(BASE_DIR, "operator_new.db")
# ═══════════════════════════════════════════════════════════════════════
# MODEL CONFIG — Qwen3 8B (thinking mode capable, better reasoning/conclusions)
# ═══════════════════════════════════════════════════════════════════════
MODEL           = os.environ.get("OLLAMA_MODEL", "qwen3:8b")
OLLAMA_BASE     = os.environ.get("OLLAMA_URL",   "http://localhost:11434")
NUM_YuanU_LAYERS  = 25
NUM_THREADS     = 8
CTX_SIZE        = 8192


def _print_model_config():
    print(f"╔══ NetworkAnalyzer Agent ══════════════════════")
    print(f"║  Model:       {MODEL}")
    print(f"║  YuanU layers:  {NUM_YuanU_LAYERS}  (VRAM)")
    print(f"║  CPU threads: {NUM_THREADS}  (RAM)")
    print(f"║  Context:     {CTX_SIZE} tokens")
    print(f"╚═════════════════════════════════════════")

_print_model_config()


def set_gpu_layers(n: int):
    global NUM_YuanU_LAYERS
    NUM_YuanU_LAYERS = n
    print(f"YuanU layers updated to {n} — takes effect on next query")


# ═══════════════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════════════

import re as _re

# ── Column validator — built at startup from real DB schema ─────────────
_TABLE_COLUMNS: dict[str, set[str]] = {}  # table_name → {col1, col2, ...}

def _load_schema_columns():
    """Load every table's real columns from both DBs into _TABLE_COLUMNS."""
    global _TABLE_COLUMNS
    try:
        sc = sqlite3.connect(SC_DB)
        for (tbl,) in sc.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            cols = {r[1].lower() for r in sc.execute(f"PRAGMA table_info({tbl})").fetchall()}
            _TABLE_COLUMNS[tbl.lower()] = cols
        sc.close()
        op = sqlite3.connect(OP_DB)
        for (tbl,) in op.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            cols = {r[1].lower() for r in op.execute(f"PRAGMA table_info({tbl})").fetchall()}
            _TABLE_COLUMNS[tbl.lower()] = cols
        op.close()
    except Exception as e:
        print(f"[SchemaLoader] Warning: {e}")

def _check_columns(sql: str) -> list[str]:
    """Return list of 'alias.col' references where col does not exist in the resolved table.
    Only checks explicit alias.column patterns to avoid false positives."""
    if not _TABLE_COLUMNS:
        return []
    # Build alias→table map from FROM/JOIN clauses
    alias_map: dict[str, str] = {}
    for m in _re.finditer(
        r'\b(?:FROM|JOIN)\s+(?:op\.)?(\w+)\s+(?:AS\s+)?(\w+)',
        sql, _re.IGNORECASE
    ):
        tbl, alias = m.group(1).lower(), m.group(2).lower()
        alias_map[alias] = tbl
        alias_map[tbl] = tbl  # table name itself as alias

    errors = []
    for m in _re.finditer(r'\b(\w+)\.(\w+)\b', sql):
        alias, col = m.group(1).lower(), m.group(2).lower()
        if alias in ('op', 'date', 'now', 'select', 'null'):
            continue
        tbl = alias_map.get(alias)
        if tbl and tbl in _TABLE_COLUMNS:
            if col not in _TABLE_COLUMNS[tbl] and col not in ('*',):
                errors.append(f"{alias}.{col} (table {tbl} has no column '{col}')")
    return errors


def _strip_limit(sql: str) -> str:
    """Remove LIMIT only when GROUP BY is on a low-cardinality categorical column
    (region, technology, segment, zone, status) — not on IDs or high-cardinality cols.
    This prevents cutting off regional breakdowns while keeping intentional TOP-N limits."""
    if not _re.search(r'\bGROUP\s+BY\b', sql, _re.IGNORECASE):
        return sql
    group_match = _re.search(r'\bGROUP\s+BY\b\s+(.+?)(?:\bHAVING\b|\bORDER\b|\bLIMIT\b|$)', sql, _re.IGNORECASE | _re.DOTALL)
    if not group_match:
        return sql
    group_cols = group_match.group(1).lower()
    categorical = ('region', 'technology', 'segment', 'zone', 'status', 'technology',
                   'value_segment', 'plan_type', 'contract_type', 'mobility_class',
                   'experience_label', 'os', 'brand', 'payment_status')
    if any(col in group_cols for col in categorical):
        sql = _re.sub(r'\bLIMIT\s+\d+\b', '', sql, flags=_re.IGNORECASE).strip()
    return sql

def _enforce_sql_rules(sql: str) -> str:
    """Minimal structural rules that cannot be inferred from data alone."""
    # subscriptions: always filter is_current=1 — this is a schema design rule, not a data value
    if _re.search(r'\bsubscriptions\b', sql, _re.IGNORECASE) and \
       not _re.search(r'is_current', sql, _re.IGNORECASE):
        def _inject_is_current(m):
            alias_match = _re.search(r'JOIN\s+(?:op\.)?subscriptions\s+(\w+)', m.group(0), _re.IGNORECASE)
            alias = alias_match.group(1) if alias_match else 'sub'
            return m.group(0) + f' AND {alias}.is_current=1'
        sql = _re.sub(
            r'JOIN\s+(?:op\.)?subscriptions\s+\w+\s+ON\s+\S+\s*=\s*\S+',
            _inject_is_current, sql, count=1, flags=_re.IGNORECASE
        )
    return sql

def _get_conn(db_path: str, attach_op: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    if attach_op:
        conn.execute(f"ATTACH DATABASE '{OP_DB}' AS op")
    return conn


def _col_facts(conn: sqlite3.Connection, table: str, col: str) -> dict | None:
    """Return real facts about table.col from the live DB.
    For categorical columns: distinct values.
    For numeric columns: min, max, p20, p80."""
    try:
        bare_table = table.split(".")[-1]  # strip "op." prefix for PRAGMA
        type_row = conn.execute(
            f"SELECT type FROM pragma_table_info('{bare_table}') WHERE name='{col}'"
        ).fetchone()
        if not type_row:
            return None
        col_type = (type_row[0] or "").upper()
        is_numeric = any(t in col_type for t in ("INT", "REAL", "FLOAT", "NUMERIC", "DOUBLE"))

        if is_numeric:
            row = conn.execute(
                f"SELECT MIN({col}), MAX({col}), COUNT(*) FROM {table} WHERE {col} IS NOT NULL"
            ).fetchone()
            if not row or row[2] == 0:
                return None
            mn, mx, n = row[0], row[1], row[2]
            p20 = conn.execute(
                f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL ORDER BY {col} ASC  LIMIT 1 OFFSET {max(0, n//5)}"
            ).fetchone()
            p80 = conn.execute(
                f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL ORDER BY {col} DESC LIMIT 1 OFFSET {max(0, n//5)}"
            ).fetchone()
            return {"kind": "numeric", "min": mn, "max": mx,
                    "p20": round(p20[0], 2) if p20 else mn,
                    "p80": round(p80[0], 2) if p80 else mx}
        else:
            rows = conn.execute(
                f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL ORDER BY {col} LIMIT 40"
            ).fetchall()
            return {"kind": "categorical", "values": [r[0] for r in rows]}
    except Exception:
        return None


def _build_alias_map(sql: str) -> dict[str, str]:
    """Map alias → real table name from FROM/JOIN clauses."""
    alias_map: dict[str, str] = {}
    for m in re.finditer(r'(?:FROM|JOIN)\s+((?:op\.)?\w+)(?:\s+(?:AS\s+)?(\w+))?', sql, re.I):
        raw_table = m.group(1)           # e.g. "op.customer_value" or "subscribers"
        bare = raw_table.split(".")[-1]  # "customer_value" / "subscribers"
        alias = (m.group(2) or bare).lower()
        alias_map[alias]      = raw_table
        alias_map[bare.lower()] = raw_table
    return alias_map


def _auto_correct_sql(sql: str, conn: sqlite3.Connection) -> tuple[str, list[str]]:
    """
    Generic SQL self-correction engine.
    Strips each WHERE filter one at a time, runs COUNT(*) to check if that filter
    is causing zero rows, then auto-corrects it using real data from the DB.

    Handles:
      - String equality:  col = 'wrong_val'  → nearest real value
      - Date/month max:   col = 'old-date'   → MAX(col) subquery
      - Numeric range:    col < -95          → p20 of joined result set
      - Subquery max:     col = (SELECT ...) → always use MAX()

    Returns (corrected_sql, list_of_correction_notes).
    """
    # Extract the FROM…WHERE body (everything after FROM, before GROUP BY / ORDER BY)
    from_match = re.search(
        r'\bFROM\b(.+?)(?:\bGROUP\s+BY\b|\bORDER\s+BY\b|$)',
        sql, re.I | re.DOTALL
    )
    if not from_match:
        return sql, []

    from_body  = from_match.group(1).strip()
    alias_map  = _build_alias_map(sql)
    corrections: list[str] = []
    fixed_sql  = sql

    # ── 1. Parse individual WHERE conditions ─────────────────────────────
    # Split on AND (avoid splitting inside parentheses / subqueries)
    where_match = re.search(
        r'\bWHERE\b(.+?)(?:\bGROUP\s+BY\b|\bORDER\s+BY\b|$)',
        sql, re.I | re.DOTALL
    )
    if not where_match:
        return sql, []

    # Tokenise top-level AND conditions (skip content inside parentheses)
    where_body = where_match.group(1)
    conditions: list[str] = []
    depth, start = 0, 0
    for i, ch in enumerate(where_body):
        if ch == '(':  depth += 1
        elif ch == ')': depth -= 1
        elif depth == 0 and where_body[i:i+4].upper() == ' AND':
            conditions.append(where_body[start:i].strip())
            start = i + 4
    conditions.append(where_body[start:].strip())

    for cond in conditions:
        cond = cond.strip()
        if not cond or cond == '1=1':
            continue

        # ── String equality: alias.col = 'val' ───────────────────────────
        str_m = re.match(r"(?:(\w+)\.)?(\w+)\s*=\s*'([^']*)'$", cond, re.I)
        if str_m:
            alias_raw, col, used_val = str_m.group(1), str_m.group(2), str_m.group(3)
            table = alias_map.get((alias_raw or "").lower()) or alias_map.get(col.lower())
            if table:
                facts = _col_facts(conn, table, col)
                if facts and facts["kind"] == "categorical":
                    vals = [str(v) for v in facts["values"]]
                    if used_val not in vals:
                        # Try case-insensitive match first
                        ci = next((v for v in vals if v.lower() == used_val.lower()), None)
                        # Try MAX for date-like columns
                        is_date_col = any(k in col.lower() for k in ("month", "date", "period"))
                        if ci:
                            new_cond = cond.replace(f"'{used_val}'", f"'{ci}'")
                            fixed_sql = fixed_sql.replace(cond, new_cond, 1)
                            corrections.append(f"{col}='{used_val}' → '{ci}' (case fix)")
                        elif is_date_col:
                            new_cond = re.sub(
                                r"=\s*'[^']*'", f"=(SELECT MAX({col}) FROM {table})", cond
                            )
                            fixed_sql = fixed_sql.replace(cond, new_cond, 1)
                            corrections.append(f"{col}='{used_val}' → MAX({col}) from {table}")
            continue

        # ── Numeric range: alias.col OP number ───────────────────────────
        num_m = re.match(r"(?:(\w+)\.)?(\w+)\s*([<>]=?)\s*(-?\d+(?:\.\d+)?)$", cond, re.I)
        if num_m:
            alias_raw, col, op, val_str = num_m.groups()
            val = float(val_str)
            table = alias_map.get((alias_raw or "").lower()) or alias_map.get(col.lower())
            if not table:
                continue
            # Strip this filter from FROM body and check actual range in joined result
            stripped_from = re.sub(
                r'(?:\w+\.)?' + re.escape(col) + r'\s*' + re.escape(op) + r'\s*' + re.escape(val_str),
                "1=1", from_body, count=1, flags=re.I
            )
            try:
                stat = conn.execute(
                    f"SELECT MIN({col}), MAX({col}), COUNT(*) FROM {stripped_from}"
                ).fetchone()
                if not stat or stat[0] is None or stat[2] == 0:
                    continue
                mn, mx, n = float(stat[0]), float(stat[1]), int(stat[2])
                out_of_range = (op in ('<', '<=') and val < mn) or (op in ('>', '>=') and val > mx)
                if not out_of_range:
                    continue
                offset = max(0, n // 5) if op in ('<', '<=') else max(0, (n * 4) // 5)
                order  = "ASC" if op in ('<', '<=') else "DESC"
                pct = conn.execute(
                    f"SELECT {col} FROM {stripped_from} ORDER BY {col} {order} LIMIT 1 OFFSET {offset}"
                ).fetchone()
                new_val = round(pct[0], 1) if pct else (round(mn, 1) if op in ('<', '<=') else round(mx, 1))
                fixed_sql = fixed_sql.replace(cond, f"{col} {op} {new_val}", 1)
                corrections.append(
                    f"{col}{op}{val_str} → {col}{op}{new_val} "
                    f"(joined range [{round(mn,1)}, {round(mx,1)}])"
                )
            except Exception:
                continue

    return fixed_sql, corrections


def _exec_with_fix(conn: sqlite3.Connection, sql: str) -> tuple[list, str | None]:
    """Execute SQL. If 0 rows, run _auto_correct_sql and retry once."""
    sql_clean = _strip_limit(_enforce_sql_rules(sql))

    # Pre-flight column check — catch hallucinated columns before execution
    col_errors = _check_columns(sql_clean)
    if col_errors:
        # Enrich error with which table actually has the column
        enriched = []
        for err in col_errors[:5]:
            # Try to find which table has the missing column
            col_match = re.search(r"no column '(\w+)'", err)
            if col_match:
                col_name = col_match.group(1).lower()
                owners = [t for t, cols in _TABLE_COLUMNS.items() if col_name in cols]
                if owners:
                    err += f" — '{col_name}' exists on: {', '.join(owners)}"
            enriched.append(err)
        err_msg = "Column errors: " + "; ".join(enriched)
        print(f"[ColCheck] {err_msg}")
        return [{"error": err_msg}], err_msg

    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql_clean).fetchall()
    if rows:
        return [dict(r) for r in rows], None

    fixed_sql, corrections = _auto_correct_sql(sql_clean, conn)
    if corrections and fixed_sql != sql_clean:
        try:
            rows2 = conn.execute(fixed_sql).fetchall()
            if rows2:
                note = "Auto-corrected: " + "; ".join(corrections)
                print(f"[AutoCorrect] {note}")
                return [dict(r) for r in rows2], note
        except Exception:
            pass
    return [], None


def query_sc(sql: str) -> list:
    try:
        conn = sqlite3.connect(SC_DB)
        # Auto-attach op DB whenever SQL references op. tables
        if re.search(r'\bop\.', sql, re.I):
            try:
                conn.execute(f"ATTACH DATABASE '{OP_DB}' AS op")
            except Exception:
                pass  # already attached
        statements = [s.strip() for s in sql.split(";") if s.strip()]
        rows, note = [], None
        for stmt in statements:
            if stmt.upper().startswith("ATTACH"):
                try:
                    conn.execute(stmt)
                except Exception:
                    pass  # already attached
            else:
                rows, note = _exec_with_fix(conn, stmt)
        conn.close()
        if note:
            rows = [{"_correction": note}] + rows
        return rows
    except Exception as e:
        return [{"error": str(e)}]

def query_op(sql: str) -> list:
    sql_clean = sql.split(";")[0].strip()
    try:
        conn = sqlite3.connect(OP_DB)
        rows, note = _exec_with_fix(conn, sql_clean)
        conn.close()
        if note:
            rows = [{"_correction": note}] + rows
        return rows
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

def schema_lookup(table_name: str) -> str:
    """Return the actual columns of a table from either DB."""
    table_name = table_name.strip().lower()
    results = []
    for db_path, label in [(SC_DB, "NetworkAnalyzer"), (OP_DB, "Operator")]:
        try:
            conn = sqlite3.connect(db_path)
            rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            conn.close()
            if rows:
                cols = ", ".join(f"{r[1]} ({r[2]})" for r in rows)
                results.append(f"{label} DB — {table_name}: {cols}")
        except Exception:
            pass
    if not results:
        # Table not found — list all available tables
        all_tables = []
        for db_path, label in [(SC_DB, "NetworkAnalyzer"), (OP_DB, "Operator")]:
            try:
                conn = sqlite3.connect(db_path)
                tbls = [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()]
                conn.close()
                all_tables.append(f"{label}: {', '.join(tbls)}")
            except Exception:
                pass
        return f"Table '{table_name}' not found. Available tables:\n" + "\n".join(all_tables)
    return "\n".join(results)


def _build_live_schema() -> str:
    """Build a compact schema string directly from the real DBs at runtime — always accurate."""
    lines = []
    for db_path, prefix, label in [(SC_DB, "", "NetworkAnalyzer DB"), (OP_DB, "op.", "OPERATOR DB")]:
        try:
            conn = sqlite3.connect(db_path)
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()]
            lines.append(f"\n=== {label} ===")
            for tbl in tables:
                cols = conn.execute(f"PRAGMA table_info({tbl})").fetchall()
                col_str = ", ".join(r[1] for r in cols)
                lines.append(f"{prefix}{tbl}: {col_str}")
            conn.close()
        except Exception:
            pass
    return "\n".join(lines)

LIVE_SCHEMA = _build_live_schema()


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
  region        TEXT      — Avatar region (Ba Sing Se, Fire Nation Capital, Omashu, etc.)
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
  zone       TEXT  — geographic zone: 'Earth Kingdom','Fire Nation','Water Tribe','Air Nomads' (NOT region names — zone is never 'Tunis')
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
!! COVERAGE RULE (MANDATORY): Any query about subscribers on a specific technology in a specific
!! region/city/nation MUST verify that technology is actually available to that subscriber via coverage.
!! ALWAYS add: EXISTS(SELECT 1 FROM coverage cv WHERE cv.msisdn=s.msisdn AND cv.technology_available='<TECH>')
!! WITHOUT this check you will count subscribers who live in that region but have no <TECH> coverage there.
!! Example — 5G subscribers in Fire Nation Capital:
!!   SELECT COUNT(DISTINCT s.msisdn) FROM subscribers s
!!   WHERE s.region='Fire Nation Capital'
!!   AND EXISTS(SELECT 1 FROM coverage cv WHERE cv.msisdn=s.msisdn AND cv.technology_available='5G')
!! This applies to ALL technologies: 2G, 3G, 4G, 5G.

TABLE kpis_daily
  cell_id             INT   — FK to cells (NOT subscribers — join cells to get technology/region)
  date                TEXT  — YYYY-MM-DD
  rsrp_avg            REAL  — signal strength dBm. See DATA FACTS for real range and p20/p80 thresholds.
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
                   WHERE s.region='Ba Sing Se'
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
  segment           TEXT  — 'prepaid','postpaid','enterprise'
  registration_date TEXT
  is_active         INT
  NOTE: NO region column — get region from subscribers.region in NetworkAnalyzer DB

TABLE plans
  plan_id        INT PK
  plan_name      TEXT
  plan_type      TEXT   — 'voice','data','bundle','MBB','FWA'
  data_cap_gb    REAL
  speed_mbps     REAL
  monthly_price  REAL   — price in Yuan
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
  arpu          REAL  — monthly spend in Yuan (5-130 Yuan typical range)
  value_segment TEXT  — 'bronze'(<15 Yuan),'silver'(15-35),'gold'(35-75),'platinum'(>=75)
  is_hvc        INT   — 1 = High Value Customer (gold or platinum)

!! CRITICAL: customer_value has ONE ROW PER SUBSCRIBER PER MONTH (6 rows per subscriber).
   ALWAYS filter to one month or AVG/SUM will be inflated 6x.
   WRONG:  JOIN customer_value cv ON cv.msisdn=x              <- 6x row explosion, ARPU wrong
   RIGHT:  JOIN customer_value cv ON cv.msisdn=x AND cv.month=(SELECT MAX(month) FROM customer_value)
!! HVC identification: ALWAYS use is_hvc=1. Do NOT use customers.segment or value_segment to find HVC.
   CANONICAL HVC ARPU query:
   SELECT cv.value_segment, COUNT(DISTINCT cv.msisdn) as n, ROUND(AVG(cv.arpu),2) as avg_arpu FROM customer_value cv WHERE cv.is_hvc=1 AND cv.month=(SELECT MAX(month) FROM customer_value) GROUP BY cv.value_segment ORDER BY avg_arpu DESC

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

5G upsell candidates in Tunis — 5G-capable device, not yet on 5G, in 5G coverage:
QUERY_SC: SELECT COUNT(DISTINCT s.msisdn) as n FROM subscribers s JOIN devices d ON s.msisdn=d.msisdn JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE s.region='Ba Sing Se' AND d.is_5g_capable=1 AND st.current_technology != '5G' AND EXISTS(SELECT 1 FROM coverage cv WHERE cv.msisdn=s.msisdn AND cv.technology_available='5G')

5G upsell by region — NO LIMIT, returns ALL regions:
QUERY_SC: SELECT s.region, COUNT(DISTINCT s.msisdn) as n FROM subscribers s JOIN devices d ON s.msisdn=d.msisdn JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE d.is_5g_capable=1 AND st.current_technology != '5G' AND EXISTS(SELECT 1 FROM coverage cv WHERE cv.msisdn=s.msisdn AND cv.technology_available='5G') GROUP BY s.region ORDER BY n DESC

3G sunset risk by region — all 3G subscribers regardless of device capability:
QUERY_SC: SELECT s.region, COUNT(DISTINCT s.msisdn) as n FROM subscribers s JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE st.current_technology='3G' GROUP BY s.region ORDER BY n DESC

FWA candidates — use the is_fwa_candidate flag on mobility_profile (already computed):
QUERY_SC: SELECT COUNT(DISTINCT msisdn) as n FROM mobility_profile WHERE is_fwa_candidate=1 AND month=(SELECT MAX(month) FROM mobility_profile)

FWA candidates by region — NO LIMIT:
QUERY_SC: SELECT s.region, COUNT(DISTINCT mp.msisdn) as n FROM mobility_profile mp JOIN subscribers s ON mp.msisdn=s.msisdn WHERE mp.is_fwa_candidate=1 AND mp.month=(SELECT MAX(month) FROM mobility_profile) GROUP BY s.region ORDER BY n DESC

VoLTE candidates (VoLTE-capable device, on 4G or 5G, VoLTE not yet activated):
QUERY_SC: SELECT COUNT(DISTINCT s.msisdn) as n FROM subscribers s JOIN devices d ON s.msisdn=d.msisdn JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE d.volte_capable=1 AND st.current_technology IN ('4G','5G') AND st.volte_active=0

VoLTE candidates by region — NO LIMIT:
QUERY_SC: SELECT s.region, COUNT(DISTINCT s.msisdn) as n FROM subscribers s JOIN devices d ON s.msisdn=d.msisdn JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE d.volte_capable=1 AND st.current_technology IN ('4G','5G') AND st.volte_active=0 GROUP BY s.region ORDER BY n DESC

HVC not on 5G plan:
QUERY_OP: SELECT COUNT(*) as n FROM customer_value cv JOIN subscriptions s ON cv.msisdn=s.msisdn AND s.is_current=1 JOIN plans p ON s.plan_id=p.plan_id WHERE cv.is_hvc=1 AND p.supports_5g=0 AND cv.month=(SELECT MAX(month) FROM customer_value)

Network KPIs for a region:
QUERY_SC: SELECT ROUND(AVG(k.dl_throughput_mbps),2) as avg_dl, ROUND(AVG(k.availability_pct),2) as availability, ROUND(AVG(k.dropped_call_rate),3) as drop_rate FROM kpis_daily k JOIN cells c ON k.cell_id=c.cell_id JOIN sites s ON c.site_id=s.site_id WHERE s.region='Fire Nation Capital' AND k.date=(SELECT MAX(date) FROM kpis_daily)

Network KPIs by region — NO LIMIT, returns ALL regions:
QUERY_SC: SELECT s.region, ROUND(AVG(k.dl_throughput_mbps),2) as avg_dl, ROUND(AVG(k.availability_pct),2) as availability, ROUND(AVG(k.dropped_call_rate),3) as drop_rate FROM kpis_daily k JOIN cells c ON k.cell_id=c.cell_id JOIN sites s ON c.site_id=s.site_id WHERE k.date=(SELECT MAX(date) FROM kpis_daily) GROUP BY s.region ORDER BY avg_dl DESC

Average download speed by region AND technology — drilldown/treemap (kpis_daily has NO subscriber_msisdn):
QUERY_SC: SELECT s.region, c.technology, ROUND(AVG(k.dl_throughput_mbps),2) as avg_dl FROM kpis_daily k JOIN cells c ON k.cell_id=c.cell_id JOIN sites s ON c.site_id=s.site_id WHERE k.date=(SELECT MAX(date) FROM kpis_daily) GROUP BY s.region, c.technology ORDER BY s.region, c.technology

Network KPIs filtered by technology (technology is on cells table, NOT on kpis_daily):
QUERY_SC: SELECT ROUND(AVG(k.dl_throughput_mbps),2) as avg_dl FROM kpis_daily k JOIN cells c ON k.cell_id=c.cell_id JOIN sites s ON c.site_id=s.site_id WHERE s.region='Fire Nation Capital' AND c.technology='4G' AND k.date=(SELECT MAX(date) FROM kpis_daily)

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
QUERY_SC: SELECT COUNT(DISTINCT q.msisdn) as n FROM qoe_daily q JOIN subscribers s ON q.msisdn=s.msisdn WHERE s.area_code='R' AND q.app_type IN ('gaming','video') AND q.experience_label='poor' AND q.date=(SELECT MAX(date) FROM qoe_daily)

Gaming experience across ALL regions (avg score, latency, throughput — no label filter):
QUERY_SC: SELECT s.region, COUNT(DISTINCT q.msisdn) as users, ROUND(AVG(q.experience_score),2) as avg_score, ROUND(AVG(q.avg_latency_ms),1) as avg_latency_ms, ROUND(AVG(q.avg_throughput_mbps),1) as avg_throughput_mbps FROM qoe_daily q JOIN subscribers s ON q.msisdn=s.msisdn WHERE q.app_type='gaming' AND q.date=(SELECT MAX(date) FROM qoe_daily) GROUP BY s.region ORDER BY avg_score ASC

Poor QoE breakdown by region and app type:
QUERY_SC: SELECT s.region, q.app_type, COUNT(DISTINCT q.msisdn) as n, ROUND(AVG(q.experience_score),2) as avg_score FROM qoe_daily q JOIN subscribers s ON q.msisdn=s.msisdn WHERE q.experience_label='poor' AND q.date=(SELECT MAX(date) FROM qoe_daily) GROUP BY s.region, q.app_type ORDER BY avg_score ASC

CRITICAL — cells table has NO msisdn column. NEVER join cells ON msisdn. cells joins via cell_id only.
CRITICAL — kpis_daily has NO msisdn/subscriber column. NEVER join kpis_daily ON msisdn. KPI region = kpis_daily JOIN cells ON cell_id JOIN sites ON site_id.
NEVER use cells.technology for subscriber breakdowns. Use subscriber_technology.current_technology instead.
CRITICAL — to get a subscriber's region, use subscribers.region directly. Do NOT join through sites.
CRITICAL — sites.zone values are ONLY 'Earth Kingdom','Fire Nation','Water Tribe','Air Nomads'. Region names like 'Tunis','Sfax','Sousse' are in sites.region, NOT sites.zone. NEVER filter WHERE zone='Tunis' — use WHERE s.region='Ba Sing Se' instead.

IMPORTANT: kpis_daily has NO technology column. NEVER filter kpis_daily by technology directly.
To filter KPIs by technology: JOIN cells c ON k.cell_id=c.cell_id WHERE c.technology='4G'
subscriber_technology is for subscriber current tech — NOT for network KPI filtering.

CRITICAL — REGIONAL BREAKDOWNS: When asked for ANY breakdown by region, distribution by region,
or per-region analysis, NEVER add LIMIT to the query. Always return ALL regions.
Queries with GROUP BY s.region must NOT have a LIMIT clause.

CRITICAL — REGION NAME CASE: Region values in the database are Title Case (first letter uppercase).
  CORRECT:   WHERE s.region='Ariana'   WHERE s.region='Ba Sing Se'   WHERE s.region='Fire Nation Capital'
  WRONG:     WHERE s.region='ariana'   WHERE s.region='tunis'   WHERE s.region='sfax'
  Full list: Agna Qel'a, Sanikiluaq, Southern Water Tribe Capital, Fire Nation Capital,
             The Boiling Rock, Island of the Sun Warriors, Ember Island, Shuhon Island, Sunset City,
             Ba Sing Se, Omashu, Gaoling, Serpent's Pass, Si Wong Desert, HeiBai's Forest,
             FumaBu Town, Sandlocke City, Gaipan Village, Northern Air Temple, Western Air Temple,
             Eastern Air Temple, Southern Air Temple, Kyoshi Island, Crescent Island

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

FULL_SCHEMA = NetworkAnalyzer_SCHEMA + OPERATOR_SCHEMA + CROSS_DB_NOTE  # DATA_PROFILE appended after it's built
_load_schema_columns()  # load real column sets from both DBs

COMPACT_SCHEMA = """
=== NetworkAnalyzer DB (query_sc) ===
subscribers: msisdn(PK), region(Title Case!), city, area_code(T/N/C/R/S), is_active — NO tech columns
devices: msisdn(PK), brand, model, max_technology, is_5g_capable, volte_capable, vowifi_capable, os
subscriber_technology: msisdn(PK), current_technology('2G'/'3G'/'4G'/'5G'), current_cell_id, volte_active, last_seen_date
sites: site_id(PK), site_name, region(Title Case!), city, latitude, longitude, site_type, zone('North'/'Center'/'South'), is_active
cells: cell_id(PK), site_id(FK), technology('2G'/'3G'/'4G'/'5G'), frequency_band, is_active, max_users
coverage: msisdn, cell_id, technology_available, signal_strength_dbm, is_home_coverage
kpis_daily: cell_id(FK→cells), date, rsrp_avg, sinr_avg, dl_throughput_mbps, ul_throughput_mbps, dropped_call_rate, availability_pct, latency_ms, congestion_level, active_users_avg — NO msisdn, NO technology
kpis_hourly: cell_id, datetime, rsrp, sinr, dl_throughput_mbps, active_users, congestion_level — currently 0 rows
network_alarms: alarm_id, cell_id, alarm_type, severity, trigger_time, clear_time, is_active
dou_monthly: msisdn, month(YYYY-MM), total_data_gb, distinct_cells_used, primary_cell_id, days_active
ott_monthly: msisdn, month, streaming_gb, gaming_gb, web_gb, voip_gb, social_gb, sms_count
mobility_profile: msisdn, month, distinct_cells_count, avg_daily_cells, primary_cell_id, mobility_class('stationary'/'low'/'medium'/'high'), is_fwa_candidate
qoe_daily: msisdn, date, app_type('gaming'/'video'/'voip'/'web'/'social'), avg_latency_ms, avg_throughput_mbps, experience_score, experience_label('poor'/'fair'/'good'/'excellent')

=== OPERATOR DB (query_op) ===
customers: msisdn(PK), full_name, segment('prepaid'/'postpaid'/'enterprise'), is_active — NO region column, get region from subscribers.region
plans: plan_id(PK), plan_name, plan_type, data_cap_gb, speed_mbps, monthly_price(Yuan), supports_5g, is_fwa_plan
subscriptions: msisdn, plan_id, start_date, end_date, is_current(1=active), contract_type — ALWAYS filter WHERE sub.is_current=1, multiple rows per subscriber (history). Bridge table between subscribers and plans: JOIN subscriptions sub ON s.msisdn=sub.msisdn AND sub.is_current=1 JOIN plans p ON sub.plan_id=p.plan_id
offers: offer_id, offer_name, target_campaign, target_technology, discount_pct, bonus_data_gb, is_active
offer_assignments: msisdn, offer_id, assigned_date, channel, accepted
campaigns: campaign_id, campaign_name, campaign_type, target_count, status, offer_id
campaign_targets: campaign_id, msisdn, reason, score, notified, converted
billing: msisdn, billing_month, total_amount, data_charges, voice_charges, payment_status — NO region column, JOIN subscribers for region
customer_value: msisdn, month(YYYY-MM), arpu(Yuan), value_segment('bronze'/'silver'/'gold'/'platinum'), is_hvc — 6 ROWS PER SUBSCRIBER, always filter: cv.month=(SELECT MAX(month) FROM op.customer_value)

=== CRITICAL RULES ===
- region values are Title Case: 'Ariana' not 'ariana', 'Tunis' not 'tunis'
- kpis_daily has NO msisdn and NO technology — filter tech via JOIN cells, filter region via JOIN cells→sites
- COUNT subscribers with COUNT(DISTINCT msisdn) — customer_value/billing/dou_monthly have multiple rows per subscriber, COUNT(*) overcounts
- To identify subscribers ON 5G: use subscriber_technology.current_technology='5G' — NOT plans.supports_5g. supports_5g means the plan allows 5G but the subscriber may not be using it yet
- cells has NO msisdn — never join cells ON msisdn
- customer_value and billing are in operator DB — use ATTACH or query_op()
- billing has one row per subscriber per month — ALWAYS filter: b.billing_month=(SELECT MAX(billing_month) FROM op.billing)
- sites.zone is only 'North'/'Center'/'South' — never WHERE zone='Tunis'
- For cross-DB queries: ATTACH DATABASE 'operator_new.db' AS op; then prefix op.table_name
- If your SQL references op.anything, you MUST use QUERY_BOTH — never QUERY_SC with op. prefix
- op.customers has NO region column — always get region from subscribers.region (NetworkAnalyzer DB)
- op.billing has NO region column — always JOIN subscribers s ON s.msisdn=b.msisdn to get region
- NEVER join op.plans directly to subscribers — plans has no msisdn. Always go through subscriptions: JOIN op.subscriptions sub ON s.msisdn=sub.msisdn AND sub.is_current=1 JOIN op.plans p ON sub.plan_id=p.plan_id
- To check 5G coverage for a subscriber: JOIN coverage cov ON s.msisdn=cov.msisdn AND cov.technology_available='5G'
- To link a subscriber to their cell KPIs: JOIN subscriber_technology st ON s.msisdn=st.msisdn JOIN kpis_daily k ON st.current_cell_id=k.cell_id AND k.date=(SELECT MAX(date) FROM kpis_daily)
- cells has NO rsrp_avg column — rsrp_avg is ONLY on kpis_daily. Always JOIN kpis_daily to get signal quality.
- For RSRP thresholds use the DATA FACTS section — it contains the real p20/p80 from the live DB.
- NEVER silently drop conditions from the user's question. If the user asks for ARPU filter, campaign exclusion, date range, or any other condition — include ALL of them in the SQL. Never simplify.
- subscriber_technology has NO usage columns — it has only: msisdn, current_technology, current_cell_id, volte_enabled, vowifi_enabled. Never invent columns like monthly_data_usage, last_seen_date, current_plan_id.
- For monthly data usage per subscriber use SC.dou_monthly (columns: msisdn, month YYYY-MM, total_data_gb, distinct_cells_used, primary_cell_id, days_active). To compare usage across months, self-join dou_monthly on msisdn with different month filters.
"""

# ═══════════════════════════════════════════════════════════════════════
# DATA PROFILE — queried from real DBs at startup, injected into prompts
# ═══════════════════════════════════════════════════════════════════════

def _build_data_profile() -> str:
    """Query every table in both DBs at startup. For each column:
    - categorical: list all distinct values
    - numeric: min, max, p20, p80
    - date: min, max, latest
    Also adds multi-row-per-subscriber warnings for time-series tables.
    Result is injected into every prompt so the model never has to guess."""

    MAX_CATS = 30   # max distinct values to list for categorical columns
    lines = ["=== DATA FACTS (auto-queried from live DB at startup — trust these over assumptions) ==="]

    # Tables with millions of rows — sample instead of full scan
    LARGE_TABLES = frozenset(("kpis_daily", "kpis_hourly", "coverage", "dou_monthly",
                               "ott_monthly", "mobility_profile", "qoe_daily", "billing",
                               "customer_value", "subscriptions", "campaign_targets", "sms_log"))

    def _profile_table(conn, table: str, label: str):
        """Profile every column of one table."""
        bare = table.split(".")[-1]
        try:
            cols = conn.execute(f"PRAGMA table_info({bare})").fetchall()
        except Exception:
            return
        if not cols:
            return
        # For large tables use a sampled subquery to keep startup fast
        is_large = bare in LARGE_TABLES
        sample = f"(SELECT * FROM {table} LIMIT 50000)" if is_large else table

        all_col_names = [col[1] for col in cols]
        tlines = [f"\n{label}.{bare}: columns=({', '.join(all_col_names)})"]
        for col in cols:
            cname, ctype = col[1], (col[2] or "").upper()
            is_num = any(t in ctype for t in ("INT","REAL","FLOAT","NUMERIC","DOUBLE"))
            is_date = any(k in cname.lower() for k in ("date","month","period","timestamp"))
            is_id   = any(k in cname.lower() for k in ("_id", "msisdn", "imei", "alarm_id",
                                                         "kpi_id", "coverage_id", "campaign_id",
                                                         "offer_id", "plan_id", "target_id"))
            try:
                if is_id:
                    continue  # IDs: listed in columns= above, no need to profile values
                # Integers with few distinct values are categorical (flags, booleans, scores)
                if is_num and not is_date:
                    n_dist_check = conn.execute(
                        f"SELECT COUNT(DISTINCT {cname}) FROM {sample} WHERE {cname} IS NOT NULL"
                    ).fetchone()[0]
                    if n_dist_check <= 10:
                        # Treat as categorical
                        vals = conn.execute(
                            f"SELECT DISTINCT {cname} FROM {sample} WHERE {cname} IS NOT NULL ORDER BY {cname}"
                        ).fetchall()
                        tlines.append(f"  {cname}: [{', '.join(str(r[0]) for r in vals)}]")
                        continue
                if is_num and not is_date:
                    row = conn.execute(
                        f"SELECT MIN({cname}), MAX({cname}), COUNT(*) FROM {sample} WHERE {cname} IS NOT NULL"
                    ).fetchone()
                    if row and row[2] and row[2] > 0:
                        mn, mx, n = row
                        p20 = conn.execute(
                            f"SELECT {cname} FROM {sample} WHERE {cname} IS NOT NULL "
                            f"ORDER BY {cname} ASC LIMIT 1 OFFSET {max(0, n//5)}"
                        ).fetchone()
                        p80 = conn.execute(
                            f"SELECT {cname} FROM {sample} WHERE {cname} IS NOT NULL "
                            f"ORDER BY {cname} DESC LIMIT 1 OFFSET {max(0, n//5)}"
                        ).fetchone()
                        tlines.append(
                            f"  {cname}: numeric, range [{round(mn,2)}, {round(mx,2)}], "
                            f"p20={round(p20[0],2) if p20 else '?'}, p80={round(p80[0],2) if p80 else '?'}"
                        )
                    continue
                # Categorical or date — use sample for large tables
                n_distinct = conn.execute(
                    f"SELECT COUNT(DISTINCT {cname}) FROM {sample}"
                ).fetchone()[0]
                if n_distinct == 0:
                    continue
                if n_distinct <= MAX_CATS:
                    vals = conn.execute(
                        f"SELECT DISTINCT {cname} FROM {sample} WHERE {cname} IS NOT NULL ORDER BY {cname} LIMIT {MAX_CATS}"
                    ).fetchall()
                    val_str = ", ".join(f"'{r[0]}'" for r in vals if r[0] is not None)
                    tlines.append(f"  {cname}: [{val_str}]")
                else:
                    row = conn.execute(
                        f"SELECT MIN({cname}), MAX({cname}) FROM {sample} WHERE {cname} IS NOT NULL"
                    ).fetchone()
                    if row:
                        tlines.append(f"  {cname}: {n_distinct} distinct values, range [{row[0]}, {row[1]}]")
            except Exception:
                continue
        if len(tlines) > 1:
            lines.extend(tlines)

    try:
        # ── NetworkAnalyzer DB ────────────────────────────────────────────────
        sc = sqlite3.connect(SC_DB)
        sc.execute(f"ATTACH DATABASE '{OP_DB}' AS op")

        sc_tables = [r[0] for r in sc.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()]
        for t in sc_tables:
            _profile_table(sc, t, "SC")

        # ── Operator DB ─────────────────────────────────────────────────
        op_tables = [r[0] for r in sc.execute(
            "SELECT name FROM op.sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()]
        for t in op_tables:
            _profile_table(sc, f"op.{t}", "OP")

        # ── Critical multi-row warnings (derived, not per-column) ────────
        lines.append("\n=== MULTI-ROW WARNINGS (inflation risk) ===")

        brow = sc.execute("SELECT COUNT(DISTINCT billing_month), MAX(billing_month) FROM op.billing").fetchone()
        lines.append(
            f"billing: {brow[0]} rows per subscriber (one per month). Latest='{brow[1]}'. "
            f"ALWAYS filter: b.billing_month=(SELECT MAX(billing_month) FROM op.billing)"
        )
        crow = sc.execute("SELECT COUNT(DISTINCT month), MAX(month) FROM op.customer_value").fetchone()
        lines.append(
            f"customer_value: {crow[0]} rows per subscriber (one per month). Latest='{crow[1]}'. "
            f"ALWAYS filter: cv.month=(SELECT MAX(month) FROM op.customer_value)"
        )
        lines.append(
            "subscriptions: multiple rows per subscriber (one per plan change). "
            "ALWAYS filter: sub.is_current=1"
        )

        # ── RSRP on subscriber-linked cells ──────────────────────────────
        lines.append("\n=== SIGNAL QUALITY ON SUBSCRIBER CELLS ===")
        r = sc.execute("""
            SELECT ROUND(MIN(k.rsrp_avg),1), ROUND(MAX(k.rsrp_avg),1), COUNT(DISTINCT st.msisdn)
            FROM subscriber_technology st
            JOIN kpis_daily k ON st.current_cell_id=k.cell_id
            WHERE k.date=(SELECT MAX(date) FROM kpis_daily)
        """).fetchone()
        if r and r[0]:
            n = r[2]
            p20 = sc.execute(f"""
                SELECT ROUND(k.rsrp_avg,1) FROM subscriber_technology st
                JOIN kpis_daily k ON st.current_cell_id=k.cell_id
                WHERE k.date=(SELECT MAX(date) FROM kpis_daily)
                ORDER BY k.rsrp_avg ASC LIMIT 1 OFFSET {max(0, n//5)}
            """).fetchone()
            lines.append(
                f"rsrp_avg on subscriber cells (not all cells): range [{r[0]}, {r[1]}] dBm. "
                f"Poor signal threshold (bottom 20%): < {p20[0] if p20 else r[0]} dBm. "
                f"NEVER use thresholds below {r[0]} — returns 0 rows."
            )

        sc.close()
    except Exception as e:
        lines.append(f"(data profile error: {e})")

    lines.append("\n=== END DATA FACTS ===")
    result = "\n".join(lines)
    # Print summary to console (not full profile — too long)
    print(f"[DataProfile] Built: {len(sc_tables)} SC tables, {len(op_tables)} OP tables, {len(lines)} fact lines")
    return result

DATA_PROFILE = _build_data_profile()

# Append real data facts to both schema strings so every prompt has them
FULL_SCHEMA    = FULL_SCHEMA    + "\n\n" + DATA_PROFILE
COMPACT_SCHEMA = COMPACT_SCHEMA + "\n\n" + DATA_PROFILE

# ═══════════════════════════════════════════════════════════════════════
# OLLAMA
# ═══════════════════════════════════════════════════════════════════════

def _sanitize_response(text: str) -> str:
    """Remove non-Latin script bleed (Hebrew, Arabic, CJK, etc.) from model output."""
    # Truncate at the first run of non-Latin/non-ASCII script characters (outside SQL strings)
    # Allow: ASCII, Latin extended, common punctuation, digits
    cleaned = re.sub(r'[\u0590-\u05FF\u0600-\u06FF\u4E00-\u9FFF\u3040-\u30FF]+.*', '', text, flags=re.DOTALL)
    return cleaned.strip()


def _llm(system: str, prompt: str, max_tokens: int = 1200, timeout: int = 300, allow_thinking: bool = True) -> str:
    return _ollama(system, prompt, max_tokens, timeout, allow_thinking)


def _ollama(system: str, prompt: str, max_tokens: int = 1200, timeout: int = 300, allow_thinking: bool = True) -> str:
    """Call Ollama via /api/chat with native thinking support for qwen3."""
    use_stream = _streaming_queue is not None
    think = bool(THINKING_ENABLED) and allow_thinking
    try:
        r = requests.post(
            f"{OLLAMA_BASE}/api/chat",
            json={
                "model": MODEL,
                "think": think,
                "thinking": {"budget_tokens": THINK_BUDGET} if think else None,
                "stream": use_stream,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": prompt},
                ],
                "options": {
                    "num_predict": max_tokens,
                    "num_gpu":     NUM_YuanU_LAYERS,
                    "num_thread":  NUM_THREADS,
                    "num_ctx":     CTX_SIZE,
                    "low_vram":    True,
                    "f16_kv":      False,
                }
            },
            stream=use_stream,
            timeout=timeout
        )
        if use_stream:
            answer_parts = []
            for raw_line in r.iter_lines():
                if _stop_event and _stop_event.is_set():
                    r.close()
                    break
                if not raw_line:
                    continue
                chunk = json.loads(raw_line)
                msg = chunk.get("message", {})
                think_tok = msg.get("thinking", "")
                if think_tok and _streaming_queue:
                    _streaming_queue.put({"type": "think_token", "token": think_tok})
                answer_tok = msg.get("content", "")
                if answer_tok:
                    answer_parts.append(answer_tok)
                    if _streaming_queue:
                        _streaming_queue.put({"type": "token", "token": answer_tok})
                if chunk.get("done"):
                    break
            return _sanitize_response("".join(answer_parts).strip())

        # Non-streaming path
        data = r.json()
        think_part  = data.get("message", {}).get("thinking", "")
        answer_part = data.get("message", {}).get("content",  "").strip()
        if think_part:
            _think_log.append(think_part.strip())
        return _sanitize_response(answer_part)
    except Exception as e:
        return f"ERROR: {e}"


def _check_sql(sql: str, db_path: str) -> str | None:
    """Compile-check SQL before executing. Returns None if valid, or an error
    string with the actual column names for every table referenced."""
    sql = sql.split(";")[0].strip()
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
        has_date_filter = (
            "K.DATE" in sql_up or
            "KPIS_DAILY.DATE" in sql_up or
            re.search(r'\bDATE\b.*KPIS_DAILY|\bKPIS_DAILY\b.*\bDATE\b', sql_up)
        )
        if not has_date_filter:
            issues.append(
                "SEMANTIC ERROR: kpis_daily queried without a date filter — this averages ALL historical data, "
                "not current values. Add: AND k.date=(SELECT MAX(date) FROM kpis_daily) "
                "or for multi-day ranges: AND k.date >= date((SELECT MAX(date) FROM kpis_daily), '-14 days')"
            )
        elif "DATE('NOW')" in sql_up or 'DATE("NOW")' in sql_up:
            issues.append(
                "SEMANTIC ERROR: DATE('now') may not exist in kpis_daily if the simulator hasn't run today. "
                "Use: k.date=(SELECT MAX(date) FROM kpis_daily) or "
                "k.date >= date((SELECT MAX(date) FROM kpis_daily), '-14 days') for ranges."
            )

    # billing same issues
    if "BILLING" in sql_up and "ATTACH" not in sql_up and " OP." not in sql_up:
        issues.append(
            "SEMANTIC ERROR: billing table is in operator_new.db, not NetworkAnalyzer. "
            "Use ATTACH DATABASE 'operator_new.db' AS op; and prefix as op.billing."
        )

    return "\n".join(issues) if issues else None


def _schema_hint_for_error(error: str, sql: str, db_path: str) -> str:
    """On a query error, extract the bad column/table, show real columns, and suggest alternatives."""
    hints = []
    try:
        conn = sqlite3.connect(db_path)
        if "ATTACH" in sql.upper() or " op." in sql:
            conn.execute(f"ATTACH DATABASE '{OP_DB}' AS op")

        tables = re.findall(r'(?:FROM|JOIN)\s+(?:op\.)?(\w+)', sql, re.I)
        tables = list(dict.fromkeys(t.lower() for t in tables))

        for tbl in tables[:4]:
            try:
                rows = conn.execute(f"PRAGMA table_info({tbl})").fetchall()
                if not rows:
                    rows = conn.execute(f"PRAGMA table_info(op.{tbl})").fetchall()
                if rows:
                    cols = [r[1] for r in rows]
                    hints.append(f"{tbl} columns: {', '.join(cols)}")
            except Exception:
                pass
        conn.close()

        # Cross-reference: find which table actually has the missing column
        missing = re.search(r'no such column[:\s]+(\w+)', error, re.I)
        if missing and _TABLE_COLUMNS:
            col = missing.group(1).lower()
            has_col = [t for t, cols in _TABLE_COLUMNS.items() if col in cols]
            if has_col:
                hints.append(f"'{col}' exists on: {', '.join(has_col)} — use one of those tables instead")
    except Exception:
        pass
    return " | ".join(hints) if hints else ""




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
    r'arpu|revenue|price|cost':             'Yuan',
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

def _chart_from_context(context: str, question: str) -> dict | None:
    """Build a bar chart directly from the last query result in context — no LLM needed."""
    import json as _json
    # Find the last JSON block in context
    blocks = re.findall(r'\[(\{.*?\}(?:,\s*\{.*?\})*)\]', context, re.DOTALL)
    if not blocks:
        return None
    try:
        rows = _json.loads(f"[{blocks[-1]}]")
    except Exception:
        return None
    if not rows or not isinstance(rows[0], dict):
        return None

    keys = list(rows[0].keys())
    # Find label column (first string column) and value column (largest numeric)
    label_col = next((k for k in keys if isinstance(rows[0][k], str)), None)
    # Prefer revenue/total column, then count, then any numeric
    numeric_cols = [k for k in keys if isinstance(rows[0].get(k), (int, float))]
    if not label_col or not numeric_cols:
        return None

    q_lower = question.lower()
    if any(w in q_lower for w in ("revenue", "arpu", "total")):
        val_col = next((k for k in numeric_cols if any(w in k for w in ("revenue", "total", "arpu"))), numeric_cols[-1])
        y_label = "Revenue at Risk (Yuan)" if "revenue" in val_col else "ARPU (Yuan)"
    else:
        val_col = next((k for k in numeric_cols if any(w in k for w in ("count", "n", "total", "at_risk"))), numeric_cols[0])
        y_label = "Count"

    x = [str(r[label_col]) for r in rows]
    y = [r[val_col] for r in rows]
    title = f"{val_col.replace('_', ' ').title()} by {label_col.replace('_', ' ').title()}"
    return {"type": "bar", "title": title, "x": x, "y": y, "x_label": label_col.replace('_', ' ').title(), "y_label": y_label}


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
- TIME-SERIES / over time / trend / past N hours/days → MUST use line: {{"type":"line","title":"...","x":[...],"y":[...],"x_label":"Date","y_label":"..."}}
- by region/category → bar: {{"type":"bar","title":"...","x":[...],"y":[...],"x_label":"...","y_label":"..."}}
- proportions → pie: {{"type":"pie","title":"...","x":[...],"y":[...]}}
- drilldown/tree → treemap: {{"type":"treemap","title":"...","labels":[...],"parents":[...],"values":[...]}}
  treemap root node always has parent ""

Question: {question}
Answer: {text}
JSON:"""

    try:
        raw = _llm("Output only valid JSON. No explanation.", prompt, max_tokens=400, timeout=60)
    except Exception:
        return None

    try:
        raw = raw.strip().strip("```json").strip("```").strip()
        raw = re.sub(r'(\d),(\d{3})', r'\1\2', raw)
        parsed = json.loads(raw)
        if parsed.get("type") == "none":
            return None
        # Auto-correct bar → line when x values are dates/times
        if parsed.get("type") == "bar":
            xs = parsed.get("x", [])
            if xs and isinstance(xs[0], str) and re.search(r'\d{4}-\d{2}|\d{2}:\d{2}|Mar |Apr |Jan |Feb ', xs[0]):
                parsed["type"] = "line"
        return parsed
    except Exception:
        return None

# ═══════════════════════════════════════════════════════════════════════
# REASONING CHAIN
# ═══════════════════════════════════════════════════════════════════════

AGENT_SYSTEM = """{rag_context}

{schema}

You are an intelligent telecom analyst for a Avatar world operator.
You reason step by step using SQL queries against two databases.
Keep your thinking brief — think for at most 3-4 sentences before acting.

SCHEMA FACTS:
- kpis_daily has no technology column; to filter by technology (5G/4G/3G/2G) always JOIN cells c ON k.cell_id=c.cell_id WHERE c.technology='5G'
- kpis_hourly has no data currently
- subscriber_technology tracks what technology each subscriber is currently on

QUERY PATTERNS — use these as reference:
- KPI trend for a technology in a region: SELECT k.date, ROUND(AVG(k.dl_throughput_mbps),2) as avg_dl FROM kpis_daily k JOIN cells c ON k.cell_id=c.cell_id JOIN sites s ON c.site_id=s.site_id WHERE c.technology='5G' AND s.region='Ba Sing Se' AND k.date>=date((SELECT MAX(date) FROM kpis_daily),'-7 days') GROUP BY k.date ORDER BY k.date
- Subscriber count by technology: SELECT st.current_technology, COUNT(*) as n FROM subscriber_technology st JOIN subscribers s ON st.msisdn=s.msisdn WHERE s.is_active=1 GROUP BY st.current_technology
- 5G capable but not using 5G: SELECT s.region, COUNT(*) as n FROM subscribers s JOIN devices d ON s.msisdn=d.msisdn JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE d.is_5g_capable=1 AND st.current_technology!='5G' GROUP BY s.region ORDER BY n DESC

At each step output EXACTLY ONE of:

SCHEMA: <table_name>
  Inspect the actual columns of any table before writing a query.
  Use this when unsure what columns a table has.
  Example: SCHEMA: subscribers

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
  Describe EXACTLY what you will do with exact counts and Yuan impact.
  Validate counts against VERIFIED GROUND-TRUTH COUNTS before proposing.
  Wait for confirmation before executing.
  NEVER PROPOSE for analysis-only questions — use CONCLUDE instead.

Rules:
- Never guess numbers — always query first.
- All monetary values (ARPU, prices, revenue) are in Yuan (Yuan) — never use $ or USD.
- Write SQL on a single line with no line breaks.
- Do not use markdown backticks around SQL.
- NEVER output QUERY_SC or QUERY_OP tags inside a CONCLUDE or PROPOSE block.
- NEVER use PROPOSE for analysis questions — use CONCLUDE for those.
- Use correct campaign types: 5G_upsell, 3G_migration, FWA, VoLTE_sunset, HVC_upsell.
- In JSON output, never use commas in numbers. Write 13532 not 13,532.

OUTPUT FORMAT — When writing CONCLUDE, output valid JSON on a single line. Field order MUST be: text, recommendations, strategy_diagram, chart — in that exact order:
{"text": "your analysis here", "recommendations": ["action 1", "action 2", "action 3"], "strategy_diagram": {"title": "Migration Strategy", "segments": [{"label": "3G Non-VoLTE", "count": 8658, "color": "red", "strategy": "4G Terminal Upgrade"}, {"label": "3G VoLTE-capable", "count": 3765, "color": "amber", "strategy": "VoLTE Migration"}], "strategies": [{"label": "4G Terminal Upgrade", "tier": "4G", "color": "amber", "actions": ["Subsidized device swap", "Bundle package offer"]}, {"label": "VoLTE Migration", "tier": "4G", "color": "purple", "actions": ["SMS activation campaign", "3-month data bonus"]}]}, "chart": {"type": "bar", "title": "Chart Title", "x": ["A","B","C"], "y": [1,2,3], "x_label": "Category", "y_label": "Value"}}

The "strategy_diagram" field: include ONLY when the question is about subscriber migration, technology upgrade strategy, segment analysis, or commercial action planning (3G sunset, VoLTE migration, 5G upsell, FWA, churn strategy). Do NOT include for plain KPI or network queries.
- "segments": the subscriber populations from your data — use real counts from your query results. "color": one of "red", "amber", "blue", "purple", "green", "teal". "strategy": must exactly match a label in "strategies".
- "strategies": the actions for each segment. "tier": the target technology tier ("2G","3G","4G","5G","FWA"). "color": same palette. "actions": 2-3 specific actions the operator should take.

The "text" field must be SHORT and conversational — 3 to 5 sentences maximum. Lead with the key finding and numbers. No bullet points, no headers, no markdown. Write like a smart analyst talking to a manager. Example: "Sidi Bouzid has the most HVCs at risk (238 customers, avg 94 Yuan ARPU). Ariana and Bizerte follow with 37 and 46 at-risk customers respectively. Total exposure across all three regions is roughly 30k Yuan/month."
The "recommendations" field: ask yourself — does this data reveal an opportunity, a risk, or an actionable gap? If yes, include 2-3 recommendations. If the question is purely factual with no commercial angle (e.g. "what is the average latency?"), omit the field entirely or set it to [].
When recommendations ARE warranted, write like a senior analyst advising a commercial director. Each item should reason from the actual numbers: what to do, why the data justifies it, which segment or region to prioritize first, and what the expected business impact is.

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
NEVER include a chart when all y/values are equal (e.g. all 1s) — a uniform chart is meaningless.
If your chart values are all equal, query an actual metric instead (e.g. subscriber count per region, site count, active users).
When asked what regions/cities/nations you cover: query subscriber count per region and show a bar chart ranked by subscribers — never assign equal placeholder values.
"""



def _run_chain(question: str, max_steps: int = 5, _resume_context: str = None, _resume_steps: list = None) -> dict:
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

    sql_examples = retrieve_sql(question, top_k=3)
    sql_hint = f"\nRELEVANT SQL EXAMPLES (use as reference):\n{sql_examples}\n" if sql_examples else ""

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
            "  Subscriber count by region+technology:  SELECT s.region, st.current_technology, COUNT(DISTINCT s.msisdn) as n FROM subscribers s JOIN subscriber_technology st ON s.msisdn=st.msisdn GROUP BY s.region, st.current_technology\n"
            "  3-level region+technology+value_segment: ATTACH DATABASE 'operator_new.db' AS op; SELECT s.region, st.current_technology, cv.value_segment, COUNT(DISTINCT s.msisdn) as n FROM subscribers s JOIN subscriber_technology st ON s.msisdn=st.msisdn JOIN op.customer_value cv ON s.msisdn=cv.msisdn WHERE cv.month=(SELECT MAX(month) FROM op.customer_value) GROUP BY s.region, st.current_technology, cv.value_segment ORDER BY s.region, st.current_technology, cv.value_segment\n"
            "  KPI speed by region+technology:         SELECT si.region, c.technology, ROUND(AVG(k.dl_throughput_mbps),2) as avg_dl FROM kpis_daily k JOIN cells c ON k.cell_id=c.cell_id JOIN sites si ON c.site_id=si.site_id WHERE k.date=(SELECT MAX(date) FROM kpis_daily) GROUP BY si.region, c.technology ORDER BY si.region, c.technology\n"
            "  Tech vs device capability:              SELECT st.current_technology, d.max_technology, COUNT(*) as n FROM subscriber_technology st JOIN devices d ON st.msisdn=d.msisdn GROUP BY st.current_technology, d.max_technology\n"
            "  Device drilldown (os/brand/tech/volte):  SELECT d.os, d.brand, d.max_technology, d.volte_capable, COUNT(*) as n FROM devices d JOIN subscriber_technology st ON d.msisdn=st.msisdn GROUP BY d.os, d.brand, d.max_technology, d.volte_capable ORDER BY n DESC\n"
            "  Utilization gap (current vs max):        SELECT d.max_technology, st.current_technology, COUNT(*) as n FROM devices d JOIN subscriber_technology st ON d.msisdn=st.msisdn GROUP BY d.max_technology, st.current_technology ORDER BY d.max_technology, n DESC\n"
            "After running ONE query, output: CONCLUDE: {\"text\": \"your analysis here\"}\n"
            "The chart is built automatically — do NOT include chart JSON in your CONCLUDE."
        )
    context = _resume_context or ""
    steps_log = _resume_steps or []
    _seen_sqls: set = set()
    _last_sql: str = None

    treemap_remind = (
        "\nYou have run your query. Now output: CONCLUDE: {\"text\": \"your analysis here\"}"
    ) if is_treemap else ""

    for step in range(max_steps):
        if _stop_event and _stop_event.is_set():
            break
        if step > 0:
            system = AGENT_SYSTEM\
                .replace("{rag_context}", rag_block)\
                .replace("{schema}", LIVE_SCHEMA + "\n\n" + COMPACT_SCHEMA)
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
                        f"You have sufficient data.\n"
                        f"Output CONCLUDE: followed by a single-line JSON object.\n"
                        f"CRITICAL: NO markdown. NO headers. NO bullet points. NO backticks.\n"
                        f"ONLY: CONCLUDE: {{\"text\": \"2-4 sentence summary\", \"chart\": {{...}}}}\n"
                        f"{rag_block}"
                    )
            else:
                # Count successful result rows — only non-zero results
                all_row_counts = [int(m.group(1)) for m in re.finditer(r'Results \((\d+) rows\)', context)]
                successful_rows = sum(n for n in all_row_counts if n > 0)
                last_nonzero = next((n for n in reversed(all_row_counts) if n > 0), 0)
                has_data = successful_rows > 0
                conclude_push = (
                    f"\nYour latest query returned {last_nonzero} rows of valid data. "
                    f"Base your conclusion ONLY on that data — ignore earlier 0-row results and schema issues. "
                    f"Do NOT write recommendations, next steps, or schema fixes. "
                    f"Output a 2-3 sentence factual summary of what the data shows. "
                    f"Output CONCLUDE now as a single-line JSON: "
                    f'CONCLUDE: {{"text": "your 2-3 sentence factual summary", "chart": {{...}}}}'
                ) if has_data else ""
                prompt = (
                    f"Question: {question}\n\n"
                    f"Data gathered so far:\n{context}\n\n"
                    f"Next step — output QUERY_SC, QUERY_OP, "
                    f"QUERY_BOTH, CONCLUDE, or PROPOSE:"
                    f"{treemap_remind}{conclude_push}"
                )
        else:
            # Detect operator-only questions — steer away from QUERY_BOTH
            _op_keywords = ["arpu","billing","plan","subscription","customer value","hvc","high value",
                            "offer","campaign","revenue","segment","churn","payment"]
            _sc_keywords = ["kpi","throughput","alarm","cell","site","coverage","signal","technology",
                            "device","mobility","fwa","volte","network","drop rate"]
            _q = question.lower()
            _is_op_only = any(k in _q for k in _op_keywords) and not any(k in _q for k in _sc_keywords)
            if _is_op_only:
                _db_hint = (
                    "This question is about commercial/operator data. "
                    "Use QUERY_OP (NOT QUERY_BOTH) — operator tables are accessed directly without ATTACH.\n"
                )
            else:
                _db_hint = ""
            # Count conditions in the question to detect complex multi-condition queries
            _condition_words = ["and", "have", "has", "are", "with", "who", "must", "not"]
            _condition_count = sum(question.lower().count(w) for w in ["and ", " who ", " with ", " have "])
            _is_complex = _condition_count >= 2
            _complex_hint = (
                "This question has MULTIPLE conditions. Write ONE complete SQL query that enforces ALL conditions simultaneously using JOINs and WHERE clauses. "
                "Do NOT explore data first or run partial queries. "
                "Do NOT query averages or ranges first — write the full query directly.\n"
            ) if _is_complex else ""
            _prior_ctx = _memory_context()
            _ctx_hint  = f"Conversation history:\n{_prior_ctx}\n" if _prior_ctx else ""
            prompt = (
                f"{_ctx_hint}"
                f"Question: {question}\n\n"
                f"{_db_hint}"
                f"{_complex_hint}"
                f"{sql_hint}"
                f"Decide your next step:\n"
                f"- If the conversation history above already contains the data needed to answer, output CONCLUDE directly.\n"
                f"- If you need fresh data, output QUERY_SC: <sql> or QUERY_OP: <sql>.\n"
                f"Do not query the database if the answer is already in context."
            )

        _is_conclude_step = context and context.count("Step ") >= 1

        _max_tok = 600 if (is_treemap and _is_conclude_step) else (1800 if _is_conclude_step else 1200)
        response = _llm(system, prompt, max_tokens=_max_tok, timeout=420)

        # Truncate at second action tag — model must output ONE action per step
        _tags = ["SCHEMA:", "QUERY_SC:", "QUERY_OP:", "QUERY_BOTH:", "CONCLUDE:", "PROPOSE:"]
        _first_pos = next((response.index(t) for t in _tags if t in response), -1)
        if _first_pos >= 0:
            _search_from = _first_pos + 1
            _second_pos = len(response)
            for _t in _tags:
                _idx = response.find(_t, _search_from)
                if _idx != -1 and _idx < _second_pos:
                    _second_pos = _idx
            response = response[:_second_pos].strip()

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

            # Reject CONCLUDE when all queries errored — must retry with corrected table
            successful_results = len(re.findall(r'Results \(\d+ rows\)', context))
            error_count = context.count("Query error:") + context.count("semantic error:") + context.count("SQL error:")
            if successful_results == 0 and error_count > 0:
                corr_match = re.search(r'SCHEMA CORRECTION: (.+)', context)
                corr_hint = corr_match.group(1) if corr_match else ""
                err_match = re.search(r"'(\w+)' exists on: ([\w, ]+)", context)
                alt_hint = f"Use table '{err_match.group(2).strip()}' instead." if err_match else ""
                context += (
                    f"\n[SYSTEM] CONCLUDE BLOCKED — you have 0 successful results and {error_count} error(s). "
                    f"You MUST NOT output CONCLUDE yet. "
                    f"{corr_hint or alt_hint} "
                    f"Output ONLY: QUERY_SC: <corrected SQL using the right table>\n"
                )
                continue

            conclusion = response.split("CONCLUDE:", 1)[1].strip()
            for tag in ["QUERY_SC:", "QUERY_OP:", "QUERY_BOTH:", "Step "]:
                if tag in conclusion:
                    conclusion = conclusion.split(tag)[0].strip()
            conclusion = conclusion.rstrip(".,: \n")

            # ── Anti-hallucination: reject metrics not present in any query result ──
            # Parse column names from all step results in context
            _all_columns: set = set()
            for _m in re.finditer(r'Results \(\d+ rows\): \[(\{.*?\})', context, re.DOTALL):
                try:
                    _row = json.loads(_m.group(1) + "}")
                    _all_columns.update(_row.keys())
                except Exception:
                    pass
            # Metrics that are commonly hallucinated when not queried
            _hallucination_suspects = {
                "churn rate": ["churn"],
                "churn indicator": ["churn"],
                "churn probability": ["churn_prob"],
                "nps": ["nps"],
                "satisfaction": ["satisfaction", "nps"],
                "complaint": ["complaint"],
            }
            _conclusion_lower = conclusion.lower()
            _hallucinated = []
            for _label, _col_hints in _hallucination_suspects.items():
                if _label in _conclusion_lower:
                    if not any(h in _all_columns for h in _col_hints):
                        _hallucinated.append(_label)
            if _hallucinated:
                context += (
                    f"\nStep {step+1}: CONCLUDE rejected — you cited {_hallucinated} but never queried "
                    f"that data. Only cite metrics from your actual query results. "
                    f"Columns you queried: {sorted(_all_columns)}. Rewrite CONCLUDE using only those.\n"
                )
                continue

            # Reject if no valid {"text": "..."} JSON — force model to rewrite
            has_text_field = bool(re.search(r'"text"\s*:', conclusion))
            if not has_text_field:
                context += (
                    f"\nStep {step+1}: CONCLUDE rejected — JSON must have a \"text\" field. "
                    f"Output ONLY this line:\n"
                    f'CONCLUDE: {{"text": "3-sentence analyst summary with key numbers", '
                    f'"chart": {{"type": "bar", "title": "...", "x": [...], "y": [...], "x_label": "...", "y_label": "..."}}}}\n'
                )
                continue

            chart_spec        = None
            text_only         = conclusion
            _recs             = []
            _extract_sql      = None
            _strategy_diagram = None
            if _last_sql and re.search(r'\b(subscribers|devices|subscriber_technology|cells|sites)\b', _last_sql, re.IGNORECASE):
                _export_raw = _llm(
                    "You are a SQL expert. Given the analysis query below, write a new SELECT query for CSV export. "
                    "Use the same JOINs and WHERE conditions. Include all meaningful identifier and descriptor columns. "
                    "Additionally, add 1-3 computed annotation columns using CASE WHEN logic based on the data patterns found — for example: "
                    "'recommended_action' (a short action for this subscriber based on their technology/segment/usage), "
                    "'priority' (High/Medium/Low based on ARPU, technology, or churn risk), "
                    "'campaign_type' (most appropriate campaign name). "
                    "Use only columns that exist in the query's tables. Output only the SQL query, nothing else. No markdown, no explanation.",
                    f"Analysis query: {_last_sql}\nAnalysis context: {text_only[:300]}",
                    max_tokens=350,
                    allow_thinking=False
                )
                _export_raw = _export_raw.strip().strip('`').strip()
                if re.match(r'(?i)^\s*SELECT\b', _export_raw):
                    _extract_sql = _export_raw
            try:
                json_match = re.search(r'\{.*\}', conclusion, re.DOTALL)
                if json_match:
                    raw = json_match.group()
                    raw = re.sub(r'(\d),(\d{3})', r'\1\2', raw)
                    try:
                        parsed    = json.loads(raw)
                        text_only         = parsed.get("text", "")
                        _recs             = parsed.get("recommendations", [])
                        _strategy_diagram = parsed.get("strategy_diagram", None)
                        # Model returned data JSON instead of {text, chart} — extract and summarize
                        if not text_only:
                            # Find any list of dicts in the parsed object
                            data_list = next((v for v in parsed.values() if isinstance(v, list) and v and isinstance(v[0], dict)), None)
                            if data_list:
                                # Auto-build chart from the data
                                keys = list(data_list[0].keys())
                                label_col = next((k for k in keys if isinstance(data_list[0][k], str)), None)
                                num_cols  = [k for k in keys if isinstance(data_list[0].get(k), (int, float))]
                                if label_col and num_cols:
                                    val_col = num_cols[-1]
                                    chart_spec = {
                                        "type": "bar",
                                        "title": " ".join(val_col.split("_")).title() + " by " + label_col.title(),
                                        "x": [str(r[label_col]) for r in data_list],
                                        "y": [r[val_col] for r in data_list],
                                        "x_label": label_col.replace("_", " ").title(),
                                        "y_label": val_col.replace("_", " ").title(),
                                    }
                                # Synthesize a short text summary from the top rows
                                top = data_list[:3]
                                summary_parts = []
                                for row in top:
                                    parts = [f"{k.replace('_',' ')}: {v}" for k, v in row.items()]
                                    summary_parts.append(" | ".join(parts))
                                text_only = "  \n".join(summary_parts)
                            else:
                                text_only = conclusion
                        if not is_treemap:
                            chart_spec = chart_spec or parsed.get("chart", None)
                            if chart_spec and chart_spec.get("type") == "bar":
                                xs = chart_spec.get("x", [])
                                if xs and isinstance(xs[0], str) and re.search(r'\d{4}-\d{2}|\d{2}:\d{2}|Mar |Apr |Jan |Feb ', xs[0]):
                                    chart_spec["type"] = "line"
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

            # Normalize currency — model sometimes writes $ instead of Yuan
            text_only = re.sub(r'\$\s*([\d,\.]+)', r'\1 Yuan', text_only)

            # Strip markdown formatting from plain text
            text_only = re.sub(r'^#{1,6}\s+', '', text_only, flags=re.MULTILINE)  # headers
            text_only = re.sub(r'\*\*(.*?)\*\*', r'\1', text_only)                # bold
            text_only = re.sub(r'\*(.*?)\*', r'\1', text_only)                    # italic
            text_only = re.sub(r'^[-*]\s+', '', text_only, flags=re.MULTILINE)    # bullets
            text_only = re.sub(r'\|.*\|', '', text_only, flags=re.MULTILINE)      # tables
            text_only = re.sub(r'\n{3,}', '\n\n', text_only).strip()              # excess newlines

            # For treemap questions, ALWAYS build chart from query results — never rely on model JSON
            if is_treemap and context:
                chart_spec = _build_treemap_from_context(context)

            # For non-treemap, try building chart directly from last query result
            if chart_spec is None and context:
                chart_spec = _chart_from_context(context, question)
            # Final fallback: ask LLM to generate chart spec from prose
            if chart_spec is None:
                chart_spec = _to_chart_spec(text_only, question)

            # Recommendations fallback — only fires when the data has an actionable angle
            _actionable_signals = [
                "candidate", "3g", "volte", "churn", "risk", "upsell", "migrate", "migration",
                "sunset", "fwa", "5g", "upgrade", "hvc", "revenue", "arpu", "poor", "low",
                "inactive", "at-risk", "opportunity", "potential", "below", "gap"
            ]
            _has_actionable = any(s in (text_only + question).lower() for s in _actionable_signals)
            if not _recs and text_only and _has_actionable:
                rec_raw = _llm(
                    "You are a senior telecom commercial analyst. Based on the analysis below, give exactly 3 recommendations. "
                    "Each recommendation must explain what to do, why the data justifies it, and the expected business impact. "
                    f"Today's date: {__import__('datetime').date.today().isoformat()}. Only reference dates from the analysis — never invent deadlines. "
                    "Output a JSON array only, no other text: [\"rec1\", \"rec2\", \"rec3\"]",
                    f"Question: {question}\n\nAnalysis: {text_only}",
                    max_tokens=800,
                    allow_thinking=True
                )
                try:
                    arr_match = re.search(r'\[.*\]', rec_raw, re.DOTALL)
                    if arr_match:
                        _recs = json.loads(arr_match.group())
                except Exception:
                    pass

            return {
                "type":             "analysis",
                "text":             text_only,
                "recommendations":  _recs,
                "extract_sql":      _extract_sql,
                "chart":            chart_spec,
                "strategy_diagram": _strategy_diagram,
                "steps":            steps_log
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

        # ── SCHEMA ────────────────────────────────────────────────
        if "SCHEMA:" in response:
            tbl = response.split("SCHEMA:", 1)[1].strip().split()[0].strip(".,;")
            schema_result = schema_lookup(tbl)
            context += f"\nStep {step+1} [SCHEMA]:\n{schema_result}\n"
            if _streaming_queue:
                _streaming_queue.put({"type": "step_sql", "step": step+1,
                    "tag": "SCHEMA", "sql": tbl, "rows": 0})
            continue

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
                        # Extract everything after the tag — may be multiline SQL
                        after_tag = response.split(tag, 1)[1]
                        # Stop at the next action tag or end of response
                        next_tag_match = re.search(
                            r'\n(?:SCHEMA:|QUERY_SC:|QUERY_OP:|QUERY_BOTH:|CONCLUDE:|PROPOSE:)',
                            after_tag
                        )
                        sql_raw = after_tag[:next_tag_match.start()].strip() if next_tag_match else after_tag.strip()
                        sql = re.sub(r'```sql|```', '', sql_raw).strip()

                        if tag == "QUERY_BOTH:" and "ATTACH" not in sql.upper():
                            attach = f"ATTACH DATABASE '{OP_DB}' AS op; "
                            sql = attach + sql
                        if tag == "QUERY_OP:":
                            sql = re.sub(r'\bop\.', '', sql)
                        # If QUERY_BOTH only touches operator tables, downgrade to QUERY_OP
                        if tag == "QUERY_BOTH:":
                            _op_tables = {"customer_value","customers","subscriptions","plans",
                                          "billing","offers","campaigns","campaign_targets","sms_log","offer_assignments"}
                            _sql_tables = set(re.findall(r'(?:op\.)?(\w+)', sql.lower()))
                            _sc_tables = {"subscribers","devices","subscriber_technology","sites","cells",
                                          "kpis_daily","coverage","network_alarms","mobility_profile","dou_monthly","qoe_daily"}
                            if _sql_tables & _op_tables and not (_sql_tables & _sc_tables):
                                # Pure operator query — run via QUERY_OP directly
                                sql = re.sub(r'ATTACH[^;]+;\s*', '', sql, flags=re.IGNORECASE)
                                sql = re.sub(r'\bop\.', '', sql)
                                runner   = query_op
                                db_path  = OP_DB

                        if not sql.upper().startswith("SELECT") and \
                           not sql.upper().startswith("ATTACH"):
                            context += f"\nStep {step+1}: Invalid SQL skipped.\n"
                            executed = True
                            break

                        # Truncation guard — if SQL ends mid-word, retry with more tokens
                        if sql and not re.search(r'[)\'"a-zA-Z0-9_]\s*$', sql):
                            retry_resp = _llm(system, prompt, max_tokens=_max_tok * 2, timeout=420)
                            for rline in retry_resp.split("\n"):
                                rs = rline.strip()
                                if rs.startswith(tag):
                                    retry_sql = re.sub(r'```sql|```', '', rs.split(tag, 1)[1]).strip()
                                    if retry_sql.upper().startswith("SELECT"):
                                        sql = retry_sql

                        db_path = OP_DB if tag == "QUERY_OP:" else SC_DB

                        # Duplicate query guard
                        sql_key = re.sub(r'\s+', ' ', sql.strip().upper())
                        if sql_key in _seen_sqls:
                            context += f"\nStep {step+1}: Duplicate query skipped — already ran this. Use the previous result or try a different query.\n"
                            executed = True
                            break
                        _seen_sqls.add(sql_key)
                        _last_sql = sql

                        # Semantic checks first (catches issues EXPLAIN won't see)
                        sem_err = _check_sql_semantics(sql)
                        if sem_err:
                            context += f"\nStep {step+1} semantic error:\n{sem_err}\n"
                            executed = True
                            break

                        sql_err = _check_sql(sql, db_path)
                        if sql_err:
                            col_swap = re.search(r"no such column[:\s]+(?:\w+\.)?(\w+)", sql_err, re.I)
                            if col_swap:
                                col_name = col_swap.group(1).lower()
                                owners = [t for t, cols in _TABLE_COLUMNS.items() if col_name in cols]
                                if len(owners) == 1:
                                    bad_tbls = re.findall(r'(?:FROM|JOIN)\s+(\w+)', sql, re.I)
                                    bad_tbl = bad_tbls[0].lower() if bad_tbls else ""
                                    good_tbl = owners[0]
                                    if bad_tbl and bad_tbl != good_tbl:
                                        new_sql = re.sub(rf'\b{re.escape(bad_tbl)}\b', good_tbl, sql, flags=re.IGNORECASE)
                                        if not _check_sql(new_sql, db_path):
                                            print(f"[AutoFix] Swapped {bad_tbl}→{good_tbl} for column '{col_name}', retrying")
                                            sql = new_sql
                                            sql_err = None
                            if sql_err:
                                context += f"\nStep {step+1}: {sql_err}\n"
                                executed = True
                                break

                        results = runner(sql)
                        _is_err = bool(results and "error" in results[0])
                        if _streaming_queue:
                            _streaming_queue.put({"type": "step_sql", "step": step+1,
                                "tag": tag.rstrip(":"), "sql": sql[:200],
                                "rows": 0 if _is_err else (len(results) if results else 0),
                                "error": results[0]["error"] if _is_err else None})
                        if results and "error" not in results[0]:
                            n = len(results)
                            if is_treemap:
                                _treemap_cap = 200
                                sample = results[:_treemap_cap]
                                compact = ", ".join(str(dict(r)) for r in sample)
                                snippet = f"[{compact}]" + (f" (showing {_treemap_cap} of {n})" if n > _treemap_cap else "")
                            elif n <= 30:
                                snippet = json.dumps(results, indent=2)
                            else:
                                compact = ", ".join(str(dict(r)) for r in results[:30])
                                snippet = f"[{compact}] (showing 30 of {n} rows)"
                            context += (
                                f"\nStep {step+1} [{tag.rstrip(':')}]:\n"
                                f"SQL: {sql[:120]}\n"
                                f"Results ({n} rows): {snippet}\n"
                            )
                        elif results and "error" in results[0]:
                            err_msg = results[0]['error']
                            context += f"\nStep {step+1}: Query error: {err_msg}\n"
                            col_swap = re.search(r"'(\w+)' exists on: ([\w]+)", err_msg)
                            tbl_swap = re.search(r"table (\w+) has no column '(\w+)'", err_msg)
                            if col_swap and tbl_swap:
                                bad_tbl  = tbl_swap.group(1)
                                col_name = col_swap.group(1)
                                good_tbl = col_swap.group(2).strip()
                                new_sql  = re.sub(rf'\b{re.escape(bad_tbl)}\b', good_tbl, sql, flags=re.IGNORECASE)
                                context += (
                                    f"[SYSTEM] Column '{col_name}' does not exist on {bad_tbl}. "
                                    f"It is on {good_tbl}. "
                                    f"Retry immediately with QUERY_SC:\n{new_sql}\n"
                                )
                            else:
                                col_hint = _schema_hint_for_error(err_msg, sql, db_path)
                                if col_hint:
                                    context += f"SCHEMA CORRECTION: {col_hint}\n"
                        else:
                            # 0 rows even after auto-correction — tell model to try differently
                            context += (
                                f"\nStep {step+1}: Query returned 0 rows after auto-correction. "
                                f"Try a different approach: check join conditions, verify column names, "
                                f"or use QUERY_OP for operator-only tables.\n"
                            )
                        executed = True
                        break
                if executed:
                    break

        if not executed:
            # Model output plain text/recommendations with no query tag — force it to act
            context += f"\nStep {step+1}: No query executed. Stop writing explanations or recommendations. Output ONLY one of: QUERY_SC: / QUERY_OP: / QUERY_BOTH: / CONCLUDE: followed by SQL or JSON on a single line.\n"

    # Fallback
    if context:
        summary = _llm(
            "You are a telecom analyst. Summarize findings and recommend actions.",
            f"Question: {question}\n\nData gathered:\n{context}\n\nAnalysis:"
        )
        chart_spec = _build_treemap_from_context(context) if is_treemap else None
        if chart_spec is None:
            chart_spec = _to_chart_spec(f"{summary}\n\nRaw data:\n{context[-1500:]}", question)
        return {"type": "analysis", "text": summary, "chart": chart_spec, "steps": steps_log}

    # Steps exhausted without CONCLUDE — save state so the user can continue
    global _partial_state
    _partial_state = {"question": question, "context": context, "steps_log": steps_log, "steps_used": max_steps}
    partial_summary = context[-800:] if context else "No data gathered yet."
    return {
        "type": "analysis",
        "text": f"I ran out of steps before completing the analysis. Here's what I gathered so far:\n\n{partial_summary}",
        "steps": steps_log,
        "truncated": True,
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
    # Requires JOIN (city/region filter + technology breakdown) — fast path can't handle
    "for all technologies", "by technology", "per technology", "each technology",
    "all technologies", "across technologies",
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
SELECT ROUND(AVG(k.dl_throughput_mbps),2) as avg_dl FROM kpis_daily k JOIN cells c ON k.cell_id=c.cell_id JOIN sites s ON c.site_id=s.site_id WHERE s.region='Fire Nation Capital' AND c.technology='4G' AND k.date=(SELECT MAX(date) FROM kpis_daily)

Active critical alarms:
SELECT COUNT(*) as n FROM network_alarms WHERE severity='critical' AND is_active=1

Subscribers on 3G in Tunis:
SELECT COUNT(*) as n FROM subscribers s JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE st.current_technology='3G' AND s.region='Ba Sing Se'
"""

    sql_raw = _llm(sql_system, f"Question: {question}\nSQL:", max_tokens=200)
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
        err_ctx = ""
        if results and "error" in results[0]:
            err_msg = results[0]["error"]
            err_ctx = f"\nStep 0 [QUERY_SC]:\nSQL: {sql}\nQuery error: {err_msg}\n"
            col_swap = re.search(r"'(\w+)' exists on: ([\w]+)", err_msg)
            tbl_swap = re.search(r"table (\w+) has no column '(\w+)'", err_msg)
            if col_swap and tbl_swap:
                err_ctx += (f"[SYSTEM] Column '{col_swap.group(1)}' does not exist on "
                            f"{tbl_swap.group(1)}. Use {col_swap.group(2)} instead.\n")
        return _run_chain(question, _resume_context=err_ctx if err_ctx else None)

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

    text_only = _llm(
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
{{"action": "assign_offer", "offer_id": 1, "msisdn_filter_sql": "SELECT msisdn FROM subscribers s JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE s.region='Ba Sing Se' AND st.current_technology='3G'"}}
{{"action": "create_campaign", "name": "FWA Conversion Tunis", "type": "FWA", "offer_id": 5, "msisdn_filter_sql": "SELECT mp.msisdn FROM mobility_profile mp JOIN dou_monthly dm ON mp.msisdn=dm.msisdn AND mp.month=dm.month WHERE mp.mobility_class='stationary' AND dm.total_data_gb>30 AND mp.month=(SELECT MAX(month) FROM mobility_profile)"}}
"""
    action_raw = _llm(
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

_memory           = []
_pending          = None
_partial_state    = None  # saved context when chain exits without CONCLUDE
_think_log        = []   # collects <think> blocks from current run
_streaming_queue  = None
_stop_event       = None   # threading.Event — set to abort generation
THINKING_ENABLED  = False  # set via dashboard toggle
THINK_BUDGET      = 512   # max thinking tokens (Ollama qwen3 native budget)

def _compress(text: str) -> str:
    if len(text) <= 300:
        return text
    if len(text) <= 600:
        return text[:500] + "…"
    return _llm(
        "Summarize in 3-4 sentences. Preserve all specific numbers, region names, and key findings.",
        text, max_tokens=150
    )

def _memory_context() -> str:
    if not _memory:
        return ""
    lines = [f"{m['role'].upper()}: {m['summary']}" for m in _memory[-8:]]
    return "Conversation history:\n" + "\n".join(lines) + "\n\n"

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

_CHITCHAT_TRIGGERS = [
    "hello", "hi", "hey", "good morning", "good afternoon", "good evening",
    "how are you", "what can you do", "what are you", "who are you",
    "what is your name", "introduce yourself", "help me", "help",
    "what do you know", "what can i ask", "capabilities",
]

def _is_chitchat(text: str) -> bool:
    t = text.lower().strip().rstrip("?!.,")
    return any(t == trigger or t.startswith(trigger) for trigger in _CHITCHAT_TRIGGERS)

_SUGGESTION_ONLY_PATTERNS = [
    "what do you suggest", "any suggestions", "what would you suggest",
    "what do you recommend", "any recommendations", "what are your recommendations",
    "what should we do", "what should i do", "what's your recommendation",
    "give me recommendations", "give recommendations", "what actions",
    "what next", "what now", "suggest something", "suggest actions",
    "what can we do", "what can i do", "how can we improve", "how to improve",
    "how do you suggest", "how do u suggest", "how would you suggest",
    "how do we increase", "how do we improve", "how do we boost", "how do we grow",
    "how can we increase", "how can we boost", "how can we grow",
    "how to increase", "how to boost", "how to grow", "how to fix",
    "ways to increase", "ways to improve", "ways to boost",
    "suggest we", "suggest i ", "suggest a way",
    "increase those", "increase the number", "increase subscribers",
    "what strategy", "what strategies", "what approach",
]

def _is_suggestion_only(text: str, has_prior_context: bool = False) -> bool:
    """Returns True if the question is asking for suggestions/strategy, not new data."""
    t = text.lower().strip().rstrip("?!.,")
    if any(p in t for p in _SUGGESTION_ONLY_PATTERNS):
        return True
    if has_prior_context and re.search(r'\b(how|what|ways?)\b.{0,40}\b(increase|improve|boost|grow|fix|tackle|address)\b', t):
        return True
    return False

def _chitchat_reply(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ["hello", "hi", "hey", "morning", "afternoon", "evening"]):
        return ("Hello! I'm your NetworkAnalyzer telecom analyst. Ask me anything about the network — "
                "subscriber counts, ARPU trends, churn risk, 5G coverage gaps, alarm hotspots, or campaign performance.")
    return ("I can help you analyze your telecom network and subscriber base. Try asking about:\n"
            "- Network KPIs by region or technology (5G/4G/3G)\n"
            "- Subscriber churn risk and retention scoring\n"
            "- ARPU trends and high-value customer segments\n"
            "- 5G upsell candidates and coverage gaps\n"
            "- Campaign ROI and offer performance\n"
            "- FWA candidate identification\n"
            "Just describe what you want to know — I'll query the data and give you numbers.")

_CONVERSATIONAL_PATTERNS = [
    "that's not right", "that's wrong", "that doesn't", "that is not", "thats not",
    "i don't think", "i dont think", "i think you", "you're wrong", "youre wrong",
    "incorrect", "not correct", "doesn't sound right", "doesnt sound right",
    "that seems off", "are you sure", "are u sure", "u sure", "i disagree",
    "no that's", "no thats", "no it's", "no its", "this is wrong", "this seems wrong",
    "this doesn't", "this doesnt", "not accurate", "wait", "hold on", "actually",
    "what do you think", "what do u think", "why do you", "why would",
    "explain why", "explain that", "can you explain", "elaborate",
    "what does that mean", "what does this mean",
    "what are you", "what r u", "what are u", "i meant", "i mean",
    "never mind", "nevermind", "forget it", "ignore that",
]

def _is_conversational(text: str, has_prior_context: bool) -> bool:
    if not has_prior_context:
        return False
    t = text.lower().strip()
    if any(p in t for p in _CONVERSATIONAL_PATTERNS):
        return True
    words = t.split()
    if len(words) <= 6 and not any(w in t for w in [
        "show", "list", "count", "how many", "what is", "what are", "give me",
        "average", "total", "compare", "find", "get", "query", "kpi", "top",
    ]):
        return True
    return False

def _classify_intent(user_input: str, mem_ctx: str) -> str:
    system = "You are an intent classifier. Reply with exactly one word: query, suggest, or converse."
    prompt = (
        f"Conversation so far:\n{mem_ctx}\n\n"
        f"New user message: {user_input}\n\n"
        "Classify the intent:\n"
        "- query: user wants new data fetched from the database. Also use 'query' if the message asks "
        "  for both data AND advice in the same turn (e.g. 'which X is highest? how do we improve it?') "
        "  — data must be fetched first.\n"
        "- suggest: user wants advice or strategy ONLY, and the needed data is already visible in the conversation above.\n"
        "- converse: user is correcting, clarifying, reacting, or asking a follow-up that needs no new data.\n"
        "Reply with exactly one word."
    )
    label = _llm(system, prompt, max_tokens=5, allow_thinking=False).strip().lower()
    if label not in ("query", "suggest", "converse"):
        return "query"
    return label

def _direct_reply(user_input: str, mem_ctx: str, mode: str) -> dict:
    if mode == "suggest":
        system = (
            "You are a senior telecom strategy consultant. "
            "The user wants actionable recommendations based on the analysis already done. "
            "Draw on the data and findings in the conversation to give concrete, specific advice. "
            "Be direct and practical. 3-5 sentences. Do NOT mention SQL or databases."
        )
    else:
        system = (
            "You are a telecom analyst in a conversation with a colleague. "
            "The user is reacting to your previous analysis — they may be questioning it, correcting it, "
            "asking why something happened, or just thinking out loud. "
            "Engage naturally: reflect on the data, acknowledge uncertainty if relevant, think it through. "
            "2-4 sentences. Do NOT mention SQL or databases. Sound like a person, not a report."
        )
    prompt = (
        f"Conversation so far:\n{mem_ctx}\n\n"
        f"User: {user_input}\n\n"
        f"Respond:"
    )
    text = _llm(system, prompt, max_tokens=500, allow_thinking=False)
    return {"type": "analysis", "text": text}

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
    global _pending, _memory, _think_log, _partial_state
    _think_log = []  # reset for this run

    # continue from truncated chain
    if user_input.strip().lower() in ("continue", "__continue__") and _partial_state:
        state = _partial_state
        _partial_state = None
        result = _run_chain(state["question"], max_steps=4, _resume_context=state["context"], _resume_steps=state["steps_log"])
        _memory.append({"role": "agent", "summary": _compress(result.get("text", ""))})
        if _think_log:
            result["think_log"] = _think_log[:]
        return result

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
        resolved_input = _llm(
            "Rewrite the follow-up question as a complete standalone question using the context. Output only the rewritten question, nothing else.",
            f"Context: {mem_ctx}\nFollow-up: {user_input}\nComplete question:",
            max_tokens=60
        )
        resolved_input = resolved_input.strip().strip('"')

    # conversational / off-topic fast path
    if _is_chitchat(user_input):
        return {"type": "analysis", "text": _chitchat_reply(user_input)}

    # LLM-based intent classifier — only runs when there's prior context
    if mem_ctx:
        _intent = _classify_intent(user_input, mem_ctx)
        if _intent in ("converse", "suggest"):
            result = _direct_reply(user_input, mem_ctx, mode=_intent)
            _memory.append({"role": "agent", "summary": _compress(result["text"])})
            return result

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
    if _think_log:
        result["think_log"] = _think_log[:]
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
    global _memory, _pending, _partial_state
    _memory        = []
    _pending       = None
    _partial_state = None