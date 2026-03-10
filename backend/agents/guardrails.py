"""
agents/guardrails.py
====================
A safety layer that runs BEFORE every specialist agent.

TWO RESPONSIBILITIES:
  1. BLOCK  — Hard stop for illegal / harmful queries.
             The graph exits immediately and returns a refusal message.

  2. SENSITIVE — Detect emotional distress (depression, panic, etc.)
               The graph continues, but injects a tone instruction into state
               so the financial_coach responds with extra empathy.

WHY A SEPARATE NODE?
  - Centralised safety: you update one file, all agents benefit.
  - No agent has to handle its own guardrailing — cleaner separation of concerns.
  - Easy to extend: just add terms to BLOCK_TERMS or SENSITIVE_TERMS.

RECRUITER NOTE:
  This mirrors how production AI systems work — guardrails live outside
  the model, in application logic, so they can't be jailbroken by prompt injection.
"""

from langchain_core.messages import AIMessage, HumanMessage
from graph.state import FinanceState


# ── Hard-block terms ───────────────────────────────────────────────────────────
# Any message containing these will be refused outright.
# The graph exits immediately — no specialist agent runs.
BLOCK_TERMS = [
    "hack bank",
    "steal card",
    "launder money",
    "tax fraud",
    "fake invoice",
]

# ── Sensitive / emotional terms ────────────────────────────────────────────────
# These trigger a "SENSITIVE" status — the conversation continues but the
# financial_coach will use a more empathetic, careful tone.
SENSITIVE_TERMS = [
    "depressed",
    "suicide",
    "self harm",
    "panic attack",
    "overwhelmed",
]


def _get_latest_user_text(messages: list) -> str:
    """Return the most recent user message as lowercase text for matching."""
    for msg in reversed(messages or []):
        if isinstance(msg, HumanMessage):
            return (msg.content or "").strip().lower()
    return ""


def guardrails_agent(state: FinanceState) -> dict:
    """
    Safety pre-check before any specialist agent runs.

    Returns one of three outcomes:
    ┌────────────┬────────────────────────────────────────────────────────┐
    │ Status     │ Effect                                                 │
    ├────────────┼────────────────────────────────────────────────────────┤
    │ BLOCK      │ Sets final_response + guardrail_status="BLOCK"        │
    │            │ Graph routes to END — user sees the refusal message.  │
    ├────────────┼────────────────────────────────────────────────────────┤
    │ SENSITIVE  │ Sets sensitive_context with tone instruction.          │
    │            │ Graph continues to orchestrator → financial_coach.    │
    ├────────────┼────────────────────────────────────────────────────────┤
    │ ALLOW      │ No issues. Graph continues normally.                  │
    └────────────┴────────────────────────────────────────────────────────┘
    """
    text = _get_latest_user_text(state.get("messages", []))

    # ── Hard block ─────────────────────────────────────────────────────
    if any(term in text for term in BLOCK_TERMS):
        refusal = "I can't help with illegal or harmful financial activity."
        return {
            "messages": [AIMessage(content=refusal, name="guardrails")],
            "final_response": refusal,
            "guardrail_status": "BLOCK",
            "sensitive_context": "",
        }

    # ── Emotional / sensitive language ─────────────────────────────────
    if any(term in text for term in SENSITIVE_TERMS):
        return {
            # No message added here — we let the financial_coach respond
            # but with the empathetic context injected below.
            "guardrail_status": "SENSITIVE",
            "sensitive_context": (
                "User may be emotionally stressed. "
                "Lead with empathy before practical advice."
            ),
        }

    # ── All clear ──────────────────────────────────────────────────────
    return {
        "guardrail_status": "ALLOW",
        "sensitive_context": "",
    }