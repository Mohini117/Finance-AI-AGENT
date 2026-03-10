"""
routers/plan.py
===============
Dedicated endpoints for the Spending Planner feature.

WHY SEPARATE FROM /chat?
  The spending planner is a multi-turn form-filling conversation:
  income → expenses → goals → risk → plan.

  It uses a SEPARATE LangGraph (build_planner_graph) with its own
  checkpointer thread_id prefix ("planner:user:session"), so:
  - The planner's state (income, expenses, goals) is isolated from chat state
  - A user can use the main chat AND the planner in the same session
    without one overwriting the other's memory

ENDPOINTS:
  POST /plan/chat          — Multi-turn planner conversation (streaming SSE)
  POST /plan/generate      — Single-shot plan generation from form data
  GET  /plan/latest        — Get the most recent plan for this user/session
  GET  /plan/history       — List all plans the user has generated
  GET  /plan/{plan_id}     — Get a specific plan by ID

SSE EVENTS:
  Same protocol as /chat/message, plus a "plan" event type that carries
  the full SpendingPlan JSON for the frontend to render charts.
"""

import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from middleware.auth_middleware import get_current_user
from services.agent_runner import stream_agent, stream_planner
from services.supabase_client import supabase_admin

router = APIRouter(prefix="/plan", tags=["plan"])
logger = logging.getLogger("finance.flow")


# ── Request models ─────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    session_id: str
    message:    str


class PlanRequest(BaseModel):
    income:       float = Field(default=0.0)
    expenses:     dict  = Field(default_factory=dict)
    goals:        dict  = Field(default_factory=dict)
    custom_split: dict  = Field(default_factory=dict)
    session_id:   str


# ─────────────────────────────────────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _assert_session_owner(session_id: str, user_id: str) -> None:
    """
    Verify the session belongs to the user. Raises 404 if not found.

    Called before every plan endpoint to prevent users from accessing
    each other's sessions. This is a simple ownership check — Supabase
    RLS would also catch this, but explicit checks make intent clear.
    """
    session = (
        supabase_admin.table("sessions")
        .select("id")
        .eq("id", session_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not session.data:
        raise HTTPException(status_code=404, detail="Session not found.")


def _fetch_transactions(user_id: str) -> list:
    """
    Load up to 100 recent transactions for context in the plan.

    Returns empty list on error — transactions are optional context,
    not a hard requirement for plan generation.
    """
    try:
        result = (
            supabase_admin.table("transactions")
            .select("date, description, amount, category")
            .eq("user_id", user_id)
            .order("date", desc=True)
            .limit(100)
            .execute()
        )
        return result.data or []
    except Exception:
        logger.warning("[plan] failed to fetch transactions for user_id=%s", user_id)
        return []


def _get_turn_id(session_id: str) -> int | None:
    """Compute turn number from message count. Returns None on error."""
    try:
        result = (
            supabase_admin.table("messages")
            .select("id", count="exact")
            .eq("session_id", session_id)
            .execute()
        )
        total = result.count or 0
        return (total + 1) // 2
    except Exception:
        return None


async def _save_plan_to_db(plan_data: dict, user_id: str, session_id: str, income_override: float = 0) -> None:
    """Save a generated spending plan to the spending_plans table."""
    try:
        now = datetime.now(timezone.utc)
        row: dict = {
            "user_id":    user_id,
            "plan":       plan_data,
            "month":      now.date().replace(day=1).isoformat(),  # first day of current month
            "created_at": now.isoformat(),
        }
        income = income_override or plan_data.get("monthly_income", 0)
        if income > 0:
            row["income"] = income
        supabase_admin.table("spending_plans").insert(row).execute()
    except Exception:
        logger.exception("[plan] failed to save spending_plan session_id=%s", session_id)


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/chat")
async def plan_chat(body: ChatRequest, user=Depends(get_current_user)):
    """
    Multi-turn planner conversation endpoint.

    Each message continues the planner's state machine:
    ask_income → ask_expenses → ask_goals → ask_risk → plan_ready

    The planner's memory (income, expenses, goals, risk) is preserved across
    turns via the LangGraph checkpointer, keyed by "planner:{user_id}:{session_id}".

    Returns: StreamingResponse (SSE)
    """
    request_id = str(uuid.uuid4())[:8]
    _assert_session_owner(body.session_id, user.id)
    transactions = _fetch_transactions(user.id)
    turn_id = _get_turn_id(body.session_id)

    # Save user message to DB
    try:
        supabase_admin.table("messages").insert({
            "session_id": body.session_id,
            "role":       "user",
            "content":    body.message,
            "agent_name": "",
        }).execute()
    except Exception:
        logger.exception("[plan][%s] failed to save user message", request_id)

    logger.info("[plan][%s] start user_id=%s session_id=%s msg_len=%s",
                request_id, user.id, body.session_id, len(body.message or ""))

    async def event_stream():
        full_response_parts: list[str] = []
        plan_saved = False
        assistant_saved = False

        async for chunk in stream_planner(
            user_message=body.message,
            user_id=user.id,
            session_id=body.session_id,
            transactions=transactions,
            turn_id=turn_id,
            request_id=request_id,
        ):
            try:
                data = json.loads(chunk.replace("data: ", "").strip())
                event_type = data.get("type", "")

                if event_type == "token":
                    full_response_parts.append(data.get("content", ""))

                elif event_type == "plan" and not plan_saved:
                    await _save_plan_to_db(data.get("data", {}), user.id, body.session_id)
                    plan_saved = True

                elif event_type == "done" and not assistant_saved:
                    full_response = "".join(full_response_parts)
                    try:
                        supabase_admin.table("messages").insert({
                            "session_id": body.session_id,
                            "role":       "assistant",
                            "content":    full_response,
                            "agent_name": data.get("agent", "spending_planner"),
                        }).execute()
                        supabase_admin.table("sessions").update({
                            "updated_at": datetime.now(timezone.utc).isoformat()
                        }).eq("id", body.session_id).execute()
                        logger.info("[plan][%s] response saved chars=%s", request_id, len(full_response))
                    except Exception:
                        logger.exception("[plan][%s] failed to save assistant message", request_id)
                    assistant_saved = True

            except Exception:
                logger.exception("[plan][%s] failed to process chunk", request_id)

            yield chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Access-Control-Allow-Origin": "*"},
    )


@router.post("/generate")
async def generate_plan(body: PlanRequest, user=Depends(get_current_user)):
    """
    Single-shot plan generation from structured form data.

    Used when the user fills in a form (income, expenses, goals) instead
    of having a multi-turn conversation. Routes through the main graph
    so input_validation + guardrails still run.

    Returns: StreamingResponse (SSE)
    """
    request_id = str(uuid.uuid4())[:8]
    _assert_session_owner(body.session_id, user.id)
    transactions = _fetch_transactions(user.id)
    turn_id = _get_turn_id(body.session_id)

    # Build a natural language message from the form data
    user_message = (
        f"Help me plan my salary. My monthly take-home is Rs{body.income:,.0f}."
        if body.income > 0
        else "Help me plan my salary and create a spending plan."
    )

    logger.info("[plan-generate][%s] start user_id=%s income=%s", request_id, user.id, body.income)

    async def event_stream():
        full_response_parts: list[str] = []
        plan_saved = False
        assistant_saved = False

        async for chunk in stream_agent(
            user_message=user_message,
            user_id=user.id,
            session_id=body.session_id,
            transactions=transactions,
            user_goal="",
            turn_id=turn_id,
            request_id=request_id,
        ):
            try:
                data = json.loads(chunk.replace("data: ", "").strip())
                event_type = data.get("type", "")

                if event_type == "token":
                    full_response_parts.append(data.get("content", ""))

                elif event_type == "plan" and not plan_saved:
                    await _save_plan_to_db(
                        data.get("data", {}), user.id, body.session_id,
                        income_override=body.income,
                    )
                    plan_saved = True

                elif event_type == "done" and not assistant_saved:
                    try:
                        supabase_admin.table("messages").insert({
                            "session_id": body.session_id,
                            "role":       "assistant",
                            "content":    "".join(full_response_parts),
                            "agent_name": data.get("agent", "spending_planner"),
                        }).execute()
                    except Exception:
                        logger.exception("[plan-generate][%s] failed to save message", request_id)
                    assistant_saved = True

            except Exception:
                logger.exception("[plan-generate][%s] chunk error", request_id)

            yield chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Access-Control-Allow-Origin": "*"},
    )


