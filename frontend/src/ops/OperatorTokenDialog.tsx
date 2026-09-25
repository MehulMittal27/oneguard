import { useEffect, useState, type FormEvent } from 'react'
import { setOperatorTokenAsk } from '../api/operatorFetch'
import { BUTTON_PRIMARY, TEXT_L, TEXT_M, TEXT_S } from './style'

type Question = { wrong: boolean; answer: (token: string) => void }

/**
 * The console's operator-token question (docs/api-contract.md §6 item 23). In
 * production every `/api/dev/*` call needs the token; the first refused call
 * opens this, the answer is kept for the tab's session (`lib/operatorToken.ts`),
 * and every waiting call retries with it. A wrong token opens it again.
 *
 * It has no Cancel: without the token the console can start nothing and follow
 * no run, so there is nothing to go back to. Styled like `JudgingRunDialog`.
 */
export function OperatorTokenDialog() {
  const [question, setQuestion] = useState<Question | null>(null)
  const [typed, setTyped] = useState('')

  useEffect(
    () =>
      setOperatorTokenAsk(
        (wrong) =>
          new Promise<string>((resolve) => {
            setTyped('')
            setQuestion({ wrong, answer: resolve })
          }),
      ),
    [],
  )

  if (!question) return null
  const token = typed.trim()

  function submit(e: FormEvent) {
    e.preventDefault()
    if (!question || !token) return
    question.answer(token)
    setQuestion(null)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-8">
      <div aria-hidden className="absolute inset-0 bg-scrim" />
      <form
        role="dialog"
        aria-modal="true"
        aria-labelledby="ops-token-title"
        aria-describedby="ops-token-body"
        onSubmit={submit}
        className={`${TEXT_M} relative flex w-[min(520px,100%)] flex-col gap-5 rounded-card bg-surface p-9 shadow-[0_30px_60px_-20px_rgba(0,0,0,0.45)]`}
      >
        <h2 id="ops-token-title" className={`${TEXT_L} font-bold text-ink`}>
          Operator token
        </h2>
        <p id="ops-token-body" className="text-ink-soft">
          The operator endpoints are locked in production. Paste the operator token to start runs, switch soft signals
          and follow the current run. It is kept for this tab only and sent with operator calls, nowhere else.
        </p>
        <label className="flex flex-col gap-2 font-semibold text-ink">
          Token
          <input
            autoFocus
            type="password"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            aria-invalid={question.wrong}
            className="min-h-11 rounded-button border border-border-quiet px-4 font-normal"
          />
        </label>
        {question.wrong && (
          <p role="alert" className="rounded-row bg-stopped-tint px-4 py-3 text-stopped">
            That token was not accepted. Check it and try again.
          </p>
        )}
        <div className="flex items-center justify-between gap-3">
          <span className={`${TEXT_S} text-ink-muted`}>Header X-OneGuard-Operator</span>
          <button type="submit" disabled={!token} className={BUTTON_PRIMARY}>
            Unlock console
          </button>
        </div>
      </form>
    </div>
  )
}
