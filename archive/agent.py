import sqlite3, json, os, re
import requests as _r
from datetime import datetime, timedelta

os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_API_KEY"] = "disabled"

DB_PATH = "NetworkAnalyzer.db"
MODEL   = "qwen2.5:7b"

# ── DB ─────────────────────────────────────────────────────────────────────────

def get_conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def run_sql(sql: str) -> list:
    try:
        conn = get_conn()
        rows = conn.execute(sql).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        return [{"error": str(e)}]

# ── Write actions ──────────────────────────────────────────────────────────────

def do_create_offer(params: dict) -> dict:
    try:
        conn = get_conn(); now = datetime.now()
        validity = int(params.get("validity_days", 30))
        conn.execute("""
            INSERT INTO offers (offer_name, target_segment, discount_pct, bonus_data_gb,
            validity_days, start_date, end_date, is_active, created_by, min_churn_risk,
            description, target_profession, target_lifestyle, target_income)
            VALUES(?,?,?,?,?,?,?,1,'agent',0,?,?,?,?)""",
            (params.get("name","New Offer"), params.get("segment","prepaid"),
             float(params.get("discount_pct",0)), int(params.get("bonus_data_gb",0)),
             validity, now.isoformat(), (now+timedelta(days=validity)).isoformat(),
             params.get("description",""), params.get("target_profession"),
             params.get("target_lifestyle"), params.get("target_income")))
        conn.commit()
        row = conn.execute("SELECT offer_id FROM offers ORDER BY offer_id DESC LIMIT 1").fetchone()
        offer_id = row["offer_id"] if row else None
        conn.close()
        return {"status": "created", "offer_id": offer_id, **params}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def do_assign_offer(offer_id: int, filters: dict) -> dict:
    try:
        conn = get_conn()
        wh = ["c.is_active=1"]; p = []
        if filters.get("segment"):    wh.append("c.segment=?");            p.append(filters["segment"])
        if filters.get("region"):     wh.append("c.region=?");             p.append(filters["region"])
        if filters.get("profession"): wh.append("cl.profession=?");        p.append(filters["profession"])
        if filters.get("lifestyle"):  wh.append("cl.lifestyle_pattern=?"); p.append(filters["lifestyle"])
        if filters.get("income"):     wh.append("cl.income_bracket=?");    p.append(filters["income"])
        customers = conn.execute(f"""
            SELECT c.customer_id FROM customers c
            LEFT JOIN customer_lifestyle cl ON c.customer_id=cl.customer_id
            WHERE {" AND ".join(wh)} LIMIT 50000
        """, tuple(p)).fetchall()
        now = datetime.now().isoformat(); count = 0
        for cust in customers:
            conn.execute("""INSERT OR IGNORE INTO offer_assignments
                (offer_id, customer_id, assigned_date, accepted) VALUES(?,?,?,0)""",
                (offer_id, cust["customer_id"], now))
            count += 1
        conn.commit(); conn.close()
        return {"status": "assigned", "offer_id": offer_id, "assigned_to": count}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def do_acknowledge_alarm(alarm_id: int) -> dict:
    try:
        conn = get_conn()
        conn.execute("UPDATE network_alarms SET is_active=0 WHERE alarm_id=?", (alarm_id,))
        conn.commit(); conn.close()
        return {"status": "acknowledged", "alarm_id": alarm_id}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ── Schema ─────────────────────────────────────────────────────────────────────

