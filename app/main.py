import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.database import init_db
from app.routes_public import router as public_router
from app.routes_admin import router as admin_router
from app.config import check_security

# 获取当前文件所在目录，用于构造绝对路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时执行
    init_db()
    check_security()
    yield
    # 关闭时执行（如需要）


app = FastAPI(title="Rules Site", lifespan=lifespan)

# 静态文件使用绝对路径
static_dir = os.path.join(BASE_DIR, "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

app.include_router(public_router)
app.include_router(admin_router)
