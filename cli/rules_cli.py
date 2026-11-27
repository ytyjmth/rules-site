"""rules-cli — Rules Site 命令行客户端。

零第三方依赖（仅用标准库），供 agent / 人工通过命令行管理在线规则。

配置来源（优先级从高到低）：
  1. 命令行参数 --url / --token
  2. 环境变量 RULES_SITE_URL / RULES_SITE_TOKEN
  3. 配置文件 ~/.rules-cli/config.json（由 login 命令写入）

常用命令：
  rules-cli login  --url http://localhost:8600 --token rs_xxx   保存并验证配置
  rules-cli status                                               查看当前配置与连通性
  rules-cli list [-q 关键字] [--page N] [--all]                  规则列表
  rules-cli get <id>                                             规则详情（含内容）
  rules-cli create <file.yaml> [--name 显示名] [--desc 描述]     创建规则
  rules-cli update <id> [--file f.yaml] [--name N] [--desc D]    更新规则
  rules-cli delete <id> [-y]                                     删除规则
  rules-cli sync                                                 扫描目录同步数据库
  rules-cli logs [--page N]                                      审计日志

加 --json 输出原始 JSON，便于程序解析。
"""
import argparse
import json
import os
import sys
import unicodedata
import urllib.error
import urllib.request
from urllib.parse import urlencode

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".rules-cli", "config.json")
API_PREFIX = "/api/v1"


# ── 配置 ────────────────────────────────────────────────

def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(url: str, token: str) -> str:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"url": url.rstrip("/"), "token": token}, f, ensure_ascii=False, indent=2)
    # 明文 Token 落盘，限制为仅当前用户可读（POSIX）
    if os.name != "nt":
        os.chmod(CONFIG_PATH, 0o600)
    return CONFIG_PATH


def mask_token(token: str) -> str:
    if not token:
        return "(未配置)"
    if len(token) <= 10:
        return token[:4] + "***"
    return token[:7] + "***" + token[-4:]


# ── HTTP ────────────────────────────────────────────────

