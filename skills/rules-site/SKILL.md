---
name: rules-site
description: 管理 Rules Site（Clash/Mihomo 规则订阅托管站）的在线规则。当需要查看、创建、更新、删除站点上的规则文件，同步规则目录，或查询操作审计日志时使用。适用于本机、Docker 或远程部署的 rules-site 实例。
---

# Rules Site 规则管理

通过 REST API（`/api/v1`）或零依赖 CLI `cli/rules_cli.py` 管理在线规则。
所有操作使用 API Token 认证（`rs_` 开头，由站点管理员提供）。

## 连接配置

优先级：命令行参数 > 环境变量 > 配置文件。

```bash
# 方式一：环境变量（推荐，agent 会话中直接 export）
export RULES_SITE_URL=http://localhost:8600
export RULES_SITE_TOKEN=rs_xxxxxxxxxxxx

# 方式二：login 保存到 ~/.rules-cli/config.json，之后命令不用再带参数
python cli/rules_cli.py login --url http://localhost:8600 --token rs_xxx

# 方式三：每次显式传参（--url/--token 写在子命令前或后均可）
python cli/rules_cli.py --url http://localhost:8600 --token rs_xxx list
python cli/rules_cli.py list --url http://localhost:8600 --token rs_xxx
```

先验证连通性，返回身份即为配置成功：

```bash
python cli/rules_cli.py status
# 或直接调 API
curl -s -H "Authorization: Bearer $RULES_SITE_TOKEN" $RULES_SITE_URL/api/v1/me
```

## 常用操作

以下示例中 CLI 用 `python cli/rules_cli.py`（在仓库根目录执行）；curl 需要
`-H "Authorization: Bearer $RULES_SITE_TOKEN"` 和 `-H "Content-Type: application/json"`。

### 查看规则列表 / 详情

```bash
python cli/rules_cli.py list              # 全部规则
python cli/rules_cli.py list -q github    # 关键字搜索
python cli/rules_cli.py get 3             # ID=3 的规则内容
python cli/rules_cli.py --json list       # JSON 输出（程序解析）
```

API: `GET /api/v1/rules?q=关键字&page=1&per_page=20`，`GET /api/v1/rules/{id}`。

### 创建规则（从本地 YAML 文件）

```bash
python cli/rules_cli.py create ./my-rules.yaml --name "My Rules" --desc "说明"
```

要求：内容为合法 YAML；文件名只允许字母、数字、下划线、连字符和点（自动补 .yaml）；
重名返回 409。创建成功返回 201 和新规则 ID。

### 更新规则

只传需要修改的字段，未传字段保留原值：

```bash
python cli/rules_cli.py update 3 --file ./new-content.yaml   # 替换内容
python cli/rules_cli.py update 3 --name "新名称" --desc "新描述"
python cli/rules_cli.py update 3 --content "payload:\n  - DOMAIN,a.com"
```

### 删除规则

删除前服务端自动备份；CLI 默认询问确认，脚本/agent 用 `-y` 跳过：

```bash
python cli/rules_cli.py delete 3 -y
```

### 同步目录与审计日志

```bash
python cli/rules_cli.py sync    # 扫描 data/rules/ 目录，新增/清理数据库记录
python cli/rules_cli.py logs    # 最近操作审计日志（谁、何时、改了什么）
```

## 直接调用 API（无 CLI 时）

```bash
BASE=http://localhost:8600 TOKEN=rs_xxx

# 列表
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/api/v1/rules"

# 创建（201）
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"filename":"new.yaml","display_name":"New","description":"","content":"payload:\n  - DOMAIN,a.com"}' \
  "$BASE/api/v1/rules"

# 更新（200，只传要改的字段）
curl -s -X PUT -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"display_name":"Renamed"}' "$BASE/api/v1/rules/3"

# 删除（200，服务端自动备份）
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" "$BASE/api/v1/rules/3"

# 同步 / 日志 / 身份
curl -s -X POST -H "Authorization: Bearer $TOKEN" "$BASE/api/v1/sync"
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/api/v1/logs?page=1"
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/api/v1/me"
```

也接受 `X-API-Token: <token>` 头替代 Authorization。

## 注意事项

- 认证失败统一返回 401（JSON），Token 管理接口用 Token 访问返回 403（仅限管理员会话），
  资源不存在 404，参数不合法 400，重名 409。
- Token 只能由管理员创建（管理面板或 `API_TOKENS` 环境变量），agent 无法自行创建/撤销 Token；
  若 Token 失效请联系站点管理员。
- 写操作会记入审计日志，操作者显示为 `api:<token名称>`。
- Token 明文只在创建时显示一次；在管理面板「API Token」页签可创建/撤销，
  或由管理员通过 `API_TOKENS` 环境变量配置（格式 `名称:token,名称:token`）。
- 规则文件头部 `# title:` / `# description:` 注释即显示名称与描述，更新内容时
  服务端会自动维护，CLI/API 只需传纯 YAML 内容。
