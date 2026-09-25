"""check-lock, check-boundary, check-phase, policy patterns, patch parsing."""
import unittest
from pathlib import Path

from helpers import KIT

from flaminlib import locks as lk
from flaminlib import patch as pt
from flaminlib import policy as pol
from flaminlib.boundary import check_boundary
from flaminlib.phase import allowed_agents, check_path_phase, new_state
from flaminlib.profiles import classify, load_profile_dir

STRUCT = """# FLAMIN:LOCK STRUCTURE_LOCKED
class S(Base):
    def v(self):
        # FLAMIN:EXTENSION validateAggregate
        return None
        # FLAMIN:EXTENSION:END
"""
TS_STRUCT = """// FLAMIN:LOCK STRUCTURE_LOCKED
export class MemberService extends BaseMemberService {
  async registerMember(input: RegisterMemberInput) {
    // FLAMIN:EXTENSION validateAggregate
    // hand-written logic goes here
    // FLAMIN:EXTENSION:END
  }
}
"""


class TestLocks(unittest.TestCase):
    def test_not_locked(self):
        self.assertTrue(lk.check_lock("a.py", "x = 1\n", "x = 2\n", None)[0])

    def test_fully_locked(self):
        old = "# FLAMIN:LOCK FULLY_LOCKED\nclass Base: pass\n"
        ok, why = lk.check_lock("b.py", old, old + "x = 1\n", {"level": "FULLY_LOCKED", "hint": "s.py"})
        self.assertFalse(ok)
        self.assertIn("FULLY_LOCKED", why)
        self.assertIn("s.py", why)
        self.assertTrue(lk.check_lock("b.py", old, old, None)[0])
        self.assertFalse(lk.check_lock("b.py", old, None, None)[0])  # delete

    def test_fully_locked_generator_hash(self):
        new = "# FLAMIN:LOCK FULLY_LOCKED\nclass Base2: pass\n"
        entry = {"level": "FULLY_LOCKED", "content_hash": lk.sha256_text(new)}
        self.assertTrue(lk.check_lock("b.py", "old", new, entry)[0])

    def test_structure_inside_and_outside(self):
        inside = STRUCT.replace("        return None\n", "        if x:\n            raise E()\n")
        self.assertTrue(lk.check_lock("s.py", STRUCT, inside, None)[0])
        outside = STRUCT.replace("class S(Base):", "class S(Base, Mixin):")
        ok, why = lk.check_lock("s.py", STRUCT, outside, None)
        self.assertFalse(ok)
        self.assertIn("line 2", why.replace("lines 2", "line 2"))
        marker = STRUCT.replace("# FLAMIN:EXTENSION:END", "# end")
        self.assertFalse(lk.check_lock("s.py", STRUCT, marker, None)[0])
        unlock = STRUCT.replace("# FLAMIN:LOCK STRUCTURE_LOCKED", "# unlocked")
        self.assertFalse(lk.check_lock("s.py", STRUCT, unlock, None)[0])

    def test_typescript_markers(self):
        inside = TS_STRUCT.replace("    // hand-written logic goes here\n", "    if (!input.phone) throw new Error('x');\n")
        self.assertTrue(lk.check_lock("s.ts", TS_STRUCT, inside, None)[0])
        self.assertFalse(lk.check_lock("s.ts", TS_STRUCT, TS_STRUCT.replace("async ", ""), None)[0])
        self.assertEqual(lk.level_of(TS_STRUCT), "STRUCTURE_LOCKED")

    def test_crlf_is_not_a_change(self):
        self.assertTrue(lk.check_lock("s.py", STRUCT, STRUCT.replace("\n", "\r\n"), None)[0])

    def test_regeneration_bypass(self):
        old = "# FLAMIN:LOCK FULLY_LOCKED\nA\n"
        self.assertTrue(lk.check_lock("b.py", old, "# FLAMIN:LOCK FULLY_LOCKED\nB\n", {"level": "FULLY_LOCKED"}, {"b.py"})[0])

    def test_unbalanced_markers(self):
        with self.assertRaises(lk.MarkerError):
            lk.extensions("# FLAMIN:EXTENSION a\n")


def _prof(name, root=""):
    return load_profile_dir(KIT / "stacks" / name, root)


