/* kvstats — app.js
   Plain ES2020, no build step. Talks to the Python server on the same origin. */
(() => {
'use strict';

const API = '';                    // same origin

/* ── helpers ─────────────────────────────────────────────── */
const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const pad2 = n => String(n).padStart(2, '0');
const num = (v, d = 1) => v == null || !isFinite(v) ? '—' : v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (v, d = 1) => v == null ? '—' : (v * 100).toFixed(d) + '%';
const signed = (v, d = 1) => (v > 0 ? '+' : v < 0 ? '−' : '') + num(Math.abs(v), d);
const hhmm = iso => { const t = new Date(iso); return pad2(t.getHours()) + ':' + pad2(t.getMinutes()); };
const hhmmss = iso => hhmm(iso) + ':' + pad2(new Date(iso).getSeconds());

const css = k => getComputedStyle(document.documentElement).getPropertyValue(k).trim();
let C = {};
const readColors = () => {
  C = { ink: css('--ink'), dim: css('--dim'), faint: css('--faint'), ghost: css('--ghost'),
        line: css('--line'), line2: css('--line-2'), good: css('--good'), bad: css('--bad'),
        pb: css('--pb'), band: css('--band'), bg: css('--bg-panel') };
};
const alpha = (hex, a) => {
  const h = hex.replace('#', '');
  const n = parseInt(h.length === 3 ? h.split('').map(c => c + c).join('') : h, 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
};

const METRICS = [
  ['score', 'score / s'], ['shots', 'shots / s'], ['hits', 'hits / s'],
  ['kills', 'kills / s'], ['accuracy', 'accuracy'], ['efficiency', 'efficiency']
];
// 0 and 1 both mean "no smoothing" to the server (compare.smooth returns the
// series unchanged for window <= 1), so "raw" is sent as 0.
const SMOOTH = [[0, 'raw'], [3, '3 s'], [5, '5 s']];
const isRatio = m => m === 'accuracy' || m === 'efficiency';

/* ── app state ───────────────────────────────────────────── */
const A = {
  view: 'run',
  ctrl: { metric: 'score', smoothing: 0, recent_n: 10, same_cfg: true },
  runs: [], payload: null, focusedId: null, kbd: -1,
  filterScenario: null, health: null
};

/* ── chart controls persist locally ──────────────────────── */
// The hash carries what you would link to — view, run, scenario filter. The
// chart controls are a reading preference, so they live here instead of
// cluttering every URL. Stored values are re-validated on the way in: a stale
// or hand-edited key must not be able to send garbage to the API.
const CTRL_KEY = 'kvstats.ctrl';
function loadCtrl() {
  let s;
  try { s = JSON.parse(localStorage.getItem(CTRL_KEY) || 'null'); } catch (_) { return; }
  if (!s || typeof s !== 'object') return;
  const c = A.ctrl;
  if (METRICS.some(m => m[0] === s.metric)) c.metric = s.metric;
  if (SMOOTH.some(w => w[0] === +s.smoothing)) c.smoothing = +s.smoothing;
  if (isFinite(+s.recent_n)) c.recent_n = clamp(Math.round(+s.recent_n), 1, 50);
  if (typeof s.same_cfg === 'boolean') c.same_cfg = s.same_cfg;
}
function saveCtrl() {
  try { localStorage.setItem(CTRL_KEY, JSON.stringify(A.ctrl)); } catch (_) {}
}

/* ── hash routing ────────────────────────────────────────── */
/* #/run · #/run/<id> · #/run/<id>?scenario=<name> · #/session · #/scenarios */
const VIEWS = ['run', 'session', 'scenarios'];

function parseHash() {
  const [path, qs] = location.hash.replace(/^#\/?/, '').split('?');
  const seg = path.split('/').filter(Boolean);
  const view = VIEWS.includes(seg[0]) ? seg[0] : 'run';
  return {
    view,
    runId: view === 'run' && /^\d+$/.test(seg[1] || '') ? +seg[1] : null,
    scenario: new URLSearchParams(qs || '').get('scenario') || null
  };
}

function formatHash({ view, runId, scenario }) {
  if (view !== 'run') return '#/' + view;                 // the rail is run-view state
  return '#/run' + (runId == null ? '' : '/' + runId) +
         (scenario ? '?scenario=' + encodeURIComponent(scenario) : '');
}

/* Patch the current route and navigate. Assigning location.hash fires
   hashchange, so applyRoute stays the one place that acts on a route.
   replace = true writes the URL without a history entry — and without firing
   hashchange — for movement the user did not ask for: normalising a bad URL,
   arrowing down the rail, a new run landing over SSE. */
function go(patch, replace) {
  const next = formatHash({ view: A.view, runId: A.focusedId, scenario: A.filterScenario, ...patch });
  if (next === location.hash) return;
  if (replace) history.replaceState(null, '', next);
  else location.hash = next;
}

/* ── fetch layer ─────────────────────────────────────────── */
async function api(path) {
  const r = await fetch(API + path);
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

/* ═══════════════════════════ CHARTS ═══════════════════════ */
let uRate = null, uDelta = null;
const SYNC = uPlot.sync('kv');

const axisBase = () => ({
  stroke: C.faint, font: '11px ' + css('--mono'),
  grid: { stroke: alpha(C.line2, .55), width: 1 },
  ticks: { stroke: alpha(C.line2, .8), width: 1, size: 4 }
});

// A race is indexed by share of the damage pool, a timed run by seconds.
// Kill boundaries only line up on the former, which is why it exists.
const xAxis = p => ({
  ...axisBase(), size: 26,
  values: (u, sp) => sp.map(v => p.axis.kind === 'progress'
    ? Math.round(v * 100) + '%' : v + 's'),
  incrs: p.axis.kind === 'progress'
    ? [.05, .1, .2, .25, .5] : [5, 10, 15, 20, 30, 60]
});

function mkRate(el, data, p) {
  const ratio = isRatio(p.rate.metric);
  const fmtY = v => v == null ? '' : ratio ? (v * 100).toFixed(0) + '%' : num(v, v < 10 ? 1 : 0);
  const opts = {
    width: el.clientWidth, height: el.clientHeight, padding: [10, 12, 0, 0],
    scales: { x: { time: false } },
    legend: { show: false },
    cursor: {
      sync: { key: SYNC.key, scales: ['x', null], setSeries: false },
      drag: { x: false, y: false },
      points: { size: 6, width: 1, stroke: () => C.bg, fill: (u, i) => u.series[i].stroke() }
    },
    axes: [
      xAxis(p),
      { ...axisBase(), size: 48, values: (u, sp) => sp.map(fmtY) }
    ],
    series: [
      {},
      { stroke: 'transparent', points: { show: false } },                                            // 1 lo
      { stroke: 'transparent', points: { show: false } },                                            // 2 hi
      { stroke: alpha(C.band, .85), width: 1, dash: [2, 4], points: { show: false } },               // 3 mean
      { stroke: C.pb, width: 1.5, dash: [6, 4], points: { show: false } },                           // 4 pb
      { stroke: C.ink, width: 2.25, points: { show: false } }                                        // 5 run
    ],
    bands: [{ series: [2, 1], fill: alpha(C.band, .16) }],
    hooks: {
      draw: [u => { drawZero(u, p); drawKills(u, p); drawTail(u, p); }],
      setCursor: [u => readout(u, p)]
    }
  };
  return new uPlot(opts, data, el);
}

function mkDelta(el, data, p) {
  const opts = {
    width: el.clientWidth, height: el.clientHeight, padding: [8, 12, 0, 0],
    scales: { x: { time: false } },
    legend: { show: false },
    cursor: {
      sync: { key: SYNC.key, scales: ['x', null], setSeries: false },
      drag: { x: false, y: false }, points: { show: false }
    },
    axes: [
      xAxis(p),
      { ...axisBase(), size: 48, values: (u, sp) => sp.map(v => (v > 0 ? '+' : '') + num(v, 0)) }
    ],
    series: [{}, { stroke: 'transparent', points: { show: false } }],
    hooks: {
      draw: [u => { drawDelta(u); drawKills(u, p); drawTail(u, p); }],
      setCursor: [u => readout(u, p)]
    }
  };
  return new uPlot(opts, data, el);
}

/* filled-to-zero cumulative delta, green above / red below, drawn by hand
   so the sign split is exact and stays readable at glance distance */
function drawDelta(u) {
  const ctx = u.ctx, d = u.data[1], xs = u.data[0];
  if (!d || d.length < 2) return;
  const { left, top, width, height } = u.bbox;
  const y0 = u.valToPos(0, 'y', true);
  const px = i => u.valToPos(xs[i], 'x', true);
  const py = i => u.valToPos(d[i], 'y', true);

  const area = new Path2D();
  area.moveTo(px(0), y0);
  for (let i = 0; i < d.length; i++) area.lineTo(px(i), py(i));
  area.lineTo(px(d.length - 1), y0);
  area.closePath();

  const clipFill = (yA, yB, color) => {
    if (yB - yA <= 0) return;
    ctx.save(); ctx.beginPath(); ctx.rect(left, yA, width, yB - yA); ctx.clip();
    ctx.fillStyle = color; ctx.fill(area); ctx.restore();
  };
  clipFill(top, clamp(y0, top, top + height), alpha(C.good, .22));
  clipFill(clamp(y0, top, top + height), top + height, alpha(C.bad, .22));

  ctx.save();
  ctx.setLineDash([3, 4]); ctx.lineWidth = 1; ctx.strokeStyle = alpha(C.dim, .55);
  ctx.beginPath(); ctx.moveTo(left, y0); ctx.lineTo(left + width, y0); ctx.stroke();
  ctx.setLineDash([]);
  ctx.lineWidth = 1.5 * devicePixelRatio; ctx.strokeStyle = alpha(C.ink, .75);
  ctx.beginPath();
  for (let i = 0; i < d.length; i++) i ? ctx.lineTo(px(i), py(i)) : ctx.moveTo(px(i), py(i));
  ctx.stroke(); ctx.restore();
}

/* One rule per kill only reads as a boundary while the rules stay countable.
   The busiest real run in the fixtures puts 76 of them over a 60-second axis:
   at ~800 px that is a dashed line every ~10 px across the full plot height,
   which is hatching rather than information, and on a penalising scenario it
   buries the zero rule underneath. Past this many, drawing nothing says more.
   Race marks are exempt: there are at most 8 of them, they are shared between
   runs and labelled, and they are the point of the progress axis. */
const MAX_PER_RUN_KILL_MARKS = 12;

/* Longest prefix of `text` that fits `max` px in the context's current font,
   with an ellipsis when it had to cut. Returns '' when nothing fits. */
function ellipsize(ctx, text, max) {
  if (max <= 0) return '';
  if (ctx.measureText(text).width <= max) return text;
  for (let n = text.length - 1; n > 0; n--) {
    const cut = text.slice(0, n) + '…';
    if (ctx.measureText(cut).width <= max) return cut;
  }
  return '';
}

/* Kill boundaries. For a race these are shared -- kill k sits at damage
   k*pool/bots in every run -- so they are drawn solid and labelled. For a
   timed run they belong to the focused run alone and are drawn subdued,
   because two runs' kills genuinely do not coincide on a clock. */
function drawKills(u, p) {
  const marks = p.marks && p.marks.kills;
  if (!marks || !marks.length) return;
  if (!p.marks.aligned && marks.length > MAX_PER_RUN_KILL_MARKS) return;
  const { top, height } = u.bbox;
  const ctx = u.ctx;
  ctx.save();
  // A window's closing boundary can sit past the last plotted second -- the
  // clock runs to 59.8 while the curve has 60 buckets numbered 0..59 -- and an
  // unclamped rule takes its label outside the plot, where the canvas cuts it
  // off mid-name. Clamped, the last bot still gets its rule and its full name.
  const xmax = u.scales.x.max;
  marks.forEach((at, i) => {
    const x = u.valToPos(Math.min(at, xmax), 'x', true);
    ctx.setLineDash(p.marks.aligned ? [4, 3] : [2, 4]);
    ctx.lineWidth = 1;
    ctx.strokeStyle = alpha(p.marks.aligned ? C.pb : C.ghost, p.marks.aligned ? .5 : .45);
    ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, top + height); ctx.stroke();
    if (p.marks.aligned) {
      ctx.setLineDash([]);
      ctx.fillStyle = C.faint;
      ctx.font = '9.5px ' + css('--mono');
      ctx.textAlign = 'right';
      // The bot's own name, which is what the stretch before this rule was
      // spent on. Real names run to 18 characters against a gap that is only
      // a fifth of the plot, so they are trimmed to what the gap holds and
      // dropped entirely when it holds nothing legible -- the rule still
      // marks the boundary, and the split table carries every full name.
      const name = (p.marks.labels && p.marks.labels[i]) || 'bot ' + (i + 1);
      const previous = i ? u.valToPos(marks[i - 1], 'x', true) : u.bbox.left;
      ctx.fillText(ellipsize(ctx, name, x - previous - 8), x - 4, top + 11);
    }
  });
  ctx.restore();
}

/* A second that lost points is a loss, not a small gain. Without a zero
   reference a -4 bucket reads the same as a weak +4 one. The gross parts are
   not recoverable -- KovaaK's writes +10 for a kill and -4 for a miss as a
   single 5.6 -- so this shows net and the misses come from the metric picker. */
function drawZero(u, p) {
  if (!p.scenario.penalising) return;
  const y0 = u.valToPos(0, 'y', true);
  const { left, top, width, height } = u.bbox;
  if (y0 < top || y0 > top + height) return;
  const ctx = u.ctx;
  ctx.save();
  ctx.setLineDash([3, 4]); ctx.lineWidth = 1;
  ctx.strokeStyle = alpha(C.dim, .6);
  ctx.beginPath(); ctx.moveTo(left, y0); ctx.lineTo(left + width, y0); ctx.stroke();
  ctx.restore();
}

/* region past compare_until — only one run still has data there.
   The server returns min(len(mine), len(base)): the FIRST index at which the
   two runs no longer overlap, so shading starts at it. When the lengths match
   that index sits one past the last plotted x and the edge guard drops it. */
function drawTail(u, p) {
  if (p.delta.compare_until == null) return;
  const x = u.valToPos(p.delta.compare_until, 'x', true);
  const { left, top, width, height } = u.bbox;
  if (x >= left + width - 1) return;
  const ctx = u.ctx;
  ctx.save();
  ctx.fillStyle = alpha(C.bg, .72);
  ctx.fillRect(x, top, left + width - x, height);
  ctx.setLineDash([2, 3]); ctx.lineWidth = 1; ctx.strokeStyle = alpha(C.faint, .8);
  ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, top + height); ctx.stroke();
  ctx.restore();
}

function readout(u, p) {
  const i = u.cursor.idx;
  const ratio = isRatio(p.rate.metric);
  const f = v => v == null ? '—' : ratio ? pct(v, 1) : num(v, 1);
  if (uRate && uRate.data[0]) {
    const d = uRate.data;
    $('#lgRun').textContent = i == null ? f(p.rate.mine ? p.rate.mine.at(-1) : null) : f(d[5] && d[5][i]);
    $('#lgPb').textContent = i == null ? '—' : f(d[4] && d[4][i]);
    $('#lgBand').textContent = i == null ? '—' : f(d[3] && d[3][i]);
    $('#lgT').textContent = i == null ? '' : (p.axis.kind === 'progress' ? Math.round(d[0][i] * 100) + '%' : i + 's');
  }
  const cd = p.delta.values;
  if (cd) $('#lgDelta').textContent = signed(i == null ? cd.at(-1) : cd[clamp(i, 0, cd.length - 1)], 1);
}

function renderCharts(p) {
  const hasCurve = !!(p.rate.mine && p.rate.mine.length);
  $('#chartRate').hidden = !hasCurve;
  $('#rateEmpty').hidden = hasCurve;
  if (uRate) { uRate.destroy(); uRate = null; }
  if (uDelta) { uDelta.destroy(); uDelta = null; }

  const m = METRICS.find(m => m[0] === p.rate.metric);
  $('#rateUnit').textContent = m ? m[1] : p.rate.unit;
  // Not a pass-through of delta.unit: the timed payload's own unit is
  // "points", but the chart has always read "score" there and stays that way.
  $('#deltaUnit').textContent = p.delta.unit === 'seconds' ? 'seconds' : 'score';
  $('#lgRecentN').textContent = p.baselines.recent_n;

  // "PB" overlay may be the best *charted* run instead of the true PB — say so.
  const note = $('#rateNote'), pbLab = $('#lgPbLabel');
  const b = p.baselines;
  if (b.pb && b.pb.is_true_pb === false && b.true_pb) {
    note.hidden = false;
    note.textContent = `overlay = best charted run ${num(b.pb.score, 1)} · true PB ${num(b.true_pb.score, 1)} has no curve`;
    pbLab.textContent = 'best charted';
  } else { note.hidden = true; pbLab.textContent = 'PB'; }

  if (!hasCurve) {
    $('#rateEmpty').innerHTML = p.run.buckets === 0
      ? `<strong>no per-second data</strong><span>KovaaK's wrote no <code>.perf</code> file for this run, so only the totals above are known. Roughly one run in seven lands this way.</span>`
      : `<strong>waiting for curve</strong><span>The run landed but its per-second file has not been parsed yet.</span>`;
    $('#chartDelta').hidden = true; $('#deltaEmpty').hidden = false;
    $('#deltaEmpty').innerHTML = `<span>Nothing to compare second by second.</span>`;
    $('#lgRun').textContent = $('#lgPb').textContent = $('#lgBand').textContent = $('#lgDelta').textContent = '—';
    return;
  }

  const n = Math.max(p.rate.mine.length, p.rate.pb ? p.rate.pb.length : 0, p.rate.band ? p.rate.band.mean.length : 0);
  const at = (arr, i) => arr && i < arr.length ? arr[i] : null;
  const idx = Array.from({ length: n }, (_, i) => i);
  const col = arr => idx.map(i => at(arr, i));
  // A race is indexed by share of the damage pool, a timed run by seconds.
  // Kill boundaries only line up on the former, which is why it exists.
  const xs = p.axis.kind === 'progress' ? idx.map(i => (i + 0.5) / n) : idx;
  const data = [xs, col(p.rate.band && p.rate.band.lo), col(p.rate.band && p.rate.band.hi),
                col(p.rate.band && p.rate.band.mean), col(p.rate.pb), col(p.rate.mine)];
  uRate = mkRate($('#chartRate'), data, p);

  const hasDelta = !!(p.delta.values && p.delta.values.length);
  $('#chartDelta').hidden = !hasDelta; $('#deltaEmpty').hidden = hasDelta;
  if (hasDelta) {
    // A rate point is a cell at (i+0.5)/n; a delta point is a grid boundary --
    // delta.values[i] is the gap accumulated by progress (i+1)/n -- so the two
    // charts' x series differ by half a step even though both are 0..1.
    const dn = p.delta.values.length;
    const dx = p.axis.kind === 'progress'
      ? Array.from({ length: dn }, (_, i) => (i + 1) / dn) : Array.from({ length: dn }, (_, i) => i);
    uDelta = mkDelta($('#chartDelta'), [dx, p.delta.values], p);
  } else {
    $('#lgDelta').textContent = '—';
    $('#deltaEmpty').innerHTML = `<strong>no baseline curve</strong><span>${p.baselines.candidates ? 'No earlier run of this scenario has per-second data to compare against.' : 'First run of this scenario — nothing to compare against yet.'}</span>`;
  }
  readout({ cursor: { idx: null } }, p);
}

const ro = new ResizeObserver(() => {
  if (uRate) uRate.setSize({ width: $('#chartRate').clientWidth, height: $('#chartRate').clientHeight });
  if (uDelta) uDelta.setSize({ width: $('#chartDelta').clientWidth, height: $('#chartDelta').clientHeight });
});
ro.observe($('#chartRate')); ro.observe($('#chartDelta'));

/* ═══════════════════════════ SPLITS ═══════════════════════ */
function renderSplits(p) {
  const panel = $('#splitPanel');
  panel.hidden = !(p.splits && p.splits.length);
  if (panel.hidden) return;
  $('#splitTitle').textContent = 'Splits';
  // Marked off the run-relative delta, not the raw one: the raw delta ranks
  // the bots you find hard, which are the same bots every run and so tell you
  // nothing about this one.
  const worst = p.splits.filter(s => s.idx != null && s.delta_adj > 0)
    .sort((a, b) => b.delta_adj - a.delta_adj).slice(0, 2).map(s => s.idx);
  // Red means worse. Every row above `score` is seconds, where more is worse;
  // score runs the other way, so it passes worse = -1 to flip the colours.
  const cell = (v, worse = 1) => v == null ? '—'
    : `<span class="${v * worse > 0 ? 'up' : v * worse < 0 ? 'dn' : ''}">${signed(v, 2)}</span>`;
  const total = p.splits.reduce((a, s) => a + s.mine, 0);
  // The baseline's own elapsed is the number the reader most wants beside
  // their own, and it is already here: its per-bot TTKs plus its dead time.
  const baseTotal = p.splits.every(s => s.base != null)
    ? p.splits.reduce((a, s) => a + s.base, 0) : null;
  const baseScore = p.delta.baseline ? p.delta.baseline.score : null;

  // The run summary moves into the panel head, where it reads as a caption
  // instead of two more rows competing with the bots for the eye.
  $('#splitSub').textContent = [
    `${p.scenario.bots} bots · ${num(p.scenario.pool, 0)} damage`,
    `${num(total, 2)} s`,
    baseTotal == null ? '' : `PB ${num(baseTotal, 2)} · ${signed(total - baseTotal, 2)}`,
    `score ${num(p.run.score, 2)}`
  ].filter(Boolean).join('  ·  ');

  // The bar is what neither chart can show: on a progress axis every bot
  // spans exactly 1/N of the width however long it actually took. Scaled so
  // the longest bot fills the track -- against the whole run the five bars
  // all sit near a fifth of it, and the differences between them, which are
  // the point, disappear. The bars stay true to each other either way.
  // Dead time gets one too (it is the same clock) but no ranking, since it
  // is not a bot you can practise.
  const longest = Math.max(...p.splits.map(s => s.mine), 0);
  const bar = s => {
    const tint = s.idx == null ? ' dead'
      : s.delta == null ? '' : s.delta > 0 ? ' up' : s.delta < 0 ? ' dn' : '';
    const width = longest > 0 ? (s.mine / longest) * 100 : 0;
    return `<span class="bar${tint}"><i style="width:${width.toFixed(2)}%"></i></span>`;
  };
  $('#splitTable').innerHTML =
    `<thead><tr><th>bot</th><th class="barh">time per bot</th><th>this run</th>
       <th>PB</th><th>Δ PB</th><th>Δ run</th></tr></thead><tbody>` +
    p.splits.map(s => `<tr class="${worst.includes(s.idx) ? 'w' : ''}">
      <td class="bot">${s.bot}</td><td class="barc">${bar(s)}</td>
      <td>${num(s.mine, 2)}</td><td>${num(s.base, 2)}</td>
      <td>${cell(s.delta)}</td><td>${cell(s.delta_adj)}</td></tr>`).join('') +
    `</tbody>`;
}

/* A rotation of bots that never die, each holding the clock for a fixed
   stretch. The window sets how much damage was on offer, so what varies is the
   share of it taken -- the one number that reads the same on a scenario
   offering 0.009 a window and one offering 0.86. */
function renderWindows(p) {
  const panel = $('#splitPanel'), rows = p.windows;
  panel.hidden = false;
  $('#splitTitle').textContent = 'Bots';

  // Green is the good direction, which is up here and down in the split table
  // above: there a delta is seconds spent, here it is damage taken.
  const cell = v => v == null ? '—'
    : `<span class="${v > 0 ? 'dn' : v < 0 ? 'up' : ''}">${signed(v * 100, 1)}</span>`;
  const shown = rows.filter(r => r.mine != null);
  const mean = shown.length ? shown.reduce((a, r) => a + r.mine, 0) / shown.length : null;
  const pb = rows.filter(r => r.base != null);
  $('#splitSub').textContent = [
    `${rows.length} bots`,
    rows.every(r => r.window_s) ? `${num(rows[0].window_s, 1)}–${num(rows.at(-1).window_s, 1)} s windows` : '',
    mean == null ? '' : `${pct(mean, 1)} of possible`,
    pb.length ? `PB ${pct(pb.reduce((a, r) => a + r.base, 0) / pb.length, 1)}` : ''
  ].filter(Boolean).join('  ·  ');

  // The bots you actually lost against the PB on, at most two, for the same
  // reason the split table marks its worst: a list of five ranks nothing.
  const worst = rows.filter(r => r.delta != null && r.delta < 0)
    .sort((a, b) => a.delta - b.delta).slice(0, 2).map(r => r.idx);

  $('#splitTable').innerHTML =
    `<thead><tr><th>bot</th><th class="barh">share of window</th><th>this run</th>
       <th>PB</th><th>Δ PB</th><th>recent</th><th>Δ recent</th></tr></thead><tbody>` +
    rows.map(r => {
      const tint = r.delta == null ? '' : r.delta > 0 ? ' dn' : r.delta < 0 ? ' up' : '';
      const width = r.mine == null ? 0 : clamp(r.mine, 0, 1) * 100;
      return `<tr class="${worst.includes(r.idx) ? 'w' : ''}">
        <td class="bot">${r.bot}</td>
        <td class="barc"><span class="bar${tint}"><i style="width:${width.toFixed(2)}%"></i></span></td>
        <td>${r.mine == null ? '—' : pct(r.mine, 1)}</td>
        <td>${r.base == null ? '—' : pct(r.base, 1)}</td>
        <td>${cell(r.delta)}</td>
        <td>${r.recent == null ? '—' : pct(r.recent, 1)}</td>
        <td>${cell(r.delta_recent)}</td></tr>`;
    }).join('') + `</tbody>`;
}

/* ═══════════════════════════ HEADLINE ═════════════════════ */
function renderHeadline(p, isNew) {
  const r = p.run, hl = $('#headline');
  $('#hlScenario').textContent = r.scenario;
  $('#hlTime').textContent = hhmmss(r.started_at);
  $('#hlDur').textContent = num(r.duration_s, 0) + ' s';
  $('#hlCfg').textContent = `${num(r.cm360, 1)} cm/360 · ${r.fov}° · ${r.dpi} dpi`;
  $('#hlScore').textContent = num(r.score, 1);

  const base = p.delta.baseline;
  const d = $('#hlDelta');
  if (base) {
    const diff = r.score - base.score;
    const rel = diff / base.score;
    const sign = diff > 0.05 ? 'up' : diff < -0.05 ? 'down' : 'flat';
    d.dataset.sign = sign;
    $('#hlArrow').textContent = sign === 'up' ? '▲' : sign === 'down' ? '▼' : '▬';
    $('#hlDeltaPct').textContent = (diff >= 0 ? '+' : '−') + Math.abs(rel * 100).toFixed(1) + '%';
    $('#hlDeltaSub').textContent =
      `${sign === 'up' ? 'ahead of' : sign === 'down' ? 'behind' : 'level with'} ` +
      `${base.is_true_pb ? 'PB' : 'best charted'} ${num(base.score, 1)} · ${signed(diff, 1)} pts`;
    hl.dataset.state = 'ok';
  } else {
    d.dataset.sign = 'none';
    $('#hlArrow').textContent = '·';
    $('#hlDeltaPct').textContent = '—';
    $('#hlDeltaSub').textContent = p.baselines.candidates ? 'no comparable earlier run' : 'first run of this scenario';
    hl.dataset.state = 'nobaseline';
  }

  const rm = p.baselines.recent_mean_score;
  // spm is meaningless on a race (score is a countdown) and kills is a
  // constant, so both slots carry something that varies instead.
  const isRace = p.scenario.shape === 'race';
  const cells = isRace ? [
    ['acc', pct(r.accuracy, 1)],
    ['elapsed', num(r.elapsed_s, 2) + '<small> s</small>'],
    ['dmg/s', num(p.scenario.pool / r.elapsed_s, 1)],
    ['avg per bot', num(r.avg_ttk, 2) + '<small> s</small>'],
    ['hits', `${num(r.hits, 0)}<small> / ${num(r.shots, 0)}</small>`],
    ['recent mean', rm ? num(rm, 0) : '—'],
    ['vs recent', rm ? signed((r.score - rm) / rm * 100, 1) + '<small>%</small>' : '—'],
    ['fps', num(r.avg_fps, 0)]
  ] : [
    ['acc', pct(r.accuracy, 1)],
    ['spm', num(r.spm, 0)],
    ['kills', num(r.kills, 0)],
    ['avg ttk', num(r.avg_ttk, 2) + '<small> s</small>'],
    ['hits', `${num(r.hits, 0)}<small> / ${num(r.shots, 0)}</small>`],
    ['recent mean', rm ? num(rm, 0) : '—'],
    ['vs recent', rm ? signed((r.score - rm) / rm * 100, 1) + '<small>%</small>' : '—'],
    ['fps', num(r.avg_fps, 0)]
  ];

  // Overshots, reloads and damage taken are recorded on every run but have
  // never been shown. They are only meaningful where they are non-zero -- 74
  // scenarios overshoot, 18 reload, 4 take return fire -- so they appear only
  // on the runs that have them rather than padding every headline with zeroes.
  if (r.overshots) cells.push(['overshots', num(r.overshots, 0)]);
  if (r.reloads) cells.push(['reloads', num(r.reloads, 0)]);
  if (r.damage_taken) cells.push(['dmg taken', num(r.damage_taken, 0)]);
  $('#hlStats').innerHTML = cells.map(([k, v]) => `<div class="cell"><dt>${k}</dt><dd class="num">${v}</dd></div>`).join('');

  if (isNew) { hl.classList.remove('flash'); void hl.offsetWidth; hl.classList.add('flash'); setTimeout(() => hl.classList.remove('flash'), 950); }
}

/* ═══════════════════════════ RUN RAIL ═════════════════════ */
function renderRunList(newId) {
  const ol = $('#runlist');
  const focused = A.runs.find(r => r.id === A.focusedId);
  let list = A.runs;
  if (A.filterScenario) list = list.filter(r => r.scenario === A.filterScenario);

  $('#railFilter').hidden = !A.filterScenario;
  if (A.filterScenario) $('#railFilter').textContent = A.filterScenario + '  ✕';

  if (!list.length) {
    ol.innerHTML = `<li class="rail-empty">${A.health && A.health.awaiting_perf
      ? 'Building the index from your KovaaK\'s stats folder. Runs appear as they are parsed.'
      : 'No runs yet. Finish a scenario and it shows up here about a second later.'}</li>`;
    $('#railFoot').textContent = '';
    return;
  }

  // per-run delta vs the best earlier run of the same scenario (same language as the headline)
  const bestBefore = {};
  const marks = {};
  [...list].reverse().forEach(r => {
    const b = bestBefore[r.scenario];
    marks[r.id] = { d: b == null ? null : (r.score - b) / b, pb: b == null || r.score > b };
    bestBefore[r.scenario] = b == null ? r.score : Math.max(b, r.score);
  });

  ol.innerHTML = list.map((r, i) => {
    const m = marks[r.id], sign = !m.d ? 'flat' : m.d > 0 ? 'up' : 'down';
    return `<li class="run" role="option" data-id="${r.id}" data-i="${i}"
      aria-selected="${r.id === A.focusedId}"
      data-same="${focused && r.scenario === focused.scenario ? 1 : 0}"
      data-pb="${m.pb ? 1 : 0}" data-nocurve="${r.buckets ? 0 : 1}" data-sign="${sign}"
      data-shape="${r.shape || 'timed'}">
      <span class="t">${hhmm(r.started_at)}</span>
      <span class="name">${r.scenario}</span>
      <span class="right"><span class="sc">${num(r.score, 1)}</span>
      <span class="d">${m.d == null ? 'first' : (m.d > 0 ? '▲' : m.d < 0 ? '▼' : '') + Math.abs(m.d * 100).toFixed(1) + '%'}</span></span>
    </li>`;
  }).join('');

  if (newId != null) {
    const el = ol.querySelector(`[data-id="${newId}"]`);
    if (el) { el.classList.add('enter'); setTimeout(() => el.classList.remove('enter'), 600); }
  }
  const sel = ol.querySelector('[aria-selected="true"]');
  if (sel) ol.scrollTop = clamp(sel.offsetTop - ol.clientHeight / 2, 0, ol.scrollHeight);

  const pbMark = focused ? list.filter(r => r.scenario === focused.scenario).length : 0;
  $('#railFoot').innerHTML = A.filterScenario
    ? `${list.length} runs · ↑↓ to move, esc to clear`
    : `${list.length} runs · ${pbMark} of this scenario · ↑↓ to move, ⏎ to filter`;
}

/* ═══════════════════════════ SHEETS ═══════════════════════ */
async function renderSession() {
  const s = await api('/api/session/today');
  $('#sessionDay').textContent = s.day;
  const by = {};
  s.runs.forEach(r => (by[r.scenario] = by[r.scenario] || []).push(r));
  const names = Object.keys(by);
  if (!s.runs.length) {
    $('#sessionSum').innerHTML = '';
    $('#sessionTable').innerHTML = `<tbody><tr><td style="padding:22px 0;color:var(--faint)">No runs today yet.</td></tr></tbody>`;
    return;
  }
  const accs = s.runs.map(r => r.accuracy);
  const best = s.runs.reduce((a, b) => b.score > a.score ? b : a);
  const span = (new Date(s.runs.at(-1).started_at) - new Date(s.runs[0].started_at)) / 6e4;
  const cells = [
    ['runs', s.runs.length], ['scenarios', names.length],
    ['best', num(best.score, 1)],
    ['mean acc', pct(accs.reduce((a, b) => a + b, 0) / accs.length, 1)],
    ['span', Math.abs(Math.round(span)) + ' min']
  ];
  $('#sessionSum').innerHTML = cells.map(([k, v]) => `<div class="cell"><dt>${k}</dt><dd>${v}</dd></div>`).join('');

  $('#sessionTable').innerHTML = `<thead><tr><th>Scenario</th><th>Runs</th><th>Best</th><th>Mean</th><th>Acc</th><th>Shape</th><th>Last</th></tr></thead><tbody>` +
    names.map(nm => {
      const rs = by[nm].slice().sort((a, b) => a.started_at < b.started_at ? -1 : 1);
      const mx = Math.max(...rs.map(r => r.score)), mn = Math.min(...rs.map(r => r.score));
      const bars = rs.map(r => `<i style="height:${4 + (mx === mn ? 14 : (r.score - mn) / (mx - mn) * 14)}px" data-pb="${r.score === mx ? 1 : 0}"></i>`).join('');
      const mean = rs.reduce((a, b) => a + b.score, 0) / rs.length;
      const acc = rs.reduce((a, b) => a + b.accuracy, 0) / rs.length;
      return `<tr data-run="${rs.at(-1).id}"><td class="name">${nm}</td><td class="n">${rs.length}</td>
        <td class="n pb">${num(mx, 1)}</td><td class="n">${num(mean, 1)}</td><td class="n">${pct(acc, 1)}</td>
        <td><div class="bars">${bars}</div></td><td class="n">${hhmm(rs.at(-1).started_at)}</td></tr>`;
    }).join('') + '</tbody>';
}

async function renderScenarios() {
  const list = await api('/api/scenarios');
  $('#scenSub').textContent = `${list.length} scenarios · click to filter the run list`;
  const val = v => Array.isArray(v) ? v.at(-1) : v;
  $('#scenTable').innerHTML = `<thead><tr><th>Scenario</th><th>Runs</th><th>PB</th><th>Recent form</th><th>% of PB</th><th></th><th>Last played</th></tr></thead><tbody>` +
    list.map(s => {
      // Race scores sit in a 906-919 band out of 1000, so every race scenario
      // pins at ~99% of PB and the bar dies. Time is the honest measure there.
      const isRace = s.shape === 'race';
      const rel = isRace
        ? (s.recent_elapsed && s.pb_elapsed ? s.pb_elapsed / s.recent_elapsed : null)
        : (val(s.recent_form) != null && s.pb ? val(s.recent_form) / s.pb : null);
      const form = val(s.recent_form);
      const w = rel == null ? 0 : clamp((rel - .7) / .3, .02, 1) * 100;   // 70–100 % of PB spread across the bar
      // % of PB means "pb time / recent time" for a race, same column and same
      // bar as the timed "recent / pb" -- both read "higher is closer to PB".
      const relTitle = isRace ? ' title="PB time / recent time · 100% is PB pace"' : '';
      return `<tr data-scenario="${s.scenario}" data-shape="${s.shape || 'timed'}"><td class="name">${s.scenario}</td><td class="n">${s.runs}</td>
        <td class="n pb">${num(s.pb, 1)}</td><td class="n">${form == null ? '—' : num(form, 1)}</td>
        <td class="n"${relTitle}>${rel == null ? '—' : (rel * 100).toFixed(1) + '%'}</td>
        <td><div class="formbar"><i style="width:${w}%"></i></div></td>
        <td class="n">${s.last_played ? hhmm(s.last_played) : '—'}</td></tr>`;
    }).join('') + '</tbody>';
}

/* ═══════════════════════════ LOADING ══════════════════════ */
async function loadRun(id, isNew) {
  const c = A.ctrl;
  const p = await api(`/api/run/${id}?metric=${c.metric}&smoothing=${c.smoothing}&recent_n=${c.recent_n}&same_cfg=${c.same_cfg ? 1 : 0}`);
  if (!p) return;
  // Fall back only onto a metric this run actually offers, and only when that
  // is a different one. A race offers none at all -- its y series is fixed by
  // the shape -- and re-fetching with the same metric that was just rejected
  // would recurse until the tab dies.
  if (p.metrics && !p.metrics.includes(A.ctrl.metric)) {
    const fallback = p.metrics.includes('score') ? 'score' : p.metrics[0];
    if (fallback && fallback !== A.ctrl.metric) {
      A.ctrl.metric = fallback;
      return loadRun(id, isNew);   // one re-fetch, then render
    }
  }
  A.payload = p; A.focusedId = id;
  buildSegs();
  renderHeadline(p, isNew);
  renderCharts(p);
  p.windows && p.windows.length ? renderWindows(p) : renderSplits(p);
  renderRunList(isNew ? id : null);
}

/* The one place a route is acted on: the hash decides the view, the focused
   run and the rail filter, and nothing else writes those three. */
async function applyRoute(isNew) {
  const r = parseHash();
  if (r.view === 'run') A.filterScenario = r.scenario;   // the filter is run-view state,
  setView(r.view);                                       // so a sheet route leaves it alone

  if (!A.runs.length) { A.payload = null; A.focusedId = null; renderRunList(); return; }

  // A sheet route names no run, so the run view keeps the one it had. A new run
  // over SSE always takes focus — this is a live dashboard, and a deep link is a
  // starting point rather than a pin. Anything unknown falls back to the newest.
  let id = r.view === 'run' ? r.runId : A.focusedId;
  if (isNew || id == null || !A.runs.some(x => x.id === id)) id = A.runs[0].id;
  go({ view: r.view, runId: id }, true);                 // URL now names what is shown

  if (isNew || !A.payload || id !== A.focusedId) await loadRun(id, isNew);
  else renderRunList();                                  // same run, new filter or view
  A.kbd = $$('.run', $('#runlist')).findIndex(el => +el.dataset.id === id);
}

addEventListener('hashchange', () => applyRoute(false));

async function refresh(isNew) {
  // Every SSE message lands here with nothing above it to catch a rejection.
  // Without this the page would sit on stale data after a failed request and
  // still claim "live".
  try {
    A.health = await api('/api/health');
    const h = A.health;
    const hEl = $('#health');
    // Only states that are actionable or still resolving. Every condition here
    // must also have a line below it, or the badge shows up saying nothing.
    hEl.hidden = !(h.awaiting_perf || h.watcher_errors || h.failed);
    hEl.textContent = [
      h.awaiting_perf ? `indexing · ${h.curves}/${h.runs} curves` : '',
      h.watcher_errors ? `${h.watcher_errors} watcher errors` : '',
      h.failed ? `${h.failed} unreadable files` : ''
    ].filter(Boolean).join('  ·  ');

    A.runs = await api('/api/runs?limit=100');
    if (!A.runs.length) {
      A.payload = null;
      if (uRate) { uRate.destroy(); uRate = null; }
      if (uDelta) { uDelta.destroy(); uDelta = null; }
      $('#headline').dataset.state = 'empty';
      $('#hlScenario').textContent = h.awaiting_perf ? 'Building index…' : 'Waiting for your first run';
      $('#hlTime').textContent = '—'; $('#hlDur').textContent = '—';
      $('#hlCfg').textContent = h.awaiting_perf ? `${h.curves} of ${h.runs} parsed` : 'kvstats is watching your stats folder';
      $('#hlScore').textContent = '—'; $('#hlDeltaPct').textContent = '—';
      $('#hlDelta').dataset.sign = 'none';
      $('#hlArrow').textContent = '·';
      $('#rateNote').hidden = true;
      $('#lgRun').textContent = $('#lgPb').textContent = $('#lgBand').textContent = $('#lgDelta').textContent = $('#lgT').textContent = '—';
      $('#hlDeltaSub').textContent = h.awaiting_perf ? 'first launch — this runs once' : 'play a scenario to begin';
      $('#hlStats').innerHTML = '';
      $('#chartRate').hidden = true; $('#rateEmpty').hidden = false;
      $('#rateEmpty').innerHTML = `<strong>${h.awaiting_perf ? 'indexing' : 'no runs yet'}</strong><span>${h.awaiting_perf ? 'Reading the history in your stats folder. The first chart appears as soon as a run with per-second data is parsed.' : 'The next run you finish lands here about a second after it ends.'}</span>`;
      $('#chartDelta').hidden = true; $('#deltaEmpty').hidden = false;
      $('#deltaEmpty').innerHTML = '<span>—</span>';
      $('#splitPanel').hidden = true;
    }
    await applyRoute(isNew);
  } catch (err) {
    setStatus('down', 'api error');
  }
}

/* ═══════════════════════════ CONTROLS ═════════════════════ */
function buildSegs() {
  const usable = (A.payload && A.payload.metrics) || METRICS.map(m => m[0]);
  // An empty list is a real answer, not a missing one: a race is plotted in
  // damage/s whichever metric is asked for, so the whole picker goes away
  // rather than standing there offering six buttons that redraw one line.
  $('#ctlMetricGroup').hidden = !usable.length;
  $('#ctlMetric').innerHTML = METRICS.filter(([k]) => usable.includes(k)).map(([k, l]) =>
    `<button type="button" role="radio" data-v="${k}" aria-checked="${A.ctrl.metric === k}">${k}</button>`).join('');
  $('#ctlSmooth').innerHTML = SMOOTH.map(([k, l]) =>
    `<button type="button" role="radio" data-v="${k}" aria-checked="${A.ctrl.smoothing === k}">${l}</button>`).join('');
}
loadCtrl();
buildSegs();
$('#ctlRecent').value = A.ctrl.recent_n;
$('#ctlSameCfg').checked = A.ctrl.same_cfg;

$('#ctlMetric').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  A.ctrl.metric = b.dataset.v; buildSegs(); saveCtrl(); if (A.focusedId) loadRun(A.focusedId);
});
$('#ctlSmooth').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  A.ctrl.smoothing = +b.dataset.v; buildSegs(); saveCtrl(); if (A.focusedId) loadRun(A.focusedId);
});
$('.stepper').addEventListener('click', e => {
  const b = e.target.closest('button[data-step]'); if (!b) return;
  const i = $('#ctlRecent');
  i.value = clamp(+i.value + +b.dataset.step, 1, 50);
  i.dispatchEvent(new Event('change'));
});
$('#ctlRecent').addEventListener('change', e => {
  A.ctrl.recent_n = clamp(+e.target.value || 10, 1, 50);
  e.target.value = A.ctrl.recent_n;
  saveCtrl();
  if (A.focusedId) loadRun(A.focusedId);
});
$('#ctlSameCfg').addEventListener('change', e => {
  A.ctrl.same_cfg = e.target.checked; saveCtrl(); if (A.focusedId) loadRun(A.focusedId);
});

