import { type InputHTMLAttributes, type ReactNode, forwardRef } from 'react'
import { cn } from '@/lib/cn'

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  error?: string
  startAdornment?: ReactNode
  endAdornment?: ReactNode
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, error, startAdornment, endAdornment, ...props }, ref) => {
    return (
      <div className="w-full">
        <div className="relative">
          {startAdornment && (
            <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-neutral-400">
              {startAdornment}
            </div>
          )}
          <input
            ref={ref}
            className={cn(
              'w-full rounded-lg border border-neutral-300 bg-surface px-3 py-2 text-sm text-ink transition-colors duration-150',
              'placeholder:text-neutral-400 focus:outline-none focus:ring-2 focus:ring-accent-400',
              error && 'border-red-400 focus:ring-red-400',
              startAdornment && 'pl-9',
              endAdornment && 'pr-9',
              className,
            )}
            {...props}
          />
          {endAdornment && (
            <div className="absolute inset-y-0 right-0 flex items-center pr-2.5">{endAdornment}</div>
          )}
        </div>
        {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
      </div>
    )
  },
)
Input.displayName = 'Input'
