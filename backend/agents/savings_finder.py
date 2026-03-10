"""
agents/savings_finder.py
========================
Finds specific, actionable money-saving tips using web search (Tavily).

FIXES:
  1. Category detection now uses csv_parser's category field directly
     instead of matching only 5 hardcoded description keywords.
  2. Uses all transactions (not just first 10) for category aggregation.
  3. LLM query-generation only called when Tavily is available.
  4. synthesis prompt explicitly handles empty/failed search results.
  5. Tips parsing uses regex instead of fragile '---' splitting.
"""

import os
import re

from langchain_core.messages import AIMessage, HumanMessage
from graph.state import FinanceState
from models import get_llm

try:
    from tavily import TavilyClient
    tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY")) if os.getenv("TAVILY_API_KEY") else None
except ImportError:
    TavilyClient = None
    tavily = None

llm = get_llm(temperature=0.3)

# Description-based category fallback — covers both Indian and US merchants
_DESC_CATEGORY_MAP = [
    (["swiggy", "zomato", "foodpanda", "grubhub", "doordash", "ubereats",
      "food", "restaurant", "cafe", "mcdonald", "kfc", "dominos", "pizza",
      "burger", "chipotle", "panera", "starbucks", "dunkin"],        "Food & Dining"),
    (["uber", "ola", "lyft", "rapido", "cab", "metro", "irctc", "redbus",
      "petrol", "diesel", "fuel", "shell", "exxon", "bp", "chevron",
      "gas station", "parking", "fastag", "airline", "indigo"],       "Transportation"),
    (["amazon", "flipkart", "myntra", "ajio", "ebay", "walmart", "target",
      "nike", "adidas", "zara", "h&m", "best buy", "meesho", "nykaa"],  "Shopping"),
    (["netflix", "spotify", "amazon prime", "hotstar", "disney", "youtube",
      "hulu", "steam", "pvr", "inox", "cinema", "gaming"],             "Entertainment"),
    (["grocery", "supermarket", "bigbasket", "blinkit", "zepto", "dmart",
      "whole foods", "trader joe", "kroger", "costco", "publix"],      "Groceries"),
    (["hospital", "clinic", "doctor", "medical", "pharmacy", "cvs",
      "walgreens", "apollo", "1mg", "netmeds", "gym", "fitness"],      "Health & Medical"),
    (["electricity", "water bill", "broadband", "internet", "airtel",
      "jio", "at&t", "verizon", "comcast", "mobile bill", "pg&e"],     "Utilities & Bills"),
    (["hotel", "airbnb", "oyo", "marriott", "hilton", "makemytrip",
      "booking.com", "expedia", "travel", "resort"],                   "Travel & Hotels"),
    (["subscription", "membership", "adobe", "microsoft", "google one",
      "icloud", "dropbox", "notion", "slack", "zoom"],                 "Subscriptions"),
]

_SEARCH_ANGLE = {
    "Food & Dining":     "reduce food delivery restaurant spending India 2025",
    "Shopping":          "stop impulse buying online shopping India 2025 tips",
    "Transportation":    "cut commute cab transport costs India 2025",
    "Entertainment":     "reduce OTT subscription entertainment costs India 2025",
    "Groceries":         "save money grocery shopping India 2025 tips",
    "Health & Medical":  "reduce medical pharmacy bills India 2025",
    "Utilities & Bills": "lower electricity internet mobile bill India 2025",
    "Travel & Hotels":   "budget travel hotel booking India 2025 save money",
    "Subscriptions":     "cancel unused subscriptions save money India 2025",
    "Finance & Banking": "reduce bank EMI charges India 2025",
}


def _get_top_categories(transactions: list, top_n: int = 3) -> list[tuple[str, float]]:
    """
    Aggregate spend by category across ALL transactions.
    Uses the category field from csv_parser first, falls back to description matching.
    """
    totals: dict[str, float] = {}
    for txn in transactions:
        try:
            amount = abs(float(txn.get("amount", 0) or 0))
        except (TypeError, ValueError):
            amount = 0.0
        if amount == 0:
            continue

        cat = (txn.get("category") or "").strip()
        if not cat or cat.lower() in ("other", "uncategorized", ""):
            desc = (txn.get("description") or "").lower()
            cat = "Other"
            for keywords, label in _DESC_CATEGORY_MAP:
                if any(k in desc for k in keywords):
                    cat = label
                    break

        totals[cat] = totals.get(cat, 0.0) + amount

    return sorted(totals.items(), key=lambda x: x[1], reverse=True)[:top_n]


