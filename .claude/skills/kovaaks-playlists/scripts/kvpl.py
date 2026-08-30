#!/usr/bin/env python3
"""kvpl - manage local KovaaK's playlists.

Reads any playlist; writes only playlists it created. Ownership is decided by the
authorName field ending with the AUTHOR_TAG marker. See references/kovaaks.md.
"""

import argparse
import glob
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

AUTHOR_TAG = "[claude-kovaaks]"
API_BASE = "https://kovaaks.com/webapp-backend"
DEFAULT_ROOT = r"C:\Program Files (x86)\Steam\steamapps\common\FPSAimTrainer\FPSAimTrainer"
WORKSHOP_APPID = "824270"

EXIT_INPUT = 2
EXIT_OWNERSHIP = 3
EXIT_CONFIG = 4

# The game writes these keys in this order; we reproduce it exactly.
KEY_ORDER = [
    "playlistName", "playlistId", "authorSteamId", "authorName", "scenarioList",
    "description", "hasOfflineScenarios", "hasEdited", "shareCode", "version",
    "updated", "isPrivate",
]

PLAYLIST_VERSION = 31
BAD_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class Fail(Exception):
    def __init__(self, message, code=EXIT_INPUT):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- config

class Config:
    def __init__(self):
        self.root = os.environ.get("KOVAAKS_DIR", DEFAULT_ROOT)
        self.playlists = os.path.join(self.root, "Saved", "SaveGames", "Playlists")
        self.scenarios = os.path.join(self.root, "Saved", "SaveGames", "Scenarios")
        workshop = os.environ.get("KOVAAKS_WORKSHOP_DIR")
        if not workshop:
            workshop = os.path.abspath(os.path.join(
                self.root, "..", "..", "..", "workshop", "content", WORKSHOP_APPID))
        self.workshop = workshop
        self._login = None

    def require_playlists_dir(self):
        if not os.path.isdir(self.playlists):
            raise Fail(
                "Playlists directory not found: %s\n"
                "Set KOVAAKS_DIR to your FPSAimTrainer install root." % self.playlists,
                EXIT_CONFIG)
        return self.playlists

    def _loginusers(self):
        """(steam_id, persona_name) for the most recent Steam login, or None."""
        if self._login is not None:
            return self._login or None
        path = os.path.abspath(os.path.join(
            self.root, "..", "..", "..", "..", "config", "loginusers.vdf"))
        found = None
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            self._login = ()
            return None
        for steam_id, body in re.findall(r'"(7656\d+)"\s*\{(.*?)\n\s*\}', text, re.S):
            persona = re.search(r'"PersonaName"\s+"([^"]*)"', body)
            entry = (steam_id, persona.group(1) if persona else "")
            if re.search(r'"MostRecent"\s+"1"', body):
                found = entry
                break
            if found is None:
                found = entry
        self._login = found or ()
        return found

    @property
    def steam_id(self):
        value = os.environ.get("KOVAAKS_STEAM_ID")
        if value:
            return value
        login = self._loginusers()
        if not login:
            raise Fail(
                "Could not determine your SteamID from loginusers.vdf. "
                "Set KOVAAKS_STEAM_ID.", EXIT_CONFIG)
        return login[0]

    @property
    def author_name(self):
        value = os.environ.get("KOVAAKS_AUTHOR_NAME")
        if value:
            return value if value.rstrip().endswith(AUTHOR_TAG) else "%s %s" % (value, AUTHOR_TAG)
        login = self._loginusers()
        persona = (login[1] if login else "") or "kovaaks"
        return "%s %s" % (persona, AUTHOR_TAG)


# ------------------------------------------------------------------------ file layer

def read_text(path):
    """Read a KovaaK's JSON file in any of the encodings the game emits."""
    with open(path, "rb") as handle:
        raw = handle.read()
    if raw[:2] == b"\xff\xfe":
        return raw.decode("utf-16-le").lstrip("\ufeff")
    if raw[:2] == b"\xfe\xff":
        return raw.decode("utf-16-be").lstrip("\ufeff")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw.decode("utf-8-sig")
    return raw.decode("utf-8")


def dump_bytes(data):
    """Serialize exactly the way the game does: UTF-8, CRLF, tabs, no trailing newline."""
    ordered = {key: data[key] for key in KEY_ORDER if key in data}
    for key, value in data.items():
        ordered.setdefault(key, value)
    text = json.dumps(ordered, indent="\t", separators=(",", ": "), ensure_ascii=False)
    return text.replace("\n", "\r\n").encode("utf-8")


