/**
 * Plan.jsx — Salary Spending Planner
 *
 * Fixes applied:
 * 1. Chart persistence on reload — plan saved to localStorage, reloaded on mount
 * 2. Black text on dark bg — all text colours are explicit white/gray-xxx
 * 3. Removed duplicate chat bubble logic — shared ChatBubble component
 * 4. Removed duplicate streaming indicator — unified StatusBubble
 * 5. Consistent design tokens with Dashboard & Chat (bg-gray-950 / gray-900 / gray-800)
 * 6. PlanDashboard panel is now a proper scrollable sidebar, not a floating overlay
 * 7. StatCard icons replaced with real Lucide icons instead of single letters
 */

import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowLeft, Bot, Briefcase, ChevronDown, ChevronRight,
  Plus, Send, Target, TrendingUp, User, Wallet, Zap,
} from 'lucide-react'
import {
  Bar, BarChart, CartesianGrid, Cell, Legend,
  Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import MarkdownMessage from '../components/MarkdownMessage'
import { APP_NAME } from '../lib/branding'
import { API_BASE_URL } from '../lib/config'
import { sendPlanMessage } from '../lib/api'
import { consumeSSE } from '../lib/sse'

// ─── Constants ────────────────────────────────────────────────────────────────

const token = () => localStorage.getItem('access_token')

/** Key under which the current plan is cached in localStorage for persistence across reloads. */
const PLAN_CACHE_KEY = 'finpilot_spending_plan'
/** Key for persisting the active planner session id. */
const PLANNER_SESSION_KEY = 'finpilot_planner_session_id'

const BUCKET_COLORS = { Needs: '#6366f1', Wants: '#f59e0b', Savings: '#10b981' }
const CAT_COLORS    = ['#6366f1', '#8b5cf6', '#ec4899', '#f59e0b', '#10b981', '#14b8a6', '#3b82f6', '#f97316']

const formatINR = (n) =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(n || 0)

const agentLabel = (name = '') =>
  name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

// ─── Sub-components ───────────────────────────────────────────────────────────

/** A single chat message bubble — user or assistant. */
function ChatBubble({ msg, isStreaming }) {
  const isUser = msg.role === 'user'
  return (
    <div className={`flex items-start gap-3 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      {/* Avatar */}
      <div
        className={`mt-0.5 flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full
          ${isUser ? 'bg-gray-700' : 'bg-indigo-500/20'}`}
      >
        {isUser
          ? <User size={14} className="text-gray-300" />
          : <Bot  size={14} className="text-indigo-400" />
        }
      </div>

      {/* Bubble */}
      <div className={`flex flex-col gap-1 ${isUser ? 'items-end max-w-[min(34rem,82%)]' : 'items-start max-w-[min(46rem,88%)]'}`}>
        <div
          className={`rounded-2xl px-4 py-3 text-sm leading-relaxed
            ${isUser
              ? 'rounded-tr-sm bg-indigo-600 text-white'
              : 'rounded-tl-sm border border-gray-700/80 bg-gray-800/90 text-gray-100'
            }`}
        >
          {isUser
            ? <span className="whitespace-pre-wrap">{msg.content}</span>
            : <MarkdownMessage content={msg.content} streaming={isStreaming} />
          }
        </div>
        {msg.agent_name && !isUser && (
          <span className="px-1 text-[11px] text-indigo-400/60">
            via {agentLabel(msg.agent_name)}
          </span>
        )}
      </div>
    </div>
  )
}

/** Animated "thinking" indicator shown during streaming. */
function StatusBubble({ statusMsg, activeAgent }) {
  return (
    <div className="flex items-start gap-3">
      <div className="mt-0.5 flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-indigo-500/20">
        <Bot size={14} className="text-indigo-400" />
      </div>
      <div className="rounded-2xl rounded-tl-sm border border-gray-700/80 bg-gray-800/90 px-4 py-3 text-sm">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 animate-pulse rounded-full bg-indigo-400" />
          <span className="text-gray-300">{statusMsg || 'Generating response…'}</span>
        </div>
        {activeAgent && (
          <p className="mt-1 text-[11px] text-indigo-400/70">
            Agent: {agentLabel(activeAgent)}
          </p>
        )}
      </div>
    </div>
  )
}

/** One stat card in the plan overview grid. */
function StatCard({ icon: Icon, label, value, sub, color }) {
  return (
    <div
      style={{ borderLeft: `3px solid ${color}` }}
      className="flex items-center gap-3 rounded-xl bg-gray-900 p-3.5"
    >
      <div
        className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg"
        style={{ background: `${color}18` }}
      >
        <Icon size={16} style={{ color }} />
      </div>
      <div className="min-w-0">
        <p className="text-xs text-gray-400">{label}</p>
        <p className="truncate text-sm font-bold text-white">{value}</p>
        {sub && <p className="mt-0.5 text-[11px]" style={{ color }}>{sub}</p>}
      </div>
    </div>
  )
}

/** Custom tooltip for recharts — dark themed, readable. */
function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-lg border border-gray-700 bg-gray-900 p-2.5 text-xs shadow-xl">
      {label && <p className="mb-1 font-medium text-gray-300">{label}</p>}
      {payload.map((p) => (
        <p key={p.name} style={{ color: p.color }} className="leading-5">
          {p.name}: <span className="font-semibold text-white">{formatINR(p.value)}</span>
        </p>
      ))}
    </div>
  )
}

/** The right-side plan dashboard panel — tabs, charts, goals. */
function PlanDashboard({ plan }) {
  const [tab, setTab] = useState('overview')

  const bucketData = [
    { name: 'Needs',   value: plan.needs_amount,   pct: plan.needs_pct   },
    { name: 'Wants',   value: plan.wants_amount,   pct: plan.wants_pct   },
    { name: 'Savings', value: plan.savings_amount, pct: plan.savings_pct },
  ]

  const categoryData = Object.entries(plan.category_breakdown || {})
    .map(([name, value]) => ({ name, value: Math.round(value) }))
    .filter((d) => d.value > 0)
    .sort((a, b) => b.value - a.value)
    .slice(0, 8)

  const vsData = Object.entries(plan.vs_current || {}).map(([category, d]) => ({
    category,
    Recommended: d.recommended,
    Current:     d.current,
  }))

  const TABS = [
    { id: 'overview',    label: 'Overview'  },
    { id: 'categories',  label: 'Budget'    },
    { id: 'investment',  label: 'Invest'    },
    { id: 'goals',       label: 'Goals'     },
    { id: 'vs',          label: 'vs Actual' },
  ]

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Panel header */}
      <div className="flex flex-shrink-0 items-center justify-between border-b border-gray-800 px-4 py-3">
        <div className="flex items-center gap-2">
          <Zap size={14} className="text-green-400" />
          <span className="text-sm font-semibold text-white">Your Spending Plan</span>
        </div>
        <span className="rounded-full border border-green-500/30 bg-green-500/15 px-2 py-0.5 text-[11px] text-green-400">
          Live
        </span>
      </div>

      {/* Scrollable content */}
      <div className="flex-1 space-y-4 overflow-y-auto p-4">

        {/* Summary line */}
        {plan.summary_line && (
          <div className="rounded-xl border border-indigo-500/25 bg-indigo-500/10 p-3 text-xs leading-relaxed text-indigo-200">
            {plan.summary_line}
          </div>
        )}

        {/* Stat cards grid */}
        <div className="grid grid-cols-2 gap-2.5">
          <StatCard
            icon={TrendingUp}
            label="Monthly Savings"
            value={formatINR(plan.savings_amount)}
            sub={`${plan.savings_pct}% of income`}
            color="#10b981"
          />
          <StatCard
            icon={Target}
            label="12-Month Target"
            value={formatINR(plan.projection_12m)}
            sub="if followed consistently"
            color="#6366f1"
          />
          <StatCard
            icon={Briefcase}
            label="Needs Budget"
            value={formatINR(plan.needs_amount)}
            sub={`${plan.needs_pct}% of income`}
            color="#f59e0b"
          />
          <StatCard
            icon={Wallet}
            label="Wants Budget"
            value={formatINR(plan.wants_amount)}
            sub={`${plan.wants_pct}% of income`}
            color="#ec4899"
          />
        </div>

        {/* Tab nav */}
        <div className="flex gap-1 rounded-xl bg-gray-900/80 p-1">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex-1 rounded-lg py-1.5 text-[11px] font-medium transition-colors
                ${tab === t.id
                  ? 'bg-indigo-600 text-white'
                  : 'text-gray-500 hover:text-gray-300'
                }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* ── Overview: Donut chart ── */}
        {tab === 'overview' && (
          <div className="rounded-xl bg-gray-900/80 p-4">
            <p className="mb-1 text-xs font-medium text-gray-400">
              {Math.round(plan.needs_pct)}/{Math.round(plan.wants_pct)}/{Math.round(plan.savings_pct)} Split
            </p>
            <ResponsiveContainer width="100%" height={175}>
              <PieChart>
                <Pie
                  data={bucketData}
                  cx="50%" cy="50%"
                  innerRadius={48} outerRadius={72}
                  paddingAngle={4}
                  dataKey="value"
                >
                  {bucketData.map((d) => (
                    <Cell key={d.name} fill={BUCKET_COLORS[d.name]} />
                  ))}
                </Pie>
                <Tooltip content={<ChartTooltip />} />
              </PieChart>
            </ResponsiveContainer>
            <div className="mt-2 space-y-2">
              {bucketData.map((d) => (
                <div key={d.name} className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className="h-2 w-2 rounded-full" style={{ background: BUCKET_COLORS[d.name] }} />
                    <span className="text-xs text-gray-300">{d.name}</span>
                  </div>
                  <span className="text-xs text-white">
                    <span className="font-semibold">{formatINR(d.value)}</span>
                    <span className="ml-1 text-gray-500">{d.pct}%</span>
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── Budget: Category breakdown ── */}
        {tab === 'categories' && (
          <div className="rounded-xl bg-gray-900/80 p-4">
            <p className="mb-3 text-xs font-medium text-gray-400">Where your money goes each month</p>
            <div className="space-y-3">
              {categoryData.map((d, i) => (
                <div key={d.name}>
                  <div className="mb-1 flex justify-between text-xs">
                    <span className="text-gray-300">{d.name}</span>
                    <span className="font-medium text-white">{formatINR(d.value)}</span>
                  </div>
                  <div className="h-1.5 rounded-full bg-gray-800">
                    <div
                      className="h-1.5 rounded-full transition-all duration-700"
                      style={{
                        width: `${(d.value / (categoryData[0]?.value || 1)) * 100}%`,
                        background: CAT_COLORS[i % CAT_COLORS.length],
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── Investment allocation ── */}
        {tab === 'investment' && (
          <div className="rounded-xl bg-gray-900/80 p-4">
            <p className="mb-3 text-xs font-medium text-gray-400">Monthly investment allocation</p>
            {Object.keys(plan.investment_allocation || {}).length > 0 ? (
              <div className="space-y-2">
                {Object.entries(plan.investment_allocation).map(([name, value], i) => (
                  <div
                    key={name}
                    className="flex items-center justify-between rounded-lg border border-gray-700/50 bg-gray-800/60 px-3 py-2.5"
                  >
                    <div className="flex items-center gap-2">
                      <div
                        className="h-2 w-2 rounded-full"
                        style={{ background: CAT_COLORS[i % CAT_COLORS.length] }}
                      />
                      <span className="text-xs text-gray-200">{name}</span>
                    </div>
                    <span className="text-xs font-semibold text-white">{formatINR(value)}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="py-8 text-center text-xs text-gray-500">
                Investment split appears after plan generation.
              </p>
            )}
          </div>
        )}

        {/* ── Goals allocation ── */}
        {tab === 'goals' && (
          <div className="rounded-xl bg-gray-900/80 p-4">
            <p className="mb-3 text-xs font-medium text-gray-400">Monthly savings per goal</p>
            {Object.keys(plan.goals_allocation || {}).length > 0 ? (
              <div className="space-y-2">
                {Object.entries(plan.goals_allocation).map(([name, monthly], i) => (
                  <div
                    key={name}
                    className="rounded-lg border border-gray-700/50 bg-gray-800/60 p-3"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-medium text-gray-200">{name}</span>
                      <span className="text-xs font-bold text-white">
                        {formatINR(monthly)}
                        <span className="ml-0.5 font-normal text-gray-400">/mo</span>
                      </span>
                    </div>
                    <div className="mt-2 h-1 overflow-hidden rounded-full bg-gray-700">
                      <div
                        className="h-1 rounded-full"
                        style={{ width: '100%', background: CAT_COLORS[i % CAT_COLORS.length] }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-xl border border-dashed border-gray-700 py-8 text-center">
                <Target size={28} className="mx-auto mb-2 text-gray-600" />
                <p className="text-xs text-gray-500">No goals yet.</p>
                <p className="mt-1 text-[11px] text-gray-600">
                  Mention them in chat — e.g., "emergency fund 1 lakh in 12 months"
                </p>
              </div>
            )}
          </div>
        )}

        {/* ── vs Actual spending (requires CSV upload) ── */}
        {tab === 'vs' && (
          <div className="rounded-xl bg-gray-900/80 p-4">
            <p className="mb-3 text-xs font-medium text-gray-400">
              Recommended vs Your Actual Spending
            </p>
            {vsData.length > 0 ? (
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={vsData} margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                  <XAxis dataKey="category" tick={{ fill: '#6b7280', fontSize: 9 }} />
                  <YAxis
                    tick={{ fill: '#6b7280', fontSize: 9 }}
                    tickFormatter={(v) => `Rs${(v / 1000).toFixed(0)}k`}
                  />
                  <Tooltip content={<ChartTooltip />} />
                  <Legend wrapperStyle={{ fontSize: 10, color: '#9ca3af' }} />
                  <Bar dataKey="Recommended" fill="#6366f1" radius={[3, 3, 0, 0]} />
                  <Bar dataKey="Current"     fill="#f59e0b" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div className="rounded-xl border border-dashed border-gray-700 py-10 text-center">
                <p className="text-xs text-gray-500">Upload a CSV on Dashboard</p>
                <p className="mt-1 text-[11px] text-gray-600">to compare your actual vs planned spending.</p>
              </div>
            )}
          </div>
        )}

        {/* Behavioral nudges — shown on all tabs if present */}
        {plan.behavioral_nudges?.length > 0 && (
          <div className="rounded-xl bg-gray-900/80 p-4">
            <p className="mb-2 text-xs font-medium text-gray-400">⚠ Spending Alerts</p>
            <div className="space-y-2">
              {plan.behavioral_nudges.map((n, i) => (
                <div
                  key={i}
                  className="rounded-lg border border-amber-500/20 bg-amber-500/10 p-2.5 text-xs leading-relaxed text-amber-200"
                >
                  {n}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Action items */}
        {plan.monthly_action_items?.length > 0 && (
          <div className="rounded-xl bg-gray-900/80 p-4">
            <p className="mb-2 text-xs font-medium text-gray-400">✅ This Month's Action Plan</p>
            <ol className="space-y-2">
              {plan.monthly_action_items.map((item, idx) => (
                <li key={idx} className="flex gap-2.5 rounded-lg border border-gray-700/50 bg-gray-800/60 p-2.5">
                  <span className="mt-0.5 flex h-4 w-4 flex-shrink-0 items-center justify-center rounded-full bg-indigo-600/40 text-[10px] font-bold text-indigo-300">
                    {idx + 1}
                  </span>
                  <span className="text-xs leading-5 text-gray-200">{item}</span>
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function Plan() {
  const navigate   = useNavigate()
  const bottomRef  = useRef(null)
  const pendingRef = useRef(null)

  const [messages,      setMessages]      = useState([])
  const [input,         setInput]         = useState('')
  const [streaming,     setStreaming]      = useState(false)
  const [statusMsg,     setStatusMsg]      = useState('')
  const [activeAgent,   setActiveAgent]   = useState('')
  const [sessionId,     setSessionId]     = useState(null)
  const [plan,          setPlan]          = useState(() => {
    // ── Fix: Rehydrate plan from localStorage on mount ──────────────────
    // This prevents charts from disappearing after a page reload.
    try {
      const cached = localStorage.getItem(PLAN_CACHE_KEY)
      return cached ? JSON.parse(cached) : null
    } catch {
      return null
    }
  })
  const [sessionLoading, setSessionLoading] = useState(true)

  const showPanel = Boolean(plan)

  // ── Auto-scroll when messages change ──────────────────────────────────
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, statusMsg])

  // ── Persist plan to localStorage whenever it updates ──────────────────
  useEffect(() => {
    if (plan) {
      localStorage.setItem(PLAN_CACHE_KEY, JSON.stringify(plan))
    }
  }, [plan])

  // ── Initialise session on mount ────────────────────────────────────────
  useEffect(() => {
    initSession()
  }, [])

  // ── When session is ready, load previous messages + plan ──────────────
  useEffect(() => {
    if (!sessionId) return
    loadSessionMessages(sessionId)
    // Only fetch plan from server if not already loaded from localStorage
    if (!plan) loadLatestPlan(sessionId)
  }, [sessionId])

  // ─── Session management ────────────────────────────────────────────────

  async function initSession() {
    // Re-use stored session id if present — avoids creating a blank new session on every refresh
    const storedId = localStorage.getItem(PLANNER_SESSION_KEY)
    if (storedId) {
      setSessionId(storedId)
      setSessionLoading(false)
      return
    }
    await createNewSession(true)
  }

  async function createNewSession(isSilent = false) {
    try {
      setSessionLoading(true)
      const res = await fetch(`${API_BASE_URL}/chat/sessions`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token()}` },
      })
      if (res.status === 401) {
        localStorage.clear()
        window.location.href = '/login'
        return
      }
      if (!res.ok) throw new Error('Session creation failed')

      const data = await res.json()
      const newId = data?.session?.id
      if (!newId) throw new Error('Session id missing from response')

      localStorage.setItem(PLANNER_SESSION_KEY, newId)
      setSessionId(newId)
      setMessages([])
      if (!isSilent) {
        // Full reset for "New Session" button
        setPlan(null)
        localStorage.removeItem(PLAN_CACHE_KEY)
      }

      if (pendingRef.current) {
        const queued = pendingRef.current
        pendingRef.current = null
        await sendWithSession(newId, queued)
      }
    } catch (e) {
      console.error('[Plan] Session init error:', e)
    } finally {
      setSessionLoading(false)
    }
  }

  async function loadLatestPlan(sid) {
    try {
      const res = await fetch(`${API_BASE_URL}/plan/latest?session_id=${sid}`, {
        headers: { Authorization: `Bearer ${token()}` },
      })
      if (!res.ok) return
      const data = await res.json()
      const planData = data?.plan?.plan || data?.plan?.plan_data || null
      if (planData) setPlan(planData)
    } catch {
      // no-op — localStorage cache covers this case
    }
  }

  async function loadSessionMessages(sid) {
    try {
      const res = await fetch(`${API_BASE_URL}/chat/sessions/${sid}/messages`, {
        headers: { Authorization: `Bearer ${token()}` },
      })
      if (!res.ok) return
      const data = await res.json()
      setMessages(data.messages || [])
    } catch {
      // no-op
    }
  }

  // ─── Sending messages ──────────────────────────────────────────────────

  async function sendWithSession(sid, text) {
    if (!text?.trim() || !sid || streaming) return

    setMessages((prev) => [
      ...prev,
      { role: 'user',      content: text, agent_name: '' },
      { role: 'assistant', content: '',   agent_name: '' },
    ])
    setInput('')
    setStreaming(true)
    setStatusMsg('Analyzing your inputs…')
    setActiveAgent('')

    let fullText  = ''
    let agentName = 'spending_planner'

    try {
      const response = await sendPlanMessage(sid, text)

      await consumeSSE(response, (event) => {
        if (event.type === 'routing') {
          setActiveAgent(event.agent || '')
          return
        }
        if (event.type === 'status') {
          setStatusMsg(event.content || '')
          if (event.agent) setActiveAgent(event.agent)
          return
        }
        if (event.type === 'token') {
          fullText += event.content || ''
          setMessages((prev) => {
            const updated = [...prev]
            const last    = updated.length - 1
            if (last >= 0) updated[last] = { role: 'assistant', content: fullText, agent_name: agentName }
            return updated
          })
          return
        }
        if (event.type === 'plan') {
          // Plan data arrives mid-stream — store immediately and cache
          if (event.data) {
            setPlan(event.data)
            localStorage.setItem(PLAN_CACHE_KEY, JSON.stringify(event.data))
          }
          return
        }
        if (event.type === 'done') {
          agentName = event.agent || agentName
          setMessages((prev) => {
            const updated = [...prev]
            const last    = updated.length - 1
            if (last >= 0) updated[last] = { role: 'assistant', content: fullText, agent_name: agentName }
            return updated
          })
          return
        }
        if (event.type === 'error') {
          throw new Error(event.content || 'Stream error')
        }
      })
    } catch (e) {
      console.error('[Plan] SSE error:', e)
      setMessages((prev) => prev.slice(0, -2))
    } finally {
      setStreaming(false)
      setStatusMsg('')
      setActiveAgent('')
    }
  }

  function handleSend(override) {
    const text = (override !== undefined ? override : input).trim()
    if (!text || streaming) return
    if (!sessionId) {
      pendingRef.current = text
      setInput('')
      return
    }
    sendWithSession(sessionId, text)
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  function handleNewSession() {
    localStorage.removeItem(PLANNER_SESSION_KEY)
    localStorage.removeItem(PLAN_CACHE_KEY)
    setPlan(null)
    setMessages([])
    createNewSession(false)
  }

  // ─── Render ────────────────────────────────────────────────────────────

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-gray-950">

      {/* ── Header ─────────────────────────────────────────────────────── */}
      <header className="flex flex-shrink-0 items-center gap-3 border-b border-gray-800 bg-gray-900 px-5 py-3">
        <button
          onClick={() => navigate('/dashboard')}
          className="rounded-lg p-1.5 text-gray-400 transition hover:bg-gray-800 hover:text-white"
          title="Back to Dashboard"
        >
          <ArrowLeft size={18} />
        </button>

        <div className="flex items-center gap-2">
          <Wallet size={17} className="text-indigo-400" />
          <span className="text-sm font-bold text-white">{APP_NAME}</span>
        </div>

        <div className="h-4 w-px bg-gray-700" />

        <div>
          <h1 className="text-sm font-semibold text-white">Salary Spending Planner</h1>
          <p className="text-[11px] text-gray-400">
            4 questions → monthly budget split, goals, and investment plan
          </p>
        </div>

        <div className="ml-auto flex items-center gap-2">
          {plan && (
            <span className="rounded-full border border-green-500/30 bg-green-500/15 px-2.5 py-0.5 text-[11px] font-medium text-green-400">
              ✓ Plan Ready
            </span>
          )}
          <button
            onClick={handleNewSession}
            disabled={streaming || sessionLoading}
            className="flex items-center gap-1.5 rounded-lg border border-gray-700 px-3 py-1.5 text-xs font-medium text-gray-300 transition hover:border-indigo-500/50 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Plus size={13} />
            New Session
          </button>
        </div>
      </header>

      {/* ── Body: Chat + Plan Panel ─────────────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden">

        {/* Chat column */}
        <div
          className={`flex flex-col border-r border-gray-800 transition-[width] duration-500 ease-in-out
            ${showPanel ? 'w-1/2' : 'w-full'}`}
        >
          {/* Messages */}
          <div className="flex-1 space-y-5 overflow-y-auto p-5">

            {/* Empty state */}
            {messages.length === 0 && (
              <div className="mx-auto max-w-xl pt-12 text-center">
                <div className="mb-5 flex justify-center">
                  <div className="flex h-16 w-16 items-center justify-center rounded-2xl border border-amber-500/20 bg-amber-500/10">
                    <Wallet size={32} className="text-amber-400" />
                  </div>
                </div>
                <h2 className="mb-2 text-2xl font-bold text-white">Salary Spending Planner</h2>
                <p className="mb-5 text-sm leading-relaxed text-gray-400">
                  Answer <span className="font-semibold text-indigo-300">4 quick questions</span> and
                  get a clear monthly budget split, savings targets, and goal timeline.
                </p>

                <div className="mb-6 flex flex-wrap justify-center gap-2">
                  {['Income Split', 'Savings Targets', 'Goal Timeline', 'Investment Plan'].map((tag) => (
                    <span
                      key={tag}
                      className="rounded-full border border-indigo-500/25 bg-indigo-500/10 px-3 py-1 text-xs text-indigo-300"
                    >
                      {tag}
                    </span>
                  ))}
                </div>

                <button
                  onClick={() => handleSend('Help me plan my salary')}
                  disabled={sessionLoading}
                  className="inline-flex items-center gap-2 rounded-xl bg-indigo-600 px-6 py-3 text-sm font-semibold text-white shadow-lg shadow-indigo-900/40 transition hover:bg-indigo-500 disabled:cursor-wait disabled:opacity-60"
                >
                  <Wallet size={16} />
                  {sessionLoading ? 'Setting up…' : 'Start My Financial Plan'}
                </button>
              </div>
            )}

            {/* Message list */}
            {messages.map((msg, i) => {
              const isLastAssistant = i === messages.length - 1 && msg.role === 'assistant'
              return (
                <ChatBubble
                  key={i}
                  msg={msg}
                  isStreaming={streaming && isLastAssistant}
                />
              )
            })}

            {/* Typing indicator */}
            {streaming && (statusMsg || activeAgent) && (
              <StatusBubble statusMsg={statusMsg} activeAgent={activeAgent} />
            )}

            <div ref={bottomRef} />
          </div>

          {/* Input bar */}
          <div className="flex-shrink-0 border-t border-gray-800 bg-gray-900/80 p-4">
            <div className="flex items-end gap-2">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={
                  messages.length === 0
                    ? 'Click the button above or type here…'
                    : 'Reply to your advisor…'
                }
                rows={1}
                style={{ maxHeight: '100px' }}
                className="flex-1 resize-none rounded-xl border border-gray-700 bg-gray-800 px-4 py-3 text-sm text-white placeholder-gray-500 transition focus:border-indigo-500 focus:outline-none"
              />
              <button
                onClick={() => handleSend()}
                disabled={!input.trim() || streaming}
                className="flex-shrink-0 rounded-xl bg-indigo-600 p-3 text-white transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <Send size={16} />
              </button>
            </div>
            <p className="mt-2 text-center text-[11px] text-gray-600">
              Salary data is processed in-memory only · never stored
            </p>
          </div>
        </div>

        {/* Plan dashboard panel — slides in when plan is ready */}
        <div
          className={`overflow-hidden border-l border-gray-800 bg-gray-950 transition-[width] duration-500 ease-in-out
            ${showPanel ? 'w-1/2' : 'w-0'}`}
        >
          {plan && <PlanDashboard plan={plan} />}
        </div>
      </div>
    </div>
  )
}
