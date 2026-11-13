import { M, loadModel, decide, inchesFor } from "./model.js";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const pct = (x, d = 0) => (x == null ? "–" : (100 * x).toFixed(d) + "%");
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const COUNTS = [[0, 0], [0, 1], [0, 2], [1, 0], [1, 1], [1, 2], [2, 0], [2, 1], [2, 2], [3, 0], [3, 1], [3, 2]];
let STORY = null;

/* ------------------------------------------------------------------ theme + nav */
function initTheme() {
  const btn = $("#theme");
  const saved = (() => { try { return localStorage.getItem("toh-theme"); } catch { return null; } })();
  if (saved) document.documentElement.dataset.theme = saved;
  btn?.addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("toh-theme", next); } catch { /* private mode */ }
    redrawAll();
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", redrawAll);
}

function initNav() {
  const links = $$(".nav a");
  const obs = new IntersectionObserver((es) => {
    es.forEach((e) => {
      if (e.isIntersecting) links.forEach((a) => a.classList.toggle("on", a.getAttribute("href") === "#" + e.target.id));
    });
  }, { rootMargin: "-45% 0px -50% 0px" });
  $$("section[id]").forEach((s) => obs.observe(s));
}

/* ------------------------------------------------------------------ small UI helpers */
function seg(el, options, value, onChange) {
  el.innerHTML = "";
  options.forEach(([v, label]) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    b.setAttribute("aria-pressed", String(v === value));
    b.addEventListener("click", () => {
      $$("button", el).forEach((x) => x.setAttribute("aria-pressed", "false"));
      b.setAttribute("aria-pressed", "true");
      onChange(v);
    });
    el.appendChild(b);
  });
}

const tip = (() => {
  const t = document.createElement("div");
  t.className = "tip";
  document.body.appendChild(t);
  return {
    show(html, ev) { t.innerHTML = html; t.style.opacity = 1; this.move(ev); },
    move(ev) {
      const x = Math.min(ev.clientX + 14, window.innerWidth - t.offsetWidth - 8);
      t.style.left = x + "px"; t.style.top = ev.clientY + 14 + "px";
    },
    hide() { t.style.opacity = 0; },
  };
})();

/* ------------------------------------------------------------------ dugout card */
const cardState = { env: "league", k: 1, teamHome: true, batting: false, margin: 0, bases: 0, outs: 1, show: "pstar" };

function colorFor(p) {
  // low confidence needed (tap freely) = strong accent; high = pale
  const strong = d3.color(css("--accent"));
  const pale = d3.color(css("--surface-2"));
  const t = Math.max(0, Math.min(1, (p - 0.05) / 0.9));
  return d3.interpolateRgb(strong, pale)(Math.pow(t, 0.8));
}

function renderCard() {
  const s = cardState;
  const innings = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
  const head = `<tr><th></th>${innings.map((i) => `<th>${i === 10 ? "10+" : i}</th>`).join("")}</tr>`;
  const rows = COUNTS.map(([b, k]) => {
    const tds = innings.map((inn) => {
      const r = decide({ env: s.env, inning: inn, teamHome: s.teamHome, batting: s.batting, margin: s.margin,
        bases: s.bases, outs: s.outs, balls: b, strikes: k, k: s.k });
      if (!r) return `<td class="muted" title="Not played">·</td>`;
      const val = s.show === "flat" ? r.pFlat : r.pStar;
      const bg = colorFor(val);
      const dark = d3.hsl(bg).l < 0.55;
      const label = val >= 0.995 ? "—" : Math.round(val * 100);
      return `<td style="background:${bg};color:${dark ? "#fff" : css("--ink")}" data-b="${b}" data-s="${k}" data-i="${inn}">${label}</td>`;
    }).join("");
    return `<tr><th class="row">${b}-${k}</th>${tds}</tr>`;
  }).join("");
  $("#card-table").innerHTML = `<thead>${head}</thead><tbody>${rows}</tbody>`;
  $$("#card-table td[data-i]").forEach((td) => {
    td.addEventListener("mousemove", (ev) => {
      const r = decide({ env: s.env, inning: +td.dataset.i, teamHome: s.teamHome, batting: s.batting, margin: s.margin,
        bases: s.bases, outs: s.outs, balls: +td.dataset.b, strikes: +td.dataset.s, k: s.k });
      tip.show(`<b>${td.dataset.b}-${td.dataset.s}, inning ${td.dataset.i}</b><br>Call worth ${pct(r.v, 1)} win prob.<br>
        Challenge worth ${pct(r.d, 2)}<br>Tap if ≥ <b>${pct(r.pStar)}</b> sure<br>
        <span style="opacity:.75">Flat 0.2-run rule: ${pct(r.pFlat)}</span>`, ev);
    });
    td.addEventListener("mouseleave", () => tip.hide());
  });
  const side = s.batting ? "offense" : "defense";
  $("#card-caption").textContent =
    `${s.batting ? "Your hitter" : "Your catcher"} must be at least this sure (%) the call was wrong before tapping. ` +
    `${s.env === "coors" ? "Coors Field run environment." : "League-average run environment."}`;
  const inch = inchesFor(side, 0.5);
  $("#card-inch").textContent = inch == null ? "" :
    `For calibration: a typical 2026 ${s.batting ? "hitter" : "catcher"} who sees a miss of about ${inch.toFixed(1)} in. should be about 50% sure.`;
}

