"""
services/supabase_client.py
============================
Initialises Supabase clients and exposes them as module-level singletons.

TWO CLIENTS — WHY?
  Supabase has a Row Level Security (RLS) system where database policies
  control what each user can read/write. Two different API keys unlock
  different levels of access:

  ┌───────────────┬────────────────────────────────────────────────────┐
  │ Client        │ Key Used           │ RLS Behaviour                 │
  ├───────────────┼────────────────────┼───────────────────────────────┤
  │ supabase      │ ANON_KEY           │ Respects RLS policies         │
  │               │ (public, safe to   │ Users can only see their own  │
  │               │  expose in app)    │ rows if policies are set up   │
  ├───────────────┼────────────────────┼───────────────────────────────┤
  │ supabase_admin│ SERVICE_ROLE_KEY   │ Bypasses ALL RLS policies     │
  │               │ (secret, server-   │ Can read/write any row        │
  │               │  side only!)       │ Needed for auth validation    │
  └───────────────┴────────────────────┴───────────────────────────────┘

USAGE GUIDELINES:
  - supabase_admin: Use in backend routes. NEVER expose SERVICE_ROLE_KEY to frontend.
  - supabase: Use for auth sign_up / sign_in (these use anon key internally).

ENV VARIABLES REQUIRED:
  SUPABASE_URL=https://xxxxx.supabase.co
  SUPABASE_ANON_KEY=eyJhbGc...
  SUPABASE_SERVICE_KEY=eyJhbGc...
"""

import os

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()


def _require_env(key: str) -> str:
    """
    Read a required environment variable.

    BUG FIX: Original called create_client(None, None) if env vars were missing,
    which produced a confusing Supabase SDK error deep in the stack.
    Now we raise a clear error at startup if any required var is missing.
    """
    value = os.getenv(key, "").strip()
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set. "
            "Add it to your .env file."
        )
    return value


def _make_anon_client() -> Client:
    """
    Anon client — respects Row Level Security.
    Use for user-facing auth operations (sign_up, sign_in).
    """
    return create_client(
        _require_env("SUPABASE_URL"),
        _require_env("SUPABASE_ANON_KEY"),
    )


def _make_admin_client() -> Client:
    """
    Service role client — bypasses RLS.
    Use for backend operations: inserting messages, reading any user's data.
    NEVER expose SUPABASE_SERVICE_KEY to the frontend.
    """
    return create_client(
        _require_env("SUPABASE_URL"),
        _require_env("SUPABASE_SERVICE_KEY"),
    )


# ── Module-level singletons ────────────────────────────────────────────────────
# Created once at import time. All routers share these instances.
# If env vars are missing, the app fails fast at startup with a clear error.
supabase: Client       = _make_anon_client()
supabase_admin: Client = _make_admin_client()