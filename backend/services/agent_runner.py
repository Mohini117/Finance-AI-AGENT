"""
services/agent_runner.py
========================
The bridge between FastAPI routes and the LangGraph agent system.

RESPONSIBILITIES:
  1. Build the LangGraph app (once, as a singleton — not rebuilt on every request).
  2. Anonymize transactions before passing to agents.
  3. Stream agent responses token-by-token as Server-Sent Events (SSE).
  4. Parse node outputs and emit structured SSE events the frontend understands.

SSE EVENT PROTOCOL:
  The frontend listens to these event types in order:
  ┌──────────┬──────────────────────────────────────────────────────────┐
  │ Type     │ Meaning                                                  │
  ├──────────┼──────────────────────────────────────────────────────────┤
  │ status   │ Progress update (show a loading indicator)               │
  │ routing  │ Which agent was chosen (show in UI sidebar)              │
  │ token    │ One word/chunk of the response (stream to chat bubble)   │
  │ plan     │ Full SpendingPlan JSON (render charts from this)         │
  │ done     │ Response complete (hide loading indicator)               │
  │ error    │ Something failed (show error toast)                      │
  └──────────┴──────────────────────────────────────────────────────────┘

IMPORTANT — WHY SINGLETONS?
  Building a LangGraph graph compiles the state machine and connects
  the checkpointer. This is expensive (~100ms). We do it ONCE at startup
  and reuse the same compiled graph across all requests.

IMPORTANT — SYNC vs ASYNC:
  LangGraph's app.stream() is SYNCHRONOUS (blocking). Calling it directly
  inside an async function would block the entire FastAPI event loop, making
  the server unresponsive to other requests while one agent runs.

  FIX: We use asyncio.to_thread() to run the synchronous stream in a
  separate thread, freeing the event loop to handle other requests concurrently.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import AsyncGenerator

from langchain_core.messages import AIMessage, HumanMessage

from graph.graph_builder import build_graph, build_planner_graph
from services.observability import build_trace_config

# ── Singleton graph instances ──────────────────────────────────────────────────
# Built once on first request; reused for all subsequent requests.
_graph = None
_planner_graph = None

# ── Logger setup ───────────────────────────────────────────────────────────────
logger = logging.getLogger("finance.flow")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _iter_text_chunks(text: str):
    """
    Split response text into word-sized chunks for streaming.

    Using regex to split on whitespace boundaries means words and spaces
    are emitted separately, which lets the frontend render text smoothly
    without words getting glued together.
    """
    for chunk in re.findall(r"\s+|\S+", text or ""):
        yield chunk


def _latest_ai_message(messages: list) -> AIMessage | None:
    """Return the most recent AIMessage from a node's output messages list."""
    for msg in reversed(messages or []):
        if isinstance(msg, AIMessage):
            return msg
    return None


def _sse(payload: dict) -> str:
    """
    Format a dict as a Server-Sent Event string.

    SSE protocol: each event is "data: <json>\\n\\n"
    The frontend's EventSource listener parses these automatically.
    """
    return f"data: {json.dumps(payload)}\n\n"


def _get_graph():
    """Return the compiled main graph, building it once if needed."""
    global _graph
    if _graph is None:
        logger.info("[runner] Building main agent graph (first request)...")
        _graph = build_graph()
    return _graph


def _get_planner_graph():
    """Return the compiled planner-only graph, building it once if needed."""
    global _planner_graph
    if _planner_graph is None:
        logger.info("[runner] Building planner graph (first request)...")
        _planner_graph = build_planner_graph()
    return _planner_graph


