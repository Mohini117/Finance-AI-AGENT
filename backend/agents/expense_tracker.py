"""
agents/expense_tracker.py
=========================
Categorizes and visualizes spending from uploaded transaction data.

DIFFERENCE FROM BUDGET ANALYST:
  - budget_analyst  → "Is my overall spending healthy?" (verdict + projection)
  - expense_tracker → "Where exactly is my money going?" (breakdown + flagged items)

FIXES:
  1. Percentages pre-computed in Python — LLM no longer asked to calculate.
  2. Flag threshold computed from original transaction count, not anonymized count.
  3. Flagged transactions found in Python, not by LLM inspecting JSON.
  4. 15-row JSON sample removed — replaced with pre-computed flagged list.
  5. user_goal awareness added — breakdown references goal when set.
  6. Uses same _CATEGORY_MAP as budget_analyst for consistent numbers.
"""

from langchain_core.messages import AIMessage, HumanMessage
from graph.state import FinanceState
from models import get_llm

llm = get_llm(temperature=0.0)

# Shared category map — identical to budget_analyst for consistent results
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

_CATEGORY_EMOJI = {
    "Food & Dining":    "🍔",
    "Transportation":   "🚗",
    "Shopping":         "🛍️",
    "Entertainment":    "🎬",
    "Groceries":        "📦",
    "Health & Medical": "🏥",
    "Utilities & Bills":"💡",
    "Travel & Hotels":  "✈️",
    "Subscriptions":    "📱",
    "Other":            "🔵",
}


def _resolve_category(txn: dict) -> str:
    cat = (txn.get("category") or "").strip()
    if cat and cat.lower() not in ("uncategorized", "other", ""):
        return cat
    desc = (txn.get("description") or "").lower()
    for keywords, label in _CATEGORY_MAP:
        if any(k in desc for k in keywords):
            return label
    return "Other"


def expense_tracker_agent(state: FinanceState) -> dict:
    """
    Produce a detailed expense breakdown from uploaded CSV transactions.

    Pre-computes all math (totals, percentages, flagged transactions) in Python.
    LLM only writes the narrative text — never does arithmetic.

    State read:  transactions, user_goal
    State write: messages
    """
    transactions = state.get("transactions", [])
    user_goal = (state.get("user_goal") or "").strip()

    if not transactions:
        return {
            "messages": [AIMessage(
                content=(
                    "## 📂 No Transactions Found\n\n"
                    "Upload a CSV from the **Dashboard** tab to get started.\n\n"
                    "💡 **Tip:** Export your bank statement as CSV and upload it here."
                ),
                name="expense_tracker",
            )]
        }

    # ── Step 1: Resolve categories + compute stats in Python ──────────
    total_spent = 0.0
    category_totals: dict[str, float] = {}
    amounts = []

    for txn in transactions:
        try:
            amount = abs(float(txn.get("amount", 0) or 0))
        except (TypeError, ValueError):
            amount = 0.0
        if amount == 0:
            continue

        cat = _resolve_category(txn)
        category_totals[cat] = category_totals.get(cat, 0.0) + amount
        total_spent += amount
        amounts.append((amount, txn.get("description", "Unknown"), cat))

    if not amounts:
        return {
            "messages": [AIMessage(
                content="## 📂 No Valid Transactions\n\nNo transactions with non-zero amounts found.",
                name="expense_tracker",
            )]
        }

    # ── Step 2: Pre-compute percentages ───────────────────────────────
    sorted_cats = sorted(category_totals.items(), key=lambda x: x[1], reverse=True)
    top_3 = sorted_cats[:3]

    # Build formatted breakdown string with pre-computed percentages
    cat_lines = []
    for cat, amt in sorted_cats:
        pct = round(amt / total_spent * 100, 1)
        emoji = _CATEGORY_EMOJI.get(cat, "🔵")
        cat_lines.append(f"{emoji} {cat}: **₹{amt:,.0f}** ({pct}%)")
    cats_formatted = "\n".join(cat_lines)

    # ── Step 3: Pre-compute flagged transactions ───────────────────────
    avg_amount = total_spent / len(amounts)
    flag_threshold = avg_amount * 2
    flagged = sorted(
        [(amt, desc, cat) for amt, desc, cat in amounts if amt >= flag_threshold],
        reverse=True
    )[:5]  # Top 5 flagged only

    if flagged:
        flagged_text = "\n".join(
            f"- ₹{amt:,.0f} — {desc} ({cat})" for amt, desc, cat in flagged
        )
    else:
        flagged_text = "Nothing unusual spotted ✅"

    # ── Step 4: Goal context ───────────────────────────────────────────
    goal_section = ""
    if user_goal:
        top_cat, top_amt = sorted_cats[0]
        goal_section = (
            f"\nGoal context: User wants to '{user_goal}'. "
            f"Note how their {top_cat} spend of ₹{top_amt:,.0f} relates to this goal."
        )

    # ── Step 5: Build prompt ───────────────────────────────────────────
    top_3_text = ", ".join(f"{c} (₹{a:,.0f}, {round(a/total_spent*100,1)}%)" for c, a in top_3)

    prompt = f"""You are a sharp expense analyst. ALL MATH IS PRE-COMPUTED — write insights, not calculations.

PRE-COMPUTED DATA:
- Total spent: ₹{total_spent:,.0f}
- Transactions: {len(amounts)}
- Average transaction: ₹{avg_amount:,.0f}
- Flag threshold (2× average): ₹{flag_threshold:,.0f}

Category breakdown (pre-computed, use EXACTLY these numbers):
{cats_formatted}

Top 3: {top_3_text}

Flagged transactions (pre-identified — ≥ ₹{flag_threshold:,.0f}):
{flagged_text}
{goal_section}

WRITE YOUR RESPONSE IN THIS EXACT FORMAT:

## 📊 Spending Breakdown
[One sentence verdict — are they balanced or over-concentrated in one category?]

{cats_formatted}

## 🔍 Top 3 Categories
[One bullet per top-3 category. Format: "emoji Category — ₹amount (X%) → ONE actionable observation"]

## ⚠️ Flagged Transactions
{flagged_text}

## 💡 One Thing to Do This Week
[The single highest-impact cut based on the top category. One sentence, specific ₹ saving, name an Indian app.{" Connect to goal: " + user_goal if user_goal else ""}]

RULES:
- Copy the category breakdown EXACTLY as shown above — do not reformat or recalculate
- Bold every rupee: **₹4,200**
- Start directly with ## — no "Sure!" or "Certainly!"
- One insight per section, no padding
"""

    response = llm.invoke([HumanMessage(content=prompt)])

    return {
        "messages": [AIMessage(content=response.content, name="expense_tracker")]
    }