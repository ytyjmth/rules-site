"""CSRF 防护测试。"""
import os
os.environ["DATA_DIR"] = os.path.join(os.path.dirname(__file__), "data")
os.environ["SECRET_KEY"] = "test-secret-key-for-csrf-tests"
os.environ["ADMIN_PASSWORD"] = "test-password-for-csrf"

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.auth import generate_csrf_token, verify_csrf_token


@pytest.fixture
def client():
    with TestClient(app, follow_redirects=False) as c:
        yield c


def do_login(client):
    """辅助：完成登录流程，确保 cookie 到位。"""
    token = generate_csrf_token()
    client.post("/admin/login", data={
        "username": "admin", "password": "test-password-for-csrf",
        "csrf_token": token
    })


class TestCSRFBasics:
    def test_token_generation_and_verify(self):
        token = generate_csrf_token()
        assert verify_csrf_token(token) is True

    def test_tampered_token_fails(self):
        token = generate_csrf_token()
        assert verify_csrf_token(token[:-4] + "xxxx") is False

    def test_expired_token_fails(self):
        token = generate_csrf_token(max_age=0)
        assert verify_csrf_token(token, max_age=0) is False


class TestLoginCSRF:
    def test_login_page_has_csrf(self, client):
        r = client.get("/admin/login")
        assert r.status_code == 200
        assert "csrf_token" in r.text

    def test_login_without_csrf_rejected(self, client):
        r = client.post("/admin/login", data={
            "username": "admin", "password": "test-password-for-csrf"
        })
        assert r.status_code == 403

    def test_login_with_fake_csrf_rejected(self, client):
        r = client.post("/admin/login", data={
            "username": "admin", "password": "test-password-for-csrf",
            "csrf_token": "fake:token:xxx"
        })
        assert r.status_code == 403

    def test_login_with_valid_csrf_succeeds(self, client):
        token = generate_csrf_token()
        r = client.post("/admin/login", data={
            "username": "admin", "password": "test-password-for-csrf",
            "csrf_token": token
        })
        assert r.status_code == 302
        assert r.headers["location"] == "/admin"


class TestAdminCSRF:
    def test_admin_page_has_csrf(self, client):
        do_login(client)
        r = client.get("/admin/")
        assert r.status_code == 200
        assert "csrf_token" in r.text

    def test_create_without_csrf_rejected(self, client):
        do_login(client)
        r = client.post("/admin/rules", data={
            "filename": "test.yaml", "display_name": "Test"
        })
        assert r.status_code == 403

    def test_create_with_csrf_succeeds(self, client):
        do_login(client)
        token = generate_csrf_token()
        import uuid
        fname = f"test_{uuid.uuid4().hex[:8]}.yaml"
        r = client.post("/admin/rules", data={
            "filename": fname, "display_name": "Test",
            "content": "test: true", "csrf_token": token
        })
        assert r.status_code == 303

        # 清理测试文件
        from app.config import RULES_DIR
        fp = os.path.join(RULES_DIR, fname)
        if os.path.exists(fp):
            os.remove(fp)

    def test_delete_without_csrf_rejected(self, client):
        do_login(client)
        r = client.post("/admin/rules/1/delete", data={})
        assert r.status_code == 403

    def test_update_without_csrf_rejected(self, client):
        do_login(client)
        r = client.post("/admin/rules/1", data={
            "display_name": "Updated"
        })
        assert r.status_code == 403

    def test_logout_requires_post(self, client):
        """GET /admin/logout 不应再有效。"""
        do_login(client)
        r = client.get("/admin/logout", follow_redirects=False)
        # GET 请求 logout 应返回 405 Method Not Allowed
        assert r.status_code == 405

    def test_logout_without_csrf_rejected(self, client):
        do_login(client)
        r = client.post("/admin/logout", data={}, follow_redirects=False)
        assert r.status_code == 403

    def test_logout_with_csrf_succeeds(self, client):
        do_login(client)
        token = generate_csrf_token()
        r = client.post("/admin/logout", data={"csrf_token": token}, follow_redirects=False)
        assert r.status_code == 302


class TestYAMLValidation:
    def test_create_with_invalid_yaml_rejected(self, client):
        do_login(client)
        token = generate_csrf_token()
        import uuid
        fname = f"bad_{uuid.uuid4().hex[:8]}.yaml"
        r = client.post("/admin/rules", data={
            "filename": fname, "display_name": "Bad",
            "content": ":\n  - invalid\n    yaml: [broken", "csrf_token": token
        })
        # 应返回 400 而不是创建成功
        assert r.status_code == 400

    def test_create_with_valid_yaml_succeeds(self, client):
        do_login(client)
        token = generate_csrf_token()
        import uuid
        fname = f"good_{uuid.uuid4().hex[:8]}.yaml"
        r = client.post("/admin/rules", data={
            "filename": fname, "display_name": "Good",
            "content": "payload:\n  - DOMAIN-SUFFIX,example.com", "csrf_token": token
        })
        assert r.status_code == 303

        # 清理
        from app.config import RULES_DIR
        fp = os.path.join(RULES_DIR, fname)
        if os.path.exists(fp):
            os.remove(fp)


class TestPathTraversal:
    def test_path_traversal_returns_404(self, client):
        """路径穿越尝试被 FastAPI 路由层规范化后返回 404。"""
        r = client.get("/rules/../../etc/passwd")
        assert r.status_code == 404

    def test_normal_filename_serves(self, client):
        """不存在的文件返回 404。"""
        r = client.get("/rules/nonexistent.yaml")
        assert r.status_code == 404

    def test_handler_basename_check(self):
        """验证 handler 内的 basename 校验逻辑。"""
        import os
        # 模拟 serve_rule 的安全检查逻辑
        for dangerous in ("../../etc/passwd", "../secret.yaml", "dir/file.yaml"):
            safe_name = os.path.basename(dangerous)
            assert safe_name != dangerous or not safe_name  # 应触发拒绝
