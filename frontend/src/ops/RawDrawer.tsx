import { useEffect, useState } from 'react'
import { getReceipt } from '../api/passport'
import type { Decision } from '../api/types'
import { CheckIcon, CrossIcon } from '../components/icons/lucide'
import { receiptOf, type Verification } from '../lib/opsConsole'
import { BUTTON_SECONDARY, TEXT_M } from './style'

/**
 * The signed receipt of one decision as JSON (P4, read when the drawer opens), in
 * a drawer from the right. A decision without one (or while it is read) shows as
 * C6 served it instead: still the raw record, but unsigned, and labelled that way.
 */
export function RawDrawer({
  decision,
  verification,
  onClose,
}: {
  decision: Decision
  verification: Verification | null
  onClose: () => void
}) {
  const [fetched, setFetched] = useState<{ id: string; receipt: unknown } | null>(null)
  const receipt =
    receiptOf(decision) ?? (fetched?.id === decision.authorization_id ? fetched.receipt : null)

  useEffect(() => {
    if (!decision.receipt_id || receiptOf(decision) !== null) return
    let cancelled = false
    const id = decision.authorization_id
    getReceipt(id).then(
      (found) => {
        if (!cancelled && found) setFetched({ id, receipt: found })
      },
      () => {
        // Unread: the drawer keeps showing the decision, labelled unsigned.
      },
    )
    return () => {
      cancelled = true
    }
  }, [decision])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <button type="button" aria-label="Close" onClick={onClose} className="absolute inset-0 bg-scrim" />
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby="ops-raw-title"
        className={`${TEXT_M} relative flex h-full w-[min(640px,92vw)] flex-col bg-surface shadow-[-20px_0_40px_-20px_rgba(0,0,0,0.35)]`}
      >
        <div className="flex items-start gap-4 border-b border-hairline px-8 py-6">
          <div className="min-w-0 flex-1">
            <h2 id="ops-raw-title" className="font-semibold text-ink">
              {receipt !== null ? 'Raw receipt' : 'Raw decision · no signed receipt yet'}
            </h2>
            <p className="text-ink-muted tabular-nums">
              {decision.authorization_id}
              {receipt !== null && verification?.verified && (
                <span className="ml-3 inline-flex items-center gap-1 font-semibold text-approved">
                  <CheckIcon size={14} /> verified{verification.keyId ? ` · key ${verification.keyId}` : ''}
                </span>
              )}
              {receipt !== null && verification && !verification.verified && (
                <span className="ml-3 inline-flex items-center gap-1 font-semibold text-stopped">
                  <CrossIcon size={14} /> signature does not verify
                </span>
              )}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className={BUTTON_SECONDARY}
          >
            Close
          </button>
        </div>
        {/* JSON as text: shop names and item details inside it stay plain text. */}
        <pre className="scrollbar-none min-h-0 flex-1 overflow-y-auto bg-ground px-8 py-6 font-mono break-words whitespace-pre-wrap text-ink-soft">
          {JSON.stringify(receipt ?? decision, null, 2)}
        </pre>
      </aside>
    </div>
  )
}
