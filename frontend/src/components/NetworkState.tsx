import type { ReactNode } from 'react'

export type NetworkStateKind = 'loading' | 'error' | 'empty'

export function NetworkState({
  kind,
  label,
  onRetry,
  children,
}: {
  kind: NetworkStateKind
  label: string
  onRetry?: () => void
  children?: ReactNode
}) {
  if (kind === 'loading') {
    return (
      <div className="flex flex-col gap-3" aria-live="polite" aria-busy="true">
        <div className="h-24 animate-pulse rounded-card bg-surface-sunken" />
        <span className="sr-only">Loading {label}</span>
      </div>
    )
  }

  return (
    <div className="flex flex-col items-start gap-4 rounded-row border border-hairline bg-surface p-5">
      <p className="text-[15px] text-ink-soft">
        {kind === 'empty' ? children ?? `No ${label} yet.` : children ?? `Couldn't load ${label}. Nothing was approved while we were offline.`}
      </p>
      {kind === 'error' && onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="h-11.5 rounded-button border-2 border-ink px-5 text-[15px] font-semibold text-ink"
        >
          Try again
        </button>
      )}
    </div>
  )
}
