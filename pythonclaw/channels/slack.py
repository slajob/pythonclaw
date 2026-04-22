"""Slack channel: Web API + inbound webhook hook.

This is a passive channel that can reply via ``chat.postMessage`` once you
dispatch a message into it. Inbound events are expected to be delivered by the
dashboard's ``/slack/events`` endpoint (Slack Events API) which forwards them
into :meth:`submit`.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .base import Channel
from ..session import Message, Session


class SlackChannel(Channel):
    kind = "slack"

    def __init__(self, name: str = "slack", token: str | None = None,
                 default_channel: str | None = None, **_: Any) -> None:
        super().__init__(name=name)
        self.token = token
        self.default_channel = default_channel
        self._sessions: dict[str, Session] = {}

    def _session_for(self, slack_channel: str) -> Session:
        s = self._sessions.get(slack_channel)
        if s is None:
            s = Session.new(channel=self.name, user=f"slack:{slack_channel}")
            self._sessions[slack_channel] = s
        return s

    def run(self) -> None:
        self._stop.wait()

    def submit(self, text: str, slack_channel: str, user: str | None = None) -> Message:
        s = self._session_for(slack_channel)
        msg = Message(role="user", content=text, channel=self.name,
                      session_id=s.id, user=user,
                      meta={"slack_channel": slack_channel})
        return self._dispatch(msg)

    def send(self, reply: Message) -> None:
        if not self.token:
            return
        slack_channel = (reply.meta or {}).get("slack_channel") or self.default_channel
        if not slack_channel:
            return
        body = json.dumps({"channel": slack_channel, "text": reply.content}).encode("utf-8")
        req = urllib.request.Request(
            "https://slack.com/api/chat.postMessage",
            data=body, method="POST",
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
                resp.read()
        except Exception as e:  # noqa: BLE001
            print(f"[slack:{self.name}] send error: {e}")
