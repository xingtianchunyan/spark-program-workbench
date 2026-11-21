# -*- coding: utf-8 -*-
"""启动 Cloudflare 快速隧道：把本机 8765 端口暴露为公网 HTTPS 地址。

用法：python tools/cloudflared_tunnel.py
启动后公网地址打印在控制台，并写入项目根目录 tunnel.url 文件。
注意：快速隧道地址每次重启都会变化，需要同步更新 .env 的 SPARK_TEAM_HTTPS_ORIGIN。
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = Path(__file__).resolve().parent / "cloudflared.exe"
LOG = ROOT / "tunnel.log"
URL_FILE = ROOT / "tunnel.url"


def main() -> int:
    if not EXE.exists():
        print(f"缺少 {EXE.name}，请先下载 cloudflared Windows 版放到 tools 目录。")
        return 1
    LOG.unlink(missing_ok=True)
    URL_FILE.unlink(missing_ok=True)  # 避免启动等待期间被旧地址误导
    process = subprocess.Popen(
        [str(EXE), "tunnel", "--url", "http://127.0.0.1:8765", "--no-autoupdate",
         "--logfile", str(LOG), "--loglevel", "info"],
        cwd=str(ROOT))
    print("隧道启动中，正在获取公网地址…")
    url = ""
    for _ in range(60):
        time.sleep(2)
        if LOG.exists():
            match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", LOG.read_text(errors="replace"))
            if match:
                url = match.group(0)
                break
    if not url:
        print("未能获取公网地址，详见 tunnel.log")
        process.terminate()
        return 1
    URL_FILE.write_text(url + "\n", encoding="utf-8")
    print(f"\n公网地址：{url}")
    print("已写入 tunnel.url。提醒：")
    print("1. 把 .env 中 SPARK_TEAM_HTTPS_ORIGIN 更新为该地址（不改则成员无法登录）")
    print("2. 地址在隧道重启后会变化，需要重新更新并告知成员")
    print("3. 关闭本窗口即停止隧道\n")
    try:
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        return 0


if __name__ == "__main__":
    sys.exit(main())
