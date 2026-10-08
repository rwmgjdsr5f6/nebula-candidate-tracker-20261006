"""旧数据库上 `list --without-feedback` 兼容性的回归测试。

模拟功能上线前已存在的数据库：其中只有符合当前字段格式的 candidates 表，
初始不含 feedback 与 stage_history 表。首次执行任何 recruiting 命令时由
程序自行补齐表结构，旧候选人记录保持原样。

从项目根目录执行 `python -m unittest discover` 即可发现并运行。
每个测试都在独立的临时目录中创建全新的 SQLite 数据库，全部操作通过子进程
调用 `python -m recruiting` 公开命令完成（每次调用都重新打开同一数据库），
结束后清理临时目录，不读取也不改动使用者已有的任何数据。所有人名、岗位、
邮箱与评价文字均为合成数据；预期结果由下方固定常量显式给出，不复制命令的
实际输出作为答案。
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

DB_NAME = "legacy.sqlite3"
QA_POSITION = "测试工程师"
DEV_POSITION = "开发工程师"
SHARED_EMAIL = "shared@example.test"
LIN_XIAO_FEEDBACK_TEXT = "林晓的合成评价"

# 固定合成记录：测试的全部预期值都由这两个常量明确给出。
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
EXPECTED_ALL = [LIN_XIAO, ZHOU_NING]


def create_legacy_database(db_path):
    """建立旧版本数据库：只有 candidates 表（当前五字段格式）。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE candidates ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " name TEXT NOT NULL, email TEXT NOT NULL,"
            " position TEXT NOT NULL, stage TEXT NOT NULL)"
        )
        # 显式指定非连续 id，避免依赖插入顺序推断编号。
        conn.executemany(
            "INSERT INTO candidates (id, name, email, position, stage)"
            " VALUES (:id, :name, :email, :position, :stage)",
            [LIN_XIAO, ZHOU_NING],
        )
        conn.commit()
    finally:
        conn.close()


