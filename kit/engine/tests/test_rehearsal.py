"""Targeted coverage for a copy-only Codex Step 0 migration."""

from pathlib import Path
from unittest.mock import patch

from helpers import Project

from flaminlib import approvals, phase, rehearsal, render
from flaminlib.product import Product
from flaminlib.statefile import STATE_FILES
from flaminlib.util import FlaminError, KIT_DIR


class RehearsalTests(Project):
    copy_kit = False

    def setUp(self):
        super().setUp()
        self.copy = self.tmp / "separate-copy"
        self.write("kit/VERSION", "v2\n")
        self.write("kit/engine/format.json", (KIT_DIR / "engine/format.json").read_text(encoding="utf-8"))
        rules = render.planned("codex")
        for path in rehearsal.V2_RULE_HASHES:
            self.write(path, rules[path])
        self.config = rules[rehearsal.CONFIG] + '\n[mcp_servers.local]\ncommand = "unrelated-tool"\n'
        self.write(rehearsal.CONFIG, self.config)
        self.write(".git/config", "[core]\n\thooksPath = other-hooks\n")
        self.write(".git/hooks/pre-commit", "git hook stays\n")
        self.write(".flamin/business/overview.md", "Product artifact\n")
        p = Product(self.root)
        for name in STATE_FILES:
            p.store.save(name, {})
        state = phase.new_state("v1")
        state.update({"tools": ["codex"], "state_format": 1})
        p.save_state(state)
        with p.tx() as (state, ledger):
            ledger.update(approvals.empty())
            approvals.open_request(p.store, state, ledger, "action", "Old edit", "exact", [],
                                   {"gate": "codex-edit"})

    def test_preserves_source_and_custom_settings(self):
        preview = rehearsal.preview_copy(self.root)
        snapshot = preview["source_snapshot"]
        self.assertEqual(rehearsal.rehearse_copy(self.root, self.copy, snapshot), preview)
        self.assertEqual(rehearsal.preview_copy(self.root), preview)
        self.assertEqual(rehearsal.rehearse_copy(self.root, self.copy, snapshot), preview)
        migrated = Product(self.copy)
        self.assertEqual(migrated.state()["open_requests"], [])
        item = approvals.load(migrated.store)["requests"]["R-0001"]
        self.assertEqual((item["status"], item["payload"]), ("superseded", "exact"))
        self.assertEqual(migrated.store.checksum_problems(), [])
        self.assertTrue(preview["git_backstop"]["custom_path_declared_in_local_config"])
        for path in (".git/config", ".git/hooks/pre-commit", ".flamin/business/overview.md"):
            self.assertEqual((self.copy / path).read_bytes(), (self.root / path).read_bytes())
        self.assertIn('command = "unrelated-tool"', (self.copy / rehearsal.CONFIG).read_text(encoding="utf-8"))

    def test_rejects_stale_preview_and_unknown_hooks(self):
        snapshot = rehearsal.preview_copy(self.root)["source_snapshot"]
        self.write("unrelated.txt", "changed after preview")
        with self.assertRaisesRegex(FlaminError, "source changed"):
            rehearsal.rehearse_copy(self.root, self.copy, snapshot)
        self.assertFalse(self.copy.exists())
        self.write(rehearsal.CONFIG, self.config.replace("timeout = 30", "timeout = 29"))
        with self.assertRaisesRegex(FlaminError, "Unrecognized Codex FLAMIN hook"):
            rehearsal.preview_copy(self.root)

    def test_failed_copy_leaves_no_destination_and_can_retry(self):
        snapshot = rehearsal.preview_copy(self.root)["source_snapshot"]
        with patch.object(render, "codex_rehearsal", side_effect=OSError("write failed")):
            with self.assertRaisesRegex(OSError, "write failed"):
                rehearsal.rehearse_copy(self.root, self.copy, snapshot)
        self.assertFalse(self.copy.exists())
        self.assertFalse(list(self.tmp.glob(".flamin-copy-*")))
        rehearsal.rehearse_copy(self.root, self.copy, snapshot)
        self.assertTrue((self.copy / rehearsal.MARKER).is_file())
