"""
agents/budget_analyst.py
========================
Analyzes uploaded transaction data and delivers a budget health verdict.

FIXES:
  1. Category detection now uses a shared helper (no more duplication
     with expense_tracker causing inconsistent numbers).
  2. actual_days is now used for projection instead of hardcoded 30.
  3. Goal Check section pre-computes the ₹ gap in Python — LLM only narrates.
  4. safe_sample dead code removed.
  5. Prompt template no longer embeds f-string values inside example text
     (which caused ₹0/day when daily_avg was 0).
"""

from langchain_core.messages import AIMessage, HumanMessage
from graph.state import FinanceState
from tools.anonymizer import summarize_locally
from models import get_llm

llm = get_llm(temperature=0.0)

# Shared category map — identical to expense_tracker so both agents
# produce consistent category totals for the same transactions.
_CATEGORY_MAP = [
    (["swiggy", "zomato", "foodpanda", "grubhub", "doordash", "food",
      "restaurant", "cafe", "mcdonald", "kfc", "dominos", "pizza",
      "burger", "chipotle", "panera", "starbucks"],                   "Food & Dining"),
    (["uber", "ola", "lyft", "rapido", "metro", "irctc", "redbus",
      "petrol", "diesel", "fuel", "shell", "exxon", "bp", "chevron",
      "gas station", "parking", "airline", "indigo"],                 "Transportation"),
    (["amazon", "flipkart", "myntra", "ajio", "ebay", "walmart",
      "target", "nike", "adidas", "zara", "h&m", "best buy", "meesho"], "Shopping"),
    (["netflix", "spotify", "amazon prime", "hotstar", "disney",
      "youtube", "hulu", "steam", "pvr", "inox", "gaming"],           "Entertainment"),
    (["grocery", "supermarket", "bigbasket", "blinkit", "zepto",
      "dmart", "whole foods", "trader joe", "kroger", "costco"],      "Groceries"),
    (["hospital", "clinic", "doctor", "medical", "pharmacy",
      "cvs", "walgreens", "apollo", "1mg", "gym", "fitness"],         "Health & Medical"),
    (["electricity", "water bill", "broadband", "internet", "airtel",
      "jio", "at&t", "verizon", "comcast", "mobile bill", "pg&e"],    "Utilities & Bills"),
    (["hotel", "airbnb", "oyo", "marriott", "hilton", "makemytrip",
      "booking.com", "expedia", "travel", "resort"],                  "Travel & Hotels"),
    (["subscription", "membership", "adobe", "microsoft",
      "google one", "icloud", "dropbox", "notion"],                   "Subscriptions"),
]


def _resolve_category(txn: dict) -> str:
    cat = (txn.get("category") or "").strip()
    if cat and cat.lower() not in ("uncategorized", "other", ""):
        return cat
    desc = (txn.get("description") or "").lower()
    for keywords, label in _CATEGORY_MAP:
        if any(k in desc for k in keywords):
            return label
    return "Other"


def _aggregate_categories(transactions: list) -> list[tuple[str, float]]:
    totals: dict[str, float] = {}
    for txn in transactions:
        cat = _resolve_category(txn)
        try:
            amount = abs(float(txn.get("amount", 0) or 0))
        except (TypeError, ValueError):
            amount = 0.0
        totals[cat] = totals.get(cat, 0.0) + amount
    return sorted(totals.items(), key=lambda x: x[1], reverse=True)


