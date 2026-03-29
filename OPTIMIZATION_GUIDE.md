# Rules Site 代码优化指南

## 审计信息
- 审计日期：2026-03-29
- 技术栈：FastAPI 0.115.6 + SQLite + Jinja2 + Docker
- 测试状态：44/44 通过，7 个 deprecation warning

---

## 一、安全加固

### 1.1 登录速率限制持久化 ⚡ 高优先级

**现状**：`routes_admin.py:17` 的 `_login_attempts` 是内存 `defaultdict(list)`，服务重启后清零，攻击者可通过重启绕过限制。

**方案 A（推荐）：SQLite 持久化**

在 `database.py` 的 `init_db()` 中新增表：

```sql
CREATE TABLE IF NOT EXISTS login_attempts (
    ip TEXT NOT NULL,
    attempt_time REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_attempts_ip_time
    ON login_attempts(ip, attempt_time);
```

`routes_admin.py` 中替换内存 dict 为数据库操作：

```python
LOGIN_RATE_LIMIT_WINDOW = 300
LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 10

def _check_login_rate_limit(ip: str) -> bool:
    cutoff = time.time() - LOGIN_RATE_LIMIT_WINDOW
    with get_db() as conn:
        conn.execute("DELETE FROM login_attempts WHERE attempt_time < ?", (cutoff,))
        count = conn.execute(
            "SELECT COUNT(*) FROM login_attempts WHERE ip = ?", (ip,)
        ).fetchone()[0]
        return count < LOGIN_RATE_LIMIT_MAX_ATTEMPTS

def _record_login_failure(ip: str):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO login_attempts (ip, attempt_time) VALUES (?, ?)",
            (ip, time.time()),
        )
```

**方案 B**：部署时加 fail2ban，监控 uvicorn 日志中的 429 响应。

---

### 1.2 Token 无法撤销 ⚡ 高优先级

**现状**：`auth.py:16-27` 的 `_verify_token` 只验证签名和过期时间，无服务端状态。改密码后旧 token 7 天内仍有效。

**最简方案**：密码变更时同步更换 `SECRET_KEY`（需重启，管理后台可接受）。

**完整方案：服务端 token 黑名单表**

1. 在 `init_db()` 中新增表：

```sql
CREATE TABLE IF NOT EXISTS token_blacklist (
    token_hash TEXT PRIMARY KEY,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_token_blacklist_expires
    ON token_blacklist(expires_at);
```

2. 在 `auth.py` 的 `_verify_token` 中增加黑名单查询：

```python
def _verify_token(token: str, max_age: int = 86400 * 7) -> bool:
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return False
        username, ts, sig = parts
        if time.time() - int(ts) > max_age:
            return False
        expected = hashlib.sha256(
            f"{SECRET_KEY}:{username}:{ts}".encode()
        ).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected):
            return False
        # 检查黑名单
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        from app.database import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT 1 FROM token_blacklist WHERE token_hash = ?", (token_hash,)
            ).fetchone()
            return row is None
    except (ValueError, IndexError):
        return False
```

3. 密码变更时将当前 token 的 hash 插入黑名单：

```python
def revoke_token(token: str):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires_at = time.time() + 86400 * 7
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO token_blacklist (token_hash, expires_at) VALUES (?, ?)",
            (token_hash, expires_at),
        )
```

4. 定期清理过期记录（可在 `get_db` 或 `lifespan` 中执行）：

```sql
DELETE FROM token_blacklist WHERE expires_at < strftime('%s', 'now');
```

---

### 1.3 CSRF Token 未绑定用户会话

**现状**：`auth.py:60` 的 `generate_csrf_token()` 只有 `ts:nonce:sig`，不绑定用户。攻击者获取自己的有效 CSRF token 后可用于其他会话的请求。

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

---

### 1.4 YAML 解析拒绝服务风险

**现状**：`routes_admin.py:37-41` 的 `_is_valid_yaml` 对上传内容直接调用 `yaml.safe_load`，恶意构造的深度嵌套 YAML 可耗尽 CPU。

**修复**：加解析超时保护：

```python
import signal

class YAMLTimeoutError(Exception):
    pass

def _timeout_handler(signum, frame):
    raise YAMLTimeoutError("YAML 解析超时")

def _is_valid_yaml(data: bytes) -> bool:
    try:
        decoded = data.decode("utf-8")
        # 限制大小，拒绝超大文件
        if len(decoded) > MAX_FILE_SIZE:
            return False
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(3)  # 3 秒超时
        try:
            yaml.safe_load(decoded)
            return True
        except YAMLTimeoutError:
            return False
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
    except (yaml.YAMLError, UnicodeDecodeError):
        return False
```

