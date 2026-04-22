"""Session + message dataclasses shared across channels, agents and providers."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal


Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class Message:
    role: Role
    content: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: float = field(default_factory=time.time)
    channel: str | None = None           # inbound channel id, e.g. "webchat"
    session_id: str | None = None        # conversation identifier
    user: str | None = None              # originating user (channel-specific)
    agent: str | None = None             # agent that produced an assistant msg
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "role": self.role, "content": self.content,
            "ts": self.ts, "channel": self.channel, "session_id": self.session_id,
            "user": self.user, "agent": self.agent, "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Message":
        return cls(
            role=d["role"], content=d["content"],
            id=d.get("id", uuid.uuid4().hex),
            ts=d.get("ts", time.time()),
            channel=d.get("channel"),
            session_id=d.get("session_id"),
            user=d.get("user"),
            agent=d.get("agent"),
            meta=d.get("meta", {}) or {},
        )


@dataclass
class Session:
    id: str
    channel: str
    user: str | None = None
    agent: str | None = None
    created: float = field(default_factory=time.time)
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(cls, channel: str, user: str | None = None, agent: str | None = None) -> "Session":
        return cls(id=uuid.uuid4().hex, channel=channel, user=user, agent=agent)
