import type { PolicyDraft } from '../../api/types'
import { formatShortDate } from '../../lib/datetime'
import { formatChf } from '../../lib/money'
import { canConfirmDraft, reviewQuestions } from '../../lib/policyReview'
import { NewPolicyShell } from './NewPolicyShell'

/**
 * A dry-run example's outcome is what this rule *would have* done to a purchase
 * the customer already made — it is not a decision, so it never borrows decision
 * wording or styling. The labels match the three counters directly above, so each
 * example reads as one case behind one counter, and each row carries its label as
 * text rather than colour alone (`.claude/CLAUDE.md` Accessibility). An outcome
 * this client doesn't recognise gets a neutral label instead of crashing — the
 * same fallback the reason-code map follows (hard rule 9).
 */
const EXAMPLE_OUTCOME: Record<string, { label: string; className: string }> = {
  violate: { label: 'Would stop', className: 'text-stopped' },
  fit: { label: 'Would fit', className: 'text-approved' },
  ask: { label: 'Would ask', className: 'text-asked' },
}

const UNKNOWN_EXAMPLE_OUTCOME = { label: 'Checked', className: 'text-ink-muted' }

/**
 * Contract §6 item 9's agent-history line, which names its own scope: this screen
 * and its dry run are card-scoped, and the card and customer counts differ
 * materially in the pack (CA0001: 14 on the card, 29 across the customer, so
 * the scope is not cosmetic). Zero attempts is worth saying — it
 * means this would be the first agent purchase on the card.
 */
function agentHistoryLine({ attempts, approved }: { attempts: number; approved: number }): string {
  if (attempts === 0) return 'No agent has bought on this card before.'
  const times = attempts === 1 ? 'once' : `${attempts} times`
  return `An agent has bought on this card ${times} before — ${approved} approved.`
}

