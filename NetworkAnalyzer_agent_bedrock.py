import os
import re
import json
import time
import random
import sqlite3
import requests
from datetime import datetime, timedelta
from rag_retriever import retrieve, retrieve_sql
from schema_graph_retriever import retrieve_graph_context, classify_intent

def _log_info(msg): print(f"[INFO] {msg}")
_log = type("_L", (), {"info": staticmethod(_log_info), "debug": staticmethod(_log_info), "warning": staticmethod(_log_info)})()


# ── AWS Bedrock client ───────────────────────────────────────────────────
_bedrock_client = None
def _get_bedrock():
    global _bedrock_client
    if _bedrock_client is None:
        import boto3
        _bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
            aws_session_token=os.environ.get("AWS_SESSION_TOKEN"),
        )
    return _bedrock_client

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
    print(f"=== NetworkAnalyzer Agent (Bedrock) ===")
    print(f"  Model:       {MODEL}")
    print(f"  YuanU layers:  {NUM_YuanU_LAYERS}  (VRAM)")
    print(f"  CPU threads: {NUM_THREADS}  (RAM)")
    print(f"  Context:     {CTX_SIZE} tokens")
    print(f"=================================")

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
    # Normalize any absolute path in ATTACH to just 'operator_new.db'
    sql = _re.sub(r"ATTACH\s+DATABASE\s+'[^']*operator_new\.db'",
                  "ATTACH DATABASE 'operator_new.db'", sql, flags=_re.IGNORECASE)
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
        # Build alias→table map so we can show real columns of the affected table
        _alias_map = _build_alias_map(sql_clean)
        enriched = []
        for err in col_errors[:5]:
            # err is like "alias.col (table tbl has no column 'col')"
            tbl_match = re.search(r'\(table (\w+)', err)
            col_match = re.search(r"no column '(\w+)'", err)
            if col_match:
                col_name = col_match.group(1).lower()
                owners = [t for t, cols in _TABLE_COLUMNS.items() if col_name in cols]
                if owners:
                    err += f" — '{col_name}' exists on: {', '.join(owners)}"
            # Always append the real columns of the table so model can self-correct
            if tbl_match:
                real_tbl = tbl_match.group(1).lower()
                if real_tbl in _TABLE_COLUMNS:
                    err += f". Real columns of {real_tbl}: {', '.join(sorted(_TABLE_COLUMNS[real_tbl]))}"
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
  msisdn          TEXT PK   — phone number, links to operator DB
  imsi            TEXT      — SIM identifier
  sim_type        TEXT      — 'SIM' or 'eSIM'
  full_name       TEXT      — subscriber display name
  is_active       INT       — 1=active subscriber
  activation_date TEXT      — YYYY-MM-DD when the SIM was activated
  age             INT
  gender          TEXT
  region          TEXT      — Avatar region (Ba Sing Se, Fire Nation Capital, Omashu, etc.)
  city            TEXT
  latitude        REAL
  longitude       REAL
  area_code       TEXT      — geographic area: T=Ba Sing Se metro, N=Water Tribe, C=Earth Kingdom, R=Rural interior, S=Fire Nation
  nation          TEXT      — elemental nation: 'Earth Kingdom','Fire Nation','Water Tribe','Air Nomads'
!! Use nation column directly: WHERE s.nation='Fire Nation' — no JOIN needed.
!! subscribers has NO technology columns. For device capability use devices.is_5g_capable / devices.max_technology.
!! For current network tech use subscriber_technology.current_technology. Always JOIN those tables.
!! WRONG COLUMN NAMES (will cause errors — never use these):
!!   subscription_date → use activation_date
!!   join_date / signup_date / start_date / contract_start → use activation_date
!!   subscriber_type / customer_type / status / sim_status → use is_active
!!   phone_number → use msisdn
!!   subscriber_name / name → use full_name

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
!! WRONG COLUMN NAMES for devices (never use these):
!!   device_model / phone_model / device_type → use model
!!   device_brand / manufacturer → use brand
!!   device_os → use os

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
  nation     TEXT  — elemental nation: 'Earth Kingdom','Fire Nation','Water Tribe','Air Nomads'
  city       TEXT
  latitude   REAL
  longitude  REAL
  site_type  TEXT  — 'macro','micro','indoor','fwa_home_cell'
  zone       TEXT  — same as nation (legacy column, prefer nation)
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
  registration_date TEXT   — YYYY-MM-DD when customer was registered
  is_active         INT
  NOTE: NO region column — get region from subscribers.region in NetworkAnalyzer DB
!! WRONG COLUMN NAMES for customers (never use these):
!!   subscription_date / join_date / signup_date → use registration_date
!!   subscriber_type / customer_type → use segment
!!   arpu / plan_name / plan_id / revenue / monthly_spend → NOT in customers table; join subscriptions+plans

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
  msisdn           TEXT
  month            TEXT  — format 'YYYY-MM', 6 months of history per subscriber
  arpu             REAL  — monthly spend in Yuan (5-130 Yuan typical range)
  value_segment    TEXT  — 'bronze'(<15 Yuan),'silver'(15-35),'gold'(35-75),'platinum'(>=75)
  is_hvc           INT   — 1 = High Value Customer (gold or platinum)
  churn_risk_score REAL  — ML churn probability 0.0-1.0 (XGBoost, AUC 0.885)
  churn_label      TEXT  — 'low'(<p70),'medium'(p70-p90),'high'(>p90) — top 10% = high risk

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

5G upsell candidates — device capable AND in 5G coverage area (BOTH conditions always required):
QUERY_SC: SELECT COUNT(DISTINCT s.msisdn) as n FROM subscribers s JOIN devices d ON s.msisdn=d.msisdn JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE d.is_5g_capable=1 AND st.current_technology != '5G' AND EXISTS(SELECT 1 FROM coverage cv WHERE cv.msisdn=s.msisdn AND cv.technology_available='5G')

5G upsell by region — device + coverage, NO LIMIT:
QUERY_SC: SELECT s.region, COUNT(DISTINCT s.msisdn) as n FROM subscribers s JOIN devices d ON s.msisdn=d.msisdn JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE d.is_5g_capable=1 AND st.current_technology != '5G' AND EXISTS(SELECT 1 FROM coverage cv WHERE cv.msisdn=s.msisdn AND cv.technology_available='5G') GROUP BY s.region ORDER BY n DESC

3G subscribers flagged for 5G upsell — Strong = 5G device AND 5G coverage, otherwise Not Candidate:
QUERY_SC: SELECT s.msisdn, s.region, d.brand, dm.total_data_gb, CASE WHEN d.is_5g_capable=1 AND EXISTS(SELECT 1 FROM coverage cv WHERE cv.msisdn=s.msisdn AND cv.technology_available='5G') THEN 'Strong Candidate' ELSE 'Not Candidate' END AS upsell_flag FROM subscribers s JOIN devices d ON s.msisdn=d.msisdn JOIN subscriber_technology st ON s.msisdn=st.msisdn JOIN dou_monthly dm ON s.msisdn=dm.msisdn AND dm.month=(SELECT MAX(month) FROM dou_monthly) WHERE s.is_active=1 AND st.current_technology='3G' ORDER BY dm.total_data_gb DESC LIMIT 100

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
CRITICAL — sites.zone values are ONLY 'Earth Kingdom','Fire Nation','Water Tribe','Air Nomads'. Region names like 'Ba Sing Se','Fire Nation Capital' are in sites.region, NOT sites.zone. NEVER filter WHERE zone='Ba Sing Se' — use WHERE s.region='Ba Sing Se' instead.

IMPORTANT: kpis_daily has NO technology column. NEVER filter kpis_daily by technology directly.
To filter KPIs by technology: JOIN cells c ON k.cell_id=c.cell_id WHERE c.technology='4G'
subscriber_technology is for subscriber current tech — NOT for network KPI filtering.

CRITICAL — REGIONAL BREAKDOWNS: When asked for ANY breakdown by region, distribution by region,
or per-region analysis, NEVER add LIMIT to the query. Always return ALL regions.
Queries with GROUP BY s.region must NOT have a LIMIT clause.

CRITICAL — REGION NAME CASE: Region values in the database are Title Case.
  CORRECT:   WHERE s.region='Ba Sing Se'   WHERE s.region='Omashu'   WHERE s.region='Fire Nation Capital'
  WRONG:     WHERE s.region='ba sing se'   WHERE s.region='omashu'
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

# ── Semantic schema index — embeddings for column-level similarity ───────
_SC_TABLES = {
    "subscribers", "devices", "subscriber_technology", "sites", "cells",
    "kpis_daily", "coverage", "network_alarms", "mobility_profile",
    "dou_monthly", "qoe_daily", "ott_monthly", "qoe_scores", "mobility_events",
    "alarms",
}
_OP_TABLES = {
    "customers", "plans", "subscriptions", "billing", "offers", "campaigns",
    "campaign_targets", "sms_log", "offer_assignments", "customer_value",
    "devices", "churn_risk_score",
}

_sem_labels:   list[str] = []   # "table.column"
_sem_db_tags:  list[str] = []   # "sc" or "op"
_sem_matrix          = None     # np.ndarray (N, D) — built lazily
_sem_model           = None

