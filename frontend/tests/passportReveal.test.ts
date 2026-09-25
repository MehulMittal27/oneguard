import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import type { Mandate } from '../src/api/types.ts'
import { REVEAL_MS, REVEAL_WINDOW_MS, isFirstPassport, markReveal, takeReveal } from '../src/lib/passportReveal.ts'

function mandate(passport?: { version: number; passport_id?: string }): Mandate {
  return {
    mandate_id: 'md_1',
    card_id: 'CA0039',
    instruction: 'x',
    checks: [],
    uncertainty_policy: 'ask',
    open_questions: [],
    status: 'active',
    confirmed_at: '2026-09-25T00:00:00Z',
    ...(passport
      ? { passport: { passport_id: passport.passport_id ?? 'pp_1', version: passport.version, issued_at: '2026-09-25T00:00:00Z', devices_count: 1 } }
      : {}),
  }
}

test('the first passport of a card that had none plays', () => {
  assert.equal(isFirstPassport(undefined, mandate({ version: 1 })), true)
  assert.equal(isFirstPassport(null, mandate({ version: 1 })), true)
  // an earlier policy from before passports existed carries none
  assert.equal(isFirstPassport(mandate(), mandate({ version: 1 })), true)
})

test('a card that already had a passport, a later version, or no passport never plays', () => {
  // a new policy starts its own passport at version 1, but the card had one before
  assert.equal(isFirstPassport(mandate({ version: 3, passport_id: 'pp_old' }), mandate({ version: 1 })), false)
  assert.equal(isFirstPassport(undefined, mandate({ version: 2 })), false)
  assert.equal(isFirstPassport(undefined, mandate()), false)
})

test('each surface plays once, for that passport only, within the window', () => {
  const t0 = 1_000_000
  markReveal('CA0039', 'pp_1', t0)
  assert.equal(takeReveal('CA0039', 'pp_other', 'card', t0), false)
  assert.equal(takeReveal('CA0023', 'pp_1', 'card', t0), false)
  assert.equal(takeReveal('CA0039', 'pp_1', 'confirmation', t0 + 10), true)
  assert.equal(takeReveal('CA0039', 'pp_1', 'confirmation', t0 + 20), false)
  assert.equal(takeReveal('CA0039', 'pp_1', 'card', t0 + 5_000), true)
  assert.equal(takeReveal('CA0039', 'pp_1', 'card', t0 + 6_000), false)

  markReveal('CA0023', 'pp_2', t0)
  assert.equal(takeReveal('CA0023', 'pp_2', 'card', t0 + REVEAL_WINDOW_MS + 1), false)
  assert.equal(takeReveal('CA0023', 'pp_2', 'confirmation', t0 + 1), false) // gone once expired
})

test('the animation is over by 800 ms, and none of it plays under reduced motion', () => {
  assert.ok(REVEAL_MS <= 800)
  const css = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8')
  const runs = [...css.matchAll(/animation: (og-[\w-]+) (\d+)ms[^;]*?(?: (\d+)ms)? both/g)]
  assert.equal(runs.length, 5)
  for (const [, name, duration, delay] of runs) {
    assert.ok(Number(duration) + Number(delay ?? 0) <= REVEAL_MS, name)
  }
  assert.match(css, /prefers-reduced-motion: reduce\)[^@]*\.og-reveal\.is-playing \*[^}]*animation: none !important/)
})
