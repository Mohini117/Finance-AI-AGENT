"""
routers/chat.py
===============
The main conversational chat endpoint — the heart of the application.

ENDPOINTS:
  GET  /chat/sessions                          — List all user sessions (sidebar)
  POST /chat/sessions                          — Create a new session
  GET  /chat/sessions/{id}/messages            — Load history for a session
  DELETE /chat/sessions/{id}                   — Delete a session
  POST /chat/message                           — Send a message (streaming SSE response)

THE STREAMING ARCHITECTURE:
  /chat/message uses Server-Sent Events (SSE) to stream the response.
  Why SSE instead of WebSockets?
  - One-directional (server → client) — SSE is simpler for this use case
  - Works with standard HTTP — easier to proxy and scale
  - FastAPI's StreamingResponse handles it natively

  EVENT TYPES EMITTED (in order):
  status → routing → status → token... → plan? → done

  The React frontend listens with EventSource and builds the chat bubble
  token by token as they arrive, giving a smooth "typing" effect.

SESSION TITLE AUTO-GENERATION:
  The first user message of a session is truncated to 40 chars and saved
  as the session title. This shows in the sidebar instead of "New Chat".

TURN ID TRACKING:
  turn_id = (total_messages + 1) // 2
  This counts conversations in pairs (user + assistant = 1 turn).
  Used in LangSmith traces to identify which turn a trace belongs to.
"""

import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from middleware.auth_middleware import get_current_user
from services.agent_runner import stream_agent
from services.supabase_client import supabase_admin

router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger("finance.flow")

# ── Config ──────────────────────────────────────────────────────────────────────
MAX_MESSAGE_LENGTH = 2000  # Characters. Prevents absurdly long prompts / prompt injection attempts.


# ── Request model ───────────────────────────────────────────────────────────────