SCHEMA = """
DATABASE SCHEMA — Tunisian Telecom Operator

TABLE customers: customer_id, full_name, region, city, segment(prepaid/postpaid/enterprise), is_active
TABLE customer_lifestyle: customer_id, profession, income_bracket(low/medium/high), lifestyle_pattern, payment_reliability, spending_sensitivity, is_student, has_children, roaming_frequency
TABLE subscriptions: customer_id, plan_id, start_date, end_date, is_current
TABLE plans: plan_id, plan_name, data_cap_gb, monthly_price, supports_5g
TABLE kpis_daily: cell_id, date, rsrp_avg, sinr_avg, dl_throughput_mbps, ul_throughput_mbps, dropped_call_rate, call_setup_success_rate, availability_pct, latency_ms, congestion_level, active_users_avg
TABLE kpis_hourly: cell_id, datetime, rsrp, sinr, dl_throughput_mbps, active_users, congestion_level
TABLE cells: cell_id, site_id, cell_name, technology(2G/3G/4G/5G), is_5g, is_active
TABLE sites: site_id, site_name, region, city, latitude, longitude, is_active
TABLE network_alarms: alarm_id, cell_id, alarm_type, severity(critical/major/minor/warning), trigger_time, is_active, description
TABLE network_incidents: incident_id, cell_id, incident_type, start_time, duration_minutes, affected_users, severity, root_cause, resolved
TABLE churn_scores: customer_id, score_date, churn_probability(0-1), risk_level(low/medium/high), contributing_factors
TABLE complaints: complaint_id, customer_id, complaint_type, status(open/resolved), submission_date, severity
TABLE offers: offer_id, offer_name, target_segment, discount_pct, bonus_data_gb, validity_days, is_active, target_profession, target_lifestyle, target_income, description
TABLE offer_assignments: offer_id, customer_id, assigned_date, accepted
TABLE billing: customer_id, billing_month, total_amount, payment_status(paid/unpaid/overdue)
TABLE data_usage_daily: customer_id, date, data_used_gb, peak_hour_usage_gb
TABLE nps_surveys: customer_id, survey_date, score(0-10), category, feedback
TABLE cei_scores: customer_id, score_date, cei_score, network_score, service_score, billing_score
TABLE roaming_usage: customer_id, country, data_used_gb, calls_minutes, total_charge

KEY JOINS:
- kpis_daily → cells → sites  (for region/city KPIs)
- customers → churn_scores  (for churn risk)
- customers → customer_lifestyle  (for profile)
- network_alarms → cells → sites  (for alarm location)

QUERY PATTERNS:
- Latest KPIs: WHERE date=(SELECT MAX(date) FROM kpis_daily)
- Latest churn: WHERE (customer_id,score_date) IN (SELECT customer_id,MAX(score_date) FROM churn_scores GROUP BY customer_id)
- Region KPIs: JOIN cells c ON k.cell_id=c.cell_id JOIN sites s ON c.site_id=s.site_id WHERE s.region='X'
"""

# ── Ollama ─────────────────────────────────────────────────────────────────────

def _ollama(system: str, prompt: str, max_tokens: int = 1000) -> str:
    try:
        r = _r.post("http://localhost:11434/api/generate",
                    json={"model": MODEL, "system": system,
                          "prompt": prompt, "stream": False,
                          "options": {"num_predict": max_tokens}},
                    timeout=180)
        return r.json().get("response", "").strip()
    except Exception as e:
        return f"ERROR:{str(e)}"

# ── STEP 1: Classify the question ─────────────────────────────────────────────

CLASSIFY_SYSTEM = """You are classifying a telecom operator's question into one of three modes.

SHOW — operator wants to see specific data, a number, or a list of existing records
Examples: "what is the drop rate in Sfax", "show me high churn customers", "how many alarms", "what offers do we have"

ANALYZE — operator wants understanding, recommendations, suggestions, or advice
Examples: "why are customers churning", "what is causing network issues", "analyze our churn",
          "what offer would you recommend", "suggest something for students", "what should we do about churn",
          "which customers should we focus on", "what would help retain customers"

ACT — operator explicitly wants to CREATE, LAUNCH, ACKNOWLEDGE, or ASSIGN something right now
Examples: "create an offer called X", "acknowledge alarm 5", "launch a campaign", "assign offer 3 to students"
IMPORTANT: Only use ACT if the operator is giving a direct command to write/create/modify data.
Questions with "recommend", "suggest", "would", "should", "help" are ANALYZE not ACT.

Output ONLY one word: SHOW, ANALYZE, or ACT"""

CLASSIFY_SYSTEM = """Classify a telecom operator question into exactly one of: SHOW, ANALYZE, ACT

SHOW = wants to see data, numbers, or a list of existing records
ANALYZE = wants understanding, investigation, recommendations, suggestions, or strategy  
ACT = explicitly commanding to create, launch, assign, delete, or modify something right now

Examples:
"what is the drop rate in Sfax" → SHOW
"show me high churn customers" → SHOW
"how many alarms are active" → SHOW
"list our current offers" → SHOW
"why are customers churning in Tunis" → ANALYZE
"what is causing network issues" → ANALYZE
"recommend an offer for students" → ANALYZE
"what should we do about churn" → ANALYZE
"suggest a retention strategy" → ANALYZE
"analyze complaint trends" → ANALYZE
"which customers should we focus on" → ANALYZE
"what offer would help retain students" → ANALYZE
"create an offer called Student Bundle" → ACT
"launch the StudentPlus plan" → ACT
"create and launch offer with 20GB for students" → ACT
"acknowledge alarm 42" → ACT
"assign offer 3 to students in Tunis" → ACT
"go ahead and create it" → ACT

Output ONLY the single word: SHOW, ANALYZE, or ACT"""