function initCard() {
  seg($("#c-env"), [["league", "Road / league"], ["coors", "Coors Field"]], cardState.env, (v) => { cardState.env = v; renderCard(); });
  seg($("#c-k"), [[2, "2 left"], [1, "1 left"]], cardState.k, (v) => { cardState.k = v; renderCard(); });
  seg($("#c-home"), [[true, "Home"], [false, "Away"]], cardState.teamHome, (v) => { cardState.teamHome = v; renderCard(); });
  seg($("#c-side"), [[false, "Defense"], [true, "Offense"]], cardState.batting, (v) => { cardState.batting = v; renderCard(); });
  seg($("#c-margin"), [[-3, "−3"], [-2, "−2"], [-1, "−1"], [0, "Tied"], [1, "+1"], [2, "+2"], [3, "+3"]], cardState.margin,
    (v) => { cardState.margin = v; renderCard(); });
  seg($("#c-outs"), [[0, "0 out"], [1, "1 out"], [2, "2 out"]], cardState.outs, (v) => { cardState.outs = v; renderCard(); });
  seg($("#c-bases"), [[0, "Empty"], [1, "1st"], [2, "2nd"], [3, "1st+2nd"], [4, "3rd"], [5, "1st+3rd"], [6, "2nd+3rd"], [7, "Loaded"]],
    cardState.bases, (v) => { cardState.bases = v; renderCard(); });
  seg($("#c-show"), [["pstar", "This model"], ["flat", "Flat 0.2-run rule"]], cardState.show, (v) => { cardState.show = v; renderCard(); });
  renderCard();
}

/* ------------------------------------------------------------------ calculator */
const calc = { env: "league", teamHome: true, batting: false, inning: 8, margin: 0, outs: 1, bases: 0, balls: 3, strikes: 2, k: 1, conf: 0.6 };

function renderCalc() {
  const r = decide(calc);
  const box = $("#verdict");
  if (!r) {
    box.className = "verdict";
    box.innerHTML = `<div class="big">—</div><p class="small muted">The home team doesn't bat in the 9th or later when it already leads.</p>`;
    return;
  }
  const tap = calc.conf >= r.pStar;
  box.className = "verdict " + (tap ? "tap" : "hold");
  const callAgainst = calc.batting ? "strike" : "ball";
  box.innerHTML = `
    <div class="small muted">The umpire called a ${callAgainst}. You're ${pct(calc.conf)} sure it was wrong.</div>
    <div class="big">${tap ? "Tap" : "Hold"}</div>
    <div class="meter"><div class="bar" style="left:calc(${(100 * r.pStar).toFixed(1)}% - 1px)"></div>
      <div class="you" style="left:${(100 * calc.conf).toFixed(1)}%"></div></div>
    <div class="small muted">Break-even confidence: <b>${pct(r.pStar)}</b> (the black line)</div>
    <dl>
      <dt>Your club's win probability now</dt><dd>${pct(r.weTeam, 1)}</dd>
      <dt>Gain if the call is overturned</dt><dd>+${pct(r.v, 2)}</dd>
      <dt>Value of the challenge you'd risk</dt><dd>${pct(r.d, 2)}</dd>
      <dt>Expected value of tapping now</dt><dd>${(calc.conf * r.v - (1 - calc.conf) * r.d >= 0 ? "+" : "−") + pct(Math.abs(calc.conf * r.v - (1 - calc.conf) * r.d), 2)}</dd>
      <dt>Flat 0.2-run rule would need</dt><dd>${pct(r.pFlat)}</dd>
    </dl>`;
}

