"""How well do players see the zone, and how willing are they to tap? Fitted to every 2026 challenge decision.

    python pipeline/perception.py

A challenge opportunity is a called pitch that went against a club with a challenge left. For each one we know how
wrong the umpire really was: w inches (w > 0 means ABS would overturn the call).

Model ("eyes and will"): a player perceives w with Gaussian error sigma (his eyesight), and if what he sees clears
a bar tau that may move with the stakes, he taps with probability rho (his willingness):

    P(challenge | w) = rho * Phi((w - tau) / sigma),   tau = t0 + t_v log V + t_k [one left] + t_l [late]

The plain probit (rho = 1) is the special case where every bit of reluctance has to be explained as bad eyesight.
Both are fitted; the likelihood decides. Separate fits for hitters (strike calls) and the battery (ball calls);
group shifts in sigma and tau for visitors at Coors Field and for the Rockies.

Calibrated beliefs: a player who perceives w_hat should believe P(call wrong) = P(w > 0 | w_hat), with the
empirical distribution of w as the prior. Those curves feed the policy simulation.

Outputs: data/out/perception.json, table perception_curves.
"""
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from scipy.optimize import minimize
from scipy.stats import norm
from statsmodels.tools.numdiff import approx_hess

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "tapor.duckdb"
OUT = ROOT / "data" / "out"
W_GRID = np.round(np.arange(-15, 15.0001, 0.05), 3)
NAMES = ["log_sigma", "logit_rho", "tau0", "tau_logv", "tau_one_left", "tau_late", "tau_group", "logsig_group"]


def opportunities(con):
    return con.execute("""
        SELECT c.*, c.wronged_team_id = 115 AS rockies_side
        FROM calls c
        WHERE c.opportunity""").df()


def design(d):
    return np.column_stack([d.w_in.clip(-12, 12), np.log(d.v_wp_env.clip(lower=1e-4)),
                            (d.wronged_ch_left == 1).astype(float), (d.inning >= 7).astype(float)])


def probs(params, X, group):
    log_sig, logit_rho, t0, tv, tk, tl, tg, sg = params
    sig = np.exp(log_sig + sg * group)
    tau = t0 + tv * X[:, 1] + tk * X[:, 2] + tl * X[:, 3] + tg * group
    rho = 1 / (1 + np.exp(-logit_rho))
    return np.clip(rho * norm.cdf((X[:, 0] - tau) / sig), 1e-12, 1 - 1e-12)


def fit(d, group=None, free_rho=True):
    """Maximum likelihood. Returns params (all 8, fixed ones at their fixed values), covariance, log-likelihood."""
    X, y = design(d), d.challenged.astype(float).values
    g = np.zeros(len(d)) if group is None else np.asarray(group, dtype=float)
    free = [0, 1, 2, 3, 4, 5] + ([6, 7] if group is not None else [])
    if not free_rho:
        free.remove(1)
    fixed = np.array([0.0, 30.0, 2.0, 0.0, 0.0, 0.0, 0.0, 0.0])         # logit_rho = 30 means rho = 1

    def full(th):
        p = fixed.copy()
        p[free] = th
        return p

    def nll(th):
        p = probs(full(th), X, g)
        return -np.sum(y * np.log(p) + (1 - y) * np.log(1 - p))

    start = np.array([0.0, -1.0, 2.0, 0.0, 0.0, 0.0, 0.0, 0.0])[free]
    r = minimize(nll, start, method="BFGS", options={"maxiter": 2000})
    r = minimize(nll, r.x, method="Nelder-Mead", options={"maxiter": 4000, "xatol": 1e-6, "fatol": 1e-6})
    cov = np.zeros((8, 8))
    cov[np.ix_(free, free)] = np.linalg.pinv(approx_hess(r.x, nll))
    params = full(r.x)
    return {"params": params, "cov": cov, "loglike": float(-r.fun), "k": len(free),
            "pred": probs(params, X, g)}


def ci_exp(est, var):
    se = np.sqrt(max(var, 0.0))
    return {"est": float(np.exp(est)), "lo": float(np.exp(est - 1.96 * se)), "hi": float(np.exp(est + 1.96 * se))}


