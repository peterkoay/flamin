"""Copy-only rehearsal for the installed v2 Codex Step 0 layout."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
import tomllib
from pathlib import Path

from . import approvals, render
from .product import Product
from .statefile import STATE_FILES, sidecar
from .util import FlaminError, KIT_DIR, canonical_json, sha256_bytes, sha256_file

MARKER = ".flamin/v4-rehearsal.json"
CONFIG = ".codex/config.toml"
HOOK_GATES = {"codex-shell", "codex-edit"}
ADAPTER_CONFIGS = (CONFIG, ".codex/hooks.json", ".claude/settings.json",
                   ".claude/settings.local.json", ".cursor/hooks.json")
HOOK_REFERENCE = re.compile(r"flamin(?:\.cmd|\.py)?[\x22\x27]?\s+hook\b", re.I)
# Earlier v2 renders differ from the latest legacy renderer at the same kit label.
V2_RULE_HASHES = {
    "AGENTS.md": "f5e04445cd697747a4b1ac6680d14bffabc4e1f14cd2e3e8ce4e06e154e84099",
    ".codex/agents/business.toml": "0b8e38fc3e1fa39326b633c26e1a989db34ef505ed197639fb30f3d59cb9a0bc",
}


def _snapshot(root: Path) -> dict[str, str]:
    """Hash regular files only. Never follow links, junctions, or special files."""
    result = {}
    for folder, dirs, files in os.walk(root, onerror=_raise_walk_error, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(folder) / name
            info = path.lstat()
            rel = path.relative_to(root).as_posix()
            if (stat.S_ISLNK(info.st_mode) or
                    getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                raise FlaminError(f"Migration requires a standalone copy. Linked path: {rel}")
            if stat.S_ISREG(info.st_mode):
                result[rel] = sha256_file(path)
            elif not stat.S_ISDIR(info.st_mode):
                raise FlaminError(f"Unsupported file type: {rel}")
    return result


def _raise_walk_error(error: OSError) -> None:
    raise error


def _strip_codex_hooks(text: str) -> str:
    """Remove byte-identifiable generated groups. Preserve unrelated TOML verbatim."""
    parsed = tomllib.loads(text)
    ending = "\r\n" if "\r\n" in text else "\n"
    updated = text
    generated = render.codex_hooks_toml()
    for group in re.split(r"(?m)(?=^\[\[hooks\.[A-Za-z]+\]\]$)", generated)[1:]:
        group = group.rstrip("\n").replace("\n", ending)
        # Ensure the range is an entire generated group, including all its child hooks.
        match = re.search(r"(?m)^" + re.escape(group) + r"(?=\r?$)", updated)
        if match:
            updated = updated[:match.start()] + updated[match.end():]
    if HOOK_REFERENCE.search(updated):
        raise FlaminError("Unrecognized Codex FLAMIN hook configuration. Review the registration manually.")
    migrated = tomllib.loads(updated)
    before_hooks = parsed.pop("hooks", {})
    after_hooks = migrated.pop("hooks", {})
    known = tomllib.loads(generated)["hooks"]
    if parsed != migrated or any(after_hooks.get(event, []) !=
            [group for group in groups if group not in known.get(event, [])]
            for event, groups in before_hooks.items()):
        raise FlaminError("Could not separate FLAMIN hooks from other Codex settings safely.")
    banner = generated.splitlines()[0] + ending
    updated = updated.replace(banner, "", 1)
    return updated


def _paths(path: Path) -> bool:
    return any(path.as_posix().endswith("/" + name) or path.as_posix() == name
               for name in ADAPTER_CONFIGS)


def _preview(root: Path) -> tuple[dict, dict[str, str]]:
    files = _snapshot(root)
    p = Product(root)
    required = {f".flamin/{name}.json{suffix}" for name in STATE_FILES for suffix in ("", ".sha256")}
    if not required <= files.keys() or CONFIG not in files or "kit/engine/format.json" not in files:
        raise FlaminError("Unrecognized installed layout. Expected Codex configuration and format-1 state.")
    if (root / "kit/VERSION").read_text(encoding="utf-8").strip() != "v2":
        raise FlaminError("Only the installed v2 kit is supported for this rehearsal.")
    if (root / "kit/engine/format.json").read_bytes().replace(b"\r\n", b"\n") != (
            KIT_DIR / "engine/format.json").read_bytes().replace(b"\r\n", b"\n"):
        raise FlaminError("The installed engine format differs from the rehearsal engine.")
    if ".flamin/.lock" in files or p.store.checksum_problems():
        raise FlaminError("Workspace is busy or state checksums failed. Retry from a quiescent workspace.")
    if ".git" in files or ".git/objects/info/alternates" in files or ".git/commondir" in files:
        raise FlaminError("Linked Git metadata is outside the supported rehearsal layout.")
    legacy_rules = render.planned("codex")
    for rel, earlier_hash in V2_RULE_HASHES.items():
        if rel not in files:
            raise FlaminError(f"Expected installed rules at {rel}.")
        current = (root / rel).read_bytes().replace(b"\r\n", b"\n")
        if current != legacy_rules[rel].encode("utf-8") and sha256_bytes(current) != earlier_hash:
            raise FlaminError(f"Review customized rules in {rel} before rehearsal.")
    state = p.state()
    if (state.get("state_format") != 1 or state.get("tools") != ["codex"] or state.get("phase") != 0
            or state.get("stack_approved")):
        raise FlaminError("This rehearsal supports Codex workspaces at Step 0 before stack approval.")
    ledger = approvals.load(p.store)
    retire = []
    for rid, record in sorted(ledger["requests"].items()):
        if record["status"] not in {"pending", "asked"}:
            continue
        if record["kind"] != "action" or record["data"].get("gate") not in HOOK_GATES:
            raise FlaminError(f"{rid} requires formal decision migration. This rehearsal leaves that workspace unchanged.")
        retire.append(rid)

    edits = {}
    for rel in sorted(files):
        if not _paths(Path(rel)):
            continue
        content = (root / rel).read_bytes().decode("utf-8")
        if rel.endswith(CONFIG):
            new = _strip_codex_hooks(content)
            if new != content:
                edits[rel] = new
        elif HOOK_REFERENCE.search(content) or ("flamin.py" in content and '"hook"' in content):
            raise FlaminError(f"Review FLAMIN hook registration in {rel} before rehearsal.")
    if CONFIG not in edits:
        raise FlaminError("Active Codex configuration has no recognized v2 FLAMIN hook groups.")

    report = {
        "source": str(root),
        "source_snapshot": sha256_bytes(canonical_json(files).encode("utf-8")),
        "installed_kit": "v2",
        "recorded_kit": state.get("kit_version"),
        "state_format": 1,
        "change_on_copy": sorted([*edits, ".flamin/state.json", ".flamin/approvals.json",
                                  ".flamin/state.json.sha256", ".flamin/approvals.json.sha256",
                                  "AGENTS.md", ".codex/agents/business.toml", MARKER]),
        "supersede_on_copy": retire,
        "git_backstop": {
            "project_pre_commit": ".git/hooks/pre-commit" in files,
            "custom_path_declared_in_local_config": bool(re.search(
                r"(?im)^\s*hookspath\s*=", (root / ".git/config").read_text(encoding="utf-8")
            )) if ".git/config" in files else False,
            "action": "preserve",
        },
    }
    return report, edits


def preview_copy(source: Path) -> dict:
    return _preview(source.resolve())[0]


def rehearse_copy(source: Path, destination: Path, expected_snapshot: str) -> dict:
    source, destination = source.resolve(), destination.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise FlaminError("Choose a separate destination outside the source workspace.")
    if destination.exists():
        marker = destination / MARKER
        if marker.is_file() and not marker.is_symlink():
            recorded = json.loads(marker.read_text(encoding="utf-8"))
            if recorded["source"] == str(source) and recorded["source_snapshot"] == expected_snapshot:
                if Product(destination).store.checksum_problems():
                    raise FlaminError("The existing rehearsal copy failed its state integrity check.")
                return recorded
        raise FlaminError("The destination already exists. Choose an unused path.")
    report, edits = _preview(source)
    if report["source_snapshot"] != expected_snapshot:
        raise FlaminError("The source changed. Review the migration preview again.")

    # Only the completed copy becomes visible at the destination. A failed copy has no effect there.
    with tempfile.TemporaryDirectory(prefix=".flamin-copy-", dir=destination.parent) as temp:
        staging = Path(temp) / "workspace"
        shutil.copytree(source, staging, symlinks=True)
        copied, _ = _preview(staging)
        if copied["source_snapshot"] != expected_snapshot or preview_copy(source)["source_snapshot"] != expected_snapshot:
            raise FlaminError("The source changed while copying. Review the migration preview again.")

        for rel, content in edits.items():
            (staging / rel).write_bytes(content.encode("utf-8"))
        p = Product(staging)
        with p.tx() as (state, ledger):
            for rid in report["supersede_on_copy"]:
                ledger["requests"][rid].update(status="superseded", superseded_by="v4 Codex rehearsal")
            approvals.sync_open_list(state, ledger)

        for rel, content in render.codex_rehearsal().items():
            (staging / rel).write_bytes(content.encode("utf-8"))
        (staging / MARKER).write_bytes(canonical_json(report).encode("utf-8"))
        if p.store.checksum_problems():
            raise FlaminError("The migrated copy failed its state integrity check.")
        if destination.exists():
            raise FlaminError("The destination appeared during rehearsal. Choose an unused path.")
        staging.rename(destination)
    return report
