"""Config resolution: env overrides, and a clear failure when a path is absent."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from kvstats import paths  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "kvstats")


class LoadConfig(unittest.TestCase):
    def test_env_override_points_at_a_fixture_tree(self):
        """Every other kvstats test module resolves its fixtures through this."""
        cfg = paths.load({"KOVAAKS_DIR": FIXTURES})
        self.assertEqual(cfg.root, FIXTURES)
        self.assertEqual(cfg.stats_dir, os.path.join(FIXTURES, "stats"))
        self.assertEqual(cfg.perf_dir, os.path.join(FIXTURES, "performances"))
        self.assertEqual(
            paths.load({"KOVAAKS_DIR": FIXTURES, "KVSTATS_DB": "/tmp/x.db"}).db_path,
            "/tmp/x.db",
        )

    def test_missing_root_fails_with_exit_code_4_naming_the_path(self):
        missing = os.path.join(tempfile.gettempdir(), "kvstats-does-not-exist")
        with self.assertRaises(paths.Fail) as caught:
            paths.load({"KOVAAKS_DIR": missing})
        self.assertEqual(caught.exception.code, paths.EXIT_CONFIG)
        self.assertIn(missing, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
