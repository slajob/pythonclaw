"""Discord channel (long-polling REST fallback).

A minimal bot-gateway that polls the Discord REST API for messages in the
configured channel id. It avoids a full WebSocket Gateway implementation on
purpose — this is a clone for learning, not a production discord library. Use
``discord.py`` or ``hikari`` in production.

Config::

    "discord": {
      "type": "discord",
      "enabled": true,
      "token_env": "DISCORD_TOKEN",
      "channel_id": "1234567890"
    }
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from .base import Channel
from ..session import Message, Session


_API = "https://discord.com/api/v10"


class DiscordChannel(Channel):
    kind = "discord"

    def __init__(self, name: str = "discord", token: str | None = None,
                 channel_id: str | None = None, poll_interval: float = 3.0,
                 **_: Any) -> None:
        super().__init__(name=name)
        self.token = token
        self.channel_id = channel_id
        self.poll_interval = poll_interval
        self._seen_after: str | None = None
        self._session = Session.new(channel=self.name, user="discord")

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        if not self.token:
            raise RuntimeError("discord channel: missing bot token")
        data = json.dumps(body).encode("utf-8") if body else None
        req = urllib.request.Request(
            f"{_API}{path}", data=data, method=method,
            headers={"Authorization": f"Bot {self.token}",
                     "Content-Type": "application/json",
                     "User-Agent": "pythonclaw (https://github.com/, 0.1)"})
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8") or "null")

    def run(self) -> None:
        if not (self.token and self.channel_id):
            print(f"[discord:{self.name}] disabled: missing token or channel_id")
            self._stop.wait()
            return
        while not self._stop.is_set():
            try:
                path = f"/channels/{self.channel_id}/messages?limit=10"
                if self._seen_after:
                    path += f"&after={self._seen_after}"
                msgs = self._request("GET", path) or []
                for m in reversed(msgs):  # oldest first
                    self._seen_after = m["id"]
                    if m.get("author", {}).get("bot"):
                        continue
                    text = m.get("content", "") or ""
                    if not text:
                        continue
                    inbound = Message(role="user", content=text, channel=self.name,
                                      session_id=self._session.id,
                                      user=m.get("author", {}).get("username"),
                                      meta={"message_id": m["id"]})
                    reply = self._dispatch(inbound)
                    self.send(reply)
            except urllib.error.HTTPError as e:
                print(f"[discord:{self.name}] HTTP {e.code}")
            except Exception as e:  # noqa: BLE001
                print(f"[discord:{self.name}] poll error: {e}")
            self._stop.wait(self.poll_interval)

    def send(self, reply: Message) -> None:
        if not (self.token and self.channel_id and reply.content):
            return
        try:
            self._request("POST", f"/channels/{self.channel_id}/messages",
                          {"content": reply.content[:1990]})
        except Exception as e:  # noqa: BLE001
            print(f"[discord:{self.name}] send error: {e}")
