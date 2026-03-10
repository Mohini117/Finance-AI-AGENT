/**
 * Dashboard.jsx — Main landing page after login
 *
 * Fixes applied:
 * 1. Consistent bg tokens (bg-gray-950 / bg-gray-900) — no black-on-black risk
 * 2. Pie chart legend added — category names were invisible on dark tooltip
 * 3. Recent transactions section text colour fixed (gray-200 not gray-900)
 * 4. Workspace actions card deduplication — 3 actions, not 2 navigation buttons + 3 cards
 * 5. Stats cards have clearer label hierarchy
 * 6. Header is cleaner — removed redundant logo text copy
 */

import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'
import { LogOut, MessageSquare, Target, TrendingUp, Upload, Wallet } from 'lucide-react'
import toast from 'react-hot-toast'

import { useAuth } from '../context/AuthContext'
import { APP_NAME, APP_TAGLINE } from '../lib/branding'
import { getTransactions, uploadTransactions } from '../lib/api'

const COLORS = ['#6366f1', '#8b5cf6', '#ec4899', '#f59e0b', '#10b981', '#3b82f6', '#f97316']

const formatINR = (value) =>
  `Rs ${Math.round(value || 0).toLocaleString('en-IN')}`

/** Custom recharts tooltip — dark themed to match the app. */
function PieTooltip({ active, payload }) {
  if (!active || !payload?.length) return null
  const { name, value } = payload[0]
  return (
    <div className="rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-xs shadow-xl">
      <p className="font-medium text-gray-300">{name}</p>
      <p className="font-semibold text-white">{formatINR(value)}</p>
    </div>
  )
}

