"""Write everything the website reads into site/data/.

    python pipeline/export.py

- we.bin + we_meta.json   P(home win) for every state, league and Coors, as uint16 (value * 65535)
- model.json             RE288, the challenge-value (D_k) table, perception curves, constants
- card.json              break-even confidence for the dugout card grid
- story.json             every number and chart series quoted in the text
"""
import json
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
import values  # noqa: E402

DB = ROOT / "data" / "tapor.duckdb"
OUT = ROOT / "data" / "out"
SITE = ROOT / "site" / "data"
COUNTS = [(b, s) for b in range(4) for s in range(3)]
MARGIN_LO, MARGIN_HI = -15, 15


def r(x, n=4):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return None
    return round(float(x), n)


def clean(o):
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return r(o, 5)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def write(name, obj):
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / name).write_text(json.dumps(clean(obj), separators=(",", ":")))
    print(f"wrote site/data/{name} ({(SITE / name).stat().st_size / 1024:.0f} KB)")


def we_binary(con):
    SITE.mkdir(parents=True, exist_ok=True)
    g = con.execute("SELECT * FROM we_grid").df()
    arrs = {}
    for env in ("league", "coors"):
        e = g[g.env == env]
        a = np.zeros((10, 2, 8, 3, 4, 3, MARGIN_HI - MARGIN_LO + 1))
        a[e.inning - 1, (~e.is_top).astype(int), e.base_state, e.outs, e.balls, e.strikes, e.margin - MARGIN_LO] = e.we
        arrs[env] = a
    stacked = np.stack([arrs["league"], arrs["coors"]])
    (SITE / "we.bin").write_bytes(np.round(stacked * 65535).astype("<u2").tobytes())
    meta = {"shape": list(stacked.shape), "order": ["env(league,coors)", "inning-1 (1..10, 10 = extras)",
                                                    "half(top,bottom)", "bases(bitmask 1B=1,2B=2,3B=4)", "outs",
                                                    "balls", "strikes", "home margin + 15"],
            "margin_lo": MARGIN_LO, "scale": 65535}
    write("we_meta.json", meta)
    return {env: values.WE(g[g.env == env]) for env in ("league", "coors")}


def card(con, we, delta):
    """Break-even confidence on a grid the dugout card uses."""
    re = con.execute("SELECT * FROM re288").df()
    re = {(x.base_state, x.outs, x.balls, x.strikes): x.re for x in re.itertuples()}
    rows = []
    for env in ("league", "coors"):
        for inning in range(1, 11):
            for team_home in (0, 1):
                for batting in (True, False):
                    is_top = (team_home == 0) == batting           # the visitors bat in the top half
                    for margin in range(-4, 5):                    # team perspective
                        home_margin = margin if team_home else -margin
                        if not is_top and inning >= 9 and home_margin > 0:
                            continue                               # bottom 9th+ with the home team ahead: not played
                        for base in (0, 1, 2, 3, 4, 5, 6, 7):
                            for outs in (0, 1, 2):
                                for (b, s) in COUNTS:
                                    st = (inning, is_top, base, outs, b, s)
                                    pb = values.after_call(we[env], *st, home_margin, "B")
                                    ps = values.after_call(we[env], *st, home_margin, "S")
                                    v = abs(pb - ps)
                                    vr = abs(values.after_call_runs(re, base, outs, b, s, "B")
                                             - values.after_call_runs(re, base, outs, b, s, "S"))
                                    for k in (1, 2):
                                        d = delta[(min(inning, 10), team_home, max(-4, min(4, margin)), k)]
                                        rows.append((env, inning, team_home, int(batting), margin, base, outs, b, s, k,
                                                     v, vr, d, d / (v + d), 0.2 / (0.2 + vr)))
    df = pd.DataFrame(rows, columns=["env", "inning", "team_home", "batting", "margin", "base", "outs", "balls",
                                     "strikes", "k", "v_wp", "v_runs", "delta_wp", "p_star", "p_flat"])
    return df


def main():
    con = duckdb.connect(str(DB))
    we = we_binary(con)
    dt = con.execute("SELECT * FROM delta_table").df()
    delta = {(x.inning, x.team_home, x.margin, x.k): x.delta_wp for x in dt.itertuples()}
    cd = card(con, we, delta)
    con.register("card_df", cd)
    con.execute("CREATE OR REPLACE TABLE card AS SELECT * FROM card_df")

    re = con.execute("SELECT base_state, outs, balls, strikes, re FROM re288 ORDER BY ALL").df()
    pc = con.execute("SELECT * FROM perception_curves WHERE model = 'eyes_will' ORDER BY side, w_hat").df()
    per = json.loads((OUT / "perception.json").read_text())
    model = {
        "re288": [[int(x.base_state), int(x.outs), int(x.balls), int(x.strikes), r(x.re, 4)] for x in re.itertuples()],
        "delta": [[int(x.inning), int(x.team_home), int(x.margin), int(x.k), r(x.delta_wp, 5)] for x in dt.itertuples()],
        "belief": {s: {"w_hat": g.w_hat.round(2).tolist()[::4], "p": g.p_wrong.round(4).tolist()[::4]}
                   for s, g in pc.groupby("side")},
        "sigma": {s: per["sides"][s]["eyes_will"]["sigma"]["est"] for s in ("offense", "defense")},
        "flat_cost_runs": 0.2,
    }
    write("model.json", model)
    # the site computes every card cell in the browser from we.bin + model.json; spot-check values for its tests
    spots = cd[(cd.env == "league") & (cd.base.isin([0, 3])) & (cd.outs == 1) & (cd.margin.isin([-1, 0, 1]))]
    write("card_spotcheck.json", spots.sample(60, random_state=1).to_dict(orient="records"))


if __name__ == "__main__":
    main()
