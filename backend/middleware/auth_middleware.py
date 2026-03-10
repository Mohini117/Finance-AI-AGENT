"""
middleware/auth_middleware.py
=============================
FastAPI dependency that validates JWT tokens on every protected route.

HOW FASTAPI DEPENDENCIES WORK:
  Instead of repeating auth logic in every route, we write it ONCE here
  and inject it with `Depends(get_current_user)`.

  FastAPI calls get_current_user() automatically before the route handler.
  If it raises HTTPException, the route never runs.
  If it returns a user object, that user is passed to the route handler.

  Example usage in any router:
      @router.get("/my-data")
      async def my_route(user = Depends(get_current_user)):
          # user.id is the authenticated user's UUID
          return {"user_id": user.id}

TOKEN FLOW:
  1. React sends: "Authorization: Bearer eyJhbGc..."
  2. HTTPBearer extracts the token string automatically
  3. We pass it to supabase_admin.auth.get_user() for validation
  4. Supabase verifies the JWT signature and expiry
  5. If valid: returns the user object
  6. If expired/invalid: raises 401

WHY USE SUPABASE TO VALIDATE (not decode JWT manually)?
  - Supabase can check if the token was explicitly revoked (logout)
  - Handles token rotation automatically
  - One less crypto dependency on our side
"""

import logging

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from services.supabase_client import supabase_admin

logger = logging.getLogger("finance.auth")

# HTTPBearer automatically extracts "Bearer <token>" from the Authorization header.
# If the header is missing or malformed, FastAPI returns 403 before our code runs.
security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(security),
):
    """
    FastAPI dependency — validates the Bearer token and returns the user.

    Inject this into any route to protect it:
        @router.get("/protected")
        async def protected(user = Depends(get_current_user)):
            return user.id

    Returns:
        Supabase User object (has .id, .email, .user_metadata, etc.)

    Raises:
        HTTPException 401 — if token is missing, expired, or invalid.
    """
    token = credentials.credentials

    try:
        response = supabase_admin.auth.get_user(token)
        user = response.user

        if not user:
            raise HTTPException(status_code=401, detail="Invalid or expired token.")

        return user

    except HTTPException:
        raise  # Re-raise our own 401 as-is

    except Exception as exc:
        logger.warning("[auth] token validation failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=401,
            detail="Authentication failed. Please log in again."
        )