class MessageRequest(BaseModel):
    session_id: str
    message:    str
    user_goal:  str = ""

    # BUG FIX: Validate message length to prevent runaway LLM usage
    @field_validator("message")
    @classmethod
    def message_must_not_be_empty_or_too_long(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Message cannot be empty.")
        if len(v) > MAX_MESSAGE_LENGTH:
            raise ValueError(f"Message too long. Maximum {MAX_MESSAGE_LENGTH} characters.")
        return v


# ─────────────────────────────────────────────────────────────────────────────
# SESSION MANAGEMENT ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/sessions")
async def get_sessions(user=Depends(get_current_user)):
    """Return all chat sessions for the authenticated user, newest first."""
    try:
        result = (
            supabase_admin.table("sessions")
            .select("id, title, created_at, updated_at")
            .eq("user_id", user.id)
            .order("updated_at", desc=True)
            .execute()
        )
        return {"sessions": result.data or []}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sessions")
async def create_session(user=Depends(get_current_user)):
    """Create a new empty chat session. Returns the session object."""
    try:
        result = (
            supabase_admin.table("sessions")
            .insert({"user_id": user.id, "title": "New Chat"})
            .execute()
        )
        return {"session": result.data[0]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str, user=Depends(get_current_user)):
    """
    Load the full message history for a session.

    Verifies the session belongs to the requesting user before returning data.
    This prevents users from reading each other's conversations.
    """
    try:
        # Ownership check — does this session belong to this user?
        session = (
            supabase_admin.table("sessions")
            .select("id")
            .eq("id", session_id)
            .eq("user_id", user.id)
            .execute()
        )
        if not session.data:
            raise HTTPException(status_code=404, detail="Session not found.")

        messages = (
            supabase_admin.table("messages")
            .select("role, content, agent_name, created_at")
            .eq("session_id", session_id)
            .order("created_at")
            .execute()
        )
        return {"messages": messages.data or []}

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, user=Depends(get_current_user)):
    """
    Delete a session and all its messages.

    Supabase cascading deletes handle message cleanup automatically
    if the schema has ON DELETE CASCADE on the messages table.
    """
    try:
        (
            supabase_admin.table("sessions")
            .delete()
            .eq("id", session_id)
            .eq("user_id", user.id)
            .execute()
        )
        return {"message": "Session deleted."}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# MAIN CHAT ENDPOINT — STREAMING SSE
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/message")
async def send_message(body: MessageRequest, user=Depends(get_current_user)):
    """
    Process a user message and stream the AI response via Server-Sent Events.

    Steps:
    1. Validate session ownership
    2. Load recent transactions for context
    3. Save user message to DB
    4. Auto-update session title on first message
    5. Stream agent response token by token
    6. Save assistant response to DB when streaming completes

    Returns: StreamingResponse (text/event-stream)
    """
    request_id = str(uuid.uuid4())[:8]

    logger.info(
        "[chat][%s] start user_id=%s session_id=%s msg_len=%s",
        request_id, user.id, body.session_id, len(body.message),
    )

    # ── 1. Validate session ownership ──────────────────────────────────
    session = (
        supabase_admin.table("sessions")
        .select("id")
        .eq("id", body.session_id)
        .eq("user_id", user.id)
        .limit(1)
        .execute()
    )
    if not session.data:
        raise HTTPException(status_code=404, detail="Session not found.")

    # ── 2. Load recent transactions for agent context ──────────────────
    try:
        txn_result = (
            supabase_admin.table("transactions")
            .select("date, description, amount, category")
            .eq("user_id", user.id)
            .order("date", desc=True)
            .limit(50)
            .execute()
        )
        transactions = txn_result.data or []
    except Exception:
        transactions = []  # Non-fatal — agent works without transaction data

    # ── 3. Save user message ───────────────────────────────────────────
    supabase_admin.table("messages").insert({
        "session_id": body.session_id,
        "role":       "user",
        "content":    body.message,
        "agent_name": "",
    }).execute()

    # ── 4. Compute turn_id and auto-title first message ────────────────
    try:
        count_result = (
            supabase_admin.table("messages")
            .select("id", count="exact")
            .eq("session_id", body.session_id)
            .execute()
        )
        message_total = count_result.count or 0
        turn_id = (message_total + 1) // 2

        # Auto-set session title from first user message
        if message_total <= 1:
            title = body.message[:40] + ("..." if len(body.message) > 40 else "")
            supabase_admin.table("sessions").update({
                "title":      title,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", body.session_id).execute()
    except Exception:
        turn_id = None  # Non-fatal — used only for observability

    # ── 5. Stream the agent response ───────────────────────────────────

    async def event_stream():
        """
        Inner async generator that:
        - Iterates over SSE chunks from stream_agent()
        - Intercepts the "done" event to save the assistant message to DB
        - Yields all chunks to the StreamingResponse

        BUG FIX: Original used list-as-mutable-reference pattern for closure vars
        (full_response=[], assistant_saved=[False]). This works but is confusing.
        Using nonlocal is the standard Python pattern for mutable closure variables.
        """
        # These accumulate across the generator's lifetime
        full_response_parts: list[str] = []
        agent_name = "advisor"
        assistant_saved = False

        async for chunk in stream_agent(
            user_message=body.message,
            user_id=user.id,
            session_id=body.session_id,
            transactions=transactions,
            user_goal=body.user_goal,
            turn_id=turn_id,
            request_id=request_id,
        ):
            try:
                data = json.loads(chunk.replace("data: ", "").strip())
                event_type = data.get("type", "")

                if event_type == "routing":
                    logger.info("[chat][%s] routed to agent=%s", request_id, data.get("agent"))

                elif event_type == "token":
                    full_response_parts.append(data.get("content", ""))

                elif event_type == "done" and not assistant_saved:
                    agent_name = data.get("agent", "advisor")
                    full_response = "".join(full_response_parts)

                    # Save completed assistant message to DB
                    try:
                        supabase_admin.table("messages").insert({
                            "session_id": body.session_id,
                            "role":       "assistant",
                            "content":    full_response,
                            "agent_name": agent_name,
                        }).execute()

                        supabase_admin.table("sessions").update({
                            "updated_at": datetime.now(timezone.utc).isoformat()
                        }).eq("id", body.session_id).execute()

                        logger.info(
                            "[chat][%s] response saved agent=%s chars=%s",
                            request_id, agent_name, len(full_response),
                        )
                    except Exception:
                        logger.exception("[chat][%s] failed to save assistant message", request_id)

                    assistant_saved = True

            except Exception:
                logger.exception("[chat][%s] failed to process chunk", request_id)

            yield chunk  # Always yield to the frontend, even on processing errors

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",       # Prevent nginx from buffering SSE
        },
    )
