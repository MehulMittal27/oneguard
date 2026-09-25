import { useEffect, useRef, useState } from 'react'
import { getPassport, passportQrUrl } from '../../api/passport'
import type { Mandate, Passport } from '../../api/types'
import { CheckIcon, DeviceIcon } from '../../components/icons/lucide'
import { REVEAL_MS, prefersReducedMotion, takeReveal } from '../../lib/passportReveal'
import { NewPolicyShell } from './NewPolicyShell'

const LABEL = 'block font-mono text-[9px] font-semibold tracking-[0.12em] text-ink-muted uppercase'

/**
 * Step 3, shown only when C2 issued the card's very first passport
 * (`lib/passportReveal.ts`): the passport as a document, sealed once by the
 * first-passport animation (ported from the captain's design, ≤ 800 ms, then
 * static; none under prefers-reduced-motion). Every other confirm closes the
 * flow as before.
 */
export function NewPolicyConfirmed({
  mandate,
  holderName,
  onDone,
}: {
  mandate: Mandate
  holderName: string
  onDone: () => void
}) {
  const [passport, setPassport] = useState<Passport | null>(null)
  const [playing, setPlaying] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const summary = mandate.passport

  useEffect(() => {
    let cancelled = false
    getPassport(mandate.card_id)
      .then((found) => {
        if (cancelled || !found) return
        setPassport(found)
        if (!prefersReducedMotion() && takeReveal(mandate.card_id, found.passport_id, 'confirmation')) {
          setPlaying(true)
          timer.current = setTimeout(() => setPlaying(false), REVEAL_MS)
        }
      })
      .catch(() => {
        // The summary from C2 is enough to show; only the key and the QR wait for the read.
      })
    return () => {
      cancelled = true
      if (timer.current) clearTimeout(timer.current)
    }
  }, [mandate.card_id])

  const version = passport?.version ?? summary?.version ?? 1
  const devices = Array.isArray(passport?.document.devices) ? passport.document.devices.length : (summary?.devices_count ?? 0)

  return (
    <NewPolicyShell
      step={3}
      stepLabel="Confirmed"
      title="Your passport is issued"
      subtitle={`For card ${mandate.card_id} only`}
      onCancel={onDone}
      footer={
        <button
          type="button"
          onClick={onDone}
          className="h-14 rounded-row bg-ink text-[16px] font-semibold text-on-ink transition-opacity hover:opacity-90"
        >
          Done
        </button>
      }
    >
      <article
        aria-label="Passport"
        className={`og-reveal relative overflow-hidden rounded-card border border-border-quiet bg-surface shadow-[0_18px_52px_rgba(20,20,18,0.075)] ${
          playing ? 'is-playing' : ''
        }`}
      >
        <header className="flex items-center justify-between gap-4 border-b border-hairline px-5 py-4">
          <span className="font-mono text-[11px] font-semibold tracking-[0.11em] text-ink uppercase">
            OG / V{version}
          </span>
          <span className="flex items-center gap-1.5 text-[10px] font-bold tracking-[0.09em] text-approved uppercase">
            <span className="size-1.5 rounded-full bg-approved" aria-hidden="true" />
            Signed
          </span>
        </header>

        <div className="px-5 pt-6 pb-5">
          <p className="font-mono text-[10px] tracking-[0.13em] text-ink-muted uppercase">Spending passport</p>
          <p className="mt-1 font-display text-[30px] leading-tight font-bold text-ink">Passport</p>

          <div className="mt-6 grid grid-cols-[minmax(0,1fr)_88px] items-end gap-5">
            <div className="min-w-0">
              <span className={LABEL}>Holder</span>
              <span className="mt-1.5 block truncate text-[20px] font-semibold text-ink">{holderName}</span>
              <span className={`${LABEL} mt-4`}>Card</span>
              <span className="mt-1.5 block font-mono text-[12px] text-ink">{mandate.card_id}</span>
            </div>
            <div className="relative size-22 rounded-row border border-ink bg-white p-1.5">
              {passport && (
                <img
                  src={passportQrUrl(mandate.card_id, passport.version)}
                  alt={`QR code to verify version ${passport.version} of this passport`}
                  className="og-qr size-full"
                />
              )}
              <span className="og-stamp" aria-hidden="true">
                Sealed
              </span>
            </div>
          </div>

          <p className="mt-6 flex items-center gap-2.5 font-mono text-[9px] tracking-[0.12em] text-ink-muted uppercase">
            What your agent may do
            <span className="h-px flex-1 bg-hairline" aria-hidden="true" />
          </p>
          <ul className="mt-1">
            {mandate.checks.map((check) => (
              <li key={check.id} className="flex min-h-10 items-center gap-2.5 border-b border-hairline py-2 last:border-b-0">
                <span className="flex size-4 shrink-0 items-center justify-center rounded-full border border-border-quiet text-approved">
                  <CheckIcon size={10} strokeWidth={3} />
                </span>
                <span className="text-[13px] text-ink">{check.text}</span>
              </li>
            ))}
          </ul>

          <div className="mt-4 flex items-center justify-between gap-5 rounded-row border border-hairline bg-surface-sunken px-4 py-3.5">
            <div>
              <span className={LABEL}>Devices</span>
              <p className="mt-1 flex items-baseline gap-1.5">
                <strong className="text-[22px] font-bold text-ink">{devices}</strong>
                <span className="text-[11px] text-ink-muted">{devices === 1 ? 'controls it' : 'control it'}</span>
              </p>
            </div>
            <span className="text-ink-muted">
              <DeviceIcon size={22} />
            </span>
          </div>

          <p className="mt-4 flex items-baseline border-t border-hairline pt-3 font-mono text-[10px] text-ink-muted">
            <span className="shrink-0">Signed by OneGuard · key&nbsp;</span>
            <span className="og-key font-bold text-ink">{passport?.key_id ?? ''}</span>
            <span className="og-caret text-ink" aria-hidden="true" />
          </p>
        </div>
      </article>
    </NewPolicyShell>
  )
}
