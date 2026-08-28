"""
Generates the Chapter 2 "Schema Overview" figure (Figure 2.1 in the report)
straight from the table groupings already described in section 2.1.1/2.1.2 —
not from the live DB, which has since grown past what that chapter covers.
Run: networkanalyzer-env/Scripts/python.exe report_assets/schema_overview_diagram.py
Output: report_assets/schema_overview.pdf (vector, safe to \\includegraphics directly)
        report_assets/schema_overview.png (quick preview)
"""
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib.lines import Line2D

NETWORK_GROUPS = [
    ("Infrastructure", ["sites", "cells"]),
    ("Subscribers", ["subscribers", "subscriber_technology", "devices", "coverage"]),
    ("KPIs", ["kpis_daily", "kpis_hourly"]),
    ("Usage", ["dou_monthly", "ott_monthly", "mobility_profile", "roaming_usage"]),
    ("Quality of Experience", ["qoe_daily", "signal_quality", "streaming_quality",
                               "web_quality", "voip_quality"]),
    ("Operations", ["network_alarms", "network_incidents", "complaints", "nps_scores"]),
]

OPERATOR_GROUPS = [
    ("Customers", ["customers", "subscriptions", "plans"]),
    ("Financials", ["billing", "customer_value"]),
    ("Campaigns", ["campaigns", "campaign_targets", "offers",
                   "offer_assignments", "sms_log", "service_interruptions"]),
]

NETWORK_COLOR  = "#e8eef7"
NETWORK_BORDER = "#4a6fa5"
OPERATOR_COLOR = "#f7efe3"
OPERATOR_BORDER = "#b5793a"
TEXT_DARK = "#1a1a1e"


HEADER_H = 0.46   # space for the bold group label
LINE_H   = 0.34   # space per bulleted table name
PAD_TOP  = 0.14
PAD_BOT  = 0.16
GAP      = 0.28


def _box_height(tables):
    return PAD_TOP + HEADER_H + len(tables) * LINE_H + PAD_BOT


def draw_column(ax, x0, width, groups, title, box_color, border_color, top=9.3):
    heights = [_box_height(tables) for _, tables in groups]
    total_h = sum(heights) + GAP * (len(groups) - 1)
    bottom = top - total_h

    # outer container hugs the actual stacked content
    ax.add_patch(FancyBboxPatch(
        (x0 - 0.15, bottom - 0.25), width + 0.3, total_h + 0.65,
        boxstyle="round,pad=0.02,rounding_size=0.12",
        linewidth=1.6, edgecolor=border_color, facecolor="white", zorder=1))
    ax.text(x0 + width / 2, top + 0.62, title, ha="center", va="bottom",
             fontsize=13, fontweight="bold", color=TEXT_DARK)

    y = top
    for (label, tables), box_h in zip(groups, heights):
        y0 = y - box_h
        ax.add_patch(FancyBboxPatch(
            (x0, y0), width, box_h,
            boxstyle="round,pad=0.02,rounding_size=0.08",
            linewidth=1.0, edgecolor=border_color, facecolor=box_color, zorder=2))
        ax.text(x0 + 0.18, y0 + box_h - PAD_TOP, label, ha="left", va="top",
                 fontsize=10.5, fontweight="bold", color=TEXT_DARK)
        body = "\n".join(f"• {t}" for t in tables)
        ax.text(x0 + 0.18, y0 + box_h - PAD_TOP - HEADER_H, body, ha="left", va="top",
                 fontsize=8.7, color=TEXT_DARK, family="monospace", linespacing=1.65)
        y = y0 - GAP
    return bottom, total_h


TOP = 9.3
fig, ax = plt.subplots(figsize=(13, 9.6))
ax.axis("off")

col_w = 5.0
left_x = 0.4
right_x = 13 - 0.4 - col_w

# Pre-compute both column heights first, so the SHORTER column can be
# vertically centered on the TALLER one's midline instead of just top-aligned
# and left to trail off into empty space at the bottom.
left_heights  = [_box_height(t) for _, t in NETWORK_GROUPS]
right_heights = [_box_height(t) for _, t in OPERATOR_GROUPS]
left_h  = sum(left_heights)  + GAP * (len(NETWORK_GROUPS) - 1)
right_h = sum(right_heights) + GAP * (len(OPERATOR_GROUPS) - 1)
tall_h  = max(left_h, right_h)
tall_center = TOP - tall_h / 2

left_top  = tall_center + left_h / 2
right_top = tall_center + right_h / 2

left_bottom, left_h = draw_column(ax, left_x, col_w, NETWORK_GROUPS,
            "NetworkAnalyzer_new.db  (network domain)",
            NETWORK_COLOR, NETWORK_BORDER, top=left_top)
right_bottom, right_h = draw_column(ax, right_x, col_w, OPERATOR_GROUPS,
            "operator_new.db  (commercial domain)",
            OPERATOR_COLOR, OPERATOR_BORDER, top=right_top)

# msisdn connector, vertically centered on whichever column is taller —
# now BOTH columns are centered on this same line, so it reads as balanced.
mid_y = tall_center
ax.add_patch(FancyArrowPatch(
    (left_x + col_w + 0.2, mid_y), (right_x - 0.2, mid_y),
    arrowstyle="<|-|>", mutation_scale=18, linewidth=2.0,
    color=TEXT_DARK, zorder=3))
ax.add_patch(FancyBboxPatch(
    (left_x + col_w + 0.55, mid_y - 0.28), (right_x - left_x - col_w) - 1.1, 0.56,
    boxstyle="round,pad=0.02,rounding_size=0.08",
    linewidth=1.2, edgecolor=TEXT_DARK, facecolor="#fdf6e3", zorder=4))
ax.text((left_x + col_w + right_x) / 2, mid_y, "msisdn",
         ha="center", va="center", fontsize=11.5, fontweight="bold",
         family="monospace", color=TEXT_DARK, zorder=5)
ax.text((left_x + col_w + right_x) / 2, mid_y - 0.62, "shared join key",
         ha="center", va="top", fontsize=8, style="italic", color="#555")

fig_bottom = min(left_bottom, right_bottom) - 0.4
ax.set_xlim(0, 13)
ax.set_ylim(fig_bottom, TOP + 1.05)
plt.tight_layout()
fig.savefig("report_assets/schema_overview.pdf", bbox_inches="tight")
fig.savefig("report_assets/schema_overview.png", dpi=220, bbox_inches="tight")
print("Wrote report_assets/schema_overview.pdf and .png")
