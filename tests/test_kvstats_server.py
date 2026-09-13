"""API payload shape. The HTTP plumbing is exercised through one live request."""

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
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

    def test_race_marks_are_named_after_the_bots_that_hold_them(self):
        """A boundary is only meaningful if you can tell which bot it ends.
        The names come from the same rows the split table uses, so the chart
        and the table can never disagree about which bot is which."""
        payload = server.build_run_payload(self.conn, self.run_for("Air Pure Medium"))
        self.assertEqual(payload["marks"]["labels"],
                         ["AIR1_Short_close", "AIR1_Short_far", "AIR2_Long3D_mid",
                          "AIR2_Short_close", "AIR2_Mid_UFO"])
        self.assertEqual(payload["marks"]["labels"],
                         [s["bot"] for s in payload["splits"] if s["idx"] is not None])

    def test_a_timed_run_carries_no_mark_labels(self):
        """Timed marks are the focused run's own kills, drawn subdued and
        unlabelled -- there is no shared boundary for a name to describe."""
        payload = server.build_run_payload(
            self.conn, self.run_for("Air Voltaic Invincible 4 Medium"))
        self.assertEqual(payload["marks"]["labels"], [])

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

    def test_split_deltas_are_offered_against_the_run_as_well_as_the_pb(self):
        """Which bot to work on is not the same question as which bot is hard.

        This run is 1.57 s per bot behind its baseline on average. Against the
        mean the picture is one bot, not five: AIR2_Mid_UFO is +6.70 over the
        run's own shortfall and every other bot comes in under it. The plain
        delta cannot say that -- it only reports that four bots were near the
        baseline and one was 8 s off, leaving the reader to do the subtraction.
        """
        payload = server.build_run_payload(self.conn, self.run_for("Air Pure Medium"))
        bots = [s for s in payload["splits"] if s["idx"] is not None]
        adjusted = {s["bot"]: s["delta_adj"] for s in bots}
        self.assertAlmostEqual(adjusted["AIR2_Mid_UFO"], 6.697, places=2)
        self.assertAlmostEqual(adjusted["AIR2_Long3D_mid"], -2.674, places=2)
        self.assertEqual(max(adjusted, key=adjusted.get), "AIR2_Mid_UFO")
        # Zero-sum is what makes the column readable as "against the rest of
        # this run" rather than as a second, differently-scaled delta.
        self.assertAlmostEqual(sum(adjusted.values()), 0.0, places=6)
        self.assertEqual(len([v for v in adjusted.values() if v > 0]), 1)

        dead = [s for s in payload["splits"] if s["idx"] is None][0]
        self.assertIsNone(dead["delta_adj"], "dead time is not a bot to work on")

    def test_split_deltas_are_absent_without_a_baseline(self):
        """Air Spectral Easy is fixtured with no .perf, so it has no charted
        PB -- there is nothing to measure against and nothing to take a mean
        of. The splits still stand on their own."""
        run_id = self.conn.execute(
            "SELECT id FROM run WHERE scenario='Air Spectral Easy' "
            "ORDER BY started_at LIMIT 1").fetchone()[0]
        payload = server.build_run_payload(self.conn, run_id)
        self.assertEqual(len(payload["splits"]), 7)
        for split in payload["splits"]:
            self.assertIsNone(split["delta"])
            self.assertIsNone(split["delta_adj"])

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

        # The curve is missing, but the splits come from the CSV kill rows,
        # not the curve, so they must still be there and still reconcile.
        bots = [s for s in payload["splits"] if s["idx"] is not None]
        dead = [s for s in payload["splits"] if s["idx"] is None]
        self.assertEqual(len(bots), 6)
        self.assertEqual(len(dead), 1)
        self.assertAlmostEqual(dead[0]["mine"], 0.059, places=3)
        total = sum(s["mine"] for s in payload["splits"])
        self.assertAlmostEqual(total, payload["run"]["elapsed_s"], places=6)
        budget = payload["scenario"]["budget"]
        self.assertAlmostEqual(total, budget - payload["run"]["score"], places=1)

    def test_delta_baseline_names_the_pb_the_delta_is_measured_against(self):
        """delta.baseline must always name the same PB that baselines.pb does,
        on both shapes -- the UI cannot be left free to label the chart with
        one baseline and the headline percentage with another."""
        race = server.build_run_payload(self.conn, self.run_for("Air Pure Medium"))
        pb = race["baselines"]["pb"]
        self.assertIsNotNone(pb)
        self.assertEqual(race["delta"]["baseline"], {
            "run_id": pb["run_id"], "score": pb["score"],
            "is_true_pb": pb["is_true_pb"]})

        # Pasu Voltaic Reload Easier is fixtured as a single run, so it is the
        # coverage for "no PB curve backs the delta" -- the exact case the
        # field must fall back to None for.
        alone = server.build_run_payload(
            self.conn, self.run_for("Pasu Voltaic Reload Easier"))
        self.assertIsNone(alone["baselines"]["pb"])
        self.assertIsNone(alone["delta"]["baseline"])

    def test_efficiency_is_withheld_where_damage_is_only_booked_at_kill_time(self):
        """VT Ground Intermediate S5 books damage at kill time: its whole-run
        dmg_possible is 6.0 against 6001 shots, so a per-second ratio is a flat
        zero pretending to be a measurement."""
        booked_at_kill = server.build_run_payload(
            self.conn, self.run_for("VT Ground Intermediate S5"))
        self.assertNotIn("efficiency", booked_at_kill["metrics"])
        per_tick = server.build_run_payload(
            self.conn, self.run_for("VT 1w2ts Horizontal Small"))
        self.assertIn("efficiency", per_tick["metrics"])
        self.assertIn("accuracy", per_tick["metrics"])

    def test_a_race_run_offers_no_metric_buttons(self):
        """A race is plotted in damage/s whatever `metric` says, so every one
        of the six buttons would redraw the same line. The payload has to say
        so, because the UI offers exactly what `metrics` lists."""
        race = server.build_run_payload(self.conn, self.run_for("Air Pure Medium"))
        self.assertEqual(race["metrics"], [])
        self.assertEqual(race["rate"]["metric"], "damage")

        # The proof that the buttons were dead: two different metrics, one
        # series. `metric` still has to be a METRICS key even so -- the UI
        # carries the timed run's choice across, and an unknown one would 400
        # every later request for this run.
        as_accuracy = server.build_run_payload(
            self.conn, self.run_for("Air Pure Medium"), metric="accuracy")
        self.assertEqual(as_accuracy["rate"]["mine"], race["rate"]["mine"])
        self.assertEqual(as_accuracy["metrics"], [])
        with self.assertRaises(ValueError):
            server.build_run_payload(
                self.conn, self.run_for("Air Pure Medium"), metric="damage")

    def test_a_timed_run_with_a_charted_baseline_gets_a_delta_and_a_band(self):
        """VT Ground Intermediate S5 is the one fixture scenario with two
        .perf-backed runs, so it is the only coverage for _fill_timed's
        pb/delta/band branch -- the most travelled branch in the file.

        The delta's final value must be the score difference exactly: that is
        the invariant the chart's endpoint and the headline number share.
        """
        newer = self.run_for("VT Ground Intermediate S5", "DESC")
        older = self.run_for("VT Ground Intermediate S5", "ASC")
        payload = server.build_run_payload(self.conn, newer)

        self.assertEqual(payload["axis"]["kind"], "time")
        self.assertEqual(len(payload["rate"]["pb"]), payload["run"]["buckets"])
        self.assertEqual(payload["delta"]["compare_until"], payload["run"]["buckets"])

        scores = dict(self.conn.execute(
            "SELECT id, score FROM run WHERE id IN (?,?)", (newer, older)))
        self.assertAlmostEqual(payload["delta"]["final"],
                               scores[newer] - scores[older], places=6)
        self.assertEqual(payload["delta"]["values"][-1], payload["delta"]["final"])
        self.assertEqual(payload["delta"]["baseline"],
                         {"run_id": older, "score": scores[older], "is_true_pb": True})

        band = payload["rate"]["band"]
        self.assertIsNotNone(band)
        for series in ("mean", "lo", "hi"):
            self.assertEqual(len(band[series]), payload["run"]["buckets"])


