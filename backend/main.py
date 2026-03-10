from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from services.observability import setup_langsmith

load_dotenv()
setup_langsmith()

from routers import auth, chat, transactions
from routers.plan import router as plan_router


app = FastAPI(
    title       = "Finance Advisor API",
    description = "AI-powered personal finance advisor backend",
    version     = "1.0.0"
)

# ── CORS must be registered FIRST — before any routers ────────────────────────
# LESSON: FastAPI middleware wraps the entire app.
# If you add it after routers, preflight OPTIONS requests fail → 401/403 errors.
app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:8000"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(transactions.router)
app.include_router(plan_router)

@app.get("/")
async def root():
    return {"message": "Finance Advisor API is running", "version": "1.0.0", "docs": "/docs"}

@app.get("/health")
async def health():
    return {"status": "healthy"}
