from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from app.config import SECRET_KEY, API_TOKENS
import hashlib
import hmac
import os
import secrets
import time


def _make_token(username: str) -> str:
    """Simple HMAC-like token: base64(username:timestamp:signature)"""
    ts = str(int(time.time()))
    sig = hashlib.sha256(f"{SECRET_KEY}:{username}:{ts}".encode()).hexdigest()[:32]
    return f"{username}:{ts}:{sig}"


def _verify_token(token: str, max_age: int = 86400 * 7) -> bool:
    """Verify token is valid and not expired."""
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return False
        username, ts, sig = parts
        if time.time() - int(ts) > max_age:
            return False
        expected = hashlib.sha256(f"{SECRET_KEY}:{username}:{ts}".encode()).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected):
            return False
        # 检查黑名单
        from app.database import check_token_blacklist
        return check_token_blacklist(token)
    except (ValueError, IndexError):
        return False


def create_login_token(username: str) -> str:
    return _make_token(username)


def get_current_user(request: Request) -> str | None:
    """Return username if logged in, else None."""
    token = request.cookies.get("token")
    if token and _verify_token(token):
        return token.split(":")[0]
    return None


def require_admin(request: Request) -> str:
    """Raise 401 if not logged in."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user


def require_admin_redirect(request: Request) -> RedirectResponse | None:
    """Redirect to login if not logged in (for page routes)."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/admin/login", status_code=302)
    return None


# ── CSRF Token ──────────────────────────────────────────

def generate_csrf_token(max_age: int = 3600) -> str:
    """生成带签名的 CSRF token，有效期 max_age 秒。"""
    ts = str(int(time.time()))
    nonce = os.urandom(8).hex()
    sig = hmac.new(
        SECRET_KEY.encode(), f"{ts}:{nonce}".encode(), hashlib.sha256
    ).hexdigest()[:32]
    return f"{ts}:{nonce}:{sig}"


def verify_csrf_token(token: str, max_age: int = 3600) -> bool:
    """验证 CSRF token 签名和有效期。"""
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return False
        ts, nonce, sig = parts
        if time.time() - int(ts) > max_age:
            return False
        expected = hmac.new(
            SECRET_KEY.encode(), f"{ts}:{nonce}".encode(), hashlib.sha256
        ).hexdigest()[:32]
        return hmac.compare_digest(sig, expected)
    except (ValueError, IndexError):
        return False


def validate_csrf(request: Request, token: str | None) -> None:
    """校验 CSRF token，失败抛 403。"""
    if not token or not verify_csrf_token(token):
        raise HTTPException(status_code=403, detail="CSRF token 无效或已过期")


# ── API Token ───────────────────────────────────────────

API_TOKEN_PREFIX = "rs_"


def hash_api_token(token: str) -> str:
    """API Token 只存哈希，明文不落库。"""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_api_token() -> str:
    """生成新 API Token，明文仅创建时返回一次。"""
    return API_TOKEN_PREFIX + secrets.token_hex(16)


def verify_api_token(token: str) -> dict | None:
    """校验 API Token，返回 {"user", "token_id"}，无效返回 None。

    依次检查环境变量 Token（API_TOKENS）与数据库 Token。
    """
    if not token:
        return None
    token_hash = hash_api_token(token)

    # 1. 环境变量配置的 Token
    for name, expected in API_TOKENS.items():
        if hmac.compare_digest(token_hash, hash_api_token(expected)):
            return {"user": f"api:{name}", "token_id": None}

    # 2. 数据库管理的 Token
    from app.database import find_api_token, touch_api_token
    row = find_api_token(token_hash)
    if row:
        touch_api_token(row["id"])
        return {"user": f"api:{row['name']}", "token_id": row["id"]}
    return None


def get_api_user(request: Request) -> str | None:
    """从请求头提取 API Token 并校验，返回身份标识。

    支持 Authorization: Bearer <token> 或 X-API-Token: <token>。
    """
    auth = request.headers.get("authorization", "")
    token = ""
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    elif request.headers.get("x-api-token"):
        token = request.headers["x-api-token"].strip()
    if not token:
        return None
    info = verify_api_token(token)
    return info["user"] if info else None


def require_api_user(request: Request) -> str:
    """API 端点鉴权：Bearer Token 或登录会话均可，失败抛 401。"""
    api_user = get_api_user(request)
    if api_user:
        return api_user
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user
