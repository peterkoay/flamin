"""Focused regression checks for maintenance approval routing and GitHub reads."""
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from helpers import Project
from flaminlib import flow, hooks, maintenance, policy
from flaminlib.product import Product
from flaminlib.util import FlaminError

URL = 'https://api.github.com/repos/peterkoay/flamin/releases/tags/v1'


class TestGithubReads(Project):
    def test_reads_and_ambiguous_operations(self):
        for command in ('curl ' + URL, 'curl -I ' + URL,
                'Invoke-RestMethod -Method Get -Uri ' + URL,
                '$releaseInfo = Invoke-RestMethod -Method Get -Uri "' + URL + '" -Headers @{ "User-Agent"="flamin" }',
                'gh api repos/peterkoay/flamin/releases/tags/v1'):
            self.assertIsNone(policy.gated_shell(command), command)
        for command in ('curl -X DELETE ' + URL, 'curl --data x ' + URL,
                'curl --config other ' + URL, 'curl ' + URL + '; curl -X DELETE ' + URL,
                'Invoke-RestMethod -Method $verb -Uri ' + URL,
                'Invoke-RestMethod -Uri ' + URL + ' -Headers @{ "x"=(gh release delete v1) }',
                'gh api --method DELETE repos/peterkoay/flamin/releases/1',
                'gh release delete v1', 'curl https://api.example.com/paid'):
            self.assertIsNotNone(policy.gated_shell(command), command)


class TestMaintenanceApprovals(Project):
    def setUp(self):
        super().setUp()
        self.write('.flamin-kit-maintenance', 'on')
        self.product = Product(self.root)

    def call(self, command, key=''):
        return hooks.check_shell(self.product, hooks.Call(tool='codex', event='PreToolUse',
            kind='shell', command=command, agent='orchestrator', call_key=key))

    def test_read_creates_no_product_or_approval_store(self):
        self.assertEqual(self.call('Invoke-RestMethod -Method Get -Uri ' + URL).decision, 'allow')
        self.assertFalse((self.root / '.flamin').exists())
        self.assertFalse((self.root / maintenance.DIRECTORY).exists())

    def test_scoped_single_use_and_duplicate_call(self):
        command = 'curl -X DELETE ' + URL
        denied = self.call(command)
        self.assertEqual(denied.decision, 'deny')
        self.assertNotIn('flamin init', denied.reason)
        self.assertEqual(self.call(command).request, denied.request)
        maintenance.answer(self.root, denied.request, True)
        self.assertEqual(self.call(command, 'key-1').decision, 'allow')
        self.assertEqual(self.call(command, 'key-1').decision, 'allow')
        self.assertEqual(self.call(command, 'key-2').decision, 'deny')
        self.assertEqual(self.call(command + '/changed').decision, 'deny')
        self.assertFalse((self.root / '.flamin').exists())

    def test_rejected_and_store_workspace_binding(self):
        denied = self.call('curl -X DELETE ' + URL)
        maintenance.answer(self.root, denied.request, False)
        self.assertEqual(self.call('curl -X DELETE ' + URL).decision, 'deny')
        path = self.root / maintenance.DIRECTORY / 'approvals.json'
        data = json.loads(path.read_text())
        data['workspace'] = 'different'
        path.write_text(json.dumps(data))
        with self.assertRaises(FlaminError):
            maintenance.pending(self.root)

    def test_main_assistant_edits_and_protected_store(self):
        call = hooks.Call(tool='codex', event='PreToolUse', agent='orchestrator')
        self.assertEqual(hooks.check_write(self.product, call, 'kit/rules/core.md', 'text', True, False).decision, 'allow')
        call.agent = 'developer'
        self.assertEqual(hooks.check_write(self.product, call, 'kit/rules/core.md', 'text', True, False).decision, 'deny')
        call.agent = 'orchestrator'
        self.assertEqual(hooks.check_write(self.product, call, '.flamin-maintenance/approvals.json', '{}', True, False).decision, 'deny')

    def test_terminal_answer_and_agent_denial(self):
        rid = self.call('curl -X DELETE ' + URL).request
        args = SimpleNamespace(id=rid, yes=True, no=False)
        with patch.dict(os.environ, {'FLAMIN_AGENT': 'orchestrator'}):
            with self.assertRaises(FlaminError):
                flow.cmd_approve(self.product, args)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(flow.cmd_approve(self.product, args), 0)
        self.assertEqual(self.call('curl -X DELETE ' + URL).decision, 'allow')

    def test_lock_and_mode_required(self):
        with maintenance.transaction(self.root):
            with self.assertRaises(FlaminError):
                maintenance.pending(self.root)
        # Adding product state invalidates maintenance mode without changing the flag.
        self.write('.flamin/state.json', '{}')
        with self.assertRaises(FlaminError):
            maintenance.pending(self.root)

    def test_maintenance_human_command_never_creates_product_token(self):
        call = hooks.Call(tool='claude', event='PreToolUse', kind='shell',
            command='flamin approve M-0001 --yes', mode='default', agent='orchestrator')
        self.assertEqual(hooks.check_shell(self.product, call).decision, 'deny')
        self.assertFalse((self.root / '.flamin').exists())

    def test_github_connector_read_and_mutation(self):
        read = hooks.Call(tool='codex', event='PreToolUse', kind='mcp',
            tool_name='mcp__github__list_releases', command='{}', agent='orchestrator')
        self.assertEqual(hooks.evaluate(self.product, read, 'pretool').decision, 'allow')
        read.tool_name = 'mcp__github__delete_release'
        self.assertEqual(hooks.evaluate(self.product, read, 'pretool').decision, 'deny')

    def test_bootstrap_message_does_not_demand_product(self):
        # Separate workspace with neither product nor maintenance flag.
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as folder:
            decision = hooks.gate(Product(Path(folder)), hooks.Call(tool='codex', event='PreToolUse'),
                                  'external-api', 'mutation', 'change GitHub')
            self.assertEqual(decision.decision, 'deny')
            self.assertIn('use `flamin init` only to start a product', decision.reason)
