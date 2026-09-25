import type { FormInput } from '../../api/types'
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
  return (
    <NewPolicyShell
      step={1}
      stepLabel="Describe"
      title="What may your agent do?"
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
                Couldn&apos;t read these checks. Nothing was approved while we were offline. Nothing
                was saved. Try again.
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
            {aiOn
              ? 'Write a sentence. AI turns it into checks on our server.'
              : "Off: you fill in a form. It's instant and needs no AI."}
          </span>
        </span>
        <AiSwitchVisual on={aiOn} />
      </button>

      {!aiOn && (
        <p className="text-[13px] leading-[1.45] text-ink-soft">
          Every field below becomes one of your rules, exactly as you enter it. Turn AI on to describe it in your own words instead.
        </p>
      )}

      {aiOn ? (
        <label className="flex flex-col gap-2">
          <span className="text-[11px] font-semibold tracking-[0.09em] text-ink-muted uppercase">Your words</span>
          <textarea
            value={instruction}
            onChange={(e) => onChangeInstruction(e.target.value)}
            rows={6}
            placeholder="Buy groceries for me, up to CHF 120 per order…"
            className="h-[150px] resize-none rounded-card border-2 border-ink bg-surface p-3.5 text-[15px] leading-[1.5] text-ink"
          />
          <p className="text-[13px] leading-[1.45] text-ink-soft">
            Takes a few seconds. If it takes too long, we stop and save nothing. Prefer instant? Turn it off and fill in a form.
          </p>
        </label>
      ) : (
        <NewPolicyForm form={form} onChange={onChangeForm} />
      )}

    </NewPolicyShell>
  )
}
