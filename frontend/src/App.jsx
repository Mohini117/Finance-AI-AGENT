import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Toaster } from 'react-hot-toast'
import { AuthProvider, useAuth } from './context/AuthContext'
import Login     from './pages/Login'
import Signup    from './pages/Signup'
import Dashboard from './pages/Dashboard'
import Chat      from './pages/Chat'
import Plan from "./pages/Plan";
  

// ─────────────────────────────────────────────
// Protects routes — redirects to /login if not authenticated
// ─────────────────────────────────────────────
function PrivateRoute({ children }) {
  const { user, loading } = useAuth()

  if (loading) return (
    <div style={{
      minHeight      : '100vh',
      display        : 'flex',
      alignItems     : 'center',
      justifyContent : 'center',
      backgroundColor: '#13131f',
      color          : '#9ca3af'
    }}>
      Loading...
    </div>
  )

  return user ? children : <Navigate to="/login" replace />
}

// ─────────────────────────────────────────────
// Redirects logged-in users away from auth pages
// ─────────────────────────────────────────────
function AuthRoute({ children }) {
  const { user, loading } = useAuth()

  if (loading) return (
    <div style={{
      minHeight      : '100vh',
      display        : 'flex',
      alignItems     : 'center',
      justifyContent : 'center',
      backgroundColor: '#13131f',
      color          : '#9ca3af'
    }}>
      Loading...
    </div>
  )

  return user ? <Navigate to="/dashboard" replace /> : children
}

function AppRoutes() {
  return (
    <Routes>
      {/* Public routes — redirect to dashboard if already logged in */}
      <Route path="/login"  element={<AuthRoute><Login  /></AuthRoute>} />
      <Route path="/signup" element={<AuthRoute><Signup /></AuthRoute>} />

      {/* Protected routes — redirect to login if not authenticated */}
      <Route path="/dashboard" element={<PrivateRoute><Dashboard /></PrivateRoute>} />
      <Route path="/chat"      element={<PrivateRoute><Chat      /></PrivateRoute>} />
      <Route path="/plan"      element={<PrivateRoute><Plan      /></PrivateRoute>} />

      {/* Default redirect */}
      <Route path="/"  element={<Navigate to="/dashboard" replace />} />
      <Route path="*"  element={<Navigate to="/dashboard" replace />} />
    </Routes>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <AppRoutes />
        <Toaster
          position="top-right"
          toastOptions={{
            style: {
              background: '#1e1e2e',
              color     : '#ffffff',
              border    : '1px solid #374151',
              fontSize  : '14px'
            },
            success: { iconTheme: { primary: '#6366f1', secondary: '#fff' } }
          }}
        />
      </BrowserRouter>
    </AuthProvider>
  )
}
