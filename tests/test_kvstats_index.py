"""Indexing: bootstrap, idempotence, curve round-trip, and failure capping."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from kvstats import index, paths  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "kvstats")


class IndexBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        shutil.copytree(os.path.join(FIXTURES, "stats"), os.path.join(self.dir, "stats"))
        shutil.copytree(os.path.join(FIXTURES, "performances"),
                        os.path.join(self.dir, "performances"))
        self.cfg = paths.load({"KOVAAKS_DIR": self.dir,
                               "KVSTATS_DB": os.path.join(self.dir, "index.sqlite3")})
        self.conn = index.connect(self.cfg.db_path)
        self.addCleanup(self.conn.close)


class Bootstrap(IndexBase):
    def test_indexes_every_csv_attaches_curves_and_never_re_reads(self):
        counts = index.bootstrap(self.conn, self.cfg)
        self.assertEqual(counts["runs"], 10)   # 6 paired + 4 CSV-only
        self.assertEqual(counts["curves"], 6)

        # the CSV-only run is still a run, just without a curve
        curveless, = self.conn.execute(
            "SELECT COUNT(*) FROM run WHERE perf_file IS NULL").fetchone()
        self.assertEqual(curveless, 4)

        # spm is derived only where a curve gave us a duration
        score, duration, spm = self.conn.execute(
            "SELECT score, duration_s, spm FROM run WHERE perf_file IS NOT NULL "
            "ORDER BY score DESC LIMIT 1").fetchone()
        self.assertAlmostEqual(spm, score / duration * 60, places=3)

        again = index.bootstrap(self.conn, self.cfg)
        self.assertEqual(again["runs"], 0, "an indexed file must never be re-read")
        total, = self.conn.execute("SELECT COUNT(*) FROM run").fetchone()
        self.assertEqual(total, 10)


class Reconciliation(IndexBase):
    def test_a_perf_that_arrives_after_the_csv_is_picked_up_on_a_later_bootstrap(self):
        """Without a reconciliation pass this run would never get a curve.

        The new-file loop only considers CSVs it has not seen, and the watcher
        only tracks files it saw arrive -- so a first launch during a mid-write
        would permanently bake in a curveless run, recoverable only by deleting
        the index.
        """
        perf_dir = os.path.join(self.dir, "performances")
        stashed = {}
        for name in os.listdir(perf_dir):
            path = os.path.join(perf_dir, name)
            stashed[path] = open(path, "rb").read()
            os.unlink(path)

        first = index.bootstrap(self.conn, self.cfg)
        self.assertEqual(first["curves"], 0)

        for path, blob in stashed.items():
            with open(path, "wb") as handle:
                handle.write(blob)

        second = index.bootstrap(self.conn, self.cfg)
        self.assertEqual(second["runs"], 0, "no new CSVs appeared")
        self.assertEqual(second["reconciled"], 6)
        remaining, = self.conn.execute(
            "SELECT COUNT(*) FROM run WHERE perf_file IS NULL").fetchone()
        self.assertEqual(remaining, 4, "only the genuinely perf-less runs stay")


class Failures(IndexBase):
    def test_a_corrupt_perf_leaves_the_run_indexed_and_stops_being_retried(self):
        # Name the file explicitly: picking listdir()[0] silently changes what
        # this test covers the moment a fixture sorting earlier is added.
        target = os.path.join(
            self.dir, "performances",
            "Pasu Voltaic Reload Easier - Challenge - "
            "2026.07.27-16.50.01 Performance.perf")
        self.assertTrue(os.path.exists(target), "fixture name drifted")
        with open(target, "wb") as handle:
            handle.write(b"not protobuf")

        counts = index.bootstrap(self.conn, self.cfg)
        self.assertEqual(counts["runs"], 10, "a bad curve must not lose the run")
        self.assertEqual(counts["curves"], 5)

        for _ in range(index.MAX_TRIES + 3):
            tries = index.record_failure(self.conn, "C:/x/bad.perf", "boom")
        self.assertEqual(tries, index.MAX_TRIES)


class Rebuild(IndexBase):
    def test_a_stale_schema_is_dropped_and_rebuilt(self):
        """The index is a disposable derived cache; a version bump must discard
        it rather than run new SQL against old columns."""
        index.bootstrap(self.conn, self.cfg)
        value, = self.conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()
        self.assertEqual(int(value), index.SCHEMA_VERSION)

        self.conn.execute("UPDATE meta SET value='0' WHERE key='schema_version'")
        self.conn.commit()
        self.conn.close()
        conn = index.connect(self.cfg.db_path)
        self.addCleanup(conn.close)
        total, = conn.execute("SELECT COUNT(*) FROM run").fetchone()
        self.assertEqual(total, 0)


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
        # The .perf score series round-trips through float32, so the raw
        # budget is ~999.998, not exactly 1000 -- places=1 still catches any
        # real budget error.
        self.assertAlmostEqual(row["budget"], 1000.0, places=1)
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

    def test_kill_number_zero_on_every_row_does_not_fail_the_insert(self):
        """"Happy Easter!" writes `Kill #` = 0 on every row. A parser that
        trusted that column would hand two rows the same idx, the `kill`
        table's PRIMARY KEY (run_id, idx) would raise UNIQUE constraint
        failed on the second INSERT, and bootstrap's per-file except would
        quietly record the run as failed with only its first kill stored. A
        test that only covers the parser would not catch this -- the crash
        happens at the INSERT."""
        failed = {row[0] for row in
                  self.conn.execute("SELECT path FROM failed").fetchall()}
        self.assertEqual(failed, set())

        run = self.conn.execute(
            "SELECT id FROM run WHERE scenario='Happy Easter!'").fetchone()
        self.assertIsNotNone(run, "the run itself must still be indexed")
        kills = index.load_kills(self.conn, run["id"])
        self.assertEqual(len(kills), 2)
        self.assertEqual([k["idx"] for k in kills], [1, 2])


if __name__ == "__main__":
    unittest.main()
