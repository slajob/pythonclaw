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


def _fresh_config(tmp: Path, port: int = 0, tools_cfg: dict | None = None) -> Config:
    data = {
        "gateway": {"host": "127.0.0.1", "port": port, "data_dir": str(tmp)},
        "router": {"default_agent": "pi",
                   "rules": [{"match": {"startswith": "@code"}, "agent": "coder"}]},
        "agents": {
            "pi": {"provider": "echo", "system": "pi-sys",
                   "tools": ["time", "calc"]},
            "coder": {"provider": "echo", "system": "coder-sys",
                      "tools": ["time", "calc"]},
            "gpt": {"provider": "echo", "system": "gpt-sys",
                    "model": "gpt-4o", "tools": []},
            "ops": {"provider": "echo", "system": "ops-sys",
                    "tools": ["shell", "ls", "read_file"]},
        },
        "providers": {"echo": {"type": "echo"}},
        "channels": {"webchat": {"type": "webchat", "enabled": True}},
        "memory": {"engine": "sqlite", "path": str(tmp / "mem.db"),
                   "max_messages_per_session": 50},
    }
    if tools_cfg is not None:
        data["tools"] = tools_cfg
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


def test_model_and_agent_override() -> None:
    with tempfile.TemporaryDirectory() as d:
        gw = Gateway(_fresh_config(Path(d)))
        session = Session.new(channel="test", user="u")

        # explicit agent override wins over router rules
        reply = gw.handle(Message(
            role="user", content="@code but via gpt",
            channel="test", session_id=session.id,
            meta={"agent": "gpt"}))
        assert reply.agent == "gpt"

        # model override is echoed back in reply.meta
        reply = gw.handle(Message(
            role="user", content="hi",
            channel="test", session_id=session.id,
            meta={"agent": "gpt", "model": "gpt-5-mini"}))
        assert reply.meta["model"] == "gpt-5-mini"


def test_openai_provider_allowed_models() -> None:
    from pythonclaw.providers import OpenAIProvider
    from pythonclaw.providers.base import CompletionRequest, ProviderError

    p = OpenAIProvider(name="openai", base_url="https://example.invalid/v1",
                       api_key=None, model="gpt-4o",
                       allowed_models=["gpt-5-mini", "gpt-4o", "gpt-5.2"])
    info = p.info()
    assert info["allowed_models"] == ["gpt-5-mini", "gpt-4o", "gpt-5.2"]
    assert info["default_model"] == "gpt-4o"
    # rejected before any network call
    try:
        p.complete(CompletionRequest(messages=[], model="not-on-list"))
    except ProviderError as e:
        assert "not in allowed_models" in str(e)
    else:
        raise AssertionError("expected ProviderError")


def test_shell_tool_disabled_by_default() -> None:
    with tempfile.TemporaryDirectory() as d:
        gw = Gateway(_fresh_config(Path(d)))  # no tools config
        session = Session.new(channel="test", user="u")
        reply = gw.handle(Message(
            role="user", content='@tool shell {"cmd": "echo hi"}',
            channel="test", session_id=session.id,
            meta={"agent": "ops"}))
        assert "disabled" in reply.content


def test_shell_tool_allowlist() -> None:
    tools_cfg = {
        "shell": {"enabled": True, "allowed_cmds": ["echo"], "timeout": 5},
    }
    with tempfile.TemporaryDirectory() as d:
        gw = Gateway(_fresh_config(Path(d), tools_cfg=tools_cfg))
        session = Session.new(channel="test", user="u")

        # allowed
        reply = gw.handle(Message(
            role="user", content='@tool shell {"cmd": "echo hello-host"}',
            channel="test", session_id=session.id,
            meta={"agent": "ops"}))
        assert "hello-host" in reply.content

        # not on allowlist
        reply = gw.handle(Message(
            role="user", content='@tool shell {"cmd": "whoami"}',
            channel="test", session_id=session.id,
            meta={"agent": "ops"}))
        assert "not in allowed_cmds" in reply.content


def test_ls_and_read_file_tools() -> None:
    with tempfile.TemporaryDirectory() as d:
        sandbox = Path(d) / "sandbox"
        sandbox.mkdir()
        (sandbox / "a.txt").write_text("hello\n", encoding="utf-8")
        (sandbox / "sub").mkdir()

        tools_cfg = {
            "ls": {"enabled": True, "allowed_paths": [str(sandbox)]},
            "read_file": {"enabled": True, "allowed_paths": [str(sandbox)],
                          "max_bytes": 1024},
        }
        gw = Gateway(_fresh_config(Path(d), tools_cfg=tools_cfg))
        session = Session.new(channel="test", user="u")

        reply = gw.handle(Message(
            role="user", content=f'@tool ls {{"path": "{sandbox}"}}',
            channel="test", session_id=session.id,
            meta={"agent": "ops"}))
        assert "a.txt" in reply.content
        assert "sub" in reply.content

        reply = gw.handle(Message(
            role="user",
            content=f'@tool read_file {{"path": "{sandbox / "a.txt"}"}}',
            channel="test", session_id=session.id,
            meta={"agent": "ops"}))
        assert "hello" in reply.content

        # path traversal rejected
        reply = gw.handle(Message(
            role="user",
            content=f'@tool ls {{"path": "{sandbox / ".." / ".."}"}}',
            channel="test", session_id=session.id,
            meta={"agent": "ops"}))
        assert "not in allowed_paths" in reply.content


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
                assert body["pythonclaw"]["agent"] == "pi"

            with urllib.request.urlopen(f"http://{host}:{port}/api/models", timeout=3) as r:
                body = json.loads(r.read())
                assert any(o["agent"] == "gpt" and o["model"] == "gpt-4o"
                           for o in body["options"])

            payload = json.dumps({
                "session_id": "ui-test", "text": "pick me",
                "agent": "gpt", "model": "gpt-5-mini",
            }).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/api/chat", data=payload,
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=3) as r:
                body = json.loads(r.read())
                assert body["reply"]["agent"] == "gpt"
                assert body["reply"]["meta"]["model"] == "gpt-5-mini"
        finally:
            dash.stop(); gw.stop()


if __name__ == "__main__":
    test_router_rules(); print("test_router_rules OK")
    test_gateway_end_to_end(); print("test_gateway_end_to_end OK")
    test_tool_call_dispatch(); print("test_tool_call_dispatch OK")
    test_model_and_agent_override(); print("test_model_and_agent_override OK")
    test_openai_provider_allowed_models(); print("test_openai_provider_allowed_models OK")
    test_shell_tool_disabled_by_default(); print("test_shell_tool_disabled_by_default OK")
    test_shell_tool_allowlist(); print("test_shell_tool_allowlist OK")
    test_ls_and_read_file_tools(); print("test_ls_and_read_file_tools OK")
    test_dashboard_http(); print("test_dashboard_http OK")
    print("all tests passed")
