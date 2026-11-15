export function randomKey() {
  return Array.from(crypto.getRandomValues(new Uint8Array(32)), n => n.toString(16).padStart(2, '0')).join('')
}
export function operationId() {
  const a = crypto.getRandomValues(new Uint8Array(16)); a[6] = (a[6]! & 15) | 64; a[8] = (a[8]! & 63) | 128
  const s = Array.from(a, n => n.toString(16).padStart(2, '0')).join('')
  return `${s.slice(0,8)}-${s.slice(8,12)}-${s.slice(12,16)}-${s.slice(16,20)}-${s.slice(20)}`
}
export class BoardError extends Error {
  status: number
  constructor(message: string, status = 0) { super(message); this.status = status }
}
export async function request(key: string, path: string, method = 'GET', body?: unknown, download = false) {
  const abort = new AbortController(); const timer = setTimeout(() => abort.abort(), body instanceof Blob ? 60000 : 15000)
  try {
    const res = await fetch(`/api/collab${path}`, {
      method, signal: abort.signal,
      headers: { 'X-Member-Key': key, ...(body === undefined ? {} : { 'Content-Type': body instanceof Blob ? 'application/octet-stream' : 'application/json' }) },
      body: body instanceof Blob ? body : body === undefined ? undefined : JSON.stringify(body),
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new BoardError(typeof data.detail === 'string' ? data.detail : '请检查必填项、日期和内容长度。', res.status)
    }
    return download ? await res.blob() : await res.json()
  } catch (e) {
    if (e instanceof BoardError) throw e
    throw new BoardError('连接中断或请求超时，尚未确认保存成功。输入已保留，请恢复连接后重试。')
  } finally { clearTimeout(timer) }
}

// Scope and generation guard also invalidates pending reads when a write starts.
export class LatestRequest {
  private generation = 0
  next() { return ++this.generation }
  current(generation: number) { return generation === this.generation }
}
