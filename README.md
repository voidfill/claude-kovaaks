# claude-kovaaks

A Claude Code skill for managing local KovaaK's playlists — create and edit them, and
search the KovaaK's scenario and playlist index to fill them.

The skill can **read** every playlist you have but **writes only playlists it created**.
Your stock, subscribed and hand-made playlists cannot be modified or deleted by it. To
change one, it clones it into a new playlist first.

## Install

Claude Code discovers skills from `~/.claude/skills/<name>/` (all projects) and
`<project>/.claude/skills/<name>/` (that project only) — it does not scan arbitrary
directories. Since managing playlists has nothing to do with whichever repo you happen
to be working in, this belongs in the personal location.

The repo root *is* the skill directory, so either put the repo there directly:

```
git clone <this repo> "%USERPROFILE%\.claude\skills\kovaaks-playlists"
```

or keep it wherever you like and link it in — symlinked skill directories are supported,
and a junction (`/J`) needs no administrator rights:

```
mkdir "%USERPROFILE%\.claude\skills"
mklink /J "%USERPROFILE%\.claude\skills\kovaaks-playlists" "C:\Users\8alex\git\claude-kovaaks"
```

If `~/.claude/skills` did not exist when your Claude Code session started, restart it so
the new directory gets watched. Then just ask Claude for what you want — "make me a warmup playlist with some easy tracking scenarios", "what's in my VDIM
tracking playlist", "import the Voltaic S5 novice benchmarks".

Requires Python 3 (stdlib only, no packages) and a KovaaK's install.

## Direct use

```
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
SKILL.md               the skill Claude reads
scripts/kvpl.py        the CLI, single file, stdlib only
references/kovaaks.md  on-disk format and API notes, all verified against a real install
tests/                 four tests: encodings, byte-exact writes, the gate, cloning
docs/                  design spec
```

```
python -m unittest discover -s tests
```

## Scope

Playlists only. Stats analysis, game settings and benchmark tracking are deliberately
out of scope.
