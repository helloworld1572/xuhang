<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { cardFieldLabels, cardFields, planCardMerge, type CardField, type CardFields } from './cardMerge'
import { deliverQueuedNote, enqueueNote, listQueuedNotes, type QueuedNote } from './noteQueue'

type Card = CardFields & { version: number; device: string; updated_at: string }
type Note = { id: number; text: string; device: string; created_at: string; reviewed_at: string | null }
type WorkspaceEvent = { id: number; kind: string; device: string; detail: string; created_at: string }
type ProjectFile = { id: number; name: string; size: number; device: string; created_at: string }
type TeamMember = { id: number; name: string }
type TeamHandoff = { id: number; sender_id: number; recipient_id: number; card_version: number; published_at: string; accepted_at: string | null }
type Team = { member: TeamMember | null; members: TeamMember[]; handoff: TeamHandoff | null; published_card: Card | null }
type State = {
  workspace_id: string; card: Card | null; notes: Note[]; events: WorkspaceEvent[]; server_time: string
  files?: ProjectFile[]; max_file_bytes?: number
  team?: Team
}
type View = 'work' | 'history' | 'connect'
type DraftField = Exclude<CardField, 'project'>
type DraftResult = { mode: 'demo' | 'model'; draft: Record<DraftField, string> }

function stored(key: string, fallback = '') { try { return sessionStorage.getItem(key) || fallback } catch { return fallback } }
function store(key: string, value: string) { try { sessionStorage.setItem(key, value) } catch { /* Session storage may be disabled. */ } }
const code = ref(stored('xuhang-code'))
const loginCode = ref('')
const device = ref(stored('xuhang-device', /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent) ? '我的手机' : '我的电脑'))
const authenticated = ref(false)
const state = ref<State>({ workspace_id: '', card: null, notes: [], events: [], server_time: '' })
const view = ref<View>('work')
const busy = ref(false)
const syncing = ref(false)
const connected = ref(false)
const error = ref('')
const toast = ref('')
const lastSync = ref('')
const editorOpen = ref(false)
const conflict = ref(false)
const showCode = ref(false)
const addresses = ref<string[]>([])
const noteText = ref('')
const queuedNotes = ref<QueuedNote[]>([])
const savingNote = ref(false)
const sendingNotes = ref(false)
const noteSyncError = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const selectedFile = ref<{ file: File; operationId: string } | null>(null)
const uploadingFile = ref(false)
const downloadingFileId = ref<number | null>(null)
const fileError = ref('')
const teamKey = ref('')
const teamName = ref('')
const recipientId = ref<number | ''>('')
const teamError = ref('')
const team = computed(() => state.value.team)
const handoff = computed(() => team.value?.handoff)
const teammates = computed(() => team.value?.members.filter(member => member.id !== team.value?.member?.id) ?? [])
const canAccept = computed(() => team.value?.member && handoff.value && !handoff.value.accepted_at && handoff.value.recipient_id === team.value.member.id)
const projectFiles = computed(() => state.value.files ?? [])
const maxFileBytes = computed(() => state.value.max_file_bytes ?? 20 * 1024 * 1024)
let noteQueueRevision = 0
const blankCard = { project: '我的课程项目', goal: '', done: '', open_questions: '', next_steps: '', evidence: '' }
const editor = reactive({ ...blankCard, version: 0 })
const editorBase = ref<CardFields>({ ...blankCard })
const discussion = ref('')
const draftResult = ref<DraftResult | null>(null)
const selectedDraftFields = ref<DraftField[]>([])
const generatingDraft = ref(false)
const draftError = ref('')
const suggestedFields = computed(() => cardFields.filter(
  (field): field is DraftField => field !== 'project' && Boolean(draftResult.value?.draft[field]),
))
const draftExample = '当前目标：完善课程项目的登录功能\n已完成：登录页已做完\n未解决问题：错误提示还没确定\n下一步：写一个输错密码的测试'
const pending = computed(() => state.value.notes.filter(n => !n.reviewed_at))
const card = computed(() => state.value.card)
const conflictCard = computed(() => conflict.value && card.value && card.value.version > editor.version ? card.value : null)
const mergePlan = computed(() => conflictCard.value ? planCardMerge(editorBase.value, editor, conflictCard.value) : null)
const mergeChoices = ref<Partial<Record<CardField, 'mine' | 'latest'>>>({})
const canMerge = computed(() => mergePlan.value?.overlaps.every(field => mergeChoices.value[field]) ?? false)
watch(() => conflictCard.value?.version, () => { mergeChoices.value = {} }, { flush: 'sync' })
const latestSave = computed(() => state.value.events.find(e => e.kind === 'resumed'))
const steps = computed(() => [!!card.value, state.value.notes.length > 0, !!latestSave.value])
const isMobile = /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent)
let timer: ReturnType<typeof setInterval>
let toastTimer: ReturnType<typeof setTimeout>
let requestCounter = 0
const operationIds = new Map<string, string>()

