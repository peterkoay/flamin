"""Release archives contain exactly the intended, verified kit files."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile
import tarfile

from tools.build_release import INCLUDED_ROOT_FILES, tracked_files, verify_kit_manifest, write_archives


class ReleaseArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        contents = {name: b"sample\n" for name in INCLUDED_ROOT_FILES}
        contents["kit/VERSION"] = b"v1\n"
        contents["kit/engine/flamin.py"] = b"print('sample')\n"
        contents[".cursor/hooks.json"] = b"machine local hook\n"
        lines = ["# manifest\n"]
        for name in sorted(set(contents) & {"flamin", "flamin.cmd"} | {name for name in contents if name.startswith("kit/")}):
            lines.append(f"{hashlib.sha256(contents[name]).hexdigest()}  {name}\n")
        contents["kit/MANIFEST"] = "".join(lines).encode()
        for name, data in contents.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True, capture_output=True)

    def test_archives_reproducible_and_exclude_local_files(self):
        (self.root / ".flamin-kit-maintenance").write_text("on")
        (self.root / ".codex/agents/orchestrator.toml").parent.mkdir(parents=True)
        (self.root / ".codex/agents/orchestrator.toml").write_text("untracked")
        paths = write_archives(self.root, self.root / "dist")
        first_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
        self.assertEqual(first_hashes, [hashlib.sha256(path.read_bytes()).hexdigest() for path in write_archives(self.root, self.root / "dist")])
        with zipfile.ZipFile(paths[0]) as archive:
            names = archive.namelist()
            self.assertIn("flamin-v1/kit/VERSION", names)
            self.assertNotIn("flamin-v1/.cursor/hooks.json", names)
            self.assertNotIn("flamin-v1/.codex/agents/orchestrator.toml", names)
            self.assertNotIn("flamin-v1/.flamin-kit-maintenance", names)
        with tarfile.open(paths[1], "r:gz") as archive:
            self.assertEqual(set(archive.getnames()), set(names))
        with tarfile.open(paths[2], "r:gz") as archive:
            npm_names = set(archive.getnames())
            self.assertEqual(npm_names, {name.replace("flamin-v1/", "package/", 1) for name in names} | {"package/package.json"})
            metadata = json.load(archive.extractfile("package/package.json"))
            self.assertEqual((metadata["name"], metadata["version"]), ("@peterkoay/flamin", "1.0.0"))

    def test_manifest_mismatch_fails_closed(self):
        (self.root / "kit/VERSION").write_text("v2\n")
        with self.assertRaisesRegex(ValueError, "kit/MANIFEST hash mismatch: kit/VERSION"):
            verify_kit_manifest(tracked_files(self.root))


if __name__ == "__main__":
    unittest.main()
