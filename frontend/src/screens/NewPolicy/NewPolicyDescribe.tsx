import { useState } from 'react'
import type { Card, FormInput } from '../../api/types'
import { CardPicker } from '../../components/CardPicker'
import { NewPolicyForm } from './NewPolicyForm'
import { NewPolicyShell } from './NewPolicyShell'

/**
 * DESIGN.md `PolicyInput`'s switch: ink track + knob, 52x32px pill. The
 * pill itself is under the 44px touch-target minimum, so the *visual* pill
 * is a plain span and the surrounding card row is the actual button —
 * see the ≥44px-tall card row in NewPolicyDescribe below.
 */
function AiSwitchVisual({ on }: { on: boolean }) {
  return (
    <span
      aria-hidden="true"
      /* The off track was a literal warm grey (#8a8478) belonging to the old
         cream palette, which reads as a stain against the recalibrated cool
         near-white. `ink-tab` is the one palette grey that clears 3:1 both
         ways: 6.0:1 under the white knob, 3.1:1 against the near-black on
         state, so neither the knob nor the on/off difference relies on
         position alone. */
      className={`relative h-[32px] w-13 shrink-0 rounded-pill transition-colors ${on ? 'bg-ink' : 'bg-ink-tab'
        }`}
    >
      <span
        className={`absolute top-[4px] size-[24px] rounded-full transition-transform ${on ? 'translate-x-6.5 bg-ground' : 'translate-x-[4px] bg-surface'
          }`}
      />
    </span>
  )
}

/**
 * DESIGN.md #13/#14 combined: one step-1 screen, body swaps with the "Read
 * my words with AI" switch rather than navigating to a different screen.
 */
export function NewPolicyDescribe({
  cardId,
  cards,
  onSelectCard,
  instruction,
  onChangeInstruction,
  form,
  onChangeForm,
  aiOn,
  onToggleAi,
  onReadWithAi,
  onSubmitForm,
  onCancel,
  formError,
}: {
  cardId: string
  // Every card on the same account (D-050) — a picker only makes sense
  // when there's more than one; an empty or single-item list falls back to
  // today's static "only this card" display.
  cards: Card[]
  onSelectCard: (cardId: string) => void
  instruction: string
  onChangeInstruction: (value: string) => void
  form: FormInput
  onChangeForm: (form: FormInput) => void
  aiOn: boolean
  onToggleAi: (on: boolean) => void
  onReadWithAi: () => void
  onSubmitForm: () => void
  onCancel: () => void
  formError: boolean
}) {
  const [pickingCard, setPickingCard] = useState(false)

  return (
    <NewPolicyShell
      step={1}
      stepLabel="Describe"
      title="Tell us what your agent may buy"
      subtitle={`For card ${cardId} only`}
      onCancel={onCancel}
      footer={
        aiOn ? (
          <>
            <button
              type="button"
              onClick={onReadWithAi}
              disabled={instruction.trim().length === 0}
              className="h-14 rounded-row bg-ink text-[16px] font-semibold text-on-ink transition-opacity disabled:cursor-not-allowed disabled:bg-surface-sunken disabled:text-ink-muted enabled:hover:opacity-90"
            >
              Read with AI
            </button>
            <button
              type="button"
              onClick={() => onToggleAi(false)}
              className="min-h-11 text-[15px] font-semibold text-ink-muted"
            >
              Use the form instead
            </button>
          </>
        ) : (
          <>
            {formError && (
              <p className="text-[13px] text-destructive">
                Couldn&apos;t read these checks — nothing was saved. Try again.
              </p>
            )}
            <button
              type="button"
              onClick={onSubmitForm}
              className="h-14 rounded-row bg-ink text-[16px] font-semibold text-on-ink enabled:hover:opacity-90"
            >
              Review checks
            </button>
            <button
              type="button"
              onClick={onCancel}
              className="min-h-11 text-[15px] font-semibold text-ink-muted"
            >
              Back without saving
            </button>
          </>
        )
      }
    >
      <section className="flex flex-col gap-3">
        <p className="text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
          Applies to
        </p>
        {cards.length > 1 ? (
          <button
            type="button"
            onClick={() => setPickingCard(true)}
            className="flex min-h-11 items-center justify-between rounded-row border border-border-quiet bg-surface-sunken px-4 py-3 text-left"
          >
            <span className="text-[15px] font-medium text-ink">Card {cardId}</span>
            <span className="text-[13px] font-semibold text-ink-muted underline underline-offset-2">
              Change
            </span>
          </button>
        ) : (
          <div className="flex min-h-11 items-center justify-between rounded-row border border-border-quiet bg-surface-sunken px-4 py-3">
            <span className="text-[15px] font-medium text-ink">Card {cardId}</span>
            <span className="text-[13px] text-ink-muted">Only this card</span>
          </div>
        )}
      </section>

      <button
        type="button"
        role="switch"
        aria-checked={aiOn}
        onClick={() => onToggleAi(!aiOn)}
        className="flex min-h-11 w-full items-center justify-between gap-4 rounded-card bg-surface p-5 text-left"
      >
        <span>
          <span className="block text-[15px] font-semibold text-ink">Read my words with AI</span>
          <span className="mt-1 block text-[13px] text-ink-muted">
            Takes a few seconds. If it takes too long, we stop and save nothing.
          </span>
        </span>
        <AiSwitchVisual on={aiOn} />
      </button>

      {aiOn ? (
        <label className="flex flex-col gap-1.5">
          <span className="text-[13px] font-medium text-ink-soft">Your words</span>
          <textarea
            value={instruction}
            onChange={(e) => onChangeInstruction(e.target.value)}
            rows={6}
            placeholder="Buy groceries for me, up to CHF 120 per order…"
            className="rounded-card border border-border-quiet bg-surface-sunken p-4 text-[15px] text-ink"
          />
        </label>
      ) : (
        <NewPolicyForm form={form} onChange={onChangeForm} />
      )}

      {pickingCard && (
        <CardPicker
          cards={cards}
          selectedCardId={cardId}
          onSelect={onSelectCard}
          onClose={() => setPickingCard(false)}
        />
      )}
    </NewPolicyShell>
  )
}
