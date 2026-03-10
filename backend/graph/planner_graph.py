"""
graph/planner_graph.py
======================
Dedicated LangGraph workflow for the Salary Spending Planner.

WHY A SEPARATE GRAPH FROM THE MAIN CHAT GRAPH?
  The spending planner is a STRUCTURED, multi-turn form-filling flow.
  It has a fixed sequence of questions (income → expenses → goals → risk)
  and must validate each answer before moving on. The main chat graph
  is open-ended and routes to any of 5 specialist agents. They have
  fundamentally different control flows — so they get separate graphs.

THE FULL WORKFLOW:
  ┌──────────────────────────────────────────────────────────┐
  │                    PLANNER GRAPH                         │
  │                                                          │
  │  START                                                   │
  │    │                                                     │
  │    ▼                                                     │
  │  [intake]  ←──────────────────────────┐                 │
  │    │ Ask the next question             │                 │
  │    │ (income/expenses/goals/risk)      │ retry           │
  │    ▼                                  │                 │
  │  [extract] ──── invalid answer ───────┘                 │
  │    │                                                     │
  │    │ valid answer                                        │
  │    ▼                                                     │
  │  [tools]                                                 │
  │    │ Run financial calculations                          │
  │    │ (SIP returns, EMI, goal savings, emergency fund)    │
  │    ▼                                                     │
  │  [plan_generator]  (only when all 4 fields collected)    │
  │    │ Build full SpendingPlan JSON                        │
  │    ▼                                                     │
  │   END                                                    │
  └──────────────────────────────────────────────────────────┘

STAGES (planner_stage in state):
  "ask_income"    → Ask for monthly take-home salary
  "ask_expenses"  → Ask for fixed monthly costs (rent, EMI, etc.)
  "ask_goals"     → Ask what they're saving for (or "no goals")
  "ask_risk"      → Ask investment risk preference
  "plan_ready"    → Plan delivered; follow-up questions handled by tools

MEMORY (via LangGraph checkpointer):
  All planner_* fields in FinanceState persist across turns automatically.
  The thread_id = "planner:{user_id}:{session_id}" keeps each user's
  planner memory separate from their main chat memory.

TOOLS USED (from tools/financial_tools.py):
  - calculate_sip_returns      → Project monthly savings growth
  - calculate_emi              → Compute EMI for any loan
  - calculate_goal_savings     → How much to save per month for a goal
  - calculate_emergency_fund   → Recommended emergency fund size
  - calculate_inflation_impact → Show why investing beats FD
  - search_investment_options  → Current rates/funds from the web
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph

from graph.memory import get_checkpointer
from graph.state import FinanceState, SpendingPlan
from models import get_llm
from tools.anonymizer import anonymize_transactions
from tools.financial_tools import ALL_FINANCIAL_TOOLS

logger = logging.getLogger("finance.flow")

# ── LLM instances ──────────────────────────────────────────────────────────────
# intake + extract use low temperature for consistent question phrasing.
# plan_generator uses 0.2 so the summary narrative sounds natural.
_llm = get_llm(temperature=0.1)
_llm_with_tools = get_llm(temperature=0.1).bind_tools(ALL_FINANCIAL_TOOLS)
_llm_plan = get_llm(temperature=0.2)

# ── Stage order ────────────────────────────────────────────────────────────────
STAGE_ORDER = ["ask_income", "ask_expenses", "ask_goals", "ask_risk", "plan_ready"]

# ── INR formatter ──────────────────────────────────────────────────────────────
def _inr(v: float) -> str:
    return f"Rs{v:,.0f}"


# =============================================================================
# HELPERS: Deterministic parsers (fast, no LLM cost)
# =============================================================================

def _to_amount(raw: str, unit: str = "") -> float:
    value = float(raw)
    unit = (unit or "").lower().strip()
    return value * {"k": 1_000, "thousand": 1_000, "l": 100_000,
                    "lakh": 100_000, "lac": 100_000, "cr": 10_000_000,
                    "crore": 10_000_000}.get(unit, 1)


def _parse_income(text: str) -> float:
    """Extract monthly income from free text. Returns 0.0 if not found."""
    norm = re.sub(r"(?<=\d),(?=\d)", "", (text or "").lower())

    # 12 LPA → monthly
    m = re.search(r"\b(\d+(?:\.\d+)?)\s*lpa\b", norm)
    if m:
        return float(m.group(1)) * 100_000 / 12

    # "X lakh/thousand per annum/annual/yearly"
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*(k|thousand|lakh|lac|l|cr|crore)?\s*"
        r"(?:per\s*annum|annum|annual|yearly|p\.a\.?)\b", norm)
    if m:
        return _to_amount(m.group(1), m.group(2) or "") / 12

    # "X to Y k" → average
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:to|-)\s*(\d+(?:\.\d+)?)\s*(k|l|lakh)?", norm)
    if m:
        lo = _to_amount(m.group(1), m.group(3) or "")
        hi = _to_amount(m.group(2), m.group(3) or "")
        return (lo + hi) / 2

    # Contextual: "salary 65k", "take home 65000"
    m = re.search(
        r"(?:salary|income|take.?home|in.?hand|earn|get|monthly)\D{0,10}"
        r"(\d+(?:\.\d+)?)\s*(k|thousand|lakh|lac|l|cr|crore)?", norm)
    if m:
        return _to_amount(m.group(1), m.group(2) or "")

    # Bare "65k" or "65000"
    m = re.search(r"\b(\d+(?:\.\d+)?)\s*(k|thousand|lakh|lac|l|cr|crore)\b", norm)
    if m:
        return _to_amount(m.group(1), m.group(2))

    # Bare number 4-9 digits
    m = re.search(r"\b(\d{4,9}(?:\.\d+)?)\b", norm)
    if m:
        return float(m.group(1))

    return 0.0


_EXPENSE_KEYS = {
    "rent":        ["rent"],
    "emi":         ["emi", "loan", "instalment", "installment"],
    "insurance":   ["insurance", "premium"],
    "utilities":   ["utility", "utilities", "electricity", "water", "gas", "internet", "wifi"],
    "school_fees": ["school", "fees", "tuition"],
    "groceries":   ["grocery", "groceries", "food"],
    "transport":   ["transport", "commute", "petrol", "diesel", "fuel", "metro", "bus"],
}

def _parse_expenses(text: str) -> dict:
    """Extract named expenses from text. Only runs during ask_expenses stage."""
    norm = (text or "").lower()
    out = {}
    for key, aliases in _EXPENSE_KEYS.items():
        for alias in aliases:
            m = re.search(
                rf"{re.escape(alias)}\D{{0,5}}(\d+(?:\.\d+)?)\s*(k|thousand|lakh|lac|l)?",
                norm)
            if m:
                amt = round(_to_amount(m.group(1), m.group(2) or ""), 0)
                if amt > 0:
                    out[key] = amt
                break
    return out


def _parse_risk(text: str) -> str:
    """Extract risk preference: conservative | moderate | aggressive"""
    low = (text or "").lower()
    if any(k in low for k in ["conservative", "low risk", "safe", "fd", "fixed deposit"]):
        return "conservative"
    if any(k in low for k in ["aggressive", "high risk", "stock", "equity", "adventurous"]):
        return "aggressive"
    if any(k in low for k in ["moderate", "balanced", "medium", "mix"]):
        return "moderate"
    return ""


def _parse_goals(text: str) -> tuple[dict, bool]:
    """Parse savings goals. Returns (goals_dict, explicitly_no_goals)."""
    low = (text or "").lower().strip()
    if any(p in low for p in ["no goal", "no specific", "none", "skip", "not now"]):
        return {}, True
    pattern = re.compile(
        r"([a-z][a-z\s]{1,40}?)\s*(?:-|:)?\s*"
        r"(\d+(?:\.\d+)?)\s*(k|thousand|lakh|lac|l)?\s*"
        r"(?:in|within)\s*(\d+)\s*(month|months|m|year|years|y)",
        re.IGNORECASE)
    goals = {}
    for m in pattern.finditer(text or ""):
        name = " ".join(m.group(1).split()).strip().title()
        target = _to_amount(m.group(2), m.group(3) or "")
        duration = int(m.group(4))
        months = duration * 12 if m.group(5).lower().startswith("y") else duration
        if name and target > 0 and months > 0:
            goals[name] = {"target": round(target, 0), "months": months}
    return goals, False


def _safe_json(text: str) -> dict:
    raw = re.sub(r"```(?:json)?", "", (text or "")).strip().strip("`")
    try:
        return json.loads(raw) if isinstance(json.loads(raw), dict) else {}
    except Exception:
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    return {}


def _normalize_pct(n: float, w: float, s: float) -> tuple[float, float, float]:
    vals = [max(n, 0.0), max(w, 0.0), max(s, 0.0)]
    total = sum(vals)
    if total <= 0:
        return 50.0, 30.0, 20.0
    scaled = [round(v * 100.0 / total, 1) for v in vals]
    scaled[0] = round(scaled[0] + round(100.0 - sum(scaled), 1), 1)
    return scaled[0], scaled[1], scaled[2]


# =============================================================================
# NODE 1: INTAKE — Asks the right question for the current stage
# =============================================================================

def intake_node(state: FinanceState) -> dict:
    """
    Ask the user the next required question based on the current planner stage.

    This node runs at the START of every turn (when stage != plan_ready) and
    also when extract_node determines an answer was invalid or incomplete.

    It uses the LLM to generate natural, varied questions (so it doesn't sound
    like a robot repeating the same phrasing). Falls back to hardcoded questions
    if the LLM fails.

    State read:  planner_stage, planner_income, planner_expenses, planner_goals,
                 planner_risk, messages
    State write: messages, final_response
    """
    stage = state.get("planner_stage") or "ask_income"
    income   = float(state.get("planner_income", 0) or 0)
    expenses = dict(state.get("planner_expenses", {}) or {})
    goals    = dict(state.get("planner_goals", {}) or {})
    risk     = str(state.get("planner_risk", "") or "")

    # Build a context summary so the LLM knows what's been collected
    known = {}
    if income > 0:
        known["monthly_income"] = _inr(income)
    if expenses:
        known["fixed_expenses"] = {k: _inr(v) for k, v in expenses.items()}
    if goals:
        known["goals"] = goals
    if risk:
        known["risk_profile"] = risk

    stage_instructions = {
        "ask_income": (
            "Ask for their monthly take-home salary after all deductions (tax, PF, etc.). "
            "Give ONE format example like '65,000' or '1.2 lakh'. "
            "Keep it friendly and under 25 words."
        ),
        "ask_expenses": (
            "Ask for their fixed monthly commitments. "
            "Examples: rent, EMI, insurance, utilities, school fees. "
            "Tell them to list multiple items like 'rent 12k, EMI 8k'. "
            "Under 35 words."
        ),
        "ask_goals": (
            "Ask what financial goals they're working towards and by when. "
            "Give format example: 'Emergency fund 1 lakh in 12 months'. "
            "Tell them they can say 'no goals' to skip. Under 40 words."
        ),
        "ask_risk": (
            "Ask how they prefer to invest their savings. "
            "Offer 3 clear options with brief descriptions: "
            "conservative (FDs, liquid funds — safe), "
            "moderate (mix of equity + debt), "
            "aggressive (mostly equity/index funds — higher returns, higher risk). "
            "Under 50 words."
        ),
    }

    fallbacks = {
        "ask_income":   "What's your monthly take-home salary after tax and PF? (e.g., 65,000 or 1.2 lakh)",
        "ask_expenses": "What are your fixed monthly costs? List them like: rent 12k, EMI 8k, insurance 2k, utilities 1k",
        "ask_goals":    "What are you saving for, and by when? (e.g., 'Emergency fund 1 lakh in 12 months') — or say 'no goals' to skip.",
        "ask_risk":     "How do you prefer to invest?\n• **Conservative** — FDs, liquid funds (safe, ~7%)\n• **Moderate** — mix of equity + debt (~10–12%)\n• **Aggressive** — mostly equity/index funds (~14%+)",
    }

    instruction = stage_instructions.get(stage, "Ask one relevant follow-up question.")

    # Build recent assistant messages to avoid repeating phrasing
    recent = [
        (getattr(m, "content", "") or "")[:100]
        for m in (state.get("messages", []) or [])[-6:]
        if isinstance(m, AIMessage)
    ]

    prompt = f"""You are a friendly financial advisor for India helping plan someone's salary.