def sigma_ci(f, group=False):
    c, p = f["cov"], f["params"]
    if not group:
        return ci_exp(p[0], c[0, 0])
    return ci_exp(p[0] + p[7], c[0, 0] + c[7, 7] + 2 * c[0, 7])


def rho_ci(f):
    p, c = f["params"], f["cov"]
    se = np.sqrt(max(c[1, 1], 0))
    inv = lambda x: float(1 / (1 + np.exp(-x)))
    return {"est": inv(p[1]), "lo": inv(p[1] - 1.96 * se), "hi": inv(p[1] + 1.96 * se)}


def wilson(k, n, z=1.96):
    if n == 0:
        return None, None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return float(c - h), float(c + h)


def belief_curve(w, sigma):
    """P(call wrong | perceived w_hat) on W_GRID, with the empirical distribution of w as the prior."""
    edges = np.arange(-40, 40.0001, 0.05)
    prior, _ = np.histogram(np.clip(w, -39.9, 39.9), bins=edges)
    centers = (edges[:-1] + edges[1:]) / 2
    prior = prior / prior.sum()
    like = norm.pdf((W_GRID[:, None] - centers[None, :]) / sigma)
    num = (like * prior * (centers > 0)).sum(axis=1)
    den = (like * prior).sum(axis=1)
    return num / np.maximum(den, 1e-300)


