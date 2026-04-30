"""Conversation memory stored in PostgreSQL (Neon).

Keeps the last N turns per conversation so the AI can
use recent context for follow‑up questions.
"""

from __future__ import annotations

import json
import threading
from typing import Any, List, Dict

from sqlalchemy import text

from db.connection import get_engine


_TABLE_CREATED = False
_TABLE_LOCK = threading.Lock()


def _ensure_table() -> None:
    """Create the chat_history table if it doesn't exist, and add query_result column if missing."""
    global _TABLE_CREATED
    with _TABLE_LOCK:
        if _TABLE_CREATED:
            return

        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS chat_history (
                    id BIGSERIAL PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    sql_query TEXT,
                    query_result TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            ))
            # Migrate existing tables that don't have the query_result column yet
            conn.execute(text(
                """
                ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS query_result TEXT;
                """
            ))

        _TABLE_CREATED = True


def add_turn(
    conversation_id: str,
    question: str,
    answer: str,
    sql_query: str | None,
    query_result: list | None = None,
) -> None:
    """Append a single Q/A turn to the history."""
    _ensure_table()
    engine = get_engine()
    result_json = json.dumps(query_result, default=str) if query_result else None
    insert_stmt = text(
        """
        INSERT INTO chat_history (conversation_id, question, answer, sql_query, query_result)
        VALUES (:conversation_id, :question, :answer, :sql_query, :query_result)
        """
    )
    with engine.begin() as conn:
        conn.execute(
            insert_stmt,
            {
                "conversation_id": conversation_id,
                "question": question,
                "answer": answer,
                "sql_query": sql_query,
                "query_result": result_json,
            },
        )


def delete_turn(turn_id: int) -> None:
    """Delete a single chat history turn by its id."""
    _ensure_table()
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM chat_history WHERE id = :id"),
            {"id": turn_id},
        )


def get_full_history(conversation_id: str) -> List[Dict[str, Any]]:
    """Return ALL turns for a conversation (oldest first) for the sidebar display."""
    _ensure_table()
    engine = get_engine()
    query = text(
        """
        SELECT id, question, answer, sql_query, query_result, created_at
        FROM chat_history
        WHERE conversation_id = :conversation_id
        ORDER BY created_at ASC
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            query, {"conversation_id": conversation_id}
        ).mappings().all()

    result = []
    for r in rows:
        row = dict(r)
        # Deserialize query_result JSON string back to a list
        if row.get("query_result"):
            try:
                row["query_result"] = json.loads(row["query_result"])
            except (json.JSONDecodeError, TypeError):
                row["query_result"] = None
        result.append(row)
    return result


def get_turn_by_id(turn_id: int) -> Dict[str, Any] | None:
    """Return a single history turn by its primary key id."""
    _ensure_table()
    engine = get_engine()
    query = text(
        """
        SELECT id, question, answer, sql_query, query_result, created_at
        FROM chat_history
        WHERE id = :id
        """
    )
    with engine.connect() as conn:
        row = conn.execute(query, {"id": turn_id}).mappings().first()
    if row is None:
        return None
    result = dict(row)
    if result.get("query_result"):
        try:
            result["query_result"] = json.loads(result["query_result"])
        except (json.JSONDecodeError, TypeError):
            result["query_result"] = None
    return result


def get_recent_history(conversation_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Return the most recent `limit` turns for a conversation (oldest first)."""
    _ensure_table()
    engine = get_engine()
    query = text(
        """
        SELECT question, answer, sql_query, created_at
        FROM chat_history
        WHERE conversation_id = :conversation_id
        ORDER BY created_at DESC
        LIMIT :limit
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            query, {"conversation_id": conversation_id, "limit": limit}
        ).mappings().all()

    # Reverse so caller sees oldest → newest
    return list(reversed([dict(r) for r in rows]))

