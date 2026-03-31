"""Admin routes — login, CRUD for rules."""
from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import os
import re
import shutil
import glob
import yaml
from datetime import datetime

from app.config import (
    ADMIN_USERNAME, ADMIN_PASSWORD, RULES_DIR, BACKUP_DIR, MAX_BACKUPS_PER_FILE,
)
from app.database import (
    get_db, sync_rules, search_rules,
    check_login_rate_limit, record_login_failure, clear_login_attempts,
    log_audit, get_audit_logs,
)
from app.auth import (
    create_login_token, require_admin_redirect, get_current_user,
    generate_csrf_token, validate_csrf,
)
from app.utils import build_template_context

router = APIRouter(prefix="/admin")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.autoescape = True
templates.env.globals["csrf_token"] = generate_csrf_token


async def _optional_file(file: UploadFile = File(default=None)):
    """表单中未选文件时，file 字段可能是空字符串而非 UploadFile。"""
    if isinstance(file, str):
        return None
    if file and file.filename:
        return file
    return None


# ── 文件上传限制 ─────────────────────────────────────────
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_YAML_PARSE_SIZE = 1 * 1024 * 1024  # YAML 解析最大 1MB，防止 DoS
FILENAME_PATTERN = re.compile(r'^[a-zA-Z0-9_\-.]+\.ya?ml$')


def _validate_filename(filename: str) -> bool:
    return bool(FILENAME_PATTERN.match(filename))


def _is_valid_yaml(data: bytes) -> bool:
    """检查内容是否为合法 YAML。限制解析大小防止 DoS。"""
    try:
        decoded = data.decode("utf-8")
        # 限制 YAML 解析大小，防止深度嵌套攻击
        if len(decoded) > MAX_YAML_PARSE_SIZE:
            return False
        yaml.safe_load(decoded)
        return True
    except (yaml.YAMLError, UnicodeDecodeError):
        return False


def _is_secure_connection(request: Request) -> bool:
    """判断客户端是否通过 HTTPS 连接（兼容反向代理）。"""
    if request.url.scheme == "https":
        return True
    forwarded = request.headers.get("x-forwarded-proto", "")
    return forwarded.split(",")[0].strip().lower() == "https"


# ── 辅助 ────────────────────────────────────────────────
def _error_response(request: Request, error_msg: str, status_code: int = 400):
    """返回错误响应，补全分页数据。"""
    rules, total, total_pages = search_rules(page=1, per_page=5)
    for r in rules:
        fp = os.path.join(RULES_DIR, r["filename"])
        r["size"] = os.path.getsize(fp) if os.path.exists(fp) else 0
    return templates.TemplateResponse(
        "admin.html",
        build_template_context(
            request=request,
            rules=rules,
            error=error_msg,
            total=total,
            total_pages=total_pages,
        ),
        status_code=status_code,
    )


def _backup_file(filepath: str):
    """编辑前备份当前版本，保留最近 N 个备份。"""
    if not os.path.exists(filepath):
        return

    filename = os.path.basename(filepath)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"{filename}.{ts}.bak")

    try:
        shutil.copy2(filepath, backup_path)
    except OSError:
        pass  # 备份失败不影响主流程

    # 清理旧备份，保留最近 N 个
    pattern = os.path.join(BACKUP_DIR, f"{filename}.*.bak")
    backups = sorted(glob.glob(pattern), reverse=True)
    for old_backup in backups[MAX_BACKUPS_PER_FILE:]:
        try:
            os.remove(old_backup)
        except OSError:
            pass


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

    if not check_login_rate_limit(client_ip):
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
        clear_login_attempts(client_ip)
        log_audit("login", username, ip=client_ip)
        return resp

    record_login_failure(client_ip)
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": "用户名或密码错误"}
    )


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(None)):
    validate_csrf(request, csrf_token)
    username = get_current_user(request) or "unknown"
    client_ip = request.client.host if request.client else "unknown"
    log_audit("logout", username, ip=client_ip)
    resp = RedirectResponse(url="/", status_code=302)
    resp.delete_cookie("token")
    return resp


