"""
Schema Knowledge Graph Retriever
Walks the schema graph to inject only relevant join paths and constraints
for a given question — keeps system prompt tight as schema grows.
"""
import json
import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_GRAPH_PATH = os.path.join(BASE_DIR, "schema_graph.json")

_graph = None

def _load_graph():
    global _graph
    if _graph is None:
        with open(_GRAPH_PATH, "r", encoding="utf-8") as f:
            _graph = json.load(f)
    return _graph

# ── Keyword → table mapping ──────────────────────────────────────────────

_KEYWORD_SHORTCUTS = {
    "arpu":       ["customer_value"],
    "hvc":        ["customer_value"],
    "high value": ["customer_value"],
    "churn":      ["customer_value"],
    "revenue":    ["customer_value", "billing"],
    "drop rate":  ["kpis_daily"],
    "throughput": ["kpis_daily"],
    "latency":    ["kpis_daily"],
    "availability": ["kpis_daily"],
    "signal":     ["kpis_daily", "coverage"],
    "rsrp":       ["kpis_daily"],
    "sinr":       ["kpis_daily"],
    "5g":         ["subscriber_technology", "devices", "coverage"],
    "3g":         ["subscriber_technology"],
    "4g":         ["subscriber_technology"],
    "volte":      ["subscriber_technology", "devices"],
    "fwa":        ["mobility_profile"],
    "stationary": ["mobility_profile"],
    "data usage": ["dou_monthly"],
    "gaming":     ["qoe_daily"],
    "video":      ["qoe_daily"],
    "experience": ["qoe_daily"],
    "alarm":      ["network_alarms"],
    "fault":      ["network_alarms"],
    "outage":     ["network_alarms"],
    "plan":       ["subscriptions", "plans"],
    "subscription": ["subscriptions"],
    "billing":    ["billing"],
    "campaign":   ["offers"],
    "upsell":     ["customer_value", "subscriber_technology", "devices"],
    "region":     ["subscribers", "sites"],
    "city":       ["subscribers", "sites"],
    "nation":     ["subscribers", "sites"],
    "device":     ["devices"],
    "brand":      ["devices"],
    "os":         ["devices"],
    "roaming":    ["subscriber_technology"],
    "coverage":   ["coverage"],
    "kpi":        ["kpis_daily"],
}

def _match_tables(question: str) -> set:
    """Return set of table names most relevant to this question."""
    q = question.lower()
    g = _load_graph()
    matched = set()

    # Direct keyword shortcuts
    for kw, tables in _KEYWORD_SHORTCUTS.items():
        if kw in q:
            matched.update(tables)

    # Match table names directly mentioned
    for tbl in g["tables"]:
        if tbl.replace("_", " ") in q or tbl in q:
            matched.add(tbl)

    # Match table tags
    for tbl, meta in g["tables"].items():
        for tag in meta.get("tags", []):
            if tag in q:
                matched.add(tbl)

    return matched

def _match_join_paths(question: str, matched_tables: set) -> list:
    """Return join path keys relevant to this question."""
    q = question.lower()
    g = _load_graph()
    paths = []
    for path_key, path_meta in g["join_paths"].items():
        # Match by tags
        if any(tag in q for tag in path_meta.get("tags", [])):
            paths.append(path_key)
            continue
        # Match by tables overlap
        path_tables = set(path_meta.get("tables", []))
        if len(path_tables & matched_tables) >= 2:
            paths.append(path_key)
    return paths

def _relevant_constraints(matched_tables: set) -> list:
    """Return constraint strings for matched tables."""
    g = _load_graph()
    constraints = []
    for tbl, constraint in g["constraints"].items():
        if tbl in matched_tables:
            constraints.append(constraint)
    return constraints

def _relevant_wrong_columns(question: str, matched_tables: set) -> list:
    """Return wrong column warnings relevant to matched tables."""
    g = _load_graph()
    q = question.lower()
    warnings = []
    for col_ref, warning in g["wrong_columns"].items():
        tbl = col_ref.split(".")[0]
        col = col_ref.split(".")[1]
        if tbl in matched_tables or col.replace("_", " ") in q:
            warnings.append(f"WRONG: {col_ref} — {warning}")
    return warnings

