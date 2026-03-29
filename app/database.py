"""数据库操作。"""
import sqlite3
import os
import glob
import re
from contextlib import contextmanager
from typing import Generator
from app.config import DB_PATH, RULES_DIR


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
        """)


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
