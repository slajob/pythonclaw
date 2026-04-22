"""SQLite-backed memory/session store.

Mirrors the OpenClaw `memory` concept: every inbound/outbound message is
persisted, grouped by `session_id`, with a configurable cap so the transcript
fed back into a provider stays bounded.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .session import Message, Session


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id       TEXT PRIMARY KEY,
    channel  TEXT NOT NULL,
    user     TEXT,
    agent    TEXT,
    created  REAL NOT NULL,
    meta     TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS messages (
    id         TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    ts         REAL NOT NULL,
    channel    TEXT,
    user       TEXT,
    agent      TEXT,
    meta       TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS messages_session_ts ON messages(session_id, ts);
"""


class SqliteMemory:
    def __init__(self, path: str | Path, max_messages: int = 200) -> None:
        self.path = Path(path)
        self.max_messages = max_messages
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # sessions ---------------------------------------------------------------
    def put_session(self, s: Session) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO sessions(id, channel, user, agent, created, meta) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (s.id, s.channel, s.user, s.agent, s.created, json.dumps(s.meta)),
            )

    def get_session(self, session_id: str) -> Session | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, channel, user, agent, created, meta FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        if not row:
            return None
        return Session(id=row[0], channel=row[1], user=row[2], agent=row[3],
                       created=row[4], meta=json.loads(row[5] or "{}"))

    def list_sessions(self, limit: int = 100) -> list[Session]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, channel, user, agent, created, meta FROM sessions "
                "ORDER BY created DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [Session(id=r[0], channel=r[1], user=r[2], agent=r[3],
                        created=r[4], meta=json.loads(r[5] or "{}")) for r in rows]

    # messages ---------------------------------------------------------------
    def append(self, msg: Message) -> None:
        if not msg.session_id:
            raise ValueError("Message.session_id is required to persist")
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO messages"
                "(id, session_id, role, content, ts, channel, user, agent, meta) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (msg.id, msg.session_id, msg.role, msg.content, msg.ts,
                 msg.channel, msg.user, msg.agent, json.dumps(msg.meta)),
            )
            self._prune(msg.session_id)

    def history(self, session_id: str, limit: int | None = None) -> list[Message]:
        n = limit or self.max_messages
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, session_id, role, content, ts, channel, user, agent, meta "
                "FROM messages WHERE session_id = ? ORDER BY ts ASC LIMIT ?",
                (session_id, n),
            ).fetchall()
        return [Message(id=r[0], session_id=r[1], role=r[2], content=r[3], ts=r[4],
                        channel=r[5], user=r[6], agent=r[7],
                        meta=json.loads(r[8] or "{}")) for r in rows]

    def _prune(self, session_id: str) -> None:
        # keep only the last N messages per session
        self._conn.execute(
            "DELETE FROM messages WHERE session_id = ? AND id NOT IN ("
            "  SELECT id FROM messages WHERE session_id = ? ORDER BY ts DESC LIMIT ?"
            ")",
            (session_id, session_id, self.max_messages),
        )

    def stats(self) -> dict[str, Any]:
        with self._lock:
            s = self._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            m = self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        return {"sessions": s, "messages": m, "path": str(self.path)}
