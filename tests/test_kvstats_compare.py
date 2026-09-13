"""Comparison math, including the invariant the whole dashboard rests on."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from kvstats import compare, index, paths  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "kvstats")


class PureMath(unittest.TestCase):
    def test_smooth_is_centred_and_never_changes_the_length(self):
        """An off-by-one here shifts the whole curve against its baseline -- the
        same silent-misalignment class as a short densified series."""
        self.assertEqual(compare.smooth([1, 2, 3], 1), [1, 2, 3])
        got = compare.smooth([0, 0, 9, 0, 0], 3)
        self.assertEqual(len(got), 5)
        self.assertAlmostEqual(got[2], 3.0, msg="the spike must stay at index 2")
        self.assertGreater(got[1], 0)
        self.assertGreater(got[3], 0)
        # a window wider than the series must not shrink it
        self.assertEqual(len(compare.smooth([4, 4, 4, 4], 5)), 4)

    def test_cumulative_delta_survives_unequal_lengths(self):
        """The headline invariant, tested where it actually breaks.

        Equal-length inputs make this tautological -- a running sum's last
        element IS the difference of sums by construction. About 6% of real run
        pairs differ in length, and truncating there silently loses score from
        whichever run is longer. Padding is what keeps the chart's endpoint
        equal to the headline number, in either direction.
        """
        mine = [3.0, 7.5, 0.0, 12.25, 4.0, 6.0, 2.5]   # 7 buckets, sums to 35.25
        base = [5.0, 1.0, 6.0, 2.0]                     # 4 buckets, sums to 14.0
        got = compare.cumulative_delta(mine, base)
        self.assertEqual(len(got), 7, "must not truncate to the shorter run")
        self.assertAlmostEqual(got[-1], 35.25 - 14.0, places=6)

        # and symmetrically, when the baseline is the longer of the two
        got = compare.cumulative_delta([1.0, 1.0], [0.5, 0.5, 0.5, 0.5])
        self.assertEqual(len(got), 4)
        self.assertAlmostEqual(got[-1], 0.0, places=6)

        # the tail past the shorter run is marked, not silently drawn as real
        self.assertEqual(compare.compare_until([1] * 7, [1] * 4), 4)
        self.assertEqual(compare.compare_until([1] * 4, [1] * 4), 4)

    def test_band_collapses_to_the_mean_under_three_curves(self):
        """Two samples produce a sigma, but it is noise dressed as confidence."""
        got = compare.band([[0, 10], [10, 20], [20, 30]])
        self.assertAlmostEqual(got["mean"][0], 10.0)
        self.assertLess(got["lo"][0], got["mean"][0])
        self.assertGreater(got["hi"][0], got["mean"][0])

        for too_few in ([[0.0, 10.0], [10.0, 20.0]], [[1, 2, 3]]):
            got = compare.band(too_few)
            self.assertEqual(got["lo"], got["mean"])
            self.assertEqual(got["hi"], got["mean"])

        self.assertEqual(compare.band([]), {"mean": [], "lo": [], "hi": []})


class SyntheticRuns:
    """A real index over hand-built runs. Not a TestCase itself, so the classes
    that mix it in do not each re-run the others' tests."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        os.makedirs(os.path.join(self.dir, "stats"))
        os.makedirs(os.path.join(self.dir, "performances"))
        self.cfg = paths.load({"KOVAAKS_DIR": self.dir,
                               "KVSTATS_DB": os.path.join(self.dir, "i.sqlite3")})
        self.conn = index.connect(self.cfg.db_path)
        self.addCleanup(self.conn.close)

    def add(self, scenario, started_at, score, cfg_key="52.0",
            duration=60.0, curve=None):
        """Insert a synthetic run, optionally with a curve."""
        # KovaaK's names every stats file distinctly even for runs that finish
        # in the same second, so the counter is what keeps these realistic --
        # without it a same-timestamp pair trips the UNIQUE on stats_file.
        self.written = getattr(self, "written", 0) + 1
        cursor = self.conn.execute(
            "INSERT INTO run(scenario, started_at, stats_file, score, cfg_key, "
            "duration_s, shots, hits, misses) VALUES(?,?,?,?,?,?,0,0,0)",
            (scenario, started_at, f"{scenario}-{started_at}-{self.written}",
             score, cfg_key, duration))
        run_id = cursor.lastrowid
        if curve is not None:
            # baselines() treats a run as drawable only when perf_file is set,
            # so a synthetic curve must come with one.
            self.conn.execute(
                "UPDATE run SET perf_file=? WHERE id=?",
                (f"{scenario}-{started_at}-{self.written}.perf", run_id))
            import array
            from kvstats import perf as perfmod
            blobs = []
            for name in perfmod.SERIES:
                values = array.array("f", curve if name == "score"
                                     else [0.0] * len(curve))
                blobs.append(values.tobytes())
            self.conn.execute(
                "INSERT INTO curve(run_id, buckets, shots, hits, misses, "
                "dmg_done, dmg_possible, score, kills) VALUES(?,?,?,?,?,?,?,?,?)",
                [run_id, len(curve), *blobs])
        self.conn.commit()
        return run_id


