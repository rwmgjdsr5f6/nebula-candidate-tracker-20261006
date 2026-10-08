"""list 命令可选 --after-id 参数的回归测试。

从项目根目录执行 `python -m unittest discover` 即可发现并运行。
每个测试都在独立的临时目录中创建全新的 SQLite 数据库，
通过子进程调用 `python -m recruiting` 公开命令，结束后清理临时目录，
不读取也不改动使用者已有的任何数据。所有人名、岗位与邮箱均为合成数据。

固定验收样例：id 1、3 属于测试工程师，id 2 属于后端工程师，
三人均为 applied 且无评价。
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
BACKEND_POSITION = "后端工程师"

INT64_MAX = "9223372036854775807"
INT64_MAX_PLUS_ONE = "9223372036854775808"


class ListAfterIdTestCase(unittest.TestCase):
    """固定验收样例：

    id 1 林晓（测试工程师，applied，无评价），
    id 2 周宁（后端工程师，applied，无评价），
    id 3 陈禾（测试工程师，applied，无评价）。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-list-after-id-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "candidates.sqlite3")

        self.lin_xiao = self.add_candidate(
            "林晓", "lin@example.test", QA_POSITION
        )
        self.zhou_ning = self.add_candidate(
            "周宁", "zhou@example.test", BACKEND_POSITION
        )
        self.chen_he = self.add_candidate(
            "陈禾", "chen@example.test", QA_POSITION
        )

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

    def add_feedback(self, candidate_id, text):
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

    # ---- 固定验收样例 ----

    def test_position_limit_one_without_after_returns_first(self):
        result = self.run_cli(
            "list", "--position", QA_POSITION, "--limit", "1"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), [self.lin_xiao])

    def test_position_limit_one_after_id_one_returns_id_three(self):
        result = self.run_cli(
            "list", "--position", QA_POSITION,
            "--limit", "1", "--after-id", "1",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        records = json.loads(result.stdout)
        self.assertEqual(records, [self.chen_he])
        self.assertEqual(
            records[0].keys(),
            {"id", "name", "email", "position", "stage"},
        )

    def test_after_last_matching_id_returns_empty(self):
        result = self.run_cli(
            "list", "--position", QA_POSITION, "--after-id", "3"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "[]\n")

    def test_after_int64_max_returns_empty(self):
        result = self.run_cli(
            "list", "--all-positions", "--after-id", INT64_MAX
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "[]\n")

    # ---- 两种范围与翻页链路 ----

    def test_all_positions_pagination_chain(self):
        page1 = self.run_cli("list", "--all-positions", "--limit", "1")
        self.assertEqual(json.loads(page1.stdout), [self.lin_xiao])
        page2 = self.run_cli(
            "list", "--all-positions", "--limit", "1", "--after-id", "1"
        )
        self.assertEqual(json.loads(page2.stdout), [self.zhou_ning])
        page3 = self.run_cli(
            "list", "--all-positions", "--limit", "1", "--after-id", "2"
        )
        self.assertEqual(json.loads(page3.stdout), [self.chen_he])
        page4 = self.run_cli(
            "list", "--all-positions", "--limit", "1", "--after-id", "3"
        )
        self.assertEqual(page4.stdout, "[]\n")

    def test_position_scope_boundary_can_skip_other_positions(self):
        # 边界 2 是后端工程师，但岗位过滤取交集后只返回 id 3 的测试工程师。
        result = self.run_cli(
            "list", "--position", QA_POSITION, "--after-id", "2"
        )
        self.assertEqual(json.loads(result.stdout), [self.chen_he])

    def test_nonexistent_boundary_still_queries(self):
        # 库里没有 id 99，边界无需对应现存记录，正常返回其后的全部记录。
        result = self.run_cli(
            "list", "--all-positions", "--after-id", "99"
        )
        self.assertEqual(json.loads(result.stdout), [])
        result = self.run_cli(
            "list", "--all-positions", "--after-id", "2"
        )
        self.assertEqual(json.loads(result.stdout), [self.chen_he])

    def test_omitting_limit_returns_everything_after_boundary(self):
        result = self.run_cli(
            "list", "--position", QA_POSITION, "--after-id", "1"
        )
        self.assertEqual(json.loads(result.stdout), [self.chen_he])

    def test_zero_after_id_equals_omitting_it(self):
        omitted = self.run_cli("list", "--all-positions")
        zeroed = self.run_cli(
            "list", "--all-positions", "--after-id", "0"
        )
        self.assertEqual(zeroed.returncode, 0, zeroed.stderr)
        self.assertEqual(zeroed.stdout, omitted.stdout)

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

    def test_omitting_after_id_returns_everything(self):
        result = self.run_cli("list", "--all-positions")
        self.assertEqual(
            json.loads(result.stdout),
            [self.lin_xiao, self.zhou_ning, self.chen_he],
        )

    # ---- 与其他筛选条件取交集 ----

    def test_intersects_with_name_filter(self):
        result = self.run_cli(
            "list", "--all-positions",
            "--after-id", "1", "--name", "陈禾",
        )
        self.assertEqual(json.loads(result.stdout), [self.chen_he])

    def test_intersects_with_email_filter(self):
        result = self.run_cli(
            "list", "--all-positions",
            "--after-id", "1", "--email", "zhou@example.test",
        )
        self.assertEqual(json.loads(result.stdout), [self.zhou_ning])

    def test_intersects_with_stage_filter(self):
        result = self.run_cli(
            "list", "--all-positions",
            "--after-id", "2", "--stage", "applied",
        )
        self.assertEqual(json.loads(result.stdout), [self.chen_he])

    def test_intersects_without_feedback_before_limit(self):
        # 先给 id 3 加评价：after-id 1 且无评价者只剩 id 2，limit 1 取 id 2。
        self.add_feedback(3, "陈禾的评价")
        result = self.run_cli(
            "list", "--all-positions",
            "--after-id", "1", "--without-feedback", "--limit", "1",
        )
        self.assertEqual(
            [r["id"] for r in json.loads(result.stdout)], [2]
        )

    def test_without_feedback_excludes_after_boundary(self):
        self.add_feedback(2, "周宁的评价")
        # after-id 1 后，id 2 有评价被排除，只返回无评价的 id 3。
        result = self.run_cli(
            "list", "--all-positions",
            "--after-id", "1", "--without-feedback",
        )
        self.assertEqual(
            [r["id"] for r in json.loads(result.stdout)], [3]
        )

    def test_duplicate_name_and_email_kept_separately(self):
        twin = self.add_candidate(
            "陈禾", "chen@example.test", BACKEND_POSITION
        )
        self.assertEqual(twin["id"], 4)
        # 同名同邮箱的 id 3、4 都保留，after-id 2 后按 id 升序返回两者。
        result = self.run_cli(
            "list", "--all-positions",
            "--after-id", "2", "--name", "陈禾",
            "--email", "chen@example.test",
        )
        self.assertEqual(
            [r["id"] for r in json.loads(result.stdout)], [3, 4]
        )

    # ---- 非法值 ----

    def assert_after_id_invalid(self, raw_value):
        result = self.run_cli(
            "list", "--all-positions", "--after-id", raw_value
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr, '{"errors":{"after_id":"invalid"}}\n'
        )

    def test_invalid_after_id_values(self):
        for raw_value in (
            "", "   ", "\t\n", "-1", "+1", "1.5",
            "12a", "1 2", "１２", INT64_MAX_PLUS_ONE,
            "999999999999999999999999999999", "0x1",
        ):
            with self.subTest(raw_value=repr(raw_value)):
                self.assert_after_id_invalid(raw_value)

    def test_after_id_error_merges_with_other_field_errors(self):
        # after-id 非法同时 limit 非法、stage 非法：三项合并进同一 errors。
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
                    "after_id": "invalid",
                    "limit": "invalid",
                    "stage": "invalid",
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

    # ---- 只读、一致性 ----

    def test_success_and_failure_queries_do_not_modify_data(self):
        self.run_cli("list", "--all-positions", "--after-id", "1")
        self.run_cli(
            "list", "--position", QA_POSITION,
            "--after-id", "1", "--limit", "1",
        )
        self.run_cli("list", "--all-positions", "--after-id", "x")
        self.run_cli(
            "list", "--all-positions", "--after-id", INT64_MAX
        )
        self.assert_state_unchanged()

    def test_results_consistent_across_restarts(self):
        first = self.run_cli(
            "list", "--all-positions", "--after-id", "1", "--limit", "1"
        )
        second = self.run_cli(
            "list", "--all-positions", "--after-id", "1", "--limit", "1"
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)


class ListAfterIdEmptyDatabaseTests(unittest.TestCase):
    """空数据库配合 --after-id 也直接返回 []。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-list-after-empty-")
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
            ["list", "--all-positions", "--after-id", "0"],
            ["list", "--all-positions", "--after-id", "5", "--limit", "10"],
            ["list", "--position", QA_POSITION, "--after-id", "5"],
        ):
            with self.subTest(argv=argv):
                result = self.run_cli(*argv)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertEqual(result.stdout, "[]\n")


if __name__ == "__main__":
    unittest.main()