/* views */
$$('.vtab').forEach(t => t.addEventListener('click', () => go({ view: t.dataset.view })));
function setView(v) {
  A.view = v;
  $$('.vtab').forEach(t => t.setAttribute('aria-selected', t.dataset.view === v));
  $('#view-run').hidden = v !== 'run';
  $('#view-session').hidden = v !== 'session';
  $('#view-scenarios').hidden = v !== 'scenarios';
  if (v === 'session') renderSession();
  if (v === 'scenarios') renderScenarios();
  if (v === 'run' && uRate) ro.disconnect(), ro.observe($('#chartRate')), ro.observe($('#chartDelta'));
}

/* run list interaction — these navigate, and applyRoute does the work */
$('#runlist').addEventListener('click', e => {
  const li = e.target.closest('.run'); if (!li) return;
  go({ runId: +li.dataset.id });
});
$('#railFilter').addEventListener('click', () => go({ scenario: null }));

$('#sessionTable').addEventListener('click', e => {
  const tr = e.target.closest('tr[data-run]'); if (!tr) return;
  // the run picked here can belong to a scenario the rail is filtering out
  go({ view: 'run', runId: +tr.dataset.run, scenario: null });
});
$('#scenTable').addEventListener('click', e => {
  const tr = e.target.closest('tr[data-scenario]'); if (!tr) return;
  const name = tr.dataset.scenario;
  const first = A.runs.find(r => r.scenario === name);
  go({ view: 'run', scenario: name, runId: first ? first.id : A.focusedId });
});

