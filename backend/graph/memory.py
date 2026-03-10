"""
graph/memory.py
===============
Provides the LangGraph checkpointer — the persistence layer for multi-turn memory.

WHAT IS A CHECKPOINTER?
  In LangGraph, a checkpointer saves the full state after every node runs.
  This enables:
  - Multi-turn conversations: the spending planner remembers income from Turn 1
    when the user provides expenses in Turn 2.
  - Session resume: users can close the browser and continue later.
  - Debugging: you can inspect state at any point in the conversation.

HOW THREAD IDs WORK:
  Each conversation session gets a unique `thread_id` (the session UUID).
  The checkpointer uses this as a key — so two users' sessions never mix.

  Usage in graph invocation:
      app.invoke(state, config={"configurable": {"thread_id": session_id}})

STORAGE STRATEGY:
  1. Try SQLite (file-based, survives server restarts)
  2. Fall back to InMemory (fast, but lost on restart — fine for dev/testing)
"""

import sqlite3


def get_checkpointer():
    """
    Return a LangGraph checkpointer for state persistence.

    Tries SQLite first (recommended for production — data survives restarts).
    Falls back to InMemory if SQLite import fails (fine for local dev/testing).

    Returns:
        A compiled checkpointer object passed to graph.compile(checkpointer=...)
    """
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        conn = sqlite3.connect("finance_memory.db", check_same_thread=False)
        checkpointer = SqliteSaver(conn)
        print("[Memory] ✅ Using SQLite checkpointer (finance_memory.db)")
        return checkpointer

    except Exception as sqlite_err:
        print(f"[Memory] SQLite failed: {sqlite_err}")

        try:
            from langgraph.checkpoint.memory import MemorySaver
            print("[Memory] ✅ Using InMemory checkpointer (data lost on restart)")
            return MemorySaver()

        except Exception as memory_err:
            print(f"[Memory] InMemory also failed: {memory_err}")
            print("[Memory] ⚠️  No checkpointer — multi-turn memory disabled!")
            return None