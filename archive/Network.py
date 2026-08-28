import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from NetworkAnalyzer_agent import query_sc

st.set_page_config(page_title="Network — NetworkAnalyzer", page_icon="📡", layout="wide")

# ── THEME ────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Share+Tech+Mono&family=Inter:wght@300;400;500;600&display=swap');
:root {
    --red-900:#1a0000;--red-800:#2d0000;--red-700:#4a0000;
    --red-600:#6b0000;--red-500:#990000;--red-400:#cc0000;
    --red-300:#ff1a1a;--red-200:#ff6666;
    --black:#0a0a0a;--dark:#111111;--mid:#1c1c1c;
    --surface:#222222;--border:#2a0000;--text:#e8e8e8;--muted:#888;
}
html,body,[data-testid="stAppViewContainer"]{background:var(--black)!important;color:var(--text)!important;font-family:'Inter',sans-serif;}
[data-testid="stSidebar"]{background:var(--dark)!important;border-right:2px solid var(--red-700)!important;}
.page-title{font-family:'Bebas Neue',sans-serif;font-size:2rem;letter-spacing:0.15em;color:#fff;border-bottom:1px solid var(--red-700);padding-bottom:8px;margin-bottom:1rem;}
.page-title span{color:var(--red-300);}
.section-label{font-family:'Bebas Neue',sans-serif;font-size:1rem;letter-spacing:0.2em;color:var(--red-300);border-bottom:1px solid var(--border);padding-bottom:4px;margin:12px 0 8px 0;}
.metric-card{background:var(--mid);border:1px solid var(--border);border-left:3px solid var(--red-400);padding:12px 16px;margin-bottom:4px;}
.metric-label{font-family:'Share Tech Mono',monospace;font-size:0.6rem;color:var(--muted);letter-spacing:0.2em;text-transform:uppercase;margin-bottom:4px;}
.metric-value{font-family:'Bebas Neue',sans-serif;font-size:1.8rem;color:#fff;line-height:1;}
.metric-sub{font-size:0.7rem;color:var(--red-200);margin-top:2px;}
.alarm-row{background:var(--mid);border-left:3px solid var(--red-400);padding:8px 12px;margin-bottom:4px;font-size:0.82rem;}
.alarm-row.critical{border-left-color:#ff1a1a;}
.alarm-row.major{border-left-color:#ff6600;}
.alarm-row.minor{border-left-color:#ffaa00;}
.alarm-row.warning{border-left-color:#0088ff;}
.stSelectbox>div>div{background:var(--mid)!important;border-color:var(--red-700)!important;color:var(--text)!important;}
.stMultiSelect>div>div{background:var(--mid)!important;border-color:var(--red-700)!important;}
[data-testid="stTabs"] button{font-family:'Share Tech Mono',monospace!important;font-size:0.7rem!important;letter-spacing:0.15em!important;color:var(--muted)!important;}
[data-testid="stTabs"] button[aria-selected="true"]{color:var(--red-300)!important;border-bottom-color:var(--red-400)!important;}
#MainMenu,footer,header{visibility:hidden;}
[data-testid="stToolbar"]{display:none;}
</style>
""", unsafe_allow_html=True)

PLOT_BASE = dict(
    paper_bgcolor="#111111", plot_bgcolor="#111111",
    font=dict(color="#e8e8e8", family="Share Tech Mono, monospace", size=11),
    margin=dict(l=16, r=16, t=36, b=16),
    colorway=["#cc0000","#ff4444","#ff8888","#880000","#ff6600","#0088ff"],
)
AX = dict(gridcolor="#1c1c1c", linecolor="#2a0000")

def bar(df, x, y, title, color_col=None):
    fig = go.Figure(go.Bar(
        x=df[x], y=df[y],
        marker=dict(
            color=df[color_col if color_col else y],
            colorscale=[[0,"#4a0000"],[0.5,"#cc0000"],[1,"#ff4444"]],
            line=dict(color="#1c1c1c", width=1)
        )
    ))
    fig.update_layout(title=dict(text=title, font_size=13),
                      xaxis=dict(**AX), yaxis=dict(**AX), **PLOT_BASE)
    return fig

# ── HEADER ───────────────────────────────────────────────────────────
st.markdown('<div class="page-title">📡 NETWORK <span>EXPLORER</span></div>',
            unsafe_allow_html=True)

# ── FILTERS ──────────────────────────────────────────────────────────
@st.cache_data(ttl=60)
def get_regions():
    rows = query_sc("SELECT DISTINCT region FROM sites WHERE region IS NOT NULL ORDER BY region")
    return ["All"] + [r[0] for r in rows]

@st.cache_data(ttl=60)
def get_dates():
    rows = query_sc("SELECT DISTINCT date FROM kpis_daily ORDER BY date DESC LIMIT 30")
    return [r[0] for r in rows]

fc1, fc2, fc3 = st.columns([2, 2, 2])
with fc1:
    regions = get_regions()
    region  = st.selectbox("Region", regions, key="net_region")
with fc2:
    dates   = get_dates()
    date    = st.selectbox("Date", dates if dates else ["latest"], key="net_date")
with fc3:
    tech_filter = st.multiselect("Technology", ["2G","3G","4G","5G"],
                                  default=["4G","5G"], key="net_tech")

region_filter = f"AND s.region='{region}'" if region != "All" else ""
tech_sql      = "(" + ",".join(f"'{t}'" for t in tech_filter) + ")" if tech_filter else "('4G')"

# ── TOP KPI CARDS ─────────────────────────────────────────────────────
@st.cache_data(ttl=30)
def load_top_kpis(region, date, tech_sql):
    rows = query_sc(f"""
        SELECT
            ROUND(AVG(k.dl_throughput_mbps),1)  as avg_dl,
            ROUND(AVG(k.availability_pct),2)     as avg_avail,
            ROUND(AVG(k.dropped_call_rate),3)    as avg_drop,
            ROUND(AVG(k.latency_ms),1)           as avg_lat,
            COUNT(DISTINCT k.cell_id)             as cells,
            SUM(k.active_users_avg)               as total_users
        FROM kpis_daily k
        JOIN cells c  ON k.cell_id=c.cell_id
        JOIN sites s  ON c.site_id=s.site_id
        WHERE k.date='{date}'
        AND c.technology IN {tech_sql}
        {region_filter if region != 'All' else ''}
    """)
    return rows[0] if rows else (0,0,0,0,0,0)

kpis = load_top_kpis(region, date, tech_sql)
kc = st.columns(6)
for col, (label, val, sub) in zip(kc, [
    ("AVG THROUGHPUT",  f"{kpis[0]} Mbps", "downlink"),
    ("AVAILABILITY",    f"{kpis[1]}%",     "uptime"),
    ("DROP RATE",       f"{kpis[2]}%",     "call drops"),
    ("LATENCY",         f"{kpis[3]} ms",   "round-trip"),
    ("ACTIVE CELLS",    f"{kpis[4]:,}" if kpis[4] else "—", "monitored"),
    ("ACTIVE USERS",    f"{int(kpis[5]):,}" if kpis[5] else "—", "on network"),
]):
    col.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">{label}</div>
        <div class="metric-value">{val}</div>
        <div class="metric-sub">{sub}</div>
    </div>""", unsafe_allow_html=True)

st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

# ── TABS ─────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs(["KPI OVERVIEW", "ALARMS", "CELL DRILLDOWN", "INCIDENTS"])

# ── TAB 1 — KPI OVERVIEW ─────────────────────────────────────────────
with tab1:
    @st.cache_data(ttl=30)
    def load_region_kpis(date, tech_sql):
        return query_sc(f"""
            SELECT s.region,
                   ROUND(AVG(k.dl_throughput_mbps),1)  as dl,
                   ROUND(AVG(k.availability_pct),2)     as avail,
                   ROUND(AVG(k.dropped_call_rate),3)    as drop_rate,
                   ROUND(AVG(k.sinr_avg),1)             as sinr,
                   ROUND(AVG(k.rsrp_avg),1)             as rsrp,
                   COUNT(DISTINCT a.alarm_id)            as alarms
            FROM kpis_daily k
            JOIN cells c  ON k.cell_id=c.cell_id
            JOIN sites s  ON c.site_id=s.site_id
            LEFT JOIN network_alarms a ON a.cell_id=c.cell_id AND a.is_active=1
            WHERE k.date='{date}' AND c.technology IN {tech_sql}
            GROUP BY s.region ORDER BY dl DESC
        """)

    rk = load_region_kpis(date, tech_sql)
    if rk:
        df = pd.DataFrame(rk, columns=["Region","DL Mbps","Availability %",
                                        "Drop Rate","SINR","RSRP","Alarms"])
        c1, c2 = st.columns(2)
        with c1:
            fig = go.Figure()
            fig.add_trace(go.Bar(name="Alarms", x=df["Region"], y=df["Alarms"],
                                  marker_color="#cc0000"))
            fig.add_trace(go.Scatter(name="Availability %", x=df["Region"],
                                      y=df["Availability %"], mode="lines+markers",
                                      line=dict(color="#ff8800", width=2),
                                      yaxis="y2"))
            fig.update_layout(title="Network Health by Region",
                               yaxis=dict(title="Alarms", **AX),
                               yaxis2=dict(title="Availability %", overlaying="y",
                                           side="right", range=[95,100], **AX),
                               xaxis=dict(**AX),
                               legend=dict(orientation="h", y=-0.3),
                               **PLOT_BASE)
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            st.plotly_chart(bar(df,"Region","DL Mbps","Avg Download Speed (Mbps)"),
                            use_container_width=True)

        c3, c4 = st.columns(2)
        with c3:
            st.plotly_chart(bar(df,"Region","SINR","Avg SINR (dB)"),
                            use_container_width=True)
        with c4:
            st.plotly_chart(bar(df,"Region","Drop Rate","Drop Rate (%)"),
                            use_container_width=True)

        st.markdown('<div class="section-label">◈ RAW DATA</div>', unsafe_allow_html=True)
        st.dataframe(df.style.background_gradient(subset=["Alarms"], cmap="Reds")
                             .background_gradient(subset=["Availability %"], cmap="RdYlGn"),
                     use_container_width=True)

        csv = df.to_csv(index=False)
        st.download_button("⬇ Export CSV", csv, "network_kpis.csv", "text/csv")

# ── TAB 2 — ALARMS ───────────────────────────────────────────────────
with tab2:
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        sev_filter = st.multiselect("Severity", ["critical","major","minor","warning"],
                                     default=["critical","major"], key="alarm_sev")
    with col_f2:
        alarm_limit = st.slider("Max rows", 10, 200, 50, key="alarm_limit")

    sev_sql = "(" + ",".join(f"'{s}'" for s in sev_filter) + ")" if sev_filter else "('critical')"

    @st.cache_data(ttl=15)
    def load_alarms(region, sev_sql, limit):
        rf = f"AND s.region='{region}'" if region != "All" else ""
        return query_sc(f"""
            SELECT a.alarm_id, s.region, s.site_name, c.technology,
                   a.alarm_type, a.severity, a.trigger_time, a.description
            FROM network_alarms a
            JOIN cells c  ON a.cell_id=c.cell_id
            JOIN sites s  ON c.site_id=s.site_id
            WHERE a.is_active=1 AND a.severity IN {sev_sql} {rf}
            ORDER BY
                CASE a.severity WHEN 'critical' THEN 1 WHEN 'major' THEN 2
                                WHEN 'minor' THEN 3 ELSE 4 END,
                a.trigger_time DESC
            LIMIT {limit}
        """)

    alarms = load_alarms(region, sev_sql, alarm_limit)

    if alarms:
        # Summary counts
        ac = st.columns(4)
        for col, sev, color in zip(ac,
            ["critical","major","minor","warning"],
            ["#ff1a1a","#ff6600","#ffaa00","#0088ff"]):
            cnt = sum(1 for a in alarms if a[5] == sev)
            col.markdown(f"""
            <div class="metric-card" style="border-left-color:{color}">
                <div class="metric-label">{sev.upper()}</div>
                <div class="metric-value">{cnt}</div>
            </div>""", unsafe_allow_html=True)

        st.markdown('<div class="section-label">◈ ACTIVE ALARMS</div>',
                    unsafe_allow_html=True)
        df_a = pd.DataFrame(alarms, columns=["ID","Region","Site","Tech",
                                               "Type","Severity","Triggered","Description"])
        st.dataframe(df_a, use_container_width=True, height=350)
        csv = df_a.to_csv(index=False)
        st.download_button("⬇ Export Alarms", csv, "alarms.csv", "text/csv")

        # Alarm type breakdown
        by_type = {}
        for a in alarms:
            by_type[a[4]] = by_type.get(a[4], 0) + 1
        df_t = pd.DataFrame(list(by_type.items()), columns=["Type","Count"])
        st.plotly_chart(bar(df_t,"Type","Count","Alarms by Type"),
                        use_container_width=True)
    else:
        st.info("No active alarms matching filters.")

# ── TAB 3 — CELL DRILLDOWN ───────────────────────────────────────────
with tab3:
    st.markdown('<div class="section-label">◈ WORST PERFORMING CELLS</div>',
                unsafe_allow_html=True)

    @st.cache_data(ttl=30)
    def load_worst_cells(region, date, tech_sql, metric, limit=20):
        rf = f"AND s.region='{region}'" if region != "All" else ""
        order = {
            "Drop Rate":    "k.dropped_call_rate DESC",
            "Low Throughput": "k.dl_throughput_mbps ASC",
            "Low Availability": "k.availability_pct ASC",
            "High Latency": "k.latency_ms DESC",
            "Low SINR":     "k.sinr_avg ASC",
        }.get(metric, "k.dropped_call_rate DESC")
        return query_sc(f"""
            SELECT c.cell_id, s.region, s.site_name, c.technology,
                   ROUND(k.dl_throughput_mbps,1) as dl,
                   ROUND(k.availability_pct,2)   as avail,
                   ROUND(k.dropped_call_rate,3)  as drop_rate,
                   ROUND(k.sinr_avg,1)            as sinr,
                   ROUND(k.rsrp_avg,1)            as rsrp,
                   k.congestion_level,
                   k.active_users_avg
            FROM kpis_daily k
            JOIN cells c ON k.cell_id=c.cell_id
            JOIN sites s ON c.site_id=s.site_id
            WHERE k.date='{date}' AND c.technology IN {tech_sql} {rf}
            ORDER BY {order} LIMIT {limit}
        """)

    mc1, mc2 = st.columns(2)
    with mc1:
        cell_metric = st.selectbox("Sort by worst",
            ["Drop Rate","Low Throughput","Low Availability","High Latency","Low SINR"],
            key="cell_metric")
    with mc2:
        cell_limit = st.slider("Show top N cells", 5, 50, 20, key="cell_limit")

    worst = load_worst_cells(region, date, tech_sql, cell_metric, cell_limit)
    if worst:
        df_c = pd.DataFrame(worst, columns=["Cell ID","Region","Site","Tech",
                                              "DL Mbps","Avail %","Drop %",
                                              "SINR","RSRP","Congestion","Users"])
        st.dataframe(df_c, use_container_width=True, height=350)

        col_a, col_b = st.columns(2)
        with col_a:
            fig = go.Figure(go.Bar(
                x=df_c["Cell ID"].astype(str), y=df_c["Drop %"],
                marker_color="#cc0000"
            ))
            fig.update_layout(title="Drop Rate by Cell", xaxis=dict(**AX),
                               yaxis=dict(**AX), **PLOT_BASE)
            st.plotly_chart(fig, use_container_width=True)
        with col_b:
            fig2 = go.Figure(go.Scatter(
                x=df_c["SINR"], y=df_c["DL Mbps"],
                mode="markers",
                marker=dict(color=df_c["Drop %"], colorscale="Reds",
                            size=8, showscale=True,
                            colorbar=dict(title="Drop %")),
                text=df_c["Site"]
            ))
            fig2.update_layout(title="SINR vs Throughput",
                                xaxis=dict(title="SINR (dB)", **AX),
                                yaxis=dict(title="DL Mbps", **AX),
                                **PLOT_BASE)
            st.plotly_chart(fig2, use_container_width=True)

# ── TAB 4 — INCIDENTS ─────────────────────────────────────────────────
with tab4:
    @st.cache_data(ttl=30)
    def load_incidents(region):
        rf = f"AND s.region='{region}'" if region != "All" else ""
        return query_sc(f"""
            SELECT i.incident_id, s.region, s.site_name, i.incident_type,
                   i.start_time, i.affected_users, i.severity,
                   i.root_cause, i.resolved
            FROM network_incidents i
            JOIN cells c ON i.cell_id=c.cell_id
            JOIN sites s ON c.site_id=s.site_id
            WHERE 1=1 {rf}
            ORDER BY i.start_time DESC LIMIT 100
        """)

    incidents = load_incidents(region)
    if incidents:
        df_i = pd.DataFrame(incidents, columns=["ID","Region","Site","Type",
                                                  "Start","Affected Users",
                                                  "Severity","Root Cause","Resolved"])
        r1, r2 = st.columns(2)
        with r1:
            total_affected = sum(i[5] for i in incidents)
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-label">TOTAL AFFECTED USERS</div>
                <div class="metric-value">{total_affected:,}</div>
                <div class="metric-sub">across all incidents</div>
            </div>""", unsafe_allow_html=True)
        with r2:
            unresolved = sum(1 for i in incidents if not i[8])
            st.markdown(f"""
            <div class="metric-card" style="border-left-color:#ff1a1a">
                <div class="metric-label">UNRESOLVED INCIDENTS</div>
                <div class="metric-value">{unresolved}</div>
                <div class="metric-sub">require attention</div>
            </div>""", unsafe_allow_html=True)

        st.dataframe(df_i, use_container_width=True, height=400)
        st.download_button("⬇ Export", df_i.to_csv(index=False),
                           "incidents.csv", "text/csv")
    else:
        st.info("No incidents found.")