# Aim benchmarks

A **benchmark** is a fixed set of scenarios with published score thresholds, used to
measure aim skill and to decide what to train. Users talk about aim almost entirely in
benchmark vocabulary — "precise tracking", "intermediate", "diamond" — so translating
that vocabulary into exact scenario names is most of the work.

Scenario names, tiers and groupings below were verified against the live KovaaK's web
index on 2026-08-30. Names change between seasons; re-verify before trusting an entry
that fails to resolve.

**Never assume anything is installed.** These docs and the workflow below work from the
web index, which is the same for everyone. `installed` and `show` describe one machine
and must not be the source for a scenario name, a benchmark's contents, or which tiers
exist — use `search-scenarios` and `search-playlists --full` for those. A playlist may
reference scenarios the user does not own; KovaaK's prompts to download them. Use
`installed` only at the end, to tell the user what they will need to download.

## Vocabulary

| Term | Meaning |
|---|---|
| **Season** | A numbered revision of a benchmark. Only the current one matters (Voltaic S5, Viscose S2). Scenario names usually carry the season. |
| **Difficulty tier** | A whole copy of the benchmark at one difficulty — Voltaic `Intermediate`, Viscose `Medium`. Every scenario exists once per tier, usually as a differently-named variant. |
| **Group** / **subgroup** | How a benchmark carves its scenarios up — "Tracking → Precise". Also called category/subcategory. |
| **Rank** | A named score band a user earns. Ranks sit *inside* a tier, so a rank name implies a tier. |
| **Sub-variant** | A non-benchmark scenario derived from a benchmark scenario for training, e.g. `VT PGT Intermediate S5 Precision Focus`. Not scored by the benchmark. |

**Group names are per-benchmark and deliberately loose.** Voltaic says
"Tracking → Precise"; Viscose says "Control Tracking → Wrist". They do not line up, and
new benchmarks will invent their own again. Never normalise them into one scheme, never
assume a group name from one benchmark exists in another, and never invent a group a
benchmark file does not list.

## Benchmarks

| Benchmark | Also written | File | Current season | Trained by |
|---|---|---|---|---|
| Voltaic | VT | [benchmarks/voltaic-s5.md](benchmarks/voltaic-s5.md) | S5 (S5.5 is an in-progress iteration) | VDIM |
| Viscose | — | [benchmarks/viscose-s2.md](benchmarks/viscose-s2.md) | S2 | running the benchmark itself |

Open only the file for the benchmark in play.

## Rough cross-walk

Only for guessing which benchmark a user's loose phrasing points at. **Approximate** —
confirm with the user rather than silently substituting one for the other.

| Loose phrasing | Voltaic S5 | Viscose S2 |
|---|---|---|
| smooth / control tracking | Tracking → Control | Control Tracking (all four subgroups) |
| precise tracking | Tracking → Precise | Control Tracking → Blending |
| reactive tracking | Tracking → Reactive | Reactive Tracking |
| switching / target switching | Switching | Flick Tech |
| speed switching | Switching → Speed | Flick Tech → Speed |
| clicking | Clicking | Click Timing |
| static clicking | Clicking → Static | Flick Tech → Micro/Stability |
| dynamic clicking | Clicking → Dynamic | Click Timing → Reading |
| flicking | *(no direct group)* | Flick Tech |

## Answering a playlist request

A request like *"make me a precise tracking playlist for around intermediate"* resolves
in this order:

1. **Benchmark.** Ask if unstated — Voltaic and Viscose produce completely different
   playlists. What the user already has installed is at most a hint, never an answer.
2. **Difficulty.** A tier word (`Intermediate`), a rank name (`diamond`), or a hedge
   ("around intermediate"). Rank names map to tiers in each benchmark's file.
3. **Group.** Match the user's words against that benchmark's own group table.
4. **Source — always ask, do not assume.** This changes the playlist more than anything
   else, and the options differ per benchmark. For Voltaic:
   - benchmark scenarios only (short, measuring not training),
   - benchmark scenarios plus their official sub-variants,
   - the full VDIM block, including its non-benchmark warmup and drill scenarios.
