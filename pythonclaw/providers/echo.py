"""Offline echo provider.

Useful for demos, tests and environments without network access. It performs a
light transformation of the last user message plus a summary of the context so
you can see memory + routing working end-to-end without a real LLM.
"""
from __future__ import annotations

from .base import CompletionRequest, CompletionResult, Provider


class EchoProvider(Provider):
    def __init__(self, name: str = "echo", **_: object) -> None:
        super().__init__(name=name)

    def complete(self, req: CompletionRequest) -> CompletionResult:
        last_user = next((m for m in reversed(req.messages) if m.role == "user"), None)
        user_text = last_user.content if last_user else ""
        n_prior = sum(1 for m in req.messages if m.role != "system")
        sys_blurb = f" [sys: {req.system[:40]}...]" if req.system else ""
        model = req.model or "echo-1"
        text = (f"(echo{sys_blurb}) You said: {user_text!r}. "
                f"History: {n_prior} msgs.")
        return CompletionResult(text=text, provider=self.name, model=model,
                                usage={"prompt_tokens": 0, "completion_tokens": 0})
