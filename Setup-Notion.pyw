"""Graphical Notion setup entry point for Windows 11 Smart App Control."""
from __future__ import annotations

import contextlib
import io
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> None:
    import tkinter as tk
    from tkinter import messagebox, simpledialog

    root = tk.Tk()
    root.withdraw()
    token = simpledialog.askstring(
        "Spark Program Workbench · Notion 设置",
        "请粘贴 Notion 内部连接令牌：",
        show="*",
        parent=root,
    )
    if not token or not token.strip():
        root.destroy()
        return

    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from spark_web import setup

    # Reuse the same validated setup implementation without exposing the token
    # in a command line, log, console history, or dialog after submission.
    setup.getpass.getpass = lambda _prompt: token.strip().strip("\"'")
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        result = setup.cli()
    if result == 0:
        try:
            setup.LOG_PATH.unlink()
        except FileNotFoundError:
            pass
        messagebox.showinfo(
            "Spark Program Workbench",
            "Notion 连接验证成功，密钥已安全写入 .env。",
            parent=root,
        )
    else:
        details = output.getvalue().strip()
        if setup.LOG_PATH.exists():
            details = setup.LOG_PATH.read_text(encoding="utf-8", errors="replace").strip()
        messagebox.showerror(
            "Spark Program Workbench",
            "Notion 设置失败。\n\n" + details[-3000:],
            parent=root,
        )
    root.destroy()


if __name__ == "__main__":
    main()
