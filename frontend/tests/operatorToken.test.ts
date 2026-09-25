import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  OPERATOR_HEADER,
  OPERATOR_TOKEN_KEY,
  createOperatorFetch,
  sessionTokenStore,
  type AskToken,
} from '../src/lib/operatorToken.ts'

const TOKEN = 'op-secret'

/** A server behind the gate: `TOKEN` passes, anything else is 401 operator_required. */
function gatedServer() {
  const seen: (string | null)[] = []
  const fetch = async (_input: string, init?: RequestInit) => {
    const sent = new Headers(init?.headers).get(OPERATOR_HEADER)
    seen.push(sent)
    if (sent === TOKEN) return new Response(JSON.stringify({ live: true, replay: true }), { status: 200 })
    return new Response(
      JSON.stringify({ error: { code: 'operator_required', message: 'The operator token was not accepted.' } }),
      { status: 401 },
    )
  }
  return { fetch, seen }
}

/** A `sessionStorage` stand-in. */
function memoryStorage(): Storage {
  const items = new Map<string, string>()
  return {
    get length() {
      return items.size
    },
    clear: () => items.clear(),
    getItem: (key) => items.get(key) ?? null,
    key: (i) => [...items.keys()][i] ?? null,
    removeItem: (key) => void items.delete(key),
    setItem: (key, value) => void items.set(key, value),
  }
}

function asker(answers: (string | null)[]) {
  const asked: boolean[] = []
  const ask: AskToken = async (wrong) => {
    asked.push(wrong)
    return answers.shift() ?? null
  }
  return { ask, asked }
}

test('a refused call asks once, keeps the token in sessionStorage and retries with it', async () => {
  const server = gatedServer()
  const storage = memoryStorage()
  const { ask, asked } = asker([TOKEN])
  const call = createOperatorFetch({ fetch: server.fetch, store: sessionTokenStore(() => storage), ask })

  const first = await call('/api/dev/soft-signals')
  assert.equal(first.status, 200)
  assert.deepEqual(asked, [false])
  assert.equal(storage.getItem(OPERATOR_TOKEN_KEY), TOKEN)
  assert.deepEqual(server.seen, [null, TOKEN])

  // Every later call sends the kept token and asks nothing.
  assert.equal((await call('/api/dev/runs/current')).status, 200)
  assert.deepEqual(asked, [false])
  assert.deepEqual(server.seen, [null, TOKEN, TOKEN])
})

test('calls refused together share one question', async () => {
  const server = gatedServer()
  let answer: (token: string) => void = () => {}
  let questions = 0
  const ask: AskToken = () => {
    questions += 1
    return new Promise((resolve) => (answer = resolve))
  }
  const call = createOperatorFetch({ fetch: server.fetch, store: sessionTokenStore(() => memoryStorage()), ask })
  const pending = Promise.all([call('/api/dev/runs/current'), call('/api/dev/soft-signals'), call('/api/dev/scenarios')])
  await new Promise((resolve) => setTimeout(resolve, 0))
  answer(TOKEN)
  const replies = await pending
  assert.deepEqual(
    replies.map((r) => r.status),
    [200, 200, 200],
  )
  assert.equal(questions, 1)
})

test('a wrong token asks again, saying so, and is not kept', async () => {
  const server = gatedServer()
  const storage = memoryStorage()
  const { ask, asked } = asker(['typo', TOKEN])
  const call = createOperatorFetch({ fetch: server.fetch, store: sessionTokenStore(() => storage), ask })

  assert.equal((await call('/api/dev/soft-signals')).status, 200)
  assert.deepEqual(asked, [false, true])
  assert.deepEqual(server.seen, [null, 'typo', TOKEN])
  assert.equal(storage.getItem(OPERATOR_TOKEN_KEY), TOKEN)
})

test('a kept token the server no longer accepts is dropped and asked for again', async () => {
  const server = gatedServer()
  const storage = memoryStorage()
  storage.setItem(OPERATOR_TOKEN_KEY, 'rotated-away')
  const { ask, asked } = asker([TOKEN])
  const call = createOperatorFetch({ fetch: server.fetch, store: sessionTokenStore(() => storage), ask })

  assert.equal((await call('/api/dev/soft-signals')).status, 200)
  assert.deepEqual(asked, [true])
  assert.equal(storage.getItem(OPERATOR_TOKEN_KEY), TOKEN)
})

test('dismissing the question returns the 401 and asks no more', async () => {
  const server = gatedServer()
  const { ask, asked } = asker([null])
  const call = createOperatorFetch({ fetch: server.fetch, store: sessionTokenStore(() => memoryStorage()), ask })

  const refused = await call('/api/dev/soft-signals')
  assert.equal(refused.status, 401)
  assert.equal(((await refused.json()) as { error: { code: string } }).error.code, 'operator_required')
  assert.equal((await call('/api/dev/soft-signals')).status, 401)
  assert.deepEqual(asked, [false])
})

test('off production nothing asks: no token is sent and the answer passes through', async () => {
  const seen: (string | null)[] = []
  const open = async (_input: string, init?: RequestInit) => {
    seen.push(new Headers(init?.headers).get(OPERATOR_HEADER))
    return new Response('{}', { status: 200 })
  }
  const { ask, asked } = asker([])
  const call = createOperatorFetch({ fetch: open, store: sessionTokenStore(() => memoryStorage()), ask })
  assert.equal((await call('/api/dev/soft-signals', { headers: { 'Content-Type': 'application/json' } })).status, 200)
  assert.deepEqual(seen, [null])
  assert.deepEqual(asked, [])
})

test('other refusals are not the gate: a 401 with another code, a 404, a 503 are returned untouched', async () => {
  for (const [status, code] of [
    [401, 'device_not_enrolled'],
    [404, 'not_found'],
    [503, 'operator_unconfigured'],
  ] as const) {
    const fetch = async () => new Response(JSON.stringify({ error: { code, message: 'no' } }), { status })
    const { ask, asked } = asker([TOKEN])
    const call = createOperatorFetch({ fetch, store: sessionTokenStore(() => memoryStorage()), ask })
    assert.equal((await call('/api/dev/runs/current')).status, status)
    assert.deepEqual(asked, [])
  }
})

test('a storage that throws (private window) still works, asking again next time', async () => {
  const server = gatedServer()
  const broken = () => {
    throw new Error('SecurityError')
  }
  const { ask, asked } = asker([TOKEN, TOKEN])
  const call = createOperatorFetch({ fetch: server.fetch, store: sessionTokenStore(broken), ask })
  assert.equal((await call('/api/dev/soft-signals')).status, 200)
  assert.equal((await call('/api/dev/soft-signals')).status, 200)
  assert.deepEqual(asked, [false, false])
})
