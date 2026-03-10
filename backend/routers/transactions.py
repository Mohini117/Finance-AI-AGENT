"""
routers/transactions.py
=======================
Handles CSV upload and transaction retrieval.

ENDPOINTS:
  POST /transactions/upload  — Upload a bank statement CSV
  GET  /transactions/        — Get all transactions for the logged-in user

UPLOAD FLOW:
  1. Validate file is CSV and within size limit
  2. Save to a temp file (needed by pandas read_csv)
  3. Parse CSV using tools/csv_parser.py (handles all Indian bank formats)
  4. Delete old transactions for this user (replace, don't append)
  5. Insert new rows into Supabase

WHY REPLACE INSTEAD OF APPEND?
  Bank statement CSVs typically cover a month. If the user uploads
  January's statement, then February's, they'd have duplicate January
  data if we appended. Replace is simpler and avoids duplicates.
  In future, we could add date-range deduplication instead.

SECURITY:
  - Auth required via get_current_user dependency
  - File size limited to MAX_CSV_SIZE_MB
  - Only .csv extension accepted
  - Temp file cleaned up whether parsing succeeds or fails
"""

import os
import tempfile

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from middleware.auth_middleware import get_current_user
from services.supabase_client import supabase_admin
from tools.csv_parser import parse_csv

router = APIRouter(prefix="/transactions", tags=["transactions"])

# ── Config ─────────────────────────────────────────────────────────────────────
MAX_CSV_SIZE_MB = 10
MAX_CSV_BYTES = MAX_CSV_SIZE_MB * 1024 * 1024  # 10 MB hard limit


@router.post("/upload")
async def upload_transactions(
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    """
    Accept a bank statement CSV, parse it, and save to Supabase.

    Replaces any existing transactions for this user with the new upload.
    Validates file type, size, and that parsed data contains at least one row.

    Returns:
        {"message": str, "transaction_count": int}
    """
    # ── Validate file type ─────────────────────────────────────────────
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are accepted.")

    tmp_path = None
    try:
        # ── Read and validate file size ────────────────────────────────
        content = await file.read()

        # BUG FIX: No file size limit in original — user could upload 500MB files
        if len(content) > MAX_CSV_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Maximum size is {MAX_CSV_SIZE_MB}MB."
            )

        # ── Save to temp file for pandas ───────────────────────────────
        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        # ── Parse the CSV ──────────────────────────────────────────────
        # parse_csv handles multiple Indian bank formats automatically.
        # Raises ValueError with a clear message if columns can't be identified.
        try:
            transactions = parse_csv(tmp_path)
        except ValueError as parse_err:
            raise HTTPException(status_code=422, detail=str(parse_err))

        if not transactions:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No valid transactions found in the CSV. "
                    "Make sure the file has date and amount columns with actual data."
                )
            )

        # ── Save to Supabase ───────────────────────────────────────────
        # Delete existing data for this user first (replace strategy)
        supabase_admin.table("transactions").delete().eq("user_id", user.id).execute()

        # BUG FIX: Original used float(str(t.get("amount", 0)).replace(",", ""))
        # which crashes if amount is None (becomes float("None")).
        # Now uses the already-cleaned float from parse_csv which always returns a float.
        rows = []
        for txn in transactions:
            amount = txn.get("amount", 0)
            # parse_csv already returns floats, but be defensive
            try:
                amount_float = float(amount) if amount is not None else 0.0
            except (TypeError, ValueError):
                amount_float = 0.0

            rows.append({
                "user_id":     user.id,
                "date":        str(txn.get("date", ""))[:50],
                "description": str(txn.get("description", ""))[:200],
                "amount":      amount_float,
                "category":    str(txn.get("category", "")) or "Uncategorized",
            })

        # Insert in batches of 500 to avoid Supabase payload limits
        batch_size = 500
        for i in range(0, len(rows), batch_size):
            supabase_admin.table("transactions").insert(rows[i:i + batch_size]).execute()

        return {
            "message":          f"{len(rows)} transactions uploaded successfully.",
            "transaction_count": len(rows),
        }

    except HTTPException:
        raise  # Re-raise our own validation errors unchanged

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(exc)}")

    finally:
        # Always clean up the temp file — even if an error occurred
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@router.get("/")
async def get_transactions(user=Depends(get_current_user)):
    """
    Return all transactions for the logged-in user, ordered by date descending.

    Used by the Dashboard to display transaction history and feed data
    into the agent when the user asks analysis questions.

    Returns:
        {"transactions": [{"date": ..., "description": ..., "amount": ..., "category": ...}]}
    """
    try:
        result = (
            supabase_admin.table("transactions")
            .select("date, description, amount, category")
            .eq("user_id", user.id)
            .order("date", desc=True)
            .execute()
        )
        return {"transactions": result.data or []}

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))