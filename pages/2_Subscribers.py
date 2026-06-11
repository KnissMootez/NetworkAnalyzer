import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from NetworkAnalyzer_agent import query_sc, query_op

st.set_page_config(page_title="Subscribers — NetworkAnalyzer", page_icon="👥", layout="wide")

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

st.markdown('<div class="page-title">👥 SUBSCRIBER <span>ANALYTICS</span></div>',
            unsafe_allow_html=True)

# ── FILTERS ──────────────────────────────────────────────────────────
@st.cache_data(ttl=60)
def get_regions():
    rows = query_sc("SELECT DISTINCT region FROM subscribers WHERE region IS NOT NULL ORDER BY region")
    return ["All"] + [r["region"] for r in rows]

fc1, fc2, fc3 = st.columns(3)
with fc1:
    region = st.selectbox("Region", get_regions(), key="sub_region")
with fc2:
    tech = st.multiselect("Technology", ["2G","3G","4G","5G"],
                           default=["2G","3G","4G","5G"], key="sub_tech")
with fc3:
    segment = st.selectbox("Segment", ["All","prepaid","postpaid","enterprise"],
                            key="sub_seg")

rf  = f"AND s.region='{region}'"     if region  != "All" else ""
tf  = "AND st.current_technology IN (" + ",".join(f"'{t}'" for t in tech) + ")" if tech else ""
sgf = f"AND c.segment='{segment}'"   if segment != "All" else ""

# ── SUMMARY CARDS ────────────────────────────────────────────────────
@st.cache_data(ttl=30)
def load_summary(region, tech, segment):
    total = query_sc(f"SELECT COUNT(DISTINCT s.msisdn) as n FROM subscribers s JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE s.is_active=1 {rf} {tf}")
    on_5g = query_sc(f"SELECT COUNT(*) as n FROM subscriber_technology st JOIN subscribers s ON s.msisdn=st.msisdn WHERE st.current_technology='5G' AND s.is_active=1 {rf}")
    on_3g = query_sc(f"SELECT COUNT(*) as n FROM subscriber_technology st JOIN subscribers s ON s.msisdn=st.msisdn WHERE st.current_technology='3G' AND s.is_active=1 {rf}")
    fwa   = query_sc(f"SELECT COUNT(DISTINCT mp.msisdn) as n FROM mobility_profile mp JOIN dou_monthly dm ON mp.msisdn=dm.msisdn AND mp.month=dm.month JOIN subscribers s ON mp.msisdn=s.msisdn WHERE mp.mobility_class='stationary' AND dm.total_data_gb>30 AND mp.month=(SELECT MAX(month) FROM mobility_profile) AND s.is_active=1 {rf}")
    return (v(total[0],"n") if total else 0,
            v(on_5g[0],"n") if on_5g else 0,
            v(on_3g[0],"n") if on_3g else 0,
            v(fwa[0],"n")   if fwa   else 0)

