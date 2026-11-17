# Flamin v4 — master-kit release

Flamin v4 packages the copyable master kit for Claude Code, Codex, and Cursor. It includes the Python 3.11+ engine, launchers, agent adapters, stack templates, documentation, and the `kit/MANIFEST` integrity record.

## Changes since v1

- Codex can use FLAMIN tools to read workspace status, see the next saved action, and propose saving intake answers from chat. Formal decisions require human confirmation.
- The snapshot verifier checks a staged tree or candidate commit against a trusted base for changes that violate FLAMIN's file and workflow rules.
- Master-kit maintainers can authorize targeted validation or explicitly skip local tests for a specific commit. The authorization is bound to the current HEAD and exact staged contents.

## Download and verify

Download `flamin-v4.zip` or `flamin-v4.tar.gz`, extract it into a new master-kit folder, and copy that folder for each product. The GitHub npm package `@peterkoay/flamin@4.0.0` distributes the same kit files. Run the kit with `flamin` or `flamin.cmd` and Python 3.11 or newer.

The archives exclude repository history, product state, maintenance flags, caches, and machine-local files. Use `flamin-v4-SHA256SUMS.txt` to verify the ZIP, tar.gz, and npm tarball.
