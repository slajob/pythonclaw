"""Onboarding wizard.

Two entrypoints:

- :func:`interactive_wizard` — prompts on stdin; used by ``pythonclaw setup``.
- :func:`apply` — deterministic, no I/O besides writing the config + ``.env``;
  used by the non-interactive path and by the dashboard's ``POST /api/settings``.

The wizard produces two files:

- ``<config>`` (default ``./pythonclaw.config.json``) — the gateway config. If
  it already exists it is merged in-place so the wizard is safe to re-run.
- ``<data_dir>/.env`` (default ``./.pythonclaw/.env``) — secrets. The gateway
  auto-loads this at startup, before providers/channels are built.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from getpass import getpass
from pathlib import Path
from typing import Any

from . import dotenv
from .config import Config


@dataclass
class Answers:
    config_path: Path
    data_dir: Path
    openai_key: str | None = None
    openai_model: str = "gpt-4o"
    make_gpt_default: bool = False
    telegram_token: str | None = None
    enable_telegram: bool = False


def _prompt(q: str, default: str = "", secret: bool = False) -> str:
    suffix = f" [{default}]" if default and not secret else ""
    if secret:
        raw = getpass(f"{q}{suffix}: ")
    else:
        raw = input(f"{q}{suffix}: ")
    return raw.strip() or default


def _yn(q: str, default: bool = True) -> bool:
    prompt = "Y/n" if default else "y/N"
    ans = input(f"{q} [{prompt}]: ").strip().lower()
    if not ans:
        return default
    return ans.startswith("y")


def interactive_wizard(default_config: Path | None = None,
                       default_data_dir: Path | None = None) -> Answers:
    print("Welcome to pythonclaw setup!")
    print("Press Enter to accept the default in [brackets]. Secrets are hidden.\n")
    a = Answers(
        config_path=Path(_prompt("Config file (created if missing)",
                                  str(default_config or "./pythonclaw.config.json"))),
        data_dir=Path(_prompt("Data directory",
                               str(default_data_dir or "./.pythonclaw"))),
    )
    print("\n--- OpenAI ---")
    if _yn("Configure OpenAI?", default=True):
        a.openai_key = _prompt("API key (leave blank to skip)", "", secret=True) or None
        a.openai_model = _prompt("Default model", "gpt-4o")
        if a.openai_key:
            a.make_gpt_default = _yn('Make "gpt" the default agent?', default=True)

    print("\n--- Telegram ---")
    if _yn("Configure Telegram bot?", default=False):
        a.telegram_token = _prompt("Bot token", "", secret=True) or None
        if a.telegram_token:
            a.enable_telegram = _yn("Enable Telegram channel now?", default=True)

    return a


def apply(answers: Answers) -> dict[str, Any]:
    """Merge ``answers`` into the config file and write secrets to the env file.

    Returns a small summary dict with the written paths and status flags.
    Safe to re-run; existing config values are preserved.
    """
    cfg_path = answers.config_path
    data_dir = answers.data_dir
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    if cfg_path.exists():
        data = Config.load(cfg_path).raw
    else:
        data = Config.default().raw
        data.setdefault("gateway", {})["data_dir"] = str(data_dir)

    data.setdefault("providers", {})
    data.setdefault("agents", {})
    data.setdefault("channels", {})
    data.setdefault("router", {"default_agent": "pi", "rules": []})

    summary: dict[str, Any] = {
        "config": str(cfg_path),
        "env_file": str(data_dir / ".env"),
        "data_dir": str(data_dir),
        "openai_configured": False,
        "telegram_configured": False,
    }

    # ---- OpenAI ---------------------------------------------------------
    if answers.openai_key or (cfg_path.exists() and data.get("providers", {}).get("openai")):
        prov = data["providers"].setdefault("openai", {
            "type": "openai",
            "base_url": "https://api.openai.com/v1",
            "api_key_env": "OPENAI_API_KEY",
            "allowed_models": ["gpt-5-mini", "gpt-4o", "gpt-5.2"],
        })
        prov.setdefault("type", "openai")
        prov["model"] = answers.openai_model or prov.get("model", "gpt-4o")
        prov.setdefault("api_key_env", "OPENAI_API_KEY")
        prov.setdefault("allowed_models", ["gpt-5-mini", "gpt-4o", "gpt-5.2"])

        if "gpt" not in data["agents"]:
            data["agents"]["gpt"] = {
                "provider": "openai",
                "system": "You are a helpful AI assistant.",
                "model": prov["model"],
                "tools": ["time", "calc", "web_search"],
            }
        else:
            data["agents"]["gpt"]["model"] = prov["model"]

        # helpful routing rule
        rules = data["router"].setdefault("rules", [])
        if not any(r.get("agent") == "gpt" for r in rules):
            rules.append({"match": {"startswith": "@gpt"}, "agent": "gpt"})

        if answers.make_gpt_default:
            data["router"]["default_agent"] = "gpt"
        summary["openai_configured"] = True

    # ---- Telegram -------------------------------------------------------
    if answers.telegram_token or (cfg_path.exists() and data.get("channels", {}).get("telegram")):
        ch = data["channels"].setdefault("telegram", {
            "type": "telegram", "token_env": "TELEGRAM_BOT_TOKEN"})
        ch["type"] = "telegram"
        ch.setdefault("token_env", "TELEGRAM_BOT_TOKEN")
        ch["enabled"] = bool(answers.enable_telegram) if answers.telegram_token \
            else ch.get("enabled", False)
        summary["telegram_configured"] = True

    # ---- write config ---------------------------------------------------
    cfg_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    # ---- write secrets into .env ---------------------------------------
    env_updates: dict[str, str | None] = {}
    if answers.openai_key:
        env_updates["OPENAI_API_KEY"] = answers.openai_key
    if answers.telegram_token:
        env_updates["TELEGRAM_BOT_TOKEN"] = answers.telegram_token
    if env_updates:
        dotenv.update(data_dir / ".env", **env_updates)
    summary["env_updated_keys"] = sorted(env_updates.keys())

    return summary
