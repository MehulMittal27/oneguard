import { useEffect, useRef, useState } from 'react'
import { approveDevice, getDevices, getPassport, passportQrUrl, removeDevice, thisDevice, verifyDocument } from '../api/passport'
import type { Device, Passport, VerifyResult } from '../api/types'
import { formatShortDate } from '../lib/datetime'
import { REVEAL_MS, prefersReducedMotion, takeReveal } from '../lib/passportReveal'
import { DeviceGateCancelled, useDevice } from '../state/DeviceContext'
import { CheckIcon, CrossIcon, DeviceIcon, ShieldIcon } from './icons/lucide'

type Load = 'loading' | 'error' | 'ready'

const STATUS: Record<Device['status'], { label: string; className: string }> = {
  enrolled: { label: 'Enrolled', className: 'bg-approved-tint text-approved' },
  pending: { label: 'Pending', className: 'bg-asked-tint text-asked' },
  removed: { label: 'Removed', className: 'bg-surface-sunken text-ink-muted' },
}

// How often the list re-reads while a device waits, so an approval made on
// another device shows up here without a reload.
const POLL_MS = 4000

/**
 * Card detail's Passport section (`../../docs/passport.md`,
 * `../../docs/api-contract.md` §6 item 22): the signed policy as a QR code to
 * the verify page, its version, the devices that control the card, and a
 * Verify that asks OneGuard to check the latest version's signature.
 */
