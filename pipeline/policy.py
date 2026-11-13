"""What is a challenge worth, and when should you spend it? A season-replay dynamic program.

    python pipeline/policy.py

The decision on a call that went against you, with k challenges left and belief p that the call was wrong:
    challenge  ->  gain V (win probability) with prob p, keep the challenge; lose it with prob 1 - p
    hold       ->  nothing now, keep k
so challenge iff  p * V  >=  (1 - p) * D_k,  i.e.  p >= D_k / (V + D_k),
where D_k is the value of the k-th challenge over the rest of the game. Savant's public rule fixes D at 0.2 runs.
Here D_k is estimated for every game state by replaying all 2026 games: each club's real later calls, real values
and real umpire misses, with simulated perception noise fitted to how 2026 players actually challenged.

Policy iteration: thresholds -> replay -> D table -> thresholds ... (3 rounds). Policies are then compared out of
sample with two-fold cross-validation by game.

Outputs: tables delta_table, policy_eval; data/out/policy.json.
"""
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "tapor.duckdb"
OUT = ROOT / "data" / "out"
D_DRAWS = 24
P_MIN = 0.01            # no policy taps on less than a 1% belief that the call was wrong
MARGINS = list(range(-4, 5))
RNG = np.random.default_rng(2026)


def load(con, model="eyes_will"):
    pc = con.execute(f"SELECT * FROM perception_curves WHERE model = '{model}' ORDER BY side, w_hat").df()
    per = json.loads((OUT / "perception.json").read_text())
    curves = {s: (g.w_hat.values, g.p_wrong.values) for s, g in pc.groupby("side")}
    sig = {s: per["sides"][s][model]["sigma"]["est"] for s in ("offense", "defense")}
    c = con.execute("""
        SELECT game_pk, at_bat_number, pitch_number, inning, is_top, wronged_side, wronged_team_id,
               wronged_team_id = home_id AS team_home, home_score - away_score AS home_margin,
               w_in, call_wrong, v_wp_env AS v, v_runs, challenged, overturned, challenge_team_id, coors,
               position_player_pitching
        FROM calls
        WHERE NOT position_player_pitching AND wronged_team_id IS NOT NULL
        ORDER BY game_pk, wronged_team_id, at_bat_number, pitch_number""").df()
    c["margin"] = np.where(c.team_home, c.home_margin, -c.home_margin)       # team's own perspective
    c["v"] = c.v.clip(lower=0)
    # simulated beliefs: perceived miss = true miss + noise, mapped through the calibrated belief curve
    p = np.zeros((len(c), D_DRAWS), dtype=np.float32)
    for side in ("offense", "defense"):
        m = (c.wronged_side == side).values
        w_hat = c.w_in.values[m, None] + sig[side] * RNG.standard_normal((m.sum(), D_DRAWS))
        p[m] = np.interp(w_hat, *curves[side])
    # every policy refuses to tap below P_MIN belief, so calls nobody would ever believe were wrong can be dropped
    # without changing any result
    keep = p.max(axis=1) >= P_MIN
    full = c.copy()
    c, p = c[keep].reset_index(drop=True), p[keep]
    return c, p, sig, full


def state_keys(inning, team_home, margin):
    inn = np.minimum(inning, 10)
    mg = np.clip(margin, -4, 4)
    return inn, team_home.astype(int), mg


class DeltaTable:
    """D_k by (inning 1-9/10+, home/away, margin -4..4, k=1,2). Sparse cells borrow from home+away pooled."""

    def __init__(self, df=None, const=0.02):
        self.t = np.full((11, 2, 9, 3), const)
        if df is not None:
            for k in (1, 2):
                g = df[df.k == k]
                full = g.groupby(["inn", "home", "mg"]).agg(d=("delta", "mean"), n=("delta", "size"))
                pooled = g.groupby(["inn", "mg"]).agg(d=("delta", "mean"), n=("delta", "size"))
                byinn = g.groupby(["inn"]).agg(d=("delta", "mean"))
                for inn in range(1, 11):
                    for home in (0, 1):
                        for mg in MARGINS:
                            if (inn, home, mg) in full.index and full.loc[(inn, home, mg), "n"] >= 300:
                                v = full.loc[(inn, home, mg), "d"]
                            elif (inn, mg) in pooled.index and pooled.loc[(inn, mg), "n"] >= 300:
                                v = pooled.loc[(inn, mg), "d"]
                            else:
                                v = byinn.loc[inn, "d"] if inn in byinn.index else const
                            self.t[inn, home, mg + 4, k] = max(v, 1e-5)

    def get(self, inning, team_home, margin, k):
        inn, home, mg = state_keys(inning, team_home, margin)
        return self.t[inn, home, mg + 4, np.minimum(k, 2)]


