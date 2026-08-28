# RENDER: manim -ql NetworkAnalyzer_presentation.py S1_Title  (quick preview)
# RENDER ALL: manim -qh NetworkAnalyzer_presentation.py --all  (high quality)
# COMBINE: ffmpeg -f concat -safe 0 -i filelist.txt -c copy NetworkAnalyzer_full.mp4

from manim import *
import numpy as np

# ─────────────────────────────────────────────
#  COLOR PALETTE  –  Light mode (overall)
# ─────────────────────────────────────────────
BG      = "#faf6f7"   # light warm near-white
PANEL   = "#f7e9ea"   # faint red-tint panel
SURFACE = "#ffffff"
BORDER  = "#e7cdcf"
PRP     = "#c7000b"   # Huawei red (primary accent)
PRP_L   = "#e0685c"
PRP_LL  = "#edb4ad"
BLU     = "#0ea5e9"
BLU_L   = "#38bdf8"
NAVY    = "#1e1b4b"
MUTED   = "#64748b"
SLATE   = "#6b7280"   # neutral grey for supporting nodes
GREEN   = "#10b981"
ORANGE  = "#f59e0b"
RED     = "#ef4444"
WHITE   = "#ffffff"

# ─────────────────────────────────────────────
#  COLOR PALETTE  –  Dashboard dark theme
# ─────────────────────────────────────────────
D_BG   = "#080f1e"
D_DARK = "#0d1729"
D_MID  = "#131f35"
D_SURF = "#192540"
D_BDR  = "#253d63"
D_TXT  = "#e2eaf8"
D_MUT  = "#6b87a8"
D_PRP  = "#818cf8"
D_BLU  = "#38bdf8"

# ─────────────────────────────────────────────
#  GLOBAL CONFIG
# ─────────────────────────────────────────────
config.pixel_height  = 1080
config.pixel_width   = 1920
config.frame_rate    = 30
config.background_color = BG


# ═════════════════════════════════════════════
#  HELPER FUNCTIONS
# ═════════════════════════════════════════════

def make_card(width, height, label_top, value, unit="",
              bg=SURFACE, border=BORDER, accent=PRP,
              label_color=MUTED, value_color=NAVY, unit_color=MUTED,
              font_size_val=36, font_size_lbl=14):
    """Stat card with top accent bar."""
    card = VGroup()
    bg_rect = RoundedRectangle(width=width, height=height,
                                corner_radius=0.15, fill_color=bg,
                                fill_opacity=1, stroke_color=border,
                                stroke_width=1.5)
    accent_bar = Rectangle(width=width, height=0.07,
                           fill_color=accent, fill_opacity=1,
                           stroke_width=0)
    accent_bar.align_to(bg_rect, UP).align_to(bg_rect, LEFT)
    accent_bar.shift(RIGHT * 0)

    val_txt = Text(str(value), font_size=font_size_val,
                   color=value_color, weight=BOLD)
    lbl_txt = Text(label_top, font_size=font_size_lbl,
                   color=label_color)
    if unit:
        unit_txt = Text(unit, font_size=12, color=unit_color)
        unit_txt.next_to(val_txt, RIGHT, buff=0.1)
        val_row = VGroup(val_txt, unit_txt)
    else:
        val_row = val_txt

    val_row.move_to(bg_rect.get_center()).shift(UP * 0.1)
    lbl_txt.next_to(val_row, DOWN, buff=0.12)

    card.add(bg_rect, accent_bar, val_row, lbl_txt)
    return card


def make_comp_box(title, subtitle, width=2.4, height=1.2,
                  color=PRP, bg=SURFACE, border=BORDER):
    """Architecture component box."""
    grp = VGroup()
    rect = RoundedRectangle(width=width, height=height,
                             corner_radius=0.2,
                             fill_color=bg, fill_opacity=1,
                             stroke_color=color, stroke_width=2.5)
    t1 = Text(title, font_size=16, color=color, weight=BOLD)
    t2 = Text(subtitle, font_size=11, color=MUTED)
    t2.next_to(t1, DOWN, buff=0.1)
    content = VGroup(t1, t2).move_to(rect.get_center())
    grp.add(rect, content)
    return grp


def make_code_block(code_lines, width=6.5, font_size=13,
                    bg="#1e2235", border_color=D_BDR):
    """Dark code block with monospace text."""
    grp = VGroup()
    lines = code_lines if isinstance(code_lines, list) else code_lines.split("\n")
    texts = VGroup()
    for line in lines:
        t = Text(line, font="Courier New", font_size=font_size,
                 color="#a5b4fc")
        texts.add(t)
    texts.arrange(DOWN, aligned_edge=LEFT, buff=0.08)

    pad = 0.3
    h = texts.height + 2 * pad
    w = max(width, texts.width + 2 * pad)
    bg_rect = RoundedRectangle(width=w, height=h,
                                corner_radius=0.15,
                                fill_color=bg, fill_opacity=1,
                                stroke_color=border_color,
                                stroke_width=1.5)
    texts.move_to(bg_rect.get_center()).align_to(
        bg_rect, LEFT).shift(RIGHT * pad)
    grp.add(bg_rect, texts)
    return grp


def make_arrow_labeled(start_obj, end_obj, label="",
                       color=PRP_LL, font_size=12):
    """Arrow between two mobjects with optional label."""
    arr = Arrow(start_obj.get_right(), end_obj.get_left(),
                buff=0.1, color=color, stroke_width=2,
                max_tip_length_to_length_ratio=0.15)
    grp = VGroup(arr)
    if label:
        lbl = Text(label, font_size=font_size, color=MUTED)
        lbl.next_to(arr, UP, buff=0.07)
        grp.add(lbl)
    return grp


def make_step_row(number, icon, title, detail,
                  state="pending", width=11):
    """One agent reasoning step row."""
    color_map = {"active": PRP, "done": GREEN, "pending": MUTED}
    c = color_map.get(state, MUTED)

    circle = Circle(radius=0.28, fill_color=c if state != "pending" else SURFACE,
                    fill_opacity=1, stroke_color=c, stroke_width=2.5)
    num = Text(str(number), font_size=16, color=WHITE if state != "pending" else c,
               weight=BOLD)
    num.move_to(circle.get_center())

    icon_t = Text(icon, font_size=18)
    title_t = Text(title, font_size=16, color=c, weight=BOLD)
    detail_t = Text(detail, font_size=13, color=MUTED)

    left = VGroup(circle, num)
    right = VGroup(icon_t, title_t, detail_t)
    right.arrange(RIGHT, buff=0.2)

    row = VGroup(left, right)
    row.arrange(RIGHT, buff=0.3, aligned_edge=UP)
    return row


