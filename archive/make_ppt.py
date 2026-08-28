from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt
import copy

# ── Theme colours ─────────────────────────────────────────────────────
BG       = RGBColor(0x0d, 0x1b, 0x2a)   # dark navy
ACCENT   = RGBColor(0x63, 0x66, 0xf1)   # indigo
ACCENT2  = RGBColor(0x38, 0xbd, 0xf8)   # sky blue
GREEN    = RGBColor(0x34, 0xd3, 0x99)   # emerald
ORANGE   = RGBColor(0xfb, 0x92, 0x3c)   # amber
WHITE    = RGBColor(0xff, 0xff, 0xff)
MUTED    = RGBColor(0x94, 0xa3, 0xb8)   # slate-400
DARK_ROW = RGBColor(0x1e, 0x2f, 0x52)
LIGHT_ROW= RGBColor(0x13, 0x1f, 0x35)

W = Inches(13.33)
H = Inches(7.5)

prs = Presentation()
prs.slide_width  = W
prs.slide_height = H

BLANK = prs.slide_layouts[6]  # completely blank


def add_slide():
    return prs.slides.add_slide(BLANK)


def bg(slide, color=BG):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = color


def box(slide, left, top, width, height, fill_color=None, line_color=None, line_width=Pt(0)):
    from pptx.util import Pt
    shape = slide.shapes.add_shape(1, left, top, width, height)  # MSO_SHAPE_TYPE.RECTANGLE
    shape.line.width = line_width
    if fill_color:
        shape.fill.solid()
        shape.fill.fore_color.rgb = fill_color
    else:
        shape.fill.background()
    if line_color:
        shape.line.color.rgb = line_color
        shape.line.width = line_width if line_width else Pt(1)
    else:
        shape.line.fill.background()
    return shape


def txt(slide, text, left, top, width, height,
        size=Pt(18), bold=False, color=WHITE, align=PP_ALIGN.LEFT, wrap=True):
    txb = slide.shapes.add_textbox(left, top, width, height)
    txb.word_wrap = wrap
    tf  = txb.text_frame
    tf.word_wrap = wrap
    p   = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.size  = size
    run.font.bold  = bold
    run.font.color.rgb = color
    return txb


def accent_bar(slide, color=ACCENT):
    box(slide, 0, Inches(7.1), W, Inches(0.4), fill_color=color)


def slide_number(slide, n):
    txt(slide, str(n), Inches(12.8), Inches(7.1), Inches(0.5), Inches(0.35),
        size=Pt(10), color=MUTED, align=PP_ALIGN.RIGHT)


def header(slide, title, subtitle=None):
    box(slide, 0, 0, W, Inches(1.4), fill_color=RGBColor(0x0a, 0x14, 0x22))
    txt(slide, title, Inches(0.4), Inches(0.15), Inches(12), Inches(0.75),
        size=Pt(32), bold=True, color=WHITE)
    if subtitle:
        txt(slide, subtitle, Inches(0.4), Inches(0.85), Inches(12), Inches(0.45),
            size=Pt(14), color=ACCENT2)


def bullet_block(slide, items, left, top, width, icon_color=ACCENT2, size=Pt(15)):
    y = top
    for item in items:
        icon_shape = box(slide, left, y + Inches(0.07), Inches(0.08), Inches(0.08),
                         fill_color=icon_color)
        txt(slide, item, left + Inches(0.18), y, width - Inches(0.2), Inches(0.38),
            size=size, color=WHITE)
        y += Inches(0.42)
    return y


def pill(slide, label, left, top, color=ACCENT):
    w = Inches(2.0)
    h = Inches(0.38)
    b = box(slide, left, top, w, h, fill_color=color)
    txt(slide, label, left, top, w, h,
        size=Pt(12), bold=True, color=WHITE, align=PP_ALIGN.CENTER)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 1 — TITLE
# ══════════════════════════════════════════════════════════════════════
s1 = add_slide(); bg(s1)

