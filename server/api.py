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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db.database as db_ops
from db.models import ProtocolDefinition
from server import web_ui
from server.page_templates import _CAPTURE_BODY, _INBOX_BODY, _RUNNER_BODY
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


_OVERLAY_JS_PATH = os.path.join(os.path.dirname(__file__), "openview_overlay.js")
try:
    _OVERLAY_VER = hashlib.sha256(open(_OVERLAY_JS_PATH, "rb").read()).hexdigest()[:10]
except Exception:
    _OVERLAY_VER = "0"
_OVERLAY_TAG = f'<script src="/openview/overlay.js?v={_OVERLAY_VER}" defer></script>'


@app.get("/static/base.css")
def static_base_css():
    return Response(content=web_ui.BASE_CSS, media_type="text/css",
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


# Prerender the bottom-nav destinations when the user hovers a tab, so switching
# tabs activates an already-loaded page instantly. Ignored by browsers that don't
# support the Speculation Rules API (they just navigate normally).
_SPECRULES_TAG = (
    '<script type="speculationrules">'
    '{"prerender":[{"where":{"or":['
    '{"href_matches":"/capture"},{"href_matches":"/run"},'
    '{"href_matches":"/history"},{"href_matches":"/more"},'
    '{"href_matches":"/inbox"}]},"eagerness":"moderate"}]}'
    '</script>'
)


def _html_response(content: str, **kwargs) -> HTMLResponse:
    """Return localized HTML for the native web pages, with the comment overlay
    and nav prerender hints injected."""
    html = localize_html(content)
    if "</body>" in html and "/openview/overlay.js" not in html:
        html = html.replace("</body>", _OVERLAY_TAG + "\n" + _SPECRULES_TAG + "\n</body>", 1)
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
            "/static/base.css",
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
# _OVERLAY_JS_PATH / _OVERLAY_VER are defined near the top of this module.


@app.get("/openview/overlay.js")
def openview_overlay_js():
    try:
        with open(_OVERLAY_JS_PATH, "r", encoding="utf-8") as f:
            js = f.read()
    except Exception:
        js = "/* overlay unavailable */"
    # Cached forever; the ?v=<hash> in the injected tag busts it when it changes.
    return Response(content=js, media_type="application/javascript",
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


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
    .file-chip .file-main { min-width:0; flex:1; }
    .file-chip .file-state { margin-top:2px; font-size:11.5px; color:var(--faint); white-space:nowrap; }
    .file-chip .bar { height:3px; border-radius:999px; background:#ebe5db; overflow:hidden; margin-top:4px; }
    .file-chip .bar span { display:block; height:100%; width:0%; background:var(--clay); }
    .file-chip.error { border-color:#d9a89a; background:#fff6f2; }
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
    .pending-text { white-space:pre-wrap; cursor:text; border-radius:6px; padding:1px 3px; margin:-1px -3px; }
    .pending-text:hover { background:rgba(0,0,0,.03); }
    .pending-text .ph-text { color:var(--faint); }
    .pending-inline { width:100%; min-height:92px; font-size:14px; background:#fff; }
    .pending-edit-row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-top:8px; }
    .pending-edit-row button { min-height:36px; padding:6px 12px; font-size:13.5px; }
"""

# _CAPTURE_BODY moved to server/page_templates.py


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
    .ai-panel textarea#aiPrompt { width:100%; min-height:220px; background:var(--inset); border:1px solid var(--line);
      border-radius:10px; padding:12px; font-size:12.5px; line-height:1.55; resize:vertical; }
    .entry .efiles { display:flex; flex-direction:column; gap:5px; margin:6px 0; }
    .entry .efile { display:inline-flex; align-items:center; gap:5px; font-size:13px; color:var(--clay-ink); overflow-wrap:anywhere; }
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

# _INBOX_BODY moved to server/page_templates.py


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
    <div class="rm">#${{esc(x.id)}} · ${{date}} · 步骤 ${{x.completed_steps}}/${{x.total_steps}}</div>
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
    .board-alarms { display:none; flex-wrap:wrap; gap:8px; margin:0 0 12px; }
    .board-alarm { display:inline-flex; align-items:center; gap:6px; font-size:13px; padding:5px 12px;
      border-radius:999px; background:#fdf1d6; color:#8a6d1e; font-variant-numeric:tabular-nums; }
    .board-alarm.over { background:#a63a24; color:#fff; }
    .board-alarm b { font-weight:700; }
    .board-alarm .ba-label { font-weight:500; opacity:.85; }
    .board-alarm svg { width:14px; height:14px; stroke:currentColor; fill:none; stroke-width:2; }

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
    .md-preview.empty { display:none; }
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
    .pr-btns { display:flex; gap:8px; }
    .pr-btns .button, .pr-btns button { flex:1 1 0; min-width:0; height:42px; min-height:42px;
      padding:6px 4px; font-size:14px; white-space:nowrap; box-sizing:border-box; margin:0; }
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
    .mic-btn { min-height:38px; padding:6px 14px; margin-top:14px; }
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

# _RUNNER_BODY moved to server/page_templates.py


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
    attachments: Optional[list[dict]] = None


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
        if _is_image_ext(ext):
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


def _is_image_ext(ext: str) -> bool:
    return str(ext or "").lower() in {
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
        ".tif", ".tiff", ".heic", ".heif", ".svg",
    }


def _file_url_for_rel(rel_path: str) -> str:
    return "/photos/" + str(rel_path).replace("\\", "/").lstrip("/")


def _windows_clipboard_file_paths() -> list[str]:
    """Return Explorer's copied-file list without exposing it to the client."""
    if os.name != "nt":
        return []
    import ctypes
    import time

    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = ctypes.c_bool
    user32.GetClipboardData.argtypes = [ctypes.c_uint]
    user32.GetClipboardData.restype = ctypes.c_void_p
    shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint]
    shell32.DragQueryFileW.restype = ctypes.c_uint

    opened = False
    for _ in range(8):
        if user32.OpenClipboard(None):
            opened = True
            break
        time.sleep(0.025)
    if not opened:
        return []
    try:
        handle = user32.GetClipboardData(15)  # CF_HDROP
        if not handle:
            return []
        count = min(int(shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)), 32)
        paths: list[str] = []
        for index in range(count):
            length = int(shell32.DragQueryFileW(handle, index, None, 0))
            if length <= 0:
                continue
            buf = ctypes.create_unicode_buffer(length + 1)
            shell32.DragQueryFileW(handle, index, buf, length + 1)
            paths.append(buf.value)
        return paths
    finally:
        user32.CloseClipboard()


async def _save_upload_stream(file: UploadFile, filepath: str) -> int:
    total = 0
    with open(filepath, "wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            total += len(chunk)
    return total


def _assert_inbox_rel_path(rel_path: str) -> str:
    clean = str(rel_path or "").replace("\\", "/").lstrip("/")
    if not clean.startswith("inbox/") or "/../" in f"/{clean}/" or clean.endswith("/"):
        raise HTTPException(400, "附件路径无效")
    return clean


def _attach_staged_files(entry_id: int, attachments: Optional[list[dict]]) -> dict:
    updated = db_ops.get_inbox_entry(entry_id)
    for item in attachments or []:
        if not isinstance(item, dict):
            continue
        rel = _assert_inbox_rel_path(str(item.get("rel_path") or ""))
        if not rel.startswith("inbox/_staged/"):
            raise HTTPException(400, "只能附加预上传文件")
        src = os.path.join(db_ops.get_photos_dir(), rel.replace("/", os.sep))
        if not os.path.isfile(src) or os.path.getsize(src) <= 0:
            raise HTTPException(400, f"预上传文件不存在或为空：{os.path.basename(src)}")

        sub_dir = os.path.join(db_ops.get_inbox_dir(), str(entry_id))
        os.makedirs(sub_dir, exist_ok=True)
        original = _safe_upload_name(item.get("name") or os.path.basename(src), "attachment")
        stem, ext = os.path.splitext(original)
        if not ext:
            ext = os.path.splitext(src)[1]
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        dest_name = f"{stem}_{ts}{ext}"
        dest = os.path.join(sub_dir, dest_name)
        os.replace(src, dest)
        updated = db_ops.add_inbox_image(entry_id, f"inbox/{entry_id}/{dest_name}")
    return updated or db_ops.get_inbox_entry(entry_id)


_STAGED_MAX_AGE_SECONDS = 6 * 3600


def _prune_staged(max_age_seconds: int = _STAGED_MAX_AGE_SECONDS) -> dict:
    """Delete stale files from the inbox staging area (inbox/_staged + _chunks).
    Staged files are moved out on finalize, so anything left past the age cutoff
    is an abandoned or incomplete upload. Returns {"removed", "bytes"}."""
    staged_dir = os.path.join(db_ops.get_inbox_dir(), "_staged")
    if not os.path.isdir(staged_dir):
        return {"removed": 0, "bytes": 0}
    now = time.time()
    removed = 0
    freed = 0
    for root, _dirs, files in os.walk(staged_dir):
        for name in files:
            p = os.path.join(root, name)
            try:
                st = os.stat(p)
                if now - st.st_mtime >= max_age_seconds:
                    freed += st.st_size
                    os.remove(p)
                    removed += 1
            except OSError:
                pass
    if removed:
        print(f"[staged] pruned {removed} stale file(s), freed {freed} bytes")
    return {"removed": removed, "bytes": freed}


@app.on_event("startup")
def _startup_prune_staged():
    try:
        _prune_staged()
    except Exception as exc:
        print(f"[staged] startup prune failed: {exc}")


def _attach_inbox_files_to_step(entry: dict, step_id: int) -> int:
    step = db_ops.get_step(step_id)
    if not step:
        return 0
    existing = {str(item.get("path") or "") for item in step.get_attachments()}
    added = 0
    for i, rel in enumerate(entry.get("image_paths", []) or [], 1):
        rel = str(rel or "").replace("\\", "/").lstrip("/")
        if not rel or rel in existing:
            continue
        name = os.path.basename(rel)
        ext = os.path.splitext(name.lower())[1]
        label = f"速记图 {i}" if _is_image_ext(ext) else (name or f"速记附件 {i}")
        db_ops.add_photo_to_step(step_id, rel, label)
        existing.add(rel)
        added += 1
    return added


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
    if body.attachments:
        entry = _attach_staged_files(entry["id"], body.attachments)
    return _inbox_to_dict(entry)


@app.post("/api/inbox/staged-media", status_code=201)
async def upload_inbox_staged_media(
    file: UploadFile = File(...),
    kind: str = Form("file"),
):
    _prune_staged()
    staged_dir = os.path.join(db_ops.get_inbox_dir(), "_staged")
    os.makedirs(staged_dir, exist_ok=True)
    ext = os.path.splitext(file.filename or "")[1] or (".jpg" if kind == "image" else ".bin")
    safe = _safe_upload_name(file.filename or f"attachment{ext}", f"attachment{ext}")
    token = uuid.uuid4().hex
    filename = f"{token}_{safe}"
    filepath = os.path.join(staged_dir, filename)
    size = await _save_upload_stream(file, filepath)
    if size <= 0:
        try:
            os.remove(filepath)
        except OSError:
            pass
        raise HTTPException(400, "文件为空")
    rel_path = f"inbox/_staged/{filename}"
    return {
        "token": token,
        "rel_path": rel_path,
        "name": safe,
        "size": size,
        "kind": "image" if kind == "image" or _is_image_ext(ext) else "file",
        "url": _file_url_for_rel(rel_path),
    }


@app.post("/api/inbox/stage-local-clipboard", status_code=201)
def stage_local_clipboard_files(request: Request, body: dict):
    host = str(request.headers.get("host") or "").lower()
    if not (
        host == "localhost" or host.startswith("localhost:")
        or host == "127.0.0.1" or host.startswith("127.0.0.1:")
        or host == "[::1]" or host.startswith("[::1]:")
    ):
        raise HTTPException(403, "本机快速登记只能从本机地址使用")

    requested = body.get("files") if isinstance(body, dict) else None
    if not isinstance(requested, list) or len(requested) > 32:
        raise HTTPException(400, "文件列表无效")

    clipboard_paths = _windows_clipboard_file_paths()
    available: list[tuple[str, int]] = []
    for path in clipboard_paths:
        try:
            if os.path.isfile(path):
                available.append((path, os.path.getsize(path)))
        except OSError:
            continue

    staged_dir = os.path.join(db_ops.get_inbox_dir(), "_staged")
    os.makedirs(staged_dir, exist_ok=True)
    used: set[str] = set()
    items: list[dict] = []
    for raw in requested:
        result: dict = {"staged": None}
        if not isinstance(raw, dict):
            items.append(result)
            continue
        safe = _safe_upload_name(raw.get("name") or "attachment.bin", "attachment.bin")
        try:
            expected_size = int(raw.get("size") or 0)
        except (TypeError, ValueError):
            expected_size = 0
        if expected_size <= 32 * 1024 * 1024:
            items.append(result)
            continue

        source = next((
            path for path, size in available
            if path not in used
            and size == expected_size
            and os.path.basename(path).casefold() == safe.casefold()
        ), None)
        if not source:
            items.append(result)
            continue

        token = uuid.uuid4().hex
        final_name = f"{token}_{safe}"
        filepath = os.path.join(staged_dir, final_name)
        try:
            os.link(source, filepath)
        except OSError:
            items.append(result)
            continue

        used.add(source)
        ext = os.path.splitext(safe)[1]
        kind = str(raw.get("kind") or "file")
        rel_path = f"inbox/_staged/{final_name}"
        result["staged"] = {
            "token": token,
            "rel_path": rel_path,
            "name": safe,
            "size": expected_size,
            "kind": "image" if kind == "image" or _is_image_ext(ext) else "file",
            "url": _file_url_for_rel(rel_path),
            "method": "hardlink",
        }
        items.append(result)
    return {"items": items}


@app.post("/api/inbox/staged-chunk", status_code=201)
async def upload_inbox_staged_chunk(
    upload_id: str = Form(...),
    index: int = Form(...),
    total: int = Form(...),
    offset: int = Form(...),
    total_size: int = Form(...),
    filename: str = Form("attachment.bin"),
    kind: str = Form("file"),
    chunk: UploadFile = File(...),
):
    token = re.sub(r"[^a-fA-F0-9]", "", str(upload_id or ""))[:64]
    if len(token) < 12:
        raise HTTPException(400, "upload_id 无效")
    if index < 0 or total <= 0 or index >= total or offset < 0 or total_size <= 0:
        raise HTTPException(400, "分片参数无效")

    staged_dir = os.path.join(db_ops.get_inbox_dir(), "_staged")
    chunk_dir = os.path.join(staged_dir, "_chunks")
    os.makedirs(chunk_dir, exist_ok=True)
    safe = _safe_upload_name(filename, "attachment.bin")
    ext = os.path.splitext(safe)[1] or ".bin"
    part_path = os.path.join(chunk_dir, f"{token}.part")
    meta_path = os.path.join(chunk_dir, f"{token}.json")

    if index == 0:
        _prune_staged()
        for p in (part_path, meta_path):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"filename": safe, "kind": kind, "total": total, "total_size": total_size}, f)
    elif not os.path.exists(part_path):
        raise HTTPException(409, "缺少前序分片，请重新上传")

    current_size = os.path.getsize(part_path) if os.path.exists(part_path) else 0
    if current_size != offset:
        raise HTTPException(409, f"分片偏移不匹配：已有 {current_size}，收到 {offset}")

    written = 0
    with open(part_path, "ab") as out:
        while True:
            data = await chunk.read(1024 * 1024)
            if not data:
                break
            out.write(data)
            written += len(data)
    if written <= 0:
        raise HTTPException(400, "分片为空")

    final_size = os.path.getsize(part_path)
    if index < total - 1:
        return {"done": False, "received": final_size}
    if final_size != total_size:
        raise HTTPException(409, f"文件大小不匹配：已有 {final_size}，应为 {total_size}")

    final_name = f"{token}_{safe}"
    final_path = os.path.join(staged_dir, final_name)
    os.replace(part_path, final_path)
    try:
        os.remove(meta_path)
    except OSError:
        pass
    rel_path = f"inbox/_staged/{final_name}"
    return {
        "done": True,
        "token": token,
        "rel_path": rel_path,
        "name": safe,
        "size": final_size,
        "kind": "image" if kind == "image" or _is_image_ext(ext) else "file",
        "url": _file_url_for_rel(rel_path),
    }


@app.post("/api/inbox/staged-chunk-raw", status_code=201)
async def upload_inbox_staged_chunk_raw(
    request: Request,
    upload_id: str = Query(...),
    index: int = Query(...),
    total: int = Query(...),
    offset: int = Query(...),
    total_size: int = Query(...),
    filename: str = Query("attachment.bin"),
    kind: str = Query("file"),
):
    token = re.sub(r"[^a-fA-F0-9]", "", str(upload_id or ""))[:64]
    if len(token) < 12:
        raise HTTPException(400, "upload_id 无效")
    if index < 0 or total <= 0 or index >= total or offset < 0 or total_size <= 0:
        raise HTTPException(400, "分片参数无效")

    staged_dir = os.path.join(db_ops.get_inbox_dir(), "_staged")
    chunk_dir = os.path.join(staged_dir, "_chunks")
    os.makedirs(chunk_dir, exist_ok=True)
    safe = _safe_upload_name(filename, "attachment.bin")
    ext = os.path.splitext(safe)[1] or ".bin"
    part_path = os.path.join(chunk_dir, f"{token}.part")
    meta_path = os.path.join(chunk_dir, f"{token}.json")

    if index == 0:
        _prune_staged()
        for p in (part_path, meta_path):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"filename": safe, "kind": kind, "total": total, "total_size": total_size}, f)
    elif not os.path.exists(part_path):
        raise HTTPException(409, "缺少前序分片，请重新上传")

    current_size = os.path.getsize(part_path) if os.path.exists(part_path) else 0
    if current_size != offset:
        raise HTTPException(409, f"分片偏移不匹配：已有 {current_size}，收到 {offset}")

    written = 0
    with open(part_path, "ab") as out:
        async for data in request.stream():
            if not data:
                continue
            out.write(data)
            written += len(data)
    if written <= 0:
        raise HTTPException(400, "分片为空")

    final_size = os.path.getsize(part_path)
    if index < total - 1:
        return {"done": False, "received": final_size}
    if final_size != total_size:
        raise HTTPException(409, f"文件大小不匹配：已有 {final_size}，应为 {total_size}")

    final_name = f"{token}_{safe}"
    final_path = os.path.join(staged_dir, final_name)
    os.replace(part_path, final_path)
    try:
        os.remove(meta_path)
    except OSError:
        pass
    rel_path = f"inbox/_staged/{final_name}"
    return {
        "done": True,
        "token": token,
        "rel_path": rel_path,
        "name": safe,
        "size": final_size,
        "kind": "image" if kind == "image" or _is_image_ext(ext) else "file",
        "url": _file_url_for_rel(rel_path),
    }


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

    # attach captured images/files to the step
    if body.attach_images:
        _attach_inbox_files_to_step(entry, step_id)

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
    attached_files = 0
    if body.step_id is not None:
        attached_files = _attach_inbox_files_to_step(entry, body.step_id)
    db_ops.update_inbox_entry(
        entry_id, status="filed", proposal=record,
        filed_experiment_id=body.experiment_id, filed_step_id=body.step_id,
        filed_at=db_ops._now(),
    )
    return {"ok": True, "audio_deleted": audio_deleted, "attached_files": attached_files}


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


