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
);
CREATE TABLE IF NOT EXISTS stage_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL,
    from_stage TEXT NOT NULL,
    to_stage TEXT NOT NULL
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
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def emit_error(errors, compact=False):
    # 登记等既有命令沿用带空格的 JSON 文本；list 的查询参数错误按约定
    # 输出无多余空白的紧凑 JSON。
    separators = (",", ":") if compact else None
    print(
        json.dumps({"errors": errors}, ensure_ascii=False, separators=separators),
        file=sys.stderr,
    )


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
    email = args.email.strip() if args.email is not None else None
    name = args.name.strip() if args.name is not None else None

    errors = {}
    if not position:
        errors["position"] = "required"
    if name is not None and not name:
        errors["name"] = "required"
    if email is not None and email_is_invalid(email):
        errors["email"] = "invalid"
    if stage is not None:
        if not stage:
            errors["stage"] = "required"
        elif stage not in STAGES:
            errors["stage"] = "invalid"
    if errors:
        emit_error(errors, compact=True)
        return 2

    sql = "SELECT id, name, email, position, stage FROM candidates WHERE position = ?"
    params = [position]
    if name is not None:
        # 默认 BINARY 比较按 UTF-8 字节完整匹配：区分英文字母大小写、
        # 保留内部空白，不作子串或通配符匹配；重名记录全部返回。
        sql += " AND name = ?"
        params.append(name)
    if email is not None:
        sql += " AND email = ?"
        params.append(email)
    if stage is not None:
        sql += " AND stage = ?"
        params.append(stage)
    sql += " ORDER BY id ASC"
    rows = conn.execute(sql, params).fetchall()
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