function uuid() {
  // crypto.randomUUID is unavailable on plain HTTP LAN origins. getRandomValues works there.
  const b = crypto.getRandomValues(new Uint8Array(16)); b[6] = (b[6]! & 15) | 64; b[8] = (b[8]! & 63) | 128
  const h = Array.from(b, x => x.toString(16).padStart(2, '0')).join('')
  return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`
}
class ApiError extends Error { constructor(message: string, public status = 0) { super(message) } }
type ApiOptions = { workspace?: string; timeoutMs?: number; download?: boolean; teamKey?: string }
async function api(path: string, method = 'GET', body?: unknown, options: ApiOptions = {}) {
  const controller = new AbortController(); const timeout = setTimeout(() => controller.abort(), options.timeoutMs ?? 10000)
  const accessCode = code.value
  const headers: Record<string, string> = { 'X-Access-Code': accessCode }
  const binary = body instanceof Blob
  if (body !== undefined) headers['Content-Type'] = binary ? 'application/octet-stream' : 'application/json'
  if (options.workspace) headers['X-Workspace-Id'] = options.workspace
  if (options.teamKey) headers['X-Team-Key'] = options.teamKey
  try {
    const response = await fetch(`/api${path}`, { method, headers, body: binary ? body : JSON.stringify(body), signal: controller.signal })
    const data = response.ok && options.download ? await response.blob() : await response.json()
    if (!response.ok) throw new ApiError(typeof data.detail === 'string' ? data.detail : '请检查必填项与内容长度后重试。', response.status)
    if (accessCode === code.value) connected.value = navigator.onLine !== false
    return data
  } catch (e) {
    if (e instanceof ApiError) {
      if (e.status === 401 && accessCode === code.value) {
        authenticated.value = false; connected.value = false; error.value = e.message
      }
      if (e.status >= 500 && path !== '/ai/draft' && accessCode === code.value) connected.value = false
      throw e
    }
    if (accessCode === code.value) connected.value = false
    throw new ApiError('暂时连接不到电脑服务。请检查 Wi-Fi 和服务是否运行；输入已保留，可以重试。')
  } finally { clearTimeout(timeout) }
}
async function mutate(path: string, method: string, data: Record<string, unknown>, options: ApiOptions = {}) {
  const key = `${options.workspace ?? ''}:${path}:${JSON.stringify(data)}`
  if (!operationIds.has(key)) operationIds.set(key, uuid())
  const result = await api(path, method, { ...data, operation_id: operationIds.get(key) }, options)
  operationIds.delete(key)
  return result
}
function message(text: string) { toast.value = text; clearTimeout(toastTimer); toastTimer = setTimeout(() => toast.value = '', 5500) }
function date(text: string, full = false) {
  if (!text) return '—'
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', ...(full ? { second: '2-digit' as const } : {}) }).format(new Date(text))
}
async function refresh(manual = false) {
  if (syncing.value || !authenticated.value) return
  syncing.value = true; const seq = ++requestCounter
  try {
    const result = await api('/state', 'GET', undefined, { teamKey: teamKey.value }) as State
    if (seq !== requestCounter) return
    if (state.value.workspace_id !== result.workspace_id) {
      disconnect()
      error.value = '此地址上的工作空间已更换。请重新连接。'
      return
    }
    state.value = result; lastSync.value = result.server_time
    if (manual) { error.value = ''; message('已获取最新进度') }
  } catch (e) { if (manual) error.value = (e as Error).message }
  finally { syncing.value = false }
}
async function login() {
  if (!device.value.trim() || !(loginCode.value || code.value).trim()) { error.value = '请填写设备名称和项目访问码。'; return }
  busy.value = true; error.value = ''; code.value = (loginCode.value || code.value).trim().toUpperCase()
  try {
    const result = await api('/state') as State
    if (!result.workspace_id) throw new Error('请先重启电脑上的续航服务，再打开新版页面。')
    ++requestCounter
    state.value = result; authenticated.value = true; lastSync.value = result.server_time
    teamName.value = device.value.trim()
    teamKey.value = ''
    try { teamKey.value = localStorage.getItem(`xuhang-team:${result.workspace_id}`) ?? '' } catch { /* Team registration checks storage before submitting. */ }
    if (teamKey.value) await refresh()
    queuedNotes.value = []; noteSyncError.value = ''
    store('xuhang-code', code.value); store('xuhang-device', device.value.trim())
    void syncQueuedNotes()
    const info = await api('/connection'); addresses.value = info.addresses
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
function disconnect() {
  ++requestCounter; authenticated.value = false; code.value = ''; loginCode.value = ''; showCode.value = false
  store('xuhang-code', ''); noteText.value = ''; error.value = ''; editorOpen.value = false
  queuedNotes.value = []; noteSyncError.value = ''
  selectedFile.value = null; fileError.value = ''
  teamKey.value = ''; teamName.value = ''; recipientId.value = ''; teamError.value = ''
}

function memberName(id: number) {
  return team.value?.members.find(member => member.id === id)?.name ?? '团队成员'
}

async function runTeamAction(action: (options: ApiOptions) => Promise<unknown>, success: string) {
  if (busy.value || syncing.value || !connected.value) return
  const workspace = state.value.workspace_id
  busy.value = true; teamError.value = ''
  try {
    await action({ workspace, teamKey: teamKey.value })
    if (!authenticated.value || workspace !== state.value.workspace_id) return
    await refresh()
    message(success)
  } catch (e) {
    if (!authenticated.value || workspace !== state.value.workspace_id) return
    teamError.value = (e as Error).message
    if (e instanceof ApiError && e.status === 409) await refresh()
  } finally { busy.value = false }
}

async function joinTeam() {
  if (busy.value || syncing.value || !connected.value || !teamName.value.trim()) return
  try {
    const key = `xuhang-team:${state.value.workspace_id}`
    const token = localStorage.getItem(key) || uuid()
    localStorage.setItem(key, token)
    teamKey.value = token
  } catch {
    teamError.value = '浏览器无法保存团队身份，请允许此站点保存数据后重试。'
    return
  }
  await runTeamAction(options => api('/team/members', 'POST', { name: teamName.value.trim() }, options), '已加入团队交接')
}

async function publishHandoff() {
  if (!card.value || !recipientId.value) return
  const data = { recipient_id: recipientId.value, card_version: card.value.version, previous_handoff_id: handoff.value?.id ?? null }
  await runTeamAction(options => mutate('/team/handoffs', 'POST', data, options), '交接已发布，等待队友确认')
}

async function acceptHandoff() {
  if (!canAccept.value || !handoff.value) return
  const path = `/team/handoffs/${handoff.value.id}/accept`
  await runTeamAction(options => mutate(path, 'POST', {}, options), '已确认接手')
}

function selectProjectFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  selectedFile.value = null
  fileError.value = ''
  if (!file) return
  if (file.size > maxFileBytes.value) {
    input.value = ''
    fileError.value = `文件太大，单个文件最多 ${fileSize(maxFileBytes.value)}。`
    return
  }
  selectedFile.value = { file, operationId: uuid() }
}

function fileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${Number((bytes / (1024 * 1024)).toFixed(1))} MB`
}

