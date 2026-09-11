"""Reading `stats/*.csv`.

The file is three blocks, not a CSV table: a per-kill matrix, a `Key:,Value`
summary, and a `Key:,Value` settings snapshot. Only lines whose key ends in ':'
are pairs; everything else is a kill row and is skipped -- v1 does not consume
per-kill data.
"""

import os
import re

# cm/360 = C / (DPI * Sens Increment). Empirically derived: across 2083 runs
# whose Sens Scale is literally cm/360, DPI * increment * label is constant to
# 7 significant figures. See the spec's "Sensitivity normalisation" section.
CM360_CONSTANT = 13062.86

FILENAME = re.compile(
    r"^(?P<scenario>.+) - Challenge - "
    r"(?P<y>\d{4})\.(?P<mo>\d{2})\.(?P<d>\d{2})-"
    r"(?P<h>\d{2})\.(?P<mi>\d{2})\.(?P<s>\d{2}) Stats\.csv$"
)

_FLOATS = {
    "Score": "score",
    "Damage Done": "damage_done",
    "Avg TTK": "avg_ttk",
    "Fight Time": "fight_time",
    "Horiz Sens": "sens_raw",
    "Sens Increment": "sens_increment",
    "FOV": "fov",
    "Avg FPS": "avg_fps",
}
_INTS = {
    "Kills": "kills",
    "Hit Count": "hits",
    "Miss Count": "misses",
    "Pause Count": "pause_count",
    "DPI": "dpi",
}
_STRINGS = {
    "Scenario": "scenario",
    "Hash": "hash",
    "Game Version": "game_version",
    "Sens Scale": "sens_scale",
    "FOVScale": "fov_scale",
    "Resolution": "resolution",
}


def cm360(dpi, sens_increment):
    """True centimetres per 360, independent of the active Sens Scale.

    Returns None when the inputs cannot produce a value rather than raising --
    a run with an unreadable sens is still a valid run.
    """
    try:
        denominator = float(dpi) * float(sens_increment)
    except (TypeError, ValueError):
        return None
    if denominator == 0:
        return None
    return CM360_CONSTANT / denominator


def parse_filename(basename):
    """('VT PGT Intermediate S5', '2026-09-10T17:17:33') or None."""
    match = FILENAME.match(basename)
    if not match:
        return None
    parts = match.groupdict()
    started = "{y}-{mo}-{d}T{h}:{mi}:{s}".format(**parts)
    return parts["scenario"], started


def _number(text, cast):
    try:
        return cast(float(text))
    except (TypeError, ValueError):
        return None


def parse(path):
    row = {key: None for key in
           list(_FLOATS.values()) + list(_INTS.values()) + list(_STRINGS.values())}

    named = parse_filename(os.path.basename(path))
    row["scenario"], row["started_at"] = named if named else (None, None)

    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            key, sep, value = line.partition(",")
            if not sep or not key.endswith(":"):
                continue  # kill row, blank line, or a table header
            key = key[:-1]
            value = value.strip()
            if key in _FLOATS:
                row[_FLOATS[key]] = _number(value, float)
            elif key in _INTS:
                row[_INTS[key]] = _number(value, int)
            elif key in _STRINGS:
                row[_STRINGS[key]] = value

    hits, misses = row["hits"], row["misses"]
    row["shots"] = (hits + misses) if hits is not None and misses is not None else None
    row["accuracy"] = (hits / row["shots"]) if row["shots"] else None

    # damage_possible is not a summary key; it is only in the per-weapon block.
    # The curve carries it, so leave it None here rather than guessing.
    row["damage_possible"] = None

    exact = cm360(row["dpi"], row["sens_increment"])
    row["cm360"] = round(exact, 2) if exact is not None else None
    row["cfg_key"] = f"{exact:.1f}" if exact is not None else None
    return row