# ── Dashboard ──────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, q: str = "", page: int = 1):
    redir = require_admin_redirect(request)
    if redir:
        return redir

    rules, total, total_pages = search_rules(q, page, per_page=5)
    for r in rules:
        fp = os.path.join(RULES_DIR, r["filename"])
        r["size"] = os.path.getsize(fp) if os.path.exists(fp) else 0

    return templates.TemplateResponse(
        "admin.html", build_template_context(request=request, rules=rules, q=q, page=page, total_pages=total_pages, total=total)
    )


# ── Create ─────────────────────────────────────────────

@router.post("/rules")
async def create_rule(
    request: Request,
    filename: str = Form(...),
    display_name: str = Form(...),
    description: str = Form(""),
    content: str = Form(""),
    file: UploadFile = Depends(_optional_file),
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

    # 校验空内容
    if not file or not file.filename:
        if not content.strip():
            return _error_response(request, "规则内容不能为空，请填写 YAML 内容或上传文件")

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

    # 生成带注释头的文件内容
    def _wrap_with_meta(content_bytes: bytes, title: str, desc: str) -> bytes:
        header = f"# title: {title}\n# description: {desc}\n\n"
        return header.encode("utf-8") + content_bytes

    file_data = _wrap_with_meta(data, display_name, description)

    filepath = os.path.join(RULES_DIR, filename)
    if os.path.exists(filepath):
        return _error_response(request, f"文件 {filename} 已存在")

    # 原子性：先写文件，再写 DB。文件写失败无副作用，DB 写失败回滚文件。
    with open(filepath, "wb") as f:
        f.write(file_data)

    username = get_current_user(request) or "unknown"
    client_ip = request.client.host if request.client else "unknown"
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO rules (filename, display_name, description) VALUES (?, ?, ?)",
                (filename, display_name, description),
            )
    except Exception:
        # DB 写入失败，回滚已写入的文件
        if os.path.exists(filepath):
            os.remove(filepath)
        raise

    try:
        log_audit("create", username, filename, client_ip, f"display_name={display_name}")
    except Exception:
        pass  # 审计失败不影响创建操作
    return RedirectResponse(url="/admin", status_code=303)


# ── Update ─────────────────────────────────────────────

