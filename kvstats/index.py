"""The SQLite index.

This database is a disposable derived cache over files KovaaK's owns. Nothing
lives only here, which is what makes "bump the version, drop everything and
re-bootstrap" a legitimate migration: a full rebuild costs about 21 seconds.
"""

import array
import os
import sqlite3

from . import perf as perfmod
from . import statscsv

SCHEMA_VERSION = 1
MAX_TRIES = 5

SCHEMA = """
CREATE TABLE run (
  id           INTEGER PRIMARY KEY,
  scenario     TEXT NOT NULL,
  started_at   TEXT NOT NULL,
  stats_file   TEXT NOT NULL UNIQUE,
  perf_file    TEXT,
  score REAL, kills INTEGER, hits INTEGER, misses INTEGER, shots INTEGER,
  accuracy REAL, damage_done REAL, damage_possible REAL,
  avg_ttk REAL, fight_time REAL, pause_count INTEGER,
  duration_s REAL, spm REAL,
  hash TEXT, game_version TEXT,
  sens_raw REAL, sens_scale TEXT, dpi INTEGER, sens_increment REAL,
  cm360 REAL, cfg_key TEXT,
  fov REAL, fov_scale TEXT, resolution TEXT, avg_fps REAL
);
CREATE INDEX run_scen_time  ON run(scenario, started_at);
CREATE INDEX run_scen_score ON run(scenario, score DESC);

CREATE TABLE curve (
  run_id  INTEGER PRIMARY KEY REFERENCES run(id) ON DELETE CASCADE,
  buckets INTEGER NOT NULL,
  shots BLOB, hits BLOB, misses BLOB,
  dmg_done BLOB, dmg_possible BLOB, score BLOB, kills BLOB
);

CREATE TABLE failed (
  path TEXT PRIMARY KEY, tries INTEGER NOT NULL, last_error TEXT, last_try TEXT
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""

_RUN_COLUMNS = (
    "scenario", "started_at", "stats_file", "perf_file", "score", "kills", "hits",
    "misses", "shots", "accuracy", "damage_done", "damage_possible", "avg_ttk",
    "fight_time", "pause_count", "duration_s", "spm", "hash", "game_version",
    "sens_raw", "sens_scale", "dpi", "sens_increment", "cm360", "cfg_key",
    "fov", "fov_scale", "resolution", "avg_fps",
)


def _current_version(conn):
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    except sqlite3.OperationalError:
        return None
    return int(row[0]) if row else None


def connect(db_path):
    directory = os.path.dirname(os.path.abspath(db_path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    # check_same_thread=False is REQUIRED, not an optimisation: the watcher
    # runs in its own thread and ThreadingHTTPServer gives every request a new
    # one. Without it every /api/* call and every index write raises
    # ProgrammingError. sqlite3.threadsafety == 3 (serialized) here, and WAL is
    # enabled before any concurrent access, so sharing a connection is safe.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    if _current_version(conn) != SCHEMA_VERSION:
        for table in ("curve", "run", "failed", "meta"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', ?)",
                     (str(SCHEMA_VERSION),))
        conn.commit()
    return conn


def perf_path_for(cfg, stats_path):
    base = os.path.basename(stats_path)
    if not base.endswith(" Stats.csv"):
        return None
    stem = base[: -len(" Stats.csv")]
    return os.path.join(cfg.perf_dir, f"{stem} Performance.perf")


def indexed_paths(conn):
    return {row[0] for row in conn.execute("SELECT stats_file FROM run")}


def record_failure(conn, path, error):
    import datetime
    now = datetime.datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO failed(path, tries, last_error, last_try) VALUES(?,1,?,?) "
        "ON CONFLICT(path) DO UPDATE SET "
        "  tries=MIN(tries+1, ?), last_error=excluded.last_error, "
        "  last_try=excluded.last_try",
        (path, str(error), now, MAX_TRIES),
    )
    conn.commit()
    row = conn.execute("SELECT tries FROM failed WHERE path=?", (path,)).fetchone()
    return row[0]


def index_stats_file(conn, path, commit=True):
    """Insert one run from its CSV. Returns the run id, or None if unparseable.

    `commit=False` is for bootstrap: one fsync per file turns a 3.6 s scan of a
    real 4300-file corpus into 68 s.
    """
    row = statscsv.parse(path)
    if not row.get("scenario") or not row.get("started_at"):
        return None
    row["stats_file"] = path
    row["perf_file"] = None
    row["duration_s"] = None
    row["spm"] = None
    values = [row.get(column) for column in _RUN_COLUMNS]
    placeholders = ",".join("?" * len(_RUN_COLUMNS))
    cursor = conn.execute(
        f"INSERT OR IGNORE INTO run({','.join(_RUN_COLUMNS)}) VALUES({placeholders})",
        values,
    )
    if commit:
        conn.commit()
    if cursor.lastrowid and cursor.rowcount:
        return cursor.lastrowid
    existing = conn.execute("SELECT id FROM run WHERE stats_file=?", (path,)).fetchone()
    return existing[0] if existing else None


def attach_perf(conn, run_id, perf_path, commit=True):
    """Decode and store the curve. Returns False if the file is unusable."""
    try:
        curve = perfmod.parse(perf_path)
    except perfmod.PerfError as error:
        record_failure(conn, perf_path, error)
        return False

    blobs = [curve["series"][name].tobytes() for name in perfmod.SERIES]
    conn.execute(
        "INSERT OR REPLACE INTO curve(run_id, buckets, shots, hits, misses, "
        "dmg_done, dmg_possible, score, kills) VALUES(?,?,?,?,?,?,?,?,?)",
        [run_id, curve["buckets"], *blobs],
    )
    duration = curve["duration_s"] or None
    conn.execute(
        "UPDATE run SET perf_file=?, duration_s=?, "
        "  spm = CASE WHEN ? > 0 THEN score / ? * 60 ELSE NULL END, "
        "  damage_possible = ? "
        "WHERE id=?",
        (perf_path, duration, duration or 0, duration or 1,
         float(sum(curve["series"]["dmg_possible"])), run_id),
    )
    if commit:
        conn.commit()
    return True


def load_curve(conn, run_id):
    row = conn.execute("SELECT * FROM curve WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        return None
    out = {}
    for name in perfmod.SERIES:
        values = array.array("f")
        values.frombytes(row[name])
        out[name] = values
    return out


def bootstrap(conn, cfg):
    """Index everything not already indexed. Safe to call repeatedly."""
    counts = {"runs": 0, "curves": 0, "failed": 0, "reconciled": 0}
    already = indexed_paths(conn)

    try:
        with os.scandir(cfg.stats_dir) as scan:
            entries = sorted(scan, key=lambda e: e.name)
    except FileNotFoundError:
        return counts

    for entry in entries:
        if not entry.name.endswith(" Stats.csv") or entry.path in already:
            continue
        try:
            run_id = index_stats_file(conn, entry.path, commit=False)
        except Exception as error:  # one bad file must never stop the scan
            record_failure(conn, entry.path, error)
            counts["failed"] += 1
            continue
        if run_id is None:
            continue
        counts["runs"] += 1

        perf_path = perf_path_for(cfg, entry.path)
        if perf_path and os.path.exists(perf_path):
            if attach_perf(conn, run_id, perf_path, commit=False):
                counts["curves"] += 1
            else:
                counts["failed"] += 1
    conn.commit()

    # Reconciliation. A run indexed from its CSV before the .perf was written
    # would otherwise NEVER get a curve: the new-file loop above only considers
    # CSVs it has not seen, and the watcher only tracks files it saw arrive. A
    # first launch during a mid-write would permanently bake in a curveless run.
    stale = conn.execute(
        "SELECT id, stats_file FROM run WHERE perf_file IS NULL").fetchall()
    burned = {row[0] for row in conn.execute(
        "SELECT path FROM failed WHERE tries >= ?", (MAX_TRIES,))}
    for run_id, stats_file in stale:
        perf_path = perf_path_for(cfg, stats_file)
        if not perf_path or perf_path in burned or not os.path.exists(perf_path):
            continue
        if attach_perf(conn, run_id, perf_path, commit=False):
            counts["curves"] += 1
            counts["reconciled"] += 1
    conn.commit()
    return counts