def write_playlist(path, data):
    blob = dump_bytes(data)
    tmp = path + ".kvpl-tmp"
    with open(tmp, "wb") as handle:
        handle.write(blob)
    os.replace(tmp, path)


def check_name(name):
    if not name or not name.strip():
        raise Fail("Playlist name may not be empty.")
    if BAD_NAME_CHARS.search(name):
        raise Fail('Playlist name may not contain any of <>:"/\\|?* or control characters.')
    if name.strip(". ") != name:
        raise Fail("Playlist name may not start or end with a dot or space.")
    return name


def playlist_path(cfg, name):
    return os.path.join(cfg.require_playlists_dir(), check_name(name) + ".json")


def load_playlist(cfg, name):
    path = playlist_path(cfg, name)
    if not os.path.isfile(path):
        raise Fail('No playlist named "%s". Run `list` to see what exists.' % name)
    try:
        return path, json.loads(read_text(path))
    except (ValueError, UnicodeDecodeError) as exc:
        raise Fail("Could not parse %s: %s" % (path, exc))


def is_managed(data):
    return str(data.get("authorName", "")).rstrip().endswith(AUTHOR_TAG)


def assert_managed(name, data):
    if not is_managed(data):
        raise Fail(
            'Refusing to modify "%s": it was not created by this skill '
            '(authorName does not end with %s).\n'
            'To work from it, clone it first:\n'
            '  create "<new name>" --from-playlist "%s"' % (name, AUTHOR_TAG, name),
            EXIT_OWNERSHIP)


def installed_index(cfg):
    names = set()
    for pattern in (os.path.join(cfg.scenarios, "*.sce"),
                    os.path.join(cfg.workshop, "*", "*.sce")):
        for path in glob.glob(pattern):
            names.add(os.path.splitext(os.path.basename(path))[0])
    return names


def scenario_entry(name, count):
    return {"scenario_name": name, "play_Count": int(count)}


def new_playlist(cfg, name, scenarios, description, installed):
    return {
        "playlistName": name,
        "playlistId": 0,
        "authorSteamId": cfg.steam_id,
        "authorName": cfg.author_name,
        "scenarioList": scenarios,
        "description": description,
        "hasOfflineScenarios": any(s["scenario_name"] not in installed for s in scenarios),
        "hasEdited": True,
        "shareCode": "",
        "version": PLAYLIST_VERSION,
        "updated": int(time.time()),
        "isPrivate": False,
    }


def touch(data, installed):
    """Refresh the fields that must change on every edit."""
    data["updated"] = int(time.time())
    data["hasEdited"] = True
    data["hasOfflineScenarios"] = any(
        s.get("scenario_name") not in installed for s in data.get("scenarioList", []))


def find_scenario(data, name):
    for index, entry in enumerate(data.get("scenarioList", [])):
        if entry.get("scenario_name") == name:
            return index
    lowered = name.lower()
    matches = [i for i, e in enumerate(data.get("scenarioList", []))
               if str(e.get("scenario_name", "")).lower() == lowered]
    if len(matches) == 1:
        return matches[0]
    raise Fail('"%s" is not in this playlist.' % name)


def parse_scenario_arg(text, default_count=1):
    """"Name" or "Name:3" -> (name, count)."""
    if ":" in text:
        head, _, tail = text.rpartition(":")
        if head and tail.strip().isdigit():
            return head, int(tail)
    return text, default_count


# ------------------------------------------------------------------------- api layer

def api_get(path, params=None):
    url = API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "kvpl/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise Fail("KovaaK's API request failed (%s): %s" % (url, exc))
    except ValueError as exc:
        raise Fail("KovaaK's API returned malformed JSON (%s): %s" % (url, exc))


def search_scenarios_api(query, max_results):
    payload = api_get("/scenario/popular",
                      {"page": 0, "max": max_results, "scenarioNameSearch": query})
    return payload.get("data", []), payload.get("total", 0)


def search_playlists_api(query, max_results):
    payload = api_get("/playlist/playlists",
                      {"page": 0, "max": max_results, "search": query.lower()})
    return payload.get("data", []), payload.get("total", 0)


# -------------------------------------------------------------------------- commands

def out(payload):
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def cmd_list(cfg, args):
    rows = []
    for path in sorted(glob.glob(os.path.join(cfg.require_playlists_dir(), "*.json"))):
        name = os.path.splitext(os.path.basename(path))[0]
        try:
            data = json.loads(read_text(path))
        except (ValueError, UnicodeDecodeError) as exc:
            rows.append({"name": name, "error": str(exc)})
            continue
        rows.append({
            "name": data.get("playlistName", name),
            "managed": is_managed(data),
            "scenarios": len(data.get("scenarioList", [])),
            "author": data.get("authorName", ""),
        })
    if args.managed_only:
        rows = [r for r in rows if r.get("managed")]
    out({"playlists": rows, "count": len(rows),
         "managed": sum(1 for r in rows if r.get("managed"))})


