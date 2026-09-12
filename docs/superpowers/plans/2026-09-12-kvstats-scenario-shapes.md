# kvstats Scenario Shapes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Classify every KovaaK's scenario by its scoring shape and let that shape drive the dashboard's axes, baseline selection and chart marks, so a run is displayed in the terms it was actually scored in.

**Architecture:** A new pure module `kvstats/shapes.py` decides `timed` vs `race` from evidence the index already holds. `index.py` gains a `kill` table and a `scenario` table at schema v2. `compare.py` resamples race curves onto a cumulative-damage progress grid; its delta math is unchanged, only its index changes. `server.py` emits a shape-tagged payload and `app.js` renders whichever axis the payload names.

**Tech Stack:** Python 3 stdlib only (no packages, no build step). `unittest`, run with `python -m unittest discover -s tests`. Frontend is plain ES2020 plus the vendored uPlot already in `kvstats/web/vendor/`.

**Spec:** `docs/superpowers/specs/2026-09-12-kvstats-scenario-shapes-design.md`

## Global Constraints

- **Stdlib only.** No new dependencies, in the package or the tests.
- **Never write to the KovaaK's install.** kvstats reads game files and writes only its own SQLite cache.
- **Schema bumps are drop-and-rebuild.** `index.connect()` already drops every table when `SCHEMA_VERSION` changes. Do not write a migration.
- **`SCHEMA_VERSION` becomes `2`** in this plan (currently `1`, `kvstats/index.py:16`).
- **The invariant:** a cumulative-delta curve's final value must equal the score difference exactly, on both the timed and race paths. This is what keeps the chart and the headline number from disagreeing.
- **Fixtures are committed binary.** `.gitattributes` already has `tests/fixtures/** -text`. Copy real files; never hand-author a fixture.
- **Test style:** `unittest.TestCase`, `sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))` then `from kvstats import ...  # noqa: E402`. Match the existing files.
- **Comments explain why, not what.** The existing modules document the trap each piece of code avoids; keep that register.

## Review checkpoints

Per the requested minimum: stop for review after **Task 3**, **Task 4**, **Task 6** and **Task 7** only. Tasks 1, 2 and 5 run straight through to the next task.

---

### Task 1: Per-kill rows and the new summary fields

**Files:**
- Modify: `kvstats/statscsv.py` (add `parse_kills`, extend `parse`)
- Test: `tests/test_kvstats_statscsv.py`
- Add fixtures: `tests/fixtures/kvstats/stats/` and `tests/fixtures/kvstats/performances/`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `statscsv.parse_kills(path) -> list[dict]`, each `{"idx": int, "t": float, "bot": str, "weapon": str, "ttk": float, "shots": int, "hits": int, "overshots": int, "dmg_done": float, "dmg_possible": float}`. `t` is seconds from `Challenge Start`.
  - `statscsv.parse(path)` gains keys `elapsed_s` (float|None), `overshots` (int|None), `reloads` (int|None), `damage_taken` (float|None).

- [ ] **Step 1: Copy the fixtures**

These are real files from the reference install. Copy them verbatim — do not edit them.

```bash
SRC="/c/Program Files (x86)/Steam/steamapps/common/FPSAimTrainer/FPSAimTrainer"
DST=tests/fixtures/kvstats

# race, tier 1 (has .perf). The slower of the pair -- used as the focus run.
cp "$SRC/stats/Air Pure Medium - Challenge - 2026.09.03-19.08.37 Stats.csv" "$DST/stats/"
cp "$SRC/performances/Air Pure Medium - Challenge - 2026.09.03-19.08.37 Performance.perf" "$DST/performances/"

# race, tier 1. The faster of the pair -- becomes the PB baseline.
cp "$SRC/stats/Air Pure Medium - Challenge - 2026.09.12-16.04.49 Stats.csv" "$DST/stats/"
cp "$SRC/performances/Air Pure Medium - Challenge - 2026.09.12-16.04.49 Performance.perf" "$DST/performances/"

# race, tier 2: CSV only, NO .perf copied, so tier 1 cannot fire for this scenario.
cp "$SRC/stats/Air Spectral Easy - Challenge - 2026.09.11-18.37.39 Stats.csv" "$DST/stats/"
cp "$SRC/stats/Air Spectral Easy - Challenge - 2026.09.05-08.20.43 Stats.csv" "$DST/stats/"

# penalising: 3 negative score buckets out of 60.
cp "$SRC/stats/VT 1w2ts Horizontal Small - Challenge - 2026.06.22-18.48.41 Stats.csv" "$DST/stats/"
cp "$SRC/performances/VT 1w2ts Horizontal Small - Challenge - 2026.06.22-18.48.41 Performance.perf" "$DST/performances/"
```

Adds ~28 KB. If the install is not present, the task cannot proceed — stop and report rather than fabricating a fixture.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_kvstats_statscsv.py`:

```python
class PerKillRows(unittest.TestCase):
    """The per-kill table was discarded in v1. It is the only exact source of
    kill times -- the .perf buckets to whole seconds, which lands kill marks up
    to 0.6 s off."""

    def test_kill_rows_carry_exact_times_and_reconcile_to_fight_time(self):
        kills = statscsv.parse_kills(fixture("Air Pure Medium - Challenge - 2026.09.03"))
        self.assertEqual(len(kills), 5)
        self.assertEqual([k["idx"] for k in kills], [1, 2, 3, 4, 5])
        self.assertEqual([k["bot"] for k in kills],
                         ["AIR1_Short_close", "AIR1_Short_far", "AIR2_Long3D_mid",
                          "AIR2_Short_close", "AIR2_Mid_UFO"])
        self.assertEqual([k["overshots"] for k in kills], [25, 25, 25, 25, 0])
        # sum(TTK) is Fight Time exactly -- the respawn gaps sit outside it
        self.assertAlmostEqual(sum(k["ttk"] for k in kills), 92.808, places=2)
        # t is seconds from Challenge Start, and the last kill ends the run
        self.assertAlmostEqual(kills[0]["t"], 15.735, places=3)
        self.assertAlmostEqual(kills[-1]["t"], 93.849, places=3)

    def test_a_pure_tracking_run_has_no_kill_rows(self):
        """1090 of 2360 real runs are invincible-tracking and never kill
        anything. An empty kill table is the normal case, not an edge case."""
        kills = statscsv.parse_kills(fixture("Air Voltaic Invincible 4 Medium"))
        self.assertEqual(kills, [])

    def test_summary_gains_elapsed_and_the_unsurfaced_counters(self):
        row = statscsv.parse(fixture("Air Pure Medium - Challenge - 2026.09.03"))
        self.assertAlmostEqual(row["elapsed_s"], 93.849, places=3)
        self.assertEqual(row["overshots"], 100)
        self.assertEqual(row["reloads"], 0)
        self.assertEqual(row["damage_taken"], 0.0)
        # the identity the whole race shape rests on
        self.assertAlmostEqual(row["score"] + row["elapsed_s"], 1000.0, places=1)
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python -m unittest tests.test_kvstats_statscsv -v`
Expected: FAIL with `AttributeError: module 'kvstats.statscsv' has no attribute 'parse_kills'`

- [ ] **Step 4: Implement**

In `kvstats/statscsv.py`, add to the `_INTS` map:

```python
_INTS = {
    "Kills": "kills",
    "Hit Count": "hits",
    "Miss Count": "misses",
    "Pause Count": "pause_count",
    "DPI": "dpi",
    "Total Overshots": "overshots",
    "Reloads": "reloads",
}
```

and to `_FLOATS`:

```python
    "Damage Taken": "damage_taken",
```

Add near the top, after `FILENAME`:

```python
# Kill rows are the leading block of the file: a header line, then one line per
# kill. They are positional, not keyed, so the column order below is the
# contract. Verified against a real install: 124 distinct bot names, 16 weapons.
_KILL_COLUMNS = 13
_CLOCK = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2}(?:\.\d+)?)$")


def _clock_seconds(text):
    match = _CLOCK.match(text.strip())
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _seconds(text):
    return _number(text.strip().rstrip("s"), float)
