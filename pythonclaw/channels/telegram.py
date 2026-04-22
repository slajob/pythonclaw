"""Telegram bot channel via getUpdates long-polling.

Config::

    "telegram": {
      "type": "telegram",
      "enabled": true,
      "token_env": "TELEGRAM_BOT_TOKEN"
    }
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .base import Channel
from ..session import Message, Session


class TelegramChannel(Channel):
    kind = "telegram"

    def __init__(self, name: str = "telegram", token: str | None = None,
                 timeout: int = 25, **_: Any) -> None:
        super().__init__(name=name)
        self.token = token
        self.timeout = timeout
        self._offset: int | None = None
        self._sessions: dict[int, Session] = {}

    def _api(self, method: str, body: dict[str, Any] | None = None, *, _timeout: float = 30) -> Any:
        if not self.token:
            raise RuntimeError("telegram channel: missing bot token")
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        data = json.dumps(body).encode("utf-8") if body else None
        headers = {"Content-Type": "application/json"} if body else {}
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if body else "GET")
        with urllib.request.urlopen(req, timeout=_timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8") or "null")

    def _session_for(self, chat_id: int) -> Session:
        s = self._sessions.get(chat_id)
        if s is None:
            s = Session.new(channel=self.name, user=f"tg:{chat_id}")
            self._sessions[chat_id] = s
        return s

    def run(self) -> None:
        if not self.token:
            print(f"[telegram:{self.name}] disabled: missing token")
            self._stop.wait()
            return
        while not self._stop.is_set():
            try:
                body = {"timeout": self.timeout}
                if self._offset is not None:
                    body["offset"] = self._offset
                resp = self._api("getUpdates", body, _timeout=self.timeout + 10)
                for upd in resp.get("result", []):
                    self._offset = upd["update_id"] + 1
                    msg = upd.get("message") or upd.get("edited_message")
                    if not msg:
                        continue
                    chat_id = msg["chat"]["id"]
                    text = msg.get("text", "")
                    if not text:
                        continue
                    s = self._session_for(chat_id)
                    inbound = Message(role="user", content=text, channel=self.name,
                                      session_id=s.id,
                                      user=str(msg.get("from", {}).get("username")
                                               or msg.get("from", {}).get("id")),
                                      meta={"chat_id": chat_id})
                    reply = self._dispatch(inbound)
                    self.send(reply)
            except urllib.error.HTTPError as e:
                print(f"[telegram:{self.name}] HTTP {e.code}")
            except Exception as e:  # noqa: BLE001
                print(f"[telegram:{self.name}] poll error: {e}")

    def send(self, reply: Message) -> None:
        chat_id = (reply.meta or {}).get("chat_id")
        if chat_id is None:
            return
        try:
            self._api("sendMessage",
                      {"chat_id": chat_id, "text": reply.content[:4000]})
        except Exception as e:  # noqa: BLE001
            print(f"[telegram:{self.name}] send error: {e}")
