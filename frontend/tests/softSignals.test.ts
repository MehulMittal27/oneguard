import assert from 'node:assert/strict'
import { test } from 'node:test'
import { nextSoftSignals, softSignalsLabel } from '../src/lib/softSignals.ts'

test('the label is what the server says, never an assumed "on"', () => {
  assert.equal(softSignalsLabel({ live: true, replay: true }), 'on')
  assert.equal(softSignalsLabel({ live: false, replay: false }), 'off')
  assert.equal(softSignalsLabel({ live: true, replay: false }), 'live only')
  assert.equal(softSignalsLabel({ live: false, replay: true }), 'replay only')
  assert.equal(softSignalsLabel(null), '…')
})

test('pressing turns the models on everywhere unless they already are', () => {
  assert.equal(nextSoftSignals({ live: true, replay: true }), false)
  assert.equal(nextSoftSignals({ live: true, replay: false }), true)
  assert.equal(nextSoftSignals({ live: false, replay: false }), true)
  assert.equal(nextSoftSignals(null), true)
})
