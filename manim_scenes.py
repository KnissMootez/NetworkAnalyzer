"""
NetworkAnalyzer — Manim explainer scenes (architecture / workflow / pipeline).

Setup (Manim needs Python 3.12, not 3.14):
    py -3.12 -m venv manim_env
    manim_env\\Scripts\\activate
    pip install manim

Render (fast preview):
    manim -pql manim_scenes.py Architecture

Higher quality for the deck:
    manim -qh manim_scenes.py Architecture
"""

from manim import *

# palette — matches the Huawei-red deck
BG    = "#140406"   # deep red-black background
RED   = "#E4002B"   # Huawei-ish red (agent / outputs)
BLUE  = "#4F8EF7"   # data sources
GREEN = "#34D399"   # cloud LLM
CARD  = "#2A0F14"   # node fill
TEXT  = "#FFFFFF"
MUTE  = "#C9AEB3"
FONT  = "Calibri"


def node(label, sub=None, w=2.7, h=1.1, color=RED, fill=CARD):
    """A labelled rounded-rectangle node. Returns (group, box)."""
    box = RoundedRectangle(width=w, height=h, corner_radius=0.12,
                           stroke_color=color, stroke_width=2.5,
                           fill_color=fill, fill_opacity=1.0)
    title = Text(label, font=FONT, weight=BOLD, color=TEXT).scale(0.42)
    grp = VGroup(box)
    if sub:
        title.move_to(box.get_center() + UP * 0.14)
        s = Text(sub, font=FONT, color=MUTE).scale(0.26)
        s.next_to(title, DOWN, buff=0.08)
        grp.add(title, s)
    else:
        title.move_to(box.get_center())
        grp.add(title)
    return grp, box


def flow(scene, arrow, color, n=1, run_time=0.9):
    """Send n dots travelling along an arrow to suggest data flow."""
    dots = [Dot(color=color, radius=0.06).move_to(arrow.get_start()) for _ in range(n)]
    scene.add(*dots)
    scene.play(*[MoveAlongPath(d, arrow) for d in dots], run_time=run_time, rate_func=linear)
    scene.remove(*dots)


class Architecture(Scene):
    def construct(self):
        self.camera.background_color = BG

        title = Text("NetworkAnalyzer  —  Architecture", font=FONT, weight=BOLD, color=TEXT).scale(0.6).to_edge(UP, buff=0.45)
        rule  = Line(title.get_left(), title.get_right(), color=RED, stroke_width=3).next_to(title, DOWN, buff=0.12)
        self.play(FadeIn(title), Create(rule), run_time=1.0)

        # ── data sources (left) ──
        db1, db1b = node("Network DB", "KPIs · alarms · usage", w=2.9, color=BLUE)
        db2, db2b = node("Commercial DB", "ARPU · plans · churn", w=2.9, color=BLUE)
        dbs = VGroup(db1, db2).arrange(DOWN, buff=0.6).to_edge(LEFT, buff=0.7).shift(DOWN * 0.3)

        # ── agent (centre) ──
        agent, agentb = node("Agent", "reason · self-correct · ground", w=3.1, h=1.35, color=RED)
        agent.move_to(LEFT * 0.1 + DOWN * 0.3)

        # ── cloud LLM (top) ──
        brk, brkb = node("AWS Bedrock", "Qwen3-32B", w=2.7, color=GREEN)
        brk.next_to(agent, UP, buff=1.15)

        # ── surfaces (right) ──
        ui1, ui1b = node("Copilot Chat", w=2.5, color=RED)
        ui2, ui2b = node("Ops Portal", w=2.5, color=RED)
        ui3, ui3b = node("Dashboard", w=2.5, color=RED)
        uis = VGroup(ui1, ui2, ui3).arrange(DOWN, buff=0.4).to_edge(RIGHT, buff=0.7).shift(DOWN * 0.3)

        self.play(FadeIn(dbs, shift=RIGHT * 0.3), run_time=0.8)
        self.play(FadeIn(agent, scale=0.9), run_time=0.7)
        self.play(FadeIn(brk, shift=DOWN * 0.2), run_time=0.6)
        self.play(FadeIn(uis, shift=LEFT * 0.3), run_time=0.8)

        # ── connections ──
        a1 = Arrow(db1b.get_right(), agentb.get_left(), color=BLUE, buff=0.15, stroke_width=3)
        a2 = Arrow(db2b.get_right(), agentb.get_left(), color=BLUE, buff=0.15, stroke_width=3)
        a3 = DoubleArrow(agentb.get_top(), brkb.get_bottom(), color=GREEN, buff=0.15, stroke_width=3)
        a4 = Arrow(agentb.get_right(), ui1b.get_left(), color=RED, buff=0.15, stroke_width=3)
        a5 = Arrow(agentb.get_right(), ui2b.get_left(), color=RED, buff=0.15, stroke_width=3)
        a6 = Arrow(agentb.get_right(), ui3b.get_left(), color=RED, buff=0.15, stroke_width=3)
        self.play(*[Create(a) for a in (a1, a2, a3, a4, a5, a6)], run_time=1.2)

        # ── data flowing ──
        flow(self, a1, BLUE); flow(self, a2, BLUE)          # DBs -> agent
        flow(self, a3, GREEN, run_time=0.7)                 # agent <-> Bedrock
        dots = [Dot(color=RED, radius=0.06).move_to(a.get_start()) for a in (a4, a5, a6)]
        self.add(*dots)
        self.play(*[MoveAlongPath(d, a) for d, a in zip(dots, (a4, a5, a6))], run_time=1.0, rate_func=linear)
        self.remove(*dots)

        caption = Text("Two databases, one reasoning agent, delivered to every surface.",
                       font=FONT, color=MUTE).scale(0.36).to_edge(DOWN, buff=0.5)
        self.play(FadeIn(caption), run_time=0.8)
        self.wait(1.5)
