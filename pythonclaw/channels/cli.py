"""Interactive CLI channel: REPL over stdin/stdout.

Type a message and hit enter. ``/quit`` ends the session. ``/new`` starts a
fresh session id. ``/session`` prints the current session id.
"""
from __future__ import annotations

import sys
from typing import Any

from .base import Channel
from ..session import Message, Session


class CLIChannel(Channel):
    kind = "cli"

    def __init__(self, name: str = "cli", prompt: str = "you> ", **_: Any) -> None:
        super().__init__(name=name)
        self.prompt = prompt
        self.session: Session | None = None

    def run(self) -> None:
        self.session = Session.new(channel=self.name, user="local")
        sys.stdout.write(f"[pythonclaw cli] session={self.session.id}\n")
        sys.stdout.flush()
        while not self._stop.is_set():
            try:
                sys.stdout.write(self.prompt)
                sys.stdout.flush()
                line = sys.stdin.readline()
            except KeyboardInterrupt:
                sys.stdout.write("\n")
                break
            if not line:
                break
            text = line.rstrip("\n")
            if not text:
                continue
            if text in ("/quit", "/exit"):
                break
            if text == "/new":
                self.session = Session.new(channel=self.name, user="local")
                sys.stdout.write(f"[new session={self.session.id}]\n")
                continue
            if text == "/session":
                sys.stdout.write(f"[session={self.session.id if self.session else '?'}]\n")
                continue
            msg = Message(role="user", content=text, channel=self.name,
                          session_id=self.session.id, user="local")
            reply = self._dispatch(msg)
            self.send(reply)

    def send(self, reply: Message) -> None:
        who = reply.agent or "bot"
        sys.stdout.write(f"{who}> {reply.content}\n")
        sys.stdout.flush()
