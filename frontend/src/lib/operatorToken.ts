/**
 * The operator token for `/api/dev/*` (docs/api-contract.md §1.2, §6 item 23).
 *
 * In production the backend answers an operator call without the right
 * `X-OneGuard-Operator` header with `401 operator_required`. This wraps `fetch`
 * so each operator call sends the token kept in `sessionStorage` (the tab's
 * session: closing the tab forgets it), and a `401 operator_required` asks for
 * the token and retries:
 *
 * - once, however many calls were refused together: the console polls several
 *   operator endpoints at once, and they all share one question;
 * - again when the answer was wrong ("That token was not accepted");
 * - not again after the operator dismisses the question, until the page
 *   reloads: the refused call's 401 goes back to its caller as is.
 *
 * Off production the backend never asks, so nothing here ever prompts.
 * Framework-free and injected (fetch, storage, the question), so the tests run
 * it under node.
 */

export const OPERATOR_HEADER = 'X-OneGuard-Operator'
export const OPERATOR_TOKEN_KEY = 'oneguard.operatorToken'
export const OPERATOR_REQUIRED = 'operator_required'

/** Asks the operator for the token; `wrong` when the last one was refused. Null: dismissed. */
export type AskToken = (wrong: boolean) => Promise<string | null>

export interface TokenStore {
  get(): string | null
  set(token: string): void
  clear(): void
}

type Fetch = (input: string, init?: RequestInit) => Promise<Response>

/** `sessionStorage`, or nothing where it throws (a private window, blocked site data). */
export function sessionTokenStore(storage: () => Storage | undefined): TokenStore {
  return {
    get() {
      try {
        return storage()?.getItem(OPERATOR_TOKEN_KEY) || null
      } catch {
        return null
      }
    },
    set(token) {
      try {
        storage()?.setItem(OPERATOR_TOKEN_KEY, token)
      } catch {
        // Not kept: the next refused call asks again.
      }
    },
    clear() {
      try {
        storage()?.removeItem(OPERATOR_TOKEN_KEY)
      } catch {
        // Nothing kept to clear.
      }
    },
  }
}

/** Whether a response is the gate's refusal (not any other 401). */
async function refusedByGate(response: Response): Promise<boolean> {
  if (response.status !== 401) return false
  try {
    const body = (await response.clone().json()) as { error?: { code?: string } }
    return body.error?.code === OPERATOR_REQUIRED
  } catch {
    return false
  }
}

function withToken(init: RequestInit | undefined, token: string | null): RequestInit {
  const headers = new Headers(init?.headers)
  if (token) headers.set(OPERATOR_HEADER, token)
  else headers.delete(OPERATOR_HEADER)
  return { ...init, headers }
}

export function createOperatorFetch({
  fetch,
  store,
  ask,
}: {
  fetch: Fetch
  store: TokenStore
  ask: AskToken
}): Fetch {
  // The one question in flight, shared by every call refused meanwhile.
  let asking: Promise<string | null> | null = null
  let dismissed = false

  function question(wrong: boolean): Promise<string | null> {
    if (!asking) {
      asking = ask(wrong)
        .then((answer) => {
          const token = answer?.trim() || null
          if (token) store.set(token)
          else dismissed = true
          return token
        })
        .finally(() => {
          asking = null
        })
    }
    return asking
  }

  return async function operatorFetch(input, init) {
    let sent = store.get()
    for (;;) {
      const response = await fetch(input, withToken(init, sent))
      if (!(await refusedByGate(response))) return response
      const held = store.get()
      // Another call stored a new token while this one was out: retry with it.
      if (held && held !== sent) {
        sent = held
        continue
      }
      if (dismissed) return response
      if (sent) store.clear()
      const answer = await question(Boolean(sent))
      if (!answer) return response
      sent = answer
    }
  }
}
