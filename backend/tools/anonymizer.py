"""
tools/anonymizer.py
===================
Strips Personally Identifiable Information (PII) from transactions
before ANY data is sent to an LLM API.

WHY THIS EXISTS — PRIVACY ARCHITECTURE:
  Raw bank transactions contain sensitive data:
  - Merchant names (reveals where you shop)
  - Reference numbers (unique identifiers)
  - Account numbers, UPI IDs, phone numbers
  - Email addresses in notes

  This module is the ONLY place where raw transaction data is processed.
  After passing through here, the data is safe to include in LLM prompts.
  This is a hard rule: never pass `state["transactions"]` directly to an LLM prompt.
  Always call anonymize_transactions() first.

TWO FUNCTIONS:
  anonymize_transactions() — returns cleaned rows (safe to show LLM as examples)
  summarize_locally()      — returns aggregate stats only (no individual rows)

DESIGN PRINCIPLE:
  "Pre-compute, then narrate" — agents send pre-computed numbers to the LLM,
  not raw data. The LLM interprets results; Python does the math.
"""

import re
from datetime import datetime

import pandas as pd


def anonymize_transactions(transactions: list) -> list:
    """
    Strip PII from transaction rows before LLM exposure.

    What gets REMOVED:
    - Long reference numbers (9+ digits): replaced with [REF]
    - Long alphanumeric IDs (12+ chars): replaced with [ID]
    - Email addresses: replaced with [EMAIL]
    - Phone numbers (10 digits): replaced with [PHONE]
    - Description is truncated to 60 chars

    What gets KEPT:
    - Date (needed for temporal analysis)
    - Amount (needed for all calculations)
    - Category (if already assigned)
    - Sanitized description (for category context)

    Args:
        transactions: List of raw transaction dicts from CSV or DB.

    Returns:
        List of sanitized transaction dicts, safe for LLM prompts.
    """
    anonymized = []

    for txn in transactions:
        clean: dict = {}

        # Keep date as-is — it's needed for trend analysis
        if "date" in txn:
            clean["date"] = txn["date"]

        # BUG FIX: was bare `except:` — now catches only numeric conversion errors
        if "amount" in txn:
            try:
                raw = str(txn["amount"]).replace(",", "").strip()
                clean["amount"] = float(raw)
            except (ValueError, TypeError):
                clean["amount"] = 0.0

        # Keep category if already assigned (from DB or previous categorization)
        if "category" in txn and txn["category"]:
            clean["category"] = str(txn["category"])

        # Sanitize description — remove PII patterns
        if "description" in txn:
            desc = str(txn["description"])
            desc = re.sub(r'\b\d{9,}\b', '[REF]', desc)         # Long reference numbers
            desc = re.sub(r'\b[A-Z0-9]{4,}\d[A-Z0-9]{6,}\b', '[ID]', desc)  # Alphanumeric IDs (must contain digits — spares pure merchant names like BIGBAZAAR)
            desc = re.sub(r'\S+@\S+', '[EMAIL]', desc)           # Email addresses
            desc = re.sub(r'\b\d{10}\b', '[PHONE]', desc)        # 10-digit phone numbers
            clean["description"] = desc[:60].strip()

        anonymized.append(clean)

    return anonymized


def summarize_locally(transactions: list) -> dict:
    """
    Compute aggregate spending statistics using Pandas — no LLM involved.

    WHY PANDAS HERE?
    Pandas handles messy data gracefully (mixed types, nulls, string amounts)
    without crashing, and is faster than manual Python loops for large datasets.

    This is the "local math" that agents always call before building an LLM prompt.
    The LLM sees ONLY these pre-computed numbers — never the raw transaction rows.

    Returns:
        {
            "total_spent":       float,  # Sum of all amounts
            "transaction_count": int,    # Number of rows
            "avg_transaction":   float,  # Mean per transaction
            "max_single_spend":  float,  # Largest single transaction
            "min_single_spend":  float,  # Smallest transaction
            "daily_avg":         float,  # Realistic daily average from actual date range
        }

    Returns empty dict if transactions list is empty.
    """
    if not transactions:
        return {}

    df = pd.DataFrame(transactions)

    # Robustly parse amounts — handles "1,200.50", "1200", 1200, None
    df["amount"] = pd.to_numeric(
        df["amount"].astype(str).str.replace(",", "", regex=False),
        errors="coerce",
    ).fillna(0.0).abs()  # Use absolute values — we treat all amounts as spending

    total_spent = float(df["amount"].sum())

    # BUG FIX: Daily avg was hardcoded to /30. Now uses actual date range.
    # If dates are available, compute span from earliest to latest date.
    # Minimum 1 day to avoid division by zero on single-day datasets.
    num_days = 30  # Sensible default
    if "date" in df.columns:
        try:
            dates = pd.to_datetime(df["date"], errors="coerce").dropna()
            if len(dates) >= 2:
                span = (dates.max() - dates.min()).days
                num_days = max(span, 1)
        except Exception:
            pass  # Fall back to default 30 days

    return {
        "total_spent":       round(total_spent, 2),
        "transaction_count": len(df),
        "avg_transaction":   round(float(df["amount"].mean()), 2),
        "max_single_spend":  round(float(df["amount"].max()), 2),
        "min_single_spend":  round(float(df["amount"].min()), 2),
        "daily_avg":         round(total_spent / num_days, 2),
    }