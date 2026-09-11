"""Locating the KovaaK's output directories.

kvstats only ever reads the game's files. The SQLite index deliberately lives
outside the install so nothing kvstats writes can be mistaken for game data.
"""

import os

EXIT_INPUT = 2
EXIT_CONFIG = 4

DEFAULT_ROOT = (
    r"C:\Program Files (x86)\Steam\steamapps\common\FPSAimTrainer\FPSAimTrainer"
)


class Fail(Exception):
    """A user-facing failure carrying the process exit code."""

    def __init__(self, message, code=EXIT_INPUT):
        super().__init__(message)
        self.code = code


class Config:
    __slots__ = ("root", "stats_dir", "perf_dir", "db_path", "playlist_in_progress")

    def __init__(self, root, stats_dir, perf_dir, db_path, playlist_in_progress):
        self.root = root
        self.stats_dir = stats_dir
        self.perf_dir = perf_dir
        self.db_path = db_path
        self.playlist_in_progress = playlist_in_progress


def default_db_path():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "kvstats", "index.sqlite3")


def load(env=None):
    env = os.environ if env is None else env
    root = env.get("KOVAAKS_DIR") or DEFAULT_ROOT
    if not os.path.isdir(root):
        raise Fail(f"KovaaK's directory not found: {root}", EXIT_CONFIG)

    stats_dir = os.path.join(root, "stats")
    perf_dir = os.path.join(root, "performances")
    if not os.path.isdir(stats_dir):
        raise Fail(f"stats directory not found: {stats_dir}", EXIT_CONFIG)
    # performances/ is optional: 318 runs in a real install have no .perf at all,
    # and a fresh install has not created the directory yet.

    return Config(
        root=root,
        stats_dir=stats_dir,
        perf_dir=perf_dir,
        db_path=env.get("KVSTATS_DB") or default_db_path(),
        playlist_in_progress=os.path.join(
            root, "Saved", "SaveGames", "PlaylistInProgress.json"
        ),
    )
