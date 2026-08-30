# Voltaic Benchmarks — Season 5

Everything below was read from the KovaaK's web index on 2026-08-30, not from any
particular machine. None of it assumes the user has anything downloaded.

## Identity

Voltaic, abbreviated **VT** in scenario names. Current season **S5**; every benchmark
scenario name ends in `S5`. The S5 scenarios in the index are authored by `Tammas`.

**S5.5** is an in-progress iteration on S5 — it adds two scenarios and changes some score
requirements. Nothing in the scenario index carries an `S5.5` marker, so treat S5.5
requests as S5 unless the user says otherwise, and expect the two extra scenarios not to
resolve.

Season 4 scenarios (`… S4`) still exist in the index and are superseded — do not use them
unless the user asks for S4 by name.

## Difficulty tiers

Six tier words appear in S5 scenario names, easiest first:

`Entry` · `Novice` · `Intermediate` · `Adept` · `Advanced` · `Elite`

All 18 scenarios exist at all six tiers.

**Only Novice, Intermediate and Advanced carry the rank ladder below.** Entry sits under
Novice, Adept between Intermediate and Advanced, Elite above Advanced; where their ranks
fall was not verified. If a user names a rank, use the ladder; if they name a tier, use
the tier.

## Ranks

Twelve ranks, four per ranked tier:

| Tier | Ranks (ascending) |
|---|---|
| Novice | Iron, Bronze, Silver, Gold |
| Intermediate | Platinum, Diamond, Jade, Master |
| Advanced | Grandmaster, Nova, Astra, Celestial |

So "diamond level" means the Intermediate tier. Ranks are per-scenario as well as
overall; a user is usually quoting their overall rank.

## Groups

Three categories, three subcategories each, two scenarios per subcategory — 18 total.

| Category | Subcategory | Scenario stems |
|---|---|---|
| Clicking | Dynamic | `Pasu`, `Popcorn` |
| Clicking | Static | *(varies by tier — see below)*, `ww5t` |
| Clicking | Linear | `Frogtagon`, `Floating Heads` |
| Tracking | Precise | `PGT`, `Snake Track` |
| Tracking | Reactive | `Aether`, `Ground` |
| Tracking | Control | `Raw Control`, `Controlsphere` |
| Switching | Speed | `DotTS`, `EddieTS` |
| Switching | Evasive | `DriftTS`, `FlyTS` |
| Switching | Stability | `ControlTS`, `Penta Bounce` |

## Scenario names

Mechanical: **`VT <stem> <Tier> S5`** — e.g. `VT PGT Intermediate S5`,
`VT Penta Bounce Elite S5`. Every one of the 108 combinations resolves in the index.

The one exception is the first Static Clicking stem, where the target count drops as the
tier rises:

| Tier | Stem |
|---|---|
| Entry, Novice | `1w4ts` |
| Intermediate, Adept | `1w3ts` |
| Advanced, Elite | `1w2ts` |

Because the rule is mechanical, a Voltaic playlist can be built for any tier without
reading a benchmark playlist. To read the official set instead, find it with
`search-playlists "Voltaic Benchmarks S5"`.

## Training method — VDIM

**VDIM** (Voltaic Daily Improvement Method), created by **lowgravity56**, is how Voltaic
is trained. It works up to each benchmark scenario rather than just running it.

The canonical playlists are published by **`4rK`**, six per tier, named
`VDIM <Tier> S5 - <Category> <I|II>`. Dozens of near-identical community edits share
those names, so **import by id**:

| Tier | Clicking I | Clicking II | Tracking I | Tracking II | Switching I | Switching II |
|---|---|---|---|---|---|---|
| Entry | 317679 | 317680 | 317681 | 317682 | 317683 | 317684 |
| Novice | 317691 | 317692 | 317693 | 317694 | 317695 | 317696 |
| Intermediate | 317685 | 317686 | 317687 | 317688 | 317689 | 317690 |
| Adept | 473509 | 473508 | 473507 | 473506 | 473505 | 473504 |
| Advanced | 317667 | 317668 | 317669 | 317670 | 317671 | 317672 |
| Elite | 317673 | 317674 | 317675 | 317676 | 317677 | 317678 |

To read a VDIM playlist's contents without installing it, use
`search-playlists "<name>" --full` and pick the result whose `playlistId` matches the
table. Only import when the user wants the playlist itself.

> The Switching playlist names contain an invisible **U+200E** between the dash and the
> word (`VDIM Novice S5 -‎ Switching I`). Never type them; match on the id, or on the
> name returned by a search. See `../kovaaks.md`.

Each category splits across two playlists, and the split is **not** by subcategory — it
groups three benchmark scenarios per playlist:

| Playlist | Benchmark scenarios it builds toward |
|---|---|
| `Clicking I` | static stem (`1w3ts`…), `ww5t`, `Floating Heads` |
| `Clicking II` | `Pasu`, `Popcorn`, `Frogtagon` |
| `Tracking I` | `Snake Track`, `PGT`, `Raw Control` |
| `Tracking II` | `Ground`, `Controlsphere`, `Aether` |
| `Switching I` | `DotTS`, `EddieTS`, `Penta Bounce` |
| `Switching II` | `FlyTS`, `DriftTS`, `ControlTS` |

So a single-subcategory request usually needs blocks from **both** playlists in that
category — e.g. Tracking → Control is `Raw Control` from `Tracking I` and `Controlsphere`
from `Tracking II`. (Verified against the Intermediate playlists; the other tiers follow
the same layout but confirm with `--full` before relying on an exact position.)

### Block structure

Every benchmark scenario gets a contiguous block, in this order:

```
2-6 warmup / drill scenarios (not VT benchmark scenarios)   x2
    official sub-variants of the benchmark scenario         x2-3
    <benchmark scenario> Hard                               x3
    <benchmark scenario>                                    x4
```

Example — the `PGT` block from `VDIM Intermediate S5 - Tracking I`:

```
VT PGT Intermediate S5 Precision Focus   x2
VT PGT Intermediate S5 Bot 1             x2
VT PGT Intermediate S5 Bot 2             x2
VT PGT Intermediate S5 Bot 3             x2
VT PGT Intermediate S5 Reactive Focus    x2
VT PGT Intermediate S5 Hard              x3
VT PGT Intermediate S5                   x4
```

To copy a block, read the VDIM playlist with `search-playlists --full` and take the
contiguous run ending at the benchmark scenario.

### Sub-variant suffixes

Appended to the full benchmark name. Which ones exist depends on the category:

| Category | Suffixes seen |
|---|---|
| Clicking | `Pokeball`, `Pokeball Small`, `Clusters`, `Speed`, `Multi`, `2 Targets`, `3 Targets`, `Hard` |
| Tracking | `Precision Focus`, `Reactive Focus`, `Bot 1`, `Bot 2`, `Bot 3`, `Hard` |
| Switching | `Speed`, `Evasive`, `Static`, `Regen`, `Smooth`, `Hard` |

Not every suffix exists for every scenario — resolve with `search-scenarios` before use.
VDIM blocks also pull in separate VT training scenarios that are not sub-variants
(`VT Quadpulse`, `VT Widepulse`, `VT Poptrack`, `VT skyTS`, `VT Speedswitch 180`, …);
those only come from reading a VDIM playlist.