# big gradient-ish rectangle top
box(s1, 0, 0, W, Inches(2.2), fill_color=RGBColor(0x0a, 0x14, 0x22))
box(s1, 0, Inches(2.2), W, Inches(0.06), fill_color=ACCENT)

txt(s1, "NetworkAnalyzer LLM", Inches(0.6), Inches(0.3), Inches(11), Inches(1.2),
    size=Pt(54), bold=True, color=WHITE, align=PP_ALIGN.CENTER)
txt(s1, "AI-Powered Network Copilot for a Tunisian Mobile Operator",
    Inches(0.6), Inches(1.5), Inches(11), Inches(0.6),
    size=Pt(20), color=ACCENT2, align=PP_ALIGN.CENTER)

# 3 stat pills
for i, (val, lbl, col) in enumerate([
    ("2 Databases", "NetworkAnalyzer + Operator", ACCENT),
    ("15+ Tables", "Network & Commercial", GREEN),
    ("Cloud + Local", "Bedrock & Ollama", ACCENT2),
]):
    x = Inches(1.8 + i * 3.4)
    box(s1, x, Inches(2.7), Inches(3.0), Inches(1.5), fill_color=DARK_ROW)
    box(s1, x, Inches(2.7), Inches(3.0), Inches(0.06), fill_color=col)
    txt(s1, val,  x, Inches(2.85), Inches(3.0), Inches(0.6),
        size=Pt(22), bold=True, color=WHITE, align=PP_ALIGN.CENTER)
    txt(s1, lbl,  x, Inches(3.45), Inches(3.0), Inches(0.4),
        size=Pt(12), color=MUTED, align=PP_ALIGN.CENTER)

txt(s1, "Internship Project — April 2026",
    Inches(0.6), Inches(4.5), Inches(11), Inches(0.4),
    size=Pt(13), color=MUTED, align=PP_ALIGN.CENTER)

accent_bar(s1)

# ══════════════════════════════════════════════════════════════════════
# SLIDE 2 — THE SITUATION
# ══════════════════════════════════════════════════════════════════════
s2 = add_slide(); bg(s2)
header(s2, "The Situation", "What problem are we solving?")
accent_bar(s2); slide_number(s2, 2)

# Left column — problem
box(s2, Inches(0.35), Inches(1.6), Inches(6.0), Inches(5.2), fill_color=DARK_ROW)
box(s2, Inches(0.35), Inches(1.6), Inches(6.0), Inches(0.06), fill_color=ORANGE)
txt(s2, "The Challenge", Inches(0.5), Inches(1.65), Inches(5.8), Inches(0.45),
    size=Pt(16), bold=True, color=ORANGE)
bullet_block(s2, [
    "Network engineers spend hours writing SQL queries",
    "Commercial team can't self-serve data insights",
    "No unified view across network + commercial data",
    "Slow response to churn signals and network degradation",
    "Manual reporting creates bottlenecks at every level",
], Inches(0.55), Inches(2.2), Inches(5.7), icon_color=ORANGE, size=Pt(13))

# Right column — opportunity
box(s2, Inches(6.85), Inches(1.6), Inches(6.1), Inches(5.2), fill_color=DARK_ROW)
box(s2, Inches(6.85), Inches(1.6), Inches(6.1), Inches(0.06), fill_color=GREEN)
txt(s2, "The Opportunity", Inches(7.0), Inches(1.65), Inches(5.8), Inches(0.45),
    size=Pt(16), bold=True, color=GREEN)
bullet_block(s2, [
    "LLMs can translate natural language → SQL automatically",
    "A single copilot interface for both network & commercial teams",
    "Real-time query + analysis without engineering involvement",
    "Proactive churn detection and network anomaly surfacing",
    "Democratise data access across the organisation",
], Inches(7.0), Inches(2.2), Inches(5.8), icon_color=GREEN, size=Pt(13))