def retrieve_graph_context(question: str, verbose: bool = False) -> str:
    """
    Main entrypoint. Given a question, returns a compact context string
    with relevant join paths, constraints, and wrong-column warnings.
    Returns empty string if nothing relevant found (e.g. chitchat).
    """
    g = _load_graph()
    matched_tables = _match_tables(question)
    if not matched_tables:
        return ""

    path_keys = _match_join_paths(question, matched_tables)
    constraints = _relevant_constraints(matched_tables)
    wrong_cols = _relevant_wrong_columns(question, matched_tables)

    if not path_keys and not constraints and not wrong_cols:
        return ""

    lines = ["=== SCHEMA GRAPH HINTS (most relevant join paths for this question) ==="]

    # Join paths
    if path_keys:
        lines.append("\nRELEVANT JOIN PATHS:")
        for pk in path_keys[:4]:  # cap at 4 to keep context short
            pm = g["join_paths"][pk]
            lines.append(f"  [{pm['description']}]")
            sql_hint = f"    FROM {pm['sql_pattern']}"
            if pm.get("filter"):
                sql_hint += f" {pm['filter']}"
            if pm.get("group_by"):
                sql_hint += f" {pm['group_by']}"
            if pm.get("requires_attach"):
                sql_hint += " ← ATTACH DATABASE 'operator_new.db' AS op required"
            lines.append(sql_hint)

    # Constraints
    if constraints:
        lines.append("\nMANDATORY FILTERS (missing these inflates or breaks results):")
        for c in constraints:
            lines.append(f"  • {c}")

    # Wrong columns
    if wrong_cols:
        lines.append("\nCOMMON COLUMN MISTAKES (avoid these):")
        for w in wrong_cols[:4]:
            lines.append(f"  • {w}")

    if verbose:
        print(f"[GraphRetriever] matched_tables={matched_tables}, paths={path_keys}, constraints={len(constraints)}, wrong_cols={len(wrong_cols)}")

    return "\n".join(lines)


# ── Intent Classifier ─────────────────────────────────────────────────────

_INTENT_PATTERNS = {
    "chitchat": [
        r"\b(hello|hi|hey|thanks|thank you|who are you|what are you|what can you do|help me|what is your name)\b",
        r"^(hi|hello|hey|thanks|ok|okay|great|good)[\s!.]*$",
    ],
    "network_kpi": [
        r"\b(throughput|drop rate|dropped call|latency|availability|rsrp|sinr|signal|congestion|kpi|cell|site|network quality|performance)\b",
    ],
    "alarm": [
        r"\b(alarm|alarms|fault|outage|incident|degradation|coverage hole|interference|backhaul|power issue)\b",
    ],
    "subscriber_tech": [
        r"\b(3g|4g|5g|2g|volte|vowifi|technology|migration|sunset|device|brand|os|capability|upsell|fwa|stationary|mobile)\b",
    ],
    "commercial": [
        r"\b(arpu|hvc|high value|churn|churn risk|revenue|billing|plan|subscription|offer|campaign|segment|platinum|gold|silver|bronze|payment)\b",
    ],
    "cross_domain": [
        r"\b(worst|best|compare|correlation|link|impact|affect|combined|together)\b",
    ],
    "geography": [
        r"\b(region|city|nation|area|zone|ba sing se|omashu|fire nation|water tribe|earth kingdom|air nomad)\b",
    ],
}

_INTENT_PRIORITY = ["chitchat", "alarm", "network_kpi", "commercial", "subscriber_tech", "cross_domain", "geography"]

def classify_intent(question: str) -> str:
    """
    Classify question intent. Returns one of:
    chitchat | network_kpi | alarm | subscriber_tech | commercial | cross_domain | geography | general
    """
    q = question.lower()
    scores = {intent: 0 for intent in _INTENT_PATTERNS}

    for intent, patterns in _INTENT_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, q):
                scores[intent] += 1

    # Multi-domain detection
    domain_hits = sum(1 for i in ["network_kpi", "commercial", "subscriber_tech"] if scores[i] > 0)
    if domain_hits >= 2:
        scores["cross_domain"] += 2

    # Return highest scoring intent (with priority tiebreak)
    best_intent = max(_INTENT_PRIORITY, key=lambda i: scores.get(i, 0))
    if scores.get(best_intent, 0) == 0:
        return "general"
    return best_intent


if __name__ == "__main__":
    tests = [
        "find HVC subscribers on 3G with high churn risk in regions where drop rate exceeds network average",
        "what is the average throughput in Ba Sing Se?",
        "show me active alarms in Fire Nation",
        "how many FWA candidates do we have by region?",
        "which subscribers are churn risks with the most revenue exposure?",
        "hello, what can you do?",
        "compare 5G upsell candidates with their ARPU",
    ]
    for q in tests:
        ctx = retrieve_graph_context(q, verbose=True)
        intent = classify_intent(q)
        print(f"\nQ: {q}")
        print(f"  Intent: {intent}")
        if ctx:
            print(f"  Graph context:\n{ctx[:300]}...")
        else:
            print("  No graph context (chitchat or unmatched)")
