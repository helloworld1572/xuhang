<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { BoardError, LatestRequest, operationId, randomKey, request } from './boardApi'

type Member = { id:number; name:string; role:string }
type Team = { id:number; name:string; description:string; role:string }
type Project = { id:number; team_id:number; name:string; goal:string; owner_id:number; start_date:string|null; due_date:string|null; status:string; version:number; revision:number; can_manage:boolean }
type RecordData = { id:number; kind:string; version:number; created_by:number; created_at:string; updated_at:string; [key:string]:any }
type FileInfo = { id:number; name:string; size:number; created_by:number; created_at:string }
type Activity = { id:number; kind:string; actor_name:string; entity_id:number|null; before:any; after:any; created_at:string }
type Snapshot = { project:Project; records:RecordData[]; files:FileInfo[]; events:Activity[]; progress:{total:number;done:number;percent:number|null;blocked:number;overdue:number}; server_time:string }
type Invite = { id:number; revoked:number; used_by:number|null; expires_at:string }

function storedKey() { try { return localStorage.getItem('xuhang-member-key') || '' } catch { return '' } }
const key = ref(storedKey())
const me = ref<{id:number;name:string}|null>(null)
const teams = ref<Team[]>([]), members = ref<Member[]>([]), projects = ref<Project[]>([]), invites = ref<Invite[]>([])
const teamId = ref<number|null>(null), projectId = ref<number|null>(null), snapshot = ref<Snapshot|null>(null)
const error = ref(''), notice = ref(''), busy = ref(false), online = ref(false), lastSync = ref('')
const loginKey = ref(''), signupName = ref(''), signupCode = ref(''), signupKind = ref('invite'), signupKey = ref(randomKey()), showKey = ref(false)
const newTeam = reactive({name:'',description:''}), joinCode = ref(''), inviteCode = ref(''), generatedInvite = ref('')
const section = ref('overview'), mine = ref(false), filterStatus = ref(''), filterMember = ref(''), filterStage = ref('')
const editorKind = ref(''), editorId = ref<number|null>(null), editorVersion = ref(0), draft = ref<Record<string,any>>({}), conflict = ref(false)
const selectedTask = ref<number|null>(null), comment = ref(''), projectNote = ref(''), recipient = ref(''), cardChoice = ref('')
const importCode = ref(''), importVersion = ref(1), contextResult = ref<any>(null)
const taskHistory = ref<Activity[]>([]), historyBefore = ref<number|null>(null), historyLoaded = ref(false)
const selectedFile = ref<File|null>(null), fileOperation = ref('')
const gate = new LatestRequest()
let polling = false, timer: ReturnType<typeof setInterval>, alive = true
const operationIds = new Map<string,string>()
const team = computed(() => teams.value.find(t => t.id === teamId.value))
const project = computed(() => snapshot.value?.project)
const canManage = computed(() => project.value?.can_manage ?? false)
const records = computed(() => snapshot.value?.records ?? [])
const tasks = computed(() => records.value.filter(r => r.kind === 'task'))
const stages = computed(() => records.value.filter(r => r.kind === 'stage').sort((a,b) => a.position - b.position || a.id-b.id))
const cards = computed(() => records.value.filter(r => r.kind === 'card'))
const handoffs = computed(() => records.value.filter(r => r.kind === 'handoff').reverse())
const task = computed(() => tasks.value.find(t => t.id === selectedTask.value))
const comments = computed(() => records.value.filter(r => r.kind === 'note' && r.task_id === selectedTask.value))
const visibleTasks = computed(() => tasks.value.filter(t => (!mine.value || t.assignee_id === me.value?.id) && (!filterStatus.value || t.status === filterStatus.value) && (!filterMember.value || String(t.assignee_id) === filterMember.value) && (!filterStage.value || String(t.stage_id) === filterStage.value)))
const statuses:Record<string,string> = {todo:'待开始',doing:'进行中',blocked:'受阻',done:'已完成'}
const eventNames:Record<string,string> = {project_created:'创建项目',project_updated:'更新项目计划',stage_created:'创建阶段',stage_updated:'调整阶段',task_created:'创建任务',task_updated:'更新任务',task_completed:'完成任务',task_reopened:'重新打开任务',task_status_changed:'更新任务状态',note_created:'补充进度',card_created:'保存接续卡',card_updated:'更新接续卡',handoff_created:'发布交接',handoff_accepted:'确认接手',file_uploaded:'上传文件'}
const name = (id:number|null) => members.value.find(m => m.id === id)?.name ?? '未分配'
const dateText = (s:string) => new Date(s).toLocaleString('zh-CN', {hour12:false})
function editable(t:RecordData) { return canManage.value || t.assignee_id === me.value?.id }
function scope() { return `${key.value}:${teamId.value}:${projectId.value}` }