def savings_finder_agent(state: FinanceState) -> dict:
    """
    Find tailored money-saving tips based on the user's actual spending patterns.

    Flow:
      1. Aggregate ALL transactions by category
      2. Build targeted search query from top category
      3. Search Tavily (only if available — skip LLM query-gen if not)
      4. Synthesise into 3 specific, actionable tips

    State read:  messages, transactions
    State write: messages, savings_tips
    """
    transactions = state.get("transactions", []) or []
    user_message = next(
        (m.content for m in reversed(state.get("messages", [])) if isinstance(m, HumanMessage)),
        "how can I save money"
    )

    # Step 1: Top categories from ALL transactions
    top_categories = _get_top_categories(transactions)

    if top_categories:
        top_cat, top_amount = top_categories[0]
        categories_summary = ", ".join(f"{c} (₹{a:,.0f})" for c, a in top_categories)
    else:
        top_cat, top_amount = "general expenses", 0
        categories_summary = "No transaction data"

    # Step 2: Search query
    search_query = _SEARCH_ANGLE.get(top_cat, f"save money {top_cat.lower()} India 2025")
    if len(top_categories) > 1:
        search_query += f" and {top_categories[1][0].lower()}"

    # Step 3: Tavily search (skip LLM query-gen if Tavily unavailable — saves latency)
    search_results = []
    search_source = "LLM general knowledge"

    if tavily:
        try:
            results = tavily.search(query=search_query, max_results=5, search_depth="advanced")
            for item in results.get("results", []):
                title = item.get("title", "")
                content = (item.get("content", "") or "")[:400]
                url = item.get("url", "")
                if title and content:
                    search_results.append(f"[{title}]({url})\n{content}")
            search_source = f"Web search: '{search_query}'"
        except Exception as exc:
            search_source = f"LLM general knowledge (search failed: {exc})"

    # Step 4: Synthesise tips
    if search_results:
        search_block = "\n\n---\n".join(search_results)
        search_instruction = "Ground your tips in the search results below. Reference specific apps, offers, or strategies mentioned."
    else:
        search_block = ""
        search_instruction = (
            "No web results available. Use your general knowledge of India's financial apps and services. "
            "Be specific: name exact apps (CRED, Zepto, Swiggy One, IRCTC, Jio, Groww) and ₹ estimates."
        )

    data_context = (
        f"User's top spending categories: {categories_summary}\n"
        f"Biggest category: {top_cat} — ₹{top_amount:,.0f} total"
        if transactions else
        "No transaction data uploaded — give general India-specific saving tips."
    )

    prompt = f"""You are a savings advisor for India. Give 3 SPECIFIC, actionable money-saving tips.

User asked: "{user_message}"
{data_context}

{search_instruction}
{"Search results:" + chr(10) + search_block if search_block else ""}

RULES:
- Tips must be about reducing spend on: {top_cat}
- Name exact Indian apps, websites, or methods
- Give realistic ₹ savings per month based on ₹{top_amount:,.0f} total spend
- Start directly with Tip 1 — no intro paragraph

FORMAT EXACTLY LIKE THIS (keep the ### and ** markers):

### Tip 1: [Short specific title]
**What to do:** [One specific action — exact app or method]
**Saves:** [₹X,XXX–₹Y,YYY per month]
**How to start:** [The very first thing to do — one sentence]

### Tip 2: [Short specific title]
**What to do:** [action]
**Saves:** [₹ estimate]
**How to start:** [first step]

### Tip 3: [Short specific title]
**What to do:** [action]
**Saves:** [₹ estimate]
**How to start:** [first step]"""

    response = llm.invoke([HumanMessage(content=prompt)])

    # Parse tips using ### markers (more reliable than --- splitting)
    tip_blocks = re.split(r"(?=###\s+Tip\s+\d)", response.content.strip())
    tips = [t.strip() for t in tip_blocks if t.strip() and "Tip" in t]

    footer = f"\n\n*Based on your top spending: **{top_cat}** | Source: {search_source}*"
    full_response = response.content.strip() + footer

    return {
        "messages": [AIMessage(content=full_response, name="savings_finder")],
        "savings_tips": tips,
    }