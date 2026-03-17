const rawApiUrl = import.meta.env.VITE_API_URL?.trim()

export const API_BASE_URL = rawApiUrl
  ? rawApiUrl.replace(/\/$/, '')
  : (import.meta.env.DEV ? 'http://localhost:8000' : '')
