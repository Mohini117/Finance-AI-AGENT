/**
 * Signup.jsx
 * Uses the shared AuthForm layout — no more duplicated inline styles.
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { loginUser, signupUser } from '../lib/api'
import { useAuth } from '../context/AuthContext'
import AuthForm, { AuthInput } from '../components/AuthForm'

export default function Signup() {
  const [fullName, setFullName] = useState('')
  const [email,    setEmail]    = useState('')
  const [password, setPassword] = useState('')
  const [loading,  setLoading]  = useState(false)
  const { login }  = useAuth()
  const navigate   = useNavigate()

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!fullName || !email || !password) {
      toast.error('Please fill in all fields')
      return
    }
    if (password.length < 6) {
      toast.error('Password must be at least 6 characters')
      return
    }
    setLoading(true)
    try {
      // Step 1: create account
      await signupUser({ email, password, full_name: fullName })

      // Step 2: auto-login so user lands on dashboard immediately
      const res = await loginUser({ email, password })
      await login(
        { user_id: res.data.user_id, email: res.data.email, full_name: res.data.full_name },
        res.data.access_token,
        res.data.refresh_token,
      )
      toast.success(`Welcome, ${fullName.split(' ')[0]}! 🎉`)
      navigate('/dashboard')
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Signup failed. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <AuthForm
      title="Create your account"
      tagline="Start your financial journey today"
      onSubmit={handleSubmit}
      loading={loading}
      submitLabel="Create account"
      loadingLabel="Creating account…"
      bottomLink={{ text: 'Already have an account?', linkText: 'Sign in', to: '/login' }}
    >
      <AuthInput
        label="Full Name"
        type="text"
        value={fullName}
        onChange={(e) => setFullName(e.target.value)}
        placeholder="John Doe"
        required
        autoComplete="name"
      />
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
        hint="(min 6 characters)"
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        placeholder="••••••••"
        required
        minLength={6}
        autoComplete="new-password"
      />
    </AuthForm>
  )
}