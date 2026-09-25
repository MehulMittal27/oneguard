import { useState } from 'react'
import { compilePolicy, confirmPolicy } from '../../api/policy'
import type { FormInput, Mandate, PolicyDraft } from '../../api/types'
import { useCustomer } from '../../state/CustomerContext'
import { DeviceGateCancelled, useDevice } from '../../state/DeviceContext'
import { usePolicy } from '../../state/PolicyContext'
import { isFirstPassport, markReveal } from '../../lib/passportReveal'
import { NewPolicyConfirmed } from './NewPolicyConfirmed'
import { NewPolicyCheck } from './NewPolicyCheck'
import { NewPolicyDescribe } from './NewPolicyDescribe'
import { NewPolicyReading } from './NewPolicyReading'
import { NewPolicyTimeout } from './NewPolicyTimeout'

const EMPTY_FORM: FormInput = {
  per_order_limit_chf: null,
  period_limit_chf: null,
  period_days: null,
  categories: [],
  sellers_used_before_only: false,
  uncertainty_policy: 'ask',
}

type Step = 'describe' | 'reading' | 'timeout' | 'check' | 'confirmed'
/**
 * Owns the new-policy flow's state machine (DESIGN.md #7/#13-16, C1/C2).
 * No router exists yet — the flow is a sibling "mode" to the tab bar,
 * rendered instead of it while active (App.tsx).
 */
export function NewPolicyFlow({
  cardId,
  onClose,
  onConfirmed,
}: {
  cardId: string
  onClose: () => void
  onConfirmed: (mandate: Mandate) => void
}) {
  const { signedInAs } = useCustomer()
  const { withDevice } = useDevice()
  const { policiesByCard } = usePolicy()
  // The confirmed policy, held for step 3 when it issued the card's first passport.
  const [confirmed, setConfirmed] = useState<Mandate | null>(null)
  const [step, setStep] = useState<Step>('describe')
  const [aiOn, setAiOn] = useState(true)
  const [instruction, setInstruction] = useState('')
  const [form, setForm] = useState<FormInput>(EMPTY_FORM)
  const [draft, setDraft] = useState<PolicyDraft | null>(null)
  const [uncertaintyPolicy, setUncertaintyPolicy] = useState<'ask' | 'decline'>('ask')
  const [confirming, setConfirming] = useState(false)
  // Neither path had a real error state — see ROADMAP.md slice 9. The
  // form path has no AI round-trip, so a failure there isn't "the AI took
  // too long" (NewPolicyTimeout's copy) — a separate inline error instead.
  const [confirmError, setConfirmError] = useState(false)
  const [formError, setFormError] = useState(false)
  const [compileError, setCompileError] = useState('')
  const [compileTimedOut, setCompileTimedOut] = useState(false)
  const [compileFallback, setCompileFallback] = useState(false)
  async function readWithAi() {
    setCompileError('')
    setCompileTimedOut(false)
    setCompileFallback(false)
    setStep('reading')
    try {
      const result = await compilePolicy(cardId, { instruction })
      if (result.compiler === 'fallback') {
        setCompileError('AI reading unavailable — rule-based reading used.')
        setCompileFallback(true)
        setStep('timeout')
        return
      }
      setDraft(result)
      setUncertaintyPolicy(result.uncertainty_policy)
      setStep('check')
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Policy reading failed.'
      setCompileError(message)
      setCompileTimedOut(/tim(?:e|ed)\s*out|took too long/i.test(message))
      setStep('timeout')
    }
  }

  async function submitForm() {
    setFormError(false)
    try {
      const result = await compilePolicy(cardId, { form })
      setDraft(result)
      setUncertaintyPolicy(result.uncertainty_policy)
      setStep('check')
    } catch {
      setFormError(true)
    }
  }

  async function handleConfirm() {
    if (!draft) return
    setConfirming(true)
    setConfirmError(false)
    try {
      // C2 is signed by this device on the draft's card: enrolled silently when
      // it is the card's first, else approved first by one that controls it.
      const before = policiesByCard[draft.card_id]
      const mandate = await withDevice(draft.card_id, () =>
        confirmPolicy({ ...draft, uncertainty_policy: uncertaintyPolicy }),
      )
      if (isFirstPassport(before, mandate) && mandate.passport) {
        // The card's first passport: seal it on step 3, and once on its Passport section.
        markReveal(mandate.card_id, mandate.passport.passport_id)
        setConfirmed(mandate)
        setStep('confirmed')
      } else {
        onConfirmed(mandate)
      }
    } catch (caught) {
      if (!(caught instanceof DeviceGateCancelled)) setConfirmError(true)
    } finally {
      setConfirming(false)
    }
  }

  if (step === 'confirmed' && confirmed) {
    return (
      <NewPolicyConfirmed
        mandate={confirmed}
        holderName={signedInAs?.name ?? ''}
        onDone={() => onConfirmed(confirmed)}
      />
    )
  }

  if (step === 'reading') {
    return <NewPolicyReading instruction={instruction} onCancel={onClose} />
  }

  if (step === 'timeout') {
    return (
      <NewPolicyTimeout
        onBack={() => setStep('describe')}
        errorMessage={compileError}
        timedOut={compileTimedOut}
        fallback={compileFallback}
        onRetry={readWithAi}
      />
    )
  }

  if (step === 'check' && draft) {
    return (
      <NewPolicyCheck
        draft={draft}
        uncertaintyPolicy={uncertaintyPolicy}
        onChangeUncertaintyPolicy={setUncertaintyPolicy}
        onEdit={() => setStep('describe')}
        onConfirm={handleConfirm}
        onCancel={onClose}
        confirming={confirming}
        error={confirmError}
      />
    )
  }

  return (
    <NewPolicyDescribe
      instruction={instruction}
      onChangeInstruction={setInstruction}
      form={form}
      onChangeForm={setForm}
      aiOn={aiOn}
      onToggleAi={setAiOn}
      onReadWithAi={readWithAi}
      onSubmitForm={submitForm}
      onCancel={onClose}
      formError={formError}
    />
  )
}
