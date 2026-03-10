"""
tools/csv_parser.py
===================
Parses ANY bank statement CSV into a clean, normalised list of transactions.

FLEXIBLE COLUMN DETECTION:
  Instead of exact string matching, this parser uses fuzzy/keyword matching
  so it works with virtually any CSV format — Indian banks, US banks, custom exports.

  Strategy:
  1. Normalize all column names (lowercase, strip whitespace, remove special chars)
  2. Score each column against known keyword patterns using substring matching
  3. Pick the best match for date, description, amount (debit/credit)
  4. Fall back to positional guessing if nothing matches
  5. Auto-categorize transactions using keyword rules on the description

OUTPUT FORMAT:
  [
    {"date": "2024-01-15", "description": "Swiggy Order", "amount": 450.0, "category": "Food & Dining"},
    ...
  ]
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

import pandas as pd


# ── Keyword sets for fuzzy column detection ────────────────────────────────────
# Each list contains substrings we look for (case-insensitive) in column names.
# More specific keywords score higher — order doesn't matter.

DATE_KEYWORDS    = ["date", "dt", "time", "posted", "booking", "txn", "trans", "value"]
DESC_KEYWORDS    = ["desc", "narr", "particular", "remark", "detail", "note",
                    "merchant", "payee", "beneficiary", "ref", "name", "memo"]
AMOUNT_KEYWORDS  = ["amount", "amt", "net", "sum", "total", "value"]
DEBIT_KEYWORDS   = ["debit", "dr", "withdraw", "paid", "payment", "expense", "spent", "out"]
CREDIT_KEYWORDS  = ["credit", "cr", "deposit", "received", "income", "in"]
BALANCE_KEYWORDS = ["balance", "bal", "avail", "closing", "opening"]


# ── Auto-categorization rules ─────────────────────────────────────────────────
# Maps keyword patterns (found in description) → category label.
# Checked in order — first match wins.

CATEGORY_RULES = [
    # Food & Dining
    (["swiggy", "zomato", "uber eat", "food", "restaurant", "cafe", "pizza",
      "burger", "mcdonalds", "kfc", "dominos", "starbucks", "dunkin",
      "chipotle", "panera", "grubhub", "doordash", "dining", "kitchen",
      "bistro", "diner", "canteen", "mess", "hotel restaurant"],
     "Food & Dining"),

    # Groceries
    (["grocery", "groceries", "supermarket", "bigbasket", "blinkit", "zepto",
      "dmart", "reliance fresh", "more retail", "walmart", "target", "kroger",
      "whole foods", "trader joe", "costco", "publix", "safeway", "aldi",
      "lidl", "market", "fruits", "vegetables", "provision"],
     "Groceries"),

    # Transportation
    (["uber", "ola", "lyft", "rapido", "cab", "taxi", "auto", "metro",
      "bus", "train", "irctc", "railway", "flight", "airline", "indigo",
      "air india", "spicejet", "petrol", "diesel", "fuel", "gas station",
      "shell", "bp oil", "exxon", "chevron", "parking", "toll", "fastag"],
     "Transportation"),

    # Shopping & Retail
    (["amazon", "flipkart", "myntra", "ajio", "meesho", "snapdeal", "ebay",
      "shopify", "etsy", "zara", "h&m", "nike", "adidas", "mall",
      "retail", "shop", "store", "purchase", "order", "best buy",
      "home depot", "ikea", "decathlon"],
     "Shopping"),

    # Entertainment
    (["netflix", "spotify", "amazon prime", "hotstar", "disney", "youtube",
      "prime video", "zee5", "sonyliv", "jiocinema", "apple tv", "hulu",
      "hbo", "cinema", "movie", "theatre", "pvr", "inox", "steam",
      "playstation", "xbox", "gaming", "concert", "event", "ticket"],
     "Entertainment"),

    # Health & Medical
    (["hospital", "clinic", "doctor", "medical", "pharmacy", "medicine",
      "pharmeasy", "1mg", "netmeds", "apollo", "health", "dental",
      "optician", "lab test", "diagnostic", "cvs", "walgreens",
      "gym", "fitness", "yoga", "wellness"],
     "Health & Medical"),

    # Utilities & Bills
    (["electricity", "water bill", "gas bill", "broadband", "internet",
      "wifi", "dth", "tata sky", "dish tv", "airtel", "jio", "bsnl",
      "vodafone", "vi mobile", "recharge", "mobile bill", "utility",
      "municipal", "maintenance", "rent", "pg", "at&t", "verizon",
      "comcast", "pg&e", "electric"],
     "Utilities & Bills"),

    # Education
    (["school", "college", "university", "tuition", "course", "udemy",
      "coursera", "byju", "unacademy", "vedantu", "fee", "admission",
      "exam", "books", "stationery", "library"],
     "Education"),

    # Finance & Banking
    (["emi", "loan", "insurance", "lic", "premium", "sip", "mutual fund",
      "zerodha", "groww", "upstox", "nse", "bse", "investment",
      "fd", "fixed deposit", "rd", "recurring", "bank charge",
      "interest", "penalty", "fine", "atm", "cash withdrawal",
      "transfer", "neft", "imps", "rtgs", "upi"],
     "Finance & Banking"),

    # Travel & Hotels
    (["hotel", "resort", "airbnb", "oyo", "makemytrip", "goibibo",
      "booking.com", "expedia", "trivago", "travel", "tour", "trip",
      "marriott", "hilton", "holiday"],
     "Travel & Hotels"),

    # Subscriptions
    (["subscription", "renewal", "annual plan", "monthly plan", "membership",
      "adobe", "microsoft", "google one", "icloud", "dropbox", "slack",
      "zoom", "notion", "figma"],
     "Subscriptions"),

    # Salary / Income (for credit entries)
    (["salary", "sal credit", "payroll", "wages", "stipend", "income",
      "dividend", "refund", "cashback", "reward"],
     "Income / Credit"),
]


def _normalize_col(name: str) -> str:
    """Lowercase, strip whitespace and special chars from a column name."""
    return re.sub(r"[^a-z0-9 ]", " ", str(name).lower().strip())


def _score_column(col_normalized: str, keywords: list[str]) -> float:
    """
    Score how well a column name matches a list of keywords.
    Returns a float 0–1+. Higher = better match.
    Exact full-word matches score highest.
    """
    score = 0.0
    for kw in keywords:
        if col_normalized.strip() == kw:
            # Exact match to full column name — highest confidence
            score += 3.0
        elif kw in col_normalized:
            # Substring match
            score += 1.0 + (len(kw) / 20)
        else:
            # Fuzzy match for typos
            ratio = SequenceMatcher(None, kw, col_normalized).ratio()
            if ratio > 0.75:
                score += ratio * 0.5
    return score


def _find_best_column(df_columns: list[str], keywords: list[str], exclude: set[str] | None = None) -> str | None:
    """
    Find the DataFrame column that best matches a set of keywords.
    Returns the actual column name or None if no confident match found.
    """
    exclude = exclude or set()
    best_col, best_score = None, 0.0

    for col in df_columns:
        if col in exclude:
            continue
        norm = _normalize_col(col)
        score = _score_column(norm, keywords)
        if score > best_score:
            best_score = score
            best_col = col

    # Require a minimum score threshold to avoid false positives
    return best_col if best_score >= 0.8 else None


def _clean_amount(value) -> float:
    """
    Convert messy amount strings to a clean float.
    Handles: "₹1,200.50", "Rs 1,200", "1,200", "(500)" (negative), None, NaN
    Returns 0.0 for anything unparseable.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    raw = re.sub(r"[₹$£€Rs,\s]", "", str(value)).strip()
    # Handle parenthetical negatives like (500)
    if raw.startswith("(") and raw.endswith(")"):
        raw = "-" + raw[1:-1]
    if not raw or raw in {"-", ".", "N/A", "NA", "n/a"}:
        return 0.0
    try:
        return abs(float(raw))
    except (ValueError, TypeError):
        return 0.0


