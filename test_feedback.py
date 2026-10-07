"""add-feedback 与 list-feedback 命令的回归测试。

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


class FeedbackTestCase(unittest.TestCase):
    """每个测试用例都通过 add 入口登记林晓和周宁，并记录返回的完整记录。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-feedback-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "candidates.sqlite3")
        self.lin_xiao = self.add_candidate(**LIN_XIAO)
        self.zhou_ning = self.add_candidate(**ZHOU_NING)

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
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def add_feedback(self, candidate_id, text):
        return self.run_cli("add-feedback", "--id", candidate_id, "--text", text)

    def list_feedback(self, candidate_id):
        return self.run_cli("list-feedback", "--id", candidate_id)

    # ---- 断言辅助 ----

    def assert_feedback_list(self, candidate_id, expected):
        result = self.list_feedback(str(candidate_id))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), expected)

    def assert_failure(self, result, expected_errors):
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        # 标准错误只有单行 JSON，不出现回溯等多行输出
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertEqual(json.loads(result.stderr), {"errors": expected_errors})


class AddFeedbackTests(FeedbackTestCase):
    def test_append_returns_echo_and_list_shows_it(self):
        """追加成功回显单行 JSON，查询返回同一项，两端空白被去除。"""
        lin_id = self.lin_xiao["id"]
        result = self.add_feedback(str(lin_id), " 表达清楚 ")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        echo = json.loads(result.stdout)
        self.assertEqual(
            echo, {"id": echo["id"], "candidate_id": lin_id, "text": "表达清楚"}
        )
        self.assertIsInstance(echo["id"], int)
        self.assertGreater(echo["id"], 0)
        self.assert_feedback_list(lin_id, [echo])

    def test_internal_whitespace_newlines_and_case_preserved(self):
        """内部空白、换行、中文和大小写原样保留。"""
        lin_id = self.lin_xiao["id"]
        text = "  沟通 Clear\t有条理\n第二行 Note  "
        result = self.add_feedback(str(lin_id), text)
        self.assertEqual(result.returncode, 0, result.stderr)
        echo = json.loads(result.stdout)
        self.assertEqual(echo["text"], "沟通 Clear\t有条理\n第二行 Note")
        self.assert_feedback_list(lin_id, [echo])

    def test_duplicate_text_creates_separate_entries(self):
        """重复提交相同文本新增独立评价，id 唯一且随追加递增。"""
        lin_id = self.lin_xiao["id"]
        first = json.loads(
            self.add_feedback(str(lin_id), "表达清楚").stdout
        )
        second = json.loads(
            self.add_feedback(str(lin_id), "表达清楚").stdout
        )
        self.assertNotEqual(first["id"], second["id"])
        self.assertLess(first["id"], second["id"])
        self.assertEqual(first["text"], second["text"])
        self.assert_feedback_list(lin_id, [first, second])

    def test_feedback_is_tracked_per_candidate(self):
        """两名候选人的评价互不影响，查询只返回目标候选人的评价。"""
        lin_id = self.lin_xiao["id"]
        zhou_id = self.zhou_ning["id"]
        lin_entry = json.loads(self.add_feedback(str(lin_id), "林晓的评价").stdout)
        zhou_entry = json.loads(self.add_feedback(str(zhou_id), "周宁的评价").stdout)

        self.assert_feedback_list(lin_id, [lin_entry])
        self.assert_feedback_list(zhou_id, [zhou_entry])

    def test_candidate_without_feedback_returns_empty_list(self):
        """已存在但没有评价的候选人返回 []。"""
        self.assert_feedback_list(self.lin_xiao["id"], [])

    def test_feedback_persists_across_restarts(self):
        """评价保存在数据库中，独立进程重复查询内容与顺序一致。"""
        lin_id = self.lin_xiao["id"]
        first = json.loads(self.add_feedback(str(lin_id), "第一条").stdout)
        second = json.loads(self.add_feedback(str(lin_id), "第二条").stdout)
        expected = [first, second]
        self.assert_feedback_list(lin_id, expected)
        self.assert_feedback_list(lin_id, expected)

    def test_profile_and_stage_changes_keep_feedback_ownership(self):
        """更正姓名、邮箱、岗位或阶段后，评价仍归属原候选人 id。"""
        lin_id = self.lin_xiao["id"]
        entry = json.loads(self.add_feedback(str(lin_id), "表达清楚").stdout)

        for argv in (
            ("set-name", "--id", str(lin_id), "--name", "林小晓"),
            ("set-email", "--id", str(lin_id), "--email", "lin.x2@example.test"),
            ("set-position", "--id", str(lin_id), "--position", "另一岗位"),
            ("set-stage", "--id", str(lin_id), "--stage", "interviewing"),
        ):
            result = self.run_cli(*argv)
            self.assertEqual(result.returncode, 0, result.stderr)

        self.assert_feedback_list(lin_id, [entry])

    def test_feedback_does_not_change_stage_or_summary(self):
        """追加与查询评价不改变候选人阶段或汇总统计。"""
        lin_id = self.lin_xiao["id"]
        before_list = self.run_cli("list", "--position", POSITION).stdout
        before_summary = self.run_cli("summary", "--position", POSITION).stdout

        self.add_feedback(str(lin_id), "表达清楚")
        self.assert_feedback_list(lin_id, json.loads(self.list_feedback(str(lin_id)).stdout))

        self.assertEqual(self.run_cli("list", "--position", POSITION).stdout, before_list)
        self.assertEqual(
            self.run_cli("summary", "--position", POSITION).stdout, before_summary
        )