# ══════════════════════════════════════════════════════════════════════
# SLIDE 3 — WHAT WE BUILT
# ══════════════════════════════════════════════════════════════════════
s3 = add_slide(); bg(s3)
header(s3, "What We Built", "Architecture overview")
accent_bar(s3); slide_number(s3, 3)

# 5 component cards in a row
components = [
    ("Chat UI",        "Dark-themed web app\nWebSocket streaming\nReasoning steps visible", ACCENT),
    ("FastAPI Server", "WebSocket endpoint\nMulti-model routing\nStreaming queue", ACCENT2),
    ("Agent Chain",    "Multi-step SQL loop\nSelf-check + scoring\nCONCLUDE guard", GREEN),
    ("Two Databases",  "NetworkAnalyzer (network)\nOperator (commercial)\nSQLite + ATTACH", ORANGE),
    ("LLM Backend",    "Local: Ollama/qwen3\nCloud: AWS Bedrock\nDynamic routing", RGBColor(0xa7,0x8b,0xfa)),
]
for i, (title, body, color) in enumerate(components):
    x = Inches(0.3 + i * 2.6)
    box(s3, x, Inches(1.7), Inches(2.45), Inches(4.8), fill_color=DARK_ROW)
    box(s3, x, Inches(1.7), Inches(2.45), Inches(0.07), fill_color=color)
    txt(s3, title, x, Inches(1.8), Inches(2.45), Inches(0.5),
        size=Pt(14), bold=True, color=color, align=PP_ALIGN.CENTER)
    # connector arrow (except last)
    if i < 4:
        txt(s3, "→", x + Inches(2.45), Inches(3.8), Inches(0.3), Inches(0.4),
            size=Pt(20), color=MUTED, align=PP_ALIGN.CENTER)
    y = Inches(2.45)
    for line in body.split("\n"):
        txt(s3, "• " + line, x + Inches(0.12), y, Inches(2.2), Inches(0.38),
            size=Pt(11), color=MUTED)
        y += Inches(0.38)

# ══════════════════════════════════════════════════════════════════════
# SLIDE 4 — HOW FAR WE GOT
# ══════════════════════════════════════════════════════════════════════
s4 = add_slide(); bg(s4)
header(s4, "How Far We Got", "Key milestones achieved")
accent_bar(s4); slide_number(s4, 4)

milestones = [
    (GREEN,  "DONE", "Natural language → SQL with multi-step reasoning chain"),
    (GREEN,  "DONE", "Two-database architecture (network + commercial) with ATTACH"),
    (GREEN,  "DONE", "Composite churn/value scoring with LLM-driven merge"),
    (GREEN,  "DONE", "Streaming UI with reasoning steps, charts (Plotly), and stop button"),
    (GREEN,  "DONE", "RAG context for telecom domain knowledge injection"),
    (GREEN,  "DONE", "Auto-error recovery: ColCheck → fast path fallback → chain retry"),
    (GREEN,  "DONE", "Cloud (AWS Bedrock 32B) vs Local (Ollama qwen3 8B) split"),
    (ACCENT, "DONE", "Treemap hierarchies, dual-axis charts, multi-bar visualisations"),
    (ORANGE, "DEMO",  "Live comparative demo: local 8B vs cloud 32B on same prompts"),
]

for i, (color, badge, text) in enumerate(milestones):
    y = Inches(1.6 + i * 0.54)
    box(s4, Inches(0.35), y, W - Inches(0.7), Inches(0.46),
        fill_color=LIGHT_ROW if i % 2 == 0 else DARK_ROW)
    box(s4, Inches(0.35), y, Inches(0.06), Inches(0.46), fill_color=color)
    txt(s4, badge, Inches(0.55), y + Inches(0.07), Inches(0.8), Inches(0.32),
        size=Pt(9), bold=True, color=color)
    txt(s4, text, Inches(1.4), y + Inches(0.07), Inches(11.2), Inches(0.32),
        size=Pt(13), color=WHITE)