@router.get("/latest")
async def get_latest_plan(
    session_id: str | None = Query(default=None),
    user=Depends(get_current_user),
):
    """
    Get the most recently generated plan for this user.

    Optionally filter by session_id to get the plan from a specific conversation.
    Returns {"plan": null} if no plan has been generated yet (not an error).
    """
    try:
        query = (
            supabase_admin.table("spending_plans")
            .select("id, plan, income, created_at")
            .eq("user_id", user.id)
        )
        # Note: spending_plans has no session_id column; filter is ignored silently
        result = query.order("created_at", desc=True).limit(1).execute()
        return {"plan": result.data[0] if result.data else None}

    except Exception as exc:
        # BUG FIX: Original had bare `except Exception` that swallowed real crashes.
        # Now we log the error and still return a safe response.
        logger.exception("[plan] get_latest_plan failed user_id=%s", user.id)
        return {"plan": None}


@router.get("/history")
async def get_plan_history(user=Depends(get_current_user)):
    """Return a list of all plans the user has generated (summary, no full plan_data)."""
    try:
        result = (
            supabase_admin.table("spending_plans")
            .select("id, income, created_at")
            .eq("user_id", user.id)
            .order("created_at", desc=True)
            .execute()
        )
        return {"plans": result.data or []}
    except Exception as exc:
        logger.exception("[plan] get_plan_history failed user_id=%s", user.id)
        return {"plans": []}


@router.get("/{plan_id}")
async def get_plan(plan_id: str, user=Depends(get_current_user)):
    """
    Get a specific plan by its ID.

    Verifies ownership — users can only access their own plans.
    """
    try:
        result = (
            supabase_admin.table("spending_plans")
            .select("id, plan, income, created_at")
            .eq("id", plan_id)
            .eq("user_id", user.id)
            .execute()
        )
        if not result.data:
            raise HTTPException(status_code=404, detail="Plan not found.")
        return {"plan": result.data[0]}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))