"""REST API v1 — 供 agent / CLI 编程调用。

认证方式（二选一）：
  1. Authorization: Bearer <token>  或  X-API-Token: <token>
     Token 来自 API_TOKENS 环境变量，或管理面板创建的数据库 Token。
  2. 管理面板登录会话（Cookie）。
     通过 Cookie 的写操作仍需 CSRF；Bearer 请求天然免疫 CSRF，无需校验。
"""
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
import os
import glob
import shutil

from app.auth import (
    get_current_user, get_api_user,
    validate_csrf, generate_api_token, hash_api_token,
)
from app.database import (
    get_db, search_rules, sync_rules, log_audit, get_audit_logs,
    create_api_token, list_api_tokens, revoke_api_token,
)
from app.config import RULES_DIR, BACKUP_DIR
from app.rules_ops import (
    validate_filename, is_valid_yaml, backup_file,
    strip_meta_comments, wrap_with_meta, MAX_FILE_SIZE,
)

router = APIRouter()


# ── 请求模型 ────────────────────────────────────────────

class RuleCreateRequest(BaseModel):
    filename: str
    display_name: str
    description: str = ""
    content: str


class RuleUpdateRequest(BaseModel):
    display_name: str | None = None
    description: str | None = None
    content: str | None = None


class TokenCreateRequest(BaseModel):
    name: str


# ── 鉴权辅助 ────────────────────────────────────────────

def _authenticate(request: Request) -> str:
    """返回操作者身份（api:xxx 或登录用户名），未认证抛 401。"""
    api_user = get_api_user(request)
    if api_user:
        return api_user
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "Unauthorized")
    return user


def _authenticate_write(request: Request, csrf_token: str | None = None) -> str:
    """写操作鉴权：Bearer Token 直接放行；Cookie 会话需 CSRF。"""
    api_user = get_api_user(request)
    if api_user:
        return api_user
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "Unauthorized")
    validate_csrf(request, csrf_token or request.headers.get("x-csrf-token"))
    return user


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ── 连接测试 ────────────────────────────────────────────

@router.get("/api/v1/me")
def api_me(request: Request):
    """验证 Token / 会话有效性，返回操作者身份。"""
    api_user = get_api_user(request)
    if api_user:
        return {"user": api_user, "authenticated_via": "api_token"}
    user = get_current_user(request)
    if user:
        return {"user": user, "authenticated_via": "session"}
    raise HTTPException(401, "Unauthorized")


# ── 规则 CRUD ───────────────────────────────────────────

@router.get("/api/v1/rules")
def api_list_rules(request: Request, q: str = "", page: int = 1, per_page: int = 20):
    """获取规则列表（分页，支持关键字搜索）。"""
    _authenticate(request)
    per_page = min(100, max(1, per_page))
    rules, total, total_pages = search_rules(q, page, per_page)
    return {"rules": rules, "total": total, "page": page, "total_pages": total_pages}


@router.get("/api/v1/rules/{rule_id}")
def api_get_rule(rule_id: int, request: Request):
    """获取单个规则详情（含文件内容）。"""
    _authenticate(request)

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


@router.post("/api/v1/rules", status_code=201)
async def api_create_rule(rule: RuleCreateRequest, request: Request):
    """创建规则。内容须为合法 YAML。"""
    username = _authenticate_write(request)
    client_ip = _client_ip(request)

    filename = rule.filename.strip()
    if not filename.endswith((".yaml", ".yml")):
        filename += ".yaml"

    if not validate_filename(filename):
        raise HTTPException(
            400, f"文件名 '{filename}' 不合法，只允许字母、数字、下划线、连字符和点"
        )

    data = rule.content.encode("utf-8")
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(400, f"文件大小超过限制（最大 {MAX_FILE_SIZE // 1024 // 1024}MB）")
    if not rule.content.strip():
        raise HTTPException(400, "规则内容不能为空")
    if not is_valid_yaml(data):
        raise HTTPException(400, "规则内容不是合法的 YAML 格式")

    file_data = wrap_with_meta(rule.content, rule.display_name, rule.description).encode("utf-8")
    filepath = os.path.join(RULES_DIR, filename)

    if os.path.exists(filepath):
        raise HTTPException(409, f"文件 {filename} 已存在")

    # 原子性：先写文件，再写 DB。DB 写失败回滚文件。
    with open(filepath, "wb") as f:
        f.write(file_data)

    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO rules (filename, display_name, description) VALUES (?, ?, ?)",
                (filename, rule.display_name, rule.description),
            )
    except Exception:
        if os.path.exists(filepath):
            os.remove(filepath)
        raise

    try:
        log_audit("create", username, filename, client_ip, f"display_name={rule.display_name}")
    except Exception:
        pass

    with get_db() as conn:
        row = conn.execute("SELECT * FROM rules WHERE filename=?", (filename,)).fetchone()
    return {**dict(row), "content": file_data.decode("utf-8")}


