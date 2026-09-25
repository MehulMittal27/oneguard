import { useEffect, useState } from 'react'
import { enrolThisDevice, getDevices, thisDevice } from '../api/passport'
import { controllerOf } from '../lib/passportDevices'
import { getOrCreateDeviceKey } from '../lib/deviceKey'
import { BottomSheet } from './BottomSheet'
import { SpinnerIcon } from './icons/lucide'

export type EnrolMode = 'offer' | 'pending'

const POLL_MS = 3000

/**
 * Adding this browser to a card another device already controls
 * (`../../docs/passport.md` §4): name it once (`offer`), then wait while the
 * card's controller approves it (`pending`). Polls the card's devices and
 * finishes by itself the moment the approval lands.
 */
export function DeviceEnrolSheet({
  cardId,
  initialMode,
  onChanged,
  onEnrolled,
  onClose,
}: {
  cardId: string
  initialMode: EnrolMode
  onChanged: () => void
  onEnrolled: () => void
  onClose: () => void
}) {
  const [mode, setMode] = useState<EnrolMode>(initialMode)
  const [label, setLabel] = useState('')
  const [pendingLabel, setPendingLabel] = useState<string | null>(null)
  // The controller's name (the customer's own text), once the card's devices are read.
  const [controllerLabel, setControllerLabel] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState(false)

  useEffect(() => {
    let cancelled = false
    void getOrCreateDeviceKey().then((key) => {
      if (!cancelled) setLabel((current) => current || key.label)
    })
    return () => {
      cancelled = true
    }
  }, [])

  // While waiting: read the card's devices until this one is enrolled.
  useEffect(() => {
    if (mode !== 'pending') return
    let cancelled = false
    async function check() {
      try {
        const devices = await getDevices(cardId)
        const mine = await thisDevice(cardId, devices)
        if (cancelled) return
        if (mine) setPendingLabel(mine.label)
        setControllerLabel(controllerOf(devices)?.label ?? null)
        if (mine?.status === 'enrolled') {
          onChanged()
          onEnrolled()
        }
      } catch {
        // A missed poll changes nothing; the next one tries again.
      }
    }
    void check()
    const interval = setInterval(check, POLL_MS)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [mode, cardId, onChanged, onEnrolled])

  async function ask() {
    setSending(true)
    setError(false)
    try {
      const status = await enrolThisDevice(cardId, label)
      onChanged()
      if (status === 'enrolled') onEnrolled()
      else setMode('pending')
    } catch {
      setError(true)
    } finally {
      setSending(false)
    }
  }

  if (mode === 'offer') {
    return (
      <BottomSheet
        title="Add this device"
        onClose={onClose}
        footer={
          <div className="grid grid-cols-2 gap-3">
            <button
              type="button"
              onClick={onClose}
              disabled={sending}
              className="h-11.5 rounded-button border-2 border-border-quiet text-[15px] font-semibold text-ink disabled:opacity-60"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={ask}
              disabled={sending || !label.trim()}
              className="h-11.5 rounded-button bg-ink text-[15px] font-semibold text-on-ink disabled:opacity-60"
            >
              {sending ? 'Asking…' : 'Ask to add'}
            </button>
          </div>
        }
      >
        <p className="text-[15px] text-ink-soft">
          Card {cardId} is controlled from another device. Name this one — you can&apos;t change the
          name later — then approve it from the card&apos;s controller.
        </p>
        <label className="mt-4 block">
          <span className="text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
            Device name
          </span>
          <input
            type="text"
            value={label}
            maxLength={60}
            onChange={(event) => setLabel(event.target.value)}
            className="mt-2 h-11.5 w-full rounded-row border-2 border-border-quiet bg-surface px-4 text-[15px] text-ink focus:border-ink focus:outline-none"
          />
        </label>
        {error && (
          <p className="mt-3 text-[13px] text-destructive">
            Couldn&apos;t reach OneGuard — nothing was added. Try again.
          </p>
        )}
      </BottomSheet>
    )
  }

  return (
    <BottomSheet
      title="Waiting for approval"
      onClose={onClose}
      footer={
        <button
          type="button"
          onClick={onClose}
          className="h-11.5 w-full rounded-button border-2 border-border-quiet text-[15px] font-semibold text-ink"
        >
          Not now
        </button>
      }
    >
      <p className="text-[15px] text-ink-soft">
        {controllerLabel ? `Waiting for approval from ${controllerLabel}.` : `This device isn't approved for card ${cardId} yet.`}
      </p>
      <div className="mt-4 flex items-center justify-between gap-3 rounded-row border border-asked-border bg-asked-tint px-4 py-3">
        {/* The customer's own name for the device — plain text. */}
        <span className="min-w-0 truncate text-[15px] font-semibold text-ink">{pendingLabel ?? label}</span>
        <span className="shrink-0 rounded-pill bg-surface px-2.5 py-1 text-[11px] font-semibold text-asked">
          Pending
        </span>
      </div>
      <p className="mt-4 text-[13px] leading-[1.45] text-ink-muted">
        On {controllerLabel ?? 'the device that controls this card'}, open Card {cardId}, then Passport, and tap
        Approve. This screen carries on by itself once it&apos;s approved.
      </p>
      <p className="mt-3 flex items-center gap-2 text-[13px] text-ink-muted" aria-live="polite">
        <SpinnerIcon size={14} strokeWidth={2.5} />
        Checking for approval…
      </p>
    </BottomSheet>
  )
}
