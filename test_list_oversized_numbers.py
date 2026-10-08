"""list 命令 --limit 与 --after-id 超长数字字符串的命令行回归测试。

现有分页测试主要覆盖普通长度输入；本模块固定 5000 位数字串（超过 Python
3.11 起 int() 默认 4300 位转换限制）在两条入口上的行为，确保共用数值校验
validate_list_number 对超长输入不抛 ValueError、不出现回溯，并保留既有分页
语义：

- 5000 个 ASCII 0 后接普通数字：前导零不影响数值（limit 2 取前两人；
  after-id 1 配合 limit 1 只取周宁），两端有空白时结果相同；
- 单独 5000 个 0：作为 --after-id 与省略等价，作为 --limit 判 invalid；
- 连续 5000 个 9：超过 SQLite int64 上限，两个参数各自判对应字段 invalid；
- 5000 个前导零包裹 9223372036854775807/9223372036854775808：前者仍为
  合法上限（limit 返回全部三人，after-id 返回 []），后者两项各自 invalid；
- 两项超长越界值同时配合非法 --stage offer：标准错误为单行紧凑 errors
  JSON，合并报告 limit、after_id、stage 三项 invalid，标准输出为空，
  退出码为 2，不出现回溯。

成功时退出码为 0、标准错误为空，数组按 id 升序并保留既有候选人字段。
每个用例在独立临时目录中准备全新的 SQLite 数据库并在结束后清理；查询前后
候选人、评价与阶段历史保持一致，重新打开数据库后结果相同。岗位范围查询、
普通长度参数以及其他公开命令的行为由本模块与既有测试共同保证。

从项目根目录执行 `python -m unittest discover` 即可发现并运行，仅使用标准
库，不读取也不改动使用者已有的任何数据。所有人名、岗位与邮箱均为合成数据。
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
APPLIED = "applied"

# 超过 Python 3.11 默认 4300 位整数转换限制的两种固定输入。
FIVE_THOUSAND_ZEROS = "0" * 5000
FIVE_THOUSAND_NINES = "9" * 5000

INT64_MAX = "9223372036854775807"
INT64_MAX_PLUS_ONE = "9223372036854775808"

CANDIDATE_FIELDS = {"id", "name", "email", "position", "stage"}


class OversizedListNumbersTestCase(unittest.TestCase):
    """固定验收样例：

    按顺序登记 id 1 林晓（测试工程师，applied，已有一条评价），
    id 2 周宁（测试工程师，applied，无评价），
    id 3 陈禾（开发工程师，applied，无评价）。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-list-oversized-test-")
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
        self.lin_feedback = self.add_feedback(1, "林晓的评价")

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
        record = json.loads(result.stdout)
        self.assertEqual(record["stage"], APPLIED)
        return record

    def add_feedback(self, candidate_id, text):
        result = self.run_cli(
            "add-feedback", "--id", str(candidate_id), "--text", text
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def list_all_positions(self):
        result = self.run_cli("list", "--all-positions")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def list_feedback(self, candidate_id):
        result = self.run_cli("list-feedback", "--id", str(candidate_id))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def stage_history(self, candidate_id):
        result = self.run_cli("stage-history", "--id", str(candidate_id))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def full_state(self):
        """候选人、全部评价与全部阶段历史的只读快照。"""
        candidates = self.list_all_positions()
        feedback = {cid: self.list_feedback(cid) for cid in (1, 2, 3)}
        history = {cid: self.stage_history(cid) for cid in (1, 2, 3)}
        return candidates, feedback, history

    def assert_state_unchanged(self):
        self.assertEqual(self.full_state(), self.state_snapshot)

    def assert_success(self, result, expected_records):
        """成功查询：退出码 0、stderr 为空、stdout 为预期记录。"""
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        records = json.loads(result.stdout)
        self.assertEqual(records, expected_records)
        return records

    def assert_errors(self, result, expected_errors, expected_stderr=None):
        """失败查询：退出码 2、stdout 为空、单行紧凑 errors JSON、无回溯。"""
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(json.loads(result.stderr), {"errors": expected_errors})
        # 紧凑 JSON：错误文本固定为 ASCII，整行不应包含任何空白。
        self.assertNotIn(" ", result.stderr)
        self.assertNotIn("\t", result.stderr)
        if expected_stderr is not None:
            self.assertEqual(result.stderr, expected_stderr)

    # ---- 5000 个前导零后接普通数字 ----

    def test_limit_5000_zeros_then_2_returns_first_two(self):
        for raw_value in (
            FIVE_THOUSAND_ZEROS + "2",
            "  " + FIVE_THOUSAND_ZEROS + "2\t",
            "\n " + FIVE_THOUSAND_ZEROS + "2 \n",
        ):
            with self.subTest(whitespace=raw_value[:6]):
                result = self.run_cli(
                    "list", "--all-positions", "--limit", raw_value
                )
                records = self.assert_success(
                    result, [self.lin_xiao, self.zhou_ning]
                )
                self.assertEqual([r["id"] for r in records], [1, 2])

    def test_after_id_5000_zeros_then_1_with_limit_1_returns_zhou_ning(self):
        for raw_after_id in (
            FIVE_THOUSAND_ZEROS + "1",
            "\t " + FIVE_THOUSAND_ZEROS + "1  ",
            " \n" + FIVE_THOUSAND_ZEROS + "1\t\n",
        ):
            with self.subTest(whitespace=raw_after_id[:6]):
                result = self.run_cli(
                    "list", "--all-positions",
                    "--after-id", raw_after_id, "--limit", "1",
                )
                records = self.assert_success(result, [self.zhou_ning])
                self.assertEqual(set(records[0]), CANDIDATE_FIELDS)

        # 两端空白同时出现在两个数值参数上时结果相同。
        result = self.run_cli(
            "list", "--all-positions",
            "--after-id", " " + FIVE_THOUSAND_ZEROS + "1 ",
            "--limit", "\t" + FIVE_THOUSAND_ZEROS + "1\n",
        )
        self.assert_success(result, [self.zhou_ning])

    # ---- 单独 5000 个 0 ----

    def test_after_id_5000_zeros_equivalent_to_omitted(self):
        expected = [self.lin_xiao, self.zhou_ning, self.chen_he]
        result = self.run_cli(
            "list", "--all-positions", "--after-id", FIVE_THOUSAND_ZEROS
        )
        self.assert_success(result, expected)
        # 与省略 --after-id 的输出逐字节相同。
        omitted = self.run_cli("list", "--all-positions")
        self.assertEqual(omitted.returncode, 0, omitted.stderr)
        self.assertEqual(result.stdout, omitted.stdout)

    def test_after_id_5000_zeros_with_surrounding_whitespace_equivalent(self):
        result = self.run_cli(
            "list", "--all-positions",
            "--after-id", " \t" + FIVE_THOUSAND_ZEROS + "\n ",
        )
        self.assert_success(
            result, [self.lin_xiao, self.zhou_ning, self.chen_he]
        )

    def test_limit_5000_zeros_is_invalid(self):
        result = self.run_cli(
            "list", "--all-positions", "--limit", FIVE_THOUSAND_ZEROS
        )
        self.assert_errors(
            result, {"limit": "invalid"},
            expected_stderr='{"errors":{"limit":"invalid"}}\n',
        )

    def test_limit_5000_zeros_with_whitespace_is_invalid(self):
        # 去空白后仍是全零，对 --limit 不是正整数。
        result = self.run_cli(
            "list", "--all-positions",
            "--limit", "  " + FIVE_THOUSAND_ZEROS + "\t",
        )
        self.assert_errors(result, {"limit": "invalid"})

    # ---- 连续 5000 个 9 ----

    def test_5000_nines_is_invalid_for_each_field(self):
        cases = [
            ("limit", ["--limit", FIVE_THOUSAND_NINES],
             '{"errors":{"limit":"invalid"}}\n'),
            ("after_id", ["--after-id", FIVE_THOUSAND_NINES],
             '{"errors":{"after_id":"invalid"}}\n'),
        ]
        for field, extra_args, expected_stderr in cases:
            with self.subTest(field=field):
                result = self.run_cli(
                    "list", "--all-positions", *extra_args
                )
                self.assert_errors(
                    result, {field: "invalid"},
                    expected_stderr=expected_stderr,
                )

    def test_5000_nines_with_whitespace_is_invalid_for_each_field(self):
        for field, option in (
            ("limit", "--limit"),
            ("after_id", "--after-id"),
        ):
            with self.subTest(field=field):
                result = self.run_cli(
                    "list", "--all-positions",
                    option, " \n" + FIVE_THOUSAND_NINES + "\t ",
                )
                self.assert_errors(result, {field: "invalid"})

    # ---- 5000 个前导零包裹 int64 上下界 ----

    def test_5000_zeros_then_int64_max_is_accepted(self):
        padded_max = FIVE_THOUSAND_ZEROS + INT64_MAX

        result = self.run_cli(
            "list", "--all-positions", "--limit", padded_max
        )
        self.assert_success(
            result, [self.lin_xiao, self.zhou_ning, self.chen_he]
        )

        result = self.run_cli(
            "list", "--all-positions", "--after-id", padded_max
        )
        self.assert_success(result, [])

    def test_5000_zeros_then_int64_max_plus_one_is_invalid(self):
        padded_over = FIVE_THOUSAND_ZEROS + INT64_MAX_PLUS_ONE

        result = self.run_cli(
            "list", "--all-positions", "--limit", padded_over
        )
        self.assert_errors(
            result, {"limit": "invalid"},
            expected_stderr='{"errors":{"limit":"invalid"}}\n',
        )

        result = self.run_cli(
            "list", "--all-positions", "--after-id", padded_over
        )
        self.assert_errors(
            result, {"after_id": "invalid"},
            expected_stderr='{"errors":{"after_id":"invalid"}}\n',
        )

    # ---- 多字段错误合并 ----

    def test_both_oversized_values_with_bad_stage_report_three_errors(self):
        result = self.run_cli(
            "list", "--all-positions",
            "--limit", FIVE_THOUSAND_NINES,
            "--after-id", FIVE_THOUSAND_NINES,
            "--stage", "offer",
        )
        # 单行紧凑 errors JSON，恰好包含 limit、after_id、stage 三项，
        # 不报告其他字段；字段插入顺序为校验顺序 stage、limit、after_id。
        self.assert_errors(
            result,
            {"limit": "invalid", "after_id": "invalid", "stage": "invalid"},
            expected_stderr=(
                '{"errors":{'
                '"stage":"invalid",'
                '"limit":"invalid",'
                '"after_id":"invalid"'
                '}}\n'
            ),
        )

    def test_errors_only_report_actual_error_fields(self):
        # 只有 limit 越界：stage 合法时不出现 stage，after-id 省略时不出现。
        result = self.run_cli(
            "list", "--all-positions",
            "--limit", FIVE_THOUSAND_NINES, "--stage", APPLIED,
        )
        self.assert_errors(result, {"limit": "invalid"})

        # 只有 after-id 越界。
        result = self.run_cli(
            "list", "--all-positions",
            "--after-id", FIVE_THOUSAND_NINES, "--stage", APPLIED,
        )
        self.assert_errors(result, {"after_id": "invalid"})

        # 只有 stage 非法时，两个合法（省略的）数值字段不被报告。
        result = self.run_cli(
            "list", "--all-positions", "--stage", "offer"
        )
        self.assert_errors(result, {"stage": "invalid"})

    # ---- 成功输出形状与排序 ----

    def test_success_payload_is_id_ascending_with_existing_fields(self):
        result = self.run_cli(
            "list", "--all-positions",
            "--after-id", FIVE_THOUSAND_ZEROS + "0",
            "--limit", FIVE_THOUSAND_ZEROS + "2",
        )
        records = self.assert_success(
            result, [self.lin_xiao, self.zhou_ning]
        )
        self.assertEqual([r["id"] for r in records], [1, 2])
        for record in records:
            self.assertEqual(set(record), CANDIDATE_FIELDS)
        # 林晓的候选人字段不携带评价，评价仍只归属于其 id。
        self.assertEqual(records[0], self.lin_xiao)

    # ---- 只读、一致性与重新打开 ----

    def test_all_success_and_failure_queries_are_read_only(self):
        succeed_args = [
            ["list", "--all-positions", "--limit", FIVE_THOUSAND_ZEROS + "2"],
            ["list", "--all-positions",
             "--after-id", FIVE_THOUSAND_ZEROS + "1", "--limit", "1"],
            ["list", "--all-positions",
             "--after-id", FIVE_THOUSAND_ZEROS],
            ["list", "--all-positions",
             "--limit", FIVE_THOUSAND_ZEROS + INT64_MAX],
            ["list", "--all-positions",
             "--after-id", FIVE_THOUSAND_ZEROS + INT64_MAX],
        ]
        fail_args = [
            ["list", "--all-positions", "--limit", FIVE_THOUSAND_ZEROS],
            ["list", "--all-positions", "--limit", FIVE_THOUSAND_NINES],
            ["list", "--all-positions", "--after-id", FIVE_THOUSAND_NINES],
            ["list", "--all-positions",
             "--limit", FIVE_THOUSAND_ZEROS + INT64_MAX_PLUS_ONE],
            ["list", "--all-positions",
             "--after-id", FIVE_THOUSAND_ZEROS + INT64_MAX_PLUS_ONE],
            ["list", "--all-positions",
             "--limit", FIVE_THOUSAND_NINES,
             "--after-id", FIVE_THOUSAND_NINES, "--stage", "offer"],
        ]
        for argv in succeed_args + fail_args:
            self.run_cli(*argv)
        self.assert_state_unchanged()

    def test_results_identical_after_reopening_database(self):
        # 每次子进程调用都会重新打开数据库文件；同一组查询分进程重复，
        # 成功输出与失败输出都必须逐字节一致。
        success_argv = (
            "list", "--all-positions",
            "--after-id", " " + FIVE_THOUSAND_ZEROS + "1\t",
            "--limit", FIVE_THOUSAND_ZEROS + "1",
        )
        first = self.run_cli(*success_argv)
        second = self.run_cli(*success_argv)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual((first.stdout, first.stderr),
                         (second.stdout, second.stderr))
        self.assertEqual(json.loads(first.stdout), [self.zhou_ning])

        failure_argv = (
            "list", "--all-positions",
            "--limit", FIVE_THOUSAND_NINES,
            "--after-id", FIVE_THOUSAND_NINES,
            "--stage", "offer",
        )
        first = self.run_cli(*failure_argv)
        second = self.run_cli(*failure_argv)
        self.assertEqual(first.returncode, 2)
        self.assertEqual((first.stdout, first.stderr),
                         (second.stdout, second.stderr))

        # 再以标准库 sqlite3 直接只读重开数据库文件核对落库内容。
        with sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True) as conn:
            candidate_rows = conn.execute(
                "SELECT id, name, email, position, stage"
                " FROM candidates ORDER BY id ASC"
            ).fetchall()
            feedback_rows = conn.execute(
                "SELECT id, candidate_id, text FROM feedback ORDER BY id ASC"
            ).fetchall()
            history_rows = conn.execute(
                "SELECT id, candidate_id, from_stage, to_stage"
                " FROM stage_history ORDER BY id ASC"
            ).fetchall()
        self.assertEqual(
            candidate_rows,
            [
                (1, "林晓", "lin@example.test", QA_POSITION, APPLIED),
                (2, "周宁", "zhou@example.test", QA_POSITION, APPLIED),
                (3, "陈禾", "chen@example.test", DEV_POSITION, APPLIED),
            ],
        )
        self.assertEqual(feedback_rows, [(1, 1, "林晓的评价")])
        self.assertEqual(history_rows, [])

    # ---- 岗位范围、普通长度参数与其他公开命令保持原有行为 ----

    def test_position_scope_and_normal_length_values_unaffected(self):
        # 岗位范围查询：测试工程师下边界 1 之后只有周宁。
        result = self.run_cli(
            "list", "--position", QA_POSITION,
            "--after-id", "1", "--limit", "1",
        )
        self.assert_success(result, [self.zhou_ning])

        # 普通长度的前导零与 int64 上限在岗位范围内同样工作。
        result = self.run_cli(
            "list", "--position", QA_POSITION, "--limit", "0002"
        )
        self.assert_success(result, [self.lin_xiao, self.zhou_ning])

        result = self.run_cli(
            "list", "--position", DEV_POSITION,
            "--after-id", FIVE_THOUSAND_ZEROS + INT64_MAX,
        )
        self.assert_success(result, [])

    def test_other_public_commands_unaffected(self):
        result = self.run_cli("get", "--id", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), self.lin_xiao)

        result = self.run_cli("list-feedback", "--id", "0001")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [self.lin_feedback])

        result = self.run_cli("stage-history", "--id", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])

        result = self.run_cli("summary", "--all-positions")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            [
                {"position": DEV_POSITION, "total": 1,
                 "counts": {"applied": 1, "interviewing": 0,
                            "hired": 0, "rejected": 0}},
                {"position": QA_POSITION, "total": 2,
                 "counts": {"applied": 2, "interviewing": 0,
                            "hired": 0, "rejected": 0}},
            ],
        )


if __name__ == "__main__":
    unittest.main()