t, f5g, t3g, tfwa = load_summary(region, tech, segment)
sc = st.columns(4)
for col, (label, val, sub) in zip(sc, [
    ("TOTAL ACTIVE",   f"{t:,}",    "filtered base"),
    ("ON 5G",          f"{f5g:,}",  "currently connected"),
    ("ON 3G",          f"{t3g:,}",  "sunset risk"),
    ("FWA CANDIDATES", f"{tfwa:,}", "stationary high-DOU"),
]):
    col.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">{label}</div>
        <div class="metric-value">{val}</div>
        <div class="metric-sub">{sub}</div>
    </div>""", unsafe_allow_html=True)

st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "TECHNOLOGY","DEVICES","MOBILITY & DOU","3G SUNSET RISK","LOOKUP"
])

with tab1:
    @st.cache_data(ttl=30)
    def load_tech(region):
        return query_sc(f"SELECT st.current_technology as tech, COUNT(*) as n FROM subscriber_technology st JOIN subscribers s ON s.msisdn=st.msisdn WHERE s.is_active=1 {rf} GROUP BY tech ORDER BY n DESC")

    @st.cache_data(ttl=30)
    def load_tech_region(tech):
        tf2 = "AND st.current_technology IN (" + ",".join(f"'{t}'" for t in tech) + ")" if tech else ""
        return query_sc(f"SELECT s.region, st.current_technology as tech, COUNT(*) as n FROM subscribers s JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE s.is_active=1 {tf2} GROUP BY s.region, tech ORDER BY s.region, n DESC")

    td = load_tech(region)
    if td:
        df = pd.DataFrame(td)
        c1, c2 = st.columns(2)
        with c1:
            st.plotly_chart(pie(df["tech"].tolist(), df["n"].tolist(),
                                "Technology Distribution"),
                            use_container_width=True, key="sub_tech_pie")
        with c2:
            st.plotly_chart(bar(df,"tech","n","Subscribers by Technology"),
                            use_container_width=True, key="sub_tech_bar")

    trd = load_tech_region(tech)
    if trd:
        df2 = pd.DataFrame(trd)
        pivot = df2.pivot_table(index="region", columns="tech",
                                values="n", fill_value=0).reset_index()
        COLORS = {"2G":"#444","3G":"#880000","4G":"#cc0000","5G":"#ff4444"}
        fig = go.Figure()
        for col in [c for c in pivot.columns if c != "region"]:
            fig.add_trace(go.Bar(name=col, x=pivot["region"], y=pivot[col],
                                  marker_color=COLORS.get(col,"#888")))
        fig.update_layout(title="Technology Mix by Region", barmode="stack",
                           xaxis=dict(**AX), yaxis=dict(**AX),
                           legend=dict(orientation="h",y=-0.3), **PLOT_BASE)
        st.plotly_chart(fig, use_container_width=True, key="sub_tech_stack")

with tab2:
    @st.cache_data(ttl=60)
    def load_devices(region):
        return query_sc(f"SELECT d.brand, COUNT(*) as n, SUM(d.is_5g_capable) as five_g, SUM(d.volte_capable) as volte, SUM(CASE WHEN d.max_technology='3G' THEN 1 ELSE 0 END) as legacy FROM devices d JOIN subscribers s ON d.msisdn=s.msisdn WHERE s.is_active=1 {rf} GROUP BY d.brand ORDER BY n DESC LIMIT 15")

    @st.cache_data(ttl=60)
    def load_cap(region):
        return query_sc(f"SELECT SUM(d.is_5g_capable) as five_g_capable, SUM(d.volte_capable) as volte_capable, SUM(d.vowifi_capable) as vowifi_capable, SUM(CASE WHEN d.max_technology='3G' THEN 1 ELSE 0 END) as legacy_3g, COUNT(*) as total FROM devices d JOIN subscribers s ON d.msisdn=s.msisdn WHERE s.is_active=1 {rf}")

    cap = load_cap(region)
    if cap and v(cap[0],"total"):
        total_d = v(cap[0],"total")
        dc = st.columns(4)
        for col, (label, key, color) in zip(dc, [
            ("5G CAPABLE",    "five_g_capable",  "#ff4444"),
            ("VOLTE CAPABLE", "volte_capable",   "#ff8800"),
            ("VOWIFI",        "vowifi_capable",  "#0088ff"),
            ("LEGACY 3G",     "legacy_3g",       "#888"),
        ]):
            n = v(cap[0], key)
            pct = n*100//total_d if total_d else 0
            col.markdown(f"""
            <div class="metric-card" style="border-left-color:{color}">
                <div class="metric-label">{label}</div>
                <div class="metric-value" style="font-size:1.3rem">{n:,} ({pct}%)</div>
            </div>""", unsafe_allow_html=True)

    dd = load_devices(region)
    if dd:
        df = pd.DataFrame(dd)
        c1, c2 = st.columns(2)
        with c1:
            st.plotly_chart(bar(df,"brand","n","Devices by Brand"),
                            use_container_width=True, key="dev_brand")
        with c2:
            fig = go.Figure()
            fig.add_trace(go.Bar(name="5G",     x=df["brand"], y=df["five_g"],  marker_color="#ff4444"))
            fig.add_trace(go.Bar(name="VoLTE",  x=df["brand"], y=df["volte"],   marker_color="#ff8800"))
            fig.add_trace(go.Bar(name="Legacy", x=df["brand"], y=df["legacy"],  marker_color="#444"))
            fig.update_layout(title="Device Capabilities by Brand", barmode="group",
                               xaxis=dict(**AX), yaxis=dict(**AX),
                               legend=dict(orientation="h",y=-0.3), **PLOT_BASE)
            st.plotly_chart(fig, use_container_width=True, key="dev_cap")
        st.dataframe(df, use_container_width=True)

with tab3:
    @st.cache_data(ttl=30)
    def load_mobility(region):
        return query_sc(f"SELECT mp.mobility_class, COUNT(*) as n, ROUND(AVG(dm.total_data_gb),1) as avg_dou, SUM(mp.is_fwa_candidate) as fwa FROM mobility_profile mp JOIN dou_monthly dm ON mp.msisdn=dm.msisdn AND mp.month=dm.month JOIN subscribers s ON mp.msisdn=s.msisdn WHERE mp.month=(SELECT MAX(month) FROM mobility_profile) AND s.is_active=1 {rf} GROUP BY mp.mobility_class ORDER BY avg_dou DESC")

    @st.cache_data(ttl=30)
    def load_dou(region):
        return query_sc(f"""SELECT CASE WHEN dm.total_data_gb<2 THEN '< 2 GB' WHEN dm.total_data_gb<10 THEN '2-10 GB' WHEN dm.total_data_gb<30 THEN '10-30 GB' WHEN dm.total_data_gb<100 THEN '30-100 GB' ELSE '100+ GB' END as bucket, COUNT(*) as n FROM dou_monthly dm JOIN subscribers s ON dm.msisdn=s.msisdn WHERE dm.month=(SELECT MAX(month) FROM dou_monthly) AND s.is_active=1 {rf} GROUP BY bucket ORDER BY MIN(dm.total_data_gb)""")

    mob = load_mobility(region)
    dou = load_dou(region)
    if mob:
        df_m = pd.DataFrame(mob)
        c1, c2 = st.columns(2)
        with c1:
            st.plotly_chart(bar(df_m,"mobility_class","n","Subscribers by Mobility"),
                            use_container_width=True, key="mob_count")
        with c2:
            st.plotly_chart(bar(df_m,"mobility_class","avg_dou","Avg DOU by Mobility (GB)"),
                            use_container_width=True, key="mob_dou")
        st.dataframe(df_m, use_container_width=True)

    if dou:
        df_d = pd.DataFrame(dou)
        st.markdown('<div class="section-label">◈ DATA USAGE DISTRIBUTION</div>',
                    unsafe_allow_html=True)
        st.plotly_chart(bar(df_d,"bucket","n","Subscribers by Monthly Data Usage"),
                        use_container_width=True, key="dou_bucket")

with tab4:
    @st.cache_data(ttl=30)
    def load_sunset(region):
        return query_sc(f"""
            SELECT s.region,
                   COUNT(DISTINCT CASE WHEN d.volte_capable=0 AND st.current_technology='3G'
                         THEN s.msisdn END) as no_volte,
                   COUNT(DISTINCT CASE WHEN d.volte_capable=1 AND st.current_technology='3G'
                         AND st.volte_active=0 THEN s.msisdn END) as volte_inactive,
                   COUNT(DISTINCT CASE WHEN d.max_technology='3G'
                         THEN s.msisdn END) as legacy_device
            FROM subscribers s
            JOIN devices d ON s.msisdn=d.msisdn
            JOIN subscriber_technology st ON s.msisdn=st.msisdn
            WHERE s.is_active=1 {rf}
            GROUP BY s.region ORDER BY (no_volte+volte_inactive) DESC
        """)

    risk = load_sunset(region)
    if risk:
        df_r = pd.DataFrame(risk)
        total_risk = sum(v(r,"no_volte") + v(r,"volte_inactive") for r in risk)
        easy_fix   = sum(v(r,"volte_inactive") for r in risk)
        hard_fix   = sum(v(r,"no_volte") for r in risk)
        rc = st.columns(3)
        for col, (label, val, color, sub) in zip(rc, [
            ("TOTAL AT RISK", f"{total_risk:,}", "#ff1a1a", "will lose voice"),
            ("EASY FIX",      f"{easy_fix:,}",  "#ff8800", "just activate VoLTE"),
            ("NEEDS UPGRADE", f"{hard_fix:,}",  "#888",    "device replacement"),
        ]):
            col.markdown(f"""
            <div class="metric-card" style="border-left-color:{color}">
                <div class="metric-label">{label}</div>
                <div class="metric-value">{val}</div>
                <div class="metric-sub">{sub}</div>
            </div>""", unsafe_allow_html=True)

        fig = go.Figure()
        fig.add_trace(go.Bar(name="No VoLTE (upgrade needed)",
                              x=df_r["region"], y=df_r["no_volte"],
                              marker_color="#cc0000"))
        fig.add_trace(go.Bar(name="VoLTE Inactive (easy fix)",
                              x=df_r["region"], y=df_r["volte_inactive"],
                              marker_color="#ff8800"))
        fig.update_layout(title="3G Sunset Risk by Region", barmode="stack",
                           xaxis=dict(**AX), yaxis=dict(**AX),
                           legend=dict(orientation="h",y=-0.3), **PLOT_BASE)
        st.plotly_chart(fig, use_container_width=True, key="sunset_risk")
        st.dataframe(df_r, use_container_width=True)
        st.download_button("⬇ Export", df_r.to_csv(index=False),
                           "sunset_risk.csv","text/csv")

with tab5:
    st.markdown('<div class="section-label">◈ SUBSCRIBER LOOKUP</div>',
                unsafe_allow_html=True)
    msisdn_input = st.text_input("Enter MSISDN", placeholder="e.g. 21650000001",
                                  key="sub_lookup")
    if msisdn_input.strip():
        msisdn = msisdn_input.strip()
        sub    = query_sc(f"SELECT * FROM subscribers WHERE msisdn='{msisdn}'")
        dev    = query_sc(f"SELECT * FROM devices WHERE msisdn='{msisdn}'")
        tech_r = query_sc(f"SELECT * FROM subscriber_technology WHERE msisdn='{msisdn}'")
        dou_r  = query_sc(f"SELECT month, total_data_gb, distinct_cells_used, days_active FROM dou_monthly WHERE msisdn='{msisdn}' ORDER BY month DESC LIMIT 6")
        plan_r = query_op(f"SELECT p.plan_name, p.monthly_price, p.data_cap_gb, p.supports_5g, p.supports_volte FROM subscriptions s JOIN plans p ON s.plan_id=p.plan_id WHERE s.msisdn='{msisdn}' AND s.is_current=1")
        val_r  = query_op(f"SELECT month, arpu, value_segment, is_hvc FROM customer_value WHERE msisdn='{msisdn}' ORDER BY month DESC LIMIT 1")

        if not sub:
            st.error(f"MSISDN {msisdn} not found.")
        else:
            s = sub[0]
            lc1, lc2, lc3 = st.columns(3)
            with lc1:
                st.markdown("**Network Profile**")
                st.write(f"Region: **{v(s,'region','—')}** / {v(s,'city','—')}")
                st.write(f"SIM: {v(s,'sim_type','—')} | Active: {'✅' if v(s,'is_active') else '❌'}")
                if tech_r:
                    t = tech_r[0]
                    st.write(f"Technology: **{v(t,'current_technology','—')}**")
                    st.write(f"VoLTE: {'✅' if v(t,'volte_active') else '❌'}")
                    st.write(f"Last Seen: {v(t,'last_seen_date','—')}")
            with lc2:
                st.markdown("**Device**")
                if dev:
                    d = dev[0]
                    st.write(f"{v(d,'brand','—')} {v(d,'model','—')}")
                    st.write(f"Max Tech: **{v(d,'max_technology','—')}**")
                    st.write(f"5G: {'✅' if v(d,'is_5g_capable') else '❌'} | VoLTE: {'✅' if v(d,'volte_capable') else '❌'}")
                    st.write(f"OS: {v(d,'os','—')} {v(d,'os_version','—')}")
            with lc3:
                st.markdown("**Commercial**")
                if plan_r:
                    p = plan_r[0]
                    st.write(f"Plan: **{v(p,'plan_name','—')}**")
                    st.write(f"Price: **{v(p,'monthly_price',0)} TND/mo**")
                    st.write(f"Data: {v(p,'data_cap_gb',0)} GB | 5G: {'✅' if v(p,'supports_5g') else '❌'}")
                if val_r:
                    vv = val_r[0]
                    seg = v(vv,'value_segment','—')
                    st.write(f"Segment: **{seg.title()}** {'⭐' if v(vv,'is_hvc') else ''}")
                    st.write(f"ARPU: **{v(vv,'arpu',0)} TND**")

            if dou_r:
                df_dou = pd.DataFrame(dou_r)
                fig = go.Figure(go.Bar(x=df_dou["month"], y=df_dou["total_data_gb"],
                                        marker_color="#cc0000"))
                fig.update_layout(title="Monthly DOU (GB)", height=200,
                                   xaxis=dict(**AX), yaxis=dict(**AX), **PLOT_BASE)
                st.plotly_chart(fig, use_container_width=True, key="lookup_dou")