# ═════════════════════════════════════════════
#  S1 – TITLE SCENE  (~8s)
# ═════════════════════════════════════════════
class S1_Title(Scene):
    def construct(self):
        # ── Corner bracket accents ──
        def corner_bracket(pos, flip_x=False, flip_y=False):
            h, w, t = 0.6, 0.6, 0.03
            lines = VGroup(
                Line(ORIGIN, RIGHT * w, color=PRP, stroke_width=3),
                Line(ORIGIN, UP * h, color=PRP, stroke_width=3),
            )
            if flip_x:
                lines.flip(UP)
            if flip_y:
                lines.flip(RIGHT)
            lines.move_to(pos)
            return lines

        tl = corner_bracket([-6.5, 3.5, 0])
        tr = corner_bracket([6.5, 3.5, 0], flip_x=True)
        bl = corner_bracket([-6.5, -3.5, 0], flip_y=True)
        br = corner_bracket([6.5, -3.5, 0], flip_x=True, flip_y=True)
        brackets = VGroup(tl, tr, bl, br)

        # ── Logo ──
        smart_t = Text("SMART", font_size=96, color=NAVY, weight=BOLD)
        care_t  = Text("CARE", font_size=96, color=PRP, weight=BOLD)
        logo = VGroup(smart_t, care_t)
        logo.arrange(RIGHT, buff=0.15)
        logo.move_to(UP * 0.9)

        # ── Gradient accent line ──
        accent_line = Rectangle(width=logo.width, height=0.07,
                                 fill_opacity=1, stroke_width=0)
        accent_line.set_color_by_gradient(PRP, BLU)
        accent_line.next_to(logo, DOWN, buff=0.18)

        # ── Subtitle ──
        subtitle = Text("AI Copilot for Tunisian Telecom Operations",
                        font_size=28, color=MUTED)
        subtitle.next_to(accent_line, DOWN, buff=0.35)

        # ── Tech stack ──
        tech = Text("Qwen2.5:7B  ·  Ollama  ·  Streamlit  ·  SQLite  ·  FAISS RAG",
                    font_size=18, color=PRP_LL)
        tech.next_to(subtitle, DOWN, buff=0.22)

        # ── Stat cards ──
        card_data = [
            ("50,000", "SUBSCRIBERS", PRP),
            ("2",      "DATABASES",   BLU),
            ("24",     "REGIONS",     GREEN),
            ("12",     "RAG DOCS",    ORANGE),
        ]
        cards = VGroup()
        for val, lbl, acc in card_data:
            c = make_card(3.0, 1.5, lbl, val, accent=acc,
                          font_size_val=40, font_size_lbl=15)
            cards.add(c)
        cards.arrange(RIGHT, buff=0.4)
        cards.next_to(tech, DOWN, buff=0.55)
        cards.move_to(cards.get_center() * RIGHT + DOWN * 2.5)

        # ── Animations ──
        self.play(FadeIn(brackets, lag_ratio=0.3, run_time=0.8))
        self.play(Write(smart_t, run_time=0.9),
                  Write(care_t,  run_time=0.9))
        self.play(GrowFromEdge(accent_line, LEFT, run_time=0.7))
        self.play(FadeIn(subtitle, shift=UP * 0.2, run_time=0.6))
        self.play(FadeIn(tech, run_time=0.5))
        self.play(
            LaggedStart(*[FadeIn(c, shift=UP * 0.3) for c in cards],
                        lag_ratio=0.18, run_time=1.6)
        )
        self.wait(1.5)


# ═════════════════════════════════════════════
#  S2 – ARCHITECTURE SCENE  (~18s)
# ═════════════════════════════════════════════
class S2_Architecture(Scene):
    def construct(self):
        title = Text("SYSTEM ARCHITECTURE", font_size=38,
                     color=NAVY, weight=BOLD)
        title.to_edge(UP, buff=0.4)
        underline = Line(title.get_left(), title.get_right(),
                         color=PRP, stroke_width=2.5)
        underline.next_to(title, DOWN, buff=0.12)
        self.play(Write(title), GrowFromEdge(underline, LEFT), run_time=0.9)

        # ── Interfaces (left) ──
        chat = make_comp_box("Copilot Chat", "ask in plain language",   color=SLATE, width=2.6, height=0.95)
        ops  = make_comp_box("Ops Portal",   "approve · send actions",   color=SLATE, width=2.6, height=0.95)
        dash = make_comp_box("Dashboard",    "analytics · query builder", color=SLATE, width=2.6, height=0.95)
        interfaces = VGroup(chat, ops, dash).arrange(DOWN, buff=0.35).to_edge(LEFT, buff=0.5)
        if_label = Text("INTERFACES", font_size=13, color=MUTED, weight=BOLD).next_to(interfaces, UP, buff=0.2)

        # ── Agent panel (the hero) ──
        panel = RoundedRectangle(width=7.4, height=3.0, corner_radius=0.25,
                                 fill_color=PANEL, fill_opacity=1, stroke_color=PRP, stroke_width=3)
        panel.move_to(RIGHT * 0.9 + DOWN * 0.1)
        agent_lbl = Text("AGENT", font_size=18, color=PRP, weight=BOLD)
        agent_sub = Text("agentic RAG loop", font_size=11, color=MUTED).next_to(agent_lbl, RIGHT, buff=0.2)
        agent_hdr = VGroup(agent_lbl, agent_sub).next_to(panel.get_top(), DOWN, buff=0.14)

        def stage(t, s, col=PRP):
            r = RoundedRectangle(width=1.55, height=1.4, corner_radius=0.12,
                                 fill_color=SURFACE, fill_opacity=1, stroke_color=col, stroke_width=2)
            a = Text(t, font_size=14, color=col, weight=BOLD)
            b = Text(s, font_size=11, color=MUTED)
            b.next_to(a, DOWN, buff=0.1)
            VGroup(a, b).move_to(r.get_center())
            return VGroup(r, a, b)

        st_intent = stage("Intent",    "classify",                 SLATE)
        st_rag    = stage("RAG",       "BM25 + FAISS\nschema graph", GREEN)
        st_reason = stage("Reasoning", "query · observe\nself-correct", PRP)
        st_ground = stage("Ground",    "verify vs\nreal rows",     ORANGE)
        stages = VGroup(st_intent, st_rag, st_reason, st_ground).arrange(RIGHT, buff=0.22)
        stages.move_to(panel.get_center()).shift(DOWN * 0.15)

        inner = VGroup(*[
            Arrow(a.get_right(), b.get_left(), buff=0.05, color=PRP_L, stroke_width=2,
                  max_tip_length_to_length_ratio=0.3)
            for a, b in [(st_intent, st_rag), (st_rag, st_reason), (st_reason, st_ground)]
        ])

        # ── Bedrock (top-right) ──
        bedrock = make_comp_box("AWS Bedrock", "Qwen3-32B", color=ORANGE, width=2.6, height=1.0).to_corner(UR, buff=0.5)

        # ── Data + knowledge (bottom row, aligned under their stages) ──
        know   = make_comp_box("Knowledge",  "SQL examples · docs",   color=GREEN, width=2.6, height=0.95)
        sc_box = make_comp_box("Network DB",  "KPIs · alarms · usage", color=SLATE, width=2.5, height=0.95)
        op_box = make_comp_box("Operator DB", "ARPU · plans · churn",  color=SLATE, width=2.5, height=0.95)
        ybot = -3.05
        know.move_to([st_rag.get_center()[0], ybot, 0])
        dbs = VGroup(sc_box, op_box).arrange(RIGHT, buff=0.4)
        dbs.move_to([st_ground.get_center()[0] + 0.7, ybot, 0])

        # ── Draw ──
        self.play(LaggedStart(FadeIn(if_label), *[FadeIn(b, scale=0.9) for b in interfaces],
                              FadeIn(panel), FadeIn(agent_hdr), lag_ratio=0.1, run_time=1.8))
        self.play(LaggedStart(*[FadeIn(s, scale=0.9) for s in stages], lag_ratio=0.15, run_time=1.6))
        self.play(*[Create(a) for a in inner], run_time=1.0)
        self.play(LaggedStart(FadeIn(bedrock, shift=DOWN * 0.2), FadeIn(know, shift=UP * 0.2),
                              *[FadeIn(b, shift=UP * 0.2) for b in (sc_box, op_box)], lag_ratio=0.12, run_time=1.6))

        # ── Cross connections ──
        # all three interfaces <-> agent (ask in / answer out)
        io_arrows = VGroup(*[
            DoubleArrow(b.get_right(), st_intent.get_left(), buff=0.12, color=SLATE,
                        stroke_width=2, max_tip_length_to_length_ratio=0.16)
            for b in (chat, ops, dash)
        ])
        a_brk = DoubleArrow(st_reason.get_top(), bedrock.get_bottom(), buff=0.12, color=ORANGE,
                            stroke_width=2.5, max_tip_length_to_length_ratio=0.12)
        a_kn  = Arrow(know.get_top(), st_rag.get_bottom(), buff=0.12, color=GREEN,
                      stroke_width=2.5, max_tip_length_to_length_ratio=0.16)
        a_db1 = DoubleArrow(st_reason.get_bottom(), sc_box.get_top(), buff=0.12, color=SLATE,
                            stroke_width=2, max_tip_length_to_length_ratio=0.14)
        a_db2 = DoubleArrow(st_ground.get_bottom(), op_box.get_top(), buff=0.12, color=SLATE,
                            stroke_width=2, max_tip_length_to_length_ratio=0.14)
        self.play(Create(io_arrows), run_time=0.9)
        self.play(Create(a_brk), Create(a_kn), Create(a_db1), Create(a_db2), run_time=1.3)

        # ── Spotlight the reasoning loop ──
        hl = SurroundingRectangle(st_reason, color=PRP, buff=0.08, corner_radius=0.12, stroke_width=3)
        self.play(Create(hl), run_time=0.6)
        self.play(hl.animate.set_stroke(opacity=0.4), run_time=0.4)
        self.play(hl.animate.set_stroke(opacity=1.0), run_time=0.4)

        caption = Text("Retrieve context · reason step by step\nself-correct · ground every number in real rows",
                       font_size=13, color=NAVY)
        caption.next_to(interfaces, DOWN, buff=0.3).align_to(interfaces, LEFT)
        self.play(FadeIn(caption), run_time=0.8)
        self.wait(1.5)


