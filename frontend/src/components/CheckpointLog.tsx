import type { ComponentType } from 'react'
import { useState } from 'react'
import type { Checkpoint, CheckpointStage } from '../api/types'
import type { IconProps } from './icons/IconProps'
import { CheckIcon, CrossIcon, HelpCircleIcon, InfoIcon } from './icons/lucide'

const STAGE_LABEL: Record<CheckpointStage, string> = {
  facts: 'Purchase read',
  status: 'Policy, permission and card',
  rules: 'Your rules',
  model: 'AI reading of missing facts',
  protections: 'Always-on protections',
  warnings: 'Warning signs',
  signals: 'Soft signals',
  decide: 'Decision',
  explain: 'Explanation',
  record: 'Recorded',
}

const OUTCOME_STYLE: Record<
  Checkpoint['outcome'],
  { Icon: ComponentType<IconProps>; fg: string; label: string }
> = {
  pass: { Icon: CheckIcon, fg: 'text-approved', label: 'Passed' },
  clear: { Icon: CheckIcon, fg: 'text-ink-muted', label: 'Clear' },
  fail: { Icon: CrossIcon, fg: 'text-stopped', label: 'Failed' },
  uncertain: { Icon: HelpCircleIcon, fg: 'text-asked', label: 'Needs you' },
  info: { Icon: InfoIcon, fg: 'text-ink-muted', label: 'Note' },
  done: { Icon: InfoIcon, fg: 'text-ink-muted', label: 'Done' },
}

function formatMs(ms: number): string {
  return ms < 1 ? `${ms.toFixed(2)} ms` : `${ms.toFixed(1)} ms`
}

/**
 * The engine's checkpoint log for one purchase (`../../docs/api-contract.md`
 * §3.10): every step in the order it ran, with each stage's time. It includes
 * protections and warning signs that ran and found nothing, which the
 * evidence above leaves out, so the customer can see everything that was
 * checked, not only what decided it. Clear rows are folded by default to keep
 * the page about the decision; one tap shows them.
 */
export function CheckpointLog({ checkpoints, latencyMs }: { checkpoints: Checkpoint[]; latencyMs?: number }) {
  const [showClear, setShowClear] = useState(false)

  const stages: { stage: CheckpointStage; ms?: number; rows: Checkpoint[] }[] = []
  for (const row of checkpoints) {
    const last = stages.at(-1)
    if (last && last.stage === row.stage) {
      last.rows.push(row)
      if (last.ms === undefined && row.ms !== undefined) last.ms = row.ms
    } else {
      stages.push({ stage: row.stage, ms: row.ms, rows: [row] })
    }
  }

  const checked = checkpoints.filter((c) => c.outcome !== 'done').length
  const clearCount = checkpoints.filter((c) => c.outcome === 'clear').length

  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">Checkpoint log</p>
        <p className="text-[12px] text-ink-muted tabular-nums">
          {checked} checks{latencyMs !== undefined ? ` · ${formatMs(latencyMs)}` : ''}
        </p>
      </div>

      <ol className="flex flex-col gap-4 rounded-card border border-hairline bg-surface p-4">
        {stages.map(({ stage, ms, rows }, stageIndex) => {
          const visible = showClear ? rows : rows.filter((r) => r.outcome !== 'clear')
          const hidden = rows.length - visible.length
          return (
            <li key={`${stage}-${stageIndex}`} className="flex flex-col gap-1.5">
              <p className="flex items-baseline justify-between gap-3 text-[13px] font-semibold text-ink">
                <span>
                  <span className="mr-2 text-ink-muted tabular-nums">{stageIndex + 1}.</span>
                  {STAGE_LABEL[stage] ?? stage}
                </span>
                {ms !== undefined && (
                  <span className="shrink-0 text-[12px] font-medium text-ink-muted tabular-nums">{formatMs(ms)}</span>
                )}
              </p>
              {visible.map((row, index) => {
                const style = OUTCOME_STYLE[row.outcome] ?? OUTCOME_STYLE.info
                return (
                  <div key={index} className="flex items-start gap-2.5 pl-6">
                    <span className={`mt-0.5 shrink-0 ${style.fg}`} aria-label={style.label}>
                      <style.Icon size={14} strokeWidth={2.6} />
                    </span>
                    <p className="min-w-0 text-[13px] text-ink-soft">
                      {/* A one-row stage named like its check says it once. */}
                      {row.check === STAGE_LABEL[stage] ? (
                        <span className="text-ink-muted">{row.detail}</span>
                      ) : (
                        <>
                          <span className="font-medium text-ink">{row.check}</span>
                          {row.detail && <span className="text-ink-muted"> — {row.detail}</span>}
                        </>
                      )}
                    </p>
                  </div>
                )
              })}
              {hidden > 0 && (
                <p className="pl-6 text-[12px] text-ink-muted">
                  {hidden} more checked, nothing found
                </p>
              )}
            </li>
          )
        })}
      </ol>

      {clearCount > 0 && (
        <button
          type="button"
          onClick={() => setShowClear((v) => !v)}
          className="self-start text-[13px] font-semibold text-ink underline underline-offset-2"
        >
          {showClear ? 'Hide checks that found nothing' : `Show all ${clearCount} checks that found nothing`}
        </button>
      )}
    </section>
  )
}
