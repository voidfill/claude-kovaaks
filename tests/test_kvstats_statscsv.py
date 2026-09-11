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


if __name__ == "__main__":
    unittest.main()
