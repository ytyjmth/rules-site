import os
import sys

# 数据目录（容器内挂载）
DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
RULES_DIR = os.path.join(DATA_DIR, "rules")
DB_PATH = os.path.join(DATA_DIR, "rules.db")

# 认证（必须通过环境变量设置，无默认值）
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

# 站点
SITE_TITLE = os.environ.get("SITE_TITLE", "规则文档")
SITE_NAME = os.environ.get("SITE_NAME", "雨天依旧美")
SITE_VERSION = os.environ.get("SITE_VERSION", "1.0.0")
SITE_ICP = os.environ.get("SITE_ICP", "")
SITE_AI_MODEL = os.environ.get("SITE_AI_MODEL", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "")

os.makedirs(RULES_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)


def check_security() -> None:
    """检查安全配置，不合规直接退出。"""
    errors = []

    if not SECRET_KEY:
        errors.append("SECRET_KEY 未设置，请设置为随机字符串")
    elif SECRET_KEY in ("change-me-in-production", "change-me-to-random-string", "changeme"):
        errors.append(f"SECRET_KEY 使用了不安全的默认值，请更换")

    if not ADMIN_PASSWORD:
        errors.append("ADMIN_PASSWORD 未设置，请设置为强密码")
    elif len(ADMIN_PASSWORD) < 8:
        errors.append("ADMIN_PASSWORD 长度不足 8 位，请使用更强的密码")
    elif ADMIN_PASSWORD.lower() in (
        "changeme", "password", "12345678", "admin123", "admin888",
        "qwerty123", "password1", "p@ssw0rd", "letmein1",
    ):
        errors.append(f"ADMIN_PASSWORD 使用了常见弱密码，请更换")

    if errors:
        print("\n" + "=" * 70)
        print("🚫 启动失败：安全配置不合规")
        print("=" * 70)
        for e in errors:
            print(f"  ❌ {e}")
        print()
        print("请通过环境变量设置后重新启动，例如：")
        print("  ADMIN_PASSWORD=your_password SECRET_KEY=$(openssl rand -hex 32) \\")
        print("  python3 -m uvicorn app.main:app")
        print("=" * 70 + "\n")
        sys.exit(1)