async function refresh() {
  if (!key.value || polling || busy.value) return
  const generation = gate.next(), identity = key.value, tid = teamId.value, pid = projectId.value
  polling = true
  try {
    const results = await Promise.all([request(identity,'/me'), tid ? request(identity,`/teams/${tid}`) : null, pid ? request(identity,`/projects/${pid}`) : null])
    if (!alive || !gate.current(generation)) return
    me.value = results[0].user; teams.value = results[0].teams
    if (results[1]) { members.value = results[1].members; projects.value = results[1].projects; invites.value = results[1].invites }
    if (results[2]) snapshot.value = results[2]
    online.value = true; lastSync.value = new Date().toISOString()
  } catch (e) {
    if (!alive || !gate.current(generation)) return
    online.value = false
    if (e instanceof BoardError && e.status === 401) me.value = null
    error.value = (e as Error).message
  } finally { polling = false }
}
async function run(action:()=>Promise<void>) {
  if (busy.value) return
  const initial = scope(); busy.value = true; gate.next(); error.value = ''; notice.value = ''
  try { await action() } catch (e) {
    if (alive && (initial === scope() || !me.value)) { error.value = (e as Error).message; conflict.value = e instanceof BoardError && e.status === 409 }
  } finally { busy.value = false; if (alive) await refresh() }
}
async function write(path:string, data:Record<string,any> = {}, method='POST') {
  const idKey = `${key.value}:${path}:${JSON.stringify(data)}`
  if (!operationIds.has(idKey)) operationIds.set(idKey,operationId())
  const result = await request(key.value,path,method,{...data,operation_id:operationIds.get(idKey)})
  operationIds.delete(idKey)
  return result
}
function saveKey(value:string) { localStorage.setItem('xuhang-member-key',value); key.value = value }
async function login() { await run(async () => { const info = await request(loginKey.value.trim(),'/me'); saveKey(loginKey.value.trim()); me.value=info.user; teams.value=info.teams; loginKey.value='' }) }
async function register() { await run(async () => {
  // Save the pending key before registration so a lost response never loses the identity.
  localStorage.setItem('xuhang-pending-key',signupKey.value)
  const info = await request('', '/register', 'POST', {name:signupName.value,key:signupKey.value,invitation:signupKind.value==='invite'?signupCode.value.trim():'',access_code:signupKind.value==='owner'?signupCode.value.trim():''})
  saveKey(signupKey.value); me.value=info; signupCode.value=''; showKey.value=true; localStorage.removeItem('xuhang-pending-key')
}) }
function logout() { gate.next(); localStorage.removeItem('xuhang-member-key'); key.value=''; me.value=null; teams.value=[]; clearProject(); teamId.value=null; generatedInvite.value=''; inviteCode.value=''; signupKey.value=randomKey() }
function clearProject() { projectId.value=null; snapshot.value=null; projects.value=[]; members.value=[]; invites.value=[]; editorKind.value=''; selectedTask.value=null; comment.value=''; projectNote.value=''; contextResult.value=null; selectedFile.value=null; fileOperation.value=''; recipient.value=''; cardChoice.value=''; importCode.value=''; filterMember.value=''; filterStage.value=''; filterStatus.value='' }
function leaveEditor() { return !editorKind.value || window.confirm('切换后将丢弃尚未保存的编辑，继续吗？') }
async function chooseTeam(tid:number) { if (busy.value || !leaveEditor()) return; gate.next(); clearProject(); teamId.value=tid; generatedInvite.value=''; inviteCode.value=''; error.value=''; notice.value=''; await refresh() }
async function chooseProject(pid:number) { if (busy.value || !leaveEditor()) return; gate.next(); projectId.value=pid; snapshot.value=null; editorKind.value=''; selectedTask.value=null; comment.value=''; projectNote.value=''; contextResult.value=null; selectedFile.value=null; fileOperation.value=''; recipient.value=''; cardChoice.value=''; importCode.value=''; filterStage.value=''; section.value='overview'; error.value=''; notice.value=''; await refresh() }
async function createTeam() { await run(async()=> { const result=await write('/teams',{...newTeam}); clearProject(); teamId.value=result.id; newTeam.name=''; newTeam.description=''; notice.value='团队已创建。可以邀请队友加入。' }) }
async function joinTeam() { await run(async()=> { const result=await write('/join',{code:joinCode.value.trim()}); clearProject(); teamId.value=result.team_id; joinCode.value=''; notice.value='已加入团队' }) }
async function invite() { await run(async()=> { if (!inviteCode.value) inviteCode.value=randomKey(); await write(`/teams/${teamId.value}/invites`,{code:inviteCode.value}); generatedInvite.value=inviteCode.value; inviteCode.value='' }) }
async function revoke(id:number) { await run(async()=> { await write(`/teams/${teamId.value}/invites/${id}/revoke`); generatedInvite.value=''; notice.value='邀请已撤销' }) }
function openEditor(kind:string, item?:RecordData|Project) {
  if (editorKind.value && !window.confirm('放弃当前未保存编辑？')) return
  editorKind.value=kind; editorId.value=item?.id??null; editorVersion.value=item?.version??0; conflict.value=false; error.value=''
  const defaults:Record<string,any> = {
    project:{name:'',goal:'',owner_id:me.value?.id,start_date:null,due_date:null,status:'active'},
    stage:{title:'',goal:'',criteria:'',position:stages.value.length,due_date:null},
    task:{title:'',description:'',criteria:'',stage_id:null,assignee_id:null,due_date:null,status:'todo',result:'',blocked_reason:'',file_ids:[]},
    card:{title:'',goal:'',done:'',open_questions:'',next_steps:'',evidence:''},
  }
  draft.value = Object.fromEntries(Object.keys(defaults[kind]).map(k => [k,item ? (item as any)[k] : defaults[kind][k]]))
}
async function saveEditor() {
  await run(async()=> {
    const kind=editorKind.value, id=editorId.value, data={...draft.value}
    for (const k of ['start_date','due_date']) if (k in data && !data[k]) data[k]=null
    if (kind==='project') {
      const path=id?`/projects/${id}`:`/teams/${teamId.value}/projects`
      const result=await write(path,{...data,version:editorVersion.value},id?'PUT':'POST')
      if (!id) { projectId.value=result.id; snapshot.value=null }
    } else {
      await write(`/projects/${projectId.value}/records/${id??kind}`,{version:editorVersion.value,data},id?'PUT':'POST')
    }
    editorKind.value=''; conflict.value=false; notice.value='已保存到团队项目'
  })
}
function loadLatest() {
  const latest = editorKind.value==='project'?project.value:records.value.find(r=>r.id===editorId.value)
  if (!latest) return
  editorKind.value=''; openEditor('kind' in latest ? latest.kind : 'project', latest)
}
function taskPayload(t:RecordData) { return Object.fromEntries(['title','description','criteria','stage_id','assignee_id','due_date','status','result','blocked_reason','file_ids'].map(k=>[k,t[k]])) }
async function toggleTask(t:RecordData) { await run(async()=> { await write(`/projects/${projectId.value}/records/${t.id}`,{version:t.version,data:{...taskPayload(t),status:t.status==='done'?'doing':'done'}},'PUT'); notice.value=t.status==='done'?'任务已重新打开':'任务已完成' }) }
function checkTask(e:Event,t:RecordData) { (e.target as HTMLInputElement).checked=t.status==='done'; void toggleTask(t) }
function chooseTask(t:RecordData) { selectedTask.value=t.id; comment.value=''; taskHistory.value=[]; historyBefore.value=null; historyLoaded.value=false }
async function loadHistory() { await run(async()=> { const id=selectedTask.value; const result=await request(key.value,`/projects/${projectId.value}/records/${id}/history${historyBefore.value?`?before=${historyBefore.value}`:''}`); if(selectedTask.value!==id)return;taskHistory.value.push(...result.events);historyBefore.value=result.next_before;historyLoaded.value=true }) }
async function addNote(forTask:boolean) { await run(async()=> { await write(`/projects/${projectId.value}/records/note`,{data:{text:forTask?comment.value:projectNote.value,task_id:forTask?selectedTask.value:null}}); if(forTask)comment.value='';else projectNote.value='' }) }
async function publishHandoff() { await run(async()=> { const card=cards.value.find(c=>c.id===Number(cardChoice.value)); if(!card)throw Error('请选择接续卡'); await write(`/projects/${projectId.value}/records/handoff`,{data:{card_id:card.id,card_version:card.version,recipient_id:Number(recipient.value)}}); notice.value='交接已发布' }) }
async function acceptHandoff(id:number) { await run(async()=> { await write(`/projects/${projectId.value}/handoffs/${id}/accept`); notice.value='已确认接手' }) }
async function importCard() { await run(async()=> { await write(`/projects/${projectId.value}/import-card`,{access_code:importCode.value,card_version:importVersion.value}); importCode.value=''; notice.value='已复制指定版本到当前项目，原记录保留' }) }
function selectFile(e:Event) { selectedFile.value=(e.target as HTMLInputElement).files?.[0]??null; fileOperation.value=operationId() }
async function upload() { await run(async()=> { const file=selectedFile.value;if(!file)return;if(file.size>20*1024*1024)throw Error('单个文件最多20 MB');await request(key.value,`/projects/${projectId.value}/files?operation_id=${fileOperation.value}&name=${encodeURIComponent(file.name)}`,'POST',file);selectedFile.value=null;notice.value='文件已上传' }) }
async function download(file:FileInfo) { await run(async()=> {const blob=await request(key.value,`/projects/${projectId.value}/files/${file.id}`,'GET',undefined,true);const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=file.name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}) }
async function loadContext() { await run(async()=> {contextResult.value=await request(key.value,`/projects/${projectId.value}/context`)}) }
function focusRefresh() { if(document.visibilityState==='visible') void refresh() }
onMounted(()=> {try {signupKey.value=localStorage.getItem('xuhang-pending-key')||signupKey.value}catch{};void refresh();timer=setInterval(()=>void refresh(),5000);document.addEventListener('visibilitychange',focusRefresh);window.addEventListener('online',focusRefresh)})
onUnmounted(()=> {alive=false;gate.next();clearInterval(timer);document.removeEventListener('visibilitychange',focusRefresh);window.removeEventListener('online',focusRefresh)})
</script>

