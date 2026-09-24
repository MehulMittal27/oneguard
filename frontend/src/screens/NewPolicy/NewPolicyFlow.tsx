import { useEffect, useState } from 'react'
import { getAccounts } from '../../api/accounts'
import { compilePolicy, confirmPolicy } from '../../api/policy'
import type { Account, FormInput, Mandate, PolicyDraft } from '../../api/types'
import { useCustomer } from '../../state/CustomerContext'
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

type Step = 'describe' | 'reading' | 'timeout' | 'check'

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
  // Which card the policy applies to — starts at whichever card this flow
  // was opened for (Home/Accounts/Card detail), but the customer can pick a
  // sibling card on the same account before compiling (D-050). Only fetched
  // for the "Applies to" picker; a failed or still-loading fetch just means
  // no other cards to offer yet, not a blocked flow.
  const [account, setAccount] = useState<Account | null>(null)
  const [selectedCardId, setSelectedCardId] = useState(cardId)

  useEffect(() => {
    if (!signedInAs) return
    let cancelled = false
    getAccounts(signedInAs.customer_id)
      .then((accounts) => {
        if (cancelled) return
        setAccount(accounts.find((a) => a.cards.some((c) => c.card_id === cardId)) ?? null)
      })
      .catch(() => {
        // No sibling cards to offer — same as a single-card account.
      })
    return () => {
      cancelled = true
    }
  }, [signedInAs, cardId])

  async function readWithAi() {
    setStep('reading')
    try {
      const result = await compilePolicy(selectedCardId, { instruction })
      setDraft(result)
      setUncertaintyPolicy(result.uncertainty_policy)
      setStep('check')
    } catch {
      setStep('timeout')
    }
  }

  async function submitForm() {
    setFormError(false)
    try {
      const result = await compilePolicy(selectedCardId, { form })
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
      const mandate = await confirmPolicy({ ...draft, uncertainty_policy: uncertaintyPolicy })
      onConfirmed(mandate)
    } catch {
      setConfirmError(true)
    } finally {
      setConfirming(false)
    }
  }

  if (step === 'reading') {
    return <NewPolicyReading instruction={instruction} onCancel={onClose} />
  }

  if (step === 'timeout') {
    return (
      <NewPolicyTimeout
        instruction={instruction}
        onCancel={onClose}
        onRetry={readWithAi}
        onUseForm={() => {
          setAiOn(false)
          setStep('describe')
        }}
      />
    )
  }

  if (step === 'check' && draft) {
    return (
      <NewPolicyCheck
        cardId={selectedCardId}
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
      cardId={selectedCardId}
      cards={account?.cards ?? []}
      onSelectCard={setSelectedCardId}
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
