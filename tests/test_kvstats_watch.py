"""The watcher: cheap tier, expensive tier, and late-arriving perf files."""

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from kvstats import index, paths, watch  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "kvstats")
TRACKING = "Air Voltaic Invincible 4 Medium - Challenge - 2026.09.10-17.17.33"


class WatchBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        os.makedirs(os.path.join(self.dir, "stats"))
        os.makedirs(os.path.join(self.dir, "performances"))
        self.cfg = paths.load({"KOVAAKS_DIR": self.dir,
                               "KVSTATS_DB": os.path.join(self.dir, "i.sqlite3")})
        self.conn = index.connect(self.cfg.db_path)
        self.addCleanup(self.conn.close)
        self.watcher = watch.Watcher(self.cfg, self.conn, tick=0.01,
                                     full_scan_every=1000)

    def drop(self, stem, with_perf=True):
        shutil.copy(os.path.join(FIXTURES, "stats", f"{stem} Stats.csv"),
                    os.path.join(self.dir, "stats"))
        if with_perf:
            shutil.copy(
                os.path.join(FIXTURES, "performances", f"{stem} Performance.perf"),
                os.path.join(self.dir, "performances"))


class Polling(WatchBase):
    def test_a_new_run_is_detected_once_and_quiet_ticks_do_not_scan(self):
        """The two-tier poll's whole point: cheap when idle, exactly-once when not."""
        self.watcher.poll_once()          # first tick establishes the baseline
        before = self.watcher.stats["scans"]
        for _ in range(5):
            self.watcher.poll_once()
        self.assertEqual(self.watcher.stats["scans"], before,
                         "an unchanged directory must not trigger a scandir")

        seen = []
        self.watcher.on_run = seen.append
        self.drop(TRACKING)
        new = self.watcher.poll_once()
        self.assertEqual(len(new), 1)
        scenario, = self.conn.execute("SELECT scenario FROM run WHERE id=?",
                                      (new[0],)).fetchone()
        self.assertEqual(scenario, "Air Voltaic Invincible 4 Medium")

        self.assertEqual(self.watcher.poll_once(), [], "no run is reported twice")
        self.assertEqual(len(seen), 1, "the callback fires once per run")


class LateArrivingPerf(WatchBase):
    def test_the_run_lands_first_and_the_curve_attaches_on_a_later_tick(self):
        """KovaaK's writes the CSV and the `.perf` separately, so the curve is
        routinely a tick or more behind. The run must never wait for it.
        """
        self.watcher.poll_once()
        self.drop(TRACKING, with_perf=False)
        run_id = self.watcher.poll_once()[0]
        perf_file, = self.conn.execute("SELECT perf_file FROM run WHERE id=?",
                                       (run_id,)).fetchone()
        self.assertIsNone(perf_file, "the run must not wait for its curve")
        self.assertIn(run_id, self.watcher.stats["awaiting_perf"])

        shutil.copy(
            os.path.join(FIXTURES, "performances", f"{TRACKING} Performance.perf"),
            os.path.join(self.dir, "performances"))
        self.watcher.poll_once()
        perf_file, duration = self.conn.execute(
            "SELECT perf_file, duration_s FROM run WHERE id=?", (run_id,)).fetchone()
        self.assertIsNotNone(perf_file)
        self.assertGreater(duration, 55)
        self.assertNotIn(run_id, self.watcher.stats["awaiting_perf"])

    def test_a_bad_perf_stops_being_retried_after_max_tries(self):
        """Otherwise a corrupt file is re-parsed on every 100 ms tick for 60 s."""
        self.watcher.poll_once()
        self.drop(TRACKING, with_perf=False)
        run_id = self.watcher.poll_once()[0]
        target = os.path.join(self.dir, "performances",
                              f"{TRACKING} Performance.perf")
        with open(target, "wb") as handle:
            handle.write(b"not protobuf")

        for _ in range(index.MAX_TRIES + 10):
            self.watcher.poll_once()

        self.assertNotIn(run_id, self.watcher.stats["awaiting_perf"])
        tries, = self.conn.execute(
            "SELECT tries FROM failed WHERE path=?", (target,)).fetchone()
        self.assertEqual(tries, index.MAX_TRIES)


class RunForever(WatchBase):
    def test_loop_starts_and_stops_cleanly(self):
        """Kept because this is the only test that drives the watcher from a real
        thread -- the shape of bug that made every /api/ call 500 in review.
        """
        stop = threading.Event()
        seen = []
        self.watcher.on_run = seen.append
        thread = threading.Thread(target=self.watcher.run_forever, args=(stop,))
        thread.start()
        time.sleep(0.05)
        self.drop(TRACKING)
        deadline = time.time() + 3
        while not seen and time.time() < deadline:
            time.sleep(0.01)
        stop.set()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(seen), 1)
        self.assertEqual(self.watcher.stats["errors"], 0,
                         "a swallowed exception here is invisible in production")


if __name__ == "__main__":
    unittest.main()
