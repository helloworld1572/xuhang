import test from 'node:test'
import assert from 'node:assert/strict'
import { LatestRequest, operationId, randomKey } from '../src/boardApi.ts'

test('switching scope or starting a write invalidates previous responses', () => {
  const gate = new LatestRequest()
  const oldProject = gate.next()
  const newProject = gate.next()
  assert.equal(gate.current(oldProject), false)
  assert.equal(gate.current(newProject), true)
  gate.next()
  assert.equal(gate.current(newProject), false)
})

test('LAN HTTP credentials and operation IDs do not require randomUUID', () => {
  assert.match(randomKey(), /^[a-f0-9]{64}$/)
  assert.match(operationId(), /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/)
})