def _collect_graph_output(app, invoke_state: dict, config: dict) -> list[tuple[str, dict]]:
    """
    Run the synchronous LangGraph stream and collect all (node_name, output) pairs.

    WHY THIS FUNCTION EXISTS:
    app.stream() is SYNCHRONOUS — it blocks until each node completes.
    We run this in asyncio.to_thread() so the FastAPI event loop stays free.
    This function is the "synchronous work" that runs in the thread pool.

    Returns:
        List of (node_name, node_output) tuples in execution order.
    """
    results = []
    for chunk in app.stream(invoke_state, config=config, stream_mode="updates"):
        for node_name, node_output in chunk.items():
            results.append((node_name, node_output))
    return results


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API: Non-streaming agent runner (used for background tasks)
# ─────────────────────────────────────────────────────────────────────────────

async def run_agent(
    user_message: str,
    user_id: str,
    session_id: str,
    transactions: list | None = None,     # BUG FIX: was `= []` — mutable default shared across calls
    user_goal: str = "",
    budget_summary: dict | None = None,   # BUG FIX: was `= {}` — same mutable default bug
    turn_id: int | None = None,
    request_id: str | None = None,
) -> dict:
    """
    Run the agent graph and return the full response (non-streaming).

    Used for background tasks or when you need the complete response
    before doing something with it (e.g., saving to DB synchronously).

    Returns:
        {"response": str, "agent_name": str, "success": bool}
    """
    app = _get_graph()

    invoke_state: dict = {
        "messages":     [HumanMessage(content=user_message)],
        "transactions": transactions or [],  # Raw — each agent anonymizes itself after category resolution
    }
    if budget_summary:
        invoke_state["budget_summary"] = budget_summary
    if user_goal:
        invoke_state["user_goal"] = user_goal

    config = build_trace_config(
        thread_id=f"{user_id}:{session_id}",
        user_id=user_id,
        session_id=session_id,
        turn_id=turn_id,
        request_id=request_id,
        is_planner=False,
        agent_hint="orchestrator",
    )

    full_response = ""
    agent_name = "advisor"

    try:
        # Run synchronous graph in thread pool — doesn't block event loop
        node_outputs = await asyncio.to_thread(_collect_graph_output, app, invoke_state, config)

        for node_name, node_output in node_outputs:
            if node_name == "orchestrator":
                continue
            last_msg = _latest_ai_message(node_output.get("messages", []))
            if last_msg:
                full_response = last_msg.content
                agent_name = getattr(last_msg, "name", node_name) or node_name

        return {"response": full_response, "agent_name": agent_name, "success": True}

    except Exception as exc:
        logger.exception("[runner] run_agent failed user_id=%s session_id=%s", user_id, session_id)
        return {"response": f"Something went wrong: {exc}", "agent_name": "error", "success": False}


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API: Streaming agent runner (main chat endpoint)
# ─────────────────────────────────────────────────────────────────────────────

