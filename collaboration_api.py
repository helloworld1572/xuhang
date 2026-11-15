"""Team collaboration. Additive schema; legacy workspace is never implicitly shared."""
from __future__ import annotations

import json
import secrets
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from urllib.parse import quote

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=6000)]
Key = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return sha256(value.encode()).hexdigest()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Operation(Model):
    operation_id: UUID


class Register(Model):
    name: Name
    key: Key
    invitation: str = Field(default="", max_length=128)
    access_code: str = Field(default="", max_length=128)


class TeamInput(Operation):
    name: Name
    description: Text = ""


class InviteInput(Operation):
    code: Key


class JoinInput(Operation):
    code: Key


class ProjectData(Model):
    name: Name
    goal: Text = ""
    owner_id: int = Field(gt=0)
    start_date: date | None = None
    due_date: date | None = None
    status: Literal["active", "paused", "completed"] = "active"

    @model_validator(mode="after")
    def dates(self):
        if self.start_date and self.due_date and self.start_date > self.due_date:
            raise ValueError("截止日期不能早于开始日期")
        return self


class ProjectInput(Operation, ProjectData):
    version: int = Field(default=0, ge=0)


class StageData(Model):
    title: Name
    goal: Text = ""
    criteria: Text = ""
    position: int = Field(default=0, ge=0, le=10000)
    due_date: date | None = None


class TaskData(Model):
    title: Name
    description: Text = ""
    criteria: Text = ""
    stage_id: int | None = Field(default=None, gt=0)
    assignee_id: int | None = Field(default=None, gt=0)
    due_date: date | None = None
    status: Literal["todo", "doing", "blocked", "done"] = "todo"
    result: Text = ""
    blocked_reason: Text = ""
    file_ids: list[int] = Field(default_factory=list, max_length=20)


class CardData(Model):
    title: Name
    goal: Text = ""
    done: Text = ""
    open_questions: Text = ""
    next_steps: Text = ""
    evidence: Text = ""


class NoteData(Model):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=6000)]
    task_id: int | None = Field(default=None, gt=0)


class HandoffData(Model):
    recipient_id: int = Field(gt=0)
    card_id: int = Field(gt=0)
    card_version: int = Field(gt=0)


class EntityInput(Operation):
    version: int = Field(default=0, ge=0)
    data: dict


class ImportInput(Operation):
    access_code: str = Field(max_length=128)
    card_version: int = Field(gt=0)


class AISuggestion(Model):
    """Contract only. AI output is not an authorized write operation."""
    project_id: int
    based_on_revision: int
    kind: Literal["summary", "create_task", "assign_task", "update_plan"]
    explanation: Text
    references: list[str] = Field(max_length=20)
    proposed_changes: dict
    status: Literal["pending_confirmation"] = "pending_confirmation"


SCHEMAS = {"stage": StageData, "task": TaskData, "card": CardData, "note": NoteData, "handoff": HandoffData}


