"""Provider base class."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ..session import Message


class ProviderError(RuntimeError):
    pass


@dataclass
class CompletionRequest:
    messages: list[Message]
    system: str | None = None
    tools: list[dict[str, Any]] | None = None
    model: str | None = None
    max_tokens: int = 1024
    temperature: float = 0.7
    meta: dict[str, Any] | None = None


@dataclass
class CompletionResult:
    text: str
    provider: str
    model: str
    usage: dict[str, int] | None = None
    raw: Any = None


class Provider:
    name: str = "base"

    def __init__(self, name: str | None = None) -> None:
        if name:
            self.name = name

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.__class__.__name__}

    def complete(self, req: CompletionRequest) -> CompletionResult:
        raise NotImplementedError

    # helpers -----------------------------------------------------------------
    @staticmethod
    def to_chat(messages: Iterable[Message], system: str | None) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        if system:
            out.append({"role": "system", "content": system})
        for m in messages:
            role = m.role if m.role in ("system", "user", "assistant", "tool") else "user"
            out.append({"role": role, "content": m.content})
        return out
