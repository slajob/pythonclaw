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
- **Tools** (`pythonclaw.tools`) — `time`, `calc`, `web_search`, plus host
  access (`shell`, `ls`, `read_file`) that is disabled until you opt in.
  Invoke via `@tool name arg=value` or `@tool name {"expr":"1+1"}`.
- **Dashboard + API** (`pythonclaw.web.Dashboard`) — tiny SPA at `/`, JSON at
  `/api/*`, and an OpenAI-compatible `/v1/chat/completions`.

## Install & run

Fastest path (interactive onboarding):

```bash
python -m pythonclaw setup        # prompts for OpenAI key, Telegram token, etc.
python -m pythonclaw run --config ./pythonclaw.config.json
# dashboard → http://127.0.0.1:18789
```

Or manually:

```bash
python -m pythonclaw init --path pythonclaw.config.json
python -m pythonclaw info --config pythonclaw.config.json
python -m pythonclaw run  --config pythonclaw.config.json
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

### Onboarding wizard

`pythonclaw setup` walks you through configuring OpenAI and Telegram. It:

1. Creates / merges into `./pythonclaw.config.json`.
2. Writes secrets to `./.pythonclaw/.env` (`chmod 600`).
3. Adds a `gpt` agent wired to the OpenAI provider if you supply a key.
4. Optionally sets `gpt` as the router default.
5. Optionally enables the Telegram channel with the token you pass.

The gateway **auto-loads `./.pythonclaw/.env`** at startup (existing env vars
win), so after `setup` you can just `pythonclaw run` — no manual `export`.

Non-interactive (CI / automation):

```bash
python -m pythonclaw setup --non-interactive \
  --config ./pythonclaw.config.json \
  --openai-key "$OPENAI_API_KEY" --openai-model gpt-4o --gpt-default \
  --telegram-token "$TELEGRAM_BOT_TOKEN" --enable-telegram
```

From the **dashboard**: click the `settings` button in the header. The modal
shows the current status (chips for `openai: on/off`, `telegram: on/off`),
lets you paste new secrets and choose defaults. Save writes to the same
config + `.env` files as the CLI; a banner tells you to restart for the
change to take effect (providers and channels are built at boot).

API:

```bash
curl -s http://127.0.0.1:18789/api/settings     # GET status
curl -s -X POST http://127.0.0.1:18789/api/settings \
  -H 'content-type: application/json' \
  -d '{"openai_key":"sk-...","openai_model":"gpt-4o","gpt_default":true,
       "telegram_token":"123:abc","enable_telegram":true}'
```

### Host access (shell / ls / read_file)

pythonclaw can run commands on the host and read files from it, but **every
host-touching tool is disabled by default**. Opt in per-tool in the `tools`
section of your config:

```json
"tools": {
  "shell": {
    "enabled": true,
    "allowed_cmds": ["ls", "cat", "echo", "pwd", "whoami", "uname",
                     "find", "head", "tail", "wc", "stat", "du", "df"],
    "denied_cmds": ["rm", "mkfs", "dd", "shutdown", "sudo", "passwd"],
    "cwd": null,
    "timeout": 10,
    "max_output_bytes": 16384
  },
  "ls":        { "enabled": true, "allowed_paths": ["/home", "/tmp"] },
  "read_file": { "enabled": true, "allowed_paths": ["/home", "/tmp"],
                 "max_bytes": 65536 }
}
```

Then attach the tools to an agent and call them from chat:

```bash
# list /home:
curl -s -X POST http://127.0.0.1:18789/api/chat \
  -H 'content-type: application/json' \
  -d '{"agent":"ops","text":"@tool ls {\"path\":\"/home\"}"}'

# run a whitelisted shell command:
curl -s -X POST http://127.0.0.1:18789/api/chat \
  -H 'content-type: application/json' \
  -d '{"agent":"ops","text":"@tool shell {\"cmd\":\"ls -la /home\"}"}'
```

**Safety model.** The shell tool parses the command with `shlex` and runs it
*without* `shell=True`, so shell meta-characters (`|`, `>`, `;`, backticks,
`&&`) don't work. Every command's basename is checked against
`allowed_cmds` first and `denied_cmds` second. `ls` / `read_file` resolve
their target path and require it to live under one of `allowed_paths`
(symlink-safe). A `cmd`/`path` outside policy returns `error: ...` and never
touches the host. Still: **if you enable `shell` with a permissive allowlist,
anyone who can POST to `/api/chat` can run those commands.** Set
`gateway.auth_token` to require a bearer token, keep `gateway.host` on
`127.0.0.1`, and keep the allowlist tight.

### Model selection

The dashboard has a **model dropdown** in the header. It lists one option per
`(agent, model)` pair derived from your config:

- every agent's own `model`
- every entry in the agent's provider's `allowed_models`

Picking `gpt · gpt-4o (openai)` sends `{ agent: "gpt", model: "gpt-4o" }` on
every subsequent `/api/chat` call, so you can hot-swap models without
restarting. The choice is persisted in `localStorage`.

The default OpenAI provider in [`configs/example.json`](configs/example.json)
ships with:

```json
"openai": {
  "type": "openai",
  "base_url": "https://api.openai.com/v1",
  "api_key_env": "OPENAI_API_KEY",
  "model": "gpt-4o",
  "allowed_models": ["gpt-5-mini", "gpt-4o", "gpt-5.2"]
}
```

Set `OPENAI_API_KEY` in your environment and use the `gpt` agent:

```bash
export OPENAI_API_KEY=sk-...
python -m pythonclaw run --config configs/example.json
# then pick a model in the dashboard, or:
curl -s http://127.0.0.1:18789/api/chat \
  -H 'content-type: application/json' \
  -d '{"text":"hi","agent":"gpt","model":"gpt-5-mini"}'
```

Requests for a model that isn't on the provider's `allowed_models` list are
rejected before any network call.

Programmatic listing:

```bash
curl -s http://127.0.0.1:18789/api/models   # dropdown-friendly, with agents
curl -s http://127.0.0.1:18789/v1/models    # OpenAI-compatible
```

### OpenAI-compatible API

```bash
# non-streaming
curl -s http://127.0.0.1:18789/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"pi","messages":[{"role":"user","content":"hi"}]}'

# streaming (SSE, OpenAI SDK-compatible)
curl -sN http://127.0.0.1:18789/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"pi","stream":true,"messages":[{"role":"user","content":"hi"}]}'
```

Point any OpenAI SDK (Python / JS / langchain / …) at
`http://127.0.0.1:18789/v1` with `api_key="pythonclaw"` (or your configured
`auth_token`) and it will talk to the gateway. The `model` field is mapped to
a configured agent or a concrete model on a provider's `allowed_models` list.

> **Note on streaming:** the underlying provider responds synchronously and
> pythonclaw chunks the final text into SSE events. It's wire-compatible with
> clients that iterate over SSE chunks but not token-by-token real-time.

### Authentication

Set `gateway.auth_token` in the config (or via `pythonclaw setup`) and the
dashboard will require `Authorization: Bearer <token>` on **every** `/api/*`
and `/v1/*` endpoint (`/` and `/healthz` stay public). This is important:
anything that reads conversations or settings — not just chat POSTs — is
gated. When host-access tools (`shell`/`ls`/`read_file`) are enabled the
gateway **warns on startup** if `auth_token` is empty or the host isn't
loopback.

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
