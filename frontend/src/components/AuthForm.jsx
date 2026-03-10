/**
 * AuthForm.jsx — Shared authentication form wrapper
 *
 * WHY THIS EXISTS:
 * Login.jsx and Signup.jsx were 95% identical — same inline styles,
 * same card layout, same logo, same submit button. This component
 * holds all that shared structure so each page only defines what's unique.
 *
 * Also fixes:
 * - Inline styles replaced with Tailwind (consistent with rest of app)
 * - Input focus ring now uses indigo to match app theme
 * - "Loading..." screen in App.jsx now also uses this bg colour
 */

import { Link } from 'react-router-dom'
import { APP_NAME } from '../lib/branding'

/**
 * Reusable labelled input field.
 * Keeps label + input together, handles focus ring colour.
 */
export function AuthInput({ label, hint, ...inputProps }) {
  return (
    <div className="space-y-1.5">
      <label className="flex items-center gap-2 text-sm text-gray-400">
        {label}
        {hint && <span className="text-xs text-gray-600">{hint}</span>}
      </label>
      <input
        {...inputProps}
        className="w-full rounded-xl border border-gray-700 bg-gray-950 px-4 py-3 text-sm text-white placeholder-gray-600 outline-none transition focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500/30"
      />
    </div>
  )
}

/**
 * Full-page authentication layout.
 * Used by both Login and Signup pages.
 */
export default function AuthForm({
  title,          // Card heading — "Sign in" or "Create account"
  tagline,        // Shown under the logo
  onSubmit,       // Form submit handler
  loading,        // Disables submit button when true
  submitLabel,    // "Sign in" | "Create account"
  loadingLabel,   // "Signing in…" | "Creating account…"
  bottomLink,     // { text, linkText, to }
  children,       // The input fields
}) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-950 px-4 py-8">
      <div className="w-full max-w-[420px]">

        {/* Logo + tagline */}
        <div className="mb-8 text-center">
          <div className="mb-3 text-5xl">💰</div>
          <h1 className="text-2xl font-bold text-white">{APP_NAME}</h1>
          <p className="mt-1.5 text-sm text-gray-500">{tagline}</p>
        </div>

        {/* Card */}
        <div className="rounded-2xl border border-gray-800 bg-gray-900 p-8">
          <h2 className="mb-6 text-lg font-semibold text-white">{title}</h2>

          <form onSubmit={onSubmit} className="space-y-5">
            {children}

            {/* Submit button */}
            <button
              type="submit"
              disabled={loading}
              className="mt-2 w-full rounded-xl bg-indigo-600 py-3 text-sm font-semibold text-white transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {loading ? loadingLabel : submitLabel}
            </button>
          </form>

          {/* Link to other auth page */}
          {bottomLink && (
            <p className="mt-6 text-center text-sm text-gray-500">
              {bottomLink.text}{' '}
              <Link to={bottomLink.to} className="font-medium text-indigo-400 transition hover:text-indigo-300">
                {bottomLink.linkText}
              </Link>
            </p>
          )}
        </div>

        {/* Privacy note */}
        <p className="mt-4 text-center text-xs text-gray-700">
          🔒 Your financial data is always anonymized and secure
        </p>
      </div>
    </div>
  )
}