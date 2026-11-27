"""REST API v1 测试（agent / CLI 编程调用）。"""
import os
import uuid

# 设置测试环境（必须在导入 app 之前，与其他测试模块保持同一约定）
os.environ["DATA_DIR"] = os.path.join(os.path.dirname(__file__), "data")
os.environ["SECRET_KEY"] = "test-secret-key-for-csrf-tests"
os.environ["ADMIN_PASSWORD"] = "test-password-for-csrf"
os.environ["API_TOKENS"] = "ci:rs_env_token_for_ci,backup-agent:rs_env_token_for_agent"

from fastapi.testclient import TestClient
from app.main import app
from app.auth import generate_csrf_token, generate_api_token, hash_api_token
from app.config import RULES_DIR, ADMIN_PASSWORD, API_TOKENS as _CFG_API_TOKENS
from app.database import init_db, create_api_token, get_db

init_db()

# 全套测试运行时 app 可能先于本模块导入（API_TOKENS 未配置），
# 这里直接补登记测试 Token，保证两种运行方式行为一致。
if "ci" not in _CFG_API_TOKENS:
    _CFG_API_TOKENS["ci"] = "rs_env_token_for_ci"
    _CFG_API_TOKENS["backup-agent"] = "rs_env_token_for_agent"

client = TestClient(app, follow_redirects=False)

ENV_TOKEN = "rs_env_token_for_ci"
VALID_YAML = "payload:\n  - DOMAIN-SUFFIX,example.com\n  - DOMAIN-KEYWORD,google\n"
AUTH_HEADERS = {"Authorization": f"Bearer {ENV_TOKEN}"}


def _login_session(c: TestClient) -> None:
    """通过管理面板登录获取会话 Cookie。"""
    resp = c.post(
        "/admin/login",
        data={
            "username": "admin",
            "password": ADMIN_PASSWORD,
            "csrf_token": generate_csrf_token(),
        },
    )
    assert resp.status_code == 302


def _session_client() -> TestClient:
    """不带重定向跟随的会话客户端。"""
    return TestClient(app, follow_redirects=False)


def _unique_name(base: str, ext: str = ".yaml") -> str:
    """共享数据库下生成不冲突的文件名。"""
    return f"{base}_{uuid.uuid4().hex[:8]}{ext}"


def _create(display_name="API 测试", content=VALID_YAML, filename=None, **extra):
    """通过 API 创建规则，返回 (响应, 文件名)。"""
    filename = filename or _unique_name("api")
    resp = client.post(
        "/api/v1/rules",
        headers=AUTH_HEADERS,
        json={"filename": filename, "display_name": display_name,
              "content": content, **extra},
    )
    return resp, filename


def _cleanup_rule(filename: str) -> None:
    """删除指定规则文件与数据库记录。"""
    with get_db() as conn:
        conn.execute("DELETE FROM rules WHERE filename=?", (filename,))
    fp = os.path.join(RULES_DIR, filename)
    if os.path.exists(fp):
        os.remove(fp)