# ═════════════════════════════════════════════
#  S3 – DASHBOARD SCENE  (~22s)
# ═════════════════════════════════════════════
class S3_Dashboard(Scene):
    def construct(self):
        # ── Browser frame ──
        browser_w, browser_h = 13.5, 7.0
        browser = RoundedRectangle(width=browser_w, height=browser_h,
                                    corner_radius=0.25,
                                    fill_color=SURFACE, fill_opacity=1,
                                    stroke_color=BORDER, stroke_width=2)
        browser.move_to(DOWN * 0.1)

        # Title bar
        titlebar = Rectangle(width=browser_w, height=0.42,
                             fill_color="#e5e9f5", fill_opacity=1,
                             stroke_width=0)
        titlebar.align_to(browser, UP).align_to(browser, LEFT)

        url_bar = RoundedRectangle(width=4, height=0.22,
                                    corner_radius=0.06,
                                    fill_color=WHITE, fill_opacity=1,
                                    stroke_color=BORDER, stroke_width=1)
        url_bar.move_to(titlebar.get_center())
        url_txt = Text("localhost:8501", font_size=10, color=MUTED)
        url_txt.move_to(url_bar.get_center())

        # Dark background fill inside browser
        content_h = browser_h - 0.42
        dark_bg = Rectangle(width=browser_w, height=content_h,
                             fill_color=D_BG, fill_opacity=1,
                             stroke_width=0)
        dark_bg.align_to(browser, DOWN).align_to(browser, LEFT)
        dark_bg.shift(UP * 0)

        self.play(FadeIn(browser, run_time=0.5))
        self.play(FadeIn(titlebar), FadeIn(dark_bg), run_time=0.4)
        self.add(url_bar, url_txt)

        # ── Sidebar ──
        sb_w, sb_h = 2.6, content_h
        sidebar = Rectangle(width=sb_w, height=sb_h,
                             fill_color=D_DARK, fill_opacity=1,
                             stroke_width=0)
        sidebar.align_to(dark_bg, LEFT).align_to(dark_bg, UP)

        # Logo in sidebar
        sb_smart = Text("SMART", font_size=22, color=D_TXT, weight=BOLD)
        sb_care  = Text("CARE",  font_size=22, color=D_PRP, weight=BOLD)
        sb_logo  = VGroup(sb_smart, sb_care)
        sb_logo.arrange(RIGHT, buff=0.08)
        sb_logo.next_to(sidebar.get_top(), DOWN, buff=0.35)
        sb_logo.align_to(sidebar, LEFT).shift(RIGHT * 0.25)

        sb_sub = Text("COPILOT // UC PLATFORM", font="Courier New",
                      font_size=9, color=D_MUT)
        sb_sub.next_to(sb_logo, DOWN, buff=0.1)
        sb_sub.align_to(sb_logo, LEFT)

        sep1 = Line(sidebar.get_left() + RIGHT * 0.15,
                    sidebar.get_right() + LEFT * 0.15,
                    color=D_BDR, stroke_width=1)
        sep1.next_to(sb_sub, DOWN, buff=0.18)

        # Status box
        status_box = RoundedRectangle(width=sb_w - 0.3, height=1.7,
                                       corner_radius=0.1,
                                       fill_color=D_SURF, fill_opacity=1,
                                       stroke_color=D_BDR, stroke_width=1)
        status_box.next_to(sep1, DOWN, buff=0.18)
        status_box.align_to(sidebar, LEFT).shift(RIGHT * 0.15)

        green_dot = Circle(radius=0.055, fill_color=GREEN,
                           fill_opacity=1, stroke_width=0)
        ol_conn = Text("OLLAMA CONNECTED", font="Courier New",
                       font_size=9, color=GREEN)
        ol_conn.next_to(green_dot, RIGHT, buff=0.07)
        conn_row = VGroup(green_dot, ol_conn)
        conn_row.next_to(status_box.get_top(), DOWN, buff=0.18)
        conn_row.align_to(status_box, LEFT).shift(RIGHT * 0.18)

        stat_lines_data = [
            "* MODEL: QWEN2.5:7B",
            "* RAG: 12 DOCUMENTS",
            "* DB: NetworkAnalyzer + OPERATOR",
        ]
        stat_lines = VGroup()
        for txt in stat_lines_data:
            t = Text(txt, font="Courier New", font_size=9, color=D_MUT)
            stat_lines.add(t)
        stat_lines.arrange(DOWN, aligned_edge=LEFT, buff=0.14)
        stat_lines.next_to(conn_row, DOWN, buff=0.14)
        stat_lines.align_to(conn_row, LEFT)

        # Model selector buttons
        btn_labels = ["7B", "14B", "Gemma", "Coder"]
        btns = VGroup()
        for lbl in btn_labels:
            col = D_PRP if lbl == "7B" else D_BDR
            btn = RoundedRectangle(width=0.52, height=0.3,
                                    corner_radius=0.06,
                                    fill_color=col, fill_opacity=1,
                                    stroke_width=0)
            btxt = Text(lbl, font_size=10,
                        color=WHITE if lbl == "7B" else D_MUT)
            btxt.move_to(btn.get_center())
            btns.add(VGroup(btn, btxt))
        btns.arrange(RIGHT, buff=0.1)
        btns.next_to(status_box, DOWN, buff=0.22)
        btns.align_to(sidebar, LEFT).shift(RIGHT * 0.2)

        scan_btn = RoundedRectangle(width=sb_w - 0.5, height=0.38,
                                     corner_radius=0.1,
                                     fill_color=D_PRP, fill_opacity=1,
                                     stroke_width=0)
        scan_btn.next_to(btns, DOWN, buff=0.18)
        scan_btn.align_to(btns, LEFT)
        scan_txt = Text("SCAN NETWORK", font_size=12,
                        color=WHITE, weight=BOLD)
        scan_txt.move_to(scan_btn.get_center())

        sidebar_group = VGroup(sidebar, sb_logo, sb_sub, sep1,
                               status_box, conn_row, stat_lines,
                               btns, scan_btn, scan_txt)

        # ── Main area ──
        main_x = sidebar.get_right()[0] + 0.3
        main_w = browser_w - sb_w - 0.1

        # Header
        hdr_dot = Circle(radius=0.09, fill_color=D_PRP,
                         fill_opacity=1, stroke_width=0)
        hdr_title = Text("NetworkAnalyzer", font_size=22,
                         color=D_TXT, weight=BOLD)
        hdr_sub = Text("TELCO INTELLIGENCE PLATFORM",
                       font="Courier New", font_size=11, color=D_MUT)
        hdr_dot.move_to(dark_bg.get_top() + DOWN * 0.4 +
                        RIGHT * (main_x - dark_bg.get_left()[0]))
        hdr_title.next_to(hdr_dot, RIGHT, buff=0.12)
        hdr_sub.next_to(hdr_title, RIGHT, buff=0.25)

        hdr_group = VGroup(hdr_dot, hdr_title, hdr_sub)
        hdr_group.next_to(sidebar.get_right(), RIGHT, buff=0.35)
        hdr_group.align_to(dark_bg, UP).shift(DOWN * 0.35)

        # Metric cards
        metric_data = [
            ("50,000",  "SUBSCRIBERS",  D_PRP),
            ("3,852",   "5G UPSELL",    D_BLU),
            ("14,612",  "3G AT-RISK",   ORANGE),
            ("7,979",   "HVC",          GREEN),
        ]
        metric_cards = VGroup()
        card_w = (main_w - 0.6) / 4
        for val, lbl, acc in metric_data:
            bg_r = RoundedRectangle(width=card_w, height=1.0,
                                     corner_radius=0.1,
                                     fill_color=D_MID, fill_opacity=1,
                                     stroke_color=D_BDR, stroke_width=1)
            acc_bar = Rectangle(width=0.06, height=0.7,
                                fill_color=acc, fill_opacity=1,
                                stroke_width=0)
            acc_bar.align_to(bg_r, LEFT).move_to(
                bg_r.get_left() + RIGHT * 0.06)
            v_txt = Text(val, font_size=18, color=D_TXT, weight=BOLD)
            l_txt = Text(lbl, font_size=9, color=D_MUT)
            v_txt.move_to(bg_r.get_center() + LEFT * 0.05)
            l_txt.next_to(v_txt, DOWN, buff=0.06)
            metric_cards.add(VGroup(bg_r, acc_bar, v_txt, l_txt))

        metric_cards.arrange(RIGHT, buff=0.18)
        metric_cards.next_to(hdr_group, DOWN, buff=0.3)
        metric_cards.align_to(hdr_group, LEFT)

        # Chat area container
        chat_bg = RoundedRectangle(
            width=main_w - 0.1,
            height=3.4,
            corner_radius=0.12,
            fill_color=D_DARK, fill_opacity=1,
            stroke_color=D_BDR, stroke_width=1
        )
        chat_bg.next_to(metric_cards, DOWN, buff=0.25)
        chat_bg.align_to(metric_cards, LEFT)

        # ── Animate dashboard build ──
        self.play(FadeIn(sidebar_group, run_time=0.9))
        self.play(FadeIn(hdr_group, run_time=0.6))
        self.play(
            LaggedStart(*[FadeIn(c, shift=UP * 0.15) for c in metric_cards],
                        lag_ratio=0.15, run_time=1.0)
        )
        self.play(FadeIn(chat_bg, run_time=0.4))

        # Pulsing dot
        self.play(hdr_dot.animate.scale(1.4).set_color(D_BLU),
                  run_time=0.3, rate_func=there_and_back)

        # ── Chat interaction ──
        # User bubble
        user_q = "Show 5G upsell candidates by region"
        ubub_bg = RoundedRectangle(
            width=4.5, height=0.55,
            corner_radius=0.18,
            fill_opacity=1, stroke_width=0
        )
        ubub_bg.set_color_by_gradient(PRP, BLU)
        ubub_txt = Text(user_q, font_size=12, color=WHITE)
        ubub_txt.move_to(ubub_bg.get_center())
        ubub = VGroup(ubub_bg, ubub_txt)
        ubub.next_to(chat_bg.get_top(), DOWN, buff=0.3)
        ubub.align_to(chat_bg, RIGHT).shift(LEFT * 0.2)

        self.play(FadeIn(ubub, shift=LEFT * 0.3, run_time=0.6))

        # Typing indicator
        typing_bg = RoundedRectangle(
            width=0.9, height=0.44,
            corner_radius=0.15,
            fill_color=D_SURF, fill_opacity=1,
            stroke_color=D_BDR, stroke_width=1
        )
        dots = VGroup()
        for i in range(3):
            d = Circle(radius=0.065, fill_color=D_MUT,
                       fill_opacity=1, stroke_width=0)
            dots.add(d)
        dots.arrange(RIGHT, buff=0.1)
        dots.move_to(typing_bg.get_center())
        typing = VGroup(typing_bg, dots)
        typing.next_to(ubub, DOWN, buff=0.22)
        typing.align_to(chat_bg, LEFT).shift(RIGHT * 0.2)

        self.play(FadeIn(typing, run_time=0.4))
        # Animate dots
        for _ in range(2):
            for d in dots:
                self.play(d.animate.shift(UP * 0.1).set_fill(D_PRP),
                          run_time=0.18, rate_func=there_and_back)

        self.wait(0.4)
        self.play(FadeOut(typing, run_time=0.3))

        # Agent response
        agent_resp_lines = [
            "Found 3,852 5G upsell candidates across 24 regions.",
            "Tunis leads with 623, followed by Sfax with 412.",
        ]
        abub_bg = RoundedRectangle(
            width=6.2, height=0.9,
            corner_radius=0.15,
            fill_color=D_SURF, fill_opacity=1,
            stroke_color=D_PRP, stroke_width=1.5
        )
        # Left border accent
        abub_accent = Rectangle(width=0.05, height=0.78,
                                fill_color=D_PRP, fill_opacity=1,
                                stroke_width=0)
        abub_accent.align_to(abub_bg, LEFT).move_to(
            abub_bg.get_left() + RIGHT * 0.04)

        alines = VGroup()
        for l in agent_resp_lines:
            alines.add(Text(l, font_size=12, color=D_TXT))
        alines.arrange(DOWN, aligned_edge=LEFT, buff=0.1)
        alines.move_to(abub_bg.get_center() + RIGHT * 0.1)

        abub = VGroup(abub_bg, abub_accent, alines)
        abub.next_to(ubub, DOWN, buff=0.22)
        abub.align_to(chat_bg, LEFT).shift(RIGHT * 0.2)

        self.play(FadeIn(abub, shift=RIGHT * 0.3, run_time=0.7))

        # Mini bar chart inside chat
        bar_data = [623, 412, 287, 251, 198]
        bar_labels = ["Tunis", "Sfax", "Sousse", "Bizerte", "Nabeul"]
        bar_colors = [PRP, PRP_L, BLU, BLU_L, BLU_L]
        max_v = max(bar_data)
        bar_grp = VGroup()
        bar_w_u = 0.38
        bar_max_h = 0.9
        for i, (v, lbl, col) in enumerate(zip(bar_data, bar_labels, bar_colors)):
            bh = (v / max_v) * bar_max_h
            b = Rectangle(width=bar_w_u, height=bh,
                           fill_color=col, fill_opacity=0.9,
                           stroke_width=0)
            lt = Text(lbl, font_size=8, color=D_MUT)
            lt.next_to(b, DOWN, buff=0.04)
            bar_grp.add(VGroup(b, lt))
        bar_grp.arrange(RIGHT, buff=0.08, aligned_edge=DOWN)

        chart_bg = RoundedRectangle(
            width=3.2, height=1.4,
            corner_radius=0.1,
            fill_color=D_MID, fill_opacity=1,
            stroke_color=D_BDR, stroke_width=1
        )
        bar_grp.move_to(chart_bg.get_center() + UP * 0.1)
        chart = VGroup(chart_bg, bar_grp)
        chart.next_to(abub, DOWN, buff=0.18)
        chart.align_to(abub, LEFT).shift(RIGHT * 0.1)

        # Animate bars growing
        self.play(FadeIn(chart_bg, run_time=0.3))
        anims = []
        for vg in bar_grp:
            bar = vg[0]
            orig = bar.copy()
            bar.stretch(0, 1, about_edge=DOWN)
            anims.append(bar.animate.become(orig))
        self.play(LaggedStart(*anims, lag_ratio=0.12, run_time=1.2))
        for vg in bar_grp:
            self.add(vg[1])

        self.wait(1.5)


