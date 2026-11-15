import json
import secrets
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from server import create_app

CODE = 'COLLAB-TEST-ONLY'


def op(**data):
    return {'operation_id': str(uuid4()), **data}


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def register(app, name, invitation=''):
    key = secrets.token_hex(32)
    client = TestClient(app, headers={'X-Member-Key': key})
    user = ok(client.post('/api/collab/register', json={'name': name, 'key': key, 'invitation': invitation, 'access_code': '' if invitation else CODE}))
    return client, user, key


def record(client, pid, kind, data):
    return ok(client.post(f'/api/collab/projects/{pid}/records/{kind}', json=op(data=data)))


def task_data(**changes):
    return {'title':'完成登录功能', 'description':'实现密码错误提示', 'criteria':'错误密码返回明确提示', 'stage_id':None,
            'assignee_id':None, 'due_date':None, 'status':'todo', 'result':'', 'blocked_reason':'', 'file_ids':[], **changes}


@pytest.fixture
def board(tmp_path):
    app = create_app(tmp_path, CODE)
    a, alice, key = register(app, '小林')
    tid = ok(a.post('/api/collab/teams', json=op(name='课程设计组',description='真实协作')))['id']
    invite = secrets.token_hex(32)
    ok(a.post(f'/api/collab/teams/{tid}/invites', json=op(code=invite)))
    b, bob, _ = register(app, '小周', invite)
    pid = ok(a.post(f'/api/collab/teams/{tid}/projects', json=op(name='课程项目',goal='完成课程设计',owner_id=alice['id'])))['id']
    return tmp_path, app, a, b, alice, bob, tid, pid, key


def test_full_flow_identity_restart_progress_and_reopen(board):
    folder, app, a, b, alice, bob, tid, pid, key = board
    s = record(a,pid,'stage',{'title':'第一版','criteria':'真机通过','position':1})
    t = record(a,pid,'task',task_data(stage_id=s['id'],assignee_id=bob['id'],due_date='2020-01-01'))
    state = ok(b.get(f'/api/collab/projects/{pid}'))
    assert state['progress'] == {'total':1,'done':0,'percent':0,'blocked':0,'overdue':1}
    done = task_data(stage_id=s['id'],assignee_id=bob['id'],due_date='2020-01-01',status='done',result='真机测试通过')
    body = op(version=1,data=done)
    path = f'/api/collab/projects/{pid}/records/{t["id"]}'
    saved = ok(b.put(path,json=body))
    assert ok(b.put(path,json=body)) == saved
    record(b,pid,'note',{'task_id':t['id'],'text':'已经补上测试截图'})
    state = ok(a.get(f'/api/collab/projects/{pid}'))
    assert state['progress']['percent'] == 100 and state['progress']['overdue'] == 0
    task = next(x for x in state['records'] if x['kind']=='task')
    assert task['completed_by'] == bob['id'] and task['completed_at']
    assert len([e for e in state['events'] if e['kind']=='task_completed']) == 1
    ok(b.put(path,json=op(version=2,data={**done,'status':'doing'})))
    restarted = TestClient(create_app(folder,CODE),headers={'X-Member-Key':key})
    assert ok(restarted.get('/api/collab/me'))['user'] == alice
    state = ok(restarted.get(f'/api/collab/projects/{pid}'))
    task = next(x for x in state['records'] if x['kind']=='task')
    assert task['completed_at'] is None and task['completed_by'] is None
    assert {'task_completed','task_reopened'} <= {e['kind'] for e in state['events']}


def test_team_isolation_and_assignment_permissions(board):
    _,app,a,b,alice,bob,tid,pid,_ = board
    outsider,other,_ = register(app,'其他团队成员')
    for path in [f'/teams/{tid}',f'/projects/{pid}',f'/projects/{pid}/context']:
        assert outsider.get('/api/collab'+path).status_code == 403
    assert outsider.post(f'/api/collab/projects/{pid}/records/task',json=op(data=task_data())).status_code == 403
    assert a.post(f'/api/collab/projects/{pid}/records/task',json=op(data=task_data(assignee_id=other['id']))).status_code == 422
    assert b.post(f'/api/collab/teams/{tid}/invites',json=op(code=secrets.token_hex(32))).status_code == 403
    assert b.post(f'/api/collab/projects/{pid}/records/stage',json=op(data={'title':'越权阶段'})).status_code == 403
    assert b.post(f'/api/collab/projects/{pid}/records/task',json=op(data=task_data(assignee_id=alice['id']))).status_code == 403
    t=record(a,pid,'task',task_data(assignee_id=bob['id']))
    path=f'/api/collab/projects/{pid}/records/{t["id"]}'
    assert b.put(path,json=op(version=1,data=task_data(assignee_id=alice['id']))).status_code == 403
    assert b.put(path,json=op(version=1,data=task_data(assignee_id=bob['id'],title='擅改计划'))).status_code == 403
    owned=record(a,pid,'task',task_data(assignee_id=alice['id']))
    assert b.put(f'/api/collab/projects/{pid}/records/{owned["id"]}',json=op(version=1,data=task_data(assignee_id=alice['id'],status='done'))).status_code == 403