/* keyboard: ↑↓ through runs, ⏎ filter the rail by scenario, Esc clear it
   (Esc backs out of a sheet view instead, where there is no rail) */
document.addEventListener('keydown', e => {
  if (/^(INPUT|TEXTAREA)$/.test(e.target.tagName)) return;
  if (A.view !== 'run') { if (e.key === 'Escape') go({ view: 'run' }); return; }
  // Ahead of the empty-list guard: a filter matching nothing leaves no items
  // to move through, and that is exactly when you most want to clear it.
  if (e.key === 'Escape') { if (A.filterScenario) go({ scenario: null }); return; }
  const items = $$('.run', $('#runlist'));
  if (!items.length) return;
  const move = d => {
    A.kbd = clamp((A.kbd < 0 ? 0 : A.kbd) + d, 0, items.length - 1);
    items.forEach(el => el.classList.remove('kbd'));
    const el = items[A.kbd]; el.classList.add('kbd');
    $('#runlist').scrollTop = clamp(el.offsetTop - $('#runlist').clientHeight / 2, 0, $('#runlist').scrollHeight);
    // arrowing is a scrub, not a destination — keep it out of the back stack
    go({ runId: +el.dataset.id }, true);
    loadRun(+el.dataset.id);
  };
  if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
  else if (e.key === 'Home') { e.preventDefault(); A.kbd = 0; move(0); }
  else if (e.key === 'End') { e.preventDefault(); A.kbd = items.length - 1; move(0); }
  else if (e.key === 'Enter') { const r = A.runs.find(x => x.id === A.focusedId); if (r) go({ scenario: r.scenario }); }
});

/* ═══════════════════════════ SSE ══════════════════════════ */
function setStatus(state, label) {
  const s = $('#status'); s.dataset.state = state;
  s.querySelector('.status-label').textContent = label;
}
let es = null, retry = 0;
function connect() {
  setStatus('connecting', 'connecting');
  es = new EventSource(API + '/events');
  es.onopen = () => { retry = 0; setStatus('live', 'live'); };
  es.onmessage = ev => {
    let m = {}; try { m = JSON.parse(ev.data); } catch (_) {}
    if (m.type === 'run') refresh(true);
  };
  es.onerror = () => {
    es.close();
    retry = Math.min(retry + 1, 6);
    setStatus('down', `reconnecting ${retry * 2}s`);
    setTimeout(connect, retry * 2000);
  };
}

/* ═══════════════════════════ BOOT ═════════════════════════ */
readColors();
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { readColors(); if (A.payload) renderCharts(A.payload); });
refresh(false).then(connect);

})();
