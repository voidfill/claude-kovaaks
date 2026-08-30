"""The six tests that matter: encodings, byte-exact writing, the ownership gate,
cloning, the forced hasOfflineScenarios flag, and the session-length estimate. Everything runs against fixtures in a temp directory, never the real
KovaaK's install."""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", ".claude", "skills", "kovaaks-playlists", "scripts"))
import kvpl  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
STOCK = os.path.join(FIXTURES, "stock-utf8.json")


def run(*argv):
    """Run the CLI against the temp install; return (exit_code, parsed_stdout)."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(io.StringIO()):
        code = kvpl.main(list(argv))
    text = buffer.getvalue()
    return code, (json.loads(text) if text.strip() else None)


class ReadEncodings(unittest.TestCase):
    def test_all_encodings_parse_identically(self):
        with open(STOCK, "rb") as handle:
            raw = handle.read()
        expected = json.loads(raw.decode("utf-8"))

        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        variants = {
            "utf8.json": raw,
            "utf8bom.json": b"\xef\xbb\xbf" + raw,
            # the game's UTF-16 playlists are little-endian and always carry a BOM
            "utf16le.json": b"\xff\xfe" + raw.decode("utf-8").encode("utf-16-le"),
        }
        for name, blob in variants.items():
            path = os.path.join(directory, name)
            with open(path, "wb") as handle:
                handle.write(blob)
            self.assertEqual(json.loads(kvpl.read_text(path)), expected, name)


class WriteFidelity(unittest.TestCase):
    def test_stock_playlist_round_trips_byte_for_byte(self):
        with open(STOCK, "rb") as handle:
            raw = handle.read()
        self.assertEqual(kvpl.dump_bytes(json.loads(raw.decode("utf-8"))), raw)


class InstallBase(unittest.TestCase):
    """A throwaway KovaaK's tree with one stock (unmanaged) playlist in it."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.playlists = os.path.join(self.root, "Saved", "SaveGames", "Playlists")
        os.makedirs(self.playlists)
        os.makedirs(os.path.join(self.root, "Saved", "SaveGames", "Scenarios"))
        shutil.copy(STOCK, os.path.join(self.playlists, "1 - Basic.json"))

        for key, value in {
            "KOVAAKS_DIR": self.root,
            "KOVAAKS_WORKSHOP_DIR": os.path.join(self.root, "workshop"),
            "KOVAAKS_STEAM_ID": "76561190000000000",
            "KOVAAKS_AUTHOR_NAME": "tester",
        }.items():
            previous = os.environ.get(key)
            os.environ[key] = value
            self.addCleanup(self._restore, key, previous)

    @staticmethod
    def _restore(key, previous):
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


class OwnershipGate(InstallBase):
    def test_refuses_unmanaged_and_allows_managed(self):
        code, _ = run("add", "1 - Basic", "--scenario", "Anything")
        self.assertEqual(code, kvpl.EXIT_OWNERSHIP)
        # the refusal must not have touched the file
        with open(STOCK, "rb") as handle:
            original = handle.read()
        with open(os.path.join(self.playlists, "1 - Basic.json"), "rb") as handle:
            self.assertEqual(handle.read(), original)

        self.assertEqual(run("create", "Mine")[0], 0)
        self.assertEqual(run("add", "Mine", "--scenario", "Anything")[0], 0)
        self.assertEqual(run("delete", "1 - Basic")[0], kvpl.EXIT_OWNERSHIP)
        self.assertEqual(run("delete", "Mine")[0], 0)


class CloneEscapeHatch(InstallBase):
    def test_from_playlist_yields_a_managed_copy(self):
        code, _ = run("create", "My Copy", "--from-playlist", "1 - Basic")
        self.assertEqual(code, 0)

        _, source = run("show", "1 - Basic")
        _, clone = run("show", "My Copy")
        self.assertFalse(source["managed"])
        self.assertTrue(clone["managed"])
        self.assertTrue(clone["author"].endswith(kvpl.AUTHOR_TAG))
        self.assertEqual([(s["name"], s["playCount"]) for s in clone["scenarios"]],
                         [(s["name"], s["playCount"]) for s in source["scenarios"]])


class OfflineFlag(InstallBase):
    """The game errors on playlists that claim offline scenarios, so it is always false."""

    def _flag(self, name):
        path = os.path.join(self.playlists, name + ".json")
        with open(path, "rb") as handle:
            return json.loads(handle.read().decode("utf-8"))["hasOfflineScenarios"]

    def test_false_on_create_and_after_every_edit(self):
        # "Nothing Owns This" is not in the (empty) installed index
        self.assertEqual(run("create", "Mine", "--scenario", "Nothing Owns This:2")[0], 0)
        self.assertIs(self._flag("Mine"), False)

        for argv in (("add", "Mine", "--scenario", "Also Missing"),
                     ("set-count", "Mine", "--scenario", "Also Missing", "--count", "3"),
                     ("move", "Mine", "--from", "0", "--to", "1"),
                     ("set-description", "Mine", "--description", "x"),
                     ("remove", "Mine", "--index", "0")):
            self.assertEqual(run(*argv)[0], 0, argv)
            self.assertIs(self._flag("Mine"), False, argv)

    def test_false_on_clone_of_a_stock_playlist(self):
        # the stock fixture references scenarios this install does not have
        self.assertEqual(run("create", "My Copy", "--from-playlist", "1 - Basic")[0], 0)
        self.assertIs(self._flag("My Copy"), False)


class SessionEstimate(InstallBase):
    """runs x 1.0 play + entries x 1.0 load + (runs - entries) x 0.5 restart."""

    def test_formula(self):
        def est(*counts):
            return kvpl.estimate_minutes(
                [{"scenario_name": "s%d" % i, "play_Count": c} for i, c in enumerate(counts)])

        self.assertEqual(est(), 0)
        self.assertEqual(est(1), 2.0)                 # 1 + 1 + 0
        self.assertEqual(est(4), 6.5)                 # 4 + 1 + 1.5
        self.assertEqual(est(1, 1, 1), 6.0)           # 3 + 3 + 0
        # the shape of VDIM Intermediate S5 - Tracking I: 30 entries, 75 runs
        counts = [3, 2, 2, 2, 2, 2, 3, 3, 3, 4, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3,
                  4, 2, 2, 3, 2, 2, 3, 3, 3, 4]
        self.assertEqual((len(counts), sum(counts)), (30, 75))
        self.assertEqual(est(*counts), 127.5)         # 75 + 30 + 22.5

    def test_missing_or_bogus_play_count_counts_as_one_run(self):
        self.assertEqual(kvpl.estimate_minutes([{"scenario_name": "s"}]), 2.0)
        self.assertEqual(kvpl.estimate_minutes([{"scenario_name": "s", "play_Count": 0}]), 2.0)

    def test_commands_report_it(self):
        self.assertEqual(run("create", "Mine", "--scenario", "A:4")[1]["estimatedMinutes"], 6.5)
        self.assertEqual(run("show", "Mine")[1]["estimatedMinutes"], 6.5)
        self.assertEqual(run("add", "Mine", "--scenario", "B")[1]["estimatedMinutes"], 8.5)
        self.assertEqual(run("set-count", "Mine", "--scenario", "B", "--count", "3")[1]
                         ["estimatedMinutes"], 11.5)
        rows = {r["name"]: r for r in run("list")[1]["playlists"]}
        self.assertEqual(rows["Mine"]["estimatedMinutes"], 11.5)
        self.assertEqual(run("remove", "Mine", "--scenario", "B")[1]["estimatedMinutes"], 6.5)


if __name__ == "__main__":
    unittest.main()
