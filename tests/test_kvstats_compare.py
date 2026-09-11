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
        self.assertEqual(got["candidates"], 1)

    def test_a_first_ever_run_yields_no_baselines_but_does_not_raise(self):
        focus = self.add("S", "2026-01-01T10:00:00", 200, curve=[2.0] * 60)
        got = compare.baselines(self.conn, focus)
        self.assertEqual(got["candidates"], 0)
        self.assertIsNone(got["pb"])
        self.assertIsNone(got["recent"]["curve"])


if __name__ == "__main__":
    unittest.main()
