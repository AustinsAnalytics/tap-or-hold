"""What is a ball/strike call worth? Run expectancy (RE288) and a win-expectancy model built from 2026 data.

    python pipeline/values.py

Win expectancy is computed, not looked up: for any state, the rest of the batting team's half-inning comes from the
empirical distribution of runs scored from that base-out-count state, and every later half-inning from the empirical
distribution of runs in that inning. Convolving them gives the distribution of the final margin, hence P(win).
The same machinery with every distribution exponentially tilted by one fitted parameter gives a Coors Field version.

Outputs: tables re288, we_grid, called_values in the database, and data/out/values_checks.json.
"""
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "tapor.duckdb"
OUT = ROOT / "data" / "out"
L = 40            # runs tracked per distribution (anything above is lumped into the last bin; never reached)
COORS = 19        # MLB venue id for Coors Field
COUNTS = [(b, s) for b in range(4) for s in range(3)]


# ----------------------------------------------------------------------------------------------- distributions
def hist(x, n=L):
    h = np.bincount(np.clip(np.asarray(x, dtype=int), 0, n - 1), minlength=n).astype(float)
    return h / h.sum()


def tilt(p, theta):
    w = p * np.exp(theta * np.arange(len(p)))
    return w / w.sum()


class RunModel:
    """Empirical run distributions: rest of the current half-inning, whole half-innings by inning, extra innings."""

    def __init__(self, pr, hi, theta=0.0):
        self.theta = theta
        rest = {}
        for (b, o, ba, s), g in pr.groupby(["base_state", "outs", "balls", "strikes"]):
            rest[(b, o, ba, s)] = tilt(hist(g.runs_rest), theta)
        self.rest = rest
        inn = {}
        pooled = hist(hi.runs)
        for i in range(1, 10):
            sub = hi[(hi.inning == i) & (hi.is_top | (i <= 8))]   # bottom of the 9th is often cut short: use tops
            # shrink each inning's histogram toward the all-innings one (20 pseudo-innings) so no score has p = 0
            raw = (hist(sub.runs) * len(sub) + 20 * pooled) / (len(sub) + 20)
            inn[i] = tilt(raw, theta)
        self.inn = inn
        self.extra = rest[(2, 0, 0, 0)]                           # extra innings start with a runner on second
        # suffix[j] = distribution of runs a team scores in its whole half-innings j..9
        suffix = {10: np.eye(1, L * 3, 0).ravel()}
        for j in range(9, 0, -1):
            suffix[j] = np.convolve(inn[j], suffix[j + 1])[: L * 3]
        self.suffix = suffix

    def diff_dist(self, inning, is_top, b, o, ba, s):
        """Distribution of (home runs still to come - away runs still to come), offset by K."""
        cur = self.rest[(b, o, ba, s)]
        if inning >= 10:
            if is_top:
                away, home = cur, self.extra
            else:
                away, home = np.array([1.0]), cur
        elif is_top:
            away = np.convolve(cur, self.suffix[inning + 1])
            home = self.suffix[inning]
        else:
            home = np.convolve(cur, self.suffix[inning + 1])
            away = self.suffix[inning + 1]
        d = np.convolve(home, away[::-1])              # index k  <->  home - away = k - (len(away) - 1)
        return d, len(away) - 1

    def we_home(self, inning, is_top, b, o, ba, s, margins):
        """P(home wins) for each home margin in `margins` (ties after regulation are coin flips)."""
        d, off = self.diff_dist(min(inning, 10), is_top, b, o, ba, s)
        k = np.arange(len(d)) - off
        cdf_gt = np.array([d[(m + k) > 0].sum() + 0.5 * d[(m + k) == 0].sum() for m in margins])
        return cdf_gt


def we_grid(model, margins=range(-15, 16)):
    rows = []
    margins = list(margins)
    for inning in range(1, 11):
        for is_top in (True, False):
            for b in range(8):
                for o in range(3):
                    for (ba, s) in COUNTS:
                        w = model.we_home(inning, is_top, b, o, ba, s, margins)
                        for m, v in zip(margins, w):
                            rows.append((inning, is_top, b, o, ba, s, m, v))
    return pd.DataFrame(rows, columns=["inning", "is_top", "base_state", "outs", "balls", "strikes", "margin", "we"])


class WE:
    """Fast lookup of P(home win), with game-over and half-inning transitions handled explicitly."""

    def __init__(self, grid):
        g = grid.copy()
        self.lo, self.hi = g.margin.min(), g.margin.max()
        self.a = np.zeros((11, 2, 8, 3, 4, 3, self.hi - self.lo + 1))
        self.a[g.inning, (~g.is_top).astype(int), g.base_state, g.outs, g.balls, g.strikes, g.margin - self.lo] = g.we

    def at(self, inning, is_top, b, o, ba, s, margin):
        m = int(np.clip(margin, self.lo, self.hi))
        return self.a[min(inning, 10), 0 if is_top else 1, b, o, ba, s, m - self.lo]

    def half_start(self, inning, is_top, margin):
        """P(home win) at the first pitch of a half-inning, or the final result if the game is already over."""
        if not is_top and inning >= 9 and margin > 0:
            return 1.0                                # home leads after the top of the 9th or later: no bottom half
        b = 2 if inning >= 10 else 0
        return self.at(inning, is_top, b, 0, 0, 0, margin)


