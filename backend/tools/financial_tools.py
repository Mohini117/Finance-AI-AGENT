"""
tools/financial_tools.py
========================
LangChain tools that the Spending Planner agent can call during generation.

WHAT IS A LANGCHAIN TOOL?
  A tool is a Python function decorated with @tool. When bound to an LLM
  with llm.bind_tools(ALL_FINANCIAL_TOOLS), the LLM can choose to CALL
  these functions mid-conversation — exactly like how ChatGPT uses plugins.

  The LLM reads each tool's docstring to decide WHEN to call it.
  So docstrings must be clear, specific, and mention the trigger conditions.

THE ReAct PATTERN (how tools are used):
  Reason → Act → Observe → Reason again

  Example: User asks "If I invest Rs5,000/month for 10 years, how much will I have?"
  1. REASON: "User wants SIP projection — I should call calculate_sip_returns"
  2. ACT:    Call calculate_sip_returns(monthly_amount=5000, years=10)
  3. OBSERVE: Tool returns "Maturity value: Rs11,61,695"
  4. REASON: "Now I can give a complete answer using this exact figure"

TWO CATEGORIES:
  - Math tools: Pure Python calculations. Instant, always accurate, no API needed.
  - Search tools: Tavily web search. Used only for time-sensitive info (current rates).

RULE: Never use search for math. Never use math tools for current market data.
"""

from __future__ import annotations

import os

from langchain_core.tools import tool

try:
    from tavily import TavilyClient
except ImportError:
    TavilyClient = None


# ─────────────────────────────────────────────────────────────────────────────
# MATH TOOLS — Pure Python, no external API, always accurate
# ─────────────────────────────────────────────────────────────────────────────

@tool
def calculate_sip_returns(monthly_amount: float, years: int, annual_rate: float = 12.0) -> str:
    """
    Calculate SIP (Systematic Investment Plan) maturity value using the standard formula.

    Use this when the user asks:
    - "If I invest Rs5,000/month for 10 years, how much will I have?"
    - "What will my SIP be worth?"
    - "How much will my monthly savings grow to?"

    The default 12% annual rate reflects historical Indian equity mutual fund averages.
    For conservative (FD/debt), use 7%. For aggressive (small-cap), use 14-15%.
    """
    # Standard SIP future value formula:
    # FV = P × [(1 + r)^n - 1] / r × (1 + r)
    # where r = monthly rate, n = total months, P = monthly investment
    monthly_rate = annual_rate / 100.0 / 12.0
    n = years * 12

    if monthly_rate == 0:
        maturity = monthly_amount * n
    else:
        maturity = (
            monthly_amount
            * ((1 + monthly_rate) ** n - 1)
            / monthly_rate
            * (1 + monthly_rate)
        )

    total_invested = monthly_amount * n
    total_returns = maturity - total_invested

    return (
        f"SIP of Rs{monthly_amount:,.0f}/month for {years} years at {annual_rate}% annual return:\n"
        f"• Total invested: Rs{total_invested:,.0f}\n"
        f"• Estimated returns: Rs{total_returns:,.0f}\n"
        f"• Maturity value: Rs{maturity:,.0f}\n"
        f"(Based on {annual_rate}% annual return — adjust rate for your actual fund)"
    )


@tool
def calculate_emi(principal: float, annual_rate: float, tenure_months: int) -> str:
    """
    Calculate monthly EMI for any loan (home, car, personal, education).

    Use this when the user:
    - Wants to take a loan and asks "what will my monthly payment be?"
    - Mentions an EMI and wants to know the total cost
    - Is comparing loan options

    Formula: EMI = P × r × (1+r)^n / [(1+r)^n - 1]
    where P = principal, r = monthly rate, n = tenure in months
    """
    monthly_rate = annual_rate / 100.0 / 12.0

    if monthly_rate == 0:
        emi = principal / tenure_months
    else:
        emi = (
            principal
            * monthly_rate
            * (1 + monthly_rate) ** tenure_months
            / ((1 + monthly_rate) ** tenure_months - 1)
        )

    total_payment = emi * tenure_months
    total_interest = total_payment - principal

    return (
        f"Loan of Rs{principal:,.0f} at {annual_rate}% for {tenure_months} months:\n"
        f"• Monthly EMI: Rs{emi:,.0f}\n"
        f"• Total interest paid: Rs{total_interest:,.0f}\n"
        f"• Total amount paid: Rs{total_payment:,.0f}"
    )


