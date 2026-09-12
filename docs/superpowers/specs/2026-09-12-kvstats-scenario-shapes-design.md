# kvstats Scenario Shapes — Design

Date: 2026-09-12

## Goal

The dashboard assumes every scenario is scored on a fixed clock. Ten scenarios in the
reference install are not: they spawn a fixed number of bots one after the next and score
you on how long you took. For those, the current charts plot a countdown timer as if it
were a rate, and put the entire comparison inside a region the UI deliberately greys out.

This change classifies every scenario by its **scoring shape** and lets the shape drive
the axes, the baseline selection and the chart marks — so that a run is displayed in the
terms it was actually scored in.

Out of scope: changing any scoring, writing to the game, benchmark rank thresholds,
decomposing a score bucket into gross gain and gross loss (shown below to be impossible
from the data).

## Verified facts

Measured against the real install on 2026-09-12 — 2360 runs, 316 scenarios, 2050 `.perf`
files. Nothing here is assumed.

### The race family

`score + elapsed = 1000.00`, holding to ±0.02 s across all 146 runs of all ten scenarios.

| Scenario | Bots | HP each | Pool | Duration range | Runs |
|---|---|---|---|---|---|
| Air Pure Medium | 5 | 1000 | 5000 | 81.2–94.3 s | 26 |
| Air Spectral Easy | 6 | 800 | 4800 | 72.6–85.1 s | 30 |
| Ground Plaza Sparky V3 | 6 | 1000 | 6000 | 105.9–124.8 s | 26 |
| Air CELESTIAL No UFO Easy Slowed | 8 | 800 | 6400 | 104.9–122.3 s | 22 |
| Air Pure Intermediate Slower No UFO | 5 | 1000 | 5000 | 79.8–91.2 s | 28 |
| Air CELESTIAL No UFO Medium | 8 | 800 | 6400 | 107.7–123.4 s | 5 |
| Air CELESTIAL No UFO Easier | 8 | 800 | 6400 | 98.0–100.6 s | 4 |
| Ground Plaza Sparky v3 Easy | 6 | 1000 | 6000 | 110.9–111.7 s | 3 |
| Air Pure Easier No UFO | 4 | 1000 | 4000 | — | 1 |
| Air Spectral Easy 85% | 6 | 800 | 4800 | — | 1 |

The Runs column is every run; duration ranges are measured over the `.perf`-backed subset
(126 of the 146).

Hits, kills and damage are **constant across every run** of a race scenario. Only time
varies. The `.perf` score series is a literal clock:

```
Air Pure Medium              score  [999, -1, -1, -1, -1, ...]
VT Ground Intermediate S5    score  [ 62, 46, 55, 40, 45, ...]
```

**Kill k always lands at cumulative damage `k x pool/N`, exactly:**

```
dur= 93.85  kills at (sec, cumdmg): [(15,1000),(32,2000),(47,3000),(65,4000),(93,5000)]
dur= 87.87  kills at (sec, cumdmg): [(15,1000),(32,2000),(45,3000),(64,4000),(87,5000)]
```

This is the load-bearing fact: on a seconds axis the boundaries never align; on a
cumulative-damage axis they align in every run, for free.

### Dead time (respawn gaps)

`Fight Time == sum(TTK)` exactly, and **excludes** the inter-bot gaps. Per-bot splits are
therefore already gap-free and need no correction.

```
bot 1 AIR1_Short_close  spawn= 0.255 kill=15.735 ttk=15.480 gap_before=0.255 overshots=25
bot 5 AIR2_Mid_UFO      spawn=65.530 kill=93.849 ttk=28.319 gap_before=0.009 overshots=0
fight_time=92.808  sum(ttk)=92.808  elapsed=93.849  score+elapsed=999.987
```

The 25 overshots per bot are that gap at the ~100/s tick rate — the player firing into a
dead bot.

Dead time is **not** a fixed per-bot constant: 0.058 s for Air Spectral Easy (6 bots) but
1.826 s for Air CELESTIAL No UFO Medium (8 bots). It is constant to ±0.003 s *within a
game version* and shifts across versions:

```
Air CELESTIAL No UFO Easy Slowed    v3.8.4 2.835   v3.9.0 1.823   v3.9.1 1.825
Air Pure Intermediate Slower No UFO v3.8.3 1.653   v3.9.0 1.044   v3.9.1 1.045
```

**Decision: do not model it.** No per-scenario constant, no version tracking. Dead time is
a per-run observed residual, `dead_s = elapsed_s - fight_time`, which is exact, needs no
cross-version assumption and cannot go stale. When two runs from different game versions
are compared, the shift is visible in the dead-time row of the split table rather than
hidden behind a flag.