def _build_semantic_index():
    global _sem_labels, _sem_db_tags, _sem_matrix, _sem_model
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np

        sc_conn = sqlite3.connect(SC_DB)
        op_conn = sqlite3.connect(OP_DB)
        labels, tags = [], []

        for db_tag, conn in [("sc", sc_conn), ("op", op_conn)]:
            for (tbl,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                for row in conn.execute(f"PRAGMA table_info({tbl})").fetchall():
                    col = row[1]
                    labels.append(f"{tbl}.{col}")
                    tags.append(db_tag)
            conn.close()

        _sem_model  = SentenceTransformer("all-MiniLM-L6-v2")
        _sem_matrix = _sem_model.encode(labels, convert_to_numpy=True, normalize_embeddings=True)
        _sem_labels = labels
        _sem_db_tags = tags
        print(f"[SemanticIndex] Built index: {len(labels)} columns")
    except Exception as e:
        print(f"[SemanticIndex] Skipped (sentence-transformers not installed?): {e}")

import threading as _threading
_sem_ready = _threading.Event()

def _build_semantic_index_bg():
    _build_semantic_index()
    _sem_ready.set()
    # Share the loaded model with rag_retriever so it doesn't load it again
    if _sem_model is not None:
        from rag_retriever import set_shared_model, _build_index, _build_sql_index
        set_shared_model(_sem_model)
        _build_index()
        _build_sql_index()
        print("[RAGPrewarm] Knowledge + SQL indexes ready.")

_threading.Thread(target=_build_semantic_index_bg, daemon=True, name="SemanticIndexBuilder").start()


def _semantic_schema_hint(question: str, top_k: int = 12) -> str:
    """Return a short schema hint string listing the most relevant columns for this question."""
    if _sem_matrix is None or _sem_model is None:
        return ""
    try:
        import numpy as np
        q_vec = _sem_model.encode([question], convert_to_numpy=True, normalize_embeddings=True)
        scores = (_sem_matrix @ q_vec.T).squeeze()
        top_idx = scores.argsort()[::-1][:top_k]
        sc_hits = [_sem_labels[i] for i in top_idx if _sem_db_tags[i] == "sc"]
        op_hits = [_sem_labels[i] for i in top_idx if _sem_db_tags[i] == "op"]
        parts = []
        if sc_hits:
            parts.append("NetworkAnalyzer DB: " + ", ".join(sc_hits))
        if op_hits:
            parts.append("Operator DB: " + ", ".join(op_hits))
        return "SEMANTIC HINT (most relevant columns): " + " | ".join(parts) if parts else ""
    except Exception:
        return ""


def _semantic_db_vote(question: str) -> str | None:
    """Return 'sc', 'op', or None if unclear — based on which DB dominates top-10 column hits."""
    if _sem_matrix is None or _sem_model is None:
        return None
    try:
        import numpy as np
        q_vec = _sem_model.encode([question], convert_to_numpy=True, normalize_embeddings=True)
        scores = (_sem_matrix @ q_vec.T).squeeze()
        top_idx = scores.argsort()[::-1][:10]
        sc_count = sum(1 for i in top_idx if _sem_db_tags[i] == "sc")
        op_count = sum(1 for i in top_idx if _sem_db_tags[i] == "op")
        if sc_count > op_count + 3:
            return "sc"
        if op_count > sc_count + 3:
            return "op"
        return None
    except Exception:
        return None

COMPACT_SCHEMA = """
=== NetworkAnalyzer DB (query_sc) ===
subscribers: msisdn(PK), region(Title Case!), city, area_code(T/N/C/R/S), is_active — NO tech columns
devices: msisdn(PK), brand, model, max_technology, is_5g_capable, volte_capable, vowifi_capable, os
subscriber_technology: msisdn(PK), current_technology('2G'/'3G'/'4G'/'5G'), current_cell_id, volte_active, last_seen_date
sites: site_id(PK), site_name, region(Title Case!), city, latitude, longitude, site_type, zone('Earth Kingdom'/'Fire Nation'/'Water Tribe'/'Air Nomads'), is_active
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
- region values are Title Case: 'Ba Sing Se' not 'ba sing se', 'Omashu' not 'omashu'
- kpis_daily has NO msisdn and NO technology — filter tech via JOIN cells, filter region via JOIN cells→sites
- COUNT subscribers with COUNT(DISTINCT msisdn) — customer_value/billing/dou_monthly have multiple rows per subscriber, COUNT(*) overcounts
- To identify subscribers ON 5G: use subscriber_technology.current_technology='5G' — NOT plans.supports_5g. supports_5g means the plan allows 5G but the subscriber may not be using it yet
- cells has NO msisdn — never join cells ON msisdn
- customer_value and billing are in operator DB — use ATTACH or query_op()
- billing has one row per subscriber per month — ALWAYS filter: b.billing_month=(SELECT MAX(billing_month) FROM op.billing)
- sites.zone is only 'Earth Kingdom'/'Fire Nation'/'Water Tribe'/'Air Nomads' — never WHERE zone='Ba Sing Se'
- For cross-DB queries: ATTACH DATABASE 'operator_new.db' AS op; then prefix op.table_name
- If your SQL references op.anything, you MUST use QUERY_BOTH — never QUERY_SC with op. prefix
- op.customers has NO region column — always get region from subscribers.region (NetworkAnalyzer DB)
- op.billing has NO region column — always JOIN subscribers s ON s.msisdn=b.msisdn to get region
- NEVER join op.plans directly to subscribers — plans has no msisdn. Always go through subscriptions: JOIN op.subscriptions sub ON s.msisdn=sub.msisdn AND sub.is_current=1 JOIN op.plans p ON sub.plan_id=p.plan_id
- To check 5G coverage for a subscriber: JOIN coverage cov ON s.msisdn=cov.msisdn AND cov.technology_available='5G'
- Technology upsell (5G, 4G) ALWAYS requires BOTH device capability AND network coverage: d.is_5g_capable=1 AND EXISTS(SELECT 1 FROM coverage cv WHERE cv.msisdn=s.msisdn AND cv.technology_available='5G'). A subscriber without 5G coverage cannot be upsold to 5G even if their device supports it.
- To link a subscriber to their cell KPIs: JOIN subscriber_technology st ON s.msisdn=st.msisdn JOIN kpis_daily k ON st.current_cell_id=k.cell_id AND k.date=(SELECT MAX(date) FROM kpis_daily)
- kpis_daily column is dropped_call_rate (NOT drop_rate). Other KPI columns: rsrp_avg, sinr_avg, dl_throughput_mbps, ul_throughput_mbps, latency_ms, congestion_level
- To get region-level KPI averages: JOIN kpis_daily k ON c.cell_id=k.cell_id JOIN cells c ON k.cell_id=c.cell_id JOIN sites si ON c.site_id=si.site_id GROUP BY si.region — kpis_daily has NO region column, ALWAYS go through cells→sites
- Network average drop rate subquery: (SELECT AVG(dropped_call_rate) FROM kpis_daily WHERE date=(SELECT MAX(date) FROM kpis_daily))
- cells has NO rsrp_avg column — rsrp_avg is ONLY on kpis_daily. Always JOIN kpis_daily to get signal quality.
- For RSRP thresholds use the DATA FACTS section — it contains the real p20/p80 from the live DB.
- When writing CASE WHEN classification columns, if all rows would fall into the same bucket (e.g. 100% "Not a Candidate"), that is a logic error — re-examine the WHERE clause and CASE conditions before concluding.
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

def _load_data_profile_cached() -> str:
    """Return DATA_PROFILE from disk cache if both DBs are unchanged, else rebuild."""
    cache_path = os.path.join(BASE_DIR, ".data_profile_cache.txt")
    try:
        sc_mtime = os.path.getmtime(SC_DB)
        op_mtime = os.path.getmtime(OP_DB)
        db_mtime = max(sc_mtime, op_mtime)
        if os.path.exists(cache_path) and os.path.getmtime(cache_path) >= db_mtime:
            with open(cache_path, "r", encoding="utf-8") as f:
                profile = f.read()
            print(f"[DataProfile] Loaded from cache ({len(profile.splitlines())} lines)")
            return profile
    except Exception:
        pass
    profile = _build_data_profile()
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(profile)
        print("[DataProfile] Cache written.")
    except Exception:
        pass
    return profile

DATA_PROFILE = _load_data_profile_cached()

# Append real data facts to both schema strings so every prompt has them
FULL_SCHEMA    = FULL_SCHEMA    + "\n\n" + DATA_PROFILE
COMPACT_SCHEMA = COMPACT_SCHEMA + "\n\n" + DATA_PROFILE

# ── Bedrock-specific schema additions ───────────────────────────────────
BEDROCK_CRITICAL_RULES = """
=== CRITICAL RULES ===
- NetworkAnalyzer DB (SC): subscribers, devices, subscriber_technology, sites, cells, kpis_daily, network_alarms, coverage, dou_monthly, mobility_profile, qoe_daily
- Operator DB (OP): customers, subscriptions, plans, billing, customer_value, offers, campaigns, campaign_targets
- Cross-DB queries: use QUERY_BOTH with ATTACH DATABASE 'operator_new.db' AS op; prefix OP tables with op.
- subscriptions: always filter is_current=1 for active subscription
- billing: one row per subscriber per month — filter b.billing_month=(SELECT MAX(billing_month) FROM op.billing)
- customer_value: one row per subscriber per month — filter cv.month=(SELECT MAX(month) FROM op.customer_value)
- kpis_daily: no msisdn column — link subscribers via subscriber_technology.current_cell_id → kpis_daily.cell_id
- region values are Title Case: 'Ba Sing Se', 'Fire Nation Capital', 'Omashu' etc.
- sites.zone is only 'Earth Kingdom'/'Fire Nation'/'Water Tribe'/'Air Nomads'
- dou_monthly is in NetworkAnalyzer DB (SC), NOT operator DB — never prefix it as op.dou_monthly
- For monthly data usage trends: use dou_monthly (msisdn, month YYYY-MM, total_data_gb). To compare two months use a self-join or CTE — NEVER use LAG() or window functions over cross-DB ATTACH queries.
- NEVER silently drop conditions from the user's question — include ALL filters requested.
- If a query returns 0 or 1 rows unexpectedly, do NOT retry the same approach — change strategy.
- When counting subscribers, ALWAYS use COUNT(DISTINCT msisdn) — tables like customer_value, billing, dou_monthly have multiple rows per subscriber (monthly history), so COUNT(*) will massively overcount.
- To identify subscribers currently ON 5G: filter subscriber_technology.current_technology='5G' — do NOT use plans.supports_5g.
"""
FULL_SCHEMA_BEDROCK = FULL_SCHEMA + BEDROCK_CRITICAL_RULES

# ═══════════════════════════════════════════════════════════════════════
# BEDROCK LLM
# ═══════════════════════════════════════════════════════════════════════

def _sanitize_response(text: str) -> str:
    """Remove non-Latin script bleed (Hebrew, Arabic, CJK, etc.) from model output."""
    # Truncate at the first run of non-Latin/non-ASCII script characters (outside SQL strings)
    # Allow: ASCII, Latin extended, common punctuation, digits
    cleaned = re.sub(r'[\u0590-\u05FF\u0600-\u06FF\u4E00-\u9FFF\u3040-\u30FF]+.*', '', text, flags=re.DOTALL)
    return cleaned.strip()


def _bedrock(system: str, prompt: str, max_tokens: int = 1200, history: list = None, **_) -> str:
    """Call AWS Bedrock converse API with streaming and optional multi-turn history."""
    client = _get_bedrock()
    model_id = os.environ.get("BEDROCK_MODEL", "qwen.qwen3-32b-v1:0")
    current_msg = {"role": "user", "content": [{"text": prompt}]}
    messages = (history + [current_msg]) if history else [current_msg]
    kwargs = dict(
        modelId=model_id,
        system=[{"text": system}],
        messages=messages,
        inferenceConfig={"maxTokens": max_tokens, "temperature": 0.1},
    )
    # Retry transient Bedrock failures (throttling / timeouts / 5xx) with
    # exponential backoff. Only retry when nothing has streamed yet, otherwise
    # a retry would re-emit duplicate tokens to the UI.
    _TRANSIENT = ("throttl", "timeout", "serviceunavailable", "503", "429",
                  "too many requests", "internalserver", "modeltimeout", "modelnotready")
    max_attempts = 4
    for attempt in range(max_attempts):
        parts = []
        try:
            response = client.converse_stream(**kwargs)
            for event in response["stream"]:
                if _stop_event and _stop_event.is_set():
                    break
                tok = event.get("contentBlockDelta", {}).get("delta", {}).get("text", "")
                if tok:
                    parts.append(tok)
                    if _streaming_queue:
                        _streaming_queue.put({"type": "token", "token": tok})
            return _sanitize_response("".join(parts).strip())
        except Exception as e:
            msg = str(e).lower()
            # mid-stream failure: keep whatever we already produced, don't duplicate
            if parts:
                print(f"[Bedrock partial after {len(parts)} toks] {e}")
                return _sanitize_response("".join(parts).strip()) or f"ERROR: {e}"
            is_transient = any(k in msg for k in _TRANSIENT)
            if is_transient and attempt < max_attempts - 1:
                wait = (2 ** attempt) * 1.5 + random.uniform(0, 0.6)
                print(f"[Bedrock retry {attempt+1}/{max_attempts-1}] {e} — backoff {wait:.1f}s")
                time.sleep(wait)
                continue
            print(f"[Bedrock ERROR] {e}")
            return f"ERROR: {e}"


def _llm(system: str, prompt: str, max_tokens: int = 1200, timeout: int = 300, allow_thinking: bool = True, think_budget: int = None, history: list = None) -> str:
    """Always routes to Bedrock in this module."""
    return _bedrock(system, prompt, max_tokens, history=history)


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

    # Hallucinated column names on devices table
    _devices_bad = {
        "D.DEVICE_MODEL": "d.model",
        "D.DEVICE_BRAND": "d.brand",
        "D.DEVICE_OS":    "d.os",
        "D.PHONE_MODEL":  "d.model",
    }
    for bad, good in _devices_bad.items():
        if bad in sql_up:
            issues.append(
                f"SEMANTIC ERROR: devices table has no column '{bad.split('.')[1].lower()}'. "
                f"Use {good} instead. Real devices columns: msisdn, imei, brand, model, max_technology, "
                f"volte_capable, vowifi_capable, is_5g_capable, os, os_version."
            )

    # Hallucinated column names on subscribers table
    _subscribers_bad = {
        "S.SUBSCRIBER_TYPE":     "subscribers has no subscriber_type column. Real columns: msisdn, full_name, region, area_code, is_active, activation_date, age, gender.",
        "S.CUSTOMER_TYPE":       "subscribers has no customer_type column.",
        "S.PLAN_ID":             "subscribers has no plan_id column. Get plan via: JOIN op.subscriptions sub ON s.msisdn=sub.msisdn AND sub.is_current=1 JOIN op.plans p ON sub.plan_id=p.plan_id.",
        "S.PLAN_NAME":           "subscribers has no plan_name column. Get plan name via op.plans.",
        "S.ARPU":                "subscribers has no arpu column. ARPU is in op.customer_value.",
        "S.SUBSCRIPTION_TYPE":   "subscribers has no subscription_type column.",
    }
    for bad, msg in _subscribers_bad.items():
        if bad in sql_up:
            issues.append(f"SEMANTIC ERROR: {msg}")

    return "\n".join(issues) if issues else None


def _schema_hint_for_error(error: str, sql: str, db_path: str) -> str:
    """On a query error, extract the bad column/table, show real columns, and suggest alternatives."""
    hints = []
    try:
        conn = sqlite3.connect(db_path)
        if "ATTACH" in sql.upper() or " op." in sql:
            conn.execute(f"ATTACH DATABASE '{OP_DB}' AS op")

        # Find all tables referenced in the SQL
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
    "area_code":        {"T": "Ba Sing Se Metro", "N": "Water Tribe", "C": "Earth Kingdom",
                         "R": "Rural Interior", "S": "South"},
    "zone":             {"Earth Kingdom": "Earth Kingdom", "Fire Nation": "Fire Nation", "Water Tribe": "Water Tribe", "Air Nomads": "Air Nomads"},
}

def _diagnose_zero_rows(sql: str, db_path: str) -> str:
    """
    When a query returns 0 rows, progressively strip WHERE/HAVING/JOIN conditions
    to find which one is killing results. Returns a diagnostic hint string.
    """
    import re as _re
    try:
        needs_attach = "op." in sql or "operator_new" in sql
        conn = _get_conn(db_path, attach_op=needs_attach)

        # Strip ATTACH statement — _get_conn already handles it
        clean_sql = _re.sub(r'ATTACH\s+DATABASE\s+[^;]+;\s*', '', sql, flags=_re.IGNORECASE).strip()
        # Also strip CTEs for bare-table test — just use the final SELECT
        # Step 1 — strip everything down to base tables, no filters
        bare = _re.sub(r'\bWHERE\b.*', '', clean_sql, flags=_re.IGNORECASE | _re.DOTALL)
        bare = _re.sub(r'\bHAVING\b.*', '', bare, flags=_re.IGNORECASE | _re.DOTALL)
        bare = _re.sub(r'\bORDER\s+BY\b.*', '', bare, flags=_re.IGNORECASE | _re.DOTALL)
        bare = _re.sub(r'\bLIMIT\b.*', '', bare, flags=_re.IGNORECASE | _re.DOTALL)
        bare = bare.strip().rstrip(';') + " LIMIT 3"
        try:
            cur = conn.execute(bare)
            bare_rows = cur.fetchall()
            if not bare_rows:
                conn.close()
                return "Even the base query with no filters returns 0 rows — the JOIN itself is broken. Check table names, ATTACH prefix, and join columns exist in both tables."
        except Exception as e:
            conn.close()
            return f"Base query (no filters) failed with: {e}. Likely a bad table name or JOIN column."

        # Step 2 — find WHERE conditions and strip them one by one
        where_match = _re.search(r'\bWHERE\b(.*?)(?:\bGROUP\b|\bHAVING\b|\bORDER\b|\bLIMIT\b|$)',
                                  clean_sql, _re.IGNORECASE | _re.DOTALL)
        if not where_match:
            conn.close()
            return "Joins are valid (base query returns rows). No WHERE clause found to diagnose further."

        where_block = where_match.group(1).strip()
        # Split on AND (rough but effective for most agent-generated SQL)
        conditions = [c.strip() for c in _re.split(r'\bAND\b', where_block, flags=_re.IGNORECASE) if c.strip()]

        culprits = []
        for i, cond in enumerate(conditions):
            # Build query with this condition removed
            remaining = [c for j, c in enumerate(conditions) if j != i]
            test_where = " AND ".join(remaining)
            test_sql = _re.sub(
                r'\bWHERE\b.*?(?=\bGROUP\b|\bHAVING\b|\bORDER\b|\bLIMIT\b|$)',
                f"WHERE {test_where} " if test_where else "",
                clean_sql, flags=_re.IGNORECASE | _re.DOTALL, count=1
            ).strip().rstrip(';') + " LIMIT 3"
            try:
                rows = conn.execute(test_sql).fetchall()
                if rows:
                    culprits.append(cond)
            except Exception:
                pass

        conn.close()

        if culprits:
            cond_list = " | ".join(f'"{c}"' for c in culprits[:3])
            return (
                f"Joins are valid — rows exist without filters. "
                f"These WHERE conditions are too restrictive or incorrect: {cond_list}. "
                f"Check the actual values in those columns and rewrite the condition."
            )
        else:
            return (
                "Joins are valid but removing individual WHERE conditions didn't recover rows. "
                "Multiple conditions together are too restrictive, or a subquery returns no matching values. "
                "Try simplifying: remove all filters, confirm rows exist, then add conditions back one at a time."
            )
    except Exception as e:
        return f"Diagnostic failed ({e}). Check table names and join paths manually."


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

    # High-cardinality ID columns — never use as treemap dimensions
    _ID_COLS = {"msisdn", "imei", "imsi", "alarm_id", "cell_id", "site_id",
                "plan_id", "offer_id", "campaign_id", "sms_id", "incident_id"}

    # If data is subscriber-level (msisdn present), build a bar chart instead — treemap is unreadable
    if "msisdn" in keys:
        numeric_cols = [k for k in keys if isinstance(rows[0].get(k), (int, float))]
        # Prefer a score/risk/arpu column for the value
        val_col = next((k for k in numeric_cols if any(w in k for w in
                        ("churn", "risk", "score", "arpu", "revenue", "total"))), numeric_cols[0] if numeric_cols else None)
        cat_col = next((k for k in keys if k.lower() in
                        ("region", "city", "nation", "technology", "value_segment", "segment")), None)
        if val_col and cat_col:
            from collections import defaultdict
            grouped: dict = defaultdict(list)
            for r in rows:
                grouped[str(r[cat_col])].append(r[val_col] if r.get(val_col) is not None else 0)
            agg = {k: round(sum(v) / len(v), 3) for k, v in grouped.items()}
            sorted_items = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)[:15]
            return {
                "type": "bar",
                "title": f"Avg {val_col.replace('_',' ').title()} by {cat_col.replace('_',' ').title()}",
                "x": [i[0] for i in sorted_items],
                "y": [i[1] for i in sorted_items],
                "x_label": cat_col.replace("_", " ").title(),
                "y_label": val_col.replace("_", " ").title(),
            }
        return None  # subscriber-level data can't make a useful treemap

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

    # Filter out ID columns and extra numeric columns — only categorical dims make sense
    dim_keys = [k for k in keys if k != count_key
                and k.lower() not in _ID_COLS
                and not isinstance(rows[0].get(k), (int, float))]
    if not dim_keys:
        return None
    dim_keys = dim_keys[:3]  # cap at 3 levels — deeper trees are unreadable

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
    # Find label column — prefer region/city/technology/segment over msisdn/id columns
    _HIGH_CARDINALITY = {"msisdn", "imei", "imsi", "alarm_id", "cell_id", "site_id", "plan_id"}
    _GOOD_LABELS = ["region", "city", "technology", "nation", "zone", "area_code",
                    "value_segment", "segment", "plan_type", "alarm_type", "brand", "os",
                    "congestion_level", "experience_label", "mobility_class", "churn_label"]
    label_col = next((k for k in keys if k.lower() in _GOOD_LABELS), None)
    if label_col is None:
        label_col = next((k for k in keys if isinstance(rows[0][k], str) and k.lower() not in _HIGH_CARDINALITY), None)

    numeric_cols = [k for k in keys if isinstance(rows[0].get(k), (int, float))]
    if not label_col or not numeric_cols:
        return None

    q_lower = question.lower()
    if any(w in q_lower for w in ("revenue", "arpu", "total")):
        val_col = next((k for k in numeric_cols if any(w in k for w in ("revenue", "total", "arpu"))), numeric_cols[-1])
        y_label = "Yuan"
    elif any(w in q_lower for w in ("churn", "risk", "score")):
        val_col = next((k for k in numeric_cols if any(w in k for w in ("churn", "risk", "score"))), numeric_cols[0])
        y_label = "Churn Risk Score"
    else:
        val_col = next((k for k in numeric_cols if any(w in k for w in ("count", "n", "total", "at_risk"))), numeric_cols[0])
        y_label = "Count"

    # If label_col is a grouping dimension — aggregate rows by it
    from collections import defaultdict
    grouped: dict = defaultdict(list)
    for r in rows:
        grouped[str(r[label_col])].append(r[val_col] if r.get(val_col) is not None else 0)
    agg = {k: round(sum(v) / len(v), 3) if "score" in val_col or "rate" in val_col or "pct" in val_col else sum(v)
           for k, v in grouped.items()}

    # Sort by value descending, cap at 20 bars
    sorted_items = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)[:20]
    x = [item[0] for item in sorted_items]
    y = [item[1] for item in sorted_items]

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
- All monetary values (ARPU, prices, revenue) are in Yuan (Yuan) — never use $ or USD or TND.
- Write SQL on a single line with no line breaks.
- Do not use markdown backticks around SQL.
- NEVER output QUERY_SC or QUERY_OP tags inside a CONCLUDE or PROPOSE block.
- NEVER use PROPOSE for analysis questions — use CONCLUDE for those.
- Use correct campaign types: 5G_upsell, 3G_migration, FWA, VoLTE_sunset, HVC_upsell.
- In JSON output, never use commas in numbers. Write 13532 not 13,532.
- Before writing the "text" field, verify all arithmetic: if your query returns grouped counts, the "remaining" group is total minus the sum of all other groups. Never reuse the total as a subgroup count.

OUTPUT FORMAT — When writing CONCLUDE, output valid JSON on a single line. Field order MUST be: text, recommendations, strategy_diagram, mindmap, chart — in that exact order:
{"text": "your analysis here", "recommendations": ["action 1", "action 2", "action 3"], "strategy_diagram": {"title": "Migration Strategy", "segments": [{"label": "3G Non-VoLTE", "count": 8658, "color": "red", "strategy": "4G Terminal Upgrade"}, {"label": "3G VoLTE-capable", "count": 3765, "color": "amber", "strategy": "VoLTE Migration"}], "strategies": [{"label": "4G Terminal Upgrade", "tier": "4G", "color": "amber", "actions": ["Subsidized device swap", "Bundle package offer"]}, {"label": "VoLTE Migration", "tier": "4G", "color": "purple", "actions": ["SMS activation campaign", "3-month data bonus"]}]}, "mindmap": {"center": "3G Sunset Strategy", "nodes": [{"id": "n1", "label": "3G-Only Devices", "value": "8641 subs", "color": "red", "type": "data", "children": [{"id": "n1a", "label": "No 4G capability", "type": "data", "color": "red"}, {"id": "n1b", "label": "Subsidized device swap", "type": "suggestion", "color": "red"}, {"id": "n1c", "label": "Targeted SMS alerts", "type": "suggestion", "color": "red"}]}, {"id": "n2", "label": "VoLTE-Capable", "value": "3711 subs", "color": "amber", "type": "data", "children": [{"id": "n2a", "label": "Has 4G device", "type": "data", "color": "amber"}, {"id": "n2b", "label": "Activate VoLTE via app", "type": "suggestion", "color": "amber"}]}]}, "chart": {"type": "bar", "title": "Chart Title", "x": ["A","B","C"], "y": [1,2,3], "x_label": "Category", "y_label": "Value"}}

The "strategy_diagram" field: include whenever your results contain subscriber segments, migration candidates, technology breakdowns, or commercial groupings. Also include for churn, HVC, upsell, FWA, and retention analysis. Omit only for plain KPI or network infrastructure queries (latency, throughput, cell counts).
- "segments": the subscriber populations from your data — use real counts from your query results. "color": one of "red", "amber", "blue", "purple", "green", "teal". "strategy": must exactly match a label in "strategies".
- "strategies": the actions for each segment. "tier": the target technology tier ("2G","3G","4G","5G","FWA"). "color": same palette. "actions": 2-3 specific actions the operator should take.

The "mindmap" field: a hierarchical interactive mind map. Include for any subscriber segment / migration / commercial query where you also include strategy_diagram.
- "center": the root topic (3-5 words, e.g. "3G Sunset Strategy").
- "nodes": array of branch objects. Each branch: {"id": unique string, "label": short name, "value": key metric e.g. "8641 subs", "color": one of the palette colors, "type": "data", "children": [...]}.
  - Children can be nested as deep as needed to reflect the real data hierarchy (e.g. technology tier → region → sub-segment).
  - Each child: {"id": unique, "label": short text, "type": "data" or "suggestion", "color": inherited or different}.
  - "type": "suggestion" — use this for any child node where you are adding a commercial recommendation or action. Suggestion nodes get a distinct visual style. Place them at whatever branch level makes sense — they don't all have to be at the leaves.
  - Keep all labels under 35 characters. Values (counts, percentages) go in the "value" field, not the label.

The "text" field must be SHORT and conversational — 3 to 5 sentences maximum. Lead with the key finding and numbers. ABSOLUTELY NO bullet points, headers, markdown formatting, tables, or code blocks in the text field — ever. This rule applies even when queries fail or return 0 rows. If data is insufficient, say so in plain sentences: "The query returned no results — likely because X. Try rephrasing as Y." Write like a smart analyst talking to a manager. Example: "Ba Sing Se has the most HVCs at risk (238 customers, avg 94 Yuan ARPU). Omashu and Northern Water Tribe follow with 37 and 46 at-risk customers respectively. Total exposure across all three regions is roughly 30k Yuan/month."
The "recommendations" field: ask yourself — does this data reveal an opportunity, a risk, or an actionable gap? If yes, include 2-3 recommendations. If the question is purely factual with no commercial angle (e.g. "what is the average latency?"), omit the field entirely or set it to [].
When recommendations ARE warranted, write like a senior analyst advising a commercial director. Each recommendation must be backed by a number from your query results. NEVER invent percentages, revenue figures, or durations.

BEFORE writing CONCLUDE — if you have only 1 query step so far and the question has a commercial angle, you MUST run 1 enrichment query before concluding. Match it to the context:
- Top ARPU / platinum subscribers → check what plan they're on (are they already on the highest tier?) or whether they have 5G-capable devices not yet on 5G
- Low ARPU / bronze subscribers → check churn_risk_score to identify who is actually at risk of leaving
- Poor KPI result (low throughput, high drop rate, low availability) → check active alarm count on those cells to see if it's a known fault
- 3G subscriber count → check how many of them have 4G/5G-capable devices to quantify the migration opportunity
- 5G upsell candidates → check their avg ARPU to estimate revenue impact of converting them
- Network alarm result → check which region or technology has the most active critical alarms
The goal is one extra number that turns a vague recommendation into a specific one. Do NOT enrich if: the question is purely a count with no commercial angle, you already have 4+ steps of data, or the enrichment would duplicate what you already queried.

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

def _compute_composite_score(question: str, step_results: list[list[dict]]) -> list[dict] | None:
    """Merge multiple query result sets on msisdn and compute composite score via LLM."""
    if not step_results or all(not r for r in step_results):
        return None
    data_block = ""
    for i, rows in enumerate(step_results):
        if rows and "error" not in rows[0]:
            cap = 30
            sample = rows[:cap]
            data_block += f"\nDataset {i+1} ({len(rows)} rows, showing {len(sample)}):\n"
            data_block += "[" + ", ".join(str(dict(r)) for r in sample) + "]"
    if not data_block:
        return None
    score_prompt = (
        f"Original question: {question}\n\n"
        f"Raw data collected from multiple queries:\n{data_block}\n\n"
        f"Merge these datasets on msisdn. Compute the composite score exactly as specified in the question. "
        f"Return ONLY a valid JSON array of the top 20 subscribers sorted by score descending. "
        f"Each object must have: msisdn, region, plan_name, arpu, risk_score, and retention_offer. "
        f"Output ONLY the JSON array. No explanation, no markdown."
    )
    print("[Scoring] Computing composite score via LLM...")
    resp = _llm("You are a data analyst. Output only valid JSON. No explanation.", score_prompt, max_tokens=2000, allow_thinking=False)
    match = re.search(r'\[.*\]', resp, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    return None


# ── Grounded recommendation context ─────────────────────────────────────
_ALARM_KEYWORDS = ("alarm", "alarms", "congestion", "coverage hole", "power issue",
                   "outage", "incident", "fault", "degradation", "downtime")

def _is_alarm_question(question: str) -> bool:
    return any(k in question.lower() for k in _ALARM_KEYWORDS)

def _fetch_alarm_context() -> str:
    """Pull real supporting numbers for active alarms and incidents: affected subscribers, ARPU, churn risk."""
    try:
        # Active alarms with subscriber impact
        alarm_sql = """
            SELECT
                a.alarm_type,
                a.severity,
                a.cell_id,
                a.description,
                si.region,
                si.city,
                c.technology                                         AS cell_technology,
                COUNT(DISTINCT st.msisdn)                            AS affected_subs,
                ROUND(AVG(cv.arpu), 2)                               AS avg_arpu,
                SUM(CASE WHEN cv.churn_label = 1 THEN 1 ELSE 0 END) AS high_risk_count,
                ROUND(AVG(cv.churn_risk_score), 3)                   AS avg_churn_score
            FROM network_alarms a
            LEFT JOIN cells c ON c.cell_id = a.cell_id
            LEFT JOIN sites si ON si.site_id = c.site_id
            LEFT JOIN subscriber_technology st ON st.current_cell_id = a.cell_id
            LEFT JOIN op.customer_value cv
                ON cv.msisdn = st.msisdn
                AND cv.month = (SELECT MAX(month) FROM op.customer_value)
            WHERE a.is_active = 1
            GROUP BY a.alarm_id, a.alarm_type, a.severity, a.cell_id
            ORDER BY CASE a.severity WHEN 'critical' THEN 1 WHEN 'major' THEN 2 ELSE 3 END
        """
        # Unresolved incidents (have direct affected_users count)
        incident_sql = """
            SELECT incident_type, severity, cell_id, affected_users, root_cause
            FROM network_incidents
            WHERE resolved = 0
            ORDER BY CASE severity WHEN 'critical' THEN 1 WHEN 'major' THEN 2 ELSE 3 END
            LIMIT 10
        """
        alarm_rows    = query_sc(alarm_sql)
        incident_rows = query_sc(incident_sql)

        lines = []
        if alarm_rows and "error" not in alarm_rows[0]:
            for r in alarm_rows:
                location = ", ".join(filter(None, [r.get('city'), r.get('region')]))
                lines.append(
                    f"- [ALARM/{r.get('severity','?').upper()}] {r.get('alarm_type','?')} "
                    f"(cell {r.get('cell_id','?')}, {r.get('cell_technology','?')}, {location}): "
                    f"{r.get('affected_subs', 0)} affected subscribers, "
                    f"avg ARPU {r.get('avg_arpu', 'N/A')} TND, "
                    f"{r.get('high_risk_count', 0)} high-churn-risk, "
                    f"avg churn score {r.get('avg_churn_score', 'N/A')}"
                )
        if incident_rows and "error" not in incident_rows[0]:
            for r in incident_rows:
                lines.append(
                    f"- [INCIDENT/{r.get('severity','?').upper()}] {r.get('incident_type','?')} "
                    f"(cell {r.get('cell_id','?')}): "
                    f"{r.get('affected_users', 0)} directly affected users, "
                    f"root cause: {r.get('root_cause','unknown')}"
                )
        if not lines:
            return ""
        return (
            "GROUNDED ALARM/INCIDENT DATA (use ONLY these real numbers — "
            "never invent percentages, dollar figures, or durations not present here):\n"
            + "\n".join(lines)
        )
    except Exception as e:
        print(f"[AlarmContext] {e}")
        return ""


def _run_chain(question: str, max_steps: int = 8, _resume_context: str = None, _resume_steps: list = None) -> dict:
    # ── Semantic cache check ─────────────────────────────────────────────
    if not _resume_context:
        _cached = _cache_lookup(question)
        if _cached:
            return _cached

    # ── Intent classification ────────────────────────────────────────────
    _intent = classify_intent(question)
    print(f"[Intent] {_intent} — '{question[:60]}'")

    # ── Schema graph context injection ───────────────────────────────────
    _graph_ctx = retrieve_graph_context(question)

    # ── RAG knowledge ────────────────────────────────────────────────────
    rag_context = ""
    if _should_use_rag(question):
        rag_context = retrieve(question, top_k=3)
        rag_block = f"\n\nTELECOM CONTEXT (use for recommendations):\n{rag_context}"
    else:
        rag_block = ""

    from datetime import date as _date
    system = AGENT_SYSTEM\
        .replace("{rag_context}", rag_block)\
        .replace("{schema}", FULL_SCHEMA_BEDROCK)
    system = f"Today's date: {_date.today().isoformat()}. Only reference dates and deadlines that appear in query results — never invent them.\n\n" + system

    # Inject schema graph context (join paths, constraints, wrong-column warnings)
    if _graph_ctx:
        system = system + f"\n\n{_graph_ctx}"

    sql_examples = retrieve_sql(question, top_k=3)
    sql_hint = f"\nCRITICAL — COPY THIS SQL PATTERN EXACTLY (same tables, same columns — only adjust filters/LIMIT):\n{sql_examples}\n" if sql_examples else ""

    # Semantic schema hint — inject top relevant columns before the chain starts
    _sem_hint = _semantic_schema_hint(question)
    if _sem_hint:
        system = system + f"\n\n{_sem_hint}"

    # Alarm grounding — inject real subscriber/ARPU/churn data for alarm-related questions
    if _intent == "alarm" or _is_alarm_question(question):
        _alarm_ctx = _fetch_alarm_context()
        if _alarm_ctx:
            system = system + f"\n\n{_alarm_ctx}"

    # Intent-specific DB routing hint
    if _intent == "commercial":
        rag_block = rag_block or ""
        system = system + "\n\nINTENT: Commercial query — prefer QUERY_OP for ARPU/HVC/churn/billing. Use ATTACH only if region/network data also needed."
    elif _intent == "network_kpi":
        system = system + "\n\nINTENT: Network KPI query — use QUERY_SC. Remember kpis_daily→cells→sites join path for region breakdown."
    elif _intent == "subscriber_tech":
        system = system + "\n\nINTENT: Subscriber technology query — use QUERY_SC with subscriber_technology. Cover device capability (devices) AND coverage check when doing upsell."

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
    _step_results: list[list[dict]] = []   # raw rows per step for composite scoring
    _last_sql: str = None                  # last executed SQL (for export)

    # Detect composite scoring questions
    _SCORE_KEYWORDS = ("score", "rank", "composite", "weighted", "weight", "prioriti", "risk score", "top 20", "top 10")
    is_scoring = any(w in q_lower for w in _SCORE_KEYWORDS)

    # Silent viz follow-ups (Mind Map / Strategy Diagram buttons): reuse the prior
    # analysis, do NOT run a new query — so the "must query before CONCLUDE" guards
    # must be skipped or these requests loop and leak raw CONCLUDE JSON as text.
    is_viz_followup = ("analysis above" in q_lower) and any(
        w in q_lower for w in ("mind map", "mindmap", "strategy_diagram", "strategy diagram"))

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
        # Bedrock 16k token limit — trim accumulated context to ~6000 chars max
        _ctx_display = context[-6000:] if len(context) > 6000 else context
        if context:
            queries_done = context.count("Step ")
            if queries_done >= 6:
                if is_treemap:
                    prompt = (
                        f"Question: {question}\n\n"
                        f"Data gathered:\n{_ctx_display}\n\n"
                        f"You have sufficient data. Output: CONCLUDE: {{\"text\": \"your analysis\"}}"
                    )
                else:
                    prompt = (
                        f"Question: {question}\n\n"
                        f"Data gathered:\n{_ctx_display}\n\n"
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
                _scoring_ready = not is_scoring or len(_step_results) >= 2
                conclude_push = (
                    f"\nYour latest query returned {last_nonzero} rows of valid data. "
                    f"Base your conclusion ONLY on that data — ignore earlier 0-row results and schema issues. "
                    f"Do NOT write recommendations, next steps, or schema fixes. "
                    f"Output a 2-3 sentence factual summary of what the data shows. "
                    f"Output CONCLUDE now as a single-line JSON: "
                    f'CONCLUDE: {{"text": "your 2-3 sentence factual summary", "chart": {{...}}}}'
                ) if has_data and _scoring_ready else (
                    f"\nScoring question: collect data from {2 - len(_step_results)} more query step(s) before concluding. "
                    f"Keep running QUERY steps to gather all needed metrics.\n"
                ) if is_scoring and has_data else ""
                prompt = (
                    f"Question: {question}\n\n"
                    f"Data gathered so far:\n{_ctx_display}\n\n"
                    f"Next step — output QUERY_SC, QUERY_OP, "
                    f"QUERY_BOTH, CONCLUDE, or PROPOSE:"
                    f"{treemap_remind}{conclude_push}"
                )
        else:
            # Detect operator-only questions — steer away from QUERY_BOTH
            _op_strong = ["arpu","billing","customer value","hvc","high value","churn","revenue","payment"]
            _op_keywords = ["plan","subscription","offer","campaign","segment"]
            _sc_keywords = ["kpi","throughput","alarm","cell","site","coverage","signal","technology",
                            "device","mobility","fwa","volte","network","drop rate",
                            "3g","4g","5g","2g","sunset","migration","upsell","usage",
                            "data usage","qoe","experience","roaming","activation"]
            _q = question.lower()
            # Strong op keywords always force QUERY_OP (even if "subscriber" in question)
            _has_strong_op = any(k in _q for k in _op_strong)
            _has_sc = any(k in _q for k in _sc_keywords)
            _is_op_only = _has_strong_op and not _has_sc or (
                any(k in _q for k in _op_keywords) and not _has_sc and "subscriber" not in _q
            )
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
            if is_scoring:
                _complex_hint = (
                    "This is a COMPOSITE SCORING question. Do NOT try to compute the score in SQL.\n"
                    "Instead, run ONE SIMPLE QUERY PER METRIC to collect raw data:\n"
                    "  Step 1: usage data — msisdn + usage_now + usage_3mo_ago from dou_monthly (self-join or CTE, NO LAG). LIMIT 200 ordered by usage drop DESC.\n"
                    "  Step 2: billing — msisdn + has_unpaid (1/0) from Operator billing table\n"
                    "  Step 3: signal quality — cell_id + avg signal vs overall average from kpis_daily.\n"
                    "  Step 4: plan cost — msisdn + plan monthly_price + regional avg price from Operator\n"
                    "IMPORTANT: Each query LIMIT 200 max. No window functions over ATTACH.\n"
                    "The system merges and scores automatically. Start with Step 1 now.\n"
                )
            else:
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

        # If the question asks for suggestions/improvements, remind the model to include them in CONCLUDE
        _max_tok = 800 if (is_treemap and _is_conclude_step) else (3600 if _is_conclude_step else 1200)
        _think_bud = 8000 if _is_conclude_step else None  # deep reasoning on conclude, none on SQL steps
        _hist = _conv_history() if _is_conclude_step else None  # full history only on conclude
        response = _llm(system, prompt, max_tokens=_max_tok, timeout=420, think_budget=_think_bud, history=_hist)

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
            # Silent viz follow-ups (Mind Map / Strategy Diagram) intentionally reuse the
            # prior analysis and must NOT be forced to query — skip the guard for them.
            queries_executed = context.count("Step ")
            if queries_executed == 0 and not is_viz_followup:
                context += f"\nStep {step+1}: CONCLUDE rejected — no data queried yet. Run a QUERY_SC or QUERY_OP first.\n"
                continue

            # Reject CONCLUDE when all queries errored — model must retry with corrected query
            successful_results = len(re.findall(r'Results \(\d+ rows\)', context))
            error_count = context.count("Query error:") + context.count("semantic error:") + context.count("SQL error:")
            if successful_results == 0 and error_count > 0:
                # Extract the schema correction hint if available
                corr_match = re.search(r'SCHEMA CORRECTION: (.+)', context)
                corr_hint = corr_match.group(1) if corr_match else ""
                # Also check if error already names the correct table
                err_match = re.search(r"'(\w+)' exists on: ([\w, ]+)", context)
                alt_hint = f"Use table '{err_match.group(2).strip()}' instead." if err_match else ""
                context += (
                    f"\n[SYSTEM] CONCLUDE BLOCKED — you have 0 successful results and {error_count} error(s). "
                    f"You MUST NOT output CONCLUDE yet. "
                    f"{corr_hint or alt_hint} "
                    f"Output ONLY: QUERY_SC: <corrected SQL using the right table>\n"
                )
                continue

            # ── Composite scoring intercept ────────────────────────
            if is_scoring and len(_step_results) >= 2:
                print(f"[Scoring] Collected {len(_step_results)} result sets — running composite score merge")
                scored = _compute_composite_score(question, _step_results)
                if scored:
                    scored_json = json.dumps(scored, indent=2)
                    context += (
                        f"\nComposite scoring complete. Top-20 ranked subscribers:\n{scored_json}\n"
                        f"Now output: CONCLUDE: {{\"text\": \"<3-sentence analyst summary citing top msisdns and scores>\", "
                        f"\"chart\": {{\"type\": \"bar\", \"title\": \"Top Subscribers by Score\", "
                        f"\"x\": [msisdn as strings], \"y\": [score as numbers], \"x_label\": \"Subscriber\", \"y_label\": \"Score\"}}}}\n"
                    )
                    is_scoring = False
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

            _log.info(f"[CONCLUDE RAW] {conclusion[:2000]}")

            chart_spec        = None
            text_only         = conclusion
            _recs             = []
            _strategy_diagram = None
            _mindmap          = None
            # Ask the model to write a row-level export SQL if the question involves individual entities
            _extract_sql = None
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
                    max_tokens=600,
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
                    raw = re.sub(r'\s+', ' ', raw)  # collapse multi-line JSON to single line
                    try:
                        parsed    = json.loads(raw)
                        text_only = parsed.get("text", "")
                        # Sanitize: if text is a markdown essay, flatten it to plain sentences
                        _md_signals = (
                            text_only.count("###") +
                            text_only.count("**") // 2 +
                            text_only.count("| ") +
                            text_only.count("```") +
                            text_only.count("\n-") * 2
                        )
                        if _md_signals >= 3 or len(text_only) > 1200:
                            _log.info(f"[TextSanitize] markdown detected (signals={_md_signals}, len={len(text_only)}) — rewriting")
                            _plain = _llm(
                                "You are a telecom analyst. Rewrite the following analysis as 3-5 plain sentences. "
                                "No bullet points, no headers, no markdown, no tables. Lead with the key finding and numbers. "
                                "Keep all specific numbers and region names.",
                                text_only, max_tokens=300, allow_thinking=False
                            )
                            if _plain and not _plain.startswith("ERROR"):
                                text_only = _plain.strip()
                        raw_recs          = parsed.get("recommendations", [])
                        _recs             = [r if isinstance(r, str) else (r.get("text") or r.get("recommendation") or r.get("action") or str(r)) for r in raw_recs]
                        _strategy_diagram = parsed.get("strategy_diagram", None)
                        _mindmap          = parsed.get("mindmap", None)
                        _log.info(f"[CONCLUDE PARSED] keys={list(parsed.keys())} strategy_diagram={'yes' if _strategy_diagram else 'no'} mindmap={'yes' if _mindmap else 'no'} chart={'yes' if parsed.get('chart') else 'no'} recs={len(_recs)}")
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

            # Hard safety — strip any leaked action tag prefixes
            for _pfx in ["CONCLUDE:", "PROPOSE:", "QUERY_SC:", "QUERY_OP:", "QUERY_BOTH:"]:
                if text_only.startswith(_pfx):
                    text_only = text_only[len(_pfx):].strip()

            # Normalize currency — model sometimes writes $ or TND instead of Yuan
            text_only = re.sub(r'\$\s*([\d,\.]+)', r'\1 Yuan', text_only)
            text_only = re.sub(r'([\d,\.]+)\s*TND', r'\1 Yuan', text_only)

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
                _grounding = _fetch_alarm_context() if _is_alarm_question(question) else ""
                _grounding_block = f"\n\n{_grounding}" if _grounding else ""
                rec_raw = _llm(
                    "You are a senior telecom commercial analyst. Based on the analysis below, give exactly 3 recommendations. "
                    "Each recommendation must explain what to do, why the data justifies it, and the expected business impact. "
                    "IMPORTANT: Use ONLY numbers that appear in the analysis or grounded data — never invent percentages, dollar amounts, or durations. "
                    f"Today's date: {__import__('datetime').date.today().isoformat()}. Only reference dates from the analysis — never invent deadlines. "
                    "Output a JSON array of exactly 3 strings, no other text. Example format (do not copy this): [\"Prioritize X because Y\", \"Launch Z targeting W\", \"Investigate A which shows B\"]",
                    f"Question: {question}\n\nAnalysis: {text_only}{_grounding_block}",
                    max_tokens=800,
                    think_budget=5000,
                    allow_thinking=True
                )
                try:
                    arr_match = re.search(r'\[.*\]', rec_raw, re.DOTALL)
                    if arr_match:
                        raw_recs = json.loads(arr_match.group())
                        _recs = [r if isinstance(r, str) else (r.get("text") or r.get("recommendation") or r.get("action") or str(r)) for r in raw_recs]
                except Exception:
                    pass

            _result = {
                "type":              "analysis",
                "text":              text_only,
                "recommendations":   _recs,
                "extract_sql":       _extract_sql,
                "chart":             chart_spec,
                "strategy_diagram":  _strategy_diagram,
                "mindmap":           _mindmap,
                "steps":             steps_log
            }
            _cache_store(question, _result)
            return _result

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
            _schema_rows = 1 if "not found" not in schema_result else 0
            if _streaming_queue:
                _streaming_queue.put({"type": "step_sql", "step": step+1,
                    "tag": "SCHEMA", "sql": tbl, "rows": _schema_rows})
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
                            context += f"\nStep {step+1}: {sql_err}\n"
                            executed = True
                            break

                        results = runner(sql)
                        if _streaming_queue:
                            _is_err = bool(results and "error" in results[0])
                            _streaming_queue.put({"type": "step_sql", "step": step+1,
                                "tag": tag.rstrip(":"), "sql": sql[:200],
                                "rows": 0 if _is_err else (len(results) if results else 0),
                                "error": results[0]["error"] if _is_err else None})
                        if results and "error" not in results[0]:
                            n = len(results)
                            # Collect raw results for composite scoring
                            if is_scoring:
                                _step_results.append(results[:200])
                            if is_treemap:
                                _treemap_cap = 200
                                sample = results[:_treemap_cap]
                                compact = ", ".join(str(dict(r)) for r in sample)
                                snippet = f"[{compact}]" + (f" (showing {_treemap_cap} of {n})" if n > _treemap_cap else "")
                            elif is_scoring:
                                # Scoring steps: keep context lean — just column names + row count
                                cols = list(results[0].keys()) if results else []
                                snippet = f"columns={cols}, {n} rows collected for scoring"
                            else:
                                _row_cap = 20
                                if n <= _row_cap:
                                    snippet = json.dumps(results, indent=2)
                                else:
                                    compact = ", ".join(str(dict(r)) for r in results[:_row_cap])
                                    snippet = f"[{compact}] (showing {_row_cap} of {n} rows)"
                            context += (
                                f"\nStep {step+1} [{tag.rstrip(':')}]:\n"
                                f"SQL: {sql[:120]}\n"
                                f"Results ({n} rows): {snippet}\n"
                            )
                            # ── Uniformity check — flag 100% same-bucket CASE WHEN results ──
                            # Skip columns that are filter values (intentionally uniform due to WHERE clause)
                            _where_vals = set(re.findall(r"='([^']+)'", sql) + re.findall(r'=(\d+)', sql))
                            if n > 5 and results:
                                for col in results[0].keys():
                                    vals = [r.get(col) for r in results if r.get(col) is not None]
                                    if vals and len(set(vals)) == 1:
                                        # Skip if this uniform value is a WHERE filter value (expected uniformity)
                                        if str(vals[0]) in _where_vals:
                                            continue
                                        context += (
                                            f"\n[SYSTEM WARNING] Column '{col}' has the same value '{vals[0]}' "
                                            f"for ALL {n} rows. This is almost certainly a logic error in the SQL. "
                                            f"Re-examine the CASE WHEN or WHERE conditions — a correct query should "
                                            f"produce a distribution of values, not 100% in one bucket. "
                                            f"Rewrite and re-run.\n"
                                        )
                                        _log.warning(f"[UniformityCheck] col={col} val={vals[0]} n={n} — injecting warning")

                            # ── Self-check (Bedrock only — fast enough to run extra call) ──
                            if n > 0 and step == 0 and not is_scoring:
                                check_prompt = (
                                    f"Question: {question}\n\n"
                                    f"SQL run: {sql}\n"
                                    f"Returned {n} rows.\n\n"
                                    f"List every condition in the question. "
                                    f"For each condition, say YES or NO whether the SQL enforces it. "
                                    f"If any condition is missing, output: MISSING: <rewritten SQL that enforces ALL conditions>. "
                                    f"If all conditions are present, output: OK"
                                )
                                check_resp = _llm(
                                    "You are a SQL reviewer. Be concise. Only output OK or MISSING: <sql>.",
                                    check_prompt, max_tokens=400, allow_thinking=False
                                )
                                if "MISSING:" in check_resp:
                                    fixed_sql = check_resp.split("MISSING:", 1)[1].strip()
                                    fixed_sql = re.sub(r"```sql|```", "", fixed_sql).strip()
                                    context += f"Self-check: query was incomplete. Corrected SQL: {fixed_sql}\nRe-run with the corrected SQL above.\n"
                                    print(f"[SelfCheck] Incomplete query detected, injecting correction")
                        elif results and "error" in results[0]:
                            err_msg = results[0]['error']
                            context += f"\nStep {step+1}: Query error: {err_msg}\n"
                            # Parse column error — if column exists on exactly one other table, mandate retry
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
                            # 0 rows — run diagnostic to find the culprit condition/join
                            diag = _diagnose_zero_rows(sql, db_path)
                            context += (
                                f"\nStep {step+1}: Query returned 0 rows.\n"
                                f"[DIAGNOSTIC] {diag}\n"
                                f"Fix the identified issue and rewrite the query.\n"
                            )
                            _log.info(f"[ZeroRowDiag] {diag[:120]}")
                        executed = True
                        break
                if executed:
                    break

        if not executed:
            # Model output plain text without an action tag.
            # Try to salvage raw SQL from the response (model forgot the tag) and run it.
            _raw_sql_match = re.search(r'```sql\s*(SELECT.+?)```', response, re.I | re.DOTALL)
            if not _raw_sql_match:
                _raw_sql_match = re.search(r'(SELECT\s+\S.+)', response, re.I)
            if _raw_sql_match:
                _salvaged_sql = re.sub(r'\s+', ' ', _raw_sql_match.group(1)).strip().rstrip(';').rstrip('`')
                _op_tables = {"customer_value","customers","subscriptions","plans","billing",
                              "offers","campaigns","campaign_targets","sms_log","offer_assignments"}
                _salvaged_tables = set(re.findall(r'\b(\w+)\b', _salvaged_sql.lower()))
                _inferred_tag = "QUERY_OP:" if _salvaged_tables & _op_tables else "QUERY_SC:"
                _inferred_runner = query_op if _inferred_tag == "QUERY_OP:" else query_sc
                print(f"[auto-tag] salvaged SQL as {_inferred_tag}: {_salvaged_sql[:120]}")
                _rows = _inferred_runner(_salvaged_sql)
                _is_err = bool(_rows and "error" in _rows[0])
                if not _is_err:
                    _step_results.append(_rows)
                _rows_text = json.dumps(_rows[:50], default=str)
                context += f"\nStep {step+1} [{_inferred_tag.rstrip(':')}]: {_salvaged_sql}\nResults ({len(_rows)} rows): {_rows_text}\n"
                _last_sql = _salvaged_sql
                steps_log.append(f"[auto-tag {_inferred_tag}] {_salvaged_sql[:120]}")
                if _streaming_queue:
                    _streaming_queue.put({"type": "step_sql", "step": step+1,
                        "tag": _inferred_tag.rstrip(':'), "sql": _salvaged_sql[:200],
                        "rows": 0 if _is_err else len(_rows),
                        "error": _rows[0].get("error") if _is_err else None})
            else:
                context += f"\nStep {step+1}: No query executed. Output ONLY one of: QUERY_SC: / QUERY_OP: / CONCLUDE: followed by SQL or JSON on a single line.\n"

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
Average 4G throughput in the Fire Nation Capital:
SELECT ROUND(AVG(k.dl_throughput_mbps),2) as avg_dl FROM kpis_daily k JOIN cells c ON k.cell_id=c.cell_id JOIN sites s ON c.site_id=s.site_id WHERE s.region='Fire Nation Capital' AND c.technology='4G' AND k.date=(SELECT MAX(date) FROM kpis_daily)

Active critical alarms:
SELECT COUNT(*) as n FROM network_alarms WHERE severity='critical' AND is_active=1

Subscribers on 3G in Ba Sing Se:
SELECT COUNT(*) as n FROM subscribers s JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE st.current_technology='3G' AND s.region='Ba Sing Se'
"""

    sql_raw = _llm(sql_system, f"Question: {question}\nSQL:", max_tokens=200)
    sql = sql_raw.strip().strip("```sql").strip("```").strip()

    # FIX 4: ATTACH queries must always go to query_sc(), check this FIRST
    _fast_tag = "QUERY_SC"
    if "ATTACH" in sql.upper() or "op." in sql:
        results = query_sc(sql)
        _fast_tag = "QUERY_BOTH"
    else:
        op_keywords = ["plan", "customer", "billing", "arpu", "offer", "campaign",
                       "segment", "subscription", "revenue", "payment"]
        use_op = any(k in question.lower() for k in op_keywords)
        # Semantic vote overrides keyword heuristic when confident
        _db_vote = _semantic_db_vote(question)
        if _db_vote == "op":
            use_op = True
        elif _db_vote == "sc":
            use_op = False
        if use_op:
            results = query_op(sql)
            _fast_tag = "QUERY_OP"
        else:
            results = query_sc(sql)

    if _streaming_queue:
        _is_err = bool(results and "error" in results[0])
        _streaming_queue.put({"type": "step_sql", "step": 1,
            "tag": _fast_tag, "sql": sql[:200],
            "rows": 0 if _is_err else len(results),
            "error": results[0]["error"] if _is_err else None})

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
{{"action": "bulk_acknowledge", "region": "Fire Nation Capital", "severity": "critical"}}
{{"action": "create_offer", "name": "...", "campaign_type": "5G_upsell", "target_technology": "5G", "discount_pct": 20, "bonus_data_gb": 10, "price_override": null, "validity_days": 30, "description": "..."}}

Output ONLY the JSON object. No explanation. No markdown.

Examples:
{{"action": "assign_offer", "offer_id": 1, "msisdn_filter_sql": "SELECT msisdn FROM subscribers s JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE s.region='Ba Sing Se' AND st.current_technology='3G'"}}
{{"action": "create_campaign", "name": "FWA Conversion Ba Sing Se", "type": "FWA", "offer_id": 5, "msisdn_filter_sql": "SELECT mp.msisdn FROM mobility_profile mp JOIN dou_monthly dm ON mp.msisdn=dm.msisdn AND mp.month=dm.month WHERE mp.mobility_class='stationary' AND dm.total_data_gb>30 AND mp.month=(SELECT MAX(month) FROM mobility_profile)"}}
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

_memory           = []       # compressed summaries for chitchat/intent context
_conv_messages    = []       # proper Bedrock multi-turn messages [{role, content}]
_MAX_CONV_TURNS   = 10       # keep last 10 user/assistant exchanges (20 messages)
_pending          = None
_partial_state    = None  # saved context when chain exits without CONCLUDE
_streaming_queue  = None
_stop_event       = None   # threading.Event — set to abort generation

# ── Semantic Cache ────────────────────────────────────────────────────────
# Caches recent agent results keyed by question embedding.
# Hit threshold: cosine similarity >= 0.92 (essentially the same question).
_CACHE_THRESHOLD  = 0.92
_CACHE_MAX        = 30
_sem_cache: list  = []  # each entry: {"vec": np.array, "question": str, "result": dict}

def _cache_lookup(question: str):
    """Return cached result if question is semantically identical to a recent one, else None."""
    global _sem_cache
    if not _sem_cache:
        return None
    try:
        from rag_retriever import _model as _rag_model
        import numpy as np
        model = _sem_model or _rag_model
        if model is None:
            return None
        q_vec = model.encode([question], convert_to_numpy=True, normalize_embeddings=True)[0]
        for entry in reversed(_sem_cache):
            sim = float(np.dot(q_vec, entry["vec"]))
            if sim >= _CACHE_THRESHOLD:
                print(f"[SemanticCache] HIT (sim={sim:.3f}) — '{entry['question'][:60]}'")
                return entry["result"]
    except Exception:
        pass
    return None

def _cache_store(question: str, result: dict):
    """Store question+result in semantic cache."""
    global _sem_cache
    try:
        from rag_retriever import _model as _rag_model
        model = _sem_model or _rag_model
        if model is None:
            return
        q_vec = model.encode([question], convert_to_numpy=True, normalize_embeddings=True)[0]
        _sem_cache.append({"vec": q_vec, "question": question, "result": result})
        if len(_sem_cache) > _CACHE_MAX:
            _sem_cache = _sem_cache[-_CACHE_MAX:]
    except Exception:
        pass

def _compress(text: str) -> str:
    if len(text) <= 300:
        return text
    if len(text) <= 600:
        return text[:500] + "…"
    # For longer agent responses, keep more — numbers and findings matter
    return _llm(
        "Summarize in 3-4 sentences. Preserve all specific numbers, region names, and key findings.",
        text, max_tokens=150
    )

def _memory_context() -> str:
    if not _memory:
        return ""
    lines = [f"{m['role'].upper()}: {m['summary']}" for m in _memory[-12:]]
    return "Conversation history:\n" + "\n".join(lines) + "\n\n"

def _conv_history() -> list:
    """Return last _MAX_CONV_TURNS exchanges as Bedrock messages list."""
    return _conv_messages[-(  _MAX_CONV_TURNS * 2):]

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
]

