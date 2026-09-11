"""
Jarvis SQLite Database
Stores conversation history, user preferences, and cached API responses.
"""

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from jarvis.config import DB_PATH


class Database:
    """Lightweight SQLite wrapper for Jarvis persistent storage."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,          -- 'user' or 'assistant'
                    content TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    metadata TEXT DEFAULT '{}'   -- JSON blob
                );

                CREATE TABLE IF NOT EXISTS preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,          -- JSON-serialized
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,          -- JSON-serialized
                    expires_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS commands (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    command TEXT NOT NULL,
                    intent TEXT,
                    success INTEGER DEFAULT 1,
                    timestamp REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_conv_timestamp
                    ON conversations(timestamp);
                CREATE INDEX IF NOT EXISTS idx_cmd_timestamp
                    ON commands(timestamp);
            """)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    #  Conversations 

    def add_message(self, role: str, content: str, metadata: dict | None = None):
        """Store a conversation message."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO conversations (role, content, timestamp, metadata) VALUES (?, ?, ?, ?)",
                (role, content, time.time(), json.dumps(metadata or {})),
            )

    def get_recent_messages(self, limit: int = 20) -> list[dict]:
        """Get recent conversation history."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content, timestamp, metadata FROM conversations ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        messages = [dict(r) for r in reversed(rows)]
        for m in messages:
            m["metadata"] = json.loads(m.get("metadata", "{}"))
        return messages

    def get_conversation_context(self, limit: int = 10) -> str:
        """Get formatted conversation context for the AI model."""
        messages = self.get_recent_messages(limit)
        if not messages:
            return ""
        lines = []
        for m in messages:
            prefix = "User" if m["role"] == "user" else "Jarvis"
            lines.append(f"{prefix}: {m['content']}")
        return "\n".join(lines)

    def clear_old_conversations(self, days: int = 30):
        """Delete conversations older than N days."""
        cutoff = time.time() - (days * 86400)
        with self._connect() as conn:
            conn.execute("DELETE FROM conversations WHERE timestamp < ?", (cutoff,))

    #  Preferences 

    def set_preference(self, key: str, value: Any):
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO preferences (key, value, updated_at) VALUES (?, ?, ?)",
                (key, json.dumps(value), time.time()),
            )

    def get_preference(self, key: str, default: Any = None) -> Any:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM preferences WHERE key = ?", (key,)
            ).fetchone()
        if row:
            return json.loads(row["value"])
        return default

    #  Cache 

    def cache_set(self, key: str, value: Any, ttl_seconds: int = 300):
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
                (key, json.dumps(value), time.time() + ttl_seconds),
            )

    def cache_get(self, key: str) -> Any | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
            ).fetchone()
        if row and row["expires_at"] > time.time():
            return json.loads(row["value"])
        return None

    def cache_clear(self):
        with self._connect() as conn:
            conn.execute("DELETE FROM cache WHERE expires_at < ?", (time.time(),))

    #  Commands 

    def log_command(self, command: str, intent: str | None = None, success: bool = True):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO commands (command, intent, success, timestamp) VALUES (?, ?, ?, ?)",
                (command, intent, int(success), time.time()),
            )

    def get_command_history(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT command, intent, success, timestamp FROM commands ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]
