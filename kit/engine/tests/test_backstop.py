"""Pre-commit backstop (check-staged), kit manifest, doctor, adapters, upgrade."""
import json
import os
import subprocess
import sys

from helpers import Project

from flaminlib import manifest as mf


class TestBackstop(Project):
    def setUp(self):
        super().setUp()
        self.to_generated()
        (self.root / "kit" / "MANIFEST").write_text(mf.build(self.root), encoding="utf-8")
        self.git("add", "-A")
        r = self.git("commit", "-q", "-m", "baseline", "--no-verify")
        self.assertEqual(r.returncode, 0, r.stderr)

    def commit(self):
        """Commit through the real git pre-commit hook written by flamin init."""
        env = dict(os.environ)
        env.pop("FLAMIN_ROOT", None)
        return subprocess.run(["git", "commit", "-q", "-m", "change"], cwd=str(self.root), capture_output=True, text=True, env=env)

    def test_fully_locked_edit_blocked_by_hook(self):
        rel = "modules/member/services/base_register_service.py"
        self.write(rel, self.read(rel) + "# edited\n")
        self.git("add", rel)
        r = self.commit()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("FULLY_LOCKED", r.stdout + r.stderr)

    def test_outside_extension_and_cross_module_blocked(self):
        rel = "modules/member/services/register_service.py"
        self.write(rel, self.read(rel).replace("class RegisterService(", "class RegisterService2("))
        self.write("modules/member/services/helper.py", "from modules.billing.services.pay import pay\n")
        self.git("add", "-A")
        out = self.flamin("check-staged", expect=1)
        self.assertIn("outside every extension body", out)
        self.assertIn("Cross-module calls go only through contracts", out)

    def test_inside_extension_passes(self):
        rel = "modules/member/services/register_service.py"
        self.write(rel, self.read(rel).replace("        return None  # business rules for the Member aggregate, written once\n",
                                               "        root.phone = root.phone.strip()\n"))
        self.git("add", rel)
        r = self.commit()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_kit_change_fails_manifest(self):
        rel = "kit/engine/flaminlib/policy.py"
        self.write(rel, self.read(rel) + "\n# weaken\n")
        self.git("add", rel)
        out = self.flamin("check-staged", expect=1)
        self.assertIn("kit/MANIFEST", out)

    def test_state_edit_fails_checksum(self):
        st = json.loads(self.read(".flamin/state.json"))
        st["phase"] = 3
        self.write(".flamin/state.json", json.dumps(st))
        self.git("add", "-A")
        self.assertIn("checksum mismatch", self.flamin("check-staged", expect=1))

    def test_range_mode(self):
        rel = "modules/member/services/base_register_service.py"
        self.write(rel, self.read(rel) + "# edited\n")
        self.git("add", rel)
        self.git("commit", "-q", "-m", "bad", "--no-verify")
        out = self.flamin("check-staged", "--range", "HEAD~1..HEAD", expect=1)
        self.assertIn("FULLY_LOCKED", out)


class TestPhaseBackstop(Project):
    def test_code_before_baseline_blocked_at_commit(self):
        self.to_stack()
        self.write("modules/member/services/x.py", "x = 1\n")
        self.git("add", "-A")
        out = self.flamin("check-staged", expect=1)
        self.assertIn("baseline", out)


class TestDoctorAndKit(Project):
    def test_kit_clean_and_dirty(self):
        from flaminlib.render import render_all
        render_all(self.root, "all")
        for n in (".claude/settings.local.json", ".cursor/hooks.json"):
            (self.root / n).unlink()
        (self.root / "kit" / "MANIFEST").write_text(mf.build(self.root), encoding="utf-8")
        self.git("init", "-q")
        out = self.flamin("doctor", "--kit", expect=None)
        self.assertNotIn("Master kit:", out, out)
        (self.root / ".flamin").mkdir()
        (self.root / "secrets").mkdir()
        out = self.flamin("doctor", "--kit", expect=1)
        self.assertIn(".flamin/ exists", out)

    def test_manifest_detects_change(self):
        (self.root / "kit" / "MANIFEST").write_text(mf.build(self.root), encoding="utf-8")
        self.assertEqual(mf.verify_tree(self.root), [])
        p = self.root / "kit" / "rules" / "core.md"
        p.write_text(p.read_text(encoding="utf-8") + "x", encoding="utf-8")
        self.assertTrue(any("core.md" in x for x in mf.verify_tree(self.root)))


