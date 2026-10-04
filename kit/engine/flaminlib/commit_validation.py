"""Human-controlled maintenance commit validation bound to the index, not the worktree."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from . import gitops, maintenance, manifest
from .util import FlaminError, iso, sha256_bytes


def entries(root):
    result = []
    for item in gitops.git(root, 'ls-files', '--stage', '-z', binary=True).split(b'\0'):
        if not item:
            continue
        meta, name = item.split(b'\t', 1)
        mode, oid, stage = meta.decode().split()
        if stage != '0' or mode not in ('100644', '100755'):
            raise FlaminError('Commit validation requires resolved regular files; symlinks/submodules are unsupported.')
        path = name.decode('utf-8')
        if Path(path).is_absolute() or '..' in Path(path).parts or path.startswith('.git/'):
            raise FlaminError('Unsafe index path.')
        result.append((path, mode, oid))
    return result


def fingerprint(root, without_manifest=False):
    files = entries(root)
    if without_manifest:
        files = [entry for entry in files if entry[0] != 'kit/MANIFEST']
    head = gitops.git(root, 'rev-parse', 'HEAD').strip() if gitops.has_head(root) else None
    data = {'workspace': str(root.resolve()), 'head': head, 'files': files}
    return sha256_bytes(json.dumps(data, sort_keys=True).encode())


def staged_manifest(root):
    lines = [manifest.HEADER]
    for path, mode, oid in sorted(entries(root)):
        if manifest.is_kit_path(path) and '__pycache__' not in Path(path).parts and not path.endswith('.pyc'):
            data = gitops.git(root, 'cat-file', 'blob', oid, binary=True)
            lines.append(f'{manifest.content_hash(data)}  {path}\n')
    return ''.join(lines)


def scoped_test(root, scopes):
    if not scopes or any(not re.fullmatch(r'test_[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*', s) for s in scopes):
        raise FlaminError('Scope must name explicit unittest modules/classes/methods beginning with test_.')
    before = fingerprint(root, True)
    with maintenance.transaction(root) as data:
        data.pop('scoped_validation', None)
        data.pop('commit_authorization', None)
    with tempfile.TemporaryDirectory(prefix='flamin-staged-tests-') as folder:
        snapshot = Path(folder)
        for path, mode, oid in entries(root):
            target = snapshot / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(gitops.git(root, 'cat-file', 'blob', oid, binary=True))
            if mode == '100755':
                target.chmod(0o755)
        tests = snapshot / 'kit/engine/tests'
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        result = subprocess.run([sys.executable, '-B', '-m', 'unittest', *scopes],
                                cwd=tests, env=env, capture_output=True, text=True)
    print(result.stdout + result.stderr, end='')
    if result.returncode:
        raise FlaminError('Scoped tests failed; no passing validation recorded.')
    if fingerprint(root, True) != before:
        raise FlaminError('Index or HEAD changed during scoped tests; rerun validation.')
    with maintenance.transaction(root) as data:
        data['scoped_validation'] = {'fingerprint': before, 'scopes': scopes, 'at': iso(),
                                     'result': 'scoped-passed'}
    print('Scoped tests passed for staged contents. Full suite was not run; human authorization is still required.')


def authorize(root, args, agent=None):
    if agent or os.environ.get('FLAMIN_AGENT') or os.environ.get('FLAMIN_TOOL') in ('claude', 'codex', 'cursor'):
        raise FlaminError('Commit authorization requires the human terminal; agents never authorize.')
    if not args.yes or not (args.reason or '').strip() or args.validation not in ('scoped', 'skipped'):
        raise FlaminError('Human authorization requires --validation scoped|skipped --reason TEXT --yes.')
    if not gitops.changes(root, None):
        raise FlaminError('Stage the intended commit contents before authorizing.')
    before = fingerprint(root, True)
    text = staged_manifest(root)
    target = root / 'kit/MANIFEST'
    staged = gitops.blob(root, '', 'kit/MANIFEST')
    if target.exists() and target.read_text(encoding='utf-8').replace('\r\n', '\n') != (staged or '').replace('\r\n', '\n'):
        raise FlaminError('kit/MANIFEST has unstaged edits; preserve/stage them before authorizing.')
    with maintenance.transaction(root) as data:
        evidence = data.get('scoped_validation', {})
        if args.validation == 'scoped' and (evidence.get('fingerprint') != before or evidence.get('result') != 'scoped-passed'):
            raise FlaminError('No scoped test pass for these staged contents and HEAD; rerun --scope.')
        target.write_text(text, encoding='utf-8', newline='\n')
        gitops.git(root, 'add', '--', 'kit/MANIFEST')
        if fingerprint(root, True) != before:
            raise FlaminError('Index or HEAD changed during authorization; no authorization recorded.')
        final = fingerprint(root)
        if fingerprint(root, True) != before:
            raise FlaminError('Index changed while binding authorization; retry.')
        data['commit_authorization'] = {'fingerprint': final, 'at': iso(),
            'validation': args.validation, 'reason': args.reason, 'approver': 'human via terminal',
            'scopes': evidence.get('scopes', []) if args.validation == 'scoped' else [],
            'full_suite': 'waived'}
    print('kit/MANIFEST refreshed from staged contents and staged. Commit authorized: ' + args.validation +
          '; full suite waived by human. Changing index contents, modes or HEAD invalidates authorization.')


def check(root):
    with maintenance.transaction(root) as data:
        auth = data.get('commit_authorization')
    if not auth:
        return None
    if auth.get('fingerprint') != fingerprint(root):
        return ['Maintenance commit authorization is stale: staged contents, file modes or HEAD changed. Revalidate/re-authorize.']
    if gitops.blob(root, '', 'kit/MANIFEST') != staged_manifest(root):
        return ['Staged kit/MANIFEST does not match staged kit contents. Re-authorize to refresh it.']
    print('Maintenance validation: ' + auth['validation'] + '; full suite waived by human. Reason: ' + auth['reason'])
    return []
