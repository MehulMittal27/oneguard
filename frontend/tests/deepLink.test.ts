import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readDeepLink } from '../src/lib/deepLink.ts'

test('?customer= names who to sign in as; ?embed=1 drops the bezel', () => {
  assert.deepEqual(readDeepLink('?customer=CU0019&embed=1'), { customerId: 'CU0019', embed: true })
  assert.deepEqual(readDeepLink('?customer=CU0019'), { customerId: 'CU0019', embed: false })
  assert.deepEqual(readDeepLink('?embed=0&customer='), { customerId: null, embed: false })
  assert.deepEqual(readDeepLink(''), { customerId: null, embed: false })
})
