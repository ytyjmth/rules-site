"""Admin routes — login, CRUD for rules."""
from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import os
import re
import time
from collections import defaultdict

from app.config import ADMIN_USERNAME, ADMIN_PASSWORD, RULES_DIR, SITE_TITLE, SITE_NAME, SITE_VERSION, SITE_ICP, SITE_AI_MODEL
from app.database import get_db
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


# ── 辅助 ────────────────────────────────────────────────
def _site_context(**extra) -> dict:
    """构建模板公共上下文。"""
    ctx = {
        "request": extra.pop("request"),
        "rules": extra.pop("rules", []),
        "error": extra.pop("error", None),
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
        is_https = request.url.scheme == "https"
        resp.set_cookie(
            "token", create_login_token(username),
            httponly=True, samesite="lax", secure=is_https, max_age=86400 * 7,
        )
        _login_attempts.pop(client_ip, None)
        return resp

    _record_login_failure(client_ip)
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": "用户名或密码错误"}
    )


@router.get("/logout")
def logout():
    resp = RedirectResponse(url="/", status_code=302)
    resp.delete_cookie("token")
    return resp


# ── Dashboard ──────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    redir = require_admin_redirect(request)
    if redir:
        return redir

    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM rules ORDER BY sort_order ASC, filename ASC"
        ).fetchall()
    rules = [dict(r) for r in rows]

    for r in rules:
        fp = os.path.join(RULES_DIR, r["filename"])
        r["size"] = os.path.getsize(fp) if os.path.exists(fp) else 0

    return templates.TemplateResponse(
        "admin.html", _site_context(request=request, rules=rules)
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
    else:
        data = content.encode("utf-8")

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

        # 动态构建更新字段
        field_map = {"display_name": display_name, "description": description}
        updates = [f"{col}=?" for col, val in field_map.items() if val is not None]
        params = [val for val in field_map.values() if val is not None]

        if updates:
            updates.append("updated_at=CURRENT_TIMESTAMP")
            params.append(rule_id)
            conn.execute(
                f"UPDATE rules SET {', '.join(updates)} WHERE id=?", params
            )

    # 更新文件内容
    filepath = os.path.join(RULES_DIR, rule["filename"])
    if file and file.filename:
        data = await file.read()
        if len(data) > MAX_FILE_SIZE:
            return _error_response(
                request,
                f"文件大小超过限制（最大 {MAX_FILE_SIZE // 1024 // 1024}MB）",
            )
        with open(filepath, "wb") as f:
            f.write(data)
    elif content is not None and content.strip():
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

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

        filepath = os.path.join(RULES_DIR, rule["filename"])
        if os.path.exists(filepath):
            os.remove(filepath)

        conn.execute("DELETE FROM rules WHERE id=?", (rule_id,))

    return RedirectResponse(url="/admin", status_code=303)
