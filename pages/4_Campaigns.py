import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from NetworkAnalyzer_agent import query_op, query_sc

st.set_page_config(page_title="Campaigns — NetworkAnalyzer", page_icon="🎯", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Share+Tech+Mono&family=Inter:wght@300;400;500;600&display=swap');
:root{--red-400:#cc0000;--red-300:#ff1a1a;--red-200:#ff6666;--black:#0a0a0a;--dark:#111111;--mid:#1c1c1c;--border:#2a0000;--text:#e8e8e8;--muted:#888;}
html,body,[data-testid="stAppViewContainer"]{background:var(--black)!important;color:var(--text)!important;font-family:'Inter',sans-serif;}
[data-testid="stSidebar"]{background:var(--dark)!important;border-right:2px solid #4a0000!important;}
.page-title{font-family:'Bebas Neue',sans-serif;font-size:2rem;letter-spacing:0.15em;color:#fff;border-bottom:1px solid #2a0000;padding-bottom:8px;margin-bottom:1rem;}
.page-title span{color:var(--red-300);}
.section-label{font-family:'Bebas Neue',sans-serif;font-size:1rem;letter-spacing:0.2em;color:var(--red-300);border-bottom:1px solid var(--border);padding-bottom:4px;margin:16px 0 10px 0;}
.metric-card{background:var(--mid);border:1px solid var(--border);border-left:3px solid var(--red-400);padding:14px 16px;margin-bottom:6px;}
.metric-label{font-family:'Share Tech Mono',monospace;font-size:0.6rem;color:var(--muted);letter-spacing:0.2em;text-transform:uppercase;margin-bottom:4px;}
.metric-value{font-family:'Bebas Neue',sans-serif;font-size:2rem;color:#fff;line-height:1;}
.metric-sub{font-size:0.7rem;color:var(--red-200);margin-top:3px;}
.opp-card{background:var(--mid);border:1px solid var(--border);border-left:4px solid var(--red-400);padding:16px 18px;margin-bottom:10px;}
.opp-title{font-family:'Bebas Neue',sans-serif;font-size:1.2rem;letter-spacing:0.1em;color:#fff;margin-bottom:4px;}
.opp-count{font-family:'Bebas Neue',sans-serif;font-size:2.2rem;line-height:1;margin-bottom:4px;}
.opp-desc{font-family:'Share Tech Mono',monospace;font-size:0.65rem;color:var(--muted);line-height:1.6;}
.offer-card{background:var(--mid);border:1px solid var(--border);border-left:3px solid var(--red-400);padding:12px 16px;margin-bottom:8px;}
.offer-name{font-family:'Bebas Neue',sans-serif;font-size:1.05rem;letter-spacing:0.1em;color:#fff;}
.offer-meta{font-family:'Share Tech Mono',monospace;font-size:0.65rem;color:var(--muted);margin-top:4px;line-height:1.8;}
.offer-badge{display:inline-block;padding:2px 8px;font-size:0.6rem;font-family:'Share Tech Mono',monospace;border-radius:2px;margin-right:6px;}
.camp-card{background:var(--mid);border:1px solid var(--border);border-left:4px solid #00cc44;padding:14px 16px;margin-bottom:8px;}
.progress-bar{height:5px;background:#111;border-radius:3px;margin-top:10px;overflow:hidden;}
.progress-fill{height:100%;border-radius:3px;background:linear-gradient(90deg,#880000,#cc0000,#ff4444);}
.empty-state{text-align:center;padding:40px 20px;font-family:'Share Tech Mono',monospace;font-size:0.75rem;color:#333;}
[data-testid="stTabs"] button{font-family:'Share Tech Mono',monospace!important;font-size:0.7rem!important;color:var(--muted)!important;}
[data-testid="stTabs"] button[aria-selected="true"]{color:var(--red-300)!important;border-bottom-color:var(--red-400)!important;}
.stButton button{background:transparent!important;border:1px solid #4a0000!important;color:var(--red-200)!important;font-family:'Share Tech Mono',monospace!important;font-size:0.72rem!important;border-radius:0!important;}
.stButton button:hover{background:#2a0000!important;border-color:var(--red-300)!important;color:#fff!important;}
#MainMenu,footer,header{visibility:hidden;}[data-testid="stToolbar"]{display:none;}
</style>
""", unsafe_allow_html=True)

PLOT_BASE = dict(
    paper_bgcolor="#111111", plot_bgcolor="#111111",
    font=dict(color="#e8e8e8", family="Share Tech Mono, monospace", size=11),
    margin=dict(l=16, r=16, t=36, b=16),
)
AX = dict(gridcolor="#1c1c1c", linecolor="#2a0000")

def v(row, key, default=0):
    try:
        return row[key] if row[key] is not None else default
    except (KeyError, TypeError):
        return default

def scalar(rows, key):
    """Extract single scalar from query result."""
    if not rows:
        return 0
    row = rows[0]
    if isinstance(row, dict):
        return row.get(key, list(row.values())[0] if row else 0) or 0
    return 0

st.markdown('<div class="page-title">🎯 CAMPAIGN <span>ENGINE</span></div>',
            unsafe_allow_html=True)

# ── DATA LOADERS ─────────────────────────────────────────────────────
@st.cache_data(ttl=15)
def load_summary():
    active    = scalar(query_op("SELECT COUNT(*) as n FROM campaigns WHERE status='active'"), "n")
    completed = scalar(query_op("SELECT COUNT(*) as n FROM campaigns WHERE status='completed'"), "n")
    targets   = scalar(query_op("SELECT COUNT(*) as n FROM campaign_targets"), "n")
    converted = scalar(query_op("SELECT COALESCE(SUM(converted),0) as n FROM campaign_targets"), "n")
    n_offers  = scalar(query_op("SELECT COUNT(*) as n FROM offers WHERE is_active=1"), "n")
    assigned  = scalar(query_op("SELECT COUNT(*) as n FROM offer_assignments"), "n")
    return active, completed, targets, converted, n_offers, assigned

@st.cache_data(ttl=15)
def load_campaigns():
    return query_op("""
        SELECT c.campaign_id, c.campaign_name, c.campaign_type,
               c.launched_date, c.status,
               COALESCE(o.offer_name,'—') as offer_name,
               COUNT(ct.msisdn) as targeted,
               COALESCE(SUM(ct.converted),0) as converted
        FROM campaigns c
        LEFT JOIN offers o ON c.offer_id=o.offer_id
        LEFT JOIN campaign_targets ct ON c.campaign_id=ct.campaign_id
        GROUP BY c.campaign_id ORDER BY c.launched_date DESC
    """)

@st.cache_data(ttl=30)
def load_offers():
    return query_op("""
        SELECT offer_id, offer_name, target_campaign, target_technology,
               discount_pct, bonus_data_gb, price_override,
               validity_days, is_active, description
        FROM offers ORDER BY is_active DESC, offer_id
    """)

@st.cache_data(ttl=60)
def load_opportunities():
    five_g    = scalar(query_sc("SELECT COUNT(DISTINCT s.msisdn) as n FROM subscribers s JOIN devices d ON s.msisdn=d.msisdn JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE d.is_5g_capable=1 AND st.current_technology='4G' AND EXISTS(SELECT 1 FROM coverage cv WHERE cv.msisdn=s.msisdn AND cv.technology_available='5G')"), "n")
    migration = scalar(query_sc("SELECT COUNT(DISTINCT s.msisdn) as n FROM subscribers s JOIN devices d ON s.msisdn=d.msisdn JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE d.max_technology IN ('4G','5G') AND st.current_technology='3G'"), "n")
    fwa       = scalar(query_sc("SELECT COUNT(DISTINCT mp.msisdn) as n FROM mobility_profile mp JOIN dou_monthly dm ON mp.msisdn=dm.msisdn AND mp.month=dm.month WHERE mp.mobility_class='stationary' AND dm.total_data_gb>30 AND mp.month=(SELECT MAX(month) FROM mobility_profile)"), "n")
    volte     = scalar(query_sc("SELECT COUNT(DISTINCT s.msisdn) as n FROM subscribers s JOIN devices d ON s.msisdn=d.msisdn JOIN subscriber_technology st ON s.msisdn=st.msisdn WHERE d.volte_capable=1 AND st.current_technology='3G' AND st.volte_active=0"), "n")
    hvc       = scalar(query_op("SELECT COUNT(*) as n FROM customer_value cv JOIN subscriptions s ON cv.msisdn=s.msisdn AND s.is_current=1 JOIN plans p ON s.plan_id=p.plan_id WHERE cv.is_hvc=1 AND p.supports_5g=0 AND cv.month=(SELECT MAX(month) FROM customer_value)"), "n")
    return five_g, migration, fwa, volte, hvc

# ── SUMMARY CARDS ────────────────────────────────────────────────────
ac, comp, targets, converted, n_offers, assigned = load_summary()
conv_rate = round(converted*100/targets, 1) if targets > 0 else 0

kc = st.columns(5)
for col, (label, val, sub, color) in zip(kc, [
    ("ACTIVE CAMPAIGNS", str(ac),           "running",            "#00cc44"),
    ("COMPLETED",        str(comp),          "finished",           "#888"),
    ("TOTAL TARGETED",   f"{targets:,}",     "subscribers",        "#cc0000"),
    ("CONVERTED",        f"{converted:,}",   f"{conv_rate}% rate", "#ff8800"),
    ("ACTIVE OFFERS",    str(n_offers),      f"{assigned:,} assigned","#0088ff"),
]):
    col.markdown(f"""
    <div class="metric-card" style="border-left-color:{color}">
        <div class="metric-label">{label}</div>
        <div class="metric-value">{val}</div>
        <div class="metric-sub">{sub}</div>
    </div>""", unsafe_allow_html=True)

st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)

tab1, tab2, tab3 = st.tabs(["OPPORTUNITIES","ACTIVE CAMPAIGNS","OFFERS CATALOGUE"])

# ── TAB 1 — OPPORTUNITIES ────────────────────────────────────────────
with tab1:
    with st.spinner("Calculating from network data..."):
        five_g, migration, fwa, volte, hvc = load_opportunities()

    OPP = [
        {"title":"5G UPSELL",      "count":five_g,    "color":"#ff4444","icon":"📶",
         "desc":"5G device + 4G plan\n+ 5G coverage available",
         "offer":"Pack 5G Discovery — 20% off, 45 TND/mo",
         "action":"find 5G upsell opportunities by region"},
        {"title":"3G MIGRATION",   "count":migration,  "color":"#ff8800","icon":"📵",
         "desc":"4G/5G device stuck on 3G\nat risk on 3G sunset",
         "offer":"4G Migration Pack — 15% off + 10GB",
         "action":"analyze 3G sunset migration opportunities"},
        {"title":"FWA CONVERSION", "count":fwa,        "color":"#0088ff","icon":"🏠",
         "desc":"Stationary + 30GB/month\nusing mobile as home internet",
         "offer":"FWA Home Starter — 29 TND/mo",
         "action":"identify FWA conversion candidates"},
        {"title":"VOLTE SUNSET",   "count":volte,      "color":"#ffaa00","icon":"📞",
         "desc":"VoLTE device on 3G\nVoLTE not yet activated",
         "offer":"VoLTE Ready Bundle — 10% off + 20GB",
         "action":"show VoLTE sunset risk subscribers"},
        {"title":"HVC UPSELL",     "count":hvc,        "color":"#cc0000","icon":"💎",
         "desc":"Gold/platinum customers\nnot on 5G plan",
         "offer":"HVC Platinum Pack — 25% off + 100GB",
         "action":"show high value customers not on 5G"},
    ]

    fig = go.Figure(go.Bar(
        x=[o["icon"]+" "+o["title"] for o in OPP],
        y=[o["count"] for o in OPP],
        marker=dict(color=[o["color"] for o in OPP],
                    line=dict(color="#1c1c1c",width=1)),
        text=[f"{o['count']:,}" for o in OPP],
        textposition="outside",
        textfont=dict(color="#e8e8e8",size=12)
    ))
    fig.update_layout(title="Addressable Subscribers by Campaign Type",
                       xaxis=dict(**AX), yaxis=dict(**AX,title="Subscribers"),
                       **PLOT_BASE)
    st.plotly_chart(fig, use_container_width=True, key="opp_bar")

    total_opp = sum(o["count"] for o in OPP)
    st.markdown(f"""
    <div style="font-family:'Share Tech Mono',monospace;font-size:0.7rem;color:#444;margin-bottom:12px;">
        TOTAL ADDRESSABLE: <span style="color:#cc0000">{total_opp:,}</span> subscribers
    </div>""", unsafe_allow_html=True)

    col_a, col_b = st.columns(2)
    for i, opp in enumerate(OPP):
        col = col_a if i % 2 == 0 else col_b
        with col:
            st.markdown(f"""
            <div class="opp-card" style="border-left-color:{opp['color']}">
                <div class="opp-title">{opp['icon']} {opp['title']}</div>
                <div class="opp-count" style="color:{opp['color']}">{opp['count']:,}</div>
                <div class="opp-desc">{opp['desc'].replace(chr(10),'<br>')}</div>
                <div style="margin-top:8px;font-family:'Share Tech Mono',monospace;font-size:0.62rem;color:#444;">
                    OFFER: <span style="color:#666">{opp['offer']}</span>
                </div>
            </div>""", unsafe_allow_html=True)
            if st.button(f"▸ Analyze in Copilot", key=f"opp_{opp['title']}",
                         use_container_width=True):
                st.session_state["copilot_prefill"] = opp["action"]
                st.switch_page("dashboard.py")

# ── TAB 2 — ACTIVE CAMPAIGNS ─────────────────────────────────────────
with tab2:
    camps = load_campaigns()
    if not camps:
        st.markdown("""
        <div class="empty-state">
            <div style="font-size:2rem;margin-bottom:12px">🎯</div>
            <div style="color:#333;margin-bottom:8px">NO CAMPAIGNS YET</div>
            <div style="color:#2a2a2a;font-size:0.65rem;line-height:2">
                Campaigns are created when you confirm a PROPOSE in the Copilot.<br>
                Go to Opportunities → Analyze in Copilot → confirm the proposal.
            </div>
        </div>""", unsafe_allow_html=True)
    else:
        sf = st.selectbox("Filter", ["all","active","draft","completed"], key="camp_sf")
        filtered = [c for c in camps if sf=="all" or v(c,"status")==sf]

        for camp in filtered:
            name     = v(camp,"campaign_name","—")
            ctype    = v(camp,"campaign_type","—")
            launched = str(v(camp,"launched_date","—"))[:10]
            status   = v(camp,"status","—")
            offer    = v(camp,"offer_name","—")
            targeted = int(v(camp,"targeted",0))
            conv     = int(v(camp,"converted",0))
            pct      = round(conv*100/targeted,1) if targeted > 0 else 0
            sc_color = {"active":"#00cc44","completed":"#888","draft":"#ff8800"}.get(status,"#cc0000")

            st.markdown(f"""
            <div class="camp-card" style="border-left-color:{sc_color}">
                <div style="display:flex;justify-content:space-between">
                    <div>
                        <div style="font-family:'Bebas Neue',sans-serif;font-size:1.1rem;color:#fff">{name}</div>
                        <div style="font-family:'Share Tech Mono',monospace;font-size:0.65rem;color:#555;margin-top:4px">
                            {ctype} &nbsp;|&nbsp; {offer} &nbsp;|&nbsp; {launched}
                        </div>
                    </div>
                    <div style="text-align:right;font-family:'Share Tech Mono',monospace">
                        <div style="font-size:0.6rem;color:{sc_color}">{status.upper()}</div>
                        <div style="font-family:'Bebas Neue',sans-serif;font-size:1.6rem">{pct}%</div>
                    </div>
                </div>
                <div style="display:flex;gap:16px;margin-top:6px;font-family:'Share Tech Mono',monospace;font-size:0.6rem;color:#444">
                    <span>TARGETED <b style="color:#e8e8e8">{targeted:,}</b></span>
                    <span>CONVERTED <b style="color:#ff4444">{conv:,}</b></span>
                    <span>REMAINING <b style="color:#555">{targeted-conv:,}</b></span>
                </div>
                <div class="progress-bar">
                    <div class="progress-fill" style="width:{min(pct,100)}%"></div>
                </div>
            </div>""", unsafe_allow_html=True)

        if len(filtered) > 1:
            df_c = pd.DataFrame(filtered)
            df_c["converted"] = df_c["converted"].fillna(0).astype(int)
            df_c["targeted"]  = df_c["targeted"].fillna(0).astype(int)
            df_c["rate"]      = (df_c["converted"]*100/df_c["targeted"].replace(0,1)).round(1)
            fig = go.Figure()
            fig.add_trace(go.Bar(name="Targeted",  x=df_c["campaign_name"], y=df_c["targeted"],  marker_color="#333"))
            fig.add_trace(go.Bar(name="Converted", x=df_c["campaign_name"], y=df_c["converted"], marker_color="#cc0000"))
            fig.update_layout(title="Campaign Performance", barmode="group",
                               xaxis=dict(**AX,tickangle=-20), yaxis=dict(**AX),
                               legend=dict(orientation="h",y=-0.3), **PLOT_BASE)
            st.plotly_chart(fig, use_container_width=True, key="camp_perf")
            st.dataframe(df_c[["campaign_name","campaign_type","status","targeted","converted","rate"]],
                         use_container_width=True)
            st.download_button("⬇ Export", df_c.to_csv(index=False), "campaigns.csv","text/csv")

# ── TAB 3 — OFFERS CATALOGUE ─────────────────────────────────────────
with tab3:
    offers_data = load_offers()
    CAMP_COLORS = {"5G_upsell":"#ff4444","3G_migration":"#ff8800",
                   "FWA":"#0088ff","VoLTE_sunset":"#ffaa00","HVC_upsell":"#cc0000"}
    CAMP_ICONS  = {"5G_upsell":"📶","3G_migration":"📵",
                   "FWA":"🏠","VoLTE_sunset":"📞","HVC_upsell":"💎"}

    if not offers_data:
        st.info("No offers found.")
    else:
        camp_types = sorted(set(v(o,"target_campaign","") for o in offers_data))
        ft = st.multiselect("Campaign type", camp_types, default=camp_types, key="off_f")
        filtered_o = [o for o in offers_data if v(o,"target_campaign") in ft]

        oc1, oc2 = st.columns(2)
        for i, offer in enumerate(filtered_o):
            camp     = v(offer,"target_campaign","")
            tech     = v(offer,"target_technology","")
            disc     = v(offer,"discount_pct",0)
            bonus    = v(offer,"bonus_data_gb",0)
            price_ov = v(offer,"price_override",None)
            validity = v(offer,"validity_days",30)
            active   = v(offer,"is_active",0)
            name     = v(offer,"offer_name","—")
            desc     = v(offer,"description","")
            color    = CAMP_COLORS.get(camp,"#cc0000")
            icon     = CAMP_ICONS.get(camp,"🎯")
            perks    = []
            if disc  and disc  > 0: perks.append(f"{disc:.0f}% OFF")
            if bonus and bonus > 0: perks.append(f"{bonus:.0f}GB BONUS")
            if price_ov:            perks.append(f"{price_ov:.0f} TND/MO")
            badges = "".join(
                f'<span class="offer-badge" style="background:{color}22;color:{color}">{p}</span>'
                for p in perks
            )
            col = oc1 if i%2==0 else oc2
            with col:
                st.markdown(f"""
                <div class="offer-card" style="border-left-color:{color}">
                    <div style="display:flex;justify-content:space-between;align-items:flex-start">
                        <div class="offer-name">{icon} {name}</div>
                        <div style="font-family:'Share Tech Mono',monospace;font-size:0.6rem;
                                    color:{'#00cc44' if active else '#555'}">
                            {'● ACTIVE' if active else '○ INACTIVE'}
                        </div>
                    </div>
                    <div style="margin:6px 0">{badges}</div>
                    <div class="offer-meta">
                        <span style="color:{color}">{camp}</span>
                        &nbsp;|&nbsp; TECH: {tech} &nbsp;|&nbsp; {validity} DAYS<br>
                        {desc}
                    </div>
                </div>""", unsafe_allow_html=True)

        by_camp = {}
        for o in filtered_o:
            c = v(o,"target_campaign","")
            by_camp[c] = by_camp.get(c,0) + 1
        df_bc = pd.DataFrame(list(by_camp.items()), columns=["Campaign","Offers"])
        fig = go.Figure(go.Bar(
            x=df_bc["Campaign"], y=df_bc["Offers"],
            marker=dict(color=[CAMP_COLORS.get(c,"#cc0000") for c in df_bc["Campaign"]],
                        line=dict(color="#1c1c1c",width=1))
        ))
        fig.update_layout(title="Offers per Campaign Type",
                           xaxis=dict(**AX), yaxis=dict(**AX), **PLOT_BASE)
        st.plotly_chart(fig, use_container_width=True, key="offers_bar")