> ⚠️ `signal.SIGALRM` 在 Windows 上不可用。如有跨平台需求，可用 `threading.Timer` 或换用更快的 ryaml 绑定。

---

## 二、数据一致性

### 2.1 文件与数据库非原子操作 ⚡ 高优先级

**创建流程**：当前代码 `routes_admin.py:134-140` 先写文件、再写 DB：

```python
with open(filepath, "wb") as f:       # 先写文件
    f.write(data)
with get_db() as conn:                 # 后写 DB
    conn.execute("INSERT INTO rules ...")
```

若 DB 插入失败，文件成为孤儿。

**修复**：先写 DB 再写文件：

```python
with get_db() as conn:
    conn.execute(
        "INSERT INTO rules (filename, display_name, description) VALUES (?, ?, ?)",
        (filename, display_name, description),
    )
    filepath = os.path.join(RULES_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(data)
```

若文件写入失败，DB 事务回滚，无孤儿记录。

**删除流程**：当前代码 `routes_admin.py:217-225` 先删 DB、再删文件，方向合理：

```python
with get_db() as conn:
    conn.execute("DELETE FROM rules WHERE id=?", (rule_id,))  # 先删 DB
os.remove(filepath)  # 后删文件
```

优点：DB 删失败则文件不会被删，不会丢数据。缺点：文件删失败时 DB 记录已丢，文件残留（但 `sync_rules` 可清理）。

**改进：加事务保护，确保 DB 操作可回滚**

当前 `get_db()` 已经在 `except` 分支做了 `conn.rollback()`，但删除操作中文件删除在 `with` 块外面，回滚不了。建议把文件操作也纳入同一上下文：

```python
@router.post("/rules/{rule_id}/delete")
async def delete_rule(rule_id: int, request: Request, csrf_token: str = Form(None)):
    redir = require_admin_redirect(request)
    if redir:
        return redir
    validate_csrf(request, csrf_token)

    with get_db() as conn:
        rule = conn.execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone()
        if not rule:
            raise HTTPException(404, "Rule not found")

        filepath = os.path.join(RULES_DIR, rule["filename"])
        if os.path.exists(filepath):
            os.remove(filepath)  # 先删文件（可恢复，有回收站/备份）
        conn.execute("DELETE FROM rules WHERE id=?", (rule_id,))
        # 文件删失败会抛异常，DB 事务自动回滚
        # DB 删失败也会回滚，文件已删但 sync 可重建记录
```

> 注意：SQLite 的 `with get_db()` 上下文在异常时自动 `rollback()`，但文件删除不可逆。如果需要更强的一致性保证，可考虑先备份文件再删除，或用 `trash` 替代 `os.remove`。

---

### 2.2 更新时 updated_at 不一致

**现状**：`routes_admin.py:191-196` 的 `update_rule` 中，只修改 `display_name` 或 `description`（不改内容）时，不更新 `updated_at`。

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

### 2.3 空内容创建不校验

**现状**：`routes_admin.py:128-130` 中，用户不填 `content`、不传 `file` 时，`content = ""`，`data = b""`，直接写入创建 0 字节 YAML 文件。同时 `_is_valid_yaml(b"")` 返回 `True`（`yaml.safe_load("")` 返回 `None`），所以空文件能通过校验。

**修复**：在 `create_rule` 中加空内容检查：

```python
if not file or not file.filename:
    if not content.strip():
        return _error_response(request, "规则内容不能为空，请填写 YAML 内容或上传文件")
```

---

## 三、架构优化

### 3.1 消除重复的数据库查询逻辑

**现状**：`routes_public.py:24-42` 的 `index` 和 `routes_admin.py:85-103` 的 `dashboard` 分页搜索逻辑完全重复（~20 行 SQL 和分页计算一模一样）。

**修复**：抽取为 `database.py` 的通用查询函数：

```python
def search_rules(
    keyword: str = "",
    page: int = 1,
    per_page: int = 5,
    order_by: str = "sort_order ASC, filename ASC",
) -> tuple[list[dict], int, int]:
    """通用分页搜索，返回 (rules, total, total_pages)。"""
    page = max(1, page)
    with get_db() as conn:
        if keyword:
            escaped_q = escape_like(keyword)
            kw = f"%{escaped_q}%"
            total = conn.execute(
                "SELECT COUNT(*) FROM rules "
                "WHERE filename LIKE ? ESCAPE '\\' "
                "OR display_name LIKE ? ESCAPE '\\' "
                "OR description LIKE ? ESCAPE '\\'",
                (kw, kw, kw),
            ).fetchone()[0]
        else:
            total = conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0]

        total_pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, total_pages)
        offset = (page - 1) * per_page

        if keyword:
            rows = conn.execute(
                f"SELECT * FROM rules "
                f"WHERE filename LIKE ? ESCAPE '\\' "
                f"OR display_name LIKE ? ESCAPE '\\' "
                f"OR description LIKE ? ESCAPE '\\' "
                f"ORDER BY {order_by} LIMIT ? OFFSET ?",
                (kw, kw, kw, per_page, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM rules ORDER BY {order_by} LIMIT ? OFFSET ?",
                (per_page, offset),
            ).fetchall()

    return [dict(r) for r in rows], total, total_pages
```