def table_names(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()


class WithoutFeedbackLegacyDatabaseTests(unittest.TestCase):
    """旧库（无 feedback、stage_history 表）上的 --without-feedback 流程。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-list-wf-legacy-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, DB_NAME)
        create_legacy_database(self.db_path)

        # 初始确为旧库：不存在评价表与阶段历史表。
        tables = table_names(self.db_path)
        self.assertNotIn("feedback", tables)
        self.assertNotIn("stage_history", tables)

    # ---- 命令调用辅助 ----

    def run_cli(self, *argv):
        # 每条命令都是独立进程，即每次都重新打开同一个数据库文件。
        return subprocess.run(
            [sys.executable, "-m", "recruiting", "--db", self.db_path, *argv],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )

    def list_without_feedback(self, scope):
        if scope == "all":
            argv = ["list", "--all-positions"]
        else:
            argv = ["list", "--position", scope]
        argv.append("--without-feedback")
        return self.run_cli(*argv)

    def plain_list(self, scope):
        if scope == "all":
            return self.run_cli("list", "--all-positions")
        return self.run_cli("list", "--position", scope)

    def add_feedback(self, candidate_id, text):
        result = self.run_cli(
            "add-feedback", "--id", str(candidate_id), "--text", text
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def delete_feedback(self, feedback_id):
        return self.run_cli(
            "delete-feedback", "--feedback-id", str(feedback_id)
        )

    def stage_history(self, candidate_id):
        return self.run_cli("stage-history", "--id", str(candidate_id))

    def list_feedback(self, candidate_id):
        result = self.run_cli("list-feedback", "--id", str(candidate_id))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    # ---- 断言辅助 ----

    def assert_query_success(self, result, expected_records):
        """成功查询：退出码 0、stderr 为空、stdout 为单行 JSON 数组，
        每项恰好五个原字段，整体按 id 升序且与固定预期逐字段相等。"""
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        # 单行 JSON 数组：恰好一个行尾换行，载荷内部不含换行。
        self.assertTrue(result.stdout.endswith("\n"))
        payload = result.stdout[:-1]
        self.assertNotIn("\n", payload)

        records = json.loads(payload)
        self.assertIsInstance(records, list)
        self.assertEqual(records, expected_records)
        self.assertEqual(
            [record["id"] for record in records],
            sorted(record["id"] for record in records),
        )
        for record in records:
            self.assertEqual(
                set(record.keys()),
                {"id", "name", "email", "position", "stage"},
            )
        return records

    def assert_candidates_unchanged(self):
        # 查询与评价增删都不得改动候选人原有的姓名、邮箱、岗位与阶段。
        result = self.plain_list("all")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), EXPECTED_ALL)

    # ---- 首次查询：旧库直接可用 ----

    def test_first_query_all_positions_returns_both_records(self):
        # 对旧库的第一次程序访问就是该筛选查询。
        result = self.list_without_feedback("all")
        records = self.assert_query_success(result, EXPECTED_ALL)
        self.assertEqual([record["id"] for record in records], [2, 7])

    def test_first_query_position_scope_returns_only_lin_xiao(self):
        result = self.list_without_feedback(QA_POSITION)
        self.assert_query_success(result, [LIN_XIAO])

    # ---- 追加评价对筛选结果的影响 ----

    def test_adding_feedback_excludes_only_owner_candidate(self):
        feedback = self.add_feedback(LIN_XIAO["id"], LIN_XIAO_FEEDBACK_TEXT)
        # 新 feedback 表的首条评价编号为 1，与候选人编号 2 不同；归属只看
        # candidate_id。
        self.assertEqual(
            feedback,
            {
                "id": 1,
                "candidate_id": LIN_XIAO["id"],
                "text": LIN_XIAO_FEEDBACK_TEXT,
            },
        )

        # 全岗位只剩周宁：同邮箱不会让另一人被一并排除。
        self.assert_query_success(
            self.list_without_feedback("all"), [ZHOU_NING]
        )
        # 林晓所在岗位筛选返回空数组。
        self.assert_query_success(
            self.list_without_feedback(QA_POSITION), []
        )
        # 周宁所在岗位仍只返回周宁本人。
        self.assert_query_success(
            self.list_without_feedback(DEV_POSITION), [ZHOU_NING]
        )

        # 评价编号不是候选人编号：编号 1 下没有任何候选人。
        probe = self.run_cli("get", "--id", str(feedback["id"]))
        self.assertEqual(probe.returncode, 2)
        self.assertEqual(probe.stdout, "")
        self.assertEqual(
            json.loads(probe.stderr), {"errors": {"id": "not_found"}}
        )

        # 追加评价不改变两名候选人的原有字段。
        self.assert_candidates_unchanged()

    # ---- 删除评价后结果恢复 ----

    def test_deleting_feedback_restores_previous_results(self):
        feedback = self.add_feedback(LIN_XIAO["id"], LIN_XIAO_FEEDBACK_TEXT)
        self.assert_query_success(
            self.list_without_feedback("all"), [ZHOU_NING]
        )

        # 使用追加结果中带回的评价编号删除，不硬编码编号。
        deleted = self.delete_feedback(feedback["id"])
        self.assertEqual(deleted.returncode, 0, deleted.stderr)
        self.assertEqual(deleted.stderr, "")
        self.assertEqual(
            json.loads(deleted.stdout),
            {
                "id": feedback["id"],
                "candidate_id": LIN_XIAO["id"],
                "text": LIN_XIAO_FEEDBACK_TEXT,
            },
        )

        self.assert_query_success(
            self.list_without_feedback("all"), EXPECTED_ALL
        )
        self.assert_query_success(
            self.list_without_feedback(QA_POSITION), [LIN_XIAO]
        )
        # 删除评价不改变两名候选人的原有字段。
        self.assert_candidates_unchanged()

    # ---- 跨进程持久化 ----

    def test_results_persist_across_separate_processes(self):
        # 每个 run_cli 都是新进程：写入后在另一进程中立即可见。
        self.assert_query_success(
            self.list_without_feedback("all"), EXPECTED_ALL
        )

        feedback = self.add_feedback(LIN_XIAO["id"], LIN_XIAO_FEEDBACK_TEXT)
        self.assert_query_success(
            self.list_without_feedback("all"), [ZHOU_NING]
        )
        self.assert_query_success(
            self.list_without_feedback(QA_POSITION), []
        )

        deleted = self.delete_feedback(feedback["id"])
        self.assertEqual(deleted.returncode, 0, deleted.stderr)
        self.assert_query_success(
            self.list_without_feedback("all"), EXPECTED_ALL
        )
        self.assert_query_success(
            self.list_without_feedback(QA_POSITION), [LIN_XIAO]
        )

    # ---- 省略开关保持普通 list 行为 ----

    def test_omitting_switch_returns_all_records_in_scope(self):
        result_all = self.plain_list("all")
        self.assertEqual(result_all.returncode, 0, result_all.stderr)
        self.assertEqual(result_all.stderr, "")
        self.assertEqual(json.loads(result_all.stdout), EXPECTED_ALL)

        result_qa = self.plain_list(QA_POSITION)
        self.assertEqual(result_qa.returncode, 0, result_qa.stderr)
        self.assertEqual(result_qa.stderr, "")
        self.assertEqual(json.loads(result_qa.stdout), [LIN_XIAO])

        result_dev = self.plain_list(DEV_POSITION)
        self.assertEqual(result_dev.returncode, 0, result_dev.stderr)
        self.assertEqual(result_dev.stderr, "")
        self.assertEqual(json.loads(result_dev.stdout), [ZHOU_NING])

    # ---- 阶段历史始终为空 ----

    def test_stage_history_remains_empty_throughout(self):
        # 筛选查询与评价增删都不应产生任何阶段历史。
        feedback = self.add_feedback(LIN_XIAO["id"], LIN_XIAO_FEEDBACK_TEXT)
        self.list_without_feedback("all")
        deleted = self.delete_feedback(feedback["id"])
        self.assertEqual(deleted.returncode, 0, deleted.stderr)

        for candidate in EXPECTED_ALL:
            with self.subTest(candidate=candidate["id"]):
                result = self.stage_history(candidate["id"])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertEqual(json.loads(result.stdout), [])

    # ---- 开关附带值属于用法错误 ----

    def test_switch_with_value_is_usage_error_and_changes_nothing(self):
        # 先让林晓拥有一条评价，再触发用法错误。
        feedback = self.add_feedback(LIN_XIAO["id"], LIN_XIAO_FEEDBACK_TEXT)

        result = self.run_cli(
            "list", "--all-positions", "--without-feedback", "true"
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage:", result.stderr)

        # 已有候选人不变。
        self.assert_candidates_unchanged()
        # 已有评价不变：林晓的评价仍在，筛选结果仍只剩周宁。
        self.assertEqual(self.list_feedback(LIN_XIAO["id"]), [feedback])
        self.assert_query_success(
            self.list_without_feedback("all"), [ZHOU_NING]
        )
        self.assert_query_success(
            self.list_without_feedback(QA_POSITION), []
        )


if __name__ == "__main__":
    unittest.main()
