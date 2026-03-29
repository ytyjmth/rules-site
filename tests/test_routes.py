"""公共路由测试。"""
import os
import tempfile

# 设置测试环境（必须在导入 app 之前）
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["SECRET_KEY"] = "test-secret-key-for-testing-12345"
os.environ["ADMIN_PASSWORD"] = "TestP@ss123"

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


class TestIndexRoute:
    """首页路由测试。"""

    def test_index_returns_200(self):
        """首页返回 200。"""
        resp = client.get("/")
        assert resp.status_code == 200

    def test_index_with_search(self):
        """搜索参数正常工作。"""
        resp = client.get("/?q=test")
        assert resp.status_code == 200

    def test_index_pagination(self):
        """分页参数正常工作。"""
        resp = client.get("/?page=1")
        assert resp.status_code == 200

    def test_index_invalid_page_clamped(self):
        """无效页码被修正。"""
        resp = client.get("/?page=-1")
        assert resp.status_code == 200


class TestServeRule:
    """规则文件下载测试。"""

    def test_serve_nonexistent_file_404(self):
        """不存在的文件返回 404。"""
        resp = client.get("/rules/nonexistent.yaml")
        assert resp.status_code == 404

    def test_path_traversal_blocked(self):
        """路径穿越被阻止。"""
        resp = client.get("/rules/../secret.txt")
        assert resp.status_code in (400, 404)

    def test_path_traversal_encoded_blocked(self):
        """编码的路径穿越被阻止。"""
        resp = client.get("/rules/..%2F..%2Fsecret.txt")
        assert resp.status_code in (400, 404)

    def test_absolute_path_blocked(self):
        """绝对路径被阻止。"""
        resp = client.get("/rules//etc/passwd")
        assert resp.status_code in (400, 404)


class TestHealthEndpoint:
    """健康检查测试。"""

    def test_health_returns_ok(self):
        """/health 返回 ok。"""
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
