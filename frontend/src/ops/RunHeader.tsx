import { useEffect, useState } from 'react'
import { CheckIcon, CrossIcon } from '../components/icons/lucide'
import { formatClock, formatElapsed, type ConsoleRun, type PassportSummary, type Verification } from '../lib/opsConsole'
import { TEXT_M, TEXT_S, TONE_CLASS } from './style'

export interface PassportLine {
  summary: PassportSummary | null
  // null: not checked (no verifier answered).
  verification: Verification | null
  qrUrl: string | null
}

const STATE: Record<ConsoleRun['state'], { label: string; tone: keyof typeof TONE_CLASS }> = {
  starting: { label: 'starting', tone: 'warn' },
  running: { label: 'running', tone: 'ok' },
  done: { label: 'done', tone: 'neutral' },
  error: { label: 'error', tone: 'bad' },
}

/**
 * The run the console follows (D7): whose it is, where it stands, and the card's
 * passport. `run` is undefined before the first read and null when no run has
 * started. `waiting` is counted from the stream, which C6 keeps current as the
 * customer answers.
 */
export function RunHeader({
  run,
  waiting,
  passport,
}: {
  run: ConsoleRun | null | undefined
  waiting: number
  passport: PassportLine | null
}) {
  const active = run?.state === 'running' || run?.state === 'starting'
  const now = useNow(active)

  if (run === undefined) {
    return <div className="h-[88px] animate-pulse rounded-card bg-surface-sunken" aria-busy="true" />
  }
  if (run === null) {
    return (
      <section aria-label="Current run" className="rounded-card border border-hairline bg-surface px-8 py-6">
        <p className={`${TEXT_M} text-ink-muted`}>No run has started yet. Pick a scenario and replay it.</p>
      </section>
    )
  }

  const state = STATE[run.state]
  const decided = run.decided ?? null
  return (
    <section
      aria-label="Current run"
      className="flex flex-wrap items-center gap-x-8 gap-y-3 rounded-card border border-hairline bg-surface px-8 py-5"
    >
      <div className="flex min-w-0 flex-col gap-1">
        <p className={`${TEXT_M} flex flex-wrap items-center gap-3 font-semibold text-ink`}>
          <span className="tabular-nums">
            {run.kind === 'live' ? 'Judging run' : 'Replay'} · {run.scenarioId}
          </span>
          <span className={`${TEXT_S} rounded-pill px-3 py-0.5 font-semibold ${TONE_CLASS[state.tone]}`}>{state.label}</span>
        </p>
        <p className={`${TEXT_M} text-ink-muted`}>
          {run.customerName ?? run.customerId ?? 'Unknown customer'}
          {run.customerId && run.customerName ? ` (${run.customerId})` : ''} · card {run.cardId}
          {run.policy ? ` · ${run.policy}` : ''}
        </p>
      </div>

      <dl className={`${TEXT_M} flex flex-wrap gap-x-6 gap-y-1 tabular-nums`}>
        <Stat label="Delivered" value={`${run.delivered}/${run.total}`} />
        <Stat label="Decided" value={decided === null ? '-' : String(decided)} />
        <Stat label="Waiting" value={String(waiting)} highlight={waiting > 0} />
        <Stat label="Started" value={run.startedAt ? formatClock(run.startedAt) : '-'} />
        <Stat
          label="Elapsed"
          value={active && run.startedAt ? formatElapsed(now - Date.parse(run.startedAt)) : run.state === 'done' ? 'finished' : '-'}
        />
      </dl>

      <Passport line={passport} />
      {run.lastError && <p className={`${TEXT_M} w-full text-stopped`}>{run.lastError}</p>}
    </section>
  )
}

function Stat({ label, value, highlight = false }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className="text-ink-muted">{label}</dt>
      <dd className={`font-semibold ${highlight ? 'text-asked-ink' : 'text-ink'}`}>{value}</dd>
    </div>
  )
}

/**
 * "Passport v<n> · <k> checks · <d> devices · ✓ verified" with a small QR, or
 * "Passport —" when the backend has no passport for the card (or no passport
 * endpoints at all). Read-only: devices are managed on the phone.
 */
function Passport({ line }: { line: PassportLine | null }) {
  const [qrFailed, setQrFailed] = useState<string | null>(null)
  const summary = line?.summary ?? null
  if (!summary) {
    return <p className={`${TEXT_S} ml-auto text-ink-muted`}>Passport —</p>
  }
  const verification = line?.verification ?? null
  return (
    <div className="ml-auto flex items-center gap-3">
      <p className={`${TEXT_S} flex items-center gap-1 font-semibold text-ink-soft tabular-nums`}>
        Passport v{summary.version} · {summary.checks} {summary.checks === 1 ? 'check' : 'checks'} ·{' '}
        {summary.devices} {summary.devices === 1 ? 'device' : 'devices'}
        {verification?.verified && (
          <span className="flex items-center gap-1 text-approved">
            {' · '}
            <CheckIcon size={12} /> verified
          </span>
        )}
        {verification && !verification.verified && (
          <span className="flex items-center gap-1 text-stopped">
            {' · '}
            <CrossIcon size={12} /> not verified
          </span>
        )}
      </p>
      {line?.qrUrl && qrFailed !== line.qrUrl && (
        <img
          src={line.qrUrl}
          alt="Passport QR code"
          width={44}
          height={44}
          onError={() => setQrFailed(line.qrUrl)}
          className="size-11 rounded-stamp border border-hairline bg-surface"
        />
      )}
    </div>
  )
}

/** The real clock, ticking each second while `running`. */
function useNow(running: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!running) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [running])
  return now
}
