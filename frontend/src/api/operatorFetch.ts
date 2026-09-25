import { createOperatorFetch, sessionTokenStore, type AskToken } from '../lib/operatorToken'

/**
 * `fetch` for every `/api/dev/*` call (`ops.ts`, `operator.ts`): sends the
 * operator token and asks for it on the gate's 401 (`lib/operatorToken.ts`,
 * docs/api-contract.md §6 item 23).
 *
 * The console (`/ops`) asks in its own dialog (`ops/OperatorTokenDialog.tsx`,
 * through `setOperatorTokenAsk`). Anywhere else, which is only the `?demo=1`
 * strip, the browser's own prompt asks.
 */
const browserPrompt: AskToken = async (wrong) =>
  window.prompt(
    wrong
      ? 'That token was not accepted. Operator token (X-OneGuard-Operator):'
      : 'Operator token (X-OneGuard-Operator), kept for this tab only:',
  )

let ask: AskToken = browserPrompt

/** Routes the token question to `next` until the returned function puts the default back. */
export function setOperatorTokenAsk(next: AskToken): () => void {
  ask = next
  return () => {
    if (ask === next) ask = browserPrompt
  }
}

export const operatorFetch = createOperatorFetch({
  fetch: (input, init) => fetch(input, init),
  store: sessionTokenStore(() => (typeof window === 'undefined' ? undefined : window.sessionStorage)),
  ask: (wrong) => ask(wrong),
})