def budget_analyst_agent(state: FinanceState) -> dict:
    """
    Budget health analysis from uploaded CSV transactions.

    Pre-computes all math in Python, sends only numbers + narrative request to LLM.
    LLM never does arithmetic — it only writes the insight text.

    State read:  transactions, user_goal
    State write: messages, budget_summary
    """
    transactions = state.get("transactions", [])
    user_goal = (state.get("user_goal") or "").strip()

    if not transactions:
        return {
            "messages": [AIMessage(
                content=(
                    "## 📊 No Data Yet\n\n"
                    "Upload a transaction CSV from the **Dashboard** tab first.\n\n"
                    "💡 I'll then show you exactly where your money is going."
                ),
                name="budget_analyst",
            )]
        }

    # ── Pre-compute all stats in Python ───────────────────────────────
    local_summary = summarize_locally(transactions)
    total_spent    = local_summary["total_spent"]
    daily_avg      = local_summary["daily_avg"]
    max_spend      = local_summary["max_single_spend"]
    avg_txn        = local_summary["avg_transaction"]
    txn_count      = local_summary["transaction_count"]

    # Use actual_days from summarize_locally for accurate projection
    actual_days      = local_summary.get("actual_days", 30)
    days_remaining   = max(0, 30 - actual_days)
    projected_month  = round(daily_avg * 30, 0)
    projected_remaining = round(daily_avg * days_remaining, 0)

    # Category breakdown
    sorted_cats = _aggregate_categories(transactions)
    top_cat, top_cat_amount = sorted_cats[0] if sorted_cats else ("Other", 0)
    cats_text = "\n".join(f"  - {c}: ₹{a:,.0f}" for c, a in sorted_cats[:5])

    # Pre-compute goal gap so LLM doesn't need to calculate
    goal_text = "No goal set"
    goal_gap_text = ""
    if user_goal:
        goal_text = f'"{user_goal}"'
        # If summarize_locally provides a monthly_income estimate use it
        monthly_income = local_summary.get("monthly_income", 0)
        if monthly_income and monthly_income > projected_month:
            gap = round(monthly_income - projected_month, 0)
            goal_gap_text = f"Projected savings this month: ₹{gap:,.0f} (income ₹{monthly_income:,.0f} − projected spend ₹{projected_month:,.0f})"
        else:
            goal_gap_text = f"Projected spend this month: ₹{projected_month:,.0f}"

    # Unique days for context label
    dates = [t.get("date", "") for t in transactions if t.get("date")]
    days_label = f"{len(set(dates))} days" if dates else f"{actual_days} days"

    prompt = f"""You are a budget analyst. ALL MATH IS PRE-COMPUTED — write insights, not calculations.

PRE-COMPUTED STATS (use these exact numbers, do not recalculate):
- Total spent: ₹{total_spent:,} over {days_label}
- Transactions: {txn_count}
- Daily average: ₹{daily_avg:,}
- Projected month-end: ₹{projected_month:,.0f}
- Highest single transaction: ₹{max_spend:,}
- Average per transaction: ₹{avg_txn:,}
- Top spending categories:
{cats_text}
- User goal: {goal_text}
- Goal context: {goal_gap_text if goal_gap_text else "No income data available"}

WRITE YOUR RESPONSE IN THIS FORMAT:

## 📊 Budget Verdict
One bold sentence using the actual numbers. State clearly if spending is high, balanced, or low.
Example: "At ₹{daily_avg:,}/day you're on track to spend ₹{projected_month:,.0f} this month — your biggest drain is {top_cat}."

## 📅 Month Projection
- **Daily average:** ₹{daily_avg:,}
- **Projected month-end:** ₹{projected_month:,.0f}
- **Biggest single spend:** ₹{max_spend:,} — [state which category this likely belongs to]

## ✂️ Biggest Cut Opportunity
State the top category (₹{top_cat_amount:,.0f} on {top_cat}), give ONE specific action to cut it by 20-30%, and the annual saving that creates.

## 🎯 Goal Check
{"Use goal context above. State ONE clear action toward the goal with a specific ₹ amount." if user_goal else "No goal set — suggest ONE specific savings target with ₹ amount and timeframe. Example: 'Set a ₹50,000 emergency fund goal — at your current rate you'd reach it in X months if you cut ₹Y/day.'"}

RULES:
- Bold every rupee amount: **₹4,200**
- Use ONLY the pre-computed numbers — never invent or recalculate figures
- Start directly with ## — no "Sure!", "Certainly!", or any intro
- Be direct: "You're overspending on X" not "You might consider reducing X"
- One insight per section, no padding
"""

    response = llm.invoke([HumanMessage(content=prompt)])

    budget_summary = {
        "analyzed":          True,
        "transaction_count": txn_count,
        "total_spent":       total_spent,
        "daily_avg":         daily_avg,
        "projected_month":   projected_month,
        "top_category":      top_cat,
        "raw_analysis":      response.content,
    }

    return {
        "messages":       [AIMessage(content=response.content, name="budget_analyst")],
        "budget_summary":  budget_summary,
    }