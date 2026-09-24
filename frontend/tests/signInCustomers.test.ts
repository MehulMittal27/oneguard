import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { Customer } from '../src/api/types.ts'
import { splitSignInCustomers } from '../src/lib/signInCustomers.ts'

function customer(id: string, scenarioIds: string[], live = true): Customer {
  return {
    customer_id: id,
    name: `Customer ${id}`,
    home_region: 'Zurich region',
    card_id: live ? `CARD-${id}` : null,
    scenario_ids: scenarioIds,
    live,
  }
}

// Mirrors the served C12 list: four live customers from the old pack, a fifth
// live customer from the served pack listed last, and sandbox customers.
const customers = [
  customer('A', ['SCEN0000', 'SCEN0001']),
  customer('B', ['SCEN0002']),
  customer('C', ['SCEN0003']),
  customer('D', ['SCEN0004']),
  customer('S1', [], false),
  customer('S2', [], false),
  customer('E', ['SCEN0101']),
]

const ids = (list: Customer[]) => list.map((c) => c.customer_id)

test('the live customer behind the latest scenario is listed first', () => {
  const { visible } = splitSignInCustomers(customers, null, 4)
  assert.deepEqual(ids(visible), ['E', 'D', 'C', 'B'])
})

test('every customer is reachable on the screen or in the sheet', () => {
  const { visible, others } = splitSignInCustomers(customers, null, 4)
  assert.deepEqual(ids(others), ['A', 'S1', 'S2'])
  assert.deepEqual([...ids(visible), ...ids(others)].sort(), ids(customers).sort())
})

test('a fifth live customer past the cap is in the sheet and can be selected', () => {
  const { visible, others } = splitSignInCustomers(customers, 'A', 4)
  assert.ok(ids(others).includes('A'))
  assert.equal(visible[0].customer_id, 'A')
  assert.equal(visible.length, 4)
})

test('a sandbox customer picked from the sheet is lifted onto the screen', () => {
  const { visible } = splitSignInCustomers(customers, 'S2', 4)
  assert.deepEqual(ids(visible), ['S2', 'E', 'D', 'C'])
})

test('ties keep API order and live customers without scenarios go last', () => {
  const list = [
    customer('X', []),
    customer('Y', ['SCEN0101']),
    customer('Z', ['SCEN0101']),
    customer('W', ['SCEN0099', 'SCEN0100']),
  ]
  assert.deepEqual(ids(splitSignInCustomers(list, null, 4).visible), ['Y', 'Z', 'W', 'X'])
})
