/* Drives kvstats' own web/app.js against a running kvstats server.
 *
 *   python -m kvstats                 # in one terminal
 *   node scripts/drive-client.mjs     # in another
 *
 * Not part of `python -m unittest discover`, and deliberately so: it needs
 * node and a live server, and a test that silently skips when it cannot find
 * either reports green while covering nothing. Run it by hand when you change
 * the rail, the paging, or the run-time formatting.
 *
 * It loads the real app.js -- the file the browser gets, unmodified on disk --
 * under a stub DOM, so it exercises the client's logic rather than a copy of
 * it. What it cannot see is anything the browser does for itself: CSS, layout,
 * real event dispatch. Those still need eyes on the page.
 *
 * Node 18+ (global fetch). No dependencies, no install.
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const arg = (name, fallback) => {
  const hit = process.argv.find((a) => a.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : fallback;
};
const BASE = arg('base', `http://127.0.0.1:${arg('port', '8777')}`);

const get = async (path) => {
  const r = await fetch(BASE + path);
  if (!r.ok) throw new Error(`${path} -> HTTP ${r.status}`);
  return r.json();
};

/* ── stub DOM ─────────────────────────────────────────────── */
const els = new Map();
const mkEl = (sel) => ({
  _sel: sel, innerHTML: '', textContent: '', hidden: false, value: '', checked: false,
  dataset: {}, style: {}, scrollTop: 0, clientHeight: 600, scrollHeight: 600, offsetTop: 0,
  classList: { add() {}, remove() {}, contains: () => false },
  addEventListener(type, fn) { (this._h ||= {})[type] = fn; },
  // A generic child stub rather than null: app.js reaches into rendered markup
  // (.status-label, [aria-selected]) that this harness does not parse. The one
  // place null is load-bearing is the keepScroll branch, which nulls it itself.
  querySelector(s) { return $el(this._sel + ' ' + s); }, querySelectorAll: () => [],
  closest: () => null, appendChild() {}, remove() {}, focus() {},
});
const $el = (sel) => { if (!els.has(sel)) els.set(sel, mkEl(sel)); return els.get(sel); };

const document = {
  documentElement: mkEl(':root'),
  querySelector: $el, querySelectorAll: () => [],
  addEventListener() {}, createElement: mkEl,
  body: mkEl('body'),
};