```

Then add `parse_kills`:

```python
def parse_kills(path):
    """The per-kill block, with `t` as seconds from Challenge Start.

    Returns [] for the ~46% of runs whose bots are invincible and never die.
    The clock in these rows is wall time with no date, so it is rebased onto
    Challenge Start; a run that crosses midnight would otherwise go negative.
    """
    rows, start = [], None
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            key, sep, value = line.partition(",")
            if sep and key == "Challenge Start:":
                start = _clock_seconds(value)
                continue
            if sep and key.endswith(":"):
                continue
            fields = line.rstrip("\n").split(",")
            if len(fields) < _KILL_COLUMNS or not fields[0].isdigit():
                continue
            rows.append(fields)

    if start is None:
        return []

    kills = []
    for fields in rows:
        at = _clock_seconds(fields[1])
        if at is None:
            continue
        offset = at - start
        if offset < 0:
            offset += 86400  # the run crossed midnight
        kills.append({
            "idx": int(fields[0]),
            "t": offset,
            "bot": fields[2],
            "weapon": fields[3],
            "ttk": _seconds(fields[4]),
            "shots": _number(fields[5], int),
            "hits": _number(fields[6], int),
            "dmg_done": _number(fields[8], float),
            "dmg_possible": _number(fields[9], float),
            "overshots": _number(fields[12], int),
        })
    kills.sort(key=lambda k: k["idx"])
    return kills
```

In `parse()`, after the `accuracy` block and before `damage_possible`, add:

```python
    # Elapsed is the CSV's own answer, not the .perf's: it is exact, it works on
    # the ~1-in-7 runs with no .perf, and for a race scenario it IS the score.
    kills = parse_kills(path)
    row["elapsed_s"] = kills[-1]["t"] if kills else None
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m unittest tests.test_kvstats_statscsv -v`
Expected: PASS, all tests

- [ ] **Step 6: Run the whole suite**

Run: `python -m unittest discover -s tests`
Expected: PASS. New fixtures now flow through `index.bootstrap` in other tests; if anything there fails, it is a real signal, not fixture noise — fix it before committing.

- [ ] **Step 7: Commit**

```bash
git add kvstats/statscsv.py tests/test_kvstats_statscsv.py tests/fixtures/kvstats
git commit -m "Parse the per-kill table and the unsurfaced summary counters"
```

---

### Task 2: Shape classification

**Files:**
- Create: `kvstats/shapes.py`
- Test: `tests/test_kvstats_shapes.py`

**Interfaces:**
- Consumes: nothing. Deliberately pure — no DB, no file IO — so it is testable from literals.
- Produces:
  - `shapes.RACE == "race"`, `shapes.TIMED == "timed"`
  - `shapes.countdown_budget(score_series) -> float|None`
  - `shapes.budget_from_totals(pairs) -> float|None` where `pairs` is an iterable of `(score, elapsed)`
  - `shapes.is_penalising(score_series) -> bool`
  - `shapes.classify(curves, totals) -> {"shape": str, "budget": float|None, "evidence": str}` with `evidence` in `{"perf-countdown", "csv-constant-budget", "default"}`

- [ ] **Step 1: Write the failing test**

Create `tests/test_kvstats_shapes.py`:

```python
"""Scenario shape classification.

A misfire here is the worst failure in this feature: it puts a fixed-clock
scenario on a progress axis, or leaves a race scenario charting a countdown
timer as a rate. The thresholds below are calibrated against 2050 real .perf
files, where this rule scores 126/126 with zero false positives.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from kvstats import shapes  # noqa: E402

# Air Pure Medium: the score series IS a clock.
COUNTDOWN = [999.0] + [-1.0] * 90 + [-0.4]
# VT Ground Intermediate S5: score accrues per second.
TIMED_CURVE = [62.0, 46.0, 55.0, 40.0, 45.0, 57.0, 54.0, 27.0, 22.0, 93.0]
# VT 1w2ts Horizontal Small: +10 a kill, about -4 a miss.
PENALISING_CURVE = [10.0, 10.0, 20.0, 5.6, 9.9, 19.9, -4.0, 6.2, 19.7]


class Countdown(unittest.TestCase):
    def test_a_clock_yields_its_budget(self):
        self.assertAlmostEqual(shapes.countdown_budget(COUNTDOWN), 1000.0, places=6)

    def test_bucket_collisions_do_not_break_detection(self):
        """perf.py buckets by floor(timestamp), so jitter occasionally writes a
        0 next to a -2. A strict all-buckets rule misses 3 of 126 real runs;
        the >=90% rule misses none. This is the reason the threshold exists."""
        jittered = list(COUNTDOWN)
        jittered[67], jittered[68] = 0.0, -1.998
        self.assertAlmostEqual(shapes.countdown_budget(jittered), 1000.0, places=6)

    def test_float32_drift_is_tolerated(self):
        """The series round-trips through float32, so -1 arrives as -1.00092."""
        drifted = [999.0] + [-1.00092, -0.99908] * 45 + [-0.29]
        self.assertAlmostEqual(shapes.countdown_budget(drifted), 1000.0, places=6)

    def test_a_scoring_curve_is_not_a_clock(self):
        self.assertIsNone(shapes.countdown_budget(TIMED_CURVE))
        self.assertIsNone(shapes.countdown_budget(PENALISING_CURVE))

    def test_degenerate_input_is_rejected_rather_than_guessed(self):
        self.assertIsNone(shapes.countdown_budget([]))
        self.assertIsNone(shapes.countdown_budget([999.0]))
        self.assertIsNone(shapes.countdown_budget([-1.0, -1.0, -1.0, -1.0]))


class CsvFallback(unittest.TestCase):
    def test_constant_budget_over_varying_time_is_a_race(self):
        """Real Air Spectral Easy runs, whose .perf files are absent."""
        got = shapes.budget_from_totals([(927.416626, 72.567), (914.885254, 85.093)])
        self.assertAlmostEqual(got, 999.98, places=1)

    def test_one_run_is_never_enough(self):
        """6 of 2360 fixed-clock runs land on a round hundred by coincidence.
        A single run is a coincidence; two runs agreeing is evidence."""
        self.assertIsNone(shapes.budget_from_totals([(940.0, 60.0)]))

    def test_a_fixed_clock_is_not_a_race(self):
        """Score varies, elapsed does not -- so the budget is not constant."""
        self.assertIsNone(shapes.budget_from_totals([(840.67, 59.40), (727.25, 59.43)]))


class Classify(unittest.TestCase):
    def test_a_countdown_curve_wins_over_the_csv_fallback(self):
        got = shapes.classify([TIMED_CURVE, COUNTDOWN], [(906.1, 93.8)])
        self.assertEqual(got["shape"], shapes.RACE)
        self.assertEqual(got["evidence"], "perf-countdown")
        self.assertAlmostEqual(got["budget"], 1000.0, places=6)

    def test_the_fallback_runs_only_when_no_curve_settles_it(self):
        got = shapes.classify([], [(927.416626, 72.567), (914.885254, 85.093)])
        self.assertEqual(got["shape"], shapes.RACE)
        self.assertEqual(got["evidence"], "csv-constant-budget")

    def test_a_fixed_clock_scenario_is_never_classified_race(self):
        got = shapes.classify([TIMED_CURVE], [(2977.0, 59.99), (2570.0, 59.99)])
        self.assertEqual(got["shape"], shapes.TIMED)
        self.assertEqual(got["evidence"], "default")
        self.assertIsNone(got["budget"])


class Penalising(unittest.TestCase):
    def test_a_negative_bucket_is_a_penalty(self):
        self.assertTrue(shapes.is_penalising(PENALISING_CURVE))
        self.assertFalse(shapes.is_penalising(TIMED_CURVE))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_kvstats_shapes -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kvstats.shapes'`

- [ ] **Step 3: Implement**

Create `kvstats/shapes.py`:

```python
"""Scenario scoring shapes.

Most scenarios are scored on a fixed clock. Ten in the reference install are
not: they spawn a fixed number of bots and score on elapsed time, writing the
score series as a literal countdown. Telling the two apart is what lets the
dashboard pick an axis that means something.

