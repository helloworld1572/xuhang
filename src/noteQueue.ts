export type QueuedNote = {
  operation_id: string
  workspace: string
  device: string
  text: string
  queued_at: number
}

const storeName = 'pending-notes'

function openQueue(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('xuhang-offline', 1)
    request.onupgradeneeded = () => {
      const store = request.result.createObjectStore(storeName, { keyPath: 'operation_id' })
      store.createIndex('workspace', 'workspace')
    }
    request.onerror = () => reject(request.error)
    request.onblocked = () => reject(new Error('本地补充记录正在被其他页面占用'))
    request.onsuccess = () => resolve(request.result)
  })
}

async function transact<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await openQueue()
  try {
    return await new Promise((resolve, reject) => {
      const transaction = db.transaction(storeName, mode)
      const request = run(transaction.objectStore(storeName))
      transaction.oncomplete = () => resolve(request.result)
      transaction.onabort = () => reject(transaction.error || request.error)
    })
  } finally {
    db.close()
  }
}

export async function listQueuedNotes(workspace: string): Promise<QueuedNote[]> {
  const notes = await transact('readonly', store => store.index('workspace').getAll(workspace)) as QueuedNote[]
  return notes.sort((a, b) => a.queued_at - b.queued_at)
}

export function enqueueNote(note: QueuedNote) {
  return transact('readwrite', store => store.add(note))
}

export async function deliverQueuedNote(note: QueuedNote, send: (note: QueuedNote) => Promise<unknown>) {
  await send(note)
  await transact('readwrite', store => store.delete(note.operation_id))
}