def _is_suggestion_only(text: str, has_prior_context: bool = False) -> bool:
    t = text.lower().strip().rstrip("?!.,")
    return any(p in t for p in _SUGGESTION_ONLY_PATTERNS)

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
    # corrections / pushback
    "that's not right", "that's wrong", "that doesn't", "that is not", "thats not",
    "i don't think", "i dont think", "i think you", "you're wrong", "youre wrong",
    "incorrect", "not correct", "doesn't sound right", "doesnt sound right",
    "that seems off", "are you sure", "are u sure", "u sure", "i disagree",
    "no that's", "no thats", "no it's", "no its", "this is wrong", "this seems wrong",
    "this doesn't", "this doesnt", "not accurate", "wait", "hold on", "actually",
    # opinions / meta questions
    "what do you think", "what do u think", "why do you", "why would",
    "explain why", "explain that", "can you explain", "elaborate",
    "what does that mean", "what does this mean",
    # clarifications
    "what are you", "what r u", "what are u", "i meant", "i mean",
    "never mind", "nevermind", "forget it", "ignore that",
]

def _is_conversational(text: str, has_prior_context: bool) -> bool:
    """Returns True if the message is a correction, opinion, or clarification that
    doesn't need new SQL — it should be answered from existing context."""
    if not has_prior_context:
        return False
    t = text.lower().strip()
    if any(p in t for p in _CONVERSATIONAL_PATTERNS):
        return True
    # Very short messages with prior context are likely follow-ups, not new queries
    words = t.split()
    if len(words) <= 6 and not any(w in t for w in [
        "show", "list", "count", "how many", "what is", "what are", "give me",
        "average", "total", "compare", "find", "get", "query", "kpi", "top",
    ]):
        return True
    return False

