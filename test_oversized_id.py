"""超长编号（超过 Python 3.11 整数转换位数限制）的回归测试。

Python 3.11 起 int() 默认只接受 4300 位以内的数字字符串，5000 位的
编号此前会让 validate_candidate_id 抛出 ValueError，命令以回溯和
退出码 1 结束。这里固定用连续 5000 个 9 与连续 5000 个 0 覆盖候选人
编号与评价编号两条入口，约定不变：格式合法但超过 SQLite int64 上限的
编号判 not_found；全零、带符号、非 ASCII 数字等仍判 invalid；标准
错误只输出单行 errors JSON，标准输出为空，退出码为 2，失败不改动任何
记录。

从项目根目录执行 `python -m unittest discover` 即可发现并运行。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

POSITION = "测试工程师"
LIN_XIAO = {"name": "林晓", "email": "lin.xiao@example.test"}

# 超过 Python 3.11 默认 4300 位整数转换限制的两种固定输入。
FIVE_THOUSAND_NINES = "9" * 5000
FIVE_THOUSAND_ZEROS = "0" * 5000
FULLWIDTH_NINES = "９" * 5000

SQLITE_INT64_MAX_PLUS_ONE = "9223372036854775808"


class OversizedIdTestCase(unittest.TestCase):
    """登记林晓到测试工程师岗位，制造一次阶段变更后追加一条评价。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-oversized-id-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "candidates.sqlite3")
        self.lin_xiao = self.run_cli(
            "add",
            "--name", LIN_XIAO["name"],
            "--email", LIN_XIAO["email"],
            "--position", POSITION,
        )
        self.assertEqual(self.lin_xiao.returncode, 0, self.lin_xiao.stderr)
        self.candidate = json.loads(self.lin_xiao.stdout)
        self.candidate_id = self.candidate["id"]

        stage_result = self.run_cli(
            "set-stage", "--id", str(self.candidate_id),
            "--stage", "interviewing",
        )
        self.assertEqual(stage_result.returncode, 0, stage_result.stderr)
        self.original_history = [
            {"from_stage": "applied", "to_stage": "interviewing"}
        ]

        feedback_result = self.run_cli(
            "add-feedback", "--id", str(self.candidate_id), "--text", "初始评价"
        )
        self.assertEqual(feedback_result.returncode, 0, feedback_result.stderr)
        self.feedback = json.loads(feedback_result.stdout)
        self.feedback_id = self.feedback["id"]

    def run_cli(self, *argv):
        return subprocess.run(
            [sys.executable, "-m", "recruiting", "--db", self.db_path, *argv],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )

    def assert_failure(self, result, expected_errors):
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        # 标准错误只有单行 JSON，不出现回溯等多行输出
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertEqual(json.loads(result.stderr), {"errors": expected_errors})

    def assert_records_unchanged(self):
        """失败后候选人、评价、阶段历史与岗位统计都保持原样。"""
        result = self.run_cli("get", "--id", str(self.candidate_id))
        self.assertEqual(json.loads(result.stdout), {
            **self.candidate, "stage": "interviewing"
        })
        result = self.run_cli("list-feedback", "--id", str(self.candidate_id))
        self.assertEqual(json.loads(result.stdout), [
            {"id": self.feedback_id,
             "candidate_id": self.candidate_id,
             "text": "初始评价"}
        ])
        result = self.run_cli("stage-history", "--id", str(self.candidate_id))
        self.assertEqual(json.loads(result.stdout), self.original_history)
        result = self.run_cli("summary", "--position", POSITION)
        self.assertEqual(json.loads(result.stdout), {
            "position": POSITION,
            "total": 1,
            "counts": {"applied": 0, "interviewing": 1,
                       "hired": 0, "rejected": 0},
        })


