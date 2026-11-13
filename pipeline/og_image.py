"""Social preview image (site/assets/og.png, 1200 x 630) drawn from the price-of-a-challenge series.

    python pipeline/og_image.py
"""
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
S = json.loads((ROOT / "site" / "data" / "story.json").read_text())
N = json.loads((ROOT / "data" / "out" / "numbers.json").read_text())

BG, INK, ACC, ORANGE, MUTED = "#1a0f33", "#f4f0fb", "#b79cff", "#f0a35a", "#9d94b5"
fig = plt.figure(figsize=(12, 6.3), dpi=100, facecolor=BG)
fig.text(0.055, 0.80, "Tap or Hold", color=INK, fontsize=64, fontweight="bold", family="serif")
fig.text(0.057, 0.70, "Pricing the ABS challenge · 2026 · built for the Colorado Rockies", color=MUTED, fontsize=17)
lines = [f"{N['missed']} missed calls in 2026",
         f"{N['wins_missed_per_club']} wins lost per club, {N['wins_fixed_per_club']} won back",
         f"A priced policy wins back {N['card_wins']}"]
for i, t in enumerate(lines):
    fig.text(0.057, 0.52 - i * 0.075, t, color=INK, fontsize=18)
fig.text(0.057, 0.10, "Austin Gentile  ·  austinsanalytics.github.io/tap-or-hold", color=ACC, fontsize=15)

ax = fig.add_axes([0.61, 0.16, 0.35, 0.46], facecolor=BG)
P = S["price"]
ax.plot(P["innings"], P["k1"], color=ACC, lw=4, marker="o", ms=6, label="Last challenge")
ax.plot(P["innings"], P["k2"], color=ORANGE, lw=3, marker="o", ms=5, label="One of two")
ax.axhline(0.2, color=MUTED, lw=2, ls="--")
ax.text(10, 0.208, "Flat 0.20-run guideline", color=MUTED, fontsize=12, ha="right")
ax.set_ylim(0, 0.26)
ax.set_xticks([1, 3, 5, 7, 9])
ax.set_xlabel("Inning", color=MUTED, fontsize=12)
ax.set_title("What losing a challenge costs (runs)", color=INK, fontsize=14, loc="left")
for s in ("top", "right"):
    ax.spines[s].set_visible(False)
for s in ("left", "bottom"):
    ax.spines[s].set_color(MUTED)
ax.tick_params(colors=MUTED, labelsize=11)
ax.legend(frameon=False, labelcolor=INK, fontsize=12, loc="center right")
out = ROOT / "site" / "assets" / "og.png"
fig.savefig(out, facecolor=BG)
print(f"wrote {out.relative_to(ROOT)}")
