"""Focused maintenance waiver tests; no full engine suite is invoked."""
import os
from types import SimpleNamespace
from unittest.mock import patch

from helpers import Project
from flaminlib import commit_validation as cv, maintenance, manifest, policy, cmds
from flaminlib.product import Product
from flaminlib.util import FlaminError


class TestCommitValidation(Project):
    def test_document_text_is_not_human_authorization_execution(self):
        phrase = 'flamin kit-maintenance authorize-commit --validation skipped --reason x --yes'
        doc = "@'\nfrom pathlib import Path\nwith Path('docs/README.md').open('a') as stream:\n stream.write('" + phrase + "')\n'@ | python -B -"
        self.assertIsNone(policy.human_only(doc))
        self.assertIsNotNone(policy.human_only(phrase))
        execution = doc.replace("stream.write('", "__import__('os').system('")
        self.assertIsNotNone(policy.human_only(execution))

    def setUp(self):
        super().setUp()
        self.git('init', '-q')
        self.write('.flamin-kit-maintenance', 'on')
        self.write('kit/example.txt', 'staged\n')
        self.git('add', 'kit/example.txt')
        self.git('add', 'kit/MANIFEST')
        self.product = Product(self.root)

    def authorize(self, validation='skipped'):
        args = SimpleNamespace(yes=True, validation=validation, reason='Human chooses scoped validation')
        with patch.dict(os.environ, {'FLAMIN_AGENT': '', 'FLAMIN_TOOL': ''}):
            cv.authorize(self.root, args)

    def test_manifest_uses_index_not_worktree(self):
        self.write('kit/example.txt', 'unstaged\n')
        self.authorize()
        self.assertEqual(cv.check(self.root), [])
        from flaminlib import gitops
        text = gitops.blob(self.root, '', 'kit/MANIFEST')
        self.assertEqual(manifest.parse(text)['kit/example.txt'], manifest.content_hash(b'staged\n'))
        self.assertEqual(gitops.blob(self.root, '', 'kit/example.txt'), 'staged\n')
        self.assertEqual(self.read('kit/example.txt'), 'unstaged\n')
        self.assertEqual(cmds.cmd_check_staged(self.product, SimpleNamespace(range=None)), 0)

    def test_changed_index_invalidates(self):
        self.authorize()
        self.write('docs/new.md', 'new\n')
        self.git('add', 'docs/new.md')
        self.assertIn('stale', cv.check(self.root)[0])

    def test_changed_mode_invalidates(self):
        self.authorize()
        self.git('update-index', '--chmod=+x', 'kit/example.txt')
        self.assertIn('stale', cv.check(self.root)[0])

    def test_scoped_requires_matching_evidence(self):
        with self.assertRaises(FlaminError):
            self.authorize('scoped')
        with maintenance.transaction(self.root) as data:
            data['scoped_validation'] = {'fingerprint': cv.fingerprint(self.root, True),
                                        'scopes': ['test_example'], 'result': 'scoped-passed'}
        self.authorize('scoped')
        self.assertEqual(cv.check(self.root), [])

    def test_human_only_and_mode(self):
        self.assertIsNotNone(policy.human_only('flamin kit-maintenance authorize-commit --validation skipped --reason x --yes'))
        with patch.dict(os.environ, {'FLAMIN_AGENT': 'orchestrator'}):
            with self.assertRaises(FlaminError):
                cv.authorize(self.root, SimpleNamespace(yes=True, reason='x', validation='skipped'))
        self.write('.flamin/state.json', '{}')
        with self.assertRaises(FlaminError):
            self.authorize()

    def test_manifest_unstaged_edits_preserved(self):
        self.write('kit/MANIFEST', 'custom\n')
        with self.assertRaises(FlaminError):
            self.authorize()
        self.assertEqual(self.read('kit/MANIFEST'), 'custom\n')

    def test_targeted_snapshot_pass_and_fail(self):
        self.write('kit/engine/tests/test_example.py',
                   'import unittest\nclass TestExample(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n')
        self.git('add', 'kit/engine/tests/test_example.py')
        # Unstaged failure must not enter the staged validation snapshot.
        self.write('kit/engine/tests/test_example.py', 'raise RuntimeError("unstaged")\n')
        cv.scoped_test(self.root, ['test_example'])
        self.authorize('scoped')
        self.write('kit/engine/tests/test_example.py', 'import unittest\nclass TestFail(unittest.TestCase):\n def test_no(self): self.fail()\n')
        self.git('add', 'kit/engine/tests/test_example.py')
        with self.assertRaises(FlaminError):
            cv.scoped_test(self.root, ['test_example'])
        with self.assertRaises(FlaminError):
            self.authorize('scoped')

    def test_head_change_invalidates(self):
        self.authorize()
        # Create a parent commit object without running a commit or hooks.
        from unittest.mock import patch
        original = cv.gitops.git
        def changed(root, *args, **kwargs):
            if args == ('rev-parse', 'HEAD'):
                return 'changed-head'
            return original(root, *args, **kwargs)
        with patch.object(cv.gitops, 'has_head', return_value=True), patch.object(cv.gitops, 'git', side_effect=changed):
            self.assertIn('stale', cv.check(self.root)[0])
