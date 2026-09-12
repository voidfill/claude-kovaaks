"""Scenario scoring shapes.

Most scenarios are scored on a fixed clock. Ten in the reference install are
not: they spawn a fixed number of bots and score on elapsed time, writing the
score series as a literal countdown. Telling the two apart is what lets the
dashboard pick an axis that means something.

Everything here is pure over already-parsed values -- no database, no file IO.
The index calls it with rows it already holds; the tests call it with literals.
"""

RACE = "race"
TIMED = "timed"

# >=90%, not 100%: perf.py buckets samples by floor(timestamp), so timing jitter
# occasionally merges two ticks into one bucket (a 0 beside a -2). A strict rule
# scores 123/126 on real runs; this one scores 126/126 with no false positives.
COUNTDOWN_MIN_FRACTION = 0.90
# The series round-trips through float32, which moves -1 by up to ~0.0025.
COUNTDOWN_TOLERANCE = 0.01

BUDGET_TOLERANCE = 0.1
MIN_DURATION_SPREAD = 1.0


def countdown_budget(score_series):
    """The budget if `score_series` is a countdown clock, else None.

    A race scenario's score arrives as [budget-1, -1, -1, ...]: the first
    bucket seeds the clock and every later one is a second ticking off. The
    final bucket is a partial second, so it is excluded rather than tested.
    """
    values = list(score_series)
    if len(values) < 4 or values[0] <= 0:
        return None
    body = values[1:-1]
    if not body:
        return None
    ticks = sum(1 for v in body if abs(v + 1.0) < COUNTDOWN_TOLERANCE)
    if ticks / len(body) < COUNTDOWN_MIN_FRACTION:
        return None
    return values[0] + 1.0


def budget_from_totals(pairs):
    """The budget if (score, elapsed) pairs show a constant budget over
    varying time, else None.

    The fallback for race scenarios with no .perf. Two runs minimum, and that
    minimum is the whole point: 6 of 2360 fixed-clock runs have a score that
    lands near a round hundred minus their clock, so one run is a coincidence.
    Two runs agreeing on a budget while disagreeing on time is not.
    """
    usable = [(s, e) for s, e in pairs if s is not None and e]
    if len(usable) < 2:
        return None
    budgets = [score + elapsed for score, elapsed in usable]
    elapsed = [e for _, e in usable]
    if max(budgets) - min(budgets) > BUDGET_TOLERANCE:
        return None
    if max(elapsed) - min(elapsed) < MIN_DURATION_SPREAD:
        return None
    return sum(budgets) / len(budgets)


def is_penalising(score_series):
    """Whether any second of the run cost points outright."""
    return any(v < 0 for v in score_series)


def classify(curves, totals):
    """-> {"shape", "budget", "evidence"}.

    `curves` is every score series available for the scenario, `totals` every
    (score, elapsed) pair. Curve evidence wins outright: it settles a scenario
    from a single run, where the totals test needs two.
    """
    for series in curves:
        budget = countdown_budget(series)
        if budget is not None:
            return {"shape": RACE, "budget": budget, "evidence": "perf-countdown"}

    budget = budget_from_totals(totals)
    if budget is not None:
        return {"shape": RACE, "budget": budget, "evidence": "csv-constant-budget"}

    return {"shape": TIMED, "budget": None, "evidence": "default"}
