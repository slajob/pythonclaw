"""The gateway ties channels, the router, agents and the memory together.

It owns the message bus. Every inbound channel message goes through:

    channel -> gateway.handle() -> router.pick() -> agent.handle() -> channel.send()

All of it is thread-safe: channels run on their own threads and the gateway
serializes per-session access via a lock map so concurrent messages in the
same session don't race on the memory store.
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import channels as chan_mod
from . import dotenv
from . import providers as prov_mod
from . import tools as tool_registry
from .agents import Agent, Router
from .config import Config
from .memory import SqliteMemory
from .providers.base import Provider
from .session import Message, Session


log = logging.getLogger("pythonclaw.gateway")


class Gateway:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._ensure_data_dir()
        self._load_dotenv()
        tool_registry.configure(config.tools)
        self.memory = self._build_memory()
        self.providers: dict[str, Provider] = self._build_providers()
        self.agents: dict[str, Agent] = self._build_agents()
        self.router = Router.from_config(config.router)
        self.channels: dict[str, chan_mod.Channel] = self._build_channels()
        self._session_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._locks_mu = threading.Lock()
        self._safety_warnings()

    def _safety_warnings(self) -> None:
        """Print loud warnings for dangerous combinations.

        Exposed-host + enabled shell tools + no auth token = trivially remotely
        exploitable. We warn at startup so operators notice even when logs are
        ignored.
        """
        tools_cfg = self.config.tools or {}
        host_access_enabled = any(
            (tools_cfg.get(n) or {}).get("enabled")
            for n in ("shell", "ls", "read_file")
        )
        if not host_access_enabled:
            return
        host = self.config.gateway.get("host", "127.0.0.1")
        has_auth = bool(self.config.gateway.get("auth_token"))
        if host not in ("127.0.0.1", "localhost", "::1"):
            log.warning(
                "SECURITY: host-access tools (shell/ls/read_file) are enabled "
                "AND gateway listens on %s. Any host-reachable client can run "
                "these tools.", host)
        if not has_auth:
            log.warning(
                "SECURITY: host-access tools (shell/ls/read_file) are enabled "
                "but gateway.auth_token is unset. Set auth_token in the "
                "config so POST /api/chat requires a bearer token.")

    # ------------------------------------------------------------------ boot
    def _ensure_data_dir(self) -> None:
        d = self.config.gateway.get("data_dir", "./.pythonclaw")
        Path(d).mkdir(parents=True, exist_ok=True)

    def _load_dotenv(self) -> None:
        """Pick up secrets written by the setup wizard.

        The file lives at ``<data_dir>/.env`` and contains ``KEY="value"``
        lines. Existing env vars win so CI / shell exports keep priority.
        """
        data_dir = Path(self.config.gateway.get("data_dir", "./.pythonclaw"))
        env_path = data_dir / ".env"
        if env_path.exists():
            dotenv.apply_to_env(dotenv.load(env_path))
            log.debug("loaded env from %s", env_path)

    def _build_memory(self) -> SqliteMemory:
        mem = self.config.memory
        return SqliteMemory(
            path=mem.get("path", "./.pythonclaw/memory.db"),
            max_messages=int(mem.get("max_messages_per_session", 200)),
        )

    def _build_providers(self) -> dict[str, Provider]:
        out: dict[str, Provider] = {}
        for name, cfg in (self.config.providers or {}).items():
            try:
                out[name] = prov_mod.build(name, cfg)
            except Exception as e:  # noqa: BLE001
                log.warning("provider %s disabled: %s", name, e)
        if not out:
            out["echo"] = prov_mod.build("echo", {"type": "echo"})
        return out

    def _build_agents(self) -> dict[str, Agent]:
        out: dict[str, Agent] = {}
        for name, cfg in (self.config.agents or {}).items():
            prov_name = cfg.get("provider", "echo")
            provider = self.providers.get(prov_name)
            if provider is None:
                log.warning("agent %s references missing provider %s; falling back to echo",
                            name, prov_name)
                provider = self.providers.setdefault(
                    "echo", prov_mod.build("echo", {"type": "echo"}))
            out[name] = Agent(
                name=name, provider=provider,
                system=cfg.get("system", ""),
                tools=list(cfg.get("tools", []) or []),
                model=cfg.get("model"),
                max_tokens=int(cfg.get("max_tokens", 1024)),
                temperature=float(cfg.get("temperature", 0.7)),
            )
        if not out:
            raise ValueError("at least one agent must be configured")
        return out

    def _build_channels(self) -> dict[str, chan_mod.Channel]:
        out: dict[str, chan_mod.Channel] = {}
        for name, cfg in (self.config.channels or {}).items():
            if cfg.get("enabled") is False:
                continue
            try:
                ch = chan_mod.build(name, cfg)
            except Exception as e:  # noqa: BLE001
                log.warning("channel %s disabled: %s", name, e)
                continue
            ch.attach(self.handle)
            out[name] = ch
        return out

    # --------------------------------------------------------------- runtime
    def start(self) -> None:
        for ch in self.channels.values():
            ch.start()

    def stop(self) -> None:
        for ch in self.channels.values():
            ch.stop()

    def join(self) -> None:
        for ch in self.channels.values():
            ch.join()

    def handle(self, msg: Message) -> Message:
        """Route an inbound message through the right agent and return the reply."""
        if not msg.session_id:
            raise ValueError("inbound message missing session_id")
        lock = self._lock_for(msg.session_id)
        with lock:
            agent_name = self.router.pick(msg)
            agent = self.agents.get(agent_name) or next(iter(self.agents.values()))
            # upsert the session record so /api/sessions and list_sessions() work.
            # we re-write on every message so the agent tag stays current — it
            # can change between turns via an explicit meta.agent override.
            if self.memory.get_session(msg.session_id) is None:
                self.memory.put_session(Session(
                    id=msg.session_id,
                    channel=msg.channel or "unknown",
                    user=msg.user, agent=agent.name))
            reply = agent.handle(msg, self.memory)
            return reply

    # --------------------------------------------------------------- helpers
    def _lock_for(self, session_id: str) -> threading.Lock:
        with self._locks_mu:
            return self._session_locks[session_id]

    def info(self) -> dict[str, Any]:
        return {
            "gateway": self.config.gateway,
            "agents": {n: a.info() for n, a in self.agents.items()},
            "providers": {n: p.info() for n, p in self.providers.items()},
            "channels": {n: c.info() for n, c in self.channels.items()},
            "router": {"default": self.router.default_agent,
                       "rules": len(self.router.rules)},
            "memory": self.memory.stats(),
        }
