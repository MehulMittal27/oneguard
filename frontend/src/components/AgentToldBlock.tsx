import { useState } from 'react'
import { getReceipt, verifyDocument } from '../api/passport'
import type { Decision, VerifyResult } from '../api/types'
import { CheckIcon, CrossIcon } from './icons/lucide'

/**
 * DecisionDetail's two passport lines (`../../docs/api-contract.md` §6 item 18):
 * "What your agent was told" — a decline's `would_approve_if`, said in the
 * words of the counterfactual the customer already reads — and the decision's
 * signed receipt, which a tap sends to OneGuard's verify.
 */
export function AgentToldBlock({ decision }: { decision: Decision }) {
  const told = decision.would_approve_if && decision.would_approve_if.length > 0 ? decision.counterfactual : null
  const [result, setResult] = useState<VerifyResult | 'checking' | 'missing' | 'error' | null>(null)
  const hasReceipt = Boolean(decision.receipt_id)

  if (!told && !hasReceipt) return null

  async function check() {
    setResult('checking')
    try {
      const receipt = await getReceipt(decision.authorization_id)
      if (!receipt) {
        setResult('missing')
        return
      }
      setResult(await verifyDocument({ document: receipt.document, signature: receipt.signature, key_id: receipt.key_id }))
    } catch {
      setResult('error')
    }
  }

  return (
    <section className="flex flex-col gap-3">
      {told && (
        <div className="rounded-card border border-hairline bg-surface p-4">
          <p className="text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
            What your agent was told
          </p>
          <p className="mt-2 text-[15px] font-medium text-ink">{told}</p>
          <p className="mt-1 text-[12px] text-ink-muted">
            Sent with the decision, so the agent&apos;s next try can stay inside your rules.
          </p>
        </div>
      )}
      {hasReceipt && (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-ink-muted">
          <span>Receipt</span>
          <span aria-hidden="true">·</span>
          <button
            type="button"
            onClick={check}
            disabled={result === 'checking'}
            className="min-h-11 font-semibold text-ink underline underline-offset-2 disabled:opacity-60"
          >
            {result === 'checking' ? 'Checking…' : 'Verify'}
          </button>
          {result && result !== 'checking' && (
            <span
              role="status"
              className={`flex items-center gap-1 ${
                typeof result === 'object' && result.valid ? 'text-approved' : 'text-stopped'
              }`}
            >
              {typeof result === 'object' && result.valid ? <CheckIcon size={14} /> : <CrossIcon size={14} />}
              {typeof result === 'object'
                ? result.valid
                  ? 'Signed by OneGuard, unchanged'
                  : `Not valid · ${result.reason}`
                : result === 'missing'
                  ? 'No receipt yet — try again in a moment'
                  : "Couldn't reach OneGuard to check"}
            </span>
          )}
        </div>
      )}
    </section>
  )
}
