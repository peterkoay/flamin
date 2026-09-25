"""Parse and apply Codex apply_patch text in memory (for check-lock on Codex)."""
from __future__ import annotations


class PatchError(ValueError):
    pass


def parse(text: str) -> list[dict]:
    """Return [{'op': 'add'|'update'|'delete', 'path', 'move_to', 'hunks': [[(tag, line)]], 'lines': [...]}]."""
    lines = text.replace("\r\n", "\n").split("\n")
    ops, cur, hunk = [], None, None
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("*** Begin Patch") or ln.startswith("*** End Patch"):
            i += 1
            continue
        if ln.startswith("*** Add File: "):
            cur = {"op": "add", "path": ln[len("*** Add File: "):].strip(), "lines": [], "hunks": []}
            ops.append(cur)
            hunk = None
        elif ln.startswith("*** Update File: "):
            cur = {"op": "update", "path": ln[len("*** Update File: "):].strip(), "hunks": [], "move_to": None}
            ops.append(cur)
            hunk = None
        elif ln.startswith("*** Delete File: "):
            cur = {"op": "delete", "path": ln[len("*** Delete File: "):].strip()}
            ops.append(cur)
            hunk = None
        elif ln.startswith("*** Move to: ") and cur and cur["op"] == "update":
            cur["move_to"] = ln[len("*** Move to: "):].strip()
        elif ln.startswith("*** End of File"):
            pass
        elif cur and cur["op"] == "add":
            if ln.startswith("+"):
                cur["lines"].append(ln[1:])
            elif ln == "" and i == len(lines) - 1:
                pass
            else:
                cur["lines"].append(ln)
        elif cur and cur["op"] == "update":
            if ln.startswith("@@"):
                hunk = []
                cur["hunks"].append(hunk)
            elif ln[:1] in (" ", "-", "+"):
                if hunk is None:
                    hunk = []
                    cur["hunks"].append(hunk)
                hunk.append((ln[0], ln[1:]))
            elif ln == "":
                if hunk is not None and i != len(lines) - 1:
                    hunk.append((" ", ""))
        i += 1
    if not ops:
        raise PatchError("no file operations found in patch")
    return ops


def apply_update(old: str, hunks: list) -> str:
    src = old.replace("\r\n", "\n").split("\n")
    pos = 0
    for hunk in hunks:
        before = [l for t, l in hunk if t in (" ", "-")]
        after = [l for t, l in hunk if t in (" ", "+")]
        if not before:
            src[pos:pos] = after
            pos += len(after)
            continue
        at = _find(src, before, pos)
        if at < 0:
            at = _find(src, [b.rstrip() for b in before], pos, strip=True)
        if at < 0:
            raise PatchError("hunk context not found in current file")
        src[at:at + len(before)] = after
        pos = at + len(after)
    return "\n".join(src)


def _find(src, block, start, strip=False):
    n = len(block)
    for i in range(start, len(src) - n + 1):
        window = [s.rstrip() for s in src[i:i + n]] if strip else src[i:i + n]
        if window == block:
            return i
    for i in range(0, min(start, len(src) - n + 1)):
        window = [s.rstrip() for s in src[i:i + n]] if strip else src[i:i + n]
        if window == block:
            return i
    return -1
