"""flamin-generate and flamin-lint: determinism, body carry-over, orphans, lint rules, every profile."""
import hashlib
import json

from helpers import MODEL, QUERY_SPEC, WRITE_SPEC, Project

from flaminlib import locks as lk


def tree_hash(root, rels):
    h = hashlib.sha256()
    for r in sorted(rels):
        h.update(r.encode() + b"\0" + (root / r).read_bytes())
    return h.hexdigest()


class TestGenerate(Project):
    def test_deterministic_and_idempotent(self):
        self.to_generated()
        locks = json.loads(self.read(".flamin/locks.json"))["files"]
        h1 = tree_hash(self.root, locks)
        self.flamin("generate", ".flamin/design/member/register.toml")
        self.assertEqual(h1, tree_hash(self.root, locks))
        for rel, e in locks.items():
            self.assertEqual(lk.level_of(self.read(rel)), e["level"])

    def test_body_carry_over_and_orphans(self):
        self.to_generated()
        rel = "modules/member/services/register_service.py"
        text = self.read(rel).replace("        return None  # business rules for the Member aggregate, written once\n",
                                      "        root.phone = root.phone.strip()  # KEEP ME\n")
        self.write(rel, text)
        self.flamin("generate", ".flamin/design/member/register.toml")
        self.assertIn("KEEP ME", self.read(rel))
        # rename the extension point in the template set: simulate by editing the file's marker name
        self.write(rel, self.read(rel).replace("FLAMIN:EXTENSION validateAggregate", "FLAMIN:EXTENSION oldName"))
        out = self.flamin("generate", ".flamin/design/member/register.toml")
        self.assertIn("ORPHAN", out)
        orphans = list((self.root / ".flamin/tmp/orphans").glob("*oldName.txt"))
        self.assertEqual(len(orphans), 1)
        self.assertIn("KEEP ME", orphans[0].read_text(encoding="utf-8"))

    def test_step7_done_only_when_every_spec_generated(self):
        self.to_baseline()
        self.write(".flamin/analysis/member/plan.md", "## Batch 1\n- a\n- b\n")
        self.flamin("plan", "member")
        self.flamin("approve", self.last_request(), "--yes")
        self.write(".flamin/design/member/register.toml", WRITE_SPEC)
        self.write(".flamin/design/member/query-by-phone.toml", QUERY_SPEC)
        self.flamin("design", "member", ".flamin/design/member/register.toml")
        out = self.flamin("generate", ".flamin/design/member/register.toml")
        self.assertIn("Step 7 continues", out)
        self.assertEqual(self.state()["modules"]["member"]["steps_done"], [6])
        self.assertIn("allowed: designer", self.flamin("check-phase", "--agent", "developer", expect=1))
        self.flamin("design", "member", ".flamin/design/member/query-by-phone.toml")
        self.assertIn("Step 7 done", self.flamin("generate", ".flamin/design/member/query-by-phone.toml"))
        self.assertEqual(self.state()["modules"]["member"]["steps_done"], [6, 7])

    def test_generate_refused_before_design(self):
        self.to_baseline()
        self.write(".flamin/analysis/member/plan.md", "## Batch 1\n- a\n")
        self.flamin("plan", "member")
        self.flamin("approve", self.last_request(), "--yes")
        self.write(".flamin/design/member/register.toml", WRITE_SPEC)
        out = self.flamin("generate", ".flamin/design/member/register.toml", expect=1)
        self.assertIn("flamin design", out)


class TestLint(Project):
    def setUp(self):
        super().setUp()
        self.to_baseline()

    def lint(self, spec_text, expect):
        self.write(".flamin/design/member/x.toml", spec_text)
        return self.flamin("lint", ".flamin/design/member/x.toml", expect=expect)

    def test_clean(self):
        self.assertIn("clean", self.lint(WRITE_SPEC, 0))
        self.assertIn("clean", self.lint(QUERY_SPEC, 0))

    def test_unknown_field_and_role(self):
        bad = WRITE_SPEC.replace('transient = ["password"]', "")
        self.assertIn("password", self.lint(bad, 1))
        bad = WRITE_SPEC.replace('{ name = "phone", type = "string", required = true }', '{ name = "phone", type = "MemberModel" }')
        self.assertIn("internal object role", self.lint(bad, 1))

    def test_extension_point_names(self):
        bad = WRITE_SPEC.replace('name = "validateAggregate"', 'name = "doMagic"')
        self.assertIn("doMagic", self.lint(bad, 1))

    def test_secret_output(self):
        bad = QUERY_SPEC.replace('output = [ { name = "id" }, { name = "phone" } ]', 'output = [ { name = "password_hash" } ]')
        self.assertIn("secret", self.lint(bad, 1))

    def test_cross_module_call_needs_contract(self):
        bad = WRITE_SPEC + '\n[[calls]]\nmodule = "billing"\n'
        self.assertIn("unknown module", self.lint(bad, 1))

    def test_drift(self):
        self.write(".flamin/analysis/member/plan.md", "## Batch 1\n- a\n")
        self.flamin("plan", "member")
        self.flamin("approve", self.last_request(), "--yes")
        self.write(".flamin/design/member/register.toml", WRITE_SPEC)
        self.flamin("design", "member", ".flamin/design/member/register.toml")
        self.flamin("generate", ".flamin/design/member/register.toml")
        self.flamin("lint", "--drift")
        self.write(".flamin/design/member/register.toml", WRITE_SPEC.replace("/api/member/register", "/api/member/signup"))
        self.assertIn("changed after generation", self.flamin("lint", "--drift", expect=1))


class TestEveryProfileGenerates(Project):
    """Each shipped profile generates base and extension files with valid markers."""

    def check_profile(self, profile):
        self.to_baseline(profile)
        self.write(".flamin/analysis/member/plan.md", "## Batch 1\n- a\n- b\n")
        self.flamin("plan", "member")
        self.flamin("approve", self.last_request(), "--yes")
        self.write(".flamin/design/member/register.toml", WRITE_SPEC)
        self.write(".flamin/design/member/query-by-phone.toml", QUERY_SPEC)
        for s in ("register", "query-by-phone"):
            self.flamin("design", "member", f".flamin/design/member/{s}.toml")
            self.flamin("generate", f".flamin/design/member/{s}.toml")
        locks = json.loads(self.read(".flamin/locks.json"))["files"]
        self.assertTrue(any(e["level"] == "STRUCTURE_LOCKED" for e in locks.values()))
        self.assertTrue(any(e["level"] == "FULLY_LOCKED" for e in locks.values()))
        for rel in locks:
            text = self.read(rel)
            self.assertNotIn("${", text.replace("${baseUrl}", ""), rel)
            lk.extensions(text)
        self.flamin("architecture-review", expect=1)  # Step 11 not done: refused, not crashed
        return locks

    def test_python_fastapi(self):
        self.check_profile("python-fastapi")

    def test_typescript_node(self):
        self.check_profile("typescript-node")

    def test_javascript_node(self):
        self.check_profile("javascript-node")

    def test_java_spring(self):
        self.check_profile("java-spring")

    def test_typescript_react(self):
        self.check_profile("typescript-react")

    def test_react_native(self):
        locks = self.check_profile("react-native")
        self.assertTrue(any("/screens/" in r for r in locks))

    def test_minimal(self):
        self.check_profile("minimal")


_ = MODEL
