"""list 命令 --limit 与 --after-id 超长数字字符串的回归测试。

从项目根目录执行 `python -m unittest discover` 即可发现并运行。
每个测试都在独立的临时目录中创建全新的 SQLite 数据库，
通过子进程调用 `python -m recruiting` 公开命令，结束后清理临时目录，
不读取也不改动使用者已有的任何数据。所有人名、岗位与邮箱均为合成数据。

覆盖的分页语义与既有测试一致，只是参数换成长度 5000 左右的数字字符串：
前导零不改变数值，全零值按各参数既有规则处理，去前导零后超过
9223372036854775807 的值按对应字段的 invalid 处理。
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

LONG_ZEROS = "0" * 5000
LONG_NINES = "9" * 5000


class ListLongNumberTestCase(unittest.TestCase):
    """固定验收样例：

    id 1 林晓（测试工程师，applied，已有评价），
    id 2 周宁（测试工程师，applied，无评价），
    id 3 陈禾（开发工程师，applied，无评价）。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-list-long-number-")
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
        self.assertEqual(
            [self.lin_xiao["id"], self.zhou_ning["id"], self.chen_he["id"]],
            [1, 2, 3],
        )
        for record in (self.lin_xiao, self.zhou_ning, self.chen_he):
            self.assertEqual(record["stage"], "applied")
        self.assert_add_feedback(1, "林晓的评价")

        self.state_snapshot = self.full_state()

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

    def full_state(self):
        """候选人、评价与阶段历史的完整只读快照。"""
        candidates = self.run_cli("list", "--all-positions")
        self.assertEqual(candidates.returncode, 0, candidates.stderr)
        feedback = self.run_cli("list-feedback", "--id", "1")
        self.assertEqual(feedback.returncode, 0, feedback.stderr)
        history = [
            json.loads(
                self.run_cli("stage-history", "--id", str(candidate_id)).stdout
            )
            for candidate_id in (1, 2, 3)
        ]
        return {
            "candidates": json.loads(candidates.stdout),
            "feedback": json.loads(feedback.stdout),
            "stage_history": history,
        }

    def assert_state_unchanged(self):
        self.assertEqual(self.full_state(), self.state_snapshot)

    # ---- 结果与错误断言辅助 ----

    def assert_success(self, result, expected_records):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        records = json.loads(result.stdout)
        self.assertEqual(records, expected_records)
        self.assertEqual(
            [record["id"] for record in records],
            sorted(record["id"] for record in records),
        )
        for record in records:
            self.assertEqual(
                record.keys(), {"id", "name", "email", "position", "stage"}
            )
        return records

    def assert_invalid(self, result, expected_errors):
        # 只报告实际出错的字段：单行紧凑 errors JSON，stdout 为空，
        # 退出码为 2，且不出现回溯。
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("\n", result.stderr.strip())
        self.assertEqual(json.loads(result.stderr), {"errors": expected_errors})
        self.assertEqual(
            result.stderr,
            json.dumps(
                {"errors": expected_errors},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n",
        )

    # ---- 超长前导零不改变数值 ----

    def test_limit_long_zeros_then_two_returns_first_two(self):
        result = self.run_cli(
            "list", "--all-positions", "--limit", LONG_ZEROS + "2"
        )
        self.assert_success(result, [self.lin_xiao, self.zhou_ning])
        self.assert_state_unchanged()

    def test_after_id_long_zeros_then_one_with_limit_one_returns_zhou_ning(self):
        result = self.run_cli(
            "list", "--all-positions",
            "--after-id", LONG_ZEROS + "1", "--limit", "1",
        )
        self.assert_success(result, [self.zhou_ning])
        self.assert_state_unchanged()

    def test_surrounding_whitespace_gives_same_result(self):
        result = self.run_cli(
            "list", "--all-positions",
            "--after-id", "  " + LONG_ZEROS + "1\t", "--limit", " 1 ",
        )
        self.assert_success(result, [self.zhou_ning])

        result = self.run_cli(
            "list", "--all-positions", "--limit", "\t" + LONG_ZEROS + "2  "
        )
        self.assert_success(result, [self.lin_xiao, self.zhou_ning])
        self.assert_state_unchanged()

    # ---- 全零串 ----

    def test_all_zeros_after_id_equivalent_to_omitted(self):
        result = self.run_cli(
            "list", "--all-positions", "--after-id", LONG_ZEROS
        )
        self.assert_success(
            result, [self.lin_xiao, self.zhou_ning, self.chen_he]
        )
        self.assert_state_unchanged()

    def test_all_zeros_limit_is_invalid(self):
        result = self.run_cli(
            "list", "--all-positions", "--limit", LONG_ZEROS
        )
        self.assert_invalid(result, {"limit": "invalid"})
        self.assert_state_unchanged()

    # ---- 远超 int64 上限的纯数字串 ----

    def test_long_nines_limit_is_invalid(self):
        result = self.run_cli(
            "list", "--all-positions", "--limit", LONG_NINES
        )
        self.assert_invalid(result, {"limit": "invalid"})
        self.assert_state_unchanged()

    def test_long_nines_after_id_is_invalid(self):
        result = self.run_cli(
            "list", "--all-positions", "--after-id", LONG_NINES
        )
        self.assert_invalid(result, {"after_id": "invalid"})
        self.assert_state_unchanged()

    # ---- 前导零后接 int64 边界值 ----

    def test_long_zeros_then_int64_max_limit_returns_everything(self):
        result = self.run_cli(
            "list", "--all-positions", "--limit", LONG_ZEROS + INT64_MAX
        )
        self.assert_success(
            result, [self.lin_xiao, self.zhou_ning, self.chen_he]
        )
        self.assert_state_unchanged()

    def test_long_zeros_then_int64_max_after_id_returns_empty(self):
        result = self.run_cli(
            "list", "--all-positions", "--after-id", LONG_ZEROS + INT64_MAX
        )
        self.assert_success(result, [])
        self.assert_state_unchanged()

    def test_long_zeros_then_int64_max_plus_one_limit_is_invalid(self):
        result = self.run_cli(
            "list", "--all-positions",
            "--limit", LONG_ZEROS + INT64_MAX_PLUS_ONE,
        )
        self.assert_invalid(result, {"limit": "invalid"})
        self.assert_state_unchanged()

    def test_long_zeros_then_int64_max_plus_one_after_id_is_invalid(self):
        result = self.run_cli(
            "list", "--all-positions",
            "--after-id", LONG_ZEROS + INT64_MAX_PLUS_ONE,
        )
        self.assert_invalid(result, {"after_id": "invalid"})
        self.assert_state_unchanged()

    # ---- 多项错误合并 ----

    def test_both_oversized_values_and_bad_stage_merge_errors(self):
        result = self.run_cli(
            "list", "--all-positions",
            "--limit", LONG_NINES,
            "--after-id", LONG_NINES,
            "--stage", "offer",
        )
        self.assert_invalid(
            result,
            {"stage": "invalid", "limit": "invalid", "after_id": "invalid"},
        )
        self.assert_state_unchanged()

    # ---- 既有行为不受影响 ----

    def test_position_scope_and_normal_length_params_unchanged(self):
        result = self.run_cli(
            "list", "--position", QA_POSITION, "--limit", "1"
        )
        self.assert_success(result, [self.lin_xiao])

        result = self.run_cli(
            "list", "--all-positions", "--after-id", "1"
        )
        self.assert_success(result, [self.zhou_ning, self.chen_he])
        self.assert_state_unchanged()

    def test_results_consistent_across_reopens(self):
        argv = (
            "list", "--all-positions",
            "--after-id", LONG_ZEROS + "1", "--limit", "1",
        )
        first = self.run_cli(*argv)
        second = self.run_cli(*argv)
        self.assert_success(first, [self.zhou_ning])
        self.assertEqual(first.returncode, second.returncode)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(first.stderr, second.stderr)
        self.assert_state_unchanged()


if __name__ == "__main__":
    unittest.main()
