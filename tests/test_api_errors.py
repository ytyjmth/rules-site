"""API 端点错误处理测试。"""
import os

# 使用 tests/data 目录（已有数据库）
os.environ["DATA_DIR"] = os.path.join(os.path.dirname(__file__), "data")
os.environ["SECRET_KEY"] = "test-secret-key-for-csrf-tests"
os.environ["ADMIN_PASSWORD"] = "test-password-for-csrf"

from fastapi.testclient import TestClient
from app.main import app
from app.auth import generate_csrf_token
from app.database import init_db

# 确保数据库已初始化
init_db()

client = TestClient(app, follow_redirects=False)


class TestAPIErrors:
    """API 端点错误返回 JSON。"""

    def test_api_unauthorized_returns_json(self):
        """未授权访问 API 返回 JSON。"""
        resp = client.get("/admin/rules/999/content")
        assert resp.status_code == 401
        assert resp.headers["content-type"] == "application/json"

    def test_api_not_found_returns_json(self):
        """资源不存在返回 JSON。"""
        # 登录
        token = generate_csrf_token()
        login_resp = client.post(
            "/admin/login",
            data={
                "username": "admin",
                "password": "test-password-for-csrf",
                "csrf_token": token,
            },
        )
        assert login_resp.status_code == 302

        # 访问不存在的资源
        resp = client.get("/admin/rules/99999/content")
        assert resp.status_code == 404
        assert resp.headers["content-type"] == "application/json"


class TestPublicAPIErrors:
    """公共 API 错误返回 JSON。"""

    def test_invalid_filename_returns_json(self):
        """无效文件名返回 JSON。"""
        resp = client.get("/rules/../etc/passwd")
        assert resp.status_code in (400, 404)
        assert resp.headers["content-type"] == "application/json"

    def test_not_found_returns_json(self):
        """文件不存在返回 JSON。"""
        resp = client.get("/rules/nonexistent.yaml")
        assert resp.status_code == 404
        assert resp.headers["content-type"] == "application/json"
