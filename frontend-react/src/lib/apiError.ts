import { AxiosError } from 'axios'

export interface ApiError {
  message: string
  code: string | null
  requestId: string | null
  isNetworkError: boolean
  status: number | null
}

interface ErrorEnvelope {
  error?: { code?: string; message?: string; request_id?: string }
}

const FALLBACK_MESSAGE = 'Something went wrong. Please try again.'

export function getApiError(err: unknown, fallback = FALLBACK_MESSAGE): ApiError {
  if (err instanceof AxiosError) {
    if (!err.response) {
      return {
        message: "Can't reach the server. Check your connection and try again.",
        code: 'network_error',
        requestId: null,
        isNetworkError: true,
        status: null,
      }
    }
    const body = err.response.data as ErrorEnvelope | undefined
    return {
      message: body?.error?.message || fallback,
      code: body?.error?.code ?? null,
      requestId: body?.error?.request_id ?? null,
      isNetworkError: false,
      status: err.response.status,
    }
  }
  return { message: fallback, code: null, requestId: null, isNetworkError: false, status: null }
}