<template>
<main class="collab">
  <header class="board-header"><div><small>一起把项目向前推进</small><h1>团队工作台</h1></div><div v-if="me" class="identity"><span>{{ me.name }} · {{ online?'已连接':'连接中断' }}</span><button :disabled="busy" @click="refresh">刷新进度</button><button :disabled="busy" @click="logout">退出</button></div></header>
  <p v-if="error" class="alert error" role="alert">{{ error }}</p><p v-if="notice" class="alert success" role="status">{{ notice }}</p>
  <section v-if="!me" class="auth-grid">
    <form class="box" @submit.prevent="login"><h2>登录已有身份</h2><p>在电脑和手机输入同一份个人登录密钥，即可使用同一个成员身份。</p><label>个人登录密钥<input v-model="loginKey" type="password" required autocomplete="off" maxlength="64"></label><button class="primary" :disabled="busy">登录</button></form>
    <form class="box" @submit.prevent="register"><h2>第一次使用</h2><label>你的名字<input v-model="signupName" required maxlength="120"></label><label>加入方式<select v-model="signupKind"><option value="invite">使用队友给的邀请码</option><option value="owner">使用电脑服务访问码</option></select></label><label>{{ signupKind==='invite'?'团队邀请码':'电脑启动窗口的访问码' }}<input v-model="signupCode" type="password" required autocomplete="off"></label><p>注册后生成个人登录密钥。保存它，便可在其他设备登录；请勿发给队友。队友使用团队邀请码注册。</p><button class="primary" :disabled="busy">创建成员身份</button></form>
  </section>
  <template v-else>
    <details class="identity-key" :open="showKey"><summary>在另一台设备登录</summary><p>打开同一个电脑服务地址，使用下方密钥登录。此密钥代表你的身份，请自行保存。</p><input aria-label="我的个人登录密钥" :value="key" readonly @focus="($event.target as HTMLInputElement).select()"></details>
    <div class="board-layout">
      <aside class="board-sidebar box">
        <h2>我的团队</h2><p v-if="!teams.length">创建团队，或用邀请码加入队友。</p>
        <button v-for="t in teams" :key="t.id" class="nav-item" :class="{active:teamId===t.id}" :disabled="busy" @click="chooseTeam(t.id)">{{ t.name }} <small>{{ t.role==='admin'?'管理员':'成员' }}</small></button>
        <details><summary>创建团队</summary><form @submit.prevent="createTeam"><label>名称<input v-model="newTeam.name" required maxlength="120"></label><label>简介<textarea v-model="newTeam.description" maxlength="6000"></textarea></label><button :disabled="busy">创建</button></form></details>
        <details><summary>加入其他团队</summary><form @submit.prevent="joinTeam"><label>邀请码<input v-model="joinCode" required></label><button :disabled="busy">加入团队</button></form></details>
        <template v-if="team"><hr><h2>{{ team.name }}的项目</h2><p>{{ team.description }}</p><button v-for="p in projects" :key="p.id" class="nav-item" :class="{active:projectId===p.id}" :disabled="busy" @click="chooseProject(p.id)">{{ p.name }}</button><button class="primary" :disabled="busy" @click="openEditor('project')">＋ 创建项目</button><hr><h2>成员 · {{ members.length }}</h2><p v-for="m in members" :key="m.id">{{ m.name }} <small>{{ m.role==='admin'?'管理员':'成员' }}</small></p>
          <template v-if="team.role==='admin'"><button :disabled="busy" @click="invite">生成一次性邀请码</button><p v-if="generatedInvite">7天有效，仅供一位队友加入。<input aria-label="新团队邀请码" :value="generatedInvite" readonly @focus="($event.target as HTMLInputElement).select()"></p><details><summary>管理邀请</summary><p v-for="i in invites" :key="i.id">#{{ i.id }} · {{ i.revoked?'已撤销':i.used_by?'已使用':new Date(i.expires_at)<new Date()?'已过期':'待使用' }}<button v-if="!i.revoked&&!i.used_by" :disabled="busy" @click="revoke(i.id)">撤销</button></p></details></template>
        </template>
      </aside>
      <div class="board-main">
        <form v-if="editorKind" class="box editor" @submit.prevent="saveEditor">
          <h2>{{ editorId?'编辑':'新增' }}{{ {project:'项目计划',stage:'阶段',task:'任务',card:'接续卡'}[editorKind] }}</h2>
          <p v-if="conflict" class="alert error">发生版本冲突，下面仍是你的输入。请复制需要保留的内容，再载入最新版重新编辑。<button type="button" @click="loadLatest">载入最新版</button></p>
          <fieldset :disabled="busy">
          <template v-if="editorKind==='project'"><label>项目名称<input v-model="draft.name" required maxlength="120"></label><label>目标与说明<textarea v-model="draft.goal" maxlength="6000"></textarea></label><label>负责人<select v-model="draft.owner_id" required><option v-for="m in members" :value="m.id">{{m.name}}</option></select></label><div class="fields"><label>开始日期<input v-model="draft.start_date" type="date"></label><label>截止日期<input v-model="draft.due_date" type="date"></label><label>状态<select v-model="draft.status"><option value="active">进行中</option><option value="paused">暂停</option><option value="completed">已结束</option></select></label></div></template>
          <template v-else>
            <label>标题<input v-model="draft.title" required maxlength="120" :disabled="editorKind==='task'&&!!editorId&&!canManage"></label>
            <template v-if="editorKind==='stage'"><label>阶段目标<textarea v-model="draft.goal" maxlength="6000"></textarea></label><label>完成标准<textarea v-model="draft.criteria" maxlength="6000"></textarea></label><div class="fields"><label>排列顺序<input v-model.number="draft.position" type="number" min="0" max="10000" required></label><label>截止日期<input v-model="draft.due_date" type="date"></label></div></template>
            <template v-if="editorKind==='task'"><fieldset :disabled="!!editorId&&!canManage"><label>说明<textarea v-model="draft.description" maxlength="6000"></textarea></label><label>完成标准<textarea v-model="draft.criteria" maxlength="6000"></textarea></label><div class="fields"><label>所属阶段<select v-model="draft.stage_id"><option :value="null">暂不归入阶段</option><option v-for="s in stages" :value="s.id">{{s.title}}</option></select></label><label>负责人<select v-model="draft.assignee_id"><option :value="null">未分配</option><option v-for="m in members.filter(m=>canManage||m.id===me?.id)" :value="m.id">{{m.name}}</option></select></label><label>截止日期<input v-model="draft.due_date" type="date"></label></div></fieldset><label>状态<select v-model="draft.status"><option v-for="(label,s) in statuses" :value="s">{{label}}</option></select></label><label v-if="draft.status==='blocked'">受阻原因<textarea v-model="draft.blocked_reason" maxlength="6000"></textarea></label><label>进度或结果说明<textarea v-model="draft.result" maxlength="6000"></textarea></label><label>关联附件（可多选）<select v-model="draft.file_ids" multiple><option v-for="f in snapshot?.files" :value="f.id">{{f.name}}</option></select></label></template>
            <template v-if="editorKind==='card'"><label v-for="(label,k) in {goal:'当前目标',done:'已完成',open_questions:'未解决问题',next_steps:'下一步',evidence:'依据'}">{{ label }}<textarea v-model="draft[k]" maxlength="6000"></textarea></label></template>
          </template>
          <div class="actions"><button class="primary" :disabled="conflict">保存</button><button type="button" @click="editorKind=''">取消</button></div>
          </fieldset>
        </form>
        <section v-if="!project" class="box welcome"><h2>{{ team?'选择或创建一个项目':'选择一个团队开始协作' }}</h2><p>共同安排计划，把任务交给具体成员，完成后打钩，团队进度随之更新。</p></section>
        <template v-else>
          <section class="box project-heading"><div><small>{{team?.name}} / 团队项目</small><h2>{{project.name}}</h2><p class="pre">{{project.goal||'尚未填写项目目标'}}</p><p class="muted">负责人 {{name(project.owner_id)}} · {{project.start_date||'未定开始日期'}} — {{project.due_date||'未定截止日期'}} · {{ {active:'进行中',paused:'暂停',completed:'已结束'}[project.status] }}</p></div><button v-if="canManage" :disabled="busy" @click="openEditor('project',project)">编辑计划</button></section>
          <nav class="tabs"><button v-for="(label,k) in {overview:'项目概览',tasks:'团队任务',plan:'阶段计划',materials:'资料与接续'}" :class="{active:section===k}" @click="section=k; if(k==='tasks')mine=false">{{label}}</button><button :class="{active:section==='mine'}" @click="section='mine';mine=true">我的任务</button></nav>
          <template v-if="section==='overview'">
            <div class="stats"><article class="box"><small>任务完成率</small><strong>{{snapshot?.progress.percent===null?'—':`${snapshot?.progress.percent}%`}}</strong><span>{{snapshot?.progress.total?`${snapshot.progress.done} / ${snapshot.progress.total} 项任务完成`:'尚未安排任务'}}</span></article><article class="box"><small>受阻任务</small><strong>{{snapshot?.progress.blocked}}</strong><span>需要团队共同解决</span></article><article class="box"><small>逾期未完成</small><strong>{{snapshot?.progress.overdue}}</strong><span>按北京时间日期统计</span></article></div>
            <section class="box"><h2>成员分工</h2><p v-for="m in members">{{m.name}} · {{tasks.filter(t=>t.assignee_id===m.id).length}} 项任务，{{tasks.filter(t=>t.assignee_id===m.id&&t.status==='done').length}} 项已完成</p><p class="muted">任务数量用于了解分工，不代表工作量或成员能力。</p></section>
            <section class="box"><h2>近期动态</h2><p v-if="!snapshot?.events.length">还没有动态</p><article class="activity" v-for="e in snapshot?.events" :key="e.id"><strong>{{e.actor_name}}</strong> {{eventNames[e.kind]||e.kind}} · {{e.after?.title||e.after?.name||''}}<span v-if="e.after?.status"> · {{statuses[e.after.status]||{active:'进行中',paused:'暂停',completed:'已结束'}[e.after.status as string]||e.after.status}}</span><small>{{dateText(e.created_at)}}</small><details v-if="e.before||e.after"><summary>查看变化依据</summary><pre>{{JSON.stringify({原记录:e.before,新记录:e.after},null,2)}}</pre></details></article></section>
            <details class="box"><summary>AI 接入准备 · 项目事实</summary><p>当前展示数据库中的真实记录，尚未调用模型。后续 AI 建议需要成员确认后才能应用。</p><button :disabled="busy" @click="loadContext">查看当前项目上下文</button><p v-if="contextResult">{{contextResult.coverage}} {{contextResult.truncated?'本次内容有截断。':''}}</p><pre v-if="contextResult">{{JSON.stringify(contextResult,null,2)}}</pre></details>
          </template>
          <section v-if="section==='tasks'||section==='mine'" class="box">
            <div class="row"><h2>{{section==='mine'?'我的任务':'团队任务'}}</h2><button class="primary" :disabled="busy" @click="openEditor('task')">＋ 创建任务</button></div>
            <div class="fields"><label>查看范围<select :value="section==='mine'?'mine':mine?'mine':'all'" @change="mine=($event.target as HTMLSelectElement).value==='mine'"><option value="all" :disabled="section==='mine'">全部成员</option><option value="mine">只看我的</option></select></label><label>状态<select v-model="filterStatus"><option value="">全部状态</option><option v-for="(label,s) in statuses" :value="s">{{label}}</option></select></label><label>负责人<select v-model="filterMember"><option value="">全部负责人</option><option value="null">未分配</option><option v-for="m in members" :value="String(m.id)">{{m.name}}</option></select></label><label>阶段<select v-model="filterStage"><option value="">全部阶段</option><option v-for="s in stages" :value="String(s.id)">{{s.title}}</option></select></label></div>
            <p v-if="!visibleTasks.length">当前没有符合条件的任务。</p><article class="task-row" v-for="t in visibleTasks" :key="t.id"><input type="checkbox" :aria-label="`完成任务：${t.title}`" :checked="t.status==='done'" :disabled="busy||!editable(t)" @change="checkTask($event,t)"><button class="task-title" @click="chooseTask(t)">{{t.title}}</button><span :class="['badge',t.status]">{{statuses[t.status]}}</span><span>{{name(t.assignee_id)}}</span><small>{{t.due_date||'未设截止日期'}}</small><button v-if="editable(t)" :disabled="busy" @click="openEditor('task',t)">更新</button></article>
          </section>
          <section v-if="task&&(section==='tasks'||section==='mine')" class="box task-detail"><div class="row"><h2>{{task.title}}</h2><button @click="selectedTask=null">关闭详情</button></div><p class="pre">{{task.description||'暂无说明'}}</p><h3>完成标准</h3><p class="pre">{{task.criteria||'尚未填写'}}</p><p>负责人：{{name(task.assignee_id)}} · {{statuses[task.status]}}</p><p v-if="task.blocked_reason" class="pre">受阻原因：{{task.blocked_reason}}</p><p class="pre">进度 / 结果：{{task.result||'尚未补充'}}</p><p v-if="task.completed_at">{{name(task.completed_by)}} 于 {{dateText(task.completed_at)}} 标记完成</p><button v-for="f in snapshot?.files.filter(f=>task?.file_ids.includes(f.id))" :disabled="busy" @click="download(f)">{{f.name}}</button><h3>讨论与进度补充</h3><article v-for="n in comments" class="activity"><strong>{{name(n.created_by)}}</strong><p class="pre">{{n.text}}</p><small>{{dateText(n.created_at)}}</small></article><form @submit.prevent="addNote(true)"><label>补充进度或问题<textarea v-model="comment" required maxlength="6000"></textarea></label><button :disabled="busy">发布补充</button></form><details><summary>任务操作历史</summary><button v-if="!historyLoaded||historyBefore" :disabled="busy" @click="loadHistory">{{historyLoaded?'加载更早记录':'读取操作历史'}}</button><p v-for="e in taskHistory">{{e.actor_name}} · {{eventNames[e.kind]}} · {{dateText(e.created_at)}}</p></details></section>
          <section v-if="section==='plan'" class="box"><div class="row"><h2>阶段计划</h2><button v-if="canManage" :disabled="busy" @click="openEditor('stage')">＋ 新增阶段</button></div><p v-if="!stages.length">先安排项目阶段，再将任务归入阶段。</p><article v-for="s in stages" class="stage"><div class="row"><h3>{{s.title}}</h3><button v-if="canManage" :disabled="busy" @click="openEditor('stage',s)">编辑 / 排序</button></div><p class="pre">{{s.goal}}</p><p>完成标准：{{s.criteria||'未填写'}}</p><p>截止 {{s.due_date||'未安排'}} · {{tasks.filter(t=>t.stage_id===s.id&&t.status==='done').length}} / {{tasks.filter(t=>t.stage_id===s.id).length}} 项任务完成</p><ul><li v-for="t in tasks.filter(t=>t.stage_id===s.id)">{{statuses[t.status]}} · {{t.title}} · {{name(t.assignee_id)}}</li></ul></article></section>
          <template v-if="section==='materials'">
            <section class="box"><h2>项目文件</h2><form @submit.prevent="upload"><label>选择文件（最多20 MB）<input type="file" :disabled="busy" @change="selectFile"></label><button :disabled="busy||!selectedFile">上传到当前项目</button></form><p v-for="f in snapshot?.files">{{f.name}} · {{Math.ceil(f.size/1024)}} KB · {{name(f.created_by)}} <button :disabled="busy" @click="download(f)">下载</button></p></section>
            <section class="box"><div class="row"><h2>项目接续卡</h2><button :disabled="busy" @click="openEditor('card')">＋ 新建接续卡</button></div><article v-for="c in cards" class="stage"><div class="row"><h3>{{c.title}} · v{{c.version}}</h3><button v-if="canManage||c.created_by===me.id" :disabled="busy" @click="openEditor('card',c)">编辑</button></div><dl><template v-for="(label,k) in {goal:'目标',done:'已完成',open_questions:'未解决',next_steps:'下一步',evidence:'依据'}"><dt>{{label}}</dt><dd class="pre">{{c[k]||'未填写'}}</dd></template></dl></article><details v-if="canManage"><summary>导入旧工作空间的一张接续卡</summary><p>先在顶部打开原工作空间，查看需要导入的版本。指定版本将复制到当前项目，并对本团队成员可见。</p><form @submit.prevent="importCard"><label>原访问码<input v-model="importCode" type="password" required></label><label>卡片版本<input v-model.number="importVersion" type="number" min="1" required></label><button :disabled="busy">确认复制到当前项目</button></form></details></section>
            <section class="box"><h2>团队交接</h2><form @submit.prevent="publishHandoff"><div class="fields"><label>接续卡<select v-model="cardChoice" required><option value="">请选择</option><option v-for="c in cards" :value="String(c.id)">{{c.title}} · v{{c.version}}</option></select></label><label>指定接手人<select v-model="recipient" required><option value="">请选择</option><option v-for="m in members.filter(m=>m.id!==me?.id)" :value="String(m.id)">{{m.name}}</option></select></label></div><button :disabled="busy||!cards.length">发布交接</button></form><article v-for="h in handoffs" class="stage"><p>{{name(h.created_by)}} → {{name(h.recipient_id)}} · {{h.accepted_at?'已接手':'待接手'}}</p><details><summary>发布时的卡片 v{{h.card_version}}</summary><p v-for="(label,k) in {title:'标题',goal:'目标',done:'已完成',open_questions:'问题',next_steps:'下一步',evidence:'依据'}" class="pre">{{label}}：{{h.card_snapshot[k]}}</p></details><button v-if="!h.accepted_at&&h.recipient_id===me.id" :disabled="busy" @click="acceptHandoff(h.id)">确认接手</button></article></section>
            <section class="box"><h2>项目讨论</h2><article v-for="n in records.filter(r=>r.kind==='note'&&!r.task_id)" class="activity"><strong>{{name(n.created_by)}}</strong><p class="pre">{{n.text}}</p><small>{{dateText(n.created_at)}}</small></article><form @submit.prevent="addNote(false)"><label>新的想法或进度<textarea v-model="projectNote" maxlength="6000" required></textarea></label><button :disabled="busy">发布到当前项目</button></form><p class="muted">团队任务和讨论当前需要在线提交。原工作空间仍保留文字离线补传。</p></section>
          </template>
          <p class="muted">{{busy?'正在保存…':lastSync?`最近同步 ${dateText(lastSync)} · 每5秒自动刷新`:''}}</p>
        </template>
      </div>
    </div>
  </template>
