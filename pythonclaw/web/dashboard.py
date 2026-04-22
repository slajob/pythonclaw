"""Local web dashboard + HTTP API.

Serves a tiny single-page chat UI at ``/`` and JSON endpoints under ``/api/*``
and ``/v1/*`` (the OpenAI-compatible surface). Defaults to 127.0.0.1:18789,
matching OpenClaw's local dashboard port.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from ..gateway import Gateway
from ..session import Message


log = logging.getLogger("pythonclaw.web")


_INDEX_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><title>pythonclaw dashboard</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  :root { color-scheme: light dark; }
  body { font-family: ui-sans-serif, system-ui, sans-serif; margin: 0; height: 100vh;
         display: grid; grid-template-rows: auto 1fr auto; }
  header { padding: 8px 14px; border-bottom: 1px solid #8885;
           display: flex; gap: 10px; align-items: center; font-weight: 600; }
  header small { font-weight: 400; opacity: .7; }
  #log { overflow: auto; padding: 10px 14px; }
  .m { white-space: pre-wrap; padding: 8px 12px; margin: 6px 0;
       border-radius: 10px; max-width: 80ch; }
  .m.user { background: #60a5fa22; align-self: flex-end; margin-left: auto; }
  .m.bot  { background: #10b98122; }
  .m.sys  { background: #9ca3af22; font-style: italic; }
  .meta { font-size: 11px; opacity: .6; margin-top: 4px; }
  #log .row { display: flex; }
  form { display: flex; gap: 6px; padding: 10px; border-top: 1px solid #8885; }
  input[type=text] { flex: 1; padding: 10px; border-radius: 8px; border: 1px solid #8885; }
  button { padding: 10px 14px; border-radius: 8px; border: 0; background: #2563eb; color: white; cursor: pointer; }
  button.secondary { background: #64748b; }
</style>
</head><body>
<header>
  <span>pythonclaw</span>
  <small id="meta">loading…</small>
  <span style="margin-left:auto"></span>
  <label for="model" style="font-weight:400;font-size:12px;opacity:.7">model</label>
  <select id="model" title="Pick an agent / model"></select>
  <button class="secondary" id="new">new session</button>
</header>
<div id="log"></div>
<form id="f">
  <input id="t" type="text" autocomplete="off" placeholder="message…" autofocus>
  <button>send</button>
</form>
<script>
let sessionId = localStorage.getItem("pc_session") || null;
let selection = JSON.parse(localStorage.getItem("pc_model") || "null");

async function info() {
  const r = await fetch("/api/info"); const j = await r.json();
  document.getElementById("meta").textContent =
    `agents: ${Object.keys(j.agents).join(", ")} · router→${j.router.default} · mem: ${j.memory.messages} msgs`;
}

async function loadModels() {
  const r = await fetch("/api/models"); const j = await r.json();
  const sel = document.getElementById("model");
  sel.innerHTML = "";
  const def = document.createElement("option");
  def.value = ""; def.textContent = "(router default)";
  sel.appendChild(def);
  for (const opt of j.options || []) {
    const o = document.createElement("option");
    o.value = JSON.stringify({ agent: opt.agent, model: opt.model });
    o.textContent = opt.label;
    sel.appendChild(o);
  }
  if (selection) {
    const key = JSON.stringify(selection);
    for (const o of sel.options) if (o.value === key) { sel.value = key; break; }
  }
  sel.onchange = () => {
    selection = sel.value ? JSON.parse(sel.value) : null;
    if (selection) localStorage.setItem("pc_model", JSON.stringify(selection));
    else localStorage.removeItem("pc_model");
  };
}
function add(role, text, meta) {
  const log = document.getElementById("log");
  const row = document.createElement("div"); row.className = "row";
  const d = document.createElement("div");
  d.className = "m " + (role === "user" ? "user" : role === "system" ? "sys" : "bot");
  d.textContent = text;
  if (meta) { const m = document.createElement("div"); m.className = "meta"; m.textContent = meta; d.appendChild(m); }
  row.appendChild(d); log.appendChild(row); log.scrollTop = log.scrollHeight;
}
async function loadHistory() {
  if (!sessionId) return;
  const r = await fetch("/api/history?session_id=" + encodeURIComponent(sessionId));
  if (!r.ok) return;
  const j = await r.json();
  for (const m of j.messages) add(m.role, m.content, m.agent ? `${m.agent}` : null);
}
document.getElementById("new").onclick = () => {
  sessionId = null; localStorage.removeItem("pc_session");
  document.getElementById("log").innerHTML = "";
  add("system", "(new session)", null);
};
document.getElementById("f").onsubmit = async (ev) => {
  ev.preventDefault();
  const t = document.getElementById("t"); const text = t.value.trim(); if (!text) return;
  t.value = ""; add("user", text);
  const body = { session_id: sessionId, text };
  if (selection) { body.agent = selection.agent; body.model = selection.model; }
  const r = await fetch("/api/chat", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  const j = await r.json();
  if (j.session_id) { sessionId = j.session_id; localStorage.setItem("pc_session", sessionId); }
  if (j.reply) {
    const m = j.reply.meta || {};
    const tag = [j.reply.agent, m.model].filter(Boolean).join(" · ");
    add("assistant", j.reply.content, tag);
  } else {
    add("system", j.error || "(no reply)");
  }
};
info(); loadModels(); loadHistory();
</script>
</body></html>
"""


