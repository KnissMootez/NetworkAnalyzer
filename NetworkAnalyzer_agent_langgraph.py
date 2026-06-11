"""
NetworkAnalyzer Agent — LangGraph version
Parallel implementation alongside NetworkAnalyzer_agent_bedrock.py.
Same external interface: run_agent(question) -> dict, _streaming_queue, _stop_event.

Graph structure:
  generate → [route] → execute_query → generate  (loop)
                     → lookup_schema → generate  (loop)
                     → finalize      → END

SQL tooling and schema are imported from the Bedrock agent — not duplicated.
What's intentionally absent vs the Bedrock agent (v1 scope):
  - Chitchat / fast-path detection
  - Composite scoring mode
  - Treemap-specific prompting
  - Proposal / confirm flow
  - Conversation memory compression
"""

import json
import re
import sqlite3
from typing import TypedDict, Literal

from langgraph.graph import StateGraph, END

import NetworkAnalyzer_agent_bedrock as _bedrock_mod
from NetworkAnalyzer_agent_bedrock import (
    query_sc,
    query_op,
    schema_lookup,
    LIVE_SCHEMA,
    NetworkAnalyzer_SCHEMA,
    SC_DB,
    OP_DB,
)
from rag_retriever import retrieve_sql

# ── Module-level vars — server.py sets these on every request ─────────────
_streaming_queue = None
_stop_event = None

MAX_STEPS = 8


# ── State ─────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    question: str
    system_prompt: str
    context: str        # accumulated "Step N [TAG]: ..." log
    steps: list         # human-readable step summaries for the UI
    step_count: int
    action: str         # last parsed action tag
    action_content: str # content following the tag
    result: dict | None
    done: bool


# ── System prompt ─────────────────────────────────────────────────────────

def _build_system() -> str:
    return f"""You are NetworkAnalyzer, an AI analyst for a mobile network operator.

{NetworkAnalyzer_SCHEMA}

LIVE SCHEMA:
{LIVE_SCHEMA}

RULES:
- Output exactly ONE action per response. Never chain multiple actions in one reply.
- Currency is always Yuan (¥) — never use $ or USD.
- Always filter active subscribers: WHERE s.is_active=1. Always use COUNT(DISTINCT s.msisdn) not COUNT(*).
- kpis_daily date filter: always (SELECT MAX(date) FROM kpis_daily) — never date('now')
- customer_value: always WHERE month=(SELECT MAX(month) FROM customer_value)
- 5G upsell candidates: d.is_5g_capable=1 AND EXISTS(SELECT 1 FROM coverage cv WHERE cv.msisdn=s.msisdn AND cv.technology_available='5G')
- subscribers.region and subscribers.nation are direct columns — never JOIN subscribers with sites to get them
- subscriptions: always filter is_current=1
- Write SQL on a single line. No markdown backticks around SQL.

ACTIONS:
QUERY_SC: <sql>        — query NetworkAnalyzer DB (network, devices, coverage, KPIs)
QUERY_OP: <sql>        — query Operator DB (customers, plans, billing, churn)
QUERY_BOTH: <sql>      — cross-DB query; ATTACH operator_new.db AS op, prefix op. tables
SCHEMA: <table_name>   — inspect a table's real columns
CONCLUDE: <json>       — final answer. JSON field order: text, recommendations, strategy_diagram, mindmap, chart

CONCLUDE format (single line JSON):
{{"text": "3-5 sentence analyst summary", "recommendations": [{{"title":"..","description":"..","priority":"high|medium|low"}}], "chart": {{"type":"bar|pie|histogram|scatter|heatmap","title":"..","x":[],"y":[]}}}}

RECOMMENDATIONS: include 2-3 when data reveals an opportunity, risk, or actionable gap. Each must cite a real number from your results. Omit for purely factual questions with no commercial angle.

BEFORE writing CONCLUDE — if you have only 1 query step so far and the question has a commercial angle, you MUST run 1 enrichment query before concluding. Match it to the context:
- Top ARPU / platinum subscribers → check their current plan type or whether they have 5G-capable devices not yet on 5G
- Low ARPU / bronze subscribers → check churn_risk_score to identify who is actually at risk
- Poor KPI (low throughput, high drop rate) → check active alarm count on those cells
- 3G subscriber count → check how many have 4G/5G-capable devices to quantify the migration opportunity
- 5G upsell candidates → check their avg ARPU to estimate revenue impact
- Network alarm result → check which region or technology has the most active critical alarms
Skip enrichment if: purely counting with no commercial angle, already 4+ steps of data, or enrichment duplicates what you already have.
"""


# ── Helper: call LLM (routes through bedrock module, streaming included) ──

def _llm(system: str, prompt: str, max_tokens: int = 1200) -> str:
    return _bedrock_mod._llm(system, prompt, max_tokens=max_tokens)


# ── Helper: parse first action tag from LLM output ────────────────────────

_TAGS = ["SCHEMA:", "QUERY_SC:", "QUERY_OP:", "QUERY_BOTH:", "CONCLUDE:", "PROPOSE:"]

def _parse_action(response: str) -> tuple[str, str]:
    """Return (action_name, content) for the first action tag found."""
    for tag in _TAGS:
        if tag in response:
            idx = response.index(tag)
            content = response[idx + len(tag):].strip()
            # Truncate at any second action tag
            for other in _TAGS:
                if other != tag and other in content:
                    content = content[:content.index(other)].strip()
            return tag.rstrip(":"), content
    return "UNKNOWN", response


