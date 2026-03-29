"""数据库同步测试。"""
import os
import tempfile
import sqlite3

# 设置测试环境（必须在导入 app 之前）
_test_data_dir = tempfile.mkdtemp()
os.environ["DATA_DIR"] = _test_data_dir
os.environ["SECRET_KEY"] = "test-secret-key-for-testing-12345"
os.environ["ADMIN_PASSWORD"] = "TestP@ss123"

from app.database import sync_rules, parse_rule_comments, filename_to_display_name, init_db
from app.config import DB_PATH, RULES_DIR


def test_parse_rule_comments():
    """测试从 YAML 文件注释提取元数据。"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("# title: 测试标题\n")
        f.write("# description: 这是描述\n")
        f.write("# 其他注释\n")
        f.write("rules:\n")
        f.write("  - domain: example.com\n")
        f.flush()

        meta = parse_rule_comments(f.name)
        assert meta["title"] == "测试标题"
        assert meta["description"] == "这是描述"

    os.unlink(f.name)


def test_parse_rule_comments_no_meta():
    """无元数据注释时返回空串。"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("rules:\n")
        f.write("  - domain: example.com\n")
        f.flush()

        meta = parse_rule_comments(f.name)
        assert meta["title"] == ""
        assert meta["description"] == ""

    os.unlink(f.name)


def test_filename_to_display_name():
    """测试文件名智能转换。"""
    assert filename_to_display_name("ytyjm_proxy_lite.yaml") == "Ytyjm Proxy Lite"
    assert filename_to_display_name("cn-ip.yml") == "Cn Ip"
    assert filename_to_display_name("simple") == "Simple"
    assert filename_to_display_name("multiple___underscores.yaml") == "Multiple Underscores"


def test_sync_rules_adds_new_files():
    """测试新文件自动添加到数据库。"""
    init_db()

    # 创建一个测试 YAML 文件
    os.makedirs(RULES_DIR, exist_ok=True)
    test_file = os.path.join(RULES_DIR, "test_rule.yaml")
    with open(test_file, "w") as f:
        f.write("# title: Test Rule\n")
        f.write("rules:\n")

    result = sync_rules()

    assert "test_rule.yaml" in result["added"]

    # 验证数据库记录
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT * FROM rules WHERE filename='test_rule.yaml'").fetchone()
    conn.close()

    assert row is not None

    # 清理
    os.remove(test_file)


def test_sync_rules_removes_deleted_files():
    """测试删除文件后数据库记录同步清理。"""
    init_db()

    os.makedirs(RULES_DIR, exist_ok=True)
    test_file = os.path.join(RULES_DIR, "to_delete.yaml")

    # 先创建文件并同步
    with open(test_file, "w") as f:
        f.write("rules:\n")
    sync_rules()

    # 删除文件再同步
    os.remove(test_file)
    result = sync_rules()

    assert "to_delete.yaml" in result["removed"]
