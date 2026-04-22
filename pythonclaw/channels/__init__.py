"""Channel registry and factory."""
from __future__ import annotations

import os
from typing import Any

from .base import Channel
from .cli import CLIChannel
from .webchat import WebChatChannel
from .discord import DiscordChannel
from .telegram import TelegramChannel
from .slack import SlackChannel


_REGISTRY = {
    "cli": CLIChannel,
    "webchat": WebChatChannel,
    "discord": DiscordChannel,
    "telegram": TelegramChannel,
    "slack": SlackChannel,
}


def build(name: str, cfg: dict[str, Any]) -> Channel:
    t = cfg.get("type", name)
    if t not in _REGISTRY:
        raise ValueError(f"unknown channel type: {t!r}")
    kwargs: dict[str, Any] = {"name": name}
    if token_env := cfg.get("token_env"):
        kwargs["token"] = os.environ.get(token_env)
    if "token" in cfg:
        kwargs["token"] = cfg["token"]
    for k in ("prompt", "webhook_url", "app_id"):
        if k in cfg:
            kwargs[k] = cfg[k]
    return _REGISTRY[t](**kwargs)


__all__ = ["Channel", "CLIChannel", "WebChatChannel", "DiscordChannel",
           "TelegramChannel", "SlackChannel", "build"]
