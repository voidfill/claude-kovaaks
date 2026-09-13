# kvstats in the browser, read-only

A zero-install history browser for KovaaK's runs: open a link, point it at your
game folder, see every run you have ever played. No install, no terminal, no
account, and nothing leaves the machine.

This is step one of three. Live updates are
[live updates](2026-09-13-kvstats-live-updates-design.md); getting a *watchable*
folder out of a default install is
[reaching the install](2026-09-13-kvstats-reaching-the-install.md). Neither is
required for this document to ship, and this document must not assume either.

Status: design. Measured 2026-09-13 against a synthetic 12,000-run corpus; see
Evidence for what those numbers do and do not support.

## What this is for

Most people arriving here are **trying the tool**, not adopting it. They want to
see whether their own data looks interesting before they agree to anything. So
the first run must work on an unmodified default install, in whatever browser
they have, with no setup — and it must reach something worth looking at fast.

Anyone who then decides they want live updates goes to steps two and three. This
document's job is to be good enough that they want to.

The entry point is therefore `<input type="file" webkitdirectory>`: a one-shot
directory snapshot. Unlike the File System Access API it is not subject to
Chrome's sensitive-path blocklist, so it reads a default
`C:\Program Files (x86)\Steam\...` install directly, and it works in Firefox and
Safari, which have no File System Access API at all. What it cannot do is look
again without another user gesture — hence no live updates here.

If the browser *does* offer `showDirectoryPicker()` and the user's folder is
readable, prefer it: same code past the source boundary, and it leaves the door
open for step two. Do not require it.

## IndexedDB is a cache, not a record

The files on disk are the source of truth. The database only exists so that a
return visit does not re-read 22,000 files. Everything follows from that:

- **Migrations are "bump the version, drop it, re-bootstrap."** The same
  strategy `index.py` already uses, for the same reason, and it stays valid here
  because nothing lives only in the database.
- **Eviction is survivable.** Browsers may clear best-effort origin storage, and
  Safari does so after about seven days without interaction. The cost is a
  re-pick and one bootstrap, not lost data. Call
  `navigator.storage.persist()` after the first successful bootstrap to make it
  less likely, record the answer, and treat a miss as normal rather than an
  error.
- **A cache with no source is stale, and must say so.** A returning visitor sees
  their index immediately — that is the point of keeping it — but the app knows
  only when it last read the folder, not what has happened since. Show the age
  of the data and make re-picking one obvious click.

The only thing that genuinely degrades is convenience, and only in this tier: a
re-bootstrap here needs a user gesture, where step two's would be silent.

## Source boundary

Everything above the source is identical in both tiers, and step two plugs in
here without surgery. A source provides:

- `list()` — the run basenames it can offer
- `read(basename)` — the `.csv` text and the `.perf` bytes, either possibly absent
- `subscribe(fn)` — optional; absent in this document

A `FileList` from the upload control satisfies the first two. A
`FileSystemDirectoryHandle` satisfies all three. **The indexer must never see
which it has.**

Equally load-bearing: the incremental path — *index these N new basenames into
an existing database* — exists from day one, even though only a manual re-pick
triggers it here. It is exactly the code an observer calls later. A bootstrap is
then just the incremental path over an empty database, not a second
implementation.

`id` is derived from the basename, which is stable across tiers and across
machines, and is what makes a re-pick reconcile instead of duplicate. It must
not be a row counter and must not embed an absolute path — `index.py` keys on
`stats_file` as an absolute path today, and that does not survive a browser
where absolute paths do not exist.

## Storage

Two layout decisions were settled by measurement and should not be revisited
casually.

**Kills are packed one record per run, never one per kill.** 298,653 individual
records took 28,354 ms to write; the same data as 8,875 packed records took
756 ms — 37× — and it also reads faster, 4.7 ms against 16.2 ms for the
per-slot aggregate. Per-kill rows look reasonable at 2,000 runs and fall off a
cliff at 12,000.

**`best_before` and `played_before` are materialised at index time**, turning
the rail's correlated subqueries into a cursor walk: 4.2 ms against 296 ms.

```
runs          keyPath "id"            -- derived from the basename
              index started_at
              index scen_time   ["scenario", "started_at"]
              index scen_score  ["scenario", "score"]   -- the PB run, for its curve
              fields: the Stats.csv summary, buckets, and the materialised
              marks (best_before, played_before, and the same_cfg variants)

curves        keyPath "run_id"
              { run_id, buckets, series: [7 ArrayBuffers] }
              order: shots, hits, misses, dmg_done, dmg_possible, score, kills

kills         keyPath "run_id"        -- packed: typed arrays plus a bots array

scenarios     keyPath "name"          -- maintained aggregate
              runs, pb, last_played, rolling last-10, AND the full shape record:
              shape, penalising, budget, pool, bots, clock_s, windowed, evidence

failed        keyPath "basename"      -- parse failures with a try count

meta          schema version, the indexed basename set, last-read timestamp
```

