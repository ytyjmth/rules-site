import os
import warnings

# 数据目录（容器内挂载）
DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
RULES_DIR = os.path.join(DATA_DIR, "rules")
DB_PATH = os.path.join(DATA_DIR, "rules.db")

# 认证
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")

# 站点
SITE_TITLE = os.environ.get("SITE_TITLE", "规则文档")
SITE_NAME = os.environ.get("SITE_NAME", "雨天依旧美")
SITE_VERSION = os.environ.get("SITE_VERSION", "1.0.0")
SITE_ICP = os.environ.get("SITE_ICP", "")
SITE_AI_MODEL = os.environ.get("SITE_AI_MODEL", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")

# 检查是否使用了默认的 SECRET_KEY
SECRET_KEY_IS_DEFAULT = SECRET_KEY == "change-me-in-production"
ADMIN_PASSWORD_IS_DEFAULT = ADMIN_PASSWORD == "changeme"

os.makedirs(RULES_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)


def check_security_warnings():
    """检查安全配置并打印警告"""
    if SECRET_KEY_IS_DEFAULT:
        warnings.warn(
            "⚠️  安全警告: SECRET_KEY 使用了默认值 'change-me-in-production'，"
            "请在生产环境中设置环境变量 SECRET_KEY 为随机字符串！",
            UserWarning
        )
        print("\n" + "=" * 70)
        print("⚠️  安全警告: SECRET_KEY 使用了默认值!")
        print("   请在生产环境中设置环境变量 SECRET_KEY 为随机字符串!")
        print("=" * 70 + "\n")
    
    if ADMIN_PASSWORD_IS_DEFAULT:
        warnings.warn(
            "⚠️  安全警告: ADMIN_PASSWORD 使用了默认值 'changeme'，"
            "请在生产环境中设置环境变量 ADMIN_PASSWORD 为强密码！",
            UserWarning
        )
        print("\n" + "=" * 70)
        print("⚠️  安全警告: ADMIN_PASSWORD 使用了默认值!")
        print("   请在生产环境中设置环境变量 ADMIN_PASSWORD 为强密码!")
        print("=" * 70 + "\n")