# ═════════════════════════════════════════════
#  S4 – AGENT LOOP SCENE  (~28s)
# ═════════════════════════════════════════════
class S4_AgentLoop(Scene):
    def construct(self):
        title = Text("HOW THE AGENT THINKS", font_size=36,
                     color=NAVY, weight=BOLD)
        title.to_edge(UP, buff=0.35)
        underline = Line(title.get_left(), title.get_right(),
                         color=PRP, stroke_width=2.5)
        underline.next_to(title, DOWN, buff=0.1)
        self.play(Write(title), GrowFromEdge(underline, LEFT), run_time=0.8)

        # Question bubble
        q_bg = RoundedRectangle(width=7.5, height=0.6,
                                 corner_radius=0.18,
                                 fill_opacity=1, stroke_width=0)
        q_bg.set_color_by_gradient(PRP, BLU)
        q_txt = Text("Show 5G upsell candidates by region",
                     font_size=17, color=WHITE)
        q_txt.move_to(q_bg.get_center())
        q_bub = VGroup(q_bg, q_txt)
        q_bub.next_to(underline, DOWN, buff=0.35)

        self.play(FadeIn(q_bub, shift=UP * 0.2, run_time=0.5))

        # Steps definitions
        steps_info = [
            (1, "CLASSIFY",    "ANALYZE mode selected",
             None),
            (2, "RAG",         "Retrieved 3 telecom docs on 5G deployment",
             None),
            (3, "LLM",         "Generating SQL query...",
             [
                 "QUERY_SC: SELECT s.region,",
                 "  COUNT(DISTINCT s.msisdn) as n",
                 "FROM subscribers s",
                 "JOIN devices d ON s.msisdn=d.msisdn",
                 "JOIN subscriber_technology st",
                 "  ON s.msisdn=st.msisdn",
                 "WHERE d.is_5g_capable=1",
                 "  AND st.current_technology='4G'",
                 "  AND EXISTS(",
                 "    SELECT 1 FROM coverage cv",
                 "    WHERE cv.msisdn=s.msisdn",
                 "    AND cv.technology_available='5G')",
                 "GROUP BY s.region ORDER BY n DESC",
             ]),
            (4, "VALIDATE",    "_check_sql(): syntax OK",           None),
            (5, "EXECUTE",     "15 rows returned — Tunis:623 Sfax:412 Sousse:287...", None),
            (6, "LLM",         "CONCLUDE: sufficient data collected", None),
            (7, "CHART SPEC",  "type: bar, x: regions, y: counts",  None),
            (8, "RESPONSE",    "Answer + bar chart rendered in dashboard", None),
        ]

        dot_colors = {
            "CLASSIFY":   BLU,
            "RAG":        GREEN,
            "LLM":        PRP,
            "VALIDATE":   GREEN,
            "EXECUTE":    BLU,
            "CHART SPEC": ORANGE,
            "RESPONSE":   GREEN,
        }

        # Split layout: steps LEFT column, SQL block RIGHT column
        LEFT_X  = -6.2   # left edge for step rows
        SQL_X   =  0.3   # left edge for SQL code block

        step_rows = []
        sql_block_ref = None

        # Build SQL block first so we know its size
        sql_lines = steps_info[2][3]
        sql_block_ref = make_code_block(sql_lines, width=6.2, font_size=11)

        for i, (num, tag, detail, code) in enumerate(steps_info):
            col = dot_colors.get(tag, PRP)
            circ = Circle(radius=0.22, fill_color=col,
                          fill_opacity=1, stroke_width=0)
            num_t = Text(str(num), font_size=14, color=WHITE)
            num_t.move_to(circ.get_center())

            tag_t = Text(tag, font_size=14, color=col)
            det_t = Text(detail, font_size=12, color=MUTED)

            row_content = VGroup(tag_t, det_t)
            row_content.arrange(RIGHT, buff=0.22)

            row = VGroup(VGroup(circ, num_t), row_content)
            row.arrange(RIGHT, buff=0.25, aligned_edge=UP)

            if i == 0:
                row.next_to(q_bub, DOWN, buff=0.28)
                row.move_to(row.get_center() * UP + RIGHT * LEFT_X)
                row.align_to(VGroup(), LEFT)
                row.to_edge(LEFT, buff=0.7)
            else:
                row.next_to(step_rows[-1], DOWN, buff=0.22)
                row.align_to(step_rows[-1], LEFT)

            step_rows.append(row)

        # Place SQL block: right column, aligned to step 3's vertical center
        sql_block_ref.move_to(
            np.array([SQL_X + sql_block_ref.width / 2, 0, 0])
        )
        sql_block_ref.align_to(step_rows[2], UP).shift(DOWN * 0.1)

        # Animate steps
        for i, row in enumerate(step_rows):
            self.play(FadeIn(row, shift=RIGHT * 0.2, run_time=0.35))
            if i == 2:
                self.play(FadeIn(sql_block_ref, run_time=0.5))
                self.wait(1.0)   # Keep SQL visible while it's being read
            elif i == 4:
                # Fade SQL when we're done with it
                self.play(FadeOut(sql_block_ref, run_time=0.3))
                self.wait(0.2)
            else:
                self.wait(0.28)

        self.wait(1.5)


