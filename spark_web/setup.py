from __future__ import annotations

import getpass
import os
import platform
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .config import ENV_PATH, load_env, load_workspace_config
from .notion import NotionClient, NotionError


LOG_PATH = ENV_PATH.parent / "setup-notion.log"


def write_failure_log(message: str, details: str = "") -> None:
    """Persist setup diagnostics without writing the submitted token."""
    header = [
        "Spark Program Workbench - Notion setup failure",
        f"Time (UTC): {datetime.now(timezone.utc).isoformat()}",
        f"Python: {sys.version}",
        f"Executable: {sys.executable}",
        f"Platform: {platform.platform()}",
        "",
        message,
    ]
    if details:
        header.extend(["", "Technical details:", details])
    LOG_PATH.write_text("\n".join(header).rstrip() + "\n", encoding="utf-8")


def write_env(values: dict[str, str], path: Path = ENV_PATH) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    lines = ["# Spark Program Workbench local secrets. Do not share or commit this file.\n"]
    for key in sorted(values):
        value = str(values[key]).replace("\\", "\\\\").replace('"', '\\"').replace("\n", r"\n")
        lines.append(f'{key}="{value}"\n')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.writelines(lines)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def main() -> None:
    print("Spark Program Workbench · Notion 设置")
    print("请粘贴启用了 Notion API 权限的 Personal Access Token；输入内容不会显示。")
    print("不要填写工作区连接 token、OAuth Client Secret 或页面链接中的参数。")
    token = getpass.getpass("NOTION_TOKEN: ").strip().strip("\"'")
    if not token:
        raise SystemExit("未输入 token，设置未改变。")
    config = load_workspace_config()
    client = NotionClient(token, config["api_version"])
    try:
        user = client.me()
    except NotionError as exc:
        if "401" in str(exc):
            raise SystemExit(
                "\n✗ Notion 拒绝了这个 token（401）。\n"
                "请使用开发者工具中“个人访问令牌 / Personal access tokens”页面复制的原始 token，\n"
                "并确认该令牌启用了 Notion API；不要使用工作区连接 token。\n"
                "设置未改变，旧 .env 也没有被覆盖。"
            ) from None
        raise SystemExit(f"\n✗ Notion 连接失败：{exc}\n设置未改变。") from None
    for name, data_source_id in config["data_sources"].items():
        source = client.retrieve_data_source(data_source_id)
        properties = source.get("properties") or {}
        expected_names = set((config.get("properties", {}).get(name) or {}).values())
        missing = sorted(expected_names - set(properties))
        if missing:
            raise SystemExit(f"{name} 缺少字段: {', '.join(missing)}")
        print(f"✓ {name}: {source.get('id')}")
    select_checks = {
        "fund_allocations": [(config["properties"]["fund_allocations"]["status"], config["select_values"]["ongoing"])],
        "transactions": [(config["properties"]["transactions"]["type"], config["select_values"]["withdrawal"])],
        "ongoing_projects": [(config["properties"]["ongoing_projects"]["status"], config["select_values"]["ongoing"])],
        "completed_projects": [(config["properties"]["completed_projects"]["status"], config["select_values"]["completed"]),
                               (config["properties"]["completed_projects"]["status"], config["select_values"]["shutdown"])],
    }
    for name, checks in select_checks.items():
        source = client.retrieve_data_source(config["data_sources"][name])
        properties = source.get("properties") or {}
        for property_name, option in checks:
            prop = properties.get(property_name) or {}
            kind = prop.get("type")
            options = (prop.get(kind) or {}).get("options", []) if kind else []
            names = {str(item.get("name")) for item in options}
            if option not in names:
                raise SystemExit(f"{name}.{property_name} 缺少选项: {option}")
    values = load_env()
    values["NOTION_TOKEN"] = token
    values.setdefault("SPARK_NOTION_AUTO_EXECUTE", "1")
    values.setdefault("SPARK_WEB_PORT", "8765")
    values.setdefault("SPARK_TEAM_MODE", "0")
    write_env(values)
    print(f"✓ Notion 连接成功：{user.get('name') or user.get('id')}")
    print("✓ 已安全写入 .env。现在可双击 Launch-Web.vbs。")


def cli() -> int:
    try:
        main()
    except SystemExit as exc:
        message = str(exc.code or "设置已取消。")
        write_failure_log(message)
        print(message, file=sys.stderr, flush=True)
        print(f"\n完整错误已保存到：{LOG_PATH}", file=sys.stderr, flush=True)
        return exc.code if isinstance(exc.code, int) and exc.code else 1
    except Exception as exc:  # Keep unexpected errors visible after a double-click.
        details = traceback.format_exc()
        message = f"设置过程中发生未预期错误：{type(exc).__name__}: {exc}"
        write_failure_log(message, details)
        print(f"\n✗ {message}", file=sys.stderr, flush=True)
        print(details, file=sys.stderr, flush=True)
        print(f"完整错误已保存到：{LOG_PATH}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
