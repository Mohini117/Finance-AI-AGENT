"""
graph/graph_builder.py
======================
Assembles the LangGraph StateGraph — the backbone of the multi-agent system.

WHAT IS LANGGRAPH?
  LangGraph is a framework for building stateful, multi-step AI workflows.
  You define NODES (agents) and EDGES (transitions between agents).
  Some edges are CONDITIONAL — the next node is chosen dynamically at runtime
  based on the current state (e.g., the guardrail_status field).

THE FULL GRAPH PIPELINE:
  User Input
      │
      ▼
  [input_validation]   ← checks for missing income, transactions, goal
      │
      ├─ BLOCK ──────────────────────────────────► END  (early exit with a clear error)
      │
      ▼
  [guardrails]          ← blocks harmful queries, flags emotional distress
      │
      ├─ BLOCK ──────────────────────────────────► END
      │
      ▼
  [orchestrator]        ← classifies intent and picks the right specialist
      │
      ├──► [expense_tracker]   → END
      ├──► [budget_analyst]    → END
      ├──► [financial_coach]   → END
      ├──► [savings_finder]    → END
      └──► [spending_planner]  → END

WHY THIS DESIGN?
  - Separation of concerns: each agent does ONE thing well.
  - The guardrails and validation nodes act as middleware — they run
    before every request, not inside each individual agent.
  - Checkpointing (SQLite/InMemory) allows multi-turn memory: the spending
    planner can ask income → expenses → goals across multiple messages.
"""

from langgraph.graph import END, StateGraph

from agents.budget_analyst import budget_analyst_agent
from agents.expense_tracker import expense_tracker_agent
from agents.financial_coach import financial_coach_agent
from agents.guardrails import guardrails_agent
from agents.input_validation import input_validation_agent
from agents.orchestrator import orchestrator_agent
from agents.savings_finder import savings_finder_agent
from graph.memory import get_checkpointer
from graph.state import FinanceState


# ─────────────────────────────────────────────
# CONDITIONAL EDGE FUNCTIONS
#
# LangGraph calls these after each node to decide
# which node runs next. They read state and return
# a string key that maps to the next node name.
# ─────────────────────────────────────────────

def route_after_validation(state: FinanceState) -> str:
    """
    After input_validation runs:
    - If validation blocked the request (e.g., asked for a plan but no income provided)
      → go to END so the error message is returned immediately.
    - Otherwise → continue to guardrails.
    """
    if state.get("validation_status", "OK") == "BLOCK":
        return END
    return "guardrails"


def route_after_guardrails(state: FinanceState) -> str:
    """
    After guardrails runs:
    - If the message contained illegal/harmful content → END immediately.
    - Otherwise → continue to orchestrator for intent classification.
    """
    status = state.get("guardrail_status", "ALLOW")
    if status in ("BLOCK", "REDIRECT"):
        return END
    return "orchestrator"


def route_to_agent(state: FinanceState) -> str:
    """
    After orchestrator runs:
    - The orchestrator sets `next_agent` to one of the 5 specialist names.
    - This function reads that value and routes accordingly.
    - Falls back to `financial_coach` if `next_agent` is missing/invalid.
    """
    return state.get("next_agent", "financial_coach")


# ─────────────────────────────────────────────
# MAIN GRAPH BUILDER
# ─────────────────────────────────────────────

def build_graph():
    """
    Builds and compiles the full multi-agent finance graph.

    Returns a compiled LangGraph application (callable like a function).
    The checkpointer enables persistent memory across conversation turns.

    Usage:
        app = build_graph()
        result = app.invoke(state, config={"configurable": {"thread_id": session_id}})
    """
    graph = StateGraph(FinanceState)

    # ── Register all nodes ─────────────────────────────────────────────
    # Each node is a Python function that takes FinanceState → returns partial dict.
    graph.add_node("input_validation", input_validation_agent)
    graph.add_node("guardrails", guardrails_agent)
    graph.add_node("orchestrator", orchestrator_agent)
    graph.add_node("expense_tracker", expense_tracker_agent)
    graph.add_node("budget_analyst", budget_analyst_agent)
    graph.add_node("financial_coach", financial_coach_agent)
    graph.add_node("savings_finder", savings_finder_agent)

    # ── Set entry point ────────────────────────────────────────────────
    # Every request always starts at input_validation.
    graph.set_entry_point("input_validation")

    # ── Add conditional edges ──────────────────────────────────────────
    # After input_validation: either END (BLOCK) or go to guardrails.
    graph.add_conditional_edges(
        "input_validation",
        route_after_validation,
        {
            "guardrails": "guardrails",
            END: END,
        },
    )

    # After guardrails: either END (BLOCK) or go to orchestrator.
    graph.add_conditional_edges(
        "guardrails",
        route_after_guardrails,
        {
            "orchestrator": "orchestrator",
            END: END,
        },
    )

    # After orchestrator: fan out to one of the 5 specialist agents.
    graph.add_conditional_edges(
        "orchestrator",
        route_to_agent,
        {
            "expense_tracker": "expense_tracker",
            "budget_analyst": "budget_analyst",
            "financial_coach": "financial_coach",
            "savings_finder": "savings_finder"
             
        },
    )

    # ── Terminal edges ─────────────────────────────────────────────────
    # All specialist agents end the graph after producing their response.
    graph.add_edge("expense_tracker", END)
    graph.add_edge("budget_analyst", END)
    graph.add_edge("financial_coach", END)
    graph.add_edge("savings_finder", END)

    # ── Compile with memory ─────────────────────────────────────────────
    # The checkpointer saves state to SQLite (or RAM fallback) between turns.
    # thread_id (session_id) is the key — each user session gets its own memory.
    return graph.compile(checkpointer=get_checkpointer())


def build_planner_graph():
    """
    Dedicated Spending Planner workflow for Chat 2 (/plan/* endpoints).

    5-node LangGraph workflow — replaces old spending_planner_agent:

      [intake] → user replies → [extract] → [tools] → [plan_generator] → END
                                           ↘ [intake]    retry on bad answer
                                           ↘ [followup]  post-plan Q&A

    Turn-by-turn:
      Turn 1: intake asks salary question
      Turn 2: extract parses income → intake asks expenses
      Turn 3: extract parses expenses → intake asks goals
      Turn 4: extract parses goals → intake asks risk preference
      Turn 5: extract parses risk → tools run → plan_generator delivers plan
      Turn 6+: followup handles Q&A about the plan

    Thread ID: "planner:{user_id}:{session_id}" — separate from Chat 1 memory.
    """
    from graph.planner_graph import build_planner_workflow
    return build_planner_workflow()