# Rules Site — 规则订阅管理站

轻量级 Clash/Mihomo 规则文件托管 + 管理面板，支持在线编辑、审计日志、API 接口。

## 功能

### 前台
- 📋 公开展示规则列表，支持预览和复制订阅链接
- 🔍 关键字搜索
- 📱 移动端适配

### 管理面板
- 🔐 登录保护 + 速率限制（持久化）
- ✏️ 在线编辑 YAML（自动备份）
- 📤 上传规则文件
- 🗑️ 删除规则（删除前备份）
- 🔄 目录同步
- 📋 操作审计日志

### API 接口
- `GET /admin/api/rules` - 规则列表
- `GET /admin/api/rules/{id}` - 规则详情

## 快速部署

```bash
# 1. 克隆项目
git clone <repo> /opt/rules-site
cd /opt/rules-site

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，修改 ADMIN_PASSWORD 和 SECRET_KEY

# 3. 生成 SECRET_KEY
openssl rand -hex 32

# 4. 启动
docker compose up -d

# 5. 访问
# 前台：http://localhost:8600
# 管理：http://localhost:8600/admin
# 审计：http://localhost:8600/admin/logs
```

## 1Panel 部署

1. 进入 1Panel → 容器 → 编排
2. 新建编排，填入 `docker-compose.yml` 内容
3. 修改环境变量
4. 启动并配置反向代理

## 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `ADMIN_USERNAME` | 否 | 管理员用户名（默认 admin） |
| `ADMIN_PASSWORD` | ✅ | 管理员密码（至少 8 位） |
| `SECRET_KEY` | ✅ | 签名密钥（随机字符串） |
| `SITE_TITLE` | 否 | 站点标题（页面 <title>） |
| `SITE_NAME` | 否 | 网站名称（页脚显示） |
| `SITE_VERSION` | 否 | 版本号（页脚显示） |
| `SITE_AI_MODEL` | 否 | AI 模型标识（页脚显示） |
| `SITE_ICP` | 否 | ICP 备案号（页脚显示并链接） |

## 安全特性

- 登录速率限制（5分钟内最多10次，持久化）
- Token 可撤销（改密码后旧 token 失效）
- CSRF 防护
- YAML 解析大小限制（防 DoS）
- 操作审计日志

## 数据备份

编辑/删除操作自动备份，保留最近 5 个版本：
```
data/backups/
├── rules.yaml.20260330_143000.bak
├── rules.yaml.20260330_150000.bak
└── ...
```

## 目录结构

```
rules-site/
├── app/
│   ├── main.py           # FastAPI 入口 + 生命周期
│   ├── config.py          # 配置 + 安全检查
│   ├── database.py        # SQLite 操作 + 审计日志
│   ├── auth.py            # 认证 + CSRF + Token 黑名单
│   ├── routes_public.py   # 前台路由（LRU 缓存）
│   ├── routes_admin.py    # 管理面板 + API + 审计
│   ├── utils.py           # 工具函数
│   ├── templates/
│   │   ├── index.html     # 前台首页
│   │   ├── admin.html     # 管理面板
│   │   ├── login.html     # 登录页
│   │   └── logs.html      # 审计日志页
│   └── static/
│       └── css/
│           ├── style.css  # 前台样式
│           ├── admin.css  # 管理面板样式
│           └── login.css  # 登录页样式
├── data/
│   ├── rules.db           # SQLite 数据库
│   ├── rules/             # YAML 规则文件
│   └── backups/           # 自动备份
├── tests/
│   ├── conftest.py        # 测试配置
│   ├── test_routes.py     # 路由测试
│   ├── test_csrf.py       # CSRF 测试
│   ├── test_database.py   # 数据库测试
│   ├── test_security.py   # 安全配置测试
│   └── test_api_errors.py # API 错误处理测试
├── Dockerfile             # 多阶段构建 + 非 root
├── docker-compose.yml     # 资源限制 + 健康检查
├── requirements.txt       # Python 依赖
├── start.sh               # 启动脚本
├── .env.example           # 环境变量模板
└── OPTIMIZATION_GUIDE.md  # 代码优化指南
```

## 数据库表

| 表 | 用途 |
|----|------|
| `rules` | 规则元数据 |
| `login_attempts` | 登录尝试记录 |
| `token_blacklist` | Token 黑名单 |
| `audit_log` | 操作审计日志 |

## 开发

```bash
# 安装依赖
pip install -r requirements.txt

# 启动开发服务器
DATA_DIR=./data ADMIN_PASSWORD=test123456 SECRET_KEY=dev python3 -m uvicorn app.main:app --reload

# 运行测试
pytest tests/ -v
```
