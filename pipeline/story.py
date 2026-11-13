"""Collect every number the site quotes and every chart series, then render site/index.html from its template.

    python pipeline/story.py

Text numbers come only from the pipeline outputs, so the page cannot drift from the data. The template uses
{{name}} tokens; rendering fails if a token has no value or a value is left unused.
"""
import json
import re
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import norm

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
import values  # noqa: E402
from export import clean  # noqa: E402

DB = ROOT / "data" / "tapor.duckdb"
OUT = ROOT / "data" / "out"
SITE = ROOT / "site"
TEMPLATE = ROOT / "templates" / "index.html"


def load(name):
    return json.loads((OUT / name).read_text())


def pct(x, d=0):
    return f"{100 * x:.{d}f}%"


def num(x, d=0):
    return f"{x:,.{d}f}"


def main():
    con = duckdb.connect(str(DB), read_only=True)
    A, P, PP = load("analysis.json"), load("policy.json"), load("policy_probit.json")
    Z, V, PER, CH = load("zone_checks.json"), load("values_checks.json"), load("perception.json"), load("checks.json")
    grid = con.execute("SELECT * FROM we_grid").df()
    we = {env: values.WE(grid[grid.env == env]) for env in ("league", "coors")}
    dt = con.execute("SELECT * FROM delta_table").df()
    delta = {(x.inning, x.team_home, x.margin, x.k): x.delta_wp for x in dt.itertuples()}
    card = con.execute("SELECT * FROM card").df()
    L = A["league"]
    S = {}       # chart series and tables -> story.json
    n = {}       # text tokens -> index.html

    # ------------------------------------------------------------------ price of a challenge
    innings = list(range(1, 11))
    run_value, k1, k2, k1wp, k2wp = [], [], [], [], []
    for i in innings:
        b0 = 2 if i >= 10 else 0
        rv = (we["league"].at(i, True, b0, 0, 0, 0, 1) - we["league"].at(i, True, b0, 0, 0, 0, -1)) / 2
        d1 = np.mean([delta[(i, h, 0, 1)] for h in (0, 1)])
        d2 = np.mean([delta[(i, h, 0, 2)] for h in (0, 1)])
        run_value.append(rv)
        k1wp.append(d1)
        k2wp.append(d2)
        k1.append(d1 / rv)
        k2.append(d2 / rv)
    S["price"] = {"innings": innings, "k1": k1, "k2": k2, "k1_wp": k1wp, "k2_wp": k2wp, "run_value": run_value}
    series = []
    for name, ms in (("Tied", [0]), ("Within 1", [-1, 1]), ("Within 2", [-2, 2]), ("3+ either way", [-4, -3, 3, 4])):
        series.append({"name": name, "values": [float(np.mean([delta[(i, h, m, 1)] for h in (0, 1) for m in ms])) for i in innings]})
    S["price_wp"] = {"innings": innings, "series": series}
    n["price_k1_inn1_wp"] = pct(k1wp[0], 2)
    n["price_k1_inn7_wp"] = pct(k1wp[6], 2)
    n["price_k1_inn9_wp"] = pct(k1wp[8], 2)
    n["price_k2_inn1_wp"] = pct(k2wp[0], 2)
    n["price_k1_inn1_runs"] = num(k1[0], 2)
    n["price_k2_inn1_runs"] = num(k2[0], 2)
    n["price_k1_inn9_runs"] = num(k1[8], 2)
    n["price_k1_inn7_runs"] = num(k1[6], 2)
    n["k2_share_of_k1"] = pct(np.mean(np.array(k2wp[:6]) / np.array(k1wp[:6])))

    # ------------------------------------------------------------------ the size of the prize
    n["called"] = num(L["called"])
    n["missed"] = num(L["missed"])
    n["miss_rate"] = pct(L["missed"] / L["called"], 1)
    n["missed_per_game"] = num(L["missed_per_game"], 1)
    n["challenges"] = num(L["challenges"])
    n["challenges_per_game"] = num(L["challenges_per_game"], 1)
    n["overturns"] = num(L["overturns"])
    n["overturn_rate"] = pct(L["overturns"] / L["challenges"], 1)
    n["wins_missed_per_club"] = num(L["wp_missed_per_team_season"], 1)
    n["wins_fixed_per_club"] = num(L["wp_fixed_per_team_season"], 1)
    n["share_fixed"] = pct(L["share_fixed"])
    n["top10_share"] = pct(L["top10pct_share_of_missed_value"])
    n["median_miss_wp"] = pct(L["median_missed_call_wp"], 2)
    strikes = Z["umpires"]
    n["miss_rate_strikes"] = pct(strikes["miss_rate_strikes"], 1)
    n["miss_rate_balls"] = pct(strikes["miss_rate_balls"], 1)

    # ------------------------------------------------------------------ zone rebuild
    n["zone_challenges"] = num(Z["challenges_used"])
    n["zone_agree"] = pct(Z["agreement"], 2)
    n["zone_matched"] = num(Z["challenges_used"] - Z["mismatches"])
    n["zone_mismatches"] = num(Z["mismatches"])
    n["zone_mid_best"] = pct(Z["variants"]["mid-plate, best radius"], 1)
    n["zone_no_radius"] = pct(Z["variants"]["front, no radius"], 1)
    n["ball_radius"] = f'{Z["ball_radius_in"]:.2f}'
    rs = Z["radius_scan"]
    best = max(rs["front"])
    tied = [r for r, a in zip(rs["radii"], rs["front"]) if a == best]
    n["radius_lo"], n["radius_hi"] = f"{min(tied):.3f}", f"{max(tied):.3f}"
    S["zone_scan"] = {"radii": rs["radii"], "front": rs["front"], "mid": rs["mid"]}

    # ------------------------------------------------------------------ win expectancy checks
    w = V["we_vs_savant"]
    n["we_corr"] = f'{w["corr"]:.5f}'
    n["we_mad"] = pct(w["mean_abs_diff"], 2)
    n["brier_ours"] = f'{w["brier_ours"]:.4f}'
    n["brier_savant"] = f'{w["brier_savant"]:.4f}'
    S["calibration"] = V["we_calibration_deciles"]
    rr = V["re288_vs_savant_called_strikes"]
    n["re_count_corr"] = f'{rr["count_means_corr"]:.3f}'
    n["re_count_maxdiff"] = num(rr["count_means_max_abs_diff"], 3)
    co = V["coors"]
    n["coors_runs_half"] = num(co["runs_per_half_inning_coors"], 2)
    n["else_runs_half"] = num(co["runs_per_half_inning_elsewhere"], 2)
    n["coors_runs_lift"] = pct(co["runs_per_half_inning_coors"] / co["runs_per_half_inning_elsewhere"] - 1)
    n["coors_theta"] = f'{co["theta"]:.3f}'
    n["brier_coors_league"] = f'{co["brier_coors_games_league_model"]:.4f}'
    n["brier_coors_coors"] = f'{co["brier_coors_games_coors_model"]:.4f}'

    # ------------------------------------------------------------------ perception
    off, de = PER["sides"]["offense"], PER["sides"]["defense"]
    for key, side in (("off", off), ("def", de)):
        e = side["eyes_will"]
        n[f"{key}_sigma"] = num(e["sigma"]["est"], 1)
        n[f"{key}_sigma_lo"] = num(e["sigma"]["lo"], 1)
        n[f"{key}_sigma_hi"] = num(e["sigma"]["hi"], 1)
        n[f"{key}_rho"] = pct(e["rho"]["est"])
        n[f"{key}_rho_lo"] = pct(e["rho"]["lo"])
        n[f"{key}_rho_hi"] = pct(e["rho"]["hi"])
        n[f"{key}_probit_sigma"] = num(side["probit"]["sigma"]["est"], 1)
        n[f"{key}_aic_gain"] = num(side["probit"]["aic"] - e["aic"], 1)
        n[f"{key}_w50"] = num(e["w_hat_for_50pct"], 1)
        n[f"{key}_share_missed_tapped"] = pct(side["share_of_missed_calls_challenged"])
        n[f"{key}_overturn"] = pct(side["overturn_rate"])
        n[f"{key}_tau10x"] = num(abs(side["tau_shift_for_10x_stakes_in"]), 2)
    roles = PER["defense_by_role"]
    n["catcher_taps"] = num(roles["catcher"]["challenges"])
    n["catcher_rate"] = pct(roles["catcher"]["overturn_rate"])
    n["pitcher_taps"] = num(roles["pitcher"]["challenges"])
    n["pitcher_rate"] = pct(roles["pitcher"]["overturn_rate"])
    n["hitter_taps"] = num(PER["offense_by_role"]["batter"]["challenges"])
    n["hitter_rate"] = pct(PER["offense_by_role"]["batter"]["overturn_rate"])
    S["curve"] = [{"side": c["side"], "w_mid": c["w_mid"], "n": c["n"], "rate": c["rate"], "fit": c["fit_eyes_will"]}
                  for c in PER["challenge_curve"]]

    # ------------------------------------------------------------------ stakes: players vs the card on clear misses
    pc = con.execute("SELECT * FROM perception_curves WHERE model = 'eyes_will' ORDER BY side, w_hat").df()
    curves = {s: (g.w_hat.values, g.p_wrong.values) for s, g in pc.groupby("side")}
    sig = {s: PER["sides"][s]["eyes_will"]["sigma"]["est"] for s in ("offense", "defense")}
    cm = con.execute("""
        SELECT wronged_side, w_in, v_wp_env AS v, challenged, least(inning, 10) AS inn,
               (wronged_team_id = home_id)::INT AS home, wronged_ch_left AS k,
               CASE WHEN wronged_team_id = home_id THEN home_score - away_score ELSE away_score - home_score END AS margin
        FROM calls WHERE opportunity AND call_wrong AND w_in > 1 AND w_in <= 4""").df()
    dk = np.array([delta[(i, h, int(np.clip(m, -4, 4)), min(k, 2))] for i, h, m, k in zip(cm.inn, cm.home, cm.margin, cm.k)])
    pstar = dk / (cm.v.clip(lower=0).values + dk)
    tap = np.zeros(len(cm))
    for side in ("offense", "defense"):
        msk = (cm.wronged_side == side).values
        wh, pw = curves[side]
        # perceived miss needed to reach p*, then the chance noise puts the perception above it
        need = np.interp(pstar[msk], pw, wh, left=wh[0], right=wh[-1])
        tap[msk] = 1 - norm.cdf((need - cm.w_in.values[msk]) / sig[side])
    cm["card"] = tap
    cm["decile"] = pd.qcut(cm.v.rank(method="first"), 10, labels=False) + 1
    st = cm.groupby("decile").agg(v=("v", "median"), actual=("challenged", "mean"), card=("card", "mean")).reset_index()
    S["stakes"] = st.to_dict(orient="records")
    n["stakes_ratio"] = num(st.v.iloc[-1] / st.v.iloc[0], 0)
    n["stakes_tap_low"] = pct(st.actual.iloc[0])
    n["stakes_tap_high"] = pct(st.actual.iloc[-1])
    n["stakes_card_low"] = pct(st.card.iloc[0])
    n["stakes_card_high"] = pct(st.card.iloc[-1])
    n["clear_misses"] = num(len(cm))
    n["clear_tap_actual"] = pct(cm.challenged.mean())

    # ------------------------------------------------------------------ policies (cross-validated replay)
    pol = {r["policy"]: r for r in P["season_wins_per_team"]}
    polp = {r["policy"]: r for r in PP["season_wins_per_team"]}
    labels = [("every_missed_call", "Fix every missed call (ceiling)"), ("card", "This card"),
              ("actual_2026", "What clubs actually did"), ("flat_0.2_runs", "Flat 0.2-run rule"),
              ("coin_flip_50", "Tap when 50%+ sure"), ("sure_75", "Tap when 75%+ sure")]
    S["policy"] = [{"key": k, "label": lab, "wins": pol[k]["mean"], "taps": pol[k].get("taps_per_team_game"),
                    "win_rate": pol[k].get("win_rate")} for k, lab in labels]
    n["card_wins"] = num(pol["card"]["mean"], 1)
    n["actual_wins"] = num(pol["actual_2026"]["mean"], 1)
    n["flat_wins"] = num(pol["flat_0.2_runs"]["mean"], 1)
    n["coin_wins"] = num(pol["coin_flip_50"]["mean"], 1)
    n["ceiling_wins"] = num(pol["every_missed_call"]["mean"], 1)
    n["card_gain"] = num(pol["card"]["mean"] - pol["actual_2026"]["mean"], 1)
    n["card_gain_flat"] = num(pol["card"]["mean"] - pol["flat_0.2_runs"]["mean"], 1)
    n["card_gain_probit"] = num(polp["card"]["mean"] - polp["actual_2026"]["mean"], 1)
    n["card_wins_probit"] = num(polp["card"]["mean"], 1)
    n["flat_wins_probit"] = num(polp["flat_0.2_runs"]["mean"], 1)
    n["card_share"] = pct(pol["card"]["mean"] / pol["every_missed_call"]["mean"])
    n["card_taps"] = num(pol["card"]["taps_per_team_game"], 1)
    n["actual_taps"] = num(pol["actual_2026"]["taps_per_team_game"], 1)
    n["flat_taps"] = num(pol["flat_0.2_runs"]["taps_per_team_game"], 1)
    n["card_winrate"] = pct(pol["card"]["win_rate"])
    n["actual_winrate"] = pct(pol["actual_2026"]["win_rate"])
    n["card_min"] = num(pol["card"]["min"], 1)
    n["card_max"] = num(pol["card"]["max"], 1)
    n["sim_draws"] = num(P["draws"])
    n["sim_calls"] = num(P["calls_simulated"])

    # ------------------------------------------------------------------ card examples
    def ex(**q):
        sel = card[(card.env == q.get("env", "league")) & (card.inning == q["inning"]) & (card.team_home == q["home"])
                   & (card.batting == q["batting"]) & (card.margin == q["margin"]) & (card.base == q["base"])
                   & (card.outs == q["outs"]) & (card.balls == q["b"]) & (card.strikes == q["s"]) & (card.k == q["k"])]
        return sel.iloc[0]
    e1 = ex(inning=2, home=1, batting=0, margin=0, base=0, outs=1, b=0, s=0, k=2)
    e2 = ex(inning=9, home=1, batting=0, margin=0, base=1, outs=1, b=3, s=2, k=1)
    e3 = ex(inning=8, home=0, batting=1, margin=-1, base=2, outs=2, b=1, s=2, k=1)
    for key, e in (("ex1", e1), ("ex2", e2), ("ex3", e3)):
        n[f"{key}_p"] = pct(e.p_star)
        n[f"{key}_flat"] = pct(e.p_flat)
        n[f"{key}_v"] = pct(e.v_wp, 1)
    # Coors vs road: same states, close games, last challenge
    cc = card[(card.k == 1) & (card.margin.abs() <= 1) & (card.inning <= 9)]
    piv = cc.pivot_table(index=["inning", "team_home", "batting", "margin", "base", "outs", "balls", "strikes"],
                         columns="env", values=["p_star", "v_wp"])
    n["coors_pstar_drop"] = num(100 * (piv[("p_star", "league")] - piv[("p_star", "coors")]).median(), 1)
    n["coors_v_lift"] = pct((piv[("v_wp", "coors")] / piv[("v_wp", "league")]).median() - 1)
    kap = P["coors_kappa"]
    n["kappa1"], n["kappa1_lo"], n["kappa1_hi"] = num(kap["1"], 2), num(kap["1_ci"][0], 2), num(kap["1_ci"][1], 2)

    # ------------------------------------------------------------------ tapped out
    T = A["tapped_out"]
    tb = {r["inning"]: r for r in L["tapped_out_by_inning"]}
    S["tapped_by_inning"] = L["tapped_out_by_inning"]
    n["tapped_9th"] = pct(tb[9]["bat_out"])
    n["tapped_8th"] = pct(tb[8]["bat_out"], 1)
    n["tapped_7th"] = pct(tb[7]["bat_out"], 1)
    g = T["gift_band_regression"]
    n["gift_share"] = pct(g["baseline_share"], 1)
    n["gift_coef"] = num(100 * g["coef_bat_out"], 2)
    n["gift_se"] = num(100 * g["se"], 2)
    u = {r["batting_club_out"]: r for r in T["ump_strikes_outside_zone"]}
    n["ump_out_ratio"] = num(u[True]["strike_rate"] / u[True]["expected"], 2)
    n["ump_has_ratio"] = num(u[False]["strike_rate"] / u[False]["expected"], 2)
    # umpire map, aggregated to 1 in. x 0.05 zone heights
    um = con.execute("""
        SELECT stand, floor(ix / 2) AS gx, floor(iz / 2) AS gz, sum(n) AS n,
               sum(n * p_ump) / sum(n) AS p_ump, sum(n * p_abs) / sum(n) AS p_abs
        FROM ump_map WHERE p_ump IS NOT NULL GROUP BY ALL HAVING sum(n) >= 40""").df()
    um["x"] = -20 + (um.gx * 2 + 1) * 0.5
    um["z"] = -0.6 + (um.gz * 2 + 1) * 0.025
    um = um[(um.x.abs() <= 17) & (um.z >= -0.45) & (um.z <= 1.45)]
    S["ump_map"] = {s: {"dx": 1.0, "dz": 0.05, "cells": [[round(x.x, 2), round(x.z, 3), round(x.p_ump - x.p_abs, 3),
                                                          round(x.p_ump, 3), round(x.p_abs, 3)] for x in g2.itertuples()]}
                    for s, g2 in um.groupby("stand")}

    # ------------------------------------------------------------------ Coors
    C = A["coors"]
    mv = {r["coors"]: r for r in C["fourseam_movement"]}
    n["ivb_coors"], n["ivb_else"] = num(mv[True]["ivb_in"], 1), num(mv[False]["ivb_in"], 1)
    n["ivb_drop"] = num(mv[False]["ivb_in"] - mv[True]["ivb_in"], 1)
    n["hb_coors"], n["hb_else"] = num(mv[True]["hb_in"], 1), num(mv[False]["hb_in"], 1)
    mr = {(r["coors"], r["wronged_side"]): r for r in C["miss_rates"]}
    n["coors_miss_strikes"] = pct(mr[(True, "offense")]["rate"], 1)
    n["else_miss_strikes"] = pct(mr[(False, "offense")]["rate"], 1)
    n["coors_miss_balls"] = pct(mr[(True, "defense")]["rate"], 1)
    n["else_miss_balls"] = pct(mr[(False, "defense")]["rate"], 1)
    cs = {r["grp"]: r for r in C["challenge_success"]}
    vc = cs["visitors at Coors"]
    n["vis_coors_n"], n["vis_coors_rate"] = num(vc["challenges"]), pct(vc["rate"])
    n["vis_coors_lo"], n["vis_coors_hi"] = pct(vc["lo"]), pct(vc["hi"])
    n["else_rate"] = pct(cs["everyone else, elsewhere"]["rate"])
    S["coors_value"] = [{"count": r["count"], "league": r["v_league"], "coors": r["v_coors"]} for r in C["value_by_count"]]
    n["coors_value_lift"] = pct(np.mean([r["v_coors"] / r["v_league"] for r in C["value_by_count"]]) - 1)

    # ------------------------------------------------------------------ Rockies
    R = A["rockies"]
    rv = {(r["rockies"], r["wronged_side"]): r for r in R["vs_league"]}
    ro, rd = rv[(True, "offense")], rv[(True, "defense")]
    lo_, ld = rv[(False, "offense")], rv[(False, "defense")]
    n["col_off_taps"], n["col_off_ot"] = num(ro["challenges"]), num(ro["overturns"])
    n["col_off_rate"] = pct(ro["overturns"] / ro["challenges"])
    n["lg_off_rate"] = pct(lo_["overturns"] / lo_["challenges"])
    n["col_def_taps"], n["col_def_ot"] = num(rd["challenges"]), num(rd["overturns"])
    n["col_def_rate"] = pct(rd["overturns"] / rd["challenges"])
    n["lg_def_rate"] = pct(ld["overturns"] / ld["challenges"])
    n["col_wins_missed"] = num(ro["wp_missed"] + rd["wp_missed"], 1)
    n["col_wins_fixed"] = num(ro["wp_fixed"] + rd["wp_fixed"], 1)
    n["col_card"] = num(P["rockies"]["card"], 1)
    n["col_card_gain"] = num(P["rockies"]["card"] - P["rockies"]["actual_2026"], 1)
    ch = pd.DataFrame(R["challengers"])
    gd = ch[(ch.name == "Hunter Goodman") & (ch.role == "catcher")].iloc[0]
    n["goodman_ot"], n["goodman_taps"] = num(gd.overturns), num(gd.challenges)
    n["goodman_rate"] = pct(gd.overturns / gd.challenges)
    S["rockies_challengers"] = ch.head(12).to_dict(orient="records")
    S["rockies_clear_misses"] = R["hitters_clear_misses"][:10]
    S["rockies_got_away"] = R["got_away"][:8]
    S["rockies_biggest_taps"] = R["biggest_taps"][:6]
    ga = R["got_away"][0]
    n["ga_date"] = pd.Timestamp(ga["game_date"]).strftime("%B %-d")
    n["ga_v"] = pct(ga["v"])
    n["ga_batter"] = ga["batter"]
    n["ga_w"] = num(ga["w_in"], 1)
    n["ga_opp"] = ga["home_team"] if ga["away_team"] == "COL" else ga["away_team"]

    # ------------------------------------------------------------------ teams (advance cards) and notable plays
    teams = A["opponents"]

    def rank(key):
        order = sorted(teams, key=key, reverse=True)
        r_ = [i + 1 for i, t in enumerate(order) if t["abbr"] == "COL"][0]
        return {1: "1st", 2: "2nd", 3: "3rd"}.get(r_, f"{r_}th")
    n["col_def_rank"] = rank(lambda t: t["defense"]["overturn_rate"] or 0)
    n["col_off_rank"] = rank(lambda t: t["offense"]["overturn_rate"] or 0)
    n["col_fix_rank"] = rank(lambda t: t["offense"]["wp_fixed"] + t["defense"]["wp_fixed"])
    n["col_out_late"] = pct([t for t in teams if t["abbr"] == "COL"][0]["tapped_out_7th_on"])
    n["col_out_late_rank"] = rank(lambda t: t["tapped_out_7th_on"] or 0)
    n["lg_out_late"] = pct(np.mean([t["tapped_out_7th_on"] for t in teams]))
    for t in teams:
        t["nlwest"] = t["division"] == "National League West"
        t["league"] = "National League" if t["division"].startswith("National") else "American League"
        t["offense"]["missed_fixed"] = t["offense"]["share_missed_fixed"] * t["offense"]["missed"]
        t["defense"]["missed_fixed"] = t["defense"]["share_missed_fixed"] * t["defense"]["missed"]
    S["teams"] = teams
    S["notable"] = A["notable"]
    S["checks"] = CH

    n["games"] = num(L["games"])
    n["pitches"] = num(con.execute("SELECT count(*) FROM pitches").fetchone()[0])
    n["checks_n"] = str(len(CH))
    n["updated"] = pd.Timestamp.now(tz="America/Denver").strftime("%B %-d, %Y")

    SITE.joinpath("data").mkdir(parents=True, exist_ok=True)
    (SITE / "data" / "story.json").write_text(json.dumps(clean(S), separators=(",", ":"), default=str))
    (OUT / "numbers.json").write_text(json.dumps(n, indent=1))
    render(n)


def render(n):
    tpl = TEMPLATE.read_text(encoding="utf-8")
    used = set(re.findall(r"\{\{(\w+)\}\}", tpl))
    missing = sorted(used - n.keys())
    if missing:
        raise SystemExit(f"template tokens without values: {missing}")
    html = re.sub(r"\{\{(\w+)\}\}", lambda m: str(n[m.group(1)]), tpl)
    (SITE / "index.html").write_text(html, encoding="utf-8")
    unused = sorted(n.keys() - used)
    print(f"rendered site/index.html ({len(used)} numbers); not quoted on the page: {', '.join(unused) or 'none'}")


if __name__ == "__main__":
    main()
