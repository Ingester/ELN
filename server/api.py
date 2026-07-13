"""
ELN App — FastAPI Server
All REST endpoints. Runs on Windows as the data host (default port 8600, env ELN_API_PORT).
"""

from __future__ import annotations
import base64
import hashlib
import hmac
import io
import os
import json
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from typing import Optional, Any
from urllib.parse import parse_qs, quote, unquote

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db.database as db_ops
from db.models import ProtocolDefinition
from server import web_ui
from utils.report_generator import generate_report
from utils.i18n import localize_html

STEP_NOTES_KEY = "__eln_step_notes"

app = FastAPI(title="ELN API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _photos_dir() -> str:
    return db_ops.get_photos_dir()


def _audio_dir() -> str:
    return db_ops.get_audio_dir()


def _audio_url(rel_path: str) -> str:
    clean = str(rel_path).replace("\\", "/")
    audio_path = os.path.join(db_ops.get_audio_dir(), clean.replace("/", os.sep))
    if os.path.exists(audio_path):
        return "/audio/" + clean
    return "/photos/" + clean


def _delete_audio_file(rel_path: str) -> bool:
    """Delete a stored audio file (checks both the audio and photos dirs).
    Used to reclaim space once a capture's transcript is safely filed."""
    rel = str(rel_path or "").replace("\\", "/").replace("/", os.sep).lstrip(os.sep)
    if not rel:
        return False
    for base in (db_ops.get_audio_dir(), db_ops.get_photos_dir()):
        p = os.path.join(base, rel)
        if os.path.isfile(p):
            try:
                os.remove(p)
                return True
            except OSError as exc:
                print(f"[inbox] could not delete audio {p}: {exc}")
    return False


_OVERLAY_TAG = '<script src="/openview/overlay.js" defer></script>'


def _html_response(content: str, **kwargs) -> HTMLResponse:
    """Return localized HTML for the native web pages, with the comment overlay injected."""
    html = localize_html(content)
    if "</body>" in html and _OVERLAY_TAG not in html:
        html = html.replace("</body>", _OVERLAY_TAG + "\n</body>", 1)
    return HTMLResponse(html, **kwargs)


# ─────────────────────────────────────────────
# Optional app-level password guard for public tunnels
# ─────────────────────────────────────────────

def _auth_password() -> str:
    return os.environ.get("ELN_AUTH_PASSWORD", "")


def _auth_cookie_name() -> str:
    return os.environ.get("ELN_AUTH_COOKIE_NAME", "eln_session")


def _auth_cookie_max_age() -> int:
    try:
        days = int(os.environ.get("ELN_AUTH_DAYS", "30"))
    except ValueError:
        days = 30
    return max(1, days) * 24 * 60 * 60


def _auth_secret() -> bytes:
    configured = os.environ.get("ELN_AUTH_COOKIE_SECRET", "")
    seed = configured or f"eln-auth:{_auth_password()}"
    return hashlib.sha256(seed.encode("utf-8")).digest()


def _make_auth_token() -> str:
    issued = str(int(time.time()))
    sig = hmac.new(_auth_secret(), issued.encode("utf-8"), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{issued}:{sig}".encode("utf-8")).decode("ascii")


def _valid_auth_token(token: str) -> bool:
    if not token:
        return False
    try:
        decoded = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        issued, sig = decoded.split(":", 1)
        expected = hmac.new(_auth_secret(), issued.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
        return (time.time() - int(issued)) <= _auth_cookie_max_age()
    except Exception:
        return False


def _openview_password() -> str:
    return os.environ.get("ELN_OPENVIEW_PASSWORD", "")


def _openview_cookie_name() -> str:
    return "eln_openview"


def _openview_secret() -> bytes:
    return hashlib.sha256(f"eln-openview:{_openview_password()}".encode("utf-8")).digest()


def _make_openview_token() -> str:
    issued = str(int(time.time()))
    sig = hmac.new(_openview_secret(), issued.encode("utf-8"), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{issued}:{sig}".encode("utf-8")).decode("ascii")


def _valid_openview_token(token: str) -> bool:
    if not token or not _openview_password():
        return False
    try:
        decoded = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        issued, sig = decoded.split(":", 1)
        expected = hmac.new(_openview_secret(), issued.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
        return (time.time() - int(issued)) <= _auth_cookie_max_age()
    except Exception:
        return False


def _client_ip(request: Request) -> str:
    for h in ("cf-connecting-ip", "x-forwarded-for"):
        v = request.headers.get(h, "")
        if v:
            return v.split(",")[0].strip()
    return (request.client.host if request.client else "") or "?"


def _ip_nickname(ip: str) -> str:
    """Stable short nickname derived from the visitor's IP, e.g. 访客·7F3."""
    h = hashlib.sha256(("eln-nick:" + str(ip)).encode("utf-8")).hexdigest()
    return "访客·" + h[:3].upper()


def _auth_cookie_secure(request: Request) -> bool:
    setting = os.environ.get("ELN_AUTH_SECURE_COOKIE", "auto").lower()
    if setting in {"1", "true", "yes", "on"}:
        return True
    if setting in {"0", "false", "no", "off"}:
        return False
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return proto == "https" or request.url.scheme == "https"


def _auth_next_path(request: Request) -> str:
    path = request.url.path or "/run"
    if request.url.query:
        path += f"?{request.url.query}"
    return path


def _wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept or "*/*" in accept


_LOGIN_CSS = """
    body { min-height:100vh; display:grid; place-items:center; padding-bottom:0; }
    main { width:min(420px, calc(100vw - 32px)); background:var(--card); border:1px solid var(--line);
           border-radius:18px; padding:26px; box-shadow:var(--shadow); }
    h1 { margin:0 0 4px; font-size:21px; font-weight:600; }
    .sub { color:var(--muted); font-size:13.5px; margin:0 0 18px; }
    label { display:block; margin:0 0 8px; }
    input { margin-bottom:14px; }
    .error { color:var(--neg); font-weight:500; }
    .hint { color:var(--faint); font-size:12.5px; margin-top:14px; line-height:1.5; }
"""


def _login_page(next_path: str, error: str = "") -> HTMLResponse:
    error_html = f'<p class="error">{_html_escape(error)}</p>' if error else ""
    head = web_ui.page_head("ELN 登录", _LOGIN_CSS)
    return _html_response(f"""
{head}
<body>
  <main>
    <h1>ELN 实验记录</h1>
    <p class="sub">输入访问密码继续</p>
    {error_html}
    <form method="post" action="/login">
      <input type="hidden" name="next" value="{_html_escape(next_path)}" />
      <label for="password">访问密码</label>
      <input id="password" name="password" type="password" autocomplete="current-password" autofocus />
      <button type="submit">进入</button>
    </form>
    <p class="hint">这个密码由电脑端环境变量 ELN_AUTH_PASSWORD 控制。Cloudflare Tunnel 对外开放时建议同时启用 Cloudflare Access。</p>
  </main>
</body>
</html>
""")


def _is_local_direct(request: Request) -> bool:
    """True for a genuine loopback request (local tools), False for tunnel
    traffic. Cloudflared connects from 127.0.0.1 too, but always adds forwarding
    headers — so a loopback client with no such header is a real local call."""
    client = (request.client.host if request.client else "") or ""
    if client not in ("127.0.0.1", "::1", "localhost"):
        return False
    fwd_headers = ("cf-connecting-ip", "x-forwarded-for", "x-forwarded-proto", "cf-ray")
    return not any(h in request.headers for h in fwd_headers)


@app.middleware("http")
async def optional_password_auth(request: Request, call_next):
    path = request.url.path
    # Always-open endpoints (login flows + the overlay bootstrap).
    if (
        request.method == "OPTIONS"
        or path in {
            "/login", "/logout", "/api/health", "/favicon.ico",
            "/openview", "/openview/login", "/openview/logout",
            "/openview/overlay.js", "/api/openview/whoami",
        }
    ):
        return await call_next(request)

    main_ok = bool(_auth_password()) and _valid_auth_token(request.cookies.get(_auth_cookie_name(), ""))
    open_ok = _valid_openview_token(request.cookies.get(_openview_cookie_name(), ""))
    # Local tools (Claude Code / Codex / curl on this machine) are treated as owner.
    local = _is_local_direct(request)
    request.state.mode = "owner" if (main_ok or local) else ("openview" if open_ok else "none")

    authed = (not _auth_password()) or main_ok or open_ok or local
    if not authed:
        if _wants_html(request):
            return RedirectResponse(f"/login?next={quote(_auth_next_path(request))}", status_code=303)
        return JSONResponse({"detail": "Authentication required"}, status_code=401)

    # Record data changes made by openview visitors so the owner can review them.
    is_openview_edit = (
        request.state.mode == "openview"
        and request.method in ("POST", "PATCH", "PUT", "DELETE")
        and path.startswith("/api/")
        and not path.startswith("/api/openview/")
    )
    body_bytes = await request.body() if is_openview_edit else b""
    response = await call_next(request)
    if is_openview_edit and 200 <= response.status_code < 300:
        try:
            _log_openview_change(request, path, body_bytes)
        except Exception as exc:
            print(f"[openview] change log failed: {exc}")
    return response


@app.get("/login", response_class=HTMLResponse)
def login_form(next: str = Query("/run")):
    return _login_page(next)


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    body = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(body, keep_blank_values=True)
    password = parsed.get("password", [""])[0]
    next_path = parsed.get("next", ["/run"])[0] or "/run"
    if not next_path.startswith("/"):
        next_path = "/run"
    # The main password grants owner access; the openview password grants
    # read+comment (openview) access — same real UI, different cookie.
    if _auth_password() and hmac.compare_digest(password, _auth_password()):
        response = RedirectResponse(next_path, status_code=303)
        response.set_cookie(
            _auth_cookie_name(), _make_auth_token(),
            max_age=_auth_cookie_max_age(), httponly=True,
            secure=_auth_cookie_secure(request), samesite="lax",
        )
        return response
    if _openview_password() and hmac.compare_digest(password, _openview_password()):
        response = RedirectResponse(next_path, status_code=303)
        response.set_cookie(
            _openview_cookie_name(), _make_openview_token(),
            max_age=_auth_cookie_max_age(), httponly=True,
            secure=_auth_cookie_secure(request), samesite="lax",
        )
        return response
    return _login_page(next_path, "密码不正确")


@app.get("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(_auth_cookie_name())
    response.delete_cookie(_openview_cookie_name())
    return response


# Mount static photo files
def mount_photos(application: FastAPI) -> None:
    photos_dir = _photos_dir()
    application.mount("/photos", StaticFiles(directory=photos_dir), name="photos")
    audio_dir = _audio_dir()
    application.mount("/audio", StaticFiles(directory=audio_dir), name="audio")


# ─────────────────────────────────────────────
# Pydantic request/response schemas
# ─────────────────────────────────────────────

class ExperimentCreate(BaseModel):
    name: str
    protocol_json: str          # full ProtocolDefinition JSON string
    protocol_id: Optional[int] = None
    notes: str = ""


class ExperimentUpdate(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class StepUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    fields_json: Optional[str] = None
    values_json: Optional[str] = None
    description_overrides_json: Optional[str] = None
    photo_paths: Optional[str] = None
    photo_pending: Optional[bool] = None
    timer_override_seconds: Optional[int] = None
    timer_finished_at: Optional[str] = None
    overtime_seconds: Optional[int] = None


class AttachmentRename(BaseModel):
    path: str
    name: str


class TimerUpdate(BaseModel):
    total_seconds: Optional[int] = None
    remaining_seconds: Optional[int] = None
    overtime_seconds: Optional[int] = None
    status: Optional[str] = None
    timer_finished_at: Optional[str] = None
    started_at: Optional[str] = None


class TimerSync(BaseModel):
    total_seconds: int
    remaining_seconds: int
    overtime_seconds: int = 0
    status: str
    action: str = "sync"
    elapsed_seconds: Optional[int] = None


class ProtocolCreate(BaseModel):
    protocol_json: str          # full ProtocolDefinition JSON string


class BoxCreate(BaseModel):
    box_name: str
    box_size: int = 10
    notes: str = ""


class BoxUpdate(BaseModel):
    box_name: Optional[str] = None
    box_size: Optional[int] = None
    notes: Optional[str] = None


class SlotUpdate(BaseModel):
    sample_name: str
    notes: str = ""
    experiment_id: Optional[int] = None
    step_id: Optional[int] = None


class StorageRegister(BaseModel):
    item_id: int
    box_id: int
    row_label: str
    col_label: str
    notes: str = ""


class StorageCreate(BaseModel):
    item_label: str
    tube_type: str = ""
    notes_template: str = ""
    default_box: str = ""


class VoiceNoteCreate(BaseModel):
    text: str
    step_id: Optional[int] = None


class VoiceNoteUpdate(BaseModel):
    text: Optional[str] = None
    step_id: Optional[int] = None


# ─────────────────────────────────────────────
# OpenView — real app behind a share password + free-position comments + change log
# ─────────────────────────────────────────────

class OpenCommentIn(BaseModel):
    page: str = "/"
    text: str
    x: float = 0.0            # document coords (fallback anchor)
    y: float = 0.0
    anchor: str = ""          # CSS selector to re-find the target element
    anchor_text: str = ""     # snippet of what the comment is on


def _open_comments_dir() -> str:
    base = os.path.join(os.path.expanduser("~"), "ELN_Data", "open_comments")
    os.makedirs(base, exist_ok=True)
    return base


def _open_comments_file() -> str:
    return os.path.join(_open_comments_dir(), "comments.jsonl")


def _read_open_comments() -> list:
    path = _open_comments_file()
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                out.append(item)
    return out


def _append_open_comment(item: dict) -> dict:
    with open(_open_comments_file(), "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
    return item


def _rewrite_open_comments(items: list) -> None:
    with open(_open_comments_file(), "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False, separators=(",", ":")) + "\n")


def _norm_page(page: str) -> str:
    page = (page or "/").strip()
    if not page.startswith("/"):
        page = "/"
    return page[:300]


# ---- change log (openview edits) ----

def _open_changes_file() -> str:
    base = os.path.join(os.path.expanduser("~"), "ELN_Data", "open_changes")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "changes.jsonl")


def _read_open_changes() -> list:
    path = _open_changes_file()
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _append_open_change(item: dict) -> None:
    with open(_open_changes_file(), "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")


def _summarize_change(method: str, path: str, body_bytes: bytes) -> str:
    try:
        data = json.loads(body_bytes or b"{}")
    except Exception:
        data = {}
    m = re.match(r"^/api/steps/(\d+)$", path)
    if m and method == "PATCH":
        step = db_ops.get_step(int(m.group(1)))
        ctx = ""
        if step:
            exp = db_ops.get_experiment(step.experiment_id)
            ctx = (f"{exp.name if exp else ''} · 第{step.step_index + 1}步 {step.title}").strip(" ·")
        parts = []
        if "values_json" in data:
            parts.append("字段/备注")
        if "fields_json" in data:
            parts.append("字段定义")
        if "photo_paths" in data or "photo_pending" in data:
            parts.append("附件")
        return f"改了步骤（{ctx}）：{'、'.join(parts) or '内容'}"
    if re.match(r"^/api/steps/(\d+)/complete$", path):
        return "把某一步标记为完成"
    if path == "/api/inbox" and method == "POST":
        return "新增了一条速记"
    m = re.match(r"^/api/inbox/(\d+)", path)
    if m:
        return (f"删除了速记 #{m.group(1)}" if method == "DELETE" else f"改动了速记 #{m.group(1)}")
    if path == "/api/experiments" and method == "POST":
        return (f"新建了实验：{data.get('name', '')}").rstrip("：")
    m = re.match(r"^/api/experiments/(\d+)$", path)
    if m:
        return (f"删除了实验 #{m.group(1)}" if method == "DELETE" else f"改了实验 #{m.group(1)} 的信息")
    return f"{method} {path}"


def _log_openview_change(request: Request, path: str, body_bytes: bytes) -> None:
    ip = _client_ip(request)
    _append_open_change({
        "id": uuid.uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ip": ip,
        "name": _ip_nickname(ip),
        "method": request.method,
        "path": path,
        "summary": _summarize_change(request.method, path, body_bytes),
    })


# ---- openview login entry ----

@app.get("/openview", response_class=HTMLResponse)
def openview_login_form(next: str = Query("/run")):
    if not _openview_password():
        return _html_response("<body><main style='padding:30px'><h1>OpenView 未启用</h1>"
                              "<p>请在电脑端设置环境变量 ELN_OPENVIEW_PASSWORD 后重启。</p></main></body>")
    if not next.startswith("/"):
        next = "/run"
    head = web_ui.page_head("ELN 查看与评论", _LOGIN_CSS)
    return _html_response(f"""
{head}
<body>
  <main>
    <h1>ELN · 查看与评论</h1>
    <p class="sub">输入分享密码进入（可浏览、可留言评论）</p>
    <form method="post" action="/openview/login">
      <input type="hidden" name="next" value="{_html_escape(next)}" />
      <label for="password">分享密码</label>
      <input id="password" name="password" type="password" autocomplete="current-password" autofocus />
      <button type="submit">进入</button>
    </form>
    <p class="hint">这是查看/评论用的公开入口，密码和主人的不同。进入后点右下角「评论」即可在任意位置留言。</p>
  </main>
</body>
</html>
""")


@app.post("/openview/login", response_class=HTMLResponse)
async def openview_login_submit(request: Request):
    body = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(body, keep_blank_values=True)
    password = parsed.get("password", [""])[0]
    next_path = parsed.get("next", ["/run"])[0] or "/run"
    if not next_path.startswith("/"):
        next_path = "/run"
    if not _openview_password() or not hmac.compare_digest(password, _openview_password()):
        head = web_ui.page_head("ELN 查看与评论", _LOGIN_CSS)
        return _html_response(f"""{head}<body><main><h1>ELN · 查看与评论</h1>
        <p class="error">密码不正确</p>
        <form method="post" action="/openview/login">
          <input type="hidden" name="next" value="{_html_escape(next_path)}" />
          <label for="password">分享密码</label>
          <input id="password" name="password" type="password" autofocus />
          <button type="submit">进入</button>
        </form></main></body></html>""")
    response = RedirectResponse(next_path, status_code=303)
    response.set_cookie(
        _openview_cookie_name(), _make_openview_token(),
        max_age=_auth_cookie_max_age(), httponly=True,
        secure=_auth_cookie_secure(request), samesite="lax",
    )
    return response


@app.get("/openview/logout")
def openview_logout():
    response = RedirectResponse("/openview", status_code=303)
    response.delete_cookie(_openview_cookie_name())
    return response


@app.get("/api/openview/whoami")
def openview_whoami(request: Request):
    main_ok = bool(_auth_password()) and _valid_auth_token(request.cookies.get(_auth_cookie_name(), ""))
    open_ok = _valid_openview_token(request.cookies.get(_openview_cookie_name(), ""))
    if main_ok or _is_local_direct(request):
        return {"mode": "owner", "name": "我", "can_comment": True}
    if open_ok:
        return {"mode": "openview", "name": _ip_nickname(_client_ip(request)), "can_comment": True}
    return {"mode": "none", "name": "", "can_comment": False}


# ---- comments API ----

@app.get("/api/openview/comments")
def list_open_comments(page: Optional[str] = Query(None)):
    items = [c for c in _read_open_comments() if c.get("v") == 2]
    if page is not None:
        p = _norm_page(page)
        items = [c for c in items if c.get("page") == p]
    return sorted(items, key=lambda c: str(c.get("created_at", "")))


@app.post("/api/openview/comments", status_code=201)
def create_open_comment(body: OpenCommentIn, request: Request):
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "评论内容不能为空")
    mode = getattr(request.state, "mode", "none")
    if mode not in ("owner", "openview"):
        raise HTTPException(403, "无权评论")
    ip = _client_ip(request)
    return _append_open_comment({
        "v": 2,
        "id": uuid.uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "page": _norm_page(body.page),
        "x": float(body.x or 0.0),
        "y": float(body.y or 0.0),
        "anchor": (body.anchor or "")[:400],
        "anchor_text": (body.anchor_text or "")[:160],
        "text": text[:4000],
        "author": ("我" if mode == "owner" else _ip_nickname(ip)),
        "ip": ip,
        "mode": mode,
    })


@app.delete("/api/openview/comments/{comment_id}", status_code=204)
def delete_open_comment(comment_id: str, request: Request):
    if getattr(request.state, "mode", "none") != "owner":
        raise HTTPException(403, "只有主人能删除评论")
    items = _read_open_comments()
    kept = [c for c in items if c.get("id") != comment_id]
    if len(kept) != len(items):
        _rewrite_open_comments(kept)


# ---- change log API + owner page ----

@app.get("/api/openview/changes")
def list_open_changes(request: Request):
    if getattr(request.state, "mode", "none") != "owner":
        raise HTTPException(403, "仅主人可见")
    return sorted(_read_open_changes(), key=lambda c: str(c.get("created_at", "")), reverse=True)


_CHANGES_CSS_EXTRA = """
    main { max-width:760px; }
    .chg { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px 14px; margin-bottom:10px; }
    .chg .w { font-weight:600; font-size:14.5px; overflow-wrap:anywhere; }
    .chg .m { color:var(--muted); font-size:12.5px; margin-top:4px; }
    .who { display:inline-block; font-size:11.5px; font-weight:600; padding:1px 8px; border-radius:999px; background:var(--clay-soft); color:var(--clay-ink); }
    .empty { text-align:center; color:var(--muted); padding:44px 0; }
"""


@app.get("/changes", response_class=HTMLResponse)
def changes_page(request: Request):
    body = f"""
<body>
  <header class="app-bar"><h1>改动记录</h1>
    <button class="icon-btn" onclick="load()" title="刷新">{web_ui.icon('refresh', 18)}</button>
  </header>
  <main>
    <div class="small" style="margin:2px 2px 12px">这里列出通过 openview（分享入口）对实验数据做的改动，最新在上。</div>
    <div id="list"><div class="small">加载中…</div></div>
  </main>
{_bottom_nav("more", "/")}
<script>
function esc(v){{ return String(v ?? "").replace(/[&<>"']/g, s => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[s])); }}
async function load(){{
  let xs = [];
  try {{ const r = await fetch("/api/openview/changes"); if(r.ok) xs = await r.json(); }} catch {{}}
  const box = document.getElementById("list");
  if(!xs.length){{ box.innerHTML = '<div class="empty">还没有来自 openview 的改动。</div>'; return; }}
  box.innerHTML = xs.map(c => {{
    const t = c.created_at ? new Date(c.created_at).toLocaleString() : "";
    return `<div class="chg"><div class="w">${{esc(c.summary || (c.method + " " + c.path))}}</div>
      <div class="m"><span class="who">${{esc(c.name || "访客")}}</span> · ${{t}} · ${{esc(c.ip || "")}}</div></div>`;
  }}).join("");
}}
load();
</script>
</body>
</html>"""
    return _html_response(web_ui.page_head("改动记录 · ELN", _NAV_CSS + _CHANGES_CSS_EXTRA) + body,
                          headers={"Cache-Control": "no-store, max-age=0"})


# ---- comment overlay (injected into every page) ----

_OVERLAY_JS_PATH = os.path.join(os.path.dirname(__file__), "openview_overlay.js")


@app.get("/openview/overlay.js")
def openview_overlay_js():
    try:
        with open(_OVERLAY_JS_PATH, "r", encoding="utf-8") as f:
            js = f.read()
    except Exception:
        js = "/* overlay unavailable */"
    return Response(content=js, media_type="application/javascript",
                    headers={"Cache-Control": "no-store, max-age=0"})


# ─────────────────────────────────────────────
# Health check
# ─────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/", response_class=HTMLResponse)
def root():
    """Web-only entry point: land on the capture page (速记)."""
    return RedirectResponse("/capture", status_code=302)


# ─────────────────────────────────────────────
# Capture inbox (速记) — default landing page
# ─────────────────────────────────────────────

def _flet_home_url(request: Request) -> str:
    """Best-effort URL back to the Flet shell (port 8550) for the other tabs."""
    host = request.url.hostname or "127.0.0.1"
    if request.url.scheme == "https" or os.environ.get("ELN_NATIVE_ONLY") == "1":
        return "/run"  # public tunnel only exposes native pages
    display = f"[{host}]" if ":" in host else host
    return f"{request.url.scheme}://{display}:8550/"


def _bottom_nav(active: str, home_url: str) -> str:
    items = [
        ("capture", "note", "速记", "/capture"),
        ("run", "flask", "实验", "/run"),
        ("history", "clock", "历史", "/history"),
        ("more", "more", "更多", "/more"),
    ]
    cells = "".join(
        f'<a class="nav-cell{" active" if key == active else ""}" href="{href}">'
        f'{web_ui.icon(ic, 22)}<span class="nl">{label}</span></a>'
        for key, ic, label, href in items
    )
    return f'<nav class="bottom-nav">{cells}</nav>'


_NAV_CSS = """
    body { padding-bottom: calc(72px + env(safe-area-inset-bottom, 0px)); }
    .bottom-nav { position:fixed; left:0; right:0; bottom:0; z-index:40; display:flex;
      background:rgba(244,242,236,.92); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
      border-top:1px solid var(--line); padding-bottom:env(safe-area-inset-bottom,0px); }
    .nav-cell { flex:1; display:flex; flex-direction:column; align-items:center; gap:3px;
      padding:9px 0 8px; color:var(--faint); text-decoration:none; box-shadow:none; background:none;
      min-height:0; border-radius:0; }
    .nav-cell .nl { font-size:11px; font-weight:500; }
    .nav-cell svg.icon { stroke-width:1.7; }
    .nav-cell.active { color:var(--clay-ink); }
"""

_CAPTURE_CSS = _NAV_CSS + """
    main { max-width:720px; }
    .cap-card { background:var(--card); border:1px solid var(--line); border-radius:var(--radius);
      padding:16px; box-shadow:var(--shadow); }
    .cap-card.drop-on { border-color:var(--clay); box-shadow:0 0 0 3px rgba(189,91,61,.12) inset; }
    #capText { min-height:120px; font-size:16px; }
    .exp-pick { margin-bottom:12px; }
    .exp-pick label { display:block; margin-bottom:5px; }
    .thumbs { display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }
    .thumb { position:relative; width:76px; height:76px; border-radius:10px; overflow:hidden;
      border:1px solid var(--line); background:#f3f1ec; }
    .thumb img { width:100%; height:100%; object-fit:cover; }
    .thumb .rm { position:absolute; top:2px; right:2px; width:22px; height:22px; min-height:0;
      border-radius:999px; background:rgba(20,18,15,.6); color:#fff; font-size:13px; padding:0;
      display:flex; align-items:center; justify-content:center; box-shadow:none; }
    .cap-tools { display:flex; gap:8px; flex-wrap:wrap; margin-top:12px; }
    .cap-tools .button, .cap-tools button { min-height:44px; flex:1; }
    .thumb.ph { display:flex; align-items:center; justify-content:center; color:var(--faint); }
    .file-chip { display:flex; align-items:center; gap:8px; max-width:100%; min-height:44px;
      border:1px solid var(--line); border-radius:10px; background:#fbfaf7; padding:7px 34px 7px 10px;
      position:relative; color:var(--muted); font-size:13px; overflow:hidden; }
    .file-chip .fn { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--ink); }
    .file-chip .rm { position:absolute; top:5px; right:5px; width:22px; height:22px; min-height:0; }
    input[type=file] { position:absolute; left:-9999px; width:1px; height:1px; opacity:0; }
    #micState { min-height:20px; color:var(--clay-ink); font-size:14px; margin-top:8px; }
    #capMic.rec { background:var(--clay); border-color:var(--clay); color:#fff; }
    .archive-row { margin-top:16px; display:flex; gap:10px; }
    .archive-row button { flex:1; min-height:50px; font-size:15.5px; }
    .pending-head { display:flex; justify-content:space-between; align-items:center; margin:22px 2px 8px; }
    .pending-head h2 { margin:0; font-size:12px; color:var(--faint); text-transform:none; letter-spacing:.04em; }
    .pending-head .edit-link { font-size:13px; }
    .pending-item { display:flex; gap:10px; background:var(--inset); border:1px solid var(--line);
      border-radius:12px; padding:10px; margin-bottom:8px; align-items:flex-start; }
    .pending-item .thumb { width:48px; height:48px; flex:0 0 auto; }
    .pending-item .edit-thumb { cursor:pointer; }
    .pending-item .pt { flex:1; min-width:0; font-size:14px; overflow-wrap:anywhere; }
    .pending-item .pm { font-size:12px; color:var(--muted); margin-top:2px; }
    .pending-edit { display:none; margin-top:8px; }
    .pending-edit.open { display:block; }
    .pending-edit textarea { min-height:92px; font-size:14px; background:#fff; }
    .pending-edit .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-top:8px; }
    .pending-edit button { min-height:36px; padding:6px 12px; font-size:13.5px; }
"""

_CAPTURE_BODY = """
<body>
  <header class="app-bar">
    <h1>速记</h1>
    <a class="button secondary" href="/inbox" id="inboxLink">__I_INBOX__ 收件箱<span id="inboxCount"></span></a>
  </header>
  <main>
    <section class="cap-card">
      <div class="field exp-pick">
        <label>属于哪个实验（可不选，交给 AI 判断）</label>
        <select id="expPick"><option value="">让 AI 判断属于哪个实验</option></select>
      </div>
      <textarea id="capText" placeholder="刚做了什么、看到了什么？直接打字，或点下面的话筒说出来。"></textarea>
      <div id="micState"></div>
      <div class="thumbs" id="thumbs"></div>
      <div class="cap-tools">
        <button id="capMic" class="secondary" onclick="toggleCapMic()">__I_MIC__<span id="capMicLabel">说</span></button>
        <label class="button secondary" for="capCam">__I_CAM__ 拍照</label>
        <label class="button secondary" for="capGal">__I_IMG__ 相册</label>
        <input id="capCam" type="file" accept="image/*" capture="environment" multiple onchange="addImages(this)" />
        <input id="capGal" type="file" accept="image/*" multiple onchange="addImages(this)" />
      </div>
      <div class="archive-row">
        <button class="green" id="archiveBtn" onclick="archive()">__I_ARCH__ 打包存档</button>
      </div>
      <div class="small" id="capHint" style="margin-top:8px"></div>
    </section>

    <div class="pending-head">
      <h2>待归档</h2>
      <a class="edit-link" href="/inbox">全部 · 历史 __I_ARR__</a>
    </div>
    <div id="pendingList"></div>
  </main>
__NAV__
<script>
__ICON_JS__
const heldImages = [];   // File objects not yet uploaded
const heldFiles = [];    // Non-image File objects not yet uploaded
const heldAudio = { blob: null };
const capVoice = { rec: null, recognizing: false, mr: null, chunks: [] };

function esc(v){ return String(v ?? "").replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s])); }
async function api(path, opts={}){
  const res = await fetch(path, {headers:{"Content-Type":"application/json", ...(opts.headers||{})}, ...opts});
  if(!res.ok) throw new Error(await res.text());
  return res.status === 204 ? null : await res.json();
}

async function loadExperiments(){
  try {
    const exps = await api("/api/experiment_summaries");
    const sel = document.getElementById("expPick");
    for(const e of exps){
      const o = document.createElement("option");
      o.value = e.id; o.textContent = e.name + " · " + e.completed_steps + "/" + e.total_steps;
      sel.appendChild(o);
    }
  } catch {}
}

function renderThumbs(){
  const box = document.getElementById("thumbs");
  const x = svgIcon("x", 13);
  const imageHtml = heldImages.map((f, i) =>
    `<span class="thumb"><img src="${URL.createObjectURL(f)}" /><button class="rm" onclick="rmImage(${i})">${x}</button></span>`
  ).join("");
  const fileHtml = heldFiles.map((f, i) =>
    `<span class="file-chip" title="${esc(f.name || "未命名文件")}">${svgIcon("clipboard",18)}<span class="fn">${esc(f.name || "未命名文件")}</span><button class="rm" onclick="rmFile(${i})">${x}</button></span>`
  ).join("");
  const audioHtml = heldAudio.blob ? `<span class="thumb" style="display:flex;align-items:center;justify-content:center;color:var(--muted)">${svgIcon("audio",24)}<button class="rm" onclick="rmAudio()">${x}</button></span>` : "";
  box.innerHTML = imageHtml + fileHtml + audioHtml;
}
function addImages(input){
  for(const f of input.files) addHeldFile(f);
  input.value = "";
  renderThumbs();
}
function addHeldFile(file){
  if(!file) return;
  if((file.type || "").startsWith("image/")) heldImages.push(file);
  else heldFiles.push(file);
}
function rmImage(i){ heldImages.splice(i,1); renderThumbs(); }
function rmFile(i){ heldFiles.splice(i,1); renderThumbs(); }
function rmAudio(){ heldAudio.blob = null; renderThumbs(); }

async function handleCapPaste(event){
  const ta = document.getElementById("capText");
  if(document.activeElement !== ta) return;
  const files = Array.from(event.clipboardData?.files || []);
  if(!files.length){
    const items = Array.from(event.clipboardData?.items || []);
    for(const item of items){
      if(item.kind === "file"){
        const f = item.getAsFile();
        if(f) files.push(f);
      }
    }
  }
  if(!files.length) return;
  event.preventDefault();
  files.forEach(addHeldFile);
  renderThumbs();
  const imageCount = files.filter(f => (f.type || "").startsWith("image/")).length;
  const fileCount = files.length - imageCount;
  const parts = [];
  if(imageCount) parts.push(`${imageCount} 张图片`);
  if(fileCount) parts.push(`${fileCount} 个文件`);
  document.getElementById("capHint").textContent = `已从剪贴板加入 ${parts.join("、")}，打包存档时会一起上传。`;
}

function handleCapDragOver(event){
  const dt = event.dataTransfer;
  if(!dt || !Array.from(dt.types || []).includes("Files")) return;
  event.preventDefault();
  document.querySelector(".cap-card")?.classList.add("drop-on");
}

function handleCapDragLeave(event){
  const card = document.querySelector(".cap-card");
  if(card && !card.contains(event.relatedTarget)) card.classList.remove("drop-on");
}

function handleCapDrop(event){
  const files = Array.from(event.dataTransfer?.files || []);
  if(!files.length) return;
  event.preventDefault();
  document.querySelector(".cap-card")?.classList.remove("drop-on");
  files.forEach(addHeldFile);
  renderThumbs();
  const imageCount = files.filter(f => (f.type || "").startsWith("image/")).length;
  const fileCount = files.length - imageCount;
  const parts = [];
  if(imageCount) parts.push(`${imageCount} 张图片`);
  if(fileCount) parts.push(`${fileCount} 个文件`);
  document.getElementById("capHint").textContent = `已加入 ${parts.join("、")}，打包存档时会一起上传。`;
}

function speechSupported(){ return !!(window.SpeechRecognition || window.webkitSpeechRecognition); }

function toggleCapMic(){
  if(capVoice.recognizing || capVoice.mr){ stopCapMic(); return; }
  startCapRecord();
}
function setMicUI(on){
  const b = document.getElementById("capMic");
  b.classList.toggle("rec", on);
  const lbl = document.getElementById("capMicLabel");
  if(lbl) lbl.textContent = on ? "停" : "说";
}
function startCapSpeech(){
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const rec = new SR(); rec.lang="zh-CN"; rec.continuous=true; rec.interimResults=true;
  const live = document.getElementById("micState");
  rec.onresult = e => {
    let interim="";
    for(let i=e.resultIndex;i<e.results.length;i++){
      const r=e.results[i];
      if(r.isFinal){ const t=r[0].transcript.trim(); if(t){ const ta=document.getElementById("capText"); ta.value=(ta.value?ta.value+" ":"")+t; } }
      else interim+=r[0].transcript;
    }
    live.textContent = interim;
  };
  rec.onend = () => { if(capVoice.recognizing){ try{rec.start();}catch{ capVoice.recognizing=false; setMicUI(false);} } };
  rec.onerror = ev => { live.textContent=""; if(ev.error==="not-allowed"){ capVoice.recognizing=false; capVoice.rec=null; setMicUI(false); document.getElementById("capHint").textContent="麦克风被拒绝，可改用键盘听写。"; } };
  capVoice.rec=rec; capVoice.recognizing=true; setMicUI(true);
  try{ rec.start(); }catch{}
}
async function startCapRecord(){
  if(!(navigator.mediaDevices && window.MediaRecorder)){ document.getElementById("capHint").textContent="此环境不支持录音，请打字。"; return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio:true});
    const preferred = ["audio/mp4;codecs=mp4a.40.2","audio/mp4","audio/webm;codecs=opus","audio/webm"];
    const mimeType = preferred.find(t => MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t));
    const mr = mimeType ? new MediaRecorder(stream, {mimeType}) : new MediaRecorder(stream); capVoice.chunks=[];
    mr.ondataavailable = e => { if(e.data && e.data.size) capVoice.chunks.push(e.data); };
    mr.onstop = () => { stream.getTracks().forEach(t=>t.stop()); const type=mr.mimeType||mimeType||"audio/mp4"; const blob=new Blob(capVoice.chunks,{type}); capVoice.chunks=[]; if(blob.size>0){ heldAudio.blob=blob; renderThumbs(); document.getElementById("capHint").textContent="已录一段语音，存档后会自动转写。"; } };
    capVoice.mr=mr; mr.start(); setMicUI(true);
  } catch(e){ document.getElementById("capHint").textContent="无法录音："+e.message; }
}
function stopCapMic(){
  if(capVoice.rec){ capVoice.recognizing=false; try{capVoice.rec.stop();}catch{} capVoice.rec=null; }
  if(capVoice.mr){ try{capVoice.mr.stop();}catch{} capVoice.mr=null; }
  document.getElementById("micState").textContent="";
  setMicUI(false);
}

async function archive(){
  stopCapMic();
  const text = document.getElementById("capText").value.trim();
  if(!text && !heldImages.length && !heldFiles.length && !heldAudio.blob){ document.getElementById("capHint").textContent="先说点什么、拍张照，粘贴文件，或打段字。"; return; }
  const btn = document.getElementById("archiveBtn");
  btn.disabled = true; document.getElementById("capHint").textContent = "存档中…";
  try {
    const hint = document.getElementById("expPick").value;
    const entry = await api("/api/inbox", {method:"POST", body: JSON.stringify({text, hinted_experiment_id: hint ? Number(hint) : null})});
    for(const f of heldImages){
      const fd = new FormData(); fd.append("file", f); fd.append("kind", "image");
      await fetch(`/api/inbox/${entry.id}/media`, {method:"POST", body:fd});
    }
    for(const f of heldFiles){
      const fd = new FormData(); fd.append("file", f); fd.append("kind", "file");
      await fetch(`/api/inbox/${entry.id}/media`, {method:"POST", body:fd});
    }
    if(heldAudio.blob){
      const fd = new FormData();
      const ext = (heldAudio.blob.type||"").includes("mp4") ? ".m4a" : ".webm";
      fd.append("file", new File([heldAudio.blob], "voice"+ext, {type:heldAudio.blob.type}));
      fd.append("kind", "audio");
      await fetch(`/api/inbox/${entry.id}/media`, {method:"POST", body:fd});
    }
    document.getElementById("capText").value = "";
    heldImages.length = 0; heldFiles.length = 0; heldAudio.blob = null; renderThumbs();
    document.getElementById("capHint").textContent = "已存进收件箱";
    setTimeout(()=>{ document.getElementById("capHint").textContent=""; }, 2500);
    loadPending();
  } catch(e){
    document.getElementById("capHint").textContent = "存档失败："+(e.message||e);
  } finally { btn.disabled = false; }
}

async function loadPending(){
  try {
    const items = await api("/api/inbox?status=pending");
    const c = document.getElementById("inboxCount");
    if(c) c.textContent = items.length ? (" " + items.length) : "";
    const box = document.getElementById("pendingList");
    if(!items.length){ box.innerHTML = '<div class="small" style="padding:0 2px">还没有待归档的速记。</div>'; return; }
    box.innerHTML = items.map(it => {
      const firstFile = it.file_urls && it.file_urls[0];
      const thumb = it.image_urls && it.image_urls[0]
        ? `<span class="thumb edit-thumb" onclick="openPendingEdit(${it.id})" role="button" title="修改识别文字" aria-label="修改识别文字"><img src="${esc(it.image_urls[0])}"></span>`
        : `<span class="thumb ph edit-thumb" onclick="openPendingEdit(${it.id})" role="button" title="修改识别文字" aria-label="修改识别文字">${svgIcon(firstFile ? "clipboard" : (it.audio_url && !it.text ? "audio" : "note"), 20)}</span>`;
      const t = new Date(it.created_at); const hh = String(t.getHours()).padStart(2,"0")+":"+String(t.getMinutes()).padStart(2,"0");
      const fileText = firstFile ? `文件：${esc(firstFile.name || "未命名文件")}` : "";
      const body = it.text ? esc(it.text) : (it.audio_url ? "语音待识别或未识别到文字" : (fileText || "图片"));
      const fileLinks = (it.file_urls || []).map(f => `<a href="${esc(f.url)}" target="_blank" rel="noopener">${svgIcon("clipboard",14)} ${esc(f.name || "文件")}</a>`).join(" ");
      const raw = esc(it.text || "");
      return `<div class="pending-item" id="pending-${it.id}">
        ${thumb}
        <div class="pt">
          <div id="pending-text-${it.id}">${body}</div>
          ${fileLinks ? `<div class="pm">${fileLinks}</div>` : ""}
          <div class="pm">${hh}${it.hinted_experiment_id?" · 已标实验":""}</div>
          <div class="pending-edit" id="pending-edit-${it.id}">
            <textarea id="pending-raw-${it.id}" placeholder="修改识别文字">${raw}</textarea>
            <div class="row">
              <button class="green" onclick="savePendingText(${it.id})">保存</button>
              <button class="secondary" onclick="closePendingEdit(${it.id})">取消</button>
              <span class="small" id="pending-status-${it.id}"></span>
            </div>
          </div>
        </div>
      </div>`;
    }).join("");
  } catch {}
}

function openPendingEdit(id){
  const panel = document.getElementById("pending-edit-"+id);
  const ta = document.getElementById("pending-raw-"+id);
  if(panel) panel.classList.add("open");
  if(ta) setTimeout(() => { ta.focus(); ta.selectionStart = ta.selectionEnd = ta.value.length; }, 30);
}
function closePendingEdit(id){
  const panel = document.getElementById("pending-edit-"+id);
  const st = document.getElementById("pending-status-"+id);
  if(panel) panel.classList.remove("open");
  if(st) st.textContent = "";
}
async function savePendingText(id){
  const ta = document.getElementById("pending-raw-"+id);
  const st = document.getElementById("pending-status-"+id);
  if(!ta) return;
  try {
    if(st) st.textContent = "保存中…";
    const updated = await api(`/api/inbox/${id}`, {method:"PATCH", body: JSON.stringify({text: ta.value})});
    const textNode = document.getElementById("pending-text-"+id);
    if(textNode) textNode.textContent = updated.text || (updated.audio_url ? "语音待识别或未识别到文字" : (updated.file_urls && updated.file_urls[0] ? "文件：" + updated.file_urls[0].name : "图片"));
    if(st) st.textContent = "已保存";
    setTimeout(() => closePendingEdit(id), 700);
  } catch(e){
    if(st) st.textContent = "保存失败：" + (e.message || e);
  }
}

loadExperiments();
document.getElementById("capText").addEventListener("paste", handleCapPaste);
const capCard = document.querySelector(".cap-card");
if(capCard){
  capCard.addEventListener("dragover", handleCapDragOver);
  capCard.addEventListener("dragleave", handleCapDragLeave);
  capCard.addEventListener("drop", handleCapDrop);
}
loadPending();
// refresh so background transcription text shows up; skip while editing a note
setInterval(() => { if(!document.querySelector(".pending-edit.open")) loadPending(); }, 12000);
</script>
</body>
</html>
"""


def _fill_icons(body: str, mapping: dict) -> str:
    for ph, (name, size) in mapping.items():
        body = body.replace(ph, web_ui.icon(name, size))
    return body


@app.get("/capture", response_class=HTMLResponse)
def capture_page(request: Request):
    body = _CAPTURE_BODY.replace("__ICON_JS__", web_ui.ICON_JS)
    body = _fill_icons(body, {
        "__I_INBOX__": ("inbox", 17), "__I_MIC__": ("mic", 18),
        "__I_CAM__": ("camera", 18), "__I_IMG__": ("image", 18),
        "__I_ARCH__": ("check", 18), "__I_ARR__": ("arrow-right", 15),
    })
    body = body.replace("__NAV__", _bottom_nav("capture", _flet_home_url(request)))
    return _html_response(web_ui.page_head("速记 · ELN", _CAPTURE_CSS) + body,
                          headers={"Cache-Control": "no-store, max-age=0"})


_INBOX_CSS = _NAV_CSS + """
    main { max-width:760px; }
    .entry { background:var(--card); border:1px solid var(--line); border-radius:var(--radius);
      padding:14px; box-shadow:var(--shadow); margin-bottom:14px; }
    .entry .etime { font-size:12px; color:var(--muted); }
    .entry .etext { font-size:15px; margin:6px 0; overflow-wrap:anywhere; white-space:pre-wrap; }
    .entry .emedia { display:flex; gap:8px; flex-wrap:wrap; margin:8px 0; }
    .entry .emedia a { width:88px; height:88px; border-radius:10px; overflow:hidden; border:1px solid var(--line); display:block; }
    .entry .emedia img { width:100%; height:100%; object-fit:cover; }
    .entry audio { width:100%; max-width:360px; margin-top:6px; }
    .raw-edit { margin:8px 0; background:var(--inset); border:1px solid var(--line);
      border-radius:10px; padding:8px 10px; }
    .raw-edit summary { cursor:pointer; color:var(--muted); font-size:13px; }
    .raw-edit textarea { min-height:96px; }
    .raw-edit button { margin-top:8px; }
    .ai-sug { border:1px solid var(--clay-line); background:var(--clay-soft); border-radius:12px; padding:10px 12px; margin:10px 0; color:var(--clay-ink); font-size:13.5px; line-height:1.5; }
    .ai-sug .lbl { display:inline-flex; align-items:center; gap:4px; font-weight:500; }
    .ai-sug .lbl svg { stroke-width:1.75; }
    .ai-sug .rs { font-size:12px; color:#8a5a44; margin-top:4px; overflow-wrap:anywhere; }
    .file-row { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:10px; }
    .file-row select { min-height:42px; }
    .entry .etext-edit { min-height:64px; margin-top:8px; }
    .entry-actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:12px; }
    .entry-actions button { min-height:42px; }
    .empty { text-align:center; color:var(--muted); padding:40px 0; }
    #topHint { font-size:13px; color:var(--muted); margin:2px 2px 12px; }
    .ai-bar { display:flex; align-items:center; gap:10px; background:var(--clay-soft); border:1px solid var(--clay-line);
      border-radius:var(--r-lg); padding:12px 14px; margin-bottom:14px; }
    .ai-bar .t { flex:1; min-width:0; font-size:13px; color:var(--clay-ink); line-height:1.5; }
    .ai-panel { display:none; background:var(--card); border:1px solid var(--line); border-radius:var(--r-lg);
      padding:14px; margin-bottom:14px; }
    .ai-panel.open { display:block; }
    .ai-panel pre { background:var(--inset); border:1px solid var(--line); border-radius:10px; padding:12px;
      font-size:12.5px; line-height:1.55; white-space:pre-wrap; word-break:break-word; max-height:280px; overflow:auto; }
    .chips { display:flex; gap:8px; margin:2px 2px 14px; }
    .chip { min-height:34px; padding:6px 14px; font-size:13px; border-radius:999px; background:var(--card);
      border:1px solid var(--line); color:var(--muted); box-shadow:none; }
    .chip.active { background:var(--clay-soft); border-color:var(--clay-line); color:var(--clay-ink); font-weight:600; }
    .badge { display:inline-block; padding:1px 8px; border-radius:999px; font-size:11.5px; font-weight:600; }
    .badge.pending { background:var(--clay-soft); color:var(--clay-ink); }
    .badge.filed { background:#e6f2e9; color:#2e6b45; }
    .badge.dismissed { background:var(--inset); color:var(--faint); }
    .filed-to { border:1px solid #cfe6d6; background:#eef6f0; border-radius:12px; padding:10px 12px; margin:10px 0;
      color:#2e6b45; font-size:13.5px; line-height:1.5; }
    .filed-to .lbl { display:inline-flex; align-items:center; gap:4px; font-weight:600; }
    .filed-to .lbl svg { stroke-width:1.9; }
    .filed-to .rs { color:#3d7a55; margin-top:4px; overflow-wrap:anywhere; }
    .raw-edit textarea { min-height:80px; }
"""

_INBOX_BODY = """
<body>
  <header class="app-bar">
    <a class="button secondary" href="/more">__I_BACK__ 更多</a>
    <h1>速记收件箱</h1>
    <button class="icon-btn" onclick="loadAll()" title="刷新" aria-label="刷新">__I_REFRESH__</button>
  </header>
  <main>
    <div class="ai-bar">
      <span class="t">这里回看历史速记、听录音、看照片。归档交给你开着的 Claude Code / Codex：它先把计划列给你看，你确认后直接写进实验（走订阅，不花 token）。</span>
      <button onclick="toggleAiPanel()">__I_SPARK__ AI 归档指令</button>
    </div>
    <div class="ai-panel" id="aiPanel">
      <div class="small" style="margin-bottom:8px">复制下面这段，粘贴进你的 Claude Code / Codex 对话里发送。它会读速记、看图片，先把「哪条写到哪个实验哪一步」的完整计划发给你，等你确认后才直接写入。</div>
      <pre id="aiPrompt"></pre>
      <div class="actions" style="margin-top:10px">
        <button class="green" onclick="copyPrompt()">复制指令</button>
        <button class="secondary" onclick="toggleAiPanel()">收起</button>
        <span class="small" id="copyHint"></span>
      </div>
    </div>
    <div class="chips" id="chips">
      <button class="chip active" data-f="pending" onclick="setFilter('pending')">待归档</button>
      <button class="chip" data-f="filed" onclick="setFilter('filed')">已归档</button>
      <button class="chip" data-f="all" onclick="setFilter('all')">全部</button>
    </div>
    <div id="entries"></div>
  </main>
__NAV__
<script>
__ICON_JS__
const AI_PROMPT = `帮我把 ELN 速记收件箱归档进实验记录。本地接口 http://127.0.0.1:8600（本机免密）。

重要：先把完整计划列给我看，等我明确说“确认”后，你再真正写入。别未经确认就写。

一、读取
1. GET /api/inbox?status=pending —— 待归档速记（id、text、image_urls、audio_url、hinted_experiment_id、created_at）。有图片就打开 image_urls 看清内容；只有 audio_url 而 text 为空，说明语音还没转写，提醒我别瞎猜。
2. GET /api/experiment_summaries —— 现有实验（id、name、进度）。对可能相关的实验 GET /api/experiments/{id}/steps 看每一步：id、step_index、title、description、fields（每个 key/label/type/options）、values（当前已填值）。

二、整理并把计划发给我（先别写）
注意：速记文字多半是语音输入转写来的，可能有同音字、错字、断句混乱。请结合我之前的速记和现有实验的上下文（步骤名、试剂、术语、进度）推断我到底在说什么，别被识别错字带偏；拿不准的地方在计划里标出来问我，别硬猜也别编造。
逐条速记：把关键信息提炼出来（去掉口语的重复啰嗦，忠实原意、不要编造），判断它该写到哪个实验、哪一步、哪些字段。数值只有我明确说了才填，带单位只填数字。
- 一条速记可以拆开写到多个步骤/字段。
- 那一步没有合适的字段时，可以给这步新增一个字段来装，或写进该步备注。
- 明显不属于任何现有实验的，提议新建实验（给出实验名和步骤结构，参考仓库里的 ELN_Protocol_Format.md / protocol_templates/）。
整理成一份清单：每条速记 → 目标实验/步骤 → 要写入的值 / 要新增的字段 / 要新建的实验。发给我，然后停下等我确认。

三、我确认后再写（用颗粒接口直接写；写前记下旧值，方便我让你撤销）
- 写某步：GET /api/steps/{step_id} 拿当前 values 和 fields → 把要写的值并进去 → PATCH /api/steps/{step_id}，body {"values_json":"<整个 values 的 JSON 字符串>"}。要加字段就同时传 "fields_json"（在原 fields 数组后追加 {"key","label","type"，需要时"options"}），再把值填进 values 对应 key。
- 长段观察、解释、异常、AI 总结不要塞进数字/短文本字段；写进 values["__eln_step_notes"]，内容可以用 Markdown。若旧值已有内容，就在后面追加新段落，不要覆盖。
- 新建实验：POST /api/experiments，body {"name":"...","protocol_json":"<ProtocolDefinition 的 JSON 字符串>"}，建好后按上面往它的步骤写。
- 每条写完：POST /api/inbox/{id}/filed，body {"experiment_id":3,"step_id":12,"summary":"一句话说写了什么"} —— 把它移出待办并留档。

四、全部写完，逐条告诉我写到了哪、加了什么字段、建了什么实验。哪条放错了我会让你改或撤销。`;
function toggleAiPanel(){
  const p = document.getElementById("aiPanel");
  const open = !p.classList.contains("open");
  p.classList.toggle("open", open);
  if(open) document.getElementById("aiPrompt").textContent = AI_PROMPT;
}
async function copyPrompt(){
  const h = document.getElementById("copyHint");
  try { await navigator.clipboard.writeText(AI_PROMPT); h.textContent = "已复制，去粘贴给 AI"; h.style.color = "var(--pos)"; }
  catch { const r = document.createRange(); r.selectNode(document.getElementById("aiPrompt")); getSelection().removeAllRanges(); getSelection().addRange(r); h.textContent = "已选中，按 Ctrl+C 复制"; }
}
let filter = "pending";

function esc(v){ return String(v ?? "").replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s])); }
async function api(path, opts={}){
  const res = await fetch(path, {headers:{"Content-Type":"application/json", ...(opts.headers||{})}, ...opts});
  if(!res.ok) throw new Error(await res.text());
  return res.status === 204 ? null : await res.json();
}

function setFilter(f){
  filter = f;
  for(const b of document.querySelectorAll("#chips .chip")){ b.classList.toggle("active", b.dataset.f === f); }
  loadAll();
}

async function loadAll(){
  let items = [];
  try { items = await api("/api/inbox?status=" + encodeURIComponent(filter)); } catch {}
  const root = document.getElementById("entries");
  if(!items.length){
    const msg = filter === "pending" ? "没有待归档的速记，都处理好了。"
              : filter === "filed" ? "还没有已归档的速记。" : "还没有速记。";
    root.innerHTML = '<div class="empty">' + msg + '</div>';
    return;
  }
  root.innerHTML = "";
  for(const it of items){ root.appendChild(renderEntry(it)); }
}

function statusBadge(s){
  if(s === "filed") return '<span class="badge filed">已归档</span>';
  if(s === "dismissed") return '<span class="badge dismissed">已忽略</span>';
  return '<span class="badge pending">待归档</span>';
}

function renderEntry(it){
  const el = document.createElement("div");
  el.className = "entry"; el.id = "entry-"+it.id;
  const t = new Date(it.created_at);
  const stamp = t.toLocaleString();
  const media = (it.image_urls||[]).map(u => `<a href="${esc(u)}" target="_blank"><img src="${esc(u)}"></a>`).join("");
  const audio = it.audio_url ? `<audio controls preload="none" src="${esc(it.audio_url)}"></audio>` : "";
  const bodyText = it.text ? esc(it.text) : (it.audio_url ? "<i>语音，尚未转写</i>" : "<i>（无文字）</i>");

  let filedTo = "";
  if(it.status === "filed"){
    const p = it.proposal || {};
    const where = it.filed_experiment_id
      ? `实验#${it.filed_experiment_id}${it.filed_step_id?(" · 步骤#"+it.filed_step_id):""}` : "";
    const sum = p.summary ? esc(p.summary) : "";
    if(where || sum){
      filedTo = `<div class="filed-to"><span class="lbl">${svgIcon("check",14)}已写入</span>`
        + (where?` ${where}`:"") + (sum?`<div class="rs">${sum}</div>`:"") + `</div>`;
    }
  }

  el.innerHTML = `
    <div class="etime">${stamp} · ${statusBadge(it.status)}${it.hinted_experiment_id?" · 已标实验":""}</div>
    <div class="etext">${bodyText}</div>
    <div class="emedia">${media}</div>
    ${audio}
    ${filedTo}
    <details class="raw-edit">
      <summary>修正识别文字</summary>
      <textarea id="raw-${it.id}" placeholder="识别文字">${esc(it.text||"")}</textarea>
      <button class="secondary" onclick="saveEntryText(${it.id})">保存</button>
      <span class="small" id="es-${it.id}"></span>
    </details>
    <div class="entry-actions">
      <button class="danger-ghost" onclick="deleteEntry(${it.id})">${svgIcon("trash",16)}删除</button>
    </div>`;
  return el;
}

async function saveEntryText(id){
  const st = document.getElementById("es-"+id);
  try {
    const edited = document.getElementById("raw-"+id).value;
    st.textContent = "保存中…";
    await api(`/api/inbox/${id}`, {method:"PATCH", body: JSON.stringify({text: edited})});
    st.textContent = "已保存";
  } catch(e){
    st.textContent = "保存失败："+(e.message||e);
  }
}
async function deleteEntry(id){
  if(!confirm("删除这条速记？")) return;
  await api(`/api/inbox/${id}`, {method:"DELETE"});
  loadAll();
}

loadAll();
// periodic refresh, but never rebuild the list while audio is playing (it would
// destroy the <audio> element and stop playback) or while editing a transcript
setInterval(() => {
  const playing = Array.from(document.querySelectorAll("#entries audio")).some(a => !a.paused && !a.ended && a.currentTime > 0);
  if(playing) return;
  if(document.querySelector("#entries details[open]")) return;
  loadAll();
}, 15000);
</script>
</body>
</html>
"""


@app.get("/inbox", response_class=HTMLResponse)
def inbox_page(request: Request):
    body = _INBOX_BODY.replace("__ICON_JS__", web_ui.ICON_JS)
    body = _fill_icons(body, {
        "__I_BACK__": ("chevron-left", 18), "__I_REFRESH__": ("refresh", 18),
        "__I_SPARK__": ("sparkle", 16),
    })
    body = body.replace("__NAV__", _bottom_nav("more", _flet_home_url(request)))
    return _html_response(web_ui.page_head("收件箱 · ELN", _INBOX_CSS) + body,
                          headers={"Cache-Control": "no-store, max-age=0"})


# ─────────────────────────────────────────────
# More hub + protocols / history / settings (native pages)
# ─────────────────────────────────────────────

_HUB_CSS = _NAV_CSS + """
    .hub-list { display:grid; gap:12px; }
    .hub-card { display:flex; align-items:center; gap:14px; background:var(--card); border:1px solid var(--line);
      border-radius:var(--r-lg); padding:16px; text-decoration:none; color:var(--ink); }
    .hub-card .hi { width:40px; height:40px; border-radius:10px; background:var(--inset); color:var(--clay-ink);
      display:flex; align-items:center; justify-content:center; flex:0 0 auto; }
    .hub-card .ht { flex:1; min-width:0; }
    .hub-card .ht b { font-weight:600; font-size:15px; }
    .hub-card .ht span { display:block; color:var(--muted); font-size:12.5px; margin-top:2px; }
    .hub-card .ha { color:var(--faint); }
    .about { margin-top:22px; text-align:center; color:var(--faint); font-size:12px; line-height:1.7; }
"""


@app.get("/more", response_class=HTMLResponse)
def more_page(request: Request):
    cards = [
        ("inbox", "inbox", "速记收件箱", "回看历史速记、听录音、看照片", "/inbox"),
        ("protocols", "flask", "协议库", "新建实验、导入或编辑协议", "/protocols"),
        ("changes", "clock", "改动记录", "openview 访客改了什么、评论了什么", "/changes"),
        ("settings", "settings", "设置", "AI 归档、访问信息", "/settings"),
    ]
    rows = "".join(
        f'<a class="hub-card" href="{href}"><span class="hi">{web_ui.icon(ic, 21)}</span>'
        f'<span class="ht"><b>{title}</b><span>{sub}</span></span>'
        f'<span class="ha">{web_ui.icon("chevron-right", 18)}</span></a>'
        for _k, ic, title, sub, href in cards
    )
    body = f"""
<body>
  <header class="app-bar"><h1>更多</h1></header>
  <main>
    <div class="hub-list">{rows}</div>
    <div class="about">ELN 实验记录 · 数据存储于本机 ELN_Data<br/>速记 → 收件箱 → AI 归档 → 实验记录</div>
  </main>
{_bottom_nav("more", "/")}
</body>
</html>"""
    return _html_response(web_ui.page_head("更多 · ELN", _HUB_CSS) + body,
                          headers={"Cache-Control": "no-store, max-age=0"})


_LIST_CSS = _NAV_CSS + """
    main { max-width:760px; }
    .row-card { background:var(--card); border:1px solid var(--line); border-radius:var(--r-lg);
      padding:14px; margin-bottom:12px; }
    .row-card .rt { font-weight:600; font-size:15px; overflow-wrap:anywhere; }
    .row-card .rm { color:var(--muted); font-size:12.5px; margin-top:3px; }
    .row-card .ra { display:flex; gap:8px; flex-wrap:wrap; margin-top:12px; }
    .row-card .ra button, .row-card .ra a.button { min-height:38px; padding:7px 13px; font-size:14px; }
    .badge { display:inline-block; font-size:11.5px; font-weight:500; padding:2px 9px; border-radius:999px;
      background:var(--inset); color:var(--muted); border:1px solid var(--line); }
    .badge.active { background:var(--clay-soft); color:var(--clay-ink); border-color:var(--clay-line); }
    .badge.done { background:var(--pos-soft); color:#2c5c40; border-color:#cfe2d5; }
    .empty { text-align:center; color:var(--muted); padding:44px 0; }
    .top-actions { display:flex; justify-content:flex-end; margin-bottom:12px; }
    .month { margin-bottom:14px; }
    .month > summary { list-style:none; cursor:pointer; display:flex; align-items:center; gap:8px;
      padding:9px 4px; font-weight:700; font-size:14px; color:var(--ink); border-bottom:1px solid var(--line);
      margin-bottom:12px; user-select:none; }
    .month > summary::-webkit-details-marker { display:none; }
    .month > summary .chev { transition:transform .15s ease; color:var(--faint); display:inline-flex; }
    .month[open] > summary .chev { transform:rotate(90deg); }
    .month > summary .mcount { margin-left:auto; font-weight:500; font-size:12px; color:var(--muted); }
    .modal-backdrop { position:fixed; inset:0; z-index:70; display:none; align-items:center; justify-content:center;
      background:rgba(20,18,15,.4); padding:18px; }
    .modal-backdrop.open { display:flex; }
    .modal { width:min(680px,100%); max-height:86vh; overflow:auto; background:#fff; border-radius:16px; padding:18px; }
    .modal h2 { margin:0 0 12px; }
    .modal textarea { min-height:200px; font-family:ui-monospace,Consolas,monospace; font-size:13px; }
"""

_MODAL_HTML = """
  <div id="modalBackdrop" class="modal-backdrop">
    <div class="modal">
      <h2 id="modalTitle">编辑</h2>
      <div id="modalBody"></div>
      <div class="actions">
        <button class="green" id="modalSave">保存</button>
        <button class="secondary" onclick="closeModal()">取消</button>
      </div>
      <div class="small" id="modalStatus" style="margin-top:8px"></div>
    </div>
  </div>
"""

_MODAL_JS = """
let modalSave = null;
function openModal(title, html, onSave){
  document.getElementById("modalTitle").textContent = title;
  document.getElementById("modalBody").innerHTML = html;
  document.getElementById("modalStatus").textContent = "";
  modalSave = onSave;
  document.getElementById("modalBackdrop").classList.add("open");
}
function closeModal(){ document.getElementById("modalBackdrop").classList.remove("open"); modalSave = null; }
document.getElementById("modalSave").addEventListener("click", async () => {
  if(!modalSave) return;
  try { await modalSave(); }
  catch(e){ document.getElementById("modalStatus").textContent = "失败：" + (e.message || e); }
});
function esc(v){ return String(v ?? "").replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s])); }
async function api(path, opts={}){
  const res = await fetch(path, {headers:{"Content-Type":"application/json", ...(opts.headers||{})}, ...opts});
  if(!res.ok) throw new Error(await res.text());
  return res.status === 204 ? null : await res.json();
}
"""


@app.get("/protocols", response_class=HTMLResponse)
def protocols_page(request: Request):
    body = f"""
<body>
  <header class="app-bar">
    <a class="button secondary" href="/more">{web_ui.icon('chevron-left',18)} 更多</a>
    <h1>协议库</h1>
  </header>
  <main>
    <div class="top-actions"><button onclick="importProto()">{web_ui.icon('plus',17)} 导入协议</button></div>
    <div id="list"><div class="small">加载中…</div></div>
  </main>
{_MODAL_HTML}
{_bottom_nav("more", "/")}
<script>
{web_ui.ICON_JS}
{_MODAL_JS}
async function load(){{
  let ps = [];
  try {{ ps = await api("/api/protocols"); }} catch {{}}
  const box = document.getElementById("list");
  if(!ps.length){{ box.innerHTML = '<div class="empty">还没有协议。点右上角导入一个。</div>'; return; }}
  box.innerHTML = ps.map(p => `
    <div class="row-card">
      <div class="rt">${{esc(p.name)}}</div>
      <div class="rm">v${{esc(p.version||"1.0")}}${{p.author?" · "+esc(p.author):""}} · 已用 ${{p.use_count||0}} 次</div>
      <div class="ra">
        <button class="green" onclick='startExp(${{p.id}}, ${{JSON.stringify(p.name)}})'>{web_ui.icon('plus',16)} 新建实验</button>
        <button class="secondary" onclick='editProto(${{p.id}})'>编辑</button>
        <button class="danger-ghost" onclick='delProto(${{p.id}}, ${{JSON.stringify(p.name)}})'>删除</button>
      </div>
    </div>`).join("");
}}
async function startExp(pid, name){{
  const p = await api("/api/protocols/"+pid);
  openModal("新建实验", `<div class="field"><label>实验名称</label><input id="expName" value="${{esc(name)}} ${{new Date().toLocaleDateString()}}" /></div>`, async () => {{
    const nm = document.getElementById("expName").value.trim();
    if(!nm) throw new Error("请输入实验名称");
    const exp = await api("/api/experiments", {{method:"POST", body: JSON.stringify({{name:nm, protocol_json:p.protocol_json, protocol_id:pid}})}});
    location.href = "/run?experiment_id=" + exp.id;
  }});
}}
async function editProto(pid){{
  const p = await api("/api/protocols/"+pid);
  let pretty = p.protocol_json;
  try {{ pretty = JSON.stringify(JSON.parse(p.protocol_json), null, 2); }} catch {{}}
  openModal("编辑协议", `<div class="field"><textarea id="protoJson">${{esc(pretty)}}</textarea></div>`, async () => {{
    const j = document.getElementById("protoJson").value;
    JSON.parse(j);
    await api("/api/protocols/"+pid, {{method:"PUT", body: JSON.stringify({{protocol_json:j}})}});
    closeModal(); load();
  }});
}}
function importProto(){{
  openModal("导入协议", `<p class="small">粘贴协议 JSON（protocol_name + steps）。</p><div class="field"><textarea id="protoJson" placeholder='{{"protocol_name":"…","steps":[…]}}'></textarea></div>`, async () => {{
    const j = document.getElementById("protoJson").value;
    JSON.parse(j);
    await api("/api/protocols", {{method:"POST", body: JSON.stringify({{protocol_json:j}})}});
    closeModal(); load();
  }});
}}
async function delProto(pid, name){{
  if(!confirm("删除协议「"+name+"」？")) return;
  await api("/api/protocols/"+pid, {{method:"DELETE"}});
  load();
}}
load();
</script>
</body>
</html>"""
    return _html_response(web_ui.page_head("协议库 · ELN", _LIST_CSS) + body,
                          headers={"Cache-Control": "no-store, max-age=0"})


@app.get("/history", response_class=HTMLResponse)
def history_page(request: Request):
    body = f"""
<body>
  <header class="app-bar">
    <h1>历史记录</h1>
    <button class="icon-btn" onclick="load()" title="刷新" aria-label="刷新">{web_ui.icon('refresh',18)}</button>
  </header>
  <main><div id="list"><div class="small">加载中…</div></div></main>
{_bottom_nav("history", "/")}
<script>
{web_ui.ICON_JS}
function esc(v){{ return String(v ?? "").replace(/[&<>"']/g, s => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[s])); }}
async function api(p){{ const r = await fetch(p); if(!r.ok) throw new Error(await r.text()); return r.json(); }}
const LABEL = {{active:"进行中", needs_wrapup:"待收尾", completed:"已完成", archived:"已归档", abandoned:"已放弃"}};
function cardHtml(x){{
  const active = (x.status==="active"||x.status==="needs_wrapup");
  const cls = x.status==="completed" ? "done" : (active ? "active" : "");
  const date = x.created_at ? new Date(x.created_at).toLocaleDateString() : "";
  return `<div class="row-card">
    <div class="rt">${{esc(x.name)}} <span class="badge ${{cls}}">${{LABEL[x.status]||x.status}}</span></div>
    <div class="rm">${{date}} · 步骤 ${{x.completed_steps}}/${{x.total_steps}}</div>
    <div class="ra">
      ${{active ? `<a class="button green" href="/run?experiment_id=${{x.id}}">继续</a>` : ""}}
      <a class="button secondary" href="/run/report/${{x.id}}?return_to=history">查看报告</a>
    </div>
  </div>`;
}}
function monthKey(x){{
  const d = x.created_at ? new Date(x.created_at) : null;
  return (d && !isNaN(d)) ? (d.getFullYear() + "-" + String(d.getMonth()+1).padStart(2,"0")) : "0000-00";
}}
async function load(){{
  let xs = [];
  try {{ xs = await api("/api/experiment_summaries?status=active,needs_wrapup,completed,archived,abandoned"); }} catch {{}}
  const box = document.getElementById("list");
  if(!xs.length){{ box.innerHTML = '<div class="empty">还没有实验记录。</div>'; return; }}
  xs.sort((a,b) => String(b.created_at||"").localeCompare(String(a.created_at||"")));
  const groups = {{}};
  for(const x of xs){{ const k = monthKey(x); (groups[k] = groups[k] || []).push(x); }}
  const now = new Date();
  const curKey = now.getFullYear() + "-" + String(now.getMonth()+1).padStart(2,"0");
  const chev = svgIcon("chevron-right", 16);
  box.innerHTML = Object.keys(groups).sort().reverse().map(key => {{
    const items = groups[key];
    const label = key === "0000-00" ? "未知日期"
      : (parseInt(key.slice(0,4),10) + "年" + parseInt(key.slice(5,7),10) + "月");
    const open = key === curKey ? " open" : "";
    return `<details class="month"${{open}}>
      <summary><span class="chev">${{chev}}</span>${{label}}<span class="mcount">${{items.length}}</span></summary>
      ${{items.map(cardHtml).join("")}}
    </details>`;
  }}).join("");
}}
load();
</script>
</body>
</html>"""
    return _html_response(web_ui.page_head("历史记录 · ELN", _LIST_CSS) + body,
                          headers={"Cache-Control": "no-store, max-age=0"})


_SETTINGS_CSS = _NAV_CSS + """
    main { max-width:600px; }
    section { margin-bottom:14px; }
    .field { margin-bottom:14px; }
    .field label { display:block; margin-bottom:6px; }
    .save-row { margin-top:6px; }
    #saveHint { margin-left:10px; font-size:13px; }
    .about { margin-top:22px; color:var(--faint); font-size:12px; line-height:1.7; }
    details.help { border-top:1px solid var(--line); padding:12px 0 2px; }
    details.help:first-of-type { border-top:0; }
    details.help > summary { cursor:pointer; font-weight:500; font-size:14.5px; list-style:none;
      display:flex; align-items:center; justify-content:space-between; gap:8px; }
    details.help > summary::-webkit-details-marker { display:none; }
    details.help > summary::after { content:"+"; color:var(--faint); font-size:18px; }
    details.help[open] > summary::after { content:"–"; }
    .help-body { font-size:13.5px; color:var(--muted); line-height:1.7; padding:8px 0 4px; }
    .help-body b { color:var(--ink); font-weight:500; }
    .help-body code, .help-body pre { font-family:ui-monospace,Consolas,monospace; }
    .help-body code { background:var(--inset); border:1px solid var(--line); border-radius:5px; padding:1px 5px; font-size:12.5px; }
    .help-body pre { background:var(--inset); border:1px solid var(--line); border-radius:10px; padding:12px; overflow:auto; font-size:12px; color:var(--ink); white-space:pre; }
    .help-body ul { margin:6px 0 6px 20px; padding:0; }
    .help-body li { margin-bottom:3px; }
"""

_HELP_HTML = """
    <section>
      <h2>使用说明</h2>
      <details class="help"><summary>怎么随手记录（速记）</summary>
        <div class="help-body">在「速记」页直接打字、点话筒说话（自动转文字）、或拍照，点<b>打包存档</b>就进收件箱——<b>不用先打开任何实验</b>。回头在「收件箱」里选实验和步骤<b>写入记录</b>；配好 AI 后，AI 会先建议放到哪一步，你确认即可。</div>
      </details>
      <details class="help"><summary>实验里怎么填数据</summary>
        <div class="help-body">从「协议库」的某个协议<b>新建实验</b>后进入实验页：逐步查看说明、在<b>记录数据</b>里填字段、写<b>备注</b>、需要就<b>拍照</b>；有计时的步骤可开始/暂停计时（电脑端到点响铃）。填完点<b>完成步骤</b>进入下一步，最后可结束并查看/保存报告。</div>
      </details>
      <details class="help"><summary>协议（protocol）格式</summary>
        <div class="help-body">
          协议是一段 JSON，描述一个实验有哪些步骤、每步记录什么。顶层字段：
          <ul>
            <li><code>protocol_name</code> 协议名，必填</li>
            <li><code>version</code> / <code>author</code> 版本、作者，可选</li>
            <li><code>steps</code> 步骤数组，必填</li>
            <li><code>storage_items</code> 预设储存物品，可选</li>
          </ul>
          每个 step：<code>title</code>、<code>description</code>（支持 Markdown，换行用 <code>\\n</code>）、<code>timer_seconds</code>（秒，30 分钟写 1800）、<code>has_camera</code>、<code>fields</code>。<br/>
          每个 field：<code>key</code>（英文唯一）、<code>label</code>（显示名）、<code>type</code>（<code>text</code>/<code>number</code>/<code>dropdown</code>）、<code>default</code>、<code>required</code>、<code>options</code>（下拉才需要）。
          <pre>{
  "protocol_name": "Colony PCR",
  "version": "1.0",
  "steps": [
    {
      "title": "配制反应体系",
      "description": "冰上配制，总体积 20 µL",
      "timer_seconds": 0,
      "has_camera": false,
      "fields": [
        {"key": "template_volume", "label": "模板用量 (µL)",
         "type": "number", "default": "1", "required": true, "options": []}
      ]
    },
    {
      "title": "PCR 扩增",
      "description": "放入 PCR 仪，运行 30 分钟",
      "timer_seconds": 1800,
      "fields": []
    }
  ]
}</pre>
          在「协议库 → 导入协议」里粘贴这段 JSON 即可。
        </div>
      </details>
      <details class="help"><summary>储存物品输入格式</summary>
        <div class="help-body">实验结束时可登记要冻存/保存的样品，一行一个，格式：<br/><code>样品名 | 管型 | 备注</code>，例如：<br/><code>PCR 产物 | 1.5mL EP管 | sample A</code><br/>也可以只写样品名。之后选 Box、点格子完成位置登记。</div>
      </details>
      <details class="help"><summary>AI 归档怎么用</summary>
        <div class="help-body">推荐用电脑上开着的 Claude Code / Codex（走订阅、不花 token、能看图）：打开 更多 → 速记收件箱，点「AI 归档指令」复制那段话发给它。它会读速记、<b>先把归档计划列给你看</b>，你确认后再直接写进实验（能分散写多处、缺字段会补、必要时新建实验）。上面这份模型服务/密钥是给实验页「AI 整理」按钮用的，可不填。</div>
      </details>
    </section>
"""


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    lan = ""
    try:
        from server.startup import get_local_ip, get_api_port
        lan = f"http://{get_local_ip()}:{get_api_port()}"
    except Exception:
        pass
    body = f"""
<body>
  <header class="app-bar">
    <a class="button secondary" href="/more">{web_ui.icon('chevron-left',18)} 更多</a>
    <h1>设置</h1>
  </header>
  <main>
    <section>
      <h2>AI 归档语音速记</h2>
      <p class="small" style="margin:0 0 14px">配置后，AI 能把收件箱里的口语整理成规范记录（先给建议，你在收件箱确认后才写入）。</p>
      <div class="field"><label>模型服务</label>
        <select id="provider"><option value="claude">Claude (Anthropic)</option><option value="openai">OpenAI / 兼容接口</option></select></div>
      <div class="field"><label>API 密钥</label>
        <input id="apiKey" type="password" placeholder="留空表示不修改已保存的密钥" autocomplete="off" /></div>
      <div class="field"><label>模型</label>
        <input id="model" placeholder="claude-opus-4-8 / gpt-4o-mini" /></div>
      <div class="field"><label>自定义地址（可选）</label>
        <input id="baseUrl" placeholder="兼容接口填 base_url，官方留空" /></div>
      <div class="save-row"><button class="green" onclick="saveAi()">保存</button><span class="small" id="saveHint"></span></div>
    </section>
    <section>
      <h2>语音转写</h2>
      <p class="small" style="margin:0 0 14px">配置后，录音会先保存到本机，再调用转写 API，把文字回填到速记或实验语音记录。</p>
      <div class="field"><label>转写服务</label>
        <select id="txProvider"><option value="local">本地 faster-whisper</option><option value="tencent">腾讯云 ASR</option><option value="openai">OpenAI Speech-to-Text</option></select></div>
      <div class="field"><label>腾讯云 SecretId</label>
        <input id="txSecretId" type="password" placeholder="留空表示不修改已保存的 SecretId" autocomplete="off" /></div>
      <div class="field"><label>腾讯云 SecretKey</label>
        <input id="txSecretKey" type="password" placeholder="留空表示不修改已保存的 SecretKey" autocomplete="off" /></div>
      <div class="field"><label>地域</label>
        <input id="txRegion" placeholder="ap-shanghai" /></div>
      <div class="field"><label>识别引擎</label>
        <input id="txEngine" placeholder="16k_zh" /></div>
      <div class="field"><label>OpenAI API Key</label>
        <input id="openaiKey" type="password" placeholder="留空表示不修改已保存的 OpenAI key" autocomplete="off" /></div>
      <div class="field"><label>OpenAI 模型</label>
        <input id="openaiModel" placeholder="gpt-4o-mini-transcribe" /></div>
      <div class="field"><label>OpenAI Base URL（可选）</label>
        <input id="openaiBaseUrl" placeholder="https://api.openai.com/v1" /></div>
      <div class="save-row"><button class="green" onclick="saveTranscription()">保存语音转写</button><span class="small" id="txHint"></span></div>
    </section>
{_HELP_HTML}
    <div class="about">局域网地址：{lan or "启动后可见"}<br/>数据存储于本机 ELN_Data，升级代码不影响数据。</div>
  </main>
{_bottom_nav("more", "/")}
<script>
async function api(p, opts={{}}){{ const r = await fetch(p, {{headers:{{"Content-Type":"application/json", ...(opts.headers||{{}})}}, ...opts}}); if(!r.ok) throw new Error(await r.text()); return r.status===204?null:r.json(); }}
async function load(){{
  try {{
    const s = await api("/api/settings/ai");
    document.getElementById("provider").value = s.provider || "claude";
    document.getElementById("model").value = s.model || "";
    document.getElementById("baseUrl").value = s.base_url || "";
    if(s.has_key) document.getElementById("apiKey").placeholder = "已设置（留空表示不修改）";
  }} catch {{}}
  try {{
    const t = await api("/api/settings/transcription");
    document.getElementById("txProvider").value = t.provider || "local";
    document.getElementById("txRegion").value = t.tencent_region || "ap-shanghai";
    document.getElementById("txEngine").value = t.tencent_engine || "16k_zh";
    document.getElementById("openaiModel").value = t.openai_model || "gpt-4o-mini-transcribe";
    document.getElementById("openaiBaseUrl").value = t.openai_base_url || "https://api.openai.com/v1";
    if(t.has_tencent_secret_id) document.getElementById("txSecretId").placeholder = "已设置（留空表示不修改）";
    if(t.has_tencent_secret_key) document.getElementById("txSecretKey").placeholder = "已设置（留空表示不修改）";
    if(t.has_openai_api_key) document.getElementById("openaiKey").placeholder = "已设置（留空表示不修改）";
  }} catch {{}}
}}
async function saveAi(){{
  const hint = document.getElementById("saveHint");
  hint.textContent = "保存中…"; hint.style.color = "var(--muted)";
  try {{
    await api("/api/settings/ai", {{method:"POST", body: JSON.stringify({{
      provider: document.getElementById("provider").value,
      api_key: document.getElementById("apiKey").value,
      model: document.getElementById("model").value,
      base_url: document.getElementById("baseUrl").value,
    }})}});
    document.getElementById("apiKey").value = "";
    hint.textContent = "已保存"; hint.style.color = "var(--pos)";
    load();
  }} catch(e){{ hint.textContent = "失败：" + (e.message||e); hint.style.color = "var(--neg)"; }}
}}
async function saveTranscription(){{
  const hint = document.getElementById("txHint");
  hint.textContent = "保存中…"; hint.style.color = "var(--muted)";
  try {{
    await api("/api/settings/transcription", {{method:"POST", body: JSON.stringify({{
      provider: document.getElementById("txProvider").value,
      tencent_secret_id: document.getElementById("txSecretId").value,
      tencent_secret_key: document.getElementById("txSecretKey").value,
      tencent_region: document.getElementById("txRegion").value,
      tencent_engine: document.getElementById("txEngine").value,
      openai_api_key: document.getElementById("openaiKey").value,
      openai_model: document.getElementById("openaiModel").value,
      openai_base_url: document.getElementById("openaiBaseUrl").value,
    }})}});
    document.getElementById("txSecretId").value = "";
    document.getElementById("txSecretKey").value = "";
    document.getElementById("openaiKey").value = "";
    hint.textContent = "已保存"; hint.style.color = "var(--pos)";
    load();
  }} catch(e){{ hint.textContent = "失败：" + (e.message||e); hint.style.color = "var(--neg)"; }}
}}
load();
</script>
</body>
</html>"""
    return _html_response(web_ui.page_head("设置 · ELN", _SETTINGS_CSS) + body,
                          headers={"Cache-Control": "no-store, max-age=0"})


# ─────────────────────────────────────────────
# Native web experiment runner
# ─────────────────────────────────────────────

_RUNNER_CSS = """
    header.app-bar .icon-btn {
      min-height:40px; min-width:40px; padding:6px 10px; border-radius:11px;
      background:#f1efeb; color:#43413d; box-shadow:none; font-size:17px; font-weight:600;
      display:inline-flex; align-items:center; justify-content:center; text-decoration:none;
    }
    .exp-wrap { flex:1; min-width:0; }
    .exp-wrap select {
      border:0; background:transparent; font-weight:700; font-size:16px;
      padding:6px 4px; min-height:40px; width:100%;
      white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
    }
    .exp-wrap select:focus { box-shadow:none; }
    .status { font-size:12px; color:var(--muted); }
    #net { font-size:12px; font-weight:600; white-space:nowrap; }
    .queue-info { color:var(--muted); font-size:12.5px; margin:2px 4px 8px; min-height:0; }
    .card { background:var(--card); border:1px solid var(--line); border-radius:var(--radius); padding:16px; margin:0 0 14px; box-shadow:var(--shadow); }

    #board { display:none; }
    #boardTitle { display:none; flex:1; min-width:0; font-weight:800; font-size:17px;
      white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .board-card { background:var(--card); border:1px solid var(--line); border-radius:var(--radius);
      box-shadow:var(--shadow); padding:14px 16px; margin:0 0 12px; cursor:pointer;
      transition:border-color .12s ease, transform .06s ease; }
    .board-card:hover { border-color:var(--clay-line, #e2cdbf); }
    .board-card:active { transform:scale(.994); }
    .bc-top { display:flex; align-items:center; gap:10px; margin-bottom:9px; }
    .bc-name { flex:1; min-width:0; font-weight:800; font-size:16px; overflow-wrap:anywhere; }
    .bc-badge { flex:0 0 auto; font-size:11.5px; font-weight:700; padding:2px 9px; border-radius:999px;
      background:#efece7; color:#7a756d; white-space:nowrap; }
    .bc-badge.s-active { background:#f6e8df; color:#8a5a44; }
    .bc-badge.s-needs_wrapup { background:#fdf1d6; color:#8a6d1e; }
    .bc-foot { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-top:8px; }
    .bc-foot-l { display:flex; align-items:center; gap:8px; min-width:0; }
    .bc-meta { font-size:12.5px; color:var(--muted); }
    .bc-timer { display:inline-flex; align-items:center; gap:4px; font-size:12px; font-weight:700;
      font-variant-numeric:tabular-nums; color:#8a6d1e; background:#fdf1d6; border-radius:999px; padding:2px 8px; white-space:nowrap; }
    .bc-timer.over { color:#fff; background:#a63a24; }
    .bc-timer svg { width:12px; height:12px; stroke:currentColor; fill:none; stroke-width:2; }
    .bc-abandon { min-height:0; padding:2px 4px; background:none; box-shadow:none; border:0;
      color:var(--faint); font-size:12px; font-weight:500; cursor:pointer; }
    .bc-abandon:hover { color:#c0503a; text-decoration:underline; }
    .board-empty { text-align:center; color:var(--muted); padding:44px 12px; line-height:1.9; }

    .chips { display:flex; gap:7px; overflow-x:auto; padding:2px 2px 10px; scrollbar-width:none; }
    .chips::-webkit-scrollbar { display:none; }
    .chip {
      flex:0 0 auto; min-width:34px; min-height:34px; padding:0 6px; border-radius:999px;
      background:#efece7; color:#7a756d; font-weight:700; font-size:13.5px; box-shadow:none;
    }
    .chip.done { background:var(--green-soft); color:#1d6f3f; }
    .chip.cur { background:var(--clay); color:#fff; }

    .stepper { display:flex; align-items:center; gap:12px; margin:2px 0 10px; }
    .stepper button { min-width:76px; min-height:38px; }
    .progress { flex:1; height:7px; border-radius:999px; background:#eceae4; overflow:hidden; }
    .progress > div { height:100%; border-radius:999px; background:var(--clay); transition:width .25s ease; }

    .step-title { font-size:20px; font-weight:800; line-height:1.3; margin:2px 0 4px; letter-spacing:.01em; }
    .desc { line-height:1.65; color:#3c3934; font-size:15px; }
    .desc p { margin:0 0 10px; }
    .desc h1, .desc h2, .desc h3 { margin:14px 0 8px; line-height:1.25; color:var(--ink); }
    .desc h1 { font-size:20px; } .desc h2 { font-size:17px; } .desc h3 { font-size:15.5px; }
    .desc ul, .desc ol { margin:8px 0 10px 22px; padding:0; }
    .desc li { margin-bottom:3px; }
    .desc table { border-collapse:collapse; width:100%; margin:10px 0; font-size:14px; }
    .desc th, .desc td { border:1px solid var(--line); padding:7px 9px; vertical-align:top; }
    .desc th { background:#faf8f4; font-weight:700; }
    .desc code { background:#f3f0ea; border-radius:5px; padding:1px 5px; font-size:.92em; }
    .desc pre { background:#f8f6f1; border:1px solid var(--line); border-radius:10px; padding:10px; overflow:auto; }
    .desc blockquote { border-left:3px solid var(--accent); margin:8px 0; padding:3px 12px; color:#6b665e; background:var(--accent-soft); border-radius:0 8px 8px 0; }

    .field { margin-top:12px; }
    .field label { display:block; margin-bottom:5px; }
    .field textarea { min-height:150px; }
    .notes textarea { min-height:88px; }
    .md-slot { display:flex; flex-direction:column; gap:8px; align-items:flex-start; }
    .md-slot .md-line { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
    .md-chip {
      min-height:28px; padding:3px 9px; border-radius:999px; border-color:var(--line-strong);
      background:#f1f0eb; color:var(--muted); font-size:12.5px; font-weight:500;
    }
    .md-chip:hover { color:var(--ink); background:#ebe8df; }
    .md-slot.has-md .md-chip { color:var(--clay-ink); background:var(--clay-soft); border-color:var(--clay-line); }
    .md-preview { width:100%; border:1px solid var(--line); border-radius:10px; background:#fbfaf7; padding:10px 12px;
      color:var(--ink); font-size:14px; line-height:1.6; overflow-wrap:anywhere; }
    .md-preview.empty { color:var(--faint); font-size:13px; }
    .md-preview :first-child { margin-top:0; }
    .md-preview :last-child { margin-bottom:0; }
    .md-preview pre { white-space:pre-wrap; word-break:break-word; background:#f4f1ea; border:1px solid var(--line); border-radius:8px; padding:8px; }
    .md-preview code { background:#f0ede6; padding:1px 4px; border-radius:4px; }
    .md-preview table { width:100%; border-collapse:collapse; margin:8px 0; font-size:13px; }
    .md-preview th, .md-preview td { border:1px solid var(--line); padding:5px 7px; text-align:left; vertical-align:top; }
    .md-box { display:none; width:100%; }
    .md-slot.editing .md-box { display:block; }
    .md-slot.editing .md-preview { display:none; }
    .md-box textarea { min-height:116px; font-family:ui-monospace, "SF Mono", Consolas, monospace; font-size:13.5px; }
    .field-md { align-items:flex-start; }
    .field-md .field-preview {
      width:100%; min-height:40px; border:1px solid var(--line-strong); border-radius:var(--r);
      background:#fff; padding:9px 12px; cursor:text; font-size:14.5px; line-height:1.55; overflow-wrap:anywhere;
    }
    .field-md .field-preview.empty { color:var(--faint); }
    .field-md .field-preview :first-child { margin-top:0; }
    .field-md .field-preview :last-child { margin-bottom:0; }
    .field-md .field-preview pre { white-space:pre-wrap; word-break:break-word; background:#f4f1ea; border:1px solid var(--line); border-radius:8px; padding:8px; }
    .field-md .field-preview code { background:#f0ede6; padding:1px 4px; border-radius:4px; }
    .field-md .field-preview table { width:100%; border-collapse:collapse; margin:8px 0; font-size:13px; }
    .field-md .field-preview th, .field-md .field-preview td { border:1px solid var(--line); padding:5px 7px; text-align:left; vertical-align:top; }
    .field-md textarea { display:none; min-height:78px; font-size:14.5px; }
    .field-md.editing .field-preview { display:none; }
    .field-md.editing textarea { display:block; }
    .done { color:var(--green); font-weight:700; }

    .photo-row { display:flex; flex-direction:column; gap:10px; margin-top:10px; }
    .photo-row input[type=text] { width:100%; }
    .pr-btns { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; }
    .pr-btns .button, .pr-btns button { width:100%; min-height:40px; padding:7px 6px; font-size:14px; }
    .pr-submit { display:flex; gap:10px; align-items:center; }
    .pr-submit .small { color:var(--muted); }
    input[type=file] { position:absolute; left:-9999px; width:1px; height:1px; opacity:0; }
    .photos { display:grid; grid-template-columns:repeat(auto-fill, minmax(150px, 1fr)); gap:10px; align-items:start; }
    .photos a { color:var(--accent-strong); }
    .attachment-item { display:flex; align-items:center; gap:6px; min-width:0; }
    .attachment-item.file { min-height:42px; border:1px solid var(--line); border-radius:10px; padding:8px 10px; background:#fbfaf7; }
    .attachment-item.file a { min-width:0; overflow-wrap:anywhere; font-size:13.5px; }
    .attachment-item.image { display:grid; gap:5px; }
    .attachment-preview { display:block; width:100%; aspect-ratio:4/3; overflow:hidden; border:1px solid var(--line); border-radius:10px; background:#f3f1ec; }
    .attachment-preview img { display:block; width:100%; height:100%; object-fit:cover; }
    .attachment-caption { display:flex; align-items:center; gap:4px; min-width:0; }
    .attachment-caption > a { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:13px; }
    .attachment-rename { width:28px; height:28px; min-height:28px; padding:0; border-radius:7px; background:transparent; color:var(--accent-strong); font-size:16px; box-shadow:none; }

    .timer { border:1px solid #f6ddba; background:var(--inset); border-radius:var(--radius); padding:14px; margin-top:14px; }
    .timer-display { font-size:40px; font-weight:800; color:var(--accent-strong); font-variant-numeric:tabular-nums; line-height:1.1; }
    .timer-edit { display:flex; gap:8px; align-items:center; margin-top:6px; }
    .timer-edit input { width:74px; }
    .timer.over { background:var(--neg-soft); border-color:#e8cabf; }
    .timer.over .timer-display { color:var(--red); }
    .timer .actions { margin-top:10px; }
    .timer .actions button { min-height:40px; min-width:72px; }

    .section-head { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-top:16px; }
    .section-head h2 { margin:0; font-size:13px; color:var(--muted); font-weight:700; text-transform:uppercase; letter-spacing:.06em; }
    .edit-link { background:transparent; color:var(--accent-strong); box-shadow:none; min-height:30px; padding:2px 6px; font-size:13px; font-weight:600; }
    .wrapup { border:1px solid #cbe7d3; background:var(--pos-soft); border-radius:var(--radius); padding:14px; margin-top:16px; }

    .main-actions { margin-top:18px; display:flex; flex-wrap:wrap; gap:10px; align-items:center; }
    .main-actions button { flex:1 1 40%; min-height:48px; }
    .main-actions .status { flex-basis:100%; text-align:center; order:3; font-size:12.5px; color:var(--muted); }
    .main-actions .status:empty { display:none; }

    .modal-backdrop { position:fixed; inset:0; z-index:70; display:none; align-items:center; justify-content:center; background:rgba(20,18,15,.4); padding:18px; }
    .modal-backdrop.open { display:flex; }
    .modal { width:min(720px,100%); max-height:88vh; overflow:auto; background:#fff; border-radius:18px; padding:18px; box-shadow:0 18px 50px rgba(0,0,0,.25); }
    .modal h2 { margin:0 0 12px; font-size:17px; }
    .modal textarea { min-height:200px; }

    /* Voice notes */
    .voice-list { display:grid; gap:8px; margin-top:10px; }
    .voice-note { display:flex; gap:10px; align-items:flex-start; background:#fbfaf7; border:1px solid var(--line); border-radius:12px; padding:10px 12px; }
    .voice-note .vtime { flex:0 0 auto; font-size:12px; color:var(--muted); font-variant-numeric:tabular-nums; padding-top:2px; }
    .voice-note .vbody { flex:1; min-width:0; font-size:14.5px; overflow-wrap:anywhere; }
    .voice-note .vbody audio { width:100%; margin-top:4px; }
    .voice-note .vtag { display:inline-block; font-size:11.5px; font-weight:700; color:#9a6a00; background:#fff3d6; border-radius:6px; padding:1px 7px; margin-left:6px; }
    .voice-note .vops { flex:0 0 auto; display:flex; gap:2px; }
    .voice-note .vops button { min-height:28px; min-width:28px; padding:0; background:transparent; box-shadow:none; color:var(--muted); font-size:14px; }

    #micBtn.rec { background:var(--clay); border-color:var(--clay); color:#fff; animation:elnMicPulse 1.2s ease infinite; }
    @keyframes elnMicPulse { 50% { opacity:.7; } }

    .sheet-backdrop { position:fixed; inset:0; z-index:65; display:none; background:rgba(20,18,15,.4); }
    .sheet-backdrop.open { display:block; }
    .sheet {
      position:fixed; left:0; right:0; bottom:0; z-index:66; display:none;
      background:#fff; border-radius:20px 20px 0 0; box-shadow:0 -10px 40px rgba(0,0,0,.25);
      padding:16px 16px calc(16px + env(safe-area-inset-bottom,0px));
      max-height:82vh; overflow:auto; max-width:720px; margin:0 auto;
    }
    .sheet.open { display:block; }
    .sheet h2 { font-size:16px; margin:0 0 4px; }
    .sheet .grab { width:40px; height:4px; border-radius:99px; background:#ddd8d0; margin:0 auto 12px; }
    #voiceLive { min-height:22px; color:var(--accent-strong); font-size:14px; margin:8px 0 2px; }
    #voiceText { min-height:96px; margin-top:6px; }

    .ai-step { border:1px solid var(--line); border-radius:12px; padding:12px; margin:10px 0; background:#fbfaf7; }
    .ai-step h3 { margin:0 0 8px; font-size:14px; }
    .ai-step textarea { min-height:66px; }
    .ai-field { display:grid; grid-template-columns:1fr auto; gap:8px; align-items:center; margin-top:8px; padding:8px 10px; border:1px solid var(--line); border-radius:10px; background:#fff; }
    .ai-field .fl { min-width:0; }
    .ai-field .fl .k { font-weight:600; font-size:13.5px; }
    .ai-field .fl .chg { font-size:12px; color:var(--muted); overflow-wrap:anywhere; }
    .ai-field .fl .chg b { color:var(--accent-strong); }
    .ai-field .fl .rs { font-size:11.5px; color:#9a938a; overflow-wrap:anywhere; }
    .ai-field input[type=checkbox] { width:22px; height:22px; }
    .ai-unassigned { border:1px dashed #d9c48f; background:#fffbf0; border-radius:10px; padding:10px; margin-top:10px; font-size:13px; }
    .ai-applied { color:var(--green); font-weight:700; font-size:12.5px; margin-top:6px; }
    .voice-controls { display:flex; gap:10px; align-items:center; margin-top:10px; }
    .voice-controls button { flex:1; min-height:46px; }
    #voiceRecBtn.rec { background:#a63a24; }
    .voice-all { margin-top:16px; }
"""

_RUNNER_BODY = """
<body>
  <header class="app-bar">
    <button class="icon-btn" id="boardBackBtn" onclick="showBoard()" title="返回看板" aria-label="返回看板" style="display:none">__I_BACK__</button>
    <div class="exp-wrap" id="expWrap">
      <select id="experimentSelect" onchange="selectExperiment(this.value)" aria-label="选择实验"></select>
    </div>
    <span id="boardTitle">实验看板</span>
    <span id="net" class="status">连接中</span>
    <button class="icon-btn" id="micBtn" onclick="openVoicePanel()" title="语音速记" aria-label="语音速记">__I_MIC__</button>
    <button class="icon-btn" onclick="refreshCurrent()" title="刷新" aria-label="刷新">__I_REFRESH__</button>
  </header>
  <main>
    <div id="board"></div>
    <div id="queueInfo" class="queue-info"></div>
    <section id="steps"></section>
  </main>
  <div id="modalBackdrop" class="modal-backdrop">
    <div class="modal">
      <h2 id="modalTitle">编辑</h2>
      <div id="modalBody"></div>
      <div class="actions">
        <button id="modalSave">保存</button>
        <button class="secondary" onclick="closeModal()">取消</button>
      </div>
      <div id="modalStatus" class="status"></div>
    </div>
  </div>

  <div id="voiceBackdrop" class="sheet-backdrop" onclick="closeVoicePanel()"></div>
  <div id="voiceSheet" class="sheet">
    <div class="grab"></div>
    <h2>语音速记</h2>
    <div class="small" id="voiceHint"></div>
    <div id="voiceLive"></div>
    <textarea id="voiceText" placeholder="说完的内容出现在这里，可以先修改再保存"></textarea>
    <div class="voice-controls">
      <button id="voiceRecBtn" onclick="toggleVoiceRec()">开始说话</button>
      <button class="green" onclick="saveVoiceText()">存入当前步骤</button>
    </div>
    <div class="voice-controls" style="margin-top:8px">
      <button class="secondary" onclick="runAiOrganize()">__I_SPARK__ AI 整理全部速记</button>
    </div>
    <div class="small" id="aiHint" style="margin-top:4px"></div>
    <div class="voice-all">
      <div class="section-head" style="margin-top:0"><h2>本实验全部速记</h2></div>
      <div id="voiceAllList" class="voice-list"></div>
    </div>
  </div>

  <div id="aiBackdrop" class="sheet-backdrop" onclick="closeAiPanel()"></div>
  <div id="aiSheet" class="sheet">
    <div class="grab"></div>
    <h2>AI 整理草稿</h2>
    <div class="small" id="aiDraftHint">AI 已把你的口语整理成下面的草稿。确认无误再写入记录，数字类字段请核对。</div>
    <div id="aiDraftBody"></div>
    <div class="voice-controls">
      <button class="green" onclick="applyAllAi()">全部写入记录</button>
      <button class="secondary" onclick="closeAiPanel()">关闭</button>
    </div>
  </div>

<script>
__ICON_JS__
const LS = {
  experiments: "eln.mobile.experiments",
  selected: "eln.mobile.selectedExperiment",
  stepIndexPrefix: "eln.mobile.stepIndex.",
  stepsPrefix: "eln.mobile.steps.",
  draftsPrefix: "eln.mobile.drafts.",
  descPrefix: "eln.mobile.desc.",
  timerPrefix: "eln.mobile.timer.",
  timers: "eln.mobile.timers",
  queue: "eln.mobile.queue"
};

let selectedExperiment = new URLSearchParams(window.location.search).get("experiment_id") || localStorage.getItem(LS.selected) || "";
let focusStepIdParam = new URLSearchParams(window.location.search).get("step_id") || "";
let steps = [];
let experiments = [];
let voiceNotes = [];
const STEP_NOTES_KEY = "__eln_step_notes";
const timerSync = {};
const timerLastSync = {};
const initializedStepPosition = {};
let modalSaveHandler = null;

function getQueue(){ try { return JSON.parse(localStorage.getItem(LS.queue) || "[]"); } catch { return []; } }
function setQueue(q){ localStorage.setItem(LS.queue, JSON.stringify(q)); renderQueueInfo(); }
function getTimers(){ try { return JSON.parse(localStorage.getItem(LS.timers) || "{}"); } catch { return {}; } }
function setTimers(t){ localStorage.setItem(LS.timers, JSON.stringify(t)); }
function enqueue(job){
  const q = getQueue();
  q.push({...job, id: Date.now() + "-" + Math.random().toString(16).slice(2)});
  setQueue(q);
}
function stepKey(expId){ return LS.stepsPrefix + expId; }
function stepIndexKey(expId){ return LS.stepIndexPrefix + expId; }
function draftKey(stepId){ return LS.draftsPrefix + stepId; }
function descKey(stepId){ return LS.descPrefix + stepId; }
function timerKey(stepId){ return LS.timerPrefix + stepId; }
function net(text, ok=true){ const el=document.getElementById("net"); el.textContent=text; el.style.color=ok ? "#43a047" : "#d98200"; }
function renderQueueInfo(){ const n = getQueue().length; document.getElementById("queueInfo").textContent = n ? ("待同步：" + n + " 项") : ""; }

async function api(path, opts={}){
  const res = await fetch(path, {headers: {"Content-Type":"application/json", ...(opts.headers||{})}, ...opts});
  if(!res.ok) throw new Error(await res.text());
  return res.status === 204 ? null : await res.json();
}

async function loadExperiments(){
  try {
    const active = await api("/api/experiments?status=active");
    const wrap = await api("/api/experiments?status=needs_wrapup");
    experiments = [...active, ...wrap];
    localStorage.setItem(LS.experiments, JSON.stringify(experiments));
    renderExperiments(experiments);
    net("已连接", true);
  } catch(e) {
    net("离线缓存", false);
    experiments = JSON.parse(localStorage.getItem(LS.experiments) || "[]");
    renderExperiments(experiments);
  }
  return experiments;
}

let view = "board";
const STATUS_LABEL = {active:"进行中", needs_wrapup:"待收尾", completed:"已完成", abandoned:"已放弃", archived:"已归档"};

function setHeaderMode(v){
  view = v;
  const board = v === "board";
  document.getElementById("boardBackBtn").style.display = board ? "none" : "inline-flex";
  document.getElementById("expWrap").style.display = board ? "none" : "block";
  document.getElementById("boardTitle").style.display = board ? "block" : "none";
  document.getElementById("micBtn").style.display = board ? "none" : "inline-flex";
  document.getElementById("board").style.display = board ? "block" : "none";
  document.getElementById("steps").style.display = board ? "none" : "block";
  document.getElementById("queueInfo").style.display = board ? "none" : "block";
  const dock = document.getElementById("elnDock");   // floating timer dock: only inside an experiment
  if(dock) dock.style.display = board ? "none" : "flex";
}

async function showBoard(){
  setHeaderMode("board");
  renderBoard(experiments);
  await loadBoardTimers();
  boardTimerKeys = Object.keys(boardTimers).sort().join(",");
  if(view === "board") renderBoard(experiments);
}

function renderBoard(exps){
  const root = document.getElementById("board");
  if(!exps || !exps.length){
    root.innerHTML = '<div class="board-empty">还没有进行中的实验。<br><a href="/protocols">去协议库新建实验 →</a></div>';
    return;
  }
  root.innerHTML = exps.map(e => {
    const total = e.total_steps || 0, done = e.completed_steps || 0;
    const pct = total ? Math.round(done / total * 100) : 0;
    const label = STATUS_LABEL[e.status] || e.status || "";
    const timerChip = boardTimers[String(e.id)]
      ? `<span class="bc-timer" id="bctimer-${e.id}">${svgIcon("timer",12)}<span class="tv"></span></span>` : "";
    return `<div class="board-card" onclick="enterExperiment('${e.id}')">
      <div class="bc-top"><span class="bc-name">${esc(e.name)}</span><span class="bc-badge s-${esc(e.status)}">${esc(label)}</span></div>
      <div class="progress"><div style="width:${pct}%"></div></div>
      <div class="bc-foot">
        <div class="bc-foot-l"><span class="bc-meta">${done}/${total} 步 · ${pct}%</span>${timerChip}</div>
        <button class="bc-abandon" onclick="event.stopPropagation(); abandonExperiment('${e.id}', ${esc(JSON.stringify(e.name))})">放弃</button>
      </div>
    </div>`;
  }).join("");
  tickBoardTimers();
}

let boardTimers = {};
let boardTimerKeys = "";

// like fmt() but shows hours for long timers: 16:00:00, otherwise MM:SS
function fmtHMS(sec){
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const mm = String(m).padStart(2, "0"), ss = String(s).padStart(2, "0");
  return h ? (h + ":" + mm + ":" + ss) : (mm + ":" + ss);
}

async function loadBoardTimers(){
  try {
    const list = await api("/api/timers/active");
    const now = Date.now();
    const map = {};
    for(const t of list){
      const updated = parseServerTime(t.updated_at);
      const rec = {
        status: t.status,
        endAt: t.status === "running" ? updated + (t.remaining_seconds || 0) * 1000 : null,
        overBase: t.overtime_seconds || 0,
        overSince: updated,
      };
      const key = String(t.experiment_id);
      const cur = map[key];
      // prefer a running timer (soonest end) over an overtime one
      if(!cur || (rec.status === "running" && (cur.status !== "running" || rec.endAt < cur.endAt))){
        map[key] = rec;
      }
    }
    boardTimers = map;
  } catch {}
}

function tickBoardTimers(){
  if(view !== "board") return;
  const now = Date.now();
  for(const key in boardTimers){
    const el = document.getElementById("bctimer-" + key);
    if(!el) continue;
    const t = boardTimers[key];
    let over = false, secs = 0;
    if(t.status === "running" && t.endAt){ secs = Math.round((t.endAt - now) / 1000); if(secs <= 0){ over = true; secs = -secs; } }
    else { over = true; secs = t.overBase + Math.round((now - t.overSince) / 1000); }
    el.classList.toggle("over", over);
    const tv = el.querySelector(".tv");
    if(tv) tv.textContent = (over ? "+" : "") + fmtHMS(secs);
  }
}

async function pollBoardTimers(){
  if(view !== "board") return;
  await loadBoardTimers();
  const keys = Object.keys(boardTimers).sort().join(",");
  if(keys !== boardTimerKeys){ boardTimerKeys = keys; if(view === "board") renderBoard(experiments); }
  else { tickBoardTimers(); }
}

async function enterExperiment(id){
  selectedExperiment = String(id);
  localStorage.setItem(LS.selected, selectedExperiment);
  initializedStepPosition[id] = false;
  if(!experiments.find(e => String(e.id) === String(id))){
    try { const full = await api(`/api/experiments/${id}`); experiments.push(full); } catch {}
  }
  renderExperiments(experiments);
  setHeaderMode("exp");
  await loadSteps(selectedExperiment);
}

async function abandonExperiment(id, name){
  if(!confirm("放弃实验「" + name + "」？它会移出看板，可在 历史 里找到。")) return;
  try {
    await api(`/api/experiments/${id}`, {method:"PATCH", body: JSON.stringify({status:"abandoned"})});
    experiments = experiments.filter(e => String(e.id) !== String(id));
    await loadExperiments();
    showBoard();
  } catch(e){ alert("放弃失败：" + (e.message || e)); }
}

async function refreshCurrent(){
  await loadExperiments();
  if(view === "board"){ showBoard(); }
  else if(selectedExperiment){ await loadSteps(selectedExperiment); }
}

function currentExperiment(){
  return experiments.find(e => String(e.id) === String(selectedExperiment)) || null;
}

function renderExperiments(exps){
  const sel = document.getElementById("experimentSelect");
  sel.innerHTML = "";
  for(const e of exps){
    const opt = document.createElement("option");
    opt.value = e.id; opt.textContent = e.name + " · " + e.completed_steps + "/" + e.total_steps;
    if(String(e.id) === String(selectedExperiment)) opt.selected = true;
    sel.appendChild(opt);
  }
}

async function selectExperiment(id){
  selectedExperiment = id;
  localStorage.setItem(LS.selected, id);
  initializedStepPosition[id] = false;
  await loadSteps(id);
}

async function loadSteps(expId){
  try {
    steps = await api(`/api/experiments/${expId}/steps`);
    localStorage.setItem(stepKey(expId), JSON.stringify(steps));
    net("已连接", true);
  } catch(e) {
    steps = JSON.parse(localStorage.getItem(stepKey(expId)) || "[]");
    net("离线缓存", false);
  }
  await restoreTimersFromServer(expId);
  await loadVoiceNotes();
  ensureInitialStepPosition(expId);
  if(focusStepIdParam){
    const fi = steps.findIndex(s => String(s.id) === String(focusStepIdParam));
    if(fi >= 0) setCurrentStepIndex(fi);
    focusStepIdParam = "";
  }
  renderSteps(steps);
}

function mergedValues(step){
  let vals = {...(step.values || {})};
  try { vals = {...vals, ...JSON.parse(localStorage.getItem(draftKey(step.id)) || "{}")}; } catch {}
  return vals;
}

function normalizedFields(step){
  const used = new Set();
  return (step.fields || []).map((field, index) => {
    let base = String(field.key || "").trim();
    if(!base){
      base = String(field.label || "")
        .toLowerCase()
        .replace(/µ/g, "u")
        .replace(/[^a-z0-9]+/g, "_")
        .replace(/^_+|_+$/g, "");
    }
    if(!base) base = `field_${index + 1}`;
    let key = base;
    let suffix = 2;
    while(used.has(key)) key = `${base}_${suffix++}`;
    used.add(key);
    return {...field, key};
  });
}

function mergedOverrides(step){
  let vals = {...(step.description_overrides || {})};
  try { vals = {...vals, ...JSON.parse(localStorage.getItem(descKey(step.id)) || "{}")}; } catch {}
  return vals;
}

function mergedTimerSeconds(step){
  const raw = localStorage.getItem(timerKey(step.id));
  if(raw !== null && raw !== "") return Math.max(0, parseInt(raw, 10) || 0);
  return step.effective_timer_seconds || 0;
}

function timerMinutes(seconds){
  const n = (seconds || 0) / 60;
  return Number.isInteger(n) ? String(n) : String(Math.round(n * 10) / 10);
}
function timerHours(seconds){ return String(Math.floor((seconds || 0) / 3600)); }
function timerMins(seconds){ return String(Math.round(((seconds || 0) % 3600) / 60)); }

function currentStepIndex(){
  const raw = localStorage.getItem(stepIndexKey(selectedExperiment));
  const idx = Math.max(0, parseInt(raw || "0", 10) || 0);
  return Math.min(idx, Math.max(0, steps.length - 1));
}

function setCurrentStepIndex(idx){
  if(!selectedExperiment) return;
  const safe = Math.min(Math.max(0, idx), Math.max(0, steps.length - 1));
  localStorage.setItem(stepIndexKey(selectedExperiment), String(safe));
}

function ensureInitialStepPosition(expId){
  if(initializedStepPosition[expId]) return;
  if(!steps.length){ initializedStepPosition[expId] = true; return; }
  const open = steps.findIndex(s => !s.completed_at);
  const idx = open >= 0 ? open : steps.length - 1;
  localStorage.setItem(stepIndexKey(expId), String(idx));
  initializedStepPosition[expId] = true;
}

function goStep(delta){
  setCurrentStepIndex(currentStepIndex() + delta);
  renderSteps(steps);
}

function goToFirstOpenStep(){
  const open = steps.findIndex(s => !s.completed_at);
  if(open >= 0) setCurrentStepIndex(open);
  renderSteps(steps);
}

function openModal(title, html, onSave){
  document.getElementById("modalTitle").textContent = title;
  document.getElementById("modalBody").innerHTML = html;
  document.getElementById("modalStatus").textContent = "";
  modalSaveHandler = onSave;
  document.getElementById("modalBackdrop").classList.add("open");
}

function closeModal(){
  document.getElementById("modalBackdrop").classList.remove("open");
  modalSaveHandler = null;
}

document.getElementById("modalSave").addEventListener("click", async () => {
  if(!modalSaveHandler) return;
  try {
    await modalSaveHandler();
    closeModal();
  } catch(e) {
    document.getElementById("modalStatus").textContent = "保存失败：" + e.message;
  }
});

function setLocalStep(stepId, patch){
  steps = steps.map(s => s.id === stepId ? {...s, ...patch} : s);
  if(selectedExperiment) localStorage.setItem(stepKey(selectedExperiment), JSON.stringify(steps));
}

async function editExperimentName(){
  const exp = currentExperiment();
  if(!exp) return;
  openModal("修改实验名", `<div class="field"><label>实验名</label><input id="editExperimentName" value="${esc(exp.name)}" /></div>`, async () => {
    const name = document.getElementById("editExperimentName").value.trim();
    if(!name) throw new Error("实验名不能为空");
    await api(`/api/experiments/${selectedExperiment}`, {method:"PATCH", body:JSON.stringify({name})});
    experiments = experiments.map(e => String(e.id) === String(selectedExperiment) ? {...e, name} : e);
    localStorage.setItem(LS.experiments, JSON.stringify(experiments));
    renderExperiments(experiments);
  });
}

function editStepText(stepId, key, title){
  const step = steps.find(s => s.id === stepId);
  if(!step) return;
  const value = step[key] || "";
  const control = key === "description"
    ? `<textarea id="editStepText">${esc(value)}</textarea>`
    : `<input id="editStepText" value="${esc(value)}" />`;
  openModal(title, `<div class="field">${control}</div>`, async () => {
    const next = document.getElementById("editStepText").value;
    await api(`/api/steps/${stepId}`, {method:"PATCH", body:JSON.stringify({[key]: next})});
    setLocalStep(stepId, {[key]: next});
    renderSteps(steps);
  });
}

function fieldsToText(fields){
  return (fields || []).map(f => {
    const options = (f.options || []).join(",");
    return [f.key || "", f.label || "", f.type || "text", f.default || "", f.required ? "true" : "false", options].join(" | ");
  }).join("\\n");
}

function parseFieldsText(text){
  return text.split(/\\r?\\n/).map(line => line.trim()).filter(Boolean).map((line, i) => {
    const parts = line.split("|").map(p => p.trim());
    const key = parts[0] || `field_${i + 1}`;
    const label = parts[1] || key;
    const type = ["text", "number", "dropdown"].includes(parts[2]) ? parts[2] : "text";
    const options = (parts[5] || "").split(",").map(x => x.trim()).filter(Boolean);
    return {key, label, type, default: parts[3] || "", required: /^true|是|yes|1$/i.test(parts[4] || ""), options};
  });
}

function editFields(stepId){
  const step = steps.find(s => s.id === stepId);
  if(!step) return;
  const help = "每行一个字段：key | 显示名称 | 类型(text/number/dropdown) | 默认值 | 是否必填(true/false) | 下拉选项1,选项2";
  openModal("编辑记录字段", `
    <p class="small">${help}</p>
    <div class="field"><textarea id="editFieldsText">${esc(fieldsToText(step.fields || []))}</textarea></div>
  `, async () => {
    const fields = parseFieldsText(document.getElementById("editFieldsText").value);
    await api(`/api/steps/${stepId}`, {method:"PATCH", body:JSON.stringify({fields_json: JSON.stringify(fields)})});
    setLocalStep(stepId, {fields});
    renderSteps(steps);
  });
}

function renderAttachments(step, attachments){
  return attachments.map(item => {
    const url = attachmentUrl(item.path);
    const renameButton = `<button type="button" class="attachment-rename"
      title="修改附件名称" aria-label="修改 ${esc(item.name)} 的名称"
      data-step-id="${step.id}" data-path="${esc(item.path)}" data-name="${esc(item.name)}"
      onclick="renameAttachment(this)">${svgIcon("pencil",15)}</button>`;
    if(isImageAttachment(item.path)){
      const disp = displayUrl(item.path);   // TIFF/BMP → server-rendered PNG preview
      return `<span class="attachment-item image">
        <a class="attachment-preview" href="${esc(disp)}" target="_blank" rel="noopener" title="打开预览">
          <img src="${esc(disp)}" alt="${esc(item.name)}" loading="lazy" />
        </a>
        <span class="attachment-caption">
          <a href="${esc(url)}" target="_blank" rel="noopener" title="${esc(item.name)}（下载原图）">${esc(item.name)}</a>
          ${renameButton}
        </span>
      </span>`;
    }
    return `<span class="attachment-item file">
      <a href="${esc(url)}" target="_blank" rel="noopener">${esc(item.name)}</a>
      ${renameButton}
    </span>`;
  }).join("");
}

function attachmentUrl(path){
  const clean = String(path || "").replace(/\\\\/g, "/").replace(/^\\/+/, "");
  return "/photos/" + clean.split("/").map(encodeURIComponent).join("/");
}

function needsConvert(path){ return /\\.(tiff?|bmp)$/i.test(String(path || "")); }

// URL to show in <img>: browsers can't render TIFF/BMP, so use the server PNG preview.
function displayUrl(path){
  if(!needsConvert(path)) return attachmentUrl(path);
  const clean = String(path || "").replace(/\\\\/g, "/").replace(/^\\/+/, "");
  return "/api/preview?path=" + encodeURIComponent(clean);
}

function isImageAttachment(path){
  return /\\.(jpe?g|png|gif|webp|bmp|tiff?|svg)$/i.test(String(path || ""));
}

function renameAttachment(button){
  const stepId = Number(button.dataset.stepId);
  const attachmentPath = button.dataset.path || "";
  const currentName = button.dataset.name || "";
  openModal(
    "修改附件名称",
    `<div class="field"><label>附件名称</label><input id="editAttachmentName" value="${esc(currentName)}" maxlength="240" /></div>`,
    async () => {
      const name = document.getElementById("editAttachmentName").value.trim();
      if(!name) throw new Error("附件名称不能为空");
      const updated = await api(`/api/steps/${stepId}/attachments/name`, {
        method:"PATCH",
        body:JSON.stringify({path:attachmentPath, name})
      });
      setLocalStep(stepId, {attachments:updated.attachments, photo_paths:updated.photo_paths});
      renderSteps(steps);
    }
  );
}

function jumpStep(i){ setCurrentStepIndex(i); renderSteps(steps); }

function renderSteps(items){
  const root = document.getElementById("steps");
  root.innerHTML = "";
  if(!items.length){ root.innerHTML = '<div class="card small">暂无缓存步骤。联网后点右上角刷新按钮。</div>'; return; }
  steps = items;
  const idx = currentStepIndex();
  const step = items[idx];
  const doneCount = items.filter(s => s.completed_at).length;
  const pct = Math.round((doneCount / items.length) * 100);
  const vals = mergedValues(step);
  const totalSeconds = mergedTimerSeconds(step);
  const isLast = idx >= items.length - 1;
  const card = document.createElement("article");
  card.className = "card";
  const chips = items.map((s, i) =>
    `<button class="chip ${s.completed_at ? "done" : ""} ${i === idx ? "cur" : ""}" onclick="jumpStep(${i})" title="${esc(s.title)}">${i + 1}</button>`
  ).join("");
  const fields = normalizedFields(step).map(f => {
    const v = vals[f.key] ?? f.default ?? "";
    if(f.type === "dropdown"){
      const opts = (f.options || []).map(o => `<option value="${esc(o)}" ${o==v?"selected":""}>${esc(o)}</option>`).join("");
      return `<div class="field"><label>${esc(f.label)}${f.required ? " *" : ""}</label><select data-step="${step.id}" data-key="${esc(f.key)}" onchange="saveDraft(${step.id})">${opts}</select></div>`;
    }
    if(f.type === "number"){
      return `<div class="field"><label>${esc(f.label)}${f.required ? " *" : ""}</label><input type="number" data-step="${step.id}" data-key="${esc(f.key)}" value="${esc(v)}" oninput="saveDraft(${step.id})" /></div>`;
    }
    const hasValue = String(v).trim().length > 0;
    const keyArg = JSON.stringify(f.key);
    return `<div class="field field-md ${hasValue ? "has-value" : ""}" data-field-step="${step.id}" data-field-key="${esc(f.key)}">
      <label>${esc(f.label)}${f.required ? " *" : ""}</label>
      <div class="field-preview ${hasValue ? "" : "empty"}" onclick='openFieldEdit(${step.id}, ${keyArg})'>${hasValue ? markdownToHtml(v) : "点击填写"}</div>
      <textarea data-step="${step.id}" data-key="${esc(f.key)}" oninput='saveDraft(${step.id}); updateFieldPreview(${step.id}, ${keyArg})' onblur='closeFieldEdit(${step.id}, ${keyArg})' placeholder="${esc(f.label)}">${esc(v)}</textarea>
    </div>`;
  }).join("");
  const notesValue = vals[STEP_NOTES_KEY] || "";
  const hasMdNote = String(notesValue).trim().length > 0;
  const notesBlock = `
    <div class="field notes md-slot ${hasMdNote ? "has-md" : ""}" id="md-slot-${step.id}">
      <div class="md-line">
        <button type="button" class="md-chip" onclick="toggleMdSlot(${step.id})" title="输入或修改 Markdown 记录">md</button>
      </div>
      <div class="md-preview ${hasMdNote ? "" : "empty"}" id="md-preview-${step.id}">${hasMdNote ? markdownToHtml(notesValue) : "暂无 Markdown 记录。点击 md 后可编辑。"}</div>
      <div class="md-box" id="md-box-${step.id}">
        <textarea data-step="${step.id}" data-key="${STEP_NOTES_KEY}" oninput="saveDraft(${step.id}); updateMdSlot(${step.id})" placeholder="Markdown 记录；报告中会按 Markdown 渲染">${esc(notesValue)}</textarea>
        <div class="small">支持 Markdown。适合放观察、异常、解释、AI 整理结果；数字和短字段仍填上面的结构化字段。</div>
      </div>
    </div>`;
  const attachments = step.attachments || (step.photo_paths || []).map((p,i) => ({path:p, name:`附件 ${i+1}`}));
  const photos = renderAttachments(step, attachments);
  const timerBlock = totalSeconds > 0 ? `
    <div class="timer" id="timer-box-${step.id}">
      <div class="small">步骤计时 · 电脑端负责响铃</div>
      <div class="timer-display" id="timer-display-${step.id}">${fmtHMS(totalSeconds)}</div>
      <div class="field timer-edit">
        <input type="number" min="0" step="1" id="th-${step.id}" value="${timerHours(totalSeconds)}" onchange="saveTimerHM(${step.experiment_id}, ${step.id})" ${step.completed_at ? "disabled" : ""} />
        <span class="small">时</span>
        <input type="number" min="0" step="1" id="tm-${step.id}" value="${timerMins(totalSeconds)}" onchange="saveTimerHM(${step.experiment_id}, ${step.id})" ${step.completed_at ? "disabled" : ""} />
        <span class="small">分</span>
      </div>
      <div class="actions">
        <button onclick="startLocalTimer(${step.experiment_id}, ${step.id}, ${totalSeconds})" ${step.completed_at ? "disabled" : ""}>开始</button>
        <button class="secondary" onclick="pauseLocalTimer(${step.experiment_id}, ${step.id})" ${step.completed_at ? "disabled" : ""}>暂停</button>
        <button class="secondary" onclick="resetLocalTimer(${step.experiment_id}, ${step.id}, ${totalSeconds})" ${step.completed_at ? "disabled" : ""}>重置</button>
      </div>
      <div class="status" id="timer-status-${step.id}"></div>
    </div>` : "";
  const voiceBlock = renderStepVoice(step);
  const photoBlock = `
    <div class="field">
      <label>附件 / 拍照记录</label>
      <div class="photos">${photos || '<span class="small">暂无附件</span>'}</div>
      <form class="photo-row" onsubmit="uploadPhoto(event, ${step.id})">
        <div class="pr-btns">
          <label class="button secondary" for="cam-${step.id}">${svgIcon("camera",16)} 拍照</label>
          <label class="button secondary" for="gal-${step.id}">${svgIcon("image",16)} 相册</label>
          <label class="button secondary" for="any-${step.id}">文件</label>
          <button type="button" class="secondary" onclick="pasteClipboard(${step.id})">剪贴板</button>
        </div>
        <input id="cam-${step.id}" name="file" type="file" accept="image/*" capture="environment" onchange="markFile(this)" />
        <input id="gal-${step.id}" name="file2" type="file" accept="image/*" onchange="markFile(this)" />
        <input id="any-${step.id}" name="file3" type="file" onchange="markFile(this)" />
        <input id="name-${step.id}" name="attachment_name" type="text" placeholder="附件名称（默认原文件名）" />
        <div class="pr-submit">
          <button type="submit">${svgIcon("upload",16)} 上传</button>
          <span class="small" id="file-${step.id}">未选择</span>
        </div>
      </form>
    </div>`;
  const wrapupBlock = isLast ? `
    <div class="wrapup">
      <div class="section-head" style="margin-top:0">
        <h2>实验收尾</h2>
      </div>
      <div class="small">最后一步完成后，补充储存物品、登记 Box 位置、查看和保存报告。</div>
      <div class="actions">
        <a class="button" href="/run/storage/${selectedExperiment}">储存物品 / 登记位置</a>
        <a class="button secondary" href="/run/report/${selectedExperiment}">查看报告</a>
        <button class="secondary" onclick="finishExperiment()">结束实验</button>
      </div>
    </div>` : "";
  card.innerHTML = `
    <div class="chips">${chips}</div>
    <div class="stepper">
      <button class="secondary" onclick="goStep(-1)" ${idx === 0 ? "disabled" : ""}>← 上一步</button>
      <div class="progress"><div style="width:${pct}%"></div></div>
      <button class="secondary" onclick="goStep(1)" ${idx >= items.length - 1 ? "disabled" : ""}>下一步 →</button>
    </div>
    <div class="section-head" style="margin-top:4px">
      <div class="small">Step ${idx + 1} / ${items.length} · 已完成 ${doneCount}/${items.length}${step.completed_at ? ' · <span class="done">本步已完成 ✓</span>' : ''}</div>
      <button class="edit-link" onclick="editExperimentName()">改实验名</button>
    </div>
    <div class="section-head" style="margin-top:2px">
      <div class="step-title">${esc(step.title)}</div>
      <button class="edit-link" onclick="editStepText(${step.id}, 'title', '修改步骤标题')">${svgIcon("pencil",15)}</button>
    </div>
    <div class="section-head">
      <h2>步骤说明</h2>
      <button class="edit-link" onclick="editStepText(${step.id}, 'description', '修改步骤说明')">编辑</button>
    </div>
    <div class="desc">${renderDescription(step)}</div>
    ${timerBlock}
    <div class="section-head">
      <h2>记录数据</h2>
      <button class="edit-link" onclick="editFields(${step.id})">编辑字段</button>
    </div>
    ${fields}
    ${notesBlock}
    ${voiceBlock}
    ${photoBlock}
    ${wrapupBlock}
    <div class="main-actions">
      <button class="secondary" onclick="saveAndSync(${step.id})">保存</button>
      <button class="green" onclick="completeStep(${step.id})" ${step.completed_at ? "disabled" : ""}>完成步骤 ✓</button>
      <span class="status" id="status-${step.id}"></span>
    </div>`;
  root.appendChild(card);
  refreshTimers();
}

function collectValues(stepId){
  const vals = {};
  document.querySelectorAll(`[data-step="${stepId}"]`).forEach(el => {
    const key = String(el.dataset.key || "").trim();
    if(key) vals[key] = el.value || "";
  });
  return vals;
}
function saveDraft(stepId){ localStorage.setItem(draftKey(stepId), JSON.stringify(collectValues(stepId))); }
function status(stepId, text){ const el=document.getElementById("status-"+stepId); if(el) el.textContent=text; }

function fieldSlot(stepId, key){
  return Array.from(document.querySelectorAll(`[data-field-step="${stepId}"]`))
    .find(el => el.dataset.fieldKey === String(key)) || null;
}

function fieldTextarea(stepId, key){
  const slot = fieldSlot(stepId, key);
  return slot ? slot.querySelector("textarea") : null;
}

function openFieldEdit(stepId, key){
  const slot = fieldSlot(stepId, key);
  const ta = fieldTextarea(stepId, key);
  if(!slot || !ta) return;
  slot.classList.add("editing");
  setTimeout(() => {
    ta.focus();
    ta.selectionStart = ta.selectionEnd = ta.value.length;
  }, 20);
}

function closeFieldEdit(stepId, key){
  updateFieldPreview(stepId, key);
  const slot = fieldSlot(stepId, key);
  if(slot) slot.classList.remove("editing");
}

function updateFieldPreview(stepId, key){
  const slot = fieldSlot(stepId, key);
  const ta = fieldTextarea(stepId, key);
  const preview = slot ? slot.querySelector(".field-preview") : null;
  if(!slot || !ta || !preview) return;
  const hasText = !!ta.value.trim();
  slot.classList.toggle("has-value", hasText);
  preview.classList.toggle("empty", !hasText);
  preview.innerHTML = hasText ? markdownToHtml(ta.value) : "点击填写";
}

function mdTextarea(stepId){
  return document.querySelector(`textarea[data-step="${stepId}"][data-key="${STEP_NOTES_KEY}"]`);
}

function toggleMdSlot(stepId){
  const slot = document.getElementById("md-slot-" + stepId);
  const ta = mdTextarea(stepId);
  if(!slot) return;
  slot.classList.toggle("editing");
  if(slot.classList.contains("editing") && ta){
    setTimeout(() => {
      ta.focus();
      ta.selectionStart = ta.selectionEnd = ta.value.length;
    }, 30);
  } else {
    updateMdSlot(stepId);
  }
}

function updateMdSlot(stepId){
  const ta = mdTextarea(stepId);
  const slot = document.getElementById("md-slot-" + stepId);
  const preview = document.getElementById("md-preview-" + stepId);
  const hasText = !!(ta && ta.value.trim());
  if(slot) slot.classList.toggle("has-md", hasText);
  if(preview){
    preview.classList.toggle("empty", !hasText);
    preview.innerHTML = hasText ? markdownToHtml(ta.value) : "暂无 Markdown 记录。点击 md 后可编辑。";
  }
}

function renderDescription(step){
  return markdownToHtml(step.description || "");
}

function markdownToHtml(markdown){
  const lines = String(markdown || "").replace(/\\r\\n/g, "\\n").split("\\n");
  const out = [];
  let i = 0;
  let inCode = false;
  let codeLines = [];
  let paragraph = [];

  function flushParagraph(){
    if(paragraph.length){
      out.push(`<p>${renderInline(paragraph.join(" "))}</p>`);
      paragraph = [];
    }
  }
  function flushCode(){
    out.push(`<pre><code>${esc(codeLines.join("\\n"))}</code></pre>`);
    codeLines = [];
  }
  function isTableSep(line){
    return /^\\s*\\|?\\s*:?-{3,}:?\\s*(\\|\\s*:?-{3,}:?\\s*)+\\|?\\s*$/.test(line);
  }
  function splitTableRow(line){
    let trimmed = line.trim();
    if(trimmed.startsWith("|")) trimmed = trimmed.slice(1);
    if(trimmed.endsWith("|")) trimmed = trimmed.slice(0, -1);
    return trimmed.split("|").map(cell => cell.trim());
  }

  while(i < lines.length){
    const raw = lines[i];
    const line = raw.trimEnd();
    if(line.trim().startsWith("```")){
      if(inCode){ flushCode(); inCode = false; } else { flushParagraph(); inCode = true; codeLines = []; }
      i++;
      continue;
    }
    if(inCode){ codeLines.push(raw); i++; continue; }

    if(!line.trim()){ flushParagraph(); i++; continue; }

    if(i + 1 < lines.length && line.includes("|") && isTableSep(lines[i + 1])){
      flushParagraph();
      const headers = splitTableRow(line);
      i += 2;
      const rows = [];
      while(i < lines.length && lines[i].trim() && lines[i].includes("|")){
        rows.push(splitTableRow(lines[i]));
        i++;
      }
      out.push(`<table><thead><tr>${headers.map(h => `<th>${renderInline(h)}</th>`).join("")}</tr></thead><tbody>${rows.map(row => `<tr>${headers.map((_, idx) => `<td>${renderInline(row[idx] || "")}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
      continue;
    }

    const heading = /^(#{1,3})\\s+(.+)$/.exec(line);
    if(heading){
      flushParagraph();
      const level = heading[1].length;
      out.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      i++;
      continue;
    }

    if(/^>\\s+/.test(line)){
      flushParagraph();
      const quote = [];
      while(i < lines.length && /^>\\s+/.test(lines[i])){
        quote.push(lines[i].replace(/^>\\s+/, ""));
        i++;
      }
      out.push(`<blockquote>${quote.map(q => `<p>${renderInline(q)}</p>`).join("")}</blockquote>`);
      continue;
    }

    if(/^[-*+]\\s+/.test(line)){
      flushParagraph();
      const items = [];
      while(i < lines.length && /^[-*+]\\s+/.test(lines[i].trimEnd())){
        items.push(lines[i].trimEnd().replace(/^[-*+]\\s+/, ""));
        i++;
      }
      out.push(`<ul>${items.map(item => `<li>${renderInline(item)}</li>`).join("")}</ul>`);
      continue;
    }

    if(/^\\d+[.)]\\s+/.test(line)){
      flushParagraph();
      const items = [];
      while(i < lines.length && /^\\d+[.)]\\s+/.test(lines[i].trimEnd())){
        items.push(lines[i].trimEnd().replace(/^\\d+[.)]\\s+/, ""));
        i++;
      }
      out.push(`<ol>${items.map(item => `<li>${renderInline(item)}</li>`).join("")}</ol>`);
      continue;
    }

    paragraph.push(line.trim());
    i++;
  }
  if(inCode) flushCode();
  flushParagraph();
  return out.join("");
}

function renderInline(text){
  let html = esc(text);
  const codes = [];
  html = html.replace(/`([^`]+)`/g, (_, code) => {
    codes.push(`<code>${code}</code>`);
    return `\\u0000${codes.length - 1}\\u0000`;
  });
  html = html.replace(/\\[([^\\]]+)\\]\\((https?:\\/\\/[^\\s)]+)\\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  html = html.replace(/\\*\\*([^*]+)\\*\\*/g, "<strong>$1</strong>");
  html = html.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  html = html.replace(/(^|[^*])\\*([^*]+)\\*(?!\\*)/g, "$1<em>$2</em>");
  html = html.replace(/(^|[^_])_([^_]+)_(?!_)/g, "$1<em>$2</em>");
  html = html.replace(/\\u0000(\\d+)\\u0000/g, (_, idx) => codes[Number(idx)] || "");
  return html;
}

function saveTimerOverride(expId, stepId, minutes){
  const value = parseFloat(minutes);
  if(Number.isNaN(value) || value < 0){ status(stepId, "计时器请输入有效分钟数"); return; }
  applyTimerSeconds(expId, stepId, Math.max(0, Math.round(value * 60)));
}
function saveTimerHM(expId, stepId){
  const h = parseFloat(document.getElementById("th-" + stepId).value) || 0;
  const m = parseFloat(document.getElementById("tm-" + stepId).value) || 0;
  if(h < 0 || m < 0){ status(stepId, "计时请输入有效的时和分"); return; }
  applyTimerSeconds(expId, stepId, Math.max(0, Math.round(h * 3600 + m * 60)));
}
function applyTimerSeconds(expId, stepId, seconds){
  localStorage.setItem(timerKey(stepId), String(seconds));
  resetLocalTimer(expId, stepId, seconds, "override");
  enqueue({type:"patchStep", stepId, payload: patchPayload(stepId)});
  status(stepId, "计时器已存本地，等待同步");
  syncNow();
}

function patchPayload(stepId){
  const body = {values_json: JSON.stringify(collectValues(stepId))};
  const timerRaw = localStorage.getItem(timerKey(stepId));
  if(timerRaw !== null && timerRaw !== "") body.timer_override_seconds = Math.max(0, parseInt(timerRaw, 10) || 0);
  return body;
}

function fmt(sec){
  sec = Math.max(0, Math.floor(sec || 0));
  const m = Math.floor(sec / 60), s = sec % 60;
  return String(m).padStart(2,"0") + ":" + String(s).padStart(2,"0");
}

function parseServerTime(value){
  const t = Date.parse(value || "");
  return Number.isFinite(t) ? t : Date.now();
}

function serverTimerToLocal(record){
  const total = Math.max(0, parseInt(record.total_seconds || 0, 10) || 0);
  const remainingAtServer = Math.max(0, parseInt(record.remaining_seconds || 0, 10) || 0);
  const overtimeAtServer = Math.max(0, parseInt(record.overtime_seconds || 0, 10) || 0);
  const updatedAt = parseServerTime(record.updated_at);
  const elapsed = Math.max(0, Math.floor((Date.now() - updatedAt) / 1000));
  if(record.status === "running"){
    if(elapsed >= remainingAtServer){
      return {
        status:"overtime",
        total,
        remaining:0,
        pausedRemaining:0,
        overtime:overtimeAtServer + elapsed - remainingAtServer,
        startedAt:null,
        updatedAt:Date.now()
      };
    }
    const remaining = remainingAtServer - elapsed;
    return {
      status:"running",
      total,
      remaining,
      pausedRemaining:remaining,
      startedAt:Date.now(),
      updatedAt:Date.now()
    };
  }
  if(record.status === "overtime"){
    return {
      status:"overtime",
      total,
      remaining:0,
      pausedRemaining:0,
      overtime:overtimeAtServer + elapsed,
      startedAt:null,
      updatedAt:Date.now()
    };
  }
  if(record.status === "paused"){
    return {
      status:"paused",
      total,
      remaining:remainingAtServer,
      pausedRemaining:remainingAtServer,
      startedAt:null,
      updatedAt:Date.now()
    };
  }
  if(record.status === "confirmed"){
    return {
      status:"confirmed",
      total,
      remaining:remainingAtServer,
      pausedRemaining:remainingAtServer,
      overtime:overtimeAtServer,
      startedAt:null,
      updatedAt:Date.now()
    };
  }
  return {status:"idle", total, remaining:total, pausedRemaining:total, startedAt:null, updatedAt:Date.now()};
}

async function restoreTimersFromServer(expId){
  try {
    const active = await api(`/api/timers/experiment/${expId}`);
    const timers = getTimers();
    for(const record of active){
      timers[record.step_id] = serverTimerToLocal(record);
      timerLastSync[record.step_id] = Date.now();
    }
    setTimers(timers);
  } catch(e) {
    // 离线时继续使用本机缓存。
  }
}

function timerState(stepId, total){
  const timers = getTimers();
  const t = timers[stepId];
  if(!t) return {status:"idle", total, remaining:total, startedAt:null, pausedRemaining:total, updatedAt:Date.now()};
  if(t.status === "running"){
    const elapsed = Math.max(0, Math.floor((Date.now() - (t.startedAt || Date.now())) / 1000));
    const remaining = (t.pausedRemaining ?? t.remaining ?? total) - elapsed;
    if(remaining <= 0){
      return {
        ...t,
        status:"overtime",
        remaining:0,
        pausedRemaining:0,
        overtime:Math.abs(remaining),
        updatedAt:Date.now()
      };
    }
    return {...t, remaining, pausedRemaining:remaining};
  }
  if(t.status === "overtime"){
    const elapsed = Math.max(0, Math.floor((Date.now() - (t.updatedAt || Date.now())) / 1000));
    return {
      ...t,
      remaining:0,
      pausedRemaining:0,
      overtime:Math.max(0, Math.floor(t.overtime || 0)) + elapsed
    };
  }
  const pausedRemaining = t.pausedRemaining ?? t.remaining ?? total;
  return {...t, remaining: pausedRemaining, pausedRemaining};
}

async function tellComputerTimer(expId, stepId, action){
  try {
    if(action === "start" || action === "reset"){
      await fetch(`/api/steps/${stepId}`, {
        method:"PATCH",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify(patchPayload(stepId))
      });
    }
    const res = await fetch(`/api/timers/${expId}/${stepId}/${action}`, {method:"POST"});
    if(!res.ok) throw new Error(await res.text());
  } catch(e) {
    status(stepId, "电脑端计时器未同步，请确认电脑服务在线");
  }
}

function queueComputerTimer(expId, stepId, action){
  timerSync[stepId] = (timerSync[stepId] || Promise.resolve()).then(
    () => tellComputerTimer(expId, stepId, action)
  );
  return timerSync[stepId];
}

async function tellComputerTimerState(expId, stepId, state, patchStep=false){
  try {
    if(patchStep){
      await fetch(`/api/steps/${stepId}`, {
        method:"PATCH",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify(patchPayload(stepId))
      });
    }
    const overtimeSeconds = Math.max(0, Math.floor(state.overtime ?? (state.remaining < 0 ? Math.abs(state.remaining) : 0) ?? 0));
    const payload = {
      total_seconds: state.total,
      remaining_seconds: state.status === "overtime" ? 0 : Math.max(0, Math.floor(state.remaining ?? state.pausedRemaining ?? state.total)),
      overtime_seconds: overtimeSeconds,
      status: state.status,
      action: state.action || "sync",
      elapsed_seconds: Math.max(0, Math.floor(state.elapsedSeconds ?? elapsedForState(state)))
    };
    const res = await fetch(`/api/timers/${expId}/${stepId}/sync`, {
      method:"POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify(payload)
    });
    if(!res.ok) throw new Error(await res.text());
    timerLastSync[stepId] = Date.now();
  } catch(e) {
    status(stepId, "电脑端计时器未同步，请确认电脑服务在线");
  }
}

function queueComputerTimerState(expId, stepId, state, patchStep=false){
  timerSync[stepId] = (timerSync[stepId] || Promise.resolve()).then(
    () => tellComputerTimerState(expId, stepId, state, patchStep)
  );
  return timerSync[stepId];
}

function startLocalTimer(expId, stepId, total){
  const timers = getTimers();
  const current = timerState(stepId, total);
  const remaining = current.status === "paused" ? current.remaining : total;
  timers[stepId] = {status:"running", total, pausedRemaining:remaining, remaining, startedAt:Date.now(), updatedAt:Date.now()};
  setTimers(timers);
  const next = timerState(stepId, total);
  next.action = "start";
  next.elapsedSeconds = elapsedForState(current);
  queueComputerTimerState(expId, stepId, next, true);
  refreshTimers();
}

function pauseLocalTimer(expId, stepId){
  const timers = getTimers();
  const current = timerState(stepId, timers[stepId]?.total || 0);
  const remaining = Math.max(0, current.remaining);
  timers[stepId] = {status:"paused", total:current.total, pausedRemaining:remaining, remaining, startedAt:null, updatedAt:Date.now()};
  setTimers(timers);
  const next = timerState(stepId, current.total);
  next.action = "pause";
  next.elapsedSeconds = elapsedForState(current);
  queueComputerTimerState(expId, stepId, next);
  refreshTimers();
}

function resetLocalTimer(expId, stepId, total, action="reset"){
  const timers = getTimers();
  const current = timerState(stepId, timers[stepId]?.total || total);
  timers[stepId] = {status:"idle", total, pausedRemaining:total, remaining:total, startedAt:null, updatedAt:Date.now()};
  setTimers(timers);
  const next = timerState(stepId, total);
  next.action = action;
  next.elapsedSeconds = elapsedForState(current);
  queueComputerTimerState(expId, stepId, next, true);
  refreshTimers();
}

function elapsedForState(state){
  if(!state) return 0;
  const total = Math.max(0, Math.floor(state.total || 0));
  const remaining = Math.floor(state.remaining ?? state.pausedRemaining ?? total);
  if(remaining < 0) return total + Math.abs(remaining);
  return Math.max(0, total - Math.max(0, remaining));
}

function refreshTimers(){
  const timers = getTimers();
  for(const step of steps){
    const totalSeconds = mergedTimerSeconds(step);
    if(!totalSeconds) continue;
    const current = timerState(step.id, totalSeconds);
    const display = document.getElementById("timer-display-"+step.id);
    const box = document.getElementById("timer-box-"+step.id);
    const text = document.getElementById("timer-status-"+step.id);
    if(!display || !box) continue;
    if(current.status === "overtime" || (current.status === "running" && current.remaining <= 0)){
      const overtime = Math.max(0, Math.floor(current.overtime ?? Math.abs(current.remaining || 0)));
      display.textContent = "+" + fmtHMS(overtime);
      box.classList.add("over");
      if(text) text.textContent = "时间到。电脑端会响铃；当前页面同步显示。";
      if(timers[step.id]?.status !== "overtime"){
        timers[step.id] = {
          ...(timers[step.id] || {}),
          status:"overtime",
          total:current.total,
          remaining:0,
          pausedRemaining:0,
          overtime,
          startedAt:null,
          updatedAt:Date.now()
        };
        setTimers(timers);
      } else if(Math.abs((timers[step.id]?.overtime || 0) - overtime) > 5){
        timers[step.id] = {...timers[step.id], overtime, updatedAt:Date.now()};
        setTimers(timers);
      }
      if(!timers[step.id]?.alerted){
        try { navigator.vibrate && navigator.vibrate([300,120,300,120,600]); } catch {}
        timers[step.id] = {...(timers[step.id] || {}), alerted:true};
        setTimers(timers);
      }
    } else {
      display.textContent = fmtHMS(current.remaining ?? totalSeconds);
      box.classList.remove("over");
      if(text) text.textContent = current.status === "running"
        ? "计时中"
        : (current.status === "paused" ? "已暂停" : (current.status === "confirmed" ? "已停止" : "未开始"));
    }
    if((current.status === "running" || current.status === "overtime") && Date.now() - (timerLastSync[step.id] || 0) > 3000){
      queueComputerTimerState(step.experiment_id, step.id, current);
    }
  }
}

function saveAndSync(stepId){
  saveDraft(stepId);
  enqueue({type:"patchStep", stepId, payload: patchPayload(stepId)});
  status(stepId, "已存本地，等待同步");
  syncNow();
}

function requiredErrors(step){
  const vals = collectValues(step.id);
  return normalizedFields(step).filter(f => f.required && !String(vals[f.key] || "").trim()).map(f => f.label);
}

function completeStep(stepId){
  const step = steps.find(s => s.id === stepId);
  const errs = requiredErrors(step);
  if(errs.length){ status(stepId, "必填未填：" + errs.join("、")); return; }
  const timers = getTimers();
  const total = mergedTimerSeconds(step);
  if(total > 0){
    const current = timerState(stepId, total);
    const remaining = Math.max(0, current.remaining ?? current.pausedRemaining ?? 0);
    timers[stepId] = {
      ...current,
      status:"confirmed",
      remaining,
      pausedRemaining:remaining,
      startedAt:null,
      updatedAt:Date.now()
    };
    setTimers(timers);
  }
  saveDraft(stepId);
  enqueue({type:"patchStep", stepId, payload: patchPayload(stepId)});
  enqueue({type:"completeStep", stepId});
  status(stepId, "已加入完成队列");
  const idx = currentStepIndex();
  if(idx < steps.length - 1){
    setCurrentStepIndex(idx + 1);
    renderSteps(steps);
  } else {
    enqueue({type:"patchExperiment", expId:Number(selectedExperiment), payload:{status:"needs_wrapup"}});
  }
  syncNow();
}

async function finishExperiment(){
  if(!selectedExperiment) return;
  if(!confirm("确认结束实验？结束后仍然可以从历史记录查看报告。")) return;
  try {
    await api(`/api/experiments/${selectedExperiment}`, {method:"PATCH", body:JSON.stringify({status:"completed"})});
    location.href = `/run/report/${selectedExperiment}`;
  } catch(e) {
    alert("结束实验失败：" + e.message);
  }
}

async function syncNow(){
  let q = getQueue();
  if(!q.length){ renderQueueInfo(); return; }
  const remain = [];
  for(const job of q){
    try {
      if(job.type === "patchStep"){
        const payload = job.payload || {values_json: JSON.stringify(job.values || {})};
        await api(`/api/steps/${job.stepId}`, {method:"PATCH", body: JSON.stringify(payload)});
      } else if(job.type === "completeStep"){
        await api(`/api/steps/${job.stepId}/complete`, {method:"POST", body: "{}"});
      } else if(job.type === "patchExperiment"){
        await api(`/api/experiments/${job.expId}`, {method:"PATCH", body: JSON.stringify(job.payload || {})});
      }
    } catch(e) {
      remain.push(job);
    }
  }
  setQueue(remain);
  if(selectedExperiment) await loadSteps(selectedExperiment);
}

function syncCurrentAndNow(){
  if(steps.length){
    const step = steps[currentStepIndex()];
    if(step){
      saveDraft(step.id);
      enqueue({type:"patchStep", stepId: step.id, payload: patchPayload(step.id)});
      status(step.id, "当前步骤已加入同步");
    }
  }
  syncNow();
}

function markFile(input){
  const form = input.closest("form");
  const stepId = form.getAttribute("onsubmit").match(/, (\\d+)\\)/)?.[1];
  if(stepId && input.files && input.files.length) {
    document.getElementById("file-"+stepId).textContent = input.files[0].name;
    const nameInput = document.getElementById("name-"+stepId);
    if(nameInput && !nameInput.value.trim()) nameInput.value = input.files[0].name;
  }
}

async function uploadPhoto(event, stepId){
  event.preventDefault();
  const form = event.currentTarget;
  const file = form.file?.files?.[0] || form.file2?.files?.[0] || form.file3?.files?.[0];
  if(!file){ document.getElementById("file-"+stepId).textContent = "请先选择照片或文件"; return; }
  const nameInput = document.getElementById("name-"+stepId);
  await uploadFileToStep(file, stepId, nameInput ? nameInput.value : file.name);
}

function clipboardFileName(blob, index=0){
  const types = {
    "image/png":"png",
    "image/jpeg":"jpg",
    "image/webp":"webp",
    "image/gif":"gif",
    "application/pdf":"pdf"
  };
  const ext = types[blob.type] || (blob.type.split("/")[1] || "bin").replace(/[^a-z0-9]+/gi, "");
  const now = new Date();
  const stamp = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
    "_",
    String(now.getHours()).padStart(2, "0"),
    String(now.getMinutes()).padStart(2, "0"),
    String(now.getSeconds()).padStart(2, "0")
  ].join("");
  return `clipboard_${stamp}${index ? "_" + (index + 1) : ""}.${ext || "bin"}`;
}

async function uploadFileToStep(file, stepId, requestedName=""){
  const fd = new FormData();
  fd.append("file", file);
  fd.append("attachment_name", String(requestedName || file.name || "").trim() || clipboardFileName(file));
  try {
    const res = await fetch(`/api/photos/upload?step_id=${stepId}`, {method:"POST", body:fd});
    if(!res.ok) throw new Error(await res.text());
    document.getElementById("file-"+stepId).textContent = "上传完成";
    await loadSteps(selectedExperiment);
  } catch(e) {
    document.getElementById("file-"+stepId).textContent = "上传失败，文件保留在本机，请联网后重试";
  }
}

async function uploadClipboardFiles(files, stepId){
  if(!files.length) return false;
  const nameInput = document.getElementById("name-"+stepId);
  const requestedName = nameInput ? nameInput.value.trim() : "";
  for(let index = 0; index < files.length; index++){
    const source = files[index];
    const file = source.name
      ? source
      : new File([source], clipboardFileName(source, index), {type:source.type || "application/octet-stream"});
    const displayName = requestedName
      ? (files.length > 1 ? `${requestedName} ${index + 1}` : requestedName)
      : file.name;
    await uploadFileToStep(file, stepId, displayName);
  }
  return true;
}

async function pasteClipboard(stepId){
  const fileStatus = document.getElementById("file-"+stepId);
  if(!navigator.clipboard || !navigator.clipboard.read){
    if(fileStatus) fileStatus.textContent = "请在页面中按 Ctrl+V，手机端可尝试长按粘贴";
    return;
  }
  try {
    const clipboardItems = await navigator.clipboard.read();
    const files = [];
    for(const item of clipboardItems){
      for(const type of item.types){
        if(type === "text/plain" || type === "text/html") continue;
        files.push(await item.getType(type));
      }
    }
    if(!await uploadClipboardFiles(files, stepId)){
      if(fileStatus) fileStatus.textContent = "剪贴板中没有图片或文件";
    }
  } catch(e) {
    if(fileStatus) fileStatus.textContent = "无法主动读取，请在页面中按 Ctrl+V 或长按粘贴";
  }
}

document.addEventListener("paste", async event => {
  const step = steps[currentStepIndex()];
  if(!step) return;
  const files = Array.from(event.clipboardData?.files || []);
  if(!files.length){
    for(const item of Array.from(event.clipboardData?.items || [])){
      if(item.kind !== "file") continue;
      const file = item.getAsFile();
      if(file) files.push(file);
    }
  }
  if(!files.length) return;
  event.preventDefault();
  await uploadClipboardFiles(files, step.id);
});

function esc(v){ return String(v ?? "").replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s])); }

// ── 语音速记（voice notes）───────────────────────────────
const voiceState = {recognizing:false, recognition:null, mediaRecorder:null, chunks:[]};

async function loadVoiceNotes(){
  if(!selectedExperiment){ voiceNotes = []; return; }
  try { voiceNotes = await api(`/api/experiments/${selectedExperiment}/voice_notes`); }
  catch { /* 离线时保留上次内容 */ }
}

function voiceTime(iso){
  const t = new Date(iso);
  if(Number.isNaN(t.getTime())) return "";
  const p = n => String(n).padStart(2, "0");
  return `${p(t.getHours())}:${p(t.getMinutes())}`;
}

function voiceNoteHtml(n){
  let body;
  if(n.text){
    body = `<span onclick="editVoiceNote(${n.id})">${esc(n.text)}</span>`;
    if(n.audio_url) body += `<audio controls preload="none" src="${esc(n.audio_url)}"></audio>`;
  } else if(n.audio_url){
    const tag = n.status === "pending" ? "转写中…" : "录音 · 待转写";
    body = `<span class="vtag">${tag}</span><audio controls preload="none" src="${esc(n.audio_url)}"></audio>`;
  } else {
    body = '<span class="vtag">空</span>';
  }
  return `<div class="voice-note">
    <span class="vtime">${voiceTime(n.created_at)}</span>
    <span class="vbody">${body}</span>
    <span class="vops"><button onclick="deleteVoiceNote(${n.id})" title="删除" aria-label="删除速记">✕</button></span>
  </div>`;
}

function renderStepVoice(step){
  const list = voiceNotes.filter(n => n.step_id === step.id);
  const inner = list.length
    ? list.map(voiceNoteHtml).join("")
    : '<span class="small">点右下角话筒，边做边说，说完的话自动记进这一步。</span>';
  return `
    <div class="section-head">
      <h2>语音速记</h2>
      <button class="edit-link" onclick="openVoicePanel()">说一段</button>
    </div>
    <div class="voice-list">${inner}</div>`;
}

function renderVoiceAll(){
  const box = document.getElementById("voiceAllList");
  if(!box) return;
  if(!voiceNotes.length){ box.innerHTML = '<span class="small">还没有速记。</span>'; return; }
  const byStep = {};
  for(const s of steps) byStep[s.id] = s;
  box.innerHTML = voiceNotes.slice().reverse().map(n => {
    const s = byStep[n.step_id];
    const tag = s ? `<div class="small" style="margin-top:4px">Step ${s.step_index + 1} · ${esc(s.title)}</div>` : "";
    return tag + voiceNoteHtml(n);
  }).join("");
}

function editVoiceNote(id){
  const n = voiceNotes.find(x => x.id === id);
  if(!n) return;
  openModal("编辑速记", `<div class="field"><textarea id="editVoiceText">${esc(n.text || "")}</textarea></div>`, async () => {
    const text = document.getElementById("editVoiceText").value.trim();
    await api(`/api/voice_notes/${id}`, {method:"PATCH", body:JSON.stringify({text})});
    await loadVoiceNotes();
    renderSteps(steps);
    renderVoiceAll();
  });
}

async function deleteVoiceNote(id){
  if(!confirm("删除这条速记？")) return;
  try {
    await api(`/api/voice_notes/${id}`, {method:"DELETE"});
    await loadVoiceNotes();
    renderSteps(steps);
    renderVoiceAll();
  } catch(e) { alert("删除失败：" + e.message); }
}

function openVoicePanel(){
  document.getElementById("voiceBackdrop").classList.add("open");
  document.getElementById("voiceSheet").classList.add("open");
  renderVoiceAll();
}

function closeVoicePanel(){
  stopVoiceRec();
  document.getElementById("voiceBackdrop").classList.remove("open");
  document.getElementById("voiceSheet").classList.remove("open");
}

function speechSupported(){
  return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
}

function initVoice(){
  const hint = document.getElementById("voiceHint");
  if(navigator.mediaDevices && window.MediaRecorder){
    hint.textContent = "点「开始说话」录一段，停止后自动上传保存并转写。";
  } else {
    hint.textContent = "此环境不支持录音。点下方输入框，用键盘上的听写（麦克风键）也可以。";
  }
}

function setRecUI(on, label){
  const b = document.getElementById("voiceRecBtn");
  const mic = document.getElementById("micBtn");
  if(b){ b.textContent = on ? (label || "停止") : "开始说话"; b.classList.toggle("rec", on); }
  if(mic) mic.classList.toggle("rec", on);
}

function toggleVoiceRec(){
  if(voiceState.recognizing || voiceState.mediaRecorder){ stopVoiceRec(); return; }
  startRecording();
}

function startSpeech(){
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const rec = new SR();
  rec.lang = "zh-CN";
  rec.continuous = true;
  rec.interimResults = true;
  const live = document.getElementById("voiceLive");
  rec.onresult = e => {
    let interim = "";
    for(let i = e.resultIndex; i < e.results.length; i++){
      const r = e.results[i];
      if(r.isFinal){
        const t = r[0].transcript.trim();
        if(t){
          const ta = document.getElementById("voiceText");
          ta.value = (ta.value ? ta.value + " " : "") + t;
        }
      } else {
        interim += r[0].transcript;
      }
    }
    live.textContent = interim;
  };
  rec.onend = () => {
    if(voiceState.recognizing){
      try { rec.start(); } catch { voiceState.recognizing = false; setRecUI(false); }
    }
  };
  rec.onerror = e => {
    live.textContent = "";
    if(e.error === "not-allowed" || e.error === "service-not-allowed"){
      voiceState.recognizing = false;
      voiceState.recognition = null;
      setRecUI(false);
      document.getElementById("voiceHint").textContent = "麦克风权限被拒绝。可改用键盘听写，或在系统设置里允许麦克风。";
    }
  };
  voiceState.recognition = rec;
  voiceState.recognizing = true;
  setRecUI(true, "停止听写");
  try { rec.start(); } catch {}
}

async function startRecording(){
  if(!(navigator.mediaDevices && window.MediaRecorder)){
    document.getElementById("voiceHint").textContent = "此环境不支持录音，请用键盘听写。";
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio:true});
    const preferred = ["audio/mp4;codecs=mp4a.40.2","audio/mp4","audio/webm;codecs=opus","audio/webm"];
    const mimeType = preferred.find(t => MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t));
    const mr = mimeType ? new MediaRecorder(stream, {mimeType}) : new MediaRecorder(stream);
    voiceState.chunks = [];
    mr.ondataavailable = e => { if(e.data && e.data.size) voiceState.chunks.push(e.data); };
    mr.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      const type = mr.mimeType || mimeType || "audio/mp4";
      const blob = new Blob(voiceState.chunks, {type});
      voiceState.chunks = [];
      if(blob.size > 0) await uploadVoiceAudio(blob, type);
    };
    voiceState.mediaRecorder = mr;
    mr.start();
    setRecUI(true, "停止录音");
  } catch(e) {
    document.getElementById("voiceHint").textContent = "无法打开麦克风：" + e.message;
  }
}

function stopVoiceRec(){
  if(voiceState.recognition){
    voiceState.recognizing = false;
    try { voiceState.recognition.stop(); } catch {}
    voiceState.recognition = null;
  }
  if(voiceState.mediaRecorder){
    try { voiceState.mediaRecorder.stop(); } catch {}
    voiceState.mediaRecorder = null;
  }
  const live = document.getElementById("voiceLive");
  if(live) live.textContent = "";
  setRecUI(false);
}

function currentStepIdForVoice(){
  const s = steps[currentStepIndex()];
  return s ? s.id : null;
}

async function uploadVoiceAudio(blob, type){
  const ext = type.includes("mp4") ? ".m4a" : (type.includes("ogg") ? ".ogg" : (type.includes("webm") ? ".webm" : ".bin"));
  const fd = new FormData();
  fd.append("file", new File([blob], "voice" + ext, {type}));
  const sid = currentStepIdForVoice();
  if(sid) fd.append("step_id", String(sid));
  const hint = document.getElementById("voiceHint");
  try {
    const res = await fetch(`/api/experiments/${selectedExperiment}/voice_notes/audio`, {method:"POST", body:fd});
    if(!res.ok) throw new Error(await res.text());
    hint.textContent = "录音已上传。转写完成后文字会自动出现在记录里。";
    await loadVoiceNotes();
    renderSteps(steps);
    renderVoiceAll();
  } catch(e) {
    hint.textContent = "录音上传失败：" + e.message;
  }
}

async function saveVoiceText(){
  const ta = document.getElementById("voiceText");
  const text = ta.value.trim();
  if(!text){ ta.focus(); return; }
  stopVoiceRec();
  try {
    await api(`/api/experiments/${selectedExperiment}/voice_notes`, {
      method:"POST",
      body:JSON.stringify({text, step_id: currentStepIdForVoice()})
    });
    ta.value = "";
    await loadVoiceNotes();
    renderSteps(steps);
    renderVoiceAll();
  } catch(e) {
    document.getElementById("voiceHint").textContent = "保存失败（网络断了？）：" + e.message;
  }
}

// ── AI 整理草稿 ─────────────────────────────────────────
let aiDraft = null;

async function runAiOrganize(){
  const hint = document.getElementById("aiHint");
  hint.textContent = "正在让 AI 整理…（首次可能要几秒到十几秒）";
  hint.style.color = "#6d5ae0";
  try {
    const draft = await api(`/api/experiments/${selectedExperiment}/ai_organize`, {
      method:"POST", body: JSON.stringify({})
    });
    aiDraft = draft;
    hint.textContent = "";
    renderAiDraft(draft);
    openAiPanel();
  } catch(e) {
    hint.style.color = "#d98200";
    hint.textContent = "整理失败：" + (e.message || e);
  }
}

function openAiPanel(){
  document.getElementById("aiBackdrop").classList.add("open");
  document.getElementById("aiSheet").classList.add("open");
}
function closeAiPanel(){
  document.getElementById("aiBackdrop").classList.remove("open");
  document.getElementById("aiSheet").classList.remove("open");
}

function renderAiDraft(draft){
  const body = document.getElementById("aiDraftBody");
  const stepsHtml = (draft.steps || []).map((s, si) => {
    const fields = (s.fields || []).map((f, fi) => {
      const changed = f.current && f.current !== f.suggested;
      const cur = f.current ? `<span class="chg">原值 <b>${esc(f.current)}</b> → 建议 <b>${esc(f.suggested)}</b></span>`
                            : `<span class="chg">建议填 <b>${esc(f.suggested)}</b></span>`;
      const rs = f.reason ? `<div class="rs">依据：${esc(f.reason)}</div>` : "";
      return `<label class="ai-field">
        <span class="fl"><span class="k">${esc(f.label)}</span>${cur}${rs}</span>
        <input type="checkbox" data-si="${si}" data-fi="${fi}" checked />
      </label>`;
    }).join("");
    const noteBox = `<textarea data-note="${si}" placeholder="这一步的 Markdown 记录（可改）">${esc(s.note || "")}</textarea>`;
    return `<div class="ai-step" id="ai-step-${si}">
      <h3>第${s.step_index + 1}步 · ${esc(s.title)}</h3>
      ${noteBox}
      ${fields}
      <div class="voice-controls" style="margin-top:8px">
        <button class="green" onclick="applyAiStep(${si})">写入这一步</button>
      </div>
      <div class="ai-applied" id="ai-applied-${si}" style="display:none">✓ 已写入</div>
    </div>`;
  }).join("");
  const un = (draft.unassigned || "").trim()
    ? `<div class="ai-unassigned"><b>未能归入步骤：</b>${esc(draft.unassigned)}</div>` : "";
  const empty = (!draft.steps || !draft.steps.length) && !un
    ? '<div class="small">AI 没能从速记里提取到可写入的内容。</div>' : "";
  body.innerHTML = `<div class="small" style="margin-bottom:8px">模型：${esc(draft.provider)}/${esc(draft.model)} · 来源 ${draft.source_note_count} 条速记</div>${stepsHtml}${un}${empty}`;
}

async function applyAiStep(si){
  const s = aiDraft && aiDraft.steps && aiDraft.steps[si];
  if(!s) return;
  const stepId = s.step_id;
  const step = steps.find(x => x.id === stepId);
  if(!step){ alert("步骤未找到，请先刷新实验。"); return; }
  const values = {...(step.values || {})};
  // fields
  document.querySelectorAll(`input[data-si="${si}"]:checked`).forEach(cb => {
    const f = s.fields[Number(cb.dataset.fi)];
    if(f) values[f.key] = f.suggested;
  });
  // note (append to existing, avoid duplicating)
  const ta = document.querySelector(`textarea[data-note="${si}"]`);
  const noteText = ta ? ta.value.trim() : (s.note || "");
  if(noteText){
    const prev = String(values[STEP_NOTES_KEY] || "").trim();
    values[STEP_NOTES_KEY] = prev && !prev.includes(noteText) ? (prev + "\\n\\n" + noteText) : (prev || noteText);
  }
  try {
    await api(`/api/steps/${stepId}`, {method:"PATCH", body: JSON.stringify({values_json: JSON.stringify(values)})});
    setLocalStep(stepId, {values});
    const badge = document.getElementById("ai-applied-" + si);
    if(badge) badge.style.display = "block";
    renderSteps(steps);
  } catch(e) {
    alert("写入失败：" + e.message);
  }
}

async function applyAllAi(){
  if(!aiDraft || !aiDraft.steps) { closeAiPanel(); return; }
  for(let si = 0; si < aiDraft.steps.length; si++){
    await applyAiStep(si);
  }
  document.getElementById("aiHint").textContent = "已全部写入记录。";
  document.getElementById("aiHint").style.color = "#2e9e5b";
  setTimeout(closeAiPanel, 600);
}

// 语音速记列表定时刷新：等待中的转写完成后自动出现
setInterval(async () => {
  if(!voiceNotes.some(n => n.status === "pending")) return;
  const before = JSON.stringify(voiceNotes);
  await loadVoiceNotes();
  if(JSON.stringify(voiceNotes) !== before){
    renderSteps(steps);
    renderVoiceAll();
  }
}, 8000);

window.addEventListener("online", syncNow);
setInterval(syncNow, 15000);
setInterval(refreshTimers, 1000);
setInterval(tickBoardTimers, 1000);
setInterval(pollBoardTimers, 5000);
function applyBackTarget(){
  const link = document.getElementById("backToFlet");
  if(!link) return;
  // Web-only: home is the capture page.
  link.href = "/capture";
  link.title = "速记";
}
renderQueueInfo();
applyBackTarget();
initVoice();

async function initRunner(){
  await loadExperiments();
  const urlExp = new URLSearchParams(window.location.search).get("experiment_id");
  if(urlExp){ await enterExperiment(urlExp); }
  else { showBoard(); }
}
initRunner();
</script>
"""


@app.get("/run", response_class=HTMLResponse)
@app.get("/mobile", response_class=HTMLResponse)
def experiment_runner(experiment_id: Optional[int] = Query(None)):
    body = _RUNNER_BODY.replace("__ICON_JS__", web_ui.ICON_JS)
    body = _fill_icons(body, {
        "__I_REFRESH__": ("refresh", 18), "__I_MIC__": ("mic", 18),
        "__I_SPARK__": ("sparkle", 17), "__I_BACK__": ("chevron-left", 20),
    })
    return _html_response(
        web_ui.page_head("ELN 实验执行", _NAV_CSS + _RUNNER_CSS)
        + body
        + _bottom_nav("run", "/")
        + web_ui.TIMER_DOCK_HTML
        + "\n</body>\n</html>",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


# ─────────────────────────────────────────────
# Experiments
# ─────────────────────────────────────────────

@app.get("/api/experiments")
def list_experiments(
    status: Optional[str] = Query(None),
    limit: Optional[int] = Query(None, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return db_ops.list_experiment_summaries(status=status, limit=limit, offset=offset)


@app.post("/api/experiments", status_code=201)
def create_experiment(body: ExperimentCreate):
    try:
        protocol = ProtocolDefinition.from_json(body.protocol_json)
    except Exception as exc:
        raise HTTPException(400, f"Invalid protocol_json: {exc}")
    exp = db_ops.create_experiment(
        name=body.name,
        protocol=protocol,
        protocol_id=body.protocol_id,
        notes=body.notes,
    )
    return {"id": exp.id, "name": exp.name, "created_at": exp.created_at, "status": exp.status}


@app.get("/api/experiments/{exp_id}")
def get_experiment(exp_id: int):
    exp = db_ops.get_experiment(exp_id)
    if not exp:
        raise HTTPException(404, "Experiment not found")
    progress = db_ops.get_experiment_progress(exp_id)
    return {
        "id": exp.id, "name": exp.name, "created_at": exp.created_at,
        "status": exp.status, "protocol_json": exp.protocol_json,
        "protocol_id": exp.protocol_id, "notes": exp.notes,
        **progress,
    }


@app.patch("/api/experiments/{exp_id}")
def update_experiment(exp_id: int, body: ExperimentUpdate):
    exp = db_ops.get_experiment(exp_id)
    if not exp:
        raise HTTPException(404, "Experiment not found")
    updates = body.model_dump(exclude_none=True)
    exp = db_ops.update_experiment(exp_id, **updates)
    return {"id": exp.id, "name": exp.name, "status": exp.status}


@app.delete("/api/experiments/{exp_id}", status_code=204)
def delete_experiment(exp_id: int):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    db_ops.delete_experiment(exp_id)


# ─────────────────────────────────────────────
# Steps
# ─────────────────────────────────────────────

@app.get("/api/experiments/{exp_id}/steps")
def get_steps(exp_id: int):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    steps = db_ops.get_steps(exp_id)
    return [_step_to_dict(s) for s in steps]


@app.get("/api/steps/{step_id}")
def get_step(step_id: int):
    step = db_ops.get_step(step_id)
    if not step:
        raise HTTPException(404, "Step not found")
    return _step_to_dict(step)


@app.patch("/api/steps/{step_id}")
def update_step(step_id: int, body: StepUpdate):
    if not db_ops.get_step(step_id):
        raise HTTPException(404, "Step not found")
    updates = body.model_dump(exclude_none=True)
    # Convert bool to int for SQLite
    if "photo_pending" in updates:
        updates["photo_pending"] = int(updates["photo_pending"])
    step = db_ops.update_step(step_id, **updates)
    return _step_to_dict(step)


@app.patch("/api/steps/{step_id}/attachments/name")
def rename_step_attachment(step_id: int, body: AttachmentRename):
    if not db_ops.get_step(step_id):
        raise HTTPException(404, "Step not found")
    if not body.name.strip():
        raise HTTPException(400, "Attachment name cannot be empty")
    try:
        step = db_ops.rename_attachment(step_id, body.path, body.name)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return _step_to_dict(step)


@app.post("/api/steps/{step_id}/complete")
def complete_step(step_id: int):
    step = db_ops.get_step(step_id)
    if not step:
        raise HTTPException(404, "Step not found")
    if step.effective_timer_seconds > 0:
        from timer_manager import get_timer_manager

        tm = get_timer_manager()
        tm.start()
        state = tm.get_state(step.experiment_id, step.id)
        if state is None:
            persisted = db_ops.get_timer(step.experiment_id, step.id)
            if persisted:
                tm.create_or_restore(
                    step.experiment_id,
                    step.id,
                    persisted.total_seconds,
                    remaining_seconds=persisted.remaining_seconds,
                    overtime_seconds=persisted.overtime_seconds,
                    status=persisted.status,
                    timer_finished_at=persisted.timer_finished_at,
                    started_at=persisted.started_at,
                )
            else:
                tm.create_or_restore(
                    step.experiment_id,
                    step.id,
                    step.effective_timer_seconds,
                )
        tm.complete_timer(step.experiment_id, step.id)
        try:
            from notifications import stop_alert_sound
            stop_alert_sound()
        except Exception:
            pass
    step = db_ops.complete_step(step_id)
    return _step_to_dict(step)


@app.get("/api/experiments/{exp_id}/pending_photos")
def get_pending_photos(exp_id: int):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    steps = db_ops.get_pending_photo_steps(exp_id)
    return [_step_to_dict(s) for s in steps]


def _step_to_dict(step) -> dict:
    return {
        "id": step.id,
        "experiment_id": step.experiment_id,
        "step_index": step.step_index,
        "title": step.title,
        "description": step.description,
        "timer_seconds": step.timer_seconds,
        "timer_override_seconds": step.timer_override_seconds,
        "effective_timer_seconds": step.effective_timer_seconds,
        "timer_finished_at": step.timer_finished_at,
        "overtime_seconds": step.overtime_seconds,
        "has_camera": bool(step.has_camera),
        "camera_required": bool(step.camera_required),
        "fields": step.get_fields(),
        "values": step.get_values(),
        "description_overrides": step.get_description_overrides(),
        "photo_paths": step.get_photo_paths(),
        "attachments": step.get_attachments(),
        "photo_pending": bool(step.photo_pending),
        "completed_at": step.completed_at,
    }


# ─────────────────────────────────────────────
# Timers
# ─────────────────────────────────────────────

@app.get("/api/timers/experiment/{exp_id}")
def list_experiment_timers(exp_id: int):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    return [_timer_to_dict(timer) for timer in db_ops.list_experiment_timers(exp_id)]


@app.get("/api/timers/{exp_id}/{step_id}")
def get_timer(exp_id: int, step_id: int):
    timer = db_ops.get_timer(exp_id, step_id)
    if not timer:
        raise HTTPException(404, "Timer not found")
    return _timer_to_dict(timer)


@app.put("/api/timers/{exp_id}/{step_id}")
def upsert_timer(exp_id: int, step_id: int, body: TimerUpdate):
    # Fetch existing or use defaults
    existing = db_ops.get_timer(exp_id, step_id)
    total = body.total_seconds if body.total_seconds is not None else (existing.total_seconds if existing else 0)
    remaining = body.remaining_seconds if body.remaining_seconds is not None else (existing.remaining_seconds if existing else total)
    overtime = body.overtime_seconds if body.overtime_seconds is not None else (existing.overtime_seconds if existing else 0)
    status = body.status if body.status is not None else (existing.status if existing else "idle")
    finished_at = body.timer_finished_at if body.timer_finished_at is not None else (existing.timer_finished_at if existing else None)
    started_at = body.started_at if body.started_at is not None else (existing.started_at if existing else None)

    timer = db_ops.upsert_timer(
        experiment_id=exp_id, step_id=step_id,
        total_seconds=total, remaining_seconds=remaining,
        overtime_seconds=overtime, status=status,
        timer_finished_at=finished_at, started_at=started_at,
    )
    return _timer_to_dict(timer)


@app.patch("/api/timers/{exp_id}/{step_id}")
def patch_timer(exp_id: int, step_id: int, body: TimerUpdate):
    return upsert_timer(exp_id, step_id, body)


def _require_timer_step(exp_id: int, step_id: int):
    step = db_ops.get_step(step_id)
    if not step or step.experiment_id != exp_id:
        raise HTTPException(404, "Step not found for experiment")
    if step.completed_at:
        raise HTTPException(409, "Completed step timer cannot be restarted")
    if step.effective_timer_seconds <= 0:
        raise HTTPException(400, "Step has no timer")
    return step


def _ensure_managed_timer(exp_id: int, step_id: int):
    step = _require_timer_step(exp_id, step_id)
    from timer_manager import get_timer_manager

    tm = get_timer_manager()
    tm.start()
    state = tm.get_state(exp_id, step_id)
    if state is None:
        persisted = db_ops.get_timer(exp_id, step_id)
        if persisted:
            state = tm.create_or_restore(
                exp_id,
                step_id,
                persisted.total_seconds,
                remaining_seconds=persisted.remaining_seconds,
                overtime_seconds=persisted.overtime_seconds,
                status=persisted.status,
                timer_finished_at=persisted.timer_finished_at,
                started_at=persisted.started_at,
            )
        else:
            state = tm.create_or_restore(exp_id, step_id, step.effective_timer_seconds)
    elif (
        state.total_seconds != step.effective_timer_seconds
        and state.status not in ("overtime", "confirmed")
    ):
        state = tm.set_total_seconds(exp_id, step_id, step.effective_timer_seconds) or state
    return tm, state


@app.post("/api/timers/{exp_id}/{step_id}/start")
def start_managed_timer(exp_id: int, step_id: int):
    tm, _ = _ensure_managed_timer(exp_id, step_id)
    state = tm.start_timer(exp_id, step_id)
    return _timer_state_to_dict(state)


@app.post("/api/timers/{exp_id}/{step_id}/sync")
def sync_managed_timer(exp_id: int, step_id: int, body: TimerSync):
    tm, _ = _ensure_managed_timer(exp_id, step_id)
    state = tm.sync_timer(
        exp_id,
        step_id,
        total_seconds=body.total_seconds,
        remaining_seconds=body.remaining_seconds,
        overtime_seconds=body.overtime_seconds,
        status=body.status,
        action=body.action,
        elapsed_seconds=body.elapsed_seconds,
    )
    return _timer_state_to_dict(state)


@app.post("/api/timers/{exp_id}/{step_id}/pause")
def pause_managed_timer(exp_id: int, step_id: int):
    tm, _ = _ensure_managed_timer(exp_id, step_id)
    state = tm.pause_timer(exp_id, step_id)
    return _timer_state_to_dict(state)


@app.post("/api/timers/{exp_id}/{step_id}/reset")
def reset_managed_timer(exp_id: int, step_id: int):
    tm, _ = _ensure_managed_timer(exp_id, step_id)
    state = tm.reset_timer(exp_id, step_id)
    try:
        from notifications import stop_alert_sound
        stop_alert_sound()
    except Exception:
        pass
    return _timer_state_to_dict(state)


@app.post("/api/timers/{exp_id}/{step_id}/confirm")
def confirm_managed_timer(exp_id: int, step_id: int):
    tm, _ = _ensure_managed_timer(exp_id, step_id)
    state = tm.confirm_overtime(exp_id, step_id)
    try:
        from notifications import stop_alert_sound
        stop_alert_sound()
    except Exception:
        pass
    return _timer_state_to_dict(state)


@app.get("/api/timers/active")
def list_active_timers():
    timers = db_ops.list_active_timers()
    result = []
    for t in timers:
        d = _timer_to_dict(t)
        step = db_ops.get_step(t.step_id)
        exp = db_ops.get_experiment(t.experiment_id)
        d["step_title"] = step.title if step else f"Step {t.step_id}"
        d["step_index"] = step.step_index if step else 0
        d["experiment_name"] = exp.name if exp else f"实验 {t.experiment_id}"
        result.append(d)
    return result


def _timer_to_dict(timer) -> dict:
    return {
        "id": timer.id,
        "experiment_id": timer.experiment_id,
        "step_id": timer.step_id,
        "total_seconds": timer.total_seconds,
        "remaining_seconds": timer.remaining_seconds,
        "overtime_seconds": timer.overtime_seconds,
        "status": timer.status,
        "timer_finished_at": timer.timer_finished_at,
        "started_at": timer.started_at,
        "updated_at": timer.updated_at,
    }


def _timer_state_to_dict(state) -> dict:
    if state is None:
        raise HTTPException(404, "Timer not found")
    return {
        "id": state.timer_id,
        "experiment_id": state.experiment_id,
        "step_id": state.step_id,
        "total_seconds": state.total_seconds,
        "remaining_seconds": state.remaining_seconds,
        "overtime_seconds": state.overtime_seconds,
        "display_seconds": state.display_seconds,
        "status": state.status,
        "timer_finished_at": state.timer_finished_at,
        "started_at": state.started_at,
    }


# ─────────────────────────────────────────────
# Photos
# ─────────────────────────────────────────────

@app.post("/api/photos/upload")
async def upload_photo(
    step_id: int = Query(...),
    file: UploadFile = File(...),
    attachment_name: str = Form(""),
):
    step = db_ops.get_step(step_id)
    if not step:
        raise HTTPException(404, "Step not found")

    # Save to photos/{exp_id}/{step_id}_{timestamp}.*. The column is named
    # photo_paths for compatibility, but it can also store general attachments.
    photos_dir = _photos_dir()
    sub_dir = os.path.join(photos_dir, str(step.experiment_id))
    os.makedirs(sub_dir, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    ext = os.path.splitext(file.filename or "photo.jpg")[1] or ".jpg"
    filename = f"step{step_id}_{ts}{ext}"
    filepath = os.path.join(sub_dir, filename)

    with open(filepath, "wb") as f:
        shutil.copyfileobj(file.file, f)
    if os.path.getsize(filepath) <= 0:
        try:
            os.remove(filepath)
        except OSError:
            pass
        raise HTTPException(400, "文件为空，请重新拍照或选择文件")

    # Relative path for URL construction
    rel_path = f"{step.experiment_id}/{filename}"
    display_name = attachment_name.strip() or os.path.basename(file.filename or filename)
    db_ops.add_photo_to_step(step_id, rel_path, display_name)

    return {"path": rel_path, "name": display_name, "url": f"/photos/{rel_path}"}


# ─────────────────────────────────────────────
# Voice notes (语音速记)
# ─────────────────────────────────────────────

def _voice_note_to_dict(note: dict) -> dict:
    d = dict(note)
    if d.get("audio_path"):
        d["audio_url"] = _audio_url(str(d["audio_path"]))
    return d


@app.get("/api/experiments/{exp_id}/voice_notes")
def list_voice_notes(exp_id: int):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    return [_voice_note_to_dict(n) for n in db_ops.list_voice_notes(exp_id)]


@app.post("/api/experiments/{exp_id}/voice_notes", status_code=201)
def create_voice_note(exp_id: int, body: VoiceNoteCreate):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "text is required")
    note = db_ops.create_voice_note(exp_id, text=text, step_id=body.step_id, status="done")
    return _voice_note_to_dict(note)


@app.post("/api/experiments/{exp_id}/voice_notes/audio", status_code=201)
async def upload_voice_audio(
    exp_id: int,
    file: UploadFile = File(...),
    step_id: Optional[int] = Form(None),
):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    audio_dir = _audio_dir()
    sub_dir = os.path.join(audio_dir, str(exp_id), "voice")
    os.makedirs(sub_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    ext = os.path.splitext(file.filename or "note.m4a")[1] or ".m4a"
    filename = f"voice_{ts}{ext}"
    filepath = os.path.join(sub_dir, filename)
    with open(filepath, "wb") as f:
        shutil.copyfileobj(file.file, f)
    if os.path.getsize(filepath) <= 0:
        try:
            os.remove(filepath)
        except OSError:
            pass
        raise HTTPException(400, "录音为空，请重试")

    try:
        from server import audio_tools
        audio_tools.remux_to_faststart_mp4(filepath)
    except Exception as exc:
        print(f"[audio] remux error for voice note (exp {exp_id}): {exc}")

    from server import voice as voice_worker
    status = "pending" if voice_worker.transcription_available() else "audio_only"
    rel_path = f"{exp_id}/voice/{filename}"
    note = db_ops.create_voice_note(
        exp_id, text="", step_id=step_id, audio_path=rel_path, status=status,
    )
    if status == "pending":
        voice_worker.notify_new_audio()
    return _voice_note_to_dict(note)


# ─────────────────────────────────────────────
# Inbox (速记捕捉收件箱)
# ─────────────────────────────────────────────

class InboxCreate(BaseModel):
    text: str = ""
    hinted_experiment_id: Optional[int] = None


class InboxUpdate(BaseModel):
    text: Optional[str] = None
    hinted_experiment_id: Optional[int] = None


class InboxProposal(BaseModel):
    experiment_id: Optional[int] = None
    step_id: Optional[int] = None
    note: str = ""
    fields: Optional[list[dict]] = None
    reason: str = ""


class InboxApply(BaseModel):
    # confirmed target; falls back to the stored proposal when omitted
    experiment_id: Optional[int] = None
    step_id: Optional[int] = None
    note: Optional[str] = None
    fields: Optional[list[dict]] = None
    attach_images: bool = True


class InboxFiled(BaseModel):
    # agent already wrote the data directly; just record where it landed
    experiment_id: Optional[int] = None
    step_id: Optional[int] = None
    summary: str = ""


def _inbox_to_dict(entry: dict) -> dict:
    d = dict(entry)
    image_urls = []
    file_urls = []
    for path in d.get("image_paths", []):
        clean = str(path).replace("\\", "/")
        name = os.path.basename(clean)
        url = "/photos/" + clean
        ext = os.path.splitext(name.lower())[1]
        if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}:
            image_urls.append(url)
        else:
            file_urls.append({"url": url, "name": name or clean})
    d["image_urls"] = image_urls
    d["file_urls"] = file_urls
    if d.get("audio_path"):
        d["audio_url"] = _audio_url(str(d["audio_path"]))
    return d


def _safe_upload_name(name: str, fallback: str) -> str:
    raw = os.path.basename(str(name or "").replace("\\", "/")).strip()
    if not raw:
        raw = fallback
    cleaned = "".join(ch if ch not in '<>:"/\\|?*\x00' else "_" for ch in raw)
    cleaned = cleaned.strip(" .")
    return (cleaned or fallback)[:160]


@app.get("/api/inbox")
def list_inbox(status: Optional[str] = Query("pending")):
    status = None if status in ("all", "") else status
    return [_inbox_to_dict(e) for e in db_ops.list_inbox_entries(status)]


@app.get("/api/inbox/{entry_id}")
def get_inbox(entry_id: int):
    e = db_ops.get_inbox_entry(entry_id)
    if not e:
        raise HTTPException(404, "Inbox entry not found")
    return _inbox_to_dict(e)


@app.post("/api/inbox", status_code=201)
def create_inbox(body: InboxCreate):
    hint = body.hinted_experiment_id
    if hint and not db_ops.get_experiment(hint):
        hint = None
    entry = db_ops.create_inbox_entry(text=body.text.strip(), hinted_experiment_id=hint)
    return _inbox_to_dict(entry)


@app.patch("/api/inbox/{entry_id}")
def patch_inbox(entry_id: int, body: InboxUpdate):
    if not db_ops.get_inbox_entry(entry_id):
        raise HTTPException(404, "Inbox entry not found")
    updates = body.model_dump(exclude_unset=True)
    return _inbox_to_dict(db_ops.update_inbox_entry(entry_id, **updates))


@app.post("/api/inbox/{entry_id}/media", status_code=201)
async def upload_inbox_media(
    entry_id: int,
    file: UploadFile = File(...),
    kind: str = Form("image"),
):
    entry = db_ops.get_inbox_entry(entry_id)
    if not entry:
        raise HTTPException(404, "Inbox entry not found")
    if kind == "audio":
        sub_dir = os.path.join(db_ops.get_inbox_audio_dir(), str(entry_id))
    else:
        sub_dir = os.path.join(db_ops.get_inbox_dir(), str(entry_id))
    os.makedirs(sub_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    default_ext = ".m4a" if kind == "audio" else ".jpg"
    ext = os.path.splitext(file.filename or ("a" + default_ext))[1] or default_ext
    if kind == "file":
        original = _safe_upload_name(file.filename or f"clipboard_file{ext}", f"clipboard_file{ext}")
        stem, original_ext = os.path.splitext(original)
        filename = f"{stem}_{ts}{original_ext or ext}"
    else:
        filename = f"{kind}_{ts}{ext}"
    filepath = os.path.join(sub_dir, filename)
    with open(filepath, "wb") as f:
        shutil.copyfileobj(file.file, f)
    if os.path.getsize(filepath) <= 0:
        try:
            os.remove(filepath)
        except OSError:
            pass
        raise HTTPException(400, "文件为空")
    rel_path = f"inbox/{entry_id}/{filename}"
    if kind == "audio":
        # Remux fragmented phone MP4 → plain faststart MP4 so <audio> plays it fully.
        try:
            from server import audio_tools
            audio_tools.remux_to_faststart_mp4(filepath)
        except Exception as exc:
            print(f"[audio] remux error for {entry_id}: {exc}")
        updated = db_ops.set_inbox_audio(entry_id, rel_path)
        # Transcribe in the background: long clips take the slower recording-file
        # recognition path, so we don't block the archive request on it.
        try:
            from server import voice as voice_worker
            if voice_worker.transcription_available():
                voice_worker.transcribe_inbox_entry_async(entry_id, filepath)
        except Exception as exc:
            print(f"[voice] inbox transcription trigger failed for {entry_id}: {exc}")
    else:
        updated = db_ops.add_inbox_image(entry_id, rel_path)
    return _inbox_to_dict(updated)


@app.post("/api/inbox/{entry_id}/proposal")
def set_inbox_proposal(entry_id: int, body: InboxProposal):
    if not db_ops.get_inbox_entry(entry_id):
        raise HTTPException(404, "Inbox entry not found")
    proposal = body.model_dump()
    updated = db_ops.update_inbox_entry(entry_id, proposal=proposal)
    return _inbox_to_dict(updated)


@app.post("/api/inbox/{entry_id}/apply")
def apply_inbox(entry_id: int, body: InboxApply):
    entry = db_ops.get_inbox_entry(entry_id)
    if not entry:
        raise HTTPException(404, "Inbox entry not found")
    proposal = entry.get("proposal") or {}
    exp_id = body.experiment_id or proposal.get("experiment_id")
    step_id = body.step_id or proposal.get("step_id")
    note = body.note if body.note is not None else proposal.get("note", "")
    fields = body.fields if body.fields is not None else proposal.get("fields")

    step = db_ops.get_step(step_id) if step_id else None
    if not step:
        raise HTTPException(400, "请先指定要写入的步骤")
    if exp_id and step.experiment_id != exp_id:
        raise HTTPException(400, "步骤不属于所选实验")
    exp_id = step.experiment_id

    values = dict(step.get_values())
    # merge field values
    valid_keys = {f.key for f in step.get_fields()}
    for f in (fields or []):
        key = str(f.get("key") or "").strip()
        if key and key in valid_keys and f.get("value") is not None:
            values[key] = str(f.get("value"))
    # append note
    note = str(note or "").strip()
    if note:
        prev = str(values.get(STEP_NOTES_KEY, "") or "").strip()
        values[STEP_NOTES_KEY] = f"{prev}\n\n{note}" if prev and note not in prev else (prev or note)
    db_ops.update_step(step_id, values_json=json.dumps(values, ensure_ascii=False))

    # attach captured images to the step
    if body.attach_images:
        for i, rel in enumerate(entry.get("image_paths", []), 1):
            name = os.path.basename(str(rel).replace("\\", "/"))
            ext = os.path.splitext(name.lower())[1]
            label = f"速记图 {i}" if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"} else (name or f"速记附件 {i}")
            db_ops.add_photo_to_step(step_id, rel, label)

    db_ops.update_inbox_entry(
        entry_id, status="filed",
        filed_experiment_id=exp_id, filed_step_id=step_id, filed_at=db_ops._now(),
    )
    return {"ok": True, "experiment_id": exp_id, "step_id": step_id}


@app.post("/api/inbox/{entry_id}/filed")
def mark_inbox_filed(entry_id: int, body: InboxFiled):
    """Mark a capture as已归档 after an agent wrote it directly via the granular
    step/experiment APIs. Records where it went + a short summary for the audit view.
    Does not write experiment data itself — that is the agent's job."""
    entry = db_ops.get_inbox_entry(entry_id)
    if not entry:
        raise HTTPException(404, "Inbox entry not found")
    record = {"summary": (body.summary or "").strip()}
    if body.experiment_id is not None:
        record["experiment_id"] = body.experiment_id
    if body.step_id is not None:
        record["step_id"] = body.step_id
    # The transcript is now in the experiment record — drop the recording to save space.
    audio_deleted = False
    if entry.get("audio_path"):
        audio_deleted = _delete_audio_file(entry["audio_path"])
        db_ops.set_inbox_audio(entry_id, "")
    db_ops.update_inbox_entry(
        entry_id, status="filed", proposal=record,
        filed_experiment_id=body.experiment_id, filed_step_id=body.step_id,
        filed_at=db_ops._now(),
    )
    return {"ok": True, "audio_deleted": audio_deleted}


@app.post("/api/inbox/{entry_id}/dismiss")
def dismiss_inbox(entry_id: int):
    if not db_ops.get_inbox_entry(entry_id):
        raise HTTPException(404, "Inbox entry not found")
    db_ops.update_inbox_entry(entry_id, status="dismissed")
    return {"ok": True}


@app.delete("/api/inbox/{entry_id}", status_code=204)
def delete_inbox(entry_id: int):
    if not db_ops.delete_inbox_entry(entry_id):
        raise HTTPException(404, "Inbox entry not found")


@app.get("/api/experiment_summaries")
def experiment_summaries(status: Optional[str] = Query("active,needs_wrapup")):
    rows = db_ops.list_experiment_summaries(status=status)
    return [
        {
            "id": r["id"], "name": r["name"], "status": r["status"],
            "total_steps": r.get("total_steps", 0),
            "completed_steps": r.get("completed_steps", 0),
            "created_at": r.get("created_at"),
            "completed_at": r.get("completed_at"),
        }
        for r in rows
    ]


@app.get("/api/preview")
def image_preview(path: str = Query(...), max: int = Query(1600, ge=64, le=4096)):
    """Render formats browsers can't show in <img> (TIFF, BMP) as PNG for preview.
    Serves a downscaled PNG from the original file under the photos dir."""
    try:
        from PIL import Image
    except Exception:
        raise HTTPException(500, "Pillow 未安装，无法预览此格式")
    base = os.path.realpath(_photos_dir())
    clean = str(path).replace("\\", "/").lstrip("/")
    full = os.path.realpath(os.path.join(base, clean.replace("/", os.sep)))
    if full != base and not full.startswith(base + os.sep):
        raise HTTPException(403, "非法路径")
    if not os.path.isfile(full):
        raise HTTPException(404, "文件不存在")
    try:
        with Image.open(full) as im:
            try:
                im.seek(0)  # first page of multi-page TIFF
            except Exception:
                pass
            if im.mode not in ("RGB", "RGBA", "L"):
                im = im.convert("RGB")
            im.thumbnail((max, max))
            buf = io.BytesIO()
            im.save(buf, format="PNG")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(415, f"无法渲染此图片：{exc}")
    return Response(content=buf.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "max-age=3600"})


class AiOrganizeRequest(BaseModel):
    note_texts: Optional[list[str]] = None


class AiSettings(BaseModel):
    provider: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None


class TranscriptionSettings(BaseModel):
    provider: Optional[str] = None
    tencent_secret_id: Optional[str] = None
    tencent_secret_key: Optional[str] = None
    tencent_region: Optional[str] = None
    tencent_engine: Optional[str] = None
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None
    openai_model: Optional[str] = None


@app.get("/api/ai/status")
def ai_status():
    from utils.app_settings import get_ai_config
    cfg = get_ai_config()
    return {
        "configured": bool(cfg.get("api_key")),
        "provider": cfg.get("provider"),
        "model": cfg.get("model"),
    }


@app.get("/api/settings/ai")
def get_settings_ai():
    from utils.app_settings import get_ai_config
    cfg = get_ai_config()
    return {
        "provider": cfg.get("provider"),
        "base_url": cfg.get("base_url", ""),
        "model": cfg.get("model", ""),
        "has_key": bool(cfg.get("api_key")),
    }


@app.post("/api/settings/ai")
def post_settings_ai(body: AiSettings):
    from utils.app_settings import set_ai_config
    # api_key: only overwrite when a non-empty value is provided
    key = body.api_key if (body.api_key and body.api_key.strip()) else None
    set_ai_config(provider=body.provider, api_key=key,
                  base_url=body.base_url, model=body.model)
    return get_settings_ai()


@app.get("/api/settings/transcription")
def get_settings_transcription():
    from utils.app_settings import get_transcription_config
    cfg = get_transcription_config()
    return {
        "provider": cfg.get("provider"),
        "tencent_region": cfg.get("tencent_region", ""),
        "tencent_engine": cfg.get("tencent_engine", ""),
        "openai_base_url": cfg.get("openai_base_url", ""),
        "openai_model": cfg.get("openai_model", ""),
        "has_tencent_secret_id": bool(cfg.get("tencent_secret_id")),
        "has_tencent_secret_key": bool(cfg.get("tencent_secret_key")),
        "has_openai_api_key": bool(cfg.get("openai_api_key")),
    }


@app.post("/api/settings/transcription")
def post_settings_transcription(body: TranscriptionSettings):
    from utils.app_settings import set_transcription_config
    secret_id = (
        body.tencent_secret_id
        if (body.tencent_secret_id and body.tencent_secret_id.strip())
        else None
    )
    secret_key = (
        body.tencent_secret_key
        if (body.tencent_secret_key and body.tencent_secret_key.strip())
        else None
    )
    openai_key = (
        body.openai_api_key
        if (body.openai_api_key and body.openai_api_key.strip())
        else None
    )
    set_transcription_config(
        provider=body.provider,
        tencent_secret_id=secret_id,
        tencent_secret_key=secret_key,
        tencent_region=body.tencent_region,
        tencent_engine=body.tencent_engine,
        openai_api_key=openai_key,
        openai_base_url=body.openai_base_url,
        openai_model=body.openai_model,
    )
    return get_settings_transcription()


@app.post("/api/experiments/{exp_id}/ai_organize")
def ai_organize(exp_id: int, body: AiOrganizeRequest):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    from server import ai_organize as organizer
    try:
        return organizer.organize_experiment(exp_id, body.note_texts)
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@app.patch("/api/voice_notes/{note_id}")
def update_voice_note(note_id: int, body: VoiceNoteUpdate):
    if not db_ops.get_voice_note(note_id):
        raise HTTPException(404, "Voice note not found")
    updates = body.model_dump(exclude_none=True)
    if "text" in updates:
        updates["status"] = "done"
    note = db_ops.update_voice_note(note_id, **updates)
    return _voice_note_to_dict(note)


@app.delete("/api/voice_notes/{note_id}", status_code=204)
def delete_voice_note(note_id: int):
    if not db_ops.delete_voice_note(note_id):
        raise HTTPException(404, "Voice note not found")


@app.get("/web/upload/{step_id}", response_class=HTMLResponse)
def web_upload_form(step_id: int):
    step = db_ops.get_step(step_id)
    if not step:
        raise HTTPException(404, "Step not found")
    _write_eln_return_target(step.experiment_id, step_id)
    app_url = _eln_step_url(step.experiment_id, step_id)
    existing_attachments = []
    for item in step.get_attachments():
        existing_attachments.append(f"""
        <form class="rename-row" method="post" action="/web/upload/{step_id}/rename">
          <input type="hidden" name="attachment_path" value="{_html_escape(item['path'])}" />
          <input type="text" name="attachment_name" value="{_html_escape(item['name'])}"
                 aria-label="附件名称" maxlength="240" />
          <button type="submit">保存名称</button>
        </form>
        """)
    existing_html = (
        '<section class="existing"><h2>已有附件</h2>'
        '<p class="muted">名称只用于显示和报告，不会改动物理文件。</p>'
        + "".join(existing_attachments)
        + "</section>"
        if existing_attachments else ""
    )
    return _html_response(f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>上传照片/文件 · Step {step.step_index + 1}</title>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 40px; color: #222; }}
    main {{ max-width: 560px; }}
    h1 {{ font-size: 22px; }}
    form {{ display: grid; gap: 16px; margin-top: 24px; }}
    input, button, a.button, label.button {{ font-size: 16px; }}
    button, a.button, label.button {{ display: inline-block; width: fit-content; border: 0; border-radius: 8px; background: #fb8c00; color: white; padding: 10px 18px; cursor: pointer; text-decoration: none; }}
    a.secondary {{ background: #f3f3f3; color: #333; }}
    .actions {{ display: flex; gap: 12px; align-items: center; margin-bottom: 28px; flex-wrap: wrap; }}
    .upload-actions {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }}
    input[type=file] {{ position: absolute; left: -9999px; width: 1px; height: 1px; opacity: 0; }}
    input[type=text] {{ box-sizing:border-box; width:100%; border:1px solid #ccc; border-radius:8px; padding:10px 12px; }}
    .filename {{ min-height: 24px; color: #444; }}
    .muted {{ color: #666; }}
    .name-field {{ display:grid; gap:6px; }}
    .existing {{ margin-top:36px; padding-top:20px; border-top:1px solid #ddd; }}
    .existing h2 {{ font-size:18px; }}
    .rename-row {{ display:grid; grid-template-columns:minmax(0,1fr) auto; gap:10px; margin:10px 0; }}
    .rename-row button {{ padding:9px 12px; }}
  </style>
</head>
<body>
  <main>
    <div class="actions">
      <a class="button" href="{app_url}">返回 ELN</a>
      <a class="button secondary" href="javascript:history.back()">返回上一页</a>
    </div>
    <h1>上传照片/文件</h1>
    <p><b>{_html_escape(step.title)}</b></p>
    <p class="muted">在 iPhone 上点“拍照”会打开相机；也可以从相册选择，或上传任意文件。上传成功后，回到 ELN 页面刷新即可看到记录。</p>
    <form method="post" enctype="multipart/form-data">
      <div class="upload-actions">
        <label class="button" for="cameraFile">拍照</label>
        <label class="button secondary" for="galleryFile">从相册选择</label>
        <label class="button secondary" for="anyFile">选择文件</label>
      </div>
      <input id="cameraFile" name="file" type="file" accept="image/*" capture="environment" />
      <input id="galleryFile" name="file" type="file" accept="image/*" />
      <input id="anyFile" name="file" type="file" />
      <div id="filename" class="filename">尚未选择照片或文件</div>
      <label class="name-field">
        <span>附件名称</span>
        <input id="attachmentName" name="attachment_name" type="text" maxlength="240"
               placeholder="默认使用原文件名，也可以改成容易识别的名称" />
      </label>
      <button type="submit">上传</button>
    </form>
    {existing_html}
  </main>
  <script>
    const cameraFile = document.getElementById("cameraFile");
    const galleryFile = document.getElementById("galleryFile");
    const anyFile = document.getElementById("anyFile");
    const uploadForm = document.querySelector("form");
    const filename = document.getElementById("filename");
    const attachmentName = document.getElementById("attachmentName");
    function showName(input) {{
      if (input.files && input.files.length) {{
        filename.textContent = "已选择：" + input.files[0].name;
        if (!attachmentName.value.trim()) attachmentName.value = input.files[0].name;
        if (input === cameraFile) galleryFile.value = "";
        if (input === cameraFile) anyFile.value = "";
        if (input === galleryFile) cameraFile.value = "";
        if (input === galleryFile) anyFile.value = "";
        if (input === anyFile) cameraFile.value = "";
        if (input === anyFile) galleryFile.value = "";
      }}
    }}
    cameraFile.addEventListener("change", () => showName(cameraFile));
    galleryFile.addEventListener("change", () => showName(galleryFile));
    anyFile.addEventListener("change", () => showName(anyFile));
    uploadForm.addEventListener("submit", () => {{
      [cameraFile, galleryFile, anyFile].forEach((input) => {{
        if (!input.files || !input.files.length) input.disabled = true;
      }});
    }});
  </script>
</body>
</html>
""")


@app.get("/web/open/{exp_id}", response_class=HTMLResponse)
def web_open_experiment(exp_id: int):
    exp = db_ops.get_experiment(exp_id)
    if not exp:
        raise HTTPException(404, "Experiment not found")
    if os.environ.get("ELN_NATIVE_ONLY") == "1":
        target = f"/run?experiment_id={exp_id}&t={int(time.time())}"
    else:
        target = f"{_web_base_url()}/stepper/{exp_id}?t={int(time.time())}"
    return _html_response(f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>打开实验</title>
  <script>
    window.location.replace("{target}");
  </script>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 40px; color: #222; }}
    a {{ color: #fb8c00; }}
  </style>
</head>
<body>
  <p>正在打开实验：{_html_escape(exp.name)}</p>
  <p><a href="{target}">如果没有自动打开，请点击这里</a></p>
</body>
</html>
""")


@app.get("/web/edit-step/{step_id}", response_class=HTMLResponse)
def web_edit_step_form(step_id: int):
    step = db_ops.get_step(step_id)
    if not step:
        raise HTTPException(404, "Step not found")
    values = step.get_values()
    fields = step.get_fields()
    app_url = _eln_step_url(step.experiment_id, step_id)
    field_html = []
    for field in fields:
        key = _html_escape(field.key)
        label = _html_escape(field.label + (" *" if field.required else ""))
        value = _html_escape(values.get(field.key, field.default) or "")
        if field.type == "dropdown":
            options = []
            current = values.get(field.key, field.default) or ""
            for opt in field.options:
                selected = " selected" if opt == current else ""
                options.append(f'<option value="{_html_escape(opt)}"{selected}>{_html_escape(opt)}</option>')
            control = f'<select name="{key}">{"".join(options)}</select>'
        else:
            control = f'<input name="{key}" type="text" value="{value}" autocomplete="off" />'
        field_html.append(f"""
        <label>
          <span>{label}</span>
          {control}
        </label>
        """)
    notes_value = _html_escape(values.get(STEP_NOTES_KEY, "") or "")

    return _html_response(f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>编辑记录数据 · Step {step.step_index + 1}</title>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #222; }}
    main {{ max-width: 680px; }}
    h1 {{ font-size: 22px; margin-bottom: 6px; }}
    .muted {{ color: #666; font-size: 13px; }}
    form {{ display: grid; gap: 16px; margin-top: 22px; }}
    label {{ display: grid; gap: 6px; }}
    label span {{ color: #555; font-size: 14px; }}
    input, select, textarea {{ font: inherit; border: 1px solid #ddd; border-radius: 8px; padding: 10px 12px; max-width: 640px; }}
    textarea {{ min-height: 140px; font-family: ui-monospace, "SF Mono", Consolas, monospace; }}
    label em {{ color: #888; font-style: normal; font-size: 12px; margin-left: 6px; }}
    button, a.button {{ display: inline-block; width: fit-content; border: 0; border-radius: 8px; background: #fb8c00; color: white; padding: 10px 18px; cursor: pointer; text-decoration: none; font: inherit; }}
    a.secondary {{ background: #f3f3f3; color: #333; }}
    .actions {{ display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-top: 8px; }}
  </style>
</head>
<body>
  <main>
    <div class="actions">
      <a class="button secondary" href="{app_url}">返回 ELN</a>
    </div>
    <h1>编辑记录数据</h1>
    <p><b>{_html_escape(step.title)}</b></p>
    <p class="muted">这里使用浏览器原生输入框，避免 Flet Web 输入时白屏。保存后会回到当前步骤。</p>
    <form method="post">
      {"".join(field_html)}
      <label>
        <span>Markdown 记录 <em>md</em></span>
        <textarea name="{STEP_NOTES_KEY}" placeholder="Markdown 记录；报告中会按 Markdown 渲染">{notes_value}</textarea>
      </label>
      <div class="actions">
        <button type="submit">保存并返回 ELN</button>
        <a class="button secondary" href="{app_url}">取消</a>
      </div>
    </form>
  </main>
</body>
</html>
""")


@app.post("/web/edit-step/{step_id}", response_class=HTMLResponse)
async def web_edit_step_save(step_id: int, request: Request):
    step = db_ops.get_step(step_id)
    if not step:
        raise HTTPException(404, "Step not found")
    form = await request.form()
    values = dict(step.get_values())
    for field in step.get_fields():
        values[field.key] = str(form.get(field.key, ""))
    values[STEP_NOTES_KEY] = str(form.get(STEP_NOTES_KEY, "")).strip()
    db_ops.update_step(step_id, values_json=json.dumps(values, ensure_ascii=False))
    app_url = _eln_step_url(step.experiment_id, step_id)
    return _html_response(f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>记录已保存</title>
  <script>setTimeout(function() {{ window.location.replace("{app_url}"); }}, 500);</script>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 40px; color: #222; }}
    a {{ color: #fb8c00; }}
  </style>
</head>
<body>
  <p>记录已保存，正在返回 ELN。</p>
  <p><a href="{app_url}">如果没有自动返回，请点击这里</a></p>
</body>
</html>
""")


@app.post("/web/upload/{step_id}", response_class=HTMLResponse)
async def web_upload_photo(
    step_id: int,
    file: UploadFile = File(...),
    attachment_name: str = Form(""),
):
    result = await upload_photo(
        step_id=step_id,
        file=file,
        attachment_name=attachment_name,
    )
    step = db_ops.get_step(step_id)
    if step:
        _write_eln_return_target(step.experiment_id, step_id)
    app_url = _eln_step_url(step.experiment_id, step_id) if step else _web_base_url()
    preview_html = _attachment_preview_html(result["path"], result.get("name", "上传文件"))
    return _html_response(f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>上传成功</title>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 40px; color: #222; }}
    img {{ max-width: min(520px, 100%); max-height: 420px; border: 1px solid #ddd; border-radius: 8px; }}
    a {{ color: #fb8c00; }}
    a.button {{ display: inline-block; border-radius: 8px; background: #fb8c00; color: white; padding: 10px 18px; text-decoration: none; margin-right: 10px; }}
    a.secondary {{ background: #f3f3f3; color: #333; }}
  </style>
</head>
<body>
  <h1>上传成功</h1>
  <p>照片/文件已经保存到 ELN。回到实验页面刷新即可看到记录。</p>
  <p>
    <a class="button" href="{app_url}">返回 ELN</a>
    <a class="button secondary" href="/web/upload/{step_id}">继续上传</a>
  </p>
  {preview_html}
</body>
</html>
""")


@app.post("/web/upload/{step_id}/rename")
async def web_rename_attachment(
    step_id: int,
    attachment_path: str = Form(...),
    attachment_name: str = Form(...),
):
    if not db_ops.get_step(step_id):
        raise HTTPException(404, "Step not found")
    try:
        db_ops.rename_attachment(step_id, attachment_path, attachment_name)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return RedirectResponse(f"/web/upload/{step_id}", status_code=303)


def _is_image_attachment(path: str) -> bool:
    return os.path.splitext(path.lower())[1] in {
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg"
    }


def _attachment_preview_html(path: str, label: str) -> str:
    safe_path = _html_escape(path)
    safe_label = _html_escape(label)
    clean_path = str(path).replace("\\", "/").lstrip("/")
    url = f"/photos/{quote(clean_path, safe='/')}"
    if _is_image_attachment(path):
        return (
            f'<a href="{url}" target="_blank" rel="noopener">'
            f'<img src="{url}" alt="{safe_label}" loading="lazy" decoding="async" />'
            "</a>"
            '<div class="attachment-actions">'
            f'<a href="{url}" target="_blank" rel="noopener">打开原图</a>'
            f'<a href="{url}" download>下载</a>'
            "</div>"
        )
    return (
        '<div class="file-attachment">'
        f'<strong>{safe_label}</strong>'
        f'<span>{safe_path}</span>'
        '<div class="attachment-actions">'
        f'<a href="{url}" target="_blank" rel="noopener">打开文件</a>'
        f'<a href="{url}" download>下载文件</a>'
        "</div>"
        "</div>"
    )


def _html_escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render_markdown_html(markdown: str) -> str:
    """Render report Markdown safely for the native browser report page."""
    try:
        from markdown_it import MarkdownIt

        prepared = str(markdown).replace("../photos/", "/photos/")
        return (
            MarkdownIt("commonmark", {"html": False, "linkify": True})
            .enable("table")
            .render(prepared)
        )
    except Exception:
        return f"<pre>{_html_escape(markdown)}</pre>"


def _eln_step_url(experiment_id: int, step_id: int) -> str:
    if os.environ.get("ELN_NATIVE_ONLY") == "1":
        return f"/run?experiment_id={experiment_id}"
    return f"{_web_base_url()}/stepper/{experiment_id}/{step_id}"


def _web_base_url() -> str:
    if os.environ.get("ELN_NATIVE_ONLY") == "1":
        return os.environ.get("ELN_NATIVE_PUBLIC_URL", "").rstrip("/") or "/run"
    configured = "" if os.environ.get("ELN_DYNAMIC_PUBLIC_URL") == "1" else os.environ.get("ELN_WEB_PUBLIC_URL", "").rstrip("/")
    if configured:
        return configured
    try:
        from server.startup import get_local_ip
        return f"http://{get_local_ip()}:8550"
    except Exception:
        return "http://127.0.0.1:8550"


def _write_eln_return_target(experiment_id: int, step_id: int) -> None:
    try:
        import time
        path = os.path.join(os.path.expanduser("~"), "ELN_Data", "web_return.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "route": "stepper",
                    "experiment_id": experiment_id,
                    "step_id": step_id,
                    "created_at": time.time(),
                },
                f,
                ensure_ascii=False,
            )
    except Exception:
        pass


def _redirect_html(url: str, message: str = "正在返回") -> HTMLResponse:
    safe_url = _html_escape(url)
    return _html_response(f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_html_escape(message)}</title>
  <script>window.location.replace("{safe_url}");</script>
</head>
<body>
  <p>{_html_escape(message)}。</p>
  <p><a href="{safe_url}">如果没有自动跳转，请点击这里</a></p>
</body>
</html>
""")


_STORAGE_CSS = """
    main { max-width:1120px; display:grid; gap:14px; }
    .item { background:#fbfaf7; border:1px solid var(--line); border-radius:12px; padding:12px; margin-bottom:10px; box-shadow:none; }
    .item b { font-size:15px; }
    .item button { margin-top:8px; min-height:36px; padding:6px 12px; font-size:14px; }
    .grid-layout { display:grid; grid-template-columns:minmax(260px,360px) 1fr; gap:14px; align-items:start; }
    .register-panel { display:none; }
    .register-panel.open { display:block; }
    .box-grid { display:grid; gap:4px; margin-top:12px; width:max-content; max-width:100%; overflow:auto; }
    .slot { width:44px; height:38px; min-height:0; padding:0; border:1px solid var(--line); border-radius:8px;
            background:#faf9f6; color:#43413d; font-size:12px; font-weight:600; box-shadow:none; }
    .slot.occupied { background:var(--accent-soft); border-color:#f2c48f; color:#9a5a00; }
    .slot.selected { background:var(--green); color:#fff; border-color:var(--green); }
    @media (max-width:760px){ .grid-layout { grid-template-columns:1fr; } .slot { width:38px; } }
"""

_REPORT_CSS = """
    main { max-width:900px; }
    section { margin-bottom:14px; }
    pre { white-space:pre-wrap; word-break:break-word; line-height:1.5; overflow:auto;
          background:#f8f6f1; border:1px solid var(--line); border-radius:10px; padding:10px; }
    code { background:#f3f0ea; padding:2px 5px; border-radius:5px; font-size:.92em; }
    table { width:100%; border-collapse:collapse; margin:12px 0; font-size:14px; }
    th, td { border:1px solid var(--line); padding:8px; text-align:left; vertical-align:top; }
    th { background:#faf8f4; }
    blockquote { border-left:3px solid var(--accent); margin-left:0; padding:3px 12px; color:#6b665e;
                 background:var(--accent-soft); border-radius:0 8px 8px 0; }
    .markdown-body { line-height:1.65; overflow-wrap:anywhere; }
    .markdown-body h1 { font-size:22px; } .markdown-body h2 { font-size:18px; } .markdown-body h3 { font-size:16px; }
    img { max-width:min(100%,640px); border:1px solid var(--line); border-radius:10px; }
    figure { margin:14px 0; }
    figcaption { color:var(--muted); font-size:13px; margin-top:5px; }
    .attachment-actions { display:flex; gap:14px; margin-top:8px; }
    .attachment-actions a { color:var(--accent-strong); font-weight:600; }
    .file-attachment { display:flex; flex-direction:column; gap:6px; padding:14px; border:1px solid var(--line);
                       border-radius:10px; background:#fbfaf7; }
    .file-attachment span { color:var(--muted); overflow-wrap:anywhere; }
    .saved { color:#1d6f3f; font-weight:600; }
    audio { width:100%; max-width:420px; }
"""


@app.get("/run/storage/{exp_id}", response_class=HTMLResponse)
def run_storage_page(exp_id: int, msg: str = Query(""), error: str = Query("")):
    exp = db_ops.get_experiment(exp_id)
    if not exp:
        raise HTTPException(404, "Experiment not found")
    items = db_ops.get_storage_items(exp_id)
    boxes = db_ops.list_boxes()
    boxes_data = [_box_to_dict(b) for b in boxes]
    slots_data = {str(b.id): [_slot_to_dict(s) for s in db_ops.get_box_slots(b.id)] for b in boxes}

    item_cards = []
    for item in items:
        pos = item.position or "未登记"
        item_cards.append(f"""
        <div class="item">
          <div><b>{_html_escape(item.item_label)}</b></div>
          <div class="muted">管型：{_html_escape(item.tube_type or "未填写")} · 位置：{_html_escape(pos)}</div>
          <div class="muted">备注：{_html_escape(item.notes or item.notes_template or "无")}</div>
          <button type="button" onclick='prepareRegister({item.id}, {json.dumps(item.item_label, ensure_ascii=False)})'>登记 / 修改位置</button>
        </div>
        """)

    if not item_cards:
        item_cards.append('<p class="muted">还没有储存物品。可以在下面添加，每行一个。</p>')

    notice = ""
    if msg:
        notice = f'<div class="notice ok">{_html_escape(msg)}</div>'
    elif error:
        notice = f'<div class="notice error">{_html_escape(error)}</div>'

    head = web_ui.page_head(f"储存登记 · {_html_escape(exp.name)}", _STORAGE_CSS)
    return _html_response(f"""
{head}
<body>
  <header class="app-bar">
    <a class="button secondary" href="/run?experiment_id={exp_id}">← 实验</a>
    <h1>储存登记 · {_html_escape(exp.name)}</h1>
    <a class="button secondary" href="/run/report/{exp_id}">报告</a>
  </header>
  <main>
    {notice}
    <section>
      <h2>添加要储存的物品</h2>
      <p class="muted">每行一个物品。推荐格式：样品名 | 管型 | 备注。也可以只写样品名。</p>
      <form method="post" action="/run/storage/{exp_id}/add">
        <textarea name="items" required placeholder="PCR 产物 Colony #1 | 1.5mL EP管 | 需要冻存"></textarea>
        <div class="actions"><button type="submit">添加物品</button></div>
      </form>
    </section>

    <section>
      <h2>Box</h2>
      <form method="post" action="/run/storage/{exp_id}/box/add" class="actions">
        <input name="box_name" placeholder="新 Box 名称" style="max-width:260px" />
        <select name="box_size" style="max-width:140px"><option value="10">10 × 10</option><option value="9">9 × 9</option></select>
        <button type="submit">新建 Box</button>
      </form>
    </section>

    <div class="grid-layout">
      <section>
        <h2>储存物品</h2>
        {"".join(item_cards)}
      </section>

      <section id="registerPanel" class="register-panel">
        <h2 id="registerTitle">登记位置</h2>
        <form method="post" action="/run/storage/{exp_id}/register">
          <input type="hidden" name="item_id" id="itemId" />
          <input type="hidden" name="position" id="position" />
          <label>选择 Box</label>
          <select name="box_id" id="boxSelect" onchange="renderGrid()"></select>
          <div id="boxGrid" class="box-grid"></div>
          <div class="muted" id="positionHint">请选择一个格子</div>
          <label>备注</label>
          <input name="notes" placeholder="可选" />
          <div class="actions">
            <button type="submit">保存位置</button>
            <button class="secondary" type="button" onclick="closeRegister()">取消</button>
          </div>
        </form>
      </section>
    </div>

    <section>
      <h2>结束与报告</h2>
      <p class="muted">补完照片和登记位置后，可以结束实验并进入报告页。以后也可以从历史记录查看。</p>
      <form method="post" action="/run/storage/{exp_id}/finish" onsubmit="return confirm('确认结束实验？')">
        <div class="actions">
          <button class="green" type="submit">结束实验并查看报告</button>
          <a class="button secondary" href="/run/report/{exp_id}">只查看报告</a>
        </div>
      </form>
    </section>
  </main>
  <script>
    const boxes = {json.dumps(boxes_data, ensure_ascii=False)};
    const slotsByBox = {json.dumps(slots_data, ensure_ascii=False)};
    let selectedPosition = "";
    function esc(v) {{ return String(v ?? "").replace(/[&<>"']/g, s => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[s])); }}
    function prepareRegister(itemId, label) {{
      document.getElementById("registerPanel").classList.add("open");
      document.getElementById("itemId").value = itemId;
      document.getElementById("registerTitle").textContent = "登记位置 · " + label;
      const sel = document.getElementById("boxSelect");
      sel.innerHTML = boxes.map(b => `<option value="${{b.id}}">${{esc(b.box_name)}} (${{b.box_size}}×${{b.box_size}})</option>`).join("");
      selectedPosition = "";
      document.getElementById("position").value = "";
      renderGrid();
    }}
    function closeRegister() {{
      document.getElementById("registerPanel").classList.remove("open");
    }}
    function renderGrid() {{
      const boxId = document.getElementById("boxSelect").value;
      const box = boxes.find(b => String(b.id) === String(boxId));
      const grid = document.getElementById("boxGrid");
      if(!box) {{ grid.innerHTML = '<p class="muted">请先新建 Box</p>'; return; }}
      const slots = slotsByBox[String(boxId)] || [];
      const byPos = Object.fromEntries(slots.map(s => [s.position, s]));
      grid.style.gridTemplateColumns = `repeat(${{box.box_size}}, 44px)`;
      grid.innerHTML = "";
      for(let r=0; r<box.box_size; r++) {{
        const row = String.fromCharCode(65 + r);
        for(let c=1; c<=box.box_size; c++) {{
          const pos = row + c;
          const slot = byPos[pos];
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "slot" + (slot ? " occupied" : "") + (pos === selectedPosition ? " selected" : "");
          btn.textContent = pos;
          btn.title = slot ? (slot.sample_name || "已占用") : "空位";
          btn.onclick = () => {{
            if(slot && !confirm(pos + " 已有内容：" + (slot.sample_name || "") + "。确认覆盖这个位置？")) return;
            selectedPosition = pos;
            document.getElementById("position").value = pos;
            document.getElementById("positionHint").textContent = "已选择：" + pos;
            renderGrid();
          }};
          grid.appendChild(btn);
        }}
      }}
    }}
  </script>
{web_ui.TIMER_DOCK_HTML}
</body>
</html>
""")


@app.post("/run/storage/{exp_id}/add", response_class=HTMLResponse)
async def run_storage_add(exp_id: int, request: Request):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    form = await request.form()
    raw = str(form.get("items", "")).strip()
    added = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        label = parts[0] if parts else ""
        if not label:
            continue
        tube = parts[1] if len(parts) > 1 else ""
        notes = parts[2] if len(parts) > 2 else ""
        db_ops.create_storage_item(exp_id, item_label=label, tube_type=tube, notes_template=notes)
        added += 1
    if added <= 0:
        return RedirectResponse(
            f"/run/storage/{exp_id}?error={quote('没有输入可添加的储存物品')}",
            status_code=303,
        )
    return RedirectResponse(
        f"/run/storage/{exp_id}?msg={quote(f'已添加 {added} 个储存物品')}",
        status_code=303,
    )


@app.post("/run/storage/{exp_id}/box/add", response_class=HTMLResponse)
async def run_storage_box_add(exp_id: int, request: Request):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    form = await request.form()
    name = str(form.get("box_name", "")).strip()
    size = int(str(form.get("box_size", "10")) or "10")
    if name:
        db_ops.create_box(name, box_size=9 if size == 9 else 10)
        return RedirectResponse(
            f"/run/storage/{exp_id}?msg={quote('Box 已新建')}",
            status_code=303,
        )
    return RedirectResponse(
        f"/run/storage/{exp_id}?error={quote('请输入 Box 名称')}",
        status_code=303,
    )


@app.post("/run/storage/{exp_id}/register", response_class=HTMLResponse)
async def run_storage_register(exp_id: int, request: Request):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    form = await request.form()
    item_id = int(str(form.get("item_id", "0")) or "0")
    box_id = int(str(form.get("box_id", "0")) or "0")
    position = str(form.get("position", "")).strip().upper()
    if not item_id or not box_id or len(position) < 2:
        return RedirectResponse(
            f"/run/storage/{exp_id}?error={quote('登记信息不完整，请选择 Box 和位置')}",
            status_code=303,
        )
    notes = str(form.get("notes", "")).strip()
    db_ops.register_storage_item(
        item_id=item_id,
        box_id=box_id,
        row_label=position[0],
        col_label=position[1:],
        notes=notes,
        exp_id=exp_id,
    )
    return RedirectResponse(
        f"/run/storage/{exp_id}?msg={quote('位置已登记')}",
        status_code=303,
    )


@app.post("/run/storage/{exp_id}/finish", response_class=HTMLResponse)
async def run_storage_finish(exp_id: int):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    db_ops.update_experiment(exp_id, status="completed")
    return _redirect_html(f"/run/report/{exp_id}", "实验已结束")


@app.get("/run/report/{exp_id}", response_class=HTMLResponse)
def run_report_page(
    exp_id: int,
    request: Request,
    saved: str = Query(""),
    return_to: str = Query("experiment"),
):
    exp = db_ops.get_experiment(exp_id)
    if not exp:
        raise HTTPException(404, "Experiment not found")
    hostname = request.url.hostname or "127.0.0.1"
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    if return_to == "history":
        if os.environ.get("ELN_NATIVE_ONLY") == "1" or request.url.scheme == "https":
            back_url = "/run"
            back_label = "返回实验列表"
        else:
            back_url = f"{request.url.scheme}://{display_host}:8550/history"
            back_label = "返回历史"
    else:
        back_url = f"/run?experiment_id={exp_id}"
        back_label = "返回实验"
    markdown = db_ops.get_report(exp_id)
    report_html = _render_markdown_html(markdown)
    photo_html = []
    for step in db_ops.get_steps(exp_id):
        for item in step.get_attachments():
            path = item["path"]
            label = item["name"]
            photo_html.append(
                "<figure>"
                f"{_attachment_preview_html(path, label)}"
                f"<figcaption>Step {step.step_index + 1} · "
                f"{_html_escape(step.title)} · {_html_escape(label)}</figcaption>"
                "</figure>"
            )
    saved_block = f'<p class="saved">已保存：{_html_escape(saved)}</p>' if saved else ""
    head = web_ui.page_head(f"实验报告 · {_html_escape(exp.name)}", _REPORT_CSS)
    return _html_response(f"""
{head}
<body>
  <header class="app-bar">
    <a class="button secondary" href="{_html_escape(back_url)}">{back_label}</a>
    <h1>实验报告 · {_html_escape(exp.name)}</h1>
    <form method="post" action="/run/report/{exp_id}/save?return_to={quote(return_to)}"><button type="submit">保存报告</button></form>
  </header>
  <main>
    {saved_block}
    <section>
      <h2>附件 / 照片预览</h2>
      {"".join(photo_html) if photo_html else '<p class="muted">暂无附件。</p>'}
    </section>
    <section>
      <h2>实验报告</h2>
      <div class="markdown-body">{report_html}</div>
    </section>
  </main>
{web_ui.TIMER_DOCK_HTML}
</body>
</html>
""")


@app.post("/run/report/{exp_id}/save", response_class=HTMLResponse)
async def run_report_save(exp_id: int, return_to: str = Query("experiment")):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    result = db_ops.save_report(exp_id)
    return _redirect_html(
        f"/run/report/{exp_id}?saved={quote(result['path'])}&return_to={quote(return_to)}",
        "报告已保存",
    )


# ─────────────────────────────────────────────
# Protocols
# ─────────────────────────────────────────────

@app.get("/api/protocols")
def list_protocols():
    protocols = db_ops.list_protocols()
    return [_protocol_to_dict(p) for p in protocols]


@app.post("/api/protocols", status_code=201)
def create_protocol(body: ProtocolCreate):
    try:
        definition = ProtocolDefinition.from_json(body.protocol_json)
    except Exception as exc:
        raise HTTPException(400, f"Invalid protocol_json: {exc}")
    p = db_ops.create_protocol(definition)
    return _protocol_to_dict(p)


@app.get("/api/protocols/{protocol_id}")
def get_protocol(protocol_id: int):
    p = db_ops.get_protocol(protocol_id)
    if not p:
        raise HTTPException(404, "Protocol not found")
    return _protocol_to_dict(p)


@app.put("/api/protocols/{protocol_id}")
def update_protocol(protocol_id: int, body: ProtocolCreate):
    if not db_ops.get_protocol(protocol_id):
        raise HTTPException(404, "Protocol not found")
    try:
        definition = ProtocolDefinition.from_json(body.protocol_json)
    except Exception as exc:
        raise HTTPException(400, f"Invalid protocol_json: {exc}")
    p = db_ops.update_protocol(protocol_id, definition)
    return _protocol_to_dict(p)


@app.delete("/api/protocols/{protocol_id}", status_code=204)
def delete_protocol(protocol_id: int):
    if not db_ops.get_protocol(protocol_id):
        raise HTTPException(404, "Protocol not found")
    db_ops.delete_protocol(protocol_id)


def _protocol_to_dict(p) -> dict:
    return {
        "id": p.id, "name": p.name, "version": p.version, "author": p.author,
        "protocol_json": p.protocol_json,
        "created_at": p.created_at, "updated_at": p.updated_at,
        "use_count": p.use_count, "last_used_at": p.last_used_at,
    }


# ─────────────────────────────────────────────
# Boxes
# ─────────────────────────────────────────────

@app.get("/api/boxes")
def list_boxes():
    boxes = db_ops.list_boxes()
    result = []
    for b in boxes:
        used = db_ops.get_box_slot_count(b.id)
        result.append({**_box_to_dict(b), "used_slots": used,
                        "total_slots": b.box_size * b.box_size})
    return result


@app.post("/api/boxes", status_code=201)
def create_box(body: BoxCreate):
    b = db_ops.create_box(body.box_name, body.box_size, body.notes)
    return _box_to_dict(b)


@app.get("/api/boxes/{box_id}")
def get_box(box_id: int):
    b = db_ops.get_box(box_id)
    if not b:
        raise HTTPException(404, "Box not found")
    used = db_ops.get_box_slot_count(box_id)
    return {**_box_to_dict(b), "used_slots": used, "total_slots": b.box_size * b.box_size}


@app.patch("/api/boxes/{box_id}")
def update_box(box_id: int, body: BoxUpdate):
    if not db_ops.get_box(box_id):
        raise HTTPException(404, "Box not found")
    updates = body.model_dump(exclude_none=True)
    b = db_ops.update_box(box_id, **updates)
    return _box_to_dict(b)


@app.delete("/api/boxes/{box_id}", status_code=204)
def delete_box(box_id: int):
    if not db_ops.get_box(box_id):
        raise HTTPException(404, "Box not found")
    db_ops.delete_box(box_id)


@app.get("/api/boxes/{box_id}/slots")
def get_slots(box_id: int):
    if not db_ops.get_box(box_id):
        raise HTTPException(404, "Box not found")
    slots = db_ops.get_box_slots(box_id)
    return [_slot_to_dict(s) for s in slots]


@app.put("/api/boxes/{box_id}/slots/{position}")
def upsert_slot(box_id: int, position: str, body: SlotUpdate):
    """position format: 'A1', 'B3', etc."""
    if not db_ops.get_box(box_id):
        raise HTTPException(404, "Box not found")
    if len(position) < 2:
        raise HTTPException(400, "Invalid position format (e.g. 'A1')")
    row_label = position[0].upper()
    col_label = position[1:]
    slot = db_ops.upsert_slot(
        box_id=box_id, row_label=row_label, col_label=col_label,
        sample_name=body.sample_name, notes=body.notes,
        experiment_id=body.experiment_id, step_id=body.step_id,
    )
    return _slot_to_dict(slot)


@app.delete("/api/boxes/{box_id}/slots/{position}", status_code=204)
def clear_slot(box_id: int, position: str):
    if not db_ops.get_box(box_id):
        raise HTTPException(404, "Box not found")
    row_label = position[0].upper()
    col_label = position[1:]
    db_ops.clear_slot(box_id, row_label, col_label)


def _box_to_dict(b) -> dict:
    return {"id": b.id, "box_name": b.box_name, "box_size": b.box_size,
            "created_at": b.created_at, "notes": b.notes}


def _slot_to_dict(s) -> dict:
    return {
        "id": s.id, "box_id": s.box_id,
        "row_label": s.row_label, "col_label": s.col_label,
        "position": s.position,
        "sample_name": s.sample_name, "notes": s.notes,
        "experiment_id": s.experiment_id, "step_id": s.step_id,
        "created_at": s.created_at,
    }


# ─────────────────────────────────────────────
# Storage items
# ─────────────────────────────────────────────

@app.get("/api/experiments/{exp_id}/storage")
def get_storage(exp_id: int):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    items = db_ops.get_storage_items(exp_id)
    return [_storage_to_dict(i) for i in items]


@app.post("/api/experiments/{exp_id}/storage")
def create_storage(exp_id: int, body: StorageCreate):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    if not body.item_label.strip():
        raise HTTPException(400, "item_label is required")
    item = db_ops.create_storage_item(
        experiment_id=exp_id,
        item_label=body.item_label.strip(),
        tube_type=body.tube_type.strip(),
        notes_template=body.notes_template.strip(),
        default_box=body.default_box.strip(),
    )
    return _storage_to_dict(item)


@app.post("/api/experiments/{exp_id}/storage/register")
def register_storage(exp_id: int, body: StorageRegister):
    if not db_ops.get_experiment(exp_id):
        raise HTTPException(404, "Experiment not found")
    item = db_ops.register_storage_item(
        item_id=body.item_id, box_id=body.box_id,
        row_label=body.row_label, col_label=body.col_label,
        notes=body.notes,
    )
    if not item:
        raise HTTPException(404, "Storage item not found")
    # Also write to box_slots
    db_ops.upsert_slot(
        box_id=body.box_id, row_label=body.row_label, col_label=body.col_label,
        sample_name=item.item_label, notes=body.notes,
        experiment_id=exp_id,
    )
    return _storage_to_dict(item)


def _storage_to_dict(i) -> dict:
    return {
        "id": i.id, "experiment_id": i.experiment_id,
        "item_key": i.item_key, "item_label": i.item_label,
        "tube_type": i.tube_type, "notes_template": i.notes_template,
        "default_box": i.default_box,
        "box_id": i.box_id, "row_label": i.row_label, "col_label": i.col_label,
        "position": i.position, "is_registered": i.is_registered,
        "notes": i.notes, "registered_at": i.registered_at,
    }


# ─────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────

@app.get("/api/experiments/{exp_id}/report")
def get_report(exp_id: int):
    exp = db_ops.get_experiment(exp_id)
    if not exp:
        raise HTTPException(404, "Experiment not found")
    steps = db_ops.get_steps(exp_id)
    storage_items = db_ops.get_storage_items(exp_id)
    boxes = {b.id: b for b in db_ops.list_boxes()}
    md = generate_report(exp, steps, storage_items, boxes,
                         db_ops.list_timer_events(exp_id),
                         db_ops.list_voice_notes(exp_id))
    return {"experiment_id": exp_id, "markdown": md}


@app.post("/api/experiments/{exp_id}/report/save")
def save_report(exp_id: int):
    try:
        return db_ops.save_report(exp_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