# ── Nodes ─────────────────────────────────────────────────────────────────

def generate(state: AgentState) -> dict:
    if _stop_event and _stop_event.is_set():
        return {"done": True}

    if state["step_count"] >= MAX_STEPS:
        prompt = (
            f"Question: {state['question']}\n\n"
            f"Data gathered:\n{state['context']}\n\n"
            f"Max steps reached. Output CONCLUDE now as single-line JSON:\n"
            f"CONCLUDE: {{\"text\": \"your summary\"}}"
        )
    elif state["context"]:
        prompt = (
            f"Question: {state['question']}\n\n"
            f"Data gathered so far:\n{state['context']}\n\n"
            f"Next step — output QUERY_SC, QUERY_OP, QUERY_BOTH, SCHEMA, or CONCLUDE:"
        )
    else:
        sql_examples = retrieve_sql(state["question"], top_k=3)
        examples_block = f"\nRELEVANT SQL EXAMPLES:\n{sql_examples}\n" if sql_examples else ""
        prompt = (
            f"Question: {state['question']}\n"
            f"{examples_block}\n"
            f"No data gathered yet. Begin with a QUERY_SC, QUERY_OP, or SCHEMA action."
        )

    response = _llm(state["system_prompt"], prompt)
    action, content = _parse_action(response)
    return {"action": action, "action_content": content, "step_count": state["step_count"] + 1}


def execute_query(state: AgentState) -> dict:
    tag = state["action"]
    sql = state["action_content"].split("\n")[0].strip()
    step = state["step_count"]

    if tag == "QUERY_SC":
        rows = query_sc(sql)
    elif tag == "QUERY_OP":
        rows = query_op(sql)
    else:  # QUERY_BOTH
        try:
            conn = sqlite3.connect(SC_DB)
            conn.execute(f"ATTACH DATABASE '{OP_DB}' AS op")
            conn.row_factory = sqlite3.Row
            rows = [dict(r) for r in conn.execute(sql).fetchall()]
            conn.close()
        except Exception as e:
            rows = [{"error": str(e)}]

    is_err = bool(rows and "error" in rows[0])
    if _streaming_queue:
        _streaming_queue.put({
            "type": "step_sql", "step": step,
            "tag": tag, "sql": sql[:200],
            "rows": 0 if is_err else len(rows),
            "error": rows[0]["error"] if is_err else None,
        })

    rows_text = json.dumps(rows[:50], default=str)
    new_context = state["context"] + f"\nStep {step} [{tag}]: {sql}\nResult: {rows_text}\n"
    new_steps = state["steps"] + [f"[{tag}] {sql[:120]}"]
    return {"context": new_context, "steps": new_steps}


def lookup_schema(state: AgentState) -> dict:
    table = state["action_content"].strip().split()[0].strip(".,;")
    result = schema_lookup(table)
    step = state["step_count"]
    if _streaming_queue:
        _streaming_queue.put({"type": "step_sql", "step": step, "tag": "SCHEMA", "sql": table, "rows": 0})
    new_context = state["context"] + f"\nStep {step} [SCHEMA {table}]:\n{result}\n"
    return {"context": new_context}


def finalize(state: AgentState) -> dict:
    raw = state["action_content"].strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE).strip()

    try:
        data = json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        try:
            data = json.loads(m.group(0)) if m else {}
        except Exception:
            data = {}

    result = {
        "type": "analysis",
        "text": data.get("text", raw[:500] if raw else "No summary produced."),
        "chart": data.get("chart"),
        "recommendations": data.get("recommendations"),
        "strategy_diagram": data.get("strategy_diagram"),
        "mindmap": data.get("mindmap"),
        "steps_log": state["steps"],
    }
    return {"result": result, "done": True}


# ── Router ────────────────────────────────────────────────────────────────

def route(state: AgentState) -> Literal["execute_query", "lookup_schema", "finalize", "generate", "__end__"]:
    if state.get("done") or (_stop_event and _stop_event.is_set()):
        return "__end__"
    action = state.get("action", "")
    if action in ("QUERY_SC", "QUERY_OP", "QUERY_BOTH"):
        return "execute_query"
    if action == "SCHEMA":
        return "lookup_schema"
    if action in ("CONCLUDE", "PROPOSE"):
        return "finalize"
    return "generate"


# ── Build graph ───────────────────────────────────────────────────────────

_builder = StateGraph(AgentState)
_builder.add_node("generate", generate)
_builder.add_node("execute_query", execute_query)
_builder.add_node("lookup_schema", lookup_schema)
_builder.add_node("finalize", finalize)
_builder.set_entry_point("generate")
_builder.add_conditional_edges("generate", route)
_builder.add_edge("execute_query", "generate")
_builder.add_edge("lookup_schema", "generate")
_builder.add_edge("finalize", END)

graph = _builder.compile()


# ── Public interface ───────────────────────────────────────────────────────

def run_agent(user_input: str) -> dict:
    initial: AgentState = {
        "question": user_input,
        "system_prompt": _build_system(),
        "context": "",
        "steps": [],
        "step_count": 0,
        "action": "",
        "action_content": "",
        "result": None,
        "done": False,
    }
    final_state = graph.invoke(initial)
    return final_state.get("result") or {
        "type": "analysis",
        "text": "Agent did not reach a conclusion.",
        "steps_log": final_state.get("steps", []),
    }
