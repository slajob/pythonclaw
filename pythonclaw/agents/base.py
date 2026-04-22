"""Agent: wraps a provider with a system prompt, a memory and a tool belt.

An agent can be addressed explicitly via the router, or used as the default
target of a gateway. It accepts inbound :class:`Message` objects and returns an
assistant :class:`Message` to be fanned back out to the originating channel.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Any

from .. import tools as tool_registry
from ..memory import SqliteMemory
from ..providers.base import CompletionRequest, Provider
from ..session import Message


_TOOL_CALL_RE = re.compile(r"^@tool\s+([a-zA-Z_][a-zA-Z0-9_]*)(?:\s+(.*))?$", re.DOTALL)


@dataclass
class Agent:
    name: str
    provider: Provider
    system: str = ""
    tools: list[str] = field(default_factory=list)
    model: str | None = None
    max_tokens: int = 1024
    temperature: float = 0.7

    def info(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider.info(),
            "system": self.system,
            "tools": list(self.tools),
            "model": self.model,
        }

    def handle(self, inbound: Message, memory: SqliteMemory) -> Message:
        # Persist the inbound message (idempotent by id).
        memory.append(inbound)

        # Short-circuit explicit tool invocation: "@tool name json-or-shell-args".
        tool_reply = self._maybe_tool_call(inbound)
        if tool_reply is not None:
            reply = Message(
                role="assistant", content=tool_reply,
                session_id=inbound.session_id, channel=inbound.channel,
                agent=self.name,
            )
            memory.append(reply)
            return reply

        history = memory.history(inbound.session_id) if inbound.session_id else [inbound]
        model_override = (inbound.meta or {}).get("model")
        req = CompletionRequest(
            messages=history,
            system=self.system or None,
            tools=tool_registry.list_specs(self.tools) if self.tools else None,
            model=model_override or self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        result = self.provider.complete(req)
        reply = Message(
            role="assistant", content=result.text,
            session_id=inbound.session_id, channel=inbound.channel,
            agent=self.name,
            meta={"provider": result.provider, "model": result.model,
                  "usage": result.usage or {}},
        )
        memory.append(reply)
        return reply

    # ---- tool dispatch -----------------------------------------------------
    def _maybe_tool_call(self, msg: Message) -> str | None:
        m = _TOOL_CALL_RE.match(msg.content.strip())
        if not m:
            return None
        name, rest = m.group(1), (m.group(2) or "").strip()
        if name not in self.tools:
            return f"error: tool {name!r} not enabled for agent {self.name!r}"
        tool = tool_registry.get(name)
        if tool is None:
            return f"error: unknown tool {name!r}"
        args = _parse_tool_args(rest)
        try:
            return tool.run(args)
        except Exception as e:  # noqa: BLE001
            return f"error: tool {name!r} failed: {e}"


def _parse_tool_args(rest: str) -> dict[str, Any]:
    """Accept either JSON (``{"expr": "1+2"}``) or shell-style ``k=v`` pairs."""
    rest = rest.strip()
    if not rest:
        return {}
    if rest.startswith("{"):
        import json
        try:
            data = json.loads(rest)
            return data if isinstance(data, dict) else {"value": data}
        except Exception:
            pass
    try:
        tokens = shlex.split(rest)
    except ValueError:
        tokens = rest.split()
    args: dict[str, Any] = {}
    positional: list[str] = []
    for t in tokens:
        if "=" in t:
            k, _, v = t.partition("=")
            args[k.strip()] = v
        else:
            positional.append(t)
    if positional and not args:
        # fallback: the first positional is `query`/`expr` for our default tools
        args["query"] = " ".join(positional)
        args["expr"] = args["query"]
    elif positional:
        args.setdefault("args", positional)
    return args
