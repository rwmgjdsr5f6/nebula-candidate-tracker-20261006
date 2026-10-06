import argparse
import json
import re
import sqlite3
import sys

STAGE_APPLIED = "applied"
STAGES = ("applied", "interviewing", "hired", "rejected")

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    position TEXT NOT NULL,
    stage TEXT NOT NULL
)
"""

FIELDS = ("id", "name", "email", "position", "stage")


def email_is_invalid(email):
    """邮箱去空白后的校验规则，不合规返回 True。"""
    if not email:
        return True
    if any(ch.isspace() for ch in email):
        return True
    if email.count("@") != 1:
        return True
    local, _, domain = email.partition("@")
    if not local or not domain:
        return True
    if "." not in domain or domain.startswith(".") or domain.endswith("."):
        return True
    return False


def connect(db_path):
    """打开（必要时创建）数据库并确保表结构存在。"""
    conn = sqlite3.connect(db_path)
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def emit_error(errors):
    print(json.dumps({"errors": errors}, ensure_ascii=False), file=sys.stderr)


def cmd_add(conn, args):
    name = args.name.strip()
    email = args.email.strip()
    position = args.position.strip()

    errors = {}
    if not name:
        errors["name"] = "required"
    if email_is_invalid(email):
        errors["email"] = "invalid"
    if not position:
        errors["position"] = "required"
    if errors:
        emit_error(errors)
        return 2

    cursor = conn.execute(
        "INSERT INTO candidates (name, email, position, stage)"
        " VALUES (?, ?, ?, ?)",
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
    print(json.dumps(record, ensure_ascii=False))
    return 0


def cmd_list(conn, args):
    position = args.position.strip()
    stage = args.stage.strip() if args.stage is not None else None

    errors = {}
    if not position:
        errors["position"] = "required"
    if stage is not None:
        if not stage:
            errors["stage"] = "required"
        elif stage not in STAGES:
            errors["stage"] = "invalid"
    if errors:
        emit_error(errors)
        return 2

    if stage is None:
        rows = conn.execute(
            "SELECT id, name, email, position, stage FROM candidates"
            " WHERE position = ? ORDER BY id ASC",
            (position,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, name, email, position, stage FROM candidates"
            " WHERE position = ? AND stage = ? ORDER BY id ASC",
            (position, stage),
        ).fetchall()
    records = [dict(zip(FIELDS, row)) for row in rows]
    print(json.dumps(records, ensure_ascii=False))
    return 0


def cmd_set_stage(conn, args):
    candidate_id_raw = args.id.strip()
    stage = args.stage.strip()

    errors = {}
    if not re.fullmatch(r"[0-9]+", candidate_id_raw) or int(candidate_id_raw) < 1:
        errors["id"] = "invalid"
    if not stage:
        errors["stage"] = "required"
    elif stage not in STAGES:
        errors["stage"] = "invalid"
    if errors:
        emit_error(errors)
        return 2

    candidate_id = int(candidate_id_raw)
    row = conn.execute(
        "SELECT id, name, email, position, stage FROM candidates WHERE id = ?",
        (candidate_id,),
    ).fetchone()
    if row is None:
        emit_error({"id": "not_found"})
        return 2

    conn.execute("UPDATE candidates SET stage = ? WHERE id = ?", (stage, candidate_id))
    conn.commit()

    record = dict(zip(FIELDS, row))
    record["stage"] = stage
    print(json.dumps(record, ensure_ascii=False))
    return 0


def cmd_summary(conn, args):
    position = args.position.strip()

    if not position:
        emit_error({"position": "required"})
        return 2

    rows = conn.execute(
        "SELECT stage, COUNT(*) FROM candidates WHERE position = ? GROUP BY stage",
        (position,),
    ).fetchall()
    counts = {stage: 0 for stage in STAGES}
    for stage, count in rows:
        if stage in counts:
            counts[stage] = count
    total = sum(counts.values())

    result = {"position": position, "total": total, "counts": counts}
    print(json.dumps(result, ensure_ascii=False))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="recruiting")
    parser.add_argument("--db", required=True, help="SQLite 数据库文件路径")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="登记候选人")
    add_parser.add_argument("--name", required=True)
    add_parser.add_argument("--email", required=True)
    add_parser.add_argument("--position", required=True)
    add_parser.set_defaults(handler=cmd_add)

    list_parser = subparsers.add_parser("list", help="按岗位查询候选人")
    list_parser.add_argument("--position", required=True)
    list_parser.add_argument("--stage")
    list_parser.set_defaults(handler=cmd_list)

    set_stage_parser = subparsers.add_parser("set-stage", help="按 id 修改候选人阶段")
    set_stage_parser.add_argument("--id", required=True)
    set_stage_parser.add_argument("--stage", required=True)
    set_stage_parser.set_defaults(handler=cmd_set_stage)

    summary_parser = subparsers.add_parser("summary", help="按岗位汇总各阶段人数")
    summary_parser.add_argument("--position", required=True)
    summary_parser.set_defaults(handler=cmd_summary)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    conn = connect(args.db)
    try:
        return args.handler(conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
