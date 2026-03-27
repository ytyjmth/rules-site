#!/usr/bin/env python3
"""首次部署时，自动将 data/rules/ 下的 YAML 文件导入数据库。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import RULES_DIR
from app.database import init_db, get_db


def import_existing():
    init_db()
    with get_db() as conn:
        existing = {r["filename"] for r in conn.execute("SELECT filename FROM rules").fetchall()}
        imported = 0
        for fname in sorted(os.listdir(RULES_DIR)):
            if not fname.endswith((".yaml", ".yml")):
                continue
            if fname in existing:
                continue
            conn.execute(
                "INSERT INTO rules (filename, display_name) VALUES (?, ?)",
                (fname, fname.rsplit(".", 1)[0]),
            )
            imported += 1
            print(f"  + {fname}")
    print(f"\n导入完成: {imported} 条")


if __name__ == "__main__":
    import_existing()
