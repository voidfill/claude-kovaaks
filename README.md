# claude-kovaaks

A Claude Code skill for managing local KovaaK's playlists — create and edit them, and
search the KovaaK's scenario and playlist index to fill them.

The skill can **read** every playlist you have but **writes only playlists it created**.
Your stock, subscribed and hand-made playlists cannot be modified or deleted by it. To
change one, it clones it into a new playlist first.

## Use

The skill lives at `.claude/skills/kovaaks-playlists/`, so it loads automatically when
you start Claude Code in this repo — nothing to install:

```
cd claude-kovaaks
claude
```

Then just ask for what you want — "make me a warmup playlist with some easy tracking scenarios", "what's in my VDIM
tracking playlist", "import the Voltaic S5 novice benchmarks".

Requires Python 3 (stdlib only, no packages) and a KovaaK's install.

## Direct use

```
cd .claude/skills/kovaaks-playlists
python scripts/kvpl.py list
python scripts/kvpl.py show "1 - Basic"
python scripts/kvpl.py search-scenarios "smooth" --aim-type Tracking
python scripts/kvpl.py create "Warmup" --scenario "VT Aether Novice S5:2"
```

`python scripts/kvpl.py --help` lists every command. All output is JSON.

## Ownership

A playlist is editable by the skill only if its `authorName` ends with
`[claude-kovaaks]`, which the skill sets on everything it creates. Anything else exits
with code 3 and is left untouched. If you edit a skill-made playlist in-game and KovaaK's
rewrites the author field, the skill will stop being able to edit it — that is the safe
direction, and you can always clone it again.

## kvstats

A localhost dashboard over your KovaaK's run history. It watches the game's output
and, about a second after a run ends, shows that run's per-second curve against your
PB and your recent form — so you can see not just whether you did better, but where
in the run you did.

```
python -m kvstats
```

Then open the printed URL. Stdlib only, no install, no build step; it works offline.

The first launch indexes your whole history into a SQLite cache outside the game directory
(about 5 s for ~2330 runs, producing a database of about 6.3 MB). After that it only reads
new files. The cache is disposable — delete it and it rebuilds.

**It never writes to the KovaaK's install.**

Three things worth knowing about the data:

- KovaaK's writes a `.perf` time-series alongside most runs, but not all — runs
  without one still appear, just without a curve.
- Runs are only compared against runs at the same true sensitivity (cm/360), since
  comparing across a sens change is not a fair comparison. Toggle it off in the UI if
  you want to compare anyway.
- Not every scenario is scored on a clock. Some spawn a fixed number of bots and
  score you on how long you took (`score = 1000 - elapsed`). Those are charted
  against share of the damage pool rather than seconds, so bot boundaries line up
  between runs, and the delta chart reads in seconds gained or lost.

## Layout

```
.claude/skills/kovaaks-playlists/
    SKILL.md             the skill Claude reads
    scripts/kvpl.py      the CLI, single file, stdlib only
    references/kovaaks.md   on-disk format and API notes, verified against a real install
kvstats/                 the dashboard: parser, index, watcher, server, web assets
tests/                   unit tests for the playlist CLI and every kvstats module
docs/                    design spec
```

```
python -m unittest discover -s tests
```

To use the skill outside this repo as well, link the skill directory into your personal
skills folder. Symlinked skill directories are supported, and a junction (`/J`) needs no
administrator rights. Run this from the repository root:

```
mkdir "%USERPROFILE%\.claude\skills"
mklink /J "%USERPROFILE%\.claude\skills\kovaaks-playlists" ^
  "%CD%\.claude\skills\kovaaks-playlists"
```

## Scope

Two tools, deliberately separate:

- **`kovaaks-playlists`** — the Claude Code skill. Playlists only; it never reads stats.
- **`kvstats`** — a local dashboard over your run history. Read-only; it never writes
  to the game.

Game settings and benchmark rank tracking remain out of scope for both.
