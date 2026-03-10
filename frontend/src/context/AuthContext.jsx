/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useEffect, useState } from 'react'
import { supabase } from '../lib/supabase'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let mounted = true

    const initAuth = async () => {
      const accessToken = localStorage.getItem('access_token')
      const refreshToken = localStorage.getItem('refresh_token')
      const savedUser = localStorage.getItem('user')

      if (accessToken && refreshToken && savedUser) {
        try {
          const { data, error } = await supabase.auth.setSession({
            access_token: accessToken,
            refresh_token: refreshToken,
          })
          if (error || !data.session) throw new Error('Invalid session')

          localStorage.setItem('access_token', data.session.access_token)
          localStorage.setItem('refresh_token', data.session.refresh_token)
          if (mounted) setUser(JSON.parse(savedUser))
        } catch {
          localStorage.clear()
          if (mounted) setUser(null)
        }
      } else if (mounted) {
        setUser(null)
      }

      if (mounted) setLoading(false)
    }

    initAuth()

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((event, session) => {
      if ((event === 'TOKEN_REFRESHED' || event === 'SIGNED_IN') && session) {
        localStorage.setItem('access_token', session.access_token)
        localStorage.setItem('refresh_token', session.refresh_token)
        if (session.user) {
          const userData = {
            user_id: session.user.id,
            email: session.user.email,
            full_name: session.user.user_metadata?.full_name || '',
          }
          localStorage.setItem('user', JSON.stringify(userData))
          setUser(userData)
        }
      }

      if (event === 'SIGNED_OUT') {
        localStorage.clear()
        setUser(null)
      }
    })

    return () => {
      mounted = false
      subscription.unsubscribe()
    }
  }, [])

  const login = async (userData, accessToken, refreshToken) => {
    localStorage.setItem('access_token', accessToken)
    localStorage.setItem('refresh_token', refreshToken)
    localStorage.setItem('user', JSON.stringify(userData))
    try {
      await supabase.auth.setSession({
        access_token: accessToken,
        refresh_token: refreshToken,
      })
    } catch {
      // Keep local token fallback.
    }
    setUser(userData)
  }

  const logout = () => {
    supabase.auth.signOut()
    localStorage.clear()
    setUser(null)
  }

  return <AuthContext.Provider value={{ user, login, logout, loading }}>{children}</AuthContext.Provider>
}

export const useAuth = () => useContext(AuthContext)
