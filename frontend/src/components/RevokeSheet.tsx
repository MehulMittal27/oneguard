import { useState } from 'react'
import { BottomSheet } from './BottomSheet'

/** Confirmation for PolicyCard's Revoke action (C5) — no DESIGN.md mockup for this, see ROADMAP.md slice 5. */
export function RevokeSheet({
  cardId,
  onConfirm,
  onClose,
}: {
  cardId: string
  onConfirm: () => Promise<void>
  onClose: () => void
}) {
  const [revoking, setRevoking] = useState(false)
  const [error, setError] = useState(false)

  async function handleRevoke() {
    setRevoking(true)
    setError(false)
    try {
      await onConfirm()
    } catch {
      setError(true)
      setRevoking(false)
    }
  }

  return (
    <BottomSheet
      title="Revoke this policy?"
      onClose={onClose}
      footer={
        <div className="grid grid-cols-2 gap-3">
          <button
            type="button"
            onClick={onClose}
            disabled={revoking}
            className="h-11.5 rounded-button border-2 border-border-quiet text-[15px] font-semibold text-ink disabled:opacity-60"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleRevoke}
            disabled={revoking}
            className="h-11.5 rounded-button bg-destructive text-[15px] font-semibold text-on-ink disabled:opacity-60"
          >
            {revoking ? 'Revoking…' : 'Revoke'}
          </button>
        </div>
      }
    >
      <p className="text-[15px] text-ink-soft">
        Your agent won&apos;t be able to spend on card {cardId} anymore. To let it spend again,
        you&apos;ll need to write a new policy from scratch.
      </p>
      {error && (
        <p className="mt-3 text-[13px] text-destructive">
          Couldn&apos;t revoke the policy — nothing changed. Try again.
        </p>
      )}
    </BottomSheet>
  )
}