# ═════════════════════════════════════════════
#  S5 – CHARTS SCENE  (~20s)
# ═════════════════════════════════════════════
class S5_Charts(Scene):
    def construct(self):
        title = Text("AUTO-GENERATED VISUALIZATIONS",
                     font_size=34, color=NAVY, weight=BOLD)
        title.to_edge(UP, buff=0.35)
        underline = Line(title.get_left(), title.get_right(),
                         color=PRP, stroke_width=2.5)
        underline.next_to(title, DOWN, buff=0.1)
        self.play(Write(title), GrowFromEdge(underline, LEFT), run_time=0.8)

        # ══ CHART 1: BAR CHART ══
        q1_bg = RoundedRectangle(width=6.5, height=0.5,
                                  corner_radius=0.15,
                                  fill_opacity=1, stroke_width=0)
        q1_bg.set_color_by_gradient(PRP, BLU)
        q1_txt = Text("5G upsell candidates by region?",
                      font_size=16, color=WHITE)
        q1_txt.move_to(q1_bg.get_center())
        q1 = VGroup(q1_bg, q1_txt)
        q1.next_to(underline, DOWN, buff=0.4)
        q1.to_edge(LEFT, buff=0.6)

        # CONCLUDE JSON hint
        json_hint = Text(
            '{"type":"bar","x":["Tunis","Sfax","Sousse",...],"y":[623,412,287,...]}',
            font="Courier New", font_size=10, color=MUTED
        )
        json_hint.next_to(q1, DOWN, buff=0.12)
        json_hint.align_to(q1, LEFT)

        self.play(FadeIn(q1, shift=UP * 0.2, run_time=0.5))
        self.play(FadeIn(json_hint, run_time=0.4))

        # Bar chart
        bar_vals  = [623, 412, 287, 251, 198, 176, 164, 152]
        bar_names = ["Tunis", "Sfax", "Sousse", "Bizerte",
                     "Nabeul", "Ariana", "Monastir", "Gabes"]
        bar_colors_list = [PRP, PRP_L, PRP_LL, BLU, BLU, BLU_L,
                           BLU_L, BLU_L]

        max_v = max(bar_vals)
        bar_max_h = 2.2
        bar_w_u = 0.72

        bar_grp = VGroup()
        bars_only = VGroup()
        for v, lbl, col in zip(bar_vals, bar_names, bar_colors_list):
            bh = (v / max_v) * bar_max_h
            b = Rectangle(width=bar_w_u, height=bh,
                           fill_color=col, fill_opacity=1,
                           stroke_width=0)
            lbl_t = Text(lbl, font_size=9, color=MUTED)
            lbl_t.next_to(b, DOWN, buff=0.06)
            bar_grp.add(VGroup(b, lbl_t))
            bars_only.add(b)

        bar_grp.arrange(RIGHT, buff=0.14, aligned_edge=DOWN)

        # Value labels all at same Y (above tallest bar)
        top_y = bars_only[0].get_top()[1]  # tallest bar top after arrange
        for vg, v in zip(bar_grp, bar_vals):
            val_t = Text(str(v), font_size=10, color=NAVY)
            val_t.move_to(np.array([vg[0].get_center()[0], top_y + 0.18, 0]))
            vg.add(val_t)
        bar_grp.next_to(json_hint, DOWN, buff=0.3)
        bar_grp.to_edge(LEFT, buff=0.7)

        # Chart container
        chart1_bg = RoundedRectangle(
            width=bar_grp.width + 0.5,
            height=bar_max_h + 1.0,
            corner_radius=0.15,
            fill_color=SURFACE, fill_opacity=1,
            stroke_color=BORDER, stroke_width=1.5
        )
        chart1_bg.move_to(bar_grp.get_center() + DOWN * 0.1)

        chart1_label = Text("5G Upsell by Region", font_size=13,
                            color=NAVY, weight=BOLD)
        chart1_label.next_to(chart1_bg, UP, buff=0.05)

        self.play(FadeIn(chart1_bg, run_time=0.3))
        self.play(LaggedStart(*[FadeIn(b, shift=UP * 0.3)
                                for b in bar_grp],
                              lag_ratio=0.1, run_time=1.4))
        self.play(FadeIn(chart1_label))
        self.wait(0.8)

        # ══ CHART 2: DONUT ══
        self.play(
            FadeOut(VGroup(q1, json_hint, chart1_bg, bar_grp,
                           chart1_label), run_time=0.5)
        )

        q2_bg = RoundedRectangle(width=5.5, height=0.5,
                                  corner_radius=0.15,
                                  fill_opacity=1, stroke_width=0)
        q2_bg.set_color_by_gradient(PRP, BLU)
        q2_txt = Text("Subscriber tier breakdown?",
                      font_size=16, color=WHITE)
        q2_txt.move_to(q2_bg.get_center())
        q2 = VGroup(q2_bg, q2_txt)
        q2.to_edge(LEFT, buff=0.6).to_edge(UP, buff=1.4)

        self.play(FadeIn(q2, shift=UP * 0.2, run_time=0.4))

        # Donut sectors
        tier_data = [
            ("Platinum", 5.2,  PRP),
            ("Gold",     10.7, PRP_L),
            ("Silver",   42.1, BLU),
            ("Bronze",   42.0, BLU_L),
        ]
        total_angle = 0
        outer_r = 1.6
        inner_r = 0.75
        sectors = VGroup()
        for lbl, pct, col in tier_data:
            angle = (pct / 100) * TAU
            sector = AnnularSector(
                inner_radius=inner_r, outer_radius=outer_r,
                angle=angle, start_angle=total_angle + PI / 2,
                fill_color=col, fill_opacity=0.92,
                stroke_color=BG, stroke_width=2
            )
            sectors.add(sector)
            total_angle += angle

        donut_center = np.array([-1.5, -0.5, 0])
        sectors.move_to(donut_center)

        # Legend
        legend = VGroup()
        for lbl, pct, col in tier_data:
            dot = Circle(radius=0.1, fill_color=col,
                         fill_opacity=1, stroke_width=0)
            ltxt = Text(f"{lbl}  {pct}%", font_size=14, color=NAVY)
            ltxt.next_to(dot, RIGHT, buff=0.15)
            row = VGroup(dot, ltxt)
            legend.add(row)
        legend.arrange(DOWN, aligned_edge=LEFT, buff=0.22)
        legend.next_to(sectors, RIGHT, buff=0.7)

        donut_lbl = Text("Subscriber Tiers", font_size=14,
                         color=NAVY, weight=BOLD)
        donut_lbl.next_to(sectors, UP, buff=0.2)

        self.play(
            LaggedStart(*[GrowFromCenter(s) for s in sectors],
                        lag_ratio=0.15, run_time=1.4)
        )
        self.play(FadeIn(legend, run_time=0.6), FadeIn(donut_lbl))
        self.wait(0.7)

        # ══ CHART 3: TREEMAP ══
        self.play(
            FadeOut(VGroup(q2, sectors, legend, donut_lbl), run_time=0.5)
        )

        q3_bg = RoundedRectangle(width=7.5, height=0.5,
                                  corner_radius=0.15,
                                  fill_opacity=1, stroke_width=0)
        q3_bg.set_color_by_gradient(PRP, BLU)
        q3_txt = Text("Drilldown: subscribers by technology & capability?",
                      font_size=15, color=WHITE)
        q3_txt.move_to(q3_bg.get_center())
        q3 = VGroup(q3_bg, q3_txt)
        q3.to_edge(LEFT, buff=0.6).to_edge(UP, buff=1.4)
        self.play(FadeIn(q3, shift=UP * 0.2, run_time=0.4))

        # Treemap rectangles
        tm_total_w = 9.0
        tm_h = 3.8
        tm_origin = np.array([-4.0, -1.5, 0])

        # Level 1 widths proportional
        w4g = tm_total_w * 0.624
        w3g = tm_total_w * 0.292
        w5g = tm_total_w * 0.084

        rect_4g = Rectangle(width=w4g - 0.06, height=tm_h,
                             fill_color=PRP, fill_opacity=0.85,
                             stroke_color=BG, stroke_width=3)
        rect_3g = Rectangle(width=w3g - 0.06, height=tm_h,
                             fill_color=BLU, fill_opacity=0.85,
                             stroke_color=BG, stroke_width=3)
        rect_5g = Rectangle(width=w5g - 0.06, height=tm_h,
                             fill_color=BLU_L, fill_opacity=0.85,
                             stroke_color=BG, stroke_width=3)

        rect_4g.move_to(tm_origin + RIGHT * (w4g / 2))
        rect_3g.move_to(tm_origin + RIGHT * (w4g + w3g / 2 + 0.06))
        rect_5g.move_to(tm_origin + RIGHT * (w4g + w3g + w5g / 2 + 0.12))

        # Labels on level 1
        def tm_label(rect, line1, line2, line3, col=WHITE):
            t1 = Text(line1, font_size=15, color=col, weight=BOLD)
            t2 = Text(line2, font_size=12, color=col)
            t3 = Text(line3, font_size=11, color=col)
            grp = VGroup(t1, t2, t3)
            grp.arrange(DOWN, buff=0.1)
            grp.move_to(rect.get_center())
            return grp

        lbl_4g = tm_label(rect_4g, "4G", "31,200 subs", "62.4%")
        lbl_3g = tm_label(rect_3g, "3G", "14,600 subs", "29.2%")
        lbl_5g = tm_label(rect_5g, "5G", "4,200 subs", "8.4%")

        # Level 2 inside 4G: split horizontally
        h_up  = tm_h * 0.53
        h_dn  = tm_h * 0.47
        rect_4g_up = Rectangle(width=w4g - 0.06, height=h_up - 0.04,
                                fill_color=PRP_L, fill_opacity=0.7,
                                stroke_color=BG, stroke_width=2)
        rect_4g_dn = Rectangle(width=w4g - 0.06, height=h_dn - 0.04,
                                fill_color=PRP_LL, fill_opacity=0.7,
                                stroke_color=BG, stroke_width=2)
        rect_4g_up.move_to(rect_4g.get_center() + UP * (h_dn / 2))
        rect_4g_dn.move_to(rect_4g.get_center() + DOWN * (h_up / 2))

        lbl_4g_up = VGroup(
            Text("5G-capable device", font_size=12, color=WHITE, weight=BOLD),
            Text("16,514  (upgrade gap)", font_size=10, color=PRP_LL)
        )
        lbl_4g_up.arrange(DOWN, buff=0.07)
        lbl_4g_up.move_to(rect_4g_up.get_center())

        lbl_4g_dn = VGroup(
            Text("4G-only device", font_size=12, color=NAVY, weight=BOLD),
            Text("14,686", font_size=10, color=MUTED)
        )
        lbl_4g_dn.arrange(DOWN, buff=0.07)
        lbl_4g_dn.move_to(rect_4g_dn.get_center())

        tm_title = Text("50,000 Subscribers  –  Technology Treemap",
                        font_size=14, color=NAVY, weight=BOLD)
        tm_title.next_to(q3, DOWN, buff=0.2)
        tm_title.to_edge(LEFT, buff=0.6)

        # Animate treemap
        self.play(FadeIn(tm_title))
        self.play(
            GrowFromCenter(rect_4g, run_time=0.6),
            GrowFromCenter(rect_3g, run_time=0.6),
            GrowFromCenter(rect_5g, run_time=0.6),
        )
        self.play(
            FadeIn(lbl_4g), FadeIn(lbl_3g), FadeIn(lbl_5g),
            run_time=0.4
        )
        self.wait(0.3)
        # Level 2
        self.play(
            FadeIn(rect_4g_up), FadeIn(rect_4g_dn), run_time=0.5
        )
        self.play(
            FadeIn(lbl_4g_up), FadeIn(lbl_4g_dn), run_time=0.4
        )
        self.wait(1.5)


