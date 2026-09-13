# Splitting kvstats into its own repository

A standalone change, independent of the browser work and worth doing whether or
not that proceeds.

## Why

`kovaaks-playlists` and `kvstats` share no code. No import crosses between them
in either direction; the only shared artefact is `references/kovaaks.md`, which
is prose.

They do not share an audience either. The skill is for Claude Code users who
clone a repository and run an agent. kvstats is for KovaaK's players who want to
look at their stats. Sending a player to something called **claude-kovaaks** to
get a stats dashboard misinforms them about what it is and what it requires —
and if the browser version ships, it wants its own GitHub Pages origin anyway.

The repository's own README already says the two tools are "deliberately
separate." Only the directory layout disagrees.

## What moves

Everything kvstats: the `kvstats/` package, `tests/test_kvstats_*.py`,
`tests/fixtures/kvstats/`, `scripts/drive-client.mjs`, and the kvstats specs —
including the three browser documents.

**The fixtures go with kvstats, and so does the frozen Python.** An earlier
draft created a contradiction here by saying the fixtures were "shared between
the Python and the JS" while also splitting the repositories. There is no
contradiction once both implementations live in the *same* new repository: the
Python is the oracle, the JS is the product, and `tests/fixtures/kvstats/` is
shared within one tree. Nothing needs to reach across a repository boundary.

`claude-kovaaks` keeps the skill, `tests/test_kvpl.py`,
`tests/fixtures/stock-utf8.json`, and the playlist spec — and becomes an honest
name.

`references/kovaaks.md` is duplicated into both. It is 109 lines of prose, and
duplicating it costs less than a dependency between two repositories. The halves
will drift; that is acceptable for notes and would not be for code.

## How

Carry the history with `git filter-repo` rather than starting fresh — there is
real work in those commits, and the specs only make sense alongside the commits
that implemented them.

## Open question

The new repository's name. It is what people will type and link, so it matters
more than anything else in this document. `kvstats` is taken as a concept by
nobody in particular but should be checked against the existing KovaaK's tool
ecosystem before being claimed.
