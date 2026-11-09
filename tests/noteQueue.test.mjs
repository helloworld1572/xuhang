import 'fake-indexeddb/auto'
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { test } from 'node:test'
import { deliverQueuedNote, enqueueNote, listQueuedNotes } from '../src/noteQueue.ts'

function note(workspace, text, queued_at) {
  return { workspace, text, queued_at, device: '测试手机', operation_id: randomUUID() }
}

test('离线追加的两条补充按保存顺序恢复，且不混入其他工作空间', async () => {
  const first = note('offline', '断网时的第一条补充', 1000)
  const second = note('offline', '断网时的第二条补充', 2000)
  await enqueueNote(second)
  await enqueueNote(first)
  await enqueueNote(note('another-workspace', '其他项目的内容', 1500))

  // 每次查询都从 IndexedDB 重新打开连接，模拟离线提交后的新页面读取。
  assert.deepEqual(await listQueuedNotes('offline'), [first, second])
  assert.deepEqual(await listQueuedNotes('empty'), [])
})

test('断网和确认丢失后保留记录，成功重试时沿用同一操作编号', async () => {
  const original = note('retry', '重新连上电脑后同步这条', 1000)
  await enqueueNote(original)

  const [offline] = await listQueuedNotes('retry')
  await assert.rejects(deliverQueuedNote(offline, async () => { throw new Error('离线') }), /离线/)
  assert.deepEqual(await listQueuedNotes('retry'), [original])

  const received = new Map()
  const [noAck] = await listQueuedNotes('retry')
  await assert.rejects(deliverQueuedNote(noAck, async item => {
    received.set(item.operation_id, item.text)
    throw new Error('确认丢失')
  }), /确认丢失/)
  assert.deepEqual(await listQueuedNotes('retry'), [original])

  const [retry] = await listQueuedNotes('retry')
  await deliverQueuedNote(retry, async item => {
    assert.equal(item.operation_id, original.operation_id)
    assert.equal(received.get(item.operation_id), item.text)
  })
  assert.deepEqual(await listQueuedNotes('retry'), [])
})
