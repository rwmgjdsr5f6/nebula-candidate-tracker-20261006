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

# SQLite 有符号整数上限；超过此值的合法 id 不可能有对应记录。
SQLITE_INT64_MAX = 9223372036854775807


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


def validate_candidate_id(candidate_id):
    """id 去空白后的规则：须为正整数（允许前导零）。

    合法返回 (None, 对应整数)；空值、全零、负数、带正号或含非 ASCII
    数字等返回 ("invalid", None)。
    """
    if not re.fullmatch(r"[0-9]+", candidate_id) or int(candidate_id) < 1:
        return "invalid", None
    return None, int(candidate_id)


def update_candidate_field(conn, candidate_id, field, value, validate_field):
    """set-stage 与 set-email 共用的按 id 更新流程。

    先合并完成 id 与字段值的全部校验，再查找记录；任一步失败都只输出
    单行 errors JSON 到 stderr、返回 2 且不改动任何记录。成功时更新
    field 列（field 仅由两个命令处理函数以字面量传入，非用户输入），
    提交后输出更新后的完整候选人 JSON，返回 0。

    validate_field 入参为已去空白的字段值，通过返回 None，否则返回
    该字段的错误值（"required" 或 "invalid"）。
    """
    errors = {}
    id_error, candidate_id_int = validate_candidate_id(candidate_id)
    if id_error is not None:
        errors["id"] = id_error
    field_error = validate_field(value)
    if field_error is not None:
        errors[field] = field_error
    if errors:
        emit_error(errors)
        return 2

    if candidate_id_int > SQLITE_INT64_MAX:
        # 超出 SQLite 整数范围的 id 必然不存在，直接按 not_found 处理，
        # 避免绑定参数时抛出 OverflowError。
        emit_error({"id": "not_found"})
        return 2
    row = conn.execute(
        "SELECT id, name, email, position, stage FROM candidates WHERE id = ?",
        (candidate_id_int,),
    ).fetchone()
    if row is None:
        emit_error({"id": "not_found"})
        return 2

    conn.execute(
        "UPDATE candidates SET {} = ? WHERE id = ?".format(field),
        (value, candidate_id_int),
    )
    conn.commit()

    record = dict(zip(FIELDS, row))
    record[field] = value
    print(json.dumps(record, ensure_ascii=False))
    return 0


def validate_stage(stage):
    """阶段去空白后的规则：为空返回 required，四种小写值之外返回 invalid。"""
    if not stage:
        return "required"
    if stage not in STAGES:
        return "invalid"
    return None


def cmd_set_stage(conn, args):
    return update_candidate_field(
        conn,
        args.id.strip(),
        "stage",
        args.stage.strip(),
        validate_stage,
    )


def validate_email(email):
    """邮箱沿用登记时的校验规则（含空值）：不合规返回 invalid。"""
    return "invalid" if email_is_invalid(email) else None


def cmd_set_email(conn, args):
    return update_candidate_field(
        conn,
        args.id.strip(),
        "email",
        args.email.strip(),
        validate_email,
    )


def cmd_get(conn, args):
    id_error, candidate_id_int = validate_candidate_id(args.id.strip())
    if id_error is not None:
        emit_error({"id": id_error})
        return 2

    if candidate_id_int > SQLITE_INT64_MAX:
        # 与修改入口一致：超出 SQLite 整数范围的 id 按 not_found 处理。
        emit_error({"id": "not_found"})
        return 2
    row = conn.execute(
        "SELECT id, name, email, position, stage FROM candidates WHERE id = ?",
        (candidate_id_int,),
    ).fetchone()
    if row is None:
        emit_error({"id": "not_found"})
        return 2

    print(json.dumps(dict(zip(FIELDS, row)), ensure_ascii=False))
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

    set_email_parser = subparsers.add_parser("set-email", help="按 id 更正候选人邮箱")
    set_email_parser.add_argument("--id", required=True)
    set_email_parser.add_argument("--email", required=True)
    set_email_parser.set_defaults(handler=cmd_set_email)

    summary_parser = subparsers.add_parser("summary", help="按岗位汇总各阶段人数")
    summary_parser.add_argument("--position", required=True)
    summary_parser.set_defaults(handler=cmd_summary)

    get_parser = subparsers.add_parser("get", help="按 id 查看单个候选人")
    get_parser.add_argument("--id", required=True)
    get_parser.set_defaults(handler=cmd_get)

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
