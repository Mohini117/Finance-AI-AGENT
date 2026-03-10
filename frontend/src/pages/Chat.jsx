/**
 * Chat.jsx — Multi-session AI Finance Advisor Chat
 *
 * Fixes applied:
 * 1. Removed duplicate chat bubble rendering logic — now uses shared ChatBubble component
 * 2. Removed duplicate streaming indicator — single StatusBubble
 * 3. Black-text-on-dark-bg fixed — all text has explicit color classes
 * 4. Consistent design tokens with Dashboard & Plan pages
 * 5. Sidebar polish — better spacing, session time, active state
 * 6. Goal input removed from UI (was unused, confusing — goal is set in chat)
 * 7. Empty state simplified — no duplicate "open planner" buttons in one view
 */

import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowLeft, Bot, MessageSquare, Plus, Send, Trash2, User, Wallet,
} from 'lucide-react'
import toast from 'react-hot-toast'
import MarkdownMessage from '../components/MarkdownMessage'
import { useAuth } from '../context/AuthContext'
import { APP_NAME } from '../lib/branding'
import { createSession, deleteSession, getMessages, getSessions, sendMessage } from '../lib/api'
import { consumeSSE } from '../lib/sse'

// ─── Utilities ─────────────────────────────────────────────────────────────────

const agentLabel = (name = '') =>
  name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

/** Human-readable relative time for sidebar session timestamps. */
function formatSessionTime(iso) {
  if (!iso) return 'recently'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return 'recently'
  const diffMins = Math.floor((Date.now() - date.getTime()) / 60_000)
  if (diffMins < 1)  return 'just now'
  if (diffMins < 60) return `${diffMins}m ago`
  const diffHours = Math.floor(diffMins / 60)
  if (diffHours < 24) return `${diffHours}h ago`
  const diffDays = Math.floor(diffHours / 24)
  if (diffDays < 7)   return `${diffDays}d ago`
  return date.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' })
}

// ─── Shared sub-components ─────────────────────────────────────────────────────

/**
 * A single chat message bubble.
 * Keeps rendering logic in ONE place — no more copy-pasted bubbles across pages.
 */
