import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { Device } from '../src/api/types.ts'
import { deviceOffer } from '../src/lib/passportDevices.ts'

function device(device_id: string, status: Device['status']): Device {
  return {
    device_id,
    card_id: 'CA1',
    label: device_id,
    status,
    enrolled_at: status === 'enrolled' ? '2026-09-25T10:00:00Z' : null,
    enrolled_by_device_id: null,
    removed_at: status === 'removed' ? '2026-09-25T11:00:00Z' : null,
    last_seen_at: '2026-09-25T10:00:00Z',
  }
}

test('a card no device controls offers to make this device its controller', () => {
  assert.equal(deviceOffer([], null), 'controller')
  // Only removed devices, or only one waiting for an approver that no longer exists: still no controller.
  assert.equal(deviceOffer([device('old', 'removed')], null), 'controller')
  assert.equal(deviceOffer([device('other', 'pending')], null), 'controller')
  // This browser's own device was removed: it starts again as a new one.
  const removed = device('mine', 'removed')
  assert.equal(deviceOffer([removed], removed), 'controller')
})

test('a card another device controls offers the pending path, then waits', () => {
  const phone = device('phone', 'enrolled')
  assert.equal(deviceOffer([phone], null), 'add')
  const mine = device('mine', 'pending')
  assert.equal(deviceOffer([phone, mine], mine), 'waiting')
})

test('the controller is offered nothing', () => {
  const mine = device('mine', 'enrolled')
  assert.equal(deviceOffer([mine, device('other', 'pending')], mine), null)
})