# ═════════════════════════════════════════════
#  S6 – SELF-CORRECTION SCENE  (~15s)
# ═════════════════════════════════════════════
class S6_SelfCorrection(Scene):
    def construct(self):
        title = Text("BUILT-IN SELF-CORRECTION",
                     font_size=36, color=NAVY, weight=BOLD)
        title.to_edge(UP, buff=0.35)
        underline = Line(title.get_left(), title.get_right(),
                         color=PRP, stroke_width=2.5)
        underline.next_to(title, DOWN, buff=0.1)
        self.play(Write(title), GrowFromEdge(underline, LEFT), run_time=0.8)

        # ── Example 1: Wrong SQL ──
        label1 = Text("Example 1  —  Column Name Error",
                      font_size=16, color=PRP_L, weight=BOLD)
        label1.next_to(underline, DOWN, buff=0.3)
        label1.to_edge(LEFT, buff=0.8)
        self.play(FadeIn(label1, run_time=0.4))

        # Wrong SQL block
        wrong_lines = [
            "QUERY_SC: SELECT * FROM subscriber_technology",
            "          WHERE technology = '5G'",
            "          LIMIT 100",
        ]
        wrong_cb = make_code_block(wrong_lines, width=6.5, font_size=14)
        wrong_cb.next_to(label1, DOWN, buff=0.25)
        wrong_cb.to_edge(LEFT, buff=0.8)

        # Highlight "technology" in red — anchor to second line of code block
        second_line = wrong_cb[1][1]
        tech_highlight = RoundedRectangle(
            width=second_line.width * 0.42, height=second_line.height + 0.08,
            corner_radius=0.05,
            fill_color=RED, fill_opacity=0.3,
            stroke_color=RED, stroke_width=1.5
        )
        # "technology" starts roughly 55% from the left of the second line
        tech_highlight.move_to(
            second_line.get_left() + RIGHT * second_line.width * 0.68
        )

        self.play(FadeIn(wrong_cb, run_time=0.5))
        self.play(FadeIn(tech_highlight, run_time=0.4))

        # Error message
        err_txt = Text("[ERR]  SQL Error: no such column: technology",
                       font_size=15, color=RED)
        err_txt.next_to(wrong_cb, DOWN, buff=0.2)
        err_txt.align_to(wrong_cb, LEFT)
        self.play(FadeIn(err_txt, run_time=0.4))

        # Fuzzy scanner
        scan_box = RoundedRectangle(
            width=7.5, height=1.5,
            corner_radius=0.12,
            fill_color="#1a1f35", fill_opacity=1,
            stroke_color=D_BDR, stroke_width=1.5
        )
        scan_box.next_to(err_txt, DOWN, buff=0.2)
        scan_box.align_to(wrong_cb, LEFT)

        scan_lines = [
            "_check_sql(): scanning subscriber_technology...",
            "columns: msisdn, current_technology, volte_active,",
            "         volte_enabled, is_roaming, ...",
        ]
        scan_txts = VGroup()
        for i, line in enumerate(scan_lines):
            col = D_MUT if i == 0 else D_TXT
            t = Text(line, font="Courier New", font_size=11, color=col)
            scan_txts.add(t)
        scan_txts.arrange(DOWN, aligned_edge=LEFT, buff=0.1)
        scan_txts.move_to(scan_box.get_center() + LEFT * 0.3)

        # Highlight current_technology in green
        col_line = scan_txts[1]
        match_highlight = RoundedRectangle(
            width=col_line.width * 0.44, height=col_line.height + 0.08,
            corner_radius=0.05,
            fill_color=GREEN, fill_opacity=0.25,
            stroke_color=GREEN, stroke_width=1.5
        )
        match_highlight.move_to(
            col_line.get_left() + RIGHT * col_line.width * 0.36
        )

        self.play(FadeIn(scan_box, run_time=0.3))
        self.play(
            LaggedStart(*[FadeIn(t, run_time=0.3) for t in scan_txts],
                        lag_ratio=0.3, run_time=0.9)
        )
        self.play(FadeIn(match_highlight, run_time=0.4))

        match_txt = Text("current_technology  — MATCH",
                         font_size=13, color=GREEN)
        match_txt.next_to(scan_box, RIGHT, buff=0.3)
        match_txt.align_to(scan_box, UP).shift(DOWN * 0.2)
        self.play(FadeIn(match_txt, run_time=0.3))

        self.wait(0.4)

        # Fixed SQL
        fixed_lines = [
            "QUERY_SC: SELECT * FROM subscriber_technology",
            "          WHERE current_technology = '5G'",
            "          LIMIT 100",
        ]
        fixed_cb = make_code_block(fixed_lines, width=6.5, font_size=14)
        fixed_cb.next_to(scan_box, RIGHT, buff=0.5)
        fixed_cb.align_to(wrong_cb, UP)

        self.play(FadeIn(fixed_cb, run_time=0.5))

        # Strikethrough old, highlight new
        strike = Line(
            wrong_cb.get_left() + DOWN * 0.28 + RIGHT * 0.6,
            wrong_cb.get_left() + DOWN * 0.28 + RIGHT * 2.9,
            color=RED, stroke_width=2
        )
        self.play(Create(strike, run_time=0.4))

        ok_txt  = Text("[OK]  SQL validated",      font_size=14, color=GREEN)
        row_txt = Text(">>   4,200 rows returned", font_size=14, color=BLU)
        ok_txt.next_to(fixed_cb, DOWN, buff=0.2)
        ok_txt.align_to(fixed_cb, LEFT)
        row_txt.next_to(ok_txt, DOWN, buff=0.1)
        row_txt.align_to(ok_txt, LEFT)
        self.play(FadeIn(ok_txt), FadeIn(row_txt), run_time=0.5)

        self.wait(0.5)

        # ── Example 2: Semantic Error ──
        self.play(
            FadeOut(VGroup(label1, wrong_cb, tech_highlight, err_txt,
                           scan_box, scan_txts, match_highlight,
                           match_txt, fixed_cb, strike, ok_txt, row_txt),
                    run_time=0.6)
        )

        label2 = Text("Example 2  —  Semantic / Logic Error",
                      font_size=16, color=ORANGE, weight=BOLD)
        label2.next_to(underline, DOWN, buff=0.3)
        label2.to_edge(LEFT, buff=0.8)
        self.play(FadeIn(label2, run_time=0.4))

        wrong2_lines = [
            "QUERY_OP: SELECT AVG(arpu) FROM customer_value cv",
            "          JOIN subscribers s ON cv.msisdn=s.msisdn",
            "          WHERE s.customer_segment='gold'",
        ]
        wrong2_cb = make_code_block(wrong2_lines, width=7.5, font_size=14)
        wrong2_cb.next_to(label2, DOWN, buff=0.25)
        wrong2_cb.to_edge(LEFT, buff=0.8)
        self.play(FadeIn(wrong2_cb, run_time=0.5))

        inflated = Text("->  Result: AVG(arpu) = 450 TND  (!) inflated!",
                        font_size=15, color=ORANGE)
        inflated.next_to(wrong2_cb, DOWN, buff=0.2)
        inflated.align_to(wrong2_cb, LEFT)
        self.play(FadeIn(inflated, run_time=0.4))

        semantic_err = Text(
            "_check_sql_semantics(): SEMANTIC ERROR\n"
            "customer_value has 6 rows/subscriber (one per month)",
            font_size=13, color=RED
        )
        semantic_err.next_to(inflated, DOWN, buff=0.2)
        semantic_err.align_to(wrong2_cb, LEFT)
        self.play(FadeIn(semantic_err, run_time=0.5))

        fixed2_lines = [
            "QUERY_OP: SELECT AVG(arpu) FROM customer_value cv",
            "          JOIN subscribers s ON cv.msisdn=s.msisdn",
            "          WHERE s.customer_segment='gold'",
            "          AND cv.month=(SELECT MAX(month)",
            "            FROM customer_value)",
        ]
        fixed2_cb = make_code_block(fixed2_lines, width=7.5, font_size=14)
        fixed2_cb.next_to(semantic_err, DOWN, buff=0.3)
        fixed2_cb.to_edge(LEFT, buff=0.8)
        self.play(FadeIn(fixed2_cb, run_time=0.5))

        correct_result = Text("->  Correct result: AVG(arpu) = 75 TND  [OK]",
                              font_size=15, color=GREEN)
        correct_result.next_to(fixed2_cb, DOWN, buff=0.2)
        correct_result.align_to(fixed2_cb, LEFT)
        self.play(FadeIn(correct_result, run_time=0.4))

        self.wait(1.5)