def _classify_intent(user_input: str, mem_ctx: str) -> str:
    """Ask the LLM to classify the user's intent given conversation context.
    Returns 'query' (needs fresh SQL), 'suggest' (wants advice from existing data),
    or 'converse' (correction, clarification, opinion — no data needed)."""
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
        return "query"  # safe default
    return label

def _direct_reply(user_input: str, mem_ctx: str, mode: str) -> dict:
    """Answer directly from conversation context — no SQL, no chain.
    mode='converse': engage with correction/clarification/question
    mode='suggest': give strategy/recommendations based on prior data
    """
    if mode == "suggest":
        system = (
            "You are a senior telecom strategy consultant. "
            "The user wants actionable recommendations based on the analysis already done. "
            "Draw on the data and findings in the conversation to give concrete, specific advice. "
            "Output a JSON object with two fields: "
            "\"summary\" (2-3 sentence overview) and "
            "\"recommendations\" (array of exactly 3 strings, each a specific actionable recommendation). "
            "Only use numbers that appeared in the prior analysis — never invent statistics. "
            "Output valid JSON only, no markdown."
        )
        prompt = (
            f"Conversation so far:\n{mem_ctx}\n\n"
            f"User: {user_input}\n\nJSON:"
        )
        raw = _llm(system, prompt, max_tokens=600, allow_thinking=False)
        try:
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            parsed = json.loads(m.group()) if m else {}
            summary = parsed.get("summary", raw.strip())
            recs = parsed.get("recommendations", [])
            if not isinstance(recs, list):
                recs = []
        except Exception:
            summary = raw.strip()
            recs = []
        return {"type": "analysis", "text": summary, "recommendations": recs}
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
# TOOL-CALLING LOOP  (structured output via Bedrock converse toolConfig)
# Replaces brittle text-tag + regex-JSON parsing. The model returns typed
# tool calls, so there is no raw "CONCLUDE: {...}" text to leak or mis-parse.
# Auto-falls back to _run_chain() if the model/region rejects tools or if
# anything in here raises — so this can never regress the working path.
# ═══════════════════════════════════════════════════════════════════════
_TOOLS_ENABLED = os.environ.get("AGENT_TOOLS", "1") != "0"
_tools_ok = True   # flips False permanently if the model reports tools unsupported