async def stream_agent(
    user_message: str,
    user_id: str,
    session_id: str,
    transactions: list | None = None,   # BUG FIX: mutable default
    user_goal: str = "",
    turn_id: int | None = None,
    request_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    Run the full multi-agent graph and stream the response as SSE events.

    This is the main function called by the /chat/message endpoint.
    The graph runs through: input_validation → guardrails → orchestrator → specialist.

    Yields SSE strings. FastAPI wraps this in a StreamingResponse.

    STREAM FLOW:
      1. Emit "status: analyzing" immediately (shows loading to user)
      2. Run graph in thread pool (non-blocking)
      3. When orchestrator picks an agent → emit "routing" event
      4. When specialist produces text → emit "token" events word by word
      5. If spending_plan in output → emit "plan" event (for chart rendering)
      6. Emit "done" to signal completion
    """
    app = _get_graph()

    logger.info(
        "[chat] stream start user_id=%s session_id=%s msg_len=%s txns=%s",
        user_id, session_id, len(user_message or ""), len(transactions or []),
    )

    invoke_state: dict = {
        "messages":     [HumanMessage(content=user_message)],
        "transactions": transactions or [],  # Raw — each agent anonymizes itself after category resolution
    }
    if user_goal:
        invoke_state["user_goal"] = user_goal

    config = build_trace_config(
        thread_id=f"{user_id}:{session_id}",
        user_id=user_id,
        session_id=session_id,
        turn_id=turn_id,
        request_id=request_id,
        is_planner=False,
        agent_hint="orchestrator",
    )

    emitted_done = False

    try:
        # Show the user something immediately — don't leave them with a blank screen
        yield _sse({"type": "status", "stage": "analyzing",
                    "content": "Analyzing your request and selecting the best agent..."})

        # BUG FIX: Run synchronous app.stream() in a thread so we don't block the event loop.
        # Without to_thread(), a slow LLM response would freeze all other active requests.
        node_outputs = await asyncio.to_thread(_collect_graph_output, app, invoke_state, config)

        for node_name, node_output in node_outputs:
            # Orchestrator only routes — it produces no user-visible content
            if node_name == "orchestrator":
                next_agent = node_output.get("next_agent", "")
                if next_agent:
                    logger.info("[chat] route chosen user_id=%s agent=%s", user_id, next_agent)
                    yield _sse({"type": "routing", "agent": next_agent})
                    yield _sse({
                        "type": "status", "stage": "routing", "agent": next_agent,
                        "content": f"{next_agent.replace('_', ' ').title()} agent selected. Generating answer...",
                    })
                continue

            # All other nodes may produce a message
            last_msg = _latest_ai_message(node_output.get("messages", []))
            if not last_msg:
                continue

            response = last_msg.content or ""
            agent_name = getattr(last_msg, "name", node_name) or node_name

            logger.info("[chat] agent output user_id=%s agent=%s chars=%s",
                        user_id, agent_name, len(response))

            yield _sse({"type": "status", "stage": "generating", "agent": agent_name,
                        "content": f"{agent_name.replace('_', ' ').title()} is preparing your response..."})

            # Stream response word by word
            for chunk_text in _iter_text_chunks(response):
                yield _sse({"type": "token", "content": chunk_text})

            # If a spending plan was generated, emit it for chart rendering
            spending_plan = node_output.get("spending_plan")
            if spending_plan:
                logger.info("[chat] plan payload emitted user_id=%s agent=%s", user_id, agent_name)
                yield _sse({"type": "plan", "data": spending_plan})
                yield _sse({"type": "status", "stage": "plan_ready", "agent": agent_name,
                            "content": "Plan data is ready. Finalizing response..."})

            yield _sse({"type": "done", "agent": agent_name})
            logger.info("[chat] stream done user_id=%s agent=%s", user_id, agent_name)
            emitted_done = True
            return

        if not emitted_done:
            logger.warning("[chat] no AI response generated user_id=%s session_id=%s", user_id, session_id)
            yield _sse({"type": "error", "content": "No response generated. Please try again."})

    except Exception as exc:
        logger.exception("[chat] stream failed user_id=%s session_id=%s", user_id, session_id)
        yield _sse({"type": "error", "content": "An error occurred. Please try again."})


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API: Streaming planner runner (dedicated /plan endpoint)
# ─────────────────────────────────────────────────────────────────────────────

# Status message shown to the user as each workflow node runs
_NODE_STATUS = {
    "intake":         "Preparing your next question...",
    "extract":        "Understanding your answer...",
    "tools":          "Running financial calculations (SIP, goals, emergency fund)...",
    "plan_generator": "Building your personalised spending plan...",
    "followup":       "Thinking through your question...",
}


async def stream_planner(
    user_message: str,
    user_id: str,
    session_id: str,
    transactions: list | None = None,
    turn_id: int | None = None,
    request_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    Run the Spending Planner workflow (Chat 2) and stream as SSE events.

    Uses the 5-node planner workflow instead of the old single-function agent:
      intake → extract → tools → plan_generator → END
                       ↘ intake   (retry on invalid answer)
                       ↘ followup (post-plan Q&A)

    Turn-by-turn:
      Turn 1: intake asks salary question
      Turn 2: extract parses income → intake asks expenses
      Turn 3: extract parses expenses → intake asks goals
      Turn 4: extract parses goals → intake asks risk
      Turn 5: extract parses risk → tools → plan_generator delivers plan
      Turn 6+: extract → followup answers follow-up questions

    Memory: checkpointer thread_id = "planner:{user_id}:{session_id}"
    Kept separate from main finance advisor chat memory.
    """
    app = _get_planner_graph()

    logger.info(
        "[plan] stream start user_id=%s session_id=%s msg_len=%s txns=%s",
        user_id, session_id, len(user_message or ""), len(transactions or []),
    )

    # CRITICAL: Pass ONLY messages + transactions.
    # The checkpointer restores planner_income, planner_expenses, planner_goals,
    # planner_risk, planner_stage from the previous turn automatically.
    # Passing them here would overwrite saved profile data.
    invoke_state: dict = {
        "messages":     [HumanMessage(content=user_message)],
        "transactions": transactions or [],  # Raw — each agent anonymizes itself after category resolution
    }

    config = build_trace_config(
        thread_id=f"planner:{user_id}:{session_id}",
        user_id=user_id,
        session_id=session_id,
        turn_id=turn_id,
        request_id=request_id,
        is_planner=True,
        agent_hint="spending_planner",
    )

    emitted_done = False

    try:
        yield _sse({"type": "routing", "agent": "spending_planner"})
        yield _sse({
            "type": "status", "stage": "analyzing",
            "agent": "spending_planner",
            "content": "Spending Planner is processing your message...",
        })

        # Run synchronous graph in thread pool — keeps FastAPI event loop free
        node_outputs = await asyncio.to_thread(_collect_graph_output, app, invoke_state, config)

        response_text = ""
        agent_name = "spending_planner"
        spending_plan = None

        for node_name, node_output in node_outputs:
            logger.info("[plan] node_done node=%s user_id=%s", node_name, user_id)

            # extract is internal — emit brief status only if answer was invalid
            if node_name == "extract":
                if not node_output.get("_extract_valid", True):
                    yield _sse({
                        "type": "status", "stage": "validating",
                        "agent": "spending_planner",
                        "content": "Let me clarify that question...",
                    })
                continue

            # Emit per-node progress status
            yield _sse({
                "type": "status", "stage": node_name,
                "agent": "spending_planner",
                "content": _NODE_STATUS.get(node_name, "Processing..."),
            })

            # Collect spending plan if plan_generator produced one
            if node_output.get("spending_plan"):
                spending_plan = node_output["spending_plan"]

            # Collect the AI message from this node
            msg = _latest_ai_message(node_output.get("messages", []))
            if msg and msg.content:
                response_text = msg.content
                agent_name = getattr(msg, "name", "spending_planner") or "spending_planner"

        # ── Stream the response token by token ────────────────────────
        if response_text:
            logger.info("[plan] streaming response chars=%s user_id=%s", len(response_text), user_id)

            for chunk_text in _iter_text_chunks(response_text):
                yield _sse({"type": "token", "content": chunk_text})

            # Emit spending plan JSON for frontend chart rendering
            if spending_plan:
                logger.info("[plan] plan payload emitted user_id=%s", user_id)
                yield _sse({"type": "plan", "data": spending_plan})
                yield _sse({
                    "type": "status", "stage": "plan_ready",
                    "agent": agent_name,
                    "content": "Your spending plan is ready!",
                })

            yield _sse({"type": "done", "agent": agent_name})
            logger.info("[plan] stream done user_id=%s agent=%s", user_id, agent_name)
            emitted_done = True

        if not emitted_done:
            logger.warning("[plan] no response generated user_id=%s session_id=%s", user_id, session_id)
            yield _sse({"type": "error", "content": "No response generated. Please try again."})

    except Exception as exc:
        logger.exception("[plan] stream failed user_id=%s session_id=%s", user_id, session_id)
        yield _sse({"type": "error", "content": "An error occurred while building your plan."})