# ----------------------------------------------------------------------------------------------- transitions
def walk(b):
    b1, b2, b3 = b & 1, (b >> 1) & 1, (b >> 2) & 1
    runs = b1 & b2 & b3
    nb = 1 | ((b2 | b1) << 1) | ((b3 | (b1 & b2)) << 2)
    return nb, runs


def after_call(we, inning, is_top, b, o, ba, s, margin, call):
    """P(home win) right after a ball ('B') or strike ('S') is called in this state."""
    sign = -1 if is_top else 1                        # runs by the batting team move the home margin by sign*runs
    if call == "B":
        if ba < 3:
            return we.at(inning, is_top, b, o, ba + 1, s, margin)
        nb, runs = walk(b)
        m = margin + sign * runs
        if not is_top and inning >= 9 and m > 0:
            return 1.0                                # bases-loaded walk-off
        return we.at(inning, is_top, nb, o, 0, 0, m)
    if s < 2:
        return we.at(inning, is_top, b, o, ba, s + 1, margin)
    if o < 2:
        return we.at(inning, is_top, b, o + 1, 0, 0, margin)
    # strike three for the third out: the half-inning is over
    if is_top:
        if inning >= 9 and margin > 0:
            return 1.0
        return we.half_start(inning, False, margin)
    if inning >= 9 and margin != 0:
        return 1.0 if margin > 0 else 0.0
    return we.half_start(inning + 1, True, margin)


def after_call_runs(re, b, o, ba, s, call):
    """Run expectancy (batting team, rest of the half-inning) right after the call."""
    if call == "B":
        if ba < 3:
            return re[(b, o, ba + 1, s)]
        nb, runs = walk(b)
        return runs + re[(nb, o, 0, 0)]
    if s < 2:
        return re[(b, o, ba, s + 1)]
    return 0.0 if o == 2 else re[(b, o + 1, 0, 0)]