class RunList(ServerBase):
    """`/api/runs` is the rail's whole world. It has to arrive already marked:
    the client cannot work out how a run stood against its history from a page
    that does not contain that history."""

    def test_every_row_says_how_it_stood_against_its_own_history(self):
        rows = json.loads(json.dumps(server.run_list(self.conn, limit=50)))
        self.assertEqual(len(rows), 11)
        for row in rows:
            self.assertIn("best_before", row)
            self.assertIn("played_before", row)

        # VT Ground Intermediate S5 is played twice in the fixtures, 1814 then
        # 2009 -- so the later run has something to have beaten.
        ground = [r for r in rows if r["scenario"] == "VT Ground Intermediate S5"]
        ground.sort(key=lambda r: r["started_at"])
        self.assertEqual([r["played_before"] for r in ground], [0, 1])
        self.assertIsNone(ground[0]["best_before"])
        self.assertEqual(ground[1]["best_before"], 1814.0)

    def test_the_rows_carry_what_the_rail_draws_with(self):
        """buckets marks the runs with no curve and shape picks the row's icon;
        both were already in the payload and must survive the rewrite."""
        rows = server.run_list(self.conn, limit=50)
        by_id = {r["id"]: r for r in rows}
        curved = self.conn.execute(
            "SELECT run_id, buckets FROM curve LIMIT 1").fetchone()
        self.assertEqual(by_id[curved[0]]["buckets"], curved[1])
        flat = self.conn.execute(
            "SELECT id FROM run WHERE perf_file IS NULL LIMIT 1").fetchone()[0]
        self.assertIsNone(by_id[flat]["buckets"])
        self.assertEqual(by_id[curved[0]]["shape"],
                         self.conn.execute(
                             "SELECT shape FROM scenario WHERE name=?",
                             (by_id[curved[0]]["scenario"],)).fetchone()[0])

    def test_the_rail_can_ask_for_the_runs_older_than_the_one_it_has(self):
        """How the rail loads more as you scroll. The cursor is a run id, so
        the client hands back the last row it drew rather than an offset that
        shifts under it when a run lands mid-scroll."""
        first = server.run_list(self.conn, limit=4)
        older = server.run_list(self.conn, limit=4, before=first[-1]["id"])

        self.assertEqual(len(first), 4)
        self.assertTrue(older, "11 fixture runs, so there is a second page")
        self.assertFalse({r["id"] for r in first} & {r["id"] for r in older},
                         "the pages must not overlap")
        self.assertLess(older[0]["started_at"], first[-1]["started_at"])

        rest = server.run_list(self.conn, limit=50, before=older[-1]["id"])
        self.assertEqual(len(first) + len(older) + len(rest), 11,
                         "and together they must be the whole history")

    def test_a_bad_cursor_is_refused_rather_than_handed_to_sqlite(self):
        """`limit` is already bounds-checked because a negative one means `no
        limit` to SQLite and a huge one overflows it. `before` reaches the same
        query, so it gets the same treatment rather than a 500 from the handler
        thread."""
        httpd = server.make_server(self.cfg, self.conn, port=0)
        self.addCleanup(httpd.shutdown)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        port = httpd.server_address[1]

        for bad in ("abc", "-1", "99999999999999999999999"):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/runs?before={bad}", timeout=5)
            self.assertEqual(caught.exception.code, 400, f"before={bad}")

        # an id that is simply not there is a valid question with an empty
        # answer, not a bad request
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/runs?before=999999", timeout=5) as r:
            self.assertEqual(json.loads(r.read()), [])

    def test_the_route_serves_the_marks_and_honours_the_sens_toggle(self):
        """The rail's marks must follow the same cm/360 switch the headline
        follows, or flipping it changes one panel and not the other."""
        self.conn.execute(
            "INSERT INTO run(scenario, started_at, stats_file, score, cfg_key, "
            "duration_s, shots, hits, misses) "
            "VALUES('VT Ground Intermediate S5', '2026-06-16T20:50:00', "
            "       'synthetic', 2500, '99.9', 59.99, 0, 0, 0)")
        self.conn.commit()

        httpd = server.make_server(self.cfg, self.conn, port=0)
        self.addCleanup(httpd.shutdown)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        port = httpd.server_address[1]

        def fetch(query):
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/runs?{query}", timeout=5) as r:
                return {row["id"]: row for row in json.loads(r.read())}

        new_id = self.conn.execute(
            "SELECT id FROM run WHERE stats_file='synthetic'").fetchone()[0]

        strict = fetch("limit=50")[new_id]
        self.assertIsNone(strict["best_before"],
                          "2009 was set at another sensitivity")
        self.assertEqual(strict["played_before"], 0)

        relaxed = fetch("limit=50&same_cfg=0")[new_id]
        self.assertEqual(relaxed["best_before"], 2009.0)
        self.assertEqual(relaxed["played_before"], 2)

    def test_one_scenario_can_be_asked_for_on_its_own(self):
        rows = server.run_list(self.conn, limit=50, scenario="Air Pure Medium")
        self.assertEqual({r["scenario"] for r in rows}, {"Air Pure Medium"})
        self.assertEqual(len(rows), 2)


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
        self.assertEqual(body["runs"], 11)
        self.assertEqual(body["curves"], 7)
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
