"""OpenAI-compatible provider (works with OpenAI, Azure OpenAI, LM Studio,
llama.cpp server, Ollama's /v1 endpoint, OpenRouter, Together, etc.)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .base import CompletionRequest, CompletionResult, Provider, ProviderError


class OpenAIProvider(Provider):
    def __init__(self, name: str, base_url: str, api_key: str | None,
                 model: str, timeout: float = 60,
                 allowed_models: list[str] | None = None) -> None:
        super().__init__(name=name)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.allowed_models = list(allowed_models) if allowed_models else []

    def info(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.__class__.__name__,
            "base_url": self.base_url,
            "default_model": self.model,
            "allowed_models": self.allowed_models or [self.model],
            "has_key": bool(self.api_key),
        }

    def complete(self, req: CompletionRequest) -> CompletionResult:
        requested = req.model or self.model
        if self.allowed_models and requested not in self.allowed_models:
            raise ProviderError(
                f"{self.name}: model {requested!r} not in allowed_models "
                f"{self.allowed_models!r}"
            )
        body: dict[str, Any] = {
            "model": requested,
            "messages": self.to_chat(req.messages, req.system),
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
        }
        if req.tools:
            body["tools"] = req.tools
        data = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        url = f"{self.base_url}/chat/completions"
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:  # noqa: S310
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise ProviderError(f"{self.name}: HTTP {e.code}: {e.read().decode('utf-8', 'replace')}") from e
        except Exception as e:
            raise ProviderError(f"{self.name}: {e}") from e

        try:
            text = payload["choices"][0]["message"]["content"] or ""
        except Exception as e:
            raise ProviderError(f"{self.name}: malformed response: {payload!r}") from e
        return CompletionResult(
            text=text, provider=self.name,
            model=payload.get("model", self.model),
            usage=payload.get("usage"),
            raw=payload,
        )
