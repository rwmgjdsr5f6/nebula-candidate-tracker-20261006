"""超长编号（超过 Python 3.11 默认整数转换位数限制）的边界回归测试。

Python 3.11 起 int() 默认只接受 4300 位以内的十进制字符串（PEP 682），
而编号规则按数值而非长度分类：5000 位的合法正整数仍须正常解析，数值
超过 SQLite 整数上限或记录不存在时判 not_found；全零、带符号、小数、
内部空白或非 ASCII 数字等非法形态仍判 invalid。任何情况下都只向标准
错误输出单行 errors JSON，标准输出为空，退出码为 2，不能抛出
ValueError 回溯。

固定合成数据：林晓登记到“测试工程师”岗位，阶段从 applied 改为
interviewing（产生一条阶段历史），并追加一条评价。

从项目根目录执行 `python -m unittest discover` 即可发现并运行。
每个测试都在独立的临时目录中创建全新的 SQLite 数据库，通过子进程
调用 `python -m recruiting` 公开命令，结束后清理临时目录。
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
FEEDBACK_TEXT = "第一条评价"

# 超过 Python 3.11+ 默认 4300 位转换上限的两种极端形态。
ALL_NINES = "9" * 5000
ALL_ZEROS = "0" * 5000

SQLITE_INT64_MAX_PLUS_ONE = "9223372036854775808"


class OversizedIdBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-oversized-id-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "candidates.sqlite3")

        result = self.run_cli(
            "add",
            "--name", LIN_XIAO["name"],
            "--email", LIN_XIAO["email"],
            "--position", POSITION,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.candidate_id = json.loads(result.stdout)["id"]

        # 制造一条阶段历史，使“原历史”非空，便于证明前导零长编号解析到
        # 的就是这名候选人。
        result = self.run_cli(
            "set-stage", "--id", str(self.candidate_id), "--stage", "interviewing"
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        result = self.run_cli(
            "add-feedback", "--id", str(self.candidate_id), "--text", FEEDBACK_TEXT
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.feedback_id = json.loads(result.stdout)["id"]

        self.expected_history = [
            {"from_stage": "applied", "to_stage": "interviewing"}
        ]
        self.expected_feedback = [{
            "id": self.feedback_id,
            "candidate_id": self.candidate_id,
            "text": FEEDBACK_TEXT,
        }]
        self.expected_summary = {
            "position": POSITION,
            "total": 1,
            "counts": {
                "applied": 0,
                "interviewing": 1,
                "hired": 0,
                "rejected": 0,
            },
        }

    # ---- 辅助 ----

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

    def stage_history(self, candidate_id):
        return self.run_cli("stage-history", "--id", candidate_id)

    def set_feedback(self, feedback_id, text):
        return self.run_cli(
            "set-feedback", "--feedback-id", feedback_id, "--text", text
        )

    def snapshot(self):
        return {
            "candidate": self.run_cli(
                "get", "--id", str(self.candidate_id)
            ).stdout,
            "feedback": self.run_cli(
                "list-feedback", "--id", str(self.candidate_id)
            ).stdout,
            "history": self.run_cli(
                "stage-history", "--id", str(self.candidate_id)
            ).stdout,
            "summary": self.run_cli("summary", "--position", POSITION).stdout,
        }

    # ---- stage-history：候选人编号 ----

    def test_stage_history_all_nines_is_not_found(self):
        """连续 5000 个 9 是合法正整数但超出 SQLite 整数上限：not_found。"""
        self.assert_failure(self.stage_history(ALL_NINES), {"id": "not_found"})

    def test_stage_history_all_zeros_then_id_returns_original_history(self):
        """5000 个前导零后接候选人编号，数值不变，返回原历史。"""
        padded = ALL_ZEROS + str(self.candidate_id)
        result = self.stage_history(padded)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), self.expected_history)

    def test_stage_history_padded_id_with_surrounding_whitespace(self):
        """超长前导零编号两端空白同样去除，结果与普通编号一致。"""
        padded = "  " + ALL_ZEROS + str(self.candidate_id) + "  "
        result = self.stage_history(padded)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), self.expected_history)

    def test_stage_history_all_zeros_is_invalid(self):
        """单独 5000 个 0 仍是全零：invalid，不因长度改变分类。"""
        self.assert_failure(self.stage_history(ALL_ZEROS), {"id": "invalid"})

    def test_stage_history_oversized_malformed_forms_are_invalid(self):
        """超长输入上的带符号、小数、内部空白与非 ASCII 数字仍为 invalid。"""
        bad_ids = (
            "-" + "9" * 4999,
            "+" + "9" * 4999,
            "9" * 2500 + " " + "9" * 2499,
            "9" * 4998 + ".0",
            "９" * 5000,
        )
        for bad_id in bad_ids:
            with self.subTest(bad_id=bad_id[:12]):
                self.assert_failure(self.stage_history(bad_id), {"id": "invalid"})

    def test_padded_id_history_stable_across_restarts(self):
        """独立进程重复查询超长前导零编号，历史结果一致。"""
        padded = ALL_ZEROS + str(self.candidate_id)
        first = self.stage_history(padded)
        second = self.stage_history(padded)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(json.loads(first.stdout), self.expected_history)

    # ---- set-feedback：评价编号 ----

    def test_set_feedback_all_nines_with_blank_text_reports_only_text(self):
        """格式合法但越界的编号配合空文本时，只报告 text 的 required。"""
        for blank in ("", "   ", "\t\n "):
            with self.subTest(blank=repr(blank)):
                self.assert_failure(
                    self.set_feedback(ALL_NINES, blank),
                    {"text": "required"},
                )

    def test_set_feedback_int64_plus_one_with_blank_text_reports_only_text(self):
        """刚越界一位的编号同样只报告空文本错误（既有边界在超长输入下一致）。"""
        self.assert_failure(
            self.set_feedback(SQLITE_INT64_MAX_PLUS_ONE, " "),
            {"text": "required"},
        )

    def test_set_feedback_all_nines_is_not_found(self):
        """超大合法编号配非空文本时按 feedback_id 的 not_found 处理。"""
        self.assert_failure(
            self.set_feedback(ALL_NINES, "新文字"),
            {"feedback_id": "not_found"},
        )

    def test_set_feedback_oversized_invalid_id_with_blank_text_reports_both(self):
        """超长但格式非法的编号与空文本同时报告，保持先合并参数错误的顺序。"""
        self.assert_failure(
            self.set_feedback("９" * 5000, " "),
            {"feedback_id": "invalid", "text": "required"},
        )

    def test_set_feedback_all_zeros_is_invalid(self):
        """5000 个 0 的评价编号仍为 invalid。"""
        self.assert_failure(
            self.set_feedback(ALL_ZEROS, "新文字"),
            {"feedback_id": "invalid"},
        )

    def test_set_feedback_padded_id_updates_feedback(self):
        """5000 个前导零后接评价编号可以正常更正，编号键与归属不变。"""
        padded = ALL_ZEROS + str(self.feedback_id)
        result = self.set_feedback(padded, " 更正后的文字 ")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {
                "id": self.feedback_id,
                "candidate_id": self.candidate_id,
                "text": "更正后的文字",
            },
        )
        result = self.run_cli(
            "list-feedback", "--id", str(self.candidate_id)
        )
        self.assertEqual(json.loads(result.stdout), [
            {
                "id": self.feedback_id,
                "candidate_id": self.candidate_id,
                "text": "更正后的文字",
            }
        ])

    # ---- add-feedback / delete-feedback 共用同一编号流程 ----

    def test_add_feedback_all_nines_is_not_found(self):
        result = self.run_cli(
            "add-feedback", "--id", ALL_NINES, "--text", "x"
        )
        self.assert_failure(result, {"id": "not_found"})

    def test_add_feedback_all_nines_with_blank_text_reports_only_text(self):
        result = self.run_cli(
            "add-feedback", "--id", ALL_NINES, "--text", " "
        )
        self.assert_failure(result, {"text": "required"})

    def test_delete_feedback_all_nines_is_not_found(self):
        result = self.run_cli(
            "delete-feedback", "--feedback-id", ALL_NINES
        )
        self.assert_failure(result, {"feedback_id": "not_found"})

    # ---- 失败不改动任何数据 ----

    def test_failures_change_nothing(self):
        """所有越界/非法尝试后，候选人、评价、历史与岗位统计均保持原样。"""
        before = self.snapshot()

        self.stage_history(ALL_NINES)
        self.stage_history(ALL_ZEROS)
        self.stage_history("９" * 5000)
        self.set_feedback(ALL_NINES, " ")
        self.set_feedback(ALL_NINES, "新文字")
        self.set_feedback("９" * 5000, " ")
        self.set_feedback(ALL_ZEROS, "新文字")
        self.run_cli("add-feedback", "--id", ALL_NINES, "--text", "x")
        self.run_cli("delete-feedback", "--feedback-id", ALL_NINES)
        # 其余按编号操作的入口同样只能报 not_found，不得写入或回溯
        self.run_cli("get", "--id", ALL_NINES)
        self.run_cli(
            "set-stage", "--id", ALL_NINES, "--stage", "hired"
        )
        self.run_cli("list-feedback", "--id", ALL_NINES)

        self.assertEqual(self.snapshot(), before)
        self.assertEqual(json.loads(before["feedback"]), self.expected_feedback)
        self.assertEqual(json.loads(before["history"]), self.expected_history)
        self.assertEqual(
            json.loads(before["summary"]), self.expected_summary
        )


if __name__ == "__main__":
    unittest.main()
