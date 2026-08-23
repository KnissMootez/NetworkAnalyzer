"""
Train an XGBoost churn model on the Indian telecom dataset (SMOTE balanced).
Only uses features that map directly to our DB — unmappable features are dropped
from training entirely (not zeroed), so the model never learns to rely on them.

Run once to train + score:    python churn_model.py
Re-score only (model exists): python churn_model.py --score-only
"""

import argparse
import pickle
import sqlite3
import zipfile
from datetime import date

import numpy as np
import pandas as pd
from imblearn.over_sampling import SMOTE
from sklearn.metrics import (accuracy_score, classification_report,
                             confusion_matrix, precision_recall_curve,
                             roc_auc_score)
from sklearn.model_selection import cross_val_score, train_test_split
from xgboost import XGBClassifier

# ── Config ────────────────────────────────────────────────────────────────────
ZIP_PATH    = "telecom customer churn dataset.zip"
CSV_NAME    = "telecom_churn_data.csv"
MODEL_PATH  = "churn_model.pkl"
OP_DB       = "operator_new.db"
NET_DB      = "NetworkAnalyzer_new.db"
SCORE_MONTH = date.today().strftime("%Y-%m")
INR_TO_YUAN = 0.43

# Features we train on — all have a direct DB equivalent
FEATURE_COLS = [
    "arpu_drop_pct",      # billing: (avg m6-m7 - m8) / avg m6-m7
    "arpu_m8",            # billing: most recent month total_amount
    "rech_amt_drop_pct",  # billing: total_amount trend (same signal, different scale)
    "rech_amt_m8",        # billing: total_amount most recent month
    "max_rech_drop_pct",  # billing: max single payment trend → max_rech_amt equivalent
    "max_rech_m8",        # billing: max single payment most recent month
    "data_drop_pct",      # dou_monthly: (avg m6-m7 - m8) / avg m6-m7
    "data_mb_m8",         # dou_monthly: most recent month total_data_gb * 1024
    "days_active_m8",     # dou_monthly: days_active most recent month
    "rech_drop_pct",      # billing: paid-month frequency trend
    "rech_num_m8",        # billing: paid in most recent month (0/1)
    "voice_drop_pct",     # ott_monthly.voice_minutes trend
    "voice_min_m8",       # ott_monthly.voice_minutes most recent month
    "has_roaming",        # subscriber_technology.data_roaming_active
    "tenure_days",        # subscribers: (today - activation_date).days
]


# ── Feature helpers ───────────────────────────────────────────────────────────

def _pct_drop(a, b):
    with np.errstate(divide="ignore", invalid="ignore"):
        d = np.where(a > 0, (a - b) / a, 0.0)
    return np.clip(d, -1.0, 5.0)


def build_training_features(df: pd.DataFrame) -> pd.DataFrame:
    f = pd.DataFrame()

    # ARPU trend
    arpu_early          = (df["arpu_6"].fillna(0) + df["arpu_7"].fillna(0)) / 2
    f["arpu_drop_pct"]  = _pct_drop(arpu_early, df["arpu_8"].fillna(0))
    f["arpu_m8"]        = df["arpu_8"].fillna(0) * INR_TO_YUAN

    # Recharge amount trend (total spend)
    ra_early             = (df["total_rech_amt_6"].fillna(0) + df["total_rech_amt_7"].fillna(0)) / 2
    f["rech_amt_drop_pct"] = _pct_drop(ra_early, df["total_rech_amt_8"].fillna(0))
    f["rech_amt_m8"]     = df["total_rech_amt_8"].fillna(0) * INR_TO_YUAN

    # Max single recharge trend (commitment signal)
    mr_early             = (df["max_rech_amt_6"].fillna(0) + df["max_rech_amt_7"].fillna(0)) / 2
    f["max_rech_drop_pct"] = _pct_drop(mr_early, df["max_rech_amt_8"].fillna(0))
    f["max_rech_m8"]     = df["max_rech_amt_8"].fillna(0) * INR_TO_YUAN

    # Data usage trend
    vol_early           = (df["vol_3g_mb_6"].fillna(0) + df["vol_3g_mb_7"].fillna(0)) / 2
    f["data_drop_pct"]  = _pct_drop(vol_early, df["vol_3g_mb_8"].fillna(0))
    f["data_mb_m8"]     = df["vol_3g_mb_8"].fillna(0)

    # Days active proxy: non-zero ARPU days (dataset has no direct days_active, use rech count)
    f["days_active_m8"] = df["total_rech_num_8"].fillna(0).clip(0, 31)

    # Recharge frequency trend
    rech_early          = (df["total_rech_num_6"].fillna(0) + df["total_rech_num_7"].fillna(0)) / 2
    f["rech_drop_pct"]  = _pct_drop(rech_early, df["total_rech_num_8"].fillna(0))
    f["rech_num_m8"]    = df["total_rech_num_8"].fillna(0)

    # Voice minutes
    mou_early           = (df["onnet_mou_6"].fillna(0) + df["onnet_mou_7"].fillna(0)) / 2
    f["voice_drop_pct"] = _pct_drop(mou_early, df["onnet_mou_8"].fillna(0))
    f["voice_min_m8"]   = df["onnet_mou_8"].fillna(0)

    # Roaming
    roam_cols           = [c for c in df.columns if "roam" in c.lower()]
    f["has_roaming"]    = (df[roam_cols].fillna(0).sum(axis=1) > 0).astype(int) if roam_cols else 0

    f["tenure_days"]    = df["aon"].fillna(0).clip(0, 3650)
    return f