@tool
def calculate_goal_savings(target_amount: float, months: int, current_savings: float = 0.0) -> str:
    """
    Calculate how much to save per month to reach a specific financial goal.

    Use this when the user has a specific goal with a deadline:
    - "I want to save Rs1 lakh for a trip in 8 months — how much per month?"
    - "I need Rs50,000 for a laptop in 6 months"
    - "Emergency fund of Rs3 lakh in 2 years — how do I get there?"
    """
    if months <= 0:
        return "Please specify a timeline greater than 0 months."

    remaining = max(target_amount - current_savings, 0.0)
    monthly_needed = remaining / months

    return (
        f"To reach Rs{target_amount:,.0f} in {months} months:\n"
        f"• Monthly savings needed: Rs{monthly_needed:,.0f}\n"
        f"• Already saved: Rs{current_savings:,.0f}\n"
        f"• Still needed: Rs{remaining:,.0f}"
    )


@tool
def calculate_inflation_impact(amount: float, years: int, inflation_rate: float = 6.0) -> str:
    """
    Show how inflation reduces purchasing power of money over time.

    Use this when:
    - User asks why they shouldn't keep money in a savings account
    - Explaining why long-term investing beats FD
    - User asks about planning for retirement or long-term goals
    - User mentions "my FD gives 7% — is that good?"

    India's average inflation is ~6%. For conservative planning use 7%.
    """
    future_cost = amount * (1 + inflation_rate / 100.0) ** years
    real_worth = amount / (1 + inflation_rate / 100.0) ** years
    purchasing_loss = future_cost - amount

    return (
        f"Rs{amount:,.0f} today with {inflation_rate}% inflation over {years} years:\n"
        f"• Same goods will cost: Rs{future_cost:,.0f}\n"
        f"• Real purchasing power today: Rs{real_worth:,.0f}\n"
        f"• Effective loss: Rs{purchasing_loss:,.0f}\n"
        f"→ This is why investing at returns above inflation beats keeping cash in FD."
    )


@tool
def calculate_emergency_fund(monthly_expenses: float) -> str:
    """
    Calculate the recommended emergency fund size for a user's expenses.

    Use this when:
    - User asks "how much should I keep as emergency fund?"
    - Building a first financial plan
    - User asks about financial safety net or job-loss protection

    Standard rule: 3 months minimum, 6 months recommended.
    Keep it in a liquid fund (not FD — needs to be accessible within 1 day).
    """
    if monthly_expenses <= 0:
        return "Please provide your monthly expense amount."

    min_fund = monthly_expenses * 3
    recommended = monthly_expenses * 6
    monthly_to_build = recommended / 12  # Build it in 1 year

    return (
        f"Emergency fund for Rs{monthly_expenses:,.0f}/month expenses:\n"
        f"• Minimum (3 months): Rs{min_fund:,.0f}\n"
        f"• Recommended (6 months): Rs{recommended:,.0f}\n"
        f"• To build in 1 year: save Rs{monthly_to_build:,.0f}/month\n"
        f"• Best place: Liquid mutual fund or sweep FD (accessible in <24 hours)\n"
        f"• Not in: Regular FD (premature withdrawal penalty loses interest)"
    )