def cmd_show(cfg, args):
    _, data = load_playlist(cfg, args.name)
    installed = installed_index(cfg)
    scenarios = []
    for index, entry in enumerate(data.get("scenarioList", [])):
        name = entry.get("scenario_name", "")
        scenarios.append({
            "index": index,
            "name": name,
            "playCount": entry.get("play_Count"),
            "installed": name in installed,
        })
    out({
        "name": data.get("playlistName"),
        "managed": is_managed(data),
        "author": data.get("authorName"),
        "playlistId": data.get("playlistId"),
        "description": data.get("description", ""),
        "scenarioCount": len(scenarios),
        "notInstalled": sum(1 for s in scenarios if not s["installed"]),
        "scenarios": scenarios,
    })


def cmd_installed(cfg, args):
    names = sorted(installed_index(cfg))
    if args.search:
        needle = args.search.lower()
        names = [n for n in names if needle in n.lower()]
    total = len(names)
    shown = names if args.max <= 0 else names[:args.max]
    out({"total": total, "shown": len(shown), "scenarios": shown})


def cmd_create(cfg, args):
    path = playlist_path(cfg, args.name)
    if os.path.exists(path):
        raise Fail('A playlist named "%s" already exists; pick another name.' % args.name)

    scenarios = []
    description = args.description or ""
    if args.from_playlist:
        _, source = load_playlist(cfg, args.from_playlist)
        for entry in source.get("scenarioList", []):
            scenarios.append(scenario_entry(entry.get("scenario_name", ""),
                                            entry.get("play_Count", 1)))
        if args.description is None:
            description = source.get("description", "")
    for raw in args.scenario or []:
        name, count = parse_scenario_arg(raw)
        scenarios.append(scenario_entry(name, count))

    installed = installed_index(cfg)
    data = new_playlist(cfg, args.name, scenarios, description, installed)
    write_playlist(path, data)
    out({
        "created": args.name,
        "file": path,
        "author": data["authorName"],
        "scenarios": len(scenarios),
        "notInstalled": [s["scenario_name"] for s in scenarios
                         if s["scenario_name"] not in installed],
    })


def cmd_add(cfg, args):
    path, data = load_playlist(cfg, args.name)
    assert_managed(args.name, data)
    installed = installed_index(cfg)
    entry = scenario_entry(args.scenario, args.count)
    scenarios = data.setdefault("scenarioList", [])
    if args.at is None or args.at >= len(scenarios):
        scenarios.append(entry)
        index = len(scenarios) - 1
    else:
        index = max(0, args.at)
        scenarios.insert(index, entry)
    touch(data, installed)
    write_playlist(path, data)
    result = {"playlist": args.name, "added": args.scenario,
              "playCount": args.count, "index": index,
              "scenarios": len(scenarios)}
    if args.scenario not in installed:
        result["warning"] = ('"%s" is not installed locally. KovaaK\'s may prompt to '
                             "download it, or the entry may not be playable."
                             % args.scenario)
    out(result)


def cmd_remove(cfg, args):
    path, data = load_playlist(cfg, args.name)
    assert_managed(args.name, data)
    scenarios = data.setdefault("scenarioList", [])
    if args.index is not None:
        if not 0 <= args.index < len(scenarios):
            raise Fail("Index %d is out of range (0-%d)." % (args.index, len(scenarios) - 1))
        index = args.index
    else:
        index = find_scenario(data, args.scenario)
    removed = scenarios.pop(index)
    touch(data, installed_index(cfg))
    write_playlist(path, data)
    out({"playlist": args.name, "removed": removed.get("scenario_name"),
         "index": index, "scenarios": len(scenarios)})


def cmd_move(cfg, args):
    path, data = load_playlist(cfg, args.name)
    assert_managed(args.name, data)
    scenarios = data.setdefault("scenarioList", [])
    if not 0 <= args.from_index < len(scenarios):
        raise Fail("--from %d is out of range (0-%d)." % (args.from_index, len(scenarios) - 1))
    if not 0 <= args.to_index < len(scenarios):
        raise Fail("--to %d is out of range (0-%d)." % (args.to_index, len(scenarios) - 1))
    entry = scenarios.pop(args.from_index)
    scenarios.insert(args.to_index, entry)
    touch(data, installed_index(cfg))
    write_playlist(path, data)
    out({"playlist": args.name, "moved": entry.get("scenario_name"),
         "from": args.from_index, "to": args.to_index})


