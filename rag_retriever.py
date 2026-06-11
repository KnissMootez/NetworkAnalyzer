import os
import re
from sentence_transformers import SentenceTransformer
import faiss
import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KNOWLEDGE_FILE = os.path.join(BASE_DIR, "rag_knowledge.txt")
SQL_EXAMPLES_FILE = os.path.join(BASE_DIR, "rag_sql_examples.txt")

# BM25 — optional, falls back gracefully if not installed
try:
    from rank_bm25 import BM25Okapi
    _HAS_BM25 = True
except ImportError:
    _HAS_BM25 = False
    print("[RAG] rank_bm25 not installed — using FAISS-only retrieval. Run: pip install rank-bm25")

# ═══════════════════════════════════════
# PARSE KNOWLEDGE FILE
# ═══════════════════════════════════════

def load_documents(path: str) -> list:
    docs = []
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    raw = content.split("==DOC==")
    for block in raw:
        block = block.strip()
        if not block or "==END==" not in block:
            continue
        body = block.split("==END==")[0].strip()
        title_match = re.search(r"title:\s*(.+)", body)
        tags_match  = re.search(r"tags:\s*(.+)", body)
        title = title_match.group(1).strip() if title_match else "Unknown"
        tags  = tags_match.group(1).strip()  if tags_match  else ""
        text  = re.sub(r"title:.*|tags:.*", "", body).strip()
        docs.append({"title": title, "tags": tags, "text": text})
    return docs

# ═══════════════════════════════════════
# BUILD INDEX
# ═══════════════════════════════════════

_model    = None
_index    = None
_docs     = None
_bm25     = None   # BM25 index for knowledge docs

def set_shared_model(model) -> None:
    """Inject an already-loaded SentenceTransformer to avoid loading it twice."""
    global _model
    if _model is None:
        _model = model

def _tokenize(text: str) -> list:
    return re.findall(r'\w+', text.lower())

def _build_index():
    global _model, _index, _docs, _bm25
    if _index is not None:
        return
    print("Loading RAG model and building index...")
    _docs  = load_documents(KNOWLEDGE_FILE)
    if _model is None:
        _model = SentenceTransformer("all-MiniLM-L6-v2", local_files_only=True)
    texts  = [f"{d['title']} {d['tags']} {d['text']}" for d in _docs]
    embeddings = _model.encode(texts, convert_to_numpy=True)
    dim    = embeddings.shape[1]
    _index = faiss.IndexFlatL2(dim)
    _index.add(embeddings.astype(np.float32))
    if _HAS_BM25:
        tokenized = [_tokenize(t) for t in texts]
        _bm25 = BM25Okapi(tokenized)
    print(f"  RAG index built: {len(_docs)} documents (BM25={'yes' if _HAS_BM25 else 'no'})")

# ═══════════════════════════════════════
# RETRIEVE
# ═══════════════════════════════════════

_sql_index = None
_sql_docs  = None
_sql_bm25  = None   # BM25 index for SQL examples

def load_sql_examples(path: str) -> list:
    docs = []
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    raw = content.split("==DOC==")
    for block in raw:
        block = block.strip()
        if not block or "==END==" not in block:
            continue
        body = block.split("==END==")[0].strip()
        title_match    = re.search(r"title:\s*(.+)", body)
        tags_match     = re.search(r"tags:\s*(.+)", body)
        question_match = re.search(r"question:\s*(.+)", body)
        db_match       = re.search(r"db:\s*(.+)", body)
        sql_match      = re.search(r"sql:\s*(.+)", body)
        if not sql_match:
            continue
        note_match = re.search(r"note:\s*(.+)", body)
        docs.append({
            "title":    title_match.group(1).strip()    if title_match    else "",
            "tags":     tags_match.group(1).strip()     if tags_match     else "",
            "question": question_match.group(1).strip() if question_match else "",
            "db":       db_match.group(1).strip()       if db_match       else "SC",
            "sql":      sql_match.group(1).strip(),
            "note":     note_match.group(1).strip()     if note_match     else "",
        })
    return docs

