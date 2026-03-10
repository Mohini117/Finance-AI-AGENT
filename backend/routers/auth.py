"""
routers/auth.py
===============
Authentication endpoints using Supabase Auth.

HOW SUPABASE AUTH WORKS:
  Supabase handles all password hashing, token generation, and session management.
  Our backend just calls the Supabase SDK — we never touch raw passwords.

  Flow:
  1. User signs up → Supabase creates a user record + sends verification email
  2. User logs in → Supabase validates password → returns JWT access_token
  3. React stores the token in memory (not localStorage for security)
  4. Every API request includes: "Authorization: Bearer <token>"
  5. auth_middleware.py validates the token on every protected route

JWT TOKENS:
  - access_token: Short-lived (~1 hour). Used for API calls.
  - refresh_token: Long-lived. Used to get a new access_token silently.

PYDANTIC MODELS:
  FastAPI uses Pydantic models to automatically:
  - Parse and validate the request body JSON
  - Return 422 Unprocessable Entity if required fields are missing/wrong type
  - Generate OpenAPI documentation
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr

from services.supabase_client import supabase

logger = logging.getLogger("finance.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Request models ─────────────────────────────────────────────────────────────
# Pydantic validates these automatically. If email is not valid email format,
# FastAPI returns 422 before our code even runs.

class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/signup")
async def signup(body: SignupRequest):
    """
    Create a new user account in Supabase Auth.

    Supabase handles:
    - Password hashing (bcrypt)
    - Duplicate email detection
    - Email verification (if configured in Supabase dashboard)

    Returns: user_id and email on success.
    Raises 400 if signup fails (email already in use, weak password, etc.)
    """
    try:
        response = supabase.auth.sign_up({
            "email":    body.email,
            "password": body.password,
            "options": {
                "data": {"full_name": body.full_name}  # Stored in user_metadata
            },
        })

        if response.user is None:
            raise HTTPException(status_code=400, detail="Signup failed. Please try again.")

        return {
            "message": "Account created successfully. Please verify your email.",
            "user_id": response.user.id,
            "email":   response.user.email,
        }

    except HTTPException:
        raise
    except Exception as exc:
        # Log the real error for debugging, but don't expose internals to client
        logger.exception("[auth] signup failed email=%s", body.email)
        raise HTTPException(status_code=400, detail="Signup failed. Please check your details and try again.")


@router.post("/login")
async def login(body: LoginRequest):
    """
    Authenticate user and return JWT tokens.

    The React frontend stores these tokens and sends access_token
    in the Authorization header for all subsequent API calls.

    Returns: access_token, refresh_token, user info.
    Raises 401 on invalid credentials.
    Raises 500 on server/network errors (logged separately from auth failures).
    """
    try:
        response = supabase.auth.sign_in_with_password({
            "email":    body.email,
            "password": body.password,
        })

        if response.user is None or response.session is None:
            raise HTTPException(status_code=401, detail="Invalid email or password.")

        return {
            "access_token":  response.session.access_token,
            "refresh_token": response.session.refresh_token,
            "user_id":       response.user.id,
            "email":         response.user.email,
            "full_name":     response.user.user_metadata.get("full_name", ""),
        }

    except HTTPException:
        raise  # Re-raise auth failures as-is (401)

    except Exception as exc:
        # BUG FIX: Original caught all exceptions and returned "Invalid email or password"
        # which hid real server errors (DB down, network issue) from logs.
        # Now: log the real error, return a distinct 500 for server errors.
        logger.exception("[auth] login error (server-side) email=%s", body.email)
        raise HTTPException(
            status_code=500,
            detail="Login failed due to a server error. Please try again later."
        )


@router.post("/logout")
async def logout():
    """
    Sign out the current user (invalidates server-side session).

    Note: The client should also clear its stored tokens on logout,
    regardless of whether this endpoint succeeds.
    """
    try:
        supabase.auth.sign_out()
        return {"message": "Logged out successfully."}
    except Exception as exc:
        logger.warning("[auth] logout error (non-critical): %s", exc)
        # Logout failures are non-critical — client clears tokens anyway
        return {"message": "Logged out."}