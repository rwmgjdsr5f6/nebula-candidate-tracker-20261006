"""list 命令可选 --limit 参数的回归测试。

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

INT64_MAX = "9223372036854775807"
INT64_MAX_PLUS_ONE = "9223372036854775808"


class ListLimitTestCase(unittest.TestCase):
    """固定验收样例：

    id 1 林晓（测试工程师，applied，已有评价），
    id 2 周宁（测试工程师，applied，无评价），
    id 3 陈禾（开发工程师，applied，无评价）。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-list-limit-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "candidates.sqlite3")

        self.lin_xiao = self.add_candidate(
            "林晓", "lin@example.test", QA_POSITION
        )
        self.zhou_ning = self.add_candidate(
            "周宁", "zhou@example.test", QA_POSITION
        )
        self.chen_he = self.add_candidate(
            "陈禾", "chen@example.test", DEV_POSITION
        )
        self.assert_add_feedback(1, "林晓的评价")

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

    def all_records(self):
        result = self.run_cli("list", "--all-positions")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def assert_state_unchanged(self):
        self.assertEqual(self.all_records(), self.state_snapshot)

    # ---- 验收样例 ----

    def test_all_positions_limit_two_returns_first_two(self):
        result = self.run_cli(
            "list", "--all-positions", "--limit", "2"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            [self.lin_xiao, self.zhou_ning],
        )

    def test_position_without_feedback_limit_one_returns_zhou_ning(self):
        result = self.run_cli(
            "list", "--position", QA_POSITION,
            "--without-feedback", "--limit", "1",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        records = json.loads(result.stdout)
        self.assertEqual(records, [self.zhou_ning])
        self.assertEqual(records[0].keys(),
                         {"id", "name", "email", "position", "stage"})

    # ---- 全局取数与截取语义 ----

    def test_limit_applies_globally_not_per_position(self):
        # 跨岗位按 id 全局升序取前 2 条，不是每个岗位各取 2 条
        result = self.run_cli(
            "list", "--all-positions", "--limit", "2"
        )
        records = json.loads(result.stdout)
        self.assertEqual([r["id"] for r in records], [1, 2])

    def test_limit_intersects_with_other_filters(self):
        # 无评价者有 id 2、3，limit 1 只取 id 2
        result = self.run_cli(
            "list", "--all-positions",
            "--without-feedback", "--limit", "1",
        )
        self.assertEqual(
            [r["id"] for r in json.loads(result.stdout)], [2]
        )

        # limit 与 name 取交集：只有陈禾命中姓名，前 1 条即陈禾
        result = self.run_cli(
            "list", "--all-positions", "--name", "陈禾", "--limit", "1"
        )
        self.assertEqual(json.loads(result.stdout), [self.chen_he])

    def test_fewer_matches_than_limit_returns_all_matches(self):
        result = self.run_cli(
            "list", "--position", QA_POSITION,
            "--without-feedback", "--limit", "10",
        )
        self.assertEqual(json.loads(result.stdout), [self.zhou_ning])

    def test_limit_equal_to_match_count_returns_all(self):
        result = self.run_cli(
            "list", "--all-positions", "--limit", "3"
        )
        self.assertEqual(
            json.loads(result.stdout),
            [self.lin_xiao, self.zhou_ning, self.chen_he],
        )

    def test_leading_zeros_accepted(self):
        result = self.run_cli(
            "list", "--all-positions", "--limit", "0002"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [r["id"] for r in json.loads(result.stdout)], [1, 2]
        )

    def test_surrounding_whitespace_is_trimmed(self):
        result = self.run_cli(
            "list", "--all-positions", "--limit", "  1\t"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [self.lin_xiao])

    def test_max_int64_accepted(self):
        result = self.run_cli(
            "list", "--all-positions", "--limit", INT64_MAX
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            [self.lin_xiao, self.zhou_ning, self.chen_he],
        )

    def test_equals_form_supported(self):
        result = self.run_cli("list", "--all-positions", "--limit=2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [r["id"] for r in json.loads(result.stdout)], [1, 2]
        )

    def test_omitting_limit_returns_everything(self):
        result = self.run_cli("list", "--all-positions")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            [self.lin_xiao, self.zhou_ning, self.chen_he],
        )

    # ---- 非法值 ----

    def assert_limit_invalid(self, raw_value, extra_args=()):
        result = self.run_cli(
            "list", "--all-positions", *extra_args, "--limit", raw_value
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        return result

    def test_invalid_limit_values(self):
        for raw_value in (
            "", "   ", "\t\n", "0", "000", "-1", "+1", "1.5",
            "12a", "1 2", "１２", INT64_MAX_PLUS_ONE,
            "999999999999999999999999999999",
        ):
            with self.subTest(raw_value=repr(raw_value)):
                result = self.assert_limit_invalid(raw_value)
                self.assertEqual(
                    result.stderr, '{"errors":{"limit":"invalid"}}\n'
                )

    def test_limit_error_merges_with_other_field_errors(self):
        # --limit 0 配合 --stage offer：limit 与 stage 的错误合并
        result = self.run_cli(
            "list", "--all-positions",
            "--limit", "0", "--stage", "offer",
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            json.loads(result.stderr),
            {"errors": {"limit": "invalid", "stage": "invalid"}},
        )
        self.assertNotIn("\n", result.stderr.strip())

    def test_invalid_limit_does_not_short_circuit_other_validation(self):
        result = self.run_cli(
            "list", "--position", "  ",
            "--limit", "x", "--email", "not-an-email",
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            json.loads(result.stderr),
            {
                "errors": {
                    "position": "required",
                    "email": "invalid",
                    "limit": "invalid",
                }
            },
        )

    # ---- 用法错误 ----

    def test_missing_limit_value_is_usage_error(self):
        result = self.run_cli("list", "--all-positions", "--limit")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage:", result.stderr)

    def test_scope_still_required_with_limit(self):
        result = self.run_cli("list", "--limit", "2")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage:", result.stderr)

    def test_scopes_still_mutually_exclusive_with_limit(self):
        result = self.run_cli(
            "list",
            "--position", QA_POSITION, "--all-positions", "--limit", "2",
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage:", result.stderr)

    # ---- 只读、一致性与空结果 ----

    def test_success_and_failure_queries_do_not_modify_data(self):
        self.run_cli("list", "--all-positions", "--limit", "1")
        self.run_cli(
            "list", "--position", QA_POSITION,
            "--without-feedback", "--limit", "1",
        )
        self.run_cli("list", "--all-positions", "--limit", "0")
        self.run_cli("list", "--all-positions", "--limit", "x")
        self.assert_state_unchanged()

    def test_results_consistent_across_restarts(self):
        first = self.run_cli("list", "--all-positions", "--limit", "2")
        second = self.run_cli("list", "--all-positions", "--limit", "2")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)


class ListLimitEmptyDatabaseTests(unittest.TestCase):
    """空数据库配合 --limit 也直接返回 []。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-list-limit-empty-")
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
            ["list", "--all-positions", "--limit", "5"],
            ["list", "--position", QA_POSITION, "--limit", "5"],
        ):
            with self.subTest(argv=argv):
                result = self.run_cli(*argv)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertEqual(result.stdout, "[]\n")


if __name__ == "__main__":
    unittest.main()
