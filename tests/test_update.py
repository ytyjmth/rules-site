"""编辑规则 + 文件注释元数据测试。"""
import os

import uuid
from fastapi.testclient import TestClient
from app.main import app
from app.auth import generate_csrf_token
from app.config import RULES_DIR, ADMIN_PASSWORD
from app.database import init_db, get_db

init_db()


def _login(client):
    """登录并返回 CSRF token。"""
    token = generate_csrf_token()
    client.post("/admin/login", data={
        "username": "admin", "password": ADMIN_PASSWORD,
        "csrf_token": token,
    })


def _create_rule(client, filename=None, display_name="测试", content="test: true"):
    """创建规则并返回 filename。"""
    if filename is None:
        filename = f"test_{uuid.uuid4().hex[:8]}.yaml"
    token = generate_csrf_token()
    client.post("/admin/rules", data={
        "filename": filename, "display_name": display_name,
        "content": content, "csrf_token": token,
    }, follow_redirects=False)
    return filename


def _get_rule_id(filename):
    """从数据库获取规则 ID。"""
    with get_db() as conn:
        row = conn.execute("SELECT id FROM rules WHERE filename=?", (filename,)).fetchone()
        return row["id"] if row else None


class TestCreateMetaComments:
    """创建规则时自动写入注释元数据。"""

    def test_create_adds_title_comment(self):
        with TestClient(app, follow_redirects=False) as client:
            _login(client)
            fname = _create_rule(client, display_name="我的规则", content="payload:\n  - DOMAIN-SUFFIX,example.com")

            filepath = os.path.join(RULES_DIR, fname)
            with open(filepath, "r", encoding="utf-8") as f:
                text = f.read()

            assert "# title: 我的规则" in text
            assert "# description:" in text
            assert "payload:" in text

    def test_create_with_description(self):
        with TestClient(app, follow_redirects=False) as client:
            _login(client)
            fname = f"test_{uuid.uuid4().hex[:8]}.yaml"
            token = generate_csrf_token()
            client.post("/admin/rules", data={
                "filename": fname, "display_name": "规则名",
                "description": "规则描述",
                "content": "test: 1", "csrf_token": token,
            }, follow_redirects=False)

            filepath = os.path.join(RULES_DIR, fname)
            with open(filepath, "r", encoding="utf-8") as f:
                text = f.read()

            assert "# title: 规则名" in text
            assert "# description: 规则描述" in text


class TestUpdateRule:
    """编辑规则的各种场景。"""

    def test_update_content(self):
        """编辑内容，保留注释。"""
        with TestClient(app, follow_redirects=False) as client:
            _login(client)
            fname = _create_rule(client, display_name="原标题", content="old: true")
            rid = _get_rule_id(fname)

            token = generate_csrf_token()
            resp = client.post(f"/admin/rules/{rid}", data={
                "content": "new: true",
                "csrf_token": token,
            }, follow_redirects=False)
            assert resp.status_code == 303

            filepath = os.path.join(RULES_DIR, fname)
            with open(filepath, "r", encoding="utf-8") as f:
                text = f.read()

            assert "# title: 原标题" in text
            assert "new: true" in text
            assert "old: true" not in text

    def test_update_content_strips_old_meta(self):
        """编辑内容时，旧注释被替换而非重复。"""
        with TestClient(app, follow_redirects=False) as client:
            _login(client)
            fname = _create_rule(client, display_name="旧标题", content="a: 1")
            rid = _get_rule_id(fname)

            token = generate_csrf_token()
            client.post(f"/admin/rules/{rid}", data={
                "display_name": "新标题",
                "content": "b: 2",
                "csrf_token": token,
            }, follow_redirects=False)

            filepath = os.path.join(RULES_DIR, fname)
            with open(filepath, "r", encoding="utf-8") as f:
                text = f.read()

            # 不应出现两组 title 注释
            assert text.count("# title:") == 1
            assert "# title: 新标题" in text

    def test_update_empty_content_keeps_file(self):
        """内容留空时，不修改文件内容。"""
        with TestClient(app, follow_redirects=False) as client:
            _login(client)
            fname = _create_rule(client, display_name="测试", content="keep: this")
            rid = _get_rule_id(fname)

            token = generate_csrf_token()
            client.post(f"/admin/rules/{rid}", data={
                "content": "",
                "csrf_token": token,
            }, follow_redirects=False)

            filepath = os.path.join(RULES_DIR, fname)
            with open(filepath, "r", encoding="utf-8") as f:
                text = f.read()

            assert "keep: this" in text

    def test_update_display_name_only(self):
        """只改名称，不动内容。"""
        with TestClient(app, follow_redirects=False) as client:
            _login(client)
            fname = _create_rule(client, display_name="旧名", content="data: 1")
            rid = _get_rule_id(fname)

            token = generate_csrf_token()
            client.post(f"/admin/rules/{rid}", data={
                "display_name": "新名",
                "csrf_token": token,
            }, follow_redirects=False)

            filepath = os.path.join(RULES_DIR, fname)
            with open(filepath, "r", encoding="utf-8") as f:
                text = f.read()

            assert "# title: 新名" in text
            assert "data: 1" in text

    def test_update_invalid_yaml_rejected(self):
        """编辑时提交非法 YAML 被拒绝。"""
        with TestClient(app, follow_redirects=False) as client:
            _login(client)
            fname = _create_rule(client, content="valid: true")
            rid = _get_rule_id(fname)

            token = generate_csrf_token()
            resp = client.post(f"/admin/rules/{rid}", data={
                "content": ":\n  - bad\n    yaml: [broken",
                "csrf_token": token,
            }, follow_redirects=False)
            assert resp.status_code == 400

            # 原文件未被破坏
            filepath = os.path.join(RULES_DIR, fname)
            with open(filepath, "r", encoding="utf-8") as f:
                text = f.read()
            assert "valid: true" in text
