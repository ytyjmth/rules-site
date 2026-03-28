"""Admin routes — login, CRUD for rules."""
from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import os
import re
import time
import yaml
from collections import defaultdict

from app.config import ADMIN_USERNAME, ADMIN_PASSWORD, RULES_DIR, SITE_TITLE, SITE_NAME, SITE_VERSION, SITE_ICP, SITE_AI_MODEL
from app.database import get_db, sync_rules
from app.auth import (
    create_login_token, require_admin_redirect, get_current_user,
    generate_csrf_token, validate_csrf,
)

router = APIRouter(prefix="/admin")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.autoescape = True
templates.env.globals["csrf_token"] = generate_csrf_token

# ── 登录速率限制 ─────────────────────────────────────────
LOGIN_RATE_LIMIT_WINDOW = 300   # 5 分钟
LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 10
_login_attempts: dict[str, list[float]] = defaultdict(list)


def _check_login_rate_limit(ip: str) -> bool:
    now = time.time()
    _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < LOGIN_RATE_LIMIT_WINDOW]
    return len(_login_attempts[ip]) < LOGIN_RATE_LIMIT_MAX_ATTEMPTS


def _record_login_failure(ip: str):
    _login_attempts[ip].append(time.time())


# ── 文件上传限制 ─────────────────────────────────────────
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
FILENAME_PATTERN = re.compile(r'^[a-zA-Z0-9_\-.]+\.ya?ml$')


def _validate_filename(filename: str) -> bool:
    return bool(FILENAME_PATTERN.match(filename))


def _is_valid_yaml(data: bytes) -> bool:
    """检查内容是否为合法 YAML。"""
    try:
        yaml.safe_load(data.decode("utf-8"))
        return True
    except (yaml.YAMLError, UnicodeDecodeError):
        return False


