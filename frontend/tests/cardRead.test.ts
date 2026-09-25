import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { settle, viewFor, type CardRead } from '../src/lib/cardRead.ts'

// What Passport shows, reduced to what tells two cards apart.
type Shown = { passport: string | null; controller: string | null }

const oliver: CardRead<Shown> = { cardId: 'CA0039', status: 'ready', value: { passport: 'pp_oliver', controller: 'this device' } }

test('a card change clears at once: the new card starts at loading, never the old card’s passport', () => {
  assert.deepEqual(viewFor(oliver, 'CA0039'), { status: 'ready', value: oliver.value })
  assert.deepEqual(viewFor(oliver, 'CA1738'), { status: 'loading' })
  assert.deepEqual(viewFor(null, 'CA1738'), { status: 'loading' })
})

test('a late answer for the card the screen has left is dropped', () => {
  // Oliver's read was in flight when the screen moved to Livia's card.
  let held: CardRead<Shown> | null = null
  held = settle(held, oliver, 'CA1738')
  assert.equal(held, null)
  assert.deepEqual(viewFor(held, 'CA1738'), { status: 'loading' })

  // Livia's answer lands, then Oliver's late one: hers stays.
  const livia: CardRead<Shown> = { cardId: 'CA1738', status: 'ready', value: { passport: null, controller: null } }
  held = settle(held, livia, 'CA1738')
  held = settle(held, oliver, 'CA1738')
  assert.deepEqual(viewFor(held, 'CA1738'), { status: 'ready', value: { passport: null, controller: null } })
})

test('a failed first read of the new card is an error, not the previous card’s data', () => {
  const held = settle(oliver, { cardId: 'CA1738', status: 'error' }, 'CA1738')
  assert.deepEqual(viewFor(held, 'CA1738'), { status: 'error' })
})

test('a failed re-read of the same card keeps what it showed (a poll hiccup)', () => {
  const held = settle(oliver, { cardId: 'CA0039', status: 'error' }, 'CA0039')
  assert.deepEqual(viewFor(held, 'CA0039'), { status: 'ready', value: oliver.value })
})

test('a fresh read of the same card replaces what it showed', () => {
  const next: CardRead<Shown> = { cardId: 'CA0039', status: 'ready', value: { passport: 'pp_oliver', controller: null } }
  assert.deepEqual(viewFor(settle(oliver, next, 'CA0039'), 'CA0039'), { status: 'ready', value: next.value })
})

// No component test setup (README §1): these check the wiring the tests above rely on.
const source = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8')

test('Passport and the waiting-devices card hold their reads with the card they were read for', () => {
  for (const path of ['../src/components/PassportSection.tsx', '../src/components/PendingDevicesCard.tsx']) {
    const text = source(path)
    assert.match(text, /viewFor\(held, /, path)
    assert.match(text, /settle\(current, /, path)
  }
})

test('card detail remounts Passport on another card, so its local state starts over', () => {
  assert.match(source('../src/screens/CardDetail/CardDetail.tsx'), /<PassportSection key=\{cardId\}/)
})