# ══════════════════════════════════════════════════════════════════════
# SLIDE 5 — LOCAL LLM
# ══════════════════════════════════════════════════════════════════════
s5 = add_slide(); bg(s5)
header(s5, "Solution A — Local LLM", "Ollama + qwen3:8b running on RTX 3050")
accent_bar(s5); slide_number(s5, 5)

box(s5, Inches(0.35), Inches(1.6), Inches(6.1), Inches(5.2), fill_color=DARK_ROW)
box(s5, Inches(0.35), Inches(1.6), Inches(6.1), Inches(0.06), fill_color=GREEN)
txt(s5, "Advantages", Inches(0.5), Inches(1.7), Inches(5.8), Inches(0.42),
    size=Pt(15), bold=True, color=GREEN)
bullet_block(s5, [
    "Zero cost per query — runs entirely on-premise",
    "No data leaves the company network",
    "No internet dependency — works offline",
    "Iterate freely — no API cost or rate limits",
    "Full control over model behaviour",
], Inches(0.55), Inches(2.2), Inches(5.7), icon_color=GREEN, size=Pt(13))

box(s5, Inches(6.85), Inches(1.6), Inches(6.1), Inches(5.2), fill_color=DARK_ROW)
box(s5, Inches(6.85), Inches(1.6), Inches(6.1), Inches(0.06), fill_color=ORANGE)
txt(s5, "Limitations", Inches(7.0), Inches(1.7), Inches(5.8), Inches(0.42),
    size=Pt(15), bold=True, color=ORANGE)
bullet_block(s5, [
    "8B parameters — limited reasoning depth",
    "Struggles with multi-condition SQL joins",
    "Thinking mode is slow (~60–120s per query)",
    "Hallucinations more frequent on complex prompts",
    "RTX 3050 VRAM (4GB) limits context window",
    "Needs hardwareupgrade for production use",
], Inches(7.0), Inches(2.2), Inches(5.8), icon_color=ORANGE, size=Pt(13))

# spec badge
box(s5, Inches(0.35), Inches(6.45), Inches(3.2), Inches(0.38), fill_color=DARK_ROW)
txt(s5, "⚙  qwen3:8b · 25 GPU layers · 8192 ctx · RTX 3050",
    Inches(0.5), Inches(6.47), Inches(3.0), Inches(0.34),
    size=Pt(10), color=MUTED)

# ══════════════════════════════════════════════════════════════════════
# SLIDE 6 — CLOUD LLM
# ══════════════════════════════════════════════════════════════════════
s6 = add_slide(); bg(s6)
header(s6, "Solution B — Cloud LLM", "AWS Bedrock + Qwen3-32B")
accent_bar(s6); slide_number(s6, 6)

box(s6, Inches(0.35), Inches(1.6), Inches(6.1), Inches(5.2), fill_color=DARK_ROW)
box(s6, Inches(0.35), Inches(1.6), Inches(6.1), Inches(0.06), fill_color=GREEN)
txt(s6, "Advantages", Inches(0.5), Inches(1.7), Inches(5.8), Inches(0.42),
    size=Pt(15), bold=True, color=GREEN)
bullet_block(s6, [
    "32B parameters — significantly stronger reasoning",
    "Handles complex multi-table, multi-condition queries",
    "Composite scoring across 4 datasets in one session",
    "Self-check step catches incomplete SQL automatically",
    "Consistent, low-latency responses (~15–25s)",
    "Scales to any query complexity without hardware limits",
], Inches(0.55), Inches(2.2), Inches(5.7), icon_color=GREEN, size=Pt(13))

box(s6, Inches(6.85), Inches(1.6), Inches(6.1), Inches(5.2), fill_color=DARK_ROW)
box(s6, Inches(6.85), Inches(1.6), Inches(6.1), Inches(0.06), fill_color=ORANGE)
txt(s6, "Considerations", Inches(7.0), Inches(1.7), Inches(5.8), Inches(0.42),
    size=Pt(15), bold=True, color=ORANGE)
