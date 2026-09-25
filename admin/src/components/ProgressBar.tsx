/** A run's decided/total, with the pending-human share hatched — the console's
 * equivalent of `frontend/src/components/LeashMeter.tsx`, for a run instead of a spend limit. */
export function ProgressBar({ decided, pendingHuman, total }: { decided: number; pendingHuman: number; total: number }) {
  const safeTotal = Math.max(total, 1)
  const decidedPct = Math.min(100, (decided / safeTotal) * 100)
  const pendingPct = Math.min(100 - decidedPct, (pendingHuman / safeTotal) * 100)
  return (
    <div className="h-[10px] w-full overflow-hidden rounded-meter bg-surface-sunken">
      <div className="flex h-full">
        <div className="h-full bg-leash-fill" style={{ width: `${decidedPct}%` }} />
        <div className="h-full bg-asked-border" style={{ width: `${pendingPct}%` }} />
      </div>
    </div>
  )
}
