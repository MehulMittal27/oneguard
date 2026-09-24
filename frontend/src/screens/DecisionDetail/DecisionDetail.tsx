import type { ComponentType } from 'react'
import type { Decision, DecisionRelation, EvidenceItem, UncertainOutcome } from '../../api/types'
import { DecisionMark } from '../../components/DecisionMark'
import { NetworkState } from '../../components/NetworkState'
import type { IconProps } from '../../components/icons/IconProps'
import {
  BackChevronIcon,
  CheckIcon,
  CrossIcon,
  HelpCircleIcon,
  InfoIcon,
} from '../../components/icons/lucide'
import { SessionBanner } from '../../components/SessionBanner'
import { formatShortDate, formatTime } from '../../lib/datetime'
import { messageWithoutCounterfactual } from '../../lib/decisionMessage'
import { getInitials } from '../../lib/initials'
import { formatChf } from '../../lib/money'
import { reasonLabel } from '../../lib/reasonCodes'
import { useCustomer } from '../../state/CustomerContext'
import { useDecisions } from '../../state/DecisionsContext'
import { usePolicy } from '../../state/PolicyContext'

const BANNER_STYLE: Record<
  'approved' | 'stopped',
  { headline: string; Icon: ComponentType<IconProps>; bg: string; fg: string }
> = {
  approved: { headline: 'Approved by your rules', Icon: CheckIcon, bg: 'bg-approved-tint', fg: 'text-approved' },
  stopped: { headline: 'Stopped by your rules', Icon: CrossIcon, bg: 'bg-stopped-tint', fg: 'text-stopped' },
}

// Only 3 real categories (D-041) — the icon stays help-circle for every
// uncertain sub-status (never a new category); only the background/text
// color and the headline change with it.
const UNCERTAIN_BANNER: Record<UncertainOutcome, { headline: string; bg: string; fg: string }> = {
  pending: { headline: 'Waiting for you', bg: 'bg-asked-tint', fg: 'text-asked' },
  expired: { headline: "You didn't answer in time", bg: 'bg-surface-expired', fg: 'text-ink-muted' },
  approved: { headline: 'Approved by you', bg: 'bg-approved-tint', fg: 'text-approved' },
  declined: { headline: 'Blocked by you', bg: 'bg-stopped-tint', fg: 'text-stopped' },
}

/**
 * Contract §6 items 9-10's provenance tag, on its own small line under the
 * explanation. It no longer sits beside the message, so the model label is the
 * contract's full wording, which says outright that only the wording changed:
 * no model sits in the decision path (`../../../CLAUDE.md` non-negotiable 1).
 * Unknown values fall back to the label that is true either way.
 */
const EXPLANATION_SOURCE: Record<string, string> = {
  template: 'Explained by OneGuard',
  model: 'Wording refined by AI · decision made by your rules',
}

const EXPLANATION_SOURCE_FALLBACK = 'Explained by OneGuard'

const EVIDENCE_STYLE: Record<
  EvidenceItem['outcome'],
  { Icon: ComponentType<IconProps>; iconFg: string; border: string }
> = {
  pass: { Icon: CheckIcon, iconFg: 'text-approved', border: 'border-hairline' },
  fail: { Icon: CrossIcon, iconFg: 'text-stopped', border: 'border-stopped-border' },
  uncertain: { Icon: HelpCircleIcon, iconFg: 'text-asked', border: 'border-asked-border' },
  // Context, not a verdict: never pass/fail colours. Also the fallback for
  // any outcome value this client doesn't know yet.
  info: { Icon: InfoIcon, iconFg: 'text-ink-muted', border: 'border-hairline' },
}

const RELATION_LABEL: Record<DecisionRelation, string> = {
  requote_of: 'Re-quote of',
  duplicate_of: 'Duplicate of',
  retry_of: 'Retry of',
  split_of: 'Split of',
}

// 'unknown' and 'not_applicable' are both real answers, never blank or "no"
// (data_dictionary.md) — each gets its own honest label.
const RETURNABLE_LABEL: Record<Decision['order_returnable'], string> = {
  true: 'Yes',
  false: 'No',
  unknown: 'Not stated by the shop',
  not_applicable: 'Not applicable',
}

