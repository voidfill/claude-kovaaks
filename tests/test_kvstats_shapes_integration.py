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
