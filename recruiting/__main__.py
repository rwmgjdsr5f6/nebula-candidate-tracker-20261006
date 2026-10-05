"""候选人登记与按岗位查询命令行入口。

用法:
    python -m recruiting --db <数据库文件> add --name <姓名> --email <邮箱> --position <岗位>
    python -m recruiting --db <数据库文件> list --position <岗位>
"""

import argparse
import json
import sqlite3
import sys

STAGE_APPLIED = "applied"

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    position TEXT NOT NULL,
    stage TEXT NOT NULL
);
"""


def validate_email(email):
    """邮箱已去除两端空白；返回错误值或 None。"""
    if not email:
        return "invalid"
    if any(ch.isspace() for ch in email):
        return "invalid"
    if email.count("@") != 1:
        return "invalid"
    local, domain = email.split("@")
    if not local or not domain:
        return "invalid"
    if "." not in domain or domain.startswith(".") or domain.endswith("."):
        return "invalid"
    return None


def connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(SCHEMA)
    return conn


def fail(errors):
    json.dump({"errors": errors}, sys.stderr, ensure_ascii=False)
    sys.stderr.write("\n")
    return 2


def cmd_add(conn, args):
    name = args.name.strip()
    email = args.email.strip()
    position = args.position.strip()

    errors = {}
    if not name:
        errors["name"] = "required"
    email_error = validate_email(email)
    if email_error:
        errors["email"] = email_error
    if not position:
        errors["position"] = "required"
    if errors:
        return fail(errors)

    cursor = conn.execute(
        "INSERT INTO candidates (name, email, position, stage) VALUES (?, ?, ?, ?)",
        (name, email, position, STAGE_APPLIED),
    )
    conn.commit()
    record = {
        "id": cursor.lastrowid,
        "name": name,
        "email": email,
        "position": position,
        "stage": STAGE_APPLIED,
    }
    json.dump(record, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def cmd_list(conn, args):
    position = args.position.strip()
    if not position:
        return fail({"position": "required"})

    rows = conn.execute(
        "SELECT id, name, email, position, stage FROM candidates "
        "WHERE position = ? ORDER BY id ASC",
        (position,),
    ).fetchall()
    records = [
        {
            "id": row[0],
            "name": row[1],
            "email": row[2],
            "position": row[3],
            "stage": row[4],
        }
        for row in rows
    ]
    json.dump(records, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="recruiting")
    parser.add_argument("--db", required=True, help="SQLite 数据库文件路径")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="登记候选人")
    add_parser.add_argument("--name", required=True)
    add_parser.add_argument("--email", required=True)
    add_parser.add_argument("--position", required=True)

    list_parser = subparsers.add_parser("list", help="按岗位查询候选人")
    list_parser.add_argument("--position", required=True)

    args = parser.parse_args(argv)
    conn = connect(args.db)
    try:
        if args.command == "add":
            return cmd_add(conn, args)
        return cmd_list(conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
