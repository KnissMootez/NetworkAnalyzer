"""Slide 23 figure: the alarm/KPI coupling, before and after.

Chart only -- the explanation belongs in the speaker notes, not on the slide.

Every number is measured from NetworkAnalyzer_new.db; nothing is illustrative.
5G is charted because it is the only technology with a sound sample on both
sides in both periods AND the one showing the genuine inversion. 4G reaches
2.4x after the fix but its clean baseline is two cells, so it is not drawn.
"""
import os
import sqlite3
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "NetworkAnalyzer_new.db")
OUT = os.path.join(BASE, "report_assets", "ch4-alarm-coupling-final.png")

BEFORE_DATE, AFTER_DATE, TECH = "2026-06-15", "2026-09-04", "5G"
INK, MUTED, RED, BLUE = "#1B1C1A", "#6D6B64", "#C7000B", "#3F6076"


def measure(conn, date, tech):
    return conn.execute(f"""
        SELECT ROUND(AVG(CASE WHEN a.cell_id IS NOT NULL THEN k.dropped_call_rate END),3),
               ROUND(AVG(CASE WHEN a.cell_id IS NULL     THEN k.dropped_call_rate END),3),
               SUM(CASE WHEN a.cell_id IS NOT NULL THEN 1 ELSE 0 END),
               SUM(CASE WHEN a.cell_id IS NULL     THEN 1 ELSE 0 END)
        FROM kpis_daily k
        JOIN cells c ON k.cell_id = c.cell_id
        LEFT JOIN (SELECT DISTINCT cell_id FROM network_alarms
                   WHERE date(trigger_time) <= '{date}'
                     AND (clear_time IS NULL OR date(clear_time) >= '{date}')) a
               ON a.cell_id = k.cell_id
        WHERE k.date = '{date}' AND c.technology = '{tech}'""").fetchone()


conn = sqlite3.connect(DB)
b_al, b_cl, b_na, b_nc = measure(conn, BEFORE_DATE, TECH)
a_al, a_cl, a_na, a_nc = measure(conn, AFTER_DATE, TECH)
t3b, t3a = measure(conn, BEFORE_DATE, "3G"), measure(conn, AFTER_DATE, "3G")
conn.close()

b_ratio, a_ratio = b_al / b_cl, a_al / a_cl
r3b, r3a = t3b[0] / t3b[1], t3a[0] / t3a[1]

plt.rcParams["font.family"] = "Georgia"
fig, ax = plt.subplots(figsize=(11.0, 5.6), dpi=220)
fig.patch.set_alpha(0)
ax.set_facecolor("none")

x, w = [0, 0.95], 0.30
alarmed, clean = [b_al, a_al], [b_cl, a_cl]

bars_a = ax.bar([i - w / 2 for i in x], alarmed, w, color=RED, zorder=3)
bars_c = ax.bar([i + w / 2 for i in x], clean, w, color=BLUE, alpha=.85, zorder=3)

for rects in (bars_a, bars_c):
    for r in rects:
        ax.annotate(f"{r.get_height():.3f}",
                    (r.get_x() + r.get_width() / 2, r.get_height()),
                    xytext=(0, 6), textcoords="offset points",
                    ha="center", fontsize=12, color=INK)

top = max(alarmed + clean)
ax.annotate(f"{b_ratio:.2f}×", (x[0], top * 1.24), ha="center",
            fontsize=30, color=MUTED, family="Georgia")
ax.annotate(f"{a_ratio:.2f}×", (x[1], top * 1.24), ha="center",
            fontsize=30, color=RED, family="Georgia")

ax.set_xticks(x)
ax.set_xticklabels([f"BEFORE\n{BEFORE_DATE}", f"AFTER\n{AFTER_DATE}"],
                   fontsize=13, color=INK, family="Courier New", linespacing=1.8)
ax.set_ylabel("dropped call rate  (%)", fontsize=12, color=MUTED, family="Georgia")
ax.set_ylim(0, top * 1.52)
ax.tick_params(axis="y", labelsize=10.5, colors=MUTED, length=0)
ax.tick_params(axis="x", length=0, pad=12)
for s in ("top", "right", "left"):
    ax.spines[s].set_visible(False)
ax.spines["bottom"].set_color("#D6D3CC")
ax.grid(axis="y", color="#E6E3DC", linewidth=.9, zorder=0)
ax.set_axisbelow(True)

ax.legend(handles=[Patch(facecolor=RED, label="cells under an active alarm"),
                   Patch(facecolor=BLUE, alpha=.85, label="clean cells")],
          loc="upper left", bbox_to_anchor=(0.0, 1.02), frameon=False,
          fontsize=11.5, labelcolor=INK)

fig.text(0.5, 0.005,
         f"{TECH} cells   ·   before {b_na}/{b_nc}   ·   after {a_na}/{a_nc}   ·   "
         f"3G {r3b:.2f}x to {r3a:.2f}x",
         ha="center", fontsize=9, color="#A5A29A", family="Courier New")

fig.subplots_adjust(bottom=0.20, top=0.95)
fig.savefig(OUT, transparent=True, bbox_inches="tight", pad_inches=0.28)
print("wrote", OUT)
print(f"  BEFORE {TECH}: {b_al} vs {b_cl} = {b_ratio:.2f}x  (n {b_na}/{b_nc})")
print(f"  AFTER  {TECH}: {a_al} vs {a_cl} = {a_ratio:.2f}x  (n {a_na}/{a_nc})")
print(f"  3G          : {r3b:.2f}x -> {r3a:.2f}x")
