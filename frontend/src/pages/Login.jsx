/**
 * Login.jsx
 * Uses the shared AuthForm layout — no more duplicated inline styles.
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { loginUser } from '../lib/api'
import { useAuth } from '../context/AuthContext'
import AuthForm, { AuthInput } from '../components/AuthForm'

export default function Login() {
  const [email,    setEmail]    = useState('')
  const [password, setPassword] = useState('')
  const [loading,  setLoading]  = useState(false)
  const { login }  = useAuth()
  const navigate   = useNavigate()

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!email || !password) {
      toast.error('Please fill in all fields')
      return
    }
    setLoading(true)
    try {
      const res = await loginUser({ email, password })
      await login(
        { user_id: res.data.user_id, email: res.data.email, full_name: res.data.full_name },
        res.data.access_token,
        res.data.refresh_token,
      )
      toast.success('Welcome back!')
      navigate('/dashboard')
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Invalid email or password')
    } finally {
      setLoading(false)
    }
  }

  return (
    <AuthForm
      title="Sign in to your account"
      tagline="Your AI-powered financial copilot"
      onSubmit={handleSubmit}
      loading={loading}
      submitLabel="Sign in"
      loadingLabel="Signing in…"
      bottomLink={{ text: "Don't have an account?", linkText: 'Create one', to: '/signup' }}
    >
      <AuthInput
        label="Email address"
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="you@example.com"
        required
        autoComplete="email"
      />
      <AuthInput
        label="Password"
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        placeholder="••••••••"
        required
        autoComplete="current-password"
      />
    </AuthForm>
  )
}