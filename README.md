# Rules Site — 规则订阅管理站

一个轻量级的 Clash/Mihomo 规则文件托管 + 管理面板，支持在线编辑、上传、一键部署到 1Panel。

## 功能

- 📋 公开展示规则文件列表，支持预览和复制订阅链接
- 🔐 管理面板登录保护
- ✏️ 在线编辑 YAML 内容
- 📤 上传规则文件
- 🗑️ 删除规则
- 📱 移动端适配

## 1Panel 部署

### 方法一：Docker Compose（推荐）

```bash
# 1. 克隆到服务器
git clone <repo> /opt/rules-site
cd /opt/rules-site

# 2. 修改配置
cp .env.example .env
# 编辑 .env 修改密码和密钥

# 3. 构建并启动
docker compose up -d

# 4. 访问
# 前台：http://localhost:8600
# 管理：http://localhost:8600/admin
```

### 方法二：1Panel 应用商店

1. 进入 1Panel → 容器 → 编排
2. 新建编排，填入 `docker-compose.yml` 内容
3. 修改环境变量（密码、密钥）
4. 启动

### 配置反向代理

在 1Panel → 网站 → 创建网站：
- 类型：反向代理
- 代理地址：`http://127.0.0.1:8600`
- 绑定域名：`rules.ytyjm.com`

## 迁移旧数据

把你现有的 YAML 文件放到 `data/rules/` 目录，然后在管理面板手动添加记录即可。

或者直接编辑数据库：

```bash
# 进入容器
docker exec -it rules-site bash

# 添加规则记录
sqlite3 /app/data/rules.db "INSERT INTO rules (filename, display_name) VALUES ('ytyjm_cn_list.yaml', '国内域名列表');"
```

## 默认账号

- 用户名：`admin`
- 密码：`changeme`（**务必修改！**）

通过环境变量 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 修改。

## 目录结构

```
rules-site/
├── app/
│   ├── main.py           # FastAPI 入口
│   ├── config.py          # 配置
│   ├── database.py        # SQLite 初始化
│   ├── auth.py            # 认证
│   ├── routes_public.py   # 前台路由
│   ├── routes_admin.py    # 管理面板路由
│   ├── templates/         # HTML 模板
│   └── static/            # CSS/JS
├── data/
│   ├── rules.db           # SQLite 数据库
│   └── rules/             # YAML 规则文件
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```
