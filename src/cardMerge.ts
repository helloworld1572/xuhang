export const cardFieldLabels = {
  project: '项目名称',
  goal: '当前目标',
  done: '已完成',
  open_questions: '未解决问题',
  next_steps: '下一步',
  evidence: '参考依据',
}

export type CardField = keyof typeof cardFieldLabels
export type CardFields = Record<CardField, string>
export const cardFields = Object.keys(cardFieldLabels) as CardField[]

export function planCardMerge(base: CardFields, draft: CardFields, latest: CardFields) {
  const merged = { ...draft }
  const adopted: CardField[] = []
  const overlaps: CardField[] = []

  for (const field of cardFields) {
    const before = base[field].trim()
    const mine = draft[field].trim()
    const theirs = latest[field].trim()
    if (theirs === before || theirs === mine) continue

    if (mine === before) {
      merged[field] = latest[field]
      adopted.push(field)
    } else {
      overlaps.push(field)
    }
  }
  return { merged, adopted, overlaps }
}