def classify(question: str) -> str:
    result = _ollama(CLASSIFY_SYSTEM, f"Question: {question}", max_tokens=5)

    for mode in ["SHOW", "ANALYZE", "ACT"]:
        if mode in result.upper():
            return mode

    # Fallback only if Qwen returns something unexpected
    t = question.lower()
    if any(w in t for w in ["create", "launch", "acknowledge", "assign", "deploy"]):
        return "ACT"
    if any(w in t for w in ["why", "analyze", "recommend", "suggest", "strategy", "should"]):
        return "ANALYZE"
    return "SHOW"

# ── STEP 2A: SHOW mode — single SQL ───────────────────────────────────────────

SHOW_SYSTEM = f"""You are a SQL expert for a Tunisian telecom database.
Write a single SQLite SELECT query to answer the question.
Output ONLY the SQL, no explanation, no markdown.
LIMIT 20 unless aggregating.

{SCHEMA}

Examples:
Q: average download speed in Sfax?
SELECT ROUND(AVG(k.dl_throughput_mbps),2) as avg_dl_mbps FROM kpis_daily k JOIN cells c ON k.cell_id=c.cell_id JOIN sites s ON c.site_id=s.site_id WHERE s.region='Sfax' AND k.date=(SELECT MAX(date) FROM kpis_daily)

Q: top 5 high churn customers in Tunis
SELECT c.full_name, c.segment, cl.profession, cs.churn_probability, cs.contributing_factors FROM customers c JOIN customer_lifestyle cl ON c.customer_id=cl.customer_id JOIN churn_scores cs ON c.customer_id=cs.customer_id WHERE c.region='Tunis' AND cs.risk_level='high' AND (cs.customer_id,cs.score_date) IN (SELECT customer_id,MAX(score_date) FROM churn_scores GROUP BY customer_id) ORDER BY cs.churn_probability DESC LIMIT 5

Q: how many critical alarms are active?
SELECT COUNT(*) as critical_alarms FROM network_alarms WHERE severity='critical' AND is_active=1

Q: what offers do we have for students?
SELECT offer_name, bonus_data_gb, discount_pct, target_segment, target_profession, target_income, description, (CASE WHEN target_profession='Student' THEN 3 WHEN target_income='low' THEN 2 WHEN target_segment='prepaid' THEN 1 ELSE 0 END) as relevance FROM offers WHERE is_active=1 ORDER BY relevance DESC, discount_pct DESC LIMIT 5
"""

def run_show(question: str) -> str:
    sql_raw = _ollama(SHOW_SYSTEM, f"Q: {question}")
    sql = re.sub(r'```sql|```', '', sql_raw).strip()
    if not sql.upper().startswith("SELECT"):
        return f"Could not generate a valid query for: {question}"
    results = run_sql(sql)
    if not results or "error" in results[0]:
        err = results[0].get("error","") if results else ""
        return f"No data found. {err}"
    data_str = json.dumps(results, indent=2)
    answer = _ollama(
        "You are a telecom assistant. Answer the question using only the data provided. State exact numbers. Be concise.",
        f"Question: {question}\n\nData:\n{data_str}\n\nAnswer:"
    )
    return answer

# ── STEP 2B: ANALYZE mode — multi-step reasoning loop ─────────────────────────

