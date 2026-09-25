"""Secret detection and redaction (DESIGN §15.1, §15.2). Heuristic detection."""
from __future__ import annotations

import math
import re

# (kind, regex, value group). Order matters: specific prefixes first.
_PATTERNS = [
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY(?: BLOCK)?-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY(?: BLOCK)?-----|$)"), 0),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), 0),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b"), 0),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"), 0),
    ("openai-key", re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}"), 0),
    ("stripe-key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"), 0),
    ("slack-token", re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}"), 0),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), 0),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"), 0),
    ("url-credentials", re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s:/@]+:([^\s@/]{3,})@"), 1),
]

# Long high-entropy value next to a word like key, token or secret.
_KEYWORD_VALUE = re.compile(
    r"(?i)\b[\w\-]*(?:api[_\-]?key|secret|token|access[_\-]?key|auth|credential|client[_\-]?secret)[\w\-]*\b"
    r"[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9_\-+/=.]{20,})"
)
# password= style assignment with a quoted literal value.
_PASSWORD = re.compile(r"(?i)\b(?:password|passwd|pwd)\b[\"']?\s*[:=]\s*([\"'])([^\"'\s]{4,})\1")


def entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(value)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def find_secrets(text: str, include_passwords: bool = True) -> list[tuple[str, int, int]]:
    """Return (kind, start, end) spans of secret values."""
    spans: list[tuple[str, int, int]] = []
    if not text:
        return spans
    for kind, rx, group in _PATTERNS:
        for m in rx.finditer(text):
            spans.append((kind, m.start(group), m.end(group)))
    for m in _KEYWORD_VALUE.finditer(text):
        value = m.group(1)
        if entropy(value) >= 3.5 and not _placeholder(value):
            spans.append(("high-entropy-secret", m.start(1), m.end(1)))
    if include_passwords:
        for m in _PASSWORD.finditer(text):
            if not _placeholder(m.group(2)):
                spans.append(("password", m.start(2), m.end(2)))
    return _merge(spans)


def _placeholder(value: str) -> bool:
    v = value.lower()
    return bool(
        re.fullmatch(r"[x*.\-_<>{}$]+", v)
        or v.startswith("${") or v.startswith("<") or "redacted" in v
        or v in {"changeme", "password", "example", "placeholder"}
        or re.fullmatch(r"(?:your|my)[_\-]?\w+", v)
    )


def _merge(spans):
    spans.sort(key=lambda s: (s[1], -(s[2] - s[1])))
    out = []
    for kind, s, e in spans:
        if out and s < out[-1][2]:
            continue
        out.append((kind, s, e))
    return out


def redact(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    spans = find_secrets(text)
    if not spans:
        return text
    parts, last = [], 0
    for kind, s, e in spans:
        parts.append(text[last:s])
        parts.append(f"[REDACTED:{kind}]")
        last = e
    parts.append(text[last:])
    return "".join(parts)


PERSONAL_FIELDS = {"user_email", "userEmail", "email"}


def redact_obj(obj):
    """Deep copy with secrets redacted and personal fields dropped."""
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items() if k not in PERSONAL_FIELDS}
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    if isinstance(obj, str):
        return redact(obj)
    return obj
