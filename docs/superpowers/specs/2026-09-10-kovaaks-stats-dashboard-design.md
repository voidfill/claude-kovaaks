# KovaaK's Stats Dashboard (`kvstats`) — Design

Date: 2026-09-10

## Goal

A localhost web dashboard that watches KovaaK's stats output and, the moment a run
finishes, shows its within-run performance curve against the player's own baselines —
answering *"did I do better, and in which parts of the run?"*

Primary use is a second monitor during play: the newest run auto-focuses, glanceable
between attempts.

**Scope change.** `README.md` currently states stats analysis is deliberately out of
scope for this repo. That line is superseded: `kvstats` is a sibling to the playlist
skill, not part of it. The README must be updated in the same change.

Out of scope for v1: terminal output (rejected — KovaaK's already shows it in-game),
per-kill TTK analysis, benchmark rank thresholds, anything that writes to the game.

## Verified environment facts

Measured on a real install on 2026-09-10, not assumed.

| Fact | Value |
|---|---|
| Stats dir | `<root>/stats/*.csv` — 2311 files, 9.6 MB |
| Perf dir | `<root>/performances/*.perf` — 2001 files, 9.7 MB |
| Runs with both | 1993. CSV-only: 318. Perf-only (orphans): 8 |
| `.perf` write lag behind its CSV | median 3 ms, p95 6 ms, max 30 ms (1993 pairs) |
| Distinct scenarios | 316 |
| History | 2025-12-27 .. 2026-09-10, 101 active days, median 20 runs/day, peak 81 |
| Runs per scenario | 94 have exactly 1, 163 have 2–9, 59 have 10+ |
| Scenario `Hash` stability | **0 of 316** scenarios changed hash across runs |
| PB run missing its `.perf` | **35 of 316** scenarios |
| Distinct cm/360 values used | 28 |
| Scenarios spanning >1 cm/360 | 82 of 316, covering 1403 runs |
| Scenarios with >=10 runs at one cm/360 | 50 (vs 59 with >=10 runs unfiltered) |
| Run durations (600 sampled) | 488 at 60 s; real tail to 118 s |

Filenames are the join key and the timestamp source:

```
<Scenario> - Challenge - YYYY.MM.DD-HH.MM.SS Stats.csv
<Scenario> - Challenge - YYYY.MM.DD-HH.MM.SS Performance.perf
```

All 2311 stats filenames match this pattern.

### Parse cost — the sizing decision

| Pass | Files | Time | Held in RAM |
|---|---|---|---|
| CSV summary only | 2311 | 1.2 s | 12 MB |
| CSV incl. per-kill rows | 2311 | 3.5 s | 70 MB |
| `.perf` full 1 s series | 2001 | 17.6 s | 77 MB |
| **Full cold parse** | 4312 | **~21 s** | **~145 MB** |

`.perf` decoding dominates at 8.8 ms/file (naive pure-Python wire walk). Incremental
cost is therefore ~10 ms per new run, against a median of 20 runs/day.

**Conclusion: parse everything once into a SQLite index, then only ever touch new
files.** No "recent files only" heuristic is needed; the full parse is a one-time 21 s.

## On-disk formats

### `stats/*.csv`

Three blocks in one file, not a well-formed CSV:

1. **Per-kill table** — `Kill #, Timestamp, Bot, Weapon, TTK, Shots, Hits, Accuracy,
   Damage Done, Damage Possible, Efficiency, Cheated, OverShots`. Empty for tracking
   scenarios (invincible bots have no discrete kills). Not consumed in v1.
2. **Summary** — `Key:,Value` lines: `Score`, `Kills`, `Hit Count`, `Miss Count`,
   `Damage Done`, `Avg TTK`, `Fight Time`, `Pause Count`, `Scenario`, `Hash`,
   `Game Version`, `Challenge Start`.
3. **Settings** — `Sens Scale`, `Horiz Sens`, `Vert Sens`, `DPI`, `FOV`, `FOVScale`,
   `Sens Increment`, `Resolution`, `Avg FPS`, `Crosshair`.

Parse rule: a line whose key ends in `:` is a summary/settings pair; a line starting
with a digit inside the first block is a kill row.

### `performances/*.perf` — undocumented protobuf

Reverse-engineered from the wire format and cross-checked against the CSV totals.
No `.proto` is published; the decoder is a hand-rolled wire walker.

Top level:

- **field 1** — header submessage: `1` scenario name, `2` hash, `3` epoch-ms start,
  `4` unknown int, `5` submessage with bot file, map, weapon.
- **field 2, repeated** — one sample per emitted metric per second. Each carries
  `1` = float timestamp (fixed32) plus exactly one metric submessage:

| Field | Metric | Encoding |
|---|---|---|
| 2 | shots | varint |
| 3 | hits | varint |
| 4 | misses | varint |
| 5 | damage done | float |
| 6 | damage possible | float |
| 7 | **score gained** | float |
| 8 | kills | varint |

**Validation:** summing each series reproduces the CSV summary exactly. A tracking run
gave 6001 shots / 3913 hits / 2088 misses / score 3913.0; a clicking run gave
80 / 52 / 28, damage 52000.0, score 52.0. Both matched their CSV to the unit.

**The critical quirk: zero buckets are omitted, not written as `0`.** One clicking run
had 59 shot samples but only 43 hit and 26 miss samples. Any decoder that treats sample
order as time order silently shifts every curve.

Run duration is the last sample timestamp. There is no duration field in the CSV, so
runs without a `.perf` have unknown duration.

## Sensitivity normalisation

`cfg_key` is **true cm/360**, and nothing else. Two runs are comparable iff their
cm/360 matches.

`Horiz Sens` alone is not usable: `Sens Scale` varies across the corpus — cm/360 (2083
runs), The FINALS (227, Dec–Mar), Overwatch (1) — so the number is in whatever scale was
active. It is however derivable from fields present in every CSV:

```
cm/360 = 13062.86 / (DPI × Sens Increment)
```

Validated three ways:

- reproduces the labelled value **exactly for all 2083** cm/360-scale runs; the constant
  `DPI × increment × cm360` is stable across them to 7 significant figures
  (13062.816 – 13062.912, the spread being rounding in the stored increment);
- two independent scales converge — `The FINALS 33 @ 400 DPI` and `Overwatch 5 @ 400 DPI`
  both yield **69.27 cm/360**, and those are in fact the same sensitivity;
- it is scale-independent, so an unrecognised `Sens Scale` still converts correctly.

Store the computed cm/360 rounded to 2 dp; key on it rounded to 1 dp. 28 distinct
values appear across the corpus. Filtering by `cfg_key` is affordable: each affected
scenario is dominated by one sensitivity (e.g. `VT Snake Track Intermediate S5` is 90
runs at 52.0 and 12 at 60.0), so 50 scenarios retain >=10 same-sens runs against 59
unfiltered. The relax toggle exists for the rest.

**FOV is excluded from `cfg_key`** by decision: benchmark runs are expected to be at the
103 Overwatch standard. `FOVScale` and `FOV` are still stored for display. Note one run
records `FOV: 1.1`, which is junk and should surface rather than be averaged in.

## Data model

SQLite, single file, **disposable** — it is a derived cache over files the game owns.
Nothing lives only in it, which makes "drop and re-bootstrap" (21 s) a legitimate
migration strategy.

```sql
CREATE TABLE run (
  id           INTEGER PRIMARY KEY,
  scenario     TEXT NOT NULL,
  started_at   TEXT NOT NULL,          -- ISO 8601, parsed from the filename
  stats_file   TEXT NOT NULL UNIQUE,   -- dedup key; an indexed file is never re-read
  perf_file    TEXT,                   -- NULL for the 318 CSV-only runs
  score REAL, kills INT, hits INT, misses INT, shots INT,
  accuracy REAL,                       -- hits/shots
  damage_done REAL, damage_possible REAL,
  avg_ttk REAL, fight_time REAL, pause_count INT,
  duration_s   REAL,                   -- last perf timestamp; NULL without a perf
  spm          REAL,                   -- score/duration*60; NULL when duration unknown
  hash TEXT, game_version TEXT,
  sens_raw REAL, sens_scale TEXT, dpi INT, sens_increment REAL,
  cm360        REAL,                   -- derived; see above
  cfg_key      TEXT,                   -- round(cm360, 1) as text
  fov REAL, fov_scale TEXT, resolution TEXT, avg_fps REAL
);
CREATE INDEX run_scen_time  ON run(scenario, started_at);
CREATE INDEX run_scen_score ON run(scenario, score DESC);

CREATE TABLE curve (
  run_id  INTEGER PRIMARY KEY REFERENCES run(id) ON DELETE CASCADE,
  buckets INT NOT NULL,                -- length of every array below
  shots BLOB, hits BLOB, misses BLOB,
  dmg_done BLOB, dmg_possible BLOB, score BLOB, kills BLOB
);

CREATE TABLE failed (path TEXT PRIMARY KEY, tries INT, last_error TEXT, last_try TEXT);
CREATE TABLE meta   (key TEXT PRIMARY KEY, value TEXT);   -- schema_version, last_scan
```

Blobs are `array('f').tobytes()` — fixed width, so a read is a `memoryview`, not a
parse. 7 series × 60 × 4 B ≈ 1.7 KB/run; the whole index lands around 10 MB.

**Densification happens at index time.** Each series is expanded onto a dense 1 s grid
via `floor(timestamp)`, gaps zero-filled, all series the same length. This moves the
omitted-zero problem into the indexer once, so comparing two runs at query time is
plain elementwise arithmetic on matching indices.

## Comparison

### Baseline selection

For a focused run of scenario S:

1. Candidates = all other runs of S sharing `cfg_key`. A UI toggle relaxes this to
   ignore sensitivity changes.
2. Drop candidates whose `duration_s` differs from the focused run's by more than
   10% — those are aborted attempts, not comparable ones. Candidates with unknown
   duration (no perf) are kept for score baselines but cannot supply a curve.
3. **PB curve = the highest-scoring candidate that has a curve.** Where that is not the
   true PB (35 of 316 scenarios), the header shows the real PB score and the overlay is
   labelled *"best run with curve data"*. It must not silently present a non-PB as PB.
4. **Recent form** = the N most recent candidates started before the focused run
   (default N = 10), as a per-bucket mean ± 1σ band. Under 3 candidates, draw the mean
   with no band; at zero candidates, the run charts alone.

Baselines are user-configurable. v1 ships PB-curve and recent-form; median/p25–p75 across
all runs, and session-average, are designed for but not built.

Runs of differing length compare over the shorter prefix, with the tail marked.

### The two charts

**Top — rate per second.** Focused run bold, PB dashed, recent-form band shaded. Metric
selector: score/s, shots/s, hits/s, accuracy, efficiency, kills/s.

Rate metrics are smoothed with a **centered rolling mean, default 5 s**, toggleable to
raw / 3 s / 5 s. This is not cosmetic: a tracking run has ~100 shots/s so per-second
accuracy is stable, but a clicking run has ~1.3, where per-second accuracy is a square
wave between 0% and 100%.

**Bottom — cumulative delta.** `cum[i] = Σ(j≤i) (mine[j] − base[j])`, in raw score
units, filled above/below a zero line, hover-synced with the top chart.

This is the chart that answers the question. It reads as *"+120 up by 20 s, gave back
300 between 35 s and 50 s"*, and it has an invariant worth the design:
**its final value is exactly the score difference vs the baseline**, so the chart and
the headline number can never disagree.

Deliberately in score units, not percent — per-bucket percentages diverge wherever the
baseline bucket approaches zero. Percent appears only on the summary line.

## Architecture

One process, one command, no daemon, no config file.

```
kvstats/
  __main__.py   python -m kvstats  ->  serve
  paths.py      locate KovaaK's dirs, env overrides (same convention as kvpl.py)
  statscsv.py   CSV   -> dict           | pure; no DB, no globals,
  perf.py       .perf -> dense arrays   | testable with no KovaaK's install
  compare.py    baselines + curve math  |
  index.py      schema, bootstrap, incremental scan
  server.py     ThreadingHTTPServer, JSON endpoints, SSE
  web/          index.html, app.js, style.css, vendor/uplot.*
```

**Stdlib only, everything vendored, no build step.** `sqlite3`, `http.server`, `array`,
`struct`. Charts use uPlot (MIT, ~45 KB, single file, zero deps, canvas) committed under
`web/vendor/`. No npm, no bundler, no CDN — it works offline.

**Two-tier polling rather than `watchdog`.** Measured on this install:

| Probe | Cost |
|---|---|
| `scandir` both dirs (4312 entries) | 10.85 ms median |
| `stat()` both dirs (mtime only) | **0.034 ms** median |

`stat()` the two directories every **100 ms** and `scandir` only when a directory's
mtime has moved (confirmed to advance on file creation). Idle cost is 0.03% of one
core -- less than a 2 s scandir poll -- while detection latency drops to <=100 ms.
A full `scandir` every 10 s regardless self-heals if the mtime signal is ever missed
on a filesystem where it is unreliable.

This meets the latency requirement without a dependency: **~170 ms** worst case from
run-end to rendered comparison (100 ms detect + 11 ms scandir + 10 ms parse + ~50 ms
push/fetch/render).

Endpoints:

```
GET  /                               static shell
GET  /api/runs?scenario=&limit=      run list
GET  /api/run/<id>                   summary + curves + resolved baselines
GET  /api/scenarios                  name, run count, PB, recent form
GET  /api/session/today              session view
GET  /api/health                     indexed / failed / awaiting-perf counts
GET  /events                         SSE stream
```

`PlaylistInProgress.json` is read best-effort to show the active playlist and per
scenario play counts (the `run 3/4` indicator). It degrades to absent.

## Live path and its hazards

Tick (100 ms) → `stat()` both dirs → on mtime change `scandir` → diff against an
in-memory `known` set seeded from the DB →
parse → insert → push `{"type":"run","id":N}` over SSE → browser fetches and re-renders.

The CSV and the `.perf` for one run are written **separately**, and a scan can catch a
file mid-write. Three guards:

- **Index off the CSV immediately; never block on the perf.** In practice the perf is
  already there: across 1993 pairs its mtime trails the CSV by a median of 3 ms and a
  maximum of 30 ms, so a run detected at the 100 ms tick has its curve on disk. The
  *awaiting-perf* list therefore exists for correctness, not latency -- it holds a run
  for a ~60 s deadline, after which `perf_file` stays NULL, already a supported state
  for 318 runs. Orphan perfs with no CSV (8 observed) are ignored.
- **A parse failure is neither fatal nor permanent.** On exception the path is not added
  to `known`, so the next tick retries; after 5 tries it lands in `failed` and stops.
  A mid-write file therefore resolves itself 2 s later.
- Every parse is wrapped per-file. One bad file never stops the scan.

| Failure | Behaviour |
|---|---|
| KovaaK's dir missing | exit 4 naming the path (matches `kvpl.py`) |
| Truncated/corrupt `.perf` | run still indexed from CSV, `perf_file` NULL, shown in health |
| Unknown `Sens Scale` | cm/360 still computes; the formula is scale-independent |
| Schema change | bump `meta.schema_version`, drop and re-bootstrap |
| Port in use | try the next port, print the URL |

## Implementation plan

1. `paths.py`, `statscsv.py` + tests. CSV → dict, all three blocks.
2. `perf.py` + tests. Wire walker, field map, densification, duration.
3. `index.py` + tests. Schema, bootstrap, incremental scan, `failed` handling.
4. `compare.py` + tests. cm/360, baseline selection, alignment, cumulative delta.
5. `server.py`. Static serving, JSON endpoints, SSE.
6. `web/`. Shell, uPlot charts, live update, metric/smoothing/baseline controls.
7. README: replace the out-of-scope line, document `python -m kvstats`.

## Testing

Fixtures: real CSV/`.perf` pairs committed as binary (`.gitattributes` already forces
this for fixtures in this repo), covering a tracking run, a clicking run, a run with no
perf, and a truncated perf.

- perf densification, specifically the **omitted-zero** case — the bug that silently
  shifts every curve
- cm/360 across all three sens scales
- curve alignment with mismatched durations
- baseline selection when the PB has no perf
- **property: the cumulative-delta curve's final value equals the score difference**
- integration: bootstrap a temp DB from a fixture dir, drop in a new file, assert the
  watcher indexes it

## Open risks

- **The `.perf` format is undocumented.** The field map is inferred and validated against
  CSV totals, but a KovaaK's update could change it. Mitigation: `perf.py` fails per-file
  and the run survives on CSV alone. Sample field 12 (observed once, in a clicking run)
  and header field 4 remain unidentified.
- **The cm/360 constant 13062.86 is empirical**, derived from this install's data. It is
  strongly evidenced but not sourced from documentation.
- **Scenario identity is the name.** Hash was stable across all 316 scenarios here, but a
  scenario edited in place under the same name would corrupt its own history. Storing
  `hash` lets a future version detect it.
- **The long tail.** 94 scenarios have a single run; baselines are meaningless for them
  and the UI must degrade rather than show empty charts.
- **Session boundaries are inferred** from inter-run gaps; there is no session marker in
  the data.
