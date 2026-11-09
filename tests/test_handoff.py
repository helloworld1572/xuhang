from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from server import create_app

CODE = "TEST-ONLY-ACCESS"


def card(version=0, **updates):
    return {"operation_id": str(uuid4()), "version": version, "device": "电脑", "project": "测试项目",
            "goal": "完成登录模块", "done": "页面完成", "open_questions": "接口未完成",
            "next_steps": "实现错误提示", "evidence": "讨论记录", **updates}


@pytest.fixture
def env(tmp_path):
    app = create_app(tmp_path, CODE)
    pc = TestClient(app, headers={"X-Access-Code": CODE})
    phone = TestClient(app, headers={"X-Access-Code": CODE})
    return tmp_path, pc, phone


def test_pc_phone_resume_and_restart(env):
    folder, pc, phone = env
    assert pc.get("/api/state").json()["card"] is None
    assert pc.put("/api/card", json=card()).json()["version"] == 1
    assert phone.get("/api/state").json()["card"]["goal"] == "完成登录模块"
    n = {"operation_id": str(uuid4()), "device": "手机", "text": "检查密码输错的提示"}
    note_id = phone.post("/api/notes", json=n).json()["id"]
    state = pc.get("/api/state").json()
    assert state["notes"][0]["text"] == n["text"]
    assert state["notes"][0]["reviewed_at"] is None
    response = pc.post("/api/resume", json={"operation_id": str(uuid4()), "device": "电脑", "note_ids": [note_id]})
    assert response.json()["reviewed"] == 1
    restarted = TestClient(create_app(folder, CODE), headers={"X-Access-Code": CODE})
    result = restarted.get("/api/state").json()
    assert result["card"]["version"] == 1
    assert result["notes"][0]["reviewed_at"]
    assert len(result["events"]) == 3


def test_old_version_never_overwrites_new_version(env):
    _, pc, phone = env
    pc.put("/api/card", json=card())
    assert pc.put("/api/card", json=card(1, goal="电脑的新目标")).status_code == 200
    assert phone.put("/api/card", json=card(1, goal="手机的旧修改")).status_code == 409
    assert pc.get("/api/state").json()["card"]["goal"] == "电脑的新目标"
    assert pc.get("/api/history/1").json()["goal"] == "完成登录模块"


def join_team(client, name):
    workspace = client.get("/api/state").json()["workspace_id"]
    headers = {"X-Workspace-Id": workspace, "X-Team-Key": str(uuid4())}
    response = client.post("/api/team/members", json={"name": name}, headers=headers)
    assert response.status_code == 200
    return headers, response.json()


def test_team_handoff_pins_card_version_and_only_recipient_can_accept(env):
    folder, pc, phone = env
    assert pc.put("/api/card", json=card()).status_code == 200
    sender_headers, sender = join_team(pc, "小林")
    recipient_headers, recipient = join_team(phone, "小周")
    assert pc.get("/api/state").json()["team"]["member"] is None
    assert pc.get("/api/state", headers=sender_headers).json()["team"]["members"] == [sender, recipient]
    assert phone.post("/api/team/members", json={"name": "小周"}, headers=recipient_headers).json() == recipient
    assert pc.post("/api/team/members", json={"name": "小周"}, headers={**sender_headers, "X-Team-Key": str(uuid4())}).status_code == 409
    payload = {"operation_id": str(uuid4()), "recipient_id": recipient["id"], "card_version": 1,
               "previous_handoff_id": None}
    assert pc.post("/api/team/handoffs", json=payload, headers={**sender_headers, "X-Workspace-Id": "other"}).status_code == 409
    assert pc.post("/api/team/handoffs", json={**payload, "recipient_id": sender["id"]}, headers=sender_headers).status_code == 422
    published = pc.post("/api/team/handoffs", json=payload, headers=sender_headers)
    assert published.status_code == 200
    assert pc.post("/api/team/handoffs", json=payload, headers=sender_headers).json() == published.json()
    assert pc.post("/api/team/handoffs", json={**payload, "operation_id": str(uuid4())}, headers=sender_headers).status_code == 409
    assert pc.put("/api/card", json=card(1, goal="后来更新的目标")).status_code == 200

    path = f"/api/team/handoffs/{published.json()['id']}/accept"
    accept = {"operation_id": str(uuid4())}
    assert pc.post(path, json=accept, headers=sender_headers).status_code == 403
    phone_team = phone.get("/api/state", headers=recipient_headers).json()["team"]
    assert phone_team["member"] == recipient
    assert phone_team["handoff"]["accepted_at"] is None
    assert phone_team["published_card"]["goal"] == "完成登录模块"
    accepted = phone.post(path, json=accept, headers=recipient_headers)
    assert accepted.status_code == 200
    restarted = TestClient(create_app(folder, CODE), headers={"X-Access-Code": CODE})
    assert restarted.post(path, json=accept, headers=recipient_headers).json() == accepted.json()
    result = restarted.get("/api/state", headers=sender_headers).json()
    assert result["card"]["version"] == 2
    assert result["team"]["handoff"]["accepted_at"] == accepted.json()["accepted_at"]
    assert [event["kind"] for event in result["events"]].count("handoff_accepted") == 1

    next_payload = {**payload, "operation_id": str(uuid4()), "previous_handoff_id": published.json()["id"]}
    assert pc.post("/api/team/handoffs", json=next_payload, headers=sender_headers).status_code == 409
    next_payload["card_version"] = 2
    assert pc.post("/api/team/handoffs", json=next_payload, headers=sender_headers).status_code == 200


