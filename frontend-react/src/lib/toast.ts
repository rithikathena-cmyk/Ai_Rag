export type ToastTone = 'success' | 'error' | 'info'

export interface Toast {
  id: string
  message: string
  tone: ToastTone
  duration: number
}

let toasts: Toast[] = []
let nextId = 0
const listeners = new Set<() => void>()

function emit() {
  for (const listener of listeners) listener()
}

export function subscribeToasts(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function getToasts() {
  return toasts
}

function push(message: string, tone: ToastTone, duration: number) {
  nextId += 1
  const id = `toast-${nextId}`
  toasts = [...toasts, { id, message, tone, duration }]
  emit()
  if (duration > 0) {
    setTimeout(() => dismissToast(id), duration)
  }
  return id
}

export function dismissToast(id: string) {
  toasts = toasts.filter((t) => t.id !== id)
  emit()
}

export const toast = {
  success: (message: string, duration = 3000) => push(message, 'success', duration),
  error: (message: string, duration = 4500) => push(message, 'error', duration),
  info: (message: string, duration = 3000) => push(message, 'info', duration),
}