_RENDERABLE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
_IMMUTABLE_CACHE = {"Cache-Control": "public, max-age=31536000, immutable"}


def _thumbs_dir() -> str:
    d = os.path.join(_photos_dir(), ".thumbs")
    os.makedirs(d, exist_ok=True)
    return d


def _resolve_photo(path: str) -> str:
    """Resolve a photos-relative path safely to an absolute file under photos."""
    base = os.path.realpath(_photos_dir())
    clean = str(path).replace("\\", "/").lstrip("/")
    full = os.path.realpath(os.path.join(base, clean.replace("/", os.sep)))
    if full != base and not full.startswith(base + os.sep):
        raise HTTPException(403, "非法路径")
    if not os.path.isfile(full):
        raise HTTPException(404, "文件不存在")
    return full


def _render_cached(full: str, max_px: int, fmt: str) -> str:
    """Downscale `full` to <=max_px and cache the result on disk; return the
    cache path. Keyed by source path + mtime + size + params, so an edited
    original produces a fresh render. fmt is 'JPEG' or 'PNG'."""
    from PIL import Image
    st = os.stat(full)
    ext = ".jpg" if fmt == "JPEG" else ".png"
    key = hashlib.sha256(
        f"{full}|{st.st_mtime_ns}|{st.st_size}|{max_px}|{fmt}".encode("utf-8")
    ).hexdigest()[:24]
    out = os.path.join(_thumbs_dir(), key + ext)
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    with Image.open(full) as im:
        try:
            im.seek(0)  # first page of multi-page TIFF
        except Exception:
            pass
        if fmt == "JPEG":
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
        elif im.mode not in ("RGB", "RGBA", "L"):
            im = im.convert("RGB")
        im.thumbnail((max_px, max_px))
        tmp = out + ".tmp"
        im.save(tmp, format=fmt, **({"quality": 82} if fmt == "JPEG" else {}))
        os.replace(tmp, out)
    return out


