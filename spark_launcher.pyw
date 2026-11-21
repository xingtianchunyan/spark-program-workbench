# -*- coding: utf-8 -*-
"""Spark Program Workbench 一键启动器。

整合三种入口，按勾选组合执行、互不影响：
- 团队服务模式：局域网常驻服务（单独控制台窗口，关窗即停止）
- 本机快速模式：仅本机，浏览器全关后自动退出（复用 Launch-Web.pyw）
- 可选：启动前先做 Notion 首次配置（令牌写入 .env）
- 可选：启动后自动打开本机页面

GUI 模块仅在直接运行时创建，支持无头导入测试启动函数。
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk


ROOT = Path(__file__).resolve().parent
PORT = 8765


def lan_ipv4_addresses() -> list[str]:
    import socket
    addresses: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = str(info[4][0])
            if not ip.startswith("127.") and ip not in addresses:
                addresses.append(ip)
    except OSError:
        pass
    return addresses


def port_in_use(port: int = PORT) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def run_notion_setup(parent: tk.Misc | None = None) -> bool:
    """Notion 首次配置（与 Setup-Notion.pyw 相同流程）。返回是否成功。"""
    token = simpledialog.askstring(
        "Spark Program Workbench · Notion 设置",
        "请粘贴 Notion 内部连接令牌：",
        show="*",
        parent=parent,
    )
    if not token or not token.strip():
        return False
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from spark_web import setup

    setup.getpass.getpass = lambda _prompt: token.strip().strip("\"'")
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        result = setup.cli()
    if result == 0:
        try:
            setup.LOG_PATH.unlink()
        except FileNotFoundError:
            pass
        messagebox.showinfo("Spark Program Workbench",
                            "Notion 连接验证成功，密钥已安全写入 .env。", parent=parent)
        return True
    details = output.getvalue().strip()
    if setup.LOG_PATH.exists():
        details = setup.LOG_PATH.read_text(encoding="utf-8", errors="replace").strip()
    messagebox.showerror("Spark Program Workbench",
                         "Notion 设置失败。\n\n" + details[-3000:], parent=parent)
    return False


def start_team_server(open_page: bool, console: bool = True) -> subprocess.Popen:
    """团队服务模式：python -m spark_web.server（常驻、读取 .env 的 SPARK_HOST/TEAM_MODE）。"""
    args = [sys.executable, "-m", "spark_web.server"]
    if not open_page:
        args.append("--no-browser")
    flags = subprocess.CREATE_NEW_CONSOLE if console else 0
    return subprocess.Popen(args, cwd=str(ROOT), creationflags=flags)


def start_local_server(open_page: bool, console: bool = True) -> subprocess.Popen:
    """本机快速模式：复用 Launch-Web.pyw（--auto-shutdown + 日志 + 出错弹窗）。"""
    flags = subprocess.CREATE_NEW_CONSOLE if console else 0
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    args = [sys.executable, str(ROOT / "Launch-Web.pyw")]
    if not open_page:
        args.append("--no-browser")
    return subprocess.Popen(args, cwd=str(ROOT), creationflags=flags, env=env)


def start_tunnel(console: bool = True) -> subprocess.Popen:
    """公网隧道：tools/cloudflared_tunnel.py（独立控制台窗口，关窗即停止）。"""
    flags = subprocess.CREATE_NEW_CONSOLE if console else 0
    return subprocess.Popen([sys.executable, str(ROOT / "tools" / "cloudflared_tunnel.py")],
                            cwd=str(ROOT), creationflags=flags)


def existing_tunnel_url(max_age: float = 7200) -> str:
    """若 tunnel.url 中的地址是最近生成的且当前仍可达，返回该地址；否则返回空串。

    不依赖 tasklist 等子进程捕获（pythonw 下 stdout 可能为 None），
    用「文件新鲜度 + 实际探测」判断隧道是否存活。
    """
    import urllib.request
    url_file = ROOT / "tunnel.url"
    try:
        if time.time() - url_file.stat().st_mtime > max_age:
            return ""
        url = url_file.read_text(encoding="utf-8", errors="replace").strip()
        if not url.startswith("https://"):
            return ""
        with urllib.request.urlopen(url, timeout=6) as response:
            return url if response.status < 500 else ""
    except (OSError, ValueError, urllib.error.URLError):
        return ""


def read_tunnel_url(timeout: float = 150, since: float | None = None) -> str:
    """从 tunnel.url / tunnel.log 轮询读取本次快速隧道的公网地址。

    since：只接受该时间戳之后修改过的文件内容，避免读到上一次运行遗留的地址。
    """
    url_file = ROOT / "tunnel.url"
    log_file = ROOT / "tunnel.log"
    deadline = time.monotonic() + timeout
    pattern = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

    def _fresh(path: Path) -> bool:
        if since is None:
            return path.exists()
        try:
            return path.stat().st_mtime >= since
        except OSError:
            return False

    while time.monotonic() < deadline:
        if _fresh(url_file):
            url = url_file.read_text(encoding="utf-8", errors="replace").strip()
            if url:
                return url
        if _fresh(log_file):
            match = pattern.search(log_file.read_text(encoding="utf-8", errors="replace"))
            if match:
                return match.group(0)
        time.sleep(2)
    return ""


def update_https_origin(url: str, env_path: Path | None = None) -> None:
    """把 .env 的 SPARK_TEAM_HTTPS_ORIGIN 更新为当前隧道地址（登录白名单校验用）。"""
    env_path = env_path or (ROOT / ".env")
    key = "SPARK_TEAM_HTTPS_ORIGIN"
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    new_line = f"{key}={url.rstrip('/')}"
    replaced = False
    for index, line in enumerate(lines):
        if line.startswith(key + "="):
            lines[index] = new_line
            replaced = True
    if not replaced:
        lines.append(new_line)
    env_path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")


class LauncherApp:
    def __init__(self) -> None:
        os.environ.setdefault("PYTHONUTF8", "1")
        self.root = tk.Tk()
        self.root.title("Spark Program Workbench 一键启动")
        self.root.resizable(False, False)
        frame = ttk.Frame(self.root, padding=16)
        frame.grid(sticky="nsew")

        ttk.Label(frame, text="服务器模式（二选一）", font=("", 10, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w")
        self.mode = tk.StringVar(value="team")
        ttk.Radiobutton(frame, text="团队服务（推荐）：局域网可访问，常驻运行，\n        关闭其控制台窗口即停止",
                        value="team", variable=self.mode
                        ).grid(row=1, column=0, columnspan=2, sticky="w")
        ttk.Radiobutton(frame, text="本机快速：仅本机使用，浏览器全部关闭后自动退出",
                        value="local", variable=self.mode
                        ).grid(row=2, column=0, columnspan=2, sticky="w")

        self.open_page = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="启动后自动打开本机页面（http://127.0.0.1:8765）",
                        variable=self.open_page).grid(row=3, column=0, columnspan=2,
                                                      sticky="w", pady=(10, 0))
        self.notion_first = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="启动前先配置 Notion（仅首次需要，约 1 分钟）",
                        variable=self.notion_first).grid(row=4, column=0, columnspan=2,
                                                         sticky="w")
        self.tunnel = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="启动公网隧道（远程成员访问，地址每次启动会变，已自动写入 .env）",
                        variable=self.tunnel).grid(row=5, column=0, columnspan=2,
                                                   sticky="w")

        addrs = lan_ipv4_addresses()
        hint = "本机访问：http://127.0.0.1:8765/"
        if addrs:
            hint += "　成员访问：" + "　".join(f"http://{a}:8765/" for a in addrs)
        ttk.Label(frame, text=hint, foreground="#666", wraplength=440).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.status = ttk.Label(frame, text="", foreground="#2a7")
        self.status.grid(row=7, column=0, columnspan=2, sticky="w")

        self.launch_btn = ttk.Button(frame, text="启动", command=self.launch)
        self.launch_btn.grid(row=8, column=0, sticky="e", padx=(0, 8), pady=(14, 0))
        ttk.Button(frame, text="退出", command=self.root.destroy).grid(
            row=8, column=1, sticky="w", pady=(14, 0))

    def launch(self) -> None:
        if self.notion_first.get():
            if not run_notion_setup(parent=self.root):
                return  # 用户取消或配置失败：不启动服务
        if port_in_use():
            messagebox.showwarning(
                "Spark Program Workbench",
                f"端口 {PORT} 已被占用，服务可能已在运行。\n"
                "请直接在浏览器访问，或先关闭旧的控制台窗口再试。",
                parent=self.root)
            return
        open_page = self.open_page.get()
        if self.mode.get() == "team":
            start_team_server(open_page=open_page)
        else:
            # 本机快速模式由 Launch-Web.pyw 自身在启动 0.8 秒后打开页面
            start_local_server(open_page=True)
        if not self.tunnel.get():
            self.root.destroy()
            return
        self._start_tunnel_flow()

    def _start_tunnel_flow(self) -> None:
        self.status.config(text="正在获取公网隧道地址，请稍候…")
        self.launch_btn.config(state="disabled")
        self.root.update()
        try:
            started_at = time.time()
            url = existing_tunnel_url()
            if url:
                note = "检测到隧道已在运行，沿用现有地址："
            else:
                for name in ("tunnel.url", "tunnel.log"):
                    (ROOT / name).unlink(missing_ok=True)
                start_tunnel()
                url = read_tunnel_url(timeout=150, since=started_at)
                note = ""
            if url:
                update_https_origin(url)
                messagebox.showinfo(
                    "公网隧道已就绪",
                    f"{note}\n成员访问地址（已自动写入 .env）：\n\n{url}\n\n"
                    "地址在本次隧道关闭前有效；重启隧道后会变化，"
                    "到时重新勾选启动并把新链接发群即可。",
                    parent=self.root)
            else:
                messagebox.showwarning(
                    "公网隧道",
                    "未能获取公网地址，详见项目目录 tunnel.log。",
                    parent=self.root)
        except Exception:
            import traceback
            with (ROOT / "launcher.log").open("a", encoding="utf-8") as handle:
                handle.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
                handle.write(traceback.format_exc())
            messagebox.showerror(
                "公网隧道",
                "启动隧道时发生错误，详情已写入项目目录 launcher.log。",
                parent=self.root)
        finally:
            self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    LauncherApp().run()


if __name__ == "__main__":
    main()
