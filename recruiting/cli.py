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
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL,
    text TEXT NOT NULL
)
"""

FIELDS = ("id", "name", "email", "position", "stage")

# SQLite 有符号整数上限；超过此值的合法 id 不可能有对应记录。
SQLITE_INT64_MAX = 9223372036854775807
_SQLITE_INT64_MAX_DIGITS = str(SQLITE_INT64_MAX)

# 超过 int64 上限的合法编号统一返回的占位值（恒大于 SQLITE_INT64_MAX）。
# Python 3.11 起 int() 默认拒绝转换 4300 位以上的数字字符串，5000 位等
# 超长编号无法得到真实整数，而各命令在定位记录前只需要“是否越界”这一
# 信息：命中越界分支后立即按 not_found 返回，占位值不会作为 SQL 参数绑定。
OVERSIZED_ID = SQLITE_INT64_MAX + 1


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


def validate_list_number(raw_value, allow_zero):
    """list 数值参数（--limit 与 --after-id）去两端空白后的共用规则。

    只接受 ASCII 数字，允许前导零，上限为 9223372036854775807；显式空值、
    纯空白、负数、带正号、小数、内部空白、非 ASCII 数字或超出上限均返回
    ("invalid", None)，合法时返回 (None, 对应整数)。两个参数唯一的区别在
    全零值（含 "0" 与任意多个 "0"）：allow_zero 为 False 时（--limit）不是
    正整数，返回 ("invalid", None)；为 True 时（--after-id）即边界 0，
    返回 (None, 0)，与省略参数等价。
    """
    value = raw_value.strip()
    if not re.fullmatch(r"[0-9]+", value):
        return "invalid", None
    digits = value.lstrip("0")
    if not digits:
        if allow_zero:
            return None, 0
        return "invalid", None
    if len(digits) > len(_SQLITE_INT64_MAX_DIGITS) or (
        len(digits) == len(_SQLITE_INT64_MAX_DIGITS)
        and digits > _SQLITE_INT64_MAX_DIGITS
    ):
        return "invalid", None
    return None, int(digits)


def validate_limit(raw_limit):
    """list 的 --limit 规则：共用数值校验，全零值不是正整数，为 invalid。"""
    return validate_list_number(raw_limit, allow_zero=False)


def validate_after_id(raw_after_id):
    """list 的 --after-id 规则：共用数值校验，全零值即边界 0，与省略等价。"""
    return validate_list_number(raw_after_id, allow_zero=True)


def cmd_list(conn, args):
    stage = args.stage.strip() if args.stage is not None else None
    email = args.email.strip() if args.email is not None else None
    name = args.name.strip() if args.name is not None else None

    errors = {}
    # 全岗位模式不接收岗位条件，也不做岗位校验；岗位模式下去空白后
    # 不能为空。
    position = None
    if not args.all_positions:
        position = args.position.strip()
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
    limit = None
    if args.limit is not None:
        limit_error, limit = validate_limit(args.limit)
        if limit_error is not None:
            errors["limit"] = limit_error
    after_id = None
    if args.after_id is not None:
        after_id_error, after_id = validate_after_id(args.after_id)
        if after_id_error is not None:
            errors["after_id"] = after_id_error
    if errors:
        emit_error(errors, compact=True)
        return 2

    sql = "SELECT id, name, email, position, stage FROM candidates"
    clauses = []
    params = []
    if position is not None:
        clauses.append("position = ?")
        params.append(position)
    if name is not None:
        # 默认 BINARY 比较按 UTF-8 字节完整匹配：区分英文字母大小写、
        # 保留内部空白，不作子串或通配符匹配；重名记录全部返回。
        clauses.append("name = ?")
        params.append(name)
    if email is not None:
        clauses.append("email = ?")
        params.append(email)
    if stage is not None:
        clauses.append("stage = ?")
        params.append(stage)
    if args.without_feedback:
        # 评价只按 candidate_id 归属判断，与评价编号、候选人编号是否相同
        # 无关；评价全部删除后子查询无命中，候选人重新算作没有评价。阶段
        # 不参与判断，同名或同邮箱的不同候选人各自独立判定。
        clauses.append(
            "NOT EXISTS ("
            "SELECT 1 FROM feedback WHERE feedback.candidate_id = candidates.id"
            ")"
        )
    if after_id:
        # 编号边界与其他筛选条件取交集：只保留 id 严格大于边界的记录，
        # 边界编号无需对应现存候选人；0 与省略参数等价，不加条件。
        clauses.append("id > ?")
        params.append(after_id)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    # 全岗位模式同样按 id 全局升序，不按岗位分组。
    sql += " ORDER BY id ASC"
    if limit is not None:
        # 全部筛选条件取交集后，再按 id 全局升序截取前 N 条，不按岗位
        # 分别取数；匹配数不足 N 时由数据库自然返回全部匹配记录。
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    records = [dict(zip(FIELDS, row)) for row in rows]
    print(json.dumps(records, ensure_ascii=False))
    return 0


def validate_candidate_id(candidate_id):
    """id 去空白后的规则：须为正整数（允许前导零）。

    合法返回 (None, 对应整数)；空值、全零、负数、带正号或含非 ASCII
    数字等返回 ("invalid", None)。

    Python 3.11 起 int() 默认拒绝转换超过 4300 位的数字字符串（抛
    ValueError），因此先按位数与字符串比较判断与 SQLite int64 上限的
    关系，只有不超过上限的编号才调用 int()：去前导零后位数更多、或位数
    相同而字典序更大即越界，返回占位值 OVERSIZED_ID，由调用方统一按
    not_found 处理。前导零再多也不影响判定结果。
    """
    if not re.fullmatch(r"[0-9]+", candidate_id):
        return "invalid", None
    digits = candidate_id.lstrip("0")
    if not digits:
        # 全零（含 "0" 与任意多个 "0"）不是正整数。
        return "invalid", None
    if len(digits) > len(_SQLITE_INT64_MAX_DIGITS) or (
        len(digits) == len(_SQLITE_INT64_MAX_DIGITS)
        and digits > _SQLITE_INT64_MAX_DIGITS
    ):
        return None, OVERSIZED_ID
    return None, int(digits)


def resolve_candidate_id(conn, candidate_id):
    """get 与 stage-history 共用的只读定位流程。

    集中维护去两端空白后的 id 校验、SQLite 整数上限处理与记录存在性
    检查。合法且记录存在时返回 id 对应整数；任一环节失败都只向标准错误
    输出单行 errors JSON 并返回 None，由调用命令以退出码 2 返回。全程只
    读取数据，不改动任何记录。
    """
    id_error, candidate_id_int = validate_candidate_id(candidate_id.strip())
    if id_error is not None:
        emit_error({"id": id_error})
        return None

    if candidate_id_int > SQLITE_INT64_MAX:
        # 超出 SQLite 整数范围的 id 必然不存在，直接按 not_found 处理，
        # 避免绑定参数时抛出 OverflowError。
        emit_error({"id": "not_found"})
        return None

    exists = conn.execute(
        "SELECT 1 FROM candidates WHERE id = ?",
        (candidate_id_int,),
    ).fetchone()
    if exists is None:
        emit_error({"id": "not_found"})
        return None

    return candidate_id_int


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
    candidate_id_int = resolve_candidate_id(conn, args.id)
    if candidate_id_int is None:
        return 2

    row = conn.execute(
        "SELECT id, name, email, position, stage FROM candidates WHERE id = ?",
        (candidate_id_int,),
    ).fetchone()
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
    candidate_id_int = resolve_candidate_id(conn, args.id)
    if candidate_id_int is None:
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


def validate_feedback_text(text):
    """add-feedback 与 set-feedback 共用的评价文本规则。

    去除两端空白后保存；内部空白、换行、中文与英文字母大小写原样保留。
    去空白后为空（空字符串或纯空白）返回 ("required", None)，否则返回
    (None, 去空白后的文本)。
    """
    text = text.strip()
    if not text:
        return "required", None
    return None, text


def validate_feedback_payload(raw_id, id_field, raw_text):
    """add-feedback 与 set-feedback 共用的编号与文本校验流程。

    编号与文本都先去除两端空白。编号沿用 validate_candidate_id：只接受
    ASCII 数字组成的正整数（允许前导零），空值、全零、负数、带正号、
    小数或非 ASCII 数字均为 invalid；追加评价时以 "id" 为键，更正评价
    时以 "feedback_id" 为键，两个入口互不混用。文本去空白后为空时以
    "text" 报告 required。

    两项校验互不短路：编号非法且文本为空时，两项错误合并进同一个 errors
    对象。编号只要格式合法就算通过参数校验，记录是否存在留给调用方按各
    自的表查询，因此合法但不存在的编号配合空文本时只报告 text 的
    required。

    返回 (errors, 编号整数, 去空白后文本)；编号或文本校验失败时，对应
    返回值为 None。调用方只需在 errors 非空时输出单行 errors JSON 并以
    退出码 2 返回，存在性检查与写入语义仍由两条入口各自保留。
    """
    errors = {}
    id_error, target_id = validate_candidate_id(raw_id.strip())
    if id_error is not None:
        errors[id_field] = id_error
    text_error, text = validate_feedback_text(raw_text)
    if text_error is not None:
        errors["text"] = text_error
    return errors, target_id, text


def cmd_add_feedback(conn, args):
    """为已登记候选人追加一条合成评价。

    编号（候选人 id）与文本的参数校验与 set-feedback 共用
    validate_feedback_payload：任一失败都把 errors 合并为单行 JSON 输出
    到标准错误、返回 2，且不写入评价。校验通过后编号只在 candidates 表
    定位候选人，不存在（含超出 SQLite 整数上限的 9223372036854775808）
    按 id 的 not_found 处理。重复提交相同文本也插入独立记录，评价 id 由
    AUTOINCREMENT 保证唯一且递增。
    """
    errors, candidate_id_int, text = validate_feedback_payload(
        args.id, "id", args.text
    )
    if errors:
        emit_error(errors)
        return 2

    if candidate_id_int > SQLITE_INT64_MAX:
        # 超出 SQLite 整数范围的 id 必然不存在，直接按 not_found 处理，
        # 避免绑定参数时抛出 OverflowError。
        emit_error({"id": "not_found"})
        return 2
    exists = conn.execute(
        "SELECT 1 FROM candidates WHERE id = ?",
        (candidate_id_int,),
    ).fetchone()
    if exists is None:
        emit_error({"id": "not_found"})
        return 2

    cursor = conn.execute(
        "INSERT INTO feedback (candidate_id, text) VALUES (?, ?)",
        (candidate_id_int, text),
    )
    conn.commit()
    record = {
        "id": cursor.lastrowid,
        "candidate_id": candidate_id_int,
        "text": text,
    }
    print(json.dumps(record, ensure_ascii=False))
    return 0


def cmd_list_feedback(conn, args):
    """按 id 查看候选人的全部合成评价，只读查询，不改动任何记录。"""
    candidate_id_int = resolve_candidate_id(conn, args.id)
    if candidate_id_int is None:
        return 2

    rows = conn.execute(
        "SELECT id, candidate_id, text FROM feedback"
        " WHERE candidate_id = ? ORDER BY id ASC",
        (candidate_id_int,),
    ).fetchall()
    records = [
        {"id": feedback_id, "candidate_id": feedback_candidate_id, "text": text}
        for feedback_id, feedback_candidate_id, text in rows
    ]
    print(json.dumps(records, ensure_ascii=False))
    return 0


def find_feedback(conn, feedback_id_int):
    """set-feedback 与 delete-feedback 共用的评价定位流程。

    集中维护 SQLite 整数上限处理与存在性查询：超出上限的合法 id 必然
    不存在，直接按 not_found 处理，避免绑定参数时抛出 OverflowError。
    找到时返回 (id, candidate_id, text) 行；未找到时只向标准错误输出
    单行 errors JSON 并返回 None，由调用命令以退出码 2 返回。全程只
    读取数据，不改动任何记录。
    """
    if feedback_id_int > SQLITE_INT64_MAX:
        emit_error({"feedback_id": "not_found"})
        return None

    row = conn.execute(
        "SELECT id, candidate_id, text FROM feedback WHERE id = ?",
        (feedback_id_int,),
    ).fetchone()
    if row is None:
        emit_error({"feedback_id": "not_found"})
        return None

    return row


def cmd_set_feedback(conn, args):
    """按评价 id 更正一条合成评价的文字。

    feedback_id 与文本的参数校验与 add-feedback 共用
    validate_feedback_payload（编号键固定为 "feedback_id"）：任一环节
    失败都只向标准错误输出单行 errors JSON、返回 2，不改动任何记录。
    编号只要格式合法就进入评价定位（合法但不存在的编号配合空文本时，
    参数校验阶段只报告 text 的 required）。定位由 find_feedback 在
    feedback 表完成，与候选人 id 互不混用；超出 SQLite 整数上限的
    9223372036854775808 按 feedback_id 的 not_found 处理。

    成功时只替换目标评价的 text，保留 id 与 candidate_id，不新增评价；
    重复更正为当前文字也按成功处理，不改变候选人资料、阶段历史或岗位
    统计。
    """
    errors, feedback_id_int, text = validate_feedback_payload(
        args.feedback_id, "feedback_id", args.text
    )
    if errors:
        emit_error(errors)
        return 2

    row = find_feedback(conn, feedback_id_int)
    if row is None:
        return 2

    conn.execute(
        "UPDATE feedback SET text = ? WHERE id = ?",
        (text, feedback_id_int),
    )
    conn.commit()
    record = {"id": row[0], "candidate_id": row[1], "text": text}
    print(json.dumps(record, ensure_ascii=False))
    return 0


def cmd_delete_feedback(conn, args):
    """按评价 id 删除一条合成评价。

    feedback_id 沿用 set-feedback 的公开规则：去两端空白后只接受 ASCII
    数字组成的正整数（允许前导零），编号即使与某位候选人 id 相同也只
    定位评价。校验失败或评价不存在时只向标准错误输出单行 errors JSON、
    返回 2，不改动任何记录。成功时删除目标评价，输出其删除前的 id、
    candidate_id 与 text（文本保持保存时的内容，换行由 JSON 转义），
    不删除候选人，也不改变阶段历史或岗位统计。
    """
    feedback_id_error, feedback_id_int = validate_candidate_id(
        args.feedback_id.strip()
    )
    if feedback_id_error is not None:
        emit_error({"feedback_id": feedback_id_error})
        return 2

    row = find_feedback(conn, feedback_id_int)
    if row is None:
        return 2

    conn.execute("DELETE FROM feedback WHERE id = ?", (feedback_id_int,))
    conn.commit()
    record = {"id": row[0], "candidate_id": row[1], "text": row[2]}
    print(json.dumps(record, ensure_ascii=False))
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


def query_stage_counts(conn, position=None):
    """按岗位与阶段分组统计人数，返回 (position, stage, count) 行。

    position 为 None 时统计全部岗位，否则只统计该岗位。默认 BINARY 分组
    区分大小写与内部空白。只读查询，不改动任何记录。
    """
    sql = "SELECT position, stage, COUNT(*) FROM candidates"
    params = []
    if position is not None:
        sql += " WHERE position = ?"
        params.append(position)
    sql += " GROUP BY position, stage"
    return conn.execute(sql, params).fetchall()


def group_stage_counts(rows):
    """把 (position, stage, count) 行整理为 岗位 -> 四阶段计数。

    每个出现的岗位都补齐 applied、interviewing、hired、rejected 四项，
    未出现的阶段计 0；四种已知阶段之外的值不参与统计。
    """
    grouped = {}
    for position, stage, count in rows:
        counts = grouped.setdefault(position, {s: 0 for s in STAGES})
        if stage in counts:
            counts[stage] = count
    return grouped


def build_summary(position, counts):
    """单个岗位的汇总对象：total 为四阶段计数之和。"""
    return {"position": position, "total": sum(counts.values()), "counts": counts}


def summary_for_position(conn, position):
    """单个岗位的各阶段人数汇总，未出现的阶段计 0。"""
    grouped = group_stage_counts(query_stage_counts(conn, position))
    counts = grouped.get(position, {s: 0 for s in STAGES})
    return build_summary(position, counts)


def summary_for_all_positions(conn):
    """全部岗位的汇总列表，只保留至少有一名候选人的岗位。

    岗位更正后旧岗位若无候选人自然不再出现；名称按 Unicode 码点升序
    在 Python 侧排序。
    """
    grouped = group_stage_counts(query_stage_counts(conn))
    return [build_summary(position, grouped[position]) for position in sorted(grouped)]


def cmd_summary(conn, args):
    if args.all_positions:
        # 一次汇总全部仍有候选人的岗位，输出单行 JSON 数组。
        print(json.dumps(summary_for_all_positions(conn), ensure_ascii=False))
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
    # 两种范围互斥且必须恰好提供其一：argparse 对同时提供（含空岗位值
    # 与开关并用）或都未提供的情况输出用法说明到 stderr 并以退出码 2
    # 结束，stdout 为空。
    list_group = list_parser.add_mutually_exclusive_group(required=True)
    list_group.add_argument("--position", help="只查询指定岗位")
    list_group.add_argument(
        "--all-positions",
        action="store_true",
        help="跨岗位查询全部候选人，按 id 全局升序",
    )
    list_parser.add_argument("--name")
    list_parser.add_argument("--stage")
    list_parser.add_argument("--email")
    list_parser.add_argument(
        "--without-feedback",
        action="store_true",
        help="只列出当前没有任何评价归属到其 id 的候选人",
    )
    list_parser.add_argument(
        "--limit",
        help="只返回按 id 全局升序排列的前 N 条匹配记录",
    )
    list_parser.add_argument(
        "--after-id",
        help="只返回 id 严格大于所给编号的匹配记录，再按 id 全局升序应用 --limit",
    )
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

    add_feedback_parser = subparsers.add_parser(
        "add-feedback", help="按 id 为候选人追加合成评价"
    )
    add_feedback_parser.add_argument("--id", required=True)
    add_feedback_parser.add_argument("--text", required=True)
    add_feedback_parser.set_defaults(handler=cmd_add_feedback)

    list_feedback_parser = subparsers.add_parser(
        "list-feedback", help="按 id 查看候选人的合成评价"
    )
    list_feedback_parser.add_argument("--id", required=True)
    list_feedback_parser.set_defaults(handler=cmd_list_feedback)

    set_feedback_parser = subparsers.add_parser(
        "set-feedback", help="按评价 id 更正一条合成评价的文字"
    )
    set_feedback_parser.add_argument("--feedback-id", required=True)
    set_feedback_parser.add_argument("--text", required=True)
    set_feedback_parser.set_defaults(handler=cmd_set_feedback)

    delete_feedback_parser = subparsers.add_parser(
        "delete-feedback", help="按评价 id 删除一条合成评价"
    )
    delete_feedback_parser.add_argument("--feedback-id", required=True)
    delete_feedback_parser.set_defaults(handler=cmd_delete_feedback)

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