def coors_kappa(sd, table, ci=False):
    """At Coors every call is worth more, so the challenge you save is too: D_coors = kappa_k * D (ratio of the
    replay's Coors continuation values to the pooled table's prediction for the same states)."""
    out = {}
    co = sd[sd.coors]
    for k in (1, 2):
        obs = (co.g1 - co.g0) if k == 1 else (co.g2 - co.g1)
        pred = table.get(co.inn.values, co.home.values, co.mg.values, k)
        out[k] = float(obs.sum() / pred.sum())
        if ci:
            g = pd.DataFrame({"game": co.game_pk.values, "o": obs.values, "p": pred}).groupby("game").sum()
            rng = np.random.default_rng(7)
            boots = [g.iloc[rng.integers(0, len(g), len(g))].pipe(lambda x: x.o.sum() / x.p.sum()) for _ in range(2000)]
            out[f"{k}_ci"] = [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))]
    return out


def thresholds_card(c, table, kappa=None):
    """p* for k = 1 and k = 2 at every opportunity, from the state-dependent table."""
    out = np.zeros((len(c), 3))
    for k in (1, 2):
        dk = table.get(c.inning.values, c.team_home.values, c.margin.values, k)
        if kappa:
            dk = np.where(c.coors.values, dk * kappa[k], dk)
        out[:, k] = np.maximum(dk / (c.v.values + dk), P_MIN)
    out[:, 0] = 2.0                                       # k = 0: cannot challenge
    return out


def thresholds_flat(c, cost_runs=0.2):
    v = c.v_runs.abs().values
    t = np.maximum(cost_runs / (cost_runs + np.maximum(v, 1e-6)), P_MIN)
    return np.stack([np.full(len(c), 2.0), t, t], axis=1)


def thresholds_const(c, q):
    return np.stack([np.full(len(c), 2.0), np.full(len(c), q), np.full(len(c), q)], axis=1)


def replay(c, p, thr, starts=True):
    """Simulate every club-game. Returns (per-start continuation values for k=0,1,2) and per-game totals from k=2."""
    rec, totals = [], []
    for (gpk, team), g in c.groupby(["game_pk", "wronged_team_id"], sort=False):
        idx = g.index.values
        n = len(idx)
        V, ok, inn = g.v.values, g.call_wrong.values, g.inning.values
        P = p[idx]                                      # n x D
        T = thr[idx]                                    # n x 3 (threshold for k = 0, 1, 2)
        S = n + 1 if starts else 1                      # start before opp s (s = 0..n); s = n is the empty future
        k = np.zeros((S, D_DRAWS, 3), dtype=np.int8)
        k[:, :, 1], k[:, :, 2] = 1, 2
        gain = np.zeros((S, D_DRAWS, 3))
        taps = np.zeros((S, D_DRAWS, 3))
        wins = np.zeros((S, D_DRAWS, 3))
        s_idx = np.arange(S)[:, None, None]
        for j in range(n):
            active = s_idx <= j                         # start s only sees opportunities j >= s
            # a club with none left entering an extra inning is given one
            if inn[j] >= 10 and (j == 0 or inn[j] > inn[j - 1]):
                k = np.where(active & (k == 0), 1, k).astype(np.int8)
            go = active & (k > 0) & (P[j][None, :, None] >= T[j][k])
            taps += go
            if ok[j]:
                gain += np.where(go, V[j], 0.0)
                wins += go
            else:
                k = (k - go).astype(np.int8)
        if starts:
            # continuation value after opportunity j = start j + 1
            m = gain.mean(axis=1)                       # S x 3
            rec.append(pd.DataFrame({
                "game_pk": gpk, "team": team, "j": np.arange(n),
                "inn": np.minimum(inn, 10), "home": g.team_home.values.astype(int),
                "mg": np.clip(g.margin.values, -4, 4), "coors": g.coors.values.astype(bool),
                "g0": m[1:, 0], "g1": m[1:, 1], "g2": m[1:, 2]}))
        totals.append((gpk, team, gain[0, :, 2].mean(), taps[0, :, 2].mean(), wins[0, :, 2].mean()))
    starts_df = pd.concat(rec, ignore_index=True) if starts else None
    return starts_df, pd.DataFrame(totals, columns=["game_pk", "team", "wp", "taps", "wins"])


def delta_from_starts(sd):
    d1 = sd.assign(k=1, delta=sd.g1 - sd.g0)[["inn", "home", "mg", "k", "delta"]]
    d2 = sd.assign(k=2, delta=sd.g2 - sd.g1)[["inn", "home", "mg", "k", "delta"]]
    return pd.concat([d1, d2], ignore_index=True)