function initCalc() {
  seg($("#k-env"), [["league", "Road / league"], ["coors", "Coors Field"]], calc.env, (v) => { calc.env = v; renderCalc(); });
  seg($("#k-home"), [[true, "Home"], [false, "Away"]], calc.teamHome, (v) => { calc.teamHome = v; renderCalc(); });
  seg($("#k-side"), [[false, "Defense (ball called)"], [true, "Offense (strike called)"]], calc.batting, (v) => { calc.batting = v; renderCalc(); });
  const inn = $("#k-inning");
  for (let i = 1; i <= 12; i++) inn.insertAdjacentHTML("beforeend", `<option value="${i}" ${i === calc.inning ? "selected" : ""}>${i}</option>`);
  inn.addEventListener("change", () => { calc.inning = +inn.value; renderCalc(); });
  const mg = $("#k-margin");
  for (let i = -6; i <= 6; i++) mg.insertAdjacentHTML("beforeend", `<option value="${i}" ${i === calc.margin ? "selected" : ""}>${i > 0 ? "Up " + i : i < 0 ? "Down " + -i : "Tied"}</option>`);
  mg.addEventListener("change", () => { calc.margin = +mg.value; renderCalc(); });
  seg($("#k-outs"), [[0, "0"], [1, "1"], [2, "2"]], calc.outs, (v) => { calc.outs = v; renderCalc(); });
  seg($("#k-balls"), [[0, "0"], [1, "1"], [2, "2"], [3, "3"]], calc.balls, (v) => { calc.balls = v; renderCalc(); });
  seg($("#k-strikes"), [[0, "0"], [1, "1"], [2, "2"]], calc.strikes, (v) => { calc.strikes = v; renderCalc(); });
  seg($("#k-k"), [[2, "2"], [1, "1"]], calc.k, (v) => { calc.k = v; renderCalc(); });
  $$("#k-bases button").forEach((b) => b.addEventListener("click", () => {
    const bit = +b.dataset.bit;
    calc.bases ^= bit;
    b.setAttribute("aria-pressed", String(!!(calc.bases & bit)));
    renderCalc();
  }));
  const conf = $("#k-conf");
  conf.value = Math.round(calc.conf * 100);
  const lab = $("#k-conf-lab");
  const upd = () => { calc.conf = +conf.value / 100; lab.textContent = conf.value + "%"; renderCalc(); };
  conf.addEventListener("input", upd);
  upd();
}

/* ------------------------------------------------------------------ chart toolkit */
function frame(el, { h = 300, m = { t: 14, r: 18, b: 40, l: 52 } } = {}) {
  el.innerHTML = "";
  const w = Math.max(280, el.clientWidth);
  if (w < 420) m = { ...m, r: Math.min(m.r, 96), l: Math.min(m.l, 48) };   // phones: keep room for the plot
  const svg = d3.select(el).append("svg").attr("width", w).attr("height", h).attr("viewBox", `0 0 ${w} ${h}`);
  const g = svg.append("g").attr("transform", `translate(${m.l},${m.t})`);
  return { svg, g, w: w - m.l - m.r, h: h - m.t - m.b, m };
}

function axes(f, x, y, { xf = null, yf = null, xt = null, yt = null, xlab = "", ylab = "", xticks = 6, yticks = 5 } = {}) {
  f.g.append("g").attr("class", "grid").call(d3.axisLeft(y).ticks(yticks).tickSize(-f.w).tickFormat(""))
    .call((g) => g.select(".domain").remove());
  const xa = d3.axisBottom(x).ticks(xticks);
  if (xf) xa.tickFormat(xf);
  if (xt) xa.tickValues(xt);
  f.g.append("g").attr("class", "axis").attr("transform", `translate(0,${f.h})`).call(xa);
  const ya = d3.axisLeft(y).ticks(yticks);
  if (yf) ya.tickFormat(yf);
  if (yt) ya.tickValues(yt);
  f.g.append("g").attr("class", "axis").call(ya).call((g) => g.select(".domain").remove());
  if (xlab) f.g.append("text").attr("x", f.w / 2).attr("y", f.h + 34).attr("text-anchor", "middle").text(xlab);
  if (ylab) f.g.append("text").attr("transform", `rotate(-90)`).attr("x", -f.h / 2).attr("y", -40).attr("text-anchor", "middle").text(ylab);
}

function dodge(labels, gap = 14, lo = -6, hi = Infinity) {
  // push end-of-line labels apart vertically so they never overlap
  const items = labels.map((t) => ({ t, y: +t.attr("y") })).sort((a, b) => a.y - b.y);
  for (let i = 1; i < items.length; i++) if (items[i].y - items[i - 1].y < gap) items[i].y = items[i - 1].y + gap;
  const over = items.length ? items[items.length - 1].y - hi : 0;
  if (over > 0) items.forEach((d) => { d.y -= over; });
  for (let i = items.length - 2; i >= 0; i--) if (items[i + 1].y - items[i].y < gap) items[i].y = items[i + 1].y - gap;
  items.forEach((d) => d.t.attr("y", Math.max(lo, d.y)));
}