Everything here is pure over already-parsed values -- no database, no file IO.
The index calls it with rows it already holds; the tests call it with literals.
"""

RACE = "race"
TIMED = "timed"

# >=90%, not 100%: perf.py buckets samples by floor(timestamp), so timing jitter
# occasionally merges two ticks into one bucket (a 0 beside a -2). A strict rule
# scores 123/126 on real runs; this one scores 126/126 with no false positives.
COUNTDOWN_MIN_FRACTION = 0.90
# The series round-trips through float32, which moves -1 by up to ~0.0025.
COUNTDOWN_TOLERANCE = 0.01

BUDGET_TOLERANCE = 0.1
MIN_DURATION_SPREAD = 1.0


def countdown_budget(score_series):
    """The budget if `score_series` is a countdown clock, else None.

    A race scenario's score arrives as [budget-1, -1, -1, ...]: the first
    bucket seeds the clock and every later one is a second ticking off. The
    final bucket is a partial second, so it is excluded rather than tested.
    """
    values = list(score_series)
    if len(values) < 4 or values[0] <= 0:
        return None
    body = values[1:-1]
    if not body:
        return None
    ticks = sum(1 for v in body if abs(v + 1.0) < COUNTDOWN_TOLERANCE)
    if ticks / len(body) < COUNTDOWN_MIN_FRACTION:
        return None
    return values[0] + 1.0


def budget_from_totals(pairs):
    """The budget if (score, elapsed) pairs show a constant budget over
    varying time, else None.

    The fallback for race scenarios with no .perf. Two runs minimum, and that
    minimum is the whole point: 6 of 2360 fixed-clock runs have a score that
    lands near a round hundred minus their clock, so one run is a coincidence.
    Two runs agreeing on a budget while disagreeing on time is not.
    """
    usable = [(s, e) for s, e in pairs if s is not None and e]
    if len(usable) < 2:
        return None
    budgets = [score + elapsed for score, elapsed in usable]
    elapsed = [e for _, e in usable]
    if max(budgets) - min(budgets) > BUDGET_TOLERANCE:
        return None
    if max(elapsed) - min(elapsed) < MIN_DURATION_SPREAD:
        return None
    return sum(budgets) / len(budgets)


def is_penalising(score_series):
    """Whether any second of the run cost points outright."""
    return any(v < 0 for v in score_series)


def classify(curves, totals):
    """-> {"shape", "budget", "evidence"}.

    `curves` is every score series available for the scenario, `totals` every
    (score, elapsed) pair. Curve evidence wins outright: it settles a scenario
    from a single run, where the totals test needs two.
    """
    for series in curves:
        budget = countdown_budget(series)
        if budget is not None:
            return {"shape": RACE, "budget": budget, "evidence": "perf-countdown"}

    budget = budget_from_totals(totals)
    if budget is not None:
        return {"shape": RACE, "budget": budget, "evidence": "csv-constant-budget"}

    return {"shape": TIMED, "budget": None, "evidence": "default"}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m unittest tests.test_kvstats_shapes -v`
Expected: PASS, 11 tests

- [ ] **Step 5: Commit**

```bash
git add kvstats/shapes.py tests/test_kvstats_shapes.py
git commit -m "Add scenario shape classification"
```

---

### Task 3: Schema v2 — kill and scenario tables — **REVIEW AFTER THIS TASK**

**Files:**
- Modify: `kvstats/index.py` (`SCHEMA_VERSION`, `SCHEMA`, `_RUN_COLUMNS`, `index_stats_file`, `attach_perf`, `bootstrap`)
- Test: `tests/test_kvstats_index.py`

**Interfaces:**
- Consumes: `statscsv.parse_kills`, `statscsv.parse` (Task 1); all of `shapes` (Task 2).
- Produces:
  - `index.store_kills(conn, run_id, kills, commit=True) -> None`
  - `index.load_kills(conn, run_id) -> list[sqlite3.Row]` ordered by `idx`
  - `index.refresh_scenario(conn, name, commit=True) -> dict|None` with keys `shape`, `penalising`, `budget`, `pool`, `bots`, `clock_s`, `evidence`
  - `index.scenario_row(conn, name) -> sqlite3.Row|None`
  - `run` table gains columns `elapsed_s`, `overshots`, `reloads`, `damage_taken`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_kvstats_index.py`:

```python
class ScenarioShapes(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        shutil.copytree(os.path.join(FIXTURES, "stats"), os.path.join(self.dir, "stats"))
        shutil.copytree(os.path.join(FIXTURES, "performances"),
                        os.path.join(self.dir, "performances"))
        self.cfg = paths.load({"KOVAAKS_DIR": self.dir,
                               "KVSTATS_DB": os.path.join(self.dir, "i.sqlite3")})
        self.conn = index.connect(self.cfg.db_path)
        self.addCleanup(self.conn.close)
        index.bootstrap(self.conn, self.cfg)

    def test_a_countdown_scenario_is_classified_race_with_its_pool_and_bots(self):
        row = index.scenario_row(self.conn, "Air Pure Medium")
        self.assertEqual(row["shape"], "race")
        self.assertEqual(row["evidence"], "perf-countdown")
        self.assertAlmostEqual(row["budget"], 1000.0, places=6)
        self.assertAlmostEqual(row["pool"], 5000.0, places=1)
        self.assertEqual(row["bots"], 5)

    def test_a_curveless_race_scenario_falls_back_to_the_csv_test(self):
        """Air Spectral Easy is fixtured with no .perf at all, so tier 1
        cannot fire and the two-run budget test has to carry it."""
        row = index.scenario_row(self.conn, "Air Spectral Easy")
        self.assertEqual(row["shape"], "race")
        self.assertEqual(row["evidence"], "csv-constant-budget")
        self.assertEqual(row["bots"], 6)

    def test_a_fixed_clock_scenario_stays_timed(self):
        row = index.scenario_row(self.conn, "Air Voltaic Invincible 4 Medium")
        self.assertEqual(row["shape"], "timed")
        self.assertEqual(row["evidence"], "default")
        self.assertIsNone(row["budget"])
        self.assertAlmostEqual(row["clock_s"], 60.0, places=0)

    def test_negative_buckets_flag_a_penalising_scenario(self):
        self.assertEqual(index.scenario_row(self.conn, "VT 1w2ts Horizontal Small")["penalising"], 1)
        self.assertEqual(index.scenario_row(self.conn, "Air Voltaic Invincible 4 Medium")["penalising"], 0)

    def test_kill_rows_are_stored_and_reconcile_to_the_score(self):
        """The one assertion that catches a per-kill parse error, a dead-time
        sign error and a budget error at once."""
        run = self.conn.execute(
            "SELECT * FROM run WHERE scenario='Air Pure Medium' "
            "ORDER BY started_at LIMIT 1").fetchone()
        kills = index.load_kills(self.conn, run["id"])
        self.assertEqual(len(kills), 5)
        self.assertEqual(kills[0]["bot"], "AIR1_Short_close")

        splits = sum(k["ttk"] for k in kills)
        dead = run["elapsed_s"] - splits
        budget = index.scenario_row(self.conn, "Air Pure Medium")["budget"]
        self.assertAlmostEqual(splits, 92.808, places=2)
        self.assertAlmostEqual(dead, 1.041, places=2)
        self.assertAlmostEqual(splits + dead, run["elapsed_s"], places=6)
        self.assertAlmostEqual(run["elapsed_s"], budget - run["score"], places=1)

    def test_a_tracking_run_stores_no_kills(self):
        run = self.conn.execute(
            "SELECT id FROM run WHERE scenario='Air Voltaic Invincible 4 Medium'").fetchone()
        self.assertEqual(index.load_kills(self.conn, run["id"]), [])
```

`tests/test_kvstats_index.py` already imports `os`, `shutil`, `sys`, `tempfile`, `unittest` and `from kvstats import index, paths` and defines `FIXTURES`. Reuse them; do not re-import.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_kvstats_index -v`
Expected: FAIL with `AttributeError: module 'kvstats.index' has no attribute 'scenario_row'`

- [ ] **Step 3: Bump the schema and add the tables**

In `kvstats/index.py`, set `SCHEMA_VERSION = 2` and add to the `SCHEMA` string, after the `curve` table:

```sql
CREATE TABLE kill (
  run_id INTEGER NOT NULL REFERENCES run(id) ON DELETE CASCADE,
  idx    INTEGER NOT NULL,
  t      REAL NOT NULL,
  bot TEXT, weapon TEXT, ttk REAL,
  shots INTEGER, hits INTEGER, overshots INTEGER,
  dmg_done REAL, dmg_possible REAL,
  PRIMARY KEY (run_id, idx)
);

CREATE TABLE scenario (
  name       TEXT PRIMARY KEY,
  shape      TEXT NOT NULL DEFAULT 'timed',
  penalising INTEGER NOT NULL DEFAULT 0,
  budget     REAL, pool REAL, bots INTEGER, clock_s REAL,
  evidence   TEXT
);
```

Add the four new run columns to the `run` table in `SCHEMA`, on the line after `duration_s REAL, spm REAL,`:

```sql
  elapsed_s REAL, overshots INTEGER, reloads INTEGER, damage_taken REAL,
```

Extend `_RUN_COLUMNS` — append `"elapsed_s", "overshots", "reloads", "damage_taken"` after `"spm"`.

Add the import at the top: `from . import shapes`, and `import statistics`.

- [ ] **Step 4: Persist kills and refresh the scenario**

In `index_stats_file`, replace the line `row["duration_s"] = None` block so that `elapsed_s` survives from the parse (it is already in `row`), then after the insert, store the kills. Replace the tail of `index_stats_file` with:

```python
    if cursor.lastrowid and cursor.rowcount:
        run_id = cursor.lastrowid
    else:
        existing = conn.execute(
            "SELECT id FROM run WHERE stats_file=?", (path,)).fetchone()
        run_id = existing[0] if existing else None

    if run_id is not None:
        store_kills(conn, run_id, statscsv.parse_kills(path), commit=False)
    if commit:
        conn.commit()
    return run_id
```

and delete the old `if commit: conn.commit()` that preceded it, so there is exactly one commit.

Add these three functions after `load_curve`:

```python
def store_kills(conn, run_id, kills, commit=True):
    """Replace this run's kill rows. A no-op for the ~46% of runs whose bots
    are invincible and never die."""
    conn.execute("DELETE FROM kill WHERE run_id=?", (run_id,))
    if kills:
        conn.executemany(
            "INSERT INTO kill(run_id, idx, t, bot, weapon, ttk, shots, hits, "
            "overshots, dmg_done, dmg_possible) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [(run_id, k["idx"], k["t"], k["bot"], k["weapon"], k["ttk"], k["shots"],
              k["hits"], k["overshots"], k["dmg_done"], k["dmg_possible"])
             for k in kills])
    if commit:
        conn.commit()


def load_kills(conn, run_id):
    return conn.execute(
        "SELECT * FROM kill WHERE run_id=? ORDER BY idx", (run_id,)).fetchall()


def scenario_row(conn, name):
    return conn.execute("SELECT * FROM scenario WHERE name=?", (name,)).fetchone()


def refresh_scenario(conn, name, commit=True):
    """Recompute one scenario's shape from every run the index holds for it.

    A fold over rows already present, so it is cheap enough to run on every
    new run rather than only at bootstrap -- which matters, because a second
    run is exactly what promotes a curveless race scenario out of 'timed'.
    """
    runs = conn.execute(
        "SELECT id, score, elapsed_s, duration_s, kills, hits, perf_file "
        "FROM run WHERE scenario=?", (name,)).fetchall()
    if not runs:
        return None

    curves = []
    for run in runs:
        if run["perf_file"] is None:
            continue
        curve = load_curve(conn, run["id"])
        if curve:
            curves.append(list(curve["score"]))

    verdict = shapes.classify(
        curves, [(r["score"], r["elapsed_s"]) for r in runs])

    def median(values):
        usable = [v for v in values if v is not None]
        return statistics.median(usable) if usable else None

    pool = bots = clock_s = None
    penalising = 0
    if verdict["shape"] == shapes.RACE:
        # Hits are the damage pool: every hit is one damage in these scenarios,
        # and the total is identical in every run of the same scenario.
        pool = median([r["hits"] for r in runs])
        bot_count = median([r["kills"] for r in runs])
        bots = int(bot_count) if bot_count else None
    else:
        clock_s = median([r["duration_s"] for r in runs])
        # A countdown is negative every bucket by construction; that is the
        # clock, not a penalty, so this is only asked of timed scenarios.
        penalising = int(any(shapes.is_penalising(c) for c in curves))

    conn.execute(
        "INSERT OR REPLACE INTO scenario"
        "(name, shape, penalising, budget, pool, bots, clock_s, evidence) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (name, verdict["shape"], penalising, verdict["budget"],
         pool, bots, clock_s, verdict["evidence"]))
    if commit:
        conn.commit()
    return {"shape": verdict["shape"], "penalising": penalising,
            "budget": verdict["budget"], "pool": pool, "bots": bots,
            "clock_s": clock_s, "evidence": verdict["evidence"]}
```

- [ ] **Step 5: Refresh scenarios at the end of bootstrap**

In `bootstrap`, immediately before the final `return counts`, add:

```python
    # Classification is a fold over indexed rows, so it runs last -- after every
    # curve is attached. Doing it per-file would classify a race scenario from
    # its first run alone, before the evidence that settles it has landed.
    for (name,) in conn.execute("SELECT DISTINCT scenario FROM run").fetchall():
        refresh_scenario(conn, name, commit=False)
    conn.commit()
```

- [ ] **Step 6: Keep the live path correct**

Two places in `kvstats/watch.py` change a scenario's evidence, and both must refresh it — indexing a run adds a `(score, elapsed)` pair for tier 2, and attaching its `.perf` adds the curve that tier 1 needs. A live run that only refreshed on one of them would sit on the wrong shape until the next restart.

Add this helper method to `Watcher`, after `__init__`:

```python
    def _refresh_scenario_for(self, run_id):
        """Re-classify the scenario this run belongs to.

        Cheap -- a fold over rows already indexed -- and it has to happen live:
        a second run is exactly what promotes a curveless race scenario out of
        'timed', and a first .perf is what promotes it via the stronger tier.
        """
        row = self.conn.execute(
            "SELECT scenario FROM run WHERE id=?", (run_id,)).fetchone()
        if row:
            index.refresh_scenario(self.conn, row[0])
```

In `_scan_stats`, immediately after `new_ids.append(run_id)`:

```python
            self._refresh_scenario_for(run_id)
```

In `_attach_ready_perfs`, immediately after `attached.append(run_id)` and before its `continue`:

```python
                    self._refresh_scenario_for(run_id)
```

- [ ] **Step 7: Run the tests**

Run: `python -m unittest tests.test_kvstats_index tests.test_kvstats_watch -v`
Expected: PASS

- [ ] **Step 8: Run the whole suite**

Run: `python -m unittest discover -s tests`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add kvstats/index.py kvstats/watch.py tests/test_kvstats_index.py
git commit -m "Add schema v2 with kill and scenario tables"
```

> **REVIEW CHECKPOINT.** Stop here. This task changes the schema, the bootstrap path and the live path at once.

---

### Task 4: Progress resampling and shape-aware baselines — **REVIEW AFTER THIS TASK**

**Files:**
- Modify: `kvstats/compare.py` (`candidates`, plus new resampling functions)
- Test: `tests/test_kvstats_compare.py`

**Interfaces:**
- Consumes: `shapes.RACE` (Task 2); `index.load_curve`, `index.scenario_row` (Task 3).
- Produces:
  - `compare.RACE_STEPS_PER_BOT == 40`
  - `compare.race_grid(bots) -> int`
  - `compare.resample_race(hits, elapsed_s, steps) -> (edges, rate)` — `edges` has `steps + 1` entries in seconds, `rate` has `steps` entries in damage/second
  - `compare.race_delta(mine_edges, base_edges) -> list[float]` — seconds gained (+) or lost (−) at each progress point
  - `compare.candidates(...)` gains keyword `shape=shapes.TIMED`
  - `compare.baselines(...)` gains keyword `shape=shapes.TIMED`, forwarded to `candidates`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_kvstats_compare.py`:

```python
class RaceResampling(unittest.TestCase):
    """A race scenario is a fixed amount of work against a free clock, so the
    meaningful axis is share of that work, not wall-clock seconds."""

    def test_the_grid_puts_kill_boundaries_on_exact_indices(self):
        self.assertEqual(compare.race_grid(5), 200)
        self.assertEqual(compare.race_grid(6), 240)
        self.assertEqual(compare.race_grid(8), 320)
        for bots in (5, 6, 8):
            steps = compare.race_grid(bots)
            self.assertEqual(steps % bots, 0, "a boundary would fall between cells")

    def test_a_flat_run_resamples_to_a_flat_rate_and_a_linear_clock(self):
        hits = [50.0] * 100                    # 5000 damage over 100 seconds
        edges, rate = compare.resample_race(hits, 100.0, 200)
        self.assertEqual(len(edges), 201)
        self.assertEqual(len(rate), 200)
        self.assertAlmostEqual(edges[0], 0.0, places=6)
        self.assertAlmostEqual(edges[-1], 100.0, places=6)
        self.assertAlmostEqual(edges[100], 50.0, places=3)
        for value in rate:
            self.assertAlmostEqual(value, 50.0, places=3)

    def test_the_last_edge_is_always_the_true_elapsed_time(self):
        """The .perf buckets to whole seconds, so the curve's own length is a
        rounded-up approximation. Anchoring the last edge to the CSV's elapsed
        is what keeps the delta equal to the score difference."""
        hits = [40.0] * 90 + [10.0]
        edges, _ = compare.resample_race(hits, 85.994, 200)
        self.assertAlmostEqual(edges[-1], 85.994, places=6)

    def test_a_slow_stretch_shows_up_as_a_low_rate(self):
        hits = [100.0] * 40 + [10.0] * 100 + [100.0] * 40   # stall in the middle
        _, rate = compare.resample_race(hits, 180.0, 200)
        self.assertGreater(rate[5], rate[100], "the stall must read as slower")

    def test_degenerate_input_returns_empty_rather_than_dividing_by_zero(self):
        self.assertEqual(compare.resample_race([], 60.0, 200), ([], []))
        self.assertEqual(compare.resample_race([0.0, 0.0], 60.0, 200), ([], []))
        self.assertEqual(compare.resample_race([1.0], 0.0, 200), ([], []))


class RaceInvariant(unittest.TestCase):
    def test_the_delta_ends_at_exactly_the_score_difference(self):
        """The invariant the whole dashboard rests on, on the race path.

        For a race, score = budget - elapsed, so the seconds one run gains on
        another IS its score advantage. If this drifts, the chart's endpoint
        and the headline number disagree.
        """
        mine_hits, base_hits = [50.0] * 100, [55.0] * 91
        mine_elapsed, base_elapsed = 93.849, 85.994
        steps = compare.race_grid(5)
        mine_edges, _ = compare.resample_race(mine_hits, mine_elapsed, steps)
        base_edges, _ = compare.resample_race(base_hits, base_elapsed, steps)

        delta = compare.race_delta(mine_edges, base_edges)
        self.assertEqual(len(delta), steps)

        budget = 1000.0
        score_difference = (budget - mine_elapsed) - (budget - base_elapsed)
        self.assertAlmostEqual(delta[-1], score_difference, places=6)
        self.assertAlmostEqual(delta[0], 0.0, places=1, msg="both runs start level")


class RaceBaselines(unittest.TestCase):
    def test_the_duration_filter_is_skipped_for_race_scenarios(self):
        """Duration IS the score here. On the slowest real Air Pure Medium run
        the +-10% floor is 84.9 s, which excludes the 81.2 s PB -- the overlay
        disappears exactly when it is most wanted."""
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        shutil.copytree(os.path.join(FIXTURES, "stats"),
                        os.path.join(directory, "stats"))
        shutil.copytree(os.path.join(FIXTURES, "performances"),
                        os.path.join(directory, "performances"))
        cfg = paths.load({"KOVAAKS_DIR": directory,
                          "KVSTATS_DB": os.path.join(directory, "i.sqlite3")})
        conn = index.connect(cfg.db_path)
        self.addCleanup(conn.close)
        index.bootstrap(conn, cfg)

        slow = conn.execute(
            "SELECT id FROM run WHERE scenario='Air Pure Medium' "
            "ORDER BY score ASC LIMIT 1").fetchone()[0]

        # 93.85 s vs 85.99 s is a 8.4% gap -- inside +-10%, so force the issue
        # by tightening the tolerance to something the pair cannot satisfy.
        timed = compare.candidates(conn, slow, duration_tol=0.01)
        raced = compare.candidates(conn, slow, duration_tol=0.01, shape="race")
        self.assertEqual(len(timed), 0, "the filter should bite on the timed path")
        self.assertEqual(len(raced), 1, "and never bite on the race path")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_kvstats_compare -v`
Expected: FAIL with `AttributeError: module 'kvstats.compare' has no attribute 'race_grid'`

- [ ] **Step 3: Implement the resampling**

In `kvstats/compare.py`, add `from . import shapes` to the imports and append:

```python
# 40 cells per bot: 200-320 points against the ~60-125 native one-second
# buckets, so the grid never invents detail the source cannot support, and
# every kill boundary lands on an exact index rather than between two.
RACE_STEPS_PER_BOT = 40


def race_grid(bots):
    return max(1, int(bots or 1)) * RACE_STEPS_PER_BOT


def resample_race(hits, elapsed_s, steps):
    """(edges, rate) on a uniform cumulative-damage grid.

    `edges[k]` is the time at which the run had done `k/steps` of the damage
    pool; `rate[k]` is the damage per second within cell k. Indexing by damage
    rather than by seconds is what makes two runs comparable: kill k always
    sits at damage `k * pool/bots`, so the boundaries coincide in every run.
    """
    cumulative, total = [], 0.0
    for value in hits:
        total += value
        cumulative.append(total)
    if not cumulative or total <= 0 or not elapsed_s:
        return [], []

    def time_at(target):
        previous = 0.0
        for i, reached in enumerate(cumulative):
            if reached >= target:
                span = reached - previous
                # bucket i covers [i, i+1); interpolate inside it
                return i + ((target - previous) / span if span > 0 else 0.0)
            previous = reached
        return float(len(cumulative))

    edges = [time_at(total * k / steps) for k in range(steps + 1)]
    # Anchor to the CSV's elapsed. The curve is bucketed to whole seconds, so
    # its own last edge is a rounded approximation -- and for a race that error
    # would land straight in the score difference.
    span = edges[-1]
    if span > 0:
        edges = [e / span * elapsed_s for e in edges]

    cell = total / steps
    rate = [cell / max(edges[k + 1] - edges[k], 1e-6) for k in range(steps)]
    return edges, rate


def race_delta(mine_edges, base_edges):
    """Seconds gained (+) or lost (-) against the baseline, by progress.

    The final value is `base_elapsed - mine_elapsed`, which for a race IS the
    score difference, because score = budget - elapsed. That is the same
    invariant `cumulative_delta` carries on the timed path.
    """
    n = min(len(mine_edges), len(base_edges))
    return [base_edges[i] - mine_edges[i] for i in range(1, n)]
```

- [ ] **Step 4: Make baseline selection shape-aware**

Change the `candidates` signature and its duration filter:

```python
def candidates(conn, run_id, same_cfg=True, duration_tol=DURATION_TOLERANCE,
               shape=shapes.TIMED):
```

and replace `if target:` with:

```python
    # Duration is the score on a race scenario, so filtering baselines by it
    # throws away the comparison. On the slowest real Air Pure Medium run the
    # +-10% floor is 84.9 s, which excludes its own 81.2 s PB.
    if target and shape != shapes.RACE:
```

Change `baselines` the same way — add `shape=shapes.TIMED` to its signature and forward it:

```python
    rows = candidates(conn, run_id, same_cfg=same_cfg, duration_tol=duration_tol,
                      shape=shape)
```

- [ ] **Step 5: Run the tests**

Run: `python -m unittest tests.test_kvstats_compare -v`
Expected: PASS

- [ ] **Step 6: Run the whole suite and commit**

```bash
python -m unittest discover -s tests
git add kvstats/compare.py tests/test_kvstats_compare.py
git commit -m "Resample race curves onto a progress grid"
```

> **REVIEW CHECKPOINT.** Stop here. This is the load-bearing math and the invariant that keeps the chart honest.

---

### Task 5: Shape-aware API payload

**Files:**
- Modify: `kvstats/server.py` (`build_run_payload`, `/api/runs`, `/api/scenarios`)
- Test: `tests/test_kvstats_server.py` (rewrite the payload assertions)

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces: `/api/run/<id>` returns `{run, scenario, axis, rate, delta, marks, splits, baselines}` as specified in the spec's "API payload" section. `/api/scenarios` rows gain `shape` and `pb_elapsed`.

- [ ] **Step 1: Write the failing test**

Replace the body of `class RunPayload` in `tests/test_kvstats_server.py` with:

```python
class RunPayload(ServerBase):
    def run_for(self, scenario, order="ASC"):
        return self.conn.execute(
            f"SELECT id FROM run WHERE scenario=? AND perf_file IS NOT NULL "
            f"ORDER BY score {order} LIMIT 1", (scenario,)).fetchone()[0]

    def test_payload_is_serialisable_and_carries_the_shape(self):
        payload = server.build_run_payload(self.conn, self.run_for("Air Pure Medium"))
        json.dumps(payload)   # arrays must be plain lists, not array.array
        for key in ("run", "scenario", "axis", "rate", "delta", "marks",
                    "splits", "baselines"):
            self.assertIn(key, payload)
        self.assertEqual(payload["scenario"]["shape"], "race")

    def test_a_race_run_is_indexed_by_progress_with_shared_kill_marks(self):
        payload = server.build_run_payload(self.conn, self.run_for("Air Pure Medium"))
        self.assertEqual(payload["axis"]["kind"], "progress")
        self.assertEqual(payload["axis"]["n"], 200)
        self.assertEqual(payload["delta"]["unit"], "seconds")
        self.assertEqual(payload["rate"]["metric"], "damage")
        self.assertTrue(payload["marks"]["aligned"])
        self.assertEqual(len(payload["marks"]["kills"]), 5)
        for i, mark in enumerate(payload["marks"]["kills"], start=1):
            self.assertAlmostEqual(mark, i / 5, places=6)

    def test_race_splits_reconcile_to_the_score(self):
        payload = server.build_run_payload(self.conn, self.run_for("Air Pure Medium"))
        bots = [s for s in payload["splits"] if s["idx"] is not None]
        dead = [s for s in payload["splits"] if s["idx"] is None]
        self.assertEqual(len(bots), 5)
        self.assertEqual(len(dead), 1)
        total = sum(s["mine"] for s in payload["splits"])
        self.assertAlmostEqual(total, payload["run"]["elapsed_s"], places=6)
        budget = payload["scenario"]["budget"]
        self.assertAlmostEqual(total, budget - payload["run"]["score"], places=1)

    def test_a_timed_run_keeps_its_per_second_axis(self):
        payload = server.build_run_payload(
            self.conn, self.run_for("Air Voltaic Invincible 4 Medium"))
        self.assertEqual(payload["axis"]["kind"], "time")
        self.assertEqual(payload["delta"]["unit"], "points")
        self.assertFalse(payload["marks"]["aligned"])
        self.assertEqual(payload["splits"], [])

    def test_a_penalising_scenario_is_flagged(self):
        payload = server.build_run_payload(
            self.conn, self.run_for("VT 1w2ts Horizontal Small"))
        self.assertTrue(payload["scenario"]["penalising"])
        self.assertEqual(payload["axis"]["kind"], "time")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m unittest tests.test_kvstats_server -v`
Expected: FAIL — `KeyError: 'scenario'` / `assertIn` failures

- [ ] **Step 3: Implement**

In `kvstats/server.py`, add `from . import shapes` and replace `build_run_payload` with:

```python
def build_run_payload(conn, run_id, metric="score", smoothing=5, recent_n=10,
                      same_cfg=True):
    if metric not in METRICS:
        raise ValueError(f"unknown metric: {metric}")
    recent_n = max(recent_n, 0)

    row = conn.execute("SELECT * FROM run WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(run_id)
    run = {key: row[key] for key in row.keys()}

    scen_row = index.scenario_row(conn, run["scenario"])
    scenario = ({key: scen_row[key] for key in scen_row.keys()} if scen_row else
                {"name": run["scenario"], "shape": shapes.TIMED, "penalising": 0,
                 "budget": None, "pool": None, "bots": None, "clock_s": None,
                 "evidence": "default"})
    is_race = scenario["shape"] == shapes.RACE

    curve = index.load_curve(conn, run_id)
    run["buckets"] = len(curve["score"]) if curve else 0

    base = compare.baselines(conn, run_id, recent_n=recent_n, same_cfg=same_cfg,
                             shape=scenario["shape"])
    payload = {
        "run": run,
        "scenario": scenario,
        "axis": {"kind": "time", "label": "seconds", "n": run["buckets"]},
        "rate": {"metric": metric, "unit": metric, "mine": [], "pb": None,
                 "band": None},
        "delta": {"unit": "points", "values": None, "final": None,
                  "compare_until": None},
        "marks": {"kills": [], "aligned": False},
        "splits": [],
        "baselines": {
            "true_pb": base["true_pb"],
            "candidates": base["candidates"],
            "recent_n": base["recent"]["n"],
            "recent_mean_score": base["recent"]["mean_score"],
            "pb": None if not base["pb"] else {
                "run_id": base["pb"]["run_id"], "score": base["pb"]["score"],
                "started_at": base["pb"]["started_at"],
                "is_true_pb": base["pb"]["is_true_pb"]},
        },
    }

    if is_race:
        _fill_race(conn, payload, run, scenario, curve, base, smoothing)
    else:
        _fill_timed(conn, payload, run, curve, base, metric, smoothing)
    return payload


def _fill_timed(conn, payload, run, curve, base, metric, smoothing):
    """The existing behaviour, unchanged: native per-second grid, score units."""
    mine = _series(curve, metric) if curve else []
    payload["rate"]["mine"] = compare.smooth(mine, smoothing)
    payload["marks"]["kills"] = [k["t"] for k in index.load_kills(conn, run["id"])]

    if curve and base["pb"] and base["pb"]["curve"]:
        pb_curve = base["pb"]["curve"]
        payload["rate"]["pb"] = compare.smooth(_series(pb_curve, metric), smoothing)
        # Always score units, and always on the RAW series: smoothing would blur
        # the invariant that the final value equals the score difference.
        values = compare.cumulative_delta(
            list(curve["score"]), list(pb_curve["score"]))
        payload["delta"].update({
            "values": values, "final": values[-1] if values else None,
            "compare_until": compare.compare_until(
                list(curve["score"]), list(pb_curve["score"]))})

    if curve and base["recent"]["curve"]:
        raw = compare.band([_series(c, metric) for c in base["recent"]["curve"]])
        payload["rate"]["band"] = {k: compare.smooth(v, smoothing)
                                   for k, v in raw.items()}


def _fill_race(conn, payload, run, scenario, curve, base, smoothing):
    """Progress axis, damage rate, seconds-based delta, shared kill marks."""
    bots = scenario["bots"] or 1
    steps = compare.race_grid(bots)
    payload["axis"] = {"kind": "progress", "label": "% of pool", "n": steps}
    payload["rate"].update({"metric": "damage", "unit": "dmg/s"})
    payload["delta"]["unit"] = "seconds"
    # Kill k always lands at damage k*pool/bots, so the marks are the same for
    # every run of the scenario -- which is the whole point of this axis.
    payload["marks"] = {"kills": [(i + 1) / bots for i in range(bots)],
                        "aligned": True}

    mine_edges = []
    if curve and run["elapsed_s"]:
        mine_edges, rate = compare.resample_race(
            list(curve["hits"]), run["elapsed_s"], steps)
        payload["rate"]["mine"] = compare.smooth(rate, smoothing)

    pb_run = None
    if base["pb"] and base["pb"]["curve"]:
        pb_run = conn.execute("SELECT elapsed_s FROM run WHERE id=?",
                              (base["pb"]["run_id"],)).fetchone()
    if mine_edges and pb_run and pb_run["elapsed_s"]:
        base_edges, base_rate = compare.resample_race(
            list(base["pb"]["curve"]["hits"]), pb_run["elapsed_s"], steps)
        payload["rate"]["pb"] = compare.smooth(base_rate, smoothing)
        values = compare.race_delta(mine_edges, base_edges)
        payload["delta"].update({
            "values": values, "final": values[-1] if values else None,
            # Both runs span the whole pool by definition, so there is no
            # region where only one of them has data.
            "compare_until": 1.0})

    if mine_edges and base["recent"]["curve"]:
        curves = []
        for recent in base["recent"]["curve"]:
            _, recent_rate = compare.resample_race(
                list(recent["hits"]), run["elapsed_s"], steps)
            if recent_rate:
                curves.append(recent_rate)
        if curves:
            raw = compare.band(curves)
            payload["rate"]["band"] = {k: compare.smooth(v, smoothing)
                                       for k, v in raw.items()}

    payload["splits"] = _race_splits(conn, run, base)


def _race_splits(conn, run, base):
    """Per-bot rows plus the dead-time residual, so the table reconciles.

    Dead time is not modelled as a scenario constant: it is stable within a
    game version but moved by up to a second across versions, so it is carried
    as this run's own residual and simply shown.
    """
    mine = index.load_kills(conn, run["id"])
    if not mine or not run["elapsed_s"]:
        return []
    base_by_idx = {}
    if base["pb"]:
        base_by_idx = {k["idx"]: k for k in index.load_kills(conn, base["pb"]["run_id"])}

    rows = []
    for kill in mine:
        other = base_by_idx.get(kill["idx"])
        rows.append({"idx": kill["idx"], "bot": kill["bot"], "mine": kill["ttk"],
                     "base": other["ttk"] if other else None,
                     "delta": (kill["ttk"] - other["ttk"]) if other else None})

    mine_dead = run["elapsed_s"] - sum(k["ttk"] for k in mine)
    base_dead = None
    if base_by_idx:
        base_run = conn.execute("SELECT elapsed_s FROM run WHERE id=?",
                                (base["pb"]["run_id"],)).fetchone()
        if base_run and base_run["elapsed_s"]:
            base_dead = base_run["elapsed_s"] - sum(
                k["ttk"] for k in base_by_idx.values())
    rows.append({"idx": None, "bot": "dead time", "mine": mine_dead,
                 "base": base_dead,
                 "delta": (mine_dead - base_dead) if base_dead is not None else None})
    return rows
```

- [ ] **Step 4: Add `shape` and `pb_elapsed` to `/api/scenarios`**

Replace the `/api/scenarios` query with:

```python
            if route == "/api/scenarios":
                return self._json(_rows(conn, """
                    SELECT s.scenario, COUNT(*) AS runs, MAX(s.score) AS pb,
                           MAX(s.started_at) AS last_played,
                           (SELECT shape FROM scenario WHERE name = s.scenario) AS shape,
                           (SELECT elapsed_s FROM run r WHERE r.scenario = s.scenario
                             ORDER BY r.score DESC LIMIT 1) AS pb_elapsed,
                           (SELECT AVG(elapsed_s) FROM (
                                SELECT elapsed_s FROM run r WHERE r.scenario = s.scenario
                                ORDER BY started_at DESC LIMIT 10)) AS recent_elapsed,
                           (SELECT AVG(score) FROM (
                                SELECT score FROM run r WHERE r.scenario = s.scenario
                                ORDER BY started_at DESC LIMIT 10)) AS recent_form
                    FROM run s GROUP BY s.scenario ORDER BY last_played DESC"""))
```

Add `shape` to both `/api/runs` SELECT lists by joining the scenario table:

```python
                        "SELECT id, scenario, started_at, score, accuracy, spm, cfg_key, "
                        "(SELECT buckets FROM curve WHERE curve.run_id = run.id) AS buckets, "
                        "(SELECT shape FROM scenario WHERE name = run.scenario) AS shape "
```

- [ ] **Step 5: Stop offering the efficiency metric where it means nothing**

`efficiency` is `dmg_done / dmg_possible` per bucket. On a scenario that books damage only at kill time the whole-run totals are tiny — `VT Ground Intermediate S5` records 3 and 6 against 6001 shots — so per-second efficiency is ~0 everywhere and the chart is a flat line pretending to be data. On `Air Pure Medium`, `dmg_done == hits`, so it is just accuracy under another name.

Rather than guess per scenario, let the payload say which metrics the run can actually support. Add to `server.py`:

```python
def _usable_metrics(curve):
    """Which metric buttons are worth offering for this run.

    Damage is booked per tick on some scenarios and only at kill time on
    others. Where it is per-kill, dmg_possible is a couple of units against
    thousands of shots, and `efficiency` draws a flat zero -- so it is withheld
    rather than shown as though it were a measurement.
    """
    usable = [name for name in METRICS if name != "efficiency"]
    if curve:
        possible = sum(curve["dmg_possible"])
        shots = sum(curve["shots"])
        if possible > 0 and possible >= 0.5 * shots:
            usable.append("efficiency")
    return usable
```

and set `payload["metrics"] = _usable_metrics(curve)` in `build_run_payload`, immediately after `run["buckets"]` is assigned.

Add to the Task 5 test class:

```python
    def test_efficiency_is_withheld_where_damage_is_only_booked_at_kill_time(self):
        timed = server.build_run_payload(
            self.conn, self.run_for("Air Voltaic Invincible 4 Medium"))
        self.assertNotIn("efficiency", timed["metrics"])
        race = server.build_run_payload(self.conn, self.run_for("Air Pure Medium"))
        self.assertIn("efficiency", race["metrics"])
        self.assertIn("accuracy", race["metrics"])
```

- [ ] **Step 6: Run the tests and commit**

```bash
python -m unittest discover -s tests
git add kvstats/server.py tests/test_kvstats_server.py
git commit -m "Emit a shape-tagged run payload"
```

---

### Task 6: Frontend — axes, kill rules, signed fill, splits — **REVIEW AFTER THIS TASK**

**Files:**
- Modify: `kvstats/web/app.js`, `kvstats/web/style.css`, `kvstats/web/index.html`

**Interfaces:**
- Consumes: the Task 5 payload.
- Produces: no exported interface — this is the last consumer.

Read `kvstats/web/app.js` in full before editing. The published design preview at the URL in the conversation shows the intended result; match its behaviour, not its styling (the app has its own tokens already).

- [ ] **Step 1: Point the render functions at the new payload keys**

`renderCharts(p)` currently reads `p.curve`, `p.pb_curve`, `p.recent_band`, `p.cumulative_delta`, `p.compare_until`. Rewrite those reads as `p.rate.mine`, `p.rate.pb`, `p.rate.band`, `p.delta.values`, `p.delta.compare_until`. Keep the existing `hasCurve` / empty-state branches exactly as they are — they are correct and already handle the ~1-in-7 curveless runs.

- [ ] **Step 2: Make the x axis follow `axis.kind`**

In `mkRate` and `mkDelta`, replace the hard-coded x formatter:

```js
      { ...axisBase(), size: 26,
        values: (u, sp) => sp.map(v => p.axis.kind === 'progress'
          ? Math.round(v * 100) + '%' : v + 's'),
        incrs: p.axis.kind === 'progress'
          ? [.05, .1, .2, .25, .5] : [5, 10, 15, 20, 30, 60] },
```

and build the x series from the axis kind in `renderCharts`:

```js
  // A race is indexed by share of the damage pool, a timed run by seconds.
  // Kill boundaries only line up on the former, which is why it exists.
  const xs = p.axis.kind === 'progress'
    ? Array.from({ length: n }, (_, i) => (i + 0.5) / n)
    : Array.from({ length: n }, (_, i) => i);
```

- [ ] **Step 3: Draw the kill boundaries**

Add beside `drawTail`:

```js
/* Kill boundaries. For a race these are shared -- kill k sits at damage
   k*pool/bots in every run -- so they are drawn solid and labelled. For a
   timed run they belong to the focused run alone and are drawn subdued,
   because two runs' kills genuinely do not coincide on a clock. */
function drawKills(u, p) {
  const marks = p.marks && p.marks.kills;
  if (!marks || !marks.length) return;
  const { top, height } = u.bbox;
  const ctx = u.ctx;
  ctx.save();
  marks.forEach((at, i) => {
    const x = u.valToPos(at, 'x', true);
    ctx.setLineDash(p.marks.aligned ? [4, 3] : [2, 4]);
    ctx.lineWidth = 1;
    ctx.strokeStyle = alpha(p.marks.aligned ? C.pb : C.ghost, p.marks.aligned ? .5 : .45);
    ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, top + height); ctx.stroke();
    if (p.marks.aligned) {
      ctx.setLineDash([]);
      ctx.fillStyle = C.faint;
      ctx.font = '9.5px ' + css('--mono');
      ctx.textAlign = 'right';
      ctx.fillText('bot ' + (i + 1), x - 4, top + 11);
    }
  });
  ctx.restore();
}
```

and call it from both charts' `draw` hooks, before `drawTail`:

```js
      draw: [u => { drawKills(u, p); drawTail(u, p); }],
```

For `mkDelta` the hook becomes `[u => { drawDelta(u); drawKills(u, p); drawTail(u, p); }]`.

- [ ] **Step 4: Add the zero rule and signed fill for penalising scenarios**

`mkRate`'s hook from Step 3 becomes its final form here — the zero rule goes underneath the boundaries so the dashed lines do not fight:

```js
      draw: [u => { drawZero(u, p); drawKills(u, p); drawTail(u, p); }],
```

Add the function, which no-ops unless the scenario is flagged:

```js
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
```

- [ ] **Step 5: Render the split table**

Add to `index.html`, directly after the delta chart panel:

```html
<section class="panel" id="splitPanel" hidden>
  <div class="phead"><span class="ptitle">Splits</span>
    <span class="punit" id="splitSub"></span></div>
  <div class="tw"><table id="splitTable"></table></div>
</section>
```

and in `app.js`:

```js
function renderSplits(p) {
  const panel = $('#splitPanel');
  panel.hidden = !(p.splits && p.splits.length);
  if (panel.hidden) return;
  const worst = p.splits.filter(s => s.idx != null && s.delta > 0)
    .sort((a, b) => b.delta - a.delta).slice(0, 2).map(s => s.idx);
  const cell = v => v == null ? '—'
    : `<span class="${v > 0 ? 'up' : v < 0 ? 'dn' : ''}">${signed(v, 2)}</span>`;
  const total = p.splits.reduce((a, s) => a + s.mine, 0);
  $('#splitSub').textContent = `${p.scenario.bots} bots · ${num(p.scenario.pool, 0)} damage`;
  $('#splitTable').innerHTML =
    `<thead><tr><th></th><th>bot</th><th>this run</th><th>PB</th><th>Δ s</th></tr></thead><tbody>` +
    p.splits.map(s => `<tr class="${worst.includes(s.idx) ? 'w' : ''}">
      <td class="idx">${s.idx == null ? '' : 'bot ' + s.idx}</td>
      <td class="bot">${s.bot}</td><td>${num(s.mine, 2)}</td>
      <td>${num(s.base, 2)}</td><td>${cell(s.delta)}</td></tr>`).join('') +
    `<tr class="tot"><td class="idx"></td><td class="bot">total elapsed</td>
       <td>${num(total, 2)}</td><td>—</td><td>—</td></tr></tbody>`;
}
```

Call `renderSplits(p)` from `loadRun`, after `renderCharts(p)`.

Add to `style.css`. Every colour here is an existing token — introduce none:

```css
/* ── split table ─────────────────────────────────────────── */
#splitPanel .tw{overflow-x:auto}
#splitTable{border-collapse:collapse; width:100%; min-width:520px}
#splitTable th,#splitTable td{
  padding:6px 14px; text-align:right; border-bottom:1px solid var(--line);
  font-family:var(--mono); font-size:12.5px;
  font-variant-numeric:tabular-nums lining-nums;
}
#splitTable th{
  font-weight:500; font-size:10.5px; letter-spacing:.1em; text-transform:uppercase;
  color:var(--faint); border-bottom-color:var(--line-2);
}
#splitTable th:first-child,#splitTable td:first-child{text-align:left}
#splitTable td.idx{color:var(--faint); white-space:nowrap}
#splitTable td.bot{color:var(--dim)}
#splitTable tbody tr:hover td{background:var(--bg-raise)}
#splitTable tr.tot td{border-top:1px solid var(--line-2); color:var(--ink)}
#splitTable tr.w td:first-child{box-shadow:inset 2px 0 0 var(--bad)}
#splitTable .up{color:var(--bad)}
#splitTable .dn{color:var(--good)}
```

- [ ] **Step 6: Make the headline and scenarios view shape-aware**

In `renderHeadline`, replace the fixed `cells` array with a shape branch:

```js
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
```

In `renderScenarios`, branch the form column so race scenarios read in time:

```js
      // Race scores sit in a 906-919 band out of 1000, so every race scenario
      // pins at ~99% of PB and the bar dies. Time is the honest measure there.
      const isRace = s.shape === 'race';
      const rel = isRace
        ? (s.recent_elapsed && s.pb_elapsed ? s.pb_elapsed / s.recent_elapsed : null)
        : (form != null && s.pb ? form / s.pb : null);
```

and label the column `% of PB` for timed and `PB time / recent` for race.

- [ ] **Step 7: Badge race rows in the run rail, and hide dead metric buttons**

`/api/runs` now returns `shape` per row. In `renderRunList`, add it to the `<li>` so the rail can mark a race run — otherwise the chart changing shape when you arrow onto one is a surprise:

```js
      data-shape="${r.shape || 'timed'}"
```

and in `style.css`:

```css
.run[data-shape="race"] .t::after{
  content:"race"; margin-left:6px; padding:1px 4px; border-radius:2px;
  font-size:9px; letter-spacing:.08em; text-transform:uppercase;
  color:var(--pb); border:1px solid color-mix(in oklab,var(--pb) 40%,var(--line-2));
}
```

Then honour `p.metrics` in `buildSegs` so a metric the run cannot support is not offered:

```js
function buildSegs() {
  const usable = (A.payload && A.payload.metrics) || METRICS.map(m => m[0]);
  $('#ctlMetric').innerHTML = METRICS.filter(([k]) => usable.includes(k)).map(([k, l]) =>
    `<button type="button" role="radio" data-v="${k}" aria-checked="${A.ctrl.metric === k}">${k}</button>`).join('');
  $('#ctlSmooth').innerHTML = SMOOTH.map(([k, l]) =>
    `<button type="button" role="radio" data-v="${k}" aria-checked="${A.ctrl.smoothing === k}">${l}</button>`).join('');
}
```

Call `buildSegs()` from `loadRun` after `A.payload = p`, and fall back to `score` when the current metric is no longer offered:

```js
  if (p.metrics && !p.metrics.includes(A.ctrl.metric)) {
    A.ctrl.metric = 'score';
    return loadRun(id, isNew);   // one re-fetch, then render
  }
```

- [ ] **Step 8: Verify in the real app**

```bash
python -m kvstats
```

Open the printed URL. Confirm, against the fixtures' own scenarios:
- an **Air Pure Medium** run shows a `%`-labelled x axis, five labelled `bot N` rules, a seconds-unit delta chart, and a split table whose total equals `1000 − score`;
- an **Air Voltaic Invincible 4 Medium** run is unchanged from before this work, with a seconds x axis and no split table;
- a **VT 1w2ts Horizontal Small** run shows the zero rule, and its metric row offers no `efficiency` button.

- [ ] **Step 9: Commit**

```bash
git add kvstats/web/app.js kvstats/web/style.css kvstats/web/index.html
git commit -m "Render the axis, marks and splits the payload's shape names"
```

> **REVIEW CHECKPOINT.** Stop here. This is the largest single chunk and the only one without automated coverage.

---

### Task 7: Integration test and documentation — **REVIEW AFTER THIS TASK**

**Files:**
- Create: `tests/test_kvstats_shapes_integration.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything.
- Produces: nothing.

- [ ] **Step 1: Write the integration test**

Create `tests/test_kvstats_shapes_integration.py`:

```python
"""End to end: a stats directory in, a correct race payload out.

