import type { Scenario } from '../api/types'
import { Badge } from './Badge'

export function ScenarioCard({ scenario, selected, onClick }: { scenario: Scenario; selected: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full rounded-card border p-4 text-left transition-colors ${
        selected ? 'border-cord-accent bg-cord-accent-tint' : 'border-hairline bg-surface hover:border-border-quiet'
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-[14px] font-semibold text-ink">{scenario.scenario_name}</p>
          <p className="text-[11px] tabular-nums text-ink-muted">{scenario.scenario_id}</p>
        </div>
        <div className="flex shrink-0 flex-wrap justify-end gap-1">
          {scenario.served && <Badge tone="accent">Served</Badge>}
          {scenario.profile ? <Badge tone="approved">Bound</Badge> : <Badge tone="neutral">Unbound</Badge>}
          {scenario.active_run_id && <Badge tone="asked">Running</Badge>}
        </div>
      </div>
      <p className="mt-2 line-clamp-2 text-[12.5px] text-ink-soft">{scenario.cardholder_instruction}</p>
      {scenario.profile && (
        <p className="mt-2 text-[11px] tabular-nums text-ink-muted">
          {scenario.profile.name} ({scenario.profile.customer_id}) · card {scenario.profile.card_id}
        </p>
      )}
    </button>
  )
}
