"""The printable dugout card: how sure a player must be before tapping, for close games, at Coors and on the road.

    python pipeline/card_pdf.py          -> site/assets/dugout-card.pdf

Each cell is the median break-even confidence over the 2026 called pitches in that cell (count x inning group x
runners), each priced with its own win-probability value and the challenge-value table from the season replay.
"""
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.pagesizes import landscape, letter
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "tapor.duckdb"
OUT = ROOT / "site" / "assets" / "dugout-card.pdf"
COUNTS = [f"{b}-{s}" for b in range(4) for s in range(3)]
GROUPS = [("1-3", 1, 3), ("4-6", 4, 6), ("7", 7, 7), ("8", 8, 8), ("9", 9, 9), ("10+", 10, 99)]
PURPLE = HexColor("#4b1d8f")
PALE = HexColor("#f1eef8")
INK = HexColor("#17151c")
MUTED = HexColor("#6f6a7a")


def thresholds(con):
    """Model break-even confidence for every state (table `card`, from export.py), weighted by how often each state
    came up in close games across all of 2026. Coors and road pages share the weights and differ only in the model."""
    w = con.execute("""
        SELECT least(c.inning, 10) AS inning, (c.wronged_team_id = c.home_id)::INT AS team_home,
               (c.wronged_side = 'offense')::INT AS batting,
               CASE WHEN c.wronged_team_id = c.home_id THEN c.home_score - c.away_score
                    ELSE c.away_score - c.home_score END AS margin,
               c.base_state AS base, c.outs, c.balls, c.strikes, count(*) AS weight
        FROM calls c
        WHERE NOT c.position_player_pitching AND c.wronged_team_id IS NOT NULL
        GROUP BY ALL HAVING abs(margin) <= 1""").df()
    card = con.execute("SELECT * FROM card WHERE abs(margin) <= 1").df()
    df = card.merge(w, on=["inning", "team_home", "batting", "margin", "base", "outs", "balls", "strikes"], how="inner")
    df["cnt"] = df.balls.astype(str) + "-" + df.strikes.astype(str)
    df["runners"] = df.base > 0
    df["coors"] = df.env == "coors"
    df["group"] = pd.cut(df.inning, [0, 3, 6, 7, 8, 9, 99], labels=[g[0] for g in GROUPS])
    return df


def wmedian(g):
    g = g.sort_values("p_star")
    c = g.weight.cumsum()
    return float(g.p_star.values[np.searchsorted(c.values, c.values[-1] / 2)])


def shade(p):
    t = float(np.clip((p - 0.05) / 0.9, 0, 1)) ** 0.8
    a, b = PURPLE, PALE
    return Color(a.red + (b.red - a.red) * t, a.green + (b.green - a.green) * t, a.blue + (b.blue - a.blue) * t)


def panel(cv, x, y, title, cells):
    cw, ch, lw = 46, 14.5, 30
    cv.setFont("Helvetica-Bold", 9.5)
    cv.setFillColor(INK)
    cv.drawString(x, y + 4, title)
    y -= 14
    cv.setFont("Helvetica-Bold", 7.5)
    cv.setFillColor(MUTED)
    for j, (g, _, _) in enumerate(GROUPS):
        cv.drawCentredString(x + lw + j * cw + cw / 2, y, f"Inn {g}")
    y -= 4
    for i, cnt in enumerate(COUNTS):
        yy = y - (i + 1) * ch
        cv.setFillColor(MUTED)
        cv.setFont("Helvetica-Bold", 8)
        cv.drawRightString(x + lw - 6, yy + 4, cnt)
        for j, (g, _, _) in enumerate(GROUPS):
            p = cells.get((cnt, g))
            xx = x + lw + j * cw
            if p is None or np.isnan(p):
                cv.setFillColor(HexColor("#f5f5f5"))
                cv.roundRect(xx + 1, yy + 1, cw - 2, ch - 2, 2, fill=1, stroke=0)
                continue
            col = shade(p)
            cv.setFillColor(col)
            cv.roundRect(xx + 1, yy + 1, cw - 2, ch - 2, 2, fill=1, stroke=0)
            dark = (0.299 * col.red + 0.587 * col.green + 0.114 * col.blue) < 0.55
            cv.setFillColor(white if dark else INK)
            cv.setFont("Helvetica-Bold", 8.5)
            cv.drawCentredString(xx + cw / 2, yy + 4, "—" if p >= 0.995 else f"{round(100 * p)}%")


def page(cv, df, env_title, notes):
    W, H = landscape(letter)
    cv.setFillColor(PURPLE)
    cv.rect(0, H - 54, W, 54, fill=1, stroke=0)
    cv.setFillColor(white)
    cv.setFont("Helvetica-Bold", 20)
    cv.drawString(36, H - 34, "TAP OR HOLD")
    cv.setFont("Helvetica", 11)
    cv.drawString(180, H - 33, f"ABS challenge card  ·  {env_title}  ·  close games (tied or within one run)")
    cv.setFillColor(INK)
    cv.setFont("Helvetica", 9.5)
    cv.drawString(36, H - 74, "Tap only if you are at least this sure the call was wrong. Lower number = tap more freely. "
                              "'—' = never worth it.")
    x0, x1, ytop = 36, 36 + 30 + 46 * 6 + 40, H - 98
    for col, k in enumerate((2, 1)):
        for row, runners in enumerate((False, True)):
            sub = df[(df.runners == runners) & (df.k == k)]
            cells = {key: wmedian(g) for key, g in sub.groupby(["cnt", "group"], observed=True)}
            title = f"{'2 CHALLENGES LEFT' if k == 2 else 'LAST CHALLENGE'}  ·  {'runners on' if runners else 'bases empty'}"
            panel(cv, (x0, x1)[col], ytop - row * 232, title, cells)
    cv.setFont("Helvetica", 7.5)
    cv.setFillColor(MUTED)
    y = 40
    for n in notes:
        cv.drawString(36, y, n)
        y -= 10
    cv.showPage()


def main():
    con = duckdb.connect(str(DB), read_only=True)
    df = thresholds(con)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    cv = canvas.Canvas(str(OUT), pagesize=landscape(letter))
    cv.setTitle("Tap or Hold: ABS dugout card")
    cv.setAuthor("Austin Gentile")
    notes = ["Each cell: the model's break-even confidence, as a median over the game states that came up in 2026 close games, priced with "
             "this park's win-probability model and a season-replay value of the challenge.",
             "Built by Austin Gentile from public Statcast and MLB Stats API data. Independent analysis; not affiliated with MLB or "
             "the Colorado Rockies."]
    page(cv, df[df.coors], "COORS FIELD", notes)
    page(cv, df[~df.coors], "ON THE ROAD", notes)
    cv.save()
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
