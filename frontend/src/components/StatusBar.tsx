import { useEffect, useState } from 'react'

function formatClock(date: Date): string {
  const hours = String(date.getHours()).padStart(2, '0')
  const minutes = String(date.getMinutes()).padStart(2, '0')
  return `${hours}:${minutes}`
}

/**
 * A cosmetic mock phone status bar (D-072). The time is real — the
 * visitor's own local wall-clock time via `Date`, ticking every 15s (a
 * status bar has never needed second-level precision) — not simulated
 * scenario time; unrelated to `lib/datetime.ts`'s formatters, which
 * exist for authorization timestamps, a different clock entirely.
 * Signal/wifi/battery are static decoration, same as a screenshot.
 *
 * Only ever rendered inside `DeviceFrame`'s `sm:` breakpoint — a real
 * phone already shows its own native status bar, so every call site
 * gates this with `hidden sm:flex`, matching every other DeviceFrame-
 * only element (D-065 onward). It renders as a persistent, non-scrolling
 * sibling *before* each screen's own scrollable content (the same
 * placement D-070 used for the plain spacer this replaces), so scrolled
 * content structurally cannot overlap or scroll under it.
 *
 * Height and horizontal padding are literal pixel values (D-073), not
 * this project's numbered spacing scale — `h-N`/`px-N` here resolve to
 * DESIGN.md's own irregular scale (`p-7` is 16px, not Tailwind's default
 * 28px), which was too little of both to clear `DeviceFrame`'s screen
 * radius (36px): content sat inside the tightest part of the corner
 * curve. These values are picked to clear that curve with real margin,
 * not from the named scale.
 */
export function StatusBar({ tone }: { tone: 'ink' | 'on-ink' }) {
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    const interval = setInterval(() => setNow(new Date()), 15000)
    return () => clearInterval(interval)
  }, [])

  const textColor = tone === 'ink' ? 'text-ink' : 'text-on-ink'

  return (
    <div
      className={`hidden shrink-0 items-center justify-between px-[28px] sm:flex sm:h-[40px] ${textColor}`}
    >
      <span className="font-sans text-[13px] font-semibold tabular-nums">{formatClock(now)}</span>
      <div className="flex items-center gap-1.5" aria-hidden="true">
        <svg width="16" height="11" viewBox="0 0 16 11" fill="none">
          <rect x="0" y="7" width="3" height="4" rx="0.5" fill="currentColor" />
          <rect x="4.5" y="5" width="3" height="6" rx="0.5" fill="currentColor" />
          <rect x="9" y="3" width="3" height="8" rx="0.5" fill="currentColor" />
          <rect x="13" y="0" width="3" height="11" rx="0.5" fill="currentColor" />
        </svg>
        <svg width="14" height="11" viewBox="0 0 14 11" fill="none">
          <path d="M7 9.2a1 1 0 1 0 0 2 1 1 0 0 0 0-2z" fill="currentColor" />
          <path
            d="M4 6.5a4.2 4.2 0 0 1 6 0"
            stroke="currentColor"
            strokeWidth="1.4"
            strokeLinecap="round"
            fill="none"
          />
          <path
            d="M1.5 3.8a7.8 7.8 0 0 1 11 0"
            stroke="currentColor"
            strokeWidth="1.4"
            strokeLinecap="round"
            fill="none"
          />
        </svg>
        <svg width="22" height="11" viewBox="0 0 22 11" fill="none">
          <rect x="0.5" y="0.5" width="18" height="10" rx="2.5" stroke="currentColor" />
          <rect x="2" y="2" width="14" height="7" rx="1.5" fill="currentColor" />
          <rect x="19" y="3.5" width="1.5" height="4" rx="0.75" fill="currentColor" />
        </svg>
      </div>
    </div>
  )
}
