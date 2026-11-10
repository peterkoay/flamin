"""Machine-only entry point. Run from a trusted installation with an explicit base commit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from flaminlib.snapshot import verify_snapshot
from flaminlib.util import FlaminError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("trusted_base")
    parser.add_argument("--candidate", help="Full commit ID. Omit to freeze and check the index.")
    args = parser.parse_args()
    try:
        report = verify_snapshot(args.repository.resolve(), args.trusted_base, args.candidate)
    except (FlaminError, ValueError, OSError, KeyError, TypeError) as exc:
        print(f"FLAMIN snapshot verification: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
