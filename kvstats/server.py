"""HTTP surface: static shell, JSON API, SSE push.

ThreadingHTTPServer is required, not optional: /events holds a connection open
for the life of the page, and a single-threaded server would then serve nothing
else.
"""

import json
import mimetypes
import os
import queue
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import compare, index, shapes, watch

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

# SQLite stores INTEGER as a signed 64-bit value. Plain int() has no such
# ceiling -- it happily parses an arbitrarily large digit string -- so a huge
# query-string value sails past the existing `except ValueError` guard and
# only fails deep inside conn.execute, as an unhandled OverflowError that
# kills the connection with no HTTP response at all.
SQLITE_INT_MAX = 2 ** 63 - 1

# /api/runs?limit=... has no natural ceiling from the UI (index.html never
# asks for more than a few hundred rows), but two things make an unvalidated
# value dangerous: a huge one overflows SQLite as above, and a *negative* one
# is silently treated by SQLite as "no limit", returning the entire table
# (2331 rows in the reference corpus). Cap well above any real UI request --
# comfortably past "give me the whole table" -- so a legitimate large request
# still works while both failure modes are rejected up front.
MAX_LIMIT = 100_000


def _bounded_int(raw, minimum, maximum):
    """int(raw), rejecting values outside [minimum, maximum].

    Raises ValueError for both non-numeric input (same as plain int()) and
    in-range-for-Python-but-out-of-range-for-us input, so callers keep a
    single except ValueError branch instead of needing a second error path
    for the overflow case.
    """
    value = int(raw)
    if value < minimum or value > maximum:
        raise ValueError(f"{raw!r} out of range [{minimum}, {maximum}]")
    return value


METRICS = {
    "score": ("score", None),
    "shots": ("shots", None),
    "hits": ("hits", None),
    "kills": ("kills", None),
    "accuracy": ("hits", "shots"),
    "efficiency": ("dmg_done", "dmg_possible"),
}


def _ratio(numerator, denominator):
    return [(n / d) if d else 0.0 for n, d in zip(numerator, denominator)]


def _series(curve, metric):
    top, bottom = METRICS[metric]
    if bottom is None:
        return list(curve[top])
    return _ratio(list(curve[top]), list(curve[bottom]))


def _usable_metrics(curve):
    """Which metric buttons are worth offering for this run.

    Damage is booked per tick on some scenarios and only at kill time on
    others. Where it is per-kill, dmg_possible is a couple of units against
    thousands of shots, and `efficiency` draws a flat zero -- so it is
    withheld rather than shown as though it were a measurement.
    """
    usable = [name for name in METRICS if name != "efficiency"]
    if curve:
        possible = sum(curve["dmg_possible"])
        shots = sum(curve["shots"])
        if possible > 0 and possible >= 0.5 * shots:
            usable.append("efficiency")
    return usable