# ── Training ──────────────────────────────────────────────────────────────────

def train():
    print("[TRAIN] Loading Indian telecom dataset...")
    with zipfile.ZipFile(ZIP_PATH) as z:
        with z.open(CSV_NAME) as fh:
            df = pd.read_csv(fh)

    print(f"[TRAIN] {len(df):,} rows, {len(df.columns)} columns")

    df["churn"] = (df["arpu_9"].fillna(0) == 0).astype(int)
    print(f"[TRAIN] Churn rate: {df['churn'].mean()*100:.1f}%  ({df['churn'].sum():,} churned)")

    X = build_training_features(df)
    y = df["churn"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    smote = SMOTE(random_state=42)
    X_train_s, y_train_s = smote.fit_resample(X_train, y_train)
    print(f"[TRAIN] After SMOTE: {y_train_s.value_counts().to_dict()}")

    model = XGBClassifier(
        n_estimators=500,
        learning_rate=0.05,
        max_depth=6,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        n_jobs=-1,
        eval_metric="logloss",
        verbosity=0,
    )

    cv_scores = cross_val_score(model, X_train_s, y_train_s, cv=5, scoring="accuracy")
    print(f"[TRAIN] 5-fold CV accuracy: {np.mean(cv_scores):.4f} (+/- {np.std(cv_scores):.4f})")

    model.fit(X_train_s, y_train_s)

    y_proba = model.predict_proba(X_test)[:, 1]

    # F1-optimal threshold
    precisions, recalls, thresholds = precision_recall_curve(y_test, y_proba)
    f1_scores  = 2 * precisions * recalls / (precisions + recalls + 1e-9)
    best_idx   = np.argmax(f1_scores)
    best_thresh = float(thresholds[best_idx]) if best_idx < len(thresholds) else 0.5
    y_pred = (y_proba >= best_thresh).astype(int)

    print("\n" + "="*55)
    print("  FINAL MODEL RESULTS  (XGBoost, Indian dataset)")
    print("="*55)
    print(f"  Accuracy : {accuracy_score(y_test, y_pred):.4f}")
    print(f"  AUC-ROC  : {roc_auc_score(y_test, y_proba):.4f}")
    print(f"  Threshold: {best_thresh:.4f}")
    print("\n  Confusion Matrix:")
    cm = confusion_matrix(y_test, y_pred)
    print(f"    TN={cm[0,0]:4d}  FP={cm[0,1]:4d}")
    print(f"    FN={cm[1,0]:4d}  TP={cm[1,1]:4d}")
    print("\n  Classification Report:")
    print(classification_report(y_test, y_pred, target_names=["active", "churn"]))
    print("="*55 + "\n")

    imp = pd.Series(model.feature_importances_, index=FEATURE_COLS).sort_values(ascending=False)
    print("  Feature importances:")
    for feat, val in imp.items():
        print(f"    {feat:<20s} {val:.4f}")

    all_proba = model.predict_proba(X)[:, 1]
    p90 = float(np.percentile(all_proba, 90))
    p70 = float(np.percentile(all_proba, 70))
    print(f"\n[TRAIN] Global thresholds — high >= {p90:.4f}, medium >= {p70:.4f}")

    with open(MODEL_PATH, "wb") as f:
        pickle.dump({"model": model, "feature_cols": FEATURE_COLS, "p90": p90, "p70": p70}, f)
    print(f"[TRAIN] Model saved to {MODEL_PATH}")

    return model


# ── DB feature builder ────────────────────────────────────────────────────────


# ── Heuristic churn scorecard ─────────────────────────────────────────────
# Replaces the XGBoost model for scoring THIS environment. The model is trained on
# an Indian prepaid recharge dataset and does not transfer: measured against the
# features it is actually fed here, data_mb_m8 differs by ~300x (training median
# 0 MB, ours ~40 GB), a recharge COUNT is mapped to a binary paid flag, and two of
# the fifteen features are exact duplicates of two others. Its 0.88 AUC is real on
# its own test set and says nothing about this data.
#
# Every term below is computed from the operator's own columns and is explainable
# in one sentence, which matters more here than a black-box probability: the agent
# can say WHY a subscriber is flagged, and a human can check it.
CHURN_WEIGHTS = {
    "arpu_decline":    0.35,   # spend falling against their own baseline
    "unpaid":          0.20,   # bills going unpaid
    "data_decline":    0.15,   # usage falling away
    "voice_decline":   0.10,
    "low_engagement":  0.10,   # few active days in the month
    "short_tenure":    0.10,   # new subscribers churn more
}
# NOTE: complaints and live incidents are deliberately NOT scored here. The
# experience penalty in db_simulator._rescore_churn already owns them, and
# counting them twice would inflate exactly the customers the retention story
# cares about. Clean split: this scorecard is COMMERCIAL signals, the penalty is
# EXPERIENCE signals, and the two are added.


def _decline(hist):
    """Fractional drop of the latest period against the mean of the prior two,
    clipped to 0..1. Only DROPS count -- growth is not negative churn risk."""
    if not hist:
        return 0.0
    latest = hist[0] or 0
    if len(hist) >= 3:
        base = ((hist[1] or 0) + (hist[2] or 0)) / 2
    elif len(hist) == 2:
        base = hist[1] or 0
    else:
        return 0.0
    if base <= 0:
        return 0.0
    return float(min(1.0, max(0.0, (base - latest) / base)))


def heuristic_churn(arpu_hist=None, data_hist=None, voice_hist=None,
                    paid_hist=None, days_active=None, tenure_days=None):
    """Base churn risk in 0..1 from a subscriber's own history.

    Deliberately NOT calibrated against observed churn -- the simulated world has
    no ground-truth outcomes to calibrate against. This is a defensible ordering,
    not a probability, and the report says so."""
    w = CHURN_WEIGHTS
    s = 0.0
    s += w["arpu_decline"]  * _decline(arpu_hist or [])
    s += w["data_decline"]  * _decline(data_hist or [])
    s += w["voice_decline"] * _decline(voice_hist or [])

    if paid_hist:                                   # 1 = paid, 0 = unpaid
        s += w["unpaid"] * (1.0 - sum(paid_hist) / len(paid_hist))

    if days_active is not None:                     # 30-day month
        s += w["low_engagement"] * min(1.0, max(0.0, (30.0 - float(days_active)) / 30.0))

    if tenure_days is not None:                     # risk decays over the first year
        s += w["short_tenure"] * min(1.0, max(0.0, (365.0 - float(tenure_days)) / 365.0))

    return float(min(1.0, max(0.0, s)))




def score_all_heuristic(op_path=None, sc_path=None, write=True):
    """Score every subscriber with the heuristic scorecard and rewrite
    customer_value.churn_risk_score / churn_label for the current month.

    Bands are percentiles of the heuristic's OWN distribution -- the thresholds
    stored in churn_model.pkl were calibrated to the XGBoost output and mean
    nothing here."""
    import sqlite3 as _sq
    from collections import defaultdict as _dd
    from datetime import date as _date
    op = _sq.connect(op_path or OP_DB); sc = _sq.connect(sc_path or NET_DB)
    month = op.execute("SELECT MAX(month) FROM customer_value").fetchone()[0]

    hist = lambda cn, sql: _collect(cn.execute(sql).fetchall())

    def _collect(rows):
        d = _dd(list)
        for m, v in rows:
            d[m].append(v or 0)
        return d

    # ARPU history comes from customer_value, NOT billing.total_amount. Billing
    # amounts are drawn independently per month (measured month-to-month
    # correlation +0.15), so a "decline against baseline" computed from them is
    # noise. customer_value.arpu carries forward with drift (+0.998), which is
    # what makes a trend term mean anything.
    arpu  = _collect(op.execute(
        "SELECT msisdn, arpu FROM customer_value ORDER BY msisdn, month DESC").fetchall())
    paid  = _collect(op.execute(
        "SELECT msisdn, CASE WHEN payment_status='paid' THEN 1 ELSE 0 END "
        "FROM billing ORDER BY msisdn, billing_month DESC").fetchall())
    data_ = _collect(sc.execute(
        "SELECT msisdn, total_data_gb FROM dou_monthly ORDER BY msisdn, month DESC").fetchall())
    voice = _collect(sc.execute(
        "SELECT msisdn, voice_minutes FROM ott_monthly ORDER BY msisdn, month DESC").fetchall())
    days  = dict(sc.execute(
        "SELECT msisdn, days_active FROM dou_monthly WHERE month=(SELECT MAX(month) FROM dou_monthly)").fetchall())
    today = _date.today()
    tenure = {}
    for m, act in sc.execute("SELECT msisdn, activation_date FROM subscribers").fetchall():
        try:
            tenure[m] = float(max(0, min(3650, (today - _date.fromisoformat(act)).days)))
        except Exception:
            tenure[m] = 365.0

    msisdns = [r[0] for r in op.execute(
        "SELECT msisdn FROM customer_value WHERE month=?", (month,)).fetchall()]
    scores = {m: heuristic_churn(arpu.get(m, [])[:3], data_.get(m, [])[:3],
                                 voice.get(m, [])[:3], paid.get(m, [])[:3],
                                 days.get(m), tenure.get(m)) for m in msisdns}

    vals = sorted(scores.values())
    pick = lambda p: vals[min(len(vals) - 1, int(len(vals) * p / 100))] if vals else 0.0
    p90, p70 = pick(90), pick(70)
    label = lambda s: "high" if s >= p90 else "medium" if s >= p70 else "low"

    print(f"[HEURISTIC] month={month}  scored {len(scores):,} subscribers")
    print(f"[HEURISTIC] bands  high >= {p90:.4f}   medium >= {p70:.4f}")
    dist = {"high": 0, "medium": 0, "low": 0}
    for s in scores.values():
        dist[label(s)] += 1
    print(f"[HEURISTIC] distribution  {dist}")

    if write:
        op.executemany(
            "UPDATE customer_value SET churn_risk_score=?, churn_label=? WHERE msisdn=? AND month=?",
            [(round(s, 4), label(s), m, month) for m, s in scores.items()])
        op.commit()
        print("[HEURISTIC] customer_value updated")
    op.close(); sc.close()
    return p90, p70


def build_db_features(op_conn, net_conn) -> pd.DataFrame:
    today = date.today()

    subs = pd.read_sql("SELECT msisdn, activation_date FROM subscribers", net_conn)
    subs["tenure_days"] = subs["activation_date"].apply(
        lambda d: float(np.clip((today - date.fromisoformat(d)).days, 0, 3650)) if d else 365.0
    )

    # Billing: last 3 months — total_amount, data_charges (max rech proxy)
    billing = pd.read_sql("""
        SELECT msisdn, billing_month, total_amount, data_charges
        FROM billing ORDER BY msisdn, billing_month DESC
    """, op_conn)
    billing["rn"] = billing.groupby("msisdn").cumcount() + 1
    b3 = billing[billing["rn"] <= 3]

    bp = b3.pivot(index="msisdn", columns="rn", values="total_amount")
    bp.columns = ["a1", "a2", "a3"]
    bp = bp.reset_index()
    arpu_early          = (bp["a2"].fillna(0) + bp["a3"].fillna(0)) / 2
    bp["arpu_drop_pct"] = _pct_drop(arpu_early.values, bp["a1"].fillna(0).values)
    bp["arpu_m8"]       = bp["a1"].fillna(0)
    # rech_amt = same as total_amount (best proxy)
    bp["rech_amt_drop_pct"] = bp["arpu_drop_pct"]
    bp["rech_amt_m8"]       = bp["arpu_m8"]

    # max_rech proxy: data_charges (largest single charge component)
    mr = b3.pivot(index="msisdn", columns="rn", values="data_charges")
    mr.columns = ["m1", "m2", "m3"]
    mr = mr.reset_index()
    mr_early                = (mr["m2"].fillna(0) + mr["m3"].fillna(0)) / 2
    mr["max_rech_drop_pct"] = _pct_drop(mr_early.values, mr["m1"].fillna(0).values)
    mr["max_rech_m8"]       = mr["m1"].fillna(0)
    bp = bp.merge(mr[["msisdn", "max_rech_drop_pct", "max_rech_m8"]], on="msisdn", how="left")

    # Data usage + days_active: last 3 months from dou_monthly
    dou = pd.read_sql("""
        SELECT msisdn, month, total_data_gb * 1024 AS data_mb, days_active
        FROM dou_monthly ORDER BY msisdn, month DESC
    """, net_conn)
    dou["rn"] = dou.groupby("msisdn").cumcount() + 1
    dou3 = dou[dou["rn"] <= 3]
    dp = dou3.pivot(index="msisdn", columns="rn", values="data_mb")
    dp.columns = ["d1", "d2", "d3"]
    dp = dp.reset_index()
    data_early          = (dp["d2"].fillna(0) + dp["d3"].fillna(0)) / 2
    dp["data_drop_pct"] = _pct_drop(data_early.values, dp["d1"].fillna(0).values)
    dp["data_mb_m8"]    = dp["d1"].fillna(0)

    da = dou3[dou3["rn"] == 1][["msisdn", "days_active"]].rename(columns={"days_active": "days_active_m8"})
    dp = dp.merge(da, on="msisdn", how="left")

    # Recharge proxy: paid billing months
    rech = pd.read_sql("""
        SELECT msisdn, billing_month,
               CASE WHEN payment_status='paid' THEN 1 ELSE 0 END AS paid
        FROM billing ORDER BY msisdn, billing_month DESC
    """, op_conn)
    rech["rn"] = rech.groupby("msisdn").cumcount() + 1
    rp = rech[rech["rn"] <= 3].pivot(index="msisdn", columns="rn", values="paid")
    rp.columns = ["r1", "r2", "r3"]
    rp = rp.reset_index()
    rech_early      = (rp["r2"].fillna(0) + rp["r3"].fillna(0)) / 2
    rp["rech_drop_pct"] = _pct_drop(rech_early.values, rp["r1"].fillna(0).values)
    rp["rech_num_m8"]   = rp["r1"].fillna(0)

    # Voice minutes: last 3 months from ott_monthly.voice_minutes
    voice = pd.read_sql("""
        SELECT msisdn, month, voice_minutes
        FROM ott_monthly ORDER BY msisdn, month DESC
    """, net_conn)
    voice["rn"] = voice.groupby("msisdn").cumcount() + 1
    vp = voice[voice["rn"] <= 3].pivot(index="msisdn", columns="rn", values="voice_minutes")
    vp.columns = ["v1", "v2", "v3"]
    vp = vp.reset_index()
    voice_early         = (vp["v2"].fillna(0) + vp["v3"].fillna(0)) / 2
    vp["voice_drop_pct"]= _pct_drop(voice_early.values, vp["v1"].fillna(0).values)
    vp["voice_min_m8"]  = vp["v1"].fillna(0)

    # Roaming from subscriber_technology
    roam = pd.read_sql(
        "SELECT msisdn, data_roaming_active AS has_roaming FROM subscriber_technology", net_conn
    )

    f = subs[["msisdn", "tenure_days"]].copy()
    f = f.merge(bp[["msisdn", "arpu_drop_pct", "arpu_m8",
                     "rech_amt_drop_pct", "rech_amt_m8",
                     "max_rech_drop_pct", "max_rech_m8"]], on="msisdn", how="left")
    f = f.merge(dp[["msisdn", "data_drop_pct", "data_mb_m8", "days_active_m8"]], on="msisdn", how="left")
    f = f.merge(rp[["msisdn", "rech_drop_pct", "rech_num_m8"]], on="msisdn", how="left")
    f = f.merge(vp[["msisdn", "voice_drop_pct", "voice_min_m8"]], on="msisdn", how="left")
    f = f.merge(roam, on="msisdn", how="left")

    for c in FEATURE_COLS:
        f[c] = pd.to_numeric(f[c], errors="coerce").fillna(0)

    return f[["msisdn"] + FEATURE_COLS]


# ── Scoring ───────────────────────────────────────────────────────────────────

def score(model=None):
    if model is None:
        with open(MODEL_PATH, "rb") as f:
            bundle = pickle.load(f)
        model = bundle["model"]
        p90   = bundle["p90"]
        p70   = bundle["p70"]
    else:
        with open(MODEL_PATH, "rb") as f:
            bundle = pickle.load(f)
        p90 = bundle["p90"]
        p70 = bundle["p70"]

    print("[SCORE] Building features from DB...")
    op_conn  = sqlite3.connect(OP_DB)
    net_conn = sqlite3.connect(NET_DB)

    feat = build_db_features(op_conn, net_conn)
    print(f"[SCORE] Got features for {len(feat):,} subscribers")

    scores = model.predict_proba(feat[FEATURE_COLS].values)[:, 1]
    feat["churn_risk_score"] = np.round(scores, 4)
    feat["churn_label"] = np.where(
        feat["churn_risk_score"] >= p90, "high",
        np.where(feat["churn_risk_score"] >= p70, "medium", "low")
    )
    print(f"[SCORE] Thresholds — high >= {p90:.4f}, medium >= {p70:.4f}")
    print(f"[SCORE] Distribution — "
          f"high: {(feat['churn_label']=='high').sum():,}  "
          f"medium: {(feat['churn_label']=='medium').sum():,}  "
          f"low: {(feat['churn_label']=='low').sum():,}")

    existing = {r[1] for r in op_conn.execute("PRAGMA table_info(customer_value)").fetchall()}
    if "churn_risk_score" not in existing:
        op_conn.execute("ALTER TABLE customer_value ADD COLUMN churn_risk_score REAL DEFAULT 0.0")
    if "churn_label" not in existing:
        op_conn.execute("ALTER TABLE customer_value ADD COLUMN churn_label TEXT DEFAULT 'low'")

    month = SCORE_MONTH
    for _, row in feat.iterrows():
        exists = op_conn.execute(
            "SELECT 1 FROM customer_value WHERE msisdn=? AND month=?",
            (row["msisdn"], month)
        ).fetchone()
        if exists:
            op_conn.execute(
                "UPDATE customer_value SET churn_risk_score=?, churn_label=? WHERE msisdn=? AND month=?",
                (row["churn_risk_score"], row["churn_label"], row["msisdn"], month)
            )
        else:
            prev = op_conn.execute(
                "SELECT arpu, value_segment, is_hvc FROM customer_value WHERE msisdn=? ORDER BY month DESC LIMIT 1",
                (row["msisdn"],)
            ).fetchone()
            if prev:
                op_conn.execute(
                    "INSERT INTO customer_value VALUES(?,?,?,?,?,?,?)",
                    (row["msisdn"], month, prev[0], prev[1], prev[2],
                     row["churn_risk_score"], row["churn_label"])
                )

    op_conn.commit()
    op_conn.close()
    net_conn.close()
    print(f"[SCORE] Wrote churn scores for {len(feat):,} subscribers (month={month})")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--score-only", action="store_true")
    args = parser.parse_args()

    if args.score_only:
        score()
    else:
        m = train()
        score(m)
