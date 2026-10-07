# NetworkAnalyzer: an LLM analytics copilot for mobile network operators

Ask a network or customer question in plain language ("How many subscribers are on 5G in Ba Sing Se?", "Which region has the most critical alarms?") and get a grounded answer computed from the operator's databases, together with the SQL that produced it.

Built during my end-of-study internship (Feb–Aug 2026). This public version runs on a **fully synthetic operator dataset** (about 50,000 subscribers in a fictional, Avatar-themed geography); no real operator data is included.

## What it does

- **Natural language → validated SQL.** An agent on **AWS Bedrock (Qwen3 32B)** turns the question into SQLite queries over the network and commercial databases, runs them, and answers from the results.
- **Self-correction.** Failing or suspicious SQL is repaired automatically (`_auto_correct_sql`) and retried before an answer is written.
- **Hybrid retrieval.** Relevant schema notes and example queries are fetched with **BM25 + FAISS** (sentence-transformer embeddings), so the prompt only carries what the question needs.
- **Schema knowledge graph.** `schema_graph.json` describes tables, join paths and constraints; the retriever walks it to inject only the joins a question needs, which keeps the prompt small as the schema grows.
- **Anti-hallucination grounding.** Every number and every negative claim in the answer is checked against the query results (`_grounding_issues`, `_ungrounded_negatives`); unsupported claims are rejected instead of shown.
- **Safety.** Destructive or prompt-injection requests ("DROP TABLE…", "ignore your instructions…") are refused.

## Components

| Part | File | Role |
|---|---|---|
| Agent | `NetworkAnalyzer_agent_bedrock.py` | Bedrock agent loop: retrieval, SQL generation, self-correction, grounding |
| Retrieval | `rag_retriever.py`, `schema_graph_retriever.py` | BM25 + FAISS hybrid search; schema-graph walk |
| Chat UI | `server.py`, `static/` | FastAPI + WebSocket chat interface (port 8000); shows the SQL steps behind each answer |
| Dashboard | `dashboard_server.py`, `dashboard_static/` | Network and customer analytics dashboard (alarm rates by region, technology mix, NPS…) |
| Operations portal | `ops_server.py`, `ops_static/` | Human-in-the-loop actions (e.g. retention offers) approved by an operator and published on an **MQTT** action bus |
| Churn scoring | `churn_model.py` | See below |
| Data | `db_simulator.py` | Generates and updates the synthetic operator databases |
| Evaluation | `eval_agent.py` | Golden-set test harness |

## Evaluation

`eval_agent.py` runs a golden set of questions through the agent. Where a question has an objective answer, the harness computes the expected value itself with reference SQL against the same databases, so the checks cannot drift from the data. Categories: metrics, categorical answers, distributions, hallucination traps, safety, routing and empty results.

In the recorded run, **11 of 16 cases passed**: all metric, safety and empty-result cases, and part of the hallucination traps. Results vary between runs, which is itself a finding: LLM agents need repeated evaluation, not a single score.

## Churn: a model that did not transfer

An **XGBoost** churn model (SMOTE-balanced) reaches **AUC-ROC 0.885** on a public Indian telecom dataset (100,000 subscribers). Measured against the features available in this operator's data, it does not transfer: one key usage feature differs by about 300×, and two of the fifteen features are duplicates. The deployed system therefore uses an **explainable churn scorecard** (ARPU decline, unpaid bills, usage decline, engagement, tenure), so the agent can say *why* a subscriber is flagged and an operator can verify it.

## Run it

Requires Python 3.12+, AWS credentials with Bedrock access, and the databases from the `db-backup` branch (see `dbbackup/RESTORE_DB.md` on that branch).

```bash
pip install -r requirements.txt
# .env (never committed): AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION

python server.py            # chat UI → http://localhost:8000
python dashboard_server.py  # analytics dashboard
python ops_server.py        # operations portal (port 8001, needs an MQTT broker on 1883)
python eval_agent.py        # golden-set evaluation
```

## Stack

Python · AWS Bedrock (Qwen3) · FastAPI · WebSockets · SQLite · FAISS · BM25 · sentence-transformers · MQTT · XGBoost · scikit-learn · LangGraph (early prototype in `NetworkAnalyzer_agent_langgraph.py`)
