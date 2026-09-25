import { useEffect, useRef, useState, type ComponentType } from 'react'
import { getReceipt, verifyDocument } from '../api/passport'
import type { Decision, EvidenceItem } from '../api/types'
import {
  CheckIcon,
  ChevronDownIcon,
  ClockIcon,
  CrossIcon,
  HelpCircleIcon,
  InfoIcon,
  type IconProps,
} from '../components/icons/lucide'
import { formatTime } from '../lib/datetime'
import { formatChf } from '../lib/money'
import {
  formatLatency,
  hasReceipt,
  isWaiting,
  outcomeBadge,
  readVerification,
  wouldApproveIf,
  type OutcomeBadge,
  type Verification,
} from '../lib/opsConsole'
import { reasonLabel } from '../lib/reasonCodes'
import { BUTTON_SECONDARY, TEXT_L, TEXT_M, TEXT_S, TONE_CLASS } from './style'

// One grid for the header and every row. Below 1000px of stream width the
// message drops to a second line under the shop, so it is never cut to a stub.
const GRID =
  'grid grid-cols-[64px_minmax(0,1fr)_auto_auto] gap-x-5 @min-[1000px]:grid-cols-[64px_minmax(0,190px)_136px_132px_minmax(0,1fr)_96px]'
const MESSAGE_CELL = 'col-span-2 col-start-2 @min-[1000px]:col-span-1 @min-[1000px]:col-start-auto'
const META_CELL = 'justify-end'

const BADGE_ICON: Record<OutcomeBadge['label'], ComponentType<IconProps>> = {
  Approved: CheckIcon,
  Stopped: CrossIcon,
  Waiting: ClockIcon,
  Expired: ClockIcon,
  Answered: CheckIcon,
}

const EVIDENCE_STYLE: Record<EvidenceItem['outcome'], { Icon: ComponentType<IconProps>; fg: string }> = {
  pass: { Icon: CheckIcon, fg: 'text-approved' },
  fail: { Icon: CrossIcon, fg: 'text-stopped' },
  uncertain: { Icon: HelpCircleIcon, fg: 'text-asked' },
  info: { Icon: InfoIcon, fg: 'text-ink-muted' },
}

// A receipt's verdict: undefined while /api/verify is asked, null when no verifier answered.
type ReceiptCheck = Verification | null | undefined

/**
 * The current run's decisions, newest delivered first (`arrivalOrder`), one
 * line each. `decisions` is null before the first read. Rows are keyed by
 * authorization id, so a step-up the customer answers flips in place rather
 * than appearing twice.
 */
export function DecisionStream({
  decisions,
  expanded,
  onToggle,
  onOpenRaw,
  emptyText = 'No decision in this run yet.',
}: {
  decisions: Decision[] | null
  // Which rows are open (kept by the console, so Overview can open one here).
  expanded: ReadonlySet<string>
  onToggle: (authorizationId: string) => void
  onOpenRaw: (decision: Decision, receiptCheck: Verification | null) => void
  emptyText?: string
}) {
  const checks = useReceiptChecks(decisions)

  return (
    <section aria-label="Decisions" className="@container flex min-h-0 flex-1 flex-col overflow-hidden rounded-card border border-hairline bg-surface">
      <div className={`${GRID} ${TEXT_S} hidden border-b border-hairline px-8 py-3 font-semibold tracking-[0.06em] text-ink-muted uppercase @min-[1000px]:grid`}>
        <span>Time</span>
        <span>Shop</span>
        <span className="text-right">Amount</span>
        <span>Outcome</span>
        <span>Message</span>
        <span className="text-right">Engine</span>
      </div>

      {/* relative: the badges' screen-reader text is absolutely placed, and must
          stay inside this scroll box rather than lengthen the page. */}
      <div className="scrollbar-none relative min-h-0 flex-1 overflow-y-auto">
        {decisions === null ? (
          <div className="flex flex-col gap-3 p-8" aria-busy="true">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-10 animate-pulse rounded-row bg-surface-sunken" />
            ))}
          </div>
        ) : decisions.length === 0 ? (
          <p className={`${TEXT_M} px-8 py-8 text-ink-muted`}>{emptyText}</p>
        ) : (
          <ol className="flex flex-col">
            {decisions.map((d) => (
              <DecisionRow
                key={d.authorization_id}
                decision={d}
                open={expanded.has(d.authorization_id)}
                onToggle={() => onToggle(d.authorization_id)}
                receipt={checks.get(d.authorization_id)}
                onOpenRaw={onOpenRaw}
              />
            ))}
          </ol>
        )}
      </div>
    </section>
  )
}