class OversizedCandidateIdTests(OversizedIdTestCase):
    def test_stage_history_5000_nines_is_not_found(self):
        """stage-history 接收连续 5000 个 9：not_found，单行 JSON。"""
        result = self.run_cli("stage-history", "--id", FIVE_THOUSAND_NINES)
        self.assert_failure(result, {"id": "not_found"})

    def test_stage_history_5000_zeros_then_real_id_returns_history(self):
        """5000 个前导零不影响数值，返回该候选人的原历史。"""
        result = self.run_cli(
            "stage-history", "--id", FIVE_THOUSAND_ZEROS + str(self.candidate_id)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), self.original_history)

    def test_stage_history_5000_zeros_is_invalid(self):
        """单独 5000 个 0 仍是全零，判 invalid。"""
        result = self.run_cli("stage-history", "--id", FIVE_THOUSAND_ZEROS)
        self.assert_failure(result, {"id": "invalid"})

    def test_oversized_id_with_surrounding_whitespace_is_not_found(self):
        """两端空白照去除，内部的 5000 位编号仍判 not_found。"""
        result = self.run_cli(
            "stage-history", "--id", "  " + FIVE_THOUSAND_NINES + "\t"
        )
        self.assert_failure(result, {"id": "not_found"})

    def test_all_candidate_id_commands_treat_5000_nines_as_not_found(self):
        """使用 --id 的既有命令对超大合法编号统一判 not_found。"""
        cases = [
            ("get", ["get", "--id", FIVE_THOUSAND_NINES]),
            ("list-feedback",
             ["list-feedback", "--id", FIVE_THOUSAND_NINES]),
            ("add-feedback",
             ["add-feedback", "--id", FIVE_THOUSAND_NINES, "--text", "x"]),
            ("set-stage",
             ["set-stage", "--id", FIVE_THOUSAND_NINES, "--stage", "hired"]),
            ("set-email",
             ["set-email", "--id", FIVE_THOUSAND_NINES,
              "--email", "a@b.example"]),
            ("set-position",
             ["set-position", "--id", FIVE_THOUSAND_NINES,
              "--position", "另一岗位"]),
            ("set-name",
             ["set-name", "--id", FIVE_THOUSAND_NINES, "--name", "新名字"]),
        ]
        for label, argv in cases:
            with self.subTest(command=label):
                self.assert_failure(self.run_cli(*argv), {"id": "not_found"})

    def test_just_above_int64_max_with_leading_zeros_is_not_found(self):
        """前导零包裹的 9223372036854775808 仍按越界处理。"""
        for raw in ("9223372036854775807", SQLITE_INT64_MAX_PLUS_ONE,
                    "000" + SQLITE_INT64_MAX_PLUS_ONE):
            with self.subTest(raw_id=raw[:20]):
                result = self.run_cli("stage-history", "--id", raw)
                self.assert_failure(result, {"id": "not_found"})

    def test_long_but_malformed_ids_remain_invalid(self):
        """长度不改变分类：带符号、小数、内部空白与全角数字仍判 invalid。"""
        malformed = [
            "+" + FIVE_THOUSAND_NINES,
            "-" + FIVE_THOUSAND_NINES,
            FIVE_THOUSAND_NINES + ".0",
            "1" + FIVE_THOUSAND_NINES[:4] + " " + "3",  # 超长编号内部空白
            "12 34",
            FIVE_THOUSAND_NINES[:-1] + "a",
            FULLWIDTH_NINES,
            "",
            "   ",
        ]
        for raw in malformed:
            with self.subTest(raw=repr(raw[:12])):
                result = self.run_cli("stage-history", "--id", raw)
                self.assert_failure(result, {"id": "invalid"})

    def test_oversized_id_with_blank_field_reports_only_field(self):
        """格式合法但越界的编号配合空字段：先合并参数错误，只报字段错误。"""
        cases = [
            ("set-stage", ["--stage", " "], {"stage": "required"}),
            ("set-email", ["--email", " "], {"email": "invalid"}),
            ("set-position", ["--position", " "], {"position": "required"}),
            ("set-name", ["--name", " "], {"name": "required"}),
            ("add-feedback", ["--text", " "], {"text": "required"}),
        ]
        for command, field_args, expected in cases:
            with self.subTest(command=command):
                result = self.run_cli(
                    command, "--id", FIVE_THOUSAND_NINES, *field_args
                )
                self.assert_failure(result, expected)

    def test_invalid_long_id_with_blank_field_reports_both(self):
        """非法编号与空字段同时出现：两个错误合并报告。"""
        result = self.run_cli(
            "set-stage", "--id", "+" + FIVE_THOUSAND_NINES,
            "--stage", " ",
        )
        self.assert_failure(
            result, {"id": "invalid", "stage": "required"}
        )
        result = self.run_cli(
            "add-feedback", "--id", FULLWIDTH_NINES, "--text", " "
        )
        self.assert_failure(
            result, {"id": "invalid", "text": "required"}
        )

    def test_repeated_failure_is_stable_across_processes(self):
        """独立进程重复查询同一超大编号，错误结果一致。"""
        first = self.run_cli("stage-history", "--id", FIVE_THOUSAND_NINES)
        second = self.run_cli("stage-history", "--id", FIVE_THOUSAND_NINES)
        self.assertEqual(first.returncode, 2)
        self.assertEqual(second.returncode, 2)
        self.assertEqual(first.stdout, "")
        self.assertEqual(second.stdout, "")
        self.assertEqual(first.stderr, second.stderr)

    def test_normal_short_id_still_works(self):
        """普通短编号（含前导零）行为不受影响。"""
        result = self.run_cli("stage-history", "--id", "0001")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), self.original_history)
        result = self.run_cli("get", "--id", " 0001 ")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["id"], self.candidate_id)

    def test_failures_change_nothing(self):
        """各种超大/非法编号失败后，数据与统计保持原样。"""
        self.run_cli("stage-history", "--id", FIVE_THOUSAND_NINES)
        self.run_cli("get", "--id", FIVE_THOUSAND_NINES)
        self.run_cli(
            "add-feedback", "--id", FIVE_THOUSAND_NINES, "--text", "x"
        )
        self.run_cli(
            "set-stage", "--id", FIVE_THOUSAND_NINES, "--stage", "hired"
        )
        self.run_cli(
            "set-name", "--id", "+" + FIVE_THOUSAND_NINES, "--name", "x"
        )
        self.run_cli("list-feedback", "--id", FIVE_THOUSAND_ZEROS)
        self.assert_records_unchanged()


