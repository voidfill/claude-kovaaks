# kvstats in the browser

A zero-install web version of the kvstats dashboard, with two ways in: a live
one for people who can give the browser a readable path, and a one-shot upload
for everyone else. The upload path exists so that nobody has to run a command
before they know whether the tool is worth it.

Status: design. Measured against a synthetic 12,000-run corpus on 2026-09-13;
every number below is observed, not estimated. The probe that produced them is
throwaway and is not part of this design.

## Why this shape

KovaaK's installs to `C:\Program Files (x86)\Steam\...` by default, and Chrome
refuses to hand a web page any directory under `Program Files`. That single
fact determines the whole design, so it is worth recording exactly what was
tried, because all three of these are closed and should not be re-litigated:

| Route | Result |
|---|---|
| `showDirectoryPicker()` on the install | Blocked: "contains system files" |
| Junction in the user profile pointing *at* the install | Blocked — Chromium resolves the link and applies the blocklist to the target |
| Drag-and-drop of the install folder | Blocked — the check is route-independent |
| Dropping `stats/` alone, deeper in the tree | Blocked — every descendant, not just upper levels |

Also ruled out, for the record: `file://` needs a browser launch flag, native
messaging needs an extension, Chrome's enterprise policies govern whether a site
may use the API at all rather than the sensitive-path list, and KovaaK's has no
configurable output directory (its `Config/` holds two JSON files and no path
setting).

What *is* allowed: any ordinary path outside the blocked roots, reached by
either access route. A junction is fine as long as it **resolves** somewhere
permitted — which is what makes the redirect in Tier 1 work, and it is the
opposite direction from the one that failed above.

`<input type="file" webkitdirectory>` is not subject to the blocklist and reads
the default install today. It buys reach at the cost of persistence and live
updates, which is exactly the trade Tier 2 makes.

## The two tiers

**Tier 1 — live.** The browser holds a `FileSystemDirectoryHandle` on a
readable directory. The index persists in IndexedDB, updates incrementally, and
new runs appear without a page reload. The handle is stored in IndexedDB and its
permission survives a restart — `queryPermission()` returns `granted` on a fresh
load with nothing asked, so the folder is chosen once and never again. Reached
two ways, neither of which the app can tell apart once it has the handle:

- Steam library already outside `Program Files` (a second drive, a custom
  library folder) — pick the install folder, done, no setup at all.
- Default install, redirected once by the user: move `stats/` and
  `performances/` into the user profile and junction them back into the game
  directory. The game writes through the junction unchanged; the browser picks
  an ordinary profile folder and never touches a link.

**Tier 2 — snapshot.** `<input type="file" webkitdirectory>`: the user points at
the install, Chrome confirms the file count, and the app reads everything once.
Full history, every curve, every PB — but no live updates, and refreshing means
picking the folder again. Works on the default install with no setup, and works
in Firefox and Safari, which have no File System Access API at all.

Tier 2 is the front door. Tier 1 is what a user upgrades to once they have
decided they want it, and the app should only ever mention the redirect *after*
they have seen their own data.

### The redirect, for the docs

Verified end to end on a real install on 2026-09-13: 2,380 stats and 2,070
`.perf` moved to the profile and junctioned back, a run played in-game landed
both its `.csv` and its `.perf` in the profile folder, the in-game score history
still displayed, and Steam's "verify integrity of game files" left the junctions
untouched. KovaaK's must be closed while the move runs. No administrator rights
are needed: Steam's folder already grants Users write access, which is why the
game can write stats there unelevated.

```bat
set GAME="C:\Program Files (x86)\Steam\steamapps\common\FPSAimTrainer\FPSAimTrainer"
mkdir "%USERPROFILE%\kvstats-data"
move %GAME%\stats "%USERPROFILE%\kvstats-data\stats"
move %GAME%\performances "%USERPROFILE%\kvstats-data\performances"
mklink /J %GAME%\stats "%USERPROFILE%\kvstats-data\stats"
mklink /J %GAME%\performances "%USERPROFILE%\kvstats-data\performances"
```

To undo — `rmdir` on a junction removes the link, never the target:

```bat
rmdir %GAME%\stats
rmdir %GAME%\performances
move "%USERPROFILE%\kvstats-data\stats" %GAME%\stats
move "%USERPROFILE%\kvstats-data\performances" %GAME%\performances
```

The user then points the app at `%USERPROFILE%\kvstats-data`.

## Measured budget

Synthetic corpus: 11,993 stats files, 10,454 `.perf`, 316 distinct scenarios,
298,653 kill rows, 165 MB on disk. Chosen because `.perf` files are recent and
heavy users will have far more than the 2,070 on the author's machine; several
costs below are invisible at 2,000 runs and dominant at 12,000.