</main>
</template>

<style scoped>
.collab { max-width:1440px; margin:auto; padding:28px; color:#213c35; }
.collab h1 { font-size:30px; margin:4px 0; }.collab h2 { font-size:20px; margin:0 0 14px; }.collab h3 {font-size:17px; margin:8px 0;}
.collab p {line-height:1.65; margin:10px 0;}.collab small,.muted {color:#64786f;}.board-header,.identity,.row,.project-heading {display:flex;align-items:center;justify-content:space-between;gap:12px;}.board-header {margin-bottom:20px;}.identity {flex-wrap:wrap;font-size:14px;}
.box {background:white;border:1px solid #dce5df;border-radius:12px;padding:24px;margin-bottom:18px;box-shadow:0 4px 14px #193a2805;}.auth-grid {display:grid;grid-template-columns:1fr 1fr;gap:24px;max-width:960px;margin:40px auto;}.board-layout {display:grid;grid-template-columns:250px minmax(0,1fr);gap:24px;}.board-sidebar {align-self:start;}.board-main {min-width:0;}
.collab button {border:1px solid #cad8d0;background:#fff;color:#274b3e;border-radius:7px;padding:8px 12px;cursor:pointer;font:inherit;font-size:14px;}.collab button:hover {background:#eff6f0;}.collab button:disabled {opacity:.5;cursor:not-allowed;}.collab button.primary,.collab button.active {background:#20644d;color:white;border-color:#20644d;}.collab .nav-item {display:block;width:100%;text-align:left;margin:8px 0;}.nav-item small {float:right;color:inherit;}
.collab label {display:block;font-size:14px;font-weight:600;margin:12px 0;}.collab input:not([type=checkbox]),.collab textarea,.collab select {display:block;width:100%;min-width:0;box-sizing:border-box;border:1px solid #c7d5cd;border-radius:7px;padding:9px;background:#fff;color:#1c322a;font:inherit;margin-top:5px;}.collab textarea {min-height:80px;resize:vertical;}.collab input[type=checkbox] {width:20px;height:20px;flex:none;accent-color:#20644d;}.collab fieldset {border:0;padding:0;margin:0;min-width:0;}.collab details {margin:16px 0;}.collab summary {cursor:pointer;font-weight:600;font-size:14px;}.collab pre {white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f7f4;padding:12px;font-size:12px;max-height:400px;overflow:auto;}.collab hr {border:0;border-top:1px solid #e0e7e1;margin:22px 0;}.collab dd {margin:4px 0 12px;}.collab dt {font-weight:600;}
.identity-key {background:#edf4ee;border-radius:8px;padding:12px 16px;max-width:700px;}.alert {padding:12px 16px;border-radius:8px;}.error {background:#fff0eb;color:#91351c;}.success {background:#e7f3e9;color:#245f3d;}.fields {display:flex;gap:12px;flex-wrap:wrap;}.fields>label {flex:1;min-width:130px;}.actions {display:flex;gap:10px;margin-top:14px;}.editor {border:2px solid #4d8d6b;}.tabs {display:flex;gap:8px;flex-wrap:wrap;margin:18px 0;}.stats {display:grid;grid-template-columns:repeat(3,1fr);gap:14px;}.stats article {display:flex;flex-direction:column;gap:8px;}.stats strong {font-size:32px;}.stats span {font-size:13px;}.activity,.stage {border-top:1px solid #e0e8e2;padding:16px 0;}.activity small {display:block;margin-top:5px;}.task-row {display:flex;gap:12px;align-items:center;border-top:1px solid #e4ebe5;padding:16px 0;flex-wrap:wrap;}.collab .task-title {border:none;text-align:left;flex:1;min-width:130px;font-weight:600;}.badge {font-size:12px;padding:4px 7px;background:#edf1ed;border-radius:5px;}.badge.done {background:#e1f0e5;color:#236b40;}.badge.blocked {background:#fff0e6;color:#9b4d1d;}.pre {white-space:pre-wrap;overflow-wrap:anywhere;}.welcome {padding:45px 24px;}
@media(max-width:850px){.collab{padding:16px;}.board-layout{grid-template-columns:1fr;gap:0;}.board-sidebar .nav-item{display:inline-block;width:auto;margin-right:8px;}.auth-grid{grid-template-columns:1fr;gap:0;}.board-header{align-items:flex-start;flex-direction:column;}.box{padding:18px;}.stats{gap:8px;}.stats article{padding:12px;}.stats strong{font-size:26px;}.task-row{gap:8px;}.project-heading{align-items:flex-start;}.identity-key{max-width:100%;}.fields{gap:8px;}}
</style>
