// Optional actual-browser check. Install playwright-core in ../tmp/xuhang-browser-qa first.
import { chromium } from '../../tmp/xuhang-browser-qa/node_modules/playwright-core/index.mjs'
import { spawn, spawnSync } from 'node:child_process'
import { mkdir } from 'node:fs/promises'
import assert from 'node:assert/strict'

const server = spawn('.venv/Scripts/python.exe', ['-X','utf8','tests/serve_browser_fixture.py'], {windowsHide:true,stdio:'pipe'})
let logs = ''
server.stderr.on('data', b => { logs += b.toString() })
let browser
const errors=[]
async function until(fn, message) {
  for(let i=0;i<80;i++){if(await fn())return;await new Promise(r=>setTimeout(r,200))}
  throw Error(message)
}
async function click(page,text,exact=true){await page.getByRole('button',{name:text,exact}).click()}
async function filled(page,label,value){await page.getByLabel(label,{exact:true}).fill(value)}
async function synced(page){await until(async()=>await page.getByRole('button',{name:'刷新进度',exact:true}).isEnabled(),'write did not finish')}
async function save(page){await click(page,'保存');await until(async()=>await page.locator('.editor').count()===0,'editor did not save')}
async function selectProject(page){await click(page,'课程小组 管理员').catch(()=>click(page,'课程小组 成员'));await until(async()=>await page.getByRole('button',{name:'登录系统',exact:true}).count()>0,'project not visible');await click(page,'登录系统');await until(async()=>await page.locator('.project-heading').count()>0,'project not loaded')}
try {
  await until(async()=>fetch('http://127.0.0.1:8876/api/health').then(r=>r.ok).catch(()=>false),'test server failed: '+logs)
  browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true})
  const ac=await browser.newContext({viewport:{width:1440,height:1000}}),bc=await browser.newContext({viewport:{width:390,height:844}})
  const a=await ac.newPage(), b=await bc.newPage()
  for(const p of [a,b])p.on('pageerror', e=>errors.push(e.message))
  await a.goto('http://127.0.0.1:8876')
  await filled(a,'你的名字','小林');await a.getByLabel('加入方式').selectOption('owner');await filled(a,'电脑启动窗口的访问码','BROWSER-TEST-ONLY');await click(a,'创建成员身份');await synced(a)
  await a.getByText('创建团队',{exact:true}).click();await filled(a,'名称','课程小组');await click(a,'创建');await synced(a)
  await click(a,'＋ 创建项目');await filled(a,'项目名称','登录系统');await filled(a,'目标与说明','两名成员完成登录功能');await save(a)
  await click(a,'生成一次性邀请码');await synced(a)
  const invite=await a.getByLabel('新团队邀请码',{exact:true}).inputValue()
  await b.goto('http://127.0.0.1:8876');await filled(b,'你的名字','小周');await filled(b,'团队邀请码',invite);await click(b,'创建成员身份');await synced(b);await selectProject(b)
  await click(a,'刷新进度');await until(async()=>await a.locator('.board-sidebar').getByText('小周 成员',{exact:true}).count()>0,'member not synchronized')
  await click(a,'阶段计划');await click(a,'＋ 新增阶段');await filled(a,'标题','第一版');await filled(a,'阶段目标','完成基本登录');await filled(a,'完成标准','双人联调通过');await save(a)
  await click(a,'团队任务');await click(a,'＋ 创建任务');await filled(a,'标题','实现错误密码提示');await filled(a,'说明','提交错误密码时提示原因');await filled(a,'完成标准','正确显示错误信息');await a.getByLabel('所属阶段').selectOption({label:'第一版'});await a.locator('.editor').getByLabel('负责人').selectOption({label:'小周'});await save(a)
  await click(b,'我的任务');await until(async()=>await b.getByRole('button',{name:'实现错误密码提示',exact:true}).count()>0,'task did not sync to mobile')
  await b.getByRole('checkbox',{name:'完成任务：实现错误密码提示'}).click();await synced(b)
  await click(a,'项目概览');await until(async()=>await a.getByText('100%',{exact:true}).count()>0,'progress did not sync')
  await click(b,'实现错误密码提示');await filled(b,'补充进度或问题','已经通过错误密码测试');await click(b,'发布补充');await synced(b)
  // Independent device identity login.
  const aliceKey=await a.getByLabel('我的个人登录密钥',{exact:true}).inputValue()
  const cc=await browser.newContext(), c=await cc.newPage();await c.goto('http://127.0.0.1:8876');await filled(c,'个人登录密钥',aliceKey);await click(c,'登录');await synced(c);assert.match(await c.locator('.identity').innerText(),/小林/);await cc.close()
  // Conflict leaves the unsaved editor intact.
  await click(a,'团队任务');await click(a,'更新');await filled(a,'进度或结果说明','电脑上的旧草稿')
  await click(b,'更新');await filled(b,'进度或结果说明','手机提交的新进度');await save(b)
  await click(a,'保存');await synced(a);assert.match(await a.locator('.editor').innerText(),/发生版本冲突/);assert.equal(await a.getByLabel('进度或结果说明',{exact:true}).inputValue(),'电脑上的旧草稿');await click(a,'载入最新版');assert.equal(await a.getByLabel('进度或结果说明',{exact:true}).inputValue(),'手机提交的新进度');await click(a,'取消')
  // Offline write: error is visible and text remains available for retry.
  await click(b,'更新');await filled(b,'进度或结果说明','断网时的输入');await bc.setOffline(true);await click(b,'保存');await synced(b);assert.equal(await b.getByLabel('进度或结果说明',{exact:true}).inputValue(),'断网时的输入');assert.match(await b.getByRole('alert').first().innerText(),/连接中断/);await bc.setOffline(false);await save(b)
  // Reopen completion and check both views converge.
  await b.getByRole('checkbox',{name:'完成任务：实现错误密码提示'}).click();await synced(b)
  await click(a,'项目概览');await until(async()=>await a.getByText('0%',{exact:true}).count()>0,'reopen did not sync')
  await mkdir('../tmp/xuhang-browser-qa/screenshots',{recursive:true})
  await a.screenshot({path:'../tmp/xuhang-browser-qa/screenshots/desktop.png',fullPage:true})
  await b.screenshot({path:'../tmp/xuhang-browser-qa/screenshots/mobile.png',fullPage:true})
  for(const p of [a,b])assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'horizontal overflow')
  assert.deepEqual(errors,[])
  console.log('PASS: two users, invitation, stages, assignment, mobile completion, cross-device identity, conflict, offline retry, reopening, responsive layout; no page errors.')
} catch(e) { console.error(e);console.error(logs);process.exitCode=1 }
finally {
  if(browser)await browser.close()
  if(server.pid)spawnSync('taskkill',['/PID',String(server.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'})
}