| Phase | Tier 1 (FSA) | Tier 2 (upload) |
|---|---|---|
| Enumerate (handles, both dirs) | 11,781 ms | 58 ms after the dialog |
| Read stats (39.9 MB) | 3,279 ms | 8,039 ms |
| Read perf (55.7 MB) | 5,461 ms | 6,987 ms |
| Parse stats | 639 ms | 733 ms |
| Parse perf | 527 ms | 541 ms |
| Materialise marks | 41 ms | 50 ms |
| Write runs | 1,370 ms | 4,305 ms |
| Write curves | 853 ms | 832 ms |
| Write kills (packed) | 756 ms | 777 ms |
| **Bootstrap total** | **24.7 s** | **22.3 s** |
| Resulting database | 45.3 MB | 49.3 MB |

Read concurrency matters and then stops mattering: 1.391 ms/file serial,
0.271 ms at 8 parallel, 0.262 ms at 32, 0.279 ms at 128. Use a pool of 16–32
and do not tune further.

Re-opening in Tier 1 does not repeat the 24.7 s. Only names are needed to find
new files, and a names-only enumeration (`.keys()`, not `.entries()`) costs
~2.2 s per directory at this scale. Render from IndexedDB immediately and
reconcile in the background.

## Live updates

`FileSystemObserver` works: attached to `stats/`, it reported a new file within
the same second it was written, so there is no polling loop and no 2.2 s sweep
in the steady state. Keep the names-only enumeration only as the
reconcile-on-open path, and as a fallback if the constructor is missing.

One new run raises **several** events — `appeared` when the file is created,
then `modified` as the game writes its contents. The indexer must coalesce
events per filename and wait for quiet before reading, or it will parse a
half-written `.csv`. Do not index directly off an event; debounce, then read.

The `.perf` is written a moment after the `.csv` — about two seconds apart in
testing. A run whose `.perf` has not landed yet should be indexed without a
curve and upgraded when the second event arrives, which is the same state the
index already supports for the runs that never get one.

## Storage

Two decisions were settled by measurement and should not be revisited casually.

**Kills are packed one record per run, never one per kill.** 298,653 individual
records took 28,354 ms to write; the same data as 8,875 packed records took
756 ms — 37× — and it also *reads* faster, 4.7 ms against 16.2 ms for the
per-slot aggregate. Per-kill rows look reasonable at 2,000 runs and fall off a
cliff at 12,000.

**`best_before` and `played_before` are materialised at index time.** They
depend only on runs older than the row, so they never change once written. One
forward pass over runs sorted by `started_at` costs 41 ms and turns the rail's
correlated subqueries into a plain cursor walk: 4.2 ms against 296 ms.

```
runs          keyPath "id"
              index started_at
              index scen_time   ["scenario", "started_at"]
              index scen_score  ["scenario", "score"]   -- the PB run's id, for
                                                        loading its curve as a
                                                        baseline
              fields: the Stats.csv summary, plus buckets, plus the four
              materialised marks (best_before, played_before, and the
              same_cfg variants best_before_cfg, played_before_cfg)

curves        keyPath "run_id"
              { run_id, buckets, series: [7 ArrayBuffers] }
              order: shots, hits, misses, dmg_done, dmg_possible, score, kills

kills         keyPath "run_id"   -- packed, one record per run
              { run_id, n, t, dmg_done, dmg_possible, shots, hits: ArrayBuffers,
                bots: string[] }

scenarios     keyPath "name"     -- maintained aggregate, see below
meta          schema version, indexed filename set
```

`scenarios` is new. The scenario list is the one query that stays slow as a
scan (247 ms), and it is a page people open constantly; maintaining it on write
is the same trick already used for the run marks.

## Queries, translated

These were written and timed against the schema above. Median of 5 at 12k runs.
Reproduce them rather than re-deriving them.

**Run rail — `compare.page()`.** The SQL decorates each row with
`(SELECT MAX(p.score) ... )` and `(SELECT COUNT(*) ... )` over prior runs of the
same scenario. With the marks materialised this is a cursor walk on
`started_at` descending; `before` becomes the cursor's upper bound. **4.2 ms**,
and **3.6 ms** for a page 10,000 rows deep — paging stays flat.

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

Computing those marks per row instead costs **296 ms** — 70× worse. Recorded so
the shortcut is not mistaken for a premature optimisation.

**Peer set — `compare.candidates()`.** Every run of a scenario, in time order.
Served directly by `scen_time`. **2.0 ms**. Filter `cfg_key` and the duration
tolerance in JS afterwards; the index does the expensive part.

```js
const st = db.transaction("runs").objectStore("runs").index("scen_time");
const peers = await req(st.getAll(
  IDBKeyRange.bound([scenario, ""], [scenario, "\uffff"])));
```

**Best per slot — `_best_by_slot()`.** The SQL is
`SELECT idx, MAX(expr) FROM kill WHERE run_id IN (...) GROUP BY idx`. Packed,
it is a get per peer and a loop over typed arrays. **4.7 ms** over 30 peers.

