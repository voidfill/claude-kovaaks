---
name: kovaaks-playlists
allowed-tools: Bash(python "${CLAUDE_SKILL_DIR}/scripts/kvpl.py" *)
description: Use when creating, editing, inspecting or deleting local KovaaK's aim-trainer playlists, or when searching the KovaaK's scenario/playlist index to build one - covers adding and reordering scenarios, setting play counts, cloning stock playlists, and importing community playlists.
---

# KovaaK's Playlists

Manage local KovaaK's playlists through `scripts/kvpl.py`. Every command prints JSON.

**Never edit playlist files directly.** Always go through the CLI. It writes the exact
byte format the game expects (UTF-8, CRLF, tabs, fixed key order) and enforces the
ownership rule below.

```
python "${CLAUDE_SKILL_DIR}/scripts/kvpl.py" <command> [args]
```

`${CLAUDE_SKILL_DIR}` resolves to this skill's directory, so the command works from any
working directory. Do not use a relative path.

## The ownership rule

The CLI can **read** any playlist but **writes only playlists it created** — those whose
`authorName` ends with `[claude-kovaaks]`. Any write against a stock or subscribed
playlist exits with code **3** and changes nothing.

This is not a suggestion you can work around; it is enforced in the code. When the user
wants to change a stock playlist, clone it first:

```
python "${CLAUDE_SKILL_DIR}/scripts/kvpl.py" create "My Tracking Warmup" --from-playlist "VDIM Novice S5 - Tracking I"
```

Then edit the clone. Tell the user you did this and why — they asked for a change to
their VDIM playlist and got a new playlist instead, so say so.

## Commands

Read (any playlist):

| Command | Purpose |
|---|---|
| `list [--managed-only]` | every playlist, each flagged `managed` |
| `show <name>` | contents, with each scenario flagged `installed` |
| `installed [--search TEXT] [--max N]` | scenarios present on this machine (`--max 0` = all) |

Write (managed playlists only):

| Command | Purpose |
|---|---|
| `create <name> [--description D] [--from-playlist P] [--scenario "NAME:COUNT"]...` | new playlist; `--scenario` repeatable |
| `add <name> --scenario NAME [--count N] [--at INDEX]` | append or insert |
| `remove <name> (--scenario NAME \| --index I)` | |
| `move <name> --from I --to J` | reorder |
| `set-count <name> --scenario NAME --count N` | |
| `set-description <name> --description D` | |
| `rename <name> <new-name>` | |
| `delete <name>` | |

Discovery (needs network):

| Command | Purpose |
|---|---|
| `search-scenarios <query> [--aim-type T] [--max N]` | search all ~61k scenarios; results flag `installed` |
| `search-playlists <query> [--max N] [--full]` | search community playlists |
| `scenario-info (<query> \| --leaderboard-id N)` | aim type, author, description, tags |
| `import <query> --as <local-name> [--playlist-id N]` | community playlist → managed local copy |

## Working notes

**Scenario names must be exact.** They are matched literally against the file on disk.
Before adding a scenario the user named loosely, resolve it with `search-scenarios` or
`installed --search` and confirm the real name.

**Not-installed scenarios are allowed but flagged.** `add` warns, and `show` marks each
entry. A playlist can reference scenarios the user does not own; KovaaK's will prompt to
download them. Surface the warning to the user rather than swallowing it.

**Play counts default to 1.** Benchmark playlists typically use 1; training playlists
use 2-4. Ask if the user has not said.

**`import` searches, it does not fetch by id.** There is no by-id endpoint, so `import`
runs a search and takes the first result unless `--playlist-id` selects a different one
from that result page. Run `search-playlists` first when the query is ambiguous, and
confirm which playlist you are importing.

**Renaming can strand references.** `LocalFavoritePlaylists.json` and
`PlaylistInProgress.json` refer to playlists by name; `rename` warns when the old name
appears in them. The user has to fix those in-game.

## Benchmarks

Most playlist requests are phrased in benchmark vocabulary — *"a precise tracking
playlist for around intermediate"*, *"something for diamond level"*. Two benchmarks are
covered: **Voltaic** (written `VT`, current season S5, trained via VDIM) and **Viscose**
(season S2, trained by running the benchmark directly).

Read `references/benchmarks.md` before answering any such request, then the one file for
the benchmark in play. It carries the exact scenario names, difficulty tiers, rank
mappings and group tables.

Three things to get right:

- **Ask which benchmark**, unless the user said. Voltaic and Viscose group scenarios
  completely differently and share almost no scenarios.
- **Ask which source.** Benchmark scenarios only, benchmark plus official sub-variants,
  or the full VDIM block with its warmup scenarios — same request, very different
  playlists.
- **Respect a time budget** when the user gives one. Every command that reports a
  playlist returns `estimatedMinutes`; `references/benchmarks.md` has the model and the
  order to cut in. A VDIM day is about two hours, so most time-boxed asks need trimming.
- **Resolve every name** through `search-scenarios`, which queries the web index and so
  works on any machine. Never treat the local install as the authority on what a
  benchmark contains — the user may own none of it. Report missing scenarios after
  creating the playlist rather than dropping them.

## Configuration

Paths and identity are auto-detected: the default Steam install location, and the user's
SteamID and persona name from `loginusers.vdf`. Override with `KOVAAKS_DIR`,
`KOVAAKS_WORKSHOP_DIR`, `KOVAAKS_STEAM_ID`, `KOVAAKS_AUTHOR_NAME`. A missing path fails
with exit code 4 naming the path.

Exit codes: `2` bad input, `3` ownership refusal, `4` path/config problem.

## Reference

`references/benchmarks.md` indexes the Voltaic and Viscose benchmarks and defines the
shape a new benchmark file must follow; `references/benchmarks/*.md` hold the
per-benchmark scenario tables.

`references/kovaaks.md` documents the on-disk playlist format, the encodings the game
emits, and the verified API endpoints. Read it before changing `scripts/kvpl.py`.