Current stage: {stage}
Your task: {instruction}

What we know so far: {json.dumps(known, indent=2) if known else "Nothing yet."}

Recent questions asked (DO NOT repeat these phrasings):
{chr(10).join(recent) if recent else "None"}

Rules:
- Ask exactly ONE question.
- Be warm and conversational.
- Use Indian context (INR, lakh, crore, SIP, EMI, PF).
- Do NOT add greetings, preambles, or repeat what you know. Just ask the question.
"""

    try:
        resp = _llm.invoke([HumanMessage(content=prompt)])
        question = (getattr(resp, "content", "") or "").strip()
        if not question:
            question = fallbacks[stage]
    except Exception:
        question = fallbacks.get(stage, "Can you tell me more?")

    return {
        "messages": [AIMessage(content=question, name="spending_planner")],
        "final_response": question,
    }


# =============================================================================
# NODE 2: EXTRACT — Validates and parses the user's latest answer
# =============================================================================

def extract_node(state: FinanceState) -> dict:
    """
    Parse the user's latest message and update the planner profile.

    This node runs AFTER the user replies. It:
    1. Reads the current stage to know WHAT to parse (income/expenses/goals/risk)
    2. Uses deterministic regex parsers first (fast, accurate)
    3. Falls back to LLM extraction for edge cases the regex misses
    4. Validates the result — if invalid, sets a retry flag so intake re-asks
    5. Advances the stage if the answer is valid

    KEY DESIGN: Parsers are stage-gated. "rent 13k" NEVER gets parsed as income
    because the income parser only runs during ask_income stage.

    State read:  messages, planner_stage, planner_income, planner_expenses,
                 planner_goals, planner_risk
    State write: planner_income, planner_expenses, planner_goals, planner_risk,
                 planner_stage, _extract_valid (internal routing flag)
    """
    stage    = state.get("planner_stage") or "ask_income"
    income   = float(state.get("planner_income", 0) or 0)
    expenses = dict(state.get("planner_expenses", {}) or {})
    goals    = dict(state.get("planner_goals", {}) or {})
    risk     = str(state.get("planner_risk", "") or "")

    # Get the user's latest message
    user_msg = next(
        (m.content for m in reversed(state.get("messages", []))
         if isinstance(m, HumanMessage)), ""
    )

    # ── Ask LLM to extract structured data from user message ───────────────
    extract_prompt = f"""Extract financial planning data from this user message.
