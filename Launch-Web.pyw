"""Windows 11 Smart App Control friendly launcher.

This file is executed by the installed Python interpreter instead of Windows
Script Host.  It keeps the server in this process, opens the browser through
spark_web.server, and retains automatic port shutdown when the last page closes.
"""
from __future__ import annotations

import os
import runpy
import secrets
import sys
import traceback
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "launch-web.log"


def show_error() -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Spark Program Workbench",
            f"本地服务未能启动。完整原因已写入：\n{LOG_PATH}",
            parent=root,
        )
        root.destroy()
    except Exception:
        pass


def main() -> None:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("SPARK_LAUNCH_NONCE", secrets.token_hex(16))
    with LOG_PATH.open("a", encoding="utf-8") as log:
        previous_stdout, previous_stderr = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = log
        try:
            sys.argv = ["spark_web.server", "--auto-shutdown"]
            runpy.run_module("spark_web.server", run_name="__main__")
        except BaseException:
            traceback.print_exc()
            log.flush()
            show_error()
        finally:
            sys.stdout, sys.stderr = previous_stdout, previous_stderr


if __name__ == "__main__":
    main()