def test_invite_revoke_expire_single_use_and_registration_retry(board):
    folder,app,a,b,alice,bob,tid,pid,_=board
    code=secrets.token_hex(32)
    payload=op(code=code)
    created=ok(a.post(f'/api/collab/teams/{tid}/invites',json=payload))
    assert ok(a.post(f'/api/collab/teams/{tid}/invites',json=payload)) == created
    ok(a.post(f'/api/collab/teams/{tid}/invites/{created["id"]}/revoke',json=op()))
    assert b.post('/api/collab/join',json=op(code=code)).status_code==400
    code=secrets.token_hex(32)
    ok(a.post(f'/api/collab/teams/{tid}/invites',json=op(code=code)))
    newkey=secrets.token_hex(32)
    body={'name':'新同学','key':newkey,'invitation':code}
    new=ok(a.post('/api/collab/register',json=body))
    assert ok(a.post('/api/collab/register',json=body))==new
    assert a.post('/api/collab/register',json={**body,'key':secrets.token_hex(32)}).status_code==400
    code=secrets.token_hex(32)
    iid=ok(a.post(f'/api/collab/teams/{tid}/invites',json=op(code=code)))['id']
    with sqlite3.connect(folder/'xuhang.sqlite3') as con:
        con.execute("UPDATE collab_invites SET expires_at='2000-01-01' WHERE id=?",(iid,))
    assert b.post('/api/collab/join',json=op(code=code)).status_code==400
    assert a.post('/api/collab/register',json={'name':'无邀请','key':secrets.token_hex(32)}).status_code==403


def test_concurrent_updates_and_idempotency(board):
    _,_,a,b,_,bob,_,pid,_=board
    task=record(a,pid,'task',task_data(assignee_id=bob['id']))
    path=f'/api/collab/projects/{pid}/records/{task["id"]}'
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls=[pool.submit(client.put,path,json=op(version=1,data=task_data(assignee_id=bob['id'],status=status))) for client,status in [(a,'doing'),(b,'done')]]
    assert sorted(f.result().status_code for f in calls)==[200,409]
    payload=op(data=task_data())
    path=f'/api/collab/projects/{pid}/records/task'
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls=[pool.submit(a.post,path,json=payload) for _ in range(2)]
    assert ok(calls[0].result())==ok(calls[1].result())
    assert a.post(path,json={**payload,'data':task_data(title='其他内容')}).status_code==409


def test_file_scope_comments_and_handoff_snapshot(board):
    _,app,a,b,alice,bob,tid,pid,_=board
    pid2=ok(a.post(f'/api/collab/teams/{tid}/projects',json=op(name='第二项目',owner_id=alice['id'])))['id']
    outsider,_,_=register(app,'无关成员')
    path=f'/api/collab/projects/{pid}/files?operation_id={uuid4()}&name=proof.txt'
    file=ok(b.post(path,content=b'actual evidence'))
    assert ok(b.post(path,content=b'actual evidence'))==file
    assert b.post(path,content=b'different').status_code==409
    fid=file['id']
    assert a.get(f'/api/collab/projects/{pid}/files/{fid}').content==b'actual evidence'
    assert outsider.get(f'/api/collab/projects/{pid}/files/{fid}').status_code==403
    assert a.get(f'/api/collab/projects/{pid2}/files/{fid}').status_code==404
    assert a.post(f'/api/collab/projects/{pid2}/records/task',json=op(data=task_data(file_ids=[fid]))).status_code==422
    t=record(a,pid,'task',task_data(file_ids=[fid]))
    assert a.post(f'/api/collab/projects/{pid2}/records/note',json=op(data={'text':'错项目','task_id':t['id']})).status_code==422
    card=record(a,pid,'card',{'title':'登录交接','goal':'最初目标'})
    h=record(a,pid,'handoff',{'recipient_id':bob['id'],'card_id':card['id'],'card_version':1})
    ok(a.put(f'/api/collab/projects/{pid}/records/{card["id"]}',json=op(version=1,data={'title':'登录交接','goal':'修改后的目标'})))
    assert a.post(f'/api/collab/projects/{pid}/handoffs/{h["id"]}/accept',json=op()).status_code==403
    ok(b.post(f'/api/collab/projects/{pid}/handoffs/{h["id"]}/accept',json=op()))
    snap=ok(b.get(f'/api/collab/projects/{pid}'))
    handoff=next(r for r in snap['records'] if r['kind']=='handoff')
    assert handoff['card_snapshot']['goal']=='最初目标' and handoff['accepted_at']


