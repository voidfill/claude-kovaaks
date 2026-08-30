# KovaaK's Playlist Skill — Design & Implementation Plan

Date: 2026-08-30

## Goal

A Claude Code skill that manages local KovaaK's playlists: create them, edit them, and
find scenarios/playlists to put in them via the public KovaaK's API.

Hard constraint: **the skill may only modify playlists it created.** Stock and
subscribed playlists are readable but never writable.

Out of scope, deliberately: stats analysis, game settings, benchmark tracking.

## Verified environment facts

All of the following were confirmed on this machine, not assumed.

| Fact | Value |
|---|---|
| Install root | `C:\Program Files (x86)\Steam\steamapps\common\FPSAimTrainer\FPSAimTrainer` |
| Playlists | `<root>/Saved/SaveGames/Playlists/*.json` — 23 files |
| Workshop scenarios | `steamapps/workshop/content/824270/<id>/*.sce` — 348 items |
| Local scenarios | `<root>/Saved/SaveGames/Scenarios/*.sce` — 28 items |
| Installed scenarios (union of basenames) | 376 |
| SteamID source | `steamapps/../config/loginusers.vdf` |
| API base | `https://kovaaks.com/webapp-backend` — unauthenticated |

Encoding: 19 playlist files are UTF-8 / CRLF / tab-indent; **4 are UTF-16LE with BOM**
(the "Switching" ones, whose filenames also contain an invisible U+200E). The reader
must handle both. The writer only ever emits UTF-8.

## Playlist file format

Filename is always `<playlistName>.json`. Fixed key order:

```
playlistName, playlistId, authorSteamId, authorName, scenarioList,
description, hasOfflineScenarios, hasEdited, shareCode, version, updated, isPrivate
```

`scenarioList` entries are `{"scenario_name": str, "play_Count": int}`.

**Byte-exact emission** — verified to reproduce all 19 UTF-8 files exactly:

```python
json.dumps(data, indent="\t", separators=(",", ": "), ensure_ascii=False) \
    .replace("\n", "\r\n").encode("utf-8")
```

UTF-8, no BOM, no trailing newline.

Sibling files reference playlists **by name** and are therefore invalidated by a
rename: `LocalFavoritePlaylists.json` (UTF-16), `PlaylistInProgress.json`,
`PlaylistOrder.json`. The skill never writes these; `rename` warns if the old name
appears in any of them.

### Fields for skill-created playlists

```json
"playlistId": 0,
"authorSteamId": "76561199007330275",
"authorName": "voidfill [claude-kovaaks]",
"shareCode": "",
"version": 31,
"hasEdited": true,
"isPrivate": false,
"updated": "<unix epoch at write time>",
"hasOfflineScenarios": "<computed: true if any scenario is not installed>"
```

## Ownership rule

A playlist is **managed** iff its `authorName` ends with the literal `[claude-kovaaks]`.

One function, `assert_managed(path)`, is called by every write command before it touches
a file. Not managed → exit code 3 and a message directing the caller to
`create --from-playlist` instead. `create`, `import` and `rename` are the only commands
that may write to a path that has no file yet, and all three refuse if a file already
exists at the target path, managed or not.

The escape hatch: `create "My Warmup" --from-playlist "VDIM Novice S5 - Tracking I"`
clones any playlist into a managed one. You can always work *from* anything; you just
cannot overwrite it.

Rejected alternatives: a repo-side registry with content hashes (stronger, but adds
state the user did not want) and a filename prefix (visible in-game, rename-fragile).

## Repo layout

```
claude-kovaaks/
├── SKILL.md                 # the skill; repo root IS the skill directory
├── scripts/kvpl.py          # single file, stdlib only
├── references/kovaaks.md    # on-disk format + verified API endpoints
├── tests/
│   ├── test_kvpl.py
│   └── fixtures/
├── docs/superpowers/specs/
└── README.md
```

Install, no admin needed:

```
mklink /J "%USERPROFILE%\.claude\skills\kovaaks-playlists" "C:\Users\8alex\git\claude-kovaaks"
```