Everything below is asserted against real KovaaK's output, so this is the test
that catches a break anywhere in the chain -- parse, classify, index, resample,
serialise -- without knowing which link failed.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from kvstats import index, paths, server  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "kvstats")


class RaceEndToEnd(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        shutil.copytree(os.path.join(FIXTURES, "stats"), os.path.join(self.dir, "stats"))
        shutil.copytree(os.path.join(FIXTURES, "performances"),
                        os.path.join(self.dir, "performances"))
        self.cfg = paths.load({"KOVAAKS_DIR": self.dir,
                               "KVSTATS_DB": os.path.join(self.dir, "i.sqlite3")})
        self.conn = index.connect(self.cfg.db_path)
        self.addCleanup(self.conn.close)
        index.bootstrap(self.conn, self.cfg)

    def slow_run(self):
        return self.conn.execute(
            "SELECT id FROM run WHERE scenario='Air Pure Medium' "
            "ORDER BY score ASC LIMIT 1").fetchone()[0]

    def test_the_whole_chain_produces_a_reconciling_race_payload(self):
        payload = server.build_run_payload(self.conn, self.slow_run())
        json.dumps(payload)

        # classified from the countdown curve
        self.assertEqual(payload["scenario"]["shape"], "race")
        self.assertEqual(payload["scenario"]["evidence"], "perf-countdown")

        # indexed by progress, with boundaries that coincide across runs
        self.assertEqual(payload["axis"]["kind"], "progress")
        self.assertEqual(len(payload["rate"]["mine"]), payload["axis"]["n"])
        self.assertEqual(payload["marks"]["kills"], [0.2, 0.4, 0.6, 0.8, 1.0])

        # the invariant, end to end: the delta's endpoint IS the score gap
        pb_score = payload["baselines"]["pb"]["score"]
        self.assertAlmostEqual(payload["delta"]["final"],
                               payload["run"]["score"] - pb_score, places=1)

        # and the split table adds up to the score
        total = sum(s["mine"] for s in payload["splits"])
        self.assertAlmostEqual(total, payload["run"]["elapsed_s"], places=6)
        self.assertAlmostEqual(
            total, payload["scenario"]["budget"] - payload["run"]["score"], places=1)

    def test_the_pb_survives_baseline_selection_on_a_slow_run(self):
        """The bug this feature exists to fix: the +-10% duration filter used
        to drop the PB from exactly the runs that needed it most."""
        payload = server.build_run_payload(self.conn, self.slow_run())
        self.assertIsNotNone(payload["baselines"]["pb"])
        self.assertIsNotNone(payload["delta"]["values"])
        self.assertGreater(payload["baselines"]["pb"]["score"], payload["run"]["score"])

    def test_a_rebuild_from_scratch_reaches_the_same_verdict(self):
        """Schema 2 is drop-and-rebuild, so a rebuild has to be idempotent."""
        before = dict(index.scenario_row(self.conn, "Air Pure Medium"))
        index.bootstrap(self.conn, self.cfg)
        self.assertEqual(dict(index.scenario_row(self.conn, "Air Pure Medium")), before)
```

- [ ] **Step 2: Run it to verify it fails, then passes**

Run: `python -m unittest tests.test_kvstats_shapes_integration -v`

If it fails, the failure is real — fix the implementation, not the test.

- [ ] **Step 3: Run the whole suite**

Run: `python -m unittest discover -s tests`
Expected: PASS, every test

- [ ] **Step 4: Update the README**

In `README.md`, in the `kvstats` section, add a third bullet to the "Two things worth knowing about the data" list — and change that lead-in to "Three things worth knowing about the data":

```markdown
- Not every scenario is scored on a clock. Some spawn a fixed number of bots and
  score you on how long you took (`score = 1000 - elapsed`). Those are charted
  against share of the damage pool rather than seconds, so bot boundaries line up
  between runs, and the delta chart reads in seconds gained or lost.
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_kvstats_shapes_integration.py README.md
git commit -m "Add end-to-end race coverage and document scenario shapes"
```

> **REVIEW CHECKPOINT.** Stop here — final review before the branch is finished.

---

## After the plan

Once Task 7 is reviewed, use `superpowers:finishing-a-development-branch` to decide how `kvstats-scenario-shapes` gets integrated.
