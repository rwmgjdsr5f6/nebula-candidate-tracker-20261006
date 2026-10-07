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


class WithoutFeedbackTestCase(unittest.TestCase):
    """验收固定样例：测试工程师岗有林晓和周宁，开发工程师岗有陈禾，
    三人均为 applied；林晓有两条同文评价，陈禾有一条，周宁没有。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-without-feedback-test-")
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

        self.add_feedback(self.lin_xiao["id"], "表达清楚")
        self.add_feedback(self.lin_xiao["id"], "表达清楚")
        self.add_feedback(self.chen_he["id"], "可以")

        snapshot = self.run_cli("list", "--all-positions")
        self.assertEqual(snapshot.returncode, 0, snapshot.stderr)
        self.state_snapshot = json.loads(snapshot.stdout)

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

    def add_feedback(self, candidate_id, text):
        result = self.run_cli(
            "add-feedback", "--id", str(candidate_id), "--text", text
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def list_without(self, *extra, scope="all"):
        if scope == "all":
            argv = ["list", "--all-positions", "--without-feedback"]
        else:
            argv = ["list", "--position", scope, "--without-feedback"]
        argv.extend(extra)
        return self.run_cli(*argv)

    def assert_only_zhou_ning(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        payload = result.stdout.rstrip("\n")
        self.assertNotIn("\n", payload)
        records = json.loads(result.stdout)
        self.assertEqual(records, [self.zhou_ning])
        self.assertEqual(
            set(records[0].keys()),
            {"id", "name", "email", "position", "stage"},
        )
        return records

    def assert_state_unchanged(self):
        result = self.run_cli("list", "--all-positions")
        self.assertEqual(json.loads(result.stdout), self.state_snapshot)

    # ---- 验收路径 ----

    def test_position_scope_returns_only_zhou_ning(self):
        self.assert_only_zhou_ning(
            self.list_without(scope=QA_POSITION)
        )

    def test_all_positions_scope_returns_only_zhou_ning(self):
        self.assert_only_zhou_ning(self.list_without())

    def test_results_sorted_by_global_id(self):
        records = self.assert_only_zhou_ning(self.list_without())
        self.assertEqual([r["id"] for r in records], sorted(r["id"] for r in records))

    def test_omitting_flag_keeps_legacy_behavior(self):
        """省略开关时三种记录全部返回，结果不受影响。"""
        result = self.run_cli("list", "--all-positions")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            [self.lin_xiao, self.zhou_ning, self.chen_he],
        )

    # ---- 评价增删改对结果的影响 ----

    def test_first_feedback_removes_candidate_and_last_deletion_restores(self):
        self.add_feedback(self.zhou_ning["id"], "初评")
        self.assertEqual(
            json.loads(self.list_without().stdout), []
        )

        second = self.add_feedback(self.zhou_ning["id"], "二评")
        # 仍有评价时不出现在结果中
        self.assertEqual(
            json.loads(self.list_without().stdout), []
        )

        # 只删除其中一条：仍有评价，不出现
        deleted = self.run_cli(
            "delete-feedback", "--feedback-id", str(second["id"])
        )
        self.assertEqual(deleted.returncode, 0, deleted.stderr)
        self.assertEqual(json.loads(self.list_without().stdout), [])

        # 删除最后一条后重新出现
        first_id = json.loads(
            self.run_cli(
                "list-feedback", "--id", str(self.zhou_ning["id"])
            ).stdout
        )[0]["id"]
        deleted_last = self.run_cli(
            "delete-feedback", "--feedback-id", str(first_id)
        )
        self.assertEqual(deleted_last.returncode, 0, deleted_last.stderr)
        self.assert_only_zhou_ning(self.list_without())

    def test_editing_feedback_text_keeps_candidate_hidden(self):
        feedback_id = json.loads(
            self.run_cli(
                "list-feedback", "--id", str(self.lin_xiao["id"])
            ).stdout
        )[0]["id"]
        result = self.run_cli(
            "set-feedback", "--feedback-id", str(feedback_id), "--text", "更正"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_only_zhou_ning(self.list_without())

    def test_stage_change_does_not_affect_judgment(self):
        result = self.run_cli(
            "set-stage", "--id", str(self.zhou_ning["id"]),
            "--stage", "interviewing",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.zhou_ning["stage"] = "interviewing"
        self.assert_only_zhou_ning(self.list_without())

    # ---- 与其他筛选组合 ----

    def test_combines_with_name_stage_email_as_intersection(self):
        # 姓名与“无评价”同时成立
        self.assert_only_zhou_ning(self.list_without("--name", "周宁"))
        # 林晓有评价，即使姓名匹配也不返回
        self.assertEqual(
            json.loads(self.list_without("--name", "林晓").stdout), []
        )
        # 阶段同时成立
        self.assert_only_zhou_ning(self.list_without("--stage", "applied"))
        self.assertEqual(
            json.loads(self.list_without("--stage", "hired").stdout), []
        )
        # 邮箱同时成立
        self.assert_only_zhou_ning(
            self.list_without("--email", "zhou.ning@example.test")
        )
        self.assertEqual(
            json.loads(
                self.list_without("--email", "lin.xiao@example.test").stdout
            ),
            [],
        )

    def test_same_name_candidates_judged_separately(self):
        """同名的不同候选人按各自 candidate_id 分别判断。"""
        other = self.add_candidate(
            "周宁", "zhou.ning.other@example.test", DEV_POSITION
        )
        result = self.list_without("--name", "周宁")
        self.assertEqual(
            json.loads(result.stdout), [self.zhou_ning, other]
        )

        self.add_feedback(other["id"], "x")
        result = self.list_without("--name", "周宁")
        self.assertEqual(json.loads(result.stdout), [self.zhou_ning])

    def test_feedback_id_equal_to_candidate_id_is_not_confused(self):
        """评价编号与候选人编号相同不影响归属判断（评价 1 属于林晓）。"""
        # 林晓的评价 id 为 1、2；周宁 id 为 2，不能因编号相同而误判
        self.assert_only_zhou_ning(self.list_without())

    # ---- 参数校验与用法错误 ----

    def test_scope_missing_is_usage_error(self):
        result = self.run_cli("list", "--without-feedback")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage:", result.stderr)

    def test_both_scopes_is_usage_error(self):
        result = self.run_cli(
            "list", "--position", QA_POSITION,
            "--all-positions", "--without-feedback",
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage:", result.stderr)

    def test_flag_does_not_take_a_value(self):
        for argv in (
            ["list", "--all-positions", "--without-feedback=true"],
            ["list", "--all-positions", "--without-feedback", "true"],
        ):
            with self.subTest(argv=argv):
                result = self.run_cli(*argv)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("usage:", result.stderr)

    def test_existing_field_validation_still_runs(self):
        """新开关不跳过既有字段校验，错误仍合并为单行紧凑 errors JSON。"""
        result = self.list_without("--stage", "offer")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr, '{"errors":{"stage":"invalid"}}\n'
        )

        merged = self.list_without("--name", " ", "--email", "bad")
        self.assertEqual(merged.returncode, 2)
        self.assertEqual(merged.stdout, "")
        self.assertEqual(
            merged.stderr,
            '{"errors":{"name":"required","email":"invalid"}}\n',
        )

    def test_successful_queries_do_not_modify_data(self):
        self.list_without()
        self.list_without(scope=QA_POSITION)
        self.list_without("--name", "周宁", "--stage", "applied")
        self.assert_state_unchanged()
        # 评价、阶段历史与岗位统计均不受查询影响
        feedback = self.run_cli(
            "list-feedback", "--id", str(self.lin_xiao["id"])
        )
        self.assertEqual(len(json.loads(feedback.stdout)), 2)


class WithoutFeedbackEmptyDatabaseTests(unittest.TestCase):
    """空数据库直接返回 []，不要求先有记录或评价。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-without-feedback-empty-")
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