ANALYZE_SYSTEM = f"""You are an intelligent telecom analyst investigating a problem.
You have access to a database and can run SQL queries to gather evidence.
Think step by step. Run multiple queries to build a complete picture before concluding.

{SCHEMA}

At each step output EXACTLY one of:
QUERY: <sql>       — to fetch more data
CONCLUDE: <text>   — when you have enough to give a full analysis

Rules:
- Start broad, then drill down based on what you find
- Always investigate at least 2-3 angles before concluding
- Look for correlations: network issues + churn, billing + complaints, etc
- QUERY lines must be valid SQLite, no markdown
- CONCLUDE must include: what you found, why it's happening, what to do about it

Example for "why are customers churning in Tunis?":
QUERY: SELECT cs.contributing_factors, COUNT(*) as count FROM churn_scores cs JOIN customers c ON cs.customer_id=c.customer_id WHERE c.region='Tunis' AND cs.risk_level='high' AND (cs.customer_id,cs.score_date) IN (SELECT customer_id,MAX(score_date) FROM churn_scores GROUP BY customer_id) GROUP BY cs.contributing_factors ORDER BY count DESC
QUERY: SELECT ROUND(AVG(k.dl_throughput_mbps),2) as dl_mbps, ROUND(AVG(k.dropped_call_rate),3) as drop_rate, ROUND(AVG(k.availability_pct),2) as availability FROM kpis_daily k JOIN cells c ON k.cell_id=c.cell_id JOIN sites s ON c.site_id=s.site_id WHERE s.region='Tunis' AND k.date=(SELECT MAX(date) FROM kpis_daily)
QUERY: SELECT complaint_type, COUNT(*) as count FROM complaints comp JOIN customers c ON comp.customer_id=c.customer_id WHERE c.region='Tunis' AND comp.status='open' GROUP BY complaint_type ORDER BY count DESC LIMIT 5
CONCLUDE: Based on the data...
"""

def run_analyze(question: str) -> str:
    history = []          # list of {sql, results} pairs
    context = ""          # accumulated findings

    for step in range(6): # max 6 queries
        # Build prompt with everything gathered so far
        if context:
            queries_done = context.count("--- Query")
            if queries_done >= 3:
                prompt = (f"Question: {question}\n\n"
                          f"Data gathered:\n{context}\n\n"
                          f"You have enough data. You MUST now output CONCLUDE: followed by your full analysis.")
            else:
                prompt = (f"Question to investigate: {question}\n\n"
                          f"Data gathered so far:\n{context}\n\n"
                          f"What do you need next? Output QUERY: for another query, or CONCLUDE: if you have enough.")
        else:
            prompt = (f"Question to investigate: {question}\n\n"
                      f"Start your investigation. Output your first QUERY:")

        response = _ollama(ANALYZE_SYSTEM, prompt, max_tokens=300)

        if response.startswith("ERROR:"):
            return "Could not reach Ollama."

        # Check if agent is done
        if "CONCLUDE:" in response:
            conclusion = response.split("CONCLUDE:", 1)[1].strip()
            return conclusion

        # Extract and run query
        if "QUERY:" in response:
            sql_line = ""
            for line in response.split("\n"):
                if line.strip().startswith("QUERY:"):
                    sql_line = line.split("QUERY:", 1)[1].strip()
                    break

            sql = re.sub(r'```sql|```', '', sql_line).strip()

            if not sql.upper().startswith("SELECT"):
                continue

            results = run_sql(sql)

            if results and "error" not in results[0]:
                result_str = json.dumps(results[:10], indent=2)
                context += f"\n--- Query {step+1}: {sql[:80]}...\nResults: {result_str}\n"
                history.append({"sql": sql, "results": results})
            else:
                context += f"\n--- Query {step+1} returned no results.\n"

    # If we used all steps without CONCLUDE, force a conclusion from gathered context
    if context:
        conclusion = _ollama(
            "You are a telecom analyst. Summarize your findings and give actionable recommendations.",
            f"Question: {question}\n\nEvidence gathered:\n{context}\n\nProvide your analysis and recommendations:"
        )
        return conclusion

    # No context at all — fall back to a broad query
    fallback = run_sql("""
        SELECT cs.contributing_factors, COUNT(*) as count
        FROM churn_scores cs
        JOIN customers c ON cs.customer_id=c.customer_id
        WHERE cs.risk_level='high'
        AND (cs.customer_id,cs.score_date) IN
            (SELECT customer_id,MAX(score_date) FROM churn_scores GROUP BY customer_id)
        GROUP BY cs.contributing_factors ORDER BY count DESC LIMIT 5
    """)
    if fallback:
        return _ollama(
            "You are a telecom analyst.",
            f"Question: {question}\n\nTop churn factors:\n{json.dumps(fallback,indent=2)}\n\nAnalyze and recommend:"
        )
    return "Could not gather enough data to analyze this question."

# ── STEP 2C: ACT mode ─────────────────────────────────────────────────────────

