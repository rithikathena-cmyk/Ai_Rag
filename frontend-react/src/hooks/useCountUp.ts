import { useEffect, useRef, useState } from 'react'

const prefersReducedMotion = () =>
  typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

/** Animates from the previous value to `target` with an ease-out curve.
 * Skips straight to `target` if the value hasn't changed, `target` isn't a
 * finite number yet (still loading), or the user prefers reduced motion. */
export function useCountUp(target: number | null | undefined, durationMs = 700): number {
  const [value, setValue] = useState(0)
  const fromRef = useRef(0)

  useEffect(() => {
    if (target == null || !Number.isFinite(target)) return
    const safeTarget = target

    if (prefersReducedMotion()) {
      setValue(safeTarget)
      fromRef.current = safeTarget
      return
    }

    const from = fromRef.current
    const delta = safeTarget - from
    if (delta === 0) return

    const start = performance.now()
    let raf: number
    function tick(now: number) {
      const progress = Math.min((now - start) / durationMs, 1)
      const eased = 1 - Math.pow(1 - progress, 3)
      setValue(Math.round(from + delta * eased))
      if (progress < 1) {
        raf = requestAnimationFrame(tick)
      } else {
        fromRef.current = safeTarget
      }
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [target, durationMs])

  return value
}