def build_run_payload(conn, run_id, metric="score", smoothing=5, recent_n=10,
                      same_cfg=True):
    if metric not in METRICS:
        raise ValueError(f"unknown metric: {metric}")
    # prior[-recent_n:] with recent_n<=0 is a Python slice quirk, not a
    # request for "no recent runs": 0 means "all prior runs" and a negative
    # value drops from the front instead of the back. Neither is reachable
    # from the UI (index.html clamps to min="1"), but the query string is not
    # validated, so clamp defensively at the boundary too.
    recent_n = max(recent_n, 0)

    row = conn.execute("SELECT * FROM run WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(run_id)
    run = {key: row[key] for key in row.keys()}

    scen_row = index.scenario_row(conn, run["scenario"])
    scenario = ({key: scen_row[key] for key in scen_row.keys()} if scen_row else
                {"name": run["scenario"], "shape": shapes.TIMED, "penalising": 0,
                 "budget": None, "pool": None, "bots": None, "clock_s": None,
                 "evidence": "default"})
    is_race = scenario["shape"] == shapes.RACE

    curve = index.load_curve(conn, run_id)
    run["buckets"] = len(curve["score"]) if curve else 0
    # A race plots damage/s whatever `metric` says -- the shape fixes the y
    # series, so every one of the six buttons would redraw the identical line.
    # No button at all is the honest offer; `metric` stays a valid METRICS key
    # on the query string so that switching back to a timed run still works.
    payload_metrics = [] if is_race else _usable_metrics(curve)

    base = compare.baselines(conn, run_id, recent_n=recent_n, same_cfg=same_cfg,
                             shape=scenario["shape"])
    payload = {
        "run": run,
        "scenario": scenario,
        "metrics": payload_metrics,
        "axis": {"kind": "time", "label": "seconds", "n": run["buckets"]},
        "rate": {"metric": metric, "unit": metric, "mine": [], "pb": None,
                 "band": None},
        "delta": {"unit": "points", "values": None, "final": None,
                  "compare_until": None, "baseline": None},
        "marks": {"kills": [], "labels": [], "aligned": False},
        "splits": [],
        "baselines": {
            "true_pb": base["true_pb"],
            "candidates": base["candidates"],
            "recent_n": base["recent"]["n"],
            "recent_mean_score": base["recent"]["mean_score"],
            "pb": None if not base["pb"] else {
                "run_id": base["pb"]["run_id"], "score": base["pb"]["score"],
                "started_at": base["pb"]["started_at"],
                "is_true_pb": base["pb"]["is_true_pb"]},
        },
    }

    if is_race:
        _fill_race(conn, payload, run, scenario, curve, base, smoothing)
    else:
        _fill_timed(conn, payload, run, curve, base, metric, smoothing)
    return payload


def _fill_timed(conn, payload, run, curve, base, metric, smoothing):
    """The existing behaviour, unchanged: native per-second grid, score units."""
    mine = _series(curve, metric) if curve else []
    payload["rate"]["mine"] = compare.smooth(mine, smoothing)
    payload["marks"]["kills"] = [k["t"] for k in index.load_kills(conn, run["id"])]

    if curve and base["pb"] and base["pb"]["curve"]:
        pb_curve = base["pb"]["curve"]
        payload["rate"]["pb"] = compare.smooth(_series(pb_curve, metric), smoothing)
        # Always score units, and always on the RAW series: smoothing would blur
        # the invariant that the final value equals the score difference.
        values = compare.cumulative_delta(
            list(curve["score"]), list(pb_curve["score"]))
        payload["delta"].update({
            "values": values, "final": values[-1] if values else None,
            "compare_until": compare.compare_until(
                list(curve["score"]), list(pb_curve["score"]))})
        # What the delta is measured against, so the UI cannot label the chart
        # with one baseline and the headline percentage with another.
        payload["delta"]["baseline"] = {
            "run_id": base["pb"]["run_id"], "score": base["pb"]["score"],
            "is_true_pb": base["pb"]["is_true_pb"]}

    if curve and base["recent"]["curve"]:
        raw = compare.band([_series(c, metric) for c in base["recent"]["curve"]])
        payload["rate"]["band"] = {k: compare.smooth(v, smoothing)
                                   for k, v in raw.items()}


def _fill_race(conn, payload, run, scenario, curve, base, smoothing):
    """Progress axis, damage rate, seconds-based delta, shared kill marks."""
    bots = scenario["bots"] or 1
    steps = compare.race_grid(bots)
    payload["axis"] = {"kind": "progress", "label": "% of pool", "n": steps}
    payload["rate"].update({"metric": "damage", "unit": "dmg/s"})
    payload["delta"]["unit"] = "seconds"
    # Kill k always lands at damage k*pool/bots, so the marks are the same for
    # every run of the scenario -- which is the whole point of this axis.
    payload["marks"] = {"kills": [(i + 1) / bots for i in range(bots)],
                        "labels": [], "aligned": True}

    mine_edges = []
    if curve and run["elapsed_s"]:
        mine_edges, rate = compare.resample_race(
            list(curve["hits"]), run["elapsed_s"], steps)
        payload["rate"]["mine"] = compare.smooth(rate, smoothing)

    pb_run = None
    if base["pb"] and base["pb"]["curve"]:
        pb_run = conn.execute("SELECT elapsed_s FROM run WHERE id=?",
                              (base["pb"]["run_id"],)).fetchone()
    if mine_edges and pb_run and pb_run["elapsed_s"]:
        base_edges, base_rate = compare.resample_race(
            list(base["pb"]["curve"]["hits"]), pb_run["elapsed_s"], steps)
        payload["rate"]["pb"] = compare.smooth(base_rate, smoothing)
        values = compare.race_delta(mine_edges, base_edges)
        payload["delta"].update({
            "values": values, "final": values[-1] if values else None,
            # Both runs span the whole pool by definition, so there is no
            # region where only one of them has data.
            "compare_until": 1.0})
        payload["delta"]["baseline"] = {
            "run_id": base["pb"]["run_id"], "score": base["pb"]["score"],
            "is_true_pb": base["pb"]["is_true_pb"]}

    if mine_edges and base["recent"]["curve"]:
        curves = []
        # Each recent run is resampled against its OWN elapsed_s, not the
        # focused run's: scaling every band member onto someone else's clock
        # moves a real Air Pure Medium run by up to ~15%, hiding a slow run
        # inside a band that looks normal.
        for recent, recent_id in zip(base["recent"]["curve"], base["recent"]["run_ids"]):
            recent_run = conn.execute("SELECT elapsed_s FROM run WHERE id=?",
                                      (recent_id,)).fetchone()
            if not recent_run or not recent_run["elapsed_s"]:
                continue
            _, recent_rate = compare.resample_race(
                list(recent["hits"]), recent_run["elapsed_s"], steps)
            if recent_rate:
                curves.append(recent_rate)
        if curves:
            raw = compare.band(curves)
            payload["rate"]["band"] = {k: compare.smooth(v, smoothing)
                                       for k, v in raw.items()}

    payload["splits"] = _race_splits(conn, run, base)
    # Name the boundaries after the bots that hold them, reusing the rows the
    # split table already loaded. A run that quit early names fewer bots than
    # the scenario has; the chart falls back to the ordinal for the rest.
    payload["marks"]["labels"] = [split["bot"] for split in payload["splits"]
                                  if split["idx"] is not None][:bots]


def _race_splits(conn, run, base):
    """Per-bot rows plus the dead-time residual, so the table reconciles.

    Dead time is not modelled as a scenario constant: it is stable within a
    game version but moved by up to a second across versions, so it is carried
    as this run's own residual and simply shown.
    """
    mine = index.load_kills(conn, run["id"])
    if not mine or not run["elapsed_s"]:
        return []
    base_by_idx = {}
    if base["pb"]:
        base_by_idx = {k["idx"]: k for k in index.load_kills(conn, base["pb"]["run_id"])}

    rows = []
    for kill in mine:
        other = base_by_idx.get(kill["idx"])
        rows.append({"idx": kill["idx"], "bot": kill["bot"], "mine": kill["ttk"],
                     "base": other["ttk"] if other else None,
                     "delta": (kill["ttk"] - other["ttk"]) if other else None})

    mine_dead = run["elapsed_s"] - sum(k["ttk"] for k in mine)
    base_dead = None
    if base_by_idx:
        base_run = conn.execute("SELECT elapsed_s FROM run WHERE id=?",
                                (base["pb"]["run_id"],)).fetchone()
        if base_run and base_run["elapsed_s"]:
            base_dead = base_run["elapsed_s"] - sum(
                k["ttk"] for k in base_by_idx.values())
    rows.append({"idx": None, "bot": "dead time", "mine": mine_dead,
                 "base": base_dead,
                 "delta": (mine_dead - base_dead) if base_dead is not None else None})
    return rows


def _rows(conn, sql, args=()):
    return [{k: r[k] for k in r.keys()} for r in conn.execute(sql, args).fetchall()]


def make_handler(cfg, conn, subscribers, lock, watcher=None):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass  # the terminal belongs to the operator, not to access logs

        def _json(self, payload, status=200):
            body = json.dumps(payload, default=float).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _static(self, relative):
            full = os.path.normpath(os.path.join(WEB, relative.lstrip("/")))
            # Trailing separator matters: a bare prefix test also admits a
            # sibling directory whose name merely starts with "web".
            if not full.startswith(WEB + os.sep) or not os.path.isfile(full):
                return self._json({"error": "not found"}, 404)
            kind = mimetypes.guess_type(full)[0] or "application/octet-stream"
            with open(full, "rb") as handle:
                body = handle.read()
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _events(self):
            channel = queue.Queue()
            with lock:
                subscribers.append(channel)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                while True:
                    try:
                        message = channel.get(timeout=15)
                        chunk = f"data: {json.dumps(message)}\n\n"
                    except queue.Empty:
                        chunk = ": keepalive\n\n"
                    self.wfile.write(chunk.encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with lock:
                    if channel in subscribers:
                        subscribers.remove(channel)

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            route = parsed.path
            query = urllib.parse.parse_qs(parsed.query)

            def one(name, default=None):
                return query.get(name, [default])[0]

            if route == "/":
                return self._static("index.html")
            if route.startswith("/web/"):
                return self._static(route[len("/web/"):])
            if route == "/events":
                return self._events()

            if route == "/api/health":
                one_row = lambda sql: conn.execute(sql).fetchone()[0]
                return self._json({
                    "runs": one_row("SELECT COUNT(*) FROM run"),
                    "curves": one_row("SELECT COUNT(*) FROM curve"),
                    "failed": one_row("SELECT COUNT(*) FROM failed"),
                    "scenarios": one_row("SELECT COUNT(DISTINCT scenario) FROM run"),
                    "awaiting_perf": len(watcher.stats["awaiting_perf"]) if watcher else 0,
                    "watcher_errors": watcher.stats["errors"] if watcher else 0,
                    # A real install has one run recording FOV 1.1. Junk like
                    # that should be visible, not silently averaged in.
                    "suspect_fov": one_row(
                        "SELECT COUNT(*) FROM run WHERE fov IS NOT NULL AND fov < 10"),
                })

            # buckets comes from the curve table, not run: the run list marks
            # runs with no per-second data, and roughly one run in seven has
            # none. NULL (no curve row) is the marker, so it must be selected
            # rather than inferred from perf_file, which is set before the
            # .perf is parsed.
            if route == "/api/runs":
                scenario = one("scenario")
                try:
                    limit = _bounded_int(one("limit", "50"), 0, MAX_LIMIT)
                except ValueError as error:
                    return self._json({"error": str(error)}, 400)
                if scenario:
                    return self._json(_rows(
                        conn,
                        "SELECT id, scenario, started_at, score, accuracy, spm, cfg_key, "
                        "(SELECT buckets FROM curve WHERE curve.run_id = run.id) AS buckets, "
                        "(SELECT shape FROM scenario WHERE name = run.scenario) AS shape "
                        "FROM run WHERE scenario=? ORDER BY started_at DESC LIMIT ?",
                        (scenario, limit)))
                return self._json(_rows(
                    conn,
                    "SELECT id, scenario, started_at, score, accuracy, spm, cfg_key, "
                    "(SELECT buckets FROM curve WHERE curve.run_id = run.id) AS buckets, "
                    "(SELECT shape FROM scenario WHERE name = run.scenario) AS shape "
                    "FROM run ORDER BY started_at DESC LIMIT ?", (limit,)))

            if route == "/api/scenarios":
                return self._json(_rows(conn, """
                    SELECT s.scenario, COUNT(*) AS runs, MAX(s.score) AS pb,
                           MAX(s.started_at) AS last_played,
                           (SELECT shape FROM scenario WHERE name = s.scenario) AS shape,
                           (SELECT elapsed_s FROM run r WHERE r.scenario = s.scenario
                             ORDER BY r.score DESC LIMIT 1) AS pb_elapsed,
                           (SELECT AVG(elapsed_s) FROM (
                                SELECT elapsed_s FROM run r WHERE r.scenario = s.scenario
                                ORDER BY started_at DESC LIMIT 10)) AS recent_elapsed,
                           (SELECT AVG(score) FROM (
                                SELECT score FROM run r WHERE r.scenario = s.scenario
                                ORDER BY started_at DESC LIMIT 10)) AS recent_form
                    FROM run s GROUP BY s.scenario ORDER BY last_played DESC"""))

            if route == "/api/session/today":
                day = one("day")
                if not day:
                    day = conn.execute(
                        "SELECT MAX(substr(started_at,1,10)) FROM run").fetchone()[0]
                return self._json({"day": day, "runs": _rows(
                    conn,
                    "SELECT id, scenario, started_at, score, accuracy, spm "
                    "FROM run WHERE substr(started_at,1,10)=? ORDER BY started_at",
                    (day,))})

            if route.startswith("/api/run/"):
                try:
                    run_id = _bounded_int(route.rsplit("/", 1)[-1], 1, SQLITE_INT_MAX)
                except ValueError:
                    return self._json({"error": "bad run id"}, 400)
                try:
                    return self._json(build_run_payload(
                        conn, run_id,
                        metric=one("metric", "score"),
                        smoothing=int(one("smoothing", "5")),
                        recent_n=int(one("recent_n", "10")),
                        same_cfg=one("same_cfg", "1") != "0"))
                except KeyError:
                    return self._json({"error": "no such run"}, 404)
                except ValueError as error:
                    return self._json({"error": str(error)}, 400)

            return self._json({"error": "not found"}, 404)

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # HTTPServer sets allow_reuse_address = 1, which on Windows lets a second
    # process bind a port that is already in use instead of raising -- two
    # instances would then silently share the port and the caller's
    # try-the-next-port loop would never fire.
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(cfg, conn, port=8777, subscribers=None, lock=None, watcher=None):
    subscribers = [] if subscribers is None else subscribers
    lock = threading.Lock() if lock is None else lock
    handler = make_handler(cfg, conn, subscribers, lock, watcher)
    return _Server(("127.0.0.1", port), handler)


def serve(cfg, port=8777):
    """Bootstrap, start the watcher, then serve until interrupted."""
    conn = index.connect(cfg.db_path)
    conn.execute("PRAGMA journal_mode=WAL")

    print("indexing...", flush=True)
    counts = index.bootstrap(conn, cfg)
    print(f"  {counts['runs']} new runs, {counts['curves']} curves, "
          f"{counts['reconciled']} reconciled, {counts['failed']} failed", flush=True)

    subscribers, lock = [], threading.Lock()

    # The watcher owns its own connection: sqlite3 objects are not shareable
    # across threads by default, and the HTTP handlers use the main one.
    watch_conn = index.connect(cfg.db_path)

    def announce(run_id):
        with lock:
            targets = list(subscribers)
        for channel in targets:
            channel.put({"type": "run", "id": run_id})

    watcher = watch.Watcher(cfg, watch_conn, on_run=announce)
    stop = threading.Event()
    threading.Thread(target=watcher.run_forever, args=(stop,), daemon=True).start()

    for candidate in range(port, port + 20):
        try:
            httpd = make_server(cfg, conn, candidate, subscribers, lock, watcher)
            break
        except OSError:
            continue
    else:
        raise SystemExit(f"no free port in {port}..{port + 19}")

    print(f"kvstats -> http://127.0.0.1:{httpd.server_address[1]}/", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.server_close()
