<div align="center">

# 💰 Finance AI Agent

### A production-grade, multi-agent AI system for personal finance management
### — expense tracking · budget analysis · salary planning · real-time investment guidance —

[![Live Demo](https://img.shields.io/badge/🚀_Live_Demo-Vercel-black?style=for-the-badge)](https://finance-ai-agent-rose.vercel.app)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18-61DAFB?style=for-the-badge&logo=react&logoColor=black)](https://react.dev)
[![LangGraph](https://img.shields.io/badge/LangGraph-Multi--Agent-FF6B35?style=for-the-badge)](https://langchain-ai.github.io/langgraph/)
[![Supabase](https://img.shields.io/badge/Supabase-Auth_+_DB-3ECF8E?style=for-the-badge&logo=supabase&logoColor=white)](https://supabase.com)

</div>

---

 

https://github.com/user-attachments/assets/20041888-0990-4c5c-b186-160d5c974b40





## 📌 What This Project Does

Finance AI Agent is a **full-stack AI application** where users upload their transaction data (CSV), chat with a multi-agent AI system, and receive personalized financial advice — all in real time.

It is **not a chatbot wrapper**. It is a **LangGraph-orchestrated multi-agent system** where each agent is a specialist:

| Agent | Responsibility |
|---|---|
| 🎯 **Orchestrator** | Classifies every user message and routes it to the correct specialist agent |
| 📊 **Expense Tracker** | Parses and categorizes uploaded CSV transactions |
| 📉 **Budget Analyst** | Runs budget health checks, flags overspending |
| 🏦 **Financial Coach** | Gives personalized savings and investment advice |
| 💡 **Savings Finder** | Searches for live investment products (SIPs, FDs, MFs) using web tools |
| 💰 **Spending Planner** | Runs a 4-question conversational session to build a complete monthly plan |

---

## 🏗️ System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        REACT FRONTEND (Vite)                        │
│   Login · Dashboard · Chat · Spending Planner · Transaction Upload  │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ REST + SSE Streaming
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      FASTAPI BACKEND                                 │
│  /auth  /chat  /transactions  /budget  /plan  /upload               │
└──────────┬────────────────────────────────────┬─────────────────────┘
           │                                    │
           ▼                                    ▼
┌──────────────────────┐            ┌───────────────────────┐
│   LANGGRAPH ENGINE   │            │  SUPABASE             │
│                      │            │  ├─ Auth (JWT)        │
│  ┌────────────────┐  │            │  ├─ sessions          │
│  │  Orchestrator  │  │            │  ├─ transactions      │
│  └───────┬────────┘  │            │  ├─ spending_plans    │
│          │ routes to │            │  └─ RLS policies      │
│  ┌───────▼────────┐  │            └───────────────────────┘
│  │ Expense Tracker│  │
│  │ Budget Analyst │  │            ┌───────────────────────┐
│  │ Financial Coach│  │            │  EXTERNAL SERVICES    │
│  │ Savings Finder │  │            │  ├─ Gemini 1.5 Flash  │
│  │ Spending       │  │            │  ├─ Tavily Web Search │
│  │   Planner      │  │            │  └─ LangSmith Tracing │
│  └────────────────┘  │            └───────────────────────┘
│                      │
│  SQLite Checkpointer │
│  (per-session memory)│
└──────────────────────┘
```

---

## 🔄 Request Flow — How a Message Travels

```
User types: "Am I overspending on food?"
         │
         ▼
  FastAPI /chat/stream
         │
         ▼
  stream_agent() ──► LangGraph StateGraph
         │
         ▼
  ┌─────────────────────────────────────────────────────────┐
  │  Node 1: orchestrator                                    │
  │  → Classifies intent as "budget_analysis"               │
  │  → Sets next_agent = "budget_analyst"                   │
  │  → Emits SSE: { type: "routing", agent: "budget_analyst"}│
  └───────────────────────┬─────────────────────────────────┘
                          │
                          ▼
  ┌─────────────────────────────────────────────────────────┐
  │  Node 2: budget_analyst                                  │
  │  → Reads transactions from state                        │
  │  → Calls Gemini 1.5 Flash                               │
  │  → Streams response tokens back                         │
  │  → Emits SSE: { type: "token", content: "..." } × N     │
  │  → Emits SSE: { type: "done", agent: "budget_analyst" } │
  └─────────────────────────────────────────────────────────┘
         │
         ▼
  React frontend renders word-by-word in real time
```

---

## 💬 Spending Planner — Dedicated Conversational Flow

The planner is architecturally separate from the main chat graph — it has its **own LangGraph** with no orchestrator, ensuring multi-turn salary planning conversations never get hijacked by other agents.

```
User: "Help me plan my salary"
         │
         ▼
  FastAPI /plan/chat  (separate endpoint)
         │
         ▼
  stream_planner() ──► build_planner_graph()
         │                    (dedicated graph)
         ▼
  spending_planner_agent
         │
         │  Q1: What is your monthly salary?
         │  Q2: What are your fixed expenses?
         │  Q3: What are your financial goals?
         │  Q4: Risk tolerance?
         │
         ▼
  Builds spending_plan JSON
         │
         ├──► SSE { type: "plan", data: {...} }  ──► React chart dashboard slides in
         └──► SSE { type: "token", ... }         ──► Chat response streams in
```

---

## 🗂️ Full File Structure

```
Finance-AI-AGENT/
│
├── backend/
│   ├── main.py                          # FastAPI app entry, CORS, router registration
│   │                                    # LangSmith tracing activated here at startup
│   │
│   ├── agents/
│   │   ├── orchestrator.py              # Intent classifier → routes to correct agent
│   │   ├── expense_tracker.py           # Parses & categorizes CSV transactions
│   │   ├── budget_analyst.py            # Spending health analysis
│   │   ├── financial_coach.py           # Personalised savings/investment advice
│   │   ├── savings_finder.py            # Live product search via Tavily web tool
│   │   ├── spending_planner_agent.py    # 4-question salary planner, emits plan JSON
│   │   └── response_format.py           # Shared markdown format rules for all agents
│   │
│   ├── graph/
│   │   ├── state.py                     # FinanceState TypedDict (shared graph state)
│   │   ├── graph_builder.py             # build_graph() + build_planner_graph()
│   │   └── memory.py                    # SQLite checkpointer (per-session memory)
│   │
│   ├── routers/
│   │   ├── auth.py                      # Supabase JWT validation
│   │   ├── chat.py                      # /chat/sessions, /chat/stream (SSE)
│   │   ├── transactions.py              # CSV upload, parse, store to Supabase
│   │   ├── budget.py                    # /budget/summary endpoint
│   │   └── plan.py                      # /plan/chat, /plan/latest (planner flow)
│   │
│   ├── services/
│   │   ├── agent_runner.py              # run_agent(), stream_agent(), stream_planner()
│   │   ├── observability.py             # LangSmith setup + privacy-safe trace config
│   │   └── csv_parser.py                # Pandas transaction normalizer
│   │
│   └── tools/
│       ├── financial_tools.py           # 7 @tool functions: SIP calc, FD rates, etc.
│       └── anonymizer.py                # Strips PII from transactions before LLM sees them
│
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Login.jsx                # Supabase auth UI
│   │   │   ├── Dashboard.jsx            # Main hub: upload CSV, nav to features
│   │   │   ├── Chat.jsx                 # General financial chat + MarkdownMessage
│   │   │   └── Plan.jsx                 # Split-panel: chat left, live charts right
│   │   │
│   │   ├── context/
│   │   │   └── AuthContext.jsx          # JWT storage + auto-refresh on expiry
│   │   │
│   │   └── lib/
│   │       └── api.js                   # All fetch calls: sendMessage, sendPlanMessage,
│   │                                    # getLatestPlan, uploadTransactions, etc.
│   │
│   └── package.json
│
├── data/
│   └── sample_transactions.csv          # Test dataset for local development
│
├── test_output/                         # Agent response test snapshots
├── requirements.txt                     # Python dependencies
├── pyproject.toml                       # Project config (uv)
└── .python-version                      # Python 3.11
```

---

## ⚙️ Tech Stack

| Layer | Technology | Why |
|---|---|---|
| **LLM** | Google Gemini 1.5 Flash | Free tier, fast, handles multi-turn conversation well |
| **Agent Framework** | LangGraph (LangChain) | Stateful multi-agent graphs with per-session memory via checkpointing |
| **Web Search Tool** | Tavily API | Real-time investment product search within agent tool calls |
| **Observability** | LangSmith | Full trace visibility — every LLM call, latency, token count |
| **Backend** | FastAPI + Python 3.11 | Async SSE streaming, clean REST API design |
| **Auth + DB** | Supabase (PostgreSQL) | Row-Level Security, JWT auth, real-time capable |
| **Frontend** | React 18 + Vite | Component-based UI, fast HMR in development |
| **Styling** | Tailwind CSS | Utility-first, consistent dark UI |
| **Charts** | Recharts | Pie, bar, and progress charts for the spending dashboard |
| **Deployment** | Vercel (frontend) | Live at finance-ai-agent-rose.vercel.app |

---

## 🔐 Security & Privacy Design

This project handles sensitive financial data — the following decisions were made deliberately:

- **PII Anonymization before LLM** — `anonymizer.py` strips merchant names and personal identifiers from transactions before they enter the LangGraph state. The LLM never sees raw transaction data.
- **JWT Auth on every endpoint** — All FastAPI routes validate the Supabase JWT. No endpoint is publicly accessible.
- **Row-Level Security (RLS)** — Supabase tables have RLS policies ensuring users can only query their own data.
- **LangSmith privacy mode** — `LANGSMITH_HIDE_INPUTS=true` can be set in production to prevent salary/income values from appearing in trace logs.
- **In-memory salary processing** — Salary figures entered in the planner are never persisted to the database raw; only the computed `plan_data` JSON is stored.

---

## 🧠 Key Engineering Decisions

### 1. Dedicated Planner Graph (solves orchestrator hijacking)
The spending planner runs on a **completely separate LangGraph** (`build_planner_graph()`) with no orchestrator node. Early versions routed planner messages through the main graph — the orchestrator would classify "65000" (a salary answer) as a transaction query and hand it to `expense_tracker`, killing the conversation. Separate graph = separate memory, separate routing, guaranteed continuity.

### 2. SSE Streaming (not WebSocket)
The backend streams responses as **Server-Sent Events** rather than WebSockets. SSE is unidirectional (server → client), stateless, and works naturally with FastAPI's `StreamingResponse`. It also survives proxy/CDN environments that sometimes block WebSocket upgrades. The tradeoff: no bi-directional communication, but that's not needed here.

### 3. Thread-ID Memory Model
LangGraph's `SQLiteCheckpointer` stores conversation state keyed by `thread_id`. This project uses two namespaces:
- `{user_id}:{session_id}` for main chat sessions
- `planner:{user_id}:{session_id}` for planner sessions

This gives every feature its own isolated memory while sharing the same checkpointer infrastructure.

### 4. Model Routing by Task Complexity
Not all agents use the same model — routing is done by task weight:
- `gemini-1.5-flash-8b` for the orchestrator (just classifies intent, cheapest)
- `gemini-1.5-flash` for most agents (fast, free tier)
- `gemini-1.5-pro` for the spending planner (complex multi-turn + tool use)

---

## 🚀 Getting Started

### Prerequisites
- Python 3.11+
- Node.js 18+
- Supabase account (free tier works)
- Google AI Studio API key (Gemini)
- LangSmith account (optional, for tracing)

### Backend Setup

```bash
# Clone the repo
git clone https://github.com/Mohini117/Finance-AI-AGENT.git
cd Finance-AI-AGENT/backend

# Install dependencies (using uv — recommended)
pip install uv
uv sync

# OR using pip
pip install -r requirements.txt

# Create .env file
cp .env.example .env
# Fill in your keys (see Environment Variables section)

# Run
uvicorn main:app --reload --port 8000
```

### Frontend Setup

```bash
cd frontend
npm install

# Create .env.local
echo "VITE_API_URL=http://localhost:8000" > .env.local

npm run dev
# App available at http://localhost:5173
```

### Environment Variables

**backend/.env**
```env
# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your-service-role-key

# Google Gemini
GOOGLE_API_KEY=your-gemini-api-key

# Tavily (for savings_finder live search)
TAVILY_API_KEY=your-tavily-key

# LangSmith (optional — enables full trace dashboard)
LANGSMITH_API_KEY=ls__your-key
LANGSMITH_PROJECT=finance-advisor
LANGSMITH_HIDE_INPUTS=false   # set true in production

# App
APP_VERSION=1.0.0
```

---

## 📊 Features Walkthrough

### 1. Transaction Upload & Analysis
- Upload any bank CSV from the Dashboard
- Backend normalizes columns via `csv_parser.py` (handles different bank formats)
- Transactions stored in Supabase with `user_id` scoping
- Ask "Where am I overspending?" → `budget_analyst` agent answers with your real data

### 2. General Financial Chat
- Conversational interface with streaming responses
- Agents maintain memory across the session (LangGraph checkpointer)
- Markdown-rendered responses: headers, bullets, bold, code blocks, emoji callouts
- Agent name shown below each response (transparency)

### 3. Salary Spending Planner
- 4-question flow: income → expenses → goals → risk tolerance
- Generates structured `spending_plan` JSON with `needs_amount`, `wants_amount`, `savings_amount`, `projection_12m`, `goals_allocation`, `behavioral_nudges`
- Split-panel UI: chat on left, live Recharts dashboard slides in on right when plan is ready
- Plan persisted to Supabase `spending_plans` table, loaded on next visit

### 4. LangSmith Observability
- Every LangGraph run is traced automatically
- View the exact prompt, response, latency, and token count for every agent call
- Filter by `feature:planner` vs `feature:chat` tags
- Privacy-safe: user IDs are hashed to first 8 chars in trace metadata

---

## 🗃️ Database Schema

```sql
-- Sessions
CREATE TABLE sessions (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     TEXT NOT NULL,
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- Transactions (from CSV upload)
CREATE TABLE transactions (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     TEXT NOT NULL,
  session_id  UUID REFERENCES sessions(id),
  date        DATE,
  description TEXT,
  amount      NUMERIC,
  category    TEXT,
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- Spending Plans (from planner agent)
CREATE TABLE spending_plans (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     TEXT NOT NULL,
  session_id  TEXT,
  plan_data   JSONB,       -- full structured plan
  income      NUMERIC,
  created_at  TIMESTAMPTZ DEFAULT now()
);
```

All tables have **Row-Level Security** policies: `auth.uid() = user_id`.

---

## 🧪 Testing

Sample transaction CSV is in `data/sample_transactions.csv`. Use it to test the full upload → analysis → planning flow locally.

```bash
# Test agent responses directly
cd backend
python -m pytest tests/  # if test suite present

# Or run a quick agent smoke test
python -c "
import asyncio
from services.agent_runner import run_agent
result = asyncio.run(run_agent('What is a SIP?', 'test-user', 'test-session'))
print(result['response'])
"
```

---

## 🌐 Live Demo

**[https://finance-ai-agent-rose.vercel.app](https://finance-ai-agent-rose.vercel.app)**

Use the sample CSV from `data/sample_transactions.csv` to explore the full feature set.

---

## 🤝 Contributing

1. Fork the repo
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Commit your changes: `git commit -m 'Add: your feature description'`
4. Push: `git push origin feature/your-feature`
5. Open a Pull Request

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

<div align="center">

**Built with LangGraph · FastAPI · React · Supabase · Gemini**

*A project that goes beyond chatbots — a real multi-agent system with memory, routing, tools, and observability.*

</div>
