"""CSV parsing and the cm/360 derivation.

cm/360 matters because `Sens Scale` is not always cm/360 -- a real history has
runs on The FINALS and Overwatch scales, where `Horiz Sens` means something
different. The increment-based formula is scale-independent.
"""

import glob
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from kvstats import statscsv  # noqa: E402

STATS = os.path.join(os.path.dirname(__file__), "fixtures", "kvstats", "stats")


def fixture(fragment):
    matches = [p for p in glob.glob(os.path.join(STATS, "*.csv")) if fragment in p]
    assert len(matches) == 1, f"expected exactly one fixture matching {fragment!r}"
    return matches[0]


class Cm360(unittest.TestCase):
    def test_cm360_is_derived_from_dpi_and_increment_not_from_the_label(self):
        """The one non-obvious fact in this module, so the one test worth keeping.

        A real history has runs on The FINALS and Overwatch scales, where
        `Horiz Sens` means something different -- labels differ by 6.6x for the
        same physical sensitivity. The increment formula is scale-independent,
        so the same sensitivity at two DPIs must agree, and a foreign-scale run
        must still resolve.
        """
        # 1600 DPI, increment 0.272143 -> the run is labelled 30.0 cm/360
        self.assertAlmostEqual(statscsv.cm360(1600, 0.272143), 30.0, places=2)
        # same 30 cm/360 expressed at 400 DPI
        self.assertAlmostEqual(statscsv.cm360(400, 1.088572), 30.0, places=2)
        # The FINALS 33 @ 400 DPI, whose label is in FINALS units
        self.assertAlmostEqual(statscsv.cm360(400, 0.471429), 69.27, places=1)
        # and never divide by zero
        self.assertIsNone(statscsv.cm360(1600, 0))
        self.assertIsNone(statscsv.cm360(None, 0.27))

        # end to end on a real non-cm/360 fixture
        foreign = [
            p for p in glob.glob(os.path.join(STATS, "*.csv"))
            if statscsv.parse(p)["sens_scale"] == "The FINALS"
        ]
        self.assertTrue(foreign, "fixture set must include a non-cm/360 run")
        row = statscsv.parse(foreign[0])
        self.assertIsNotNone(row["cm360"])
        self.assertNotAlmostEqual(row["cm360"], row["sens_raw"], places=1)


class Filename(unittest.TestCase):
    def test_scenario_names_may_contain_the_separator_words(self):
        """Three real scenarios contain ` - `, so a greedy split loses the name."""
        self.assertEqual(
            statscsv.parse_filename(
                "VT Snake Track Intermediate S5 - Challenge - 2026.09.10-17.17.33 Stats.csv"
            ),
            ("VT Snake Track Intermediate S5", "2026-09-10T17:17:33"),
        )
        self.assertEqual(
            statscsv.parse_filename("Challenge - Challenge - 2026.01.02-03.04.05 Stats.csv"),
            ("Challenge", "2026-01-02T03:04:05"),
        )
        self.assertIsNone(statscsv.parse_filename("notes.csv"))


class Parse(unittest.TestCase):
    def test_summary_fields_for_both_scoring_shapes(self):
        """Tracking runs score by hits, clicking runs by kills, and the per-kill
        rows of a clicking CSV must not leak into the summary dict."""
        track = statscsv.parse(fixture("Air Voltaic Invincible 4 Medium"))
        self.assertEqual(track["scenario"], "Air Voltaic Invincible 4 Medium")
        self.assertEqual(track["score"], 3913.0)
        self.assertEqual(track["hits"], 3913)
        self.assertEqual(track["misses"], 2088)
        self.assertEqual(track["shots"], 6001)
        self.assertAlmostEqual(track["accuracy"], 3913 / 6001, places=6)
        self.assertEqual(track["cfg_key"], "30.0")

        click = statscsv.parse(fixture("Pasu Voltaic Reload Easier"))
        self.assertEqual(click["kills"], 52)
        self.assertEqual(click["shots"], 80)
        self.assertEqual(click["score"], 52.0)
        self.assertNotIn("1", click)


class PerKillRows(unittest.TestCase):
    """The per-kill table was discarded in v1. It is the only exact source of
    kill times -- the .perf buckets to whole seconds, which lands kill marks up
    to 0.6 s off."""

    def test_kill_rows_carry_exact_times_and_reconcile_to_fight_time(self):
        kills = statscsv.parse_kills(fixture("Air Pure Medium - Challenge - 2026.09.03"))
        self.assertEqual(len(kills), 5)
        self.assertEqual([k["idx"] for k in kills], [1, 2, 3, 4, 5])
        self.assertEqual([k["bot"] for k in kills],
                         ["AIR1_Short_close", "AIR1_Short_far", "AIR2_Long3D_mid",
                          "AIR2_Short_close", "AIR2_Mid_UFO"])
        self.assertEqual([k["overshots"] for k in kills], [25, 25, 25, 25, 0])
        # sum(TTK) is Fight Time exactly -- the respawn gaps sit outside it
        self.assertAlmostEqual(sum(k["ttk"] for k in kills), 92.808, places=2)
        # t is seconds from Challenge Start, and the last kill ends the run
        self.assertAlmostEqual(kills[0]["t"], 15.735, places=3)
        self.assertAlmostEqual(kills[-1]["t"], 93.849, places=3)

    def test_a_pure_tracking_run_has_no_kill_rows(self):
        """1090 of 2360 real runs are invincible-tracking and never kill
        anything. An empty kill table is the normal case, not an edge case."""
        kills = statscsv.parse_kills(fixture("Air Voltaic Invincible 4 Medium"))
        self.assertEqual(kills, [])

    def test_summary_gains_elapsed_and_the_unsurfaced_counters(self):
        row = statscsv.parse(fixture("Air Pure Medium - Challenge - 2026.09.03"))
        self.assertAlmostEqual(row["elapsed_s"], 93.849, places=3)
        self.assertEqual(row["overshots"], 100)
        self.assertEqual(row["reloads"], 0)
        self.assertEqual(row["damage_taken"], 0.0)
        # the identity the whole race shape rests on
        self.assertAlmostEqual(row["score"] + row["elapsed_s"], 1000.0, places=1)


if __name__ == "__main__":
    unittest.main()
