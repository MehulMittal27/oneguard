import { useEffect, useState } from 'react'
import { verifyDocument } from '../../api/passport'
import type { VerifyResult, WouldApproveIf } from '../../api/types'
import { CheckIcon, CrossIcon, ShieldIcon, SpinnerIcon } from '../../components/icons/lucide'
import { DeviceFrame } from '../../components/DeviceFrame'
import { StatusBar } from '../../components/StatusBar'
import { formatShortDate, formatTime } from '../../lib/datetime'
import { verifyRequestFrom } from '../../lib/verifyLink'

type Doc = Record<string, unknown>

function text(value: unknown): string | null {
  return typeof value === 'string' && value ? value : null
}

function when(value: unknown): string | null {
  const iso = text(value)
  return iso ? `${formatShortDate(iso)} ${formatTime(iso)} UTC` : null
}

const OPERATOR: Record<string, string> = { '<=': '≤', '>=': '≥', '<': '<', '>': '>', '=': '=', '!=': '≠', in: 'in', not_in: 'not in' }

/** One `would_approve_if` bound as a line of text (a receipt is read by people, not only agents). */
function boundLine(bound: WouldApproveIf): string {
  if ('remove_items' in bound) return `without item ${bound.remove_items.join(', ')}`
  if ('requires' in bound) return bound.requires.replace(/_/g, ' ')
  const value = Array.isArray(bound.value) ? bound.value.join(', ') : String(bound.value)
  const scope = bound.scope === 'period' && bound.period_days ? ` (over ${bound.period_days} days)` : ''
  return `${bound.field} ${OPERATOR[bound.operator] ?? bound.operator} ${value}${scope}`
}

function Row({ label, value }: { label: string; value: string | null }) {
  if (!value) return null
  return (
    <div className="flex items-start justify-between gap-4 border-t border-hairline py-3 first:border-t-0">
      <dt className="shrink-0 text-[13px] text-ink-muted">{label}</dt>
      {/* Values come from the signed document — plain text only. */}
      <dd className="min-w-0 text-right text-[14px] font-medium break-words text-ink">{value}</dd>
    </div>
  )
}

function PassportDetails({ doc, current }: { doc: Doc; current: boolean | null | undefined }) {
  const holder = (doc.holder ?? {}) as Doc
  const checks = Array.isArray(doc.checks) ? (doc.checks as Doc[]) : []
  const devices = Array.isArray(doc.devices) ? doc.devices.length : 0
  const status = doc.revoked_at ? 'Revoked' : current === false ? 'Replaced by a newer version' : 'In force'
  return (
    <>
      <dl className="rounded-card bg-surface px-5 py-1">
        <Row label="Holder" value={text(holder.name)} />
        <Row label="Card" value={text(doc.card_id)} />
        <Row label="Version" value={doc.version != null ? String(doc.version) : null} />
        <Row label="Issued" value={when(doc.issued_at)} />
        <Row label="Status" value={status} />
        <Row label="Devices" value={devices === 1 ? '1 device controls it' : `${devices} devices control it`} />
      </dl>
      {checks.length > 0 && (
        <section className="flex flex-col gap-2">
          <p className="text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
            What the agent may do
          </p>
          {checks.map((check, index) => (
            <p key={index} className="rounded-row border border-hairline bg-surface px-4 py-3 text-[15px] text-ink-soft">
              {text(check.text) ?? text(check.id)}
            </p>
          ))}
        </section>
      )}
    </>
  )
}

const OUTCOME: Record<string, string> = { approve: 'Approved', decline: 'Declined', step_up: 'Asked the customer' }