def main():
    con = duckdb.connect(str(DB))
    d = opportunities(con)
    res = {"n_opportunities": int(len(d)), "n_challenges": int(d.challenged.sum()),
           "overturn_rate": float(d.loc[d.challenged, "call_wrong"].mean()), "sides": {}}
    for side in ("offense", "defense"):
        s = d[d.wronged_side == side].reset_index(drop=True)
        ew, pr = fit(s), fit(s, free_rho=False)
        p = ew["params"]
        out = {
            "n": int(len(s)), "challenges": int(s.challenged.sum()),
            "overturn_rate": float(s.loc[s.challenged, "call_wrong"].mean()),
            "missed_calls": int(s.call_wrong.sum()),
            "share_of_missed_calls_challenged": float(s.loc[s.call_wrong, "challenged"].mean()),
            "eyes_will": {"sigma": sigma_ci(ew), "rho": rho_ci(ew), "loglike": ew["loglike"], "aic": 2 * ew["k"] - 2 * ew["loglike"],
                          "params": dict(zip(NAMES, map(float, p))),
                          "pred_successes": float((ew["pred"] * (s.w_in > 0)).sum()),
                          "pred_challenges": float(ew["pred"].sum())},
            "probit": {"sigma": sigma_ci(pr), "loglike": pr["loglike"], "aic": 2 * pr["k"] - 2 * pr["loglike"],
                       "params": dict(zip(NAMES, map(float, pr["params"])))},
            "actual_successes": int((s.challenged & s.call_wrong).sum()),
        }
        # how much the bar moves with the stakes: a 10x bigger swing in win probability moves tau by this many inches
        out["tau_shift_for_10x_stakes_in"] = float(p[3] * np.log(10))
        # group shifts (eyesight and bar) for visitors at Coors and for the Rockies
        for name, mask in {"visitors_at_coors": s.coors & ~s.rockies_side, "rockies": s.rockies_side,
                           "rockies_at_coors": s.coors & s.rockies_side}.items():
            n_ch = int((s.challenged & mask).sum())
            k_ok = int((s.challenged & mask & s.call_wrong).sum())
            lo, hi = wilson(k_ok, n_ch)
            entry = {"n": int(mask.sum()), "challenges": n_ch, "overturn_ci": [lo, hi],
                     "share_missed_challenged": float(s.loc[mask & s.call_wrong, "challenged"].mean()),
                     "overturn_rate": float(s.loc[s.challenged & mask, "call_wrong"].mean()) if n_ch else None,
                     "rest_overturn_rate": float(s.loc[s.challenged & ~mask, "call_wrong"].mean())}
            if n_ch >= 40:
                # small groups: shift eyesight and bar only (willingness shared), probit form for stability
                g = fit(s, group=mask.values, free_rho=False)
                entry["sigma_group"] = sigma_ci(g, group=True)
                entry["sigma_rest"] = sigma_ci(g)
                entry["tau_shift_in"] = float(g["params"][6])
            out[name] = entry
        res["sides"][side] = out

    # who taps for the defense, and how they do
    dd = d[(d.wronged_side == "defense") & d.challenged]
    role = np.where(dd.challenger_id == dd.catcher, "catcher", np.where(dd.challenger_id == dd.pitcher, "pitcher", "other"))
    res["defense_by_role"] = {r: {"challenges": int((role == r).sum()),
                                  "overturn_rate": float(dd.loc[role == r, "call_wrong"].mean()) if (role == r).any() else None}
                              for r in ("catcher", "pitcher", "other")}
    ob = d[(d.wronged_side == "offense") & d.challenged]
    res["offense_by_role"] = {"batter": {"challenges": int((ob.challenger_id == ob.batter).sum()),
                                         "overturn_rate": float(ob.loc[ob.challenger_id == ob.batter, "call_wrong"].mean())},
                              "other": int((ob.challenger_id != ob.batter).sum())}

    # calibrated belief curves P(wrong | perceived w) for each side, from the eyes-and-will eyesight
    curves = []
    for side in ("offense", "defense"):
        w_all = con.execute(f"""SELECT w_in FROM calls WHERE wronged_side = '{side}'
                               AND NOT position_player_pitching""").df().w_in.values
        for model in ("eyes_will", "probit"):
            sig = res["sides"][side][model]["sigma"]["est"]
            p = belief_curve(w_all, sig)
            curves += [(side, model, float(wv), float(pv)) for wv, pv in zip(W_GRID, p)]
            for q in (0.5, 0.75):
                hit = np.nonzero(p >= q)[0]
                res["sides"][side][model][f"w_hat_for_{int(q * 100)}pct"] = float(W_GRID[hit[0]]) if len(hit) else None
    con.register("pc", pd.DataFrame(curves, columns=["side", "model", "w_hat", "p_wrong"]))
    con.execute("CREATE OR REPLACE TABLE perception_curves AS SELECT * FROM pc")

    # the raw challenge curve (rate of tapping by how wrong the call really was), with both fitted curves
    bins = np.arange(-6, 6.01, 0.5)
    rows = []
    for side in ("offense", "defense"):
        s = d[d.wronged_side == side].reset_index(drop=True)
        ew = fit(s)
        pr = fit(s, free_rho=False)
        s["pred_ew"], s["pred_pr"] = ew["pred"], pr["pred"]
        g = s.groupby(pd.cut(s.w_in, bins), observed=True).agg(
            n=("challenged", "size"), rate=("challenged", "mean"), ew=("pred_ew", "mean"), pr=("pred_pr", "mean"))
        rows += [{"side": side, "w_mid": float(iv.mid), "n": int(r.n), "rate": float(r.rate), "fit_eyes_will": float(r.ew),
                  "fit_probit": float(r.pr)} for iv, r in g.iterrows()]
    res["challenge_curve"] = rows

    # stakes: tap rate on clear misses (1-4 in.) by how much the call was worth
    clear = d[(d.w_in > 1) & (d.w_in <= 4)].copy()
    clear["stakes"] = pd.qcut(clear.v_wp_env, 5, labels=["lowest", "low", "middle", "high", "highest"])
    st = clear.groupby(["wronged_side", "stakes"], observed=True).agg(
        n=("challenged", "size"), tap_rate=("challenged", "mean"), v_median=("v_wp_env", "median")).reset_index()
    res["stakes_response"] = st.to_dict(orient="records")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "perception.json").write_text(json.dumps(res, indent=1, default=str))
    show = {k: v for k, v in res.items() if k not in ("challenge_curve",)}
    print(json.dumps(show, indent=1, default=str)[:6000])


if __name__ == "__main__":
    main()
