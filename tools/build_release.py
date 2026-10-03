"""Build reproducible, copyable Flamin master-kit archives from tracked files.

Usage: python -B tools/build_release.py --output-dir dist
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
FIXED_TIME = (2020, 1, 1, 0, 0, 0)
INCLUDED_ROOT_FILES = {
    ".gitattributes", ".gitignore", "AGENTS.md", "CLAUDE.md",
    "LICENSE", "README.md", "flamin", "flamin.cmd",
}
INCLUDED_DIRECTORIES = ("kit/", "docs/", ".claude/", ".codex/", ".cursor/")
EXCLUDED_PATHS = {".cursor/hooks.json", ".claude/settings.local.json"}
EXCLUDED_PARTS = {".git", ".flamin", "__pycache__", ".pytest_cache", ".mypy_cache", "secrets"}


def version(root: Path) -> str:
    value = (root / "kit" / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"v[0-9]+(?:\.[0-9]+){0,2}", value):
        raise ValueError(f"Invalid kit/VERSION: {value!r}")
    return value


def tracked_files(root: Path) -> list[tuple[str, int, bytes]]:
    result = subprocess.run(
        ["git", "ls-files", "--stage", "-z"], cwd=root, check=True, capture_output=True
    )
    files = []
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode = int(metadata.split()[0], 8)
        name = raw_path.decode("utf-8", "surrogateescape").replace("\\", "/")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or not name or name.startswith("/"):
            raise ValueError(f"Unsafe tracked path: {name!r}")
        if name in EXCLUDED_PATHS or any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        if name not in INCLUDED_ROOT_FILES and not name.startswith(INCLUDED_DIRECTORIES):
            continue
        if mode not in (0o100644, 0o100755):
            raise ValueError(f"Cannot package non-regular tracked file: {name} ({mode:o})")
        disk_path = root / name
        if disk_path.is_symlink() or not disk_path.is_file():
            raise ValueError(f"Missing or symlinked tracked file: {name}")
        files.append((name, mode, disk_path.read_bytes()))
    names = {name for name, _, _ in files}
    required = INCLUDED_ROOT_FILES | {"kit/VERSION", "kit/MANIFEST"}
    missing = sorted(required - names)
    if missing:
        raise ValueError(f"Missing required tracked files: {', '.join(missing)}")
    return sorted(files)


def verify_kit_manifest(files: list[tuple[str, int, bytes]]) -> None:
    data = {name: contents for name, _, contents in files}
    manifest = data["kit/MANIFEST"].decode("utf-8")
    expected = {}
    for line in manifest.splitlines():
        if not line or line.startswith("#"):
            continue
        digest, separator, name = line.partition("  ")
        if separator != "  " or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"Malformed kit/MANIFEST entry: {line!r}")
        expected[name] = digest
    actual = {name for name in data if (name.startswith("kit/") and name != "kit/MANIFEST") or name in ("flamin", "flamin.cmd")}
    if set(expected) != actual:
        raise ValueError("kit/MANIFEST does not match tracked kit files and launchers")
    for name in sorted(actual):
        normalized = data[name].decode("utf-8", "surrogateescape").replace("\r\n", "\n")
        digest = hashlib.sha256(normalized.encode("utf-8", "surrogateescape")).hexdigest()
        if expected[name] != digest:
            raise ValueError(f"kit/MANIFEST hash mismatch: {name}")


def write_archives(root: Path, output_dir: Path) -> list[Path]:
    release_version = version(root)
    files = tracked_files(root)
    verify_kit_manifest(files)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"flamin-{release_version}"
    zip_path = output_dir / f"{stem}.zip"
    tar_path = output_dir / f"{stem}.tar.gz"
    npm_path = output_dir / f"{stem}-npm.tgz"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, mode, contents in files:
            info = zipfile.ZipInfo(f"{stem}/{name}", FIXED_TIME)
            info.create_system = 3
            info.external_attr = (mode << 16)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, contents, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    with tar_path.open("wb") as output, gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0, compresslevel=9) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for name, mode, contents in files:
                info = tarfile.TarInfo(f"{stem}/{name}")
                info.size = len(contents)
                info.mode = mode & 0o777
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                archive.addfile(info, io.BytesIO(contents))
    package_metadata = {
        "name": "@peterkoay/flamin",
        "version": release_version.removeprefix("v") + (".0.0" if release_version.count(".") == 0 else ".0" if release_version.count(".") == 1 else ""),
        "description": "Copyable Flamin AI software-building kit (requires Python 3.11+)",
        "license": "MIT",
        "repository": {"type": "git", "url": "git+https://github.com/peterkoay/flamin.git"},
        "publishConfig": {"registry": "https://npm.pkg.github.com"},
    }
    metadata_bytes = (json.dumps(package_metadata, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with npm_path.open("wb") as output, gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0, compresslevel=9) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for name, mode, contents in [("package.json", 0o100644, metadata_bytes), *files]:
                info = tarfile.TarInfo(f"package/{name}")
                info.size = len(contents)
                info.mode = mode & 0o777
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                archive.addfile(info, io.BytesIO(contents))
    checksum_path = output_dir / f"{stem}-SHA256SUMS.txt"
    checksum_path.write_text(
        "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in (zip_path, tar_path, npm_path)),
        encoding="ascii", newline="\n",
    )
    return [zip_path, tar_path, npm_path, checksum_path]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    for path in write_archives(ROOT, args.output_dir.resolve()):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
