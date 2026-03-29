"""公共工具函数。"""
from fastapi import Request
from app.config import SITE_TITLE, SITE_NAME, SITE_VERSION, SITE_ICP, SITE_AI_MODEL


def escape_like(s: str) -> str:
    """转义 LIKE 通配符，防止用户输入 % _ 被当作模式匹配。"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_template_context(request: Request, **extra) -> dict:
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