### Penalising scenarios

31 scenarios write negative score buckets.

```
VT 1w2ts Horizontal    score  [10, 10, 20, 5.6, 9.9, 19.9, ..., -4.0, 6.2, ...]
                       misses [ 0,  0,  0,   1,   0,    0, ...,    1,   1, ...]
```

+10 a kill, about -4 a miss. `1w2ts Pasu Perfected Goated` additionally decays kill value
with TTK (10.0 down to 2.3); `VT Quadpulse` / `VT Widepulse` take return fire
(`Damage Taken` 16–43) folded into score.

### Other score families (fixed clock)

`score = hits` (68 scenarios, tracking ticks) · `score = k x hits` (Controlsphere x3,
Centering II x18, Smooth Your Vertical x6) · `score = 10 x kills` (Voltaic clicking, 9) ·
`score = damage_done`. Durations pinned to 45 s / 60 s within ±0.1 s.

### Per-kill table volume

75,217 rows across 2360 runs; ~4.5 MB of SQLite on a 6.3 MB database; 0.5 s to parse.
124 distinct bot names, 16 weapons. **1090 of 2360 runs have no kill rows at all** — pure
invincible-tracking scenarios where nothing ever dies. Splits do not apply there and the
UI must render zero kill marks rather than an empty table.

### Metrics that silently mean two things

`efficiency` is `dmg_done / dmg_possible` per bucket. On `VT Ground Intermediate S5` the
whole-run totals are 3 and 6 — damage is only booked at kill time — so per-second
efficiency is ~0 everywhere. On `Air Pure Medium`, `dmg_done == hits`, so efficiency is
just accuracy. Unsurfaced entirely today: overshots (74 scenarios, max 22,170), reloads
(18), damage taken (4).

## Classification

Tiered, strongest evidence first. Stored per *scenario*, so curveless runs inherit it.

1. **`.perf` countdown test.** `score[0] > 0` and >= 90 % of interior buckets within 0.01
   of -1 → `race`, `budget = score[0] + 1`.
   Measured: **126/126 perf-backed race runs, 0 false positives, 0 false negatives.**
   The 90 % slack is required, not cosmetic: `perf.py` buckets by `floor(timestamp)`, so
   timing jitter produces an occasional `0` followed by `-2`. A strict all-buckets rule
   scores 123/126.
2. **CSV-only fallback**, for the 20 race runs with no `.perf`. Scenario-level: `score +
   elapsed` constant to 0.1 while `elapsed` varies by > 1 s. 0 FP / 0 FN at scenario level.
   Requires >= 2 runs. A single-run, curveless scenario stays `timed` until a second run
   arrives — the safe direction, and self-correcting. All ten race scenarios have at least
   one `.perf`-backed run today, so tier 1 covers them all.
3. **`penalising`** — any negative bucket in the `.perf` score series. Computed only for
   non-race scenarios; a countdown is negative every bucket by construction, which is not
   a penalty.
4. Otherwise `timed`, `clock_s` = median duration.

A single-run CSV test (`score + elapsed` lands on a round hundred) was rejected: it yields
6 false positives out of 2360, all fixed-60 s scenarios whose score happens to land near a
round hundred minus 60.

`elapsed` is derived from the CSV (`last kill timestamp - Challenge Start`), not from the
`.perf`. It matched perf duration to 0.01 s on every run checked and it works on curveless
runs. No run in the corpus crosses midnight; wrap anyway.

## Data model

Schema version 1 → 2. `connect()` already drops and rebuilds on a version bump. Cost:
~21 s → ~22 s bootstrap, 6.3 MB → ~11 MB.

```sql
CREATE TABLE scenario (
  name       TEXT PRIMARY KEY,
  shape      TEXT NOT NULL DEFAULT 'timed',   -- 'timed' | 'race'
  penalising INTEGER NOT NULL DEFAULT 0,
  budget     REAL,     -- race: 1000
  pool       REAL,     -- race: total damage required
  bots       INTEGER,  -- race: kills to finish
  clock_s    REAL,     -- timed: nominal clock
  evidence   TEXT      -- 'perf-countdown' | 'csv-constant-budget' | 'default'
);

CREATE TABLE kill (
  run_id INTEGER NOT NULL REFERENCES run(id) ON DELETE CASCADE,
  idx    INTEGER NOT NULL,          -- 1-based
  t      REAL NOT NULL,             -- seconds from Challenge Start
  bot TEXT, weapon TEXT, ttk REAL,
  shots INTEGER, hits INTEGER, overshots INTEGER,
  dmg_done REAL, dmg_possible REAL,
  PRIMARY KEY (run_id, idx)
);
```