ACT_SYSTEM = f"""You are executing actions on a Tunisian telecom database.
Parse the user's command and output the appropriate ACTION call.

Available actions:
ACTION create_offer(name, segment, discount_pct, bonus_data_gb, validity_days, target_profession, target_lifestyle, target_income, description)
ACTION assign_offer(offer_id, profession, lifestyle, income, segment, region)
ACTION acknowledge_alarm(alarm_id)

Output ONLY the ACTION line, nothing else.

Examples:
create offer called Maxi Etudiant 15GB for students prepaid
ACTION create_offer(name="Maxi Etudiant", segment="prepaid", discount_pct=0, bonus_data_gb=15, validity_days=30, target_profession="Student", description="15GB for students, ID required")

acknowledge alarm 42
ACTION acknowledge_alarm(alarm_id=42)
"""

def parse_and_execute_action(action_str: str) -> str:
    action_str = action_str.strip()

    if "create_offer" in action_str:
        params = {}
        for key in ["name","segment","discount_pct","bonus_data_gb","validity_days",
                    "target_profession","target_lifestyle","target_income","description"]:
            m = re.search(rf'{key}=["\']?(.*?)["\']?(?:[,)]|$)', action_str)
            if m: params[key] = m.group(1).strip('"\'')
        result = do_create_offer(params)
        if result.get("status") == "created":
            filters = {k: params[v] for k,v in [("profession","target_profession"),
                ("lifestyle","target_lifestyle"),("income","target_income"),("segment","segment")] if params.get(v)}
            assigned = do_assign_offer(result["offer_id"], filters)
            return (f"✅ Offer **{params.get('name')}** created (ID {result['offer_id']})\n"
                    f"• Data: {params.get('bonus_data_gb',0)} GB\n"
                    f"• Discount: {params.get('discount_pct',0)}%\n"
                    f"• Segment: {params.get('segment','prepaid')}\n"
                    f"• Target: {params.get('target_profession','general')}\n"
                    f"• Assigned to: {assigned.get('assigned_to',0):,} customers\n"
                    f"• Visible on Offers dashboard.")
        return f"Error: {result.get('message')}"

    if "assign_offer" in action_str:
        m = re.search(r'offer_id=(\d+)', action_str)
        if m:
            filters = {}
            for key in ["profession","lifestyle","income","segment","region"]:
                km = re.search(rf'{key}=["\']?(.*?)["\']?(?:[,)]|$)', action_str)
                if km: filters[key] = km.group(1).strip('"\'')
            result = do_assign_offer(int(m.group(1)), filters)
            return f"✅ Offer assigned to {result.get('assigned_to',0):,} customers."

    if "acknowledge_alarm" in action_str:
        m = re.search(r'alarm_id=(\d+)', action_str)
        if m:
            do_acknowledge_alarm(int(m.group(1)))
            return f"✅ Alarm {m.group(1)} acknowledged and closed."

    return f"Could not parse action: {action_str}"

def run_act(question: str) -> str:
    action_raw = _ollama(ACT_SYSTEM, question, max_tokens=100)
    if action_raw.startswith("ERROR:"):
        return "Could not reach Ollama."
    if "ACTION" in action_raw:
        return parse_and_execute_action(action_raw)
    return f"Could not determine action from: {question}"

# ── MAIN ENTRY POINT ───────────────────────────────────────────────────────────

# ── Conversation memory (per session) ─────────────────────────────────────────

_conversation = []  # stores {role, content} pairs
_last_analysis = "" # stores last ANALYZE output for follow-up actions

def reset_conversation():
    global _conversation, _last_analysis
    _conversation = []
    _last_analysis = ""

# ── STEP 2D: RECOMMEND — bridges ANALYZE into ACT ─────────────────────────────

RECOMMEND_SYSTEM = """You are a telecom strategy advisor.
You are given an analysis of a telecom problem and the available offers in the database.
Your job is to recommend SPECIFIC actions using the real data available.
Be concrete — reference actual offer names, actual customer counts, actual regions.
End with a clear suggested next step the operator can take right now."""

