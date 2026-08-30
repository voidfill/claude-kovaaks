# KovaaK's on-disk format and API

Everything here was verified against a real install on 2026-08-30, not inferred from
documentation. KovaaK's has no official API docs; the endpoints below were recovered
from the kovaaks.com frontend bundle and confirmed by calling them.

## Paths

Relative to the install root
(`C:\Program Files (x86)\Steam\steamapps\common\FPSAimTrainer\FPSAimTrainer`):

| Path | Contents |
|---|---|
| `Saved/SaveGames/Playlists/*.json` | one file per playlist |
| `Saved/SaveGames/Scenarios/*.sce` | locally created scenarios |
| `Saved/SaveGames/PrimaryUserSettings.json` | game settings (not touched by this skill) |
| `stats/*.csv` | one file per run (not touched by this skill) |

Workshop scenarios live outside the install root, at
`steamapps/workshop/content/824270/<id>/*.sce`. The **installed scenario set** is the
union of `.sce` basenames from that directory and `Saved/SaveGames/Scenarios`.

SteamID and persona name come from `Steam/config/loginusers.vdf` — prefer the block with
`"MostRecent" "1"`.

## Playlist file format

The filename is always `<playlistName>.json`. Keys appear in this order:

```
playlistName, playlistId, authorSteamId, authorName, scenarioList, description,
hasOfflineScenarios, hasEdited, shareCode, version, updated, isPrivate
```

`scenarioList` entries are `{"scenario_name": str, "play_Count": int}` — note the
inconsistent casing, which is the game's, not a typo.

### Encodings

The game emits **two** encodings and reads both back:

- UTF-8, no BOM, CRLF line endings, tab indent — 19 of 23 files on this machine.
- UTF-16LE **with BOM**, same structure — 4 files (the VDIM "Switching" playlists).

Those four filenames also contain an invisible **U+200E** (left-to-right mark) between
the dash and the word. It must be preserved when matching by name, and it is why the CLI
forces UTF-8 on stdout — the Windows console codepage cannot encode it.

The reader handles UTF-8, UTF-8-BOM, UTF-16LE and UTF-16BE. The writer only emits UTF-8.

### Byte-exact emission

This reproduces all 19 UTF-8 files byte for byte:

```python
json.dumps(data, indent="\t", separators=(",", ": "), ensure_ascii=False) \
    .replace("\n", "\r\n").encode("utf-8")
```

No BOM and no trailing newline — the file ends at the closing `}`.

### Field notes

- `playlistId` — a real id for downloaded playlists; `0` for locally created ones.
- `shareCode` — set on published playlists, empty otherwise.
- `version` — `31` on every file observed.
- `updated` — Unix epoch seconds.
- `hasOfflineScenarios` — **semantics inferred.** Every stock file says `false` even
  where scenarios are demonstrably missing (`1 - Basic` references 9 the user does not
  have). The CLI computes it from the installed index. If that ever misbehaves in-game,
  hardcode `false` to match observed reality.

### Sibling files that reference playlists by name

Renaming a playlist strands entries in these; the skill does not rewrite them.

| File | Notes |
|---|---|
| `LocalFavoritePlaylists.json` | UTF-16, array of playlist names |
| `PlaylistInProgress.json` | the playlist currently being played, with per-scenario progress |
| `PlaylistOrder.json` | play counts by index, no names |

## API

Base: `https://kovaaks.com/webapp-backend` — no authentication required for any of these.

| Endpoint | Parameters | Returns |
|---|---|---|
| `/scenario/popular` | `page`, `max` (both required), `scenarioNameSearch`, `duration` | paged scenario search; `leaderboardId`, `scenarioName`, `scenario.aimType`, `scenario.authors`, `counts.plays`, `topScore` |
| `/scenario/details` | `leaderboardId` | aim type, creator, description, tags, created date |
| `/playlist/playlists` | `page`, `max`, `search` (lowercased) | paged playlist search; includes the **full `scenarioList`** with `scenarioName`, `playCount`, `aimType`, plus `playlistId`, `playlistCode`, `subscribers` |
| `/playlist/trending` | none | trending playlists, no scenario lists |
| `/leaderboard/scores/global` | `leaderboardId`, `page`, `max` | leaderboard scores with attributes (fov, sens) |
| `/user/profile/by-username` | `username` | profile, SteamID, avatar |

Corpus sizes as of 2026-08-30: 61,670 scenarios, 371,814 playlists.

Notable gaps: there is **no fetch-playlist-by-id endpoint**. `/playlist/fetch`,
`/playlists`, `/playlist/popular` and `/staff-picks/playlist` all return
`404 Invalid Route`. To import a specific playlist you must search for it and select the
matching `playlistId` from the results.

Playlist `scenarioList` from the API uses different key names than the on-disk format:
`scenarioName`/`playCount` on the wire, `scenario_name`/`play_Count` on disk.
