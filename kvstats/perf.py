"""Decoding `performances/*.perf`.

KovaaK's publishes no schema for this file. The layout below was recovered from
the protobuf wire format and validated by summation against the CSV totals: for
a tracking run the series add to 6001 shots / 3913 hits / 2088 misses / score
3913.0, matching its CSV exactly, and likewise for a clicking run.

    top level
      field 1  header submessage
                 1 scenario name, 2 hash, 3 epoch-ms start,
                 4 unknown int, 5 submessage (bot file, map, weapon)
      field 2  repeated sample: field 1 = float timestamp, plus exactly one
               metric submessage whose field number selects the series

THE TRAP: a metric is omitted for a second in which it was zero, so sample
order is not time order and the series have different lengths in the file. Every
series is rebuilt here on a dense floor(timestamp) grid.
"""

import array
import struct

SERIES = ("shots", "hits", "misses", "dmg_done", "dmg_possible", "score", "kills")

_FIELD_TO_SERIES = {
    2: "shots",
    3: "hits",
    4: "misses",
    5: "dmg_done",
    6: "dmg_possible",
    7: "score",
    8: "kills",
}


class PerfError(Exception):
    """The file is absent, empty, truncated, or not in the expected layout."""


def _varint(buf, i):
    result = shift = 0
    while True:
        if i >= len(buf):
            raise PerfError("truncated varint")
        byte = buf[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, i
        shift += 7
        if shift > 63:
            raise PerfError("varint too long")


def _fields(buf):
    """Yield (field_number, wire_type, payload) at one nesting level."""
    i = 0
    end = len(buf)
    while i < end:
        key, i = _varint(buf, i)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _varint(buf, i)
        elif wire == 5:
            value, i = buf[i:i + 4], i + 4
            if len(value) != 4:
                raise PerfError("truncated fixed32")
        elif wire == 1:
            value, i = buf[i:i + 8], i + 8
            if len(value) != 8:
                raise PerfError("truncated fixed64")
        elif wire == 2:
            length, i = _varint(buf, i)
            value, i = buf[i:i + length], i + length
            if len(value) != length:
                raise PerfError("truncated length-delimited field")
        else:
            raise PerfError(f"unsupported wire type {wire}")
        yield number, wire, value


def _f32(raw):
    return struct.unpack("<f", raw)[0]


def _header(payload):
    out = {"scenario": None, "hash": None, "started_ms": None}
    for number, wire, value in _fields(payload):
        if number == 1 and wire == 2:
            out["scenario"] = value.decode("utf-8", "replace")
        elif number == 2 and wire == 2:
            out["hash"] = value.decode("utf-8", "replace")
        elif number == 3 and wire == 0:
            out["started_ms"] = value
    return out


def _sample(payload):
    """-> (timestamp, series_name, value) or None if it carries no known metric."""
    timestamp = None
    found = None
    for number, wire, value in _fields(payload):
        if number == 1 and wire == 5:
            timestamp = _f32(value)
        elif wire == 2 and number in _FIELD_TO_SERIES:
            for inner_number, inner_wire, inner in _fields(value):
                if inner_number != 1:
                    continue
                found = (
                    _FIELD_TO_SERIES[number],
                    _f32(inner) if inner_wire == 5 else float(inner),
                )
    if timestamp is None or found is None:
        return None
    return timestamp, found[0], found[1]


def parse(path):
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as error:
        raise PerfError(f"cannot read {path}: {error}") from error
    if not raw:
        raise PerfError(f"empty file: {path}")

    header = {}
    samples = []
    last_timestamp = 0.0
    for number, wire, value in _fields(raw):
        if number == 1 and wire == 2:
            header = _header(value)
        elif number == 2 and wire == 2:
            parsed = _sample(value)
            if parsed is None:
                continue
            samples.append(parsed)
            last_timestamp = max(last_timestamp, parsed[0])

    if not samples:
        raise PerfError(f"no samples decoded from {path}")

    # Dense grid. floor(timestamp) is the bucket; gaps stay zero.
    buckets = int(last_timestamp) + 1
    series = {name: array.array("f", bytes(4 * buckets)) for name in SERIES}
    for timestamp, name, value in samples:
        index = int(timestamp)
        if 0 <= index < buckets:
            series[name][index] += value

    return {
        "buckets": buckets,
        "duration_s": last_timestamp,
        "series": series,
        "header": header,
    }
