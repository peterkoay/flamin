"""Engine-owned approvals for master-kit maintenance, without product state."""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

from . import approvals as ap
from .policy import maintenance_mode
from .statefile import atomic_write_bytes
from .util import FlaminError, iso, sha256_text

DIRECTORY = '.flamin-maintenance'


@contextlib.contextmanager
def transaction(root: Path):
    if not maintenance_mode(root):
        raise FlaminError('Maintenance approvals require human-enabled maintenance mode.')
    directory = root / DIRECTORY
    directory.mkdir(exist_ok=True)
    lock = directory / '.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise FlaminError('Maintenance approval store is locked; retry when the other operation finishes.')
    os.close(fd)
    try:
        path = directory / 'approvals.json'
        workspace = str(root.resolve())
        data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
            'workspace': workspace, 'counters': {'request': 0}, 'requests': {}}
        if data.get('workspace') != workspace:
            raise FlaminError('Maintenance approval store belongs to a different workspace.')
        yield data
        atomic_write_bytes(path, (json.dumps(data, indent=2) + '\n').encode())
    finally:
        lock.unlink()


def request(root, kind, payload, title, call_key):
    full = f'{root.resolve()}\nmaintenance\n{kind}\n{payload}'
    digest = sha256_text(full)
    with transaction(root) as data:
        for req in data['requests'].values():
            if req['payload_hash'] != digest:
                continue
            if call_key and req.get('consumed_key') == call_key:
                return True, req['id']
            if req['status'] == 'approved' and not req.get('consumed'):
                req.update(consumed=iso(), consumed_key=call_key)
                return True, req['id']
            if req['status'] == 'pending':
                return False, req['id']
        data['counters']['request'] += 1
        rid = f"M-{data['counters']['request']:04d}"
        data['requests'][rid] = dict(id=rid, kind='action', gate=kind, title=title,
            payload=payload, payload_hash=digest, status='pending', created=iso(),
            answered=None, approver=None)
        return False, rid


def answer(root, rid, yes):
    with transaction(root) as data:
        req = data['requests'].get(rid)
        if req is None:
            raise FlaminError(f'Unknown maintenance request {rid}.')
        if req['status'] == 'pending':
            req.update(status='approved' if yes else 'rejected', answered=iso(),
                       approver='human via terminal')
        return req['status']


def pending(root):
    with transaction(root) as data:
        return [r for r in data['requests'].values() if r['status'] == 'pending']