class Dashboard:
    def __init__(self, gateway: Gateway) -> None:
        self.gateway = gateway
        cfg = gateway.config.gateway
        self.host = cfg.get("host", "127.0.0.1")
        self.port = int(cfg.get("port", 18789))
        self.auth_token = cfg.get("auth_token")
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        handler = _make_handler(self)
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="pc-web", daemon=True)
        self._thread.start()
        log.info("dashboard on http://%s:%d", self.host, self.port)

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def join(self) -> None:
        if self._thread:
            self._thread.join()


# ---------------------------------------------------------------- handler

def _make_handler(dash: "Dashboard"):
    class Handler(BaseHTTPRequestHandler):
        server_version = "pythonclaw/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            log.debug("%s - - %s", self.address_string(), fmt % args)

        # ---- helpers ----
        def _write(self, status: int, body: bytes, content_type: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj: Any) -> None:
            self._write(status, json.dumps(obj).encode("utf-8"))

        def _read_json(self) -> Any:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                return json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                return None

        def _auth_ok(self) -> bool:
            if not dash.auth_token:
                return True
            got = self.headers.get("Authorization", "")
            return got == f"Bearer {dash.auth_token}"

        # ---- routing ----
        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                self._write(200, _INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/healthz":
                self._json(200, {"ok": True, "ts": time.time()})
                return
            if path == "/api/info":
                self._json(200, dash.gateway.info())
                return
            if path == "/api/history":
                qs = _parse_qs(self.path)
                sid = qs.get("session_id")
                if not sid:
                    self._json(400, {"error": "session_id required"}); return
                msgs = dash.gateway.memory.history(sid)
                self._json(200, {"session_id": sid,
                                 "messages": [m.to_dict() for m in msgs]})
                return
            if path == "/api/sessions":
                self._json(200, {"sessions": [
                    {"id": s.id, "channel": s.channel, "user": s.user,
                     "agent": s.agent, "created": s.created}
                    for s in dash.gateway.memory.list_sessions()]})
                return
            if path == "/v1/models":
                self._json(200, _openai_models_payload(dash.gateway))
                return
            if path == "/api/models":
                self._json(200, _ui_models_payload(dash.gateway))
                return
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if not self._auth_ok():
                self._json(401, {"error": "unauthorized"}); return
            if path == "/api/chat":
                self._handle_chat(); return
            if path == "/v1/chat/completions":
                self._handle_openai_completions(); return
            if path == "/slack/events":
                self._handle_slack_events(); return
            self._json(404, {"error": "not found"})

        # ---- handlers ----
        def _handle_chat(self) -> None:
            body = self._read_json() or {}
            text = (body.get("text") or "").strip()
            if not text:
                self._json(400, {"error": "text required"}); return
            webchat = dash.gateway.channels.get("webchat")
            if webchat is None:
                self._json(500, {"error": "webchat channel not enabled"}); return
            try:
                reply = webchat.submit(  # type: ignore[attr-defined]
                    text=text, session_id=body.get("session_id"),
                    user=body.get("user"),
                    model=body.get("model"),
                    agent=body.get("agent"))
            except Exception as e:  # noqa: BLE001
                self._json(500, {"error": str(e)}); return
            self._json(200, {"session_id": reply.session_id,
                             "reply": reply.to_dict()})

        def _handle_openai_completions(self) -> None:
            body = self._read_json() or {}
            messages = body.get("messages") or []
            if not messages:
                self._json(400, {"error": "messages required"}); return
            # The dashboard funnels OpenAI-style calls through the webchat channel
            # so they share the same session machinery and get stored in memory.
            webchat = dash.gateway.channels.get("webchat")
            if webchat is None:
                self._json(500, {"error": "webchat channel not enabled"}); return
            last_user = next(
                (m for m in reversed(messages) if (m.get("role") == "user")), None)
            if not last_user:
                self._json(400, {"error": "no user message"}); return
            session_id = (body.get("user") or body.get("session_id")
                          or f"openai-{uuid.uuid4().hex[:8]}")
            agent_override, model_override = _resolve_openai_model(
                dash.gateway, body.get("model"))
            try:
                reply = webchat.submit(  # type: ignore[attr-defined]
                    text=str(last_user.get("content", "")),
                    session_id=session_id, user=body.get("user"),
                    agent=agent_override, model=model_override)
            except Exception as e:  # noqa: BLE001
                self._json(500, {"error": str(e)}); return
            self._json(200, _openai_response(body, reply))

        def _handle_slack_events(self) -> None:
            body = self._read_json() or {}
            # URL verification handshake
            if body.get("type") == "url_verification":
                self._json(200, {"challenge": body.get("challenge", "")}); return
            event = body.get("event") or {}
            if event.get("type") == "message" and not event.get("bot_id"):
                slack_ch = dash.gateway.channels.get("slack")
                if slack_ch is not None:
                    try:
                        reply = slack_ch.submit(  # type: ignore[attr-defined]
                            text=event.get("text", ""),
                            slack_channel=event.get("channel", ""),
                            user=event.get("user"))
                        slack_ch.send(reply)
                    except Exception as e:  # noqa: BLE001
                        log.warning("slack event failed: %s", e)
            self._json(200, {"ok": True})

    return Handler


# ---------------------------------------------------------------- utilities

def _parse_qs(path: str) -> dict[str, str]:
    if "?" not in path:
        return {}
    import urllib.parse
    return {k: v[0] for k, v in urllib.parse.parse_qs(path.split("?", 1)[1]).items()}


def _ui_models_payload(gw: Gateway) -> dict[str, Any]:
    """Rich payload the dashboard dropdown uses.

    One entry per (agent, concrete model) pair: the agent's default model plus
    every ``allowed_models`` entry on the agent's provider.
    """
    options: list[dict[str, Any]] = []
    for agent_name, agent in gw.agents.items():
        prov_info = agent.provider.info()
        prov_name = prov_info.get("name", "")
        models: list[str] = []
        allowed = prov_info.get("allowed_models") or []
        if agent.model:
            models.append(agent.model)
        for m in allowed:
            if m not in models:
                models.append(m)
        if not models:
            default = prov_info.get("default_model")
            if default:
                models.append(default)
        if not models:
            models.append(agent_name)
        for m in models:
            options.append({
                "id": f"{agent_name}:{m}",
                "agent": agent_name,
                "model": m,
                "provider": prov_name,
                "label": f"{agent_name} · {m} ({prov_name})",
            })
    return {"options": options,
            "agents": {n: a.info() for n, a in gw.agents.items()}}


def _openai_models_payload(gw: Gateway) -> dict[str, Any]:
    """OpenAI-compatible /v1/models payload: one row per selectable model."""
    data: list[dict[str, Any]] = []
    seen: set[str] = set()
    for opt in _ui_models_payload(gw)["options"]:
        mid = opt["model"]
        if mid in seen:
            continue
        seen.add(mid)
        data.append({"id": mid, "object": "model", "owned_by": opt["provider"],
                     "pythonclaw_agent": opt["agent"]})
    # also expose agent names so OpenAI SDK users can target them directly
    for agent_name in gw.agents:
        if agent_name not in seen:
            data.append({"id": agent_name, "object": "model",
                         "owned_by": "pythonclaw"})
            seen.add(agent_name)
    return {"object": "list", "data": data}


def _resolve_openai_model(gw: Gateway, model: str | None) -> tuple[str | None, str | None]:
    """Map an OpenAI-API ``model`` value to (agent_override, model_override).

    - ``model == <agent_name>``: route to that agent, no model override.
    - ``model`` matches a provider's ``allowed_models``: find an agent backed by
      that provider and pass the model through as an override.
    - otherwise: no override; the router / agent defaults decide.
    """
    if not model:
        return None, None
    if model in gw.agents:
        return model, None
    for agent_name, agent in gw.agents.items():
        allowed = agent.provider.info().get("allowed_models") or []
        if model in allowed or model == agent.model:
            return agent_name, model
    return None, model


def _openai_response(req: dict[str, Any], reply: Message) -> dict[str, Any]:
    agent_id = reply.agent or "pythonclaw"
    usage = (reply.meta or {}).get("usage") or {}
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": req.get("model") or agent_id,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": reply.content},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": int(usage.get("prompt_tokens", 0)),
            "completion_tokens": int(usage.get("completion_tokens", 0)),
            "total_tokens": int(usage.get("total_tokens", 0)),
        },
        "pythonclaw": {"agent": agent_id, "session_id": reply.session_id},
    }
