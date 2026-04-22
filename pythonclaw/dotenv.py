"""Tiny dotenv reader / writer (stdlib only).

Supports ``KEY=VALUE`` lines with optional double- or single-quoted values,
blank lines and ``# comments``. Good enough for our onboarding needs; not a
replacement for ``python-dotenv``.
"""
from __future__ import annotations

import os
from pathlib import Path


def load(path: str | os.PathLike[str]) -> dict[str, str]:
    p = Path(path)
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or \
           (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        # unescape \n and \"
        val = val.replace("\\n", "\n").replace('\\"', '"').replace("\\'", "'")
        if key:
            out[key] = val
    return out


def apply_to_env(data: dict[str, str], override: bool = False) -> None:
    """Apply loaded values to ``os.environ``.

    By default existing env vars win (use :func:`apply_to_env(..., override=True)`
    to force). This matches typical dotenv semantics and lets CI / shell exports
    override secrets on disk.
    """
    for k, v in data.items():
        if override or k not in os.environ:
            os.environ[k] = v


def save(path: str | os.PathLike[str], data: dict[str, str]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for k in sorted(data):
        v = data[k]
        escaped = v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        lines.append(f'{k}="{escaped}"')
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def update(path: str | os.PathLike[str], **kv: str | None) -> dict[str, str]:
    """Read, merge and write back. ``None`` values delete the key."""
    data = load(path)
    for k, v in kv.items():
        if v is None:
            data.pop(k, None)
        else:
            data[k] = v
    save(path, data)
    return data