def _escape_like(s: str) -> str:
    """转义 LIKE 通配符，防止用户输入 % _ 被当作模式匹配。"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _is_secure_connection(request: Request) -> bool:
    """判断客户端是否通过 HTTPS 连接（兼容反向代理）。"""
    if request.url.scheme == "https":
        return True
    forwarded = request.headers.get("x-forwarded-proto", "")
    return forwarded.split(",")[0].strip().lower() == "https"


# ── 辅助 ────────────────────────────────────────────────
def _site_context(request: Request, **extra) -> dict:
    """构建模板公共上下文。"""
    ctx = {
        "request": request,
        "rules": extra.pop("rules", []),
        "error": extra.pop("error", None),
        "q": extra.pop("q", ""),
        "page": extra.pop("page", 1),
        "total_pages": extra.pop("total_pages", 1),
        "total": extra.pop("total", 0),
        "site_title": SITE_TITLE,
        "site_name": SITE_NAME,
        "site_version": SITE_VERSION,
        "site_icp": SITE_ICP,
        "site_ai_model": SITE_AI_MODEL,
    }
    ctx.update(extra)
    return ctx


def _error_response(request: Request, error_msg: str, status_code: int = 400):
    return templates.TemplateResponse(
        "admin.html",
        _site_context(request=request, error=error_msg),
        status_code=status_code,
    )


# ── Login ──────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/admin", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), csrf_token: str = Form(None)):
    validate_csrf(request, csrf_token)
    client_ip = request.client.host if request.client else "unknown"

    if not _check_login_rate_limit(client_ip):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "登录尝试过于频繁，请 5 分钟后再试"},
            status_code=429,
        )

    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        resp = RedirectResponse(url="/admin", status_code=302)
        is_secure = _is_secure_connection(request)
        resp.set_cookie(
            "token", create_login_token(username),
            httponly=True, samesite="lax", secure=is_secure, max_age=86400 * 7,
        )
        _login_attempts.pop(client_ip, None)
        return resp

    _record_login_failure(client_ip)
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": "用户名或密码错误"}
    )


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(None)):
    validate_csrf(request, csrf_token)
    resp = RedirectResponse(url="/", status_code=302)
    resp.delete_cookie("token")
    return resp


# ── Dashboard ──────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, q: str = "", page: int = 1):
    redir = require_admin_redirect(request)
    if redir:
        return redir

    per_page = 5
    page = max(1, page)

    with get_db() as conn:
        if q:
            escaped_q = _escape_like(q)
            keyword = f"%{escaped_q}%"
            total = conn.execute(
                "SELECT COUNT(*) FROM rules WHERE filename LIKE ? ESCAPE '\\' OR display_name LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\'",
                (keyword, keyword, keyword),
            ).fetchone()[0]
            offset = (page - 1) * per_page
            rows = conn.execute(
                "SELECT * FROM rules WHERE filename LIKE ? ESCAPE '\\' OR display_name LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' ORDER BY sort_order ASC, filename ASC LIMIT ? OFFSET ?",
                (keyword, keyword, keyword, per_page, offset),
            ).fetchall()
        else:
            total = conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0]
            offset = (page - 1) * per_page
            rows = conn.execute(
                "SELECT * FROM rules ORDER BY sort_order ASC, filename ASC LIMIT ? OFFSET ?",
                (per_page, offset),
            ).fetchall()
    rules = [dict(r) for r in rows]

    for r in rules:
        fp = os.path.join(RULES_DIR, r["filename"])
        r["size"] = os.path.getsize(fp) if os.path.exists(fp) else 0

    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)

    return templates.TemplateResponse(
        "admin.html", _site_context(request=request, rules=rules, q=q, page=page, total_pages=total_pages, total=total)
    )


# ── Create ─────────────────────────────────────────────

@router.post("/rules")
async def create_rule(
    request: Request,
    filename: str = Form(...),
    display_name: str = Form(...),
    description: str = Form(""),
    content: str = Form(""),
    file: UploadFile = File(None),
    csrf_token: str = Form(None),
):
    redir = require_admin_redirect(request)
    if redir:
        return redir

    validate_csrf(request, csrf_token)

    if not filename.endswith((".yaml", ".yml")):
        filename += ".yaml"

    if not _validate_filename(filename):
        return _error_response(request, f"文件名 '{filename}' 不合法，只允许字母、数字、下划线、连字符和点")

    if file and file.filename:
        data = await file.read()
        if len(data) > MAX_FILE_SIZE:
            return _error_response(request, f"文件大小超过限制（最大 {MAX_FILE_SIZE // 1024 // 1024}MB）")
        if not _is_valid_yaml(data):
            return _error_response(request, "上传文件内容不是合法的 YAML 格式")
    else:
        data = content.encode("utf-8")
        if content.strip() and not _is_valid_yaml(data):
            return _error_response(request, "规则内容不是合法的 YAML 格式")

    filepath = os.path.join(RULES_DIR, filename)
    if os.path.exists(filepath):
        return _error_response(request, f"文件 {filename} 已存在")

    with open(filepath, "wb") as f:
        f.write(data)

    with get_db() as conn:
        conn.execute(
            "INSERT INTO rules (filename, display_name, description) VALUES (?, ?, ?)",
            (filename, display_name, description),
        )

    return RedirectResponse(url="/admin", status_code=303)


# ── Update ─────────────────────────────────────────────

@router.post("/rules/{rule_id}")
async def update_rule(
    rule_id: int,
    request: Request,
    display_name: str = Form(None),
    description: str = Form(None),
    content: str = Form(None),
    file: UploadFile = File(None),
    csrf_token: str = Form(None),
):
    redir = require_admin_redirect(request)
    if redir:
        return redir

    validate_csrf(request, csrf_token)

    with get_db() as conn:
        rule = conn.execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone()
        if not rule:
            raise HTTPException(404, "Rule not found")

    # 先更新文件内容
    filepath = os.path.join(RULES_DIR, rule["filename"])
    if file and file.filename:
        data = await file.read()
        if len(data) > MAX_FILE_SIZE:
            return _error_response(
                request,
                f"文件大小超过限制（最大 {MAX_FILE_SIZE // 1024 // 1024}MB）",
            )
        if not _is_valid_yaml(data):
            return _error_response(request, "上传文件内容不是合法的 YAML 格式")
        with open(filepath, "wb") as f:
            f.write(data)
    elif content is not None:
        if content.strip() and not _is_valid_yaml(content.encode("utf-8")):
            return _error_response(request, "规则内容不是合法的 YAML 格式")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

    # 文件写入成功后再更新数据库
    with get_db() as conn:
        field_map = {"display_name": display_name, "description": description}
        updates = [f"{col}=?" for col, val in field_map.items() if val is not None]
        params = [val for val in field_map.values() if val is not None]

        if content is not None or (file and file.filename):
            updates.append("updated_at=CURRENT_TIMESTAMP")

        if updates:
            params.append(rule_id)
            conn.execute(
                f"UPDATE rules SET {', '.join(updates)} WHERE id=?", params
            )

    return RedirectResponse(url="/admin", status_code=303)


# ── Delete ─────────────────────────────────────────────

@router.post("/rules/{rule_id}/delete")
async def delete_rule(rule_id: int, request: Request, csrf_token: str = Form(None)):
    redir = require_admin_redirect(request)
    if redir:
        return redir

    validate_csrf(request, csrf_token)

    with get_db() as conn:
        rule = conn.execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone()
        if not rule:
            raise HTTPException(404, "Rule not found")

        conn.execute("DELETE FROM rules WHERE id=?", (rule_id,))

    # DB 删除成功后再删文件，失败也不影响一致性
    filepath = os.path.join(RULES_DIR, rule["filename"])
    if os.path.exists(filepath):
        os.remove(filepath)

    return RedirectResponse(url="/admin", status_code=303)


@router.get("/rules/{rule_id}/content")
def get_rule_content(rule_id: int, request: Request):
    """返回规则文件的当前内容，供前端编辑器加载。"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "Unauthorized")

    with get_db() as conn:
        rule = conn.execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone()
        if not rule:
            raise HTTPException(404, "Rule not found")

    filepath = os.path.join(RULES_DIR, rule["filename"])
    if not os.path.exists(filepath):
        return {"content": ""}

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    return {"content": content, "filename": rule["filename"]}


# ── Sync ────────────────────────────────────────────────

@router.post("/sync")
async def sync_rules_endpoint(request: Request, csrf_token: str = Form(None)):
    """扫描 data/rules/ 目录，同步文件到数据库。"""
    redir = require_admin_redirect(request)
    if redir:
        return redir

    validate_csrf(request, csrf_token)
    result = sync_rules()
    return RedirectResponse(url="/admin", status_code=303)