class TestAdapters(Project):
    def test_render_all_tools(self):
        self.init("all")
        s = json.loads(self.read(".claude/settings.json"))
        self.assertEqual(s["agent"], "orchestrator")
        self.assertEqual(s["env"]["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"], "1")
        self.assertEqual(s["env"]["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"], "10")
        self.assertIn("Agent(fork)", s["permissions"]["deny"])
        orch = self.read(".claude/agents/orchestrator.md")
        self.assertIn("Agent(business, analyst, architect, planner, designer, developer, tester)", orch)
        for w in ("business", "developer", "tester"):
            self.assertNotIn("Agent", self.read(f".claude/agents/{w}.md").split("---")[1])
        hooks = json.loads(self.read(".claude/settings.local.json"))["hooks"]
        h = hooks["PreToolUse"][0]["hooks"][0]
        self.assertEqual(h["command"], "python" if os.name == "nt" else "python3")
        self.assertEqual(h["args"][0], "${CLAUDE_PROJECT_DIR}/kit/engine/flamin.py")
        self.assertFalse((self.root / ".codex/agents/orchestrator.toml").exists())
        self.assertFalse((self.root / ".cursor/agents/orchestrator.md").exists())
        self.assertEqual(len(list((self.root / ".codex/agents").glob("*.toml"))), 7)
        import tomllib
        for f in (self.root / ".codex/agents").glob("*.toml"):
            d = tomllib.loads(f.read_text(encoding="utf-8"))
            self.assertTrue({"name", "description", "developer_instructions", "model"} <= set(d))
        cur = json.loads(self.read(".cursor/hooks.json"))
        self.assertTrue(cur["hooks"]["preToolUse"][0]["failClosed"])
        self.assertNotIn("matcher", cur["hooks"]["preToolUse"][0])
        self.assertFalse((self.root / ".cursor/rules").exists())
        self.assertIn("Orchestrator", self.read("AGENTS.md"))
        self.assertLess(len(self.read("AGENTS.md").encode()), 32 * 1024)
        gi = self.read(".gitignore")
        for e in (".flamin/audit/", ".claude/settings.local.json", ".cursor/hooks.json", ".env"):
            self.assertIn(e, gi)
        self.assertIn("already current", self.flamin("init", "--tool", "all"))


class TestUpgrade(Project):
    def test_upgrade_never_touches_state_and_migration_gate(self):
        self.to_stack()
        before = self.read(".flamin/state.json")
        newkit = self.tmp / "newkit"
        import shutil
        shutil.copytree(self.root / "kit", newkit / "kit")
        (newkit / "kit" / "VERSION").write_text("v2\n", encoding="utf-8")
        self.flamin("upgrade", "--from", str(newkit))  # preview only
        self.assertEqual((self.root / "kit" / "VERSION").read_text().strip(), "v1")
        self.flamin("upgrade", "--from", str(newkit), "--yes")
        self.assertEqual((self.root / "kit" / "VERSION").read_text().strip(), "v2")
        self.assertEqual(before, self.read(".flamin/state.json"))
        (newkit / "kit" / "engine" / "format.json").write_text('{"state_format": 2}\n', encoding="utf-8")
        out = self.flamin("upgrade", "--from", str(newkit), "--yes", expect=1)
        self.assertIn("migration is needed", out)
        out = self.flamin("upgrade", "--from", str(newkit), "--migrate", expect=1)
        self.assertIn("No tested migration", out)
        self.assertEqual(before, self.read(".flamin/state.json"))