function lines(f, x, y, series, { dots = true } = {}) {
  const labs = [];
  series.forEach((s) => {
    const line = d3.line().defined((d) => d[1] != null).x((d) => x(d[0])).y((d) => y(d[1]));
    f.g.append("path").datum(s.data).attr("fill", "none").attr("stroke", s.color).attr("stroke-width", s.width || 2.5)
      .attr("stroke-dasharray", s.dash || null).attr("d", line);
    if (dots && !s.noDots) {
      f.g.selectAll(null).data(s.data.filter((d) => d[1] != null)).join("circle").attr("cx", (d) => x(d[0])).attr("cy", (d) => y(d[1]))
        .attr("r", s.r || 3.5).attr("fill", s.color)
        .on("mousemove", (ev, d) => tip.show(s.tip ? s.tip(d) : `${s.name}: ${d[1]}`, ev)).on("mouseleave", () => tip.hide());
    }
    if (s.label) {
      const last = s.labelAt ? s.data.find((d) => d[0] === s.labelAt) : s.data[s.data.length - 1];
      labs.push(f.g.append("text").attr("class", "lab").attr("x", x(last[0]) + (s.dx ?? 8)).attr("y", y(last[1]) + (s.dy ?? 4))
        .attr("text-anchor", s.anchor || "start").style("fill", s.color).text(s.label));
    }
  });
  f.labels = (f.labels || []).concat(labs);
  dodge(f.labels, 14, -4, f.h + 4);
}

/* ------------------------------------------------------------------ charts */
function chartPrice() {
  const el = $("#ch-price");
  if (!el) return;
  const P = STORY.price;
  const f = frame(el, { h: 320, m: { t: 16, r: 120, b: 42, l: 54 } });
  const x = d3.scaleLinear().domain([1, 10]).range([0, f.w]);
  const ymax = d3.max([...P.k1, ...P.k2]) * 1.15;
  const y = d3.scaleLinear().domain([0, Math.max(ymax, 0.3)]).range([f.h, 0]);
  axes(f, x, y, { xt: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], xf: (d) => (d === 10 ? "10+" : d), yf: (d) => d.toFixed(2),
    xlab: "Inning", ylab: "Price of a lost challenge (runs)" });
  f.g.append("line").attr("x1", 0).attr("x2", f.w).attr("y1", y(0.2)).attr("y2", y(0.2)).attr("stroke", css("--chart-4"))
    .attr("stroke-dasharray", "6 4").attr("stroke-width", 2);
  f.g.append("text").attr("class", "lab").attr("x", f.w + 8).attr("y", y(0.2) + 4).style("fill", css("--chart-4")).text("Flat 0.2 rule");
  lines(f, x, y, [
    { name: "Last challenge", color: css("--chart-1"), data: P.innings.map((i, j) => [i, P.k1[j]]), label: "Last challenge",
      tip: (d) => `Inning ${d[0]}: last challenge worth ${d[1].toFixed(3)} runs` },
    { name: "One of two", color: css("--chart-2"), data: P.innings.map((i, j) => [i, P.k2[j]]), label: "One of two",
      tip: (d) => `Inning ${d[0]}: second-to-last challenge worth ${d[1].toFixed(3)} runs` },
  ]);
}

function chartPriceWP() {
  const el = $("#ch-price-wp");
  if (!el) return;
  const P = STORY.price_wp;
  const f = frame(el, { h: 300, m: { t: 16, r: 110, b: 42, l: 54 } });
  const x = d3.scaleLinear().domain([1, 10]).range([0, f.w]);
  const all = P.series.flatMap((s) => s.values);
  const y = d3.scaleLinear().domain([0, d3.max(all) * 1.12]).range([f.h, 0]);
  axes(f, x, y, { xt: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], xf: (d) => (d === 10 ? "10+" : d), yf: (d) => (100 * d).toFixed(1) + "%",
    xlab: "Inning", ylab: "Value of last challenge (win prob.)" });
  const colors = [css("--chart-1"), css("--chart-2"), css("--chart-3"), css("--chart-4")];
  lines(f, x, y, P.series.map((s, i) => ({ name: s.name, color: colors[i % 4], data: P.innings.map((inn, j) => [inn, s.values[j]]),
    label: s.name, tip: (d) => `${s.name}, inning ${d[0]}: ${pct(d[1], 2)}` })));
}