def cmd_set_count(cfg, args):
    path, data = load_playlist(cfg, args.name)
    assert_managed(args.name, data)
    index = find_scenario(data, args.scenario)
    data["scenarioList"][index]["play_Count"] = args.count
    touch(data, installed_index(cfg))
    write_playlist(path, data)
    out({"playlist": args.name, "scenario": args.scenario, "playCount": args.count})


def cmd_set_description(cfg, args):
    path, data = load_playlist(cfg, args.name)
    assert_managed(args.name, data)
    data["description"] = args.description
    touch(data, installed_index(cfg))
    write_playlist(path, data)
    out({"playlist": args.name, "description": args.description})


SIBLING_FILES = ["LocalFavoritePlaylists.json", "PlaylistInProgress.json"]


def sibling_references(cfg, name):
    hits = []
    savegames = os.path.dirname(cfg.playlists)
    for filename in SIBLING_FILES:
        path = os.path.join(savegames, filename)
        if not os.path.isfile(path):
            continue
        try:
            if name in read_text(path):
                hits.append(filename)
        except (OSError, UnicodeDecodeError):
            continue
    return hits


def cmd_rename(cfg, args):
    path, data = load_playlist(cfg, args.name)
    assert_managed(args.name, data)
    target = playlist_path(cfg, args.new_name)
    if os.path.exists(target):
        raise Fail('A playlist named "%s" already exists.' % args.new_name)
    stale = sibling_references(cfg, args.name)
    data["playlistName"] = args.new_name
    touch(data, installed_index(cfg))
    write_playlist(target, data)
    os.remove(path)
    result = {"renamed": args.name, "to": args.new_name, "file": target}
    if stale:
        result["warning"] = ("The old name still appears in %s; KovaaK's may show a "
                             "stale favourite or in-progress entry until you fix it "
                             "in-game." % ", ".join(stale))
    out(result)


def cmd_delete(cfg, args):
    path, data = load_playlist(cfg, args.name)
    assert_managed(args.name, data)
    os.remove(path)
    out({"deleted": args.name, "file": path})


def cmd_search_scenarios(cfg, args):
    data, total = search_scenarios_api(args.query, args.max)
    installed = installed_index(cfg)
    rows = []
    for item in data:
        scenario = item.get("scenario") or {}
        aim_type = scenario.get("aimType")
        if args.aim_type and (aim_type or "").lower() != args.aim_type.lower():
            continue
        name = item.get("scenarioName", "")
        rows.append({
            "name": name,
            "leaderboardId": item.get("leaderboardId"),
            "aimType": aim_type,
            "authors": scenario.get("authors", []),
            "plays": (item.get("counts") or {}).get("plays"),
            "installed": name in installed,
        })
    out({"query": args.query, "total": total, "results": rows})


def cmd_search_playlists(cfg, args):
    data, total = search_playlists_api(args.query, args.max)
    rows = []
    for item in data:
        row = {
            "name": item.get("playlistName"),
            "playlistId": item.get("playlistId"),
            "playlistCode": item.get("playlistCode"),
            "author": item.get("webappUsername") or item.get("steamAccountName"),
            "subscribers": item.get("subscribers"),
            "scenarios": len(item.get("scenarioList") or []),
            "description": item.get("description", ""),
        }
        if args.full:
            row["scenarioList"] = [
                {"name": s.get("scenarioName"), "playCount": s.get("playCount"),
                 "aimType": s.get("aimType")}
                for s in item.get("scenarioList") or []]
        rows.append(row)
    out({"query": args.query, "total": total, "results": rows})


def cmd_scenario_info(cfg, args):
    leaderboard_id = args.leaderboard_id
    if leaderboard_id is None:
        data, _ = search_scenarios_api(args.query, 1)
        if not data:
            raise Fail('No scenario found matching "%s".' % args.query)
        leaderboard_id = data[0].get("leaderboardId")
    info = api_get("/scenario/details", {"leaderboardId": leaderboard_id})
    info["leaderboardId"] = leaderboard_id
    info["installed"] = info.get("scenarioName") in installed_index(cfg)
    out(info)


