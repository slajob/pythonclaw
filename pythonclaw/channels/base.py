"""Channel base class.

A channel is the adapter between an external chat surface (CLI, web browser,
Discord, ...) and the pythonclaw gateway. It is responsible for:

* running a listener loop on its own thread (``start``)
* calling ``on_inbound(msg)`` for every message it receives
* implementing ``send(reply)`` for responses coming back from the gateway
"""
from __future__ import annotations

import threading
from typing import Any, Callable

from ..session import Message


InboundHandler = Callable[[Message], Message]


class Channel:
    """Base channel. Subclasses override ``run`` (blocking loop) and ``send``."""
    kind: str = "base"

    def __init__(self, name: str, **_: Any) -> None:
        self.name = name
        self._handler: InboundHandler | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # lifecycle --------------------------------------------------------------
    def attach(self, handler: InboundHandler) -> None:
        self._handler = handler

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=f"chan-{self.name}",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout=timeout)

    # overridable ------------------------------------------------------------
    def run(self) -> None:
        """Blocking listener loop. Default: wait for stop."""
        self._stop.wait()

    def send(self, reply: Message) -> None:  # noqa: ARG002
        """Send an outbound reply. Default: no-op."""

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.kind,
                "running": bool(self._thread and self._thread.is_alive())}

    # internals --------------------------------------------------------------
    def _loop(self) -> None:
        try:
            self.run()
        except Exception as e:  # noqa: BLE001
            print(f"[channel:{self.name}] crashed: {e}")

    def _dispatch(self, msg: Message) -> Message:
        if not self._handler:
            raise RuntimeError(f"channel {self.name!r} not attached to a gateway")
        return self._handler(msg)