def update_candidate_field(conn, candidate_id, field, value, validate_field,
                           after_update=None):
    """set-stage、set-email、set-position 与 set-name 共用的按 id 更新流程。

    先合并完成 id 与字段值的全部校验，再查找记录；任一步失败都只输出
    单行 errors JSON 到 stderr、返回 2 且不改动任何记录。成功时更新
    field 列（field 仅由两个命令处理函数以字面量传入，非用户输入），
    提交后输出更新后的完整候选人 JSON，返回 0。

    validate_field 入参为已去空白的字段值，通过返回 None，否则返回
    该字段的错误值（"required" 或 "invalid"）。after_update 可选，
    在 UPDATE 之后、提交之前以 (conn, 更新前记录 dict, 新值) 调用，
    用于在同一事务内登记阶段历史等附带写入。
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
    if after_update is not None:
        after_update(conn, dict(zip(FIELDS, row)), value)
    conn.commit()

    record = dict(zip(FIELDS, row))
    record[field] = value
    print(json.dumps(record, ensure_ascii=False))
    return 0


def cmd_get(conn, args):
    """按 id 查看单个候选人，只读查询，不改动任何记录。"""
    id_error, candidate_id_int = validate_candidate_id(args.id.strip())
    if id_error is not None:
        emit_error({"id": id_error})
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

    print(json.dumps(dict(zip(FIELDS, row)), ensure_ascii=False))
    return 0


def validate_stage(stage):
    """阶段去空白后的规则：为空返回 required，四种小写值之外返回 invalid。"""
    if not stage:
        return "required"
    if stage not in STAGES:
        return "invalid"
    return None


def record_stage_history(conn, record, new_stage):
    """阶段实际改成不同值时，按发生顺序追加一条变更前后的历史。

    重复设置当前阶段不增加历史；登记时的 applied 也不在此记录。
    """
    old_stage = record["stage"]
    if old_stage != new_stage:
        conn.execute(
            "INSERT INTO stage_history (candidate_id, from_stage, to_stage)"
            " VALUES (?, ?, ?)",
            (record["id"], old_stage, new_stage),
        )


def cmd_set_stage(conn, args):
    return update_candidate_field(
        conn,
        args.id.strip(),
        "stage",
        args.stage.strip(),
        validate_stage,
        after_update=record_stage_history,
    )


def cmd_stage_history(conn, args):
    """按 id 查看候选人的阶段变更历史，只读查询，不改动任何记录。"""
    id_error, candidate_id_int = validate_candidate_id(args.id.strip())
    if id_error is not None:
        emit_error({"id": id_error})
        return 2

    if candidate_id_int > SQLITE_INT64_MAX:
        # 超出 SQLite 整数范围的 id 必然不存在，直接按 not_found 处理，
        # 避免绑定参数时抛出 OverflowError。
        emit_error({"id": "not_found"})
        return 2
    row = conn.execute(
        "SELECT id FROM candidates WHERE id = ?",
        (candidate_id_int,),
    ).fetchone()
    if row is None:
        emit_error({"id": "not_found"})
        return 2

    rows = conn.execute(
        "SELECT from_stage, to_stage FROM stage_history"
        " WHERE candidate_id = ? ORDER BY id ASC",
        (candidate_id_int,),
    ).fetchall()
    history = [
        {"from_stage": from_stage, "to_stage": to_stage}
        for from_stage, to_stage in rows
    ]
    print(json.dumps(history, ensure_ascii=False))
    return 0


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


def validate_position(position):
    """岗位沿用登记规则：去空白后为空返回 required。"""
    if not position:
        return "required"
    return None


def cmd_set_position(conn, args):
    return update_candidate_field(
        conn,
        args.id.strip(),
        "position",
        args.position.strip(),
        validate_position,
    )


def validate_name(name):
    """姓名沿用登记规则：去空白后为空返回 required。"""
    if not name:
        return "required"
    return None


def cmd_set_name(conn, args):
    return update_candidate_field(
        conn,
        args.id.strip(),
        "name",
        args.name.strip(),
        validate_name,
    )


def summary_for_position(conn, position):
    """单个岗位的各阶段人数汇总，未出现的阶段计 0。"""
    rows = conn.execute(
        "SELECT stage, COUNT(*) FROM candidates WHERE position = ? GROUP BY stage",
        (position,),
    ).fetchall()
    counts = {stage: 0 for stage in STAGES}
    for stage, count in rows:
        if stage in counts:
            counts[stage] = count
    return {"position": position, "total": sum(counts.values()), "counts": counts}


def cmd_summary(conn, args):
    if args.all_positions:
        # 一次汇总全部岗位：按当前岗位、阶段分组，只保留至少有一名候选人的
        # 岗位；岗位更正后旧岗位若无候选人自然不再出现。默认 BINARY 分组
        # 区分大小写与内部空白；名称按 Unicode 码点升序在 Python 侧排序。
        rows = conn.execute(
            "SELECT position, stage, COUNT(*) FROM candidates"
            " GROUP BY position, stage"
        ).fetchall()
        grouped = {}
        for position, stage, count in rows:
            counts = grouped.setdefault(position, {s: 0 for s in STAGES})
            if stage in counts:
                counts[stage] = count
        results = [
            {
                "position": position,
                "total": sum(grouped[position].values()),
                "counts": grouped[position],
            }
            for position in sorted(grouped)
        ]
        print(json.dumps(results, ensure_ascii=False))
        return 0

    position = args.position.strip()

    if not position:
        emit_error({"position": "required"})
        return 2

    result = summary_for_position(conn, position)
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
    list_parser.add_argument("--name")
    list_parser.add_argument("--stage")
    list_parser.add_argument("--email")
    list_parser.set_defaults(handler=cmd_list)

    get_parser = subparsers.add_parser("get", help="按 id 查看单个候选人")
    get_parser.add_argument("--id", required=True)
    get_parser.set_defaults(handler=cmd_get)

    set_stage_parser = subparsers.add_parser("set-stage", help="按 id 修改候选人阶段")
    set_stage_parser.add_argument("--id", required=True)
    set_stage_parser.add_argument("--stage", required=True)
    set_stage_parser.set_defaults(handler=cmd_set_stage)

    stage_history_parser = subparsers.add_parser(
        "stage-history", help="按 id 查看候选人阶段变更历史"
    )
    stage_history_parser.add_argument("--id", required=True)
    stage_history_parser.set_defaults(handler=cmd_stage_history)

    set_email_parser = subparsers.add_parser("set-email", help="按 id 更正候选人邮箱")
    set_email_parser.add_argument("--id", required=True)
    set_email_parser.add_argument("--email", required=True)
    set_email_parser.set_defaults(handler=cmd_set_email)

    set_position_parser = subparsers.add_parser("set-position", help="按 id 更正候选人岗位")
    set_position_parser.add_argument("--id", required=True)
    set_position_parser.add_argument("--position", required=True)
    set_position_parser.set_defaults(handler=cmd_set_position)

    set_name_parser = subparsers.add_parser("set-name", help="按 id 更正候选人姓名")
    set_name_parser.add_argument("--id", required=True)
    set_name_parser.add_argument("--name", required=True)
    set_name_parser.set_defaults(handler=cmd_set_name)

    summary_parser = subparsers.add_parser("summary", help="按岗位汇总各阶段人数")
    # 两种模式互斥且必须恰好提供其一：argparse 对同时提供或都未提供的情况
    # 输出用法说明到 stderr 并以退出码 2 结束，stdout 为空。
    summary_group = summary_parser.add_mutually_exclusive_group(required=True)
    summary_group.add_argument("--position", help="汇总指定岗位")
    summary_group.add_argument(
        "--all-positions",
        action="store_true",
        help="一次汇总全部至少有一名候选人的岗位",
    )
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
