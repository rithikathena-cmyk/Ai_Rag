import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios'
import { tokenStorage } from '@/lib/tokenStorage'

// '/api' (relative) works for local dev, where Vite's own proxy forwards it
// to the backend (see vite.config.ts), and for any deploy where the frontend
// and backend share an origin. A statically-hosted build (e.g. Vercel) has
// no such proxy at runtime, so VITE_API_BASE_URL lets the build point
// directly at the backend's own origin (e.g. an ngrok tunnel URL) instead.
export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
})

let onUnauthorized: (() => void) | null = null

export function setUnauthorizedHandler(handler: () => void) {
  onUnauthorized = handler
}

api.interceptors.request.use((config) => {
  const token = tokenStorage.getAccess()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  // A free ngrok tunnel serves an HTML "visit site" interstitial in place of
  // the real response unless this header is present — harmless against any
  // other backend origin, so it's sent unconditionally rather than only when
  // VITE_API_BASE_URL happens to be an ngrok domain.
  config.headers['ngrok-skip-browser-warning'] = 'true'
  return config
})

let refreshPromise: Promise<string | null> | null = null

async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = tokenStorage.getRefresh()
  if (!refreshToken) return null

  try {
    const { data } = await axios.post<{ access_token: string; token_type: string }>(
      '/api/auth/refresh',
      { refresh_token: refreshToken },
    )
    tokenStorage.setAccess(data.access_token)
    return data.access_token
  } catch {
    return null
  }
}

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as (InternalAxiosRequestConfig & { _retried?: boolean }) | undefined

    if (error.response?.status !== 401 || !original || original._retried) {
      throw error
    }
    if (
      original.url?.includes('/auth/login') ||
      original.url?.includes('/auth/demo-login') ||
      original.url?.includes('/auth/refresh')
    ) {
      throw error
    }

    original._retried = true
    refreshPromise ??= refreshAccessToken().finally(() => {
      refreshPromise = null
    })

    const newToken = await refreshPromise
    if (!newToken) {
      tokenStorage.clear()
      onUnauthorized?.()
      throw error
    }

    original.headers.Authorization = `Bearer ${newToken}`
    return api.request(original)
  },
)