def _build_sql_index():
    global _model, _sql_index, _sql_docs, _sql_bm25
    if _sql_index is not None:
        return
    if not os.path.exists(SQL_EXAMPLES_FILE):
        return
    print("Building SQL examples RAG index...")
    _sql_docs = load_sql_examples(SQL_EXAMPLES_FILE)
    if _model is None:
        _build_index()
    texts = [f"{d['title']} {d['tags']} {d['question']}" for d in _sql_docs]
    embeddings = _model.encode(texts, convert_to_numpy=True)
    dim = embeddings.shape[1]
    _sql_index = faiss.IndexFlatL2(dim)
    _sql_index.add(embeddings.astype(np.float32))
    if _HAS_BM25:
        tokenized = [_tokenize(t) for t in texts]
        _sql_bm25 = BM25Okapi(tokenized)
    print(f"  SQL RAG index built: {len(_sql_docs)} examples (BM25={'yes' if _HAS_BM25 else 'no'})")


def _rrf_fusion(faiss_indices: list, bm25_indices: list, k: int = 60) -> list:
    """Reciprocal Rank Fusion — combines FAISS and BM25 rankings into a single score."""
    scores = {}
    for rank, idx in enumerate(faiss_indices):
        scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank + 1)
    for rank, idx in enumerate(bm25_indices):
        scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.keys(), key=lambda i: scores[i], reverse=True)


def retrieve_sql(question: str, top_k: int = 3) -> str:
    _build_sql_index()
    if _sql_index is None or _sql_docs is None:
        return ""

    fetch_k = min(top_k * 3, len(_sql_docs))  # fetch more candidates for RRF
    query_vec = _model.encode([question], convert_to_numpy=True).astype(np.float32)
    _, faiss_idxs = _sql_index.search(query_vec, fetch_k)
    faiss_list = [i for i in faiss_idxs[0] if i < len(_sql_docs)]

    if _HAS_BM25 and _sql_bm25 is not None:
        tokens = _tokenize(question)
        bm25_scores = _sql_bm25.get_scores(tokens)
        bm25_list = sorted(range(len(_sql_docs)), key=lambda i: bm25_scores[i], reverse=True)[:fetch_k]
        ranked = _rrf_fusion(faiss_list, bm25_list)
    else:
        ranked = faiss_list

    results = []
    for idx in ranked[:top_k]:
        if idx < len(_sql_docs):
            d = _sql_docs[idx]
            entry = f"Q: {d['question']}\nQUERY_{d['db']}: {d['sql']}"
            if d.get("note"):
                entry += f"\nNOTE: {d['note']}"
            results.append(entry)
    return "\n\n".join(results)

# ═══════════════════════════════════════
# RETRIEVE (knowledge)
# ═══════════════════════════════════════

def retrieve(question: str, top_k: int = 3) -> str:
    _build_index()
    fetch_k = min(top_k * 3, len(_docs))
    query_vec = _model.encode([question], convert_to_numpy=True).astype(np.float32)
    _, faiss_idxs = _index.search(query_vec, fetch_k)
    faiss_list = [i for i in faiss_idxs[0] if i < len(_docs)]

    if _HAS_BM25 and _bm25 is not None:
        tokens = _tokenize(question)
        bm25_scores = _bm25.get_scores(tokens)
        bm25_list = sorted(range(len(_docs)), key=lambda i: bm25_scores[i], reverse=True)[:fetch_k]
        ranked = _rrf_fusion(faiss_list, bm25_list)
    else:
        ranked = faiss_list

    results = []
    for idx in ranked[:top_k]:
        if idx < len(_docs):
            d = _docs[idx]
            results.append(f"[{d['title']}]\n{d['text']}")
    return "\n\n---\n\n".join(results)

# ═══════════════════════════════════════
# TEST
# ═══════════════════════════════════════

if __name__ == "__main__":
    tests = [
        "find FWA candidates",
        "5G upsell opportunities",
        "3G sunset migration",
        "VoLTE subscribers at risk",
        "network drop rate is high in Sfax",
        "high value customers to upsell",
    ]
    for q in tests:
        print(f"\nQ: {q}")
        context = retrieve(q, top_k=2)
        titles = re.findall(r'\[(.+?)\]', context)
        print(f"  Retrieved: {titles}")