5. **Resolve every name** with `search-scenarios` — it queries the web index, so it works
   regardless of what is downloaded. Names in these docs are a starting point, not a
   guarantee: treat a name that does not resolve as a doc bug and say so rather than
   writing it anyway.
6. **Create the playlist**, then run `show` and tell the user which scenarios they still
   need to download. Not being installed is never a reason to leave a scenario out.

## Play counts

Taken from the VDIM playlists published by their author, which is the only observed
authority on this:

| Role in the playlist | Count |
|---|---|
| Warmup / general drill scenario | 2 |
| Official sub-variant of a benchmark scenario | 3 |
| The `Hard` variant | 3 |
| The benchmark scenario itself | 4 |

A playlist meant purely to *run* the benchmark uses **1** for every scenario.

## Time budgets

Users often ask for a session of a given length — *"a 30 minute precise tracking
playlist"*. Every command that reports a playlist returns `estimatedMinutes`, so read the
number rather than doing the arithmetic by hand.

The model:

```
minutes = runs x 1.0            play time
        + entries x 1.0         loading a scenario and settling into it
        + (runs - entries) x 0.5  restarting one already on screen
```

Play time is flat at one minute. Scenarios are built around a 60s run because fatigue
dominates past that — all 18 Voltaic S5 benchmark scenarios are exactly 60s. Viscose
varies a little (48-75s), and a few of its entries carry `Timelimit=1000` because they end
on a kill count rather than a clock; neither is worth modelling, and reading `.sce` files
would only work for scenarios the user has installed.

What that buys, for scale:

| Playlist | Estimate |
|---|---|
| Voltaic Intermediate Benchmarks S5 (18 entries, 18 runs) | 36 min |
| Viscose Benchmark S2 - Medium (39 entries, 39 runs) | 78 min |
| VDIM Intermediate S5 - Tracking I (30 entries, 75 runs) | 128 min |
| VDIM Intermediate S5 - Clicking I (30 entries, 80 runs) | 135 min |

So a full VDIM day is a two-hour commitment and a whole benchmark run is over half an
hour. A 30-minute request means a slice of a block, not a block — say so rather than
silently delivering something much shorter than the source material.

### Trimming to a budget

Build the natural playlist first, then cut in this order, re-reading `estimatedMinutes`
as you go:

1. **Reduce play counts**, floor of 1, keeping every scenario. Coverage matters more than
   repetition when time is short.
2. **Still over — drop scenarios**, in this order: general warmups, then drills, then
   official sub-variants. Keep the benchmark scenario and its `Hard` variant to the end;
   a block that never reaches what it builds toward is not worth playing.
3. **Report the estimate against the ask.** If the honest minimum still overshoots — one
   subcategory at count 1 is 2 scenarios and 4 minutes for Voltaic, but a full VDIM block
   cannot compress below roughly half its length — tell the user the number and let them
   decide, rather than cutting past the point where the playlist still does its job.

## Adding a new benchmark

Drop one file in `benchmarks/` and add a row to the table above. Keep the same headings
so the agent can read an unfamiliar benchmark the same way:

1. **Identity** — full name, abbreviation, season, author, and the `playlistId` of each
   official playlist where community re-uploads share its name.
2. **Difficulty tiers** — ordered easiest to hardest, with the exact tier word as it
   appears in scenario names.
3. **Groups** — the benchmark's own group/subgroup names. Its own, not Voltaic's.
4. **Scenario table** — group → subgroup → exact scenario name per tier, or a naming rule
   if the names are mechanical.
5. **Training method** — how people actually train it, and what sources exist for step 4
   above.
6. **Ranks** — the ladder, and which tier each rank falls in.

State plainly which parts were verified and which were supplied by the user, and source
every scenario name from the web index rather than from a local install.