@router.post("/rules/{rule_id}")
async def update_rule(
    rule_id: int,
    request: Request,
    display_name: str = Form(None),
    description: str = Form(None),
    content: str = Form(None),
    file: UploadFile = Depends(_optional_file),
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

    # 确定最终的 display_name 和 description（新值或保留旧值）
    final_display_name = display_name if display_name is not None else rule["display_name"]
    final_description = description if description is not None else rule["description"]

    # 先更新文件内容
    filepath = os.path.join(RULES_DIR, rule["filename"])

    # 编辑前备份当前版本
    _backup_file(filepath)

    def _strip_meta_comments(text: str) -> str:
        """移除文件头部的 # title: / # description: 注释，返回纯内容。"""
        lines = text.split("\n")
        result_lines = []
        skipped_header = True
        for line in lines:
            if skipped_header and re.match(r"^\s*#\s*(title|description)\s*[:：]", line, re.IGNORECASE):
                continue
            elif skipped_header and re.match(r"^\s*#\s*$", line):
                # 空注释行也跳过
                continue
            else:
                skipped_header = False
                result_lines.append(line)
        # 去掉开头多余空行
        while result_lines and not result_lines[0].strip():
            result_lines.pop(0)
        return "\n".join(result_lines)

    def _wrap_with_meta(content: str, title: str, desc: str) -> str:
        header = f"# title: {title}\n# description: {desc}\n\n"
        return header + content

    if file and file.filename:
        data = await file.read()
        if len(data) > MAX_FILE_SIZE:
            return _error_response(
                request,
                f"文件大小超过限制（最大 {MAX_FILE_SIZE // 1024 // 1024}MB）",
            )
        raw = data.decode("utf-8")
        raw = _strip_meta_comments(raw)
        if raw.strip() and not _is_valid_yaml(raw.encode("utf-8")):
            return _error_response(request, "上传文件内容不是合法的 YAML 格式")
        wrapped = _wrap_with_meta(raw, final_display_name, final_description)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(wrapped)
    elif content is not None and content.strip():
        if not _is_valid_yaml(content.encode("utf-8")):
            return _error_response(request, "规则内容不是合法的 YAML 格式")
        content = _strip_meta_comments(content)
        wrapped = _wrap_with_meta(content, final_display_name, final_description)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(wrapped)
    elif display_name is not None or description is not None:
        # 没改内容，只改了元数据 → 更新文件头部注释
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                raw = f.read()
            raw = _strip_meta_comments(raw)
            wrapped = _wrap_with_meta(raw, final_display_name, final_description)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(wrapped)

    # 文件写入成功后再更新数据库，失败时从备份恢复
    username = get_current_user(request) or "unknown"
    client_ip = request.client.host if request.client else "unknown"
    try:
        with get_db() as conn:
            field_map = {"display_name": display_name, "description": description}
            updates = [f"{col}=?" for col, val in field_map.items() if val is not None]
            params = [val for val in field_map.values() if val is not None]

            # 所有更新都带上时间戳
            updates.append("updated_at=CURRENT_TIMESTAMP")

            params.append(rule_id)
            conn.execute(
                f"UPDATE rules SET {', '.join(updates)} WHERE id=?", params
            )
    except Exception:
        # DB 失败，从最近的备份恢复文件
        pattern = os.path.join(BACKUP_DIR, f"{os.path.basename(filepath)}.*.bak")
        backups = sorted(glob.glob(pattern), reverse=True)
        if backups:
            shutil.copy2(backups[0], filepath)
        raise
    try:
        log_audit("update", username, rule["filename"], client_ip)
    except Exception:
        pass
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

    # 1. 备份（失败不影响流程）
    _backup_file(filepath)

    # 2. 删除文件
    if os.path.exists(filepath):
        os.remove(filepath)

    # 3. 删除数据库记录（文件已删，DB 删除失败时 sync_rules 可清理残留）
    with get_db() as conn:
        conn.execute("DELETE FROM rules WHERE id=?", (rule_id,))

    username = get_current_user(request) or "unknown"
    client_ip = request.client.host if request.client else "unknown"
    log_audit("delete", username, rule["filename"], client_ip)
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
    username = get_current_user(request) or "unknown"
    client_ip = request.client.host if request.client else "unknown"
    result = sync_rules()
    log_audit("sync", username, ip=client_ip, details=str(result))
    return RedirectResponse(url="/admin", status_code=303)


# ── Audit Logs ─────────────────────────────────────────

@router.get("/logs", response_class=HTMLResponse)
def audit_logs(request: Request, page: int = 1):
    """审计日志页面。"""
    redir = require_admin_redirect(request)
    if redir:
        return redir

    logs, total, total_pages = get_audit_logs(page, per_page=20)
    return templates.TemplateResponse(
        "logs.html",
        build_template_context(
            request=request,
            logs=logs,
            page=page,
            total_pages=total_pages,
            total=total,
        ),
    )


# ── API 接口 ────────────────────────────────────────────

@router.get("/api/rules")
def api_list_rules(request: Request, q: str = "", page: int = 1, per_page: int = 20):
    """API: 获取规则列表。"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "Unauthorized")

    per_page = min(100, max(1, per_page))
    rules, total, total_pages = search_rules(q, page, per_page)
    return {"rules": rules, "total": total, "page": page, "total_pages": total_pages}


@router.get("/api/rules/{rule_id}")
def api_get_rule(rule_id: int, request: Request):
    """API: 获取单个规则详情。"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "Unauthorized")

    with get_db() as conn:
        rule = conn.execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone()
        if not rule:
            raise HTTPException(404, "Rule not found")

    filepath = os.path.join(RULES_DIR, rule["filename"])
    content = ""
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

    return {**dict(rule), "content": content}
