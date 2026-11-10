"""Machine entry point for previewing and rehearsing a copy-only v2 migration."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from flaminlib.rehearsal import preview_copy, rehearse_copy
from flaminlib.util import FlaminError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--copy-to", type=Path)
    parser.add_argument("--expected-snapshot")
    args = parser.parse_args()
    if (args.copy_to is None) != (args.expected_snapshot is None):
        parser.error("--copy-to and --expected-snapshot must be supplied together")
    try:
        result = (preview_copy(args.source) if args.copy_to is None else
                  rehearse_copy(args.source, args.copy_to, args.expected_snapshot))
    except (FlaminError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"FLAMIN migration rehearsal: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