@router.put("/api/v1/rules/{rule_id}")
async def api_update_rule(rule_id: int, rule: RuleUpdateRequest, request: Request):
    """更新规则。只传需要修改的字段，未传字段保留原值。"""
    username = _authenticate_write(request)
    client_ip = _client_ip(request)

    with get_db() as conn:
        row = conn.execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Rule not found")
    rule_dict = dict(row)

    final_display_name = rule.display_name if rule.display_name is not None else rule_dict["display_name"]
    final_description = rule.description if rule.description is not None else rule_dict["description"]

    filepath = os.path.join(RULES_DIR, rule_dict["filename"])
    backup_file(filepath)

    if rule.content is not None:
        if len(rule.content.encode("utf-8")) > MAX_FILE_SIZE:
            raise HTTPException(400, f"文件大小超过限制（最大 {MAX_FILE_SIZE // 1024 // 1024}MB）")
        if not rule.content.strip():
            raise HTTPException(400, "规则内容不能为空")
        if not is_valid_yaml(rule.content.encode("utf-8")):
            raise HTTPException(400, "规则内容不是合法的 YAML 格式")

    if rule.content is not None and rule.content.strip():
        raw = strip_meta_comments(rule.content)
        wrapped = wrap_with_meta(raw, final_display_name, final_description)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(wrapped)
    elif rule.display_name is not None or rule.description is not None:
        # 没改内容，只改了元数据 → 更新文件头部注释
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                raw = f.read()
            raw = strip_meta_comments(raw)
            wrapped = wrap_with_meta(raw, final_display_name, final_description)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(wrapped)

    # 文件写入成功后再更新数据库，失败时从备份恢复
    try:
        with get_db() as conn:
            field_map = {
                "display_name": rule.display_name,
                "description": rule.description,
            }
            updates = [f"{col}=?" for col, val in field_map.items() if val is not None]
            params = [val for val in field_map.values() if val is not None]
            updates.append("updated_at=CURRENT_TIMESTAMP")
            params.append(rule_id)
            conn.execute(f"UPDATE rules SET {', '.join(updates)} WHERE id=?", params)
    except Exception:
        pattern = os.path.join(BACKUP_DIR, f"{os.path.basename(filepath)}.*.bak")
        backups = sorted(glob.glob(pattern), reverse=True)
        if backups:
            shutil.copy2(backups[0], filepath)
        raise

    try:
        log_audit("update", username, rule_dict["filename"], client_ip)
    except Exception:
        pass

    with get_db() as conn:
        row = conn.execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone()
    content = ""
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    return {**dict(row), "content": content}


@router.delete("/api/v1/rules/{rule_id}")
async def api_delete_rule(rule_id: int, request: Request):
    """删除规则（删除前自动备份）。"""
    username = _authenticate_write(request)
    client_ip = _client_ip(request)

    with get_db() as conn:
        rule = conn.execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone()
        if not rule:
            raise HTTPException(404, "Rule not found")

    filepath = os.path.join(RULES_DIR, rule["filename"])
    backup_file(filepath)

    if os.path.exists(filepath):
        os.remove(filepath)

    with get_db() as conn:
        conn.execute("DELETE FROM rules WHERE id=?", (rule_id,))

    try:
        log_audit("delete", username, rule["filename"], client_ip)
    except Exception:
        pass
    return {"deleted": True, "id": rule_id, "filename": rule["filename"]}


# ── 目录同步 ────────────────────────────────────────────

@router.post("/api/v1/sync")
async def api_sync(request: Request):
    """扫描 data/rules/ 目录，同步文件到数据库。"""
    username = _authenticate_write(request)
    client_ip = _client_ip(request)
    result = sync_rules()
    try:
        log_audit("sync", username, ip=client_ip, details=str(result))
    except Exception:
        pass
    return result


# ── 审计日志 ────────────────────────────────────────────

@router.get("/api/v1/logs")
def api_logs(request: Request, page: int = 1, per_page: int = 20):
    """获取审计日志（分页）。"""
    _authenticate(request)
    per_page = min(100, max(1, per_page))
    logs, total, total_pages = get_audit_logs(page, per_page)
    return {"logs": logs, "total": total, "page": page, "total_pages": total_pages}


# ── API Token 管理 ─────────────────────────────────────
# 路径挂在 /admin/api/tokens 下，供管理面板与已持有 Token 的 agent 管理。

@router.get("/admin/api/tokens")
def api_list_tokens(request: Request):
    """列出 API Token（脱敏，不含明文与哈希）。"""
    _authenticate(request)
    return {"tokens": list_api_tokens()}


@router.post("/admin/api/tokens", status_code=201)
async def api_create_token(body: TokenCreateRequest, request: Request):
    """创建 API Token。明文仅在响应中返回一次，服务端只保存哈希。"""
    username = _authenticate_write(request)
    client_ip = _client_ip(request)

    name = body.name.strip()
    if not name or len(name) > 64:
        raise HTTPException(400, "Token 名称不能为空且不超过 64 字符")

    token = generate_api_token()
    try:
        token_id = create_api_token(name, hash_api_token(token))
    except Exception:
        raise HTTPException(500, "Token 创建失败")

    try:
        log_audit("token_create", username, ip=client_ip, details=f"name={name}")
    except Exception:
        pass
    return {"id": token_id, "name": name, "token": token}


@router.delete("/admin/api/tokens/{token_id}")
async def api_revoke_token(token_id: int, request: Request):
    """撤销 API Token（标记 revoked，不再可用）。"""
    username = _authenticate_write(request)
    client_ip = _client_ip(request)

    if not revoke_api_token(token_id):
        raise HTTPException(404, "Token not found")

    try:
        log_audit("token_revoke", username, ip=client_ip, details=f"id={token_id}")
    except Exception:
        pass
    return {"revoked": True, "id": token_id}
