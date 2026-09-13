# Live updates in the browser

Step two of three. Makes a run appear in the dashboard shortly after it ends,
the way `python -m kvstats` does today.

Ready to implement. The question that blocked it — whether the observer survives
a backgrounded tab — was answered on 2026-09-13 and answered well.

Depends on [the read-only browser app](2026-09-13-kvstats-browser-design.md) for
the source boundary and the incremental index path, and on
[reaching the install](2026-09-13-kvstats-reaching-the-install.md) for how a
default-path user obtains a watchable folder at all. Users whose Steam library
already lives outside `Program Files` need only this document.

## A backgrounded tab is not throttled

Measured over ten minutes with the tab minimised, a file written every thirty
seconds from outside the browser, each filename carrying its own write time so
the lag was measured rather than estimated:

| | |
|---|---|
| Files written / noticed | 20 / 20, none missed |
| Fires while `visibilityState === "hidden"` | 78 of 78 |
| First-notice lag | median **3 ms**, max 12 ms |
| Freeze, discard, or reload | none |

No throttling, no batching, and no drift as the tab sat there. This is faster
than the Python's filesystem poll, and it means the README's "about a second
after a run ends" survives the port.

**What this did not test:** a machine under real memory pressure. Tab discard is
driven by memory, not elapsed time, so "Chrome never discarded the tab" here
means only that it had no reason to. Treat throttling as settled and discard as
unlikely but unproven — which the reconcile below covers anyway.

## What is already known

Both confirmed on 2026-09-13, in a foreground tab:

**A stored handle keeps its permission.** Persisted in IndexedDB,
`queryPermission()` returned `granted` on a fresh page load having asked
nothing. So the folder is chosen once rather than per visit — though the test
covered a reload, not a browser restart, a reboot, a week of dormancy, or the
target directory being renamed or moved. Any of those plausibly invalidates it,
and the claim should not be widened past what was actually observed until a
matrix exists.

**`FileSystemObserver` fires within milliseconds**, foreground or background.
Two mechanics follow, both mandatory:

- **One run raises several events** — 3.9 per file on average across 78
  observed events. `appeared` when the file is created, then `modified` as the
  game writes its contents. Indexing on the first event parses a half-written
  CSV essentially every time. Coalesce per basename and wait for quiet before
  reading; this is load-bearing, not a precaution.
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

### Events plus a safety-net sweep

Events carry the normal case; a periodic enumeration catches whatever they miss.
Same belt-and-braces shape `watch.py` already uses, and for the same reason: the
observer is a notification, not a guarantee. It cannot report what happened while
the tab was discarded, and it has failure modes nobody has mapped.

- **The observer is the primary path.** 3 ms, and it does the work.
- **Sweep only while the tab is focused.** A backgrounded tab is already covered
  by events, and a user who is not looking does not need a reconcile. This also
  keeps the cost off the machine while a game is running, which is exactly when
  it should not be spending cycles.
- **Reconcile on every page load**, before anything else. This is the only thing
  that catches a discarded tab or a closed browser, so it is not optional.

**The sweep interval must scale with library size.** A names-only enumeration
(`.keys()`, never `.entries()`) costs ~2.2 s per directory at 12k runs. A flat
10 s interval would spend a fifth of wall-clock time enumerating on a large
library, while being nearly free on a small one. Time each sweep and set the
next delay from what it actually cost — `max(10 s, 10 × last sweep)` keeps the
duty cycle at or under ten percent and self-tunes across the whole range, from a
200-run beginner to the 12k case. Do not hard-code 10 s.

## Open questions

- The handle-permission matrix above: restart, reboot, dormancy, moved target.
- Whether a discarded tab can re-attach silently on restore, or whether the user
  must click. The load-time reconcile makes this a UX question rather than a
  correctness one.
- Whether the observer behaves under genuine memory pressure — a fullscreen game
  plus a large library — which the ten-minute idle test could not provoke.
- Whether the observer survives the target being replaced wholesale — relevant
  if [reaching the install](2026-09-13-kvstats-reaching-the-install.md) ends up
  using copy-based sync, where the watched directory's contents are rewritten
  rather than appended to.