def test_explicit_legacy_import_is_additive_and_requires_both_permissions(board):
    folder,app,a,b,_,_,_,pid,_=board
    legacy=TestClient(app,headers={'X-Access-Code':CODE})
    body=op(version=0,device='旧电脑',project='历史课程',goal='旧目标',done='已有工作')
    ok(legacy.put('/api/card',json=body))
    before=ok(legacy.get('/api/state'))
    assert not ok(a.get(f'/api/collab/projects/{pid}'))['records']
    assert a.post(f'/api/collab/projects/{pid}/import-card',json=op(access_code='bad',card_version=1)).status_code==403
    assert b.post(f'/api/collab/projects/{pid}/import-card',json=op(access_code=CODE,card_version=1)).status_code==403
    payload=op(access_code=CODE,card_version=1)
    imported=ok(a.post(f'/api/collab/projects/{pid}/import-card',json=payload))
    assert ok(a.post(f'/api/collab/projects/{pid}/import-card',json=payload))==imported
    TestClient(create_app(folder,CODE))
    after=ok(legacy.get('/api/state'))
    assert before['card']==after['card'] and before['workspace_id']==after['workspace_id']
    assert ok(a.get(f'/api/collab/projects/{pid}'))['records'][0]['goal']=='旧目标'


def test_context_bounds_schema_and_no_credentials(board):
    _,_,a,_,_,_,_,pid,key=board
    for i in range(11):
        record(a,pid,'note',{'text':str(i)+'x'*5900})
    context=ok(a.get(f'/api/collab/projects/{pid}/context'))
    assert context['truncated'] and context['mode']=='facts_only'
    assert sum(len(json.dumps(s['record'],ensure_ascii=False)) for s in context['sources'])<=50000
    assert len(context['sources'])<11
    serialized=json.dumps(context)
    assert key not in serialized and CODE not in serialized and 'key_hash' not in serialized
    assert all(s['source_id'].startswith('record:') for s in context['sources'])
    schema=ok(a.get('/api/collab/ai/suggestion-schema'))
    assert schema['properties']['status']['const']=='pending_confirmation'


def test_empty_progress_plan_updates_and_cross_project_stage(board):
    _,_,a,b,alice,bob,tid,pid,_=board
    assert ok(a.get(f'/api/collab/projects/{pid}'))['progress']['percent'] is None
    payload=op(version=1,name='新项目名',goal='新计划',owner_id=alice['id'],start_date='2026-11-15',due_date='2026-12-01')
    assert b.put(f'/api/collab/projects/{pid}',json=payload).status_code==403
    ok(a.put(f'/api/collab/projects/{pid}',json=payload))
    assert a.put(f'/api/collab/projects/{pid}',json={**payload,'operation_id':str(uuid4())}).status_code==409
    assert a.put(f'/api/collab/projects/{pid}',json=op(version=2,name='日期错误',owner_id=alice['id'],start_date='2026-12-01',due_date='2026-11-01')).status_code==422
    s=record(a,pid,'stage',{'title':'阶段A','position':1})
    ok(a.put(f'/api/collab/projects/{pid}/records/{s["id"]}',json=op(version=1,data={'title':'阶段A','position':5,'criteria':'完成两人验收'})))
    pid2=ok(a.post(f'/api/collab/teams/{tid}/projects',json=op(name='另一项目',owner_id=alice['id'])))['id']
    assert a.post(f'/api/collab/projects/{pid2}/records/task',json=op(data=task_data(stage_id=s['id']))).status_code==422


def test_history_pagination_keeps_snapshots_and_filters_file_id_collisions(board):
    folder,app,a,b,alice,bob,tid,pid,_=board
    task=record(a,pid,'task',task_data(assignee_id=bob['id']))
    path=f'/api/collab/projects/{pid}/records/{task["id"]}'
    for version in range(1,52):
        ok(b.put(path,json=op(version=version,data=task_data(assignee_id=bob['id'],result=f'更新 {version}'))))
    # Files and entities have separate integer IDs; history must filter by kind as well.
    ok(a.post(f'/api/collab/projects/{pid}/files?operation_id={uuid4()}&name=example.txt',content=b'file'))
    page=ok(a.get(path+'/history'))
    assert len(page['events'])==50 and page['next_before']
    older=ok(a.get(path+'/history',params={'before':page['next_before']}))
    assert len(older['events'])==2 and older['next_before'] is None
    assert all(e['kind'].startswith('task_') for e in page['events']+older['events'])
    assert page['events'][0]['before']['result']=='更新 50'
    outsider,_,_=register(app,'非成员')
    assert outsider.get(path+'/history').status_code==403