function ReceiptDetails({ doc }: { doc: Doc }) {
  const auth = (doc.authorization ?? {}) as Doc
  const resolution = (doc.resolution ?? null) as Doc | null
  const bounds = Array.isArray(doc.would_approve_if) ? (doc.would_approve_if as WouldApproveIf[]) : []
  const amount = text(auth.billing_amount_chf)
  const answer = resolution
    ? `${resolution.outcome === 'approve' ? 'Approved' : 'Declined'} by ${resolution.resolved_by === 'timeout' ? 'nobody (time ran out)' : 'the customer'}`
    : null
  return (
    <>
      <dl className="rounded-card bg-surface px-5 py-1">
        <Row label="Decision" value={OUTCOME[String(doc.outcome)] ?? text(doc.outcome)} />
        <Row label="Answer" value={answer} />
        <Row label="Amount" value={amount ? `CHF ${amount}` : null} />
        <Row label="Shop" value={text(auth.merchant_id)} />
        <Row label="Purchase" value={text(auth.source_id) ?? text(auth.live_id)} />
        <Row label="Decided" value={when(doc.decided_at)} />
        <Row label="Passport" value={doc.passport_version != null ? `Version ${String(doc.passport_version)}` : null} />
      </dl>
      {bounds.length > 0 && (
        <section className="flex flex-col gap-2">
          <p className="text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
            Would have been approved with
          </p>
          {bounds.map((bound, index) => (
            <p key={index} className="rounded-row border border-hairline bg-surface px-4 py-3 text-[14px] text-ink-soft">
              {boundLine(bound)}
            </p>
          ))}
        </section>
      )}
    </>
  )
}

/**
 * `/verify` (`../../../docs/passport.md`): where the passport's QR code lands,
 * on any phone, with no sign-in. Asks OneGuard to check the stored document's
 * signature and shows who holds it and what it allows.
 */
export function Verify() {
  const [request] = useState(() => verifyRequestFrom(window.location.search))
  const [result, setResult] = useState<VerifyResult | 'error' | null>(null)

  useEffect(() => {
    if (!request) return
    let cancelled = false
    verifyDocument(request).then(
      (checked) => !cancelled && setResult(checked),
      () => !cancelled && setResult('error'),
    )
    return () => {
      cancelled = true
    }
  }, [request])

  const loaded = result && result !== 'error' ? result : null
  const kind = loaded?.document_type ?? (request && 'receipt_id' in request ? 'receipt' : 'passport')
  const doc = (loaded?.document ?? null) as Doc | null

  return (
    <DeviceFrame>
      <div className="flex min-h-0 flex-1 flex-col bg-ground">
        <StatusBar tone="ink" />
        <div className="scrollbar-none flex-1 overflow-y-auto">
          <div className="flex flex-col gap-6 px-8 pt-9 pb-9 sm:pt-5">
            <p className="flex items-center gap-2 font-display text-[17px] font-bold text-ink">
              <ShieldIcon size={22} strokeWidth={2} />
              OneGuard
            </p>
            <h1 className="font-display text-[30px] leading-tight font-bold text-ink">
              {kind === 'receipt' ? 'Receipt check' : 'Passport check'}
            </h1>

            {!request ? (
              <p className="text-[15px] text-ink-muted">
                This link doesn&apos;t name a passport or a receipt to check.
              </p>
            ) : result === null ? (
              <p className="flex items-center gap-2 text-[15px] text-ink-muted" aria-live="polite">
                <SpinnerIcon size={18} />
                Checking the signature…
              </p>
            ) : result === 'error' ? (
              <p className="rounded-card border border-hairline bg-surface p-5 text-[15px] text-ink-soft">
                Couldn&apos;t check it: OneGuard didn&apos;t answer, or doesn&apos;t know this document.
              </p>
            ) : (
              <>
                <div className={`rounded-hero p-6 ${result.valid ? 'bg-approved-tint' : 'bg-stopped-tint'}`}>
                  <p
                    className={`flex items-center gap-2 text-[20px] font-bold ${result.valid ? 'text-approved' : 'text-stopped'}`}
                  >
                    {result.valid ? <CheckIcon size={22} /> : <CrossIcon size={22} />}
                    {result.valid ? 'Valid' : 'Not valid'}
                  </p>
                  <p className="mt-2 text-[14px] leading-[1.45] text-ink-soft">{result.reason}</p>
                </div>
                {doc && (kind === 'receipt' ? <ReceiptDetails doc={doc} /> : <PassportDetails doc={doc} current={result.current} />)}
              </>
            )}

            <p className="text-[12px] leading-[1.45] text-ink-muted">
              OneGuard signs every passport and receipt with its own key. Anyone can check one against
              the public key at <span className="font-mono">/api/passport/keys</span>.
            </p>
          </div>
        </div>
      </div>
    </DeviceFrame>
  )
}
