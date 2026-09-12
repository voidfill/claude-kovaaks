"""Scenario shape classification.

A misfire here is the worst failure in this feature: it puts a fixed-clock
scenario on a progress axis, or leaves a race scenario charting a countdown
timer as a rate. The thresholds below are calibrated against 2050 real .perf
files, where this rule scores 126/126 with zero false positives.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from kvstats import shapes  # noqa: E402

# Air Pure Medium: the score series IS a clock.
COUNTDOWN = [999.0] + [-1.0] * 90 + [-0.4]
# VT Ground Intermediate S5: score accrues per second.
TIMED_CURVE = [62.0, 46.0, 55.0, 40.0, 45.0, 57.0, 54.0, 27.0, 22.0, 93.0]
# VT 1w2ts Horizontal Small: +10 a kill, about -4 a miss.
PENALISING_CURVE = [10.0, 10.0, 20.0, 5.6, 9.9, 19.9, -4.0, 6.2, 19.7]


class Countdown(unittest.TestCase):
    def test_a_clock_yields_its_budget(self):
        self.assertAlmostEqual(shapes.countdown_budget(COUNTDOWN), 1000.0, places=6)

    def test_bucket_collisions_do_not_break_detection(self):
        """perf.py buckets by floor(timestamp), so jitter occasionally writes a
        0 next to a -2. A strict all-buckets rule misses 3 of 126 real runs;
        the >=90% rule misses none. This is the reason the threshold exists."""
        jittered = list(COUNTDOWN)
        jittered[67], jittered[68] = 0.0, -1.998
        self.assertAlmostEqual(shapes.countdown_budget(jittered), 1000.0, places=6)

    def test_float32_drift_is_tolerated(self):
        """The series round-trips through float32, so -1 arrives as -1.00092."""
        drifted = [999.0] + [-1.00092, -0.99908] * 45 + [-0.29]
        self.assertAlmostEqual(shapes.countdown_budget(drifted), 1000.0, places=6)

    def test_a_scoring_curve_is_not_a_clock(self):
        self.assertIsNone(shapes.countdown_budget(TIMED_CURVE))
        self.assertIsNone(shapes.countdown_budget(PENALISING_CURVE))

    def test_degenerate_input_is_rejected_rather_than_guessed(self):
        self.assertIsNone(shapes.countdown_budget([]))
        self.assertIsNone(shapes.countdown_budget([999.0]))
        self.assertIsNone(shapes.countdown_budget([-1.0, -1.0, -1.0, -1.0]))


class CsvFallback(unittest.TestCase):
    def test_constant_budget_over_varying_time_is_a_race(self):
        """Real Air Spectral Easy runs, whose .perf files are absent."""
        got = shapes.budget_from_totals([(927.416626, 72.567), (914.885254, 85.093)])
        self.assertAlmostEqual(got, 999.98, places=1)

    def test_one_run_is_never_enough(self):
        """6 of 2360 fixed-clock runs land on a round hundred by coincidence.
        A single run is a coincidence; two runs agreeing is evidence."""
        self.assertIsNone(shapes.budget_from_totals([(940.0, 60.0)]))

    def test_a_fixed_clock_is_not_a_race(self):
        """Score varies, elapsed does not -- so the budget is not constant."""
        self.assertIsNone(shapes.budget_from_totals([(840.67, 59.40), (727.25, 59.43)]))


class Classify(unittest.TestCase):
    def test_a_countdown_curve_wins_over_the_csv_fallback(self):
        got = shapes.classify([TIMED_CURVE, COUNTDOWN], [(906.1, 93.8)])
        self.assertEqual(got["shape"], shapes.RACE)
        self.assertEqual(got["evidence"], "perf-countdown")
        self.assertAlmostEqual(got["budget"], 1000.0, places=6)

    def test_the_fallback_runs_only_when_no_curve_settles_it(self):
        got = shapes.classify([], [(927.416626, 72.567), (914.885254, 85.093)])
        self.assertEqual(got["shape"], shapes.RACE)
        self.assertEqual(got["evidence"], "csv-constant-budget")

    def test_a_fixed_clock_scenario_is_never_classified_race(self):
        got = shapes.classify([TIMED_CURVE], [(2977.0, 59.99), (2570.0, 59.99)])
        self.assertEqual(got["shape"], shapes.TIMED)
        self.assertEqual(got["evidence"], "default")
        self.assertIsNone(got["budget"])


class Penalising(unittest.TestCase):
    def test_a_negative_bucket_is_a_penalty(self):
        self.assertTrue(shapes.is_penalising(PENALISING_CURVE))
        self.assertFalse(shapes.is_penalising(TIMED_CURVE))
