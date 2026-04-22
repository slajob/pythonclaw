"""Provider registry.

Providers convert a list of chat messages into an assistant response. pythonclaw
ships with an offline ``echo`` provider plus HTTP clients for OpenAI-compatible
and Anthropic-compatible backends, all behind the same ``Provider`` interface.
"""
from __future__ import annotations

import os
from typing import Any

from .base import Provider, ProviderError
from .echo import EchoProvider
from .openai import OpenAIProvider
from .anthropic import AnthropicProvider


def build(name: str, cfg: dict[str, Any]) -> Provider:
    t = cfg.get("type", name)
    if t == "echo":
        return EchoProvider(name=name, **{k: v for k, v in cfg.items() if k != "type"})
    if t == "openai":
        return OpenAIProvider(
            name=name,
            base_url=cfg.get("base_url", "https://api.openai.com/v1"),
            api_key=_resolve_key(cfg),
            model=cfg.get("model", "gpt-4o-mini"),
            timeout=cfg.get("timeout", 60),
            allowed_models=cfg.get("allowed_models"),
        )
    if t == "anthropic":
        return AnthropicProvider(
            name=name,
            base_url=cfg.get("base_url", "https://api.anthropic.com/v1"),
            api_key=_resolve_key(cfg),
            model=cfg.get("model", "claude-sonnet-4-6"),
            timeout=cfg.get("timeout", 60),
        )
    raise ProviderError(f"unknown provider type: {t!r}")


def _resolve_key(cfg: dict[str, Any]) -> str | None:
    if cfg.get("api_key"):
        return cfg["api_key"]
    env = cfg.get("api_key_env")
    if env:
        return os.environ.get(env)
    return None


__all__ = ["Provider", "ProviderError", "EchoProvider", "OpenAIProvider",
           "AnthropicProvider", "build"]
