"""
Jarvis Persistent Memory
Long-term memory storage backed by the Jarvis SQLite database.

Jarvis can permanently remember facts about the user across sessions,
recall them on demand, and forget them when asked. Memories are injected
into the system prompt so the model always "knows" what it has learned.

Also exposes Gemini tool declarations (memory_store / memory_recall /
memory_forget / memory_list) following the same pattern as pc_control.
"""

import json
import logging
import time
from typing import Any, Dict, List

from jarvis.database import Database

logger = logging.getLogger("jarvis.memory")

_MEMORY_TOOLS = {"memory_store", "memory_recall", "memory_forget", "memory_list"}


class MemoryStore:
    """Persistent long-term memory for Jarvis."""

    def __init__(self, db: Database):
        self.db = db
        self._init_table()

    def _init_table(self):
        with self.db._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    category TEXT DEFAULT 'general',
                    importance REAL DEFAULT 1.0,
                    created_at REAL NOT NULL,
                    last_recalled REAL
                );
                CREATE INDEX IF NOT EXISTS idx_memories_created
                    ON memories(created_at);
            """)

    # ── Core operations ──────────────────────────────────────────

    def remember(self, content: str, category: str = "general", importance: float = 1.0) -> str:
        """Store a new memory. Deduplicates exact matches. Returns confirmation text."""
        content = (content or "").strip()
        if not content:
            return "Nothing to remember."
        for mem in self.search(content):
            if mem["content"].lower() == content.lower():
                return f"I already know: \"{content}\""
        with self.db._connect() as conn:
            conn.execute(
                "INSERT INTO memories (content, category, importance, created_at) "
                "VALUES (?, ?, ?, ?)",
                (content, category or "general", importance, time.time()),
            )
        logger.info("Memory stored: %s", content[:80])
        return f"Remembered ({category or 'general'}): \"{content}\""

    def search(self, query: str = "", limit: int = 10) -> List[Dict[str, Any]]:
        """Search memories by keyword. Empty query returns the most recent ones."""
        with self.db._connect() as conn:
            if (query or "").strip():
                rows = conn.execute(
                    "SELECT id, content, category, created_at FROM memories "
                    "WHERE content LIKE ? "
                    "ORDER BY importance DESC, created_at DESC LIMIT ?",
                    (f"%{query.strip()}%", limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, content, category, created_at FROM memories "
                    "ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def forget(self, query: str) -> str:
        """Delete all memories matching a keyword. Returns a summary."""
        matches = self.search(query, limit=100)
        if not matches:
            return f"No memories matching \"{query}\"."
        with self.db._connect() as conn:
            for mem in matches:
                conn.execute("DELETE FROM memories WHERE id = ?", (mem["id"],))
        return f"Forgot {len(matches)} memory(ies) matching \"{query}\"."

    def forget_all(self) -> str:
        with self.db._connect() as conn:
            conn.execute("DELETE FROM memories")
        return "All memories erased."

    def count(self) -> int:
        with self.db._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()
        return int(row["n"]) if row else 0

    def prompt_block(self, limit: int = 30) -> str:
        """Render memories for injection into the system prompt."""
        memories = self.search("", limit=limit)
        if not memories:
            return ""
        return "\n".join(f"- ({m['category']}) {m['content']}" for m in memories)


# ── Gemini tool declarations ────────────────────────────────────

def get_memory_tool_declarations():
    """Gemini FunctionDeclarations for the persistent-memory toolset."""
    from google.genai import types

    return [
        types.FunctionDeclaration(
            name="memory_store",
            description=(
                "Permanently remember a fact, preference, or note about the user "
                "across sessions. Use when the user says 'remember that ...' or "
                "shares durable information worth recalling later."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The fact to remember, phrased clearly and self-contained.",
                    },
                    "category": {
                        "type": "string",
                        "description": "Short tag, e.g. 'preference', 'person', 'work', 'schedule'.",
                    },
                },
                "required": ["content"],
            },
        ),
        types.FunctionDeclaration(
            name="memory_recall",
            description=(
                "Search long-term memory for facts matching a keyword. Use when the "
                "user asks 'what do you know about ...' or a personal question that "
                "stored memories might answer."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keyword(s) to search for. Empty string lists recent memories.",
                    },
                },
            },
        ),
        types.FunctionDeclaration(
            name="memory_forget",
            description="Delete stored memories matching a keyword. Use when the user says 'forget ...'.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keyword matching the memories to delete.",
                    },
                },
                "required": ["query"],
            },
        ),
        types.FunctionDeclaration(
            name="memory_list",
            description="List all stored long-term memories with their IDs.",
            parameters={"type": "object", "properties": {}},
        ),
    ]


def is_memory_tool(tool_name: str) -> bool:
    return tool_name in _MEMORY_TOOLS


def execute_memory_tool(tool_name: str, arguments: Dict[str, Any], memory: MemoryStore) -> str:
    """Execute a memory tool and return a JSON string result."""
    try:
        if tool_name == "memory_store":
            result = memory.remember(
                arguments.get("content", ""),
                arguments.get("category", "general"),
            )
        elif tool_name == "memory_recall":
            matches = memory.search(arguments.get("query", ""))
            if not matches:
                result = "No memories found."
            else:
                result = json.dumps(
                    [{"content": m["content"], "category": m["category"]} for m in matches],
                    indent=2,
                )
        elif tool_name == "memory_forget":
            result = memory.forget(arguments.get("query", ""))
        elif tool_name == "memory_list":
            matches = memory.search("", limit=100)
            if not matches:
                result = "Memory is empty."
            else:
                result = json.dumps(
                    [
                        {"id": m["id"], "content": m["content"], "category": m["category"]}
                        for m in matches
                    ],
                    indent=2,
                )
        else:
            result = f"Unknown memory tool: {tool_name}"
        return json.dumps({"success": True, "result": result})
    except Exception as e:
        logger.error("Memory tool '%s' failed: %s", tool_name, e)
        return json.dumps({"success": False, "error": str(e)})
