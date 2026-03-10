"""
models/llm.py
=============
Centralised LLM factory for the entire application.

WHY A FACTORY FUNCTION?
  - All agents call `get_llm()` — changing the LLM provider for the whole app
    requires changing exactly ONE file.
  - Temperature and provider can be tuned per agent without duplicating setup code.
  - Switching between Groq and Gemini is a single env variable change.

SUPPORTED PROVIDERS:
  ┌──────────┬────────────────────────────────────┬──────────────────────┐
  │ Provider │ Default Model                      │ Env Var              │
  ├──────────┼────────────────────────────────────┼──────────────────────┤
  │ groq     │ llama-3.1-8b-instant               │ GROQ_API_KEY         │
  │ gemini   │ gemini-2.5-flash                   │ GEMINI_API_KEY       │
  └──────────┴────────────────────────────────────┴──────────────────────┘

ENV VARIABLES:
  LLM_PROVIDER=groq       # or "gemini"
  GROQ_MODEL=llama-3.1-8b-instant
  GEMINI_MODEL=gemini-2.5-flash
  GROQ_API_KEY=gsk_...
  GEMINI_API_KEY=AIza...

TEMPERATURE GUIDE (used by different agents):
  0.0 → Deterministic, factual (budget analyst, expense tracker, orchestrator)
  0.2 → Slightly varied (spending planner — plan text sounds natural)
  0.3 → Creative (savings finder — tip generation)
  0.4 → Conversational (financial coach — warm, natural responses)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# ── Load .env once ─────────────────────────────────────────────────────────────
_ENV_LOADED = False

def _load_environment() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    # Look for .env in the backend root directory
    backend_dir = Path(__file__).resolve().parents[1]
    load_dotenv(backend_dir / ".env")
    load_dotenv()  # Also load from current working directory
    _ENV_LOADED = True


def get_llm(temperature: float = 0.0, provider: str | None = None) -> Any:
    """
    Return a configured LangChain chat model instance.

    Args:
        temperature: Creativity level. 0.0 = deterministic, 1.0 = very creative.
        provider:    "groq" or "gemini". Overrides LLM_PROVIDER env var if set.

    Returns:
        A LangChain chat model (ChatGroq or ChatGoogleGenerativeAI).

    Raises:
        ValueError: If the required API key is not found in environment.

    Example:
        llm = get_llm(temperature=0.0)
        response = llm.invoke([HumanMessage(content="Hello")])
    """
    _load_environment()

    selected = (provider or os.getenv("LLM_PROVIDER", "groq")).strip().lower()
    if selected not in {"groq", "gemini"}:
        selected = "groq"  # Default fallback

    if selected == "gemini":
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError(
                "Gemini selected but no API key found. "
                "Set GEMINI_API_KEY or GOOGLE_API_KEY in your .env file."
            )
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            temperature=temperature,
            google_api_key=api_key,
        )

    # Default: Groq (fast, cheap, good for most tasks)
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not found. Set it in your .env file or environment."
        )
    from langchain_groq import ChatGroq
    return ChatGroq(
        model=os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
        temperature=temperature,
        api_key=api_key,
    )


def get_llm_with_tools(tools: list[Any], temperature: float = 0.0, provider: str | None = None) -> Any:
    """
    Return a chat model with tools bound to it (function calling).

    The LLM can then call the provided tools during generation.
    Used by spending_planner_agent for financial calculations (SIP, compound interest).

    Args:
        tools:       List of LangChain tool objects.
        temperature: Creativity level.
        provider:    LLM provider override.

    Example:
        llm = get_llm_with_tools([sip_calculator_tool, compound_interest_tool])
        response = llm.invoke(messages)  # LLM may call tools automatically
    """
    return get_llm(temperature=temperature, provider=provider).bind_tools(tools)