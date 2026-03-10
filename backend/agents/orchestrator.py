"""
agents/orchestrator.py
======================
Routes every user query to the correct specialist agent.

AGENTS & THEIR JOB:
  ┌──────────────────┬────────────────────────────────────────────────────────┐
  │ expense_tracker  │ "Where is my money going?" — spending breakdown,       │
  │                  │ category analysis, top expenses from uploaded CSV       │
  ├──────────────────┼────────────────────────────────────────────────────────┤
  │ budget_analyst   │ "Is my spending healthy?" — budget verdict, projection, │
  │                  │ overspending detection, month-end forecast              │
  ├──────────────────┼────────────────────────────────────────────────────────┤
  │ savings_finder   │ "How do I spend less?" — cost-cutting tips, cheaper    │
  │                  │ alternatives, category-specific saving advice           │
  ├──────────────────┼────────────────────────────────────────────────────────┤
  │ financial_coach  │ "What should I do with my money?" — investment advice, │
  │                  │ SIP/FD/ELSS, salary planning, goal setting, emotional  │
  │                  │ support, general finance. Also handles greetings.       │
  └──────────────────┴────────────────────────────────────────────────────────┘

ROUTING STRATEGY (3 layers):
  1. routing_hint  — User explicitly requested an agent → trust it.
  2. Heuristic     — Ordered keyword matching (no LLM, zero latency).
  3. LLM fallback  — Ambiguous intent → LLM classifies with full context.

CRITICAL ORDERING RULE:
  Heuristics are checked in ORDER. More specific agents (expense_tracker,
  budget_analyst, savings_finder) come BEFORE financial_coach to prevent
  the general coach from swallowing specific queries.
"""

import logging
import re

from langchain_core.messages import HumanMessage
from graph.state import FinanceState
from models import get_llm

logger = logging.getLogger("finance.orchestrator")

llm = get_llm(temperature=0.0)

AVAILABLE_AGENTS = [
    "expense_tracker",
    "budget_analyst",
    "savings_finder",
    "financial_coach",
]

GREETING_TERMS = {
    "hi", "hello", "hey", "hii", "helo",
    "good morning", "good afternoon", "good evening", "good night",
    "how are you", "what's up", "sup",
    "who are you", "what can you do",
    "thanks", "thank you", "ok", "okay", "cool",
}

# ── Heuristic routes — ORDERED LIST, first match wins ─────────────────────────
# Rule: MORE SPECIFIC agents first, GENERAL agents last.
HEURISTIC_ROUTES = [

    # ── expense_tracker ───────────────────────────────────────────────
    # Trigger: user wants to SEE or UNDERSTAND their spending data
    ("expense_tracker", [
        "show my spending", "show my expenses", "show my transactions",
        "view my spending", "view my expenses", "view transactions",
        "spending breakdown", "expense breakdown", "expense report",
        "analyze my spending", "analyze my expenses", "analyse my spending",
        "spending analysis", "expense analysis",
        "where am i spending", "where is my money going", "where does my money go",
        "where do i spend", "what do i spend on", "what am i spending on",
        "how much did i spend on", "spending on food", "spending on transport",
        "top expenses", "top spending", "highest expenses", "biggest expenses",
        "show me my categories", "spending categories", "expense categories",
        "categorize my", "categorise my",
        "transaction history", "transaction list", "recent transactions",
        "what are my transactions",
    ]),

    # ── budget_analyst ────────────────────────────────────────────────
    # Trigger: user wants a VERDICT on their overall budget health
    ("budget_analyst", [
        "check my budget", "budget check", "budget health", "budget report",
        "budget analysis", "budget score", "budget status",
        "how is my budget", "how's my budget", "am i on budget",
        "budget summary", "monthly budget",
        "am i overspending", "am i over spending", "overspending",
        "over budget", "exceeding budget", "spending too much",
        "is my spending healthy", "is my spending okay", "is my spending normal",
        "monthly projection", "month end projection", "projected spend",
        "how much will i spend", "spending forecast", "end of month",
        "how much do i spend", "monthly spend", "monthly spending",
        "am i on track", "on track with", "am i saving enough",
        "spending vs goal", "compare to goal",
    ]),

    # ── savings_finder ────────────────────────────────────────────────
    # Trigger: user wants TIPS to spend less or save more
    ("savings_finder", [
        "save money", "save more money", "i want to save", "help me save",
        "how to save", "how can i save", "ways to save", "tips to save",
        "money saving", "saving tips", "saving ideas", "saving advice",
        "finance tips", "finance related tips", "financial tips",
        "tips on finance", "tips about finance", "tips on saving",
        "tell me about finance", "give me finance tips",
        "give me tips", "give me advice on saving",
        "cut down", "cut expenses", "cut costs", "cut my spending",
        "cut my bills", "reduce expenses", "reduce my expenses",
        "reduce spending", "reduce my spending", "reduce costs",
        "lower my expenses", "lower my bills", "lower my spending",
        "spend less", "spend lesser", "spending less",
        "cheaper alternative", "cheaper option", "cheaper way",
        "cheapest way", "more affordable",
        "save on groceries", "save on food", "save on transport",
        "save on shopping", "save on subscriptions", "save on bills",
        "i want to save more", "need to save more", "start saving",
        "how do i save", "how should i save", "what can i cut",
        "where can i save", "where can i cut",
        "frugal", "frugal tips", "cashback tips", "discount tips",
        "cut my grocery", "cut my food", "reduce my monthly", "reduce my bill",
    ]),

    # ── financial_coach ───────────────────────────────────────────────
    # Trigger: investments, planning, general/emotional finance questions
    # NOTE: Must come LAST — broadest category
    ("financial_coach", [
        "invest", "investing", "investment", "investments",
        "sip", "mutual fund", "mutual funds", "mf",
        "fd", "fixed deposit", "recurring deposit", "rd",
        "stock", "stocks", "equity", "share market", "stock market",
        "elss", "nps", "ppf", "provident fund",
        "zerodha", "groww", "upstox",
        "emergency fund", "build emergency", "contingency fund",
        "financial goal", "financial plan", "plan my finances",
        "plan my money", "financial planning",
        "saving for", "save for", "save up for",
        "down payment", "buy a house", "buy a car", "buy a home",
        "retirement", "retire early", "financial freedom",
        "grow my money", "grow my wealth", "build wealth",
        "wealth management", "net worth", "passive income",
        "portfolio", "diversify",
        "plan my salary", "salary plan", "50/30/20", "allocate salary",
        "divide my salary", "split my salary", "salary budget",
        "monthly plan", "plan my income",
        "stressed about money", "worried about money", "anxious about",
        "money problems", "financial stress",
        "help me with money", "help me with finances",
        "financial advice", "money advice",
        "debt", "loan", "emi", "credit card debt", "pay off loan",
        "tax saving", "80c", "itr",
        "insurance", "term insurance", "health insurance",
    ]),
]