export function PassportSection({ cardId, policyVersion }: { cardId: string; policyVersion?: number }) {
  const { withDevice, requestEnrol, version: deviceVersion } = useDevice()
  const [passport, setPassport] = useState<Passport | null>(null)
  const [devices, setDevices] = useState<Device[]>([])
  const [mine, setMine] = useState<Device | null>(null)
  const [load, setLoad] = useState<Load>('loading')
  const [attempt, setAttempt] = useState(0)
  const [busy, setBusy] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  // Kept with the version it checked: a new version is a new document, and an
  // earlier check says nothing about it.
  const [checked, setChecked] = useState<{ version: number; result: VerifyResult | 'checking' | 'error' } | null>(null)
  const [refresh, setRefresh] = useState(0)
  // The first-passport animation (lib/passportReveal.ts): on for REVEAL_MS once, then static.
  const [revealing, setRevealing] = useState(false)
  const revealTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => {
    if (revealTimer.current) clearTimeout(revealTimer.current)
  }, [])

  useEffect(() => {
    let cancelled = false
    async function read() {
      try {
        const [nextPassport, nextDevices] = await Promise.all([getPassport(cardId), getDevices(cardId)])
        const nextMine = await thisDevice(cardId, nextDevices)
        if (cancelled) return
        if (nextPassport && !prefersReducedMotion() && takeReveal(cardId, nextPassport.passport_id, 'card')) {
          setRevealing(true)
          revealTimer.current = setTimeout(() => setRevealing(false), REVEAL_MS)
        }
        setPassport(nextPassport)
        setDevices(nextDevices)
        setMine(nextMine)
        setLoad('ready')
      } catch {
        if (!cancelled) setLoad((current) => (current === 'ready' ? current : 'error'))
      }
    }
    void read()
    const interval = setInterval(read, POLL_MS)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [cardId, attempt, deviceVersion, policyVersion, refresh])

  async function change(device: Device, action: 'approve' | 'remove') {
    setBusy(device.device_id)
    setActionError(null)
    try {
      await withDevice(cardId, () => (action === 'approve' ? approveDevice : removeDevice)(cardId, device.device_id))
      setRefresh((n) => n + 1)
    } catch (error) {
      if (!(error instanceof DeviceGateCancelled)) {
        setActionError(error instanceof Error ? error.message : 'Something went wrong. Try again.')
      }
    } finally {
      setBusy(null)
    }
  }

  async function check() {
    if (!passport) return
    const { version } = passport
    setChecked({ version, result: 'checking' })
    try {
      const result = await verifyDocument({
        document: passport.document,
        signature: passport.signature,
        key_id: passport.key_id,
      })
      setChecked({ version, result })
    } catch {
      setChecked({ version, result: 'error' })
    }
  }

  const heading = <p className="mb-3 font-display text-[20px] font-bold text-ink">Passport</p>

  if (load === 'loading') {
    return (
      <div>
        {heading}
        <div className="h-44 animate-pulse rounded-card bg-surface-sunken" aria-busy="true" />
      </div>
    )
  }

  if (load === 'error') {
    return (
      <div>
        {heading}
        <div className="flex flex-col items-start gap-3 rounded-card border border-hairline bg-surface p-5">
          <p className="text-[15px] text-ink-soft">Couldn&apos;t load this card&apos;s passport.</p>
          <button
            type="button"
            onClick={() => {
              setLoad('loading')
              setAttempt((n) => n + 1)
            }}
            className="h-11.5 rounded-button border-2 border-ink px-5 text-[15px] font-semibold text-ink"
          >
            Try again
          </button>
        </div>
      </div>
    )
  }

  const document = passport?.document ?? {}
  const revoked = Boolean(document.revoked_at)
  const issuedAt = typeof document.issued_at === 'string' ? document.issued_at : null
  const verify = checked && checked.version === passport?.version ? checked.result : null
  const iControl = mine?.status === 'enrolled'
  const listed = devices.filter((d) => d.status !== 'removed' || d.device_id === mine?.device_id)
  const removedCount = devices.length - listed.length

  return (
    <div>
      {heading}
      <div className={`og-reveal rounded-card bg-surface p-5 ${revealing ? 'is-playing' : ''}`}>
        {passport ? (
          <div className="flex items-start gap-4">
            <div className="relative shrink-0">
              <img
                src={passportQrUrl(cardId, passport.version)}
                alt={`QR code to verify version ${passport.version} of this card's passport`}
                width={112}
                height={112}
                className="og-qr size-28 rounded-row border border-hairline bg-white p-1"
              />
              <span className="og-stamp" aria-hidden="true">
                Sealed
              </span>
            </div>
            <div className="min-w-0 flex-1">
              <p className="flex items-center gap-1.5 text-[15px] font-semibold text-ink">
                <span className="text-approved">
                  <ShieldIcon size={18} strokeWidth={2} />
                </span>
                Signed by OneGuard
              </p>
              <p className="mt-1 text-[13px] text-ink-muted">
                Version {passport.version}
                {issuedAt && ` · issued ${formatShortDate(issuedAt)}`}
              </p>
              <p className="mt-0.5 flex items-baseline text-[12px] text-ink-muted">
                <span className="shrink-0">Key&nbsp;</span>
                <span className="og-key font-mono text-ink">{passport.key_id}</span>
                <span className="og-caret text-ink" aria-hidden="true" />
              </p>
              {revoked && (
                <span className="mt-2 inline-flex rounded-pill bg-stopped-tint px-2.5 py-0.5 text-[11px] font-semibold text-stopped">
                  Revoked
                </span>
              )}
              <p className="mt-2 text-[12px] leading-[1.4] text-ink-muted">
                Scan to check it. Anyone can verify what your agent is allowed to do.
              </p>
            </div>
          </div>
        ) : (
          <p className="text-[15px] text-ink-muted">
            The passport is issued with this card&apos;s policy. It will show here in a moment.
          </p>
        )}

        {passport && (
          <div className="mt-4 flex flex-col gap-2 border-t border-hairline pt-4">
            <button
              type="button"
              onClick={check}
              disabled={verify === 'checking'}
              className="h-11.5 w-full rounded-button border-2 border-ink text-[15px] font-semibold text-ink disabled:opacity-60"
            >
              {verify === 'checking' ? 'Checking…' : 'Verify'}
            </button>
            {verify && verify !== 'checking' && (
              <p
                role="status"
                className={`flex items-start gap-1.5 text-[13px] ${
                  verify === 'error' ? 'text-destructive' : verify.valid ? 'text-approved' : 'text-stopped'
                }`}
              >
                <span className="mt-px shrink-0">
                  {verify !== 'error' && verify.valid ? <CheckIcon size={15} /> : <CrossIcon size={15} />}
                </span>
                <span className="min-w-0 break-words">
                  {verify === 'error'
                    ? "Couldn't reach OneGuard to check. Try again."
                    : verify.valid
                      ? `Valid · signed with key ${verify.key_id}`
                      : `Not valid · ${verify.reason}`}
                </span>
              </p>
            )}
          </div>
        )}

        <div className="mt-4 border-t border-hairline pt-4">
          <p className="text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
            Devices that control this card
          </p>
          {listed.length === 0 ? (
            <p className="mt-2 text-[13px] text-ink-muted">
              None yet. The first device to confirm or change this card&apos;s policy becomes its controller.
            </p>
          ) : (
            <ul className="mt-2 flex flex-col gap-2">
              {listed.map((device) => {
                const status = STATUS[device.status] ?? STATUS.removed
                const isMine = device.device_id === mine?.device_id
                const since =
                  device.status === 'enrolled' && device.enrolled_at
                    ? `Since ${formatShortDate(device.enrolled_at)}`
                    : device.status === 'pending'
                      ? 'Asked to join'
                      : device.removed_at
                        ? `Removed ${formatShortDate(device.removed_at)}`
                        : ''
                return (
                  <li key={device.device_id} className="rounded-row border border-hairline px-4 py-3">
                    <div className="flex items-center gap-3">
                      <span className="shrink-0 text-ink-muted">
                        <DeviceIcon size={20} />
                      </span>
                      <div className="min-w-0 flex-1">
                        {/* The customer's own name for the device — plain text. */}
                        <p className="truncate text-[15px] font-medium text-ink">{device.label}</p>
                        <p className="truncate text-[12px] text-ink-muted">
                          {isMine ? 'This device' : since}
                          {isMine && since ? ` · ${since}` : ''}
                        </p>
                      </div>
                      <span className={`shrink-0 rounded-pill px-2.5 py-1 text-[11px] font-semibold ${status.className}`}>
                        {status.label}
                      </span>
                    </div>
                    {iControl && !isMine && device.status !== 'removed' && (
                      <div className="mt-3 flex gap-2">
                        {device.status === 'pending' && (
                          <button
                            type="button"
                            onClick={() => change(device, 'approve')}
                            disabled={busy !== null}
                            className="h-10 flex-1 rounded-button bg-ink text-[14px] font-semibold text-on-ink disabled:opacity-60"
                          >
                            {busy === device.device_id ? 'Working…' : 'Approve'}
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={() => change(device, 'remove')}
                          disabled={busy !== null}
                          className="h-10 flex-1 rounded-button border-2 border-destructive-border text-[14px] font-semibold text-destructive disabled:opacity-60"
                        >
                          Remove
                        </button>
                      </div>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
          {removedCount > 0 && (
            <p className="mt-2 text-[12px] text-ink-muted">
              {removedCount === 1 ? '1 removed device' : `${removedCount} removed devices`} not shown.
            </p>
          )}
          {actionError && <p className="mt-2 text-[13px] text-destructive">{actionError}</p>}
          {!iControl && listed.some((d) => d.status === 'enrolled') && (
            <div className="mt-3 flex flex-col gap-2">
              <p className="text-[13px] text-ink-muted">
                {mine?.status === 'pending'
                  ? 'This device is waiting for approval from one that controls the card.'
                  : "This device can see the card but can't change its policy."}
              </p>
              {mine?.status !== 'pending' && (
                <button
                  type="button"
                  onClick={() => requestEnrol(cardId)}
                  className="h-11.5 w-full rounded-button border-2 border-border-quiet text-[15px] font-semibold text-ink"
                >
                  Add this device
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
