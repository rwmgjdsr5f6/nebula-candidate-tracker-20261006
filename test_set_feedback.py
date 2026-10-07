"""set-feedback 命令的回归测试。

从项目根目录执行 `python -m unittest discover` 即可发现并运行。
每个测试都在独立的临时目录中创建全新的 SQLite 数据库，
通过子进程调用 `python -m recruiting` 公开命令，结束后清理临时目录，
不读取也不改动使用者已有的任何数据。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

POSITION = "合成测试岗位"
LIN_XIAO = {"name": "林晓", "email": "lin.xiao@example.test"}
ZHOU_NING = {"name": "周宁", "email": "zhou.ning@example.test"}


class SetFeedbackTestCase(unittest.TestCase):
    """每个用例都登记林晓和周宁，并预置三条评价：

    林晓持有编号连续的前两条，周宁持有第三条。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-set-feedback-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "candidates.sqlite3")
        self.lin_xiao = self.add_candidate(**LIN_XIAO)
        self.zhou_ning = self.add_candidate(**ZHOU_NING)
        self.lin_first = self.append_feedback(self.lin_xiao["id"], "第一条")
        self.lin_second = self.append_feedback(self.lin_xiao["id"], "第二条")
        self.zhou_only = self.append_feedback(self.zhou_ning["id"], "周宁的评价")

    # ---- 命令调用辅助 ----

    def run_cli(self, *argv):
        return subprocess.run(
            [sys.executable, "-m", "recruiting", "--db", self.db_path, *argv],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )

    def add_candidate(self, name, email):
        result = self.run_cli(
            "add", "--name", name, "--email", email, "--position", POSITION
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def append_feedback(self, candidate_id, text):
        result = self.run_cli(
            "add-feedback", "--id", str(candidate_id), "--text", text
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def set_feedback(self, feedback_id, text):
        return self.run_cli(
            "set-feedback", "--feedback-id", str(feedback_id), "--text", text
        )

    def list_feedback(self, candidate_id):
        return self.run_cli("list-feedback", "--id", str(candidate_id))

    # ---- 断言辅助 ----

    def assert_set_success(self, result, feedback_id, candidate_id, text):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        record = json.loads(result.stdout)
        self.assertEqual(
            record,
            {"id": feedback_id, "candidate_id": candidate_id, "text": text},
        )
        return record

    def assert_list(self, candidate_id, expected):
        result = self.list_feedback(candidate_id)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), expected)

    def assert_failure(self, result, expected_errors):
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        # 标准错误只有单行 JSON，不出现回溯等多行输出
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertEqual(json.loads(result.stderr), {"errors": expected_errors})


class SetFeedbackSuccessTests(SetFeedbackTestCase):
    def test_set_replaces_only_target_text(self):
        """只替换目标评价的 text，其余评价的内容与归属保持原样。"""
        target = self.lin_second
        self.assert_set_success(
            self.set_feedback(target["id"], " 表达清楚 "),
            target["id"],
            self.lin_xiao["id"],
            "表达清楚",
        )
        self.assert_list(
            self.lin_xiao["id"],
            [
                self.lin_first,
                {"id": target["id"], "candidate_id": self.lin_xiao["id"],
                 "text": "表达清楚"},
            ],
        )
        self.assert_list(self.zhou_ning["id"], [self.zhou_only])

    def test_inner_whitespace_newlines_and_case_kept(self):
        """两端空白去除，内部空白、换行、中文与大小写原样保留。"""
        text = "  Line TWO 二行  \n继续  "
        target = self.lin_first
        self.assert_set_success(
            self.set_feedback(target["id"], text),
            target["id"],
            self.lin_xiao["id"],
            "Line TWO 二行  \n继续",
        )

    def test_leading_zero_feedback_id_succeeds(self):
        target = self.lin_first
        result = self.set_feedback("000{}".format(target["id"]), "ok")
        self.assert_set_success(
            result, target["id"], self.lin_xiao["id"], "ok"
        )

    def test_feedback_id_with_surrounding_whitespace_succeeds(self):
        target = self.lin_first
        result = self.set_feedback("  {}  ".format(target["id"]), "ok")
        self.assert_set_success(
            result, target["id"], self.lin_xiao["id"], "ok"
        )

    def test_repeat_set_to_same_text_succeeds_without_new_feedback(self):
        """重复更正为当前文字也成功，不新增评价，数量与顺序不变。"""
        target = self.lin_first
        self.assert_set_success(
            self.set_feedback(target["id"], "改写"),
            target["id"],
            self.lin_xiao["id"],
            "改写",
        )
        self.assert_set_success(
            self.set_feedback(target["id"], "改写"),
            target["id"],
            self.lin_xiao["id"],
            "改写",
        )
        self.assert_list(
            self.lin_xiao["id"],
            [
                {"id": target["id"], "candidate_id": self.lin_xiao["id"],
                 "text": "改写"},
                self.lin_second,
            ],
        )

    def test_set_does_not_touch_profile_history_or_summary(self):
        """更正评价不改变候选人资料、阶段历史或岗位统计。"""
        lin_id = self.lin_xiao["id"]
        before_get = self.run_cli("get", "--id", str(lin_id))
        before_history = self.run_cli("stage-history", "--id", str(lin_id))
        before_summary = self.run_cli("summary", "--position", POSITION)

        self.set_feedback(self.lin_first["id"], "改写")

        after_get = self.run_cli("get", "--id", str(lin_id))
        after_history = self.run_cli("stage-history", "--id", str(lin_id))
        after_summary = self.run_cli("summary", "--position", POSITION)
        self.assertEqual(after_get.stdout, before_get.stdout)
        self.assertEqual(after_history.stdout, before_history.stdout)
        self.assertEqual(after_summary.stdout, before_summary.stdout)

    def test_result_persists_across_restarts(self):
        """更正结果保存在数据库中，独立进程重复查询一致。"""
        target = self.lin_second
        self.set_feedback(target["id"], "持久化")
        expected = [
            self.lin_first,
            {"id": target["id"], "candidate_id": self.lin_xiao["id"],
             "text": "持久化"},
        ]
        self.assert_list(self.lin_xiao["id"], expected)
        self.assert_list(self.lin_xiao["id"], expected)


