# Rules Site — 规则订阅管理站

[![Docker](https://img.shields.io/badge/Docker-ytyjmth%2Frules--site-blue)](https://hub.docker.com/r/ytyjmth/rules-site)
[![Version](https://img.shields.io/badge/version-v1.0.3-green)](https://github.com/ytyjm/rules-site)

轻量级 Clash/Mihomo 规则文件托管 + 管理面板，支持在线编辑、审计日志、API 接口。

## 快速开始

一键部署：

```bash
docker run -d \
  --name rules-site \
  -p 8600:8000 \
  -v ./data:/app/data \
  -e ADMIN_PASSWORD=your_password \
  -e SECRET_KEY=$(openssl rand -hex 32) \
  ytyjmth/rules-site:latest
```

访问：
- 前台：http://localhost:8600
- 管理：http://localhost:8600/admin

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
- `GET /admin/api/rules` - 规则列表（管理面板会话）
- `GET /admin/api/rules/{rule_id}` - 规则详情
- `GET /api/v1/*` - Agent / CLI 编程调用 REST API（见下文）

## 部署方式

### 方式一：Docker Hub 镜像（推荐）

```bash
# 直接运行
docker run -d \
  --name rules-site \
  -p 8600:8000 \
  -v ./data:/app/data \
  -e ADMIN_PASSWORD=your_password \
  -e SECRET_KEY=$(openssl rand -hex 32) \
  ytyjmth/rules-site:latest

# 或使用 docker-compose
curl -O https://raw.githubusercontent.com/ytyjmth/rules-site/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/ytyjmth/rules-site/main/.env.example
cp .env.example .env
# 编辑 .env 配置密码
docker compose up -d
```

### 方式二：从源码构建

```bash
git clone https://github.com/ytyjm/rules-site.git
cd rules-site
cp .env.example .env
# 编辑 .env，修改 ADMIN_PASSWORD 和 SECRET_KEY
docker compose up -d
```

### 方式三：1Panel 部署

1. 进入 1Panel → 容器 → 编排
2. 新建编排，填入 `docker-compose.yml` 内容
3. 修改环境变量
4. 启动并配置反向代理

## Docker 镜像

| 标签 | 说明 |
|------|------|
| `latest` | 最新稳定版 |
| `v1.0.3` | 当前稳定版本 |

镜像特点：
- 多阶段构建，镜像体积小
- 内置健康检查

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
| `API_TOKENS` | 否 | API Token（格式 `名称:token,名称:token`，供 agent / CLI 编程调用；也可部署后在管理面板创建） |

## Agent 编程调用（API / CLI / Skill）

支持通过 API Token 让 agent、脚本、CI 直接管理在线规则，无需登录会话。

**获取 Token**（两种方式任选）：

1. 环境变量：`API_TOKENS="ci:rs_xxx,my-agent:rs_yyy"`（重启生效）
2. 管理面板 →「API Token」页签 → 创建（明文仅显示一次，可随时撤销）

### REST API（/api/v1）

认证：请求头携带 `Authorization: Bearer <token>` 或 `X-API-Token: <token>`。

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/me` | 验证 Token，返回身份 |
| `GET` | `/api/v1/rules?q=&page=&per_page=` | 规则列表（分页/搜索） |
| `GET` | `/api/v1/rules/{id}` | 规则详情（含内容） |
| `POST` | `/api/v1/rules` | 创建规则（JSON body） |
| `PUT` | `/api/v1/rules/{id}` | 更新规则（只传需修改字段） |
| `DELETE` | `/api/v1/rules/{id}` | 删除规则（自动备份） |
| `POST` | `/api/v1/sync` | 扫描目录同步数据库 |
| `GET` | `/api/v1/logs` | 审计日志 |
| `GET/POST/DELETE` | `/admin/api/tokens[/{id}]` | Token 管理（脱敏列表/创建/撤销，**仅限管理员会话**，API Token 不可调用） |

错误统一返回 JSON：401 未认证、400 参数不合法、404 不存在、409 重名。

```bash
TOKEN=rs_xxx BASE=http://localhost:8600
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/api/v1/rules"
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"filename":"new.yaml","display_name":"New","content":"payload:\n  - DOMAIN,a.com"}' \
  "$BASE/api/v1/rules"
```

### CLI（零依赖）

镜像内已内置；源码部署时位于 `cli/rules_cli.py`，仅需 Python 3 标准库：

```bash
# 首次配置（保存到 ~/.rules-cli/config.json）
python cli/rules_cli.py login --url http://localhost:8600 --token rs_xxx

# 常用命令
python cli/rules_cli.py list [-q 关键字] [--all]   # 列表 / 搜索
python cli/rules_cli.py get 3                      # 查看规则内容
python cli/rules_cli.py create ./f.yaml --name "名称" --desc "描述"
python cli/rules_cli.py update 3 --file ./new.yaml # 或 --name/--desc/--content
python cli/rules_cli.py delete 3 -y                # 删除（自动备份）
python cli/rules_cli.py sync                       # 目录同步
python cli/rules_cli.py logs                       # 审计日志
```

- 配置优先级：`--url/--token` > `RULES_SITE_URL/RULES_SITE_TOKEN` 环境变量 > 配置文件
- `--url/--token/--json` 写在子命令前或后均可；加 `--json` 输出原始 JSON，便于脚本与 agent 解析
- Docker 容器内：`docker exec rules-site python cli/rules_cli.py list`（URL 用 `http://localhost:8000`）

### Skill（agent 技能文件）

`skills/rules-site/SKILL.md` 提供了符合 agent Skill 规范的说明文件，把 `skills/` 目录
加入你的 agent（ZCode / Claude Code 等）配置后，agent 即可按指引自动调用上述 API/CLI
完成规则管理。

## 安全特性

- 登录速率限制（5分钟内最多10次，持久化）
- Token 可撤销（改密码后旧 token 失效）
- CSRF 防护
- API Token 只存哈希，明文仅创建时返回一次，可随时撤销
- Token 管理接口仅限管理员会话，API Token 无法创建/撤销 Token（防提权与横向撤销）
- 规则元数据（显示名称/描述）单行化 + 限长，防注释区逃逸注入 YAML
- Bearer Token 请求天然免疫 CSRF；会话 Cookie 写操作仍需 CSRF 校验
- YAML 解析大小限制（防 DoS，超过 1MB 的 YAML 内容将被拒绝）
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
│   ├── auth.py            # 认证 + CSRF + API Token + Token 黑名单
│   ├── rules_ops.py       # 规则文件共享操作（校验/备份/元数据）
│   ├── routes_public.py   # 前台路由（LRU 缓存）
│   ├── routes_admin.py    # 管理面板 + 审计
│   ├── routes_api.py      # REST API v1（agent / CLI 编程调用）
│   ├── utils.py           # 工具函数
│   ├── templates/
│   │   ├── index.html     # 前台首页
│   │   ├── admin.html     # 管理面板（含 API Token 管理）
│   │   ├── login.html     # 登录页
│   │   └── logs.html      # 审计日志页
│   └── static/
│       └── css/
│           ├── style.css  # 前台样式
│           ├── admin.css  # 管理面板样式
│           └── login.css  # 登录页样式
├── cli/
│   └── rules_cli.py       # 命令行客户端（零依赖）
├── skills/
│   └── rules-site/
│       └── SKILL.md       # agent Skill 定义
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
│   ├── test_update.py     # 更新功能测试
│   ├── test_api_errors.py # API 错误处理测试
│   └── test_api_v1.py     # REST API v1 测试
├── Dockerfile             # 多阶段构建
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
| `api_tokens` | API Token（哈希存储） |

## 开发

```bash
# 安装依赖
pip install -r requirements.txt

# 启动开发服务器
DATA_DIR=./data ADMIN_PASSWORD=test123456 SECRET_KEY=dev python3 -m uvicorn app.main:app --reload

# 运行测试（需要测试依赖：pytest / httpx）
pip install -r requirements-dev.txt
pytest tests/ -v
```
