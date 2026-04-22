"""Built-in tools exposed to agents.

Each tool is a callable with a JSON-schema-ish spec describing its name,
description and parameters. Agents can invoke tools via the ``@tool name arg``
shortcut or through provider-native tool-use once wired in.
"""
from __future__ import annotations

import ast
import operator
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
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


REGISTRY: dict[str, Tool] = {
    "time": time_tool,
    "calc": calc_tool,
    "web_search": web_search_tool,
}


def get(name: str) -> Tool | None:
    return REGISTRY.get(name)


def list_specs(names: list[str] | None = None) -> list[dict[str, Any]]:
    names = names or list(REGISTRY.keys())
    return [REGISTRY[n].spec() for n in names if n in REGISTRY]
