---
name: agent-behavior-feedback
description: What works and what to avoid when modifying the SmartCareLLM agent and frontend
metadata: 
  node_type: memory
  type: feedback
  originSessionId: eb35e8f4-9c89-48df-9d3c-4d10b4ccdae0
---

Don't use small models (≤4B) for the SQL reasoning agent — they ignore the single-action-per-step format and dump the entire chain in one response.
**Why:** gemma4:e2b (2B) tested and failed — outputs multiple QUERY+CONCLUDE blocks at once, hallucinated numbers.
**How to apply:** When suggesting model alternatives, require at least 7-8B for reliable agent behavior.

There is NO local LLM. The agent runs entirely on AWS Bedrock — `qwen.qwen3-32b-v1:0` (env BEDROCK_MODEL, qwen3-next-80b is the larger option). Ollama / local qwen3:8b / RTX-GPU inference were fully removed.
**Why:** Local 7-8B on the RTX 3050 was the early approach but quality + VRAM limits drove the switch to Bedrock 32B; local paths are gone.
**How to apply:** Never suggest local/Ollama/GPU-layer options or qwen3:8b. All model talk = Bedrock model ids.

subscribers.region is a direct column — never join subscribers with sites to get region.
**Why:** The agent kept joining subscribers JOIN sites ON region which multiplies rows. subscribers has its own region column.
**How to apply:** In SQL examples and RAG, always use `SELECT region, COUNT(*) FROM subscribers GROUP BY region` directly.

kpis_daily date filter must use `(SELECT MAX(date) FROM kpis_daily)` not `date('now')`.
**Why:** Simulator doesn't run in real-time; latest date is 2026-03-30. date('now') returns 0 rows.
**How to apply:** Always use MAX(date) subquery for "latest" KPI queries.

customer_value has 6 rows per subscriber (one per month) — always filter by month.
**Why:** Without month filter, AVG(arpu) and COUNT are inflated 6x.
**How to apply:** Canonical: `WHERE cv.month=(SELECT MAX(month) FROM customer_value)`

Never add hardcoded SQL patterns to the system prompt to fix agent mistakes.
**Why:** User explicitly called this out as "whack-a-mole" — fixing one query pattern breaks the next. The self-correction engine (ColCheck → real column names injected → model retries) and RAG SQL examples are the right fix.
**How to apply:** When the agent uses wrong columns or wrong tables, fix via: (1) ColCheck error message enrichment, (2) RAG example in rag_sql_examples.txt, (3) routing logic (_is_op_only). Never via AGENT_SYSTEM QUERY PATTERNS.

RAG examples fix KNOWLEDGE gaps; they are whack-a-mole for REASONING/SYNTHESIS bugs.
**Why:** User refused a RAG example to fix a composite-ranking bug. RAG/ColCheck only help when the model doesn't KNOW something (which column/table/join). When the model gathered correct data but reasoned badly about combining/ranking it, a worked example only fixes that one question — every other composite question repeats the failure. That IS whack-a-mole. Concrete case (2026-06-17): "which regions for 3G migration" — agent ran 2 separate queries (count per region; ARPU per region), never merged them, then ranked by ARPU while quoting counts → surfaced #4-by-volume region as #1 and a non-top-8 region as #2. Root cause: no consolidation step; recency bias toward the ARPU query; enrichment reflex bolting on a commercial angle.
**How to apply:** For reasoning/synthesis failures, fix with a GENERAL behavioral principle, not a SQL example. Added a RANKING & COMPARISON rule to both AGENT_SYSTEM and AGENT_SYSTEM_TOOLS (~line 1974 / 3663): compute all metrics in ONE grouped query, ORDER BY the asked metric (default volume/count), keep secondary metrics on the same row, never rank by one metric while quoting another, reconcile separate queries before concluding. A principle ("how to think about ranking") is NOT whack-a-mole; a per-question SQL example is. The bigger structural fixes (a forced consolidation/planner step, or a deterministic merge/rank tool) were offered but user chose the lightweight principle first — revisit if composite questions still fail.

FWA candidates definition in this DB: mobility_class IN ('stationary','low_mobility') AND total_data_gb>30 from mobility_profile JOIN dou_monthly.
**Why:** The model kept returning 89% of the subscriber base (no filter) when asked about FWA candidates. Fixed by adding correct RAG examples.
**How to apply:** If FWA queries return suspiciously large counts, check if the mobility + data filter was applied.

Chart builder: when query results contain msisdn, NEVER build a treemap — aggregate by region/technology/segment and build a bar chart instead.
**Why:** 97-subscriber result rendered as a 97-tile treemap of phone numbers — completely unreadable.
**How to apply:** _build_treemap_from_context checks for "msisdn" in result keys and routes to bar chart. _chart_from_context prefers categorical label cols over high-cardinality IDs.

Schema graph retriever (schema_graph_retriever.py) and intent classifier are the right place to fix join-path routing — not the system prompt.
**Why:** System prompt already very long. Graph retriever injects only relevant subgraph per query, keeping context tight as schema grows.
**How to apply:** Add new tables/relationships to schema_graph.json. Add intent keywords to _INTENT_PATTERNS. Don't hardcode join paths in AGENT_SYSTEM.

Semantic cache threshold is 0.92 cosine similarity — very high intentionally.
**Why:** Lower threshold would return cached results for related-but-different questions (e.g. "drop rate in Fire Nation" vs "drop rate in Water Tribe").
**How to apply:** Don't lower the threshold. Cache is session-scoped and cleared by Forget Memory.