class TestBoundary(unittest.TestCase):
    def setUp(self):
        self.py = [_prof("python-fastapi")]

    def test_cross_module_direct_import_denied(self):
        v = check_boundary("modules/booking/services/x.py", "from modules.catalog.services.y import Y\n", self.py,
                           {"catalog": {"contracts": ["modules/catalog/contracts/"]}})
        self.assertEqual(len(v), 1)
        self.assertIn("modules/catalog/contracts/", v[0])

    def test_contract_import_allowed(self):
        self.assertEqual(check_boundary("modules/booking/services/x.py", "from modules.catalog.contracts.c import C\n", self.py), [])

    def test_same_module_and_external(self):
        self.assertEqual(check_boundary("modules/booking/services/x.py",
                                        "import json\nfrom fastapi import APIRouter\nfrom modules.booking.repository.r import R\n", self.py), [])

    def test_layer_direction(self):
        v = check_boundary("modules/booking/repository/r.py", "from modules.booking.services.s import S\n", self.py)
        self.assertTrue(v and "may not use" in v[0])

    def test_tests_are_not_checked(self):
        self.assertEqual(check_boundary("tests/test_x.py", "from modules.catalog.services.y import Y\n", self.py), [])

    def test_no_layers_profile(self):
        v = check_boundary("modules/a/b.py", "from modules.c.d import e\n", [_prof("minimal")])
        self.assertEqual(v, [])

    def test_typescript_relative_import(self):
        ts = [_prof("typescript-node")]
        v = check_boundary("src/modules/booking/services/x.ts", "import { y } from '../../catalog/services/y.ts';\n", ts)
        self.assertTrue(v and "catalog" in v[0], v)
        self.assertEqual(check_boundary("src/modules/booking/services/x.ts",
                                        "import type { C } from '../../catalog/contracts/c.ts';\n", ts), [])

    def test_java_package_import(self):
        j = [_prof("java-spring")]
        v = check_boundary("src/main/java/app/modules/booking/service/X.java",
                           "import app.modules.catalog.service.CatalogService;\n", j)
        self.assertTrue(v, v)
        self.assertEqual(check_boundary("src/main/java/app/modules/booking/service/X.java",
                                        "import app.modules.catalog.contracts.CatalogContract;\n", j), [])


class TestPhase(unittest.TestCase):
    def setUp(self):
        self.py = [_prof("python-fastapi")]
        self.s = new_state("v1")

    def info(self, p):
        return classify(p, self.py)

    def test_code_before_baseline_blocked(self):
        ok, why = check_path_phase(self.info("modules/member/services/x.py"), "modules/member/services/x.py", self.s)
        self.assertFalse(ok)
        self.assertIn("baseline", why)

    def test_code_needs_generation_and_lease(self):
        s = self.s
        s["baseline"]["confirmed"] = True
        s["phase"] = 2
        s["modules"]["member"] = {"steps_done": [6], "lease": None}
        p = "modules/member/services/x.py"
        self.assertFalse(check_path_phase(self.info(p), p, s)[0])
        s["modules"]["member"]["steps_done"] = [6, 7]
        self.assertFalse(check_path_phase(self.info(p), p, s)[0])  # no lease
        s["modules"]["member"]["lease"] = "developer@s-1"
        self.assertTrue(check_path_phase(self.info(p), p, s)[0])
        s["modules"]["member"]["steps_done"] = [6, 7, 8, 9, 10]
        self.assertFalse(check_path_phase(self.info(p), p, s)[0])
        self.assertTrue(check_path_phase(self.info(p), p, s, mode="commit")[0])

    def test_kit_and_tool_files_are_never_product_code(self):
        for p in ("kit/engine/flaminlib/hooks.py", ".flamin/stacks/x/y.py", ".claude/x.py", "docs/x.py"):
            self.assertIsNone(self.info(p).profile, p)
            self.assertTrue(check_path_phase(self.info(p), p, self.s)[0], p)

    def test_confirmed_model_passes_at_commit(self):
        s = self.s
        s["baseline"].update({"confirmed": True, "models": {"member": "abc"}})
        s["modules"]["member"] = {"steps_done": []}
        m = ".flamin/design/member/model.toml"
        self.assertTrue(check_path_phase(self.info(m), m, s, mode="commit", new_hash="abc")[0])
        self.assertFalse(check_path_phase(self.info(m), m, s, mode="commit", new_hash="changed")[0])

    def test_design_spec_needs_plan(self):
        s = self.s
        s["baseline"]["confirmed"] = True
        s["phase"] = 2
        s["modules"]["member"] = {"steps_done": [], "lease": None}
        p = ".flamin/design/member/register.toml"
        self.assertFalse(check_path_phase(self.info(p), p, s)[0])
        s["modules"]["member"]["steps_done"] = [6]
        self.assertTrue(check_path_phase(self.info(p), p, s)[0])
        m = ".flamin/design/member/model.toml"
        self.assertFalse(check_path_phase(self.info(m), m, s)[0])  # baseline confirmed
        s["baseline"]["window"] = {"modules": ["member"]}
        self.assertTrue(check_path_phase(self.info(m), m, s)[0])

    def test_launch_gate(self):
        s = self.s
        self.assertEqual(allowed_agents(s), {"business"})
        s["intake"] = {k: "x" for k in ("name", "purpose", "users", "journeys", "features", "platforms", "data_sensitivity", "success")}
        self.assertEqual(allowed_agents(s), {"business", "architect"})  # Step 0 exception
        s["phase"] = 1
        self.assertEqual(allowed_agents(s), {"business", "analyst"})
        s["phase1"]["steps_done"] = [1]
        self.assertEqual(allowed_agents(s), {"architect"})
        s["phase"] = 2
        s["modules"] = {"a": {"steps_done": []}, "b": {"steps_done": [6, 7]}}
        self.assertEqual(allowed_agents(s), {"planner", "developer"})