function chartStakes() {
  const el = $("#ch-stakes");
  if (!el) return;
  const S = STORY.stakes;
  const f = frame(el, { h: 320, m: { t: 16, r: 120, b: 46, l: 54 } });
  const x = d3.scaleLinear().domain([1, 10]).range([0, f.w]);
  const y = d3.scaleLinear().domain([0, Math.max(0.6, d3.max(S, (d) => Math.max(d.actual, d.card)) * 1.1)]).range([f.h, 0]);
  axes(f, x, y, { xt: d3.range(1, 11), yf: (d) => pct(d), xlab: "Stakes decile (1 = cheapest calls, 10 = most valuable)",
    ylab: "Share of clear misses challenged" });
  lines(f, x, y, [
    { name: "2026 players", color: css("--chart-4"), data: S.map((d) => [d.decile, d.actual]), label: "2026 players",
      tip: (d) => `Decile ${d[0]}: players tapped ${pct(d[1])}` },
    { name: "This card", color: css("--chart-1"), data: S.map((d) => [d.decile, d.card]), label: "This card",
      tip: (d) => `Decile ${d[0]}: the card would tap ${pct(d[1])}` },
  ]);
}

function chartCurve() {
  const el = $("#ch-curve");
  if (!el) return;
  const C = STORY.curve;
  const f = frame(el, { h: 300, m: { t: 16, r: 110, b: 46, l: 54 } });
  const x = d3.scaleLinear().domain([-4, 4.5]).range([0, f.w]);
  const y = d3.scaleLinear().domain([0, Math.max(0.5, d3.max(C.filter((d) => d.n >= 25 && d.w_mid <= 4.5), (d) => d.rate) * 1.1)]).range([f.h, 0]);
  axes(f, x, y, { yf: (d) => pct(d), xlab: "How wrong the call really was (in.; > 0 = ABS overturns it)", ylab: "Share challenged" });
  f.g.append("line").attr("x1", x(0)).attr("x2", x(0)).attr("y1", 0).attr("y2", f.h).attr("stroke", css("--rule")).attr("stroke-width", 2);
  const sides = [["defense", css("--chart-1"), "Catchers & pitchers"], ["offense", css("--chart-2"), "Hitters"]];
  sides.forEach(([side, color, name]) => {
    const pts = C.filter((d) => d.side === side && d.w_mid >= -4 && d.w_mid <= 4.5 && d.n >= 25);
    lines(f, x, y, [{ name, color, data: pts.map((d) => [d.w_mid, d.fit]), noDots: true, width: 2, label: name }]);
    f.g.selectAll(null).data(pts).join("circle").attr("cx", (d) => x(d.w_mid)).attr("cy", (d) => y(d.rate)).attr("r", 4)
      .attr("fill", "none").attr("stroke", color).attr("stroke-width", 2)
      .on("mousemove", (ev, d) => tip.show(`${name}<br>${d.w_mid > 0 ? "Missed by" : "Correct by"} ~${Math.abs(d.w_mid).toFixed(2)} in.<br>
        Challenged ${pct(d.rate, 1)} of ${d.n.toLocaleString()} calls`, ev)).on("mouseleave", () => tip.hide());
  });
}

function chartZoneScan() {
  const el = $("#ch-zone");
  if (!el) return;
  const Z = STORY.zone_scan;
  const f = frame(el, { h: 280, m: { t: 16, r: 120, b: 46, l: 54 } });
  const x = d3.scaleLinear().domain(d3.extent(Z.radii)).range([0, f.w]);
  const y = d3.scaleLinear().domain([Math.min(0.8, d3.min(Z.mid)), 1]).range([f.h, 0]);
  axes(f, x, y, { yf: (d) => pct(d), xlab: "Ball radius assumed (in.)", ylab: "Challenges reproduced" });
  lines(f, x, y, [
    { name: "Published plate coordinates", color: css("--chart-1"), data: Z.radii.map((r, i) => [r, Z.front[i]]), noDots: true,
      label: "As published", width: 3 },
    { name: "Extrapolated to mid-plate", color: css("--chart-2"), data: Z.radii.map((r, i) => [r, Z.mid[i]]), noDots: true,
      label: "Moved to mid-plate", width: 2.5, dash: "5 4" },
  ]);
}

