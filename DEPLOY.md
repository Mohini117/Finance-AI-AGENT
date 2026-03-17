# Deploy Finance AI Agent

This repo deploys cleanly as two services:

- `Finance-AI-AGENT/backend` -> Python web service
- `Finance-AI-AGENT/frontend` -> static Vite site

The simplest free setup is Render because it can host both from one GitHub repo. The included `render.yaml` file is ready for that layout.

## What you need

- A Supabase project
- One LLM key: `GROQ_API_KEY` or `GEMINI_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`
- `SUPABASE_SERVICE_KEY`
- `TAVILY_API_KEY` if you want live savings/product search
- `LANGCHAIN_API_KEY` only if you want LangSmith tracing

## Required env vars

Backend:

```env
LLM_PROVIDER=groq
GROQ_API_KEY=
GEMINI_API_KEY=
TAVILY_API_KEY=
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_KEY=
LANGCHAIN_API_KEY=
LANGCHAIN_PROJECT=finance-ai-agent
APP_VERSION=1.0.0
CORS_ALLOWED_ORIGINS=https://your-frontend-domain.onrender.com
```

Frontend:

```env
VITE_SUPABASE_URL=
VITE_SUPABASE_ANON_KEY=
VITE_API_URL=https://your-backend-domain.onrender.com
```

## Render steps

1. Push this repo to GitHub.
2. In Render, create a new Blueprint and select the repo.
3. Render will detect `render.yaml` in the repo root.
4. Fill in every env var marked `sync: false`.
5. Set backend `CORS_ALLOWED_ORIGINS` to the exact frontend URL Render gives you.
6. Set frontend `VITE_API_URL` to the exact backend URL Render gives you.
7. Deploy both services.

## Notes

- The frontend uses React Router, so the static site needs SPA rewrites. `render.yaml` already adds that.
- The backend uses SSE streaming, and the current start command keeps that working on Render.
- If you already committed real keys in local `.env` files, rotate them before going live.
- Render free web services may sleep when idle, so the first backend response after inactivity can be slow.