class SetFeedbackFailureTests(SetFeedbackTestCase):
    def test_blank_text_is_required(self):
        for blank in ("", "   ", "\t\n "):
            with self.subTest(blank=repr(blank)):
                self.assert_failure(
                    self.set_feedback(self.lin_first["id"], blank),
                    {"text": "required"},
                )

    def test_invalid_feedback_id_is_reported(self):
        for bad_id in ("abc", "0", "000", "-1", "1.5", "+1", "１２"):
            with self.subTest(bad_id=bad_id):
                self.assert_failure(
                    self.set_feedback(bad_id, "x"),
                    {"feedback_id": "invalid"},
                )

    def test_invalid_id_and_blank_text_reported_together(self):
        """先合并参数错误：非法编号配合空文本同时报告两项。"""
        self.assert_failure(
            self.set_feedback("abc", " "),
            {"feedback_id": "invalid", "text": "required"},
        )

    def test_unknown_id_with_blank_text_reports_only_text(self):
        """不存在的编号配合空文本时仅报告 text 的 required。"""
        missing_id = self.zhou_only["id"] + 1000
        self.assert_failure(
            self.set_feedback(missing_id, " "),
            {"text": "required"},
        )

    def test_unknown_feedback_id_is_not_found(self):
        missing_id = self.zhou_only["id"] + 1000
        self.assert_failure(
            self.set_feedback(missing_id, "x"),
            {"feedback_id": "not_found"},
        )

    def test_candidate_id_is_not_a_feedback_id(self):
        """候选人编号不等于评价编号：无对应评价时按 not_found 处理。"""
        # 候选人 id 远小于已分配的评价编号上限时必然找不到评价
        self.assert_failure(
            self.set_feedback(self.zhou_only["id"] + 1, "x"),
            {"feedback_id": "not_found"},
        )

    def test_id_above_sqlite_int64_max_is_not_found(self):
        self.assert_failure(
            self.set_feedback("9223372036854775808", "x"),
            {"feedback_id": "not_found"},
        )

    def test_failure_saves_nothing(self):
        """参数或存在性错误不改动任何已保存的评价。"""
        lin_id = self.lin_xiao["id"]
        self.set_feedback(self.lin_first["id"], " ")
        self.set_feedback("abc", " ")
        self.set_feedback("abc", "x")
        self.set_feedback(self.zhou_only["id"] + 1000, "x")
        self.set_feedback("9223372036854775808", "x")
        self.assert_list(lin_id, [self.lin_first, self.lin_second])
        self.assert_list(self.zhou_ning["id"], [self.zhou_only])

    def test_missing_options_are_usage_errors(self):
        """缺少任一必填选项时标准错误为用法说明，退出码为 2。"""
        result = self.run_cli(
            "set-feedback", "--feedback-id", str(self.lin_first["id"])
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage", result.stderr)

        result = self.run_cli("set-feedback", "--text", "x")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage", result.stderr)


if __name__ == "__main__":
    unittest.main()