def install_collaboration(app, connect, access_code):
    with connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS collab_schema(version INTEGER PRIMARY KEY, installed_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS collab_users(id INTEGER PRIMARY KEY, name TEXT NOT NULL, key_hash TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS collab_teams(id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS collab_members(team_id INTEGER NOT NULL, user_id INTEGER NOT NULL, role TEXT NOT NULL, PRIMARY KEY(team_id,user_id));
        CREATE TABLE IF NOT EXISTS collab_invites(id INTEGER PRIMARY KEY, team_id INTEGER NOT NULL, code_hash TEXT UNIQUE NOT NULL, created_by INTEGER NOT NULL, expires_at TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0, used_by INTEGER);
        CREATE TABLE IF NOT EXISTS collab_projects(id INTEGER PRIMARY KEY, team_id INTEGER NOT NULL, payload TEXT NOT NULL, version INTEGER NOT NULL, revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS collab_entities(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, version INTEGER NOT NULL, created_by INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS collab_entities_project ON collab_entities(project_id,kind,id);
        CREATE TABLE IF NOT EXISTS collab_files(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, name TEXT NOT NULL, content BLOB NOT NULL, digest TEXT NOT NULL, created_by INTEGER NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS collab_events(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, actor_id INTEGER NOT NULL, kind TEXT NOT NULL, entity_id INTEGER, before_json TEXT, after_json TEXT, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS collab_events_project ON collab_events(project_id,id);
        CREATE TABLE IF NOT EXISTS collab_operations(user_id INTEGER NOT NULL, operation_id TEXT NOT NULL, request_hash TEXT NOT NULL, response TEXT NOT NULL, PRIMARY KEY(user_id,operation_id));
        """)
        con.execute("INSERT OR IGNORE INTO collab_schema VALUES(1,?)", (stamp(),))

    router = APIRouter(prefix="/api/collab")

    def current_user(x_member_key: str = Header(default="")):
        with connect() as con:
            row = con.execute("SELECT id,name FROM collab_users WHERE key_hash=?", (digest(x_member_key),)).fetchone()
        if row is None:
            raise HTTPException(401, "请使用个人登录密钥登录团队工作台。")
        return dict(row)

    def membership(con, team_id, user, admin=False):
        row = con.execute("SELECT role FROM collab_members WHERE team_id=? AND user_id=?", (team_id, user["id"])).fetchone()
        if row is None or (admin and row["role"] != "admin"):
            raise HTTPException(403, "没有该团队的访问权限或管理权限。")
        return row["role"]

    def project(con, pid, user, manage=False):
        row = con.execute("SELECT * FROM collab_projects WHERE id=?", (pid,)).fetchone()
        if row is None:
            raise HTTPException(404, "项目不存在。")
        role = membership(con, row["team_id"], user)
        data = unpack(row)
        data["can_manage"] = role == "admin" or data["owner_id"] == user["id"]
        if manage and not data["can_manage"]:
            raise HTTPException(403, "仅项目负责人或团队管理员可以操作。")
        return data

    def unpack(row):
        d = dict(row)
        if "payload" in d:
            d.update(json.loads(d.pop("payload")))
        return d

    def entity(con, pid, eid, kind=None):
        row = con.execute("SELECT * FROM collab_entities WHERE project_id=? AND id=?", (pid, eid)).fetchone()
        if row is None or (kind and row["kind"] != kind):
            raise HTTPException(422, "关联记录不属于当前项目。")
        return unpack(row)

    def member_exists(con, team_id, uid):
        if uid is not None and not con.execute("SELECT 1 FROM collab_members WHERE team_id=? AND user_id=?", (team_id, uid)).fetchone():
            raise HTTPException(422, "负责人或接手人必须属于当前团队。")

    def mutate(con, user, body, path, action, extra=""):
        fingerprint = digest(path + body.model_dump_json() + extra)
        prior = con.execute("SELECT * FROM collab_operations WHERE user_id=? AND operation_id=?", (user["id"], str(body.operation_id))).fetchone()
        if prior:
            if prior["request_hash"] != fingerprint:
                raise HTTPException(409, "操作编号已用于其他内容，请重新发起。")
            return json.loads(prior["response"])
        result = action()
        con.execute("INSERT INTO collab_operations VALUES(?,?,?,?)", (user["id"], str(body.operation_id), fingerprint, json.dumps(result, ensure_ascii=False)))
        return result

    def event(con, pid, user, kind, eid=None, before=None, after=None):
        con.execute("INSERT INTO collab_events(project_id,actor_id,kind,entity_id,before_json,after_json,created_at) VALUES(?,?,?,?,?,?,?)", (pid, user["id"], kind, eid, json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False), stamp()))
        con.execute("UPDATE collab_projects SET revision=revision+1,updated_at=? WHERE id=?", (stamp(), pid))

    def invitation(con, code):
        row = con.execute("SELECT * FROM collab_invites WHERE code_hash=?", (digest(code),)).fetchone()
        if row is None or row["revoked"] or row["used_by"] is not None or row["expires_at"] < stamp():
            raise HTTPException(400, "邀请码无效、已使用、已撤销或已过期。")
        return row

    @router.post("/register")
    def register(body: Register):
        with connect() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute("SELECT id,name FROM collab_users WHERE key_hash=?", (digest(body.key),)).fetchone()
            if existing:
                if existing["name"] != body.name:
                    raise HTTPException(409, "此登录密钥已注册。")
                return dict(existing)
            invite = invitation(con, body.invitation) if body.invitation else None
            if invite is None and not secrets.compare_digest(body.access_code.encode(), access_code.encode()):
                raise HTTPException(403, "首次注册需要服务访问码或有效团队邀请码。")
            uid = con.execute("INSERT INTO collab_users(name,key_hash,created_at) VALUES(?,?,?)", (body.name, digest(body.key), stamp())).lastrowid
            if invite:
                con.execute("INSERT INTO collab_members VALUES(?,?, 'member')", (invite["team_id"], uid))
                con.execute("UPDATE collab_invites SET used_by=? WHERE id=?", (uid, invite["id"]))
            return {"id": uid, "name": body.name}

    @router.get("/me")
    def me(user=Depends(current_user)):
        with connect() as con:
            teams = [dict(r) for r in con.execute("SELECT t.*,m.role FROM collab_teams t JOIN collab_members m ON m.team_id=t.id WHERE m.user_id=? ORDER BY t.id", (user["id"],))]
        return {"user": user, "teams": teams}

    @router.post("/teams")
    def create_team(body: TeamInput, user=Depends(current_user)):
        with connect() as con:
            con.execute("BEGIN IMMEDIATE")
            def action():
                tid = con.execute("INSERT INTO collab_teams(name,description,created_at) VALUES(?,?,?)", (body.name, body.description, stamp())).lastrowid
                con.execute("INSERT INTO collab_members VALUES(?,?,'admin')", (tid, user["id"]))
                return {"id": tid}
            return mutate(con, user, body, "teams", action)

    @router.get("/teams/{tid}")
    def team(tid: int, user=Depends(current_user)):
        with connect() as con:
            con.execute("BEGIN")
            role = membership(con, tid, user)
            members = [dict(r) for r in con.execute("SELECT u.id,u.name,m.role FROM collab_users u JOIN collab_members m ON m.user_id=u.id WHERE m.team_id=? ORDER BY u.id", (tid,))]
            projects = [unpack(r) for r in con.execute("SELECT * FROM collab_projects WHERE team_id=? ORDER BY id", (tid,))]
            invites = [dict(r) for r in con.execute("SELECT id,expires_at,revoked,used_by FROM collab_invites WHERE team_id=? ORDER BY id DESC LIMIT 50", (tid,))] if role == "admin" else []
            return {"members": members, "projects": projects, "invites": invites}

    @router.post("/teams/{tid}/invites")
    def create_invite(tid: int, body: InviteInput, user=Depends(current_user)):
        with connect() as con:
            con.execute("BEGIN IMMEDIATE")
            membership(con, tid, user, True)
            def action():
                expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
                iid = con.execute("INSERT INTO collab_invites(team_id,code_hash,created_by,expires_at) VALUES(?,?,?,?)", (tid, digest(body.code), user["id"], expires)).lastrowid
                return {"id": iid, "expires_at": expires}
            return mutate(con, user, body, f"invite:{tid}", action)

    @router.post("/teams/{tid}/invites/{iid}/revoke")
    def revoke(tid: int, iid: int, body: Operation, user=Depends(current_user)):
        with connect() as con:
            con.execute("BEGIN IMMEDIATE")
            membership(con, tid, user, True)
            def action():
                if not con.execute("UPDATE collab_invites SET revoked=1 WHERE id=? AND team_id=?", (iid, tid)).rowcount:
                    raise HTTPException(404, "邀请不存在。")
                return {"ok": True}
            return mutate(con, user, body, f"revoke:{tid}:{iid}", action)

    @router.post("/join")
    def join(body: JoinInput, user=Depends(current_user)):
        with connect() as con:
            con.execute("BEGIN IMMEDIATE")
            def action():
                invite = invitation(con, body.code)
                con.execute("INSERT OR IGNORE INTO collab_members VALUES(?,?,'member')", (invite["team_id"], user["id"]))
                con.execute("UPDATE collab_invites SET used_by=? WHERE id=?", (user["id"], invite["id"]))
                return {"team_id": invite["team_id"]}
            return mutate(con, user, body, "join", action)

    @router.post("/teams/{tid}/projects")
    def create_project(tid: int, body: ProjectInput, user=Depends(current_user)):
        with connect() as con:
            con.execute("BEGIN IMMEDIATE")
            membership(con, tid, user)
            member_exists(con, tid, body.owner_id)
            def action():
                payload = body.model_dump_json(exclude={"operation_id", "version"})
                pid = con.execute("INSERT INTO collab_projects(team_id,payload,version,created_at,updated_at) VALUES(?,?,1,?,?)", (tid, payload, stamp(), stamp())).lastrowid
                event(con, pid, user, "project_created", after=json.loads(payload))
                return {"id": pid}
            return mutate(con, user, body, f"project:{tid}", action)

    @router.put("/projects/{pid}")
    def update_project(pid: int, body: ProjectInput, user=Depends(current_user)):
        with connect() as con:
            con.execute("BEGIN IMMEDIATE")
            old = project(con, pid, user, True)
            member_exists(con, old["team_id"], body.owner_id)
            def action():
                if body.version != old["version"]:
                    raise HTTPException(409, "项目已更新，请查看最新版后重新编辑。")
                data = body.model_dump(mode="json", exclude={"operation_id", "version"})
                con.execute("UPDATE collab_projects SET payload=?,version=version+1 WHERE id=?", (json.dumps(data), pid))
                event(con, pid, user, "project_updated", before=old, after=data)
                return {"id": pid, "version": body.version + 1}
            return mutate(con, user, body, f"project-update:{pid}", action)

    def add_entity(con, pid, kind, data, user):
        eid = con.execute("INSERT INTO collab_entities(project_id,kind,payload,version,created_by,created_at,updated_at) VALUES(?,?,?,1,?,?,?)", (pid, kind, json.dumps(data, ensure_ascii=False), user["id"], stamp(), stamp())).lastrowid
        event(con, pid, user, kind + "_created", eid, after=data)
        return {"id": eid, "version": 1}

    def validate_data(con, p, kind, raw):
        from pydantic import ValidationError
        if kind not in SCHEMAS:
            raise HTTPException(404, "不支持的记录类型。")
        try:
            data = SCHEMAS[kind].model_validate(raw).model_dump(mode="json")
        except ValidationError:
            raise HTTPException(422, "请检查必填项、日期、字段类型和内容长度。") from None
        if kind == "task":
            member_exists(con, p["team_id"], data["assignee_id"])
            if data["stage_id"]:
                entity(con, p["id"], data["stage_id"], "stage")
            for fid in data["file_ids"]:
                if not con.execute("SELECT 1 FROM collab_files WHERE id=? AND project_id=?", (fid, p["id"])).fetchone():
                    raise HTTPException(422, "附件不属于当前项目。")
        if kind == "note" and data["task_id"]:
            entity(con, p["id"], data["task_id"], "task")
        if kind == "handoff":
            member_exists(con, p["team_id"], data["recipient_id"])
            card = entity(con, p["id"], data["card_id"], "card")
            if card["version"] != data["card_version"]:
                raise HTTPException(409, "接续卡已更新，请重新查看再交接。")
            data.update(card_snapshot=card, accepted_at=None)
        return data

    @router.post("/projects/{pid}/records/{kind}")
    def create_entity(pid: int, kind: str, body: EntityInput, user=Depends(current_user)):
        with connect() as con:
            con.execute("BEGIN IMMEDIATE")
            p = project(con, pid, user, kind == "stage")
            def action():
                data = validate_data(con, p, kind, body.data)
                if kind == "task":
                    if not p["can_manage"] and data["assignee_id"] not in (None, user["id"]):
                        raise HTTPException(403, "仅负责人或管理员可以给其他成员分配任务。")
                    data.update(completed_by=user["id"] if data["status"] == "done" else None, completed_at=stamp() if data["status"] == "done" else None)
                return add_entity(con, pid, kind, data, user)
            return mutate(con, user, body, f"create:{pid}:{kind}", action)

    @router.put("/projects/{pid}/records/{eid}")
    def update_entity(pid: int, eid: int, body: EntityInput, user=Depends(current_user)):
        with connect() as con:
            con.execute("BEGIN IMMEDIATE")
            p = project(con, pid, user)
            old = entity(con, pid, eid)
            if old["kind"] not in ("task", "stage", "card"):
                raise HTTPException(400, "此记录只能追加，不能改写。")
            allowed = p["can_manage"] or (old["kind"] == "task" and old["assignee_id"] == user["id"]) or (old["kind"] == "card" and old["created_by"] == user["id"])
            if not allowed:
                raise HTTPException(403, "没有修改这条记录的权限。")
            def action():
                if old["version"] != body.version:
                    raise HTTPException(409, "内容已被另一位成员修改。输入已保留，请对照最新版后重新编辑。")
                data = validate_data(con, p, old["kind"], body.data)
                if old["kind"] == "task":
                    if not p["can_manage"]:
                        fixed = ("title", "description", "criteria", "stage_id", "assignee_id", "due_date")
                        if any(data[k] != old[k] for k in fixed):
                            raise HTTPException(403, "任务负责人可更新状态和结果；计划与分配由项目负责人管理。")
                    done = data["status"] == "done"
                    data["completed_by"] = (old["completed_by"] or user["id"]) if done else None
                    data["completed_at"] = (old["completed_at"] or stamp()) if done else None
                con.execute("UPDATE collab_entities SET payload=?,version=version+1,updated_at=? WHERE id=?", (json.dumps(data, ensure_ascii=False), stamp(), eid))
                action_kind = old["kind"] + "_updated"
                if old["kind"] == "task" and old["status"] != data["status"]:
                    action_kind = "task_completed" if data["status"] == "done" else ("task_reopened" if old["status"] == "done" else "task_status_changed")
                event(con, pid, user, action_kind, eid, old, data)
                return {"id": eid, "version": old["version"] + 1}
            return mutate(con, user, body, f"update:{pid}:{eid}", action)

    @router.post("/projects/{pid}/handoffs/{eid}/accept")
    def accept(pid: int, eid: int, body: Operation, user=Depends(current_user)):
        with connect() as con:
            con.execute("BEGIN IMMEDIATE")
            project(con, pid, user)
            old = entity(con, pid, eid, "handoff")
            if old["recipient_id"] != user["id"]:
                raise HTTPException(403, "只有指定接手人可以确认。")
            def action():
                if old["accepted_at"]:
                    raise HTTPException(409, "已确认接手。")
                data = {k: old[k] for k in ("recipient_id", "card_id", "card_version", "card_snapshot")}
                data["accepted_at"] = stamp()
                con.execute("UPDATE collab_entities SET payload=?,version=version+1,updated_at=? WHERE id=?", (json.dumps(data), stamp(), eid))
                event(con, pid, user, "handoff_accepted", eid, old, data)
                return {"id": eid}
            return mutate(con, user, body, f"accept:{pid}:{eid}", action)

    @router.post("/projects/{pid}/import-card")
    def import_card(pid: int, body: ImportInput, user=Depends(current_user)):
        with connect() as con:
            con.execute("BEGIN IMMEDIATE")
            project(con, pid, user, True)
            if not secrets.compare_digest(body.access_code.encode(), access_code.encode()):
                raise HTTPException(403, "导入旧资料需要原工作空间访问码。")
            def action():
                row = con.execute("SELECT payload FROM cards WHERE version=?", (body.card_version,)).fetchone()
                if not row:
                    raise HTTPException(404, "旧接续卡版本不存在。")
                data = json.loads(row[0])
                data["title"] = data.pop("project")
                data["evidence"] = (data.get("evidence", "") + f"\n导入自旧工作空间接续卡 v{body.card_version}")[:6000]
                return add_entity(con, pid, "card", CardData.model_validate(data).model_dump(), user)
            return mutate(con, user, body, f"import:{pid}", action)

    @router.post("/projects/{pid}/files")
    async def upload(pid: int, request: Request, operation_id: UUID, name: str, user=Depends(current_user)):
        if not name.strip() or len(name) > 255 or any(c in '/\\' or ord(c) < 32 for c in name):
            raise HTTPException(422, "文件名无效。")
        with connect() as con:
            project(con, pid, user)
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 20 * 1024 * 1024:
                raise HTTPException(413, "单个文件最多 20 MB。")
        with connect() as con:
            con.execute("BEGIN IMMEDIATE")
            project(con, pid, user)
            def action():
                fid = con.execute("INSERT INTO collab_files(project_id,name,content,digest,created_by,created_at) VALUES(?,?,?,?,?,?)", (pid, name, bytes(content), sha256(content).hexdigest(), user["id"], stamp())).lastrowid
                event(con, pid, user, "file_uploaded", fid, after={"name": name})
                return {"id": fid}
            return mutate(con, user, Operation(operation_id=operation_id), f"file:{pid}:{name}", action, sha256(content).hexdigest())

    @router.get("/projects/{pid}/files/{fid}")
    def download(pid: int, fid: int, user=Depends(current_user)):
        with connect() as con:
            project(con, pid, user)
            row = con.execute("SELECT name,content FROM collab_files WHERE project_id=? AND id=?", (pid, fid)).fetchone()
            if not row:
                raise HTTPException(404, "文件不存在。")
            return Response(row["content"], media_type="application/octet-stream", headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(row["name"], safe="")})

    def snapshot(con, pid, user):
        p = project(con, pid, user)
        records = [unpack(r) for r in con.execute("SELECT * FROM collab_entities WHERE project_id=? ORDER BY id", (pid,))]
        tasks = [r for r in records if r["kind"] == "task"]
        total, done = len(tasks), sum(t["status"] == "done" for t in tasks)
        today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        progress = {"total": total, "done": done, "percent": round(done / total * 100) if total else None,
                    "blocked": sum(t["status"] == "blocked" for t in tasks),
                    "overdue": sum(bool(t["due_date"] and t["due_date"] < today and t["status"] != "done") for t in tasks)}
        events = [dict(r) for r in con.execute("SELECT e.*,u.name AS actor_name FROM collab_events e JOIN collab_users u ON u.id=e.actor_id WHERE project_id=? ORDER BY e.id DESC LIMIT 60", (pid,))]
        for e in events:
            e["before"] = json.loads(e.pop("before_json"))
            e["after"] = json.loads(e.pop("after_json"))
        files = [dict(r) for r in con.execute("SELECT id,name,length(content) AS size,digest,created_by,created_at FROM collab_files WHERE project_id=? ORDER BY id DESC", (pid,))]
        return {"project": p, "records": records, "progress": progress, "events": events, "files": files, "server_time": stamp()}

    @router.get("/projects/{pid}")
    def state(pid: int, user=Depends(current_user)):
        with connect() as con:
            con.execute("BEGIN")
            return snapshot(con, pid, user)

    @router.get("/projects/{pid}/context")
    def context(pid: int, user=Depends(current_user)):
        with connect() as con:
            con.execute("BEGIN")
            p = project(con, pid, user)
            # Bound both record count and text volume. No attachment body or credential is returned.
            sources, used, truncated = [], 0, False
            rows = con.execute("SELECT * FROM collab_entities WHERE project_id=? ORDER BY CASE kind WHEN 'stage' THEN 0 WHEN 'task' THEN 1 ELSE 2 END,id LIMIT 101", (pid,)).fetchall()
            for row in rows[:100]:
                value = unpack(row)
                length = len(json.dumps(value, ensure_ascii=False))
                if used + length > 50000:
                    truncated = True
                    break
                used += length
                sources.append({"source_id": f"record:{value['id']}:v{value['version']}", "record": value})
            members = [dict(r) for r in con.execute("SELECT u.id,u.name,m.role FROM collab_members m JOIN collab_users u ON u.id=m.user_id WHERE team_id=? LIMIT 101", (p["team_id"],))]
            files = [dict(r) for r in con.execute("SELECT id,name,digest,created_by,created_at FROM collab_files WHERE project_id=? ORDER BY id DESC LIMIT 21", (pid,))]
            recent = [dict(r) for r in con.execute("SELECT id,actor_id,kind,entity_id,created_at FROM collab_events WHERE project_id=? ORDER BY id DESC LIMIT 20", (pid,))]
            counts = {r["status"]: r["n"] for r in con.execute("SELECT json_extract(payload,'$.status') AS status,count(*) AS n FROM collab_entities WHERE project_id=? AND kind='task' GROUP BY status", (pid,))}
            return {"project": p, "based_on_revision": p["revision"], "task_counts": counts, "members": members[:100], "sources": sources, "files": files[:20], "recent_changes": recent,
                    "truncated": truncated or len(rows) > 100 or len(members) > 100 or len(files) > 20,
                    "coverage": "至多100条记录、50000字符、100位成员、20份附件元数据和最近20条变更；不读取附件正文。", "mode": "facts_only"}

    @router.get("/ai/suggestion-schema")
    def suggestion_schema(user=Depends(current_user)):
        return AISuggestion.model_json_schema()

    @router.get("/projects/{pid}/records/{eid}/history")
    def record_history(pid: int, eid: int, before: int | None = None, user=Depends(current_user)):
        with connect() as con:
            con.execute("BEGIN")
            project(con, pid, user)
            item = entity(con, pid, eid)
            rows = [dict(r) for r in con.execute("SELECT e.*,u.name AS actor_name FROM collab_events e JOIN collab_users u ON u.id=e.actor_id WHERE e.project_id=? AND e.entity_id=? AND e.kind LIKE ? AND (? IS NULL OR e.id<?) ORDER BY e.id DESC LIMIT 51", (pid, eid, item['kind']+'_%', before, before))]
            result = rows[:50]
            for row in result:
                row['before'] = json.loads(row.pop('before_json'))
                row['after'] = json.loads(row.pop('after_json'))
            return {'events': result, 'next_before': result[-1]['id'] if len(rows)>50 else None}

    app.include_router(router)