class Baselines(SyntheticRuns, unittest.TestCase):
    def test_pb_selection_respects_curves_and_sensitivity(self):
        """Three rules at once, because they all shape the same overlay.

        The PB overlay must be the best run we can actually draw, must say so
        when that is not the true PB (35 of 316 real scenarios have a PB with no
        `.perf`), and must not reach across a sensitivity change unless asked.
        """
        self.add("S", "2026-01-02T10:00:00", 999)                      # no curve
        self.add("S", "2026-01-01T10:00:00", 300, curve=[3.0] * 60)
        self.add("S", "2026-01-01T11:00:00", 900, cfg_key="27.0", curve=[9.0] * 60)
        focus = self.add("S", "2026-01-03T10:00:00", 200, curve=[2.0] * 60)

        got = compare.baselines(self.conn, focus)
        self.assertEqual(got["true_pb"]["score"], 999)
        self.assertEqual(got["pb"]["score"], 300, "the other-sens 900 must not win")
        self.assertFalse(got["pb"]["is_true_pb"],
                         "a non-PB overlay must never be labelled as the PB")

        relaxed = compare.baselines(self.conn, focus, same_cfg=False)
        self.assertEqual(relaxed["pb"]["score"], 900)

    def test_recent_form_looks_only_backwards_and_skips_aborted_runs(self):
        """A baseline built from later runs would leak the future into a replay,
        and a 12 s quit-out is not a performance to average against."""
        self.add("S", "2026-01-01T10:00:00", 100, curve=[1.0] * 60)
        self.add("S", "2026-01-01T11:00:00", 900, duration=12.0, curve=[9.0] * 12)
        focus = self.add("S", "2026-01-02T10:00:00", 200, curve=[2.0] * 60)
        self.add("S", "2026-01-03T10:00:00", 999, curve=[9.0] * 60)   # after
        got = compare.baselines(self.conn, focus)
        self.assertEqual(got["recent"]["n"], 1)
        self.assertEqual(got["recent"]["mean_score"], 100)
        # PB is all-time, so the later 999 run is a legitimate candidate; only
        # `recent` is scoped to look backwards. Don't "fix" this back to 1.
        self.assertEqual(got["candidates"], 2)

    def test_a_first_ever_run_yields_no_baselines_but_does_not_raise(self):
        focus = self.add("S", "2026-01-01T10:00:00", 200, curve=[2.0] * 60)
        got = compare.baselines(self.conn, focus)
        self.assertEqual(got["candidates"], 0)
        self.assertIsNone(got["pb"])
        self.assertIsNone(got["recent"]["curve"])


