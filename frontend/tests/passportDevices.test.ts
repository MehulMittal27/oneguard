import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { Device } from '../src/api/types.ts'
import { controllerLine, controllerOf, deviceOffer, isController, waitingLine } from '../src/lib/passportDevices.ts'

function device(device_id: string, status: Device['status'], role?: Device['role']): Device {
  return {
    ...(role !== undefined ? { role } : {}),
    device_id,
    card_id: 'CA1',
    label: device_id,
    status,
    enrolled_at: status === 'enrolled' ? `2026-09-25T10:0${device_id.length % 10}:00Z` : null,
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

test('the controller is the device the backend names, and only it manages devices', () => {
  const laptop = device('laptop', 'enrolled', 'approved')
  const phone = device('phone', 'enrolled', 'controller')
  const tablet = device('tablet', 'pending', null)
  const devices = [laptop, phone, tablet]
  assert.equal(controllerOf(devices)?.device_id, 'phone')
  assert.equal(isController(devices, phone), true)
  assert.equal(isController(devices, laptop), false) // approved: changes the policy, manages no device
  assert.equal(isController(devices, tablet), false)
  assert.equal(isController(devices, null), false)
  assert.equal(controllerLine(devices, phone), 'Controller · this device')
  assert.equal(controllerLine(devices, laptop), 'Controller · phone')
  assert.equal(waitingLine(devices), 'Waiting for approval from phone')
})

test('a backend before controllers: the earliest enrolled device is the controller', () => {
  const first = device('a', 'enrolled') // enrolled 10:01
  const later = device('abc', 'enrolled') // enrolled 10:03
  assert.equal(controllerOf([later, first])?.device_id, 'a')
  assert.equal(isController([later, first], first), true)
})

test('no enrolled device: no controller, no line, and the waiting text stays generic', () => {
  const devices = [device('old', 'removed', null), device('new', 'pending', null)]
  assert.equal(controllerOf(devices), null)
  assert.equal(controllerLine(devices, null), null)
  assert.equal(waitingLine(devices), 'Waiting for approval from the card’s controller')
})
