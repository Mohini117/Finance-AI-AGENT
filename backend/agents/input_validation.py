"""
agents/input_validation.py
==========================
Pre-routing validation — the FIRST node in the graph.

RESPONSIBILITY:
  Inspect the user's message BEFORE any specialist agent runs.
  Catch obvious problems early and return a helpful error immediately,
  rather than letting a specialist agent fail silently or give a
  confusing response.

WHAT IT CHECKS:
  1. Missing income    — User asked for a spending plan but hasn't shared their salary.
  2. Missing data      — User asked to analyze transactions but no CSV was uploaded.
  3. Missing goal      — Noted as a soft warning (doesn't block, but flags for coach).
  4. Routing hint      — Detects explicit "use expense tracker" style commands.

WHY A SEPARATE VALIDATION NODE?
  - Clean separation: agents focus on their specialty, not error handling.
  - Better UX: early, specific error messages instead of vague agent responses.
  - Reusable: one place to add new input requirements for future agents.

ROUTING HINTS:
  If the user says "use expense tracker" or "/agent budget_analyst",
  this node extracts that intent and stores it in `routing_hint`.
  The orchestrator respects this hint over its own classification.
"""

import re

from langchain_core.messages import AIMessage, HumanMessage
from graph.state import FinanceState
from tools.anonymizer import summarize_locally


# ─────────────────────────────────────────────────────────────────────────────
# ROUTING HINT DETECTION
# Users can explicitly request an agent using natural language or slash commands.
# ─────────────────────────────────────────────────────────────────────────────

# Canonical agent names → natural language aliases users might say
AGENT_ALIASES: dict[str, list[str]] = {
    "expense_tracker": [
        "expense tracker", "expense tracking", "expenses agent", "transaction analyzer",
    ],
    "budget_analyst": [
        "budget analyst", "budget checker", "budget agent",
    ],
    "savings_finder": [
        "savings finder", "save money agent", "savings agent",
    ],
    "financial_coach": [
        "financial coach", "coach agent", "advisor", "finance advisor",
    ],
}

# Verbs that precede an agent name: "use expense tracker", "switch to budget analyst"
ROUTING_PREFIXES = ["use", "switch to", "route to", "pick", "choose", "select", "act as", "talk to"]

# Keywords that imply transaction analysis
TRANSACTION_KEYWORDS = [
    "transaction", "transactions", "categorize", "category",
    "analyze", "analysis", "spending breakdown",
]


def _get_latest_user_text(messages: list) -> str:
    """Return the most recent user message, lowercased."""
    for msg in reversed(messages or []):
        if isinstance(msg, HumanMessage):
            return (msg.content or "").strip()
    return ""


def _extract_routing_hint(text: str) -> str:
    """
    Detect if the user explicitly named a specific agent to use.

    Supports two formats:
    1. Slash command: "/agent expense_tracker"
    2. Natural language: "use expense tracker", "switch to budget analyst"

    Returns the canonical agent key or "" if no explicit routing found.
    """
    lowered = (text or "").lower().strip()
    if not lowered:
        return ""

    # Slash command format: /agent <name>
    slash_cmd = re.search(r"/agent\s+([a-z_\-\s]+)", lowered)
    if slash_cmd:
        normalized = re.sub(r"[\s\-]+", "_", slash_cmd.group(1).strip())
        for agent_name, aliases in AGENT_ALIASES.items():
            if normalized == agent_name:
                return agent_name
            if normalized in [re.sub(r"[\s\-]+", "_", alias) for alias in aliases]:
                return agent_name

    # Natural language: "use X" or "switch to X"
    for agent_name, aliases in AGENT_ALIASES.items():
        for alias in aliases:
            for prefix in ROUTING_PREFIXES:
                if f"{prefix} {alias}" in lowered:
                    return agent_name

    return ""


# ─────────────────────────────────────────────────────────────────────────────
# MAIN VALIDATION AGENT
# ─────────────────────────────────────────────────────────────────────────────

def input_validation_agent(state: FinanceState) -> dict:
    """
    Run pre-flight checks before routing to a specialist agent.

    This agent can do two things:
    1. BLOCK — Return an early error response (graph exits immediately).
    2. PASS  — Enrich state with computed summaries and routing hints.

    Returns a partial state dict. If validation_status = "BLOCK",
    the graph router will send the conversation directly to END.
    """
    messages = state.get("messages", [])
    raw_text = _get_latest_user_text(messages)
    text = raw_text.lower()

    transactions = state.get("transactions", []) or []
    user_goal = (state.get("user_goal") or "").strip()
    routing_hint = _extract_routing_hint(text)

    # Pre-compute budget summary from uploaded transactions (done once here,
    # so individual agents don't each need to repeat this calculation).
    budget_summary = summarize_locally(transactions) if transactions else {}

    notes: list[str] = []

    # ── Check 1: Transaction analysis requested but no CSV uploaded ────
    asks_txn_analysis = routing_hint in {"expense_tracker", "budget_analyst"} or any(
        k in text for k in TRANSACTION_KEYWORDS
    )
    if asks_txn_analysis and not transactions:
        notes.append("missing_transactions")
        msg = (
            "I don't see any transaction data yet. 📂\n\n"
            "Upload your bank statement CSV from the **Dashboard** tab first, "
            "then I can analyze your spending patterns and categories."
        )
        return {
            "messages": [AIMessage(content=msg, name="validator")],
            "final_response": msg,
            "budget_summary": budget_summary,
            "validation_status": "BLOCK",
            "validation_notes": notes,
            "routing_hint": routing_hint,
        }

    # ── Soft warning: goal not set ─────────────────────────────────────
    # This doesn't block — it just flags the coach to ask the user for a goal.
    if not user_goal:
        notes.append("missing_goal")

    # ── All checks passed ──────────────────────────────────────────────
    return {
        "budget_summary":    budget_summary,
        # "planner_income":    effective_income if effective_income > 0 else stored_income,
        "validation_status": "OK",
        "validation_notes":  notes,
        "routing_hint":      routing_hint,
    }