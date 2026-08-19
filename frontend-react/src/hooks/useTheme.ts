import { useCallback, useEffect, useState } from 'react'

export type ThemeChoice = 'light' | 'dark' | 'system'

const STORAGE_KEY = 'athena-theme'

function apply(choice: ThemeChoice) {
  const root = document.documentElement
  if (choice === 'system') {
    root.removeAttribute('data-theme')
  } else {
    root.setAttribute('data-theme', choice)
  }
}

function readStored(): ThemeChoice {
  const stored = localStorage.getItem(STORAGE_KEY)
  return stored === 'light' || stored === 'dark' ? stored : 'system'
}

/** Persists to localStorage and stamps `data-theme` on <html> — index.css's
 * dark-mode CSS variable overrides key off that same attribute (or, for
 * "system", off the OS `prefers-color-scheme` media query directly). */
export function useTheme() {
  const [choice, setChoiceState] = useState<ThemeChoice>(() => readStored())

  useEffect(() => {
    apply(choice)
  }, [choice])

  const setChoice = useCallback((next: ThemeChoice) => {
    setChoiceState(next)
    if (next === 'system') {
      localStorage.removeItem(STORAGE_KEY)
    } else {
      localStorage.setItem(STORAGE_KEY, next)
    }
  }, [])

  const toggle = useCallback(() => {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    const currentlyDark = choice === 'dark' || (choice === 'system' && prefersDark)
    setChoice(currentlyDark ? 'light' : 'dark')
  }, [choice, setChoice])

  return { choice, setChoice, toggle }
}