Return ONLY valid JSON. No markdown, no explanation.

Stage we are collecting: {stage}
User message: "{user_msg}"

Return this exact JSON (use null for fields not mentioned):
{{
  "income_monthly": <number or null>,
  "income_annual":  <number or null>,
  "expenses": {{"rent": <num or null>, "emi": <num or null>, "insurance": <num or null>,
               "utilities": <num or null>, "groceries": <num or null>,
               "transport": <num or null>, "other": <num or null>}},
  "goals": [{{"name": <str>, "target": <num>, "months": <num>}}],
  "goals_none": <true if user said no goals, else false>,
  "risk_profile": <"conservative"|"moderate"|"aggressive"|null>,
  "is_valid_answer": <true if the message actually answers the {stage} question, else false>
}}
"""
    llm_data = {}
    try:
        resp = _llm.invoke([HumanMessage(content=extract_prompt)])
        llm_data = _safe_json(getattr(resp, "content", "") or "")
    except Exception:
        pass

    is_valid = llm_data.get("is_valid_answer", True)  # Default trust the user
    updates = {"planner_stage": stage}

    # ── Stage-gated parsing ────────────────────────────────────────────────
    if stage == "ask_income":
        # Deterministic parser takes priority
        parsed = _parse_income(user_msg)
        llm_monthly = llm_data.get("income_monthly")
        llm_annual  = llm_data.get("income_annual")

        if parsed > 0:
            new_income = parsed
        elif isinstance(llm_monthly, (int, float)) and llm_monthly > 0:
            new_income = float(llm_monthly)
        elif isinstance(llm_annual, (int, float)) and llm_annual > 0:
            new_income = float(llm_annual) / 12.0
        else:
            new_income = 0.0

        if new_income > 0:
            updates["planner_income"] = round(new_income, 0)
            updates["planner_stage"] = "ask_expenses"
            updates["_extract_valid"] = True
            logger.info("[planner] income extracted: %s", _inr(new_income))
        else:
            # Could not parse income — re-ask
            updates["_extract_valid"] = False
            updates["_retry_hint"] = (
                "I couldn't quite catch your salary. "
                "Could you share it like this: '65,000' or '1.2 lakh'?"
            )

    elif stage == "ask_expenses":
        # Regex parser
        parsed = _parse_expenses(user_msg)
        # Merge with LLM-extracted expenses
        llm_expenses = llm_data.get("expenses") or {}
        merged = {}
        for k, v in (llm_expenses or {}).items():
            try:
                if v and float(v) > 0:
                    merged[k.lower().replace(" ", "_")] = round(float(v), 0)
            except Exception:
                pass
        merged.update(parsed)  # Regex wins on conflict

        if merged:
            updates["planner_expenses"] = merged
            updates["planner_stage"] = "ask_goals"
            updates["_extract_valid"] = True
            logger.info("[planner] expenses extracted: %s", merged)
        else:
            # No expenses detected
            updates["_extract_valid"] = False
            updates["_retry_hint"] = (
                "I didn't catch any expense amounts. "
                "Try: 'rent 12k, EMI 8k' or 'no fixed costs' if you have none."
            )

    elif stage == "ask_goals":
        parsed, no_goals = _parse_goals(user_msg)
        goals_none = llm_data.get("goals_none", False) or no_goals
        llm_goals_raw = llm_data.get("goals") or []

        # Merge LLM-extracted goals
        merged_goals = {}
        for g in (llm_goals_raw or []):
            if isinstance(g, dict) and g.get("name"):
                try:
                    t = float(g["target"] or 0)
                    m = int(g["months"] or 0)
                    if t > 0 and m > 0:
                        merged_goals[g["name"].strip().title()] = {"target": round(t, 0), "months": m}
                except Exception:
                    pass
        merged_goals.update(parsed)  # Regex wins

        if goals_none:
            updates["planner_goals"] = {}
        else:
            updates["planner_goals"] = {**goals, **merged_goals}

        updates["planner_stage"] = "ask_risk"
        updates["_extract_valid"] = True
        logger.info("[planner] goals extracted: %s", updates["planner_goals"])

    elif stage == "ask_risk":
        parsed = _parse_risk(user_msg)
        llm_risk = str(llm_data.get("risk_profile") or "").strip().lower()
        final_risk = parsed or (llm_risk if llm_risk in {"conservative", "moderate", "aggressive"} else "")

        if final_risk:
            updates["planner_risk"] = final_risk
            updates["planner_stage"] = "plan_ready"
            updates["_extract_valid"] = True
            logger.info("[planner] risk extracted: %s", final_risk)
        else:
            updates["_extract_valid"] = False
            updates["_retry_hint"] = (
                "Please choose one: **conservative**, **moderate**, or **aggressive**."
            )

    elif stage == "plan_ready":
        # Already have a plan — user is asking follow-up questions
        updates["_extract_valid"] = True

    return updates


# =============================================================================
# NODE 3: TOOLS — Runs financial tool calculations
# =============================================================================

def tools_node(state: FinanceState) -> dict:
    """
    Run LangChain financial tools to enrich the plan with real calculations.

    This node uses ReAct (Reason → Act → Observe) to:
    - Calculate SIP returns for the savings amount
    - Calculate EMI if the user mentioned loans
    - Compute emergency fund size
    - Calculate monthly savings needed for each goal
    - Search for current investment options if needed

    The tool results are stored in planner_tool_results in state so that
    plan_generator_node can use them when building the final plan text.

    State read:  planner_income, planner_expenses, planner_goals, planner_risk,
                 messages (for follow-up context)
    State write: planner_tool_results, messages
    """
    income   = float(state.get("planner_income", 0) or 0)
    expenses = dict(state.get("planner_expenses", {}) or {})
    goals    = dict(state.get("planner_goals", {}) or {})
    risk     = str(state.get("planner_risk", "") or "moderate")

    if income <= 0:
        return {"planner_tool_results": {}}

    # Estimate savings amount (rough 20% as a starting point for tool calls)
    total_expenses = sum(expenses.values())
    estimated_savings = max(income * 0.2, income - total_expenses - (income * 0.3))
    monthly_savings = round(max(estimated_savings, income * 0.1), 0)

    # Rate based on risk preference
    rate_map = {"conservative": 7.0, "moderate": 11.0, "aggressive": 14.0}
    annual_rate = rate_map.get(risk, 11.0)

    tool_map = {t.name: t for t in ALL_FINANCIAL_TOOLS}
    results = {}

    # ── Always calculate: SIP projection for savings amount ────────────
    try:
        results["sip_10yr"] = tool_map["calculate_sip_returns"].invoke({
            "monthly_amount": monthly_savings,
            "years": 10,
            "annual_rate": annual_rate,
        })
        results["sip_5yr"] = tool_map["calculate_sip_returns"].invoke({
            "monthly_amount": monthly_savings,
            "years": 5,
            "annual_rate": annual_rate,
        })
    except Exception as e:
        logger.warning("[planner] SIP tool failed: %s", e)

    # ── Emergency fund calculation ──────────────────────────────────────
    try:
        monthly_expenses = total_expenses if total_expenses > 0 else income * 0.6
        results["emergency_fund"] = tool_map["calculate_emergency_fund"].invoke({
            "monthly_expenses": monthly_expenses,
        })
    except Exception as e:
        logger.warning("[planner] Emergency fund tool failed: %s", e)

    # ── Per-goal savings needed ─────────────────────────────────────────
    goal_calcs = {}
    for goal_name, goal_data in (goals or {}).items():
        try:
            target = float(goal_data.get("target", 0) or 0)
            months = int(goal_data.get("months", 1) or 1)
            if target > 0 and months > 0:
                result = tool_map["calculate_goal_savings"].invoke({
                    "target_amount": target,
                    "months": months,
                    "current_savings": 0.0,
                })
                goal_calcs[goal_name] = result
        except Exception as e:
            logger.warning("[planner] Goal savings tool failed for %s: %s", goal_name, e)
    if goal_calcs:
        results["goal_calculations"] = goal_calcs

    # ── EMI check if user has loans ────────────────────────────────────
    emi_expense = expenses.get("emi", 0)
    if emi_expense > 0:
        # Just document the existing EMI, no recalculation needed
        results["emi_note"] = f"Existing EMI commitment: {_inr(emi_expense)}/month"

    # ── Inflation impact on savings ────────────────────────────────────
    try:
        results["inflation_impact"] = tool_map["calculate_inflation_impact"].invoke({
            "amount": monthly_savings * 12,
            "years": 10,
            "inflation_rate": 6.0,
        })
    except Exception as e:
        logger.warning("[planner] Inflation tool failed: %s", e)

    logger.info("[planner] tools completed, %d results", len(results))
    return {"planner_tool_results": results}


# =============================================================================
# NODE 4: PLAN GENERATOR — Builds the full SpendingPlan
# =============================================================================

def plan_generator_node(state: FinanceState) -> dict:
    """
    Generate the complete, structured SpendingPlan when all data is collected.

    This is the most complex node — it:
    1. Uses LLM to propose personalised % splits (needs/wants/savings)
    2. Computes all INR amounts deterministically from those percentages
    3. Builds a detailed category breakdown
    4. Allocates savings across goals proportionally
    5. Incorporates tool calculation results (SIP projections, goal targets)
    6. Compares vs actual spending if transactions were uploaded
    7. Generates behavioral nudges for overspending categories
    8. Formats everything as readable markdown for the chat + SpendingPlan JSON for charts

    WHY HYBRID (LLM % splits + deterministic math)?
    - The LLM is great at understanding context (e.g., high EMI → lower wants %)
    - But LLMs are unreliable at arithmetic → we compute all Rs amounts ourselves
    - This gives us natural-feeling plans with accurate numbers

    State read:  planner_income, planner_expenses, planner_goals, planner_risk,
                 planner_tool_results, transactions
    State write: messages, spending_plan, final_response, planner_stage
    """
    income   = float(state.get("planner_income", 0) or 0)
    expenses = dict(state.get("planner_expenses", {}) or {})
    goals    = dict(state.get("planner_goals", {}) or {})
    risk     = str(state.get("planner_risk", "") or "moderate")
    tool_results = dict(state.get("planner_tool_results", {}) or {})
    transactions = list(state.get("transactions", []) or [])

    total_expenses = sum(expenses.values())

    # ── Step 1: Ask LLM for personalised % splits ──────────────────────
    split_prompt = f"""You are an Indian financial planner. Propose a monthly budget split.