class _ToolsUnsupported(Exception):
    pass

_TOOLCONFIG = {"tools": [
    {"toolSpec": {
        "name": "run_sql",
        "description": ("Run ONE read-only SQLite SELECT and get the rows back. "
                        "database='network' for subscribers/devices/subscriber_technology/KPIs/coverage/alarms; "
                        "'operator' for customers/plans/billing/ARPU/churn/segments; "
                        "'both' for a cross-DB query (use the op. prefix; ATTACH is added automatically)."),
        "inputSchema": {"json": {
            "type": "object",
            "properties": {
                "database": {"type": "string", "enum": ["network", "operator", "both"]},
                "sql": {"type": "string", "description": "A single valid SQLite SELECT, one line, no markdown."},
            },
            "required": ["database", "sql"],
        }},
    }},
    {"toolSpec": {
        "name": "inspect_schema",
        "description": "Return the real column list of a table when unsure what columns exist.",
        "inputSchema": {"json": {
            "type": "object",
            "properties": {"table": {"type": "string"}},
            "required": ["table"],
        }},
    }},
    {"toolSpec": {
        "name": "conclude",
        "description": ("Submit the FINAL answer for any analysis/breakdown/trend/comparison/recommendation "
                        "question. Does NOT create campaigns or send anything."),
        "inputSchema": {"json": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "3-5 plain sentences, NO markdown/bullets/tables. Lead with the key finding and real numbers."},
                "recommendations": {"type": "array", "items": {"type": "string"},
                                    "description": "0-3 actions, each backed by a real number. Omit if purely factual."},
                "chart": {"type": "object", "description": "Optional chart spec built from real query rows (bar/pie/line/treemap...)."},
                "strategy_diagram": {"type": "object", "description": "Optional, for subscriber-segment/migration/commercial answers."},
                "mindmap": {"type": "object", "description": "Optional hierarchical mind map."},
            },
            "required": ["text"],
        }},
    }},
    {"toolSpec": {
        "name": "propose_action",
        "description": ("ONLY when the user explicitly asks to CREATE/LAUNCH/ASSIGN/SEND/ENROLL. Describe exactly "
                        "what will happen with real counts and Yuan impact. Requires user confirmation."),
        "inputSchema": {"json": {
            "type": "object",
            "properties": {"description": {"type": "string"}},
            "required": ["description"],
        }},
    }},
]}