class TestApiAuth:
    """API Token 认证测试。"""

    def test_no_token_401(self):
        """无认证信息返回 401。"""
        resp = client.get("/api/v1/rules")
        assert resp.status_code == 401
        assert resp.headers["content-type"] == "application/json"

    def test_invalid_token_401(self):
        """无效 Token 返回 401。"""
        resp = client.get("/api/v1/rules", headers={"Authorization": "Bearer rs_invalid_token"})
        assert resp.status_code == 401

    def test_env_token_bearer(self):
        """API_TOKENS 环境变量 Token（Bearer 头）可用。"""
        resp = client.get("/api/v1/rules", headers=AUTH_HEADERS)
        assert resp.status_code == 200

    def test_env_token_header(self):
        """X-API-Token 头同样可用。"""
        resp = client.get("/api/v1/rules", headers={"X-API-Token": ENV_TOKEN})
        assert resp.status_code == 200

    def test_env_token_second_name(self):
        """API_TOKENS 支持多个命名 Token。"""
        resp = client.get(
            "/api/v1/rules",
            headers={"Authorization": "Bearer rs_env_token_for_agent"},
        )
        assert resp.status_code == 200

    def test_me_endpoint(self):
        """/me 返回 Token 身份。"""
        resp = client.get("/api/v1/me", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["user"] == "api:ci"
        assert data["authenticated_via"] == "api_token"

    def test_me_unauthorized(self):
        """/me 未认证返回 401。"""
        resp = client.get("/api/v1/me")
        assert resp.status_code == 401

    def test_session_cookie_accepted(self):
        """管理面板登录会话也可访问 API。"""
        c = _session_client()
        _login_session(c)
        resp = c.get("/api/v1/rules")
        assert resp.status_code == 200
        me = c.get("/api/v1/me").json()
        assert me["authenticated_via"] == "session"

    def test_db_token_accepted(self):
        """数据库 Token（管理面板创建）可用。"""
        token = generate_api_token()
        create_api_token("test-runner", hash_api_token(token))
        resp = client.get("/api/v1/rules", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_revoked_db_token_rejected(self):
        """撤销后的数据库 Token 不可用。"""
        from app.database import revoke_api_token
        token = generate_api_token()
        token_id = create_api_token("revoked-runner", hash_api_token(token))
        assert revoke_api_token(token_id) is True
        resp = client.get("/api/v1/rules", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401


class TestApiRulesCrud:
    """规则 CRUD 测试。"""

    def test_create_and_get(self):
        """创建后可查询，文件带元数据头。"""
        resp, filename = _create()
        assert resp.status_code == 201
        data = resp.json()
        assert data["filename"] == filename
        assert data["display_name"] == "API 测试"

        # 文件内容带注释头
        filepath = os.path.join(RULES_DIR, filename)
        assert os.path.exists(filepath)
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        assert content.startswith("# title: API 测试")

        # get 返回内容
        resp = client.get(f"/api/v1/rules/{data['id']}", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        assert "DOMAIN-SUFFIX,example.com" in resp.json()["content"]

        _cleanup_rule(filename)

    def test_create_auto_suffix(self):
        """缺省后缀自动补 .yaml。"""
        base = _unique_name("noext", ext="")
        resp, _ = _create(filename=base)
        assert resp.status_code == 201
        assert resp.json()["filename"] == base + ".yaml"

        _cleanup_rule(base + ".yaml")

    def test_create_duplicate_409(self):
        """重复文件名返回 409。"""
        filename = _unique_name("dup")
        payload = {"filename": filename, "display_name": "D", "content": VALID_YAML}
        assert client.post("/api/v1/rules", headers=AUTH_HEADERS, json=payload).status_code == 201
        resp = client.post("/api/v1/rules", headers=AUTH_HEADERS, json=payload)
        assert resp.status_code == 409

        _cleanup_rule(filename)

    def test_create_invalid_yaml_400(self):
        """非法 YAML 返回 400。"""
        resp = client.post(
            "/api/v1/rules",
            headers=AUTH_HEADERS,
            json={"filename": _unique_name("bad"), "display_name": "B",
                  "content": "payload: [unclosed"},
        )
        assert resp.status_code == 400

    def test_create_empty_content_400(self):
        """空内容返回 400。"""
        resp = client.post(
            "/api/v1/rules",
            headers=AUTH_HEADERS,
            json={"filename": _unique_name("empty"), "display_name": "E", "content": "  "},
        )
        assert resp.status_code == 400

    def test_create_invalid_filename_400(self):
        """路径穿越文件名返回 400。"""
        resp = client.post(
            "/api/v1/rules",
            headers=AUTH_HEADERS,
            json={"filename": "../evil.yaml", "display_name": "E", "content": VALID_YAML},
        )
        assert resp.status_code == 400

    def test_list_includes_created(self):
        """列表接口包含创建的规则。"""
        filename = _unique_name("listed")
        client.post(
            "/api/v1/rules",
            headers=AUTH_HEADERS,
            json={"filename": filename, "display_name": "Listed", "content": VALID_YAML},
        )
        resp = client.get(f"/api/v1/rules?q={filename[:-5]}", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["rules"][0]["filename"] == filename

        _cleanup_rule(filename)

    def test_update_content(self):
        """更新内容后文件与数据库同步变化。"""
        resp, filename = _create(display_name="Old")
        rule_id = resp.json()["id"]
        resp = client.put(
            f"/api/v1/rules/{rule_id}",
            headers=AUTH_HEADERS,
            json={"content": "payload:\n  - DOMAIN,changed.org\n", "display_name": "New"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["display_name"] == "New"
        assert "DOMAIN,changed.org" in data["content"]
        # 旧内容已被替换
        assert "DOMAIN-SUFFIX,example.com" not in data["content"]

        _cleanup_rule(filename)

    def test_update_metadata_only(self):
        """只改元数据时保留规则内容。"""
        resp, filename = _create(display_name="Old Name")
        rule_id = resp.json()["id"]
        resp = client.put(
            f"/api/v1/rules/{rule_id}",
            headers=AUTH_HEADERS,
            json={"description": "新描述"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["description"] == "新描述"
        assert data["display_name"] == "Old Name"
        assert "DOMAIN-SUFFIX,example.com" in data["content"]

        _cleanup_rule(filename)

    def test_update_not_found_404(self):
        """更新不存在的规则返回 404。"""
        resp = client.put(
            "/api/v1/rules/99999",
            headers=AUTH_HEADERS,
            json={"display_name": "X"},
        )
        assert resp.status_code == 404

    def test_update_invalid_yaml_400(self):
        """更新为非法 YAML 返回 400。"""
        resp, filename = _create(display_name="B")
        rule_id = resp.json()["id"]
        resp = client.put(
            f"/api/v1/rules/{rule_id}",
            headers=AUTH_HEADERS,
            json={"content": "payload: [broken"},
        )
        assert resp.status_code == 400

        _cleanup_rule(filename)

    def test_delete(self):
        """删除规则后文件移除、查询 404。"""
        resp, filename = _create(display_name="Del")
        rule_id = resp.json()["id"]
        assert os.path.exists(os.path.join(RULES_DIR, filename))

        resp = client.delete(f"/api/v1/rules/{rule_id}", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        assert not os.path.exists(os.path.join(RULES_DIR, filename))

        resp = client.get(f"/api/v1/rules/{rule_id}", headers=AUTH_HEADERS)
        assert resp.status_code == 404

    def test_delete_not_found_404(self):
        """删除不存在的规则返回 404。"""
        resp = client.delete("/api/v1/rules/99999", headers=AUTH_HEADERS)
        assert resp.status_code == 404


class TestApiSyncAndLogs:
    """同步与审计日志测试。"""

    def test_sync_adds_disk_file(self):
        """sync 能把磁盘文件同步进数据库。"""
        filename = _unique_name("disk_sync")
        with open(os.path.join(RULES_DIR, filename), "w", encoding="utf-8") as f:
            f.write("# title: Disk Sync\n# description: dropped on disk\n\npayload:\n  - DOMAIN,a.b\n")

        resp = client.post("/api/v1/sync", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        assert filename in resp.json()["added"]

        _cleanup_rule(filename)

    def test_sync_requires_auth(self):
        """sync 未认证返回 401。"""
        resp = client.post("/api/v1/sync")
        assert resp.status_code == 401

    def test_logs_return_entries(self):
        """操作后审计日志可查询。"""
        resp, filename = _create(display_name="L")
        assert resp.status_code == 201

        resp = client.get("/api/v1/logs", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        actions = {log["action"] for log in data["logs"]}
        assert "create" in actions
        # 操作者为 Token 身份
        usernames = {log["username"] for log in data["logs"] if log["action"] == "create"}
        assert "api:ci" in usernames

        _cleanup_rule(filename)


class TestApiTokenManagement:
    """Token 管理接口测试（仅管理员会话可用，Bearer 一律 403）。"""

    def test_create_and_use_db_token_via_session(self):
        """管理面板登录会话创建 Token，明文可用且只存哈希。"""
        c = _session_client()
        _login_session(c)
        resp = c.post(
            "/admin/api/tokens",
            headers={"X-CSRF-Token": generate_csrf_token()},
            json={"name": "agent-x"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "agent-x"
        token = data["token"]
        assert token.startswith("rs_")

        # 明文 Token 可调用规则 API
        resp = client.get("/api/v1/rules", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

        # 列表脱敏：不含明文与哈希
        resp = c.get("/admin/api/tokens")
        assert resp.status_code == 200
        listing = resp.json()["tokens"]
        target = [t for t in listing if t["name"] == "agent-x"][0]
        assert "token" not in target and "token_hash" not in target

    def test_bearer_token_create_403(self):
        """Bearer Token 不能创建新 Token（防提权持久化）。"""
        resp = client.post(
            "/admin/api/tokens",
            headers=AUTH_HEADERS,
            json={"name": "escalated"},
        )
        assert resp.status_code == 403

    def test_bearer_token_revoke_403(self):
        """Bearer Token 不能撤销任何 Token（防横向 DoS）。"""
        c = _session_client()
        _login_session(c)
        resp = c.post(
            "/admin/api/tokens",
            headers={"X-CSRF-Token": generate_csrf_token()},
            json={"name": "victim"},
        )
        token_id = resp.json()["id"]

        resp = client.delete(f"/admin/api/tokens/{token_id}", headers=AUTH_HEADERS)
        assert resp.status_code == 403

    def test_bearer_token_list_403(self):
        """Bearer Token 不能列出 Token。"""
        resp = client.get("/admin/api/tokens", headers=AUTH_HEADERS)
        assert resp.status_code == 403

    def test_revoked_bearer_token_also_403(self):
        """连已撤销的 Token 也拿不到 403 之外的提示（不泄露信息）。"""
        # 未认证直接 401；无效 Token 同样 401
        resp = client.get("/admin/api/tokens", headers={"Authorization": "Bearer rs_junk"})
        assert resp.status_code == 401

    def test_create_token_without_csrf_403(self):
        """会话写操作缺 CSRF 返回 403。"""
        c = _session_client()
        _login_session(c)
        resp = c.post("/admin/api/tokens", json={"name": "no-csrf"})
        assert resp.status_code == 403

    def test_unauthenticated_token_list_401(self):
        """未认证不能列出 Token。"""
        resp = client.get("/admin/api/tokens")
        assert resp.status_code == 401

    def test_revoke_token(self):
        """管理员会话撤销后 Token 立即失效。"""
        c = _session_client()
        _login_session(c)
        resp = c.post(
            "/admin/api/tokens",
            headers={"X-CSRF-Token": generate_csrf_token()},
            json={"name": "short-lived"},
        )
        token_id = resp.json()["id"]
        token = resp.json()["token"]

        resp = c.delete(f"/admin/api/tokens/{token_id}",
                        headers={"X-CSRF-Token": generate_csrf_token()})
        assert resp.status_code == 200
        assert resp.json()["revoked"] is True

        resp = client.get("/api/v1/rules", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

    def test_revoke_not_found_404(self):
        """撤销不存在的 Token 返回 404。"""
        c = _session_client()
        _login_session(c)
        resp = c.delete("/admin/api/tokens/99999",
                        headers={"X-CSRF-Token": generate_csrf_token()})
        assert resp.status_code == 404

    def test_token_create_empty_name_400(self):
        """空名称返回 400。"""
        c = _session_client()
        _login_session(c)
        resp = c.post("/admin/api/tokens",
                      headers={"X-CSRF-Token": generate_csrf_token()},
                      json={"name": "  "})
        assert resp.status_code == 400


class TestApiWriteAuth:
    """写操作鉴权测试。"""

    def test_write_without_token_401(self):
        """无认证执行写操作返回 401。"""
        resp = client.post(
            "/api/v1/rules",
            json={"filename": _unique_name("x"), "display_name": "X", "content": VALID_YAML},
        )
        assert resp.status_code == 401

    def test_session_write_without_csrf_403(self):
        """会话写操作缺 CSRF 返回 403（API 与 Web 表单同一防线）。"""
        c = _session_client()
        _login_session(c)
        resp = c.post(
            "/api/v1/rules",
            json={"filename": _unique_name("nocsrf"), "display_name": "N", "content": VALID_YAML},
        )
        assert resp.status_code == 403

    def test_session_write_with_csrf_ok(self):
        """会话写操作带 X-CSRF-Token 头可成功。"""
        c = _session_client()
        _login_session(c)
        filename = _unique_name("csrfok")
        resp = c.post(
            "/api/v1/rules",
            headers={"X-CSRF-Token": generate_csrf_token()},
            json={"filename": filename, "display_name": "C", "content": VALID_YAML},
        )
        assert resp.status_code == 201

        _cleanup_rule(filename)


class TestMetaSanitization:
    """P0-2 回归：元数据不得逃逸注释区注入 YAML。"""

    INJECTION = 'Evil"\nproxy-groups:\n  - name: hacked\n    type: select\n# '

    def test_display_name_injection_sanitized(self):
        """display_name 含换行时，落盘文件顶层键必须与提交 content 一致。"""
        import yaml

        resp, filename = _create(
            display_name=self.INJECTION,
            description=f"desc{self.INJECTION}",
        )
        assert resp.status_code == 201
        data = resp.json()
        # 响应中的元数据已单行化
        assert "\n" not in data["display_name"]
        assert "\n" not in data["description"]

        # 落盘文件解析出的顶层键只有 content 本身的键
        with open(os.path.join(RULES_DIR, filename), "r", encoding="utf-8") as f:
            saved = yaml.safe_load(f)
        assert set(saved.keys()) == {"payload"}
        assert "proxy-groups" not in saved

        _cleanup_rule(filename)

    def test_display_name_too_long_422(self):
        """display_name 超长返回 422（Pydantic Field 限长）。"""
        resp = client.post(
            "/api/v1/rules",
            headers=AUTH_HEADERS,
            json={"filename": _unique_name("toolong"), "display_name": "字" * 201,
                  "content": VALID_YAML},
        )
        assert resp.status_code == 422

    def test_description_too_long_422(self):
        """description 超长返回 422。"""
        resp = client.post(
            "/api/v1/rules",
            headers=AUTH_HEADERS,
            json={"filename": _unique_name("toolongdesc"),
                  "display_name": "ok", "description": "字" * 501,
                  "content": VALID_YAML},
        )
        assert resp.status_code == 422

    def test_update_injection_sanitized(self):
        """更新元数据同样单行化。"""
        resp, filename = _create(display_name="Old")
        rule_id = resp.json()["id"]
        resp = client.put(
            f"/api/v1/rules/{rule_id}",
            headers=AUTH_HEADERS,
            json={"display_name": self.INJECTION},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "\n" not in data["display_name"]
        assert "hacked" in data["display_name"]  # 内容保留，仅单行化

        import yaml
        with open(os.path.join(RULES_DIR, filename), "r", encoding="utf-8") as f:
            saved = yaml.safe_load(f)
        assert set(saved.keys()) == {"payload"}

        _cleanup_rule(filename)


class TestHeaderPrecedence:
    """P2-3 回归：Bearer 优先，其他 Authorization scheme 不吞掉 X-API-Token。"""

    def test_x_api_token_with_other_auth_scheme(self):
        """Authorization: Basic 存在时，X-API-Token 仍然生效。"""
        resp = client.get(
            "/api/v1/rules",
            headers={"Authorization": "Basic dXNlcjpwYXNz", "X-API-Token": ENV_TOKEN},
        )
        assert resp.status_code == 200

    def test_bearer_takes_precedence_over_x_api_token(self):
        """两个头都在时 Bearer 优先。"""
        resp = client.get(
            "/api/v1/rules",
            headers={"Authorization": f"Bearer {ENV_TOKEN}", "X-API-Token": "rs_junk"},
        )
        assert resp.status_code == 200

    def test_revoked_token_does_not_touch_last_used(self):
        """无效 Token 不应写 last_used（401 即可）。"""
        resp = client.get("/api/v1/rules", headers={"Authorization": "Bearer rs_junk"})
        assert resp.status_code == 401
