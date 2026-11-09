"""Local Wi-Fi handoff API. Run with start.py; data stays under data/."""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints, ValidationError
from starlette.concurrency import run_in_threadpool

from ai_draft import DraftRequest, DraftResult, DraftUnavailableError, generate_draft
from network import lan_addresses

ROOT = Path(__file__).resolve().parent
MAX_FILE_BYTES = 20 * 1024 * 1024
FILE_METADATA = "id, name, size, device, created_at"
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=6000)]
Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


def now():
    return datetime.now(timezone.utc).isoformat()


class CardInput(BaseModel):
    version: int = Field(ge=0)
    operation_id: UUID
    device: Short
    project: Short
    goal: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=6000)]
    done: Text = ""
    open_questions: Text = ""
    next_steps: Text = ""
    evidence: Text = ""


class NoteInput(BaseModel):
    operation_id: UUID
    device: Short
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=6000)]


class ResumeInput(BaseModel):
    operation_id: UUID
    device: Short
    note_ids: list[int] = Field(max_length=1000)


class TeamMemberInput(BaseModel):
    name: Short


class TeamHandoffInput(BaseModel):
    operation_id: UUID
    recipient_id: int = Field(gt=0)
    card_version: int = Field(gt=0)
    previous_handoff_id: int | None = Field(gt=0)


class AcceptHandoffInput(BaseModel):
    operation_id: UUID