New columns on `run`: `elapsed_s`, `overshots`, `reloads`, `damage_taken`.

`scenario` is recomputed on bootstrap and whenever a run lands for that name — a fold over
rows the index already holds, so no incremental cost.

## The progress/yield model

Each run reduces to two parallel arrays: `progress` (monotonic, 0→1) and `yield`
(per-bucket increment of the free resource, in score units).

| shape | `progress[i]` | `yield[i]` |
|---|---|---|
| `timed` | `(i+1) / buckets` | `score[i]` |
| `race` | `cum_damage[i] / pool` | `-delta_elapsed[i]` |

For a race, `yield` **is already the series KovaaK's writes**: `[999, -1, -1, ...]` is
`[budget-1, -1, -1, ...]`, so `sum(yield) = budget - elapsed = score`. The countdown was
never junk data — it was the yield series plotted on the wrong axis.

Consequence: **the delta math in `compare.py` does not change.** `cumulative_delta` is
already correct for both shapes; only its index changes. The load-bearing invariant —
final value equals the score difference — survives by construction, because for a race
`-(t_mine - t_base) == score_mine - score_base` falls out of `score = budget - elapsed`.

### Resampling

Only `race` resamples. `timed` keeps its native per-second grid untouched, so the 306
working scenarios carry zero risk.

Grid of `bots x 40` points (200 for 5 bots, 240 for 6, 320 for 8) so kill boundaries land
on exact grid indices. At grid point *p*, cumulative yield is `-(time at which cumulative
damage first reached p x pool)`, linearly interpolated within the straddling bucket.

`compare_until` becomes a progress value rather than a bucket index. For a race it is
always 1.0 — both runs cover the whole pool by definition — so the shaded tail disappears.
For `timed` it still applies where durations differ (~6 % of runs).

### Bucket resolution is not good enough for marks

Validated on real runs: grid edges land 0.06–0.6 s from true kill times, and the delta
invariant lands 0.019 s off, both from the `.perf`'s 1-second buckets.

**Therefore kill rules and splits come from the exact CSV `kill.t` / `kill.ttk`, never
from the interpolated curve.** The resampled curve supplies only the shape of the rate
line.

### Rate chart y-series

- `timed` → `score/s` (unchanged)
- `race` → **damage/s**

Damage/s, not "% on target". Shots are not a clean 100/s throughout — Air Pure Medium
records 8501 shots over 93.85 s — so the two are not interchangeable. Damage/s is the
quantity whose integral over time is the pool and which therefore determines the score.

## API payload

```json
{
  "run":      { "...": "...", "elapsed_s": 93.849, "dead_s": 1.041 },
  "scenario": { "name": "", "shape": "", "penalising": 0, "budget": 0,
                "pool": 0, "bots": 0, "clock_s": 0, "evidence": "" },
  "axis":     { "kind": "progress", "label": "% of pool", "n": 200 },
  "rate":     { "metric": "damage", "unit": "dmg/s",
                "mine": [], "pb": [], "band": { "mean": [], "lo": [], "hi": [] } },
  "delta":    { "unit": "seconds", "values": [], "final": -11.63, "compare_until": 1.0 },
  "marks":    { "kills": [0.2, 0.4, 0.6, 0.8, 1.0], "aligned": true },
  "splits":   [ { "idx": 1, "bot": "AIR1_Short_close", "mine": 15.48,
                  "base": 14.90, "delta": 0.58 } ],
  "baselines": { "...": "unchanged" }
}
```

`marks.aligned` tells the chart whether kill boundaries are shared (race — one set of
rules) or per-run (timed — focused run's marks, baseline's subdued).

This replaces the flat `curve` / `pb_curve` / `cumulative_delta` / `recent_band` keys.
`app.js` is the only consumer, so a clean break is fine.

## Baseline selection

`candidates()` gains a shape branch: the ±10 % `duration_tol` filter is **skipped entirely
for race**, because duration is the score there. Measured harm today: on the slowest Air
Pure Medium run (94.3 s) the tolerance floor is 84.9 s, which excludes the 81.2 s PB — the
overlay vanishes precisely when it is most wanted. On Air CELESTIAL No UFO Easy Slowed the
filter drops 12 of 15 candidates.

`same_cfg` (cm/360) is unchanged and still applies to both shapes.

## Presentation

**Rate chart, race.** x = % of damage pool; kill boundaries as shared vertical rules,
labelled per bot. The stretch between two rules is a bot.

**Delta chart, race.** Same progress axis, cursor synced. Unit is seconds gained/lost,
positive = ahead, which keeps the existing green-above / red-below convention and
`drawDelta()` unchanged. 1 s = 1 point, so the final value can carry both labels.

