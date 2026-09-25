import type { Health } from '../api/ops'
import { ShieldIcon } from '../components/icons/lucide'
import { healthChips } from '../lib/opsConsole'
import { TEXT_M, TEXT_S, TONE_CLASS as CHIP_TONE } from './style'

/**
 * The console's header: "VISECA" as a text wordmark with a gold slash (no
 * third-party logo file: docs/brand-permission.md does not exist), the health
 * chips from `/healthz`, and OneGuard only as a small mark at the right edge.
 *
 * `health` is undefined until the first read, null in mock mode. `offline`: the
 * last read got no answer at all.
 */
export function OpsHeader({ health, offline }: { health: Health | null | undefined; offline: boolean }) {
  return (
    <header className="flex min-h-18 shrink-0 flex-wrap items-center gap-x-8 gap-y-3 border-b border-hairline bg-surface px-9 py-4">
      <div className="flex shrink-0 items-center gap-5">
        {/* The brand as text: colours and type only, no logo file. */}
        <span className="ops-wordmark" aria-label="Viseca">
          VISECA
        </span>
        <span className={`${TEXT_M} border-l border-hairline pl-5 text-ink-muted`}>Agent control console</span>
      </div>

      <ul aria-label="Health" className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
        {offline ? (
          <li className={`${TEXT_S} rounded-pill px-3 py-1 font-semibold ${CHIP_TONE.bad}`}>offline</li>
        ) : health === undefined ? (
          <li className={`${TEXT_S} rounded-pill px-3 py-1 ${CHIP_TONE.neutral}`}>reading health…</li>
        ) : health === null ? (
          <li className={`${TEXT_S} rounded-pill px-3 py-1 ${CHIP_TONE.neutral}`}>mock mode: nothing runs</li>
        ) : (
          healthChips(health).map((chip) => (
            <li
              key={chip.id}
              className={`${TEXT_S} rounded-pill px-3 py-1 font-semibold whitespace-nowrap tabular-nums ${CHIP_TONE[chip.tone]}`}
            >
              {chip.label}
            </li>
          ))
        )}
      </ul>

      <p className={`${TEXT_S} flex shrink-0 items-center gap-2 text-ink-muted`}>
        <ShieldIcon size={13} strokeWidth={2} />
        Powered by OneGuard
      </p>
    </header>
  )
}

/** The disclaimer every console page carries. */
export function OpsFooter() {
  return (
    <footer className={`${TEXT_S} shrink-0 border-t border-hairline bg-surface px-9 py-3 text-ink-muted`}>
      Prototype for the Swiss {'{ai}'} Weeks challenge · not an official Viseca product
    </footer>
  )
}
