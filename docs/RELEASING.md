# Publishing the master kit

The current kit version is `v1` in `kit/VERSION`. Its GitHub tag and Release use `v1`; the matching GitHub npm registry package is `@peterkoay/flamin@1.0.0`. This is the master-kit version, separate from versions of products made with the kit.

The recommended download is the full-kit ZIP or tar.gz from GitHub Releases. Both extract to `flamin-v1/`, ready to keep as a master kit. The npm package contains the same files under npm's `package/` root and has no runtime npm dependencies. Python 3.11 or newer remains required. npm does not turn `flamin` into a JavaScript command or install Python.

## Local release candidate

From the repository root, while master-kit maintenance mode is on:

```text
python -B -m unittest discover -s kit/engine/tests -t kit/engine -v
python -B -m unittest discover -s tests -t . -v
.\flamin.cmd kit-maintenance test       # Windows PowerShell
python -B tools/build_release.py --output-dir dist
```

On Linux or macOS, use `./flamin kit-maintenance test`. The builder checks `kit/MANIFEST` against every tracked kit file and launcher before writing `dist/flamin-v1.zip`, `dist/flamin-v1.tar.gz`, `dist/flamin-v1-npm.tgz`, and `dist/flamin-v1-SHA256SUMS.txt`. File order, timestamps, owners, and modes are fixed so identical source files yield identical archive bytes. It packages tracked files only, and explicitly excludes the machine-local Cursor hook; local product state, maintenance flags, ignored secrets, untracked adapters and caches cannot enter the archive. Do not commit `dist/`.

When npm is installed, `npm pack --dry-run --offline --ignore-scripts dist/flamin-v1-npm.tgz` inspects the file list without publishing. `docs/RELEASE_NOTES_v1.md` supplies the GitHub Release text.

## Publication procedure

Review the release candidate, make a clean commit, and end maintenance mode in a human terminal. Publishing is a release gate. After explicit human approval for the exact `v1` release and package, a maintainer runs:

```text
git tag -a v1 -m "Flamin v1"
git push origin v1
```

The tag-triggered [release workflow](../.github/workflows/release.yml) checks that the tag matches `kit/VERSION`, runs the engine and distribution tests on Python 3.11 and 3.13 across Linux, macOS, and Windows, then builds the archives. Only the final publish job has `contents: write` and `packages: write`. It creates the GitHub Release, attaches the ZIP, tar.gz, npm tarball and checksum file, and publishes the tarball to GitHub's npm registry. The workflow uses its repository `GITHUB_TOKEN`; no token is placed in source or a command line. Repository Actions settings must permit this token to publish releases and packages.

For a later kit release, update `kit/VERSION` and add `docs/RELEASE_NOTES_<tag>.md` before tagging. The workflow derives the notes path and npm tarball name from the tag. Run `flamin kit-maintenance test` after kit changes.

## Using the registry package

The registry is `https://npm.pkg.github.com`; the package name is `@peterkoay/flamin`. Access to a private repository's package requires GitHub Packages authentication and package read access. Configure authentication in your own user-level npm settings, then install the chosen version from the GitHub registry. Copy the installed package contents from `node_modules/@peterkoay/flamin/` into a separate master-kit folder before starting a product. For ordinary use, downloading the GitHub Release archive avoids npm setup.

GitHub's [npm registry documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-npm-registry) explains authentication and package permissions.
