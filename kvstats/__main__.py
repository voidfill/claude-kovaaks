"""python -m kvstats [--port N]"""

import argparse
import sys

from . import paths, server


def main(argv=None):
    parser = argparse.ArgumentParser(prog="kvstats")
    parser.add_argument("--port", type=int, default=8777)
    args = parser.parse_args(argv)
    try:
        cfg = paths.load()
    except paths.Fail as error:
        print(error, file=sys.stderr)
        return error.code
    server.serve(cfg, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
