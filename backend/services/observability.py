"""
services/observability.py
=========================
LangSmith tracing setup for monitoring and debugging LLM calls.

WHAT IS LANGSMITH?
  LangSmith is Anthropic/LangChain's observability platform for LLM applications.
  When enabled, every LLM call, agent step, and tool invocation is automatically
  logged as a "trace" you can inspect at smith.langchain.com.

  This is invaluable for debugging because you can see:
  - Exactly what prompt was sent to the LLM
  - What the LLM responded with at each step
  - Which tools were called and what they returned
  - Latency and token counts for each step
  - Which session/user/turn a trace belongs to

HOW TRACES ARE ORGANISED:
  Each graph invocation creates one trace (a "run" in LangSmith terms).
  Traces are tagged and labelled so you can filter by:
  - feature: "chat" or "planner"
  - session_id: all traces from one conversation
  - turn_id: which message in the conversation
  - agent: which specialist handled the request

ENV VARIABLES (optional — app works without them, just no tracing):
  LANGCHAIN_API_KEY=ls__...   (or LANGSMITH_API_KEY)
  LANGCHAIN_PROJECT=finance-advisor
  LANGCHAIN_TRACING_V2=true   (auto-set by setup_langsmith)

USAGE:
  Call setup_langsmith() once at app startup.
  Then use build_trace_config() to get the config dict passed to app.stream().
"""

import os

from dotenv import load_dotenv

load_dotenv()


def setup_langsmith() -> bool:
    """
    Enable LangSmith tracing by setting the required environment variables.

    This function is idempotent — safe to call multiple times.
    Call it once in main.py before building the graph.

    Supports both LANGCHAIN_* and LANGSMITH_* env var naming conventions
    (LangChain changed the naming; this handles both for backwards compatibility).

    Returns:
        True if LangSmith was successfully configured.
        False if no API key was found (tracing disabled silently).
    """
    api_key = (
        os.getenv("LANGCHAIN_API_KEY") or os.getenv("LANGSMITH_API_KEY") or ""
    ).strip()

    if not api_key:
        return False  # No key → tracing disabled, app still works normally

    project = (
        os.getenv("LANGCHAIN_PROJECT")
        or os.getenv("LANGSMITH_PROJECT")
        or "finance-advisor"
    ).strip()

    # Set all required LangSmith env vars
    os.environ["LANGCHAIN_API_KEY"]      = api_key
    os.environ["LANGCHAIN_PROJECT"]      = project
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com")

    return True


def build_trace_config(
    *,
    thread_id:  str,
    user_id:    str,
    session_id: str,
    turn_id:    int | None = None,
    request_id: str | None = None,
    is_planner: bool = False,
    agent_hint: str = "",
) -> dict:
    """
    Build the LangGraph run config with full trace metadata.

    This dict is passed as the `config` argument to app.stream() or app.invoke().
    LangGraph reads:
    - configurable.thread_id: which checkpointer thread to load/save state to
    - run_name: label shown in LangSmith trace list
    - metadata: searchable key-value pairs in LangSmith
    - tags: filterable labels in LangSmith

    The thread_id is critical: it's the key used by the checkpointer to
    persist state across turns. Same thread_id = same conversation memory.

    Args:
        thread_id:   Unique identifier for this conversation thread.
                     Format: "{user_id}:{session_id}" for chat,
                             "planner:{user_id}:{session_id}" for planner.
        user_id:     Supabase user UUID (used in metadata, not stored raw).
        session_id:  Chat session UUID.
        turn_id:     Which turn in the conversation (for trace filtering).
        request_id:  Short random ID for this specific HTTP request (for log correlation).
        is_planner:  True if this is a planner graph invocation.
        agent_hint:  Expected agent name (for trace labelling before routing).

    Returns:
        Config dict to pass to app.stream() or app.invoke().
    """
    feature = "planner" if is_planner else "chat"

    # Truncate user_id for privacy — we don't need the full UUID in traces
    safe_user_ref = f"u_{user_id[:8]}" if user_id else "u_unknown"

    metadata: dict = {
        "feature":     feature,
        "user_ref":    safe_user_ref,   # Partial user ID for privacy
        "session_id":  session_id,
        "thread_id":   thread_id,
        "agent_hint":  agent_hint or "unknown",
        "app_version": os.getenv("APP_VERSION", "1.0.0"),
    }

    tags: list[str] = [
        "finance-advisor",
        f"feature:{feature}",
        f"session:{session_id}",
        f"agent:{agent_hint or 'unknown'}",
    ]

    if turn_id is not None:
        metadata["turn_id"] = turn_id
        tags.append(f"turn:{turn_id}")

    if request_id:
        metadata["request_id"] = request_id
        tags.append(f"request:{request_id}")

    run_suffix = str(turn_id) if turn_id is not None else "na"

    return {
        "configurable": {"thread_id": thread_id},     # Used by LangGraph checkpointer
        "run_name":     f"{feature}:{session_id}:turn:{run_suffix}",  # LangSmith label
        "metadata":     metadata,
        "tags":         tags,
    }