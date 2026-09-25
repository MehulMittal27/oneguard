import type { ReactNode } from 'react'
import { ApiError } from '../api/client'

export function Spinner() {
  return (
    <div className="flex items-center gap-2 text-[13px] text-ink-muted">
      <span className="size-3 animate-spin rounded-full border-2 border-border-quiet border-t-cord-accent" />
      Loading…
    </div>
  )
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="rounded-card border border-dashed border-border-dashed p-8 text-center text-[13px] text-ink-muted">{children}</div>
}

/** Renders an ApiError with its code and detail, or a plain "unreachable" line for a
 * network failure — root CLAUDE.md rule 10: a screen with no reason for its state is a bug. */
export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const isApiError = error instanceof ApiError
  return (
    <div className="rounded-card border border-stopped-border bg-stopped-tint p-5 text-[13px] text-stopped">
      <p className="font-semibold">
        {isApiError ? `${error.code} (${error.status})` : 'Backend unreachable'}
      </p>
      <p className="mt-1 text-stopped/90">
        {isApiError ? error.message : 'Check that the backend is running at the configured API base URL.'}
      </p>
      {isApiError && error.detail !== undefined && (
        <pre className="mt-2 overflow-x-auto rounded-tile bg-surface/60 p-2 text-[11px] text-ink-soft">
          {JSON.stringify(error.detail, null, 2)}
        </pre>
      )}
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded-button border border-stopped-border px-3 py-1.5 text-[12px] font-semibold text-stopped"
        >
          Retry
        </button>
      )}
    </div>
  )
}