function DecisionRow({
  decision: d,
  open,
  onToggle,
  receipt,
  onOpenRaw,
}: {
  decision: Decision
  open: boolean
  onToggle: () => void
  receipt: ReceiptCheck
  onOpenRaw: (decision: Decision, receiptCheck: Verification | null) => void
}) {
  const badge = outcomeBadge(d)
  const waiting = isWaiting(d)
  const BadgeIcon = badge.answer === 'declined' ? CrossIcon : BADGE_ICON[badge.label]
  const verified = receipt?.verified === true
  const panelId = `ops-row-${d.authorization_id}`
  // An opened row brings its detail into view, however far down the stream it is.
  const rowRef = useRef<HTMLLIElement>(null)
  useEffect(() => {
    if (open) rowRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [open])

  return (
    <li
      ref={rowRef}
      className={`border-b border-hairline transition-colors duration-500 last:border-b-0 ${
        waiting ? 'bg-asked-tint/45' : open ? 'bg-ground' : 'bg-surface'
      }`}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-controls={panelId}
        className={`${GRID} ${TEXT_L} w-full items-center gap-y-1 px-8 py-4 text-left hover:bg-surface-sunken/60 ${
          waiting ? 'shadow-[inset_4px_0_0_var(--asked-dot)]' : ''
        }`}
      >
        <span className="text-ink-muted tabular-nums" title={`${d.occurred_at} (simulated)`}>
          {formatTime(d.occurred_at)}
        </span>
        {/* Shop name: untrusted merchant text, a plain text node. */}
        <span className="truncate font-semibold text-ink" title={d.merchant.name}>
          {d.merchant.name}
        </span>
        <span className="text-right font-semibold whitespace-nowrap text-ink tabular-nums">
          {formatChf(d.billing_amount_chf)}
        </span>
        <span
          className={`${TEXT_M} inline-flex items-center gap-1.5 justify-self-start rounded-pill px-3 py-1 font-semibold ${TONE_CLASS[badge.tone]}`}
          title={badge.description}
        >
          <BadgeIcon size={14} strokeWidth={2.6} />
          {badge.label}
          <span className="sr-only">: {badge.description}</span>
        </span>
        <span className={`${MESSAGE_CELL} truncate text-ink-soft`} title={d.message}>
          {d.message}
        </span>
        <span className={`${META_CELL} flex items-center gap-2`}>
          {d.latency_ms !== undefined && (
            <span className={`${TEXT_S} rounded-pill bg-surface-sunken px-2 py-0.5 font-semibold whitespace-nowrap text-ink-muted tabular-nums`}>
              {formatLatency(d.latency_ms)}
            </span>
          )}
          {verified && (
            <span className="text-approved" title="Signed receipt verified">
              <CheckIcon size={14} strokeWidth={2.6} />
              <span className="sr-only">receipt verified</span>
            </span>
          )}
          <span className={`text-ink-muted transition-transform ${open ? 'rotate-180' : ''}`}>
            <ChevronDownIcon size={16} />
          </span>
        </span>
      </button>

      {open && <RowDetail id={panelId} decision={d} receipt={receipt} onOpenRaw={onOpenRaw} />}
    </li>
  )
}

function RowDetail({
  id,
  decision: d,
  receipt,
  onOpenRaw,
}: {
  id: string
  decision: Decision
  receipt: ReceiptCheck
  onOpenRaw: (decision: Decision, receiptCheck: Verification | null) => void
}) {
  const told = wouldApproveIf(d)
  const signed = hasReceipt(d)
  return (
    <div id={id} className={`${TEXT_M} grid gap-x-10 gap-y-5 px-8 pt-1 pb-6 @min-[1000px]:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]`}>
      <div className="flex min-w-0 flex-col gap-3">
        <p className="text-ink">{d.message}</p>
        <Label>Evidence</Label>
        <ul className="flex flex-col gap-2">
          {d.evidence.map((item, index) => {
            const style = EVIDENCE_STYLE[item.outcome] ?? EVIDENCE_STYLE.info
            return (
              <li key={index} className="flex items-start gap-3">
                <span className={`mt-0.5 shrink-0 ${style.fg}`}>
                  <style.Icon size={15} strokeWidth={2.6} />
                </span>
                <span className="min-w-0">
                  <span className="font-semibold text-ink">{item.rule}</span>
                  <span className="text-ink-muted"> · {item.detail}</span>
                  {item.source && <span className="text-ink-muted"> ({item.source.replace('_', ' ')})</span>}
                </span>
              </li>
            )
          })}
        </ul>
      </div>

      <div className="flex min-w-0 flex-col gap-3">
        <Label>Counterfactual</Label>
        <p className={d.counterfactual ? 'text-ink' : 'text-ink-muted'}>{d.counterfactual ?? 'None given for this decision'}</p>

        {told.length > 0 && (
          <>
            <Label>What your agent was told</Label>
            <ul className="flex list-disc flex-col gap-1 pl-5 text-ink">
              {told.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </>
        )}

        {d.reason_codes.length > 0 && (
          <>
            <Label>Why</Label>
            <p className="text-ink">{d.reason_codes.map(reasonLabel).join(' · ')}</p>
          </>
        )}

        <Label>Receipt</Label>
        <div className="flex flex-wrap items-center gap-3">
          <ReceiptStatus hasReceipt={signed} receipt={receipt} />
          <button
            type="button"
            onClick={() => onOpenRaw(d, receipt ?? null)}
            className={BUTTON_SECONDARY}
          >
            {signed ? 'Raw receipt' : 'Raw decision'}
          </button>
        </div>
      </div>
    </div>
  )
}

function ReceiptStatus({ hasReceipt, receipt }: { hasReceipt: boolean; receipt: ReceiptCheck }) {
  if (!hasReceipt) return <span className="text-ink-muted">No signed receipt on this decision</span>
  if (receipt === undefined) return <span className="text-ink-muted">Verifying…</span>
  if (receipt === null) return <span className="text-ink-muted">Not checked: no verifier answered</span>
  return receipt.verified ? (
    <span className="flex items-center gap-1 font-semibold text-approved">
      <CheckIcon size={14} /> verified{receipt.keyId ? ` · key ${receipt.keyId}` : ''}
    </span>
  ) : (
    <span className="flex items-center gap-1 font-semibold text-stopped">
      <CrossIcon size={14} /> signature does not verify
    </span>
  )
}

function Label({ children }: { children: string }) {
  return <p className="font-semibold text-ink-muted">{children}</p>
}

/** One decision's receipt (P4) through `/api/verify` (P5); null when no verifier answered. */
async function checkReceipt(d: Decision): Promise<Verification | null> {
  try {
    const receipt = await getReceipt(d.authorization_id)
    if (!receipt) return null
    return readVerification(
      await verifyDocument({ document: receipt.document, signature: receipt.signature, key_id: receipt.key_id }),
    )
  } catch {
    return null
  }
}

/**
 * Each receipt on screen is sent to `/api/verify` once. Decisions without a
 * receipt are never sent; nothing here changes a decision. Absent from the map:
 * not answered yet.
 */
function useReceiptChecks(decisions: Decision[] | null): ReadonlyMap<string, Verification | null> {
  const [checks, setChecks] = useState<ReadonlyMap<string, Verification | null>>(new Map())
  const asked = useRef(new Set<string>())
  useEffect(() => {
    for (const d of decisions ?? []) {
      const id = d.authorization_id
      if (!hasReceipt(d) || asked.current.has(id)) continue
      asked.current.add(id)
      void checkReceipt(d).then((verification) => {
        setChecks((prev) => new Map(prev).set(id, verification))
      })
    }
  }, [decisions])
  return checks
}
