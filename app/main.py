import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from starlette.staticfiles import StaticFiles
from app.database import init_db, sync_rules, get_db, cleanup_expired_tokens
from app.routes_public import router as public_router
from app.routes_admin import router as admin_router
from app.config import check_security

# 获取当前文件所在目录，用于构造绝对路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class CachedStaticFiles(StaticFiles):
    """带缓存头的静态文件服务。"""

    async def __call__(self, scope, receive, send) -> None:
        # 先让父类处理
        await super().__call__(scope, receive, send)

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        # 添加长缓存头：1年
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时执行
    init_db()
    cleanup_expired_tokens()
    sync_rules()
    check_security()
    yield
    # 关闭时执行（如需要）


app = FastAPI(title="Rules Site", lifespan=lifespan)

# 静态文件使用绝对路径，带缓存头
static_dir = os.path.join(BASE_DIR, "static")
app.mount("/static", CachedStaticFiles(directory=static_dir), name="static")

app.include_router(public_router)
app.include_router(admin_router)


@app.get("/health")
def health() -> dict:
    """健康检查，验证数据库可用性。"""
    try:
        with get_db() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(503, detail={"status": "error", "detail": str(e)})
