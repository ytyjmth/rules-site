"""Public-facing routes."""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates
from functools import lru_cache
from app.auth import generate_csrf_token
from app.database import search_rules
from app.config import RULES_DIR
from app.utils import build_template_context
import os

router = APIRouter()

# 启用 autoescape 防止 XSS，使用绝对路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.autoescape = True
templates.env.globals["csrf_token"] = generate_csrf_token


@lru_cache(maxsize=64)
def _read_preview(filepath: str, mtime: float) -> tuple[str, int]:
    """读取文件预览，带 LRU 缓存。mtime 用于缓存失效。"""
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()
    return "".join(lines[:50]), len(lines)


@router.get("/")
def index(request: Request, q: str = "", page: int = 1):
    rules, total, total_pages = search_rules(q, page, per_page=5)

    for rule in rules:
        filepath = os.path.join(RULES_DIR, rule["filename"])
        if os.path.exists(filepath):
            mtime = os.path.getmtime(filepath)
            rule["preview"], rule["line_count"] = _read_preview(filepath, mtime)
        else:
            rule["preview"] = ""
            rule["line_count"] = 0

    return templates.TemplateResponse(
        "index.html",
        build_template_context(
            request=request,
            rules=rules,
            q=q,
            page=page,
            total_pages=total_pages,
            total=total,
        ),
    )


@router.get("/rules/{filename:path}")
def serve_rule(filename: str):
    """Serve YAML file for direct subscription use."""
    # 安全校验：拒绝路径穿越
    safe_name = os.path.basename(filename)
    if safe_name != filename or not safe_name:
        raise HTTPException(400, "Invalid filename")
    filepath = os.path.join(RULES_DIR, safe_name)
    if not os.path.exists(filepath):
        raise HTTPException(404, "Not found")
    return FileResponse(
        filepath,
        media_type="text/yaml",
        filename=safe_name,
    )
