"""Single-use, user-verified WebAuthn decisions for paired workspaces."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from .util import FlaminError, canonical_json

CHALLENGE_SECONDS = 120
TOKEN = re.compile(r"[A-Za-z0-9_-]+")


def recorded_outcome(database: Path, workspace: str, rid: str, payload_hash: str) -> str | None:
    uri = database.resolve(strict=True).as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        row = db.execute("SELECT outcome FROM decisions WHERE workspace = ? AND request_id = ? AND payload_hash = ?",
                         (workspace, rid, payload_hash)).fetchone()
    return row[0] if row else None


def encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode(value: str) -> bytes:
    if not isinstance(value, str) or not TOKEN.fullmatch(value):
        raise FlaminError("Malformed WebAuthn response.")
    raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if encode(raw) != value:
        raise FlaminError("Malformed WebAuthn response.")
    return raw


def verify_assertion(assertion: dict, challenge: str, credential_id: str, public_key: bytes,
                     origin: str, rp_id: str) -> None:
    """Verify an ES256 assertion from a credential enrolled by the trusted operator."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
    except ImportError as exc:
        raise FlaminError("The protected approval service requires the cryptography package.") from exc

    try:
        if assertion["id"] != credential_id:
            raise ValueError
        client = decode(assertion["client_data"])
        data = json.loads(client)
        if (data["type"] != "webauthn.get" or data["challenge"] != challenge or
                data["origin"] != origin or data.get("crossOrigin", False) is not False or
                "topOrigin" in data):
            raise ValueError
        auth = decode(assertion["authenticator_data"])
        # The desk requests no extensions and accepts only assertion data.
        if (len(auth) != 37 or auth[:32] != hashlib.sha256(rp_id.encode("ascii")).digest()
                or auth[32] & 0x05 != 0x05 or auth[32] & 0xC0):
            raise ValueError
        key = serialization.load_der_public_key(public_key)
        if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
            raise ValueError
        key.verify(decode(assertion["signature"]), auth + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, KeyError, ValueError, TypeError, FlaminError) as exc:
        raise FlaminError("User-verified credential assertion did not match this decision.") from exc


class DecisionDesk:
    """Use a database and credential configuration writable only by the installed service."""

    def __init__(self, database: Path, workspace: Path, origin: str, credential_id: str, public_key: bytes,
                 inspect):
        self.database = database
        self.workspace = str(workspace.resolve())
        self.origin = origin
        self.credential_id = credential_id
        self.public_key = public_key
        self.inspect = inspect
        self.pending = {}
        with closing(self._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS decisions (
                workspace TEXT NOT NULL, request_id TEXT NOT NULL, payload_hash TEXT NOT NULL,
                revision TEXT NOT NULL, outcome TEXT NOT NULL, decided_at INTEGER NOT NULL,
                credential_id TEXT NOT NULL, challenge TEXT NOT NULL UNIQUE,
                PRIMARY KEY (workspace, request_id, payload_hash)
            )""")

    def _connect(self):
        return sqlite3.connect(self.database, isolation_level="IMMEDIATE")

    def prepare(self, rid: str, decision: str, reviewed: dict) -> dict:
        if decision not in ("approve", "reject"):
            raise FlaminError("Choose approve or reject.")
        current = self.inspect(rid)
        if current != reviewed or current["workspace"] != self.workspace:
            raise FlaminError("The proposal changed. Review it again.")
        if current["kind"] not in ("assumption", "stack"):
            raise FlaminError("This approval service accepts assumptions and stack proposals.")
        now = int(time.time())
        self.pending = {nonce: item for nonce, item in self.pending.items() if item[2] > now}
        if len(self.pending) >= 64:
            raise FlaminError("Too many pending confirmations. Wait for them to expire.")
        if self.outcome(rid, current["payload_hash"]):
            raise FlaminError("This proposal already has a recorded decision.")
        # The signed challenge commits to the exact workspace, revision, payload and choice.
        token = encode(hashlib.sha256(canonical_json({
            "proposal": current, "decision": decision, "expires": now + CHALLENGE_SECONDS,
            "nonce": secrets.token_hex(32),
        }).encode()).digest())
        self.pending[token] = (current, decision, now + CHALLENGE_SECONDS)
        return {"challenge": token, "credential_id": self.credential_id}

    def decide(self, challenge: str, assertion: dict) -> dict:
        pending = self.pending.get(challenge)
        if pending is None or pending[2] <= time.time():
            raise FlaminError("This decision challenge is missing, expired, or already used.")
        proposal, decision, _ = pending
        verify_assertion(assertion, challenge, self.credential_id, self.public_key, self.origin, "localhost")
        del self.pending[challenge]
        if self.inspect(proposal["id"]) != proposal:
            raise FlaminError("The proposal changed while you confirmed it. Review it again.")
        result = "agreed" if decision == "approve" else "rejected"
        with closing(self._connect()) as db, db:
            inserted = db.execute(
                "INSERT OR IGNORE INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (self.workspace, proposal["id"], proposal["payload_hash"], proposal["state_revision"],
                 result, int(time.time()), self.credential_id, challenge),
            )
            if inserted.rowcount != 1:
                raise FlaminError("This proposal already has a recorded decision.")
        return {"id": proposal["id"], "outcome": result, "payload_hash": proposal["payload_hash"]}

    def outcome(self, rid: str, payload_hash: str) -> str | None:
        return recorded_outcome(self.database, self.workspace, rid, payload_hash)