**Split table** (race only, under the delta chart). Per-bot rows, then `dead time`,
`total elapsed`, `score`. Splits + dead = elapsed = `budget - score`, exactly; if the
table does not reconcile, the code is wrong.

**Penalising.** Zero rule, signed fill, and misses as a *separate panel on its own scale* —
never a second y-axis.

A bucket holding +10 for a kill and -4 for a miss is written to file as `5.6`. **The parts
are not recoverable**, so the line shows net and the misses strip carries penalty pressure
separately. A later refinement could fit `score[i] ~ a*kills[i] + b*misses[i]` per scenario
and decompose only where the fit is tight; that fitted model is deliberately not in v1.

**Headline stats**, shape-aware (`renderHeadline`). `spm` is meaningless for a race, and
`kills` is a constant label rather than a stat:

| | timed | race |
|---|---|---|
| | acc · spm · kills · avg ttk | acc · elapsed · dmg/s · avg per bot |

(`on target` would be redundant for a race: `dmg_done == hits` there, so it is the same
number as `acc`.)

`avg_ttk` is 18.77 s on a race and 0.77 s on a clicking scenario; the race caption reads
*avg per bot*.

**Scenarios view.** `% of PB` is `recent_form / pb`, and the formbar spreads 70–100 % of PB
across its width. Race scores live in a 906–919 band out of 1000, so every race scenario
pins at ~99 % and the bar is dead. For race, express form in *time*: `elapsed_recent /
elapsed_pb`. Air Pure Medium then reads 114 % of PB time instead of 98.8 % of PB score.

**Run rail and session view.** Not broken — score is score and compares fine across shapes.
Add a small shape badge on race rows so the different chart is not a surprise; change
nothing else.

**Metric availability.** The payload carries `metrics`, the list of metric buttons this run
can actually support, and the UI offers only those. `efficiency` is withheld where
`sum(dmg_possible) < 0.5 * sum(shots)` — the signature of a scenario that books damage only
at kill time, where the per-second ratio is a flat zero rather than a measurement. This is
decided per run from the curve, not hardcoded per scenario.

**The unsurfaced counters.** `overshots`, `reloads` and `damage_taken` are recorded on every
run and shown on none. They join the headline grid, but only on runs where they are
non-zero (74 scenarios overshoot, 18 reload, 4 take return fire) — padding every headline
with three zeroes would cost more than it tells.

## Implementation order

1. `statscsv.py` — parse the per-kill table; add `elapsed_s`, `overshots`, `reloads`,
   `damage_taken`.
2. `index.py` — schema 2, `kill` and `scenario` tables, classifier, scenario fold.
3. `compare.py` — progress resampling for race; shape branch in `candidates()`.
4. `server.py` — new payload.
5. `web/app.js` + `style.css` — axis labels, kill rules, signed fill, split table,
   shape-aware headline and scenarios view.
6. `tests/` — see below. `test_kvstats_server.py` asserts on the old payload keys and is
   rewritten in step 4, not left broken.

## Testing

Minimal by intent — only what would let a real defect through silently. Existing tests
stay; these are added.

- **Classifier, 0 FP / 0 FN.** Fixture set of one race run, one timed run, one penalising
  run, plus a race run with no `.perf`. Asserts the tier-1 rule, the tier-2 fallback, and
  that a timed scenario is never classified `race`. This is the foundation: a misfire puts
  a timed scenario on a progress axis.
- **The invariant, extended to race.** The existing property test — cumulative delta's
  final value equals the score difference — must also hold on the progress-resampled path.
- **Splits reconcile.** `sum(kill.ttk) + dead_s == elapsed_s == budget - score` on a race
  fixture. This is the one assertion that catches a per-kill parsing error, a dead-time
  sign error and a budget error at once.

Not tested, deliberately: chart rendering, axis labels, and the resampling grid's exact
interpolation — the first two have no logic worth asserting, and the third is covered
transitively by the invariant test.

## Open risks

- **The countdown detector reads an undocumented format.** It is validated 126/126 here,
  but a KovaaK's update could change the score series. Mitigation: misclassification
  degrades to `timed`, which is the current behaviour, not a crash.
- **Tier 2 needs two runs.** A brand-new race scenario whose first run has no `.perf` will
  chart as `timed` until a second run lands. Visible via `scenario.evidence`.
- **`bots x 40` is a chosen resolution.** 200–320 points is well above the ~60–125 native
  buckets, so the grid never invents detail it cannot support, but the constant is a
  judgement call rather than a measurement.
- **1090 of 2360 runs have no kills.** Every splits/marks path must handle an empty kill
  table as the normal case, not an edge case.