Return ONLY valid JSON. No explanation, no markdown.

User profile:
- Monthly income: {_inr(income)}
- Fixed expenses: {json.dumps({k: _inr(v) for k, v in expenses.items()})}
- Goals: {json.dumps(goals) if goals else "None specified"}
- Risk preference: {risk}
- Total fixed costs: {_inr(total_expenses)} ({round(total_expenses/income*100, 1) if income else 0}% of income)

Return:
{{
  "needs_pct": <number: % for essentials — must cover at least their fixed costs>,
  "wants_pct": <number: % for lifestyle spending>,
  "savings_pct": <number: % for savings/investments — needs+wants+savings = 100>,
  "category_breakdown": {{
    "Rent/EMI":      <monthly INR>,
    "Groceries":     <monthly INR>,
    "Utilities":     <monthly INR>,
    "Transport":     <monthly INR>,
    "Healthcare":    <monthly INR>,
    "Dining Out":    <monthly INR>,
    "Shopping":      <monthly INR>,
    "Entertainment": <monthly INR>
  }},
  "investment_allocation": {{
    "Emergency Fund (Liquid)": <monthly INR>,
    "Index Funds (SIP)":       <monthly INR>,
    "ELSS / Tax Saver":        <monthly INR>,
    "FD / Debt Fund":          <monthly INR>
  }},
  "monthly_action_items": ["<3-5 specific actionable steps for this month>"],
  "summary_line": "<One powerful sentence: what this plan achieves for this specific user>"
}}

