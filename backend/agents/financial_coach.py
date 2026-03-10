"""
agents/financial_coach.py
=========================
The general-purpose personal finance advisor — the fallback agent.

WHEN IT RUNS:
  - Vague or open-ended questions: "How do I manage money better?"
  - Emotional/stressed messages (after guardrails sets sensitive_context)
  - Greetings and small talk (handled without any LLM call)
  - Any query the orchestrator can't classify to a specialist

FIXES vs previous version:
  1. Goal is now extracted from the conversation itself — not only from
     state["user_goal"]. If the user says "saving for a home" in their
     message, that IS their goal. We no longer ask again.
  2. Conversation history is scanned for a previously stated goal so the
     agent remembers across turns.
  3. The "ask for goal" mode is only triggered when there is genuinely NO
     goal anywhere in the message or history — not as a default fallback.
  4. Advisor now gives actual advice when user explicitly states a goal
     in the same message (e.g. "show some tips about investment").
"""

import re

from langchain_core.messages import AIMessage, HumanMessage
from graph.state import FinanceState
from models import get_llm
from tools.anonymizer import summarize_locally

llm = get_llm(temperature=0.4)


# ── Greeting detection (no LLM needed) ────────────────────────────────────────
_GREETING_PHRASES = {
    "hi", "hello", "hey", "good morning", "good afternoon", "good evening",
    "how are you", "what can you do", "who are you", "thanks", "thank you",
}

def _is_small_talk(message: str) -> bool:
    text = (message or "").strip().lower()
    if not text:
        return True
    if text in _GREETING_PHRASES:
        return True
    if len(text.split()) <= 6 and any(text.startswith(p) for p in _GREETING_PHRASES):
        return True
    return False


def _small_talk_reply() -> str:
    return (
        "Hi! I'm your personal finance coach. Here's what I can help with:\n\n"
        "- 📊 **Spending analysis** — upload a CSV to see where your money goes\n"
        "- 💰 **Salary planning** — build a monthly budget before you spend\n"
        "- ✂️ **Savings tips** — find cheaper alternatives and cut expenses\n"
        "- 🎯 **Budget health** — check if you're on track for your goals\n\n"
        "Ask me anything, or say _'plan my salary'_ to get started."
    )


def _get_latest_user_message(messages: list) -> str:
    for msg in reversed(messages or []):
        if isinstance(msg, HumanMessage):
            return (msg.content or "").strip()
    return ""


# ── Goal extraction ────────────────────────────────────────────────────────────
# These patterns look for goals stated directly in the user's message.
# If ANY of these match, we treat it as a goal and give advice immediately.

_GOAL_PATTERNS = [
    # Explicit goal phrases
    r"(save|saving|savings)\s+(for|up\s+for|towards?)\s+([\w\s]+)",
    r"(build|building|create|creating|start|starting)\s+an?\s+(emergency fund|fund|corpus|savings)",
    r"(invest|investing|investment)\s+(in|on|tips|advice|ideas)?",
    r"(buy|buying|purchase|down payment)\s+(a\s+)?(house|home|car|flat|property)",
    r"(retire|retirement|retire\s+early|financial\s+freedom|fire)",
    r"(pay\s+off|clear|reduce)\s+(debt|loan|emi|credit card)",
    r"(budget|budgeting|manage|managing)\s+(my\s+)?(money|salary|expenses|spending|finances)",
    r"(cut|reduce|lower|decrease)\s+(my\s+)?(expenses|spending|costs|bills)",
    r"(emergency|contingency)\s+fund",
    r"(tips?|advice|suggest|help)\s+(on|for|about|with)?\s+(invest|saving|money|finance|budget)",
    r"(plan|planning)\s+(my\s+)?(salary|budget|finances|money|retirement)",
    r"(grow|growing)\s+(my\s+)?(money|wealth|savings|income)",
    r"(foreign|international)\s+trip",
    r"sip|fd|elss|mutual fund|stock|zerodha|groww",
]

_GOAL_KEYWORDS = [
    "investment", "investing", "savings", "budget", "emergency fund",
    "down payment", "retirement", "loan", "emi", "debt", "expenses",
    "spending", "salary", "income", "wealth", "tips", "advice",
    "planning", "finance", "money", "sip", "fd", "mutual fund",
]

def _extract_goal_from_message(message: str) -> str | None:
    """
    Try to extract a financial goal from the user's current message.
    Returns a short goal string, or None if nothing found.
    """
    text = message.lower().strip()

    for pattern in _GOAL_PATTERNS:
        match = re.search(pattern, text)
        if match:
            # Return a cleaned version of the full message as the goal context
            return message.strip()

    # Keyword fallback — if any finance keyword appears, treat message as goal-bearing
    if any(kw in text for kw in _GOAL_KEYWORDS):
        return message.strip()

    return None


def _extract_goal_from_history(messages: list) -> str | None:
    """
    Scan previous human messages for a stated goal.
    Returns the most recent one found, or None.
    """
    for msg in reversed(messages or []):
        if isinstance(msg, HumanMessage):
            content = (msg.content or "").strip()
            goal = _extract_goal_from_message(content)
            if goal:
                return goal
    return None


