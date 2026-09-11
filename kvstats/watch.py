"""Watching the KovaaK's output directories.

Two tiers, because the cost difference is 320x. Measured on a real install with
4312 files: `scandir` over both directories is 10.85 ms, while `stat()` on the
two directories is 0.034 ms. So the loop stats the cheap signal every 100 ms and
only scans when a directory's mtime has actually moved -- <=100 ms detection for
less idle work than a 2 s scan poll would cost.

A periodic unconditional scan is the safety net, in case directory mtime is ever
unreliable on the underlying filesystem.
"""

import os
import time
import traceback

from . import index


class Watcher:
    def __init__(self, cfg, conn, on_run=None, tick=0.1, full_scan_every=10.0,
                 perf_deadline=60.0):
        self.cfg = cfg
        self.conn = conn
        self.on_run = on_run
        self.tick = tick
        self.full_scan_every = full_scan_every
        self.perf_deadline = perf_deadline

        self._known = index.indexed_paths(conn)
        self._mtimes = {}
        self._awaiting = {}          # run_id -> (perf_path, first_seen_monotonic)
        self._last_full_scan = 0.0
        self.stats = {"scans": 0, "ticks": 0, "errors": 0,
                      "awaiting_perf": self._awaiting}

    # -- tier 1 -----------------------------------------------------------
    def _directories_changed(self):
        changed = False
        for directory in (self.cfg.stats_dir, self.cfg.perf_dir):
            try:
                mtime = os.stat(directory).st_mtime_ns
            except OSError:
                mtime = None
            if self._mtimes.get(directory) != mtime:
                self._mtimes[directory] = mtime
                changed = True
        return changed

    # -- tier 2 -----------------------------------------------------------
    def _scan_stats(self):
        new_ids = []
        try:
            entries = sorted(os.scandir(self.cfg.stats_dir), key=lambda e: e.name)
        except FileNotFoundError:
            return new_ids

        for entry in entries:
            if not entry.name.endswith(" Stats.csv") or entry.path in self._known:
                continue
            try:
                run_id = index.index_stats_file(self.conn, entry.path)
            except Exception as error:
                # Likely caught mid-write. Do NOT mark it known: the next tick
                # retries, and record_failure caps the retries at MAX_TRIES.
                tries = index.record_failure(self.conn, entry.path, error)
                if tries >= index.MAX_TRIES:
                    self._known.add(entry.path)
                continue

            self._known.add(entry.path)
            if run_id is None:
                continue
            perf_path = index.perf_path_for(self.cfg, entry.path)
            if perf_path:
                self._awaiting[run_id] = (perf_path, time.monotonic())
            new_ids.append(run_id)
        return new_ids

    def _attach_ready_perfs(self):
        for run_id in list(self._awaiting):
            perf_path, first_seen = self._awaiting[run_id]
            if os.path.exists(perf_path):
                if index.attach_perf(self.conn, run_id, perf_path):
                    del self._awaiting[run_id]
                    continue
                # attach_perf already called record_failure. Stop once the file
                # has burned its budget -- otherwise a corrupt .perf is
                # re-parsed and re-committed on every 100 ms tick until the
                # deadline, roughly 600 times.
                tries = self.conn.execute(
                    "SELECT tries FROM failed WHERE path=?", (perf_path,)).fetchone()
                if tries and tries[0] >= index.MAX_TRIES:
                    del self._awaiting[run_id]
                    continue
            if time.monotonic() - first_seen >= self.perf_deadline:
                # 318 runs in a real install never get a .perf. That is a
                # supported state, not an error.
                del self._awaiting[run_id]

    # -- the loop ---------------------------------------------------------
    def poll_once(self):
        self.stats["ticks"] += 1
        now = time.monotonic()
        due = (now - self._last_full_scan) >= self.full_scan_every

        if not self._directories_changed() and not due:
            self._attach_ready_perfs()
            return []

        self.stats["scans"] += 1
        self._last_full_scan = now
        new_ids = self._scan_stats()
        self._attach_ready_perfs()

        if self.on_run:
            for run_id in new_ids:
                self.on_run(run_id)
        return new_ids

    def run_forever(self, stop_event):
        while not stop_event.is_set():
            try:
                self.poll_once()
            except Exception:
                # The loop outlives any single bad tick, but never silently:
                # a swallowed exception here turns the watcher into a no-op
                # that spins at 10 Hz and indexes nothing.
                self.stats["errors"] += 1
                traceback.print_exc()
            stop_event.wait(self.tick)
