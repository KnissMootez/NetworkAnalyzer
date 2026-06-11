"""
NetworkAnalyzer Agent — optimized for qwen2.5:14b.
Imports shared DB / chart / action infrastructure from NetworkAnalyzer_agent.py.
Only the system prompt, reasoning chain, and memory live here.
"""

import re
import json
from NetworkAnalyzer_agent import (
    # databases
    query_sc, query_op, write_sc, write_op, SC_DB, OP_DB,
    # schema
    FULL_SCHEMA,
    # LLM call + SQL validator
    _ollama, _check_sql,
    # chart builders
    _build_treemap_from_context, _to_chart_spec,
    # action execution
    _execute_action,
    # RAG
    _should_use_rag, retrieve,
    # confirmation helpers
    _is_confirmation, _is_denial,
    # proactive alerts (model-agnostic)
    get_proactive_alerts,
)

# ═══════════════════════════════════════════════════════════════════════
# 14b SYSTEM PROMPT — clean, high-trust, no hand-holding
# ═══════════════════════════════════════════════════════════════════════

AGENT_SYSTEM = """{rag_context}

{schema}

You are a senior telecom analyst for a Tunisian mobile operator.
Query the databases to gather data, then deliver concise, actionable insights.

At each step output EXACTLY ONE of:
  QUERY_SC: <sql>     — NetworkAnalyzer DB
  QUERY_OP: <sql>     — Operator DB
  QUERY_BOTH: <sql>   — Cross-DB (ATTACH DATABASE, op. prefix for operator tables)
  CONCLUDE: <json>    — Final answer when you have sufficient data
  PROPOSE: <text>     — Action proposal with exact counts and TND impact; needs confirmation

Rules:
- SQL must be valid SQLite, SELECT only, single line, no markdown.
- Never select raw msisdn lists — always aggregate.
- Never add LIMIT to GROUP BY region queries.
- PROPOSE requires at least 2 successful queries first.
- All prices in TND. Never invent data.

CONCLUDE format (single-line JSON, no commas inside numbers):
{"text": "your analysis", "chart": {<spec>}}
Chart types: bar, pie, line, area, multibar, scatter, histogram, heatmap, treemap
Treemap requires: labels[], parents[], values[] — root node parent is ""
Omit chart if nothing useful to show.
"""

# ═══════════════════════════════════════════════════════════════════════
# MEMORY
# ═══════════════════════════════════════════════════════════════════════

_memory:  list = []
_pending: dict | None = None


def _compress(text: str) -> str:
    if len(text) <= 200:
        return text
    if len(text) <= 400:
        return text[:300] + "…"
    return _ollama(
        "Summarize in 2-3 sentences. Keep key numbers.",
        text, max_tokens=80
    )


def _memory_context() -> str:
    if not _memory:
        return ""
    lines = [f"{m['role'].upper()}: {m['summary']}" for m in _memory[-4:]]
    return "Recent context:\n" + "\n".join(lines) + "\n\n"


def reset_memory():
    global _memory, _pending
    _memory  = []
    _pending = None


# ═══════════════════════════════════════════════════════════════════════
# REASONING CHAIN — 14b version
# ═══════════════════════════════════════════════════════════════════════

