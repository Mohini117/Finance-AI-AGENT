"""
graph/state.py
==============
Defines the SHARED MEMORY (state) for the entire multi-agent system.

WHY THIS MATTERS:
  In LangGraph, every agent reads from and writes into a single shared
  TypedDict called the "state". Think of it as a whiteboard that all
  agents can see and update. When agent A finishes, its output is merged
  into the state, and the next agent picks up from there.

FLOW DIAGRAM:
  User Message
      │
      ▼
  FinanceState ──► input_validation ──► guardrails ──► orchestrator ──► specialist agent
                       (shared state flows through every node)
"""

from __future__ import annotations

import operator
from typing import Annotated, Optional, TypedDict


# ─────────────────────────────────────────────
# SpendingPlan: Structured output from the
# Spending Planner agent.
#
# Stored in state so the React/frontend can
# render pie charts and projections directly
# from structured data — no parsing needed.
# ─────────────────────────────────────────────
class SpendingPlan(TypedDict, total=False):
    """
    A fully computed monthly spending plan.

    Populated by spending_planner_agent and passed to the frontend
    for chart rendering (needs/wants/savings pie, goal progress bars, etc.)
    """

    # Core income and bucket amounts (in INR)
    monthly_income: float
    needs_amount: float        # Essentials: rent, food, utilities
    wants_amount: float        # Lifestyle: dining, shopping, entertainment
    savings_amount: float      # Investments + emergency fund

    # Percentage split (should sum to 100)
    needs_pct: float
    wants_pct: float
    savings_pct: float

    # Category-level breakdown: {"Rent/EMI": 15000, "Groceries": 5000, ...}
    category_breakdown: dict

    # Per-goal monthly allocation: {"Emergency Fund": 3000, "Trip": 1500}
    goals_allocation: dict

    # Savings projections
    projection_6m: float       # Estimated savings after 6 months
    projection_12m: float      # Estimated savings after 12 months

    # Behavioral tips: e.g. "You're overspending on dining by Rs2,000"
    behavioral_nudges: list

    # Comparison of recommended vs actual spending (if CSV uploaded)
    vs_current: dict

    # Metadata
    plan_version: int
    created_at: str

    # Where to invest savings: {"Index Funds": 5000, "Emergency Fund": 3000}
    investment_allocation: dict

    # Concrete tasks: ["Auto-transfer savings on salary day", ...]
    monthly_action_items: list

    # One-line plan summary: "Save Rs14,000/month using a 50/30/20 split."
    summary_line: str


# ─────────────────────────────────────────────
# FinanceState: The central shared state.
#
# Every agent function receives this as input
# and returns a PARTIAL dict that gets merged
# back in by LangGraph's `operator.add` annotation.
# ─────────────────────────────────────────────
class FinanceState(TypedDict):
    """
    The single shared state that flows through the entire agent graph.

    Notes:
    - `messages` uses `operator.add` so each agent APPENDS, not overwrites.
    - All other fields are plain overwrites (last writer wins).
    - Fields are optional at runtime — agents check with `.get()` defensively.
    """

    # ── Conversation history ───────────────────────────────────────────
    # Each agent adds AIMessage/HumanMessage objects to this list.
    # `operator.add` means: new list gets concatenated, not replaced.
    messages: Annotated[list, operator.add]

    # ── User data ──────────────────────────────────────────────────────
    # Parsed from uploaded CSV. Each row: {"date": ..., "amount": ..., "category": ...}
    transactions: list

    # Plain-text goal: "Save 1 lakh in 6 months for a laptop"
    user_goal: str

    # ── Agent outputs ──────────────────────────────────────────────────
    # Computed by budget_analyst: {"total_spent": 45000, "daily_avg": 1500, ...}
    budget_summary: dict

    # List of tip strings from savings_finder agent
    savings_tips: list

    # Fully structured plan from spending_planner_agent
    spending_plan: Optional[SpendingPlan]

    # ── Routing ────────────────────────────────────────────────────────
    # Set by orchestrator; consumed by the conditional edge router
    # Values: "expense_tracker" | "budget_analyst" | "financial_coach" |
    #         "savings_finder" | "spending_planner"
    next_agent: str

    # Optional override hint from input_validation (e.g., user said "use expense tracker")
    routing_hint: str

    # ── Response ───────────────────────────────────────────────────────
    # The final text response — readable by frontend without parsing messages[]
    final_response: str

    # ── Safety layer ───────────────────────────────────────────────────
    # Set by guardrails agent: "ALLOW" | "SENSITIVE" | "BLOCK"
    guardrail_status: str

    # Extra tone instruction for coach: "User may be stressed. Use empathetic tone."
    sensitive_context: str

    # ── Validation layer ───────────────────────────────────────────────
    # Set by input_validation: "OK" | "BLOCK"
    validation_status: str

    # Flags like ["missing_income", "missing_goal", "missing_transactions"]
    validation_notes: list

    # ── Spending Planner memory ────────────────────────────────────────
    # These persist across conversation turns via LangGraph's checkpointer
    # so the planner can build up the user profile incrementally.
    planner_income: float          # Monthly take-home salary in INR
    planner_expenses: dict         # {"rent": 12000, "emi": 5000, ...}
    planner_goals: dict            # {"Emergency Fund": {"target": 100000, "months": 12}}
    planner_custom_split: dict     # If user overrides default 50/30/20
    planner_risk: str              # "conservative" | "moderate" | "aggressive"
    planner_stage: str             # Conversation stage: "ask_income" → ... → "plan_ready"

    # ── Planner workflow fields (planner_graph.py) ─────────────────────
    # Tool results from tools_node: SIP projections, goal calculations etc.
    planner_tool_results: dict

    # Routing flag set by extract_node:
    #   True  → answer valid, advance stage
    #   False → answer invalid, re-ask via intake_node
    _extract_valid: bool

    # Hint message prepended by intake_node when re-asking after invalid answer
    _retry_hint: str