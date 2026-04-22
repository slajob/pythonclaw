"""Command-line interface.

Subcommands (modelled loosely on the OpenClaw CLI):

    pythonclaw run      [--config PATH]     # start gateway + dashboard + channels
    pythonclaw chat     [--config PATH] [--agent NAME]  # CLI REPL
    pythonclaw send     --text "hi"         # one-shot message through gateway
    pythonclaw info     [--config PATH]     # print agents/providers/channels
    pythonclaw init     [--path PATH]       # write a starter config
    pythonclaw version
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import signal
import sys
import time
from pathlib import Path

from . import __version__
from .config import Config
from .gateway import Gateway
from .session import Message, Session
from .web.dashboard import Dashboard


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pythonclaw", description="A Python clone of OpenClaw.")
    p.add_argument("--log-level", default="INFO")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="start gateway, dashboard and all enabled channels")
    r.add_argument("--config", default="configs/example.json")

    c = sub.add_parser("chat", help="start an interactive CLI chat REPL")
    c.add_argument("--config", default="configs/example.json")
    c.add_argument("--agent", default=None, help="force a specific agent")

    s = sub.add_parser("send", help="one-shot send: print the reply and exit")
    s.add_argument("--config", default="configs/example.json")
    s.add_argument("--text", required=True)
    s.add_argument("--agent", default=None)

    i = sub.add_parser("info", help="print loaded config summary")
    i.add_argument("--config", default="configs/example.json")

    n = sub.add_parser("init", help="write a starter config")
    n.add_argument("--path", default="./pythonclaw.config.json")

    sub.add_parser("version", help="print version and exit")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s")
    try:
        return _dispatch(args)
    except KeyboardInterrupt:
        print("\n[pythonclaw] bye")
        return 0


def _dispatch(args: argparse.Namespace) -> int:
    if args.cmd == "version":
        print(f"pythonclaw {__version__}")
        return 0
    if args.cmd == "init":
        return _cmd_init(args.path)
    cfg = _load_config(args.config)
    if args.cmd == "info":
        return _cmd_info(cfg)
    if args.cmd == "run":
        return _cmd_run(cfg)
    if args.cmd == "chat":
        return _cmd_chat(cfg, agent=args.agent)
    if args.cmd == "send":
        return _cmd_send(cfg, text=args.text, agent=args.agent)
    return 2


def _load_config(path: str) -> Config:
    p = Path(path)
    if not p.exists():
        print(f"[pythonclaw] config not found: {p} — falling back to defaults")
        return Config.default()
    return Config.load(p)


# ---- commands ---------------------------------------------------------------

def _cmd_init(path: str) -> int:
    dst = Path(path)
    if dst.exists():
        print(f"[pythonclaw] refusing to overwrite {dst}")
        return 1
    example = Path(__file__).resolve().parent.parent / "configs" / "example.json"
    if example.exists():
        shutil.copyfile(example, dst)
    else:
        dst.write_text(json.dumps(Config.default().raw, indent=2), encoding="utf-8")
    print(f"[pythonclaw] wrote {dst}")
    return 0


def _cmd_info(cfg: Config) -> int:
    gw = Gateway(cfg)
    print(json.dumps(gw.info(), indent=2, default=str))
    return 0


def _cmd_run(cfg: Config) -> int:
    gw = Gateway(cfg)
    dash = Dashboard(gw)
    gw.start()
    dash.start()
    host, port = dash.host, dash.port
    print(f"[pythonclaw] gateway up. dashboard → http://{host}:{port}/")
    print("[pythonclaw] press Ctrl-C to stop.")
    stop = {"stop": False}

    def _handler(signum, _frame):  # noqa: ANN001
        print(f"\n[pythonclaw] caught signal {signum}; shutting down…")
        stop["stop"] = True
    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)
    try:
        while not stop["stop"]:
            time.sleep(0.5)
    finally:
        dash.stop()
        gw.stop()
    return 0


def _cmd_chat(cfg: Config, agent: str | None = None) -> int:
    gw = Gateway(cfg)
    print(f"[pythonclaw] chat. agents: {', '.join(gw.agents)}. default: {gw.router.default_agent}.")
    session = Session.new(channel="cli", user="local")
    print(f"[pythonclaw] session={session.id} (type /quit to exit, /new for a fresh session)")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); break
        if not text:
            continue
        if text in ("/quit", "/exit"):
            break
        if text == "/new":
            session = Session.new(channel="cli", user="local")
            print(f"[new session={session.id}]"); continue
        msg = Message(role="user", content=text, channel="cli",
                      session_id=session.id, user="local")
        if agent:
            # bypass router: address agent explicitly
            a = gw.agents.get(agent)
            if a is None:
                print(f"[unknown agent: {agent}]"); continue
            reply = a.handle(msg, gw.memory)
        else:
            reply = gw.handle(msg)
        print(f"{reply.agent or 'bot'}> {reply.content}")
    return 0


def _cmd_send(cfg: Config, text: str, agent: str | None = None) -> int:
    gw = Gateway(cfg)
    session = Session.new(channel="cli", user="local")
    msg = Message(role="user", content=text, channel="cli",
                  session_id=session.id, user="local")
    if agent:
        a = gw.agents.get(agent)
        if a is None:
            print(f"[unknown agent: {agent}]", file=sys.stderr); return 1
        reply = a.handle(msg, gw.memory)
    else:
        reply = gw.handle(msg)
    print(reply.content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
