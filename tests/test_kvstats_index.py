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
        self.assertEqual(counts["runs"], 3)   # 2 paired + 1 CSV-only
        self.assertEqual(counts["curves"], 2)

        # the CSV-only run is still a run, just without a curve
        curveless, = self.conn.execute(
            "SELECT COUNT(*) FROM run WHERE perf_file IS NULL").fetchone()
        self.assertEqual(curveless, 1)

        # spm is derived only where a curve gave us a duration
        score, duration, spm = self.conn.execute(
            "SELECT score, duration_s, spm FROM run WHERE perf_file IS NOT NULL "
            "ORDER BY score DESC LIMIT 1").fetchone()
        self.assertAlmostEqual(spm, score / duration * 60, places=3)

        again = index.bootstrap(self.conn, self.cfg)
        self.assertEqual(again["runs"], 0, "an indexed file must never be re-read")
        total, = self.conn.execute("SELECT COUNT(*) FROM run").fetchone()
        self.assertEqual(total, 3)


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
        self.assertEqual(second["reconciled"], 2)
        remaining, = self.conn.execute(
            "SELECT COUNT(*) FROM run WHERE perf_file IS NULL").fetchone()
        self.assertEqual(remaining, 1, "only the genuinely perf-less run stays")


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
        self.assertEqual(counts["runs"], 3, "a bad curve must not lose the run")
        self.assertEqual(counts["curves"], 1)

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


if __name__ == "__main__":
    unittest.main()
