import type { PolicyDraft } from '../../api/types'
import { NewPolicyShell } from './NewPolicyShell'

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
            disabled={confirming}
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
        {draft.open_questions.length > 0 && (
          <div className="rounded-row border border-asked-border bg-asked-tint px-4 py-3">
            {draft.open_questions.map((question) => (
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

      <section className="rounded-hero bg-ink p-5 text-on-ink">
        <p className="text-[11px] font-semibold tracking-[0.08em] text-on-ink-muted uppercase">
          Dry run on your history
        </p>
        <div className="mt-3 grid grid-cols-3 gap-3 text-center">
          <div>
            <p className="font-display text-[28px] font-bold text-stopped-on-ink tabular-nums">
              {dryRun.would_violate}
            </p>
            <p className="text-[12px] text-on-ink-muted">Would stop</p>
          </div>
          <div>
            <p className="font-display text-[28px] font-bold text-approved-on-ink tabular-nums">
              {dryRun.would_fit}
            </p>
            <p className="text-[12px] text-on-ink-muted">Would fit</p>
          </div>
          <div>
            <p className="font-display text-[28px] font-bold text-asked-on-ink tabular-nums">
              {dryRun.would_ask}
            </p>
            <p className="text-[12px] text-on-ink-muted">Would ask</p>
          </div>
        </div>
        <p className="mt-4 text-[13px] text-on-ink-soft">{dryRun.insight}</p>
      </section>
    </NewPolicyShell>
  )
}
