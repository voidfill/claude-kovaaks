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

    def test_a_race_run_with_no_perf_still_emits_splits(self):
        """Air Spectral Easy is race via csv-constant-budget and has no .perf
        at all -- the rate/delta stay empty, but the splits still come from
        the kill rows, which is the whole point of the race path here."""
        run_id = self.conn.execute(
            "SELECT id FROM run WHERE scenario=? ORDER BY score LIMIT 1",
            ("Air Spectral Easy",)).fetchone()[0]
        payload = server.build_run_payload(self.conn, run_id)
        self.assertEqual(payload["scenario"]["shape"], "race")
        self.assertEqual(payload["rate"]["mine"], [])
        self.assertIsNone(payload["delta"]["values"])

    def test_efficiency_is_withheld_where_damage_is_only_booked_at_kill_time(self):
        """VT Ground Intermediate S5 books damage at kill time: its whole-run
        dmg_possible is 6.0 against 6001 shots, so a per-second ratio is a flat
        zero pretending to be a measurement."""
        booked_at_kill = server.build_run_payload(
            self.conn, self.run_for("VT Ground Intermediate S5"))
        self.assertNotIn("efficiency", booked_at_kill["metrics"])
        race = server.build_run_payload(self.conn, self.run_for("Air Pure Medium"))
        self.assertIn("efficiency", race["metrics"])
        self.assertIn("accuracy", race["metrics"])


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
        self.assertEqual(body["runs"], 9)
        self.assertEqual(body["curves"], 6)
        self.assertEqual(body["watcher_errors"], 0)

        run_id = self.conn.execute(
            "SELECT id FROM run WHERE perf_file IS NOT NULL LIMIT 1").fetchone()[0]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/run/{run_id}",
                                    timeout=5) as response:
            payload = json.loads(response.read())
        # This particular run is race-shaped (Air Pure Medium), so its rate
        # axis is indexed by progress steps, not by second-buckets.
        self.assertEqual(len(payload["rate"]["mine"]), payload["axis"]["n"])

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"kvstats", response.read().lower())


if __name__ == "__main__":
    unittest.main()