def fit_table(c, p, rounds=3):
    table = DeltaTable()
    for _ in range(rounds):
        sd, _ = replay(c, p, thresholds_card(c, table))
        table = DeltaTable(delta_from_starts(sd))
    return table, sd


def main(model="eyes_will"):
    con = duckdb.connect(str(DB))
    c, p, sig, full = load(con, model)
    primary = model == "eyes_will"
    print(f"calls simulated: {len(c)} of {len(full)} (the rest never reach {P_MIN:.0%} belief), draws: {D_DRAWS}")

    # ---- full-sample table (for the card and the site)
    table, sd = fit_table(c, p)
    kappa_full = coors_kappa(sd, table, ci=True)
    kappa = {k: kappa_full[k] for k in (1, 2)}
    print("Coors multiplier on challenge value:", kappa)
    rows = [{"inning": inn, "team_home": home, "margin": mg, "k": k, "delta_wp": float(table.t[inn, home, mg + 4, k])}
            for inn in range(1, 11) for home in (0, 1) for mg in MARGINS for k in (1, 2)]
    if primary:
        con.register("dt", pd.DataFrame(rows))
        con.execute("CREATE OR REPLACE TABLE delta_table AS SELECT * FROM dt")

    # ---- two-fold cross-validated comparison of policies (whole games held out)
    games = c.game_pk.unique()
    fold = dict(zip(games, RNG.integers(0, 2, len(games))))
    c["fold"] = c.game_pk.map(fold)
    results = []
    for f in (0, 1):
        tr, te = c[c.fold != f], c[c.fold == f]
        t_tab, t_sd = fit_table(tr.reset_index(drop=True), p[tr.index.values])
        t_kappa = coors_kappa(t_sd, t_tab)
        te = te.reset_index()                          # keep original row index in column 'index'
        pt = p[te["index"].values]
        te = te.drop(columns="index")
        for name, thr in {
            "card": thresholds_card(te, t_tab),           # one price table everywhere (Coors multiplier CI spans 1)
            "flat_0.2_runs": thresholds_flat(te),
            "coin_flip_50": thresholds_const(te, 0.5),
            "sure_75": thresholds_const(te, 0.75),
        }.items():
            _, tot = replay(te, pt, thr, starts=False)
            tot["policy"] = name
            results.append(tot)
    ev = pd.concat(results, ignore_index=True)
    # actual 2026 decisions and the perfect-information ceiling, on the same opportunity set
    fa = full[full.challenged & (full.challenge_team_id == full.wronged_team_id)].assign(
        wpv=lambda x: np.where(x.call_wrong, x.v, 0.0), win=lambda x: x.call_wrong.astype(float))
    act = fa.groupby(["game_pk", "wronged_team_id"]).agg(wp=("wpv", "sum"), taps=("v", "size"), wins=("win", "sum")).reset_index(
        ).rename(columns={"wronged_team_id": "team"})
    act["policy"] = "actual_2026"
    ceil = full[full.call_wrong].groupby(["game_pk", "wronged_team_id"]).v.sum().rename("wp").reset_index().rename(
        columns={"wronged_team_id": "team"})
    ceil["policy"] = "every_missed_call"
    ev = pd.concat([ev, act, ceil], ignore_index=True)
    if primary:
        con.register("ev", ev)
        con.execute("CREATE OR REPLACE TABLE policy_eval AS SELECT * FROM ev")
    per_team = ev.groupby(["policy", "team"]).wp.sum().reset_index()
    summary = per_team.groupby("policy").wp.agg(["mean", "std", "min", "max"]).reset_index()
    n_team_games = c.groupby(["game_pk", "wronged_team_id"]).ngroups
    beh = ev.groupby("policy").agg(taps=("taps", "sum"), wins=("wins", "sum")).reset_index()
    beh["taps_per_team_game"] = beh.taps / n_team_games
    beh["win_rate"] = beh.wins / beh.taps.where(beh.taps > 0)
    summary = summary.merge(beh[["policy", "taps_per_team_game", "win_rate"]], on="policy", how="left")
    out = {"draws": D_DRAWS, "p_min": P_MIN, "calls_simulated": int(len(c)), "calls_total": int(len(full)),
           "season_wins_per_team": summary.to_dict(orient="records"),
           "rockies": per_team[per_team.team == 115].set_index("policy").wp.to_dict()}
    OUT.mkdir(parents=True, exist_ok=True)
    out["perception_model"] = model
    out["coors_kappa"] = kappa_full
    out["sigma_in"] = sig
    (OUT / ("policy.json" if primary else f"policy_{model}.json")).write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else "eyes_will")
