import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from NetworkAnalyzer_agent import query_op

st.set_page_config(page_title="Commercial — NetworkAnalyzer", page_icon="💰", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Share+Tech+Mono&family=Inter:wght@300;400;500;600&display=swap');
:root{--red-400:#cc0000;--red-300:#ff1a1a;--red-200:#ff6666;--black:#0a0a0a;--dark:#111111;--mid:#1c1c1c;--border:#2a0000;--text:#e8e8e8;--muted:#888;}
html,body,[data-testid="stAppViewContainer"]{background:var(--black)!important;color:var(--text)!important;font-family:'Inter',sans-serif;}
[data-testid="stSidebar"]{background:var(--dark)!important;border-right:2px solid #4a0000!important;}
.page-title{font-family:'Bebas Neue',sans-serif;font-size:2rem;letter-spacing:0.15em;color:#fff;border-bottom:1px solid #2a0000;padding-bottom:8px;margin-bottom:1rem;}
.page-title span{color:var(--red-300);}
.section-label{font-family:'Bebas Neue',sans-serif;font-size:1rem;letter-spacing:0.2em;color:var(--red-300);border-bottom:1px solid var(--border);padding-bottom:4px;margin:12px 0 8px 0;}
.metric-card{background:var(--mid);border:1px solid var(--border);border-left:3px solid var(--red-400);padding:12px 16px;margin-bottom:4px;}
.metric-label{font-family:'Share Tech Mono',monospace;font-size:0.6rem;color:var(--muted);letter-spacing:0.2em;text-transform:uppercase;margin-bottom:4px;}
.metric-value{font-family:'Bebas Neue',sans-serif;font-size:1.8rem;color:#fff;line-height:1;}
.metric-sub{font-size:0.7rem;color:var(--red-200);margin-top:2px;}
[data-testid="stTabs"] button{font-family:'Share Tech Mono',monospace!important;font-size:0.7rem!important;color:var(--muted)!important;}
[data-testid="stTabs"] button[aria-selected="true"]{color:var(--red-300)!important;border-bottom-color:var(--red-400)!important;}
#MainMenu,footer,header{visibility:hidden;}[data-testid="stToolbar"]{display:none;}
</style>
""", unsafe_allow_html=True)

PLOT_BASE = dict(
    paper_bgcolor="#111111", plot_bgcolor="#111111",
    font=dict(color="#e8e8e8", family="Share Tech Mono, monospace", size=11),
    margin=dict(l=16, r=16, t=36, b=16),
    colorway=["#cc0000","#ff4444","#ff8888","#880000","#ff6600","#0088ff"],
)
AX = dict(gridcolor="#1c1c1c", linecolor="#2a0000")

def v(row, key, default=0):
    try:
        return row[key] if row[key] is not None else default
    except (KeyError, TypeError):
        return default

def bar(df, x, y, title, key=None):
    fig = go.Figure(go.Bar(
        x=df[x], y=df[y],
        marker=dict(color=df[y],
                    colorscale=[[0,"#4a0000"],[0.5,"#cc0000"],[1,"#ff4444"]],
                    line=dict(color="#1c1c1c",width=1))
    ))
    fig.update_layout(title=dict(text=title,font_size=13),
                      xaxis=dict(**AX), yaxis=dict(**AX), **PLOT_BASE)
    return fig

def pie(labels, values, title):
    fig = go.Figure(go.Pie(
        labels=labels, values=values, hole=0.55,
        marker=dict(colors=["#cc0000","#880000","#ff4444","#ff8800","#0088ff","#555"],
                    line=dict(color="#111",width=2))
    ))
    fig.update_layout(title=dict(text=title,font_size=13), **PLOT_BASE)
    return fig

@st.cache_data(ttl=60)
def get_latest_month():
    r = query_op("SELECT MAX(month) as latest FROM customer_value")
    return v(r[0],"latest","2026-03") if r else "2026-03"

@st.cache_data(ttl=60)
def get_regions():
    rows = query_op("SELECT DISTINCT region FROM customers WHERE region IS NOT NULL ORDER BY region")
    return ["All"] + [r["region"] for r in rows]

st.markdown('<div class="page-title">💰 COMMERCIAL <span>ANALYTICS</span></div>',
            unsafe_allow_html=True)

fc1, fc2, fc3 = st.columns(3)
with fc1:
    segment = st.selectbox("Segment", ["All","prepaid","postpaid","enterprise"], key="com_seg")
with fc2:
    value_seg = st.multiselect("Value Tier", ["platinum","gold","silver","bronze"],
                                default=["platinum","gold","silver","bronze"], key="com_val")
with fc3:
    region = st.selectbox("Region", get_regions(), key="com_region")

sgf = f"AND c.segment='{segment}'" if segment != "All" else ""
vf  = "AND cv.value_segment IN (" + ",".join(f"'{vv}'" for vv in value_seg) + ")" if value_seg else ""
rf  = f"AND c.region='{region}'"   if region  != "All" else ""

# ── SUMMARY CARDS ────────────────────────────────────────────────────
@st.cache_data(ttl=30)
def load_summary(segment, value_seg, region):
    latest = get_latest_month()
    rev  = query_op(f"SELECT ROUND(SUM(cv.arpu),0) as total_rev FROM customer_value cv JOIN customers c ON cv.msisdn=c.msisdn WHERE cv.month='{latest}' {sgf} {vf} {rf}")
    arpu = query_op(f"SELECT ROUND(AVG(cv.arpu),2) as avg_arpu FROM customer_value cv JOIN customers c ON cv.msisdn=c.msisdn WHERE cv.month='{latest}' {sgf} {vf} {rf}")
    hvc  = query_op(f"SELECT COUNT(*) as n FROM customer_value cv JOIN customers c ON cv.msisdn=c.msisdn WHERE cv.month='{latest}' AND cv.is_hvc=1 {sgf} {rf}")
    unp  = query_op(f"SELECT COUNT(DISTINCT b.msisdn) as n FROM billing b JOIN customers c ON b.msisdn=c.msisdn WHERE b.payment_status='unpaid' {sgf} {rf}")
    return (v(rev[0],"total_rev") if rev else 0,
            v(arpu[0],"avg_arpu") if arpu else 0,
            v(hvc[0],"n")         if hvc  else 0,
            v(unp[0],"n")         if unp  else 0)

rev, arpu, hvc, unpaid = load_summary(segment, value_seg, region)
cc = st.columns(4)
for col, (label, val, sub, color) in zip(cc, [
    ("TOTAL REVENUE", f"{rev:,.0f} TND", "this month",     "#cc0000"),
    ("AVG ARPU",      f"{arpu} TND",     "per subscriber", "#ff8800"),
    ("HVC CUSTOMERS", f"{hvc:,}",        "gold+platinum",  "#ff4444"),
    ("UNPAID BILLS",  f"{unpaid:,}",     "outstanding",    "#888"),
]):
    col.markdown(f"""
    <div class="metric-card" style="border-left-color:{color}">
        <div class="metric-label">{label}</div>
        <div class="metric-value">{val}</div>
        <div class="metric-sub">{sub}</div>
    </div>""", unsafe_allow_html=True)

st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

tab1, tab2, tab3, tab4 = st.tabs(["ARPU & SEGMENTS","PLANS","BILLING","HVC LIST"])

with tab1:
    @st.cache_data(ttl=30)
    def load_seg_arpu(segment, region):
        latest = get_latest_month()
        return query_op(f"SELECT cv.value_segment as segment, COUNT(*) as n, ROUND(AVG(cv.arpu),2) as avg_arpu, ROUND(SUM(cv.arpu),0) as total_rev FROM customer_value cv JOIN customers c ON cv.msisdn=c.msisdn WHERE cv.month='{latest}' {sgf} {rf} GROUP BY cv.value_segment ORDER BY avg_arpu DESC")

    @st.cache_data(ttl=30)
    def load_arpu_trend(segment, region):
        return query_op(f"SELECT cv.month, cv.value_segment as segment, ROUND(AVG(cv.arpu),2) as avg_arpu FROM customer_value cv JOIN customers c ON cv.msisdn=c.msisdn WHERE 1=1 {sgf} {rf} GROUP BY cv.month, cv.value_segment ORDER BY cv.month")

    sd = load_seg_arpu(segment, region)
    td = load_arpu_trend(segment, region)

    if sd:
        df_s = pd.DataFrame(sd)
        COLORS = {"platinum":"#ff4444","gold":"#ff8800","silver":"#888","bronze":"#553300"}
        c1, c2 = st.columns(2)
        with c1:
            fig = go.Figure()
            for _, row in df_s.iterrows():
                fig.add_trace(go.Bar(
                    name=row["segment"].title(),
                    x=[row["segment"].title()], y=[row["n"]],
                    marker_color=COLORS.get(row["segment"],"#cc0000"),
                    text=[f"{row['avg_arpu']} TND"], textposition="outside",
                    textfont=dict(color="#e8e8e8",size=10)
                ))
            fig.update_layout(title="Customer Value Segments", showlegend=False,
                               xaxis=dict(**AX), yaxis=dict(**AX), **PLOT_BASE)
            st.plotly_chart(fig, use_container_width=True, key="seg_bar")
        with c2:
            st.plotly_chart(
                pie(df_s["segment"].str.title().tolist(),
                    df_s["total_rev"].tolist(), "Revenue Share by Segment"),
                use_container_width=True, key="seg_pie"
            )
        st.dataframe(df_s, use_container_width=True)

    if td:
        df_t = pd.DataFrame(td)
        fig2 = go.Figure()
        for seg in df_t["segment"].unique():
            sub = df_t[df_t["segment"]==seg]
            fig2.add_trace(go.Scatter(
                name=seg.title(), x=sub["month"], y=sub["avg_arpu"],
                mode="lines+markers",
                line=dict(color=COLORS.get(seg,"#cc0000"),width=2),
                marker=dict(size=5)
            ))
        fig2.update_layout(title="ARPU Trend by Segment",
                            xaxis=dict(**AX), yaxis=dict(**AX),
                            legend=dict(orientation="h",y=-0.3), **PLOT_BASE)
        st.plotly_chart(fig2, use_container_width=True, key="arpu_trend")

with tab2:
    @st.cache_data(ttl=60)
    def load_plans():
        return query_op("SELECT p.plan_name, p.plan_type, p.monthly_price, p.data_cap_gb, p.supports_5g, p.supports_volte, COUNT(s.msisdn) as subscribers FROM plans p LEFT JOIN subscriptions s ON p.plan_id=s.plan_id AND s.is_current=1 GROUP BY p.plan_id ORDER BY subscribers DESC")

    pd_data = load_plans()
    if pd_data:
        df_p = pd.DataFrame(pd_data)
        df_p["supports_5g"]    = df_p["supports_5g"].map({1:"✅",0:"❌"})
        df_p["supports_volte"] = df_p["supports_volte"].map({1:"✅",0:"❌"})
        df_plot = df_p[df_p["subscribers"] > 0].copy()
        c1, c2 = st.columns(2)
        with c1:
            fig = go.Figure(go.Bar(
                x=df_plot["plan_name"], y=df_plot["subscribers"],
                marker=dict(color=df_plot["subscribers"],
                            colorscale=[[0,"#4a0000"],[0.5,"#cc0000"],[1,"#ff4444"]])
            ))
            fig.update_layout(title="Subscribers by Plan",
                               xaxis=dict(**AX,tickangle=-30), yaxis=dict(**AX), **PLOT_BASE)
            st.plotly_chart(fig, use_container_width=True, key="plan_bar")
        with c2:
            tc = df_plot.groupby("plan_type")["subscribers"].sum().reset_index()
            st.plotly_chart(pie(tc["plan_type"].tolist(), tc["subscribers"].tolist(),
                                "Subscribers by Plan Type"),
                            use_container_width=True, key="plan_pie")
        st.dataframe(df_p, use_container_width=True)

with tab3:
    @st.cache_data(ttl=30)
    def load_billing(segment, region):
        return query_op(f"""
            SELECT b.billing_month as month,
                   ROUND(SUM(b.total_amount),0) as billed,
                   ROUND(SUM(CASE WHEN b.payment_status='paid' THEN b.total_amount ELSE 0 END),0) as paid,
                   ROUND(SUM(CASE WHEN b.payment_status='unpaid' THEN b.total_amount ELSE 0 END),0) as unpaid,
                   COUNT(DISTINCT CASE WHEN b.payment_status='unpaid' THEN b.msisdn END) as unpaid_subs
            FROM billing b JOIN customers c ON b.msisdn=c.msisdn
            WHERE 1=1 {sgf} {rf}
            GROUP BY b.billing_month ORDER BY b.billing_month DESC LIMIT 12
        """)

    bd = load_billing(segment, region)
    if bd:
        df_b = pd.DataFrame(bd)
        fig = go.Figure()
        fig.add_trace(go.Bar(name="Paid",   x=df_b["month"], y=df_b["paid"],   marker_color="#00aa44"))
        fig.add_trace(go.Bar(name="Unpaid", x=df_b["month"], y=df_b["unpaid"], marker_color="#cc0000"))
        fig.update_layout(title="Monthly Billing — Paid vs Unpaid", barmode="stack",
                           xaxis=dict(**AX), yaxis=dict(**AX),
                           legend=dict(orientation="h",y=-0.3), **PLOT_BASE)
        st.plotly_chart(fig, use_container_width=True, key="billing_bar")
        st.dataframe(df_b, use_container_width=True)
        st.download_button("⬇ Export", df_b.to_csv(index=False), "billing.csv","text/csv")

with tab4:
    hvc_limit = st.slider("Show top N HVCs", 20, 200, 50, key="hvc_limit")

    @st.cache_data(ttl=30)
    def load_hvc(segment, region, limit):
        latest = get_latest_month()
        return query_op(f"""
            SELECT c.msisdn, c.full_name, c.region, c.segment,
                   cv.value_segment, cv.arpu, p.plan_name, p.supports_5g
            FROM customer_value cv
            JOIN customers c ON cv.msisdn=c.msisdn
            JOIN subscriptions s ON cv.msisdn=s.msisdn AND s.is_current=1
            JOIN plans p ON s.plan_id=p.plan_id
            WHERE cv.month='{latest}' AND cv.is_hvc=1 {sgf} {rf}
            ORDER BY cv.arpu DESC LIMIT {limit}
        """)

    hvc_data = load_hvc(segment, region, hvc_limit)
    if hvc_data:
        df_h = pd.DataFrame(hvc_data)
        df_h["supports_5g"] = df_h["supports_5g"].map({1:"✅",0:"❌"})
        not_5g = len(df_h[df_h["supports_5g"]=="❌"])
        st.markdown(f"""
        <div class="metric-card" style="border-left-color:#ff8800;margin-bottom:12px">
            <div class="metric-label">HVC NOT ON 5G PLAN</div>
            <div class="metric-value">{not_5g}</div>
            <div class="metric-sub">upsell opportunity</div>
        </div>""", unsafe_allow_html=True)
        st.dataframe(df_h, use_container_width=True, height=400)
        st.download_button("⬇ Export", df_h.to_csv(index=False), "hvc.csv","text/csv")