## CLI

`python scripts/kvpl.py <command>` — all output is JSON on stdout. Errors go to stderr
with a nonzero exit: `2` bad input, `3` ownership refusal, `4` path/config problem.

**Read — any playlist**

| Command | Notes |
|---|---|
| `list` | every playlist with `managed`, scenario count, author |
| `show <name>` | full contents; each scenario tagged `installed: true/false` |
| `installed [--search TEXT]` | the 376-scenario local index |

**Write — managed only**

| Command | Notes |
|---|---|
| `create <name> [--description D] [--from-playlist P] [--scenario "N:count"]...` | refuses if the file exists |
| `add <name> --scenario N [--count C] [--at I]` | warns if not installed; default count 1 |
| `remove <name> (--scenario N \| --index I)` | |
| `move <name> --from I --to J` | reorder |
| `set-count <name> --scenario N --count C` | |
| `set-description <name> --description D` | |
| `rename <name> <new-name>` | moves file and rewrites field together; refuses if the target exists |
| `delete <name>` | |
| `import (<query> \| --playlist-id N) --as <local-name>` | API result to a managed local copy |

**Discovery — network**

| Command | Endpoint |
|---|---|
| `search-scenarios <query> [--aim-type T] [--max N]` | `scenario/popular?page&max&scenarioNameSearch` |
| `search-playlists <query> [--max N]` | `playlist/playlists?page&max&search` |
| `scenario-info (<name> \| --leaderboard-id N)` | `scenario/details?leaderboardId` |

Config resolution is env var, then default: `KOVAAKS_DIR`, `KOVAAKS_WORKSHOP_DIR`
(default `<root>/../../../workshop/content/824270`), `KOVAAKS_STEAM_ID`,
`KOVAAKS_AUTHOR_NAME`. If a path does not resolve, every command fails with exit 4 and
names the missing path.

Writes are atomic: temp file in the same directory, then `os.replace`.

## Implementation plan

Each step is independently verifiable. Do them in order.

1. **Skeleton.** `git init`, README, directory structure. Commit.
2. **Config + reader.** Path and SteamID resolution, multi-encoding parse, `list` and
   `show`. *Verify:* `list` returns all 23 playlists with `managed: false`; `show` works
   on both a UTF-8 and a UTF-16 file.
3. **Installed index.** `installed`. *Verify:* count is 376, and `show "1 - Basic"`
   marks exactly 9 scenarios uninstalled.
4. **Writer, gate, `create`, `delete`.** *Verify:* a stock file round-tripped through the
   writer is byte-identical, and `add` on a stock playlist exits 3.
   **Then stop:** create one playlist and load KovaaK's manually to confirm
   `playlistId: 0` works before building on it.
5. **Mutations.** `add`, `remove`, `move`, `set-count`, `set-description`, `rename`.
6. **Discovery.** `search-scenarios`, `search-playlists`, `scenario-info`, `import`.
7. **Tests.** See below.
8. **SKILL.md and references/kovaaks.md**, written last against the CLI as built.

## Testing

Deliberately minimal — four tests, against fixtures, never the real install:

1. Reader parses UTF-8, UTF-8-BOM and UTF-16LE fixtures.
2. Writer round-trips a real stock playlist byte-for-byte.
3. Gate: a write command exits 3 on an unmanaged fixture and succeeds on a managed one.
4. `create --from-playlist` produces a managed clone whose scenario list matches the source.

The network layer is not unit-tested. The endpoints were verified by hand and are
recorded in `references/kovaaks.md`.

## Open risks

1. **`playlistId: 0` is unverified in-game.** Gated by the manual check at step 4. If the
   game rejects it, switch to a high unused integer.
2. **In-game edits may strip the `[claude-kovaaks]` tag**, making a playlist unmanaged
   and read-only to the skill. Fails safe; accepted.
3. **`hasOfflineScenarios` semantics are inferred.** All 23 existing files say `false`
   even where scenarios are missing. If computing it causes any problem, hardcode
   `false` to match observed reality.