def _auto_categorize(description: str) -> str:
    """
    Assign a spending category based on keywords in the description.
    Returns 'Other' if no rule matches.
    """
    desc_lower = description.lower()
    for keywords, category in CATEGORY_RULES:
        if any(kw in desc_lower for kw in keywords):
            return category
    return "Other"


def _detect_columns(df: pd.DataFrame) -> dict:
    """
    Detect which DataFrame columns map to date, description, amount, debit, credit.
    Uses fuzzy scoring so it works even with unusual column names.

    Returns a dict: {"date": col_or_None, "desc": col_or_None, ...}
    """
    cols = df.columns.tolist()
    used = set()

    date_col   = _find_best_column(cols, DATE_KEYWORDS)
    if date_col: used.add(date_col)

    desc_col   = _find_best_column(cols, DESC_KEYWORDS, exclude=used)
    if desc_col: used.add(desc_col)

    # Try specific amount first, then debit/credit split
    _tmp_debit  = _find_best_column(cols, DEBIT_KEYWORDS,  exclude=used)
    _tmp_credit = _find_best_column(cols, CREDIT_KEYWORDS, exclude=used)
    _pre_exclude = used | ({_tmp_debit} if _tmp_debit else set()) | ({_tmp_credit} if _tmp_credit else set())
    amount_col = _find_best_column(cols, AMOUNT_KEYWORDS, exclude=_pre_exclude)
    if amount_col: used.add(amount_col)

    debit_col  = _find_best_column(cols, DEBIT_KEYWORDS,  exclude=used)
    if debit_col: used.add(debit_col)

    credit_col = _find_best_column(cols, CREDIT_KEYWORDS, exclude=used)

    return {
        "date":   date_col,
        "desc":   desc_col,
        "amount": amount_col,
        "debit":  debit_col,
        "credit": credit_col,
    }