def create_app(data_dir: Path | None = None, access_code: str | None = None):
    data_dir = data_dir or Path(os.environ.get("XUHANG_DATA_DIR", ROOT / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    code_path = data_dir / "access-code.txt"
    if access_code is None:
        if not code_path.exists():
            code_path.write_text(secrets.token_hex(6).upper(), encoding="utf-8")
        access_code = code_path.read_text(encoding="utf-8").strip()
    if not access_code:
        raise RuntimeError("访问码不能为空")
    db_path = data_dir / "xuhang.sqlite3"

    @contextmanager
    def connect():
        con = sqlite3.connect(db_path, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    with connect() as con:
        con.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS cards(version INTEGER PRIMARY KEY, payload TEXT NOT NULL,
          device TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL,
          device TEXT NOT NULL, created_at TEXT NOT NULL, reviewed_at TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
          device TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, request TEXT NOT NULL, response TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS workspace_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS project_files (
          id INTEGER PRIMARY KEY, operation_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
          size INTEGER NOT NULL, device TEXT NOT NULL, created_at TEXT NOT NULL,
          digest TEXT NOT NULL, content BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS team_members (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE, key_hash TEXT NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS team_handoffs (
          id INTEGER PRIMARY KEY, sender_id INTEGER NOT NULL, recipient_id INTEGER NOT NULL,
          card_version INTEGER NOT NULL, published_at TEXT NOT NULL, accepted_at TEXT);
        """)
        con.execute("INSERT OR IGNORE INTO workspace_metadata VALUES ('id', ?)", (secrets.token_hex(16),))
        workspace_id = con.execute("SELECT value FROM workspace_metadata WHERE key='id'").fetchone()[0]

    app = FastAPI(title="续航 · 局域网接续", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def response_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        return response

    def authorize(x_access_code: str = Header(default="")):
        if not secrets.compare_digest(x_access_code.encode(), access_code.encode()):
            raise HTTPException(401, "访问码不正确，请重新连接项目。")

    def decode_card(row):
        return {**json.loads(row["payload"]), "version": row["version"],
                "device": row["device"], "updated_at": row["created_at"]} if row else None

    def event(con, kind, device, detail, stamp):
        con.execute("INSERT INTO events(kind,device,detail,created_at) VALUES(?,?,?,?)", (kind, device, detail, stamp))

    def replay(con, body, kind):
        request = kind + ":" + body.model_dump_json()
        row = con.execute("SELECT request,response FROM operations WHERE id=?", (str(body.operation_id),)).fetchone()
        if row:
            if row["request"] != request:
                raise HTTPException(409, "这次提交的编号已被使用，请重新发起操作。")
            return request, json.loads(row["response"])
        return request, None

    def remember(con, body, request, response):
        con.execute("INSERT INTO operations VALUES(?,?,?)", (str(body.operation_id), request, json.dumps(response, ensure_ascii=False)))
        return response

    def team_member(con, key: UUID | None):
        if key is None:
            return None
        return con.execute("SELECT id, name FROM team_members WHERE key_hash=?",
                           (sha256(key.bytes).hexdigest(),)).fetchone()

    def require_team_member(con, key):
        member = team_member(con, key)
        if member is None:
            raise HTTPException(403, "请先在这台设备上加入团队交接。")
        return member

    def require_workspace(x_workspace_id: Annotated[str, Header()]):
        if x_workspace_id != workspace_id:
            raise HTTPException(409, "当前工作空间已更换，请重新连接后再操作。")

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": "0.1.0", "app": "xuhang"}

    @app.get("/api/state", dependencies=[Depends(authorize)])
    def state(x_team_key: UUID | None = Header(default=None)):
        with connect() as con:
            con.execute("BEGIN")
            member = team_member(con, x_team_key)
            handoff = con.execute("SELECT * FROM team_handoffs ORDER BY id DESC LIMIT 1").fetchone()
            latest_card = con.execute("SELECT * FROM cards ORDER BY version DESC LIMIT 1").fetchone()
            published_card = None
            if handoff is not None and handoff["card_version"] != latest_card["version"]:
                published_card = decode_card(con.execute("SELECT * FROM cards WHERE version=?", (handoff["card_version"],)).fetchone())
            return {
                "workspace_id": workspace_id,
                "card": decode_card(latest_card),
                "notes": [dict(r) for r in con.execute("SELECT * FROM notes ORDER BY id DESC")],
                "events": [dict(r) for r in con.execute("SELECT * FROM events ORDER BY id DESC LIMIT 60")],
                "files": [dict(r) for r in con.execute(f"SELECT {FILE_METADATA} FROM project_files ORDER BY id DESC")],
                "max_file_bytes": MAX_FILE_BYTES,
                "team": {
                    "member": dict(member) if member is not None else None,
                    "members": [dict(row) for row in con.execute("SELECT id, name FROM team_members ORDER BY id")],
                    "handoff": dict(handoff) if handoff is not None else None,
                    "published_card": published_card,
                },
                "server_time": now(),
            }

    @app.post("/api/team/members", dependencies=[Depends(authorize), Depends(require_workspace)])
    def join_team(body: TeamMemberInput, x_team_key: Annotated[UUID, Header()]):
        with connect() as con:
            con.execute("BEGIN IMMEDIATE")
            member = team_member(con, x_team_key)
            if member is not None:
                return dict(member)
            if con.execute("SELECT 1 FROM team_members WHERE name=?", (body.name,)).fetchone():
                raise HTTPException(409, "这个团队名称已被使用，请换一个名称。")
            cursor = con.execute("INSERT INTO team_members(name,key_hash) VALUES(?,?)",
                                 (body.name, sha256(x_team_key.bytes).hexdigest()))
            return {"id": cursor.lastrowid, "name": body.name}

    @app.post("/api/team/handoffs", dependencies=[Depends(authorize), Depends(require_workspace)])
    def publish_handoff(body: TeamHandoffInput, x_team_key: Annotated[UUID, Header()]):
        with connect() as con:
            con.execute("BEGIN IMMEDIATE")
            sender = require_team_member(con, x_team_key)
            req, previous = replay(con, body, f"team-publish:{sender['id']}")
            if previous is not None:
                return previous
            latest = con.execute("SELECT id, accepted_at FROM team_handoffs ORDER BY id DESC LIMIT 1").fetchone()
            if latest is not None and latest["accepted_at"] is None:
                raise HTTPException(409, "还有一项交接待确认，请刷新进度。")
            if (latest["id"] if latest is not None else None) != body.previous_handoff_id:
                raise HTTPException(409, "团队交接已更新，请查看最新记录后再发布。")
            if con.execute("SELECT MAX(version) FROM cards").fetchone()[0] != body.card_version:
                raise HTTPException(409, "接续卡已更新，请刷新并查看最新版本后再发布。")
            recipient = con.execute("SELECT name FROM team_members WHERE id=?", (body.recipient_id,)).fetchone()
            if recipient is None or body.recipient_id == sender["id"]:
                raise HTTPException(422, "请选择一位已加入的队友接手。")
            stamp = now()
            cursor = con.execute(
                "INSERT INTO team_handoffs(sender_id,recipient_id,card_version,published_at) VALUES(?,?,?,?)",
                (sender["id"], body.recipient_id, body.card_version, stamp),
            )
            event(con, "handoff_published", sender["name"], f"将接续卡 v{body.card_version} 交给 {recipient['name']}", stamp)
            return remember(con, body, req, {"id": cursor.lastrowid, "published_at": stamp})

    @app.post("/api/team/handoffs/{handoff_id}/accept", dependencies=[Depends(authorize), Depends(require_workspace)])
    def accept_handoff(handoff_id: int, body: AcceptHandoffInput, x_team_key: Annotated[UUID, Header()]):
        with connect() as con:
            con.execute("BEGIN IMMEDIATE")
            recipient = require_team_member(con, x_team_key)
            handoff = con.execute("SELECT * FROM team_handoffs WHERE id=?", (handoff_id,)).fetchone()
            if handoff is None:
                raise HTTPException(404, "找不到这项交接。")
            if handoff["recipient_id"] != recipient["id"]:
                raise HTTPException(403, "只有指定接手人可以确认这项交接。")
            req, previous = replay(con, body, f"team-accept:{recipient['id']}:{handoff_id}")
            if previous is not None:
                return previous
            if handoff["accepted_at"] is not None:
                raise HTTPException(409, "这项交接已确认，请刷新进度。")
            stamp = now()
            con.execute("UPDATE team_handoffs SET accepted_at=? WHERE id=?", (stamp, handoff_id))
            event(con, "handoff_accepted", recipient["name"], f"已接手接续卡 v{handoff['card_version']}", stamp)
            return remember(con, body, req, {"id": handoff_id, "accepted_at": stamp})

    @app.get("/api/connection", dependencies=[Depends(authorize)])
    def connection(request: Request):
        port = request.url.port or 8765
        return {"addresses": [f"http://{ip}:{port}" for ip in lan_addresses()]}

    def store_file(operation_id: UUID, name: str, device: str, content: bytes):
        digest = sha256(content).hexdigest()
        with connect() as con:
            con.execute("BEGIN IMMEDIATE")
            previous = con.execute(
                f"SELECT {FILE_METADATA}, digest FROM project_files WHERE operation_id=?", (str(operation_id),)
            ).fetchone()
            if previous is not None:
                if (previous["name"], previous["device"], previous["digest"]) != (name, device, digest):
                    raise HTTPException(409, "这次上传的编号已被使用。请重新选择文件。")
                result = dict(previous)
                del result["digest"]
                return result

            stamp = now()
            cursor = con.execute(
                "INSERT INTO project_files(operation_id,name,size,device,created_at,digest,content) VALUES(?,?,?,?,?,?,?)",
                (str(operation_id), name, len(content), device, stamp, digest, content),
            )
            event(con, "file", device, f"发送了文件 {name}", stamp)
            return {"id": cursor.lastrowid, "name": name, "size": len(content), "device": device, "created_at": stamp}

    @app.post("/api/files", dependencies=[Depends(authorize)])
    async def upload_file(
        request: Request,
        operation_id: UUID,
        name: Annotated[str, Query(min_length=1, max_length=255)],
        device: Annotated[str, Query(min_length=1, max_length=80)],
        x_workspace_id: str | None = Header(default=None),
    ):
        if x_workspace_id is not None and x_workspace_id != workspace_id:
            raise HTTPException(409, "当前工作空间已更换，请重新连接后再发送文件。")
        if name.strip() in {"", ".", ".."} or any(c in "/\\" or ord(c) < 32 or ord(c) == 127 for c in name):
            raise HTTPException(422, "文件名无效，请重命名后再上传。")
        device = device.strip()
        if not device:
            raise HTTPException(422, "请填写设备名称。")
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/octet-stream":
            raise HTTPException(415, "请发送原始文件内容。")

        length = request.headers.get("content-length", "")
        if length.isdecimal() and int(length) > MAX_FILE_BYTES:
            raise HTTPException(413, "单个文件最多 20 MB。")
        content = bytearray()
        async for chunk in request.stream():
            if len(content) + len(chunk) > MAX_FILE_BYTES:
                raise HTTPException(413, "单个文件最多 20 MB。")
            content.extend(chunk)
        return await run_in_threadpool(store_file, operation_id, name, device, bytes(content))

    @app.get("/api/files/{file_id}", dependencies=[Depends(authorize)])
    def download_file(file_id: int):
        with connect() as con:
            row = con.execute("SELECT name, content FROM project_files WHERE id=?", (file_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "找不到这个文件。")
        filename = quote(row["name"], safe="")
        return Response(
            content=row["content"],
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
        )

    @app.post("/api/ai/draft", dependencies=[Depends(authorize)])
    def ai_draft(body: DraftRequest) -> DraftResult:
        try:
            return DraftResult.model_validate(generate_draft(body.text))
        except TimeoutError:
            raise HTTPException(504, "生成草稿超时。原有内容已保留，可以稍后重试。") from None
        except ValidationError:
            raise HTTPException(502, "草稿格式不正确。原有内容已保留，可以重试或手动填写。") from None
        except DraftUnavailableError:
            raise HTTPException(502, "模型服务暂不可用。原有内容已保留，可以稍后重试。") from None

    @app.put("/api/card", dependencies=[Depends(authorize)])
    def save_card(body: CardInput):
        with connect() as con:
            con.execute("BEGIN IMMEDIATE")
            req, previous = replay(con, body, "card")
            if previous is not None:
                return previous
            version = con.execute("SELECT COALESCE(MAX(version),0) FROM cards").fetchone()[0]
            if version != body.version:
                raise HTTPException(409, "接续卡已在另一端更新。你的输入已保留，请对照最新版本后重新保存。")
            stamp = now()
            payload = body.model_dump(exclude={"version", "operation_id", "device"})
            con.execute("INSERT INTO cards VALUES(?,?,?,?)", (version + 1, json.dumps(payload, ensure_ascii=False), body.device, stamp))
            event(con, "saved", body.device, f"保存了接续卡 v{version + 1}", stamp)
            return remember(con, body, req, {"version": version + 1, "updated_at": stamp})

    @app.post("/api/notes", dependencies=[Depends(authorize)])
    def add_note(body: NoteInput, x_workspace_id: str | None = Header(default=None)):
        if x_workspace_id is not None and x_workspace_id != workspace_id:
            raise HTTPException(409, "待同步补充属于另一个工作空间。请回到原来的工作空间同步。")
        with connect() as con:
            con.execute("BEGIN IMMEDIATE")
            req, previous = replay(con, body, "note")
            if previous is not None:
                return previous
            if not con.execute("SELECT 1 FROM cards LIMIT 1").fetchone():
                raise HTTPException(409, "先在电脑上保存第一张接续卡，再补充想法。")
            stamp = now()
            cursor = con.execute("INSERT INTO notes(text,device,created_at) VALUES(?,?,?)", (body.text, body.device, stamp))
            event(con, "note", body.device, "补充了一条想法", stamp)
            return remember(con, body, req, {"id": cursor.lastrowid, "created_at": stamp})

    @app.post("/api/resume", dependencies=[Depends(authorize)])
    def resume(body: ResumeInput):
        with connect() as con:
            con.execute("BEGIN IMMEDIATE")
            req, previous = replay(con, body, "resume")
            if previous is not None:
                return previous
            if not con.execute("SELECT 1 FROM cards LIMIT 1").fetchone():
                raise HTTPException(409, "先保存一张接续卡。")
            stamp = now()
            count = 0
            for note_id in set(body.note_ids):
                count += con.execute("UPDATE notes SET reviewed_at=? WHERE id=? AND reviewed_at IS NULL", (stamp, note_id)).rowcount
            event(con, "resumed", body.device, f"恢复工作，已查看 {count} 条补充", stamp)
            return remember(con, body, req, {"reviewed": count, "resumed_at": stamp})

    @app.get("/api/history/{version}", dependencies=[Depends(authorize)])
    def history(version: int):
        with connect() as con:
            card = decode_card(con.execute("SELECT * FROM cards WHERE version=?", (version,)).fetchone())
            if card is None:
                raise HTTPException(404, "未找到此版本")
            return card

    dist = ROOT / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/")
    def index():
        if not (dist / "index.html").exists():
            raise HTTPException(503, "请先在项目目录运行 npm run build")
        return FileResponse(dist / "index.html")

    return app
