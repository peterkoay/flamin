"""Shared context for commands and hooks: state access, audit, ledger."""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

from . import audit
from .profiles import product_profiles
from .statefile import Store, flamin_dir
from .util import KIT_DIR, FlaminError, iso, project_root, read_text, rel


def kit_version() -> str:
    return (read_text(KIT_DIR / "VERSION") or "v1").strip()


class Product:
    def __init__(self, root: Path | None = None, tool: str = "cli", agent: str | None = None):
        self.root = root or project_root()
        self.store = Store(self.root)
        self.fdir = flamin_dir(self.root)
        self.tool = tool
        self.agent = agent
        self._profiles = None

    # ------------------------------------------------------------ state
    def require(self) -> None:
        if not self.store.exists():
            raise FlaminError("No product here yet. Say \"start a new project\" or run `flamin init`.")

    def state(self) -> dict:
        self.require()
        return self.store.load("state")

    def save_state(self, state: dict) -> None:
        self.store.save("state", state)

    @contextlib.contextmanager
    def tx(self):
        """Read-modify-write of state under the lock. Yields (state, approvals)."""
        self.require()
        with self.store.transaction():
            state = self.store.load("state")
            appr = self.store.load("approvals", default={"requests": {}, "assumptions": {}})
            before = (json.dumps(state, sort_keys=True), json.dumps(appr, sort_keys=True))
            yield state, appr
            if json.dumps(state, sort_keys=True) != before[0]:
                self.store.save("state", state)
            if json.dumps(appr, sort_keys=True) != before[1]:
                self.store.save("approvals", appr)

    def profiles(self):
        if self._profiles is None:
            self._profiles = product_profiles(self.root)
        return self._profiles

    def modules(self) -> dict:
        return self.store.load("modules", default={})

    def locks(self) -> dict:
        return self.store.load("locks", default={"files": {}})

    # ------------------------------------------------------------ logging
    def log(self, action: str, target: str = "", decision: str = "allow", reason: str = "",
            approver: str | None = None, **extra) -> None:
        e = audit.make_entry(action, target=target, decision=decision, reason=reason, tool=self.tool,
                             agent=self.agent, approver=approver, root=self.root, **extra)
        audit.append(e, root=self.root)

    def ledger(self, step: str, module: str = "-", files: list[str] | None = None, decisions: str = "") -> None:
        """Hard step-boundary checkpoint (P-06): one entry per engine step command."""
        p = self.fdir / "sessions" / "ledger.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        new = not p.exists()
        with open(p, "a", encoding="utf-8", newline="\n") as fh:
            if new:
                fh.write("# Step ledger\n\nWritten by the engine after every step command. Do not edit.\n\n")
            fh.write(f"- {iso()} | {step} | module: {module} | files: {', '.join(files or []) or '-'}"
                     f" | {decisions or '-'}\n")

    def relpath(self, path) -> str:
        return rel(path, self.root)

    def tmp(self) -> Path:
        d = self.fdir / "tmp"
        d.mkdir(parents=True, exist_ok=True)
        return d


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes")
