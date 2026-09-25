import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { canonical, moneyString } from '../src/lib/canonical.ts'
import { verifyRequestFrom } from '../src/lib/verifyLink.ts'

// The backend signs canonical JSON (backend/oneguard/passport/canonical.py) and
// checks a device's signature over the same bytes, so the two must agree to
// the byte. These are the backend's own expectations (tests/test_passport.py).

test('keys are sorted, whitespace dropped, undefined members left out', () => {
  assert.equal(
    canonical({ b: [1, 2.0, { z: null, a: true }], a: 'é', skip: undefined }),
    '{"a":"é","b":[1,2,{"a":true,"z":null}]}',
  )
})

test('integer-like keys sort as strings, not first', () => {
  assert.equal(canonical({ b: 1, 10: 2, 2: 3 }), '{"10":2,"2":3,"b":1}')
})

test('money keys are two-decimal strings, half-even; other numbers stay numbers', () => {
  assert.equal(
    canonical({ unit_price: 18, billing_amount_chf: 44.5, value: 399.9, quantity: 2.0 }),
    '{"billing_amount_chf":"44.50","quantity":2,"unit_price":"18.00","value":399.9}',
  )
  assert.equal(moneyString(0.125), '0.12')
  assert.equal(moneyString(0.135), '0.14')
  assert.throws(() => canonical({ value: Number.NaN }))
})

test('a device-signed request body reads the same on both sides', () => {
  const signed = {
    method: 'POST',
    path: '/api/cards/CA0039/policy/tighten',
    body: { add_checks: [{ id: 'C2', text: 'Total at or below CHF 300 across any 7 days' }] },
    ts: 1790000000,
    nonce: 'abc',
  }
  assert.equal(
    canonical(signed),
    '{"body":{"add_checks":[{"id":"C2","text":"Total at or below CHF 300 across any 7 days"}]},' +
      '"method":"POST","nonce":"abc","path":"/api/cards/CA0039/policy/tighten","ts":1790000000}',
  )
})

test('the mock passport re-serialises to the bytes it was signed over', () => {
  const fixture = JSON.parse(readFileSync(new URL('../src/mocks/fixtures/passport.json', import.meta.url), 'utf8'))
  for (const document of [fixture.passport.document, fixture.receipts.AU0037.document]) {
    assert.equal(canonical(JSON.parse(canonical(document))), canonical(document))
    assert.ok(canonical(document).startsWith('{"'))
  }
  // The same bytes Python's json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False) gives.
  assert.ok(canonical(fixture.receipts.AU0037.document).includes('"billing_amount_chf":"520.00"'))
})

test('a verify link names a passport version or a receipt', () => {
  assert.deepEqual(verifyRequestFrom('?passport=pp_1&v=2'), { passport_id: 'pp_1', version: 2 })
  assert.deepEqual(verifyRequestFrom('?passport=pp_1&v=x'), { passport_id: 'pp_1' })
  assert.deepEqual(verifyRequestFrom('?receipt=rc_1'), { receipt_id: 'rc_1' })
  assert.equal(verifyRequestFrom(''), null)
})
