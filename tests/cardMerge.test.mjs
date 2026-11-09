import assert from 'node:assert/strict'
import { test } from 'node:test'
import { planCardMerge } from '../src/cardMerge.ts'

const base = {
  project: '课程项目',
  goal: '完善登录流程',
  done: '完成登录页',
  open_questions: '确认提示文案',
  next_steps: '补测试用例',
  evidence: '旧测试记录',
}

test('旧草稿合并时保留另一端的已完成事项和被清空的依据', () => {
  const draft = { ...base, next_steps: '补错密重试用例' }
  const latest = { ...base, done: '完成登录页和校验', evidence: '' }
  const result = planCardMerge(base, draft, latest)
  assert.deepEqual(result.merged, { ...latest, next_steps: draft.next_steps })
  assert.deepEqual(result.adopted, ['done', 'evidence'])
  assert.deepEqual(result.overlaps, [])
})

test('同一字段的不同改动保留草稿并要求选择，相同内容可合并', () => {
  const draft = { ...base, next_steps: '验证账号锁定', goal: '检查登录  ' }
  const latest = { ...base, next_steps: '完善提示文案', goal: '检查登录' }
  const result = planCardMerge(base, draft, latest)
  assert.deepEqual(result.overlaps, ['next_steps'])
  assert.deepEqual(result.adopted, [])
  assert.equal(result.merged.next_steps, draft.next_steps)
})
