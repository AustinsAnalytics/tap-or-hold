// The decision model in the browser. Mirrors pipeline/values.py (win expectancy transitions) and
// pipeline/policy.py (break-even confidence). Every number the dugout card and the calculator show comes from here.
export const M = { ready: false };

export async function loadModel(base = "data/") {
  const [meta, model, buf] = await Promise.all([
    fetch(base + "we_meta.json").then((r) => r.json()),
    fetch(base + "model.json").then((r) => r.json()),
    fetch(base + "we.bin").then((r) => r.arrayBuffer()),
  ]);
  M.we = new Uint16Array(buf);
  M.shape = meta.shape;               // [env, inning, half, bases, outs, balls, strikes, margin]
  M.lo = meta.margin_lo;
  M.scale = meta.scale;
  M.re = new Map(model.re288.map(([b, o, ba, s, v]) => [`${b}${o}${ba}${s}`, v]));
  M.delta = new Map(model.delta.map(([inn, h, mg, k, d]) => [`${inn}|${h}|${mg}|${k}`, d]));
  M.belief = model.belief;
  M.sigma = model.sigma;
  M.flatCost = model.flat_cost_runs;
  M.ready = true;
  return M;
}

const ENV = { league: 0, coors: 1 };

export function weAt(env, inning, isTop, b, o, ba, s, margin) {
  const nm = M.shape[7];
  const m = Math.max(M.lo, Math.min(M.lo + nm - 1, margin));
  const inn = Math.min(inning, 10) - 1;
  const idx = ((((((ENV[env] * 10 + inn) * 2 + (isTop ? 0 : 1)) * 8 + b) * 3 + o) * 4 + ba) * 3 + s) * nm + (m - M.lo);
  return M.we[idx] / M.scale;
}

function halfStart(env, inning, isTop, margin) {
  if (!isTop && inning >= 9 && margin > 0) return 1; // home ahead after the top of the 9th or later: game over
  return weAt(env, inning, isTop, inning >= 10 ? 2 : 0, 0, 0, 0, margin);
}

export function walk(b) {
  const b1 = b & 1, b2 = (b >> 1) & 1, b3 = (b >> 2) & 1;
  return [1 | ((b2 | b1) << 1) | ((b3 | (b1 & b2)) << 2), b1 & b2 & b3];
}

// P(home win) right after a ball ("B") or strike ("S") in this state; margin is home minus away.
export function afterCall(env, inning, isTop, b, o, ba, s, margin, call) {
  const sign = isTop ? -1 : 1;
  if (call === "B") {
    if (ba < 3) return weAt(env, inning, isTop, b, o, ba + 1, s, margin);
    const [nb, runs] = walk(b);
    const m = margin + sign * runs;
    if (!isTop && inning >= 9 && m > 0) return 1;
    return weAt(env, inning, isTop, nb, o, 0, 0, m);
  }
  if (s < 2) return weAt(env, inning, isTop, b, o, ba, s + 1, margin);
  if (o < 2) return weAt(env, inning, isTop, b, o + 1, 0, 0, margin);
  if (isTop) {
    if (inning >= 9 && margin > 0) return 1;
    return halfStart(env, inning, false, margin);
  }
  if (inning >= 9 && margin !== 0) return margin > 0 ? 1 : 0;
  return halfStart(env, inning + 1, true, margin);
}

function reAfter(b, o, ba, s, call) {
  if (call === "B") {
    if (ba < 3) return M.re.get(`${b}${o}${ba + 1}${s}`);
    const [nb, runs] = walk(b);
    return runs + M.re.get(`${nb}${o}00`);
  }
  if (s < 2) return M.re.get(`${b}${o}${ba}${s + 1}`);
  return o === 2 ? 0 : M.re.get(`${b}${o + 1}00`);
}

export function deltaOf(inning, teamHome, margin, k) {
  const inn = Math.min(inning, 10);
  const mg = Math.max(-4, Math.min(4, margin));
  return M.delta.get(`${inn}|${teamHome ? 1 : 0}|${mg}|${Math.min(k, 2)}`);
}

// The whole decision for one situation. margin is the deciding club's lead; batting = the club is hitting.
export function decide({ env = "league", inning, teamHome, batting, margin, bases, outs, balls, strikes, k }) {
  const isTop = teamHome ? !batting : batting;           // visitors hit in the top half
  if (!isTop && inning >= 9 && (teamHome ? margin : -margin) > 0) return null;   // that half-inning is never played
  const homeMargin = teamHome ? margin : -margin;
  const pb = afterCall(env, inning, isTop, bases, outs, balls, strikes, homeMargin, "B");
  const ps = afterCall(env, inning, isTop, bases, outs, balls, strikes, homeMargin, "S");
  const v = Math.abs(pb - ps);
  const vRuns = Math.abs(reAfter(bases, outs, balls, strikes, "B") - reAfter(bases, outs, balls, strikes, "S"));
  const d = deltaOf(inning, teamHome, margin, k);
  const pStar = d / (v + d);
  const pFlat = M.flatCost / (M.flatCost + vRuns);
  const teamNow = weAt(env, inning, isTop, bases, outs, balls, strikes, homeMargin);
  return { v, vRuns, d, pStar, pFlat, weTeam: teamHome ? teamNow : 1 - teamNow, isTop };
}

// Perceived miss (inches) at which a calibrated player reaches belief p, for hitters ("offense") or the battery.
export function inchesFor(side, p) {
  const c = M.belief[side];
  for (let i = 0; i < c.p.length; i++) if (c.p[i] >= p) return c.w_hat[i];
  return null;
}