@app.get("/api/thumb")
def image_thumb(path: str = Query(...), w: int = Query(360, ge=48, le=1200)):
    """Small cached JPEG thumbnail for list/grid views (any image incl. TIFF)."""
    full = _resolve_photo(path)
    if os.path.splitext(full)[1].lower() not in _RENDERABLE_EXT:
        raise HTTPException(415, "不支持缩略图")
    try:
        out = _render_cached(full, w, "JPEG")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(415, f"无法生成缩略图：{exc}")
    return FileResponse(out, media_type="image/jpeg", headers=_IMMUTABLE_CACHE)


@app.get("/api/preview")
def image_preview(path: str = Query(...), max: int = Query(1600, ge=64, le=4096)):
    """Render formats browsers can't show in <img> (TIFF, BMP) as a PNG for a
    full-size preview. Result is cached on disk so big TIFFs decode only once."""
    try:
        import PIL  # noqa: F401
    except Exception:
        raise HTTPException(500, "Pillow 未安装，无法预览此格式")
    full = _resolve_photo(path)
    try:
        out = _render_cached(full, max, "PNG")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(415, f"无法渲染此图片：{exc}")
    return FileResponse(out, media_type="image/png", headers=_IMMUTABLE_CACHE)


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
        return f"/run?experiment_id={experiment_id}&step_id={step_id}"
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
