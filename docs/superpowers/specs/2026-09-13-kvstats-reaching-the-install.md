# Reaching the install

Step three of three, and **a direction rather than a specification**. It records
why the problem exists, which approaches are viable, and what each costs. The
mechanics get designed if and when someone builds one.

The problem only exists for **default-path installs on Windows**. A Steam
library anywhere outside `Program Files` is readable by the browser today, with
no setup, and those users skip this document entirely.

## Why there is a problem

KovaaK's installs to `C:\Program Files (x86)\Steam\...` by default, and Chrome
refuses to hand a web page any directory under `Program Files`. Every route was
tried on 2026-09-13 and every one is closed:

| Route | Result |
|---|---|
| `showDirectoryPicker()` on the install | Blocked: "contains system files" |
| Junction in the profile pointing *at* the install | Blocked — Chromium resolves the link and checks the target |
| Drag-and-drop of the install folder | Blocked — the check is route-independent |
| Dropping `stats/` alone, deeper in the tree | Blocked — every descendant, not just upper levels |

Also closed: `file://` needs a browser launch flag; native messaging needs an
extension; Chrome's enterprise policies govern whether a site may use the API at
all, not the sensitive-path list; and KovaaK's has no configurable output
directory.

What *is* allowed is any ordinary path outside the blocked roots. A junction is
fine as long as it **resolves** somewhere permitted — the opposite direction
from the one that failed above. That is what makes option A possible.

The upload control used by
[step one](2026-09-13-kvstats-browser-design.md) is not subject to the
blocklist and reads a default install today. It simply cannot watch.

## Option A — redirect with junctions

Move `stats/` and `performances/` into the user profile and junction them back
into the game folder. The game writes through the junction unchanged; the
browser picks an ordinary profile folder and never touches a link.

**Verified end to end on a real install**: 2,380 stats and 2,070 `.perf` moved
and junctioned, a run played in-game landed both files in the profile folder,
in-game score history still displayed, and Steam's *verify integrity of game
files* left the junctions untouched. No administrator rights needed — Steam's
folder already grants Users write access, which is why the game can write there
unelevated.

Then it was undone cleanly and the install returned to normal.

**What it costs, honestly.** This is the riskiest thing the project would ask
anyone to do, and the hazard list is longer than the verification:

- **A terminal command**, which is the friction this whole design set out to
  avoid.
- **`move` fails across volumes.** Windows `move` cannot move a *directory* to
  another drive — confirmed: "Access is denied. 0 dir(s) moved." Game on `D:`
  and profile on `C:` is extremely common. PowerShell's `Move-Item` succeeds
  because it falls back to copy-and-delete; a `.bat` using `move` does not.
  Any published procedure must handle this, and must check errors — an
  unchecked failure leaves the game writing to a real folder nobody reads while
  the dashboard sits frozen at setup, with no error anywhere.
- **Steam's uninstall path is untested**, and it is the catastrophic one. If it
  deletes recursively through reparse points rather than removing the link, it
  takes the profile data with it. *Verify integrity* is a different code path
  and proves nothing about it. Likewise untested: "Move install folder", content
  updates that ship files under `stats/`, and reinstalling to another drive,
  which orphans the profile data.
- **`rmdir /s` on a junction deletes the target's contents.** That is muscle
  memory for many people, and a note in a document is thin protection.
- **Users forget.** In six months someone sees a shortcut overlay on `stats`,
  "fixes" it, and loses everything. In Tier 1 the tool is a web page that may
  not have been opened in months, so it cannot remind them.
- Redirected or roaming profiles, OneDrive-backed profile folders, backup tools
  running `robocopy /MIR` over the game directory, and antivirus heuristics on a
  script writing to `Program Files`.

## Option B — copy instead of move

Copy `stats/` and `performances/` into a profile folder and leave the install
completely untouched. No reparse points, no interaction with Steam's uninstaller,
no undo procedure — you delete the copy — and no way to lose data, because the
originals never move.

It dominates option A on every axis except freshness. The cost is duplicate disk
(~165 MB at 12k runs, and it grows) and staleness: a copy is a snapshot, so
something must re-run it. That "something" is either the user, manually, or a
scheduled task — and a scheduled task is a background process, which is close to
the resident companion this project deliberately is not.

Worth noting for [step two](2026-09-13-kvstats-live-updates-design.md): if a
task refreshes the copy, the browser can watch the *copy* directory and get
near-live updates without ever touching the install. Whether an observer behaves
sanely when a directory's contents are rewritten rather than appended to is
unknown.

Not evaluated in any depth. It deserves to be, before A is ever recommended.

## Option C — do nothing

Default-path users get [step one](2026-09-13-kvstats-browser-design.md) only:
a zero-install history browser, refreshed by re-picking the folder. Live updates
are for people whose install is already reachable, plus anyone willing to run
the Python.

**This is the default**, and the baseline any other option has to beat. It costs
nothing, risks nothing, and is honest about what it does.

## How to choose

Not now. Ship step one, see whether people actually ask for live updates and
say they will not run the Python, and only then decide between A and B — with B
evaluated properly first, since it is the safer of the two and has never been
examined.

Whatever is chosen, it is opt-in, it is clearly labelled as modifying the user's
machine, and it never happens automatically.