async function uploadProjectFile() {
  const selection = selectedFile.value
  if (!selection || uploadingFile.value || !authenticated.value) return
  const workspace = state.value.workspace_id
  const params = new URLSearchParams({ name: selection.file.name, device: device.value.trim(), operation_id: selection.operationId })
  uploadingFile.value = true
  fileError.value = ''
  try {
    await api(`/files?${params}`, 'POST', selection.file, { workspace, timeoutMs: 60000 })
    if (!authenticated.value || state.value.workspace_id !== workspace) return
    selectedFile.value = null
    if (fileInput.value) fileInput.value.value = ''
    message('文件已发送，另一端可以下载。')
    await refresh()
  } catch (e) {
    if (authenticated.value && state.value.workspace_id === workspace) fileError.value = (e as Error).message
  } finally {
    uploadingFile.value = false
  }
}

async function downloadProjectFile(file: ProjectFile) {
  if (downloadingFileId.value !== null || !authenticated.value) return
  const workspace = state.value.workspace_id
  downloadingFileId.value = file.id
  fileError.value = ''
  try {
    const content = await api(`/files/${file.id}`, 'GET', undefined, { download: true, timeoutMs: 60000 }) as Blob
    if (!authenticated.value || state.value.workspace_id !== workspace) return
    const url = URL.createObjectURL(content)
    const link = document.createElement('a')
    link.href = url
    link.download = file.name
    document.body.append(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 60000)
  } catch (e) {
    if (authenticated.value && state.value.workspace_id === workspace) fileError.value = (e as Error).message
  } finally {
    downloadingFileId.value = null
  }
}
function editCard() {
  const current = card.value
  editorBase.value = current ? { ...current } : { ...blankCard }
  for (const field of cardFields) editor[field] = editorBase.value[field]
  editor.version = current?.version ?? 0
  discussion.value = ''
  clearDraftPreview()
  error.value = ''; conflict.value = false; editorOpen.value = true
}

function clearDraftPreview() {
  draftResult.value = null
  selectedDraftFields.value = []
  draftError.value = ''
}

async function requestDraft() {
  const text = discussion.value.trim()
  if (!text || generatingDraft.value || busy.value || conflict.value) return

  const workspace = state.value.workspace_id
  generatingDraft.value = true
  clearDraftPreview()
  try {
    const result = await api('/ai/draft', 'POST', { text }, { timeoutMs: 35000 }) as DraftResult
    if (!authenticated.value || !editorOpen.value || state.value.workspace_id !== workspace) return
    draftResult.value = result
    selectedDraftFields.value = suggestedFields.value.filter(field => !editor[field].trim())
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      draftError.value = '电脑上的续航服务版本较旧。重启服务后再试。'
    } else {
      draftError.value = (e as Error).message
    }
  } finally {
    generatingDraft.value = false
  }
}

function applyDraft() {
  const result = draftResult.value
  if (!result || busy.value || conflict.value || !selectedDraftFields.value.length) return
  for (const field of selectedDraftFields.value) editor[field] = result.draft[field]
  clearDraftPreview()
  message('已填入编辑框。请核对内容，再点保存现场。')
}

function applyCardMerge() {
  const latest = conflictCard.value
  const plan = mergePlan.value
  if (!latest || !plan || !canMerge.value) return

  for (const field of cardFields) {
    editor[field] = mergeChoices.value[field] === 'latest' ? latest[field] : plan.merged[field]
  }
  editorBase.value = { ...latest }
  editor.version = latest.version
  conflict.value = false
  error.value = ''
  message('已合并到草稿。确认内容后保存现场。')
}

async function saveCard() {
  if (busy.value || conflict.value || generatingDraft.value) return
  busy.value = true; error.value = ''
  try {
    await mutate('/card', 'PUT', { ...editor, device: device.value })
    editorOpen.value = false; message('工作现场已保存，手机现在可以查看'); await refresh()
  } catch (e) {
    error.value = (e as Error).message
    if (e instanceof ApiError && e.status === 409) { conflict.value = true; await refresh() }
  } finally { busy.value = false }
}
async function submitNote() {
  const text = noteText.value.trim()
  const workspace = state.value.workspace_id
  if (!text || !card.value || !authenticated.value || savingNote.value) return
  const note: QueuedNote = { operation_id: uuid(), workspace, device: device.value.trim(), text, queued_at: Date.now() }
  savingNote.value = true; error.value = ''
  try {
    await enqueueNote(note)
    noteQueueRevision++
    if (!authenticated.value || state.value.workspace_id !== workspace) return
    if (!queuedNotes.value.some(item => item.operation_id === note.operation_id)) queuedNotes.value.push(note)
    noteText.value = ''
    message('已保存到这台设备，连接电脑后自动同步')
    void syncQueuedNotes()
  } catch {
    error.value = '无法写入浏览器本地存储。输入仍在，请检查浏览器存储权限或空间，再试一次。'
  } finally { savingNote.value = false }
}

