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

from . import compare, index, watch

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

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


def build_run_payload(conn, run_id, metric="score", smoothing=5, recent_n=10,
                      same_cfg=True):
    if metric not in METRICS:
        raise ValueError(f"unknown metric: {metric}")

    row = conn.execute("SELECT * FROM run WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(run_id)
    run = {key: row[key] for key in row.keys()}

    curve = index.load_curve(conn, run_id)
    run["buckets"] = len(curve["score"]) if curve else 0

    mine = _series(curve, metric) if curve else []
    payload = {
        "run": run,
        "metric": metric,
        "curve": compare.smooth(mine, smoothing),
        "cumulative_delta": None,
        "compare_until": None,
        "delta_baseline": None,
        "pb_curve": None,
        "recent_band": None,
        "baselines": {},
    }

    base = compare.baselines(conn, run_id, recent_n=recent_n, same_cfg=same_cfg)
    payload["baselines"] = {
        "true_pb": base["true_pb"],
        "candidates": base["candidates"],
        "recent_n": base["recent"]["n"],
        "recent_mean_score": base["recent"]["mean_score"],
        "pb": None if not base["pb"] else {
            "run_id": base["pb"]["run_id"],
            "score": base["pb"]["score"],
            "started_at": base["pb"]["started_at"],
            "is_true_pb": base["pb"]["is_true_pb"],
        },
    }

    if curve and base["pb"] and base["pb"]["curve"]:
        pb_curve = base["pb"]["curve"]
        payload["pb_curve"] = compare.smooth(_series(pb_curve, metric), smoothing)

        # The delta chart is ALWAYS in score units, whatever metric the top
        # chart shows -- a running sum of per-second accuracy differences is a
        # meaningless quantity, and the spec specifies score units. It is also
        # computed on the RAW series: smoothing would blur the invariant that
        # the final value equals the score difference exactly.
        payload["cumulative_delta"] = compare.cumulative_delta(
            list(curve["score"]), list(pb_curve["score"]))
        payload["compare_until"] = compare.compare_until(
            list(curve["score"]), list(pb_curve["score"]))
        # What the delta is measured against, so the UI cannot label the chart
        # with one baseline and the headline percentage with another.
        payload["delta_baseline"] = {
            "run_id": base["pb"]["run_id"],
            "score": base["pb"]["score"],
            "is_true_pb": base["pb"]["is_true_pb"],
        }

    if curve and base["recent"]["curve"]:
        recent_series = [_series(c, metric) for c in base["recent"]["curve"]]
        raw_band = compare.band(recent_series)
        payload["recent_band"] = {
            key: compare.smooth(values, smoothing)
            for key, values in raw_band.items()
        }
    return payload


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

            if route == "/api/runs":
                scenario = one("scenario")
                try:
                    limit = int(one("limit", "50"))
                except ValueError as error:
                    return self._json({"error": str(error)}, 400)
                if scenario:
                    return self._json(_rows(
                        conn,
                        "SELECT id, scenario, started_at, score, accuracy, spm, cfg_key "
                        "FROM run WHERE scenario=? ORDER BY started_at DESC LIMIT ?",
                        (scenario, limit)))
                return self._json(_rows(
                    conn,
                    "SELECT id, scenario, started_at, score, accuracy, spm, cfg_key "
                    "FROM run ORDER BY started_at DESC LIMIT ?", (limit,)))

            if route == "/api/scenarios":
                return self._json(_rows(conn, """
                    SELECT s.scenario, COUNT(*) AS runs, MAX(s.score) AS pb,
                           MAX(s.started_at) AS last_played,
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
                    run_id = int(route.rsplit("/", 1)[-1])
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