def run_recommend(analysis: str, question: str) -> str:
    """After an analysis, find matching offers and suggest concrete next steps."""

    # Fetch relevant offers from DB
    offers = run_sql("""
        SELECT offer_name, bonus_data_gb, discount_pct, target_segment,
               target_profession, target_income, description
        FROM offers WHERE is_active=1
        ORDER BY discount_pct DESC LIMIT 10
    """)

    # Count actionable customers
    churn_counts = run_sql("""
        SELECT cs.contributing_factors, COUNT(*) as count
        FROM churn_scores cs
        JOIN customers c ON cs.customer_id=c.customer_id
        WHERE cs.risk_level='high'
        AND (cs.customer_id,cs.score_date) IN
            (SELECT customer_id,MAX(score_date) FROM churn_scores GROUP BY customer_id)
        GROUP BY cs.contributing_factors
        ORDER BY count DESC LIMIT 5
    """)

    prompt = (
        f"Original question: {question}\n\n"
        f"Analysis findings:\n{analysis}\n\n"
        f"Available offers in database:\n{json.dumps(offers, indent=2)}\n\n"
        f"High churn customer breakdown:\n{json.dumps(churn_counts, indent=2)}\n\n"
        f"Based on the analysis and available offers, provide:\n"
        f"1. Which existing offer best addresses the main churn cause\n"
        f"2. How many customers would benefit (use the counts above)\n"
        f"3. Whether a new offer should be created and what it should include\n"
        f"4. The single most impactful action to take right now\n"
        f"Be specific, use real offer names and real numbers."
    )
    return _ollama(RECOMMEND_SYSTEM, prompt)


# ── MAIN ENTRY POINT with memory ──────────────────────────────────────────────

def run_agent(user_input: str, session_id: str = "default") -> str:
    global _last_analysis

    # Check if this is a follow-up action after an analysis
    follow_up_triggers = ["do it", "go ahead", "yes do it", "execute", "apply it",
                          "implement it", "confirm", "yes proceed", "assign it", "launch it"]
    explicit_create = any(w in user_input.lower() for w in ["create offer", "create an offer",
                          "launch offer", "add offer", "acknowledge alarm", "assign offer"])
    is_followup = (
        _last_analysis and
        not explicit_create and
        any(t in user_input.lower() for t in follow_up_triggers) and
        len(user_input.split()) < 10  # very short follow-up only
    )

    if is_followup:
        # User is acting on the previous analysis
        combined = f"Based on this analysis:\n{_last_analysis[:500]}\n\nUser command: {user_input}"
        return run_act(combined)

    mode = classify(user_input)

    if mode == "SHOW":
        _last_analysis = ""
        return run_show(user_input)

    elif mode == "ANALYZE":
        analysis = run_analyze(user_input)
        _last_analysis = analysis
        # After analysis, automatically add recommendations
        recommendation = run_recommend(analysis, user_input)
        return analysis + "\n\n---\n**Recommended Actions:**\n" + recommendation

    elif mode == "ACT":
        _last_analysis = ""
        return run_act(user_input)

    return run_show(user_input)

# ── TEST ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("NetworkAnalyzer Agent — Full Pipeline Test")
    print("=" * 60)

    # 1. Classification test
    tests = [
        ("What is the drop rate in Sfax?",         "SHOW"),
        ("How many critical alarms are active?",    "SHOW"),
        ("Why are customers churning in Tunis?",    "ANALYZE"),
        ("What is causing network issues in Sfax?", "ANALYZE"),
        ("Create an offer for students with 15GB",  "ACT"),
        ("Acknowledge alarm 42",                    "ACT"),
    ]
    print("\n[1] Mode classification:\n")
    correct = 0
    for question, expected in tests:
        mode = classify(question)
        ok = "ok" if mode == expected else "FAIL"
        if mode == expected: correct += 1
        print(f"  [{ok}] {question[:50]} → {mode}")
    print(f"\n  Result: {correct}/{len(tests)} correct")

    # 2. SHOW test
    print("\n" + "=" * 60)
    print("\n[2] SHOW mode — drop rate in Sfax:\n")
    print(run_show("What is the drop rate in Sfax?"))

    # 3. ANALYZE + RECOMMEND test
    print("\n" + "=" * 60)
    print("\n[3] ANALYZE + RECOMMEND — why are customers churning in Tunis?")
    print("(this takes ~60 seconds)\n")
    result = run_agent("Why are customers churning in Tunis?")
    print(result)

    # 4. Follow-up ACT test
    print("\n" + "=" * 60)
    print("\n[4] Follow-up ACT — assign best offer:\n")
    followup = run_agent("go ahead and assign the best offer to them")
    print(followup)