class AddFeedbackErrorTests(FeedbackTestCase):
    def test_blank_text_is_required(self):
        """空文本或纯空白报 text 的 required。"""
        lin_id = str(self.lin_xiao["id"])
        for blank in ("", "   ", "\t\n"):
            with self.subTest(blank=repr(blank)):
                self.assert_failure(
                    self.add_feedback(lin_id, blank), {"text": "required"}
                )
        self.assert_feedback_list(self.lin_xiao["id"], [])

    def test_invalid_id_and_blank_text_reported_together(self):
        """非法 id 与空文本同时出现时在一个 errors 对象中报告两项。"""
        self.assert_failure(
            self.add_feedback("abc", "  "), {"id": "invalid", "text": "required"}
        )

    def test_invalid_id_rules(self):
        """id 规则与既有命令一致：空白、零、负数、非 ASCII 数字均 invalid。"""
        for bad_id in ("", "   ", "0", "0000", "-1", "+1", "1.5", "abc", "１２"):
            with self.subTest(bad_id=bad_id):
                self.assert_failure(
                    self.add_feedback(bad_id, "评价"), {"id": "invalid"}
                )

    def test_unknown_id_is_not_found(self):
        """参数合法但候选人不存在时只返回 id 的 not_found。"""
        missing_id = str(max(self.lin_xiao["id"], self.zhou_ning["id"]) + 1000)
        self.assert_failure(
            self.add_feedback(missing_id, "评价"), {"id": "not_found"}
        )

    def test_id_above_sqlite_int64_max_is_not_found(self):
        self.assert_failure(
            self.add_feedback("9223372036854775808", "评价"), {"id": "not_found"}
        )

    def test_failed_add_saves_nothing(self):
        """业务错误不保存评价，也不改动候选人。"""
        lin_id = self.lin_xiao["id"]
        before = self.run_cli("get", "--id", str(lin_id)).stdout
        self.add_feedback("abc", "  ")
        self.add_feedback(str(lin_id), "")
        self.add_feedback(str(lin_id + 1000), "评价")
        self.assert_feedback_list(lin_id, [])
        self.assertEqual(self.run_cli("get", "--id", str(lin_id)).stdout, before)

    def test_id_with_whitespace_and_leading_zeros_succeeds(self):
        lin_id = self.lin_xiao["id"]
        result = self.add_feedback("  000{}  ".format(lin_id), "评价")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["candidate_id"], lin_id)

    def test_missing_required_options_is_usage_error(self):
        """缺少必填选项时输出用法说明到标准错误，退出码为 2。"""
        for argv in (
            ("add-feedback",),
            ("add-feedback", "--id", "1"),
            ("add-feedback", "--text", "评价"),
            ("list-feedback",),
        ):
            with self.subTest(argv=argv):
                result = self.run_cli(*argv)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("usage", result.stderr)


class ListFeedbackIdRuleTests(FeedbackTestCase):
    def test_id_with_surrounding_whitespace_succeeds(self):
        result = self.list_feedback("  {}  ".format(self.lin_xiao["id"]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), [])

    def test_id_with_leading_zeros_succeeds(self):
        result = self.list_feedback("000{}".format(self.lin_xiao["id"]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])

    def test_invalid_id_rules(self):
        for bad_id in ("", "   ", "0", "-1", "+1", "1.5", "abc", "１２"):
            with self.subTest(bad_id=bad_id):
                self.assert_failure(self.list_feedback(bad_id), {"id": "invalid"})

    def test_unknown_id_is_not_found(self):
        missing_id = str(max(self.lin_xiao["id"], self.zhou_ning["id"]) + 1000)
        self.assert_failure(self.list_feedback(missing_id), {"id": "not_found"})

    def test_id_above_sqlite_int64_max_is_not_found(self):
        self.assert_failure(
            self.list_feedback("9223372036854775808"), {"id": "not_found"}
        )


class FeedbackExistingDatabaseTests(unittest.TestCase):
    """已有数据库（无 feedback 表）直接可用，历史候选人初始评价为空。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-feedback-legacy-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "candidates.sqlite3")

    def run_cli(self, *argv):
        return subprocess.run(
            [sys.executable, "-m", "recruiting", "--db", self.db_path, *argv],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )

    def test_legacy_database_without_feedback_table(self):
        """手工建立只有 candidates 表的旧库：初始评价为空，可直接追加。"""
        import sqlite3

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE candidates ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " name TEXT NOT NULL, email TEXT NOT NULL,"
            " position TEXT NOT NULL, stage TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO candidates (name, email, position, stage)"
            " VALUES (?, ?, ?, ?)",
            ("林晓", "lin.xiao@example.test", POSITION, "applied"),
        )
        conn.commit()
        conn.close()

        result = self.run_cli("list-feedback", "--id", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])

        result = self.run_cli("add-feedback", "--id", "1", "--text", " 表达清楚 ")
        self.assertEqual(result.returncode, 0, result.stderr)
        echo = json.loads(result.stdout)
        self.assertEqual(
            echo, {"id": echo["id"], "candidate_id": 1, "text": "表达清楚"}
        )

        result = self.run_cli("list-feedback", "--id", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [echo])


if __name__ == "__main__":
    unittest.main()