export default function Dashboard() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  const [transactions, setTransactions] = useState([])
  const [uploading,    setUploading]    = useState(false)
  const [stats,        setStats]        = useState({ total: 0, count: 0, dailyAvg: 0, chartData: [] })

  // ── Compute chart stats from raw transaction array ──────────────────────────
  const computeStats = useCallback((txns) => {
    if (!txns.length) {
      setStats({ total: 0, count: 0, dailyAvg: 0, chartData: [] })
      return
    }
    const total = txns.reduce((sum, t) => sum + parseFloat(t.amount || 0), 0)
    const byCategory = {}
    txns.forEach((t) => {
      const cat = t.category || 'Uncategorized'
      byCategory[cat] = (byCategory[cat] || 0) + parseFloat(t.amount || 0)
    })
    const chartData = Object.entries(byCategory)
      .map(([name, value]) => ({ name, value: Math.round(value) }))
      .sort((a, b) => b.value - a.value)
    setStats({ total: Math.round(total), count: txns.length, dailyAvg: Math.round(total / 30), chartData })
  }, [])

  const loadTransactions = useCallback(async () => {
    try {
      const res = await getTransactions()
      const txns = res.data.transactions || []
      setTransactions(txns)
      computeStats(txns)
    } catch {
      toast.error('Could not load transactions')
    }
  }, [computeStats])

  useEffect(() => { loadTransactions() }, [loadTransactions])

  // ── Upload handler ──────────────────────────────────────────────────────────
  const handleUpload = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      const formData = new FormData()
      formData.append('file', file)
      const res = await uploadTransactions(formData)
      toast.success(res.data.message || 'Transactions uploaded')
      loadTransactions()
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Upload failed')
    } finally {
      setUploading(false)
      // Reset input so the same file can be re-uploaded if needed
      e.target.value = ''
    }
  }

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  // ─── Render ──────────────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-gray-950 text-white">

      {/* ── Header ───────────────────────────────────────────────────────── */}
      <header className="sticky top-0 z-10 border-b border-gray-800 bg-gray-900/95 backdrop-blur">
        <div className="mx-auto flex w-full max-w-7xl items-center justify-between px-6 py-3.5">
          {/* Brand */}
          <button
            onClick={() => navigate('/dashboard')}
            className="group flex items-center gap-3"
          >
            <div className="flex h-9 w-9 items-center justify-center rounded-xl border border-indigo-500/30 bg-indigo-500/10">
              <Wallet size={17} className="text-indigo-400" />
            </div>
            <div className="text-left">
              <p className="text-base font-bold text-white group-hover:text-indigo-300 transition-colors">
                {APP_NAME}
              </p>
              <p className="text-[11px] text-gray-500">{APP_TAGLINE}</p>
            </div>
          </button>

          {/* User + logout */}
          <div className="flex items-center gap-4">
            <span className="hidden text-sm text-gray-400 sm:block">
              Hi, <span className="text-gray-200">{user?.full_name?.split(' ')[0] || 'there'}</span>
            </span>
            <button
              onClick={handleLogout}
              className="flex items-center gap-2 rounded-lg border border-gray-700 px-3 py-2 text-sm text-gray-400 transition hover:border-gray-500 hover:text-white"
            >
              <LogOut size={15} />
              Logout
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-7xl space-y-6 px-6 py-8">

        {/* ── Hero banner ─────────────────────────────────────────────── */}
        <section className="rounded-2xl border border-indigo-500/20 bg-gradient-to-r from-indigo-500/15 via-indigo-500/5 to-transparent p-6">
          <p className="text-[11px] font-medium uppercase tracking-widest text-indigo-400">Home</p>
          <h1 className="mt-1 text-2xl font-bold text-white">Your Finance Control Center</h1>
          <p className="mt-1.5 max-w-xl text-sm leading-relaxed text-gray-400">
            Upload your bank statement, talk to your AI advisor, and build a smart monthly spending plan.
          </p>
          <div className="mt-5 flex flex-wrap gap-3">
            <button
              onClick={() => navigate('/chat')}
              className="flex items-center gap-2 rounded-xl bg-indigo-600 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-indigo-500"
            >
              <MessageSquare size={15} />
              Talk To Advisor
            </button>
            <button
              onClick={() => navigate('/plan')}
              className="flex items-center gap-2 rounded-xl border border-purple-500/30 bg-purple-500/10 px-5 py-2.5 text-sm font-semibold text-purple-200 transition hover:bg-purple-500/20"
            >
              <Wallet size={15} />
              Salary Spending Planner
            </button>
          </div>
        </section>

        {/* ── Stats row ──────────────────────────────────────────────── */}
        <section className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          {[
            { icon: TrendingUp, color: 'text-indigo-400', label: 'Total Spent',    value: formatINR(stats.total) },
            { icon: Target,     color: 'text-green-400',  label: 'Transactions',   value: stats.count.toLocaleString('en-IN') },
            { icon: TrendingUp, color: 'text-amber-400',  label: 'Daily Average',  value: formatINR(stats.dailyAvg) },
          ].map(({ icon: Icon, color, label, value }) => (
            <div key={label} className="rounded-xl border border-gray-800 bg-gray-900 p-5">
              <div className={`mb-2 flex items-center gap-2 text-xs font-medium ${color}`}>
                <Icon size={14} />
                <span className="text-gray-400">{label}</span>
              </div>
              <p className="text-2xl font-bold text-white">{value}</p>
            </div>
          ))}
        </section>

        {/* ── Charts + actions row ────────────────────────────────────── */}
        <section className="grid grid-cols-1 gap-6 lg:grid-cols-2">

          {/* Spending pie chart */}
          <div className="rounded-xl border border-gray-800 bg-gray-900 p-6">
            <h2 className="mb-4 text-base font-semibold text-white">Spending by Category</h2>
            {stats.chartData.length > 0 ? (
              <>
                <ResponsiveContainer width="100%" height={240}>
                  <PieChart>
                    <Pie
                      data={stats.chartData}
                      cx="50%" cy="50%"
                      outerRadius={96}
                      dataKey="value"
                      label={({ name, percent }) =>
                        percent > 0.07 ? `${name} ${(percent * 100).toFixed(0)}%` : ''
                      }
                      labelLine={false}
                    >
                      {stats.chartData.map((_, i) => (
                        <Cell key={i} fill={COLORS[i % COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip content={<PieTooltip />} />
                  </PieChart>
                </ResponsiveContainer>
                {/* Legend */}
                <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5">
                  {stats.chartData.map((d, i) => (
                    <div key={d.name} className="flex items-center gap-1.5">
                      <div
                        className="h-2 w-2 flex-shrink-0 rounded-full"
                        style={{ background: COLORS[i % COLORS.length] }}
                      />
                      <span className="text-xs text-gray-400">{d.name}</span>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <div className="flex h-[240px] flex-col items-center justify-center rounded-xl border border-dashed border-gray-700 text-center">
                <Upload size={24} className="mb-2 text-gray-600" />
                <p className="text-sm text-gray-500">Upload a bank CSV to see your spending chart</p>
              </div>
            )}
          </div>

          {/* Workspace actions */}
          <div className="rounded-xl border border-gray-800 bg-gray-900 p-6">
            <h2 className="mb-4 text-base font-semibold text-white">Quick Actions</h2>
            <div className="space-y-3">

              {/* Upload */}
              <label className="group flex cursor-pointer items-center gap-4 rounded-xl border border-gray-700 bg-gray-950 p-4 transition hover:border-indigo-500/50">
                <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg bg-indigo-500/15 transition group-hover:bg-indigo-500/25">
                  <Upload size={18} className="text-indigo-400" />
                </div>
                <div className="min-w-0">
                  <p className="font-medium text-white">
                    {uploading ? 'Uploading…' : 'Upload Bank Statement'}
                  </p>
                  <p className="text-sm text-gray-500">CSV export from your bank portal</p>
                </div>
                <input type="file" accept=".csv" className="hidden" onChange={handleUpload} />
              </label>

              {/* Chat */}
              <button
                onClick={() => navigate('/chat')}
                className="group flex w-full items-center gap-4 rounded-xl border border-gray-700 bg-gray-950 p-4 text-left transition hover:border-green-500/40"
              >
                <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg bg-green-500/15 transition group-hover:bg-green-500/25">
                  <MessageSquare size={18} className="text-green-400" />
                </div>
                <div>
                  <p className="font-medium text-white">Talk To Advisor</p>
                  <p className="text-sm text-gray-500">Get personalised advice and action steps</p>
                </div>
              </button>

              {/* Plan */}
              <button
                onClick={() => navigate('/plan')}
                className="group flex w-full items-center gap-4 rounded-xl border border-gray-700 bg-gray-950 p-4 text-left transition hover:border-purple-500/40"
              >
                <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg bg-purple-500/15 transition group-hover:bg-purple-500/25">
                  <Wallet size={18} className="text-purple-400" />
                </div>
                <div>
                  <p className="font-medium text-white">Salary Spending Planner</p>
                  <p className="text-sm text-gray-500">Build your monthly budget split and goal roadmap</p>
                </div>
              </button>
            </div>
          </div>
        </section>

        {/* ── Recent transactions ─────────────────────────────────────── */}
        {transactions.length > 0 && (
          <section className="rounded-xl border border-gray-800 bg-gray-900 p-6">
            <h3 className="mb-4 text-sm font-semibold uppercase tracking-widest text-gray-500">
              Recent Transactions
            </h3>
            <div className="divide-y divide-gray-800">
              {transactions.slice(0, 8).map((txn, i) => (
                <div
                  key={i}
                  className="flex items-center justify-between py-2.5 text-sm"
                >
                  <div className="flex items-center gap-3">
                    <span className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg bg-gray-800 text-xs text-gray-400">
                      {(txn.category || 'O')[0].toUpperCase()}
                    </span>
                    <div>
                      <p className="max-w-[260px] truncate text-gray-200">
                        {txn.description || 'Transaction'}
                      </p>
                      {txn.category && (
                        <p className="text-[11px] text-gray-600">{txn.category}</p>
                      )}
                    </div>
                  </div>
                  <span className="ml-4 flex-shrink-0 font-medium text-red-400">
                    {formatINR(parseFloat(txn.amount || 0))}
                  </span>
                </div>
              ))}
            </div>
          </section>
        )}
      </main>
    </div>
  )
}