def test_concurrent_team_publishes_and_accepts_have_one_winner(env):
    _, pc, phone = env
    assert pc.put("/api/card", json=card()).status_code == 200
    sender_headers, _ = join_team(pc, "小林")
    recipient_headers, recipient = join_team(phone, "小周")

    def publish(_):
        return pc.post("/api/team/handoffs", headers=sender_headers, json={
            "operation_id": str(uuid4()), "recipient_id": recipient["id"], "card_version": 1,
            "previous_handoff_id": None,
        })

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, range(2)))
    assert sorted(result.status_code for result in results) == [200, 409]
    path = f"/api/team/handoffs/{next(result.json()['id'] for result in results if result.is_success)}/accept"

    def accept(_):
        return phone.post(path, headers=recipient_headers, json={"operation_id": str(uuid4())}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(accept, range(2))) == [200, 409]


def test_retries_are_deduplicated_even_after_restart(env):
    folder, pc, phone = env
    payload = card()
    assert pc.put("/api/card", json=payload).json() == pc.put("/api/card", json=payload).json()
    n = {"operation_id": str(uuid4()), "device": "手机", "text": "同一条想法"}
    first = phone.post("/api/notes", json=n).json()
    restarted = TestClient(create_app(folder, CODE), headers={"X-Access-Code": CODE})
    assert restarted.post("/api/notes", json=n).json() == first
    assert len(pc.get("/api/state").json()["notes"]) == 1
    assert phone.post("/api/notes", json={**n, "text": "不同内容"}).status_code == 409


def test_queued_note_cannot_sync_into_replacement_workspace(env, tmp_path):
    folder, pc, phone = env
    original = phone.get("/api/state").json()["workspace_id"]
    restarted = TestClient(create_app(folder, CODE), headers={"X-Access-Code": CODE})
    assert restarted.get("/api/state").json()["workspace_id"] == original
    replacement = TestClient(create_app(tmp_path / "replacement", CODE), headers={"X-Access-Code": CODE})
    replacement.put("/api/card", json=card())
    note = {"operation_id": str(uuid4()), "device": "手机", "text": "离线保存的想法"}
    assert replacement.post("/api/notes", json=note, headers={"X-Workspace-Id": original}).status_code == 409
    assert replacement.get("/api/state").json()["notes"] == []
    pc.put("/api/card", json=card())
    assert phone.post("/api/notes", json=note, headers={"X-Workspace-Id": original}).status_code == 200


def test_new_note_during_resume_stays_pending(env):
    _, pc, phone = env
    pc.put("/api/card", json=card())
    ids = [phone.post("/api/notes", json={"operation_id": str(uuid4()), "device": "手机", "text": str(i)}).json()["id"] for i in range(2)]
    pc.post("/api/resume", json={"operation_id": str(uuid4()), "device": "电脑", "note_ids": ids[:1]})
    notes = pc.get("/api/state").json()["notes"]
    assert notes[0]["reviewed_at"] is None
    assert notes[1]["reviewed_at"] is not None


def test_access_validation_and_no_spurious_records(env):
    _, pc, _ = env
    anonymous = TestClient(pc.app)
    assert anonymous.get("/api/state").status_code == 401
    assert anonymous.put("/api/card", json=card()).status_code == 401
    assert anonymous.get("/api/history/1").status_code == 401
    assert anonymous.post("/api/ai/draft", json={"text": "下一步：测试"}).status_code == 401
    assert anonymous.get("/data/access-code.txt").status_code == 404
    assert pc.post("/api/notes", json={"operation_id": str(uuid4()), "device": "手机", "text": "还未建卡"}).status_code == 409
    assert pc.put("/api/card", json=card(goal="  ")).status_code == 422
    assert pc.put("/api/card", json=card(goal="a" * 6001)).status_code == 422
    assert pc.get("/api/state").json()["events"] == []


