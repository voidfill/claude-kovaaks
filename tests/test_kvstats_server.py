"""API payload shape. The HTTP plumbing is exercised through one live request."""

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from kvstats import index, paths, server  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "kvstats")


class ServerBase(unittest.TestCase):
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


class RunPayload(ServerBase):
    def run_id_with_curve(self):
        return self.conn.execute(
            "SELECT id FROM run WHERE perf_file IS NOT NULL LIMIT 1").fetchone()[0]

    def test_payload_is_serialisable_and_carries_run_curve_and_baselines(self):
        payload = server.build_run_payload(self.conn, self.run_id_with_curve())
        json.dumps(payload)   # arrays must already be plain lists, not array.array
        self.assertIn("run", payload)
        self.assertIn("curve", payload)
        self.assertIn("baselines", payload)
        self.assertEqual(len(payload["curve"]), payload["run"]["buckets"])

        # a curveless run is a normal case, not an error
        curveless = self.conn.execute(
            "SELECT id FROM run WHERE perf_file IS NULL").fetchone()[0]
        payload = server.build_run_payload(self.conn, curveless)
        self.assertEqual(payload["curve"], [])
        self.assertIsNone(payload["cumulative_delta"])

    def test_the_delta_is_raw_score_units_whatever_the_view_controls_say(self):
        """The chart controls must not be able to change the headline number.

        Smoothing is a display choice, and a running sum of per-second accuracy
        differences is meaningless -- so the delta is computed on raw score in
        both cases, however the curve above it is drawn.
        """
        run_id = self.run_id_with_curve()
        raw = server.build_run_payload(self.conn, run_id, smoothing=1)
        smoothed = server.build_run_payload(self.conn, run_id, smoothing=5)
        self.assertEqual(len(raw["curve"]), len(smoothed["curve"]))
        self.assertEqual(raw["cumulative_delta"], smoothed["cumulative_delta"])

        by_accuracy = server.build_run_payload(self.conn, run_id, metric="accuracy")
        self.assertNotEqual(raw["curve"], by_accuracy["curve"])
        self.assertEqual(raw["cumulative_delta"], by_accuracy["cumulative_delta"])


class LiveServer(ServerBase):
    def test_the_api_answers_over_real_http_from_a_server_thread(self):
        """The one test that exercises the real concurrency path.

        Every pure-payload test above calls `build_run_payload` on the main
        thread. In review, a `sqlite3.connect` without `check_same_thread=False`
        passed all of those and still made every `/api/*` request 500 from the
        handler thread -- only `GET /` worked. This test is what closes that gap,
        so it must hit a JSON endpoint, not just the static shell.
        """
        httpd = server.make_server(self.cfg, self.conn, port=0)
        self.addCleanup(httpd.shutdown)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        port = httpd.server_address[1]

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health",
                                    timeout=5) as response:
            body = json.loads(response.read())
        self.assertEqual(body["runs"], 3)
        self.assertEqual(body["curves"], 2)
        self.assertEqual(body["watcher_errors"], 0)

        run_id = self.conn.execute(
            "SELECT id FROM run WHERE perf_file IS NOT NULL LIMIT 1").fetchone()[0]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/run/{run_id}",
                                    timeout=5) as response:
            payload = json.loads(response.read())
        self.assertEqual(len(payload["curve"]), payload["run"]["buckets"])

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"kvstats", response.read().lower())


if __name__ == "__main__":
    unittest.main()