bullet_block(s6, [
    "Per-query cost (manageable at enterprise scale)",
    "Data sent to AWS — requires compliance review",
    "Requires internet connectivity",
    "16k token input limit (mitigated by context trimming)",
    "AWS account + Bedrock model access required",
], Inches(7.0), Inches(2.2), Inches(5.8), icon_color=ORANGE, size=Pt(13))

box(s6, Inches(0.35), Inches(6.45), Inches(4.0), Inches(0.38), fill_color=DARK_ROW)
txt(s6, "⚙  Qwen3-32B · AWS Bedrock · converse_stream API · 16k ctx",
    Inches(0.5), Inches(6.47), Inches(3.8), Inches(0.34),
    size=Pt(10), color=MUTED)

# ══════════════════════════════════════════════════════════════════════
# SLIDE 7 — COMPARATIVE
# ══════════════════════════════════════════════════════════════════════
s7 = add_slide(); bg(s7)
header(s7, "Local vs Cloud — Head to Head", "Edit this slide with your demo conclusions")
accent_bar(s7); slide_number(s7, 7)

# Table layout — fits 8 rows + header inside the slide
col_w = [Inches(3.2), Inches(4.8), Inches(4.8)]
col_x = [Inches(0.35), Inches(3.55), Inches(8.35)]
row_h = Inches(0.46)
tbl_top = Inches(1.58)

# Header row
for c, w, x in zip(["Dimension", "Local  (qwen3:8b)", "Cloud  (Qwen3-32B)"], col_w, col_x):
    box(s7, x, tbl_top, w, row_h, fill_color=ACCENT)
    txt(s7, c, x + Inches(0.1), tbl_top + Inches(0.07), w - Inches(0.1), row_h,
        size=Pt(13), bold=True, color=WHITE, align=PP_ALIGN.CENTER)

rows = [
    ("Response time",        "~60–120s (thinking on)",  "~15–25s"),
    ("SQL accuracy",         "Moderate — misses joins", "High — handles complexity"),
    ("Multi-step reasoning", "Up to 5 steps",           "Up to 8 steps + self-check"),
    ("Composite scoring",    "Not supported",           "4-dataset merge"),
    ("Data privacy",         "100% on-premise",         "Sent to AWS"),
    ("Cost",                 "Hardware only",           "Per-query (pay-as-you-go)"),
    ("Production readiness", "Needs bigger GPU",        "Ready now"),
    ("Conclusion",           "← Edit here",             "← Edit here"),
]

for i, (dim, local, cloud) in enumerate(rows):
    y = tbl_top + row_h * (i + 1)
    is_last = i == len(rows) - 1
    rc = (RGBColor(0x12, 0x28, 0x0e) if is_last
          else (DARK_ROW if i % 2 == 0 else LIGHT_ROW))

    for x, w in zip(col_x, col_w):
        box(s7, x, y, w, row_h, fill_color=rc)

    dim_color  = ACCENT2  if is_last else MUTED
    cell_color = GREEN    if is_last else WHITE
    txt(s7, dim,   col_x[0] + Inches(0.1), y + Inches(0.08), col_w[0] - Inches(0.1), row_h,
        size=Pt(12), bold=is_last, color=dim_color)
    txt(s7, local, col_x[1] + Inches(0.1), y + Inches(0.08), col_w[1] - Inches(0.1), row_h,
        size=Pt(12), bold=is_last, color=cell_color, align=PP_ALIGN.CENTER)
    txt(s7, cloud, col_x[2] + Inches(0.1), y + Inches(0.08), col_w[2] - Inches(0.1), row_h,
        size=Pt(12), bold=is_last, color=cell_color, align=PP_ALIGN.CENTER)

# ══════════════════════════════════════════════════════════════════════
# Save
# ══════════════════════════════════════════════════════════════════════
out = r"d:\Dev\NetworkAnalyzerLLM\NetworkAnalyzerLLM_Presentation.pptx"
prs.save(out)
print(f"Saved: {out}")
