# Flamin v1 — first master-kit release

Flamin v1 packages the copyable master kit for Claude Code, Codex, and Cursor. It includes the Python 3.11+ standard-library engine, launchers, rendered agent adapters, stack templates, documentation, and the `kit/MANIFEST` integrity record.

Download `flamin-v1.zip` or `flamin-v1.tar.gz`, extract it into a new master-kit folder, and copy that folder for each product. The `@peterkoay/flamin` GitHub npm package contains the same kit files for teams that distribute tools through a package registry. The package is a file distribution; the kit runs through `flamin` or `flamin.cmd` with Python installed.

The archives exclude repository history, product state, maintenance flags, caches, and machine-local files. `flamin-v1-SHA256SUMS.txt` provides checksums for all three distributions.