async function syncQueuedNotes() {
  if (!authenticated.value || sendingNotes.value) return
  const workspace = state.value.workspace_id
  const accessCode = code.value
  const stillActive = () => authenticated.value && state.value.workspace_id === workspace && code.value === accessCode
  let sent = 0
  sendingNotes.value = true
  noteSyncError.value = ''
  try {
    const revision = noteQueueRevision
    const notes = await listQueuedNotes(workspace)
    if (!stillActive()) return
    if (revision === noteQueueRevision) queuedNotes.value = notes
    if (navigator.onLine === false) return
    for (const note of notes) {
      if (!stillActive()) break
      try {
        await deliverQueuedNote(note, ({ operation_id, device, text }) => api('/notes', 'POST', { operation_id, device, text }, { workspace }))
        noteQueueRevision++
        sent++
        if (stillActive()) queuedNotes.value = queuedNotes.value.filter(item => item.operation_id !== note.operation_id)
      } catch (e) {
        if (!stillActive()) break
        if (!(e instanceof ApiError)) throw e
        if (e.status > 0) noteSyncError.value = e.message
        if (e.status !== 422) break
      }
    }
  } catch {
    if (stillActive()) noteSyncError.value = '暂时无法读写本地补充记录。请检查浏览器存储后重试。'
  } finally {
    sendingNotes.value = false
    if (sent && stillActive()) {
      message(`已同步 ${sent} 条补充，电脑可以查看`)
      void refresh()
    }
  }
}
async function resume() {
  busy.value = true; error.value = ''
  try {
    await mutate('/resume', 'POST', { note_ids: pending.value.map(n => n.id), device: device.value })
    message('已记录本次恢复。补充内容仍保留在接续记录里。'); await refresh()
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
async function copy(text: string) {
  try { await navigator.clipboard.writeText(text); message('地址已复制') }
  catch { message('浏览器不支持自动复制，请长按或选中地址复制。') }
}
function focusNote() { document.getElementById('note-input')?.focus(); document.getElementById('note-input')?.scrollIntoView({ behavior: 'smooth', block: 'center' }) }
function visibleRefresh() {
  if (!authenticated.value || document.hidden || busy.value) return
  void syncQueuedNotes()
  if (navigator.onLine !== false) void refresh()
}
function markOffline() { connected.value = false }
onMounted(async () => {
  if (code.value) await login()
  timer = setInterval(visibleRefresh, 5000)
  document.addEventListener('visibilitychange', visibleRefresh)
  window.addEventListener('online', visibleRefresh)
  window.addEventListener('offline', markOffline)
})
onUnmounted(() => {
  clearInterval(timer); clearTimeout(toastTimer)
  document.removeEventListener('visibilitychange', visibleRefresh)
  window.removeEventListener('online', visibleRefresh)
  window.removeEventListener('offline', markOffline)
})
</script>

<template>
  <div v-if="!authenticated" class="login-layout">
    <section class="login-story">
      <a class="brand" href="/"><span class="brand-mark">续</span><span>续航<small>XUHANG</small></span></a>
      <div class="login-copy"><span class="eyebrow">让每一次继续，都有起点</span><h1>换个设备，<br>思路依然在线。</h1><p>把未完成的工作留在这里。<br>在路上补充灵感，回来后接着向前。</p><div class="journey"><span>电脑保存</span><b>→</b><span>手机补充</span><b>→</b><span>继续工作</span></div></div>
      <div class="login-footer">续航 · 局域网体验版 <span>01 / 连接你的工作</span></div>
    </section>
    <main class="login-form-wrap"><form class="login-form" @submit.prevent="login">
      <span class="pill">局域网 · 同一个项目</span><h2>连接工作空间</h2><p class="muted">电脑和手机输入同一个访问码，就能接上进度。</p>
      <label for="device">这台设备的名称</label><input id="device" v-model="device" maxlength="80" required autocomplete="off" placeholder="例如：我的电脑 / 我的手机">
      <label for="access-code">项目访问码</label><input id="access-code" v-model="loginCode" maxlength="64" required autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="查看电脑启动窗口中的访问码">
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <button class="button primary wide" :disabled="busy">{{ busy ? '正在连接…' : '进入工作空间' }} <span>→</span></button>
      <p class="login-hint">首次使用？在电脑运行「启动续航.cmd」，窗口中会显示手机地址和访问码。</p>
      <div class="privacy-note"><i class="dot"></i> 数据保存在运行服务的电脑上</div>
    </form></main>
  </div>

  <div v-else class="app-shell">
    <aside class="sidebar">
      <a class="brand" href="/" aria-label="续航主页"><span class="brand-mark">续</span><span>续航<small>XUHANG</small></span></a>
      <div class="workspace-label">我的工作空间</div>
      <nav aria-label="主要导航">
        <button :class="{active:view==='work'}" @click="view='work'"><span class="nav-icon">▦</span>工作接续<span v-if="pending.length" class="nav-count">{{ pending.length }}</span></button>
        <button :class="{active:view==='history'}" @click="view='history'"><span class="nav-icon">◷</span>接续记录</button>
        <button :class="{active:view==='connect'}" @click="view='connect'"><span class="nav-icon">⇄</span>连接设备</button>
      </nav>
      <div class="sidebar-note"><div class="little-orbit">↗</div><strong>灵感不必等到回去</strong><p>在手机上记下一句，<br>让下次继续更轻松。</p><button @click="view='connect'">连接我的手机 <span>→</span></button></div>
      <div class="device-badge"><span class="device-avatar">{{ isMobile ? '手' : '电' }}</span><div><strong>{{ device }}</strong><small>局域网体验版 v0.1</small></div><button title="断开工作空间" aria-label="断开工作空间" @click="disconnect">↪</button></div>
    </aside>

    <div class="main-wrap">
      <header class="topbar"><div class="breadcrumb">我的工作空间 <span>/</span> <strong>{{ view==='work' ? '工作接续' : view==='history' ? '接续记录' : '连接设备' }}</strong></div><div class="connection-state" :class="{offline:!connected}"><i class="dot"></i>{{ connected ? '已连接电脑服务' : '连接中断 · 等待重连' }}</div></header>
      <main class="main-content">
        <div v-if="error && !editorOpen" class="error page-error" role="alert">{{ error }}<button @click="error=''" aria-label="关闭提示">×</button></div>
        <div v-if="!connected" class="offline-banner" role="status">当前显示上次获取的内容。补充可以先保存在这台设备，重新连到电脑后会自动同步。</div>

        <template v-if="view==='work'">
          <section class="page-heading"><div><span class="eyebrow">WORK IN CONTINUITY</span><h1>接上思路，继续向前<span class="accent">。</span></h1><p>保存现在的进度，让下一次开始少一点回想。</p></div><button class="button secondary refresh" :disabled="syncing || busy" @click="refresh(true)"><span>↻</span>{{ syncing ? '同步中…' : '刷新进度' }}</button></section>
          <section class="flow-strip" aria-label="接续流程"><div v-for="(label,i) in ['电脑保存现场','手机补充想法','回到电脑继续']" :key="label" :class="{complete:steps[i]}"><span class="step-number">{{ steps[i] ? '✓' : `0${i+1}` }}</span><span>{{ label }}</span><small>{{ steps[i] ? '已完成' : ['从接续卡开始','随时记录灵感','查看变化再出发'][i] }}</small><b v-if="i<2">→</b></div></section>
          <div class="workspace-grid">
            <section class="panel handoff-panel">
              <div class="panel-heading"><div><span class="section-icon">▤</span><h2>工作接续卡</h2></div><span class="tag">{{ card ? `版本 ${card.version}` : '等待首次保存' }}</span></div>
              <template v-if="card">
                <div class="card-intro"><span class="project-tag">{{ card.project }}</span><h3>{{ card.goal }}</h3><p class="meta">{{ card.device }} 保存于 {{ date(card.updated_at) }}</p></div>
                <div class="card-sections"><section><h4><i class="small-marker green"></i>已完成</h4><p :class="{placeholder:!card.done}">{{ card.done || '还没有记录完成事项' }}</p></section><section><h4><i class="small-marker amber"></i>未解决问题</h4><p :class="{placeholder:!card.open_questions}">{{ card.open_questions || '暂未记录问题' }}</p></section><section class="next-section"><h4><i class="small-marker dark"></i>下一步</h4><p :class="{placeholder:!card.next_steps}">{{ card.next_steps || '编辑接续卡，写下下一步行动' }}</p></section><section v-if="card.evidence" class="evidence-section"><h4>参考依据</h4><p>{{ card.evidence }}</p></section></div>
                <footer class="card-footer"><span>已保存到电脑数据库</span><button class="button secondary" @click="editCard">编辑并保存现场 <span>↗</span></button></footer>
              </template>
              <div v-else class="empty-card"><div class="empty-illustration"><span>✓</span><i></i><i></i><i></i></div><h3>给下次继续，留一个起点</h3><p>记录做到哪里、遇到什么问题，<br>以及接下来想做的第一件事。</p><button class="button primary" @click="editCard">＋ 保存第一张接续卡</button><small>本版由你手动填写，所有内容都可以修改。</small></div>
            </section>

            <div class="right-column">
              <section class="panel note-panel">
                <div class="panel-heading"><div><span class="section-icon">＋</span><h2>随手补充</h2></div><span class="tag subtle">{{ device }}</span></div>
                <p class="panel-description">新想法、待确认的问题，先记下来。</p>
                <form @submit.prevent="submitNote">
                  <label class="sr-only" for="note-input">补充想法</label>
                  <textarea id="note-input" v-model="noteText" maxlength="6000" :disabled="!card" :readonly="savingNote" placeholder="例如：登录失败时，记得增加清晰的错误提示。" rows="4" required></textarea>
                  <div class="note-form-footer">
                    <small>{{ card ? '先保存在本机，连到电脑后同步' : '请先保存一张接续卡' }}</small>
                    <button class="button primary" :disabled="savingNote || !card || !noteText.trim()">{{ savingNote ? '保存中…' : connected ? '提交补充' : '离线保存' }} <span>↑</span></button>
                  </div>
                </form>
                <div v-if="queuedNotes.length || noteSyncError" class="note-queue">
                  <div class="note-queue-heading" role="status">
                    <strong v-if="queuedNotes.length">{{ sendingNotes && connected ? '正在同步' : '已存本机 · 待同步' }} {{ queuedNotes.length }} 条</strong>
                    <button v-if="queuedNotes.length" class="button secondary" :disabled="sendingNotes" @click="syncQueuedNotes">立即同步</button>
                  </div>
                  <p v-if="noteSyncError" class="error" role="alert">{{ noteSyncError }}</p>
                  <ul v-if="queuedNotes.length" aria-label="本机待同步补充">
                    <li v-for="note in queuedNotes" :key="note.operation_id"><time>{{ date(new Date(note.queued_at).toISOString()) }}</time><p>{{ note.text }}</p></li>
                  </ul>
                </div>
              </section>
              <section class="panel updates-panel"><div class="panel-heading"><div><span class="section-icon amber-text">◉</span><h2>待查看的新变化</h2></div><span class="count-badge">{{ pending.length }}</span></div>
                <div v-if="pending.length" class="pending-list"><article v-for="note in pending" :key="note.id" class="note-item"><div class="note-meta"><strong>{{ note.device }}</strong><time>{{ date(note.created_at) }}</time></div><p>{{ note.text }}</p><span class="idea-label">补充想法 · 待你确认</span></article></div>
                <div v-else class="no-updates"><span>◎</span><strong>{{ card ? '进度已对齐' : '等待你的第一段进度' }}</strong><p>{{ card ? '手机提交的新想法会出现在这里。' : '保存接续卡后，就能跨设备补充。' }}</p></div>
                <div class="resume-footer"><button class="button primary wide" :disabled="busy || !card" @click="resume">{{ pending.length ? '我已查看，继续工作' : '从这里继续工作' }} <span>→</span></button><small>查看补充不会自动改写接续卡或标记任务完成。</small></div>
              </section>
            </div>
          </div>
          <section v-if="team" class="panel file-panel" aria-labelledby="team-handoff-title">
            <div class="panel-heading">
              <div><span class="section-icon" aria-hidden="true">⇄</span><h2 id="team-handoff-title">团队交接</h2></div>
              <span class="tag">{{ handoff ? (handoff.accepted_at ? '已接手' : '待接手') : '尚未发布' }}</span>
            </div>
            <div class="file-panel-body">
              <div v-if="handoff" class="team-handoff-summary">
                <strong>{{ memberName(handoff.sender_id) }} → {{ memberName(handoff.recipient_id) }}</strong>
                <p class="file-help">接续卡 v{{ handoff.card_version }} · {{ date(handoff.published_at) }} 发布<span v-if="handoff.accepted_at"> · {{ date(handoff.accepted_at) }} 确认接手</span></p>
                <details v-if="team.published_card">
                  <summary>查看发布时的接续卡 v{{ handoff.card_version }}</summary>
                  <dl class="handoff-snapshot">
                    <template v-for="field in cardFields" :key="field">
                      <dt>{{ cardFieldLabels[field] }}</dt><dd>{{ team.published_card[field] || '—' }}</dd>
                    </template>
                  </dl>
                </details>
                <button v-if="canAccept" type="button" class="button primary" :disabled="busy || syncing || !connected" @click="acceptHandoff">确认接手</button>
                <p v-else-if="!handoff.accepted_at" class="file-help">等待 {{ memberName(handoff.recipient_id) }} 确认接手。</p>
              </div>

              <form v-if="!team.member" class="team-form" @submit.prevent="joinTeam">
                <label for="team-name">团队名称</label>
                <div class="team-form-controls">
                  <input id="team-name" v-model="teamName" maxlength="80" required autocomplete="off" placeholder="例如：小林">
                  <button class="button secondary" :disabled="busy || syncing || !connected">加入团队交接</button>
                </div>
                <p class="file-help">请填写队友能认出的名称。这个团队身份保存在当前浏览器和网址下。</p>
              </form>
              <template v-else>
                <p class="file-help">当前团队身份：{{ team.member.name }}</p>
                <template v-if="!handoff || handoff.accepted_at">
                  <p v-if="!card" class="file-help">保存第一张接续卡后即可发布交接。</p>
                  <p v-else-if="!teammates.length" class="file-help">请让队友在另一台设备登录并加入团队交接，队友就会出现在这里。</p>
                  <form v-else class="team-form" @submit.prevent="publishHandoff">
                    <label for="team-recipient">指定接手人</label>
                    <div class="team-form-controls">
                      <select id="team-recipient" v-model="recipientId" required>
                        <option disabled value="">请选择队友</option>
                        <option v-for="member in teammates" :key="member.id" :value="member.id">{{ member.name }}</option>
                      </select>
                      <button class="button primary" :disabled="busy || syncing || !connected || !recipientId">发布交接</button>
                    </div>
                    <p class="file-help">发布当前已保存的接续卡 v{{ card.version }}。</p>
                  </form>
                </template>
              </template>
              <p v-if="teamError" class="error" role="alert">{{ teamError }}</p>
            </div>
          </section>
          <section class="panel file-panel" aria-labelledby="project-files-title">
            <div class="panel-heading">
              <div><span class="section-icon" aria-hidden="true">▧</span><h2 id="project-files-title">项目文件</h2></div>
              <span class="tag">{{ projectFiles.length }} 个文件</span>
            </div>
            <div class="file-panel-body">
              <p class="file-help">文件保存在电脑上。使用同一项目访问码的设备可以下载，单个文件最多 {{ fileSize(maxFileBytes) }}。</p>
              <form class="file-upload" @submit.prevent="uploadProjectFile">
                <label for="project-file" class="sr-only">选择项目文件</label>
                <input id="project-file" ref="fileInput" type="file" :disabled="uploadingFile" @change="selectProjectFile">
                <button class="button primary" :disabled="!selectedFile || uploadingFile">{{ uploadingFile ? '正在发送…' : '发送文件' }}</button>
              </form>
              <p v-if="selectedFile" class="file-help selected-file">待发送　{{ selectedFile.file.name }} · {{ fileSize(selectedFile.file.size) }}</p>
              <p v-if="fileError" class="error" role="alert">{{ fileError }}</p>
              <p v-else-if="uploadingFile" class="file-help" role="status">正在上传，请保持页面打开。</p>
              <p v-if="!projectFiles.length" class="file-help">还没有文件。可以先发送一份项目资料。</p>
              <ul v-else class="file-list" aria-label="已发送的文件">
                <li v-for="file in projectFiles" :key="file.id">
                  <div>
                    <strong>{{ file.name }}</strong>
                    <p>{{ fileSize(file.size) }} · {{ file.device }} · {{ date(file.created_at) }}</p>
                  </div>
                  <button type="button" class="button secondary" :disabled="downloadingFileId !== null" :aria-label="`下载 ${file.name}`" @click="downloadProjectFile(file)">{{ downloadingFileId === file.id ? '正在下载…' : '下载' }}</button>
                </li>
              </ul>
            </div>
          </section>
          <section class="bottom-line"><span><i class="dot"></i>每 5 秒自动获取更新 · 上次同步 {{ date(lastSync, true) }}</span><button @click="view='history'">查看全部接续记录 →</button></section>
          <button v-if="card" class="mobile-note-shortcut" @click="focusNote">＋ 记个想法</button>
        </template>

        <template v-if="view==='history'">
          <section class="page-heading"><div><span class="eyebrow">YOUR CONTINUITY LOG</span><h1>每一步，都有迹可循<span class="accent">。</span></h1><p>保存、补充与恢复，都留在同一条时间线上。</p></div><button class="button secondary" :disabled="syncing" @click="refresh(true)">↻ 刷新记录</button></section>
          <div class="history-grid"><section class="panel"><div class="panel-heading"><h2>工作动态</h2><span class="tag">最近 60 条</span></div><div v-if="!state.events.length" class="empty-text">保存第一张接续卡后，这里会记录你的每次接续。</div><ol class="timeline"><li v-for="event in state.events" :key="event.id"><span class="timeline-dot" :class="event.kind"></span><div><strong>{{ event.detail }}</strong><p>{{ event.device }} · {{ date(event.created_at,true) }}</p></div></li></ol></section><section class="panel"><div class="panel-heading"><h2>所有补充</h2><span class="tag">{{ state.notes.length }} 条</span></div><div v-if="!state.notes.length" class="empty-text">还没有补充想法。</div><article v-for="note in state.notes" :key="note.id" class="note-item history-note"><div class="note-meta"><strong>{{ note.device }}</strong><time>{{ date(note.created_at) }}</time></div><p>{{ note.text }}</p><span class="idea-label" :class="{reviewed:note.reviewed_at}">{{ note.reviewed_at ? '已查看 · 保留记录' : '待查看' }}</span></article></section></div>
        </template>

        <template v-if="view==='connect'">
          <section class="page-heading"><div><span class="eyebrow">ONE PROJECT, MORE POSSIBILITIES</span><h1>把进度，带到另一端<span class="accent">。</span></h1><p>手机接入能访问电脑的网络，再输入同一个项目访问码。</p></div></section>
          <div class="connection-grid"><section class="panel connection-guide"><span class="large-number">01</span><h2>在手机浏览器打开地址</h2><p>选择电脑 WLAN / Wi-Fi 网卡对应的地址。多个地址时，以启动窗口和 ipconfig 为准。</p><div v-for="address in addresses" :key="address" class="address-box"><code>{{ address }}</code><button class="button secondary" @click="copy(address)">复制</button></div><p v-if="!addresses.length" class="error">未识别到局域网地址，请在电脑运行 ipconfig 查看 Wi-Fi 的 IPv4 地址，使用 http://该地址:8765。</p><span class="large-number second">02</span><h2>输入同一个项目访问码</h2><p>给手机起个名字，例如“我的手机”，然后输入以下访问码。</p><div class="access-box"><code>{{ showCode ? code : '•••• •••• ••••' }}</code><button class="button secondary" @click="showCode=!showCode">{{ showCode ? '隐藏' : '显示访问码' }}</button></div></section><section class="panel connection-tips"><span class="section-icon">⇄</span><h2>让接续保持在线</h2><ul><li>保持电脑开机，续航服务持续运行。</li><li>手机先关闭移动数据，确认连接同一 Wi-Fi。</li><li>若出现 Windows 防火墙提示，在你信任的网络上允许访问。</li><li>校园网、访客 Wi-Fi 可能禁止设备互访，可换成可信热点测试。</li></ul><div class="scope-note"><strong>断网时也能补充</strong><p>提前打开接续卡。断网后，补充会先保存在当前浏览器；回到同一个网页并重新连上电脑后，记录会自动同步。</p></div></section></div>
        </template>
        <footer class="app-footer"><span>续航 XUHANG</span><span>让工作接续，让思路延续。</span><span>LOCAL FIRST / V0.1</span></footer>
      </main>
    </div>

    <div v-if="editorOpen" class="modal-overlay" @keydown.esc="!busy && !generatingDraft && (editorOpen=false)">
      <section class="editor-modal" role="dialog" aria-modal="true" aria-labelledby="editor-title">
        <header>
          <div><span class="eyebrow">SAVE YOUR CONTEXT</span><h2 id="editor-title">{{ card ? '保存工作现场' : '创建第一张接续卡' }}</h2></div>
          <button class="close-button" :disabled="busy || generatingDraft" @click="editorOpen=false" aria-label="关闭编辑">×</button>
        </header>
        <form @submit.prevent="saveCard">
          <div class="editor-fields">
            <details v-if="!conflict" class="draft-panel">
              <summary>从讨论记录生成草稿</summary>
              <p>先预览建议，勾选想填入的字段，再确认保存。</p>
              <label for="draft-discussion">讨论记录</label>
              <textarea id="draft-discussion" v-model="discussion" rows="5" maxlength="6000" :disabled="busy || generatingDraft" placeholder="粘贴讨论记录，或使用下方的演示材料。" @input="clearDraftPreview"></textarea>
              <div class="draft-actions">
                <button v-if="!discussion" type="button" class="button secondary" :disabled="busy || generatingDraft" @click="discussion=draftExample">使用演示材料</button>
                <button type="button" class="button secondary" :disabled="busy || generatingDraft || !discussion.trim()" @click="requestDraft">{{ generatingDraft ? '正在生成…' : '生成草稿' }}</button>
              </div>
              <p v-if="generatingDraft" role="status">正在生成预览，请稍等。</p>
              <p v-if="draftError" class="error" role="alert">{{ draftError }}</p>
              <section v-if="draftResult" class="draft-preview" aria-label="生成结果">
                <strong>{{ draftResult.mode === 'demo' ? '演示草稿 · 本地规则整理' : 'AI 草稿 · 待确认' }}</strong>
                <p v-if="draftResult.mode === 'demo'">演示会提取带字段标签的内容，并附上原文依据。</p>
                <p v-if="!suggestedFields.length">这次没有可填入的建议。你可以补充讨论记录后重试。</p>
                <p v-else>空白字段已勾选。有内容的字段由你决定是否替换。</p>
                <div v-for="field in suggestedFields" :key="field" class="draft-suggestion">
                  <label><input v-model="selectedDraftFields" type="checkbox" :value="field" :disabled="busy"> {{ cardFieldLabels[field] }}</label>
                  <p v-if="editor[field]" class="draft-current">当前内容　{{ editor[field] }}</p>
                  <p>{{ draftResult.draft[field] }}</p>
                </div>
                <button type="button" class="button primary" :disabled="busy || !selectedDraftFields.length" @click="applyDraft">填入所选字段</button>
              </section>
            </details>
            <section v-if="conflict" class="conflict-box" aria-label="处理版本冲突">
              <template v-if="conflictCard && mergePlan">
                <strong>另一端已保存 v{{ conflictCard.version }}</strong>
                <p>各自修改的字段会合并到草稿。同一字段有不同改动时，请选择要保留的内容。</p>
                <details v-if="mergePlan.adopted.length" class="conflict-adopted">
                  <summary>查看将保留的另一端改动（{{ mergePlan.adopted.length }} 项）</summary>
                  <div v-for="field in mergePlan.adopted" :key="field">
                    <strong>{{ cardFieldLabels[field] }}</strong>
                    <p>{{ conflictCard[field] || '（已清空）' }}</p>
                  </div>
                </details>
                <fieldset v-for="field in mergePlan.overlaps" :key="field" class="conflict-field">
                  <legend>{{ cardFieldLabels[field] }}</legend>
                  <label><input v-model="mergeChoices[field]" :name="`merge-${field}`" type="radio" value="mine"> 保留我的草稿</label>
                  <p>{{ editor[field] || '（空白）' }}</p>
                  <label><input v-model="mergeChoices[field]" :name="`merge-${field}`" type="radio" value="latest"> 采用另一端版本</label>
                  <p>{{ conflictCard[field] || '（空白）' }}</p>
                </fieldset>
                <button type="button" class="button secondary" :disabled="!canMerge" @click="applyCardMerge">合并到草稿</button>
              </template>
              <template v-else>
                <strong>另一端已有更新</strong>
                <p>草稿已保留，先获取最新版本再合并。</p>
                <button type="button" class="button secondary" :disabled="syncing" @click="refresh()">获取最新版本</button>
              </template>
            </section>
            <fieldset class="editor-card-inputs" :disabled="busy || conflict" aria-label="接续卡草稿">
              <label>项目名称 <span>*</span><input v-model="editor.project" required maxlength="80" autofocus placeholder="例如：软件课程设计"></label>
              <label>当前目标 <span>*</span><textarea v-model="editor.goal" rows="2" required maxlength="6000" placeholder="这次正在完成什么任务？"></textarea></label>
              <div class="field-pair">
                <label>已完成<textarea v-model="editor.done" rows="4" maxlength="6000" placeholder="已经完成并确认的事项，每行一条"></textarea></label>
                <label>未解决问题<textarea v-model="editor.open_questions" rows="4" maxlength="6000" placeholder="遇到了什么问题？还有什么没做？"></textarea></label>
              </div>
              <label>下一步<textarea v-model="editor.next_steps" rows="3" maxlength="6000" placeholder="回来后，先做哪一件事？"></textarea></label>
              <label>参考依据 <small>选填</small><textarea v-model="editor.evidence" rows="2" maxlength="6000" placeholder="填写讨论结论、文档链接或测试结果说明。"></textarea></label>
            </fieldset>
            <p v-if="error" class="error" role="alert">{{ error }}</p>
          </div>
          <footer>
            <span>手动确认 · 保存为新版本</span>
            <div><button type="button" class="button secondary" :disabled="busy || generatingDraft" @click="editorOpen=false">取消</button><button class="button primary" :disabled="busy || conflict || generatingDraft">{{ busy ? '保存中…' : '保存现场' }} <span>↗</span></button></div>
          </footer>
        </form>
      </section>
    </div>
  </div>
  <div v-if="toast" class="toast" role="status"><span>✓</span>{{ toast }}</div>
</template>

<style scoped>
.file-panel {
  margin-top: 20px;
}

.file-panel-body {
  padding: 0 20px 20px;
}

.team-handoff-summary {
  border: 1px solid #dce6df;
  border-radius: 12px;
  padding: 12px;
  overflow-wrap: anywhere;
}

.team-handoff-summary details { margin: 8px 0; }
.team-handoff-summary summary { cursor: pointer; }
.handoff-snapshot dt { font-weight: 600; }
.handoff-snapshot dd { margin: 4px 0 10px; white-space: pre-wrap; }
.team-form label { display: block; margin: 8px 0; font-weight: 600; }
.team-form-controls { display: flex; flex-wrap: wrap; gap: 12px; }
.team-form-controls input, .team-form-controls select {
  flex: 1 1 180px;
  min-width: 0;
  padding: 10px;
  border: 1px solid #ccd9d0;
  border-radius: 8px;
  background: white;
  font: inherit;
}

.file-help {
  color: #5d6a63;
  font-size: 13px;
  margin: 8px 0;
}

.selected-file {
  overflow-wrap: anywhere;
}

.file-upload, .file-list li {
  display: flex;
  align-items: center;
  gap: 12px;
}

.file-upload {
  flex-wrap: wrap;
  margin: 12px 0;
}

.file-upload input {
  flex: 1 1 180px;
  min-width: 0;
}

.file-list {
  max-height: 20rem;
  overflow: auto;
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
}

.file-list li {
  justify-content: space-between;
  border-top: 1px solid #e8ede6;
  padding: 10px 0;
  overflow-wrap: anywhere;
}

.file-list li > div {
  min-width: 0;
}

.file-list p {
  margin: 3px 0 0;
  color: #5d6a63;
  font-size: 12px;
}

.draft-panel {
  margin-bottom: 20px;
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 12px;
  background: #f7f9f5;
}

.draft-panel > summary {
  cursor: pointer;
  font-weight: 600;
}

.draft-panel > label {
  margin-bottom: 6px;
}

.draft-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}

.draft-preview {
  margin-top: 12px;
  overflow-wrap: anywhere;
}

.draft-suggestion {
  border-top: 1px solid var(--line);
  padding: 8px 0;
}

.draft-suggestion label {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
}

.draft-suggestion input {
  width: auto;
  margin: 0;
}

.draft-suggestion p {
  margin: 4px 0;
  white-space: pre-wrap;
}

.draft-current {
  opacity: .8;
}

.editor-card-inputs {
  border: 0;
  margin: 0;
  padding: 0;
  min-width: 0;
}

.conflict-box {
  margin-bottom: 20px;
}

.conflict-field, .conflict-adopted {
  margin: 0 0 12px;
  border: 1px solid #e3d6af;
  border-radius: 8px;
  min-width: 0;
  padding: 8px 12px;
}

.conflict-field legend {
  font-weight: 600;
}

.conflict-field label {
  display: flex;
  gap: 6px;
  align-items: center;
  margin: 0;
}

.conflict-field input {
  width: auto;
  margin: 0;
}

.conflict-field p {
  margin: 4px 0 10px 20px;
  max-height: 10rem;
  overflow: auto;
}
</style>