print(f"[agent] tool-calling loop v2 loaded (salvage+override) — AGENT_TOOLS={'on' if _TOOLS_ENABLED else 'off'}")

def _bedrock_converse_tools(system: str, messages: list, max_tokens: int = 3000):
    client = _get_bedrock()
    model_id = os.environ.get("BEDROCK_MODEL", "qwen.qwen3-32b-v1:0")
    return client.converse(
        modelId=model_id,
        system=[{"text": system}],
        messages=messages,
        toolConfig=_TOOLCONFIG,
        inferenceConfig={"maxTokens": max_tokens, "temperature": 0.1},
    )


def _first_json_obj(s: str) -> str | None:
    """Return the first balanced {...} object in s (ignores braces inside strings).
    Robust to several concatenated objects (Qwen sometimes repeats its answer)."""
    start = s.find("{")
    if start < 0:
        return None
    depth = 0; in_str = False; esc = False
    for i in range(start, len(s)):
        c = s[i]
        if esc:
            esc = False; continue
        if c == "\\":
            esc = True; continue
        if c == '"':
            in_str = not in_str; continue
        if in_str:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None


def _try_text_conclude(text: str):
    """If the model wrote its final answer as JSON text instead of calling the
    conclude tool, parse it. Returns a conclude-shaped dict or None."""
    if not text or "{" not in text:
        return None
    obj = _first_json_obj(text)
    if not obj:
        return None
    raw = re.sub(r'(\d),(\d{3})', r'\1\2', obj)
    raw = re.sub(r'\s+', ' ', raw)
    try:
        d = json.loads(raw)
    except Exception:
        return None
    return d if isinstance(d, dict) and "text" in d else None