@tool
def classify_spending_category(description: str) -> str:
    """
    Classify a transaction description into Needs / Wants / Savings.

    Use this when:
    - User asks "is [transaction] a need or a want?"
    - Helping categorize a specific purchase
    - User wants to know which budget bucket something falls under

    Returns a plain text category label: "Needs", "Wants", or "Savings/Investment"
    """
    desc_lower = (description or "").lower()

    # Check savings/investment first (highest priority — protect these)
    savings_keywords = [
        "mutual fund", "sip", "stock", "zerodha", "groww", "fd",
        "fixed deposit", "insurance", "ppf", "nps", "gold", "elss",
    ]
    for keyword in savings_keywords:
        if keyword in desc_lower:
            return (
                f"'{description}' → Savings/Investment\n"
                f"✅ This is a good habit — keep it up."
            )

    # Check essential needs
    needs_keywords = [
        "rent", "grocery", "groceries", "medicine", "hospital", "doctor",
        "electricity", "gas", "water", "emi", "loan", "metro", "bus",
        "petrol", "diesel", "fuel", "school", "college", "fees", "insurance",
    ]
    for keyword in needs_keywords:
        if keyword in desc_lower:
            return (
                f"'{description}' → Needs (Essential)\n"
                f"This is a fixed or essential cost."
            )

    # Check lifestyle wants
    wants_keywords = [
        "restaurant", "cafe", "zomato", "swiggy", "amazon", "flipkart",
        "netflix", "hotstar", "spotify", "movie", "theatre", "bar",
        "shopping", "clothes", "salon", "spa", "travel", "hotel",
        "uber", "ola", "swiggy", "blinkit",
    ]
    for keyword in wants_keywords:
        if keyword in desc_lower:
            return (
                f"'{description}' → Wants (Lifestyle)\n"
                f"Track this category carefully — it's where most overspending happens."
            )

    return (
        f"'{description}' → Uncategorized (likely Wants)\n"
        f"When unsure, treat it as a Want and track it in your discretionary budget."
    )


# ─────────────────────────────────────────────────────────────────────────────
# SEARCH TOOL — Uses Tavily API for current, time-sensitive information
#
# WHEN TO USE (examples):
#   - "What are the best liquid mutual funds right now?"
#   - "What is the current PPF interest rate?"
#   - "Are there any new government savings schemes?"
#
# WHEN NOT TO USE:
#   - Any math question → use the calculator tools above instead
#   - General financial concepts → LLM already knows these
# ─────────────────────────────────────────────────────────────────────────────

@tool
def search_investment_options(query: str) -> str:
    """
    Search the web for current Indian investment options, interest rates, and government schemes.

    Use ONLY for time-sensitive information that changes over time:
    - Current FD interest rates from banks
    - New government schemes (PPF, NPS, Sukanya Samriddhi updates)
    - Current mutual fund performance or category recommendations
    - Tax-saving investment options for the current financial year

    Keep queries specific (4-8 words): 'best liquid mutual funds India 2024'
    NOT 'investments' (too broad).

    Do NOT use this for math questions — use the calculator tools instead.
    """
    if not TavilyClient:
        return (
            "Web search is unavailable (Tavily package not installed). "
            "Based on general knowledge: consider HDFC Liquid Fund, SBI Liquid Fund, "
            "or Parag Parikh Conservative Hybrid for low-risk options."
        )

    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        return (
            "Web search is unavailable (TAVILY_API_KEY not set). "
            "For current rates, check: bankbazaar.com, valueresearchonline.com, or moneycontrol.com."
        )

    try:
        client = TavilyClient(api_key=api_key)
        results = client.search(
            query=f"{query} India 2024 personal finance",
            max_results=3,
            search_depth="basic",
            include_answer=True,
        )

        # Prefer the AI-generated answer if Tavily provides one
        if results.get("answer"):
            return f"Search result: {results['answer']}"

        # Fall back to top result snippets
        snippets = []
        for r in results.get("results", [])[:2]:
            snippets.append(f"• {r.get('title', '')}: {(r.get('content', '') or '')[:200]}")

        return "\n".join(snippets) if snippets else "No results found. Try a more specific query."

    except Exception as exc:
        return (
            f"Search failed ({type(exc).__name__}). "
            "For current rates, check: bankbazaar.com or valueresearchonline.com."
        )


# ─────────────────────────────────────────────────────────────────────────────
# EXPORTED TOOL LIST
#
# This list is passed to llm.bind_tools() in spending_planner_agent.py.
# The agent has access to ALL tools and picks whichever fits the user's question.
# Add new tools here to make them available to the planner agent.
# ─────────────────────────────────────────────────────────────────────────────

ALL_FINANCIAL_TOOLS = [
    calculate_sip_returns,
    calculate_emi,
    calculate_goal_savings,
    calculate_inflation_impact,
    calculate_emergency_fund,
    classify_spending_category,
    search_investment_options,   # Last — use search only after math tools are exhausted
]