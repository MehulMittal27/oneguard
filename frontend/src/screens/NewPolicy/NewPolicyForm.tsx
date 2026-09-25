import type { FormInput } from '../../api/types'
import { CheckIcon, PlusIcon } from '../../components/icons/lucide'

// A representative sample of DESIGN.md's category vocabulary
// (data/merchants.csv / items.csv have the full list).
const CATEGORIES = [
  'groceries',
  'household',
  'dining',
  'food_delivery',
]

const PERIOD_OPTIONS = [7, 14, 30] as const

/**
 * DESIGN.md `PolicyInput`, AI-off path (#14): instant form, no compile
 * round-trip. Rendered inside NewPolicyDescribe when the switch is off.
 */
export function NewPolicyForm({
  form,
  onChange,
}: {
  form: FormInput
  onChange: (form: FormInput) => void
}) {
  function toggleCategory(category: string) {
    const has = form.categories.includes(category)
    onChange({
      ...form,
      categories: has ? form.categories.filter((c) => c !== category) : [...form.categories, category],
    })
  }

  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-3">
        <p className="text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
          Spending limits
        </p>
        <label className="flex flex-col gap-1.5">
          <span className="text-[15px] font-medium text-ink">Each order up to</span>
          <span className="flex items-center gap-3">
          <span className="text-[14px] font-semibold text-ink-muted">CHF</span>
          <input
            type="number"
            min={0}
            inputMode="decimal"
            value={form.per_order_limit_chf ?? ''}
            onChange={(e) =>
              onChange({
                ...form,
                per_order_limit_chf: e.target.value === '' ? null : Number(e.target.value),
              })
            }
            className="h-12 min-w-0 flex-1 rounded-row border border-border-quiet bg-surface px-4 text-[15px] text-ink tabular-nums"
          />
          </span>
          <span className="text-[13px] text-ink-muted">Delivery counts toward the limit.</span>
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-[15px] font-medium text-ink">Total across the period up to</span>
          <span className="flex items-center gap-3">
          <span className="text-[14px] font-semibold text-ink-muted">CHF</span>
          <input
            type="number"
            min={0}
            inputMode="decimal"
            value={form.period_limit_chf ?? ''}
            onChange={(e) =>
              onChange({
                ...form,
                period_limit_chf: e.target.value === '' ? null : Number(e.target.value),
                period_days: e.target.value === '' ? null : form.period_days,
              })
            }
            className="h-12 min-w-0 flex-1 rounded-row border border-border-quiet bg-surface px-4 text-[15px] text-ink tabular-nums"
          />
          </span>
        </label>
        <p className="text-[13px] font-medium text-ink-soft">Period</p>
        <div className="flex gap-2">
          {PERIOD_OPTIONS.map((days) => (
            <button
              key={days}
              type="button"
              disabled={form.period_limit_chf == null}
              aria-pressed={form.period_days === days}
              onClick={() => onChange({ ...form, period_days: days })}
              className={`h-11 flex-1 rounded-tile text-[13px] font-medium ${
                form.period_limit_chf == null
                  ? 'cursor-not-allowed border border-hairline bg-surface-sunken text-ink-muted opacity-60'
                  : form.period_days === days
                  ? 'bg-ink text-on-ink'
                  : 'border border-hairline bg-surface-sunken text-ink-soft'
              }`}
            >
              {days} days
            </button>
          ))}
        </div>
        <p className="text-[13px] leading-[1.45] text-ink-muted">
          {form.period_limit_chf == null
            ? 'Enter a period total before choosing its rolling window.'
            : form.period_days == null
              ? 'Choose a period window to apply this total. Older orders drop off day by day; only approved purchases count.'
              : 'A rolling window: older orders drop off day by day. Only approved purchases count.'}
        </p>
      </section>

      <section className="flex flex-col gap-3">
        <p className="text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
          What it may buy
        </p>
        <div className="flex flex-wrap gap-2">
          {CATEGORIES.map((category) => {
            const active = form.categories.includes(category)
            return (
              <button
                key={category}
                type="button"
                aria-pressed={active}
                onClick={() => toggleCategory(category)}
                className={`flex min-h-11 items-center gap-1.5 rounded-pill px-4 text-[13px] font-medium ${
                  active ? 'bg-ink text-on-ink' : 'border border-hairline bg-surface text-ink-soft'
                }`}
              >
                {active ? <CheckIcon size={14} strokeWidth={2.4} /> : <PlusIcon size={14} strokeWidth={2.4} />}
                {category === 'food_delivery' ? 'Food delivery' : category[0].toUpperCase() + category.slice(1)}
              </button>
            )
          })}
        </div>
        <p className="text-[13px] text-ink-muted">
          {form.categories.length > 0
            ? 'Anything outside these is uncertain, so it asks you first.'
            : 'Choose categories to add item rules. Other purchase limits still apply.'}
        </p>
      </section>

      <section className="flex flex-col gap-3">
        <p className="text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
          Which sellers
        </p>
        <div className="flex flex-col gap-2">
          {[
            { value: false, label: 'Any seller' },
            { value: true, label: 'Only sellers this card has used' },
          ].map((option) => (
            <button
              key={String(option.value)}
              type="button"
              aria-pressed={form.sellers_used_before_only === option.value}
              onClick={() => onChange({ ...form, sellers_used_before_only: option.value })}
              className={`min-h-11 rounded-row border px-4 py-3 text-left text-[15px] font-medium ${
                form.sellers_used_before_only === option.value
                  ? 'border-ink bg-surface-sunken text-ink'
                  : 'border-border-quiet text-ink-soft'
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <p className="text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
          When I&apos;m not sure
        </p>
        <div className="grid grid-cols-2 gap-3">
          {/* Never "approve" — PolicyInput never offers it (frontend/.claude/DESIGN.md). */}
          {[
            { value: 'ask' as const, label: 'Ask me' },
            { value: 'decline' as const, label: 'Stop it' },
          ].map((option) => (
            <button
              key={option.value}
              type="button"
              aria-pressed={form.uncertainty_policy === option.value}
              onClick={() => onChange({ ...form, uncertainty_policy: option.value })}
              className={`min-h-11 rounded-row border-2 px-4 py-3 text-[15px] font-semibold ${
                form.uncertainty_policy === option.value
                  ? 'border-ink bg-surface-sunken text-ink'
                  : 'border-border-quiet text-ink-soft'
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
        <p className="text-[13px] leading-[1.45] text-ink-muted">
          Ask me: you get a card in Approvals and about two minutes to answer. If time runs out, nothing is paid. There is no “approve when unsure” option.
        </p>
      </section>
    </div>
  )
}
