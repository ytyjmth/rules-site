from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from app.config import SECRET_KEY
import hashlib
import hmac
import os
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
        # 使用恒定时间比较，防止时序攻击
        return hmac.compare_digest(sig, expected)
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


def require_admin_redirect(request: Request):
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


def validate_csrf(request: Request, token: str | None):
    """校验 CSRF token，失败抛 403。"""
    if not token or not verify_csrf_token(token):
        raise HTTPException(status_code=403, detail="CSRF token 无效或已过期")
