"""WebChat channel.

Unlike other channels, WebChat does not spin its own listener thread — it is
driven by the dashboard HTTP server which calls :meth:`submit` for every user
message and hands the reply straight back in the HTTP response.
"""
from __future__ import annotations

import threading
from typing import Any

from .base import Channel
from ..session import Message, Session


class WebChatChannel(Channel):
    kind = "webchat"

    def __init__(self, name: str = "webchat", **_: Any) -> None:
        super().__init__(name=name)
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def run(self) -> None:  # driven externally by the HTTP server
        self._stop.wait()

    # called by the dashboard ------------------------------------------------
    def ensure_session(self, session_id: str | None, user: str | None = None) -> Session:
        with self._lock:
            if session_id and session_id in self._sessions:
                return self._sessions[session_id]
            s = Session.new(channel=self.name, user=user or "web") if not session_id \
                else Session(id=session_id, channel=self.name, user=user or "web")
            self._sessions[s.id] = s
            return s

    def submit(self, text: str, session_id: str | None = None,
               user: str | None = None, model: str | None = None,
               agent: str | None = None) -> Message:
        s = self.ensure_session(session_id, user)
        meta: dict[str, Any] = {}
        if model:
            meta["model"] = model
        if agent:
            meta["agent"] = agent
        msg = Message(role="user", content=text, channel=self.name,
                      session_id=s.id, user=s.user, meta=meta)
        return self._dispatch(msg)

    def send(self, reply: Message) -> None:  # no-op: replies go over HTTP
        return