const ctx = {
  document, console, URLSearchParams,
  setTimeout, clearTimeout, setInterval, clearInterval,
  Date, Math, JSON, Object, Array, String, Number, Boolean, Set, Map, Promise, Error,
  isFinite, parseInt, parseFloat, encodeURIComponent, decodeURIComponent,
  getComputedStyle: () => ({ getPropertyValue: () => '#888' }),
  matchMedia: () => ({ addEventListener() {} }),
  ResizeObserver: class { observe() {} disconnect() {} },
  requestAnimationFrame: (fn) => setTimeout(fn, 0),
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  location: { hash: '' },
  history: { replaceState(_a, _b, url) { ctx.location.hash = url.replace(/^.*#/, '#'); } },
  addEventListener() {},
  EventSource: class { addEventListener() {} close() {} },
  uPlot: Object.assign(function () {
    return { destroy() {}, setData() {}, setSize() {}, valToPos: () => 0, bbox: {}, over: mkEl('over') };
  }, { sync: () => ({ sub() {} }), paths: { spline: () => () => {} } }),
  fetch: (p) => fetch(BASE + p),
};
ctx.window = ctx;
ctx.globalThis = ctx;

/* ── load app.js, exposing what the assertions need to drive ── */
let src = readFileSync(join(ROOT, 'kvstats', 'web', 'app.js'), 'utf8');
// Hand back the internals just before the IIFE closes. The file on disk is
// never touched -- this edit lives only in the string we hand to the VM.
const EXPOSE = `
globalThis.__t = { A, loadRail, loadMore, renderRunList, refresh, applyRoute, RAIL_PAGE,
                   runTime, runStamp, runTitle };
`;
const close = src.lastIndexOf('})();');
if (close < 0) throw new Error('app.js no longer ends in an IIFE; nothing to hook');
src = src.slice(0, close) + EXPOSE + src.slice(close);
// app.js self-starts with refresh().then(connect); the harness decides when
// things happen instead.
src = src.replace('refresh(false).then(connect);', '');

try {
  await get('/api/health');
} catch (e) {
  console.error(`cannot reach kvstats at ${BASE} -- start it with "python -m kvstats"`);
  console.error(`  (${e.message})`);
  process.exit(2);
}

vm.createContext(ctx);
new vm.Script(src, { filename: 'app.js' }).runInContext(ctx);
const t = ctx.__t;


let failed = 0;
const ok = (cond, label, extra = '') => {
  console.log(`${cond ? '  PASS' : '  FAIL'}  ${label}${extra ? '  -- ' + extra : ''}`);
  if (!cond) failed++;
};

const health = await get('/api/health');
const TOTAL = health.runs;
console.log(`corpus: ${TOTAL} runs\n`);

console.log('initial load');
await t.refresh(false);
t.renderRunList();
ok(t.A.runs.length === t.RAIL_PAGE, 'first page is one RAIL_PAGE', `${t.A.runs.length}`);
ok(t.A.more === true, 'more pages are advertised');
ok(t.A.railScenario === null && t.A.railStale === false, 'rail records what it fetched');
const foot = $el('#railFoot').innerHTML;
ok(foot.includes(`of ${TOTAL} runs`), 'footer names the real total', foot);

console.log('\nappending pages');
const before = t.A.runs.length;
$el('#runlist').scrollTop = 4321;
await t.loadMore();
ok(t.A.runs.length === before + t.RAIL_PAGE, 'a page was appended', `${t.A.runs.length}`);
ok($el('#runlist').scrollTop === 4321, 'the append did not yank the scroll back');

console.log('\nwalking the whole history');
let guard = 0;
while (t.A.more && guard++ < 100) await t.loadMore();
ok(t.A.runs.length === TOTAL, 'every run loaded exactly once', `${t.A.runs.length} vs ${TOTAL}`);
ok(t.A.more === false, 'the end of the list is detected');
const ids = t.A.runs.map((r) => r.id);
ok(new Set(ids).size === ids.length, 'no duplicate rows across pages');
let ordered = true;
for (let i = 1; i < t.A.runs.length; i++) {
  const a = t.A.runs[i - 1], b = t.A.runs[i];
  if (a.started_at < b.started_at || (a.started_at === b.started_at && a.id < b.id)) ordered = false;
}
ok(ordered, 'rows stay newest-first across page joins');

console.log('\nserver-side scenario filter');
t.A.filterScenario = 'Plink Palace Easy';
await t.loadRail();
t.renderRunList();
ok(t.A.runs.length > 0 && t.A.runs.every((r) => r.scenario === 'Plink Palace Easy'),
  'only that scenario comes back', `${t.A.runs.length} rows`);
ok(t.A.railScenario === 'Plink Palace Easy', 'the rail records the filter it used');
const ffoot = $el('#railFoot').innerHTML;
ok(!ffoot.includes('of ' + TOTAL), 'a filtered footer does not claim the whole history', ffoot);

console.log('\nthe marks the rail draws');
t.A.filterScenario = null;
await t.loadRail();
const row = t.A.runs.find((r) => r.id === 1280);
ok(row && row.best_before === 2913, 'run 1280 is marked against the out-of-page PB',
  row ? `best_before=${row.best_before}` : 'row missing');
t.A.focusedId = 1280;
t.renderRunList();
const html = $el('#runlist').innerHTML;
const li = html.split('<li').find((x) => x.includes('data-id="1280"')) || '';
ok(li.includes('data-pb="0"'), 'and is NOT marked a personal best');
ok(li.includes('data-sign="down"'), 'and reads as a loss');
ok(/▼0\.3%/.test(li), 'at the same 0.3% the headline shows', li.match(/>[^<]*%/)?.[0] || '?');
ok(li.includes('title="against your best before this run"'), 'with the ledger label on it');

console.log('');
console.log('run time formatting');
{
  const two = (n) => String(n).padStart(2, '0');
  const iso = (d) => d.getFullYear() + '-' + two(d.getMonth() + 1) + '-' + two(d.getDate())
    + 'T' + two(d.getHours()) + ':' + two(d.getMinutes()) + ':' + two(d.getSeconds());
  const now = new Date();
  const at = (daysAgo, h, m, sec) => {
    const d = new Date(now); d.setDate(d.getDate() - daysAgo); d.setHours(h, m, sec, 0); return iso(d);
  };
  const M = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

  const today = at(0, 16, 47, 5);
  ok(t.runTime(today) === '16:47', 'a run from today reads as a clock', t.runTime(today));

  const back = at(4, 19, 54, 10), bd = new Date(back);
  const wantYear = bd.getFullYear() === now.getFullYear() ? '' : ' ' + bd.getFullYear();
  ok(t.runTime(back) === M[bd.getMonth()] + ' ' + bd.getDate() + wantYear,
    'an older run reads as a date', t.runTime(back));
  ok(!/\d\d:\d\d/.test(t.runTime(back)), 'and carries no clock time', t.runTime(back));
  ok(t.runTime(back).length <= 6, 'and fits the 5ch rail column', t.runTime(back));

  ok(t.runTime('2020-12-27T22:04:19') === 'Dec 27 2020',
    'a run from another year names the year', t.runTime('2020-12-27T22:04:19'));
  ok(!wantYear ? !/ 20\d\d/.test(t.runTime(back)) : true,
    'but a run from this year does not repeat it', t.runTime(back));

  ok(t.runStamp(today) === '16:47:05', 'the headline keeps seconds for today', t.runStamp(today));
  ok(t.runStamp('2020-12-27T22:04:19') === 'Dec 27 2020 22:04:19',
    'and dates them for anything older', t.runStamp('2020-12-27T22:04:19'));

  // "today" has to mean the whole date, not one component of it. A run from
  // exactly a month or a year ago shares today's day-of-month, and a sameDay
  // that skips the month or the year renders it as a bare clock.
  const pick = (yearOffset, monthStep) => {
    for (let k = 0; k < 12; k++) {
      const m = (now.getMonth() + monthStep + k) % 12;
      const y = now.getFullYear() - yearOffset;
      const c = new Date(y, m, now.getDate(), 10, 30, 0);
      if (c.getDate() === now.getDate() && !(yearOffset === 0 && m === now.getMonth())) return c;
    }
    return null;
  };
  const otherMonth = pick(0, 1);
  if (otherMonth) {
    ok(!/^\d\d:\d\d$/.test(t.runTime(iso(otherMonth))),
      'same day-of-month, different month is still a date', t.runTime(iso(otherMonth)));
  }
  const lastYear = pick(1, 0);
  if (lastYear) {
    ok(t.runTime(iso(lastYear)) === M[lastYear.getMonth()] + ' ' + lastYear.getDate()
       + ' ' + lastYear.getFullYear(),
      'same day and month, different year is a dated year', t.runTime(iso(lastYear)));
  }

  const title = t.runTitle('2020-12-27T22:04:19');
  ok(/2020/.test(title) && /December/.test(title) && /22:04:19|10:04:19/.test(title),
    'the hover title is the full stamp', title);
}

console.log('');
console.log('titles reach the markup');
t.A.filterScenario = null;
await t.loadRail();
t.A.focusedId = t.A.runs[0].id;
t.renderRunList();
{
  const html = $el('#runlist').innerHTML;
  const row = t.A.runs[0];
  const first = html.split('<li').find((x) => x.includes('data-id="' + row.id + '"')) || '';
  ok(/<span class="t" title="[^"]+">/.test(first), 'each rail row carries a full-stamp title',
    (first.match(/<span class="t"[^>]*>/) || ['?'])[0]);
  ok(first.includes('>' + t.runTime(row.started_at) + '<'), 'and shows the short form',
    t.runTime(row.started_at));
}

console.log('');
console.log(failed ? `${failed} FAILED` : 'all passed');
process.exit(failed ? 1 : 0);
