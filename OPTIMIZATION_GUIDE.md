# Rules Site 代码优化指南

## 审计信息
- 审计日期：2026-03-29
- 最后更新：2026-04-01
- 技术栈：FastAPI 0.115.6 + SQLite + Jinja2 + Docker
- 测试状态：44/44 通过，7 个 deprecation warning

---

## 一、安全加固

### 1.1 登录速率限制持久化 ✅ 已完成

SQLite 持久化 `login_attempts` 表，重启后限制不丢失。

---

### 1.2 Token 无法撤销 ✅ 已完成

`token_blacklist` 表 + 密码变更时自动加入黑名单 + 定期清理过期记录。

---

### 1.3 CSRF Token 未绑定用户会话

**现状**：`auth.py:66` 的 `generate_csrf_token()` 只有 `ts:nonce:sig`，不绑定用户。攻击者获取自己的有效 CSRF token 后可用于其他会话的请求。

**修复**：将 username 写入签名：

```python
def generate_csrf_token(username: str = "", max_age: int = 3600) -> str:
    ts = str(int(time.time()))
    nonce = os.urandom(8).hex()
    sig = hmac.new(
        SECRET_KEY.encode(),
        f"{ts}:{nonce}:{username}".encode(),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"{ts}:{nonce}:{username}:{sig}"

def verify_csrf_token(token: str, username: str = "", max_age: int = 3600) -> bool:
    try:
        parts = token.split(":")
        if len(parts) != 4:
            return False
        ts, nonce, token_user, sig = parts
        if token_user != username:
            return False
        if time.time() - int(ts) > max_age:
            return False
        expected = hmac.new(
            SECRET_KEY.encode(),
            f"{ts}:{nonce}:{username}".encode(),
            hashlib.sha256,
        ).hexdigest()[:32]
        return hmac.compare_digest(sig, expected)
    except (ValueError, IndexError):
        return False
```

同时更新 `validate_csrf` 和模板中的调用，传入当前用户名。

> 💡 单用户场景风险极低，可不改。

---

### 1.4 YAML 解析拒绝服务风险 ✅ 已缓解

已加 `MAX_FILE_SIZE` 限制（5MB）。极端场景（深度嵌套小文件）未做超时保护，但实际风险可忽略。

---

## 二、数据一致性

### 2.1 文件与数据库非原子操作 ✅ 已完成

创建流程：先写文件，DB 失败时自动回滚删除文件。删除流程：先删 DB 再删文件。

---

### 2.2 更新时 updated_at 不一致

**现状**：`routes_admin.py` 的 `update_rule` 中，只修改 `display_name` 或 `description`（不改内容）时，不更新 `updated_at`。

**修复**：所有更新操作都带上时间戳：

```python
if updates:
    updates.append("updated_at=CURRENT_TIMESTAMP")
    params.append(rule_id)
    conn.execute(
        f"UPDATE rules SET {', '.join(updates)} WHERE id=?", params
    )
```

---

### 2.3 空内容创建不校验 ✅ 已完成

`create_rule` 已加空内容检查，空文件无法创建。

---

## 三、架构优化

### 3.1 消除重复的数据库查询逻辑 ✅ 已完成

`search_rules()` 已抽取到 `database.py`，前台和管理面板共用。

---

### 3.2 健康检查验证依赖 ✅ 已完成

`/health` 已验证数据库连接可用性，失败返回 503。

---

### 3.3 搜索结果页码链接不保留 q 参数 ✅ 已完成

模板中分页链接已正确携带 q 参数。

---

## 四、性能优化

> 低并发场景无感，以下为可选优化。

### 4.1 文件 I/O 在请求路径上

**现状**：前台每次请求读取前 50 行 YAML 用于预览。

**方案**：预览改为 AJAX 按需加载，或加 LRU 缓存（key 含 mtime）。

---

### 4.2 数据库连接策略

**现状**：每次请求新建 SQLite 连接。

**方案**：FastAPI 依赖注入 + 连接复用。SQLite 开销小，低并发无需改。

---

### 4.3 静态文件缓存头

**现状**：`StaticFiles` 默认配置，浏览器每次重新验证。

**方案**：middleware 给 `/static/*` 加 `Cache-Control`。

---

## 五、代码质量

### 5.1 Starlette deprecation warning

7 个 warning 来自 FastAPI 内部，等升级依赖即可，无需改代码。

---

### 5.2 路由中同步/异步混用

当前混用合理：含 `UploadFile` 用 `async def`，其他用 `def`。无需改动。

---

### 5.3 routes_admin.py 中 _error_response 丢失上下文

**现状**：创建/更新失败时，错误页丢失 `rules`、`page`、`q` 等分页数据。

**修复**：重定向回 `/admin` 并通过 query param 传错误信息：

```python
def _error_redirect(error_msg: str, q: str = "", page: int = 1) -> RedirectResponse:
    from urllib.parse import quote
    params = f"?error={quote(error_msg)}"
    if q:
        params += f"&q={quote(q)}"
    if page > 1:
        params += f"&page={page}"
    return RedirectResponse(url=f"/admin{params}", status_code=303)
```

---

### 5.4 文件名验证正则过于严格

当前只允许 `[a-zA-Z0-9_\-.]`，不支持中文。通过 `display_name` 可展示中文名，当前设计合理。

---

### 5.5 .env 文件安全

`.env` 已在 `.gitignore` 中 ✅。Docker 部署建议用环境变量注入，而非挂载 `.env` 文件。

---

## 六、Docker 优化

### 6.1 Dockerfile

多阶段构建 ✅ 已完成。非 root 用户：v1.0.2 已改回 root 运行（简化 1Panel 部署），不再追求此项。

---

### 6.2 docker-compose

健康检查 ✅ 已完成。资源限制可选添加（当前无 OOM 风险）。

---

## 七、新增功能

### 7.1 操作审计日志 ✅ 已完成

`audit_log` 表 + `log_audit()` + 分页日志页面 `/admin/logs`。

---

### 7.2 规则文件变更历史 ✅ 已完成

编辑/删除前自动备份，保留最近 5 个版本。

---

### 7.3 API 接口 ✅ 已完成

- `GET /admin/api/rules` — 规则列表
- `GET /admin/api/rules/{rule_id}` — 规则详情

---

## 优先级总结

| 优先级 | 条目 | 状态 |
|--------|------|------|
| 中 | 2.2 updated_at 不一致 | 未做 |
| 中 | 5.3 错误页丢失上下文 | 未做 |
| 低 | 1.3 CSRF 绑定用户 | 未做（单用户可忽略） |
| 低 | 4.1 文件 I/O 优化 | 未做（低并发无感） |
| 低 | 4.2 DB 连接复用 | 未做（低并发无感） |
| 低 | 4.3 静态缓存 | 未做（低并发无感） |