def _get_latest_user_message(messages: list) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return msg.content or ""
    return ""


def _heuristic_route(message: str) -> str:
    """Ordered keyword matching — first match wins. Returns "" if no match."""
    text = (message or "").lower().strip()
    if not text:
        return "financial_coach"

    # Greetings → financial_coach handles small talk
    if text in GREETING_TERMS or any(text.startswith(f"{g} ") for g in GREETING_TERMS):
        return "financial_coach"

    for agent_name, keywords in HEURISTIC_ROUTES:
        if any(kw in text for kw in keywords):
            return agent_name

    return ""


def _llm_route(user_message: str, has_transactions: bool, history_text: str) -> str:
    """LLM fallback for queries that slip past heuristics."""
    prompt = f"""You are a routing agent for a personal finance assistant.
Return ONLY one agent name — nothing else, no explanation, no punctuation.

AGENT DEFINITIONS:
- expense_tracker : User wants to SEE or UNDERSTAND their spending data.
                    "show my spending", "where am I spending", "expense breakdown",
                    "spending categories", "analyze my expenses", "how much did I spend on X"

- budget_analyst  : User wants a VERDICT on their overall budget health.
                    "check my budget", "am I overspending", "is my spending healthy",
                    "budget report", "monthly projection", "am I on track"

- savings_finder  : User wants TIPS to spend less or save more.
                    "how to save money", "tips to reduce expenses", "finance tips",
                    "money saving ideas", "cut my bills", "save on X", "cheaper alternatives"

- financial_coach : Investment advice, financial planning, or general money guidance.
                    "how to invest", "SIP/FD/ELSS", "emergency fund", "retirement",
                    "plan my salary", "saving for a house", "stressed about money", anything vague

EXAMPLES:
"tell me finance related tips"       → savings_finder
"give me some finance tips"          → savings_finder
"ways to save more money"            → savings_finder
"show my spending breakdown"         → expense_tracker
"what are my top expenses"           → expense_tracker
"how much did I spend on food"       → expense_tracker
"am I on budget this month"          → budget_analyst
"is my spending healthy"             → budget_analyst
"how to start investing in SIP"      → financial_coach
"I want to build an emergency fund"  → financial_coach
"I'm stressed about money"           → financial_coach
"saving for a down payment"          → financial_coach

User message: "{user_message}"
Has uploaded transaction data: {has_transactions}
Recent conversation:
{history_text}

Reply with ONLY one of: expense_tracker, budget_analyst, savings_finder, financial_coach"""

    response = llm.invoke([HumanMessage(content=prompt)])
    chosen = re.sub(r"[^a-z_]", "", response.content.strip().lower())
    return chosen if chosen in AVAILABLE_AGENTS else "financial_coach"


def orchestrator_agent(state: FinanceState) -> dict:
    """
    Classify intent and set next_agent in state.
    Never produces a user-visible response — only sets routing metadata.
    """
    messages = state.get("messages", [])
    user_message = _get_latest_user_message(messages)
    has_transactions = bool(state.get("transactions"))

    # Layer 1: Explicit routing hint
    routing_hint = (state.get("routing_hint") or "").strip()
    if routing_hint in AVAILABLE_AGENTS:
        logger.info("[orchestrator] hint → %s", routing_hint)
        return {"next_agent": routing_hint}

    # Layer 2: Heuristic keyword match
    heuristic_choice = _heuristic_route(user_message)
    if heuristic_choice:
        logger.info("[orchestrator] heuristic → %s | query='%s'", heuristic_choice, user_message)
        return {"next_agent": heuristic_choice}

    # Layer 3: LLM fallback
    history_lines = []
    for msg in messages[-6:]:
        role = "User" if isinstance(msg, HumanMessage) else "Assistant"
        content = (getattr(msg, "content", "") or "").strip()
        history_lines.append(f"{role}: {content[:200]}")
    history_text = "\n".join(history_lines)

    chosen_agent = _llm_route(user_message, has_transactions, history_text)
    logger.info("[orchestrator] LLM → %s | query='%s'", chosen_agent, user_message)

    return {"next_agent": chosen_agent}