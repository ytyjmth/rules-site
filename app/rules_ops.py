"""规则文件共享操作：校验、备份、元数据包装。

供 Web 表单路由（routes_admin）与 REST API（routes_api）共用，
确保两条入口的校验规则与文件格式完全一致。
"""
import os
import re
import shutil
import glob
import yaml
from datetime import datetime

from app.config import BACKUP_DIR, MAX_BACKUPS_PER_FILE

# ── 文件上传限制 ─────────────────────────────────────────
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_YAML_PARSE_SIZE = 1 * 1024 * 1024  # YAML 解析最大 1MB，防止 DoS
FILENAME_PATTERN = re.compile(r'^[a-zA-Z0-9_\-.]+\.ya?ml$')


def validate_filename(filename: str) -> bool:
    return bool(FILENAME_PATTERN.match(filename))


def is_valid_yaml(data: bytes) -> bool:
    """检查内容是否为合法 YAML。限制解析大小防止 DoS。"""
    try:
        decoded = data.decode("utf-8")
        # 限制 YAML 解析大小，防止深度嵌套攻击
        if len(decoded) > MAX_YAML_PARSE_SIZE:
            return False
        yaml.safe_load(decoded)
        return True
    except (yaml.YAMLError, UnicodeDecodeError):
        return False


def backup_file(filepath: str):
    """编辑/删除前备份当前版本，保留最近 N 个备份。"""
    if not os.path.exists(filepath):
        return

    filename = os.path.basename(filepath)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"{filename}.{ts}.bak")

    try:
        shutil.copy2(filepath, backup_path)
    except OSError:
        pass  # 备份失败不影响主流程

    # 清理旧备份，保留最近 N 个
    pattern = os.path.join(BACKUP_DIR, f"{filename}.*.bak")
    backups = sorted(glob.glob(pattern), reverse=True)
    for old_backup in backups[MAX_BACKUPS_PER_FILE:]:
        try:
            os.remove(old_backup)
        except OSError:
            pass


def strip_meta_comments(text: str) -> str:
    """移除文件头部的 # title: / # description: 注释，返回纯内容。"""
    lines = text.split("\n")
    result_lines = []
    skipped_header = True
    for line in lines:
        if skipped_header and re.match(r"^\s*#\s*(title|description)\s*[:：]", line, re.IGNORECASE):
            continue
        elif skipped_header and re.match(r"^\s*#\s*$", line):
            # 空注释行也跳过
            continue
        else:
            skipped_header = False
            result_lines.append(line)
    # 去掉开头多余空行
    while result_lines and not result_lines[0].strip():
        result_lines.pop(0)
    return "\n".join(result_lines)


def wrap_with_meta(content: str, title: str, desc: str) -> str:
    """生成带注释头的文件内容。

    title/desc 会被单行化并限长，防止换行把内容顶出注释区、注入真实 YAML。
    """
    header = (
        f"# title: {_sanitize_meta(title, _META_TITLE_MAX)}\n"
        f"# description: {_sanitize_meta(desc, _META_DESC_MAX)}\n\n"
    )
    return header + content


_META_TITLE_MAX = 200
_META_DESC_MAX = 500


def _sanitize_meta(value: str, limit: int) -> str:
    """单行化（换行/制表符折叠为空格）并截断，杜绝注释区逃逸。"""
    return " ".join(str(value or "").split())[:limit]
