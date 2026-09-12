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


class Baselines(unittest.TestCase):
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
        cursor = self.conn.execute(
            "INSERT INTO run(scenario, started_at, stats_file, score, cfg_key, "
            "duration_s, shots, hits, misses) VALUES(?,?,?,?,?,?,0,0,0)",
            (scenario, started_at, f"{scenario}-{started_at}", score, cfg_key,
             duration))
        run_id = cursor.lastrowid
        if curve is not None:
            # baselines() treats a run as drawable only when perf_file is set,
            # so a synthetic curve must come with one.
            self.conn.execute("UPDATE run SET perf_file=? WHERE id=?",
                              (f"{scenario}-{started_at}.perf", run_id))
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


if __name__ == "__main__":
    unittest.main()