`scenarios` carries the **shape** fields, not just the list-page aggregate.
`shapes.py` classification is structural — `compare.page()` exempts race
scenarios from the duration-tolerance rule, and `build_run_payload` branches
entirely on shape — so a schema without it silently reverts the 2026-09-12
design. Note that classification is a fold that can flip retroactively: a
scenario becomes race on its first `.perf`, and `fixed_windows` needs at least
two runs. Recompute a scenario's shape whenever a run is added to it, and
recompute the affected runs' marks when it changes (see below).

`failed` mirrors the Python's table and its `MAX_TRIES` budget. The spec expects
half-written files; without a retry ceiling a genuinely corrupt one is either
re-parsed forever or dropped silently.

### Marks are materialised, not immutable

An earlier draft claimed the marks "never change once written." That is false
outside strict append order. They must be recomputed when:

- a run is inserted **older** than one already indexed — a re-pick after a
  restored backup, or any out-of-order arrival;
- a scenario's **shape** changes, because the race exemption changes which prior
  runs count.

Recomputing is a forward pass from the earliest affected `started_at`, which
costs 41 ms over the whole 12k corpus. Cheap enough that the rule can simply be:
detect the condition, walk forward, done.

### Multi-tab

Two tabs share one database and will both try to index and both read-modify-write
the maintained aggregates — a lost update that does not self-heal, because the
aggregates are maintained rather than derived on read. Elect a writer with Web
Locks; other tabs read. This is cheap now and very annoying to retrofit.

## Queries, translated

Written and timed against the schema above; median of 5 at 12k runs. Reproduce
rather than re-derive.

**Run rail — `compare.page()`.** With the marks materialised this is a cursor
walk on `started_at` descending; `before` becomes the cursor's upper bound.
**4.2 ms**, and **3.6 ms** at 10,000 rows deep — paging stays flat.

```js
const idx = db.transaction("runs").objectStore("runs").index("started_at");
const out = [];
await new Promise((res, rej) => {
  const c = idx.openCursor(before ? IDBKeyRange.upperBound(before) : null, "prev");
  c.onsuccess = () => {
    const cur = c.result;
    if (!cur || out.length >= limit) return res();
    out.push(cur.value); cur.continue();
  };
  c.onerror = () => rej(c.error);
});
```

Computing the marks per row instead costs **296 ms**, 70× worse — recorded so
the materialisation does not read as premature optimisation.

**Peer set — `compare.candidates()`.** Served directly by `scen_time`, **2.0 ms**.
Apply the `cfg_key` filter, the duration tolerance, and the race exemption in JS
afterwards; the index does the expensive part.

```js
const st = db.transaction("runs").objectStore("runs").index("scen_time");
const peers = await req(st.getAll(
  IDBKeyRange.bound([scenario, ""], [scenario, "\uffff"])));
```

**Best per slot — `_best_by_slot()`.** `GROUP BY idx` becomes a get per peer and
a loop over typed arrays. **4.7 ms** over 30 peers.

```js
const os = db.transaction("kills").objectStore("kills");
const best = new Map();
for (const id of peerIds) {
  const p = await req(os.get(id)); if (!p) continue;
  const dd = new Float32Array(p.dmg_done), dp = new Float32Array(p.dmg_possible);
  for (let j = 0; j < p.n; j++) {
    if (!dp[j]) continue;                       // no damage offered
    const share = dd[j] / dp[j], cur = best.get(j + 1);
    if (cur === undefined || share > cur) best.set(j + 1, share);
  }
}
```

**Scenario list.** Read the maintained `scenarios` store. The full scan below is
only the rebuild path: **247 ms**.

**Day view.** `substr(started_at,1,10) = ?` becomes a bounded range, since the
field is a sortable ISO string. **1.0 ms**.

```js
idx.getAll(IDBKeyRange.bound(day + "T00:00:00", day + "T23:59:59"))
```

**Curves for a run view** — eleven gets on `curves`, **1.8 ms**. Store the series
as `ArrayBuffer`; IndexedDB handles them natively, no encoding step.

## The real work is the payload builder

The queries above are the easy half and translating them is nearly done. The
bulk of `server.py`'s 650 lines is not queries:

- `build_run_payload` / `_fill_timed` / `_fill_race` — metric selection,
  smoothing, baseline resolution, `compare_until`'s tail marking
- `_race_splits` — dead-time residual and `delta_adj`, which sums to zero by
  construction
- `_bot_windows` and `_window_summary` — per-window share against PB and recent
  form, weighted rather than an unweighted mean of per-window shares
- all of `compare.py` — `cumulative_delta`'s pad-don't-truncate invariant,
  `resample_race`, `race_grid`, `band`'s three-curve rule

Every one carries a comment recording something learned the hard way. Budget for
this as the main body of work, not as a detail of "porting the API."

## Verification

