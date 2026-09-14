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

## Layout

```
.claude/skills/kovaaks-playlists/
    SKILL.md             the skill Claude reads
    scripts/kvpl.py      the CLI, single file, stdlib only
    references/kovaaks.md   on-disk format and API notes, verified against a real install
tests/                   unit tests for the playlist CLI
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

Playlists only — the skill never reads your stats. The run-history dashboard that used to
live here as `kvstats` is now [aimcurve](https://github.com/voidfill/aimcurve), its own
repository under its own name.

Game settings and benchmark rank tracking remain out of scope.