function ChatBubble({ msg, isStreaming }) {
  const isUser = msg.role === 'user'
  return (
    <div className={`flex items-start gap-3 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      {/* Avatar */}
      <div
        className={`mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full
          ${isUser ? 'bg-gray-700' : 'bg-primary/20'}`}
      >
        {isUser
          ? <User size={15} className="text-gray-300" />
          : <Bot  size={15} className="text-primary"  />
        }
      </div>

      {/* Bubble + agent tag */}
      <div
        className={`flex flex-col gap-1
          ${isUser
            ? 'items-end max-w-[min(40rem,86%)]'
            : 'items-start max-w-[min(50rem,90%)]'
          }`}
      >
        <div
          className={`rounded-2xl px-4 py-3 text-sm leading-relaxed
            ${isUser
              ? 'rounded-tr-sm bg-primary text-white'
              : 'rounded-tl-sm border border-gray-800 bg-surface text-gray-100'
            }`}
        >
          {isUser
            ? <span className="whitespace-pre-wrap">{msg.content}</span>
            : <MarkdownMessage content={msg.content} streaming={isStreaming} />
          }
        </div>
        {msg.agent_name && !isUser && (
          <span className="px-1 text-[11px] text-gray-500">
            via {agentLabel(msg.agent_name)}
          </span>
        )}
      </div>
    </div>
  )
}

/** Animated typing / routing indicator. */
function StatusBubble({ statusMsg, activeAgent }) {
  return (
    <div className="flex items-start gap-3">
      <div className="mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-primary/20">
        <Bot size={15} className="text-primary" />
      </div>
      <div className="rounded-2xl rounded-tl-sm border border-gray-800 bg-surface px-4 py-3 text-sm">
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

// ─── Sidebar session item ──────────────────────────────────────────────────────

function SessionItem({ session, isActive, onSelect, onDelete }) {
  return (
    <div
      onClick={() => onSelect(session.id)}
      className={`group flex cursor-pointer items-center justify-between rounded-lg border p-3 transition
        ${isActive
          ? 'border-indigo-500/50 bg-indigo-500/15 shadow-[0_0_0_1px_rgba(99,102,241,0.15)]'
          : 'border-transparent hover:border-gray-700 hover:bg-gray-800/60'
        }`}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span
            className={`h-1.5 w-1.5 flex-shrink-0 rounded-full
              ${isActive ? 'bg-indigo-400' : 'bg-gray-600 group-hover:bg-indigo-400'}`}
          />
          <span className="truncate text-sm font-medium text-white">
            {session.title || 'Untitled Chat'}
          </span>
        </div>
        <p className="mt-0.5 pl-3.5 text-[11px] text-gray-500">
          {formatSessionTime(session.updated_at || session.created_at)}
        </p>
      </div>

      <button
        onClick={(e) => { e.stopPropagation(); onDelete(session.id) }}
        className={`ml-2 flex-shrink-0 rounded-md p-1.5 text-gray-600 transition hover:bg-red-500/10 hover:text-red-400
          ${isActive ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}`}
        title="Delete session"
      >
        <Trash2 size={13} />
      </button>
    </div>
  )
}

// ─── Suggested starter prompts shown in empty chat ─────────────────────────────

const STARTERS = [
  'Analyze my spending',
  'Am I overspending?',
  'How can I save more?',
  "Where does my money go?",
]

// ─── Main page ─────────────────────────────────────────────────────────────────

export default function Chat() {
  const { user }   = useAuth()
  const navigate   = useNavigate()
  const bottomRef  = useRef(null)

  const [sessions,    setSessions]    = useState([])
  const [activeId,    setActiveId]    = useState(null)
  const [messages,    setMessages]    = useState([])
  const [input,       setInput]       = useState('')
  const [streaming,   setStreaming]   = useState(false)
  const [statusMsg,   setStatusMsg]   = useState('')
  const [activeAgent, setActiveAgent] = useState('')

  // Load sessions on mount
  useEffect(() => { loadSessions() }, [])

  // Auto-scroll to bottom
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, statusMsg])

  // ─── Data loaders ────────────────────────────────────────────────────────

  async function loadSessions() {
    try {
      const res = await getSessions()
      setSessions(res.data.sessions || [])
    } catch {
      toast.error('Could not load sessions')
    }
  }

  async function loadMessages(sessionId) {
    try {
      const res = await getMessages(sessionId)
      setMessages(res.data.messages || [])
    } catch {
      toast.error('Could not load chat history')
    }
  }

  // ─── Session actions ─────────────────────────────────────────────────────

  async function handleNewChat() {
    try {
      const res = await createSession()
      const session = res.data.session
      setSessions((prev) => [session, ...prev])
      setActiveId(session.id)
      setMessages([])
    } catch {
      toast.error('Could not create session')
    }
  }

  function handleSelectSession(id) {
    setActiveId(id)
    loadMessages(id)
  }

  async function handleDeleteSession(id) {
    try {
      await deleteSession(id)
      setSessions((prev) => prev.filter((s) => s.id !== id))
      if (activeId === id) {
        setActiveId(null)
        setMessages([])
      }
    } catch {
      toast.error('Could not delete session')
    }
  }

  // ─── Messaging ───────────────────────────────────────────────────────────

  async function handleSend() {
    const text = input.trim()
    if (!text || !activeId || streaming) return

    setMessages((prev) => [
      ...prev,
      { role: 'user',      content: text, agent_name: '' },
      { role: 'assistant', content: '',   agent_name: '' },
    ])
    setInput('')
    setStreaming(true)
    setStatusMsg('Analyzing your request…')
    setActiveAgent('')

    let fullText  = ''
    let agentName = 'advisor'

    try {
      const response = await sendMessage(activeId, text, '')

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
          throw new Error(event.content || 'Stream failed')
        }
      })

      // Refresh session list so title updates in sidebar
      await loadSessions()
    } catch {
      toast.error('Something went wrong. Please try again.')
      setMessages((prev) => prev.slice(0, -2))
    } finally {
      setStreaming(false)
      setStatusMsg('')
      setActiveAgent('')
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  // ─── Render ──────────────────────────────────────────────────────────────

  return (
    <div className="flex h-screen overflow-hidden bg-base">

      {/* ── Left sidebar ─────────────────────────────────────────────── */}
      <aside className="flex w-64 flex-shrink-0 flex-col border-r border-gray-800 bg-surface">

        {/* Sidebar header */}
        <div className="flex items-center gap-3 border-b border-gray-800 px-4 py-3.5">
          <button
            onClick={() => navigate('/dashboard')}
            className="rounded-lg p-1.5 text-gray-400 transition hover:bg-gray-800 hover:text-white"
            title="Back to Dashboard"
          >
            <ArrowLeft size={17} />
          </button>
          <div className="flex items-center gap-2">
            <MessageSquare size={15} className="text-indigo-400" />
            <span className="text-sm font-semibold text-white">Advisor Chat</span>
          </div>
        </div>

        {/* New chat button */}
        <div className="px-3 pt-3">
          <button
            onClick={handleNewChat}
            className="flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-gray-700 py-2.5 text-sm text-gray-400 transition hover:border-indigo-500/50 hover:text-indigo-300"
          >
            <Plus size={15} />
            New Chat
          </button>
        </div>

        {/* Session list */}
        <div className="flex-1 space-y-1 overflow-y-auto px-3 py-3">
          {sessions.length === 0 ? (
            <p className="py-6 text-center text-xs text-gray-600">No chats yet</p>
          ) : (
            sessions.map((s) => (
              <SessionItem
                key={s.id}
                session={s}
                isActive={activeId === s.id}
                onSelect={handleSelectSession}
                onDelete={handleDeleteSession}
              />
            ))
          )}
        </div>

        {/* User email at bottom */}
        <div className="border-t border-gray-800 px-4 py-3">
          <p className="truncate text-xs text-gray-600">{user?.email}</p>
        </div>
      </aside>

      {/* ── Main chat area ────────────────────────────────────────────── */}
      <div className="flex flex-1 flex-col overflow-hidden">

        {/* No session selected — landing screen */}
        {!activeId ? (
          <div className="flex flex-1 items-center justify-center bg-base p-6">
            <div className="w-full max-w-lg rounded-2xl border border-indigo-500/20 bg-gradient-to-b from-indigo-500/10 to-transparent p-10 text-center">
              <div className="mb-4 flex justify-center">
                <div className="flex h-16 w-16 items-center justify-center rounded-2xl border border-indigo-500/25 bg-indigo-500/10">
                  <Bot size={32} className="text-indigo-400" />
                </div>
              </div>
              <h2 className="mb-2 text-2xl font-bold text-white">
                Talk To Your {APP_NAME} Advisor
              </h2>
              <p className="mb-6 text-sm leading-relaxed text-gray-400">
                Get personalised advice on budgeting, savings, debt control,
                and monthly spending decisions.
              </p>

              <div className="mb-6 flex flex-wrap justify-center gap-2">
                {['Spending Analysis', 'Savings Strategy', 'Budget Health', 'Goal Guidance'].map((tag) => (
                  <span
                    key={tag}
                    className="rounded-full border border-indigo-400/25 bg-indigo-500/10 px-3 py-1 text-xs text-indigo-300"
                  >
                    {tag}
                  </span>
                ))}
              </div>

              <div className="flex flex-col items-center gap-3">
                <button
                  onClick={handleNewChat}
                  className="flex items-center gap-2 rounded-xl bg-primary px-6 py-2.5 text-sm font-semibold text-white transition hover:bg-indigo-500"
                >
                  <Plus size={16} />
                  Start New Chat
                </button>
                <button
                  onClick={() => navigate('/plan')}
                  className="flex items-center gap-2 text-sm text-gray-500 transition hover:text-purple-300"
                >
                  <Wallet size={14} />
                  Open Salary Spending Planner
                </button>
              </div>
            </div>
          </div>

        ) : (
          /* Session selected — show messages */
          <>
            {/* Messages scroll area */}
            <div className="flex-1 space-y-5 overflow-y-auto p-5">

              {/* Empty chat prompt */}
              {messages.length === 0 && (
                <div className="mx-auto mt-12 max-w-lg text-center">
                  <div className="mb-3 flex justify-center">
                    <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-indigo-500/25 bg-indigo-500/10">
                      <Bot size={24} className="text-indigo-400" />
                    </div>
                  </div>
                  <h3 className="mb-1 text-lg font-semibold text-white">
                    What do you want to improve this month?
                  </h3>
                  <p className="mb-5 text-sm text-gray-500">
                    Ask anything about spending, savings, or your financial goals.
                  </p>
                  <div className="flex flex-wrap justify-center gap-2">
                    {STARTERS.map((q) => (
                      <button
                        key={q}
                        onClick={() => setInput(q)}
                        className="rounded-lg border border-gray-700 bg-surface px-3 py-1.5 text-sm text-gray-300 transition hover:border-indigo-500/50 hover:text-white"
                      >
                        {q}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* Messages */}
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

              {/* Streaming indicator */}
              {streaming && (statusMsg || activeAgent) && (
                <StatusBubble statusMsg={statusMsg} activeAgent={activeAgent} />
              )}

              <div ref={bottomRef} />
            </div>

            {/* Input bar */}
            <div className="flex-shrink-0 border-t border-gray-800 bg-surface p-4">
              <div className="mx-auto flex max-w-3xl items-end gap-3">
                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Ask your finance advisor anything… (Enter to send)"
                  rows={1}
                  style={{ maxHeight: '120px' }}
                  className="flex-1 resize-none rounded-xl border border-gray-700 bg-base px-4 py-3 text-sm text-white placeholder-gray-500 transition focus:border-primary focus:outline-none"
                />
                <button
                  onClick={handleSend}
                  disabled={!input.trim() || streaming}
                  className="flex-shrink-0 rounded-xl bg-primary p-3 text-white transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <Send size={17} />
                </button>
              </div>
              <p className="mt-2 text-center text-[11px] text-gray-600">
                Your data is anonymized before processing
              </p>
            </div>
          </>
        )}
      </div>
    </div>
  )
}