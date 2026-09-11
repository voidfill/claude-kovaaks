"use strict";

const $ = (id) => document.getElementById(id);
const state = { runId: null, rate: null, delta: null };

const opts = () => new URLSearchParams({
  metric: $("metric").value,
  smoothing: $("smoothing").value,
  recent_n: $("recent").value,
  same_cfg: $("samecfg").checked ? "1" : "0",
});

function destroy(chart) { if (chart) chart.destroy(); return null; }

function axes() {
  return [
    { stroke: "#8b93a1", grid: { stroke: "#232830" } },
    { stroke: "#8b93a1", grid: { stroke: "#232830" } },
  ];
}

// Baselines are not always the same length as the focused run -- about 6% of
// real runs differ. slice() alone would truncate a longer baseline and leave a
// shorter one short, which uPlot renders as a silently missing tail. Pad with
// null, which is uPlot's own gap value.
function fit(values, n) {
  const out = values.slice(0, n);
  while (out.length < n) out.push(null);
  return out;
}

function drawRate(payload) {
  const n = payload.curve.length;
  const t = Array.from({ length: n }, (_, i) => i);
  const series = [{}, { label: "this run", stroke: "#e6e8ec", width: 2 }];
  const data = [t, payload.curve];
  const bands = [];

  if (payload.recent_band) {
    // uPlot's band is drawn between two series by index. Fit lo/hi exactly
    // like mean, or the fill misaligns against the focused run on the ~6% of
    // runs whose baseline curves are a different length. The bound series
    // themselves are undrawn (width 0) and hidden from the legend via the
    // "u-band-bound" class -- only the fill and the mean line are visible.
    const hiIdx = series.length;
    series.push({ label: "recent +1σ", class: "u-band-bound", stroke: "transparent", width: 0 });
    data.push(fit(payload.recent_band.hi, n));
    const loIdx = series.length;
    series.push({ label: "recent -1σ", class: "u-band-bound", stroke: "transparent", width: 0 });
    data.push(fit(payload.recent_band.lo, n));
    bands.push({ series: [hiIdx, loIdx], fill: "rgba(59,130,246,.15)" });

    series.push({ label: "recent mean", stroke: "#3b82f6", width: 1, dash: [2, 3] });
    data.push(fit(payload.recent_band.mean, n));
  }
  if (payload.pb_curve) {
    series.push({ label: "PB", stroke: "#fbbf24", width: 1, dash: [6, 4] });
    data.push(fit(payload.pb_curve, n));
  }

  state.rate = destroy(state.rate);
  state.rate = new uPlot({
    width: $("rate").clientWidth, height: 240, series, axes: axes(), bands,
    scales: { x: { time: false } }, cursor: { sync: { key: "kv" } },
  }, data, $("rate"));
}

function drawDelta(payload) {
  state.delta = destroy(state.delta);
  if (!payload.cumulative_delta) {
    $("delta").innerHTML = '<p class="muted">no PB curve to compare against yet</p>';
    return;
  }
  $("delta").innerHTML = "";
  const values = payload.cumulative_delta;
  const t = Array.from({ length: values.length }, (_, i) => i);
  const cut = payload.compare_until;
  const tail = cut != null && cut < values.length
    ? ` (only one run has data past ${cut}s)` : "";
  state.delta = new uPlot({
    width: $("delta").clientWidth, height: 200,
    series: [{}, {
      label: `cumulative Δ score vs PB${tail}`,
      stroke: (u) => (u.data[1].at(-1) >= 0 ? "#4ade80" : "#f87171"),
      fill: (u) => (u.data[1].at(-1) >= 0 ? "rgba(74,222,128,.15)"
                                          : "rgba(248,113,113,.15)"),
      width: 2,
    }],
    axes: axes(), scales: { x: { time: false } },
    cursor: { sync: { key: "kv" } },
  }, [t, values], $("delta"));
}

function pct(mine, base) {
  // A baseline of 0 makes "percent change" undefined, not infinite or zero --
  // !base also catches null/undefined baselines, which is the common case.
  if (!base) return null;
  return ((mine - base) / base) * 100;
}

function renderHeadline(payload) {
  const run = payload.run;
  const b = payload.baselines;
  void b;
  $("scenario").textContent = run.scenario;
  $("headline").textContent = run.score == null ? "--" : run.score.toFixed(0);

  const parts = [];
  // pct() returns null for an undefined percentage (e.g. a zero baseline
  // score); render a dash instead of letting null.toFixed(1) throw and blank
  // the whole dashboard before either chart draws.
  const signed = (d, label) => d == null
    ? `<span class="muted">-- ${label}</span>`
    : `<span class="${d >= 0 ? "up" : "down"}">${d >= 0 ? "+" : ""}${d.toFixed(1)}% ${label}</span>`;

  // The percentage MUST be measured against whatever the delta chart is drawn
  // against, or the number and the chart contradict each other on exactly the
  // runs where the true PB has no curve.
  const drawn = payload.delta_baseline;
  if (drawn) {
    parts.push(signed(pct(run.score, drawn.score),
                      drawn.is_true_pb ? "vs PB" : "vs best charted run"));
  } else if (b.true_pb) {
    parts.push(signed(pct(run.score, b.true_pb.score), "vs PB"));
  }
  if (drawn && !drawn.is_true_pb && b.true_pb) {
    parts.push(`<span class="muted">true PB ${b.true_pb.score.toFixed(0)} has no curve data</span>`);
  }
  if (b.recent_mean_score) {
    parts.push(signed(pct(run.score, b.recent_mean_score), `vs last ${b.recent_n}`));
  }
  if (!b.candidates) {
    parts.push('<span class="muted">first run of this scenario</span>');
  }
  $("deltas").innerHTML = parts.join("");
}

async function show(runId) {
  state.runId = runId;
  const response = await fetch(`/api/run/${runId}?${opts()}`);
  if (!response.ok) return;
  const payload = await response.json();
  renderHeadline(payload);
  drawRate(payload);
  drawDelta(payload);
  document.querySelectorAll("#runs li").forEach((li) => {
    li.classList.toggle("active", Number(li.dataset.id) === runId);
  });
}

async function refreshRuns(focusNewest) {
  const rows = await (await fetch("/api/runs?limit=25")).json();
  $("runs").innerHTML = rows.map((r) =>
    `<li data-id="${r.id}" title="${r.scenario}">${r.score == null ? "--" : r.score.toFixed(0)}</li>`
  ).join("");
  $("runs").querySelectorAll("li").forEach((li) => {
    li.onclick = () => show(Number(li.dataset.id));
  });
  if (focusNewest && rows.length) show(rows[0].id);
}

function subscribe() {
  const source = new EventSource("/events");
  source.onopen = () => { $("status").textContent = "watching"; };
  source.onerror = () => { $("status").textContent = "reconnecting"; };
  source.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "run") refreshRuns(true);
  };
}

for (const id of ["metric", "smoothing", "recent", "samecfg"]) {
  $(id).addEventListener("change", () => { if (state.runId) show(state.runId); });
}
// Resizing fires ~60x/s while dragging. show() is a full fetch plus two chart
// rebuilds, so it must be debounced -- and a resize needs neither.
let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    for (const chart of [state.rate, state.delta]) {
      if (chart) chart.setSize({ width: chart.root.parentNode.clientWidth,
                                 height: chart.height });
    }
  }, 150);
});

refreshRuns(true);
subscribe();
