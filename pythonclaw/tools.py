"""Built-in tools exposed to agents.

Each tool is a callable with a JSON-schema-ish spec describing its name,
description and parameters. Agents can invoke tools via the ``@tool name arg``
shortcut or through provider-native tool-use once wired in.
"""
from __future__ import annotations

import ast
import operator
import os
import shlex
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


ToolFn = Callable[[dict[str, Any]], str]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]   # JSON schema (object)
    run: ToolFn

    def spec(self) -> dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": self.parameters,
        }}


# --- time ------------------------------------------------------------------

def _time_tool(_: dict[str, Any]) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S %z", time.localtime())


time_tool = Tool(
    name="time",
    description="Return the current local time as an ISO-like string.",
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
    run=_time_tool,
)


# --- calc (safe arithmetic) ------------------------------------------------

_ALLOWED_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_ALLOWED_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _eval_ast(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_ast(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        return _ALLOWED_BINOPS[type(node.op)](_eval_ast(node.left), _eval_ast(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
        return _ALLOWED_UNARY[type(node.op)](_eval_ast(node.operand))
    raise ValueError("unsupported expression")


def _calc_tool(args: dict[str, Any]) -> str:
    expr = str(args.get("expr", "")).strip()
    if not expr:
        return "error: missing 'expr'"
    try:
        tree = ast.parse(expr, mode="eval")
        return str(_eval_ast(tree))
    except Exception as e:
        return f"error: {e}"


calc_tool = Tool(
    name="calc",
    description="Evaluate a basic arithmetic expression (+ - * / // % **).",
    parameters={
        "type": "object",
        "properties": {"expr": {"type": "string", "description": "Arithmetic expression"}},
        "required": ["expr"],
        "additionalProperties": False,
    },
    run=_calc_tool,
)


# --- web_search (DuckDuckGo Instant Answer API, best-effort) --------------

def _web_search_tool(args: dict[str, Any]) -> str:
    q = str(args.get("query", "")).strip()
    if not q:
        return "error: missing 'query'"
    url = "https://api.duckduckgo.com/?" + urllib.parse.urlencode(
        {"q": q, "format": "json", "no_redirect": 1, "no_html": 1}
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "pythonclaw/0.1"})
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
            import json as _json
            data = _json.loads(resp.read().decode("utf-8", errors="replace"))
        heading = data.get("Heading") or ""
        abstract = data.get("AbstractText") or ""
        related = "; ".join(
            r.get("Text", "") for r in (data.get("RelatedTopics") or [])[:3]
            if isinstance(r, dict)
        )
        parts = [p for p in (heading, abstract, related) if p]
        return " | ".join(parts) or "(no results)"
    except Exception as e:
        return f"error: {e}"


web_search_tool = Tool(
    name="web_search",
    description="Search the web via DuckDuckGo's Instant Answer API.",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    },
    run=_web_search_tool,
)


# --- host access: shell / ls / read_file -----------------------------------
#
# Everything below is DISABLED by default. It is enabled by the ``tools``
# section of the gateway config via :func:`configure` (called from Gateway).
# The tools are allowlist-based; an empty allowlist means "deny". A single
# ``"*"`` entry means "allow all" — use with care.

@dataclass
class _ShellState:
    enabled: bool = False
    allowed_cmds: list[str] = field(default_factory=list)  # basenames or "*"
    denied_cmds: list[str] = field(default_factory=lambda: [
        "rm", "rmdir", "mkfs", "mkfs.ext4", "dd", "shutdown", "reboot",
        "poweroff", "halt", "passwd", "sudo", "su", "chmod", "chown",
    ])
    cwd: str | None = None
    timeout: float = 10.0
    max_output_bytes: int = 16384


@dataclass
class _FsState:
    enabled: bool = False
    allowed_paths: list[str] = field(default_factory=list)
    max_bytes: int = 65536


_shell_state = _ShellState()
_ls_state = _FsState()
_read_state = _FsState()


def _path_allowed(path: str, roots: list[str]) -> Path | None:
    """Resolve ``path`` and return it iff it lives under one of ``roots``.

    ``roots`` are resolved once and compared by path components (no string
    prefix tricks like /home/alice-bad vs /home/alice).
    """
    try:
        target = Path(path).expanduser().resolve()
    except OSError:
        return None
    for r in roots:
        try:
            root = Path(r).expanduser().resolve()
        except OSError:
            continue
        try:
            target.relative_to(root)
            return target
        except ValueError:
            continue
    return None


def _shell_tool(args: dict[str, Any]) -> str:
    st = _shell_state
    if not st.enabled:
        return "error: shell tool disabled (set tools.shell.enabled=true)"
    cmd = args.get("cmd") or args.get("command")
    if not cmd:
        return "error: missing 'cmd'"
    try:
        parts = shlex.split(cmd) if isinstance(cmd, str) else [str(x) for x in cmd]
    except ValueError as e:
        return f"error: bad command: {e}"
    if not parts:
        return "error: empty command"
    head = os.path.basename(parts[0])
    if head in st.denied_cmds:
        return f"error: command {head!r} is denied"
    if st.allowed_cmds != ["*"] and head not in st.allowed_cmds:
        return f"error: command {head!r} not in allowed_cmds"
    try:
        result = subprocess.run(  # noqa: S603
            parts, cwd=st.cwd, capture_output=True, timeout=st.timeout,
            text=True, errors="replace", check=False)
    except subprocess.TimeoutExpired:
        return f"error: timeout after {st.timeout}s"
    except FileNotFoundError:
        return f"error: command not found: {parts[0]}"
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    out = result.stdout or ""
    if result.stderr:
        out = (out + ("\n" if out else "") + "[stderr]\n" + result.stderr).rstrip()
    if len(out) > st.max_output_bytes:
        out = out[:st.max_output_bytes] + f"\n[truncated; total {len(out)} bytes]"
    if result.returncode != 0:
        out = f"{out}\n[exit={result.returncode}]".strip()
    return out or f"(exit={result.returncode})"


shell_tool = Tool(
    name="shell",
    description=("Run a shell command on the host. Disabled unless enabled in "
                 "config; allowlisted via tools.shell.allowed_cmds."),
    parameters={
        "type": "object",
        "properties": {
            "cmd": {"type": "string", "description": "The command to run."},
        },
        "required": ["cmd"],
        "additionalProperties": False,
    },
    run=_shell_tool,
)


def _ls_tool(args: dict[str, Any]) -> str:
    st = _ls_state
    if not st.enabled:
        return "error: ls tool disabled (set tools.ls.enabled=true)"
    path = str(args.get("path", "")).strip()
    if not path:
        return "error: missing 'path'"
    target = _path_allowed(path, st.allowed_paths)
    if target is None:
        return f"error: path not in allowed_paths: {st.allowed_paths}"
    try:
        entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name))
    except NotADirectoryError:
        return f"error: not a directory: {target}"
    except PermissionError:
        return f"error: permission denied: {target}"
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    lines: list[str] = [f"# {target}"]
    for p in entries[:500]:
        try:
            st_ = p.stat()
            size = st_.st_size if p.is_file() else "-"
        except OSError:
            size = "?"
        kind = "d" if p.is_dir() else ("l" if p.is_symlink() else "f")
        lines.append(f"{kind}\t{size}\t{p.name}")
    if len(entries) > 500:
        lines.append(f"# ... {len(entries) - 500} more entries truncated")
    return "\n".join(lines)


