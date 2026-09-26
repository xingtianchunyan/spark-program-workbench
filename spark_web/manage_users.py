from __future__ import annotations

import argparse
import getpass

from .storage import Store, utcnow


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Spark Workbench users")
    parser.add_argument("username")
    parser.add_argument("--role", choices=["viewer", "operator", "approver", "administrator"], default="operator")
    parser.add_argument("--forum-username", default="")
    args = parser.parse_args()
    password = getpass.getpass("工作台密码: ")
    confirm = getpass.getpass("再次输入: ")
    if password != confirm or len(password) < 10:
        raise SystemExit("两次密码不一致，或密码少于 10 个字符")
    store = Store()
    with store.transaction() as conn:
        conn.execute("""INSERT INTO users(username,password_hash,role,forum_username,enabled,created_at)
            VALUES(?,?,?,?,1,?) ON CONFLICT(username) DO UPDATE SET
            password_hash=excluded.password_hash,role=excluded.role,
            forum_username=excluded.forum_username,enabled=1""",
            (args.username, store.hash_password(password), args.role, args.forum_username or None, utcnow()))
    print(f"已保存用户 {args.username}")


if __name__ == "__main__":
    main()