def _run_chain(question: str, max_steps: int = 10) -> dict:
    rag_block = ""  # 14b has sufficient telecom domain knowledge — RAG skipped to save prefill

    system = AGENT_SYSTEM\
        .replace("{rag_context}", rag_block)\
        .replace("{schema}", FULL_SCHEMA)

    q_lower = question.lower()
    is_treemap = any(w in q_lower for w in
        ("drilldown", "drill down", "drill-down", "tree", "treemap", "hierarchy"))

    if is_treemap:
        system += (
            "\n\nThis question requires a TREEMAP. Query the schema to find the right tables and columns, "
            "then run ONE query with 2-3 GROUP BY columns. "
            "The chart is built automatically from your query results — "
            "output CONCLUDE: {\"text\": \"your analysis here\"}"
        )

    context    = ""
    steps_log  = []

    for step in range(max_steps):
        # Build prompt
        if not context:
            prompt = (
                f"Question: {question}\n\n"
                f"Run a QUERY_SC or QUERY_OP to retrieve data first. "
                f"Output only: QUERY_SC: <sql>"
            )
        else:
            queries_done = context.count("Step ")
            if queries_done >= 6 or (is_treemap and queries_done >= 1):
                if is_treemap:
                    prompt = (
                        f"Question: {question}\n\nData:\n{context}\n\n"
                        f"Output: CONCLUDE: {{\"text\": \"your analysis here\"}}"
                    )
                else:
                    prompt = (
                        f"Question: {question}\n\nData gathered:\n{context}\n\n"
                        f"Sufficient data collected. Output CONCLUDE: <json> or PROPOSE: <text>."
                        f"{rag_block}"
                    )
            else:
                prompt = (
                    f"Question: {question}\n\nData so far:\n{context}\n\n"
                    f"Next step — QUERY_SC, QUERY_OP, QUERY_BOTH, CONCLUDE, or PROPOSE:"
                )

        _is_conclude = bool(context)
        _max_tok = 200 if (is_treemap and _is_conclude) else (1000 if _is_conclude else 400)
        response = _ollama(system, prompt, max_tokens=_max_tok, timeout=480)

        if response.startswith("ERROR:"):
            if context:
                break
            return {"type": "error", "text": response}

        steps_log.append(response[:500])

        # ── CONCLUDE ──────────────────────────────────────────────────
        if "CONCLUDE:" in response:
            if context.count("Step ") == 0:
                context += f"\nStep {step+1}: CONCLUDE rejected — no data yet. Run a query first.\n"
                continue

            conclusion = response.split("CONCLUDE:", 1)[1].strip()
            # Strip markdown code fences the model sometimes wraps output in
            conclusion = re.sub(r'^```(?:json)?\s*', '', conclusion).rstrip('`').strip()
            for tag in ["QUERY_SC:", "QUERY_OP:", "QUERY_BOTH:", "Step "]:
                if tag in conclusion:
                    conclusion = conclusion.split(tag)[0].strip()
            conclusion = conclusion.rstrip(".,: \n")

            chart_spec = None
            text_only  = conclusion
            try:
                m = re.search(r'\{.*\}', conclusion, re.DOTALL)
                if m:
                    raw = re.sub(r'(\d),(\d{3})', r'\1\2', m.group())
                    try:
                        parsed    = json.loads(raw)
                        text_only = parsed.get("text", conclusion)
                        if not is_treemap:
                            chart_spec = parsed.get("chart")
                            if chart_spec is None and "labels" in parsed:
                                chart_spec = {k: parsed[k] for k in
                                    ("type", "title", "labels", "parents", "values") if k in parsed}
                                chart_spec.setdefault("type", "treemap")
                    except json.JSONDecodeError:
                        tm = re.search(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
                        text_only = tm.group(1) if tm else re.sub(r'\{.*', '', conclusion, flags=re.DOTALL).strip()
                    text_only = re.sub(r'\{.*\}', '', text_only, flags=re.DOTALL).strip()
                    for tag in ["PROPOSE:", "QUERY_SC:", "QUERY_OP:"]:
                        if tag in text_only:
                            text_only = text_only.split(tag)[0].strip()
            except Exception:
                text_only = re.sub(r'\{.*\}', '', conclusion, flags=re.DOTALL).strip()

            if is_treemap and context:
                chart_spec = _build_treemap_from_context(context)
            if chart_spec is None:
                full_ctx = f"{text_only}\n\nRaw data:\n{context[-1500:]}" if context else text_only
                chart_spec = _to_chart_spec(full_ctx, question)

            return {"type": "analysis", "text": text_only, "chart": chart_spec, "steps": steps_log}

        # ── PROPOSE ───────────────────────────────────────────────────
        if "PROPOSE:" in response:
            # Require at least 2 successful queries before proposing
            successful_queries = context.count("Results (")
            if successful_queries < 2:
                context += (
                    f"\nStep {step+1}: PROPOSE rejected — only {successful_queries} successful "
                    f"quer{'y' if successful_queries == 1 else 'ies'} so far. "
                    f"Run at least 2 queries before proposing.\n"
                )
                continue

            proposal = response.split("PROPOSE:", 1)[1].strip()
            for tag in ["QUERY_SC:", "QUERY_OP:", "QUERY_BOTH:", "Step "]:
                if tag in proposal:
                    proposal = proposal.split(tag)[0].strip()
            proposal = proposal.rstrip(".,: \n")

            # Don't return an empty or tag-only proposal
            if len(proposal) < 20:
                context += f"\nStep {step+1}: PROPOSE text too short or empty. Write a full proposal with counts and TND impact.\n"
                continue

            return {
                "type": "proposal", "text": proposal,
                "steps": steps_log, "pending_confirmation": True
            }

        # ── QUERY ─────────────────────────────────────────────────────
        executed = False
        for tag, runner in [
            ("QUERY_BOTH:", query_sc),
            ("QUERY_SC:",   query_sc),
            ("QUERY_OP:",   query_op),
        ]:
            if tag not in response:
                continue
            for line in response.split("\n"):
                stripped = line.strip()
                if not stripped.startswith(tag):
                    continue
                sql_raw = stripped.split(tag, 1)[1].strip()
                sql = re.sub(r'```sql|```', '', sql_raw).strip()

                if tag == "QUERY_BOTH:" and "ATTACH" not in sql.upper():
                    sql = f"ATTACH DATABASE '{OP_DB}' AS op; " + sql

                if not sql.upper().startswith(("SELECT", "ATTACH")):
                    context += f"\nStep {step+1}: Invalid SQL skipped.\n"
                    executed = True
                    break

                db_path = OP_DB if tag == "QUERY_OP:" else SC_DB
                err = _check_sql(sql, db_path)
                if err:
                    context += f"\nStep {step+1}: {err}\n"
                    executed = True
                    break

                results = runner(sql)
                if results and "error" not in results[0]:
                    n = len(results)
                    if n <= 30:
                        snippet = json.dumps(results, indent=2)
                    else:
                        snippet = "[" + ", ".join(str(dict(r)) for r in results) + "]"
                    context += (
                        f"\nStep {step+1} [{tag.rstrip(':')}]:\n"
                        f"SQL: {sql[:120]}\n"
                        f"Results ({n} rows): {snippet}\n"
                    )
                else:
                    err_msg = results[0].get("error", "no results") if results else "no results"
                    context += f"\nStep {step+1}: Query error: {err_msg}\n"
                executed = True
                break
            if executed:
                break

        if not executed:
            context += f"\nStep {step+1}: No valid query detected. Output QUERY_SC: <sql>\n"

    # Fallback: summarise whatever we gathered
    if context:
        summary = _ollama(
            "You are a telecom analyst. Summarize findings and recommend actions.",
            f"Question: {question}\n\nData:\n{context}\n\nAnalysis:",
            max_tokens=400
        )
        chart_spec = _build_treemap_from_context(context) if is_treemap else None
        if chart_spec is None:
            chart_spec = _to_chart_spec(f"{summary}\n\nData:\n{context[-1500:]}", question)
        return {"type": "analysis", "text": summary, "chart": chart_spec, "steps": steps_log}

    return {"type": "error", "text": "Could not gather enough data to answer this question."}


# ═══════════════════════════════════════════════════════════════════════
# FAST PATH — simple single-query questions
# ═══════════════════════════════════════════════════════════════════════

COMPLEX_PATTERNS = [
    "campaign", "propose", "upsell", "migrate", "recommend", "suggest",
    "should we", "how do we", "which subscribers", "find candidates",
    "create", "launch", "assign", "compare",
    "drilldown", "drill down", "drill-down", "tree", "treemap", "hierarchy",
    "breakdown", "by region", "per region", "each region", "all regions",
    "distribution", "sunset", "analyze", "analysis", "insight",
]


def _is_complex(question: str) -> bool:
    q = question.lower()
    return any(p in q for p in COMPLEX_PATTERNS) or len(q.split()) > 10


def _fast_query(question: str) -> dict:
    rag_block = ""  # 14b skips RAG
    sql_system = (
        f"You are a telecom SQL analyst.\n{rag_block}\n\n{FULL_SCHEMA}\n\n"
        "Output ONE SQL query. No explanation. No markdown.\n"
        "NEVER SELECT raw msisdn lists. Aggregate with COUNT(*) or GROUP BY.\n"
        "kpis_daily has no msisdn and no technology column.\n"
        "subscribers.region is the subscriber region — no need to join sites.\n"
    )
    sql_raw = _ollama(sql_system, f"Question: {question}\nSQL:", max_tokens=250)
    sql = sql_raw.strip().strip("```sql").strip("```").strip()

    runner = query_sc
    if "ATTACH" in sql.upper() or " op." in sql:
        runner = query_sc
    elif any(k in question.lower() for k in
             ["plan", "customer", "billing", "offer", "campaign", "subscription"]):
        runner = query_op

    results = runner(sql)
    if not results or "error" in results[0]:
        return _run_chain(question)

    chart_spec = None
    keys = list(results[0].keys())
    if len(keys) == 2:
        try:
            x_vals = [str(r[keys[0]]) for r in results]
            y_vals = [float(r[keys[1]]) if isinstance(r[keys[1]], float)
                      else int(r[keys[1]]) for r in results]
            q_lower = question.lower()
            if any(w in q_lower for w in ["treemap","tree","drill","hierarchy"]):
                chart_spec = {"type":"treemap","title":question[:60],
                              "labels":["Total"]+x_vals,
                              "parents":[""]+["Total"]*len(x_vals),
                              "values":[sum(y_vals)]+y_vals}
            elif any(w in q_lower for w in ["proportion","share","pie"]):
                chart_spec = {"type":"pie","title":question[:60],"x":x_vals,"y":y_vals}
            else:
                chart_spec = {"type":"bar","title":question[:60],"x":x_vals,"y":y_vals,
                              "x_label":keys[0].replace("_"," ").title(),
                              "y_label":keys[1].replace("_"," ").title()}
        except (ValueError, TypeError):
            pass

    snippet = results[:12]
    note    = f" ({len(results)} total)" if len(results) > 12 else ""
    text    = _ollama(
        "Telecom analyst. Concise 1-2 sentence summary of ONLY the data shown. "
        "No invented numbers. Plain text only.",
        f"Question: {question}\nData{note}: {snippet}\nSummary:",
        max_tokens=150
    ).strip()

    if chart_spec is None:
        chart_spec = _to_chart_spec(json.dumps(results[:24]), question)

    return {"type": "analysis", "text": text, "chart": chart_spec}


# ═══════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════

def run_agent(user_input: str) -> dict:
    global _pending, _memory

    if _pending and _is_confirmation(user_input):
        proposal = _pending; _pending = None
        result = _execute_action(proposal["text"], proposal["question"])
        _memory.append({"role": "agent", "summary": _compress(result["text"])})
        return result

    if _pending and _is_denial(user_input):
        _pending = None
        return {"type": "cancelled", "text": "Action cancelled. No changes were made."}

    mem_ctx  = _memory_context()
    enriched = f"{mem_ctx}{user_input}" if mem_ctx else user_input
    _memory.append({"role": "user", "summary": user_input})

    # Resolve short follow-ups with memory context
    resolved = enriched
    if len(user_input.split()) <= 4 and mem_ctx:
        resolved = _ollama(
            "Rewrite as a complete standalone question using the context. Output only the question.",
            f"Context: {mem_ctx}\nFollow-up: {user_input}\nComplete question:",
            max_tokens=60
        ).strip().strip('"')

    result = _run_chain(resolved) if _is_complex(resolved) else _fast_query(resolved)

    if result.get("type") == "proposal":
        _pending = {"text": result["text"], "question": user_input}
        result["text"] += "\n\n---\n**Type 'confirm' to proceed or 'cancel' to abort.**"

    _memory.append({"role": "agent", "summary": _compress(result.get("text", ""))})
    return result