def _build_category_snapshot(transactions: list) -> str:
    if not transactions:
        return "No transactions available"
    category_totals: dict[str, float] = {}
    for txn in transactions:
        cat = str(txn.get("category") or "Other")
        try:
            amount = abs(float(txn.get("amount", 0) or 0))
        except (TypeError, ValueError):
            amount = 0.0
        category_totals[cat] = category_totals.get(cat, 0.0) + amount
    top = sorted(category_totals.items(), key=lambda x: x[1], reverse=True)[:3]
    return ", ".join([f"{c}: ₹{v:,.0f}" for c, v in top]) if top else "No data"


def financial_coach_agent(state: FinanceState) -> dict:
    """
    Flexible conversational finance coach.

    Goal resolution order:
      1. state["user_goal"]  — set by orchestrator or a previous turn
      2. Current user message — extracted via regex/keyword matching
      3. Conversation history — scanned for previously stated goals
      4. Truly unknown        — only NOW do we ask for the goal
    """
    messages = state.get("messages", [])
    user_message = _get_latest_user_message(messages)

    # ── Fast path: small talk ──────────────────────────────────────────
    if _is_small_talk(user_message):
        text = _small_talk_reply()
        return {
            "messages": [AIMessage(content=text, name="financial_coach")],
            "final_response": text,
        }

    # ── Resolve goal — 4-level priority ───────────────────────────────
    user_goal = (state.get("user_goal") or "").strip()

    if not user_goal:
        # Try current message first
        user_goal = _extract_goal_from_message(user_message) or ""

    if not user_goal:
        # Try conversation history
        user_goal = _extract_goal_from_history(messages) or ""

    missing_goal = not user_goal

    # ── Build spending context ─────────────────────────────────────────
    sensitive_note = state.get("sensitive_context", "") or ""
    validation_notes = state.get("validation_notes", []) or []
    transactions = state.get("transactions", []) or []

    budget_summary = state.get("budget_summary") or {}
    if not budget_summary and transactions:
        budget_summary = summarize_locally(transactions)

    context_lines = []
    if transactions:
        context_lines.append(f"- Transactions loaded: {len(transactions)}")
        if budget_summary.get("total_spent"):
            context_lines.append(
                f"- Total spent: ₹{budget_summary['total_spent']:,} "
                f"across {budget_summary.get('transaction_count', len(transactions))} transactions"
            )
        if budget_summary.get("daily_avg"):
            context_lines.append(f"- Daily average: ₹{budget_summary['daily_avg']:,}")
        context_lines.append(f"- Top categories: {_build_category_snapshot(transactions)}")
    else:
        context_lines.append("- No transactions uploaded yet")

    context_block = "\n".join(context_lines)

    # ── Tone ───────────────────────────────────────────────────────────
    tone_instruction = (
        "Lead with one short empathetic line (max 12 words), then move to practical steps."
        if sensitive_note
        else "Open with one direct, confident line — no filler words."
    )

    data_instruction = (
        "Use the transaction data below. Reference actual numbers — daily avg, top category."
        if transactions
        else "No transaction data. Give advice based on their message and stated goal only."
    )

    # ── Prompt: two very different modes ──────────────────────────────
    if missing_goal:
        # ONLY ask for goal — no advice yet
        prompt = f"""You are a sharp personal finance coach for India.

The user said: "{user_message}"

You have NO idea what their financial goal is. Your ENTIRE response must be:
1. One sentence acknowledging what they asked.
2. One question asking their specific goal — give 3 examples in brackets like:
   (emergency fund, home down payment, early retirement)

Max 2 sentences total. No bullets. No advice. No tips. Just the question."""

    else:
        # Give actual, specific advice
        prompt = f"""You are a sharp, direct personal finance coach for India. Give specific advice, not generic tips.

User message: "{user_message}"
Inferred/stated goal: "{user_goal}"
Emotional context: {sensitive_note if sensitive_note else "none"}

Spending data:
{context_block}

Instructions:
- {tone_instruction}
- Every point must directly help them achieve: "{user_goal}"
- {data_instruction}
- Use Indian terms: SIP, FD, EMI, ELSS, Groww, Zerodha, CRED, Zepto. Format amounts as ₹X,XXX.
- Never start with "Certainly", "Sure", "Great question", "As a beginner", or "Of course".
- Never ask for their goal — you already know it. Never ask them to upload data again.
- Keep total response under 150 words.

Response structure:
1. One direct opening line referencing their actual question or goal.
2. 3 specific action bullets — each with a concrete step, ₹ amount if relevant, and an Indian app/method.
3. One closing line that is a SPECIFIC next step with a timeframe (e.g. "Start a ₹2,000 SIP on Groww this weekend").
   Never end with vague lines like "let me know" or "happy to help"."""

    response = llm.invoke([HumanMessage(content=prompt)])
    text = response.content if hasattr(response, "content") else str(response)

    # ── Persist the resolved goal back to state ────────────────────────
    update: dict = {
        "messages": [AIMessage(content=text, name="financial_coach")],
        "final_response": text,
    }
    if user_goal and not state.get("user_goal"):
        update["user_goal"] = user_goal

    return update