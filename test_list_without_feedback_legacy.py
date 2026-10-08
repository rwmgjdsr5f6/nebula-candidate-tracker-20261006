"""list 命令 --without-feedback 开关的旧数据库兼容性回归测试。

与 test_list_without_feedback.py 的区别：那里的数据库通过公开命令逐条
登记生成，本表结构在首次调用时即与新版一致；本模块手工构造一个“旧版”
SQLite 数据库——只有 candidates 表（字段与现有格式一致），初始不含
feedback 表与 stage_history 表——验证开关在旧库上查询、评价增删、
用法错误与跨进程持久化等行为。

从项目根目录执行 `python -m unittest discover` 即可发现并运行。
所有数据均为固定合成记录，期望值由这组记录直接写明（不是运行实际
命令后照抄输出）。每个测试使用独立临时目录，通过子进程调用
`python -m recruiting`，结束后清理临时目录，不读取也不改动使用者
已有的任何数据库。
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

QA_POSITION = "测试工程师"
DEV_POSITION = "开发工程师"
SHARED_EMAIL = "shared@example.test"

# 固定合成记录：旧库 candidates 表中只有这两人，编号刻意不连续，
# 且两人共用同一邮箱。期望值（含字段集合与排列顺序）由这组记录明确
# 给出，命令输出必须与这些字面量一致，而不是反过来照抄输出。
LIN_XIAO = {
    "id": 2,
    "name": "林晓",
    "email": SHARED_EMAIL,
    "position": QA_POSITION,
    "stage": "interviewing",
}
ZHOU_NING = {
    "id": 7,
    "name": "周宁",
    "email": SHARED_EMAIL,
    "position": DEV_POSITION,
    "stage": "applied",
}

CANDIDATE_FIELDS = ("id", "name", "email", "position", "stage")

FEEDBACK_TEXT = "一面反馈：表达清楚"

# 旧版 candidates 表 DDL：列定义与现有 SCHEMA 完全一致，但旧库中
# 尚不存在 feedback 与 stage_history 两张表。
LEGACY_CANDIDATES_DDL = """
CREATE TABLE candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    position TEXT NOT NULL,
    stage TEXT NOT NULL
)
"""


def build_legacy_database(db_path):
    """手工创建只有 candidates 表的旧数据库并写入固定合成记录。

    直接使用标准库 sqlite3 写入，全程不调用 recruiting 命令，以保证
    起始状态确实停留在旧表结构。
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(LEGACY_CANDIDATES_DDL)
        conn.executemany(
            "INSERT INTO candidates (id, name, email, position, stage)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (
                    LIN_XIAO["id"],
                    LIN_XIAO["name"],
                    LIN_XIAO["email"],
                    LIN_XIAO["position"],
                    LIN_XIAO["stage"],
                ),
                (
                    ZHOU_NING["id"],
                    ZHOU_NING["name"],
                    ZHOU_NING["email"],
                    ZHOU_NING["position"],
                    ZHOU_NING["stage"],
                ),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def user_table_names(db_path):
    """返回数据库中的用户表名（排除 sqlite_sequence 等内部表）。"""
    conn = sqlite3.connect(db_path)
    try:
        return [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master"
                " WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                " ORDER BY name"
            )
        ]
    finally:
        conn.close()


