"""Rebuild the ABS verdict for every called pitch, check it against real challenge outcomes, and map the umpires' zone.

    python pipeline/zone.py

ABS calls a strike when any part of the ball touches the batter's rectangle (17 in wide, 27%-53.5% of height).
We do not know the exact plane or ball radius ABS uses, so both are tested against the ~6,000 challenges whose
outcome we know: the published plate coordinates with the radius that best reproduces the verdicts win clearly.

Outputs: tables calls (one row per called pitch with ABS verdict and how wrong the call was) and ump_map;
data/out/zone_checks.json.
"""
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy.ndimage import gaussian_filter

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "tapor.duckdb"
OUT = ROOT / "data" / "out"


def agreement(df, edge_col, r):
    abs_ball = df[edge_col] - r > 0
    pred = ((df.orig_call == "S") & abs_ball) | ((df.orig_call == "B") & ~abs_ball)
    return float((pred == df.overturned).mean()), int((pred != df.overturned).sum())


def main():
    con = duckdb.connect(str(DB))
    ch = con.execute("""SELECT orig_call, overturned, edge_mid_in, edge_front_in
                        FROM called WHERE challenged AND edge_front_in IS NOT NULL""").df()
    radii = np.round(np.arange(1.30, 1.6001, 0.005), 3)
    scan = {col: [agreement(ch, col, r)[0] for r in radii] for col in ("edge_front_in", "edge_mid_in")}
    best_col = max(scan, key=lambda c: max(scan[c]))
    best_r = float(radii[int(np.argmax(scan[best_col]))])
    # where several radii tie, take the middle of the tied range
    tied = radii[np.array(scan[best_col]) == max(scan[best_col])]
    best_r = float(np.round(np.median(tied), 3))
    acc, miss = agreement(ch, best_col, best_r)
    checks = {
        "challenges_used": int(len(ch)),
        "plane": "published plate coordinates (front of plate)" if best_col == "edge_front_in" else "mid-plate",
        "ball_radius_in": best_r, "agreement": acc, "mismatches": miss,
        "radius_scan": {"radii": radii.tolist(), "front": scan["edge_front_in"], "mid": scan["edge_mid_in"]},
        "variants": {
            "front, no radius": agreement(ch, "edge_front_in", 0.0)[0],
            "mid-plate, no radius": agreement(ch, "edge_mid_in", 0.0)[0],
            "mid-plate, best radius": max(scan["edge_mid_in"]),
            "front, best radius": acc},
    }

    # every called pitch: ABS verdict and signed 'wrongness' w in inches (w > 0: the umpire missed it by w)
    con.execute(f"""
        CREATE OR REPLACE TABLE calls AS
        SELECT c.*, v.v_wp, v.v_wp_coors, v.v_wp_env, v.v_runs, v.we_home,
               c.{best_col} - {best_r} AS abs_margin_in,
               c.{best_col} - {best_r} <= 0 AS abs_strike,
               CASE c.orig_call WHEN 'S' THEN c.{best_col} - {best_r} ELSE {best_r} - c.{best_col} END AS w_in,
               g.venue_id = 19 AS coors, g.home_id, g.away_id
        FROM called c
        JOIN called_values v USING (game_pk, at_bat_number, pitch_number)
        JOIN games g USING (game_pk)
        WHERE c.{best_col} IS NOT NULL
    """)
    con.execute("ALTER TABLE calls ADD COLUMN call_wrong BOOLEAN; UPDATE calls SET call_wrong = w_in > 0;")
    # mismatch detail: how close to the edge are the challenges we get wrong?
    mm = con.execute(f"""
        SELECT abs(abs_margin_in) AS dist FROM calls
        WHERE challenged AND ((call_wrong AND NOT overturned) OR (NOT call_wrong AND overturned))""").df()
    checks["mismatch_max_dist_in"] = float(mm.dist.max()) if len(mm) else 0.0
    checks["mismatch_median_dist_in"] = float(mm.dist.median()) if len(mm) else 0.0
    summary = con.execute("""
        SELECT count(*) AS called, avg(call_wrong::INT) AS miss_rate,
               avg(CASE WHEN orig_call = 'S' THEN call_wrong::INT END) AS miss_rate_strikes,
               avg(CASE WHEN orig_call = 'B' THEN call_wrong::INT END) AS miss_rate_balls,
               sum(call_wrong::INT) AS missed_calls
        FROM calls""").df().iloc[0].to_dict()
    checks["umpires"] = {k: float(v) for k, v in summary.items()}

    # the umpires' zone: P(umpire calls strike) by location, catcher's view, height normalised to the ABS zone
    d = con.execute("""
        SELECT plate_x * 12 AS x_in, (plate_z - sz_bot) / (sz_top - sz_bot) AS zn, stand,
               (orig_call = 'S')::INT AS ump_strike, abs_strike::INT AS abs_strike
        FROM calls WHERE plate_x IS NOT NULL""").df()
    xe = np.arange(-20, 20.01, 0.5)
    ze = np.arange(-0.6, 1.6001, 0.025)
    rows = []
    for stand in ("R", "L"):
        s = d[d.stand == stand]
        n, _, _ = np.histogram2d(s.x_in, s.zn, bins=[xe, ze])
        k, _, _ = np.histogram2d(s.x_in, s.zn, bins=[xe, ze], weights=s.ump_strike)
        a, _, _ = np.histogram2d(s.x_in, s.zn, bins=[xe, ze], weights=s.abs_strike)
        sig = (1.0, 1.2)                               # about 0.5 in sideways, 0.03 of the zone height vertically
        ns, ks, as_ = gaussian_filter(n, sig), gaussian_filter(k, sig), gaussian_filter(a, sig)
        p_ump = np.where(ns > 3, ks / np.maximum(ns, 1e-9), np.nan)
        p_abs = np.where(ns > 3, as_ / np.maximum(ns, 1e-9), np.nan)
        xc, zc = (xe[:-1] + xe[1:]) / 2, (ze[:-1] + ze[1:]) / 2
        for i, x in enumerate(xc):
            for j, z in enumerate(zc):
                rows.append((stand, i, j, round(float(x), 2), round(float(z), 4), float(n[i, j]), p_ump[i, j],
                             p_abs[i, j]))
    um = pd.DataFrame(rows, columns=["stand", "ix", "iz", "x_in", "zn", "n", "p_ump", "p_abs"])
    con.register("um_df", um)
    con.execute("CREATE OR REPLACE TABLE ump_map AS SELECT * FROM um_df")

    # attach each called pitch's smoothed umpire strike probability (for 'expected call' comparisons)
    con.execute("""
        CREATE OR REPLACE TABLE calls_ump AS
        SELECT c.game_pk, c.at_bat_number, c.pitch_number, m.p_ump
        FROM calls c
        JOIN ump_map m ON m.stand = c.stand
             AND m.ix = floor((c.plate_x * 12 + 20) / 0.5)::INT
             AND m.iz = floor(((c.plate_z - c.sz_bot) / (c.sz_top - c.sz_bot) + 0.6) / 0.025)::INT
    """)
    checks["ump_map_join_rate"] = con.execute(
        "SELECT (SELECT count(*) FROM calls_ump WHERE p_ump IS NOT NULL) / (SELECT count(*) FROM calls)").fetchone()[0]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "zone_checks.json").write_text(json.dumps(checks, indent=1))
    print(json.dumps({k: v for k, v in checks.items() if k != "radius_scan"}, indent=1))


if __name__ == "__main__":
    main()