/** DESIGN.md #7: step-2 review — checks, uncertainty choice, dry run, confirm. */
export function NewPolicyCheck({
  cardId,
  draft,
  uncertaintyPolicy,
  onChangeUncertaintyPolicy,
  onEdit,
  onConfirm,
  onCancel,
  confirming,
  error,
}: {
  cardId: string
  draft: PolicyDraft
  uncertaintyPolicy: 'ask' | 'decline'
  onChangeUncertaintyPolicy: (value: 'ask' | 'decline') => void
  onEdit: () => void
  onConfirm: () => void
  onCancel: () => void
  confirming: boolean
  error: boolean
}) {
  const { dry_run: dryRun } = draft
  // No checks read: C2 would refuse the draft, so confirming is off and the
  // open question says what to write instead (contract §6 item 13).
  const canConfirm = canConfirmDraft(draft)
  const questions = reviewQuestions(draft)

  return (
    <NewPolicyShell
      step={2}
      stepLabel="Review"
      title="Here's what I understood"
      subtitle={`For card ${cardId} only`}
      onCancel={onCancel}
      footer={
        <>
          {error && (
            <p className="text-[13px] text-destructive">
              Couldn&apos;t confirm this policy — nothing was saved. Try again.
            </p>
          )}
          <button
            type="button"
            onClick={onConfirm}
            disabled={confirming || !canConfirm}
            className="h-14 rounded-row bg-ink text-[16px] font-semibold text-on-ink transition-opacity disabled:cursor-not-allowed disabled:opacity-70 enabled:hover:opacity-90"
          >
            {confirming ? 'Confirming…' : 'Confirm policy'}
          </button>
          <button
            type="button"
            onClick={onCancel}
            className="min-h-11 text-[15px] font-semibold text-ink-muted"
          >
            Back without saving
          </button>
        </>
      }
    >
      {/* Contract §6 item 10. 'fallback' means a rule-based parse produced these
          checks, so they may be cruder — worth knowing before confirming. Not on
          the step-1 spinner: `compiler` arrives with the draft, so there is
          nothing to read while it is still up. */}
      {draft.compiler === 'fallback' && (
        <div
          role="status"
          className="rounded-row border border-asked-border bg-asked-tint px-4 py-3 text-[13px] text-asked-ink"
        >
          AI reading unavailable — rule-based reading used
        </div>
      )}

      {draft.instruction && (
        <div className="rounded-card border border-hairline bg-surface p-4">
          <div className="flex items-center justify-between">
            <p className="text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
              Your words
            </p>
            <button type="button" onClick={onEdit} className="min-h-11 text-[13px] font-semibold text-ink">
              Edit
            </button>
          </div>
          <p className="mt-2 text-[15px] text-ink-soft">{draft.instruction}</p>
        </div>
      )}

      <section className="flex flex-col gap-3">
        <p className="text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
          What I understood
        </p>
        {draft.checks.length === 0 && (
          <p className="text-[15px] text-ink-muted">No checks were read from this instruction.</p>
        )}
        {draft.checks.map((check) => (
          <div
            key={check.id}
            className="flex items-start justify-between gap-3 rounded-row border border-hairline bg-surface px-4 py-3"
          >
            <div>
              <p className="text-[15px] font-medium text-ink">{check.text}</p>
              {check.uncertainty && (
                <p className="mt-1 text-[13px] text-asked">{check.uncertainty}</p>
              )}
            </div>
            <span
              className={`shrink-0 rounded-pill px-2.5 py-1 text-[11px] font-semibold ${
                check.source === 'exact'
                  ? 'bg-approved-tint text-approved'
                  : 'bg-asked-tint text-asked'
              }`}
            >
              {check.source === 'exact' ? 'Exact' : 'My reading'}
            </span>
          </div>
        ))}
        {questions.length > 0 && (
          <div className="rounded-row border border-asked-border bg-asked-tint px-4 py-3">
            {questions.map((question) => (
              <p key={question} className="text-[13px] text-asked-ink">
                {question}
              </p>
            ))}
          </div>
        )}
      </section>

      <section className="flex flex-col gap-3">
        <p className="text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
          When I&apos;m not sure
        </p>
        <div className="grid grid-cols-2 gap-3">
          {/* Never "approve" — PolicyInput never offers it (frontend/.claude/DESIGN.md). */}
          {[
            { value: 'ask' as const, label: 'Ask me' },
            { value: 'decline' as const, label: 'Block' },
          ].map((option) => (
            <button
              key={option.value}
              type="button"
              aria-pressed={uncertaintyPolicy === option.value}
              onClick={() => onChangeUncertaintyPolicy(option.value)}
              className={`min-h-11 rounded-row border-2 px-4 py-3 text-[15px] font-semibold ${
                uncertaintyPolicy === option.value
                  ? 'border-ink bg-surface-sunken text-ink'
                  : 'border-border-quiet text-ink-soft'
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </section>

      <section className="rounded-hero border border-hairline bg-surface p-5">
        <p className="text-[11px] font-semibold tracking-[0.08em] text-cord-accent uppercase">
          Dry run on your history
        </p>
        <div className="mt-3 grid grid-cols-3 gap-3 text-center">
          <div>
            <p className="font-display text-[28px] font-bold text-stopped tabular-nums">
              {dryRun.would_violate}
            </p>
            <p className="text-[12px] text-ink-muted">Would stop</p>
          </div>
          <div>
            <p className="font-display text-[28px] font-bold text-approved tabular-nums">
              {dryRun.would_fit}
            </p>
            <p className="text-[12px] text-ink-muted">Would fit</p>
          </div>
          <div>
            <p className="font-display text-[28px] font-bold text-asked tabular-nums">
              {dryRun.would_ask}
            </p>
            <p className="text-[12px] text-ink-muted">Would ask</p>
          </div>
        </div>
        <p className="mt-4 text-[13px] text-ink-muted">{dryRun.insight}</p>

        {/* The rows behind the counters — up to 3, one per outcome that occurred. */}
        {dryRun.examples && dryRun.examples.length > 0 && (
          <div className="mt-4 flex flex-col gap-3 border-t border-hairline pt-4">
            {dryRun.examples.slice(0, 3).map((example) => {
              const outcome = EXAMPLE_OUTCOME[example.outcome] ?? UNKNOWN_EXAMPLE_OUTCOME
              return (
                <div
                  key={`${example.occurred_at}-${example.merchant_name}`}
                  className="flex items-start justify-between gap-3"
                >
                  <div className="min-w-0">
                    {/* Merchant name is untrusted shop text — plain text node only. */}
                    <p className="truncate text-[14px] font-medium text-ink">
                      {example.merchant_name}
                    </p>
                    <p className="mt-0.5 text-[12px] text-ink-muted">
                      {formatShortDate(example.occurred_at)} · {example.reason}
                    </p>
                  </div>
                  <div className="shrink-0 text-right">
                    <p className="text-[14px] font-medium text-ink tabular-nums">
                      {formatChf(example.billing_amount_chf)}
                    </p>
                    <p className={`mt-0.5 text-[11px] font-semibold ${outcome.className}`}>
                      {outcome.label}
                    </p>
                  </div>
                </div>
              )
            })}
          </div>
        )}

        {dryRun.agent_history && (
          <p className="mt-4 border-t border-hairline pt-4 text-[13px] text-ink-muted">
            {agentHistoryLine(dryRun.agent_history)}
          </p>
        )}
      </section>
    </NewPolicyShell>
  )
}