class ApiError(Exception):
    """API 调用失败（非 2xx 或网络错误）。"""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def api_request(method: str, path: str, url: str, token: str,
                payload: dict | None = None, timeout: float = 30.0) -> dict:
    """调用 REST API，返回解析后的 JSON。失败抛 ApiError。"""
    full_url = url.rstrip("/") + API_PREFIX + path
    data = None
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(full_url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8")).get("detail", "")
        except Exception:
            pass
        raise ApiError(f"HTTP {e.code}: {detail or e.reason}", status=e.code) from e
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise ApiError(f"无法连接 {url}（{getattr(e, 'reason', e)}）") from e
    except json.JSONDecodeError as e:
        raise ApiError(f"响应不是合法 JSON：{e}") from e
    except ValueError as e:
        # 覆盖 http.client.InvalidURL（URL 含控制字符）与 UnicodeEncodeError（非法编码）
        raise ApiError(f"请求构造失败：{e}") from e


# ── 输出 ────────────────────────────────────────────────

def _display_width(s: str) -> int:
    """终端显示宽度：东亚全角字符按 2 列计算。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _display_width(s))


def print_table(rows: list[dict], columns: list[tuple[str, str]]):
    """columns: [(表头, 字段名), ...]。按显示宽度对齐，中英文混排不错位。"""
    if not rows:
        print("(空)")
        return
    str_rows = [[str(r.get(field, "") or "-") for _, field in columns] for r in rows]
    widths = [
        max(_display_width(h), *(_display_width(row[i]) for row in str_rows))
        for i, (h, _) in enumerate(columns)
    ]
    header = "  ".join(_pad(h, w) for h, w in zip([c[0] for c in columns], widths))
    print(header)
    print("  ".join("-" * w for w in widths))
    for row in str_rows:
        print("  ".join(_pad(c, w) for c, w in zip(row, widths)))


def print_json(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


# ── 命令实现 ────────────────────────────────────────────

def cmd_login(args):
    url = args.url or os.environ.get("RULES_SITE_URL") or load_config().get("url")
    token = args.token or os.environ.get("RULES_SITE_TOKEN") or load_config().get("token")
    if not url or not token:
        print("错误：需要 --url 与 --token（或设置 RULES_SITE_URL / RULES_SITE_TOKEN）",
              file=sys.stderr)
        return 1
    # 先验证连通性，再落盘，避免留下无效配置
    me = api_request("GET", "/me", url, token)
    path = save_config(url, token)
    print(f"已保存配置到 {path}")
    print(f"连接成功，当前身份：{me.get('user')}（{me.get('authenticated_via')}）")
    return 0


def cmd_status(args, url: str, token: str):
    print(f"URL   : {url or '(未配置)'}")
    print(f"Token : {mask_token(token)}")
    if not url or not token:
        print("提示：先运行 login 命令完成配置。", file=sys.stderr)
        return 1
    me = api_request("GET", "/me", url, token)
    print(f"身份  : {me.get('user')}（{me.get('authenticated_via')}）")
    return 0


def cmd_list(args, url: str, token: str):
    merged_rules = []
    last_page = None
    page = 1
    while True:
        qs = urlencode({"q": args.q, "page": page, "per_page": 50})
        data = api_request("GET", f"/rules?{qs}", url, token)
        last_page = data
        rows = data["rules"]
        if args.json and not args.all:
            print_json(data)
            return 0
        if args.json and args.all:
            merged_rules.extend(rows)
        elif rows:
            print_table(rows, [("ID", "id"), ("文件名", "filename"), ("显示名称", "display_name"),
                               ("描述", "description"), ("更新时间", "updated_at")])
        if args.all and page < data["total_pages"] and rows:
            page += 1
            if not args.json:
                print()
            continue
        if args.json and args.all:
            print_json({
                "rules": merged_rules,
                "total": last_page["total"],
                "page": last_page["page"],
                "total_pages": last_page["total_pages"],
            })
            return 0
        if not args.json:
            print(f"\n共 {data['total']} 条，第 {data['page']}/{data['total_pages']} 页")
        break
    return 0


def cmd_get(args, url: str, token: str):
    data = api_request("GET", f"/rules/{args.id}", url, token)
    if args.json:
        print_json(data)
        return 0
    print(f"ID     : {data['id']}")
    print(f"文件名 : {data['filename']}")
    print(f"名称   : {data['display_name']}")
    print(f"描述   : {data['description'] or '-'}")
    print(f"创建   : {data['created_at']}  更新: {data['updated_at']}")
    print("─" * 40)
    print(data["content"])
    return 0


def cmd_create(args, url: str, token: str):
    try:
        with open(args.file, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        print(f"错误：无法读取文件 {args.file}（{e}）", file=sys.stderr)
        return 1
    display_name = args.name or os.path.splitext(os.path.basename(args.file))[0]
    payload = {
        "filename": args.filename or os.path.basename(args.file),
        "display_name": display_name,
        "description": args.desc or "",
        "content": content,
    }
    data = api_request("POST", "/rules", url, token, payload=payload)
    if args.json:
        print_json(data)
    else:
        print(f"已创建：[{data['id']}] {data['filename']}（{data['display_name']}）")
    return 0


def cmd_update(args, url: str, token: str):
    payload = {}
    if args.file:
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                payload["content"] = f.read()
        except OSError as e:
            print(f"错误：无法读取文件 {args.file}（{e}）", file=sys.stderr)
            return 1
    if args.content is not None:
        payload["content"] = args.content
    if args.name is not None:
        payload["display_name"] = args.name
    if args.desc is not None:
        payload["description"] = args.desc
    if not payload:
        print("错误：未指定任何更新内容（--file / --content / --name / --desc）", file=sys.stderr)
        return 1
    data = api_request("PUT", f"/rules/{args.id}", url, token, payload=payload)
    if args.json:
        print_json(data)
    else:
        print(f"已更新：[{data['id']}] {data['filename']}（{data['display_name']}）")
    return 0


def cmd_delete(args, url: str, token: str):
    if not args.yes:
        confirm = input(f"确认删除规则 {args.id}？（删除前会自动备份）[y/N] ")
        if confirm.strip().lower() not in ("y", "yes"):
            print("已取消")
            return 0
    data = api_request("DELETE", f"/rules/{args.id}", url, token)
    if args.json:
        print_json(data)
    else:
        print(f"已删除：{data['filename']}")
    return 0


def cmd_sync(args, url: str, token: str):
    data = api_request("POST", "/sync", url, token)
    if args.json:
        print_json(data)
    else:
        for name in data.get("added", []):
            print(f"+ 新增 {name}")
        for name in data.get("removed", []):
            print(f"- 移除 {name}")
        if not data.get("added") and not data.get("removed"):
            print("目录与数据库已一致，无变更")
    return 0


def cmd_logs(args, url: str, token: str):
    qs = urlencode({"page": args.page, "per_page": 20})
    data = api_request("GET", f"/logs?{qs}", url, token)
    if args.json:
        print_json(data)
    else:
        print_table(data["logs"], [("ID", "id"), ("操作", "action"), ("文件", "filename"),
                                   ("操作者", "username"), ("IP", "ip"), ("时间", "created_at")])
        print(f"\n共 {data['total']} 条，第 {data['page']}/{data['total_pages']} 页")
    return 0


# ── 参数解析 ────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rules-cli",
        description="Rules Site 命令行客户端：管理在线 Clash/Mihomo 规则",
        epilog="--url/--token/--json 写在子命令前或后均可，如"
               " rules-cli --url URL list 或 rules-cli list --url URL",
    )

    # 全局参数（子命令前的位置）
    parser.add_argument("--url", help="站点地址，如 http://localhost:8600")
    parser.add_argument("--token", help="API Token（rs_ 开头）")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON（供程序解析）")

    # 子命令内参数（default=SUPPRESS：仅在显式给出时覆盖全局值）
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--url", default=argparse.SUPPRESS, help="站点地址")
    common.add_argument("--token", default=argparse.SUPPRESS, help="API Token（rs_ 开头）")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="输出原始 JSON（供程序解析）")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("login", parents=[common],
                       help="保存并验证配置（--url/--token 也可省略，回退环境变量/已有配置）")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("status", parents=[common], help="查看当前配置与连通性")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("list", parents=[common], help="规则列表")
    p.add_argument("-q", "--q", default="", help="关键字搜索")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--all", action="store_true", help="自动翻页获取全部")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("get", parents=[common], help="查看规则详情")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("create", parents=[common], help="创建规则")
    p.add_argument("file", help="本地 YAML 规则文件路径")
    p.add_argument("--name", help="显示名称（默认取文件名）")
    p.add_argument("--desc", default="", help="描述")
    p.add_argument("--filename", help="上传后的文件名（默认取本地文件名）")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("update", parents=[common], help="更新规则")
    p.add_argument("id", type=int)
    p.add_argument("--file", help="用本地 YAML 文件内容替换规则内容")
    p.add_argument("--content", help="直接传入 YAML 文本内容")
    p.add_argument("--name", help="新的显示名称")
    p.add_argument("--desc", help="新的描述")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("delete", parents=[common], help="删除规则")
    p.add_argument("id", type=int)
    p.add_argument("-y", "--yes", action="store_true", help="跳过确认")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("sync", parents=[common], help="扫描 data/rules/ 目录同步数据库")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("logs", parents=[common], help="审计日志")
    p.add_argument("--page", type=int, default=1)
    p.set_defaults(func=cmd_logs)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config = load_config()
    url = args.url or os.environ.get("RULES_SITE_URL") or config.get("url") or ""
    token = args.token or os.environ.get("RULES_SITE_TOKEN") or config.get("token") or ""

    try:
        if args.command == "login":
            return args.func(args)
        return args.func(args, url, token)
    except ApiError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