class RunPage(SyntheticRuns, unittest.TestCase):
    """`page` is the run rail's data. Its one job is to say how each run stood
    against its own history -- and to answer that from the whole history, not
    from the slice it happens to be returning."""

    def test_best_before_reaches_past_the_page_it_returns(self):
        """The bug this function exists to kill.

        Folding the marks over the returned rows makes the answer depend on how
        many rows were asked for. On the real corpus that mis-marked 27 of the
        newest 100 runs: a 2913 PB sat five minutes before the window, so the
        rail called a 2903 run a personal best at +3.0% while the headline,
        which reads all of history, called the same run -0.3%.
        """
        self.add("S", "2026-01-01T10:00:00", 2913)     # the PB, out of the page
        self.add("S", "2026-01-01T11:00:00", 2800)
        self.add("S", "2026-01-01T12:00:00", 2903)

        rows = compare.page(self.conn, limit=2)

        self.assertEqual([r["score"] for r in rows], [2903, 2800],
                         "newest first, and only the page that was asked for")
        self.assertEqual(rows[0]["best_before"], 2913,
                         "the out-of-page 2913 still has to count")
        self.assertEqual(rows[1]["best_before"], 2913)


    def test_first_means_first_ever_not_first_in_the_page(self):
        """16 of the newest 100 real runs were labelled `first` because their
        scenario had nothing else in the window. `best_before` alone cannot
        tell those apart from a true debut -- both are NULL -- so the count
        comes back as its own column."""
        self.add("S", "2026-01-01T10:00:00", 100)
        self.add("S", "2026-01-01T11:00:00", 200)
        self.add("NEW", "2026-01-01T12:00:00", 50)

        rows = {r["scenario"]: r for r in compare.page(self.conn, limit=2)}

        self.assertEqual(rows["NEW"]["played_before"], 0, "a real debut")
        self.assertEqual(rows["S"]["played_before"], 1,
                         "one earlier run, even though the page cannot see it")


    def test_a_sensitivity_change_is_not_a_personal_best(self):
        """`baselines` refuses to reach across a cm/360 change and the rail has
        to refuse the same way, or the same pair of runs reads as a PB in one
        panel and a loss in the other. 82 of 316 real scenarios have been
        played at more than one sensitivity."""
        self.add("S", "2026-01-01T10:00:00", 900, cfg_key="52.0")
        focus = self.add("S", "2026-01-01T11:00:00", 300, cfg_key="27.0")

        row = compare.page(self.conn, limit=1)[0]
        self.assertEqual(row["id"], focus)
        self.assertIsNone(row["best_before"], "the 900 was set at another sens")
        self.assertEqual(row["played_before"], 0)

        relaxed = compare.page(self.conn, limit=1, same_cfg=False)[0]
        self.assertEqual(relaxed["best_before"], 900)
        self.assertEqual(relaxed["played_before"], 1)


    def test_a_quit_out_is_not_a_personal_best_to_lose_to(self):
        """The +-10% duration filter, which is the reason this is a correlated
        subquery and not a window function: the tolerance is relative to each
        focused run, not constant across a partition. A run with no `.perf` has
        no duration to judge and still counts, exactly as in `candidates`."""
        self.add("S", "2026-01-01T09:00:00", 900, duration=12.0)   # quit-out
        self.add("S", "2026-01-01T10:00:00", 300, duration=60.0)
        self.add("S", "2026-01-01T11:00:00", 400, duration=None)   # no .perf
        self.add("S", "2026-01-01T12:00:00", 200, duration=60.0)

        row = compare.page(self.conn, limit=1)[0]
        self.assertEqual(row["best_before"], 400,
                         "the 12 s 900 is out; the unmeasured 400 is in")
        self.assertEqual(row["played_before"], 2)

    def test_the_duration_filter_is_skipped_for_race_scenarios(self):
        """Duration IS the score on a race, so filtering by it throws the
        comparison away -- `candidates` skips it there and so must the rail,
        or every race run in the list reads as a debut."""
        self.conn.execute(
            "INSERT INTO scenario(name, shape, bots) VALUES('R', 'race', 5)")
        self.add("R", "2026-01-01T10:00:00", 920.0, duration=80.0)
        self.add("R", "2026-01-01T11:00:00", 906.0, duration=94.0)

        # 14 s apart against a +-9.4 s tolerance, so the timed rule would bite.
        row = compare.page(self.conn, limit=1)[0]
        self.assertEqual(row["best_before"], 920.0,
                         "being 15% faster is the whole point of a race")
        self.assertEqual(row["played_before"], 1)


    def test_a_later_run_never_counts_as_a_run_you_beat(self):
        """The rail is a ledger, not a verdict. `best_before` is what you had
        to beat at the time, so tomorrow's 999 cannot retroactively take a
        personal best away from today. This is the one place the rail is meant
        to differ from the headline, which measures against your best ever."""
        self.add("S", "2026-01-01T10:00:00", 100)
        self.add("S", "2026-01-01T11:00:00", 200)
        self.add("S", "2026-01-01T12:00:00", 999)

        rows = compare.page(self.conn, limit=3)
        self.assertEqual([r["score"] for r in rows], [999, 200, 100])
        self.assertEqual(rows[1]["best_before"], 100,
                         "the later 999 must not reach backwards")
        self.assertEqual(rows[1]["played_before"], 1)

    def test_a_run_with_no_recorded_sensitivity_still_has_a_history(self):
        """`candidates` filters by cm/360 only when the focused run has one to
        filter by. Matching NULL against NULL instead would cut every such run
        off from its own past."""
        self.add("S", "2026-01-01T10:00:00", 100, cfg_key="52.0")
        self.add("S", "2026-01-01T11:00:00", 200, cfg_key=None)

        row = compare.page(self.conn, limit=1)[0]
        self.assertEqual(row["best_before"], 100)
        self.assertEqual(row["played_before"], 1)


    def test_the_page_is_read_in_order_rather_than_sorted(self):
        """The rail asks for the newest N runs by time, and until now nothing
        indexed time -- only (scenario, started_at) and (scenario, score). So
        every fetch scanned the whole table into a temp b-tree. That is what
        makes a deep page cost more than a shallow one, which is the thing
        lazy-loading the rail cannot afford.
        """
        for hour in range(12):
            self.add("S", f"2026-01-01T{hour:02d}:00:00", 100 + hour)

        plan = " ".join(
            row[3] for row in self.conn.execute(
                "EXPLAIN QUERY PLAN "
                "SELECT id FROM run ORDER BY started_at DESC LIMIT 5"))
        self.assertNotIn("TEMP B-TREE", plan,
                         f"the newest-first read still sorts the table: {plan}")


    def test_paging_older_walks_the_whole_history_without_gaps(self):
        """The rail loads more as you scroll, so the pages have to tile: every
        run exactly once, newest first, however they are cut up."""
        for hour in range(10):
            self.add("S", f"2026-01-01{'T%02d:00:00' % hour}", 100 + hour)

        # Bounded deliberately. A cursor that fails to advance -- `<=` instead
        # of `<` on the row value, say -- makes this walk forever, and a suite
        # that hangs is worse than one that fails.
        seen, cursor = [], None
        for _ in range(10):
            rows = compare.page(self.conn, limit=3, before=cursor)
            if not rows:
                break
            seen += [r["id"] for r in rows]
            cursor = rows[-1]["id"]
        else:
            self.fail(f"the cursor never reached the end: {len(seen)} rows seen")

        every = [r[0] for r in self.conn.execute(
            "SELECT id FROM run ORDER BY started_at DESC, id DESC")]
        self.assertEqual(seen, every, "the pages must tile the history exactly")

    def test_a_later_page_is_still_marked_against_runs_it_cannot_see(self):
        """The reason the marks are computed server-side. A run's history is
        OLDER than it, so it sits on pages the client has not fetched yet --
        page two must already know what page five contains."""
        self.add("S", "2026-01-01T10:00:00", 900)      # the PB, five pages down
        for hour in range(11, 21):
            self.add("S", f"2026-01-01T{hour}:00:00", 100 + hour)

        second = compare.page(self.conn, limit=3,
                              before=compare.page(self.conn, limit=3)[-1]["id"])
        for row in second:
            self.assertEqual(row["best_before"], 900,
                             "the unfetched 900 still has to count")

    def test_runs_sharing_a_timestamp_are_neither_dropped_nor_repeated(self):
        """`started_at` has second resolution and no uniqueness constraint. A
        cursor on time alone would skip a run on the page boundary; the cursor
        is the whole (time, id) pair so a tie cannot straddle it."""
        for _ in range(4):
            self.add("S", "2026-01-01T10:00:00", 100)

        first = compare.page(self.conn, limit=2)
        second = compare.page(self.conn, limit=2, before=first[-1]["id"])
        ids = [r["id"] for r in first] + [r["id"] for r in second]
        self.assertEqual(len(set(ids)), 4, f"all four runs, each once: {ids}")


