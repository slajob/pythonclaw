# pythonclaw

A **Python clone of [OpenClaw](https://docs.openclaw.ai/)** — a self-hosted
gateway that connects messaging channels (CLI, WebChat, Discord, Telegram,
Slack, …) to AI agents, with a local web dashboard, multi-agent routing,
persistent sessions and an OpenAI-compatible HTTP API.

This implementation follows the shape of OpenClaw's architecture but is
written from scratch in pure Python using only the standard library (PyYAML
is optional for YAML configs).

## Architecture

```
            ┌──────────┐     ┌──────────┐     ┌──────────┐
 inbound ──▶│ channels │───▶ │ gateway  │───▶ │  router  │──▶ agent ──▶ provider
            └──────────┘     │          │     └──────────┘                │
                             │  memory  │ ◀─────────────────────── reply ─┘
                             └──────────┘
                                  ▲
                                  │ /api/*, /v1/*
                             ┌──────────┐
                             │ dashboard│  (http://127.0.0.1:18789)
                             └──────────┘
```

- **Gateway** (`pythonclaw.gateway.Gateway`) — wires everything together and
  owns the message bus. Thread-safe per-session.
- **Channels** (`pythonclaw.channels.*`) — adapters for chat surfaces:
  `cli`, `webchat`, `discord`, `telegram`, `slack`.
- **Agents** (`pythonclaw.agents.Agent`) — provider + system prompt + tool belt.
- **Router** (`pythonclaw.agents.Router`) — picks the agent for each inbound
  message via simple `startswith` / `contains` / `regex` / `channel` rules.
- **Providers** (`pythonclaw.providers.*`) — `echo` (offline), OpenAI-compatible
  (`openai`, Azure, LM Studio, Ollama, OpenRouter, Together, …), Anthropic.
- **Memory** (`pythonclaw.memory.SqliteMemory`) — SQLite-backed, per-session
  transcript with automatic pruning.
- **Tools** (`pythonclaw.tools`) — `time`, `calc`, `web_search`. Invoke via
  `@tool name arg=value` or `@tool name {"expr":"1+1"}`.
- **Dashboard + API** (`pythonclaw.web.Dashboard`) — tiny SPA at `/`, JSON at
  `/api/*`, and an OpenAI-compatible `/v1/chat/completions`.

## Install & run

```bash
# from the repo root, no install required:
python -m pythonclaw init --path pythonclaw.config.json
python -m pythonclaw info --config pythonclaw.config.json
python -m pythonclaw run  --config pythonclaw.config.json
# dashboard → http://127.0.0.1:18789
```

Or install as a package:

```bash
pip install -e .
pythonclaw run --config configs/example.json
```

### One-shot and REPL

```bash
python -m pythonclaw send --text "hello"                  # print reply and exit
python -m pythonclaw send --text "@tool calc expr=2+3*4"  # -> 14.0
python -m pythonclaw chat                                 # interactive REPL
python -m pythonclaw chat --agent coder                   # force agent
```

### OpenAI-compatible API

```bash
curl -s http://127.0.0.1:18789/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"pi","messages":[{"role":"user","content":"hi"}]}'
```

Point any OpenAI SDK (Python / JS / langchain / …) at
`http://127.0.0.1:18789/v1` with `api_key="pythonclaw"` (or your configured
`auth_token`) and it will talk to the gateway.

## Configuration

See [`configs/example.json`](configs/example.json). Highlights:

- `gateway.host` / `gateway.port` — where the dashboard listens
  (default `127.0.0.1:18789`, matching OpenClaw's local dashboard).
- `gateway.auth_token` — optional bearer token for `POST` endpoints.
- `router.default_agent` — fallback agent.
- `router.rules[].match.{startswith,contains,regex,channel}` — rule predicates.
- `agents.<name>.{provider,system,tools,model,max_tokens,temperature}`.
- `providers.<name>.{type,base_url,api_key_env,model}` for `echo` / `openai`
  / `anthropic`.
- `channels.<name>.enabled` — per-channel toggle.
- `memory.{path, max_messages_per_session}`.

Environment variables inside strings are interpolated with `${VAR}`.

## Channels

| Channel   | Inbound                                     | Outbound                  |
| --------- | ------------------------------------------- | ------------------------- |
| `cli`     | stdin REPL                                  | stdout                    |
| `webchat` | Dashboard (`/api/chat`, `/v1/chat/completions`) | HTTP response         |
| `discord` | REST long-poll (`GET /channels/:id/messages`) | `chat.postMessage`-like |
| `telegram`| `getUpdates` long-poll                      | `sendMessage`             |
| `slack`   | Events API POST to `/slack/events`          | `chat.postMessage`        |

The Discord/Telegram/Slack channels are intentionally minimal — swap in
`discord.py`, `python-telegram-bot` or `slack_sdk` in production.

## Tests

```bash
python tests/test_gateway.py     # stdlib-only runner
# or
pytest -q                        # if pytest is installed
```

## License

MIT — mirrors OpenClaw's own license.

## Credits

Inspired by and modelled on the OpenClaw documentation at
<https://docs.openclaw.ai/>. This is an independent re-implementation in
Python, not an official OpenClaw project.