def _finalize_tools(inp: dict, question: str, treemap_ctx: str,
                    is_treemap: bool, last_sql: str, steps_log: list) -> dict:
    """Turn a `conclude` tool input into the result dict (same post-processing
    the text path does: currency/markdown cleanup, treemap build, export SQL)."""
    text = (inp.get("text") or "").strip()
    # Defensive: if the model crammed the whole answer JSON into the text field,
    # unwrap it so we don't render raw JSON.
    if text.startswith("{"):
        inner = _try_text_conclude(text)
        if inner:
            inp = inner
            text = (inner.get("text") or "").strip()
    text = re.sub(r'\$\s*([\d,\.]+)', r'\1 Yuan', text)
    text = re.sub(r'([\d,\.]+)\s*TND', r'\1 Yuan', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\*(.*?)\*', r'\1', text)
    text = re.sub(r'^[-*]\s+', '', text, flags=re.MULTILINE).strip()

    chart = inp.get("chart") or None
    if is_treemap and treemap_ctx:
        built = _build_treemap_from_context(treemap_ctx)
        if built:
            chart = built

    extract_sql = None
    if last_sql and re.search(r'\b(subscribers|devices|subscriber_technology|cells|sites)\b', last_sql, re.I):
        raw = _llm(
            "You are a SQL expert. Rewrite the analysis query as a CSV export SELECT with all identifier and "
            "descriptor columns, plus 1-3 CASE WHEN annotation columns (recommended_action, priority, campaign_type). "
            "Use only columns that exist. Output only the SQL.",
            f"Analysis query: {last_sql}", max_tokens=600, allow_thinking=False
        ).strip().strip('`').strip()
        if re.match(r'(?i)^\s*SELECT\b', raw):
            extract_sql = raw

    result = {"type": "analysis", "text": text, "steps": steps_log}
    recs = inp.get("recommendations")
    if isinstance(recs, list) and recs:
        result["recommendations"] = [str(r) for r in recs]
    if chart:
        result["chart"] = chart
    if isinstance(inp.get("strategy_diagram"), dict) and inp["strategy_diagram"]:
        result["strategy_diagram"] = inp["strategy_diagram"]
    if isinstance(inp.get("mindmap"), dict) and inp["mindmap"]:
        result["mindmap"] = inp["mindmap"]
    if extract_sql:
        result["extract_sql"] = extract_sql
    return result


def _run_chain_tools(question: str, max_steps: int = 8) -> dict:
    global _tools_ok
    _cached = _cache_lookup(question)
    if _cached:
        return _cached

    q_lower = question.lower()
    # Composite-scoring questions use a bespoke merge in the chain path — defer.
    _score_kw = ("score", "rank", "composite", "weighted", "weight", "prioriti",
                 "risk score", "top 20", "top 10")
    if any(w in q_lower for w in _score_kw):
        raise _ToolsUnsupported("scoring → chain path")

    _intent = classify_intent(question)
    _graph_ctx = retrieve_graph_context(question)
    rag_block = ""
    if _should_use_rag(question):
        rag_block = f"\n\nTELECOM CONTEXT (use for recommendations):\n{retrieve(question, top_k=3)}"

    from datetime import date as _date
    system = AGENT_SYSTEM.replace("{rag_context}", rag_block).replace("{schema}", FULL_SCHEMA_BEDROCK)
    system = (f"Today's date: {_date.today().isoformat()}. Only reference dates that appear in query results.\n\n"
              + system)
    if _graph_ctx:
        system += f"\n\n{_graph_ctx}"
    _sem_hint = _semantic_schema_hint(question)
    if _sem_hint:
        system += f"\n\n{_sem_hint}"
    _sql_ex = retrieve_sql(question, top_k=3)
    if _sql_ex:
        system += f"\n\nSQL PATTERNS to copy (same tables/columns, adjust filters/LIMIT):\n{_sql_ex}"
    if _intent == "alarm" or _is_alarm_question(question):
        _ac = _fetch_alarm_context()
        if _ac:
            system += f"\n\n{_ac}"
    system += ("\n\n=== OUTPUT MODE OVERRIDE (highest priority — overrides everything above) ===\n"
               "You have TOOLS. IGNORE every earlier instruction about writing 'CONCLUDE:', 'QUERY_SC:', "
               "'QUERY_OP:', 'PROPOSE:', or emitting JSON as plain text — that format is OBSOLETE here.\n"
               "- To read data: CALL the run_sql tool (one query per call). Use inspect_schema if unsure of columns.\n"
               "- To give the final answer: CALL the conclude tool. Put the analysis in its 'text' field and any "
               "chart / strategy_diagram / mindmap in their own fields. Do NOT write the answer as plain text or JSON.\n"
               "- To create/launch/send: CALL the propose_action tool.\n"
               "Run at least one query before concluding unless the conversation already has the data.")

    is_treemap = any(w in q_lower for w in ("drilldown", "drill down", "drill-down", "tree", "treemap", "hierarchy"))

    _prior = _memory_context()
    user_text = (f"Conversation so far:\n{_prior}\n\n" if _prior else "") + f"Question: {question}"
    messages = [{"role": "user", "content": [{"text": user_text}]}]

    last_sql = None
    steps_log = []
    seen = set()
    treemap_ctx = ""

    for step in range(max_steps):
        if _stop_event and _stop_event.is_set():
            return {"type": "cancelled", "text": "Stopped."}
        try:
            resp = _bedrock_converse_tools(system, messages)
        except Exception as e:
            msg = str(e).lower()
            if step == 0:
                if any(k in msg for k in ("validation", "toolconfig", "not support", "unsupported", "tool")):
                    _tools_ok = False  # don't keep retrying tools on a model that lacks them
                raise _ToolsUnsupported(str(e))
            break  # mid-chain failure → fall out to best-effort below

        out = resp.get("output", {}).get("message", {})
        blocks = out.get("content", []) or []
        tool_uses = [b["toolUse"] for b in blocks if "toolUse" in b]

        if not tool_uses:
            # Model ignored the tools and wrote the answer as text/JSON (Qwen tends to,
            # because the base prompt describes the legacy CONCLUDE-JSON format). Salvage
            # that conclude object instead of nudging in a loop.
            joined = " ".join(b.get("text", "") for b in blocks if b.get("text"))
            cd = _try_text_conclude(joined)
            if cd:
                print("[Tools v2] salvaged text-emitted conclude → clean result")
                finished = _finalize_tools(cd, question, treemap_ctx, is_treemap, last_sql, steps_log)
                _cache_store(question, finished)
                return finished
            if joined and _streaming_queue:   # reasoning only → thinking panel
                _streaming_queue.put({"type": "think_token", "token": joined})
            messages.append({"role": "assistant", "content": blocks or [{"text": "(no output)"}]})
            messages.append({"role": "user", "content": [{"text":
                "Call the conclude tool with your final answer (or run_sql for more data). Never write JSON as text."}]})
            continue

        # reasoning text that accompanies a tool call → thinking panel (skip answer-JSON)
        for b in blocks:
            t = b.get("text")
            if t and _streaming_queue and '"text"' not in t:
                _streaming_queue.put({"type": "think_token", "token": t})

        messages.append({"role": "assistant", "content": blocks})
        tool_results = []
        finished = None

        for tu in tool_uses:
            name = tu.get("name"); tid = tu.get("toolUseId"); inp = tu.get("input") or {}

            if name == "conclude":
                print("[Tools v2] conclude tool called")
                finished = _finalize_tools(inp, question, treemap_ctx, is_treemap, last_sql, steps_log)
                break
            if name == "propose_action":
                finished = {"type": "proposal", "text": (inp.get("description") or "").strip(), "steps": steps_log}
                break
            if name == "inspect_schema":
                tool_results.append((tid, schema_lookup(inp.get("table", ""))))
                steps_log.append(f"[schema] {inp.get('table','')}")
                continue
            if name == "run_sql":
                sql = re.sub(r'```sql|```', '', inp.get("sql", "")).strip().rstrip(';')
                db = inp.get("database", "network")
                if not sql.upper().startswith(("SELECT", "WITH", "ATTACH")):
                    tool_results.append((tid, "Only SELECT/WITH queries are allowed.")); continue
                key = re.sub(r'\s+', ' ', sql.upper())
                if key in seen:
                    tool_results.append((tid, "Duplicate query — reuse the earlier result or try a different one.")); continue
                seen.add(key)
                if db == "operator":
                    runner, db_path = query_op, OP_DB
                    sql = re.sub(r'\bop\.', '', sql)
                elif db == "both":
                    runner, db_path = query_sc, SC_DB
                    if "ATTACH" not in sql.upper():
                        sql = f"ATTACH DATABASE '{OP_DB}' AS op; " + sql
                else:
                    runner, db_path = query_sc, SC_DB
                sem = _check_sql_semantics(sql)
                if sem:
                    tool_results.append((tid, f"semantic error: {sem}")); continue
                serr = _check_sql(sql, db_path)
                if serr:
                    tool_results.append((tid, serr)); continue
                rows = runner(sql)
                last_sql = sql
                if rows and "error" in rows[0]:
                    tool_results.append((tid, f"Query error: {rows[0]['error']}"))
                    steps_log.append(f"[sql err] {sql[:90]}")
                    _n = 0
                elif not rows:
                    tool_results.append((tid, f"0 rows. Diagnostic: {_diagnose_zero_rows(sql, db_path)}"))
                    steps_log.append(f"[sql 0] {sql[:90]}")
                    _n = 0
                else:
                    _n = len(rows)
                    cap = 200 if is_treemap else 30
                    sample = [dict(r) for r in rows[:cap]]
                    tool_results.append((tid, f"{_n} rows: {json.dumps(sample, default=str)}"
                                              + (f" (showing {cap} of {_n})" if _n > cap else "")))
                    steps_log.append(f"[sql {_n}] {sql[:90]}")
                    if is_treemap:
                        treemap_ctx += f"\nResults ({_n} rows): {json.dumps(sample, default=str)}\n"
                if _streaming_queue:
                    _streaming_queue.put({"type": "step_sql", "step": step + 1, "tag": db.upper(),
                                          "sql": sql[:200], "rows": _n,
                                          "error": rows[0]["error"] if (rows and "error" in rows[0]) else None})
                continue

        if finished is not None:
            _cache_store(question, finished)
            return finished

        if tool_results:
            messages.append({"role": "user", "content": [
                {"toolResult": {"toolUseId": tid, "content": [{"text": str(txt)[:6000]}]}}
                for tid, txt in tool_results
            ]})

    # step budget exhausted (or mid-chain failure)
    return {"type": "analysis",
            "text": "I couldn't finish within the step budget — try narrowing the question.",
            "steps": steps_log, "truncated": True}

# ═══════════════════════════════════════════════════════════════════════
# DIMENSION CLARIFY  — when a drill/breakdown request is under-specified,
# ask which breakdown the user wants instead of guessing. Each option is a
# self-contained query, so clicking one just runs a normal drilldown (no
# resume/pending state needed).
# ═══════════════════════════════════════════════════════════════════════
_DRILL_VERBS = ("drilldown", "drill down", "drill-down", "drill into", "breakdown",
                "break down", "break it down", "distribution", "categorize", "categorise")
# Used to detect whether the user already named a dimension (for the
# "Run exactly what I asked" shortcut option).
_NAMED_DIM_WORDS = (" by ", "region", "technolog", " tech", "value segment", "segment",
                    "device", "brand", "arpu", "churn", "plan", "5g-capab", "5g capab",
                    "5g capable", "volte", "nation", "city", "tenure")
_SUBS_ENTITY_WORDS = ("subscriber", "customer", "user base", "our base", "subscribers", "users", " subs")

# Queries the chooser most recently offered — if the next message is one of these
# (an option was clicked) we skip the chooser and run it, so it can't loop.
_recent_clarify: set = set()

_SUBS_DRILL_PRESETS = [
    {"label": "Region → Technology → Value segment",
     "query": "give me a drilldown of subscriber counts by region, technology, and value segment"},
    {"label": "Technology → Device 5G-capability",
     "query": "give me a drilldown of subscriber counts by current technology and device 5G capability"},
    {"label": "Region → Churn risk",
     "query": "give me a drilldown of subscriber counts by region and churn risk level"},
    {"label": "Value segment → Technology",
     "query": "give me a drilldown of subscriber counts by value segment and current technology"},
    {"label": "Just pick the most useful",
     "query": "give me a drilldown of subscriber counts by region, technology, and value segment"},
]

def _maybe_clarify_dimensions(question: str):
    """Return a clarify response for ANY subscriber drill/breakdown request (the
    user wants to always confirm the path). If they already named dimensions, the
    first option re-runs exactly what they asked. Else None."""
    ql = question.lower()
    is_drill   = any(v in ql for v in _DRILL_VERBS)
    about_subs = any(w in ql for w in _SUBS_ENTITY_WORDS)
    if not (is_drill and about_subs):
        return None
    options = []
    if any(w in ql for w in _NAMED_DIM_WORDS):
        options.append({"label": "Run exactly what I asked", "query": question})
    options += _SUBS_DRILL_PRESETS
    # de-dupe by query, keep order
    seen, uniq = set(), []
    for o in options:
        if o["query"] not in seen:
            seen.add(o["query"]); uniq.append(o)
    return {
        "type": "clarify",
        "text": "How would you like the subscriber base broken down? Pick a drill-down path:",
        "options": uniq,
    }

# ── General curiosity: clarify ANY vague question, not just drilldowns ──
# Cheap marker pre-filter so specific questions never pay the latency; then a
# small quiet LLM call decides whether a choice would change the answer.
_AMBIGUOUS_MARKERS = (
    "best", "worst", " good ", " bad ", "improve", "optimi", "look at", "review",
    "analyze", "analyse", "fix ", "help with", "what about", "anything", "interesting",
    "issues", "problems", "insight", "opportunit", "do something", "what should",
    "where should", "who should", "underperform", "at risk", "focus on", "worth",
    "concern", "recommend", "biggest", "top ", "most important", "priorit",
)

_CLARIFY_SYS = (
    "You decide whether a telecom-analytics question is too ambiguous to answer well.\n"
    "Available data: subscribers (region, current technology, value segment, device 5G-capability, "
    "churn risk, tenure), network KPIs (throughput, dropped-call rate, latency, availability, "
    "alarms by region/technology/severity), commercial (ARPU, plans, billing, segments).\n\n"
    'If the question is specific enough to answer directly, output exactly: {"clarify": false}\n'
    "ONLY if a key choice would MEANINGFULLY change the answer, output:\n"
    '{"clarify": true, "question": "<one short question>", "options": ['
    '{"label": "<short choice>", "query": "<a complete standalone question to run>"}, ...]}\n'
    "2-4 options, each a fully self-contained question. Always include a final option "
    '{"label": "Just decide for me", "query": "<the single most useful interpretation>"}.\n'
    "Strongly prefer clarify:false — only ask when truly necessary. Output ONLY the JSON."
)

def _llm_quiet(system, prompt, **k):
    """LLM call that does NOT stream tokens to the UI (used for control decisions)."""
    global _streaming_queue
    saved = _streaming_queue
    _streaming_queue = None
    try:
        return _llm(system, prompt, **k)
    finally:
        _streaming_queue = saved

def _llm_clarify_gate(question: str):
    """Ask a clarifying question for genuinely vague/open requests. Returns a
    clarify dict or None. Gated by markers so clear questions skip the LLM call."""
    if not any(m in question.lower() for m in _AMBIGUOUS_MARKERS):
        return None
    raw = _llm_quiet(_CLARIFY_SYS, f"Question: {question}\nJSON:", max_tokens=400, allow_thinking=False)
    if not raw or raw.startswith("ERROR"):
        return None
    obj_txt = _first_json_obj(raw)
    if not obj_txt:
        return None
    try:
        obj = json.loads(re.sub(r'\s+', ' ', obj_txt))
    except Exception:
        return None
    if not obj.get("clarify"):
        return None
    opts = [{"label": str(o.get("label", "")).strip(), "query": str(o.get("query", "")).strip()}
            for o in (obj.get("options") or []) if o.get("query")][:4]
    if len(opts) < 2:
        return None
    return {"type": "clarify",
            "text": str(obj.get("question") or "Which would you like?").strip(),
            "options": opts}

def _maybe_clarify(question: str):
    """Unified curiosity: deterministic subscriber-drill catalog first (instant),
    then the general LLM clarify gate for other vague questions."""
    return _maybe_clarify_dimensions(question) or _llm_clarify_gate(question)

# ═══════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════

def run_agent(user_input: str) -> dict:
    global _pending, _memory, _conv_messages, _partial_state

    # continue from truncated chain
    if user_input.strip().lower() in ("continue", "__continue__") and _partial_state:
        state = _partial_state
        _partial_state = None
        result = _run_chain(state["question"], max_steps=4, _resume_context=state["context"], _resume_steps=state["steps_log"])
        _memory.append({"role": "agent", "summary": _compress(result.get("text", ""))})
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
    _conv_messages.append({"role": "user", "content": [{"text": user_input}]})

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

    # Drill/breakdown → always offer the path chooser first. Each option is a full
    # query; when one is picked the next message contains it, so we bypass the
    # chooser and run it (prevents an infinite ask loop).
    global _recent_clarify
    if any(opt and opt in resolved_input for opt in _recent_clarify):
        _recent_clarify = set()                          # a chosen option → run it directly
    else:
        _clar = _maybe_clarify(resolved_input)
        if _clar:
            _recent_clarify = {o["query"] for o in _clar["options"]}
            print(f"[Clarify] offering choices — '{resolved_input[:50]}'")
            return _clar

    # LLM-based intent classifier — only runs when there's prior context
    if mem_ctx:
        _intent = _classify_intent(user_input, mem_ctx)
        if _intent in ("converse", "suggest"):
            result = _direct_reply(user_input, mem_ctx, mode=_intent)
            _memory.append({"role": "agent", "summary": _compress(result["text"])})
            return result
        # _intent == "query" → fall through to normal chain

    # Fast single-shot path for simple metric questions ("how many 5G subs?",
    # "average throughput in X") — skips the full 8-step reasoning loop. Excludes
    # treemap/drilldown questions, which need the richer chain to build hierarchy.
    _q_low = resolved_input.lower()
    _treemapish = any(w in _q_low for w in
                      ("drilldown", "drill down", "drill-down", "tree", "treemap", "hierarchy", "breakdown"))
    if _is_simple_metric(resolved_input) and not _treemapish:
        print(f"[FastPath] simple metric — '{resolved_input[:60]}'")
        result = _fast_query(resolved_input)
        _memory.append({"role": "agent", "summary": _compress(result.get("text", ""))})
        _ans = result.get("text", "")
        if _ans:
            _conv_messages.append({"role": "assistant", "content": [{"text": _ans}]})
            if len(_conv_messages) > _MAX_CONV_TURNS * 2:
                _conv_messages[:] = _conv_messages[-(_MAX_CONV_TURNS * 2):]
        return result

    # Structured tool-calling path (preferred). Falls back to the text-tag
    # chain automatically if the model/region doesn't support tools.
    result = None
    if _TOOLS_ENABLED and _tools_ok:
        try:
            result = _run_chain_tools(resolved_input)
        except _ToolsUnsupported as e:
            print(f"[Tools] falling back to text chain: {e}")
            result = None
        except Exception as e:
            print(f"[Tools] error, falling back to text chain: {e}")
            result = None
    if result is None:
        result = _run_chain(resolved_input)

    # store proposal for confirmation
    if result.get("type") == "proposal":
        _pending = {"text": result["text"], "question": user_input}
        result["text"] = (
            result["text"] +
            "\n\n---\n**Type 'confirm' to proceed or 'cancel' to abort.**"
        )

    _memory.append({"role": "agent", "summary": _compress(result.get("text", ""))})
    _answer_text = result.get("text", "")
    if _answer_text:
        _conv_messages.append({"role": "assistant", "content": [{"text": _answer_text}]})
        # trim to window
        if len(_conv_messages) > _MAX_CONV_TURNS * 2:
            _conv_messages[:] = _conv_messages[-(_MAX_CONV_TURNS * 2):]
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
    global _memory, _conv_messages, _pending, _partial_state, _sem_cache, _recent_clarify
    _memory        = []
    _conv_messages = []
    _pending       = None
    _partial_state = None
    _sem_cache     = []
    _recent_clarify = set()