def cmd_import(cfg, args):
    path = playlist_path(cfg, args.new_name)
    if os.path.exists(path):
        raise Fail('A playlist named "%s" already exists; pick another name.' % args.new_name)
    data, _ = search_playlists_api(args.query, args.max)
    if not data:
        raise Fail('No playlist found matching "%s".' % args.query)
    if args.playlist_id is not None:
        matches = [p for p in data if p.get("playlistId") == args.playlist_id]
        if not matches:
            raise Fail("Playlist id %d was not in the first %d results for \"%s\"; "
                       "refine the query or raise --max."
                       % (args.playlist_id, args.max, args.query))
        source = matches[0]
    else:
        source = data[0]

    scenarios = [scenario_entry(s.get("scenarioName", ""), s.get("playCount", 1))
                 for s in source.get("scenarioList") or []]
    installed = installed_index(cfg)
    playlist = new_playlist(cfg, args.new_name, scenarios,
                            args.description if args.description is not None
                            else source.get("description", ""), installed)
    write_playlist(path, playlist)
    out({
        "imported": source.get("playlistName"),
        "as": args.new_name,
        "file": path,
        "sourcePlaylistId": source.get("playlistId"),
        "sourceCode": source.get("playlistCode"),
        "scenarios": len(scenarios),
        "notInstalled": [s["scenario_name"] for s in scenarios
                         if s["scenario_name"] not in installed],
    })


# ---------------------------------------------------------------------------- parser

def build_parser():
    parser = argparse.ArgumentParser(
        prog="kvpl", description="Manage local KovaaK's playlists.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="list every local playlist")
    p.add_argument("--managed-only", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="show one playlist in full")
    p.add_argument("name")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("installed", help="list locally installed scenarios")
    p.add_argument("--search")
    p.add_argument("--max", type=int, default=50, help="0 for no limit")
    p.set_defaults(func=cmd_installed)

    p = sub.add_parser("create", help="create a new managed playlist")
    p.add_argument("name")
    p.add_argument("--description", default=None)
    p.add_argument("--from-playlist", dest="from_playlist",
                   help="clone the scenario list of an existing playlist")
    p.add_argument("--scenario", action="append", metavar="NAME[:COUNT]")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("add", help="add a scenario to a managed playlist")
    p.add_argument("name")
    p.add_argument("--scenario", required=True)
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--at", type=int, default=None, help="insert position")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("remove", help="remove a scenario from a managed playlist")
    p.add_argument("name")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--scenario")
    group.add_argument("--index", type=int)
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("move", help="reorder a scenario within a managed playlist")
    p.add_argument("name")
    p.add_argument("--from", dest="from_index", type=int, required=True)
    p.add_argument("--to", dest="to_index", type=int, required=True)
    p.set_defaults(func=cmd_move)

    p = sub.add_parser("set-count", help="set a scenario's play count")
    p.add_argument("name")
    p.add_argument("--scenario", required=True)
    p.add_argument("--count", type=int, required=True)
    p.set_defaults(func=cmd_set_count)

    p = sub.add_parser("set-description", help="set a playlist's description")
    p.add_argument("name")
    p.add_argument("--description", required=True)
    p.set_defaults(func=cmd_set_description)

    p = sub.add_parser("rename", help="rename a managed playlist")
    p.add_argument("name")
    p.add_argument("new_name", metavar="new-name")
    p.set_defaults(func=cmd_rename)

    p = sub.add_parser("delete", help="delete a managed playlist")
    p.add_argument("name")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("search-scenarios", help="search the KovaaK's scenario index")
    p.add_argument("query")
    p.add_argument("--aim-type", dest="aim_type")
    p.add_argument("--max", type=int, default=15)
    p.set_defaults(func=cmd_search_scenarios)

    p = sub.add_parser("search-playlists", help="search community playlists")
    p.add_argument("query")
    p.add_argument("--max", type=int, default=10)
    p.add_argument("--full", action="store_true", help="include full scenario lists")
    p.set_defaults(func=cmd_search_playlists)

    p = sub.add_parser("scenario-info", help="details for one scenario")
    p.add_argument("query", nargs="?")
    p.add_argument("--leaderboard-id", dest="leaderboard_id", type=int)
    p.set_defaults(func=cmd_scenario_info)

    p = sub.add_parser("import", help="import a community playlist as a managed copy")
    p.add_argument("query")
    p.add_argument("--as", dest="new_name", required=True)
    p.add_argument("--playlist-id", dest="playlist_id", type=int,
                   help="pick this id from the search results")
    p.add_argument("--description", default=None)
    p.add_argument("--max", type=int, default=10)
    p.set_defaults(func=cmd_import)

    return parser


def main(argv=None):
    # Playlist names can contain characters the Windows console codepage cannot encode
    # (four stock playlists carry an invisible U+200E), so force UTF-8 output.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    if args.command == "scenario-info" and not args.query and args.leaderboard_id is None:
        print("scenario-info needs a query or --leaderboard-id", file=sys.stderr)
        return EXIT_INPUT
    try:
        args.func(Config(), args)
    except Fail as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    return 0


if __name__ == "__main__":
    sys.exit(main())