Important constraints:
- needs_pct + wants_pct + savings_pct must equal exactly 100
- needs_pct must be at least {min(round(total_expenses/income*100 + 5, 0), 70) if income else 50}% to cover fixed costs
- Adjust investment_allocation based on {risk} risk preference
- Use Indian terms: SIP, ELSS, FD, liquid fund
"""

    raw = {}
    try:
        resp = _llm_plan.invoke([HumanMessage(content=split_prompt)])
        raw = _safe_json(getattr(resp, "content", "") or "")
    except Exception as e:
        logger.warning("[planner] split LLM failed: %s", e)

    # ── Step 2: Normalise percentages ─────────────────────────────────
    needs_pct, wants_pct, savings_pct = _normalize_pct(
        float(raw.get("needs_pct", 50) or 50),
        float(raw.get("wants_pct", 30) or 30),
        float(raw.get("savings_pct", 20) or 20),
    )

    # ── Step 3: Compute INR amounts ────────────────────────────────────
    needs_amount   = round(income * needs_pct   / 100, 0)
    wants_amount   = round(income * wants_pct   / 100, 0)
    savings_amount = round(income * savings_pct / 100, 0)

    # ── Step 4: Category breakdown ─────────────────────────────────────
    cat_breakdown: dict[str, float] = {}
    raw_cats = raw.get("category_breakdown") or {}
    for k, v in raw_cats.items():
        try:
            if float(v) >= 0:
                cat_breakdown[str(k)] = round(float(v), 0)
        except Exception:
            pass

    if not cat_breakdown:
        # Sensible fallback using user's actual expenses
        rent_emi = expenses.get("rent", 0) + expenses.get("emi", 0)
        cat_breakdown = {
            "Rent/EMI":      rent_emi or round(needs_amount * 0.50, 0),
            "Groceries":     expenses.get("groceries", round(needs_amount * 0.20, 0)),
            "Utilities":     expenses.get("utilities", round(needs_amount * 0.10, 0)),
            "Transport":     expenses.get("transport", round(needs_amount * 0.10, 0)),
            "Healthcare":    round(needs_amount * 0.05, 0),
            "Dining Out":    round(wants_amount * 0.40, 0),
            "Shopping":      round(wants_amount * 0.35, 0),
            "Entertainment": round(wants_amount * 0.25, 0),
        }

    # ── Step 5: Investment allocation ──────────────────────────────────
    invest_alloc: dict[str, float] = {}
    raw_invest = raw.get("investment_allocation") or {}
    for k, v in raw_invest.items():
        try:
            if float(v) >= 0:
                invest_alloc[str(k)] = round(float(v), 0)
        except Exception:
            pass

    if not invest_alloc:
        # Risk-based defaults
        if risk == "conservative":
            invest_alloc = {
                "Emergency Fund (Liquid)": round(savings_amount * 0.40, 0),
                "FD / Debt Fund":          round(savings_amount * 0.35, 0),
                "Index Funds (SIP)":       round(savings_amount * 0.15, 0),
                "ELSS / Tax Saver":        round(savings_amount * 0.10, 0),
            }
        elif risk == "aggressive":
            invest_alloc = {
                "Index Funds (SIP)":       round(savings_amount * 0.50, 0),
                "ELSS / Tax Saver":        round(savings_amount * 0.25, 0),
                "Emergency Fund (Liquid)": round(savings_amount * 0.15, 0),
                "FD / Debt Fund":          round(savings_amount * 0.10, 0),
            }
        else:  # moderate
            invest_alloc = {
                "Emergency Fund (Liquid)": round(savings_amount * 0.30, 0),
                "Index Funds (SIP)":       round(savings_amount * 0.40, 0),
                "ELSS / Tax Saver":        round(savings_amount * 0.20, 0),
                "FD / Debt Fund":          round(savings_amount * 0.10, 0),
            }

    # ── Step 6: Goal allocation ────────────────────────────────────────
    goals_alloc: dict[str, float] = {}
    if goals:
        monthly_needs = {}
        total_need = 0.0
        for name, g in goals.items():
            req = float(g.get("target", 0)) / max(int(g.get("months", 1)), 1)
            if req > 0:
                monthly_needs[name] = req
                total_need += req
        if total_need > 0:
            scale = min(1.0, savings_amount / total_need)
            goals_alloc = {n: round(v * scale, 0) for n, v in monthly_needs.items()}

    # ── Step 7: Compare vs actual spending (if CSV uploaded) ──────────
    vs_current: dict = {}
    nudges: list = []
    if transactions:
        actual: dict[str, float] = {}
        for txn in anonymize_transactions(transactions[:50]):
            cat = str(txn.get("category") or "Other")
            try:
                actual[cat] = actual.get(cat, 0) + abs(float(txn.get("amount", 0) or 0))
            except Exception:
                pass
        for cat, rec in cat_breakdown.items():
            if cat in actual:
                curr = round(actual[cat], 0)
                rec  = round(rec, 0)
                status = "over" if curr > rec else "under"
                vs_current[cat] = {"recommended": rec, "current": curr,
                                   "difference": round(rec - curr, 0), "status": status}
                if status == "over":
                    nudges.append(
                        f"You're spending **{_inr(curr - rec)} extra** on {cat}. "
                        f"Budget: {_inr(rec)}/month."
                    )

    # ── Step 8: Action items ───────────────────────────────────────────
    raw_actions = raw.get("monthly_action_items") or []
    actions = [str(x).strip() for x in raw_actions if str(x).strip()] if raw_actions else []
    if not actions:
        actions = [
            f"Auto-transfer {_inr(savings_amount)} to a separate savings account on salary day.",
            f"Set a UPI spending limit of {_inr(wants_amount)} for discretionary expenses.",
            "Enable spending notifications on your bank app to track in real-time.",
            "Review your budget every Sunday — redirect surplus to goals.",
        ]
        if expenses.get("emi", 0) > 0:
            actions.append(f"Your EMI of {_inr(expenses['emi'])} is already budgeted — don't add new loans.")

    # ── Step 9: Summary line ───────────────────────────────────────────
    summary = str(raw.get("summary_line", "")).strip() or (
        f"Save {_inr(savings_amount)}/month using a "
        f"{int(needs_pct)}/{int(wants_pct)}/{int(savings_pct)} "
        f"Needs/Wants/Savings split, tailored to your {risk} risk profile."
    )

    # ── Step 10: Package SpendingPlan ─────────────────────────────────
    plan: SpendingPlan = {
        "monthly_income":       round(income, 0),
        "needs_amount":         needs_amount,
        "wants_amount":         wants_amount,
        "savings_amount":       savings_amount,
        "needs_pct":            needs_pct,
        "wants_pct":            wants_pct,
        "savings_pct":          savings_pct,
        "category_breakdown":   cat_breakdown,
        "goals_allocation":     goals_alloc,
        "projection_6m":        round(savings_amount * 6, 0),
        "projection_12m":       round(savings_amount * 12, 0),
        "behavioral_nudges":    nudges,
        "vs_current":           vs_current,
        "plan_version":         5,
        "created_at":           datetime.now(timezone.utc).isoformat(),
        "investment_allocation": invest_alloc,
        "monthly_action_items":  actions,
        "summary_line":          summary,
    }

    # ── Step 11: Build readable chat response ─────────────────────────
    goal_lines = []
    if goals:
        for name, g in goals.items():
            monthly = goals_alloc.get(name, 0)
            target  = float(g.get("target", 0))
            months  = int(g.get("months", 1))
            shortfall = ""
            if monthly * months < target:
                gap = target - (monthly * months)
                shortfall = f" ⚠️ Shortfall: {_inr(gap)} — consider a part-time income boost."
            # Include tool calculation if available
            goal_calc = (tool_results.get("goal_calculations") or {}).get(name, "")
            goal_lines.append(
                f"- **{name}**: Save **{_inr(monthly)}/month** → "
                f"reach **{_inr(target)}** in {months} months{shortfall}"
            )
    else:
        ef = tool_results.get("emergency_fund", "")
        goal_lines = [
            "- No specific goal set.",
            f"- **Recommended**: Build a 3-month emergency fund of **{_inr(income * 3)}** first.",
            "- Then invest surplus in index funds via SIP.",
        ]

    # SIP projection section using tool results
    sip_section = ""
    if tool_results.get("sip_10yr"):
        rate_label = {"conservative": "7%", "moderate": "11%", "aggressive": "14%"}.get(risk, "11%")
        sip_data = tool_results["sip_10yr"]
        sip_section = (
            f"\n\n## 📈 What Your Savings Can Grow To\n"
            f"If you invest **{_inr(savings_amount)}/month** at {rate_label} annual return:\n"
            + "```\n" + sip_data + "\n```"
        )

    invest_lines = [f"- **{k}**: {_inr(float(v))}/month" for k, v in invest_alloc.items()]
    action_lines = [f"{i+1}. {a}" for i, a in enumerate(actions)]
    nudge_section = ""
    if nudges:
        nudge_section = "\n\n## 🔔 Spending Alerts (vs Your Past Data)\n" + "\n".join(f"- ⚠️ {n}" for n in nudges)

    plan_text = (
        f"## 💰 Your Monthly Salary Plan\n"
        f"_{summary}_\n\n"
        f"**Monthly Income: {_inr(income)}**\n\n"
        f"---\n\n"
        f"## 📊 Budget Split ({int(needs_pct)}/{int(wants_pct)}/{int(savings_pct)})\n"
        f"| Bucket | Amount | % of Income |\n"
        f"|--------|--------|-------------|\n"
        f"| 🏠 **Needs** (essentials) | **{_inr(needs_amount)}** | {needs_pct}% |\n"
        f"| 🎯 **Wants** (lifestyle) | **{_inr(wants_amount)}** | {wants_pct}% |\n"
        f"| 💎 **Savings** (future) | **{_inr(savings_amount)}** | {savings_pct}% |\n\n"
        f"---\n\n"
        f"## 🎯 Goal Plan\n"
        + "\n".join(goal_lines)
        + f"\n\n**Projected savings:** {_inr(plan['projection_6m'])} (6 months) · "
        f"{_inr(plan['projection_12m'])} (12 months)\n\n"
        f"---\n\n"
        f"## 📈 Where To Invest Monthly\n"
        + "\n".join(invest_lines)
        + sip_section
        + f"\n\n---\n\n"
        f"## ✅ This Month's Action Plan\n"
        + "\n".join(action_lines)
        + nudge_section
    )

    logger.info("[planner] plan generated income=%s needs=%.0f wants=%.0f savings=%.0f",
                _inr(income), needs_pct, wants_pct, savings_pct)

    return {
        "messages":       [AIMessage(content=plan_text, name="spending_planner")],
        "spending_plan":  plan,
        "final_response": plan_text,
        "planner_stage":  "plan_ready",
    }


# =============================================================================
# NODE 5: FOLLOWUP — Handles questions after the plan is delivered
# =============================================================================

def followup_node(state: FinanceState) -> dict:
    """
    Handle follow-up questions after a plan has been delivered.

    Examples:
    - "How do I start an SIP?"
    - "What if my rent goes up to 15k next month?"
    - "Can you recalculate if I save 5k more?"

    Uses tool-calling so the LLM can compute things like SIP projections,
    EMI changes, or updated goal timelines on-the-fly.

    State read:  messages, planner_income, planner_expenses, planner_goals,
                 planner_risk, spending_plan
    State write: messages, final_response
    """
    income   = float(state.get("planner_income", 0) or 0)
    expenses = dict(state.get("planner_expenses", {}) or {})
    goals    = dict(state.get("planner_goals", {}) or {})
    risk     = str(state.get("planner_risk", "") or "moderate")
    plan     = dict(state.get("spending_plan") or {})

    user_msg = next(
        (m.content for m in reversed(state.get("messages", []))
         if isinstance(m, HumanMessage)), ""
    )

    system = f"""You are a personal finance advisor for India. The user has already received their spending plan.