```js
const os = db.transaction("kills").objectStore("kills");
const best = new Map();
for (const id of peerIds) {
  const p = await req(os.get(id)); if (!p) continue;
  const dd = new Float32Array(p.dmg_done), dp = new Float32Array(p.dmg_possible);
  for (let j = 0; j < p.n; j++) {
    if (!dp[j]) continue;                       // NULL guard: no damage offered
    const share = dd[j] / dp[j], cur = best.get(j + 1);
    if (cur === undefined || share > cur) best.set(j + 1, share);
  }
}
```

**Scenario list.** `GROUP BY scenario` with correlated subqueries for `pb`,
`recent_form` and the race-shape equivalents. As a full scan: **247 ms**.
Maintain the `scenarios` store on write instead; the scan below is the
rebuild-from-scratch path.

```js
const c = db.transaction("runs").objectStore("runs").openCursor();
// accumulate per scenario: runs, pb, last_played, and a rolling last-10 for
// recent_form / recent_elapsed
```

**Day view.** `substr(started_at,1,10) = ?` becomes a bounded range on
`started_at`, since the field is a sortable ISO string. **1.0 ms**.

```js
idx.getAll(IDBKeyRange.bound(day + "T00:00:00", day + "T23:59:59"))
```

**Curves for a run view** — the run's own, its PB, and recent form. Eleven gets
on `curves`: **1.8 ms**. Store and read the series as `ArrayBuffer`; IndexedDB
handles them natively and no encoding step is needed.

## The frontend

Unchanged in kind. `kvstats/web/` is already a static page — `app.js`,
`style.css`, and a vendored uPlot, no build step — talking to a small JSON API.
The work is replacing that API's implementation, not the UI: the fetches become
IndexedDB reads returning the same shapes. `scripts/drive-client.mjs`, which
already exercises `app.js` under a stub DOM, keeps working and becomes more
valuable, since there is no longer a Python server to test against.

## Parsers

`kvstats/perf.py` and `kvstats/statscsv.py` were ported to JS and verified
against the repository's own fixtures: 11 stats files and 7 `.perf` files,
including the truncated one, produce **zero differences** — every summary field,
`cm360`, `cfg_key`, kill count, kill offsets, bucket count, duration, and all
seven series sums. The port is the main correctness risk in this project and it
is retired.

Two traps cost real time and are now encoded in the port:

- A `.perf` metric is *omitted* for any second in which it was zero, so sample
  order is not time order. Every series must be rebuilt on a dense
  `floor(timestamp)` grid.
- Within a sample, integer series (shots, hits, misses, kills) are protobuf
  varints while the float ones (score, damage) are fixed32. Reading only the
  fixed32s silently yields zeros for four of the seven series — it parses
  cleanly and produces wrong numbers.

`tests/fixtures/kvstats/` is the shared asset between the Python and the JS.
Keep both suites pointed at it; it is what makes the two implementations
comparable rather than merely similar.

## The Python

Frozen, not deleted. It remains the reference implementation and the oracle the
JS is diffed against, and it is the only option for anyone who wants live
updates without redirecting anything. No new features land there. Retire it
only once the web app reaches parity, and not before the fixtures-based diff is
running in CI.

## Repository

kvstats moves to its own repository. It shares no code with
`kovaaks-playlists` — no import crosses between them in either direction — and
the audiences do not overlap: one is for Claude Code users who clone a repo, the
other is for KovaaK's players who want a link. A player being sent to something
called `claude-kovaaks` for a stats dashboard is being misinformed about what it
is and what it needs. The web app also wants its own GitHub Pages origin.

Carry the history across with `git filter-repo`. The format notes in
`references/kovaaks.md` get duplicated into both; it is prose, and duplicating
109 lines costs less than a dependency between two repositories.

## Settled by testing

All five questions this design opened were answered on 2026-09-13. Recorded so
they are not reopened:

1. **A stored handle survives a reload.** `queryPermission()` returns `granted`
   on a fresh page load having asked nothing. Tier 1 is chosen once, ever — no
   per-visit click, and no need for a PWA install to earn persistence.
2. **`FileSystemObserver` fires**, within the same second as the write. See
   Live updates above for the debounce this requires.
3. **The redirect works end to end**, including a real run written by the game
   through the junction.
4. **Steam's verify-integrity leaves the junctions alone.**
5. **The blocklist covers every descendant** of `Program Files`, not just its
   upper levels.

## Open questions

None blocking. Two worth settling during implementation:

- **How stale may Tier 2 data be?** A returning Tier 2 visitor has their old
  index in IndexedDB and no way to know whether it is current. Showing it
  immediately is right; what the app says about its age, and how loudly it
  offers a re-pick, is a UI decision this spec does not make.
- **Non-Windows.** The redirect is Windows-specific. Linux and macOS installs
  put the game elsewhere and are likely reachable by the picker directly, but
  no one has checked.

## Out of scope

Game settings, benchmark rank tracking, any upload of a user's data anywhere,
and anything resident in the background. The app reads files the user points it
at and writes only to its own origin storage. It never writes to the KovaaK's
install — the redirect in Tier 1 is performed by the user, once, with commands
they can read and undo.