class OversizedFeedbackIdTests(OversizedIdTestCase):
    def test_set_feedback_5000_nines_with_blank_text_reports_only_text(self):
        """set-feedback 超大合法编号 + 纯空白文本：只报 text 的 required。"""
        result = self.run_cli(
            "set-feedback",
            "--feedback-id", FIVE_THOUSAND_NINES,
            "--text", "   ",
        )
        self.assert_failure(result, {"text": "required"})

    def test_set_feedback_5000_nines_with_text_is_not_found(self):
        """超大合法编号配合正常文本：feedback_id 的 not_found。"""
        result = self.run_cli(
            "set-feedback",
            "--feedback-id", FIVE_THOUSAND_NINES,
            "--text", "新文字",
        )
        self.assert_failure(result, {"feedback_id": "not_found"})

    def test_set_feedback_5000_zeros_then_real_id_succeeds(self):
        """5000 个前导零后接真实评价编号：正常更正，结构与键不变。"""
        result = self.run_cli(
            "set-feedback",
            "--feedback-id", FIVE_THOUSAND_ZEROS + str(self.feedback_id),
            "--text", " 更正后 ",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), {
            "id": self.feedback_id,
            "candidate_id": self.candidate_id,
            "text": "更正后",
        })

    def test_set_feedback_5000_zeros_is_invalid(self):
        """单独 5000 个 0 判 invalid；配合空文本时两项错误合并。"""
        result = self.run_cli(
            "set-feedback", "--feedback-id", FIVE_THOUSAND_ZEROS,
            "--text", "x",
        )
        self.assert_failure(result, {"feedback_id": "invalid"})

        result = self.run_cli(
            "set-feedback", "--feedback-id", FIVE_THOUSAND_ZEROS,
            "--text", " ",
        )
        self.assert_failure(
            result, {"feedback_id": "invalid", "text": "required"}
        )

    def test_delete_feedback_5000_nines_is_not_found(self):
        """delete-feedback 对超大合法编号判 feedback_id 的 not_found。"""
        result = self.run_cli(
            "delete-feedback", "--feedback-id", FIVE_THOUSAND_NINES
        )
        self.assert_failure(result, {"feedback_id": "not_found"})

    def test_delete_feedback_5000_zeros_then_real_id_succeeds(self):
        """5000 个前导零不影响评价编号，删除返回原对象。"""
        result = self.run_cli(
            "delete-feedback",
            "--feedback-id", FIVE_THOUSAND_ZEROS + str(self.feedback_id),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), {
            "id": self.feedback_id,
            "candidate_id": self.candidate_id,
            "text": "初始评价",
        })
        # 删除后该候选人评价为空
        result = self.run_cli(
            "list-feedback", "--id", str(self.candidate_id)
        )
        self.assertEqual(json.loads(result.stdout), [])

    def test_long_malformed_feedback_ids_remain_invalid(self):
        """带符号、小数、全角数字等超长编号仍判 invalid。"""
        for raw in ("+" + FIVE_THOUSAND_NINES, "-1",
                    FIVE_THOUSAND_NINES + ".5", FULLWIDTH_NINES,
                    "   ", FIVE_THOUSAND_ZEROS):
            with self.subTest(raw=repr(raw[:12])):
                result = self.run_cli(
                    "set-feedback", "--feedback-id", raw, "--text", "x"
                )
                self.assert_failure(result, {"feedback_id": "invalid"})

    def test_int64_boundary_with_blank_text_reports_only_text(self):
        """刚越界编号 + 空文本：参数校验阶段只报 text 的 required。"""
        result = self.run_cli(
            "set-feedback",
            "--feedback-id", SQLITE_INT64_MAX_PLUS_ONE,
            "--text", "\t ",
        )
        self.assert_failure(result, {"text": "required"})

    def test_missing_feedback_id_option_is_usage_error(self):
        """缺少 --feedback-id 仍为 argparse 用法错误，退出码 2。"""
        result = self.run_cli("set-feedback", "--text", "x")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage", result.stderr)

    def test_feedback_failures_change_nothing(self):
        """评价编号的各种失败不改动评价、候选人、历史与统计。"""
        self.run_cli(
            "set-feedback", "--feedback-id", FIVE_THOUSAND_NINES,
            "--text", "x",
        )
        self.run_cli(
            "set-feedback", "--feedback-id", FIVE_THOUSAND_NINES,
            "--text", " ",
        )
        self.run_cli(
            "delete-feedback", "--feedback-id", FIVE_THOUSAND_NINES
        )
        self.run_cli(
            "delete-feedback", "--feedback-id", FIVE_THOUSAND_ZEROS
        )
        self.assert_records_unchanged()


if __name__ == "__main__":
    unittest.main()
