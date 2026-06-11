import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import json
import re
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import NetworkAnalyzer_agent        as _agent_7b
import NetworkAnalyzer_agent_14b    as _agent_14b
import NetworkAnalyzer_agent_gemma  as _agent_gemma
import NetworkAnalyzer_agent_qwen3  as _agent_qwen3
from NetworkAnalyzer_agent import query_sc, query_op, get_proactive_alerts

def _active_agent():
    m = st.session_state.get("active_model")
    if m == "14b":    return _agent_14b
    if m == "gemma":  return _agent_gemma
    if m == "qwen3":  return _agent_qwen3
    return _agent_7b

def run_agent(user_input):    return _active_agent().run_agent(user_input)
def reset_memory():           _agent_7b.reset_memory(); _agent_14b.reset_memory(); _agent_gemma.reset_memory(); _agent_qwen3.reset_memory()

# ═══════════════════════════════════════════════════════════════════════
# PAGE CONFIG
# ═══════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="NetworkAnalyzer Copilot",
    page_icon="💠",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ═══════════════════════════════════════════════════════════════════════
# THEME
# ═══════════════════════════════════════════════════════════════════════

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Share+Tech+Mono&family=Inter:wght@300;400;500;600&display=swap');
:root {
    --p400:#818cf8;--p300:#a5b4fc;--p200:#c7d2fe;--p500:#6366f1;--p600:#4f46e5;--p700:#1e1b4b;
    --b400:#38bdf8;--b300:#7dd3fc;--b200:#bae6fd;--b500:#0ea5e9;--b600:#0369a1;--b700:#0c2340;
    --black:#080f1e;--dark:#0d1729;--mid:#131f35;--surface:#192540;
    --border:#253d63;--text:#e2eaf8;--muted:#6b87a8;--white:#ffffff;
}
html,body,[data-testid="stAppViewContainer"]{background-color:var(--black)!important;color:var(--text)!important;font-family:'Inter',sans-serif;}
[data-testid="stAppViewContainer"]{background:linear-gradient(135deg,#0a0f2a18 0%,transparent 60%),repeating-linear-gradient(0deg,transparent,transparent 40px,#1a2a4a06 40px,#1a2a4a06 41px),repeating-linear-gradient(90deg,transparent,transparent 40px,#1a2a4a06 40px,#1a2a4a06 41px),var(--black);}
[data-testid="stSidebar"]{background:var(--dark)!important;border-right:2px solid var(--b700)!important;}
[data-testid="stSidebar"]::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,var(--p400),var(--b400),transparent);}
.sc-header{font-family:'Bebas Neue',sans-serif;font-size:2.8rem;letter-spacing:0.15em;color:var(--white);line-height:1;}
.sc-subheader{font-family:'Share Tech Mono',monospace;font-size:0.72rem;color:var(--b300);letter-spacing:0.3em;text-transform:uppercase;margin-top:2px;}
.sc-logo-accent{color:var(--p300);}
.header-bar{border-bottom:1px solid var(--border);padding-bottom:1rem;margin-bottom:1.5rem;display:flex;align-items:flex-end;gap:1.5rem;}
.header-pulse{width:10px;height:10px;background:var(--p300);border-radius:50%;animation:pulse 1.8s ease-in-out infinite;margin-bottom:6px;flex-shrink:0;}
@keyframes pulse{0%,100%{opacity:1;box-shadow:0 0 0 0 #818cf866;}50%{opacity:0.6;box-shadow:0 0 0 6px #818cf800;}}
.metric-card{background:var(--mid);border:1px solid var(--border);border-left:3px solid var(--p400);padding:14px 16px;position:relative;overflow:hidden;}
.metric-card::after{content:'';position:absolute;top:0;right:0;width:40px;height:40px;background:var(--p600);opacity:0.12;clip-path:polygon(100% 0,0 0,100% 100%);}
.metric-label{font-family:'Share Tech Mono',monospace;font-size:0.62rem;color:var(--muted);letter-spacing:0.2em;text-transform:uppercase;margin-bottom:6px;}
.metric-value{font-family:'Bebas Neue',sans-serif;font-size:2rem;color:var(--white);line-height:1;}
.metric-sub{font-size:0.7rem;color:var(--p200);margin-top:3px;}
.alert-card{background:var(--mid);border:1px solid var(--border);border-left:3px solid var(--p400);padding:10px 14px;margin-bottom:8px;}
.alert-card.critical{border-left-color:#f87171;}
.alert-card.warning{border-left-color:#fb923c;}
.alert-card.info{border-left-color:var(--b400);}
.alert-msg{font-size:0.82rem;color:var(--text);}
.alert-action{font-family:'Share Tech Mono',monospace;font-size:0.68rem;color:var(--muted);margin-top:3px;}

.chat-container{display:flex;flex-direction:column;gap:12px;max-height:480px;overflow-y:auto;padding:4px 2px;scrollbar-width:thin;scrollbar-color:var(--border) var(--dark);}
.msg-user{align-self:flex-end;background:linear-gradient(135deg,var(--p600),var(--b600));color:var(--white);padding:10px 14px;max-width:75%;font-size:0.88rem;border-top-left-radius:8px;border-bottom-left-radius:8px;border-top-right-radius:2px;border-bottom-right-radius:8px;}
.msg-agent{align-self:flex-start;background:var(--surface);border:1px solid var(--border);border-left:3px solid var(--p400);color:var(--text);padding:12px 16px;max-width:85%;font-size:0.88rem;border-top-right-radius:8px;border-bottom-right-radius:8px;}
.msg-proposal{border-left-color:#fb923c;background:#0f1420;}
.msg-success{border-left-color:#34d399;background:#071a12;}
.msg-error{border-left-color:#f87171;background:#1a0a0a;}
.msg-label{font-family:'Share Tech Mono',monospace;font-size:0.6rem;color:var(--muted);letter-spacing:0.2em;text-transform:uppercase;margin-bottom:5px;}

.msg-typing{align-self:flex-start;background:var(--surface);border:1px solid var(--border);border-left:3px solid var(--b700);color:var(--muted);padding:12px 16px;max-width:85%;font-size:0.82rem;border-top-right-radius:8px;border-bottom-right-radius:8px;}
.typing-dots{display:inline-flex;gap:4px;align-items:center;margin-top:4px;}
.typing-dots span{width:6px;height:6px;background:var(--p400);border-radius:50%;animation:typing-bounce 1.2s ease-in-out infinite;}
.typing-dots span:nth-child(2){animation-delay:0.2s;}
.typing-dots span:nth-child(3){animation-delay:0.4s;}
@keyframes typing-bounce{0%,80%,100%{transform:translateY(0);opacity:0.4;}40%{transform:translateY(-5px);opacity:1;}}

[data-testid="stTextInput"] input{background:var(--mid)!important;border:1px solid var(--border)!important;border-radius:4px!important;color:var(--white)!important;font-family:'Share Tech Mono',monospace!important;font-size:0.85rem!important;}
[data-testid="stTextInput"] input:focus{border-color:var(--p400)!important;box-shadow:0 0 0 2px #818cf822!important;}
.stButton button{background:transparent!important;border:1px solid var(--p600)!important;color:var(--p200)!important;font-family:'Share Tech Mono',monospace!important;font-size:0.75rem!important;letter-spacing:0.15em!important;border-radius:4px!important;transition:all 0.15s ease!important;}
.stButton button:hover{background:var(--p700)!important;border-color:var(--p300)!important;color:var(--white)!important;}
.confirm-btn button{border-color:#34d399!important;color:#34d399!important;}
.confirm-btn button:hover{background:#071a12!important;color:#6ee7b7!important;}
.cancel-btn button{border-color:#f87171!important;color:#f87171!important;}
.section-label{font-family:'Bebas Neue',sans-serif;font-size:1.1rem;letter-spacing:0.2em;color:var(--p300);border-bottom:1px solid var(--border);padding-bottom:6px;margin-bottom:12px;margin-top:8px;}
hr{border-color:var(--border)!important;}
[data-testid="stDataFrame"]{border:1px solid var(--border)!important;}
.sidebar-title{font-family:'Bebas Neue',sans-serif;font-size:1.0rem;letter-spacing:0.25em;color:var(--p300);margin-bottom:8px;}
.status-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#34d399;margin-right:6px;animation:pulse 2s infinite;}
.status-line{font-family:'Share Tech Mono',monospace;font-size:0.68rem;color:var(--muted);margin:3px 0;}
[data-testid="stTabs"] button{font-family:'Share Tech Mono',monospace!important;font-size:0.7rem!important;letter-spacing:0.15em!important;color:var(--muted)!important;}
[data-testid="stTabs"] button[aria-selected="true"]{color:var(--p300)!important;border-bottom-color:var(--p400)!important;}
::-webkit-scrollbar{width:4px;}
::-webkit-scrollbar-track{background:var(--dark);}
::-webkit-scrollbar-thumb{background:var(--border);}
#MainMenu,footer,header{visibility:hidden;}
[data-testid="stToolbar"]{display:none;}
</style>
""", unsafe_allow_html=True)

# FIX 7: Auto-scroll JS — scrolls the chat container to bottom after render
AUTOSCROLL_JS = """
<script>
(function() {
    function scrollChat() {
        var containers = document.querySelectorAll('.chat-container');
        containers.forEach(function(c) { c.scrollTop = c.scrollHeight; });
    }
    // Run immediately and after a short delay (Streamlit may still be rendering)
    scrollChat();
    setTimeout(scrollChat, 150);
    setTimeout(scrollChat, 400);
})();
</script>
"""

# ═══════════════════════════════════════════════════════════════════════
# SESSION STATE
# ═══════════════════════════════════════════════════════════════════════

if "messages"       not in st.session_state: st.session_state.messages       = []
if "alerts"         not in st.session_state: st.session_state.alerts         = []
if "alerts_loaded"  not in st.session_state: st.session_state.alerts_loaded  = False
if "pending"        not in st.session_state: st.session_state.pending        = False
if "input_key"      not in st.session_state: st.session_state.input_key      = 0
if "last_result"    not in st.session_state: st.session_state.last_result    = None
if "analyzing"      not in st.session_state: st.session_state.analyzing      = False
if "steps_log"      not in st.session_state: st.session_state.steps_log      = {}
if "chart_history"  not in st.session_state: st.session_state.chart_history  = []

# ═══════════════════════════════════════════════════════════════════════
# CHART HELPERS
# ═══════════════════════════════════════════════════════════════════════

PLOT_BASE = dict(
    paper_bgcolor="#111e35", plot_bgcolor="#111e35",
    font=dict(color="#dce4f5", family="Share Tech Mono, monospace", size=11),
    margin=dict(l=16, r=16, t=36, b=16),
    colorway=["#818cf8","#60a5fa","#a78bfa","#34d399","#fb923c","#94a3b8"],
)
AXIS_STYLE = dict(gridcolor="#1e2f52", linecolor="#1e2f52")

def bar_chart(df, x, y, title):
    fig = go.Figure(go.Bar(
        x=df[x], y=df[y],
        marker=dict(color=df[y],
                    colorscale=[[0,"#1e3460"],[0.5,"#4f46e5"],[1,"#a78bfa"]],
                    line=dict(color="#0d1729",width=1))
    ))
    fig.update_layout(title=dict(text=title,font_size=13),
                      xaxis=dict(**AXIS_STYLE), yaxis=dict(**AXIS_STYLE), **PLOT_BASE)
    return fig

def pie_chart(labels, values, title):
    fig = go.Figure(go.Pie(
        labels=labels, values=values, hole=0.55,
        marker=dict(colors=["#818cf8","#60a5fa","#a78bfa","#34d399","#fb923c","#94a3b8"],
                    line=dict(color="#0d1729",width=2))
    ))
    fig.update_layout(title=dict(text=title,font_size=13), **PLOT_BASE)
    return fig

def _chart_key():
    return str(abs(hash(str(time.time()))) % 10000000) + "_" + str(int(time.time()*1000) % 10000)

def render_structured_response(result: dict, key_prefix: str = ""):
    """Render chart from structured agent response."""
    chart = result.get("chart")
    if not chart:
        return

    key     = key_prefix + _chart_key()
    ctype   = chart.get("type", "bar")
    title   = chart.get("title", "")
    x_label = chart.get("x_label", "")
    y_label = chart.get("y_label", "")

    # ── treemap (uses labels/parents/values, not x/y) ──────────────
    if ctype == "treemap":
        labels      = chart.get("labels", [])
        parents     = chart.get("parents", [])
        values      = chart.get("values", [])
        value_unit  = chart.get("value_unit", "")
        value_label = chart.get("value_label", "Value")
        if not labels or not parents or not values:
            return
        # Depth-based colors: root=darkest, leaves=brightest
        depths = []
        parent_map = dict(zip(labels, parents))
        for lbl in labels:
            d = 0
            p = parent_map.get(lbl, "")
            while p:
                d += 1
                p = parent_map.get(p, "")
            depths.append(d)
        # Depth-based: deep navy at root → bright violet/blue at leaves
        depth_palettes = [
            (30,  45, 100),   # depth 0 — dark navy
            (50,  70, 160),   # depth 1 — mid blue
            (100, 110, 220),  # depth 2 — indigo
            (160, 150, 250),  # depth 3 — violet
            (200, 190, 255),  # depth 4 — light violet
        ]
        node_colors = []
        for d in depths:
            r, g, b = depth_palettes[min(d, len(depth_palettes)-1)]
            node_colors.append(f"rgba({r},{g},{b},1)")
        fig = go.Figure(go.Treemap(
            labels=labels, parents=parents, values=values,
            branchvalues="total",
            textinfo="label+value+percent parent",
            textfont=dict(color="#ffffff", size=12),
            marker=dict(
                colors=node_colors,
                line=dict(color="#0a0a0a", width=2),
            ),
            pathbar=dict(visible=True, thickness=22,
                         textfont=dict(color="#e8e8e8", size=11)),
            hovertemplate=f"<b>%{{label}}</b><br>{value_label}: %{{value:,.2f}} {value_unit}<br>%{{percentParent:.1%}} of parent<extra></extra>"
        ))
        treemap_layout = {**PLOT_BASE, "margin": dict(l=10, r=10, t=40, b=10)}
        fig.update_layout(title=title, **treemap_layout)
        st.plotly_chart(fig, use_container_width=True, key=f"chart_tree_{key}")
        return

    # ── heatmap (uses x/y/z) ────────────────────────────────────────
    if ctype == "heatmap":
        x = chart.get("x", [])
        y = chart.get("y", [])
        z = chart.get("z", [])
        if not x or not y or not z:
            return
        fig = go.Figure(go.Heatmap(
            x=x, y=y, z=z,
            colorscale=[[0,"#0d1729"],[0.5,"#4f46e5"],[1,"#a78bfa"]],
            showscale=True
        ))
        fig.update_layout(title=title,
                          xaxis=dict(title=x_label,**AXIS_STYLE),
                          yaxis=dict(title=y_label,**AXIS_STYLE), **PLOT_BASE)
        st.plotly_chart(fig, use_container_width=True, key=f"chart_heat_{key}")
        return

    # ── all other charts need x and y ───────────────────────────────
    x = chart.get("x", [])
    y = chart.get("y", [])
    if not x or not y:
        return

    # If y is a dict, it's always a multibar regardless of declared type
    if isinstance(y, dict):
        ctype = "multibar"

    if ctype == "bar":
        fig = go.Figure(go.Bar(
            x=x, y=y,
            marker=dict(
                color=y if all(isinstance(v,(int,float)) for v in y) else "#818cf8",
                colorscale=[[0,"#1e3460"],[0.5,"#4f46e5"],[1,"#a78bfa"]],
                line=dict(color="#0d1729",width=1)
            ),
            text=[f"{v:,.2f}" if isinstance(v,float) else f"{v:,}" for v in y],
            textposition="outside", textfont=dict(color="#e8e8e8")
        ))
        fig.update_layout(title=title,
                          xaxis=dict(title=x_label,**AXIS_STYLE),
                          yaxis=dict(title=y_label,**AXIS_STYLE), **PLOT_BASE)
        st.plotly_chart(fig, use_container_width=True, key=f"chart_bar_{key}")

    elif ctype == "pie":
        fig = go.Figure(go.Pie(
            labels=x, values=y, hole=0.5,
            marker=dict(colors=["#818cf8","#60a5fa","#a78bfa","#34d399","#fb923c","#94a3b8"],
                        line=dict(color="#0d1729",width=2))
        ))
        fig.update_layout(title=title, **PLOT_BASE)
        st.plotly_chart(fig, use_container_width=True, key=f"chart_pie_{key}")

    elif ctype == "line":
        fig = go.Figure(go.Scatter(
            x=x, y=y, mode="lines+markers",
            line=dict(color="#818cf8",width=2),
            marker=dict(size=6, color="#a78bfa")
        ))
        fig.update_layout(title=title,
                          xaxis=dict(title=x_label,**AXIS_STYLE),
                          yaxis=dict(title=y_label,**AXIS_STYLE), **PLOT_BASE)
        st.plotly_chart(fig, use_container_width=True, key=f"chart_line_{key}")

    elif ctype == "area":
        fig = go.Figure(go.Scatter(
            x=x, y=y, mode="lines", fill="tozeroy",
            line=dict(color="#818cf8",width=2),
            fillcolor="rgba(129,140,248,0.15)"
        ))
        fig.update_layout(title=title,
                          xaxis=dict(title=x_label,**AXIS_STYLE),
                          yaxis=dict(title=y_label,**AXIS_STYLE), **PLOT_BASE)
        st.plotly_chart(fig, use_container_width=True, key=f"chart_area_{key}")

    elif ctype == "scatter":
        labels_arr = chart.get("labels", x)
        fig = go.Figure(go.Scatter(
            x=x, y=y, mode="markers", text=labels_arr,
            marker=dict(
                color=y if all(isinstance(v,(int,float)) for v in y) else "#818cf8",
                colorscale=[[0,"#1e3460"],[1,"#a78bfa"]],
                size=8, showscale=True
            )
        ))
        fig.update_layout(title=title,
                          xaxis=dict(title=x_label,**AXIS_STYLE),
                          yaxis=dict(title=y_label,**AXIS_STYLE), **PLOT_BASE)
        st.plotly_chart(fig, use_container_width=True, key=f"chart_scatter_{key}")

    elif ctype == "histogram":
        fig = go.Figure(go.Histogram(
            x=x,
            marker=dict(color="#818cf8", line=dict(color="#0d1729",width=1)),
            opacity=0.85
        ))
        fig.update_layout(title=title, bargap=0.05,
                          xaxis=dict(title=x_label,**AXIS_STYLE),
                          yaxis=dict(title="Frequency",**AXIS_STYLE), **PLOT_BASE)
        st.plotly_chart(fig, use_container_width=True, key=f"chart_hist_{key}")

    elif ctype == "multibar":
        SERIES_COLORS = ["#818cf8","#60a5fa","#a78bfa","#34d399","#fb923c","#94a3b8"]
        fig = go.Figure()
        if isinstance(y, dict):
            for i, (series_name, values) in enumerate(y.items()):
                fig.add_trace(go.Bar(name=series_name, x=x, y=values,
                                     marker_color=SERIES_COLORS[i % len(SERIES_COLORS)]))
        fig.update_layout(title=title, barmode="group",
                          xaxis=dict(title=x_label,**AXIS_STYLE),
                          yaxis=dict(title=y_label,**AXIS_STYLE),
                          legend=dict(orientation="h",y=-0.3), **PLOT_BASE)
        st.plotly_chart(fig, use_container_width=True, key=f"chart_multi_{key}")

# ═══════════════════════════════════════════════════════════════════════
# SIDEBAR
# ═══════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("""
    <div style="text-align:center;padding:8px 0 16px 0;">
        <div style="font-family:'Bebas Neue',sans-serif;font-size:1.6rem;letter-spacing:0.2em;color:#fff;">
            SMART<span style="color:#a78bfa;">CARE</span>
        </div>
        <div style="font-family:'Share Tech Mono',monospace;font-size:0.6rem;color:#555;letter-spacing:0.3em;">
            COPILOT // UC PLATFORM
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div style="background:#111e35;border:1px solid #1e2f52;padding:10px 12px;margin-bottom:12px;">
        <div class="status-line"><span class="status-dot"></span>OLLAMA CONNECTED</div>
        <div class="status-line">◈ MODEL : QWEN2.5:7B</div>
        <div class="status-line">◈ RAG   : 12 DOCUMENTS</div>
        <div class="status-line">◈ DB    : NetworkAnalyzer + OPERATOR</div>
    </div>
    """, unsafe_allow_html=True)

    if not st.session_state.alerts_loaded:
        if st.button("⚡ SCAN NETWORK", use_container_width=True):
            with st.spinner("Scanning..."):
                st.session_state.alerts = get_proactive_alerts()
                st.session_state.alerts_loaded = True
            st.rerun()
    else:
        st.markdown('<div class="sidebar-title">▸ ACTIVE ALERTS</div>', unsafe_allow_html=True)
        if not st.session_state.alerts:
            st.markdown('<div class="status-line">No alerts detected.</div>', unsafe_allow_html=True)
        for alert in st.session_state.alerts:
            sev = alert.get("severity","info")
            st.markdown(f"""
            <div class="alert-card {sev}">
                <div class="alert-msg">{alert['message']}</div>
                <div class="alert-action">▸ {alert['action']}</div>
            </div>""", unsafe_allow_html=True)
            if st.button("Investigate", key=f"inv_{alert['type']}", use_container_width=True):
                st.session_state.messages.append({"role":"user","text":alert["action"]})
                st.rerun()

    st.markdown("---")
    st.markdown('<div class="sidebar-title">▸ QUICK QUERIES</div>', unsafe_allow_html=True)

    quick = [
        ("📶 5G Upsell",      "find 5G upsell opportunities by region"),
        ("📵 3G Sunset",      "which subscribers are at risk from 3G sunset by region"),
        ("🏠 FWA Convert",    "identify FWA conversion candidates by region"),
        ("💎 HVC Status",     "show high value customer distribution"),
        ("⚠️ Network KPIs",  "show network KPI summary by region"),
        ("🔔 Critical Alarms","list active critical network alarms"),
    ]
    for label, query in quick:
        if st.button(label, use_container_width=True, key=f"q_{label}"):
            st.session_state.messages.append({"role":"user","text":query})
            st.rerun()

    st.markdown("---")
    st.markdown('<div class="sidebar-title">▸ MODEL</div>', unsafe_allow_html=True)

    _model_options = {"qwen2.5:7b": "7b", "qwen2.5:14b": "14b", "gemma2:9b": "gemma", "qwen3:8b": "qwen3"}
    _default_gpu   = {"qwen2.5:7b": 20,   "qwen2.5:14b": 28,   "gemma2:9b": 28,      "qwen3:8b": 20}
    _m = st.session_state.get("active_model", "7b")
    _cur_key = next((k for k, v in _model_options.items() if v == _m), "qwen2.5:7b")

    _selected_model = st.selectbox(
        "Model", list(_model_options.keys()),
        index=list(_model_options.keys()).index(_cur_key),
        label_visibility="collapsed"
    )
    _gpu_val = st.slider(
        "GPU layers", 0, 48, value=_default_gpu[_selected_model], step=1
    )

    _rag_mode = st.selectbox(
        "RAG", ["auto", "always", "never"],
        index=["auto", "always", "never"].index(_agent_7b.RAG_MODE),
        help="auto = model decides per query | always = force RAG | never = skip RAG",
        label_visibility="collapsed"
    )

    if st.button("APPLY MODEL", use_container_width=True, key="apply_model"):
        st.session_state.active_model = _model_options[_selected_model]
        for _ag in (_agent_7b, _agent_14b, _agent_gemma, _agent_qwen3):
            _ag.MODEL          = _selected_model
            _ag.NUM_GPU_LAYERS = _gpu_val
            _ag.RAG_MODE       = _rag_mode
        reset_memory()
        st.session_state.messages      = []
        st.session_state.steps_log     = {}
        st.session_state.alerts        = []
        st.session_state.alerts_loaded = False
        st.session_state.pending       = False
        st.session_state.last_result   = None
        st.session_state.chart_history = []
        st.session_state.analyzing     = False
        st.session_state.input_key    += 1
        st.rerun()

    st.markdown("---")
    if st.button("🗑 CLEAR SESSION", use_container_width=True):
        reset_memory()
        st.session_state.messages      = []
        st.session_state.alerts        = []
        st.session_state.alerts_loaded = False
        st.session_state.pending       = False
        st.session_state.last_result   = None
        st.session_state.chart_history = []
        st.session_state.analyzing     = False
        st.session_state.input_key    += 1
        st.rerun()

# ═══════════════════════════════════════════════════════════════════════
# HEADER
# ═══════════════════════════════════════════════════════════════════════

st.markdown("""
<div class="header-bar">
    <div>
        <div class="sc-header">SMART<span class="sc-logo-accent">CARE</span> COPILOT</div>
        <div class="sc-subheader">◈ Network Intelligence · Subscriber Analytics · Campaign Engine</div>
    </div>
    <div class="header-pulse"></div>
</div>
""", unsafe_allow_html=True)

# ── KPI Cards ────────────────────────────────────────────────────────

@st.cache_data(ttl=30)
def load_kpis():
    total  = query_sc("SELECT COUNT(*) as n FROM subscribers WHERE is_active=1")
    alarms = query_sc("SELECT COUNT(*) as n FROM network_alarms WHERE is_active=1 AND severity='critical'")
    hvc    = query_op("SELECT COUNT(*) as n FROM customer_value WHERE is_hvc=1 AND month=(SELECT MAX(month) FROM customer_value)")
    fwa    = query_sc("SELECT COUNT(DISTINCT mp.msisdn) as n FROM mobility_profile mp JOIN dou_monthly dm ON mp.msisdn=dm.msisdn AND mp.month=dm.month WHERE mp.mobility_class='stationary' AND dm.total_data_gb>30 AND mp.month=(SELECT MAX(month) FROM mobility_profile)")
    return (
        f"{total[0]['n']:,}"  if total  else "—",
        str(alarms[0]['n'])   if alarms else "—",
        f"{hvc[0]['n']:,}"    if hvc    else "—",
        f"{fwa[0]['n']:,}"    if fwa    else "—",
    )

t_subs, t_alarms, t_hvc, t_fwa = load_kpis()
kpi_cols = st.columns(4)
for col, (label, value, sub) in zip(kpi_cols, [
    ("ACTIVE SUBSCRIBERS", t_subs,   "network total"),
    ("CRITICAL ALARMS",    t_alarms, "requires attention"),
    ("HVC CUSTOMERS",      t_hvc,    "gold + platinum"),
    ("FWA CANDIDATES",     t_fwa,    "stationary high-DOU"),
]):
    col.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">{label}</div>
        <div class="metric-value">{value}</div>
        <div class="metric-sub">{sub}</div>
    </div>""", unsafe_allow_html=True)

st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

# ── Nav bar ──────────────────────────────────────────────────────────

st.markdown("""
<div style="background:#111e35;border:1px solid #1e2f52;padding:10px 16px;
            margin-bottom:12px;display:flex;gap:24px;align-items:center;">
    <div style="font-family:'Share Tech Mono',monospace;font-size:0.62rem;color:#5a7090;letter-spacing:0.15em;">EXPLORE ▸</div>
    <a href="/Network"     target="_self" style="font-family:'Share Tech Mono',monospace;font-size:0.7rem;color:#818cf8;text-decoration:none;letter-spacing:0.1em;">📡 NETWORK</a>
    <a href="/Subscribers" target="_self" style="font-family:'Share Tech Mono',monospace;font-size:0.7rem;color:#818cf8;text-decoration:none;letter-spacing:0.1em;">👥 SUBSCRIBERS</a>
    <a href="/Commercial"  target="_self" style="font-family:'Share Tech Mono',monospace;font-size:0.7rem;color:#818cf8;text-decoration:none;letter-spacing:0.1em;">💰 COMMERCIAL</a>
    <a href="/Campaigns"   target="_self" style="font-family:'Share Tech Mono',monospace;font-size:0.7rem;color:#818cf8;text-decoration:none;letter-spacing:0.1em;">🎯 CAMPAIGNS</a>
</div>
""", unsafe_allow_html=True)

# ── Two-column layout ─────────────────────────────────────────────────

col_chat, col_viz = st.columns([3, 2], gap="medium")

# ─────────────────────────────────────────────────────────────────────
# LEFT — CHAT
# ─────────────────────────────────────────────────────────────────────

with col_chat:
    st.markdown('<div class="section-label">◈ COPILOT INTERFACE</div>', unsafe_allow_html=True)

    chat_container = st.container(height=480, border=False)
    with chat_container:
        if not st.session_state.messages:
            st.markdown("""
            <div style="text-align:center;padding:40px 20px;color:#333;">
                <div style="font-family:'Bebas Neue',sans-serif;font-size:1.4rem;letter-spacing:0.2em;color:#1e3460;margin-bottom:8px;">READY FOR ANALYSIS</div>
                <div style="font-family:'Share Tech Mono',monospace;font-size:0.7rem;color:#444;line-height:1.8;">
                    Ask about network KPIs · subscriber campaigns<br>
                    5G upsell · FWA conversion · 3G migration
                </div>
            </div>""", unsafe_allow_html=True)
        else:
            for i, msg in enumerate(st.session_state.messages):
                if msg["role"] == "user":
                    st.markdown(f'<div class="msg-user">{msg["text"]}</div>', unsafe_allow_html=True)
                else:
                    mtype = msg.get("type","analysis")
                    cls   = "msg-agent"
                    label = "COPILOT"
                    if   mtype == "proposal":  cls += " msg-proposal"; label = "PROPOSAL — AWAITING CONFIRMATION"
                    elif mtype == "success":   cls += " msg-success";  label = "EXECUTED"
                    elif mtype == "cancelled": cls += " msg-error";    label = "CANCELLED"
                    elif mtype == "error":     cls += " msg-error";    label = "ERROR"
                    safe = msg["text"].replace("\n","<br>").replace("**","")
                    elapsed = msg.get("elapsed")
                    elapsed_str = f'<div style="font-family:\'Share Tech Mono\',monospace;font-size:0.58rem;color:#444;margin-top:4px;">⏱ {elapsed}s</div>' if elapsed else ""
                    st.markdown(f'<div class="{cls}"><div class="msg-label">{label}</div>{safe}{elapsed_str}</div>', unsafe_allow_html=True)
                    steps = st.session_state.steps_log.get(i, [])
                    if steps:
                        with st.expander(f"◈ {len(steps)} reasoning steps", expanded=False):
                            for step in steps:
                                s = step.strip()
                                if any(t in s for t in ("QUERY_SC", "QUERY_OP", "QUERY_BOTH")):
                                    sql = s.split(":", 1)[-1].strip()[:300]
                                    st.markdown(f"`QUERY` `{sql}`")
                                elif "CONCLUDE" in s:
                                    st.markdown("`✓ CONCLUDE`")
                                elif "rejected" in s.lower() or "No query" in s:
                                    st.markdown(f"`⚠ RETRY` {s[:200]}")
                                else:
                                    st.markdown(f"`◈` {s[:200]}")
                    think_log = msg.get("think_log", [])
                    if think_log:
                        with st.expander(f"💭 {len(think_log)} thinking block(s)", expanded=False):
                            for j, block in enumerate(think_log):
                                if len(think_log) > 1:
                                    st.markdown(f"**Step {j+1}**")
                                st.markdown(f'<div style="font-family:monospace;font-size:0.72rem;color:#8a9fc0;white-space:pre-wrap;line-height:1.5;">{block}</div>', unsafe_allow_html=True)

        if st.session_state.analyzing:
            st.markdown("""
            <div class="msg-typing">
                <div class="msg-label">COPILOT</div>
                ◈ Analyzing your query...
                <div class="typing-dots"><span></span><span></span><span></span></div>
            </div>""", unsafe_allow_html=True)
            with st.expander("◈ Agent is thinking...", expanded=True):
                st.markdown("`◈` Routing query...")
                st.markdown("`◈` Preparing SQL...")

    if st.session_state.pending:
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        bc1, bc2, _ = st.columns([2, 2, 3])
        with bc1:
            st.markdown('<div class="confirm-btn">', unsafe_allow_html=True)
            if st.button("✅ CONFIRM", use_container_width=True, key="confirm_btn"):
                with st.spinner("Executing action..."):
                    result = run_agent("confirm")
                _idx = len(st.session_state.messages)
                st.session_state.messages.append({"role":"agent","text":result["text"],"type":result["type"]})
                st.session_state.steps_log[_idx] = result.get("steps", [])
                st.session_state.pending     = False
                st.session_state.last_result = result
                if result.get("chart"):
                    st.session_state.chart_history.append({
                        "question": "confirm",
                        "chart":    result["chart"],
                        "text":     result.get("text", ""),
                    })
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)
        with bc2:
            st.markdown('<div class="cancel-btn">', unsafe_allow_html=True)
            if st.button("❌ CANCEL", use_container_width=True, key="cancel_btn"):
                result = run_agent("cancel")
                _idx = len(st.session_state.messages)
                st.session_state.messages.append({"role":"agent","text":result["text"],"type":result["type"]})
                st.session_state.steps_log[_idx] = result.get("steps", [])
                st.session_state.pending = False
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    # Auto-resize textarea via JS — grows with content, min 1 row
    st.markdown("""
    <script>
    (function(){
        function resize(ta){ ta.style.height='auto'; ta.style.height=(ta.scrollHeight)+'px'; }
        function attach(){
            document.querySelectorAll('textarea').forEach(function(ta){
                if(!ta.dataset.autosize){
                    ta.dataset.autosize='1';
                    ta.style.overflow='hidden';
                    ta.style.resize='none';
                    ta.addEventListener('input',function(){ resize(ta); });
                    resize(ta);
                }
            });
        }
        new MutationObserver(attach).observe(document.body,{childList:true,subtree:true});
        attach();
    })();
    </script>""", unsafe_allow_html=True)

    inp_col, btn_col, reset_col = st.columns([5, 1, 1])
    with inp_col:
        user_input = st.text_area(
            "input", label_visibility="collapsed",
            placeholder="Ask anything about your network or subscribers...",
            key=f"chat_input_{st.session_state.input_key}",
            height=44
        )
    with btn_col:
        send = st.button("SEND ▶", use_container_width=True)
    with reset_col:
        if st.button("FORGET", use_container_width=True):
            reset_memory()
            st.session_state.messages      = []
            st.session_state.steps_log     = {}
            st.session_state.pending       = False
            st.session_state.last_result   = None
            st.session_state.analyzing     = False
            st.session_state.input_key    += 1
            st.rerun()

    # text_area doesn't auto-submit on Enter — SEND button is the trigger
    # Strip trailing newlines that text_area adds when user presses Enter
    user_input = (user_input or "").rstrip("\n")

    # FIX 9: Two-phase render — show typing indicator first, then run agent
    if send and user_input.strip():
        if not st.session_state.analyzing:
            # Phase 1: record user message, set analyzing flag, rerun to show typing bubble
            st.session_state.messages.append({"role":"user","text":user_input.strip()})
            st.session_state.input_key += 1
            st.session_state.analyzing = True
            st.rerun()

    if st.session_state.analyzing:
        # Phase 2: actually run the agent (typing bubble is already visible from last render)
        last_user = next(
            (m["text"] for m in reversed(st.session_state.messages) if m["role"] == "user"),
            ""
        )
        with st.spinner("◈ Analyzing — this may take 1-3 minutes..."):
            _t0 = time.time()
            result = run_agent(last_user)
            _elapsed = round(time.time() - _t0, 1)
        _idx = len(st.session_state.messages)
        st.session_state.messages.append({"role":"agent","text":result["text"],"type":result["type"],"elapsed":_elapsed,"think_log":result.get("think_log",[])})
        st.session_state.steps_log[_idx] = result.get("steps", [])
        if result.get("type") == "proposal":
            st.session_state.pending = True
        st.session_state.last_result = result
        st.session_state.analyzing   = False
        if result.get("chart"):
            st.session_state.chart_history.append({
                "question": last_user,
                "chart":    result["chart"],
                "text":     result.get("text", ""),
            })
        st.rerun()

# ─────────────────────────────────────────────────────────────────────
# RIGHT — ANALYTICS
# ─────────────────────────────────────────────────────────────────────

with col_viz:
    st.markdown('<div class="section-label">◈ LIVE SNAPSHOT</div>', unsafe_allow_html=True)

    # Chart history — most recent first, older ones collapsed
    if st.session_state.chart_history:
        for i, entry in enumerate(reversed(st.session_state.chart_history)):
            idx   = len(st.session_state.chart_history) - 1 - i
            label = entry["question"][:60] + ("…" if len(entry["question"]) > 60 else "")
            expanded = (i == 0)  # only most recent is open by default
            with st.expander(label, expanded=expanded):
                render_structured_response({"chart": entry["chart"]}, key_prefix=f"hist_{idx}_")

    # Always-on charts
    @st.cache_data(ttl=30)
    def load_tech_dist():
        return query_sc("SELECT current_technology as tech, COUNT(*) as n FROM subscriber_technology GROUP BY tech ORDER BY n DESC")

    @st.cache_data(ttl=30)
    def load_net_health():
        return query_sc("""
            SELECT s.region, ROUND(AVG(k.availability_pct),1) as availability,
                   COUNT(DISTINCT a.alarm_id) as alarms
            FROM kpis_daily k JOIN cells c ON k.cell_id=c.cell_id
            JOIN sites s ON c.site_id=s.site_id
            LEFT JOIN network_alarms a ON a.cell_id=c.cell_id AND a.is_active=1
            WHERE k.date=(SELECT MAX(date) FROM kpis_daily)
            GROUP BY s.region ORDER BY alarms DESC LIMIT 8
        """)

    td = load_tech_dist()
    if td and "error" not in td[0]:
        st.plotly_chart(
            pie_chart([r["tech"] for r in td], [r["n"] for r in td], "Technology Distribution"),
            use_container_width=True, key="snap_tech_pie"
        )

    nd = load_net_health()
    if nd and "error" not in nd[0]:
        df_n = pd.DataFrame(nd)
        fig = go.Figure(go.Bar(
            x=df_n["region"], y=df_n["alarms"],
            marker=dict(color=df_n["alarms"],
                        colorscale=[[0,"#1e3460"],[0.5,"#4f46e5"],[1,"#a78bfa"]])
        ))
        fig.update_layout(title="Active Alarms by Region",
                          xaxis=dict(**AXIS_STYLE, tickangle=-30),
                          yaxis=dict(**AXIS_STYLE), **PLOT_BASE)
        st.plotly_chart(fig, use_container_width=True, key="snap_alarms_bar")

    st.markdown("""
    <div style="font-family:'Share Tech Mono',monospace;font-size:0.65rem;color:#444;text-align:center;margin-top:8px;">
        ▸ Use sidebar navigation for full analytics
    </div>""", unsafe_allow_html=True)

    # Tabs for deeper analytics
    tab_map, tab1, tab2, tab3 = st.tabs(["🗺 COVERAGE MAP", "NETWORK", "SUBSCRIBERS", "COMMERCIAL"])

    with tab_map:
        # ── Avatar world map — pixel coords on 4096×3072 image ──────────
        IMG_W, IMG_H = 4096, 3072

        # Region centre pixels (x=right, y=down) on the 4096×3072 image
        REGION_XY = {
            "Agna Qel'a":                  (2085,  312),
            "Sanikiluaq":                  (2507,  261),
            "Southern Water Tribe Capital":(1940, 2762),
            "Fire Nation Capital":         ( 803, 1467),
            "The Boiling Rock":            ( 819, 1091),
            "Island of the Sun Warriors":  ( 751, 1076),
            "Ember Island":                (1532, 1204),
            "Shuhon Island":               (1447, 1495),
            "Sunset City":                 ( 589, 1532),
            "Ba Sing Se":                  (3344,  918),
            "Omashu":                      (2574, 1851),
            "Gaoling":                     (2803, 1922),
            "Serpent's Pass":              (2860, 1089),
            "Si Wong Desert":              (2979, 1568),
            "HeiBai's Forest":             (2374, 1694),
            "FumaBu Town":                 (1692,  551),
            "Sandlocke City":              (3100, 1391),
            "Gaipan Village":              (2484, 1323),
            "Northern Air Temple":         (2517,  442),
            "Western Air Temple":          (1204,  738),
            "Eastern Air Temple":          (3626, 1285),
            "Southern Air Temple":         (1510, 2138),
            "Kyoshi Island":               (2439, 1992),
            "Crescent Island":             (1775, 1278),
        }

        NATION_COLORS = {
            "Earth Kingdom": "rgba(139,195,74,0.25)",
            "Fire Nation":   "rgba(239,83,80,0.25)",
            "Water Tribe":   "rgba(41,182,246,0.25)",
            "Air Nomads":    "rgba(255,183,77,0.25)",
        }
        NATION_BORDER = {
            "Earth Kingdom": "#8bc34a",
            "Fire Nation":   "#ef5350",
            "Water Tribe":   "#29b6f6",
            "Air Nomads":    "#ffb74d",
        }

        TECH_COLORS = {
            "2G": "#94a3b8",
            "3G": "#fb923c",
            "4G": "#60a5fa",
            "5G": "#a78bfa",
        }
        TECH_SIZES = {"2G": 6, "3G": 8, "4G": 10, "5G": 13}

        # Toggle
        show_towers = st.toggle("📡 Show Cell Towers", value=False)

        @st.cache_data(ttl=60)
        def load_region_stats():
            return query_sc("""
                SELECT s.region, s.zone,
                       COUNT(DISTINCT s.msisdn) as subscribers,
                       ROUND(AVG(k.availability_pct),1) as availability,
                       ROUND(AVG(k.dl_throughput_mbps),1) as avg_dl
                FROM subscribers s
                LEFT JOIN subscriber_technology st ON s.msisdn=st.msisdn
                LEFT JOIN kpis_daily k ON st.current_cell_id=k.cell_id
                  AND k.date=(SELECT MAX(date) FROM kpis_daily)
                WHERE s.is_active=1
                GROUP BY s.region, s.zone
            """)

        @st.cache_data(ttl=60)
        def load_coverage_sites():
            return query_sc("""
                SELECT s.site_name, s.region, s.zone,
                       s.latitude, s.longitude,
                       c.technology
                FROM sites s
                JOIN cells c ON s.site_id = c.site_id
                WHERE s.is_active=1 AND c.is_active=1
                ORDER BY c.technology
            """)

        import base64, os as _os
        img_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "static", "atla_map.jpg")
        with open(img_path, "rb") as _f:
            _img_b64 = base64.b64encode(_f.read()).decode()

        fig_map = go.Figure()

        # Background image
        fig_map.add_layout_image(dict(
            source=f"data:image/jpeg;base64,{_img_b64}",
            xref="x", yref="y",
            x=0, y=0,
            sizex=IMG_W, sizey=IMG_H,
            sizing="stretch",
            layer="below",
        ))

        # Region dots + nation-colored markers
        reg_data = load_region_stats()
        if reg_data and "error" not in reg_data[0]:
            import pandas as _pd
            df_reg = _pd.DataFrame(reg_data)
            for _, row in df_reg.iterrows():
                xy = REGION_XY.get(row["region"])
                if not xy:
                    continue
                nation = row.get("zone", "Earth Kingdom")
                color  = NATION_BORDER.get(nation, "#8bc34a")
                subs   = row.get("subscribers", 0)
                avail  = row.get("availability") or 0
                dl     = row.get("avg_dl") or 0
                size   = max(12, min(40, int(subs / 300)))
                fig_map.add_trace(go.Scatter(
                    x=[xy[0]], y=[IMG_H - xy[1]],
                    mode="markers+text",
                    name=nation,
                    legendgroup=nation,
                    showlegend=False,
                    marker=dict(size=size, color=NATION_COLORS.get(nation,"rgba(139,195,74,0.3)"),
                                line=dict(color=color, width=2)),
                    text=[row["region"]],
                    textposition="top center",
                    textfont=dict(size=9, color=color, family="Share Tech Mono,monospace"),
                    hovertemplate=(
                        f"<b>{row['region']}</b><br>"
                        f"Nation: {nation}<br>"
                        f"Subscribers: {subs:,}<br>"
                        f"Availability: {avail}%<br>"
                        f"Avg DL: {dl} Mbps<extra></extra>"
                    ),
                ))

        # Nation legend entries
        for nation, color in NATION_BORDER.items():
            fig_map.add_trace(go.Scatter(
                x=[None], y=[None], mode="markers",
                name=nation, legendgroup=nation,
                marker=dict(size=10, color=NATION_COLORS[nation],
                            line=dict(color=color, width=2)),
            ))

        # Cell tower dots (toggled)
        if show_towers:
            cov_data = load_coverage_sites()
            if cov_data and "error" not in cov_data[0]:
                import pandas as _pd2
                df_cov = _pd2.DataFrame(cov_data)
                for tech in ["2G", "3G", "4G", "5G"]:
                    df_t = df_cov[df_cov["technology"] == tech]
                    if df_t.empty:
                        continue
                    # Map sites to region pixel coords with small jitter
                    import numpy as _np
                    xs, ys, names = [], [], []
                    for _, sr in df_t.iterrows():
                        xy = REGION_XY.get(sr["region"])
                        if xy:
                            xs.append(xy[0] + _np.random.randint(-120, 120))
                            ys.append(IMG_H - xy[1] + _np.random.randint(-120, 120))
                            names.append(sr["site_name"])
                    fig_map.add_trace(go.Scatter(
                        x=xs, y=ys, mode="markers",
                        name=tech,
                        marker=dict(size=TECH_SIZES[tech], color=TECH_COLORS[tech], opacity=0.7,
                                    symbol="circle"),
                        hovertemplate="<b>%{customdata}</b><br>Tech: " + tech + "<extra></extra>",
                        customdata=names,
                    ))

        fig_map.update_layout(
            xaxis=dict(range=[0, IMG_W], showgrid=False, zeroline=False, visible=False),
            yaxis=dict(range=[0, IMG_H], showgrid=False, zeroline=False, visible=False,
                       scaleanchor="x", scaleratio=1),
            paper_bgcolor="#0a0f1e",
            plot_bgcolor="#0a0f1e",
            font=dict(color="#dce4f5", family="Share Tech Mono, monospace", size=10),
            margin=dict(l=0, r=0, t=0, b=0),
            height=620,
            dragmode="pan",
            legend=dict(
                orientation="v",
                yanchor="top", y=0.99,
                xanchor="left", x=0.01,
                bgcolor="rgba(10,15,30,0.75)",
                bordercolor="#253d63",
                borderwidth=1,
                font=dict(color="#dce4f5", size=10),
            ),
        )
        st.plotly_chart(fig_map, use_container_width=True, key="tab_coverage_map",
                        config={"scrollZoom": True})

        # Summary counts
        if show_towers:
            cov_data2 = load_coverage_sites()
            if cov_data2 and "error" not in cov_data2[0]:
                import pandas as _pd3
                df_s = _pd3.DataFrame(cov_data2)
                summary = df_s.groupby("technology").size().reset_index(name="sites")
                s_cols = st.columns(len(summary))
                for col, (_, row) in zip(s_cols, summary.iterrows()):
                    color = TECH_COLORS.get(row["technology"], "#818cf8")
                    col.markdown(f"""
                    <div style="background:#111e35;border:1px solid #253d63;
                                border-left:3px solid {color};padding:8px 12px;text-align:center;">
                        <div style="font-family:'Share Tech Mono',monospace;font-size:0.6rem;
                                    color:#6b87a8;letter-spacing:0.2em;">{row['technology']} SITES</div>
                        <div style="font-family:'Bebas Neue',sans-serif;font-size:1.6rem;
                                    color:#e2eaf8;">{row['sites']:,}</div>
                    </div>""", unsafe_allow_html=True)

    with tab1:
        @st.cache_data(ttl=30)
        def load_tab_network():
            return query_sc("""
                SELECT s.region,
                       ROUND(AVG(k.availability_pct),1) as availability,
                       ROUND(AVG(k.dropped_call_rate),3) as drop_rate,
                       ROUND(AVG(k.dl_throughput_mbps),1) as throughput,
                       COUNT(DISTINCT a.alarm_id) as alarms
                FROM kpis_daily k JOIN cells c ON k.cell_id=c.cell_id
                JOIN sites s ON c.site_id=s.site_id
                LEFT JOIN network_alarms a ON a.cell_id=c.cell_id AND a.is_active=1
                WHERE k.date=(SELECT MAX(date) FROM kpis_daily)
                GROUP BY s.region ORDER BY alarms DESC LIMIT 10
            """)

        nd2 = load_tab_network()
        if nd2 and "error" not in nd2[0]:
            df = pd.DataFrame(nd2)
            fig = go.Figure()
            fig.add_trace(go.Bar(name="Alarms", x=df["region"], y=df["alarms"], marker_color="#818cf8"))
            fig.add_trace(go.Scatter(name="Availability %", x=df["region"], y=df["availability"],
                                      mode="lines+markers", line=dict(color="#34d399",width=2),
                                      marker=dict(size=6), yaxis="y2"))
            fig.update_layout(title="Network Health by Region",
                               yaxis=dict(title="Alarms",**AXIS_STYLE),
                               yaxis2=dict(title="Avail %",overlaying="y",side="right",range=[95,100],**AXIS_STYLE),
                               xaxis=dict(**AXIS_STYLE),
                               legend=dict(orientation="h",y=-0.25), **PLOT_BASE)
            st.plotly_chart(fig, use_container_width=True, key="tab_net_dual")
            st.plotly_chart(bar_chart(df,"region","throughput","Avg DL Throughput (Mbps)"),
                            use_container_width=True, key="tab_net_dl")

    with tab2:
        @st.cache_data(ttl=30)
        def load_tab_tech():
            return query_sc("SELECT current_technology as tech, COUNT(*) as n FROM subscriber_technology GROUP BY tech ORDER BY n DESC")

        @st.cache_data(ttl=30)
        def load_tab_mob():
            return query_sc("SELECT mobility_class, COUNT(*) as n FROM mobility_profile WHERE month=(SELECT MAX(month) FROM mobility_profile) GROUP BY mobility_class")

        td2 = load_tab_tech()
        if td2 and "error" not in td2[0]:
            df = pd.DataFrame(td2)
            st.plotly_chart(pie_chart(df["tech"].tolist(), df["n"].tolist(), "Technology Distribution"),
                            use_container_width=True, key="tab_sub_tech")

        md = load_tab_mob()
        if md and "error" not in md[0]:
            df = pd.DataFrame(md)
            st.plotly_chart(bar_chart(df,"mobility_class","n","Mobility Profile"),
                            use_container_width=True, key="tab_sub_mob")

    with tab3:
        @st.cache_data(ttl=30)
        def load_tab_seg():
            return query_op("SELECT value_segment, COUNT(*) as n, ROUND(AVG(arpu),2) as avg_arpu FROM customer_value WHERE month=(SELECT MAX(month) FROM customer_value) GROUP BY value_segment ORDER BY avg_arpu DESC")

        @st.cache_data(ttl=30)
        def load_tab_plan():
            return query_op("SELECT p.plan_type, COUNT(*) as n FROM subscriptions s JOIN plans p ON s.plan_id=p.plan_id WHERE s.is_current=1 GROUP BY p.plan_type ORDER BY n DESC")

        seg_data = load_tab_seg()
        if seg_data and "error" not in seg_data[0]:
            df = pd.DataFrame(seg_data)
            COLORS = {"platinum":"#a78bfa","gold":"#fb923c","silver":"#94a3b8","bronze":"#60a5fa"}
            fig = go.Figure()
            for _, row in df.iterrows():
                fig.add_trace(go.Bar(
                    name=row["value_segment"].title(),
                    x=[row["value_segment"].title()], y=[row["n"]],
                    marker_color=COLORS.get(row["value_segment"],"#818cf8"),
                    text=[f"{row['avg_arpu']} Yuan"], textposition="outside",
                    textfont=dict(color="#e8e8e8",size=10)
                ))
            fig.update_layout(title="Customer Value Segments", showlegend=False, **PLOT_BASE)
            st.plotly_chart(fig, use_container_width=True, key="tab_com_seg")

        # ── Churn Risk ────────────────────────────────────────────────────
        @st.cache_data(ttl=30)
        def load_tab_churn():
            return query_op("""
                SELECT churn_label, COUNT(*) as n,
                       ROUND(AVG(churn_risk_score),3) as avg_score,
                       ROUND(AVG(arpu),2) as avg_arpu
                FROM customer_value
                WHERE month=(SELECT MAX(month) FROM customer_value)
                  AND churn_label IS NOT NULL
                GROUP BY churn_label ORDER BY avg_score DESC
            """)

        @st.cache_data(ttl=30)
        def load_churn_top():
            try:
                import sqlite3 as _sql
                conn = _sql.connect("NetworkAnalyzer_new.db")
                conn.row_factory = _sql.Row
                conn.execute("ATTACH DATABASE 'operator_new.db' AS op")
                rows = conn.execute("""
                    SELECT cv.msisdn, cu.full_name, s.nation, s.region,
                           ROUND(cv.churn_risk_score,3) as score,
                           cv.value_segment, ROUND(cv.arpu,2) as arpu
                    FROM op.customer_value cv
                    JOIN op.customers cu ON cv.msisdn=cu.msisdn
                    JOIN subscribers s ON cv.msisdn=s.msisdn
                    WHERE cv.month=(SELECT MAX(month) FROM op.customer_value)
                      AND cv.churn_label='high'
                    ORDER BY cv.churn_risk_score DESC LIMIT 15
                """).fetchall()
                conn.close()
                return [dict(r) for r in rows]
            except Exception as e:
                return [{"error": str(e)}]

        churn_data = load_tab_churn()
        if churn_data and "error" not in churn_data[0]:
            df_ch = pd.DataFrame(churn_data)
            CHURN_COLORS = {"high": "#ef4444", "medium": "#fb923c", "low": "#34d399"}
            col1, col2 = st.columns(2)
            with col1:
                fig_ch = go.Figure()
                for _, row in df_ch.iterrows():
                    fig_ch.add_trace(go.Bar(
                        name=row["churn_label"].title(),
                        x=[row["churn_label"].title()], y=[row["n"]],
                        marker_color=CHURN_COLORS.get(row["churn_label"], "#818cf8"),
                        text=[f"{row['n']:,}"], textposition="outside",
                        textfont=dict(color="#e8e8e8", size=10)
                    ))
                fig_ch.update_layout(title="Churn Risk Distribution", showlegend=False, **PLOT_BASE)
                st.plotly_chart(fig_ch, use_container_width=True, key="tab_churn_dist")
            with col2:
                fig_sc = go.Figure(go.Bar(
                    x=df_ch["churn_label"].str.title().tolist(),
                    y=df_ch["avg_score"].tolist(),
                    marker_color=[CHURN_COLORS.get(l, "#818cf8") for l in df_ch["churn_label"]],
                    text=[f"{s:.3f}" for s in df_ch["avg_score"]],
                    textposition="outside",
                    textfont=dict(color="#e8e8e8", size=10)
                ))
                fig_sc.update_layout(title="Avg Churn Score by Label", **PLOT_BASE)
                st.plotly_chart(fig_sc, use_container_width=True, key="tab_churn_score")

        top_churn = load_churn_top()
        if top_churn and "error" not in top_churn[0]:
            st.markdown("#### Top 15 High-Risk Subscribers")
            st.dataframe(pd.DataFrame(top_churn), use_container_width=True, hide_index=True)

        # FIX 8: renamed pd_data → plan_data to avoid shadowing pandas alias
        plan_data = load_tab_plan()
        if plan_data and "error" not in plan_data[0]:
            df = pd.DataFrame(plan_data)
            st.plotly_chart(pie_chart(df["plan_type"].tolist(), df["n"].tolist(), "Active Plan Types"),
                            use_container_width=True, key="tab_com_plan")

# ── Footer ────────────────────────────────────────────────────────────

st.markdown("""
<div style="margin-top:24px;border-top:1px solid #1e2f52;padding-top:10px;display:flex;justify-content:space-between;align-items:center;">
    <div style="font-family:'Share Tech Mono',monospace;font-size:0.62rem;color:#5a7090;">NetworkAnalyzer COPILOT // HUAWEI UC PLATFORM // INTERNSHIP BUILD</div>
    <div style="font-family:'Share Tech Mono',monospace;font-size:0.62rem;color:#818cf8;">QWEN2.5:7B · LOCAL INFERENCE · RAG ENABLED</div>
</div>
""", unsafe_allow_html=True)