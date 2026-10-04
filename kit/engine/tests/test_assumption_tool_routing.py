"""Scoped regressions for role ownership and Codex tool routing."""
import json
import tomllib

from helpers import Project
from flaminlib import render, policy, hooks
from flaminlib.product import Product


class TestAssumptionToolRouting(Project):
    def document_command(self, text):
        return "@'\nfrom pathlib import Path\nwith Path('docs/README.md').open('a') as stream:\n stream.write('" + text + "')\n'@ | python -B -"

    def test_literal_document_references_and_readonly_tail(self):
        self.write('.flamin-kit-maintenance', 'on')
        phrase = 'flamin upgrade --from kit --yes; flamin intake --check; flamin approve A-001 --yes'
        command = self.document_command(phrase) + '\ngit diff --check\ngit status --short'
        self.assertIsNone(policy.human_only(command))
        call = hooks.Call(tool='codex', event='PreToolUse', command=command,
                          agent='orchestrator', kind='shell')
        self.assertEqual(hooks.check_shell(Product(self.root), call).decision, 'allow')

    def test_actual_execution_and_compound_execution_stay_human_only(self):
        doc = self.document_command('flamin upgrade --from kit --yes')
        for command in ('flamin upgrade --from kit --yes', 'flamin approve A-001 --yes',
                        doc + '\nflamin upgrade --from kit --yes',
                        doc + '; flamin approve A-001 --yes',
                        doc + ' && flamin upgrade --from kit --yes',
                        doc.replace("stream.write(", "__import__('os').system("),
                        doc.replace("stream.write('", "stream.write(f'"),
                        'python -c "import os; os.system(\'flamin upgrade --from kit --yes\')"'):
            self.assertIsNotNone(policy.human_only(command), command)

    def test_host_specific_protocol_matches_role_tools(self):
        files = render.codex_shared()
        business = tomllib.loads(files['.codex/agents/business.toml'])['developer_instructions']
        self.assertIn('Return proposed intake key/value changes to the Orchestrator', business)
        self.assertIn('No shell: do not use exec_command', business)
        self.assertIn('Use native apply_patch', business)
        self.assertIn('not available on every Codex host', business)
        self.assertIn('request current file contents from the Orchestrator', business)
        tester = tomllib.loads(files['.codex/agents/tester.toml'])['developer_instructions']
        self.assertIn('Shell tools remain subject', tester)
        self.assertNotIn('No shell:', tester)
        self.assertNotIn('Codex host tool protocol', render._prompt('business'))

    def test_business_shell_denied_with_document_context_guidance(self):
        self.init('codex')
        payload = {'hook_event_name': 'PreToolUse', 'tool_name': 'Bash',
                   'agent_type': 'business', 'agent_id': 'business-1',
                   'tool_input': {'command': 'Get-Content .flamin/decisions/assumptions.md'}}
        _, output, error = self.hook('codex', payload)
        result = json.loads(output)['hookSpecificOutput']
        self.assertEqual(result['permissionDecision'], 'deny')
        self.assertIn('Authorized native editing', output + error)
        self.assertIn('current file context', output + error)

    def test_business_patch_allowed_orchestrator_patch_denied(self):
        self.init('codex')
        path = '.flamin/decisions/assumptions.md'
        self.write(path, '# Questions\n')
        patch = ('*** Begin Patch\n*** Update File: ' + path + '\n@@\n'
                 '-# Questions\n+# Questions and answers\n*** End Patch\n')
        payload = {'hook_event_name': 'PreToolUse', 'tool_name': 'apply_patch',
                   'agent_type': 'business', 'agent_id': 'business-1',
                   'tool_input': {'command': patch}}
        _, output, _ = self.hook('codex', payload)
        if output.strip():
            self.assertEqual(json.loads(output)['hookSpecificOutput']['permissionDecision'], 'allow')
        payload['agent_type'] = 'orchestrator'
        payload['agent_id'] = 'main-1'
        _, output, _ = self.hook('codex', payload)
        self.assertEqual(json.loads(output)['hookSpecificOutput']['permissionDecision'], 'deny')
