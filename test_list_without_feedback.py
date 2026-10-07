"""list 命令 --without-feedback 开关的回归测试。

从项目根目录执行 `python -m unittest discover` 即可发现并运行。
每个测试都在独立的临时目录中创建全新的 SQLite 数据库，
通过子进程调用 `python -m recruiting` 公开命令，结束后清理临时目录，
不读取也不改动使用者已有的任何数据。所有人名、岗位与邮箱均为合成数据。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

QA_POSITION = "测试工程师"
DEV_POSITION = "开发工程师"
SAME_TEXT = "同文评价"

OMITTED = object()


class WithoutFeedbackTestCase(unittest.TestCase):
    """固定验收样例：

    id 1 林晓（测试工程师，applied，两条同文评价），
    id 2 周宁（测试工程师，applied，无评价），
    id 3 陈禾（开发工程师，applied，一条评价）。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-list-wf-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "candidates.sqlite3")

        self.lin_xiao = self.add_candidate(
            "林晓", "lin.xiao@example.test", QA_POSITION
        )
        self.zhou_ning = self.add_candidate(
            "周宁", "zhou.ning@example.test", QA_POSITION
        )
        self.chen_he = self.add_candidate(
            "陈禾", "chen.he@example.test", DEV_POSITION
        )

        self.assert_add_feedback(1, SAME_TEXT)
        self.assert_add_feedback(1, SAME_TEXT)
        self.assert_add_feedback(3, "一条评价")

        self.state_snapshot = self.all_records()

    # ---- 命令调用辅助 ----

    def run_cli(self, *argv):
        return subprocess.run(
            [sys.executable, "-m", "recruiting", "--db", self.db_path, *argv],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )

    def add_candidate(self, name, email, position):
        result = self.run_cli(
            "add", "--name", name, "--email", email, "--position", position
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def assert_add_feedback(self, candidate_id, text):
        result = self.run_cli(
            "add-feedback", "--id", str(candidate_id), "--text", text
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def set_feedback(self, feedback_id, text):
        return self.run_cli(
            "set-feedback", "--feedback-id", str(feedback_id), "--text", text
        )

    def delete_feedback(self, feedback_id):
        return self.run_cli(
            "delete-feedback", "--feedback-id", str(feedback_id)
        )

    def list_without_feedback(self, scope, name=OMITTED, stage=OMITTED,
                              email=OMITTED, switch=True):
        if scope == "all":
            argv = ["list", "--all-positions"]
        else:
            argv = ["list", "--position", scope]
        if name is not OMITTED:
            argv.extend(["--name", name])
        if stage is not OMITTED:
            argv.extend(["--stage", stage])
        if email is not OMITTED:
            argv.extend(["--email", email])
        if switch:
            argv.append("--without-feedback")
        return self.run_cli(*argv)

    def all_records(self):
        result = self.run_cli("list", "--all-positions")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    # ---- 断言辅助 ----

    def assert_success_query(self, result, expected_records):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        payload = result.stdout.rstrip("\n")
        self.assertNotIn("\n", payload)
        records = json.loads(result.stdout)
        self.assertIsInstance(records, list)
        self.assertEqual(records, expected_records)
        for record in records:
            self.assertEqual(
                set(record.keys()),
                {"id", "name", "email", "position", "stage"},
            )
        return records

    def assert_usage_error(self, *argv):
        result = self.run_cli(*argv)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage:", result.stderr)

    def assert_state_unchanged(self):
        self.assertEqual(self.all_records(), self.state_snapshot)

    # ---- 验收样例 ----

    def test_position_scope_returns_only_zhou_ning(self):
        result = self.list_without_feedback(QA_POSITION)
        self.assert_success_query(result, [self.zhou_ning])

    def test_all_positions_scope_returns_only_zhou_ning(self):
        result = self.list_without_feedback("all")
        records = self.assert_success_query(result, [self.zhou_ning])
        self.assertEqual([r["id"] for r in records], [self.zhou_ning["id"]])

    def test_results_sorted_by_global_id(self):
        # 再补一名无评价候选人，验证跨岗位按 id 全局升序
        extra = self.add_candidate(
            "额外者", "extra@example.test", DEV_POSITION
        )
        result = self.list_without_feedback("all")
        records = self.assert_success_query(
            result, [self.zhou_ning, extra]
        )
        self.assertEqual(
            [r["id"] for r in records],
            sorted(r["id"] for r in records),
        )

    # ---- 评价增删改对结果的影响 ----

    def test_first_feedback_removes_candidate_from_results(self):
        self.assert_add_feedback(self.zhou_ning["id"], "首条评价")
        self.assert_success_query(self.list_without_feedback("all"), [])
        self.assert_success_query(
            self.list_without_feedback(QA_POSITION), []
        )

    def test_deleting_last_feedback_brings_candidate_back(self):
        self.assert_add_feedback(self.zhou_ning["id"], "首条评价")
        deleted = self.delete_feedback(4)
        self.assertEqual(deleted.returncode, 0, deleted.stderr)
        self.assert_success_query(
            self.list_without_feedback("all"), [self.zhou_ning]
        )

    def test_editing_text_keeps_candidate_excluded(self):
        result = self.set_feedback(3, "更正后的文字")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_success_query(
            self.list_without_feedback("all"), [self.zhou_ning]
        )

    def test_deleting_only_some_feedback_keeps_candidate_excluded(self):
        # 林晓原有两条同文评价，删除其中一条后仍有一条，不得出现
        deleted = self.delete_feedback(2)
        self.assertEqual(deleted.returncode, 0, deleted.stderr)
        records = self.assert_success_query(
            self.list_without_feedback("all"), [self.zhou_ning]
        )
        self.assertNotIn(self.lin_xiao["id"], [r["id"] for r in records])

        # 陈禾的评价更正后同样仍有评价
        edited = self.set_feedback(3, "再次更正")
        self.assertEqual(edited.returncode, 0, edited.stderr)
        self.assert_success_query(
            self.list_without_feedback("all"), [self.zhou_ning]
        )

    def test_stage_does_not_affect_judgment(self):
        result = self.run_cli(
            "set-stage", "--id", str(self.zhou_ning["id"]),
            "--stage", "hired",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.zhou_ning["stage"] = "hired"
        self.assert_success_query(
            self.list_without_feedback("all"), [self.zhou_ning]
        )

    # ---- 与其他筛选组合 ----

    def test_combines_with_name_stage_email_filters(self):
        # 所有条件同时成立才返回周宁
        result = self.list_without_feedback(
            "all", name="周宁", stage="applied",
            email="zhou.ning@example.test",
        )
        self.assert_success_query(result, [self.zhou_ning])

        # 阶段不匹配时联合结果为空
        mismatch = self.list_without_feedback(
            "all", name="周宁", stage="hired"
        )
        self.assert_success_query(mismatch, [])

        # 林晓虽无 stage/name 冲突，但有评价，不返回
        self.assert_success_query(
            self.list_without_feedback("all", name="林晓"), []
        )

    def test_same_name_candidates_judged_separately(self):
        # 同名但不同 id：给其中一人加评价不影响另一人
        other = self.add_candidate(
            "周宁", "zhou.ning.other@example.test", DEV_POSITION
        )
        result = self.list_without_feedback("all", name="周宁")
        records = self.assert_success_query(
            result, [self.zhou_ning, other]
        )

        self.assert_add_feedback(other["id"], "只属于另一个周宁")
        result = self.list_without_feedback("all", name="周宁")
        self.assert_success_query(result, [self.zhou_ning])

    def test_same_email_like_value_does_not_cross_judgment(self):
        # 评价按 candidate_id 归属而非按邮箱，两个不同 id 即使邮箱不同
        # 也互不影响；这里显式验证有评价者的邮箱不会“带累”无评价者
        result = self.list_without_feedback(
            "all", email="zhou.ning@example.test"
        )
        self.assert_success_query(result, [self.zhou_ning])

    # ---- 省略开关保持旧行为 ----

    def test_omitting_switch_keeps_legacy_results(self):
        result = self.list_without_feedback("all", switch=False)
        self.assert_success_query(
            result, [self.lin_xiao, self.zhou_ning, self.chen_he]
        )
        position_result = self.list_without_feedback(
            QA_POSITION, switch=False
        )
        self.assert_success_query(
            position_result, [self.lin_xiao, self.zhou_ning]
        )

    # ---- 用法与字段校验 ----

    def test_scope_still_required_with_switch(self):
        self.assert_usage_error("list", "--without-feedback")

    def test_scopes_still_mutually_exclusive_with_switch(self):
        self.assert_usage_error(
            "list", "--position", QA_POSITION, "--all-positions",
            "--without-feedback",
        )

    def test_switch_takes_no_value(self):
        # 位置参数形式附带值
        self.assert_usage_error(
            "list", "--all-positions", "--without-feedback", "true"
        )
        # 等号形式附带值同样是用法错误
        self.assert_usage_error(
            "list", "--all-positions", "--without-feedback=true"
        )

    def test_field_validation_not_skipped(self):
        result = self.list_without_feedback(
            "all", name="   ", stage="offer"
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        # 字节级约定：标准错误恰好一行紧凑 JSON
        self.assertEqual(
            result.stderr,
            '{"errors":{"name":"required","stage":"invalid"}}\n',
        )
        self.assert_state_unchanged()

    # ---- 只读与持久性 ----

    def test_query_does_not_modify_any_data(self):
        before_feedback = {
            cid: self.feedback_dump(cid)
            for cid in (
                self.lin_xiao["id"],
                self.zhou_ning["id"],
                self.chen_he["id"],
            )
        }
        self.list_without_feedback("all")
        self.list_without_feedback(QA_POSITION)
        self.list_without_feedback(
            "all", name="周宁", stage="applied"
        )
        self.assert_state_unchanged()
        for cid, expected in before_feedback.items():
            self.assertEqual(self.feedback_dump(cid), expected)

    def feedback_dump(self, candidate_id):
        result = self.run_cli("list-feedback", "--id", str(candidate_id))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_results_consistent_across_restarts(self):
        first = self.list_without_feedback("all")
        second = self.list_without_feedback("all")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)


class WithoutFeedbackEmptyDatabaseTests(unittest.TestCase):
    """空数据库直接返回 []，不要求先有任何记录或手工初始化。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-list-wf-empty-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "candidates.sqlite3")

    def run_cli(self, *argv):
        return subprocess.run(
            [sys.executable, "-m", "recruiting", "--db", self.db_path, *argv],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )

    def test_empty_database_returns_empty_array(self):
        for argv in (
            ["list", "--all-positions", "--without-feedback"],
            ["list", "--position", "测试工程师", "--without-feedback"],
        ):
            with self.subTest(argv=argv):
                result = self.run_cli(*argv)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertEqual(result.stdout, "[]\n")


if __name__ == "__main__":
    unittest.main()