class WithoutFeedbackLegacyDatabaseTestCase(unittest.TestCase):
    """旧库（只有 candidates 表）上 --without-feedback 的端到端行为。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-legacy-wf-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        # 数据库文件名固定为 legacy.sqlite3，与使用场景描述一致。
        self.db_path = os.path.join(self.tmpdir, "legacy.sqlite3")
        build_legacy_database(self.db_path)

    # ---- 命令调用与解析辅助 ----

    def run_cli(self, *argv):
        # 每次调用都是全新进程并重新打开同一数据库文件，因此所有步骤
        # 天然验证跨进程持久化，而不是同一连接内的内存状态。
        return subprocess.run(
            [sys.executable, "-m", "recruiting", "--db", self.db_path, *argv],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )

    def list_without_feedback(self, scope):
        if scope == "all":
            argv = ["list", "--all-positions", "--without-feedback"]
        else:
            argv = ["list", "--position", scope, "--without-feedback"]
        return self.run_cli(*argv)

    def list_plain(self, scope):
        if scope == "all":
            argv = ["list", "--all-positions"]
        else:
            argv = ["list", "--position", scope]
        return self.run_cli(*argv)

    def get_candidate(self, candidate_id):
        return self.run_cli("get", "--id", str(candidate_id))

    def list_feedback(self, candidate_id):
        return self.run_cli("list-feedback", "--id", str(candidate_id))

    def stage_history(self, candidate_id):
        return self.run_cli("stage-history", "--id", str(candidate_id))

    def add_feedback(self, candidate_id, text):
        return self.run_cli(
            "add-feedback", "--id", str(candidate_id), "--text", text
        )

    def delete_feedback(self, feedback_id):
        return self.run_cli(
            "delete-feedback", "--feedback-id", str(feedback_id)
        )

    # ---- 断言辅助 ----

    def assert_success_query(self, result, expected_records):
        """成功查询：退出码 0、stderr 为空、stdout 为单行 JSON 数组。

        期望值与实际值逐字段比较，并核对每项恰好是候选人五字段对象。
        """
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        # 期望的标准输出逐字节等于固定记录序列化结果加单个换行；
        # 去掉该换行后不得再含换行，即结果只能是单行 JSON 数组。
        payload = result.stdout.rstrip("\n")
        self.assertNotIn("\n", payload)
        self.assertEqual(
            result.stdout,
            json.dumps(expected_records, ensure_ascii=False) + "\n",
        )
        records = json.loads(result.stdout)
        self.assertIsInstance(records, list)
        self.assertEqual(records, expected_records)
        for record in records:
            self.assertEqual(set(record.keys()), set(CANDIDATE_FIELDS))
        return records

    def assert_candidate_unchanged(self, candidate_id, expected_record):
        """get 查看候选人：五字段与固定记录逐一相符。"""
        result = self.get_candidate(candidate_id)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), expected_record)

    def assert_stage_history_empty(self, candidate_id):
        result = self.stage_history(candidate_id)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "[]\n")

    # ---- 旧库起始结构 ----

    def test_legacy_database_starts_without_feedback_or_history_tables(self):
        # 任何命令调用之前，旧库只有 candidates 一张用户表。
        self.assertEqual(user_table_names(self.db_path), ["candidates"])
        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(
                [row[1] for row in conn.execute("PRAGMA table_info(candidates)")],
                list(CANDIDATE_FIELDS),
            )
            rows = conn.execute(
                "SELECT id, name, email, position, stage"
                " FROM candidates ORDER BY id ASC"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(
            rows,
            [
                (
                    LIN_XIAO["id"],
                    LIN_XIAO["name"],
                    LIN_XIAO["email"],
                    LIN_XIAO["position"],
                    LIN_XIAO["stage"],
                ),
                (
                    ZHOU_NING["id"],
                    ZHOU_NING["name"],
                    ZHOU_NING["email"],
                    ZHOU_NING["position"],
                    ZHOU_NING["stage"],
                ),
            ],
        )

        # 首次只读查询会在旧库上补齐缺失的表，但不改动 candidates 数据。
        result = self.list_without_feedback("all")
        self.assert_success_query(result, [LIN_XIAO, ZHOU_NING])
        tables = user_table_names(self.db_path)
        self.assertIn("candidates", tables)
        self.assertIn("feedback", tables)
        self.assertIn("stage_history", tables)

    # ---- 旧库上的首次筛选 ----

    def test_all_positions_without_feedback_returns_both_sorted(self):
        result = self.list_without_feedback("all")
        records = self.assert_success_query(result, [LIN_XIAO, ZHOU_NING])
        # 按 id 全局升序：2 在 7 之前，不按岗位分组。
        self.assertEqual([r["id"] for r in records], [2, 7])

    def test_position_scope_without_feedback_returns_only_lin_xiao(self):
        result = self.list_without_feedback(QA_POSITION)
        self.assert_success_query(result, [LIN_XIAO])

        # 另一岗位同理只返回该岗位的周宁。
        other = self.list_without_feedback(DEV_POSITION)
        self.assert_success_query(other, [ZHOU_NING])

    def test_repeated_queries_are_identical_across_processes(self):
        first = self.list_without_feedback("all")
        second = self.list_without_feedback("all")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        # 两个独立进程重新打开同一数据库，标准输出逐字节一致。
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(
            first.stdout,
            json.dumps([LIN_XIAO, ZHOU_NING], ensure_ascii=False) + "\n",
        )

    # ---- 评价追加 / 删除生命周期 ----

    def test_feedback_add_and_delete_lifecycle(self):
        # 给林晓（候选人 id 2）追加一条合成评价。
        added = self.add_feedback(LIN_XIAO["id"], FEEDBACK_TEXT)
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertEqual(added.stderr, "")
        feedback_record = json.loads(added.stdout)
        # 评价编号与候选人编号分属不同命名空间：评价归属候选人 2，
        # 评价自身的编号取自追加结果而不能被当作候选人编号。
        self.assertEqual(
            set(feedback_record.keys()), {"id", "candidate_id", "text"}
        )
        self.assertEqual(feedback_record["candidate_id"], LIN_XIAO["id"])
        self.assertEqual(feedback_record["text"], FEEDBACK_TEXT)
        feedback_id = feedback_record["id"]
        self.assertNotEqual(feedback_id, LIN_XIAO["id"])

        # 追加后全岗位筛选只剩周宁；林晓所在岗位筛选返回 []。
        self.assert_success_query(
            self.list_without_feedback("all"), [ZHOU_NING]
        )
        self.assert_success_query(
            self.list_without_feedback(QA_POSITION), []
        )

        # 两人邮箱相同：评价按 candidate_id 归属，周宁不被连带排除，
        # 其名下评价仍为空。
        self.assertEqual(json.loads(self.list_feedback(7).stdout), [])
        self.assertEqual(
            json.loads(self.list_feedback(2).stdout), [feedback_record]
        )
        # 评价编号（恰好为 1）不是候选人编号：不存在 id 1 的候选人，
        # get 必须按 not_found 处理，而不能返回评价或任何候选人。
        peer = self.get_candidate(feedback_id)
        self.assertEqual(peer.returncode, 2)
        self.assertEqual(peer.stdout, "")
        self.assertIn("not_found", peer.stderr)

        # 再次在新进程中查询，追加结果跨进程保持一致。
        self.assert_success_query(
            self.list_without_feedback("all"), [ZHOU_NING]
        )

        # 追加评价不改姓名、邮箱、岗位、阶段（林晓仍是 interviewing）。
        self.assert_candidate_unchanged(LIN_XIAO["id"], LIN_XIAO)
        self.assert_candidate_unchanged(ZHOU_NING["id"], ZHOU_NING)

        # 按追加结果中的评价编号删除该评价，而不是按候选人编号删除。
        deleted = self.delete_feedback(feedback_id)
        self.assertEqual(deleted.returncode, 0, deleted.stderr)
        self.assertEqual(deleted.stderr, "")
        self.assertEqual(json.loads(deleted.stdout), feedback_record)

        # 两名候选人都仍在，删除评价没有误删候选人。
        self.assert_candidate_unchanged(LIN_XIAO["id"], LIN_XIAO)
        self.assert_candidate_unchanged(ZHOU_NING["id"], ZHOU_NING)

        # 删除最后一条评价后筛选恢复：全岗位两人，测试工程师岗位林晓。
        self.assert_success_query(
            self.list_without_feedback("all"), [LIN_XIAO, ZHOU_NING]
        )
        self.assert_success_query(
            self.list_without_feedback(QA_POSITION), [LIN_XIAO]
        )
        self.assertEqual(json.loads(self.list_feedback(2).stdout), [])

        # 整个过程未发生阶段变更，两人的阶段历史始终为空。
        self.assert_stage_history_empty(LIN_XIAO["id"])
        self.assert_stage_history_empty(ZHOU_NING["id"])

    # ---- 省略开关保持普通 list 行为 ----

    def test_plain_list_without_switch_returns_all_records(self):
        # 无评价时省略开关：两种范围均返回对应全部记录。
        self.assert_success_query(
            self.list_plain("all"), [LIN_XIAO, ZHOU_NING]
        )
        self.assert_success_query(self.list_plain(QA_POSITION), [LIN_XIAO])
        self.assert_success_query(self.list_plain(DEV_POSITION), [ZHOU_NING])

        # 林晓有评价后省略开关：结果与有无评价无关，仍是对应范围全部。
        added = self.add_feedback(LIN_XIAO["id"], FEEDBACK_TEXT)
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assert_success_query(
            self.list_plain("all"), [LIN_XIAO, ZHOU_NING]
        )
        self.assert_success_query(self.list_plain(QA_POSITION), [LIN_XIAO])
        self.assert_success_query(self.list_plain(DEV_POSITION), [ZHOU_NING])

        # 查询与追加都不改变候选人字段，阶段历史保持为空。
        self.assert_candidate_unchanged(LIN_XIAO["id"], LIN_XIAO)
        self.assert_candidate_unchanged(ZHOU_NING["id"], ZHOU_NING)
        self.assert_stage_history_empty(LIN_XIAO["id"])
        self.assert_stage_history_empty(ZHOU_NING["id"])

    # ---- 开关附带值的用法错误 ----

    def test_switch_with_value_is_usage_error_and_changes_nothing(self):
        # 空格形式与等号形式附带 true 都是命令行用法错误。
        for argv in (
            ["list", "--all-positions", "--without-feedback", "true"],
            ["list", "--all-positions", "--without-feedback=true"],
        ):
            with self.subTest(argv=argv):
                result = self.run_cli(*argv)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("usage:", result.stderr)

        # 用法错误不改动任何数据：两名候选人原样保留，两人均无评价。
        self.assert_success_query(
            self.list_plain("all"), [LIN_XIAO, ZHOU_NING]
        )
        self.assert_candidate_unchanged(LIN_XIAO["id"], LIN_XIAO)
        self.assert_candidate_unchanged(ZHOU_NING["id"], ZHOU_NING)
        self.assertEqual(json.loads(self.list_feedback(2).stdout), [])
        self.assertEqual(json.loads(self.list_feedback(7).stdout), [])
        self.assert_stage_history_empty(LIN_XIAO["id"])
        self.assert_stage_history_empty(ZHOU_NING["id"])


if __name__ == "__main__":
    unittest.main()
