"""list 命令可选 --after-id 参数的回归测试。

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


class ListAfterIdTestCase(unittest.TestCase):
    """固定验收样例：

    id 1 林晓（测试工程师，applied，已有评价），
    id 2 周宁（测试工程师，applied，无评价），
    id 3 陈禾（开发工程师，applied，无评价）。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-list-after-id-test-")
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

    # ---- 边界语义 ----

    def test_position_scope_returns_records_after_boundary(self):
        result = self.run_cli(
            "list", "--position", QA_POSITION, "--after-id", "1"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), [self.zhou_ning])

    def test_all_positions_scope_returns_records_after_boundary(self):
        result = self.run_cli(
            "list", "--all-positions", "--after-id", "1"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout), [self.zhou_ning, self.chen_he]
        )

    def test_boundary_is_strictly_exclusive(self):
        # id 严格大于边界：边界记录本身不返回
        result = self.run_cli(
            "list", "--all-positions", "--after-id", "2"
        )
        self.assertEqual(json.loads(result.stdout), [self.chen_he])

    def test_boundary_need_not_match_existing_candidate(self):
        result = self.run_cli(
            "list", "--all-positions", "--after-id", "100"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "[]\n")

    def test_boundary_at_last_id_returns_empty(self):
        result = self.run_cli(
            "list", "--position", QA_POSITION, "--after-id", "3"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "[]\n")

    def test_max_int64_boundary_returns_empty(self):
        result = self.run_cli(
            "list", "--all-positions", "--after-id", INT64_MAX
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "[]\n")

    def test_zero_same_as_omitted(self):
        for raw_value in ("0", "000"):
            with self.subTest(raw_value=raw_value):
                result = self.run_cli(
                    "list", "--all-positions", "--after-id", raw_value
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    json.loads(result.stdout),
                    [self.lin_xiao, self.zhou_ning, self.chen_he],
                )

    def test_omitting_limit_returns_everything_after_boundary(self):
        result = self.run_cli(
            "list", "--all-positions", "--after-id", "1"
        )
        self.assertEqual(
            json.loads(result.stdout), [self.zhou_ning, self.chen_he]
        )

    def test_leading_zeros_accepted(self):
        result = self.run_cli(
            "list", "--all-positions", "--after-id", "0001"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [r["id"] for r in json.loads(result.stdout)], [2, 3]
        )

    def test_surrounding_whitespace_is_trimmed(self):
        result = self.run_cli(
            "list", "--all-positions", "--after-id", "  1\t"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [r["id"] for r in json.loads(result.stdout)], [2, 3]
        )

    def test_equals_form_supported(self):
        result = self.run_cli("list", "--all-positions", "--after-id=1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [r["id"] for r in json.loads(result.stdout)], [2, 3]
        )

    # ---- 与既有筛选及 limit 的交集 ----

    def test_limit_applies_after_boundary(self):
        result = self.run_cli(
            "list", "--all-positions", "--after-id", "1", "--limit", "1"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [self.zhou_ning])

    def test_intersects_with_name_filter(self):
        result = self.run_cli(
            "list", "--all-positions",
            "--name", "陈禾", "--after-id", "1",
        )
        self.assertEqual(json.loads(result.stdout), [self.chen_he])
        # 边界越过唯一姓名命中时为空
        result = self.run_cli(
            "list", "--all-positions",
            "--name", "陈禾", "--after-id", "3",
        )
        self.assertEqual(json.loads(result.stdout), [])

    def test_intersects_with_stage_and_without_feedback(self):
        # 无评价者有 id 2、3，边界 1 之后仍是两者；limit 1 只取 id 2
        result = self.run_cli(
            "list", "--all-positions",
            "--without-feedback", "--stage", "applied",
            "--after-id", "1",
        )
        self.assertEqual(
            [r["id"] for r in json.loads(result.stdout)], [2, 3]
        )
        result = self.run_cli(
            "list", "--all-positions",
            "--without-feedback", "--after-id", "1", "--limit", "1",
        )
        self.assertEqual(json.loads(result.stdout), [self.zhou_ning])

    def test_intersects_with_email_filter(self):
        result = self.run_cli(
            "list", "--all-positions",
            "--email", "zhou@example.test", "--after-id", "1",
        )
        self.assertEqual(json.loads(result.stdout), [self.zhou_ning])

    # ---- 非法值 ----

    def test_invalid_after_id_values(self):
        for raw_value in (
            "", "   ", "\t\n", "-1", "+1", "1.5", "12a", "1 2",
            "１２", INT64_MAX_PLUS_ONE,
            "999999999999999999999999999999",
        ):
            with self.subTest(raw_value=repr(raw_value)):
                result = self.run_cli(
                    "list", "--all-positions", "--after-id", raw_value
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertEqual(
                    result.stderr, '{"errors":{"after_id":"invalid"}}\n'
                )

    def test_after_id_error_merges_with_other_field_errors(self):
        result = self.run_cli(
            "list", "--all-positions",
            "--after-id", "x", "--limit", "0", "--stage", "offer",
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            json.loads(result.stderr),
            {
                "errors": {
                    "stage": "invalid",
                    "limit": "invalid",
                    "after_id": "invalid",
                }
            },
        )
        self.assertNotIn("\n", result.stderr.strip())

    def test_invalid_after_id_does_not_short_circuit_other_validation(self):
        result = self.run_cli(
            "list", "--position", "  ",
            "--after-id", "-1", "--email", "not-an-email",
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            json.loads(result.stderr),
            {
                "errors": {
                    "position": "required",
                    "email": "invalid",
                    "after_id": "invalid",
                }
            },
        )

    # ---- 用法错误 ----

    def test_missing_after_id_value_is_usage_error(self):
        result = self.run_cli("list", "--all-positions", "--after-id")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage:", result.stderr)

    def test_scope_still_required_with_after_id(self):
        result = self.run_cli("list", "--after-id", "1")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage:", result.stderr)

    def test_scopes_still_mutually_exclusive_with_after_id(self):
        result = self.run_cli(
            "list",
            "--position", QA_POSITION, "--all-positions", "--after-id", "1",
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage:", result.stderr)

    # ---- 只读、一致性与空结果 ----

    def test_success_and_failure_queries_do_not_modify_data(self):
        self.run_cli("list", "--all-positions", "--after-id", "1")
        self.run_cli(
            "list", "--position", QA_POSITION,
            "--after-id", "1", "--limit", "1",
        )
        self.run_cli("list", "--all-positions", "--after-id", "x")
        self.run_cli("list", "--all-positions", "--after-id", "-1")
        self.assert_state_unchanged()

    def test_results_consistent_across_restarts(self):
        first = self.run_cli("list", "--all-positions", "--after-id", "1")
        second = self.run_cli("list", "--all-positions", "--after-id", "1")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)


class ListAfterIdEmptyDatabaseTests(unittest.TestCase):
    """空数据库配合 --after-id 也直接返回 []。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-list-after-id-empty-")
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
            ["list", "--all-positions", "--after-id", "1"],
            ["list", "--position", QA_POSITION, "--after-id", "1"],
        ):
            with self.subTest(argv=argv):
                result = self.run_cli(*argv)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertEqual(result.stdout, "[]\n")


if __name__ == "__main__":
    unittest.main()
