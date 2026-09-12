"""Decoding the undocumented .perf protobuf.

The format is validated by summation: each per-second series must add up to the
total the CSV reports for the same run. That cross-check is the only proof the
field map is right, so it is the centrepiece of this module's tests.
"""

import glob
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from kvstats import perf, statscsv  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "fixtures", "kvstats")
PERF = os.path.join(ROOT, "performances")
STATS = os.path.join(ROOT, "stats")


def pair(fragment):
    perfs = [p for p in glob.glob(os.path.join(PERF, "*.perf")) if fragment in p]
    csvs = [p for p in glob.glob(os.path.join(STATS, "*.csv")) if fragment in p]
    assert len(perfs) == 1 and len(csvs) == 1, fragment
    return perfs[0], csvs[0]


class SumsMatchTheCsv(unittest.TestCase):
    def test_every_series_sums_to_its_csv_total(self):
        """Ground truth for the whole decoder.

        `.perf` is an undocumented protobuf. The only independent check that the
        wire walk reads the right fields with the right types is that each
        series sums to the total the CSV reports for the same run -- across both
        scoring shapes, since tracking and clicking populate different series.
        """
        for fragment in ("Air Voltaic Invincible 4 Medium", "Pasu Voltaic Reload Easier"):
            with self.subTest(run=fragment):
                perf_path, csv_path = pair(fragment)
                curve = perf.parse(perf_path)
                row = statscsv.parse(csv_path)
                self.assertEqual(round(sum(curve["series"]["hits"])), row["hits"])
                self.assertEqual(round(sum(curve["series"]["misses"])), row["misses"])
                self.assertEqual(round(sum(curve["series"]["shots"])), row["shots"])
                self.assertAlmostEqual(sum(curve["series"]["score"]), row["score"], places=1)


class Densification(unittest.TestCase):
    def test_omitted_zero_buckets_are_filled_not_compressed(self):
        """The trap this format sets.

        The clicking run emits fewer hit samples than shot samples. A decoder
        that appends in sample order produces a short hits array and silently
        shifts the curve left -- every later comparison would then be against
        misaligned seconds. Densification must leave real zeros in the gaps, so
        every series is bucket-length and the sparse one genuinely contains zeros.
        """
        curve = perf.parse(pair("Pasu Voltaic Reload Easier")[0])
        lengths = {name: len(values) for name, values in curve["series"].items()}
        self.assertEqual(set(lengths.values()), {curve["buckets"]}, lengths)
        self.assertIn(0.0, list(curve["series"]["hits"]), "sparse series must retain zero buckets")


class Corrupt(unittest.TestCase):
    def test_damaged_files_raise_perf_error(self):
        """The watcher's retry cap depends on this failing loudly, not returning junk."""
        truncated = os.path.join(ROOT, "truncated - Challenge - 2026.01.01-00.00.00 Performance.perf")
        with self.assertRaises(perf.PerfError):
            perf.parse(truncated)

        import tempfile
        handle = tempfile.NamedTemporaryFile(suffix=".perf", delete=False)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        with self.assertRaises(perf.PerfError):
            perf.parse(handle.name)


if __name__ == "__main__":
    unittest.main()