def _fallback_detect(df: pd.DataFrame) -> dict:
    """
    Last-resort column detection by data type and position.
    Used when fuzzy matching scores too low (very unusual column names).
    """
    cols = df.columns.tolist()
    result = {"date": None, "desc": None, "amount": None, "debit": None, "credit": None}

    for col in cols:
        series = df[col].dropna()
        if result["date"] is None:
            # Try to parse as date
            try:
                pd.to_datetime(series.head(5), infer_datetime_format=True, errors="raise")
                result["date"] = col
                continue
            except Exception:
                pass

        if result["amount"] is None:
            # Numeric column
            try:
                cleaned = series.head(10).apply(lambda x: _clean_amount(x))
                if cleaned.mean() > 0:
                    result["amount"] = col
                    continue
            except Exception:
                pass

        if result["desc"] is None and df[col].dtype == object:
            result["desc"] = col

    return result


CREDIT_COLUMNS = ["credit", "cr amount", "deposit amount", "deposit", "received"]


def parse_csv(filepath: str) -> list[dict]:
    """
    Read and normalise ANY bank statement CSV file.

    Strategy:
      1. Try multiple encodings
      2. Skip junk header rows (banks often add bank name / account info before actual headers)
      3. Detect columns via fuzzy keyword scoring
      4. Fall back to positional/type-based detection
      5. Clean amounts, filter zero/empty rows
      6. Auto-categorize every transaction

    Args:
        filepath: Absolute path to the CSV file.

    Returns:
        List of dicts: [{"date", "description", "amount", "category"}]

    Raises:
        ValueError: Only if the file is completely unreadable.
    """
    # ── Step 1: Load with encoding fallback ───────────────────────────
    df = None
    for encoding in ["utf-8", "utf-8-sig", "latin-1", "windows-1252", "cp1252"]:
        try:
            df = pd.read_csv(filepath, encoding=encoding, skipinitialspace=True, dtype=str)
            if not df.empty:
                break
        except Exception:
            continue

    if df is None or df.empty:
        raise ValueError("Could not read the CSV file. Make sure it's a valid CSV format.")

    # ── Step 2: Skip junk header rows ─────────────────────────────────
    # Many Indian bank CSVs have 2–5 rows of account info before the real headers.
    # We find the first row that looks like a proper header (has date/amount-like words).
    header_keywords = DATE_KEYWORDS + AMOUNT_KEYWORDS + DEBIT_KEYWORDS + ["narr", "desc", "particular"]

    # First check if the actual column headers already look valid
    col_text = " ".join(str(c).lower() for c in df.columns)
    header_match_score = sum(1 for kw in header_keywords if kw in col_text)

    real_header_row = None
    if header_match_score < 2:
        # Headers look like junk — scan rows for the real header
        for i, row in df.iterrows():
            row_text = " ".join(str(v).lower() for v in row.values)
            matches = sum(1 for kw in header_keywords if kw in row_text)
            if matches >= 2:
                real_header_row = i
                break

    if real_header_row is not None and real_header_row > 0:
        # Re-read using that row as the header
        try:
            df = pd.read_csv(
                filepath,
                encoding="utf-8",
                skipinitialspace=True,
                dtype=str,
                header=real_header_row,
            )
        except Exception:
            pass  # Keep original df if re-read fails

    # Normalize column names
    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(how="all")  # Drop fully empty rows

    # ── Step 3: Detect columns ─────────────────────────────────────────
    col_map = _detect_columns(df)

    # Fall back to positional detection if fuzzy matching found nothing useful
    if not col_map["date"] and not col_map["amount"] and not col_map["debit"]:
        col_map = _fallback_detect(df)

    # ── Step 4: Validate we have minimum required columns ─────────────
    if not col_map["date"]:
        raise ValueError(
            f"No date column found. Detected columns: {list(df.columns[:10])}. "
            "Expected something like: date, txn date, transaction date, posting date, etc."
        )

    if not col_map["amount"] and not col_map["debit"]:
        raise ValueError(
            f"No amount column found. Detected columns: {list(df.columns[:10])}. "
            "Expected something like: amount, debit, withdrawal, dr amount, etc."
        )

    # ── Step 5: Build output ───────────────────────────────────────────
    transactions = []

    for _, row in df.iterrows():
        # Date
        raw_date = row.get(col_map["date"], "") if col_map["date"] else ""
        if not raw_date or str(raw_date).strip().lower() in {"nan", "", "none", "date"}:
            continue

        # Description — try all text columns if primary not found
        desc = ""
        if col_map["desc"]:
            desc = str(row.get(col_map["desc"], "") or "").strip()
        if not desc:
            # Try to find any non-numeric column with meaningful text
            for col in df.columns:
                val = str(row.get(col, "")).strip()
                if val and val.lower() not in {"nan", "none"} and not re.match(r"^[\d.,₹$\s]+$", val):
                    desc = val
                    break

        # Amount
        if col_map["amount"]:
            amount = _clean_amount(row.get(col_map["amount"], 0))
        elif col_map["debit"] or col_map["credit"]:
            debit  = _clean_amount(row.get(col_map["debit"],  0)) if col_map["debit"]  else 0.0
            credit = _clean_amount(row.get(col_map["credit"], 0)) if col_map["credit"] else 0.0
            amount = debit if debit > 0 else (credit if credit > 0 else 0.0)
        else:
            amount = 0.0

        if amount == 0.0:
            continue

        # Category
        category = _auto_categorize(desc) if desc else "Other"

        transactions.append({
            "date":        str(raw_date).strip()[:50],
            "description": desc[:200],
            "amount":      round(amount, 2),
            "category":    category,
        })

    return transactions