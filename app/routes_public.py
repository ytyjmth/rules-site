"""Public-facing routes."""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates
from app.auth import generate_csrf_token
from app.database import get_db
from app.config import RULES_DIR, SITE_TITLE, SITE_NAME, SITE_VERSION, SITE_ICP, SITE_AI_MODEL
import os

router = APIRouter()

# 启用 autoescape 防止 XSS，使用绝对路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.autoescape = True
templates.env.globals["csrf_token"] = generate_csrf_token


@router.get("/")
def index(request: Request):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM rules ORDER BY sort_order ASC, filename ASC"
        ).fetchall()
    rules = [dict(r) for r in rows]

    # 预览内容（截取前 50 行）
    for rule in rules:
        filepath = os.path.join(RULES_DIR, rule["filename"])
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                lines = f.readlines()
            rule["preview"] = "".join(lines[:50])
            rule["line_count"] = len(lines)
        else:
            rule["preview"] = ""
            rule["line_count"] = 0

    return templates.TemplateResponse("index.html", {
        "request": request,
        "rules": rules,
        "site_title": SITE_TITLE,
        "site_name": SITE_NAME,
        "site_version": SITE_VERSION,
        "site_icp": SITE_ICP,
        "site_ai_model": SITE_AI_MODEL,
    })


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