路由层简化为：

```python
from app.database import search_rules

@router.get("/", response_class=HTMLResponse)
def index(request: Request, q: str = "", page: int = 1):
    rules, total, total_pages = search_rules(q, page, per_page=5)
    # ... 补充文件 size / preview 等附加信息 ...
    return templates.TemplateResponse(
        "index.html",
        build_template_context(request=request, rules=rules, q=q, page=page, total_pages=total_pages, total=total),
    )
```

---

### 3.2 健康检查验证依赖

**现状**：`main.py:31` 的 `/health` 只返回 `{"status": "ok"}`，不验证数据库是否可用。

**修复**：

```python
@app.get("/health")
def health():
    try:
        with get_db() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(503, detail={"status": "error", "detail": str(e)})
```

---

### 3.3 搜索结果页码链接不保留 q 参数（已修复 ✅）

当前模板中分页链接已正确携带 q 参数。此项无需修改。

---

## 四、性能优化

### 4.1 文件 I/O 在请求路径上

**现状**：`routes_public.py:50-55` 每次请求都读取前 50 行 YAML 用于预览。5 条规则 × 50 行 I/O，每次都执行。

**方案 A（推荐）：预览改为点击加载**

前端改为 AJAX 加载预览，减少首屏 I/O。已有类似的 `openEdit` fetch 逻辑可复用。

**方案 B：加 LRU 缓存**

```python
from functools import lru_cache

@lru_cache(maxsize=64)
def _read_preview(filepath: str, mtime: float) -> str:
    with open(filepath, "r", encoding="utf-8") as f:
        return "".join(f.readlines()[:50])
```

缓存 key 包含 `mtime`，文件修改时自动失效。

---

### 4.2 数据库连接策略

**现状**：`database.py:55` 每次请求新建 SQLite 连接。对低并发够用，但频繁创建销毁有开销。

**修复**：用 FastAPI 依赖注入 + 连接复用：

```python
from fastapi import Depends

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

# 路由中使用
@router.get("/")
def index(request: Request, conn: sqlite3.Connection = Depends(get_connection)):
    ...
```

---

### 4.3 静态文件缓存头

**现状**：`main.py:24` 的 `StaticFiles` 使用默认配置，浏览器每次请求都重新验证。

**修复**：给 CSS/JS 加长缓存：

```python
app.mount(
    "/static",
    StaticFiles(directory=static_dir),
    name="static",
)

# 或自定义 middleware 给 /static/* 加 Cache-Control
```

> 注：FastAPI 的 `StaticFiles` 不直接支持 `cache_headers` 参数，需要通过 middleware 或自定义 StaticFiles 子类实现。

---

## 五、代码质量

### 5.1 修复 Starlette deprecation warning

44 个测试中有 7 个 `DeprecationWarning`，来自 FastAPI 内部对旧式 `TemplateResponse` 的调用。当前代码写法正确（`request` 在前），等 FastAPI 升级即可。无需改代码，升级依赖即可。

---

### 5.2 路由中同步/异步混用

**现状**：部分路由是 `def`（同步），部分是 `async def`（异步）。

当前混用基本合理：含 `UploadFile` 的路由用 `async def`（需要异步读取），其他用 `def`。不影响正确性，但应保持风格一致。当前已经基本正确，无需大改。

---

### 5.3 routes_admin.py 中 _error_response 丢失上下文

**现状**：`routes_admin.py:61-65` 的 `_error_response` 只传了 `error` 和 `request`，丢失了 `rules`、`page`、`q` 等分页数据，导致用户看到的错误页没有规则列表。

**修复**：创建/更新失败时重定向回 `/admin` 并通过 query param 传错误信息：

```python
def _error_redirect(error_msg: str, q: str = "", page: int = 1) -> RedirectResponse:
    from urllib.parse import quote
    params = f"?error={quote(error_msg)}"
    if q:
        params += f"&q={quote(q)}"
    if page > 1:
        params += f"&page={page}"
    return RedirectResponse(url=f"/admin{params}", status_code=303)

# 或在 _error_response 中补全分页数据：
def _error_response(request: Request, error_msg: str, status_code: int = 400):
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM rules ORDER BY sort_order ASC, filename ASC LIMIT 5"
        ).fetchall()
    rules = [dict(r) for r in rows]
    return templates.TemplateResponse(
        "admin.html",
        build_template_context(request=request, rules=rules, error=error_msg, total=total),
        status_code=status_code,
    )
```

