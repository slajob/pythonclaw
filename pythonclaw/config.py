"""Configuration loader.

pythonclaw accepts JSON configs by default and YAML if PyYAML is installed.
Values of the form ``${ENV_VAR}`` are interpolated from the process environment
so secrets stay out of the file on disk.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _interpolate(value: Any) -> Any:
    if isinstance(value, str):
        def sub(m: re.Match[str]) -> str:
            return os.environ.get(m.group(1), "")
        return _ENV_RE.sub(sub, value)
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    return value


@dataclass
class Config:
    raw: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "Config":
        p = Path(path)
        text = p.read_text(encoding="utf-8")
        suffix = p.suffix.lower()
        if suffix in (".yaml", ".yml"):
            try:
                import yaml  # type: ignore
            except ImportError as e:
                raise RuntimeError(
                    "YAML config requires PyYAML. Install with: pip install pythonclaw[yaml]"
                ) from e
            data = yaml.safe_load(text) or {}
        else:
            data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError(f"Config root must be an object, got {type(data).__name__}")
        return cls(raw=_interpolate(data), path=p)

    @classmethod
    def default(cls) -> "Config":
        """Return a minimal in-memory config useful for tests and demos."""
        return cls(raw={
            "gateway": {"name": "pythonclaw", "host": "127.0.0.1", "port": 18789,
                        "data_dir": "./.pythonclaw", "auth_token": None},
            "router": {"default_agent": "pi", "rules": []},
            "agents": {"pi": {"provider": "echo",
                              "system": "You are Pi, a helpful AI coding assistant.",
                              "tools": ["time", "calc"]}},
            "providers": {"echo": {"type": "echo"}},
            "channels": {"webchat": {"type": "webchat", "enabled": True}},
            "memory": {"engine": "sqlite", "path": "./.pythonclaw/memory.db",
                       "max_messages_per_session": 200},
        })

    def get(self, *path: str, default: Any = None) -> Any:
        cur: Any = self.raw
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                return default
            cur = cur[key]
        return cur

    # convenience accessors
    @property
    def gateway(self) -> dict[str, Any]:
        return self.raw.get("gateway", {})

    @property
    def router(self) -> dict[str, Any]:
        return self.raw.get("router", {})

    @property
    def agents(self) -> dict[str, Any]:
        return self.raw.get("agents", {})

    @property
    def providers(self) -> dict[str, Any]:
        return self.raw.get("providers", {})

    @property
    def channels(self) -> dict[str, Any]:
        return self.raw.get("channels", {})

    @property
    def memory(self) -> dict[str, Any]:
        return self.raw.get("memory", {})

    @property
    def tools(self) -> dict[str, Any]:
        return self.raw.get("tools", {})
