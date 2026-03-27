"""安全配置检查测试。"""
import os
import sys
import subprocess


def test_exits_on_missing_password():
    """未设密码 → 退出码 1"""
    env = os.environ.copy()
    env["DATA_DIR"] = os.path.join(os.path.dirname(__file__), "data")
    env["SECRET_KEY"] = "valid-key"
    env.pop("ADMIN_PASSWORD", None)
    result = subprocess.run(
        [sys.executable, "-c", "from app.config import check_security; check_security()"],
        capture_output=True, text=True, env=env,
        cwd=os.path.dirname(os.path.dirname(__file__)),
    )
    assert result.returncode == 1
    assert "ADMIN_PASSWORD 未设置" in result.stdout


def test_exits_on_missing_secret_key():
    """未设 SECRET_KEY → 退出码 1"""
    env = os.environ.copy()
    env["DATA_DIR"] = os.path.join(os.path.dirname(__file__), "data")
    env["ADMIN_PASSWORD"] = "strong-password"
    env.pop("SECRET_KEY", None)
    result = subprocess.run(
        [sys.executable, "-c", "from app.config import check_security; check_security()"],
        capture_output=True, text=True, env=env,
        cwd=os.path.dirname(os.path.dirname(__file__)),
    )
    assert result.returncode == 1
    assert "SECRET_KEY 未设置" in result.stdout


def test_exits_on_weak_password():
    """弱密码 changeme → 退出码 1"""
    env = os.environ.copy()
    env["DATA_DIR"] = os.path.join(os.path.dirname(__file__), "data")
    env["SECRET_KEY"] = "valid-key"
    env["ADMIN_PASSWORD"] = "changeme"
    result = subprocess.run(
        [sys.executable, "-c", "from app.config import check_security; check_security()"],
        capture_output=True, text=True, env=env,
        cwd=os.path.dirname(os.path.dirname(__file__)),
    )
    assert result.returncode == 1
    assert "changeme" in result.stdout


def test_exits_on_weak_secret_key():
    """常见默认 SECRET_KEY → 退出码 1"""
    env = os.environ.copy()
    env["DATA_DIR"] = os.path.join(os.path.dirname(__file__), "data")
    env["SECRET_KEY"] = "change-me-in-production"
    env["ADMIN_PASSWORD"] = "strong-password"
    result = subprocess.run(
        [sys.executable, "-c", "from app.config import check_security; check_security()"],
        capture_output=True, text=True, env=env,
        cwd=os.path.dirname(os.path.dirname(__file__)),
    )
    assert result.returncode == 1
    assert "不安全的默认值" in result.stdout


def test_passes_with_valid_config():
    """正常配置 → 不退出"""
    env = os.environ.copy()
    env["DATA_DIR"] = os.path.join(os.path.dirname(__file__), "data")
    env["SECRET_KEY"] = "my-random-secret-key-12345"
    env["ADMIN_PASSWORD"] = "MyStr0ngP@ss!"
    result = subprocess.run(
        [sys.executable, "-c", "from app.config import check_security; check_security()"],
        capture_output=True, text=True, env=env,
        cwd=os.path.dirname(os.path.dirname(__file__)),
    )
    assert result.returncode == 0