---

### 5.4 文件名验证正则过于严格

**现状**：`routes_admin.py:31` 的 `FILENAME_PATTERN = re.compile(r'^[a-zA-Z0-9_\-.]+\.ya?ml$')` 不支持中文文件名和空格。

如果用户需要中文规则名，当前只能通过 `display_name` 实现。是否需要扩展取决于需求，当前设计是合理的安全选择。

---

### 5.5 .env 文件安全

`.env` 已在 `.gitignore` 中 ✅，但本地明文存储密码。Docker 部署时建议用 Docker Secrets 或环境变量注入，而非挂载 `.env` 文件。

---

## 六、Docker 优化

### 6.1 Dockerfile 多阶段构建 + 非 root 用户

```dockerfile
FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim
RUN groupadd -r app && useradd -r -g app app
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY app/ app/
COPY start.sh .
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1
CMD ["bash", "start.sh"]
```

---

### 6.2 docker-compose 健康检查依赖

```yaml
services:
  rules-site:
    image: rules-site:latest
    container_name: rules-site
    restart: unless-stopped
    ports:
      - "8600:8000"
    volumes:
      - ./data:/app/data
    environment:
      - ADMIN_USERNAME=${ADMIN_USERNAME}
      - ADMIN_PASSWORD=${ADMIN_PASSWORD}
      - SECRET_KEY=${SECRET_KEY}
      - SITE_TITLE=${SITE_TITLE}
      - SITE_NAME=${SITE_NAME}
      - SITE_VERSION=${SITE_VERSION}
      - SITE_AI_MODEL=${SITE_AI_MODEL}
      - SITE_ICP=${SITE_ICP}
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
    deploy:
      resources:
        limits:
          memory: 256M
```

---

## 七、新增功能建议

### 7.1 操作审计日志

当前无审计记录。建议在数据库加表：

```sql
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,          -- create/update/delete/login
    filename TEXT,                 -- 操作的文件
    username TEXT NOT NULL,
    ip TEXT,
    details TEXT,                  -- JSON 格式，记录变更内容
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

在各路由中插入审计记录：

```python
def log_action(conn, action: str, filename: str = None, details: str = None):
    conn.execute(
        "INSERT INTO audit_log (action, filename, username, ip, details) VALUES (?, ?, ?, ?, ?)",
        (action, filename, "admin", request.client.host, details),
    )
```

---

### 7.2 规则文件变更历史

当前编辑直接覆盖，无法回退。建议用简单方案：

```python
import shutil
from datetime import datetime

def backup_rule(filepath: str):
    """编辑前备份当前版本。"""
    if os.path.exists(filepath):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = f"{filepath}.{ts}.bak"
        shutil.copy2(filepath, backup)
```

或更精简：保留最近 N 个版本（自动清理旧备份）。

---

### 7.3 API 接口

当前只有页面渲染。如果需要给外部工具调用，建议加 JSON API：

```python
@router.get("/api/rules")
def api_list_rules(q: str = "", page: int = 1, per_page: int = 20):
    rules, total, total_pages = search_rules(q, page, per_page)
    return {"rules": rules, "total": total, "page": page, "total_pages": total_pages}

@router.get("/api/rules/{rule_id}")
def api_get_rule(rule_id: int):
    ...
```

---

## 优先级总结

| 优先级 | 条目 | 影响 |
|--------|------|------|
| ⚡ 高 | 1.1 登录速率持久化 | 安全：重启绕过限制 |
| ⚡ 高 | 1.2 Token 撤销 | 安全：改密码后旧 token 仍有效 |
| ⚡ 高 | 2.1 文件/DB 原子性（创建流程） | 一致性：DB 失败导致孤儿文件 |
| 中 | 1.3 CSRF 绑定用户 | 安全：跨会话 token 复用 |
| 中 | 1.4 YAML DoS | 安全：恶意文件耗尽 CPU |
| 中 | 2.2 updated_at 不一致 | 数据质量 |
| 中 | 2.3 空内容校验 | 数据质量 |
| 中 | 3.1 重复查询抽取 | 可维护性 |
| 中 | 5.3 错误页丢失上下文 | 用户体验 |
| 低 | 4.1 文件 I/O 优化 | 性能（低并发无感） |
| 低 | 4.2 DB 连接复用 | 性能（SQLite 开销小） |
| 低 | 4.3 静态缓存 | 性能 |
| 低 | 6.1/6.2 Docker 优化 | 运维 |
| 低 | 7.1-7.3 新功能 | 功能扩展 |

最后更新：2026-03-29