class TestPolicy(unittest.TestCase):
    def test_destructive(self):
        for cmd in ["rm -rf /", "rm -rf ~/", "git push --force origin main", "git push -f", "psql -c 'DROP TABLE users'",
                    "git filter-branch --all", "Remove-Item -Recurse -Force C:\\", "mkfs.ext4 /dev/sda1", "rm -rf .flamin"]:
            self.assertIsNotNone(pol.destructive(cmd), cmd)
        for cmd in ["rm -rf node_modules", "git push origin feature", "ls -la", "python -m pytest"]:
            self.assertIsNone(pol.destructive(cmd), cmd)

    def test_human_only(self):
        for cmd in ["./flamin approve R-0001 --yes", ".\\flamin.cmd approve R-1", "python kit/engine/flamin.py approve A-001 --yes",
                    "flamin cr-approve v2 --yes", "./flamin upgrade --from ../kit", "flamin doctor --clear-stale-lock"]:
            self.assertIsNotNone(pol.human_only(cmd), cmd)
        for cmd in ["./flamin status", "flamin cr-approve v2 --cr CR-001=a@7", "flamin release"]:
            self.assertIsNone(pol.human_only(cmd), cmd)

    def test_gated_shell(self):
        cases = {"pip install requests": "dependency", "npm install lodash": "dependency", "rm old.txt": "delete",
                 "alembic upgrade head": "migration", "curl https://api.example.com/v1": "external-api",
                 "eas submit -p ios": "app-store", "echo x > .env": "secret-change"}
        for cmd, kind in cases.items():
            self.assertEqual(pol.gated_shell(cmd), kind, cmd)
        for cmd in ["pip install -r requirements.txt", "npm install", "curl http://localhost:8000/x", "./flamin release"]:
            self.assertIsNone(pol.gated_shell(cmd), cmd)

    def test_shell_write_targets(self):
        self.assertIn(".flamin/state.json", pol.shell_write_targets("echo {} > .flamin/state.json"))
        self.assertIn("kit/engine/flamin.py", pol.shell_write_targets("sed -i 's/a/b/' kit/engine/flamin.py"))
        self.assertIn("CLAUDE.md", pol.shell_write_targets("Set-Content CLAUDE.md 'x'"))

    def test_paths(self):
        self.assertTrue(pol.is_state_path(".flamin/approvals.json"))
        self.assertTrue(pol.is_state_path(".flamin/tmp/ask/abc"))
        self.assertFalse(pol.is_state_path(".flamin/decisions/assumptions.md"))
        for p in ("kit/engine/x.py", "flamin", "flamin.cmd", ".claude/settings.json", ".codex/hooks.json",
                  ".cursor/hooks.json", "CLAUDE.md", "AGENTS.md", ".git/hooks/pre-commit", ".flamin/stacks/p/profile.json"):
            self.assertTrue(pol.is_enforcement_path(p), p)
        self.assertTrue(pol.is_secret_file(".env"))
        self.assertFalse(pol.is_secret_file(".env.example"))

    def test_normalize_args(self):
        self.assertEqual(pol.normalize_flamin_args(".\\flamin.cmd approve R-0001"), "approve R-0001")
        self.assertEqual(pol.normalize_flamin_args("./flamin approve 'A-001' --yes && echo ok"), "approve A-001 --yes")


class TestPatch(unittest.TestCase):
    def test_parse_and_apply(self):
        text = ("*** Begin Patch\n*** Update File: a.py\n@@\n class S:\n-    x = 1\n+    x = 2\n"
                "*** Add File: b.py\n+print('hi')\n*** Delete File: c.py\n*** End Patch\n")
        ops = pt.parse(text)
        self.assertEqual([o["op"] for o in ops], ["update", "add", "delete"])
        self.assertEqual(pt.apply_update("class S:\n    x = 1\n", ops[0]["hunks"]), "class S:\n    x = 2\n")
        self.assertEqual(ops[1]["lines"], ["print('hi')"])
        with self.assertRaises(pt.PatchError):
            pt.apply_update("other\n", ops[0]["hunks"])


if __name__ == "__main__":
    unittest.main()
