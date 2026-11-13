"""League, Rockies, Coors Field, tapped-out and opponent analyses.

    python pipeline/analysis.py

Everything here is descriptive or a simple regression on the tables built earlier. Output: data/out/analysis.json
"""
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "tapor.duckdb"
OUT = ROOT / "data" / "out"
COL = 115


def wilson(k, n, z=1.96):
    if n == 0:
        return None, None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return float(c - h), float(c + h)


def rate(k, n):
    lo, hi = wilson(k, n)
    return {"k": int(k), "n": int(n), "rate": float(k / n) if n else None, "lo": lo, "hi": hi}


def league(con):
    q = lambda s: con.execute(s).df()
    base = q("""
        SELECT count(*) AS called, sum(call_wrong::INT) AS missed,
               sum(challenged::INT) AS challenges, sum((challenged AND overturned)::INT) AS overturns,
               sum(CASE WHEN call_wrong THEN v_wp_env ELSE 0 END) AS wp_missed,
               sum(CASE WHEN challenged AND overturned THEN v_wp_env ELSE 0 END) AS wp_fixed,
               count(DISTINCT game_pk) AS games
        FROM calls""").iloc[0]
    teams = 30
    out = {k: float(v) for k, v in base.items()}
    out["missed_per_game"] = out["missed"] / out["games"]
    out["challenges_per_game"] = out["challenges"] / out["games"]
    out["wp_missed_per_team_season"] = out["wp_missed"] / teams
    out["wp_fixed_per_team_season"] = out["wp_fixed"] / teams
    out["share_fixed"] = out["wp_fixed"] / out["wp_missed"]
    # concentration: how much of the value of missed calls sits in the top 10% most valuable misses
    v = q("SELECT v_wp_env AS v FROM calls WHERE call_wrong ORDER BY v DESC").v.values
    out["top10pct_share_of_missed_value"] = float(v[: len(v) // 10].sum() / v.sum())
    out["median_missed_call_wp"] = float(np.median(v))
    # by side
    sides = q("""
        SELECT wronged_side, sum(call_wrong::INT) AS missed, sum(challenged::INT) AS challenges,
               sum((challenged AND overturned)::INT) AS overturns,
               sum(CASE WHEN call_wrong THEN v_wp_env ELSE 0 END) AS wp_missed,
               sum(CASE WHEN challenged AND overturned THEN v_wp_env ELSE 0 END) AS wp_fixed
        FROM calls GROUP BY ALL""")
    out["by_side"] = sides.to_dict(orient="records")
    # where the misses happen: by count
    bc = q("""
        SELECT balls || '-' || strikes AS count, count(*) AS called, avg(call_wrong::INT) AS miss_rate,
               avg(v_wp_env) AS mean_v_wp, avg(abs(v_runs)) AS mean_v_runs,
               sum(challenged::INT) AS challenges, avg(CASE WHEN challenged THEN call_wrong::INT END) AS overturn_rate
        FROM calls GROUP BY ALL ORDER BY count""")
    out["by_count"] = bc.to_dict(orient="records")
    # how often each club runs out, by inning (share of pitches thrown with the batting club at zero)
    tap = q("""
        SELECT least(inning, 10) AS inning, avg((s.bat_ch_left = 0)::INT) AS bat_out,
               avg((s.fld_ch_left = 0)::INT) AS fld_out
        FROM pitches p JOIN pitch_state s USING (game_pk, at_bat_number, pitch_number)
        GROUP BY ALL ORDER BY inning""")
    out["tapped_out_by_inning"] = tap.to_dict(orient="records")
    return out


def stakes(con):
    """Tap rate on clear misses vs the stakes, actual players (data) — the card's answer comes from export."""
    d = con.execute("""
        SELECT wronged_side, v_wp_env AS v, challenged, w_in FROM calls
        WHERE opportunity AND call_wrong AND w_in BETWEEN 1 AND 4""").df()
    d["decile"] = pd.qcut(d.v.rank(method="first"), 10, labels=False)
    g = d.groupby("decile").agg(v_median=("v", "median"), tap_rate=("challenged", "mean"), n=("v", "size")).reset_index()
    lo, hi = g.iloc[0], g.iloc[-1]
    return {"deciles": g.to_dict(orient="records"),
            "stakes_ratio_top_vs_bottom": float(hi.v_median / lo.v_median),
            "tap_rate_bottom": float(lo.tap_rate), "tap_rate_top": float(hi.tap_rate)}


def rockies(con):
    q = lambda s: con.execute(s).df()
    out = {}
    t = q("""
        SELECT wronged_team_id = 115 AS rockies, wronged_side,
               sum(challenged::INT) AS challenges, sum((challenged AND overturned)::INT) AS overturns,
               sum(call_wrong::INT) AS missed,
               sum(CASE WHEN call_wrong AND opportunity THEN 1 ELSE 0 END) AS missed_with_challenge_left,
               sum(CASE WHEN call_wrong THEN v_wp_env ELSE 0 END) AS wp_missed,
               sum(CASE WHEN challenged AND overturned THEN v_wp_env ELSE 0 END) AS wp_fixed,
               count(DISTINCT game_pk) AS games
        FROM calls WHERE wronged_team_id IS NOT NULL GROUP BY ALL""")
    t["per_team"] = np.where(t.rockies, 1, 29)
    out["vs_league"] = t.to_dict(orient="records")
    # Rockies players who tapped
    pl = q("""
        WITH ch AS (
            SELECT c.*, CASE WHEN c.challenger_id = c.batter THEN 'hitter'
                             WHEN c.challenger_id = c.catcher THEN 'catcher'
                             WHEN c.challenger_id = c.pitcher THEN 'pitcher' ELSE 'other' END AS role
            FROM calls c WHERE c.challenged AND c.challenge_team_id = 115)
        SELECT challenger_id AS player_id, p.name, role, count(*) AS challenges, sum(overturned::INT) AS overturns,
               sum(CASE WHEN overturned THEN v_wp_env ELSE 0 END) AS wp_gained,
               avg(abs_margin_in) AS mean_margin
        FROM ch LEFT JOIN players p ON p.player_id = ch.challenger_id
        GROUP BY ALL ORDER BY challenges DESC""")
    out["challengers"] = pl.to_dict(orient="records")
    # Rockies hitters: clear misses they let go (strike called on a pitch 1+ in. off the zone, challenge in hand)
    hit = q("""
        SELECT c.batter AS player_id, p.name, count(*) AS clear_misses, sum(c.challenged::INT) AS tapped,
               sum(c.v_wp_env) AS wp_at_stake
        FROM calls c LEFT JOIN players p ON p.player_id = c.batter
        WHERE c.wronged_team_id = 115 AND c.wronged_side = 'offense' AND c.opportunity AND c.w_in > 1
        GROUP BY ALL HAVING count(*) >= 5 ORDER BY wp_at_stake DESC""")
    out["hitters_clear_misses"] = hit.to_dict(orient="records")
    # the ones that got away: most valuable missed calls against the Rockies that were not challenged
    got = q("""
        SELECT c.game_date, c.inning, c.is_top, c.balls, c.strikes, c.outs, c.base_state,
               c.home_score, c.away_score, c.home_team, c.away_team, c.orig_call, c.w_in, c.v_wp_env AS v,
               c.wronged_ch_left, b.name AS batter, pt.name AS pitcher, ct.name AS catcher, c.wronged_side
        FROM calls c
        LEFT JOIN players b ON b.player_id = c.batter LEFT JOIN players pt ON pt.player_id = c.pitcher
        LEFT JOIN players ct ON ct.player_id = c.catcher
        WHERE c.wronged_team_id = 115 AND c.call_wrong AND NOT c.challenged
        ORDER BY c.v_wp_env DESC LIMIT 12""")
    got["game_date"] = got.game_date.astype(str)
    out["got_away"] = got.to_dict(orient="records")
    best = q("""
        SELECT c.game_date, c.inning, c.is_top, c.balls, c.strikes, c.outs, c.base_state, c.home_score, c.away_score,
               c.home_team, c.away_team, c.orig_call, c.w_in, c.v_wp_env AS v, ch.name AS challenger, c.overturned
        FROM calls c LEFT JOIN players ch ON ch.player_id = c.challenger_id
        WHERE c.challenge_team_id = 115 AND c.challenged ORDER BY c.v_wp_env DESC LIMIT 8""")
    best["game_date"] = best.game_date.astype(str)
    out["biggest_taps"] = best.to_dict(orient="records")
    return out


def coors(con):
    q = lambda s: con.execute(s).df()
    out = {}
    m = q("""
        SELECT coors, wronged_side, count(*) AS called, sum(call_wrong::INT) AS missed
        FROM calls GROUP BY ALL ORDER BY ALL""")
    out["miss_rates"] = [{**r, **rate(r["missed"], r["called"])} for r in m.to_dict(orient="records")]
    # challenge success: visitors at Coors vs visitors elsewhere; Rockies at home vs road
    s = q("""
        SELECT CASE WHEN coors AND challenge_team_id <> 115 THEN 'visitors at Coors'
                    WHEN coors THEN 'Rockies at Coors'
                    WHEN challenge_team_id = 115 THEN 'Rockies on the road'
                    ELSE 'everyone else, elsewhere' END AS grp,
               count(*) AS challenges, sum(overturned::INT) AS overturns,
               avg(abs(abs_margin_in)) AS mean_dist
        FROM calls WHERE challenged GROUP BY ALL""")
    out["challenge_success"] = [{**r, **rate(r["overturns"], r["challenges"])} for r in s.to_dict(orient="records")]
    # value of the same call at Coors vs elsewhere, by count (runs: league RE288 has no park term, so WP only)
    v = q("""
        SELECT balls || '-' || strikes AS count, avg(v_wp) AS v_league, avg(v_wp_coors) AS v_coors
        FROM calls GROUP BY ALL ORDER BY count""")
    out["value_by_count"] = v.to_dict(orient="records")
    # pitch movement at altitude (context): average induced vertical break of four-seamers, Coors vs elsewhere
    mv = q("""
        SELECT g.venue_id = 19 AS coors, avg(p.pfx_z * 12) AS ivb_in, avg(abs(p.pfx_x) * 12) AS hb_in, count(*) AS n
        FROM pitches p JOIN games g USING (game_pk) WHERE p.pitch_type = 'FF' GROUP BY ALL""")
    out["fourseam_movement"] = mv.to_dict(orient="records")
    return out


def tapped_out(con):
    """Does the game change when the batting club has no challenges left?"""
    q = lambda s: con.execute(s).df()
    out = {}
    # (1) umpires: strike calls on pitches outside the ABS zone, actual vs expected from location alone
    u = q("""
        SELECT c.bat_ch_left = 0 AS bat_out, c.fld_ch_left = 0 AS fld_out, c.abs_strike, c.orig_call, u.p_ump
        FROM calls c JOIN calls_ump u USING (game_pk, at_bat_number, pitch_number)
        WHERE u.p_ump IS NOT NULL AND NOT c.position_player_pitching""")
    rows = []
    for out_flag, g in u[~u.abs_strike].groupby("bat_out"):
        rows.append({"batting_club_out": bool(out_flag), "n": int(len(g)),
                     "strike_rate": float((g.orig_call == "S").mean()), "expected": float(g.p_ump.mean())})
    out["ump_strikes_outside_zone"] = rows
    rows = []
    for out_flag, g in u[u.abs_strike].groupby("fld_out"):
        rows.append({"fielding_club_out": bool(out_flag), "n": int(len(g)),
                     "ball_rate": float((g.orig_call == "B").mean()), "expected": float(1 - g.p_ump.mean())})
    out["ump_balls_inside_zone"] = rows
    # (2) pitchers: do they throw more to the umpire's 'gift band' (outside ABS, umpire strike prob >= 0.5)
    #     when the batting club is out? Linear probability model with pitcher fixed effects, count and inning dummies.
    d = q("""
        SELECT p.pitcher, p.game_pk, p.balls || '-' || p.strikes AS cnt, least(p.inning, 10) AS inn,
               (s.bat_ch_left = 0)::INT AS bat_out,
               ((p.plate_x IS NOT NULL) AND (coalesce(m.p_ump, 0) >= 0.5)
                 AND (12 * CASE WHEN abs(p.plate_x) - 8.5/12 <= 0 AND p.plate_z <= p.sz_top AND p.plate_z >= p.sz_bot
                           THEN -1 ELSE sqrt(power(greatest(abs(p.plate_x) - 8.5/12, 0), 2)
                                + power(greatest(p.plate_z - p.sz_top, p.sz_bot - p.plate_z, 0), 2)) END) > 1.45)::INT AS gift
        FROM pitches p JOIN pitch_state s USING (game_pk, at_bat_number, pitch_number)
        LEFT JOIN ump_map m ON m.stand = p.stand
             AND m.ix = floor((p.plate_x * 12 + 20) / 0.5)::INT
             AND m.iz = floor(((p.plate_z - p.sz_bot) / (p.sz_top - p.sz_bot) + 0.6) / 0.025)::INT
        WHERE p.plate_x IS NOT NULL""")
    out["gift_band_share"] = {"batting_club_out": float(d[d.bat_out == 1].gift.mean()),
                              "batting_club_has_one": float(d[d.bat_out == 0].gift.mean()),
                              "n_out": int((d.bat_out == 1).sum()), "n_has": int((d.bat_out == 0).sum())}
    X = pd.get_dummies(d[["cnt", "inn"]].astype(str), drop_first=True).astype(float)
    X.insert(0, "bat_out", d.bat_out.astype(float))
    y = d.gift.astype(float)
    # absorb pitcher fixed effects by demeaning within pitcher
    grp = d.pitcher
    Xd = X - X.groupby(grp).transform("mean")
    yd = y - y.groupby(grp).transform("mean")
    beta, *_ = np.linalg.lstsq(Xd.values, yd.values, rcond=None)
    resid = yd.values - Xd.values @ beta
    # cluster-robust (by game) standard error for the bat_out coefficient
    XtX_inv = np.linalg.pinv(Xd.values.T @ Xd.values)
    meat = np.zeros_like(XtX_inv)
    for _, idx in pd.Series(np.arange(len(d))).groupby(d.game_pk.values):
        s = Xd.values[idx.values].T @ resid[idx.values]
        meat += np.outer(s, s)
    V = XtX_inv @ meat @ XtX_inv
    out["gift_band_regression"] = {"coef_bat_out": float(beta[0]), "se": float(np.sqrt(V[0, 0])),
                                   "baseline_share": float(y.mean()), "n": int(len(d))}
    return out


def opponents(con):
    q = lambda s: con.execute(s).df()
    t = q("""
        WITH g AS (SELECT count(DISTINCT game_pk) AS n, team FROM (
                     SELECT game_pk, home_id AS team FROM games UNION ALL SELECT game_pk, away_id FROM games) GROUP BY ALL),
        c AS (
            SELECT wronged_team_id AS team, wronged_side,
                   sum(challenged::INT) AS challenges, sum((challenged AND overturned)::INT) AS overturns,
                   sum(call_wrong::INT) AS missed,
                   sum((call_wrong AND opportunity)::INT) AS missed_with_left,
                   sum((call_wrong AND challenged)::INT) AS missed_fixed,
                   sum(CASE WHEN challenged AND overturned THEN v_wp_env ELSE 0 END) AS wp_fixed,
                   sum(CASE WHEN call_wrong THEN v_wp_env ELSE 0 END) AS wp_missed
            FROM calls WHERE wronged_team_id IS NOT NULL GROUP BY ALL)
        SELECT c.*, g.n AS games, tm.abbr, tm.team AS team_name, tm.division
        FROM c JOIN g USING (team) JOIN teams tm ON tm.team_id = c.team""")
    late = q("""
        SELECT p.bat_team_id AS team, avg((s.bat_ch_left = 0)::INT) AS out_late
        FROM pitches p JOIN pitch_state s USING (game_pk, at_bat_number, pitch_number)
        WHERE p.inning >= 7 GROUP BY ALL""")
    who = q("""
        SELECT c.challenge_team_id AS team, c.challenger_id AS player_id, p.name,
               CASE WHEN c.challenger_id = c.batter THEN 'hitter' WHEN c.challenger_id = c.catcher THEN 'catcher'
                    WHEN c.challenger_id = c.pitcher THEN 'pitcher' ELSE 'other' END AS role,
               count(*) AS challenges, sum(c.overturned::INT) AS overturns
        FROM calls c LEFT JOIN players p ON p.player_id = c.challenger_id
        WHERE c.challenged GROUP BY ALL""")
    # the counts each club taps in most (share of its challenges)
    cnt = q("""
        SELECT challenge_team_id AS team, balls || '-' || strikes AS count, count(*) AS n
        FROM calls WHERE challenged GROUP BY ALL""")
    cards = []
    for team, g in t.groupby("team"):
        r = {"team_id": int(team), "abbr": g.abbr.iloc[0], "name": g.team_name.iloc[0], "division": g.division.iloc[0],
             "games": int(g.games.iloc[0])}
        for side in ("offense", "defense"):
            s = g[g.wronged_side == side].iloc[0]
            r[side] = {"challenges": int(s.challenges), "per_game": float(s.challenges / s.games),
                       "overturn_rate": float(s.overturns / s.challenges) if s.challenges else None,
                       "missed": int(s.missed), "share_missed_fixed": float(s.missed_fixed / s.missed),
                       "wp_fixed": float(s.wp_fixed), "wp_missed": float(s.wp_missed)}
        lt = late[late.team == team]
        r["tapped_out_7th_on"] = float(lt.out_late.iloc[0]) if len(lt) else None
        w = who[who.team == team].sort_values("challenges", ascending=False)
        r["top_challengers"] = w.head(5)[["name", "role", "challenges", "overturns"]].to_dict(orient="records")
        roles = w.groupby("role").agg(c=("challenges", "sum"), o=("overturns", "sum"))
        r["by_role"] = {k: {"challenges": int(v.c), "overturns": int(v.o)} for k, v in roles.iterrows()}
        cc = cnt[cnt.team == team].sort_values("n", ascending=False)
        r["top_counts"] = cc.head(3)[["count", "n"]].to_dict(orient="records")
        cards.append(r)
    return cards


def notable(con):
    q = lambda s: con.execute(s).df()
    biggest = q("""
        SELECT c.game_date, c.inning, c.is_top, c.balls, c.strikes, c.outs, c.base_state, c.home_score, c.away_score,
               c.home_team, c.away_team, c.orig_call, c.w_in, c.v_wp_env AS v, ch.name AS challenger, c.overturned,
               b.name AS batter, pt.name AS pitcher
        FROM calls c LEFT JOIN players ch ON ch.player_id = c.challenger_id
        LEFT JOIN players b ON b.player_id = c.batter LEFT JOIN players pt ON pt.player_id = c.pitcher
        WHERE c.challenged AND c.overturned ORDER BY c.v_wp_env DESC LIMIT 10""")
    missed = q("""
        SELECT c.game_date, c.inning, c.is_top, c.balls, c.strikes, c.outs, c.base_state, c.home_score, c.away_score,
               c.home_team, c.away_team, c.orig_call, c.w_in, c.v_wp_env AS v, c.wronged_ch_left,
               b.name AS batter, pt.name AS pitcher
        FROM calls c LEFT JOIN players b ON b.player_id = c.batter LEFT JOIN players pt ON pt.player_id = c.pitcher
        WHERE c.call_wrong AND NOT c.challenged AND c.opportunity ORDER BY c.v_wp_env DESC LIMIT 10""")
    for df in (biggest, missed):
        df["game_date"] = df.game_date.astype(str)
    return {"biggest_overturns": biggest.to_dict(orient="records"),
            "biggest_untapped_misses": missed.to_dict(orient="records")}


def main():
    con = duckdb.connect(str(DB))
    res = {"league": league(con), "stakes": stakes(con), "rockies": rockies(con), "coors": coors(con),
           "tapped_out": tapped_out(con), "opponents": opponents(con), "notable": notable(con)}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "analysis.json").write_text(json.dumps(res, indent=1, default=str))
    show = {k: res[k] for k in ("league", "stakes", "coors", "tapped_out")}
    for k in ("by_count", "tapped_out_by_inning", "by_side"):
        show["league"].pop(k, None)
    show["stakes"].pop("deciles", None)
    print(json.dumps(show, indent=1, default=str)[:7000])


if __name__ == "__main__":
    main()