# ----------------------------------------------------------------------------------------------- main
def main():
    con = duckdb.connect(str(DB))
    pr = con.execute("""
        SELECT p.game_pk, p.inning, p.is_top, p.outs, p.base_state, p.balls, p.strikes, r.runs_rest,
               g.venue_id = 19 AS coors
        FROM pitches p JOIN pitch_runs r USING (game_pk, at_bat_number, pitch_number) JOIN games g USING (game_pk)
        WHERE r.complete AND p.balls <= 3 AND p.strikes <= 2 AND p.outs <= 2""").df()
    hi = con.execute("""
        SELECT h.*, g.venue_id = 19 AS coors FROM half_innings h JOIN games g USING (game_pk)
        WHERE h.complete AND h.inning <= 9""").df()

    # RE288: average runs from each base-out-count state to the end of the half-inning
    re_tab = pr.groupby(["base_state", "outs", "balls", "strikes"]).runs_rest.agg(["mean", "size"]).reset_index()
    re = {(r.base_state, r.outs, r.balls, r.strikes): r["mean"] for _, r in re_tab.iterrows()}
    con.register("re_df", re_tab.rename(columns={"mean": "re", "size": "n"}))
    con.execute("CREATE OR REPLACE TABLE re288 AS SELECT * FROM re_df")

    league = RunModel(pr[~pr.coors], hi[~hi.coors])

    # Coors: one tilt parameter, fitted by maximum likelihood to the runs in every Coors half-inning
    ch = hi[hi.coors]

    def nll(theta):
        return -sum(np.log(tilt(league.inn[i], theta)[min(r, L - 1)]) for i, r in zip(ch.inning, ch.runs))
    theta = minimize_scalar(nll, bounds=(-0.5, 0.8), method="bounded").x
    coors = RunModel(pr[~pr.coors], hi[~hi.coors], theta=theta)
    emp_coors = hist(ch.runs)[:6]
    fit_coors = np.mean([tilt(league.inn[i], theta) for i in ch.inning], axis=0)[:6]

    grid_l, grid_c = we_grid(league), we_grid(coors)
    grid_l["env"], grid_c["env"] = "league", "coors"
    con.register("grid_df", pd.concat([grid_l, grid_c]))
    con.execute("CREATE OR REPLACE TABLE we_grid AS SELECT * FROM grid_df")
    we_l, we_c = WE(grid_l), WE(grid_c)

    # value of flipping each called pitch, for the team the umpire's call went against
    c = con.execute("""
        SELECT c.game_pk, c.at_bat_number, c.pitch_number, c.inning, c.is_top, c.base_state, c.outs, c.balls,
               c.strikes, c.home_score - c.away_score AS margin, g.venue_id = 19 AS coors, c.home_win_exp,
               c.delta_run_exp, c.final_call
        FROM called c JOIN games g USING (game_pk)
        WHERE c.balls <= 3 AND c.strikes <= 2 AND c.outs <= 2""").df()
    vals = np.zeros((len(c), 5))
    for i, r in enumerate(c.itertuples(index=False)):
        st = (r.inning, r.is_top, r.base_state, r.outs, r.balls, r.strikes)
        sign = 1 if not r.is_top else -1             # home perspective -> batting-team perspective
        out = []
        for we in (we_l, we_c):
            pb = after_call(we, *st, r.margin, "B")
            ps = after_call(we, *st, r.margin, "S")
            out.append(sign * (pb - ps))             # win probability the batting team gains from 'ball' over 'strike'
        rb = after_call_runs(re, r.base_state, r.outs, r.balls, r.strikes, "B")
        rs = after_call_runs(re, r.base_state, r.outs, r.balls, r.strikes, "S")
        cur_we = we_l.at(*st, r.margin)
        vals[i] = (out[0], out[1], rb - rs, cur_we, rs - re[(r.base_state, r.outs, r.balls, r.strikes)])
    c["v_wp"], c["v_wp_coors"], c["v_runs"], c["we_home"], c["re_delta_strike"] = vals.T
    c["v_wp_env"] = np.where(c.coors, c.v_wp_coors, c.v_wp)
    keep = ["game_pk", "at_bat_number", "pitch_number", "v_wp", "v_wp_coors", "v_wp_env", "v_runs", "we_home"]
    con.register("v_df", c[keep])
    con.execute("CREATE OR REPLACE TABLE called_values AS SELECT * FROM v_df")

    # ---------------- validation
    checks = {}
    # (1) our RE288 transitions vs Savant's published delta_run_exp on called strikes
    cs = c[c.final_call == "S"].dropna(subset=["delta_run_exp"])
    by_count = cs.groupby(["balls", "strikes"]).agg(ours=("re_delta_strike", "mean"), savant=("delta_run_exp", "mean"))
    checks["re288_vs_savant_called_strikes"] = {
        "n": int(len(cs)),
        "by_count": [{"count": f"{b}-{s}", "ours": float(r.ours), "savant": float(r.savant)}
                     for (b, s), r in by_count.iterrows()],
        "count_means_corr": float(np.corrcoef(by_count.ours, by_count.savant)[0, 1]),
        "count_means_max_abs_diff": float((by_count.ours - by_count.savant).abs().max()),
        "pitch_level_corr": float(np.corrcoef(cs.re_delta_strike, cs.delta_run_exp)[0, 1]),
        "note": "Savant's per-pitch values barely vary with runners/outs inside a count; ours do (RE288)."}
    # (2) win expectancy vs Savant's published home_win_exp, and both vs who actually won
    gm = con.execute("SELECT game_pk, (home_runs > away_runs)::INT AS home_win FROM games").df()
    cc = c.dropna(subset=["home_win_exp"]).merge(gm, on="game_pk")
    w = 1.0 / cc.groupby("game_pk").game_pk.transform("size")      # every game counts once
    brier = lambda p: float(np.sum(w * (p - cc.home_win) ** 2) / w.sum())
    checks["we_vs_savant"] = {"n_pitches": int(len(cc)), "corr": float(np.corrcoef(cc.we_home, cc.home_win_exp)[0, 1]),
                              "mean_abs_diff": float((cc.we_home - cc.home_win_exp).abs().mean()),
                              "brier_ours": brier(cc.we_home), "brier_savant": brier(cc.home_win_exp)}
    bins = np.clip((cc.we_home * 10).astype(int), 0, 9)
    checks["we_calibration_deciles"] = [
        {"bin": int(b), "pred": float(np.average(g.we_home, weights=w[g.index])),
         "actual": float(np.average(g.home_win, weights=w[g.index])), "n": int(len(g))} for b, g in cc.groupby(bins)]
    # (3) Coors tilt
    co = cc[cc.coors]
    wc = 1.0 / co.groupby("game_pk").game_pk.transform("size")
    we_coors_at = np.array([we_c.at(r.inning, r.is_top, r.base_state, r.outs, r.balls, r.strikes, r.margin)
                            for r in co.itertuples(index=False)])
    checks["coors"] = {
        "theta": float(theta), "half_innings": int(len(ch)),
        "runs_per_half_inning_coors": float(ch.runs.mean()),
        "runs_per_half_inning_elsewhere": float(hi[~hi.coors].runs.mean()),
        "empirical_p0to5": [float(x) for x in emp_coors], "tilted_p0to5": [float(x) for x in fit_coors],
        "brier_coors_games_league_model": float(np.sum(wc * (co.we_home - co.home_win) ** 2) / wc.sum()),
        "brier_coors_games_coors_model": float(np.sum(wc * (we_coors_at - co.home_win) ** 2) / wc.sum()),
        "n_games": int(co.game_pk.nunique())}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "values_checks.json").write_text(json.dumps(checks, indent=1))
    print(json.dumps({k: v for k, v in checks.items() if k != "we_calibration_deciles"}, indent=1))


if __name__ == "__main__":
    main()
