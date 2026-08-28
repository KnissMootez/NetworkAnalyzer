import sqlite3
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import json

app = FastAPI(title="NetworkAnalyzer Mock API", version="1.0")

DB_PATH = "NetworkAnalyzer.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ─────────────────────────────────────────────
# CUSTOMER ENDPOINTS
# ─────────────────────────────────────────────

@app.get("/customer/{customer_id}")
def get_customer(customer_id: int):
    conn = get_db()
    c = conn.cursor()
    row = c.execute("""
        SELECT cu.*, p.plan_name, p.data_cap_gb, p.monthly_price, p.supports_5g,
               d.model_name as device, d.supports_5g as device_5g, d.supports_volte
        FROM customers cu
        JOIN subscriptions s ON cu.customer_id = s.customer_id AND s.is_current = 1
        JOIN plans p ON s.plan_id = p.plan_id
        JOIN customer_devices cd ON cu.customer_id = cd.customer_id AND cd.is_primary = 1
        JOIN device_catalog d ON cd.device_id = d.device_id
        WHERE cu.customer_id = ?
    """, (customer_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Customer not found")
    return dict(row)

@app.get("/customer/{customer_id}/churn")
def get_churn(customer_id: int):
    conn = get_db()
    row = conn.execute("""
        SELECT * FROM churn_scores
        WHERE customer_id = ?
        ORDER BY score_date DESC LIMIT 1
    """, (customer_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="No churn data found")
    return dict(row)

@app.get("/customer/{customer_id}/complaints")
def get_complaints(customer_id: int):
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM complaints
        WHERE customer_id = ?
        ORDER BY submission_date DESC LIMIT 10
    """, (customer_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/customer/{customer_id}/usage")
def get_usage(customer_id: int):
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM data_usage_daily
        WHERE customer_id = ?
        ORDER BY date DESC LIMIT 30
    """, (customer_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/customer/{customer_id}/cei")
def get_cei(customer_id: int):
    conn = get_db()
    row = conn.execute("""
        SELECT * FROM cei_scores
        WHERE customer_id = ?
        ORDER BY score_date DESC LIMIT 1
    """, (customer_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="No CEI data found")
    return dict(row)

@app.get("/customer/{customer_id}/billing")
def get_billing(customer_id: int):
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM billing
        WHERE customer_id = ?
        ORDER BY billing_month DESC LIMIT 6
    """, (customer_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ─────────────────────────────────────────────
# NETWORK ENDPOINTS
# ─────────────────────────────────────────────

@app.get("/cell/{cell_id}/kpis")
def get_cell_kpis(cell_id: int):
    conn = get_db()
    row = conn.execute("""
        SELECT * FROM kpis_daily
        WHERE cell_id = ?
        ORDER BY date DESC LIMIT 1
    """, (cell_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="No KPI data found")
    return dict(row)

@app.get("/network/alarms")
def get_active_alarms():
    conn = get_db()
    rows = conn.execute("""
        SELECT a.*, c.cell_name, s.region, s.city
        FROM network_alarms a
        JOIN cells c ON a.cell_id = c.cell_id
        JOIN sites s ON c.site_id = s.site_id
        WHERE a.is_active = 1
        ORDER BY a.trigger_time DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/network/incidents")
def get_incidents():
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM network_incidents
        ORDER BY start_time DESC LIMIT 20
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/coverage/region/{region}")
def get_coverage(region: str):
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM coverage_grid
        WHERE region = ?
    """, (region,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ─────────────────────────────────────────────
# OFFERS ENDPOINTS
# ─────────────────────────────────────────────

@app.get("/offers")
def get_offers():
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM offers WHERE is_active = 1
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]

class OfferAssignment(BaseModel):
    customer_id: int
    offer_id: int

class NewOffer(BaseModel):
    offer_name: str
    target_segment: str
    discount_pct: float
    bonus_data_gb: int
    validity_days: int
    min_churn_risk: Optional[float] = 0.0
    description: Optional[str] = ""

@app.post("/offers/assign")
def assign_offer(data: OfferAssignment):
    conn = get_db()
    from datetime import datetime
    conn.execute("""
        INSERT INTO offer_assignments (offer_id, customer_id, assigned_date, accepted)
        VALUES (?, ?, ?, 0)
    """, (data.offer_id, data.customer_id, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return {"status": "success", "message": f"Offer {data.offer_id} assigned to customer {data.customer_id}"}

@app.post("/offers/create")
def create_offer(offer: NewOffer):
    conn = get_db()
    from datetime import datetime, timedelta
    now = datetime.now()
    conn.execute("""
        INSERT INTO offers (offer_name, target_segment, discount_pct, bonus_data_gb,
        validity_days, start_date, end_date, is_active, created_by, min_churn_risk, description)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'agent', ?, ?)
    """, (
        offer.offer_name, offer.target_segment, offer.discount_pct,
        offer.bonus_data_gb, offer.validity_days,
        now.isoformat(), (now + timedelta(days=offer.validity_days)).isoformat(),
        offer.min_churn_risk, offer.description
    ))
    conn.commit()
    conn.close()
    return {"status": "success", "message": f"Offer '{offer.offer_name}' created successfully"}