Answer their follow-up question using their profile below.

Profile:
- Monthly income: {_inr(income)}
- Fixed expenses: {json.dumps({k: _inr(v) for k, v in expenses.items()})}
- Goals: {json.dumps(goals) if goals else "None"}
- Risk profile: {risk}
- Current savings allocation: {_inr(float(plan.get('savings_amount', 0)))}/month

Rules:
- Be direct, practical, under 150 words unless a detailed calculation is needed.
- Use bullet points for multi-step advice.
- Use Indian terms: SIP, FD, EMI, UPI, ELSS, PF, PPF, NPS.
- Use Rs formatting.
- If you need to calculate something (SIP returns, EMI, goal timeline), use your tools.
- Don't re-explain the full plan unless explicitly asked.
"""

    conv = [SystemMessage(content=system), HumanMessage(content=user_msg)]
    reply = ""

    try:
        for _ in range(4):  # Max 4 rounds of tool calling (ReAct)
            resp = _llm_with_tools.invoke(conv)
            conv.append(resp)
            if resp.tool_calls:
                tool_map = {t.name: t for t in ALL_FINANCIAL_TOOLS}
                for call in resp.tool_calls:
                    try:
                        output = tool_map[call["name"]].invoke(call["args"])
                    except Exception as e:
                        output = f"Tool error: {e}"
                    conv.append(ToolMessage(content=str(output), tool_call_id=call["id"]))
            else:
                reply = getattr(resp, "content", "") or ""
                break
    except Exception as e:
        logger.warning("[planner] followup failed: %s", e)
        reply = "I can update your plan. Tell me what changed — salary, expenses, goals, or risk preference?"

    if not reply:
        reply = "Could you rephrase your question? I'm happy to recalculate or explain any part of your plan."

    return {
        "messages":       [AIMessage(content=reply, name="spending_planner")],
        "final_response": reply,
    }


# =============================================================================
# ROUTING FUNCTIONS
# =============================================================================

def route_after_extract(state: FinanceState) -> Literal["intake", "tools", "followup"]:
    """
    After extract_node runs, decide what to do next:

    - If extraction failed (invalid answer) → go back to intake to re-ask
    - If stage just became plan_ready → run tools then generate plan
    - If stage is plan_ready and we already have a plan → go to followup
    - Otherwise → go to intake to ask the next question
    """
    valid = state.get("_extract_valid", True)
    stage = state.get("planner_stage", "ask_income")

    if not valid:
        return "intake"  # Re-ask with a helpful hint

    if stage == "plan_ready":
        if state.get("spending_plan"):
            return "followup"  # Already have a plan — answer follow-up
        return "tools"  # Newly arrived at plan_ready — generate plan

    return "intake"  # Still collecting data — ask next question


def route_after_tools(state: FinanceState) -> Literal["plan_generator"]:
    """After tools run, always generate the plan."""
    return "plan_generator"





# =============================================================================
# GRAPH BUILDER
# =============================================================================

def _route_entry(state: FinanceState) -> str:
    """
    Decide where to enter the graph each turn.

    - Opening message ("Help me plan my salary", "hi", etc.)
      → intake: ask the first question (income)
    - All subsequent messages
      → extract: parse the user's answer to the previous question

    How we detect "opening message":
      stage == "ask_income" AND no AI messages yet means we haven't
      asked anything — so we should ask (intake), not parse (extract).
    """
    stage = state.get("planner_stage") or "ask_income"
    messages = state.get("messages", [])
    from langchain_core.messages import AIMessage as _AI
    has_ai_message = any(isinstance(m, _AI) for m in messages)

    if stage == "ask_income" and not has_ai_message:
        return "intake"   # First turn — ask income question
    return "extract"      # All other turns — parse user's reply


def build_planner_workflow():
    """
    Build and compile the spending planner LangGraph workflow.

    The graph has 5 nodes connected by conditional edges:
      intake → (user replies) → extract → tools → plan_generator → END
                                        ↘ intake (retry on invalid answer)
                                        ↘ followup (after plan exists)

    Memory is provided by the SQLite checkpointer (falls back to InMemory).
    Thread ID convention: "planner:{user_id}:{session_id}"

    Returns:
        A compiled LangGraph application ready for .invoke() / .stream()
    """
    graph = StateGraph(FinanceState)

    # ── Register nodes ─────────────────────────────────────────────────
    graph.add_node("intake",          intake_node)
    graph.add_node("extract",         extract_node)
    graph.add_node("tools",           tools_node)
    graph.add_node("plan_generator",  plan_generator_node)
    graph.add_node("followup",        followup_node)

    # ── Entry point ────────────────────────────────────────────────────
    graph.set_conditional_entry_point(
        _route_entry,
        {"intake": "intake", "extract": "extract"},
    )

    # ── Edges ──────────────────────────────────────────────────────────

    # intake → END (intake asks a question; user must reply next turn)
    graph.add_edge("intake", END)

    # extract → intake (re-ask) | tools (ready for plan) | followup (post-plan Q&A)
    graph.add_conditional_edges(
        "extract",
        route_after_extract,
        {
            "intake":         "intake",
            "tools":          "tools",
            "followup":       "followup",
        },
    )

    # tools → plan_generator (always)
    graph.add_edge("tools", "plan_generator")

    # plan_generator → END
    graph.add_edge("plan_generator", END)

    # followup → END
    graph.add_edge("followup", END)

    # ── Compile with memory ─────────────────────────────────────────────
    checkpointer = get_checkpointer()
    return graph.compile(checkpointer=checkpointer)