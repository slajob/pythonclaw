"""Rule-based multi-agent router.

OpenClaw lets a gateway delegate an incoming message to one of several agents.
pythonclaw mirrors that with a very small rule engine: each rule has a ``match``
clause (``startswith``, ``contains``, ``regex``, ``channel``) and picks an
``agent``. A default agent is used when nothing matches.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..session import Message


@dataclass
class Rule:
    agent: str
    startswith: str | None = None
    contains: str | None = None
    regex: re.Pattern[str] | None = None
    channel: str | None = None

    def matches(self, msg: Message) -> bool:
        text = msg.content or ""
        if self.channel and msg.channel != self.channel:
            return False
        if self.startswith is not None and not text.startswith(self.startswith):
            return False
        if self.contains is not None and self.contains not in text:
            return False
        if self.regex is not None and not self.regex.search(text):
            return False
        # at least one positive predicate must have fired (channel alone counts)
        return any(v is not None for v in
                   (self.startswith, self.contains, self.regex, self.channel))


class Router:
    def __init__(self, default_agent: str, rules: list[Rule] | None = None) -> None:
        self.default_agent = default_agent
        self.rules: list[Rule] = list(rules or [])

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "Router":
        default = cfg.get("default_agent")
        if not default:
            raise ValueError("router.default_agent is required")
        rules: list[Rule] = []
        for raw in cfg.get("rules", []) or []:
            match = raw.get("match", {})
            regex = match.get("regex")
            rules.append(Rule(
                agent=raw["agent"],
                startswith=match.get("startswith"),
                contains=match.get("contains"),
                regex=re.compile(regex) if regex else None,
                channel=match.get("channel"),
            ))
        return cls(default_agent=default, rules=rules)

    def pick(self, msg: Message) -> str:
        # explicit override from the inbound message wins over rules
        override = (msg.meta or {}).get("agent")
        if override:
            return override
        for rule in self.rules:
            if rule.matches(msg):
                return rule.agent
        return self.default_agent
