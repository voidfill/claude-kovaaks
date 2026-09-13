# Live updates in the browser

Step two of three. Makes a run appear in the dashboard shortly after it ends,
the way `python -m kvstats` does today.

**Do not implement this yet.** One blocking question has to be answered first,
and if it answers badly this document collapses to a single line: live stays in
the Python. See Blocking question.

Depends on [the read-only browser app](2026-09-13-kvstats-browser-design.md) for
the source boundary and the incremental index path, and on
[reaching the install](2026-09-13-kvstats-reaching-the-install.md) for how a
default-path user obtains a watchable folder at all. Users whose Steam library
already lives outside `Program Files` need only this document.

## Blocking question

**Does `FileSystemObserver` fire in a backgrounded tab?**

Everything measured so far was in a foreground tab, which is not the use case.
The use case is a fullscreen aim trainer on one monitor and the dashboard on
another, or minimised entirely — a tab that Chrome may throttle, and may discard
outright under memory pressure. A discarded tab loses its observer, its
in-memory state, and any debounce in flight.

The Python server has no equivalent failure mode: it is a process, and it keeps
running.

Test before writing any more of this: attach an observer, background the tab
behind a fullscreen game, play a run, and see whether and when it fires. Then
repeat minimised, and after twenty minutes idle. If it does not fire reliably,
stop — the honest answer is that live belongs in the Python, and this document
should say so instead of describing a feature that works on the developer's
monitor and nobody else's.

## What is already known

Both confirmed on 2026-09-13, in a foreground tab:

**A stored handle keeps its permission.** Persisted in IndexedDB,
`queryPermission()` returned `granted` on a fresh page load having asked
nothing. So the folder is chosen once rather than per visit — though the test
covered a reload, not a browser restart, a reboot, a week of dormancy, or the
target directory being renamed or moved. Any of those plausibly invalidates it,
and the claim should not be widened past what was actually observed until a
matrix exists.

**`FileSystemObserver` exists and fires**, within the same second as the write.
Two mechanics follow, both mandatory:

- **One run raises several events.** `appeared` when the file is created, then
  `modified` as the game writes its contents. Indexing on the event parses a
  half-written CSV. Coalesce per basename and wait for quiet before reading.
- **The `.perf` arrives after the `.csv`** — about two seconds apart in testing.
  Index the run curve-less on the first event and upgrade it when the second
  arrives. That is the same state the index already carries for the roughly one
  run in seven that never gets a `.perf` at all.

A parse that fails because the file was still being written is not a failure;
it is a retry. This is what step one's `failed` store and its try budget are
for, and it mirrors `watch.py`'s existing reasoning about not marking a
mid-write file as known.

## Design

Thin, because step one does the work.

The observer calls the **same incremental path** a manual re-pick calls:
*index these N new basenames into an existing database*. If that boundary was
built as specified, this is wiring.

`subscribe(fn)` on the source, absent in step one, is implemented here by a
`FileSystemObserver` on `stats/` and `performances/`, non-recursive.

**Marks invalidation applies.** A newly arrived run is normally the newest, so
the materialised marks append cleanly — but not always. A run whose scenario
crosses a shape boundary (race classification flips on the first `.perf`, and
`fixed_windows` needs two runs) changes which prior runs count as peers, so the
forward-pass recompute from step one is triggered here too, not just on
out-of-order inserts.

**Fallback if the observer is unavailable or unreliable:** a names-only
enumeration diff. Measured at ~2.2 s per directory at 12k runs, so roughly a
3–5 s update latency at that scale against the Python's ~1 s — and a constant
background cost that scales with library size. Acceptable as a fallback, not as
the design.

**Reconcile on open regardless.** Whatever happened while the page was closed is
found by the same names-only enumeration, ~4 s for both directories at 12k runs.
Render from the cache immediately and reconcile behind it.

## Open questions

- The handle-permission matrix above: restart, reboot, dormancy, moved target.
- Whether a discarded tab can re-attach silently on restore, or whether the user
  must click.
- Whether the observer survives the target being replaced wholesale — relevant
  if [reaching the install](2026-09-13-kvstats-reaching-the-install.md) ends up
  using copy-based sync, where the watched directory's contents are rewritten
  rather than appended to.
