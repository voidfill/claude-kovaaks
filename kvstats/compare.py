"""Baseline selection and curve arithmetic.

Everything here is pure over lists; the only database work is candidate
selection. The load-bearing property is in `cumulative_delta`: its final value
is exactly the score difference against the baseline, so the chart and the
headline number cannot disagree.
"""

import statistics

from . import index

DEFAULT_RECENT_N = 10
DURATION_TOLERANCE = 0.10


def smooth(values, window):
    """Centred rolling mean. Preserves length; shrinks the window at the edges."""
    values = list(values)
    if window <= 1 or not values:
        return values
    half = window // 2
    out = []
    for i in range(len(values)):
        lo = max(0, i - half)
        hi = min(len(values), i + half + 1)
        chunk = values[lo:hi]
        out.append(sum(chunk) / len(chunk))
    return out


def align(a, b):
    """Truncate to the shorter prefix. Use `pad` when totals must be preserved."""
    n = min(len(a), len(b))
    return list(a[:n]), list(b[:n])


def pad(a, b):
    """Zero-extend both to the longer length."""
    n = max(len(a), len(b))
    a = list(a) + [0.0] * (n - len(a))
    b = list(b) + [0.0] * (n - len(b))
    return a, b


def cumulative_delta(mine, base):
    """Running sum of (mine - base).

    Pads rather than truncates, and that choice is load-bearing. The final
    value must equal sum(mine) - sum(base) exactly, because the score series
    sums to the run's score -- so the last point of this curve IS the score
    difference, and the chart can never disagree with the headline number.
    Truncating to the shorter prefix breaks that on ~6% of real runs, where the
    two runs differ in length.
    """
    mine, base = pad(mine, base)
    out = []
    total = 0.0
    for m, b in zip(mine, base):
        total += m - b
        out.append(total)
    return out


def compare_until(mine, base):
    """Index past which only one curve has data, for marking the tail."""
    return min(len(mine), len(base))


def band(curves):
    """Per-bucket mean and +-1 sigma across a set of equal-ish curves."""
    curves = [list(c) for c in curves if len(c)]
    if not curves:
        return {"mean": [], "lo": [], "hi": []}
    n = min(len(c) for c in curves)
    # Under three curves a standard deviation is noise pretending to be a
    # confidence band, so the band collapses onto the mean.
    banded = len(curves) >= 3
    mean, lo, hi = [], [], []
    for i in range(n):
        column = [c[i] for c in curves]
        mu = statistics.fmean(column)
        sigma = statistics.pstdev(column) if banded else 0.0
        mean.append(mu)
        lo.append(mu - sigma)
        hi.append(mu + sigma)
    return {"mean": mean, "lo": lo, "hi": hi}


def _focus(conn, run_id):
    row = conn.execute("SELECT * FROM run WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(f"no such run: {run_id}")
    return row


def candidates(conn, run_id, same_cfg=True, duration_tol=DURATION_TOLERANCE):
    """Other runs of the same scenario that this run can fairly be judged against."""
    focus = _focus(conn, run_id)
    sql = "SELECT * FROM run WHERE scenario=? AND id<>?"
    args = [focus["scenario"], run_id]
    if same_cfg and focus["cfg_key"] is not None:
        sql += " AND cfg_key IS ?"
        args.append(focus["cfg_key"])
    rows = conn.execute(sql + " ORDER BY started_at", args).fetchall()

    target = focus["duration_s"]
    if target:
        keep = []
        for row in rows:
            # Unknown duration means no .perf. Such a run still counts toward
            # score baselines; it simply cannot supply a curve.
            if row["duration_s"] is None:
                keep.append(row)
            elif abs(row["duration_s"] - target) <= target * duration_tol:
                keep.append(row)
        rows = keep
    return rows


def baselines(conn, run_id, recent_n=DEFAULT_RECENT_N, same_cfg=True,
              duration_tol=DURATION_TOLERANCE):
    focus = _focus(conn, run_id)
    rows = candidates(conn, run_id, same_cfg=same_cfg, duration_tol=duration_tol)

    result = {
        "pb": None,
        "true_pb": None,
        "recent": {"n": 0, "mean_score": None, "curve": None},
        "candidates": len(rows),
    }
    if not rows:
        return result

    scored = [r for r in rows if r["score"] is not None]
    if scored:
        true_pb = max(scored, key=lambda r: r["score"])
        result["true_pb"] = {"run_id": true_pb["id"], "score": true_pb["score"],
                             "started_at": true_pb["started_at"]}

        # The overlay must be a run we can actually draw. 35 of 316 real
        # scenarios have a PB with no .perf, so falling back is the norm.
        drawable = [r for r in scored if r["perf_file"] is not None]
        if drawable:
            pb = max(drawable, key=lambda r: r["score"])
            result["pb"] = {
                "run_id": pb["id"],
                "score": pb["score"],
                "started_at": pb["started_at"],
                "is_true_pb": pb["id"] == true_pb["id"],
                "curve": index.load_curve(conn, pb["id"]),
            }

    prior = [r for r in rows if r["started_at"] < focus["started_at"]]
    # prior[-recent_n:] misbehaves at the boundary: recent_n=0 slices as
    # prior[0:], i.e. every prior run, and a negative recent_n drops runs off
    # the front instead of returning none. Clamp here too, independent of any
    # clamp upstream -- this function has its own default and is called
    # directly by tests.
    recent_n = max(recent_n, 0)
    recent = prior[-recent_n:] if recent_n else []
    if recent:
        result["recent"]["n"] = len(recent)
        result["recent"]["mean_score"] = statistics.fmean(
            [r["score"] for r in recent if r["score"] is not None] or [0.0])
        curves = [index.load_curve(conn, r["id"]) for r in recent
                  if r["perf_file"] is not None]
        curves = [c for c in curves if c]
        if curves:
            result["recent"]["curve"] = curves
    return result
