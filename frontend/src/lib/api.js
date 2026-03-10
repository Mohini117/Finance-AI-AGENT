import axios from 'axios'

const BASE_URL = import.meta.env.VITE_API_URL

const api = axios.create({ baseURL: BASE_URL })

const redirectToLogin = () => {
  localStorage.clear()
  if (window.location.pathname !== '/login') {
    window.location.href = '/login'
  }
}

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      redirectToLogin()
    }
    return Promise.reject(error)
  }
)

export const loginUser          = (data)       => api.post('/auth/login', data)
export const signupUser         = (data)       => api.post('/auth/signup', data)
export const getSessions        = ()           => api.get('/chat/sessions')
export const createSession      = ()           => api.post('/chat/sessions')
export const deleteSession      = (id)         => api.delete(`/chat/sessions/${id}`)
export const getMessages        = (sessionId)  => api.get(`/chat/sessions/${sessionId}/messages`)
export const uploadTransactions = (formData)   => api.post('/transactions/upload', formData)
export const getTransactions    = ()           => api.get('/transactions/')

export const sendMessage = async (sessionId, message, userGoal = '') => {
  const token = localStorage.getItem('access_token')
  const response = await fetch(`${BASE_URL}/chat/message`, {
    method : 'POST',
    headers: {
      'Content-Type' : 'application/json',
      'Authorization': `Bearer ${token}`
    },
    body: JSON.stringify({
      session_id: sessionId,
      message,
      user_goal : userGoal
    })
  })

  if (response.status === 401) {
    redirectToLogin()
    throw new Error('Unauthorized')
  }
  return response
}


// ── Spending Planner endpoints ────────────────────────────────────────────────

export const getLatestPlan = (sessionId = '') =>
  api.get(`/plan/latest${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''}`)

export const getPlanHistory = () =>
  api.get('/plan/history')

export const sendPlanMessage = async (sessionId, message) => {
  const token = localStorage.getItem('access_token')
  const response = await fetch(`${BASE_URL}/plan/chat`, {
    method : 'POST',
    headers: {
      'Content-Type' : 'application/json',
      'Authorization': `Bearer ${token}`
    },
    body: JSON.stringify({ session_id: sessionId, message })
  })
  if (response.status === 401) {
    redirectToLogin()
    throw new Error('Unauthorized')
  }
  return response
}

export default api
