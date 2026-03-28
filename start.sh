#!/bin/bash
# 启动脚本：启动服务（数据库初始化由 lifespan 处理）
set -e

echo "🚀 启动服务..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
