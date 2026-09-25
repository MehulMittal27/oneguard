import { useState } from 'react'
import type { Health } from '../api/ops'
import { CheckIcon } from '../components/icons/lucide'
import { formatClock, flattenHealth } from '../lib/opsConsole'
import { BUTTON_SECONDARY, TEXT_M, TEXT_S, TONE_CLASS } from './style'

/**
 * The Health tab: the whole `/healthz` body as a table (nested keys joined with
 * dots, nothing left out), with a copy button, and the few lines an operator
 * checks first: the worker's last poll, the event cursor and the machine.
 */
export function HealthPanel({
  health,
  failed,
  onRefresh,
}: {
  health: Health | null | undefined
  failed: boolean
  onRefresh: () => void
}) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    if (!health) return
    try {
      await navigator.clipboard.writeText(JSON.stringify(health, null, 2))
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    } catch {
      setCopied(false)
    }
  }

  const rows = health ? flattenHealth(health) : []
  const worker = health?.worker
  const machine = health?.machine
  const lastPoll = worker?.last_poll_at ?? null
  const cursor = worker?.events_cursor ?? health?.events_cursor ?? null
  const machineLine = machine
    ? [
        machine.region,
        machine.cpus != null ? `${machine.cpus} CPU${machine.cpus === 1 ? '' : 's'}` : null,
        machine.memory_mb != null ? `${machine.memory_mb} MB` : null,
        machine.machine_id,
      ]
        .filter(Boolean)
        .join(' · ')
    : null

  return (
    <section aria-label="Health" className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-card border border-hairline bg-surface">
      <div className={`${TEXT_M} flex shrink-0 flex-wrap items-center gap-x-8 gap-y-3 border-b border-hairline px-8 py-4`}>
        <span
          className={`${TEXT_S} rounded-pill px-3 py-1 font-semibold ${
            failed ? TONE_CLASS.bad : health?.status === 'ok' ? TONE_CLASS.ok : health ? TONE_CLASS.warn : TONE_CLASS.neutral
          }`}
        >
          {failed ? 'no answer' : health === undefined ? 'reading…' : health === null ? 'mock mode' : (health.status ?? 'unknown')}
        </span>
        <Line label="Worker's last poll" value={lastPoll ? formatClock(lastPoll) : worker?.configured === false ? 'no worker' : '-'} />
        <Line label="Event cursor" value={cursor === null ? '-' : String(cursor)} />
        <Line label="Machine" value={machineLine || 'not exposed by this host'} />
        <div className="ml-auto flex gap-3">
          <button type="button" className={BUTTON_SECONDARY} onClick={onRefresh}>
            Refresh
          </button>
          <button type="button" className={BUTTON_SECONDARY} disabled={!health} onClick={copy}>
            {copied ? (
              <>
                <CheckIcon size={14} /> Copied
              </>
            ) : (
              'Copy JSON'
            )}
          </button>
        </div>
      </div>

      <div className="scrollbar-none min-h-0 flex-1 overflow-y-auto">
        {failed && (
          <p role="alert" className={`${TEXT_M} px-8 py-4 font-semibold text-stopped`}>
            /healthz did not answer; retrying. The table shows the last answer.
          </p>
        )}
        {rows.length === 0 ? (
          <p className={`${TEXT_M} px-8 py-6 text-ink-muted`}>
            {health === null ? 'Mock mode: no server to ask.' : 'Reading /healthz…'}
          </p>
        ) : (
          <table className={`${TEXT_M} w-full`}>
            <thead className="sr-only">
              <tr>
                <th>Key</th>
                <th>Value</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.key} className="border-b border-hairline last:border-b-0">
                  <th scope="row" className="w-[42%] px-8 py-2.5 text-left font-mono font-normal text-ink-muted">
                    {row.key}
                  </th>
                  <td className="px-8 py-2.5 font-mono break-all text-ink tabular-nums">{row.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  )
}

function Line({ label, value }: { label: string; value: string }) {
  return (
    <p className="flex items-baseline gap-2">
      <span className="text-ink-muted">{label}</span>
      <span className="font-semibold text-ink tabular-nums">{value}</span>
    </p>
  )
}
