"""Anthropic-compatible provider."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .base import CompletionRequest, CompletionResult, Provider, ProviderError


class AnthropicProvider(Provider):
    def __init__(self, name: str, base_url: str, api_key: str | None,
                 model: str, timeout: float = 60, version: str = "2023-06-01") -> None:
        super().__init__(name=name)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.version = version

    def complete(self, req: CompletionRequest) -> CompletionResult:
        msgs: list[dict[str, Any]] = []
        for m in req.messages:
            if m.role in ("user", "assistant"):
                msgs.append({"role": m.role, "content": m.content})
        body: dict[str, Any] = {
            "model": req.model or self.model,
            "messages": msgs,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
        }
        if req.system:
            body["system"] = req.system
        data = json.dumps(body).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": self.version,
        }
        if self.api_key:
            headers["x-api-key"] = self.api_key
        url = f"{self.base_url}/messages"
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:  # noqa: S310
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise ProviderError(f"{self.name}: HTTP {e.code}: {e.read().decode('utf-8', 'replace')}") from e
        except Exception as e:
            raise ProviderError(f"{self.name}: {e}") from e

        try:
            blocks = payload.get("content", [])
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        except Exception as e:
            raise ProviderError(f"{self.name}: malformed response: {payload!r}") from e
        return CompletionResult(
            text=text, provider=self.name,
            model=payload.get("model", self.model),
            usage=payload.get("usage"),
            raw=payload,
        )