/**
 * DESIGN.md #8/#9, generalized to all 3 decision categories (the mockups
 * only illustrate the stopped case) — reusable from Activity, Approvals'
 * Expired section, and Card detail's activity list.
 */
export function DecisionDetail({
  authorizationId,
  backLabel,
  onBack,
  onSelectRelated,
  onViewPolicy,
  onGoToApprovals,
  onGoHome,
}: {
  authorizationId: string
  backLabel: string
  onBack: () => void
  onSelectRelated: (authorizationId: string) => void
  onViewPolicy: (cardId: string) => void
  onGoToApprovals: () => void
  // Only used by the manipulated-purchase persona chrome below (D-058) —
  // every other decision reaches this screen via onBack/backLabel as usual.
  onGoHome: () => void
}) {
  const { decisions, status: decisionsStatus, retry: retryDecisions } = useDecisions()
  const { policiesByCard } = usePolicy()
  const { signedInAs, logout } = useCustomer()
  const decision = decisions.find((d) => d.authorization_id === authorizationId)
  // Normally unreachable — every caller passes an id from its own already
  // -loaded list — but a blank screen is still a bug if it ever happens
  // (ROADMAP.md slice 9: no screen fails silently).
  if (!decision) {
    return (
      <div className="flex flex-col gap-7 px-8 pt-4 pb-9 sm:pt-5">
        <button
          type="button"
          onClick={onBack}
          className="flex min-h-11 items-center gap-1 text-[15px] font-semibold text-ink-muted"
        >
          <BackChevronIcon size={20} strokeWidth={2} />
          {backLabel}
        </button>
        {decisionsStatus === 'loading' ? (
          <NetworkState kind="loading" label="purchase details" />
        ) : decisionsStatus === 'error' ? (
          <NetworkState kind="error" label="purchase details" onRetry={retryDecisions} />
        ) : (
          <NetworkState kind="empty" label="purchase details">
            This purchase couldn&apos;t be found.
          </NetworkState>
        )}
      </div>
    )
  }

  const isUncertain = decision.decision === 'uncertain'
  const banner =
    decision.decision === 'uncertain'
      ? UNCERTAIN_BANNER[decision.uncertain_outcome ?? 'pending']
      : BANNER_STYLE[decision.decision]
  const mandate = policiesByCard[decision.card_id]
  // DESIGN.md #9's persona chrome (D-058) — extra framing only for the one
  // scenario it was drawn for (a real injection attempt), not a guess at
  // every decision's presentation.
  const isManipulated = Boolean(decision.injection_flag)

  // The backend's own link, when it sends one (docs/api-contract.md §2).
  const link = decision.related ?? null
  const linked = link ? decisions.find((d) => d.authorization_id === link.authorization_id) : undefined

  // Heuristic neighbours on the same card: the fallback when there is no
  // backend link, and never repeating the linked decision when there is.
  const related = decisions
    .filter(
      (d) =>
        d.card_id === decision.card_id &&
        // Another run on this card is a replay of the same purchases, not a
        // neighbour of this one.
        (d.run_id ?? null) === (decision.run_id ?? null) &&
        d.authorization_id !== decision.authorization_id &&
        d.authorization_id !== link?.authorization_id &&
        (d.decision === 'stopped' || d.decision === 'uncertain'),
    )
    .sort((a, b) => b.occurred_at.localeCompare(a.occurred_at))
    .slice(0, 3)

  // A still-pending uncertain purchase is actionable, not just viewable —
  // route straight to where it can actually be answered.
  function selectRelated(related: Decision) {
    if (related.status === 'pending_human') {
      onGoToApprovals()
    } else {
      onSelectRelated(related.authorization_id)
    }
  }

  return (
    <div className="flex flex-col gap-7 px-8 pt-4 pb-9 sm:pt-5">
      <button
        type="button"
        onClick={isManipulated ? logout : onBack}
        className="flex min-h-11 items-center gap-1 text-[15px] font-semibold text-ink-muted"
      >
        <BackChevronIcon size={20} strokeWidth={2} />
        {isManipulated ? 'Switch customer' : backLabel}
      </button>

      {/* DESIGN.md #9 calls this "His words" — reworded to the customer's
          own name rather than a pronoun, since this screen isn't written
          for one specific persona and shouldn't assume one. */}
      {isManipulated && signedInAs && (
        <div className="flex items-center gap-3">
          <span className="flex size-11 shrink-0 items-center justify-center rounded-full bg-ink font-sans text-[15px] font-semibold text-on-ink">
            {getInitials(signedInAs.name)}
          </span>
          <div className="min-w-0">
            <p className="truncate text-[15px] font-semibold text-ink">{signedInAs.name}</p>
            <p className="truncate text-[13px] text-ink-muted">Card {decision.card_id}</p>
          </div>
        </div>
      )}

      {isManipulated && signedInAs && mandate?.instruction && (
        <div className="rounded-row border border-hairline bg-surface-sunken p-4">
          <p className="text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
            {signedInAs.name}&apos;s words
          </p>
          <p className="mt-2 text-[15px] text-ink-soft">{mandate.instruction}</p>
        </div>
      )}

      <SessionBanner session={decision.session} />

      <div className={`rounded-hero p-6 ${banner.bg}`}>
        <p className={`flex items-center gap-2 text-[15px] font-semibold ${banner.fg}`}>
          {isUncertain ? (
            <HelpCircleIcon size={18} strokeWidth={2.4} />
          ) : decision.decision === 'approved' ? (
            <CheckIcon size={18} strokeWidth={2.4} />
          ) : (
            <CrossIcon size={18} strokeWidth={2.4} />
          )}
          {banner.headline}
        </p>
        {/* Merchant name is untrusted merchant text — plain text node only. */}
        <p className="mt-3 text-[17px] font-bold text-ink">{decision.merchant.name}</p>
        <p className="font-display text-[34px] leading-tight font-bold text-ink tabular-nums">
          {formatChf(decision.billing_amount_chf)}
        </p>
        <p className="mt-1 text-[13px] text-ink-muted">
          {formatShortDate(decision.occurred_at)} · {formatTime(decision.occurred_at)}
        </p>
        {/*
          The reason, first thing under the banner: CLAUDE.md rule 10 — a
          decision with no visible reason is a bug. D-040 had removed the
          restated reason text, which left the message unrendered on the one
          screen whose whole job is explaining. The message takes the full
          width, the counterfactual is one line under it, and the provenance tag
          is a small line of its own below both.
        */}
        <div className="mt-4 border-t border-hairline pt-4">
          {/* The counterfactual line below says the suggestion; the message's
              trailing copy of it, which older messages still carry, is dropped so
              it is said once. */}
          <p className="text-[15px] leading-[1.45] font-medium text-ink">
            {messageWithoutCounterfactual(decision.message, decision.counterfactual)}
          </p>
          {decision.counterfactual && (
            <p className="mt-1 text-[14px] leading-[1.45] text-ink-soft">{decision.counterfactual}</p>
          )}
          {decision.explanation_source && (
            <p className="mt-2 text-[11px] text-ink-muted">
              {EXPLANATION_SOURCE[decision.explanation_source] ?? EXPLANATION_SOURCE_FALLBACK}
            </p>
          )}
          {/* The codes the engine actually emitted, in the customer's words. One
              label map for the whole app (hard rule 9); an unknown code gets the
              neutral line rather than its own id. */}
          {decision.reason_codes.length > 0 && (
            <ul className="mt-3 flex flex-wrap gap-1.5">
              {decision.reason_codes.map((code) => (
                <li
                  key={code}
                  className="rounded-pill bg-surface-sunken px-2.5 py-1 text-[12px] text-ink-soft"
                >
                  {reasonLabel(code)}
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="mt-4 flex gap-6 border-t border-hairline pt-4 text-[13px]">
          <div>
            <p className="text-ink-muted">Returnable</p>
            <p className="mt-0.5 font-medium text-ink">
              {RETURNABLE_LABEL[decision.order_returnable]}
            </p>
          </div>
          {decision.delivery_by && (
            <div>
              <p className="text-ink-muted">Delivery by</p>
              <p className="mt-0.5 font-medium text-ink">
                {formatShortDate(decision.delivery_by)}
              </p>
            </div>
          )}
        </div>
      </div>

      {decision.injection_flag && (
        <div className="rounded-card border-2 border-dashed border-stopped-border bg-surface p-5">
          <span className="inline-flex items-center rounded-pill bg-stopped-tint px-3 py-1 text-[13px] font-medium text-stopped">
            Untrusted shop text
          </span>
          <div className="mt-3 flex flex-col gap-3">
            {decision.items.map((item, index) => (
              <p key={index} className="text-[14px] text-ink-soft">
                {/* Verbatim merchant text — plain text node only, never rendered as HTML/markdown. */}
                {item.item_details}
              </p>
            ))}
          </div>
          <p className="mt-3 text-[13px] text-ink-muted">
            We read this text for facts only — any instructions inside it are ignored.{' '}
            {decision.injection_flag.reason}
          </p>
        </div>
      )}

      <section className="flex flex-col gap-3">
        <p className="text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
          What decided it
        </p>
        {decision.evidence.map((item, index) => {
          const style = EVIDENCE_STYLE[item.outcome] ?? EVIDENCE_STYLE.info
          return (
            <div
              key={index}
              className={`flex items-start gap-3 rounded-row border px-4 py-3 ${style.border}`}
            >
              <span className={`shrink-0 ${style.iconFg}`}>
                <style.Icon size={16} strokeWidth={2.6} />
              </span>
              <div>
                <p className="text-[15px] font-medium text-ink">{item.rule}</p>
                <p className="mt-0.5 text-[13px] text-ink-muted">{item.detail}</p>
              </div>
            </div>
          )
        })}
        <p className="text-[12px] text-ink-muted">
          Decided by your own rules — not by us, and not by the shop.
        </p>
      </section>

      <section>
        <p className="mb-3 text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
          Policy applied
        </p>
        {mandate ? (
          <button
            type="button"
            onClick={() => onViewPolicy(decision.card_id)}
            className={`flex w-full flex-col items-start gap-2 rounded-card border p-4 text-left ${
              mandate.status === 'revoked' ? 'border-stopped-border bg-surface' : 'border-hairline bg-surface'
            }`}
          >
            <span className="flex items-center gap-2">
              {mandate.status === 'revoked' && (
                <span className="rounded-pill bg-stopped-tint px-2.5 py-0.5 text-[11px] font-semibold text-stopped">
                  Revoked
                </span>
              )}
              <span className="text-[13px] text-ink-muted">
                {mandate.status === 'revoked'
                  ? `Card ${decision.card_id} · tap to view or add a new policy`
                  : `Card ${decision.card_id} · tap to manage or revoke`}
              </span>
            </span>
            {mandate.status === 'revoked' && (
              <span className="text-[12px] text-ink-muted">This policy was later revoked.</span>
            )}
            <span className="flex flex-wrap gap-2">
              {mandate.checks.map((check) => (
                <span
                  key={check.id}
                  className="rounded-pill bg-surface-sunken px-3 py-1.5 text-[13px] font-medium text-ink-soft"
                >
                  {check.text}
                </span>
              ))}
            </span>
          </button>
        ) : (
          <p className="text-[13px] text-ink-muted">
            No policy was ever set up on card {decision.card_id} in this session.
          </p>
        )}
      </section>

      {link && (
        <section>
          <p className="mb-3 text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
            {RELATION_LABEL[link.relation] ?? 'Related to'}
          </p>
          {linked ? (
            <DecisionMark decision={linked} onClick={() => selectRelated(linked)} />
          ) : (
            <p className="text-[13px] text-ink-muted">An earlier purchase that isn&apos;t loaded here.</p>
          )}
        </section>
      )}

      {related.length > 0 && (
        <section>
          <p className="mb-3 text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
            Also caught on this card
          </p>
          <div className="flex flex-col gap-1">
            {related.map((r) => (
              <DecisionMark key={r.authorization_id} decision={r} onClick={() => selectRelated(r)} />
            ))}
          </div>
        </section>
      )}

      {isManipulated && signedInAs && (
        <button
          type="button"
          onClick={onGoHome}
          className="h-14 rounded-row bg-ink text-[16px] font-semibold text-on-ink"
        >
          Back to {signedInAs.name}&apos;s Home
        </button>
      )}
    </div>
  )
}