**The parsers are ported and agree with the Python across the repository's
fixtures** — 11 stats files and 7 `.perf`, including the truncated one, zero
differences on every summary field, `cm360`, `cfg_key`, kill counts and offsets,
bucket counts, durations, and all seven series sums.

That is real but not sufficient: 11 files chosen *because* they exercise known
traps cannot retire the risk. **The exit criterion is a full-corpus oracle
diff** — the frozen Python and the JS over a complete real install, every field
compared. Specifically not yet covered: float formatting (Python's `round()` is
banker's rounding, JS's is not, and `cfg_key` is rounded to 1 dp — a one-ulp
disagreement silently changes which runs are comparable), non-ASCII scenario
names through `TextDecoder`, and truncated or malformed CSVs.

The same oracle applies to the payload builder: same run id, same options,
Python JSON against JS JSON, byte-identical.

Two traps are already encoded in the port and must stay encoded:

- A `.perf` metric is *omitted* for any second in which it was zero, so sample
  order is not time order. Series are rebuilt on a dense `floor(timestamp)` grid.
- Within a sample, integer series are protobuf varints while float ones are
  fixed32. Reading only the fixed32s parses cleanly and silently yields zeros
  for four of the seven series.

**`scripts/drive-client.mjs` does not survive as-is.** It is `fetch()` against a
running server on `:8777` plus an `EventSource` stub; with the server gone there
is nothing to connect to, and Node has no IndexedDB. Reviving it means either a
dev dependency in a repo whose stated virtue is having none, or keeping the
Python server alive purely as a test fixture. Decide this deliberately — it is a
real work item, not a footnote.

## Evidence

Every number here is **one run, one machine, one browser, one disk**, against a
*synthetic* corpus. That supports the relative conclusions strongly — the 37×
packing win, the 70× marks win, flat deep paging, the shape of the concurrency
curve — because both sides of each comparison ran under identical conditions.

It supports the absolute wall-clock numbers much more weakly. Synthetic files
have uniform sizes and names and were written in one pass; a real folder is nine
months of interleaved writes. The reads were very likely warm-cache, and a real
first run is cold, on unknown hardware, with an antivirus scanning 22,000 file
opens by a browser process — the workload Defender is most expensive on. Treat
the 22–25 s bootstrap as a lower bound, plausibly several times higher in the
field, and note it is the only number a new user ever experiences.

Untested and worth knowing before promising anything: peak memory (Tier 2 hands
the page 22,000 `File` objects and reads 95 MB), behaviour when the tab is closed
mid-bootstrap, and Firefox and Safari entirely — despite cross-browser reach
being half of this tier's case.

| Phase (12k runs, upload tier) | |
|---|---|
| Enumerate after the dialog | 58 ms |
| Read stats (39.9 MB) | 8,039 ms |
| Read perf (55.7 MB) | 6,987 ms |
| Parse stats (298,653 kill rows) | 733 ms |
| Parse perf (10,454 files) | 541 ms |
| Materialise marks (316 scenarios) | 50 ms |
| Write runs / curves / packed kills | 4,305 / 832 / 777 ms |
| **Total** | **22.3 s**, 49 MB database |

Read concurrency: 0.925 ms/file serial, 0.175 ms at 8 parallel, 0.279 at 32,
0.190 at 128. A pool of 8–32 is right on this machine; do not hard-code a
number tuned on one device, and never assume high concurrency helps on a
spinning disk or a network-redirected profile.

## The frontend

`kvstats/web/` is already static — `app.js`, `style.css`, a vendored uPlot, no
build step — talking to a small JSON API. The API's *implementation* changes;
the payload shapes should not. Keep them identical so the oracle diff above can
compare them directly.

## The Python

Frozen: no new features, kept as the reference implementation and the oracle.
It also remains the only live option for anyone who has Python and will not
modify their install, which is a larger group than it sounds.

Retirement is deliberately not scheduled here. The previously stated condition —
"once the fixtures diff runs in CI" — is unreachable: there is no CI in this
repository and standing it up (Python, Node, fixtures, a differential harness)
is unscoped work. Either scope that separately or stop treating retirement as
imminent.

## Out of scope

Live updates, the access problem for default installs, game settings, benchmark
rank tracking, and any upload of user data anywhere. Note the wording collision:
Chrome's directory control shows *"Upload N files to this site?"* — nothing is
uploaded, and the UI must say so before the dialog appears, because it is the
first thing a new user reads.

## Open questions

- **How loudly should staleness be reported?** The app knows when it last read
  the folder and nothing since. Age is easy; how insistently to prompt a re-pick
  is a UI decision this document does not make.
- ~~Non-Windows.~~ Answered from Chromium's blocklist source: Linux's default
  Steam path is not listed, so those users get the live tier directly with no
  setup; macOS is blocked but the game does not ship for it. See
  [reaching the install](2026-09-13-kvstats-reaching-the-install.md).
- **What replaces `drive-client.mjs`**, given the constraints above.