function chartUmp() {
  const el = $("#ch-ump");
  if (!el) return;
  const U = STORY.ump_map[umpStand];
  el.innerHTML = "";
  const w = Math.min(el.clientWidth, 460), h = w * 1.05;
  const svg = d3.select(el).append("svg").attr("width", w).attr("height", h).attr("viewBox", `0 0 ${w} ${h}`);
  const m = { t: 10, r: 10, b: 36, l: 44 };
  const x = d3.scaleLinear().domain([-17, 17]).range([m.l, w - m.r]);
  const y = d3.scaleLinear().domain([-0.45, 1.45]).range([h - m.b, m.t]);
  const col = d3.scaleDiverging([-0.6, 0, 0.6], d3.interpolateRgbBasis([css("--chart-3"), css("--surface"), css("--chart-2")]));
  const cw = Math.abs(x(U.dx) - x(0)) + 0.6, chh = Math.abs(y(0) - y(U.dz)) + 0.6;
  svg.append("g").selectAll("rect").data(U.cells).join("rect")
    .attr("x", (d) => x(d[0] - U.dx / 2)).attr("y", (d) => y(d[1] + U.dz / 2)).attr("width", cw).attr("height", chh)
    .attr("fill", (d) => col(d[2]))
    .on("mousemove", (ev, d) => tip.show(`Umpire strike rate ${pct(d[3])}<br>ABS strike rate ${pct(d[4])}<br>
      ${d[2] > 0 ? "Umpires give extra strikes here" : "Umpires take strikes away here"}`, ev)).on("mouseleave", () => tip.hide());
  // the ABS rectangle (ball centre must be within 1.45 in. of it): draw the rulebook rectangle and the ball-radius band
  const top = 1, bot = 0;
  svg.append("rect").attr("x", x(-8.5)).attr("y", y(top)).attr("width", x(8.5) - x(-8.5)).attr("height", y(bot) - y(top))
    .attr("fill", "none").attr("stroke", css("--ink")).attr("stroke-width", 2);
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(x).ticks(5).tickFormat((d) => d + '"'));
  svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`)
    .call(d3.axisLeft(y).tickValues([0, 0.5, 1]).tickFormat((d) => ({ 0: "bottom", 0.5: "mid", 1: "top" }[d])));
  svg.append("text").attr("x", (m.l + w - m.r) / 2).attr("y", h - 4).attr("text-anchor", "middle").attr("fill", css("--ink-3"))
    .attr("font-size", 12).text("Inches from the middle of the plate (catcher's view)");
}
let umpStand = "R";

function chartTapped() {
  const el = $("#ch-tapped");
  if (!el) return;
  const T = STORY.tapped_by_inning;
  const f = frame(el, { h: 260, m: { t: 16, r: 110, b: 42, l: 54 } });
  const x = d3.scaleLinear().domain([1, 10]).range([0, f.w]);
  const y = d3.scaleLinear().domain([0, d3.max(T, (d) => d.bat_out) * 1.2]).range([f.h, 0]);
  axes(f, x, y, { xt: d3.range(1, 11), xf: (d) => (d === 10 ? "10+" : d), yf: (d) => pct(d), xlab: "Inning", ylab: "Pitches to a tapped-out lineup" });
  lines(f, x, y, [{ name: "Batting club out of challenges", color: css("--chart-1"), data: T.map((d) => [d.inning, d.bat_out]),
    label: "Lineup tapped out", tip: (d) => `Inning ${d[0]}: ${pct(d[1], 1)} of pitches` }]);
}

function chartPolicy() {
  const el = $("#ch-policy");
  if (!el) return;
  const P = STORY.policy;
  const m = { t: 8, r: 70, b: 30, l: 210 };
  el.innerHTML = "";
  const w = Math.max(320, el.clientWidth), h = P.length * 44 + m.t + m.b;
  const svg = d3.select(el).append("svg").attr("width", w).attr("height", h);
  const x = d3.scaleLinear().domain([0, d3.max(P, (d) => d.wins) * 1.05]).range([m.l, w - m.r]);
  const y = d3.scaleBand().domain(P.map((d) => d.label)).range([m.t, h - m.b]).padding(0.28);
  svg.append("g").selectAll("rect").data(P).join("rect").attr("x", x(0)).attr("y", (d) => y(d.label)).attr("height", y.bandwidth())
    .attr("width", (d) => x(d.wins) - x(0)).attr("rx", 4)
    .attr("fill", (d) => (d.key === "card" ? css("--chart-1") : d.key === "every_missed_call" ? css("--hold") : css("--chart-4")));
  svg.append("g").selectAll("text").data(P).join("text").attr("x", m.l - 10).attr("y", (d) => y(d.label) + y.bandwidth() / 2 + 4)
    .attr("text-anchor", "end").attr("fill", css("--ink-2")).attr("font-size", 13).text((d) => d.label);
  svg.append("g").selectAll("text").data(P).join("text").attr("x", (d) => x(d.wins) + 6).attr("y", (d) => y(d.label) + y.bandwidth() / 2 + 4)
    .attr("fill", css("--ink")).attr("font-size", 13).attr("font-weight", 600).text((d) => d.wins.toFixed(2));
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(x).ticks(5));
}

function chartCalib() {
  const el = $("#ch-calib");
  if (!el) return;
  const C = STORY.calibration;
  const f = frame(el, { h: 280, m: { t: 14, r: 20, b: 44, l: 54 } });
  const x = d3.scaleLinear().domain([0, 1]).range([0, f.w]);
  const y = d3.scaleLinear().domain([0, 1]).range([f.h, 0]);
  axes(f, x, y, { xf: (d) => pct(d), yf: (d) => pct(d), xlab: "Predicted home win probability", ylab: "Home teams that won" });
  f.g.append("line").attr("x1", x(0)).attr("y1", y(0)).attr("x2", x(1)).attr("y2", y(1)).attr("stroke", css("--rule")).attr("stroke-width", 2);
  f.g.selectAll(null).data(C).join("circle").attr("cx", (d) => x(d.pred)).attr("cy", (d) => y(d.actual)).attr("r", 6)
    .attr("fill", css("--chart-1"))
    .on("mousemove", (ev, d) => tip.show(`Predicted ${pct(d.pred, 1)}<br>Actual ${pct(d.actual, 1)}<br>${d.n.toLocaleString()} pitches`, ev))
    .on("mouseleave", () => tip.hide());
}

function chartCoors() {
  const el = $("#ch-coors");
  if (!el) return;
  const C = STORY.coors_value;
  const m = { t: 10, r: 16, b: 40, l: 46 };
  el.innerHTML = "";
  const w = Math.max(320, el.clientWidth), h = 280;
  const svg = d3.select(el).append("svg").attr("width", w).attr("height", h);
  const x0 = d3.scaleBand().domain(C.map((d) => d.count)).range([m.l, w - m.r]).padding(0.2);
  const x1 = d3.scaleBand().domain(["league", "coors"]).range([0, x0.bandwidth()]).padding(0.08);
  const y = d3.scaleLinear().domain([0, d3.max(C, (d) => Math.max(d.league, d.coors)) * 1.1]).range([h - m.b, m.t]);
  const colors = { league: css("--chart-4"), coors: css("--chart-1") };
  svg.append("g").selectAll("g").data(C).join("g").attr("transform", (d) => `translate(${x0(d.count)},0)`)
    .selectAll("rect").data((d) => [["league", d.league, d.count], ["coors", d.coors, d.count]]).join("rect")
    .attr("x", (d) => x1(d[0])).attr("y", (d) => y(d[1])).attr("width", x1.bandwidth()).attr("height", (d) => y(0) - y(d[1]))
    .attr("rx", 2).attr("fill", (d) => colors[d[0]])
    .on("mousemove", (ev, d) => tip.show(`${d[2]} count, ${d[0] === "coors" ? "Coors Field" : "league"}<br>A flipped call is worth ${pct(d[1], 2)} win prob. on average`, ev))
    .on("mouseleave", () => tip.hide());
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(x0));
  svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5).tickFormat((d) => pct(d, 1)));
}

/* ------------------------------------------------------------------ tables */
const BASES = ["empty", "1st", "2nd", "1st & 2nd", "3rd", "1st & 3rd", "2nd & 3rd", "loaded"];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const dateFmt = (s) => new Date(s + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" });

function situation(r) {
  const half = r.is_top ? "Top" : "Bot";
  return `${half} ${r.inning}, ${r.outs} out, ${BASES[r.base_state]}, ${r.balls}-${r.strikes}`;
}

function renderRockies() {
  const t1 = $("#t-col-challengers");
  if (t1) {
    t1.innerHTML = `<thead><tr><th>Player</th><th>Role</th><th class="r">Taps</th><th class="r">Won</th><th class="r">Win rate</th>
      <th class="r">Wins gained</th></tr></thead><tbody>${STORY.rockies_challengers.map((r) => `<tr><td>${esc(r.name)}</td>
      <td>${esc(r.role)}</td><td class="r">${r.challenges}</td><td class="r">${r.overturns}</td><td class="r">${pct(r.overturns / r.challenges)}</td>
      <td class="r">${r.wp_gained.toFixed(2)}</td></tr>`).join("")}</tbody>`;
  }
  const t2 = $("#t-col-gotaway");
  if (t2) {
    t2.innerHTML = `<thead><tr><th>Date</th><th>Game</th><th>Situation</th><th>Score</th><th>Call</th><th class="r">Missed by</th>
      <th class="r">Worth</th><th class="r">Challenges left</th></tr></thead><tbody>${STORY.rockies_got_away.map((r) => {
        const opp = r.home_team === "COL" ? `vs ${r.away_team}` : `at ${r.home_team}`;
        const col = r.home_team === "COL" ? r.home_score : r.away_score;
        const oth = r.home_team === "COL" ? r.away_score : r.home_score;
        const call = r.orig_call === "S" ? `strike on ${esc(r.batter)}` : `ball to ${esc(r.batter)}`;
        return `<tr><td>${dateFmt(r.game_date)}</td><td>${opp}</td><td>${situation(r)}</td><td>${col}-${oth}</td><td>${call}</td>
          <td class="r">${r.w_in < 0.1 ? "&lt;0.1" : r.w_in.toFixed(1)} in.</td><td class="r">${pct(r.v, 1)}</td>
          <td class="r">${r.wronged_ch_left === 0 ? '<span class="pill warn">none</span>' : r.wronged_ch_left}</td></tr>`;
      }).join("")}</tbody>`;
  }
  const t3 = $("#t-col-hitters");
  if (t3) {
    t3.innerHTML = `<thead><tr><th>Hitter</th><th class="r">Clear misses</th><th class="r">Tapped</th><th class="r">Win prob. at stake</th></tr></thead>
      <tbody>${STORY.rockies_clear_misses.map((r) => `<tr><td>${esc(r.name)}</td><td class="r">${r.clear_misses}</td>
      <td class="r">${r.tapped}</td><td class="r">${pct(r.wp_at_stake, 1)}</td></tr>`).join("")}</tbody>`;
  }
  const ck = $("#checks");
  if (ck) {
    ck.innerHTML = STORY.checks.map((c) => `<div class="check"><span class="ok">${c.pass ? "✓" : "✗"}</span>
      <span>${esc(c.check)}: <b class="num">${typeof c.value === "number" ? c.value.toLocaleString() : esc(c.value)}</b>
      ${c.note ? `<span class="muted"> · ${esc(c.note)}</span>` : ""}</span></div>`).join("");
  }
}

/* ------------------------------------------------------------------ teams */
function renderTeams(filter = "NL West") {
  const el = $("#teams");
  if (!el) return;
  const list = (filter === "NL West" ? STORY.teams.filter((t) => t.nlwest) : STORY.teams)
    .slice().sort((a, b) => a.name.localeCompare(b.name));
  el.innerHTML = list.map((t) => `
    <div class="team">
      <h4>${t.name}</h4>
      <div class="small muted" style="margin-bottom:8px">${t.games} games · out of challenges ${pct(t.tapped_out_7th_on)} of pitches from the 7th on</div>
      <div class="row"><span>Hitters: taps / game</span><span>${t.offense.per_game.toFixed(2)}</span></div>
      <div class="row"><span>Hitters: win rate</span><span>${pct(t.offense.overturn_rate)}</span></div>
      <div class="row"><span>Battery: taps / game</span><span>${t.defense.per_game.toFixed(2)}</span></div>
      <div class="row"><span>Battery: win rate</span><span>${pct(t.defense.overturn_rate)}</span></div>
      <div class="row"><span>Missed calls fixed</span><span>${pct((t.offense.missed_fixed + t.defense.missed_fixed) / (t.offense.missed + t.defense.missed))}</span></div>
      <div class="row"><span>Wins recovered</span><span>${(t.offense.wp_fixed + t.defense.wp_fixed).toFixed(2)}</span></div>
      <div class="small" style="margin-top:8px"><b>Who taps:</b> ${t.top_challengers.slice(0, 3).map((p) => `${p.name} (${p.role}, ${p.overturns}/${p.challenges})`).join(", ")}</div>
    </div>`).join("");
}

/* ------------------------------------------------------------------ boot */
function redrawAll() {
  if (!STORY) return;
  if (M.ready) renderCard();
  [chartPrice, chartPriceWP, chartStakes, chartCurve, chartZoneScan, chartUmp, chartTapped, chartPolicy, chartCalib, chartCoors].forEach((fn) => {
    try { fn(); } catch (e) { console.error(fn.name, e); }
  });
}

async function main() {
  initTheme();
  initNav();
  const [story] = await Promise.all([fetch("data/story.json").then((r) => r.json()), loadModel()]);
  STORY = story;
  initCard();
  initCalc();
  const us = $("#ump-stand");
  if (us) seg(us, [["R", "Right-handed hitters"], ["L", "Left-handed hitters"]], umpStand, (v) => { umpStand = v; chartUmp(); });
  const tf = $("#team-filter");
  if (tf) seg(tf, [["NL West", "NL West"], ["All", "All 30 clubs"]], "NL West", (v) => renderTeams(v));
  renderTeams("NL West");
  renderRockies();
  redrawAll();
  let t;
  window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(redrawAll, 150); });
}

main().catch((e) => {
  console.error(e);
  const n = $("#load-error");
  if (n) n.hidden = false;
});
