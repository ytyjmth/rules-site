#!/bin/bash
# 启动脚本：初始化数据库 + 导入已有规则 + 启动服务
set -e

echo "🔧 初始化数据库..."
python3 -c "
import sys; sys.path.insert(0, '.')
from app.database import init_db
init_db()
print('  DB ready')
"

echo "📂 导入已有规则文件..."
python3 -m app.init_rules

echo "🚀 启动服务..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
