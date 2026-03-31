"""数据库操作。"""
import sqlite3
import os
import glob
import re
import time
import hashlib
from contextlib import contextmanager
from typing import Generator
from app.config import DB_PATH, RULES_DIR
from app.utils import escape_like


def parse_rule_comments(filepath: str) -> dict:
    """从 YAML 文件头部注释提取元数据。

    支持格式：
        # title: 轻量代理规则
        # description: AI、GitHub 等常用代理域名

    返回 {"title": "...", "description": "..."}，未找到则值为空串。
    """
    meta = {"title": "", "description": ""}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("#"):
                    break  # 非注释行，停止扫描
                m = re.match(r"^#\s*(title|description)\s*[:：]\s*(.+)$", line, re.IGNORECASE)
                if m:
                    key = m.group(1).lower()
                    meta[key] = m.group(2).strip()
    except (OSError, UnicodeDecodeError):
        pass
    return meta


def filename_to_display_name(filename: str) -> str:
    """文件名智能转换为可读标题。

    ytyjm_proxy_lite.yaml → Ytyjm Proxy Lite
    cn-ip.yaml             → Cn Ip
    """
    name = filename
    for ext in (".yaml", ".yml"):
        if name.lower().endswith(ext):
            name = name[:-len(ext)]
            break
    # 下划线、连字符转空格，连续空格合并
    name = re.sub(r"[_\-]+", " ", name).strip()
    # 每个词首字母大写
    name = " ".join(w.capitalize() for w in name.split())
    return name or filename


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """每次调用新建连接，用完自动关闭，避免线程泄漏。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                description TEXT DEFAULT '',
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- 登录速率限制持久化
            CREATE TABLE IF NOT EXISTS login_attempts (
                ip TEXT NOT NULL,
                attempt_time REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_login_attempts_ip_time
                ON login_attempts(ip, attempt_time);

            -- Token 黑名单
            CREATE TABLE IF NOT EXISTS token_blacklist (
                token_hash TEXT PRIMARY KEY,
                expires_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_token_blacklist_expires
                ON token_blacklist(expires_at);

            -- 操作审计日志
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                filename TEXT,
                username TEXT NOT NULL,
                ip TEXT,
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_audit_log_created
                ON audit_log(created_at DESC);
        """)


_ALLOWED_ORDER_BY = frozenset({
    "sort_order ASC, filename ASC",
    "filename ASC",
    "display_name ASC",
    "updated_at DESC",
})


def search_rules(
    keyword: str = "",
    page: int = 1,
    per_page: int = 5,
    order_by: str = "sort_order ASC, filename ASC",
) -> tuple[list[dict], int, int]:
    """通用分页搜索，返回 (rules, total, total_pages)。"""
    if order_by not in _ALLOWED_ORDER_BY:
        order_by = "sort_order ASC, filename ASC"
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


def check_login_rate_limit(ip: str, window: int = 300, max_attempts: int = 10) -> bool:
    """检查 IP 是否超过登录速率限制。"""
    cutoff = time.time() - window
    with get_db() as conn:
        conn.execute("DELETE FROM login_attempts WHERE attempt_time < ?", (cutoff,))
        count = conn.execute(
            "SELECT COUNT(*) FROM login_attempts WHERE ip = ?", (ip,)
        ).fetchone()[0]
        return count < max_attempts


def record_login_failure(ip: str):
    """记录登录失败尝试。"""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO login_attempts (ip, attempt_time) VALUES (?, ?)",
            (ip, time.time()),
        )


def clear_login_attempts(ip: str):
    """清除指定 IP 的登录尝试记录（登录成功时调用）。"""
    with get_db() as conn:
        conn.execute("DELETE FROM login_attempts WHERE ip = ?", (ip,))


def check_token_blacklist(token: str) -> bool:
    """检查 token 是否在黑名单中。返回 True 表示有效。"""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM token_blacklist WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        return row is None


def revoke_token(token: str):
    """将 token 加入黑名单。"""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires_at = time.time() + 86400 * 7
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO token_blacklist (token_hash, expires_at) VALUES (?, ?)",
            (token_hash, expires_at),
        )


def cleanup_expired_tokens():
    """清理过期的黑名单 token。"""
    with get_db() as conn:
        conn.execute(
            "DELETE FROM token_blacklist WHERE expires_at < strftime('%s', 'now')"
        )


def log_audit(action: str, username: str, filename: str = None, ip: str = None, details: str = None):
    """记录审计日志。"""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO audit_log (action, filename, username, ip, details) VALUES (?, ?, ?, ?, ?)",
            (action, filename, username, ip, details),
        )


def get_audit_logs(page: int = 1, per_page: int = 20) -> tuple[list[dict], int, int]:
    """获取审计日志，分页。"""
    page = max(1, page)
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, total_pages)
        offset = (page - 1) * per_page
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (per_page, offset),
        ).fetchall()
    return [dict(r) for r in rows], total, total_pages


def sync_rules() -> dict[str, list[str]]:
    """扫描 RULES_DIR，将磁盘上的 YAML 文件同步到数据库。

    元数据提取优先级：
      1. 文件头部 # title: / # description: 注释
      2. 文件名智能转换（下划线转空格，首字母大写）

    - 新文件：自动插入数据库
    - 已有文件：跳过（不覆盖用户在后台的修改）
    - 数据库有但磁盘无：删除数据库记录
    """
    os.makedirs(RULES_DIR, exist_ok=True)

    # 扫描磁盘文件
    disk_files = set()
    for pattern in ("*.yaml", "*.yml"):
        for fp in glob.glob(os.path.join(RULES_DIR, pattern)):
            disk_files.add(os.path.basename(fp))

    with get_db() as conn:
        # 获取数据库现有记录
        db_rows = conn.execute("SELECT id, filename FROM rules").fetchall()
        db_files = {row["filename"]: row["id"] for row in db_rows}

        # 新文件 → 插入
        added = []
        for filename in sorted(disk_files - db_files.keys()):
            filepath = os.path.join(RULES_DIR, filename)

            # 尝试从文件注释提取元数据
            meta = parse_rule_comments(filepath)
            display_name = meta["title"] or filename_to_display_name(filename)
            description = meta["description"]

            conn.execute(
                "INSERT INTO rules (filename, display_name, description) VALUES (?, ?, ?)",
                (filename, display_name, description),
            )
            added.append(filename)

        # 磁盘删除的文件 → 清理数据库
        removed = []
        for filename in sorted(db_files.keys() - disk_files):
            conn.execute("DELETE FROM rules WHERE id=?", (db_files[filename],))
            removed.append(filename)

    return {"added": added, "removed": removed}
