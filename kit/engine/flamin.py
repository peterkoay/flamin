# flamin engine entry point.
# Keep this file readable by very old Python: the version check must run
# before any modern syntax is parsed, so a too-old Python gets a clear message.
import sys

if sys.version_info < (3, 11):
    sys.stderr.write(
        "flamin needs Python 3.11 or newer. This Python is %d.%d.\n"
        "Install a current Python and run 'flamin doctor'.\n"
        % (sys.version_info[0], sys.version_info[1])
    )
    sys.exit(3)

import os

sys.dont_write_bytecode = True  # keep the kit free of caches (DESIGN §2.3)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flaminlib.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
