"""Smoke tests for pythonclaw.

Runs entirely offline against the echo provider. Usable both via pytest and
``python tests/test_gateway.py`` so the test layer doesn't impose extra deps.
"""
from __future__ import annotations

import json
import tempfile
import urllib.request
from pathlib import Path

from pythonclaw.agents import Router
from pythonclaw.config import Config
from pythonclaw.gateway import Gateway
from pythonclaw.session import Message, Session
from pythonclaw.web.dashboard import Dashboard


def _fresh_config(tmp: Path, port: int = 0) -> Config:
    data = {
        "gateway": {"host": "127.0.0.1", "port": port, "data_dir": str(tmp)},
        "router": {"default_agent": "pi",
                   "rules": [{"match": {"startswith": "@code"}, "agent": "coder"}]},
        "agents": {
            "pi": {"provider": "echo", "system": "pi-sys",
                   "tools": ["time", "calc"]},
            "coder": {"provider": "echo", "system": "coder-sys",
                      "tools": ["time", "calc"]},
        },
        "providers": {"echo": {"type": "echo"}},
        "channels": {"webchat": {"type": "webchat", "enabled": True}},
        "memory": {"engine": "sqlite", "path": str(tmp / "mem.db"),
                   "max_messages_per_session": 50},
    }
    return Config(raw=data)


def test_router_rules() -> None:
    r = Router.from_config({"default_agent": "pi",
                            "rules": [{"match": {"startswith": "@code"}, "agent": "coder"}]})
    assert r.pick(Message(role="user", content="hello")) == "pi"
    assert r.pick(Message(role="user", content="@code write tests")) == "coder"


def test_gateway_end_to_end() -> None:
    with tempfile.TemporaryDirectory() as d:
        gw = Gateway(_fresh_config(Path(d)))
        session = Session.new(channel="test", user="u")
        r1 = gw.handle(Message(role="user", content="hello",
                               channel="test", session_id=session.id))
        assert r1.agent == "pi"
        assert "hello" in r1.content

        r2 = gw.handle(Message(role="user", content="@code refactor",
                               channel="test", session_id=session.id))
        assert r2.agent == "coder"

        history = gw.memory.history(session.id)
        # 2 user + 2 assistant messages
        assert len(history) == 4


def test_tool_call_dispatch() -> None:
    with tempfile.TemporaryDirectory() as d:
        gw = Gateway(_fresh_config(Path(d)))
        session = Session.new(channel="test", user="u")
        reply = gw.handle(Message(role="user", content="@tool calc expr=2+3*4",
                                  channel="test", session_id=session.id))
        assert reply.content.strip() == "14.0"


def test_dashboard_http() -> None:
    with tempfile.TemporaryDirectory() as d:
        gw = Gateway(_fresh_config(Path(d)))
        dash = Dashboard(gw)
        gw.start()
        dash.start()
        try:
            host, port = dash.host, dash._server.server_address[1]  # type: ignore[union-attr]
            with urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=3) as r:
                body = json.loads(r.read())
                assert body["ok"] is True

            payload = json.dumps({"text": "ping"}).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/api/chat", data=payload,
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=3) as r:
                body = json.loads(r.read())
                assert body["reply"]["role"] == "assistant"
                assert "ping" in body["reply"]["content"]

            payload = json.dumps({
                "model": "pi",
                "messages": [{"role": "user", "content": "howdy"}],
            }).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/chat/completions", data=payload,
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=3) as r:
                body = json.loads(r.read())
                assert body["choices"][0]["message"]["content"]
                assert body["object"] == "chat.completion"
        finally:
            dash.stop(); gw.stop()


if __name__ == "__main__":
    test_router_rules(); print("test_router_rules OK")
    test_gateway_end_to_end(); print("test_gateway_end_to_end OK")
    test_tool_call_dispatch(); print("test_tool_call_dispatch OK")
    test_dashboard_http(); print("test_dashboard_http OK")
    print("all tests passed")