def test_draft_preview_only_syncs_after_explicit_save(env):
    _, pc, phone = env
    assert pc.put("/api/card", json=card()).status_code == 200
    before = phone.get("/api/state").json()
    discussion = "当前目标：完善登录流程\n已完成：登录页\n下一步：测试密码错误提示"

    assert pc.post("/api/ai/draft", json={"text": "  "}).status_code == 422
    response = pc.post("/api/ai/draft", json={"text": discussion})
    assert response.status_code == 200
    result = response.json()
    assert result["mode"] == "demo"
    assert result["draft"]["next_steps"] == "测试密码错误提示"
    assert result["draft"]["open_questions"] == ""
    assert result["draft"]["evidence"] == discussion
    after = phone.get("/api/state").json()
    assert after["card"] == before["card"]
    assert after["events"] == before["events"]

    assert pc.put("/api/card", json=card(1, **result["draft"])).status_code == 200
    synced = phone.get("/api/state").json()["card"]
    assert synced["version"] == 2
    assert synced["next_steps"] == "测试密码错误提示"


def test_draft_timeout_and_invalid_model_response_leave_card_untouched(env, monkeypatch):
    _, pc, phone = env
    pc.put("/api/card", json=card())
    before = phone.get("/api/state").json()["card"]

    def timeout(_):
        raise TimeoutError

    monkeypatch.setattr("server.generate_draft", timeout)
    assert pc.post("/api/ai/draft", json={"text": "讨论记录"}).status_code == 504
    monkeypatch.setattr("server.generate_draft", lambda _: {"mode": "model", "draft": {"goal": ["错误类型"]}})
    assert pc.post("/api/ai/draft", json={"text": "讨论记录"}).status_code == 502
    assert phone.get("/api/state").json()["card"] == before


def test_simultaneous_card_updates_have_one_winner(env):
    _, pc, phone = env
    pc.put("/api/card", json=card())
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(client.put, "/api/card", json=card(1, goal=goal)) for client, goal in [(pc, "甲"), (phone, "乙")]]
        statuses = sorted(job.result().status_code for job in jobs)
    assert statuses == [200, 409]
    assert pc.get("/api/state").json()["card"]["version"] == 2


def test_files_transfer_in_both_directions_and_persist(env):
    folder, pc, phone = env
    binary = b"\x00\xff\x0d\x0a"
    body_headers = {"Content-Type": "application/octet-stream"}
    upload = {"operation_id": str(uuid4()), "name": "课堂资料 & 笔记.pdf", "device": "电脑"}
    sent = pc.post("/api/files", params=upload, content=binary, headers=body_headers)
    assert sent.status_code == 200
    assert pc.post("/api/files", params=upload, content=binary, headers=body_headers).json() == sent.json()
    assert pc.post("/api/files", params=upload, content=b"different", headers=body_headers).status_code == 409
    assert phone.get("/api/state").json()["files"] == [sent.json()]
    path = f"/api/files/{sent.json()['id']}"
    assert TestClient(pc.app).get(path).status_code == 401
    received = phone.get(path)
    assert received.content == binary
    assert received.headers["content-type"] == "application/octet-stream"
    assert received.headers["content-disposition"].startswith("attachment; filename*=UTF-8''%E8%AF%BE")

    upload = {"operation_id": str(uuid4()), "name": "手机笔记.txt", "device": "手机"}
    reply = phone.post("/api/files", params=upload, content=b"from phone", headers=body_headers).json()
    restarted = TestClient(create_app(folder, CODE), headers={"X-Access-Code": CODE})
    assert restarted.get("/api/state").json()["files"] == [reply, sent.json()]
    assert restarted.get(f"/api/files/{reply['id']}").content == b"from phone"


def test_file_limits_and_access_do_not_create_records(env, monkeypatch):
    _, pc, _ = env
    monkeypatch.setattr("server.MAX_FILE_BYTES", 8)
    upload = {"operation_id": str(uuid4()), "name": "测试.txt", "device": "电脑"}
    headers = {"Content-Type": "application/octet-stream"}
    assert TestClient(pc.app).post("/api/files", params=upload, content=b"123", headers=headers).status_code == 401
    assert pc.post("/api/files", params=upload, content=b"123456789", headers=headers).status_code == 413
    assert pc.post("/api/files", params=upload, content=iter([b"12345678", b"9"]), headers=headers).status_code == 413
    assert pc.post("/api/files", params={**upload, "name": "../test.txt"}, content=b"123", headers=headers).status_code == 422
    assert pc.post("/api/files", params=upload, content=b"123", headers={**headers, "X-Workspace-Id": "old"}).status_code == 409
    assert pc.get("/api/state").json()["files"] == []
    assert pc.get("/api/files/1").status_code == 404
