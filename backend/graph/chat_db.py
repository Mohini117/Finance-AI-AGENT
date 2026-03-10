"""
chat_db.py
==========
SQLite-based chat session persistence layer.

WHAT IT STORES:
  ┌────────────────┬─────────────────────────────────────────────────────┐
  │ Table          │ Contents                                            │
  ├────────────────┼─────────────────────────────────────────────────────┤
  │ sessions       │ session_id, title (auto-generated), timestamps      │
  │ messages       │ Full chat history per session (role + content)      │
  │ session_data   │ Uploaded transactions + user goal per session       │
  └────────────────┴─────────────────────────────────────────────────────┘

WHY THIS EXISTS ALONGSIDE LANGGRAPH'S MEMORY?
  LangGraph's checkpointer (finance_memory.db) stores the AGENT STATE —
  structured data like planner_income, budget_summary, etc.

  This database (finance_chats.db) stores the USER-FACING chat history —
  what the user and the AI said, in a format suitable for the frontend
  sidebar (session list, conversation display, resume past chats).

  They serve different purposes and are kept separate intentionally.

DATABASE FILE: finance_chats.db (SQLite, created automatically on first run)
"""

import json
import sqlite3
import uuid
from datetime import datetime

DB_PATH = "finance_chats.db"


def init_db() -> None:
    """
    Create all required tables if they don't exist.

    Call this once at application startup (e.g., in main.py or app.py).
    Safe to call multiple times — uses CREATE TABLE IF NOT EXISTS.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # ── sessions: One row per chat session ────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            title      TEXT,        -- Auto-generated from first user message
            created_at TEXT,        -- ISO timestamp
            updated_at TEXT         -- Updated on every new message
        )
    """)

    # ── messages: Full conversation history ───────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            role       TEXT,        -- "user" or "assistant"
            content    TEXT,
            agent_name TEXT,        -- Which agent produced this message (e.g., "budget_analyst")
            timestamp  TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
    """)

    # ── session_data: Per-session context ─────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS session_data (
            session_id   TEXT PRIMARY KEY,
            transactions TEXT,      -- JSON array of transaction dicts
            user_goal    TEXT,      -- Plain-text goal string
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
    """)

    conn.commit()
    conn.close()


def create_session(title: str = "New Chat") -> str:
    """
    Create a new chat session and return its UUID.

    The title is initially "New Chat" and updated once the user sends
    their first message (see update_session_title).

    Returns:
        session_id: A UUID4 string used as the primary key.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    session_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    cursor.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?)",
        (session_id, title, now, now)
    )
    conn.commit()
    conn.close()
    return session_id


def get_all_sessions() -> list[dict]:
    """
    Return all sessions ordered by most recently updated.

    Used to populate the frontend sidebar session list.

    Returns:
        List of dicts: [{"session_id": ..., "title": ..., "updated_at": ...}]
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT session_id, title, updated_at FROM sessions ORDER BY updated_at DESC"
    )
    rows = cursor.fetchall()
    conn.close()
    return [{"session_id": r[0], "title": r[1], "updated_at": r[2]} for r in rows]


def save_message(session_id: str, role: str, content: str, agent_name: str = "") -> None:
    """
    Persist a single message to the database.

    Args:
        session_id: The session this message belongs to.
        role:       "user" or "assistant".
        content:    The message text.
        agent_name: Which specialist agent produced this (empty for user messages).
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    cursor.execute(
        "INSERT INTO messages (session_id, role, content, agent_name, timestamp) VALUES (?, ?, ?, ?, ?)",
        (session_id, role, content, agent_name, now)
    )
    # Keep updated_at current so the sidebar shows the most recent session first
    cursor.execute(
        "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
        (now, session_id)
    )
    conn.commit()
    conn.close()


def get_messages(session_id: str) -> list[dict]:
    """
    Load the full conversation history for a session.

    Returns:
        List of dicts: [{"role": "user"|"assistant", "content": ..., "agent": ...}]
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role, content, agent_name FROM messages WHERE session_id = ? ORDER BY id ASC",
        (session_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1], "agent": r[2]} for r in rows]


def update_session_title(session_id: str, first_message: str) -> None:
    """
    Auto-generate a session title from the user's first message.

    Truncates to 40 characters so sidebar titles stay readable.
    Called once, when the first user message is sent.
    """
    title = first_message[:40] + "..." if len(first_message) > 40 else first_message
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE sessions SET title = ? WHERE session_id = ?",
        (title, session_id)
    )
    conn.commit()
    conn.close()


def save_session_data(session_id: str, transactions: list, user_goal: str) -> None:
    """
    Save (or update) the uploaded transactions and goal for a session.

    Uses INSERT OR REPLACE so this is safe to call multiple times.
    Transactions are stored as a JSON string.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO session_data (session_id, transactions, user_goal)
        VALUES (?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            transactions = excluded.transactions,
            user_goal    = excluded.user_goal
    """, (session_id, json.dumps(transactions), user_goal))
    conn.commit()
    conn.close()


def load_session_data(session_id: str) -> tuple[list, str]:
    """
    Load transactions and goal for a session.

    Returns:
        (transactions_list, user_goal_string)
        Returns ([], "") if no data has been saved yet.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT transactions, user_goal FROM session_data WHERE session_id = ?",
        (session_id,)
    )
    row = cursor.fetchone()
    conn.close()
    if row:
        return json.loads(row[0]), row[1]
    return [], ""


def delete_session(session_id: str) -> None:
    """
    Delete a session and ALL associated data (messages + session_data).

    Called when the user clicks "Delete" in the frontend sidebar.
    Uses cascading deletes in the correct order to respect foreign keys.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM messages WHERE session_id = ?",      (session_id,))
    cursor.execute("DELETE FROM session_data WHERE session_id = ?",  (session_id,))
    cursor.execute("DELETE FROM sessions WHERE session_id = ?",      (session_id,))
    conn.commit()
    conn.close()