ls_tool = Tool(
    name="ls",
    description=("List a directory on the host (read-only). Restricted to "
                 "tools.ls.allowed_paths."),
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    },
    run=_ls_tool,
)


def _read_file_tool(args: dict[str, Any]) -> str:
    st = _read_state
    if not st.enabled:
        return "error: read_file tool disabled (set tools.read_file.enabled=true)"
    path = str(args.get("path", "")).strip()
    if not path:
        return "error: missing 'path'"
    target = _path_allowed(path, st.allowed_paths)
    if target is None:
        return f"error: path not in allowed_paths: {st.allowed_paths}"
    if not target.is_file():
        return f"error: not a regular file: {target}"
    try:
        with target.open("rb") as f:
            data = f.read(st.max_bytes + 1)
    except PermissionError:
        return f"error: permission denied: {target}"
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    text = data[:st.max_bytes].decode("utf-8", errors="replace")
    if len(data) > st.max_bytes:
        text += f"\n[truncated at {st.max_bytes} bytes]"
    return text


read_file_tool = Tool(
    name="read_file",
    description=("Read a file from the host (read-only). Restricted to "
                 "tools.read_file.allowed_paths."),
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    },
    run=_read_file_tool,
)


REGISTRY: dict[str, Tool] = {
    "time": time_tool,
    "calc": calc_tool,
    "web_search": web_search_tool,
    "shell": shell_tool,
    "ls": ls_tool,
    "read_file": read_file_tool,
}


def configure(cfg: dict[str, Any] | None) -> None:
    """Apply per-tool configuration from the ``tools`` section of the gateway
    config. Idempotent — can be called again to re-apply new config."""
    cfg = cfg or {}
    sh = cfg.get("shell") or {}
    _shell_state.enabled = bool(sh.get("enabled", False))
    _shell_state.allowed_cmds = list(sh.get("allowed_cmds") or [])
    if "denied_cmds" in sh:
        _shell_state.denied_cmds = list(sh["denied_cmds"])
    _shell_state.cwd = sh.get("cwd")
    _shell_state.timeout = float(sh.get("timeout", 10.0))
    _shell_state.max_output_bytes = int(sh.get("max_output_bytes", 16384))

    for name, state in (("ls", _ls_state), ("read_file", _read_state)):
        entry = cfg.get(name) or {}
        state.enabled = bool(entry.get("enabled", False))
        state.allowed_paths = list(entry.get("allowed_paths") or [])
        state.max_bytes = int(entry.get("max_bytes", 65536))


def get(name: str) -> Tool | None:
    return REGISTRY.get(name)


def list_specs(names: list[str] | None = None) -> list[dict[str, Any]]:
    names = names or list(REGISTRY.keys())
    return [REGISTRY[n].spec() for n in names if n in REGISTRY]