class RunPageMatchesBaselines(SyntheticRuns, unittest.TestCase):
    """The rail and the headline must not be able to drift apart.

    `page` restates `candidates`' rules in SQL so the whole history can be
    consulted in one query. That restatement is the risk, so this walks a
    corpus built to make every rule matter and insists the two agree on every
    row. The fixtures alone are too thin for that -- most of their scenarios
    have at most one prior run, so no filter ever gets to discriminate -- so
    each real scenario is seeded with a history designed to trip the rules.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        shutil.copytree(os.path.join(FIXTURES, "stats"),
                        os.path.join(self.dir, "stats"))
        shutil.copytree(os.path.join(FIXTURES, "performances"),
                        os.path.join(self.dir, "performances"))
        cfg = paths.load({"KOVAAKS_DIR": self.dir,
                          "KVSTATS_DB": os.path.join(self.dir, "i.sqlite3")})
        self.conn = index.connect(cfg.db_path)
        self.addCleanup(self.conn.close)
        index.bootstrap(self.conn, cfg)

    def seed(self):
        """Give every fixture scenario a history that exercises each rule."""
        for name, cfg_key, duration in self.conn.execute(
                "SELECT scenario, cfg_key, duration_s FROM run GROUP BY scenario"):
            span = duration or 60.0
            for hour, score, key, dur in (
                    # a high score at another sensitivity, which same_cfg must drop
                    (1, 9000.0, "99.9", span),
                    # a high score at a wild duration, which the timed rule drops
                    # and the race rule keeps
                    (2, 8000.0, cfg_key, span * 2.5),
                    # unmeasured, so it counts on either path
                    (3, 500.0, cfg_key, None),
                    # ordinary, in tolerance
                    (4, 400.0, cfg_key, span * 1.02),
                    # and one in the future, which must never reach backwards
                    (9, 9999.0, cfg_key, span)):
                self.add(name, f"2026-10-0{hour}T12:00:00", score,
                         cfg_key=key, duration=dur)

    def fold(self, run, same_cfg):
        """What the rail would compute if it folded `candidates` in Python."""
        shape = index.scenario_row(self.conn, run["scenario"])["shape"]
        prior = [c for c in compare.candidates(self.conn, run["id"],
                                               same_cfg=same_cfg, shape=shape)
                 if c["started_at"] < run["started_at"]]
        scores = [c["score"] for c in prior if c["score"] is not None]
        return (max(scores) if scores else None), len(prior)

    def test_every_row_agrees_with_a_python_fold_over_candidates(self):
        self.seed()
        total = self.conn.execute("SELECT COUNT(*) FROM run").fetchone()[0]
        for same_cfg in (True, False):
            rows = compare.page(self.conn, limit=total, same_cfg=same_cfg)
            self.assertEqual(len(rows), total, "the whole corpus, not a page")
            disagreed = 0
            for row in rows:
                best, played = self.fold(row, same_cfg)
                if (row["best_before"], row["played_before"]) != (best, played):
                    disagreed += 1
                with self.subTest(run=row["id"], scenario=row["scenario"],
                                  same_cfg=same_cfg):
                    self.assertEqual(row["best_before"], best)
                    self.assertEqual(row["played_before"], played)
            self.assertEqual(disagreed, 0)


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

        places=6 holds here only because these literals define the score from
        the same elapsed the delta is built from. On a real pair the two come
        from different sources -- a three-decimal CSV timestamp against the
        game's own clock -- and agree to about +-0.02 s, not to six places.
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


if __name__ == "__main__":
    unittest.main()
