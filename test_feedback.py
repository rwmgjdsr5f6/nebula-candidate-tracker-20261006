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
    """每个用例都通过 add 入口登记林晓和周宁。"""

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
        return self.run_cli(
            "add-feedback", "--id", str(candidate_id), "--text", text
        )

    def list_feedback(self, candidate_id):
        return self.run_cli("list-feedback", "--id", str(candidate_id))

    # ---- 断言辅助 ----

    def assert_add_success(self, result, candidate_id, text):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        record = json.loads(result.stdout)
        self.assertEqual(
            set(record), {"id", "candidate_id", "text"}
        )
        self.assertIsInstance(record["id"], int)
        self.assertGreaterEqual(record["id"], 1)
        self.assertEqual(record["candidate_id"], candidate_id)
        self.assertEqual(record["text"], text)
        return record

    def assert_list(self, candidate_id, expected):
        result = self.list_feedback(candidate_id)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        records = json.loads(result.stdout)
        self.assertEqual(records, expected)
        for record in records:
            self.assertEqual(set(record), {"id", "candidate_id", "text"})
        return records

    def assert_failure(self, result, expected_errors):
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        # 标准错误只有单行 JSON，不出现回溯等多行输出
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertEqual(json.loads(result.stderr), {"errors": expected_errors})


class AddFeedbackTests(FeedbackTestCase):
    def test_add_returns_only_three_fields(self):
        lin_id = self.lin_xiao["id"]
        self.assert_add_success(
            self.add_feedback(lin_id, " 表达清楚 "), lin_id, "表达清楚"
        )

    def test_surrounding_whitespace_trimmed_inner_kept(self):
        """两端空白去除，内部空白、换行、中文与大小写原样保留。"""
        lin_id = self.lin_xiao["id"]
        text = "  Line TWO 二行  \n继续  "
        self.assert_add_success(
            self.add_feedback(lin_id, text), lin_id, "Line TWO 二行  \n继续"
        )

    def test_duplicate_text_creates_separate_increasing_ids(self):
        """重复提交相同文本也新增独立评价，id 唯一且递增。"""
        lin_id = self.lin_xiao["id"]
        first = self.assert_add_success(
            self.add_feedback(lin_id, "一样"), lin_id, "一样"
        )
        second = self.assert_add_success(
            self.add_feedback(lin_id, "一样"), lin_id, "一样"
        )
        self.assertEqual(second["id"], first["id"] + 1)
        self.assert_list(lin_id, [first, second])

    def test_leading_zero_id_succeeds(self):
        lin_id = self.lin_xiao["id"]
        result = self.add_feedback("000{}".format(lin_id), "ok")
        self.assert_add_success(result, lin_id, "ok")

    def test_blank_text_is_required(self):
        for blank in ("", "   ", "\t\n "):
            with self.subTest(blank=repr(blank)):
                self.assert_failure(
                    self.add_feedback(self.lin_xiao["id"], blank),
                    {"text": "required"},
                )

    def test_invalid_id_is_reported(self):
        for bad_id in ("abc", "0", "000", "-1", "1.5", "+1", "１２"):
            with self.subTest(bad_id=bad_id):
                self.assert_failure(
                    self.add_feedback(bad_id, "x"), {"id": "invalid"}
                )

    def test_invalid_id_and_blank_text_reported_together(self):
        """非法 id 与空文本同时出现时在一个 errors 对象中报告两项。"""
        self.assert_failure(
            self.add_feedback("abc", " "),
            {"id": "invalid", "text": "required"},
        )

    def test_unknown_id_is_not_found(self):
        missing_id = max(self.lin_xiao["id"], self.zhou_ning["id"]) + 1000
        self.assert_failure(
            self.add_feedback(missing_id, "x"), {"id": "not_found"}
        )

    def test_id_above_sqlite_int64_max_is_not_found(self):
        self.assert_failure(
            self.add_feedback("9223372036854775808", "x"),
            {"id": "not_found"},
        )

    def test_failure_saves_nothing(self):
        """参数或存在性错误不写入评价，不改动候选人。"""
        lin_id = self.lin_xiao["id"]
        before = self.run_cli("get", "--id", str(lin_id))
        self.add_feedback(lin_id, " ")
        self.add_feedback("abc", " ")
        self.add_feedback(lin_id + 1000, "x")
        self.add_feedback("9223372036854775808", "x")
        self.assert_list(lin_id, [])
        after = self.run_cli("get", "--id", str(lin_id))
        self.assertEqual(after.stdout, before.stdout)

    def test_missing_options_are_usage_errors(self):
        """缺少必填选项时标准错误为用法说明，标准输出为空，退出码为 2。"""
        result = self.run_cli("add-feedback", "--id", str(self.lin_xiao["id"]))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage", result.stderr)

        result = self.run_cli("add-feedback", "--text", "x")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage", result.stderr)


class ListFeedbackTests(FeedbackTestCase):
    def test_empty_for_existing_candidate(self):
        self.assert_list(self.lin_xiao["id"], [])
        self.assert_list(self.zhou_ning["id"], [])

    def test_only_target_candidate_feedback_ascending(self):
        """只返回目标候选人的评价，按 id 升序。"""
        lin_id = self.lin_xiao["id"]
        zhou_id = self.zhou_ning["id"]
        a = self.assert_add_success(
            self.add_feedback(lin_id, "甲"), lin_id, "甲"
        )
        self.add_feedback(zhou_id, "乙的评价")
        b = self.assert_add_success(
            self.add_feedback(lin_id, "乙"), lin_id, "乙"
        )
        self.assert_list(lin_id, [a, b])
        zhou_result = self.list_feedback(zhou_id)
        self.assertEqual(zhou_result.returncode, 0, zhou_result.stderr)
        self.assertEqual(len(json.loads(zhou_result.stdout)), 1)

    def test_id_with_surrounding_whitespace_succeeds(self):
        result = self.run_cli(
            "list-feedback", "--id", "  {}  ".format(self.lin_xiao["id"])
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), [])

    def test_invalid_and_unknown_ids_fail(self):
        self.assert_failure(
            self.run_cli("list-feedback", "--id", "abc"), {"id": "invalid"}
        )
        self.assert_failure(
            self.run_cli("list-feedback", "--id", "0"), {"id": "invalid"}
        )
        missing_id = max(self.lin_xiao["id"], self.zhou_ning["id"]) + 1000
        self.assert_failure(
            self.run_cli("list-feedback", "--id", str(missing_id)),
            {"id": "not_found"},
        )
        self.assert_failure(
            self.run_cli("list-feedback", "--id", "9223372036854775808"),
            {"id": "not_found"},
        )

    def test_missing_id_option_is_usage_error(self):
        result = self.run_cli("list-feedback")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage", result.stderr)


class FeedbackPersistenceAndCorrectionsTests(FeedbackTestCase):
    def test_feedback_persists_across_restarts(self):
        """评价保存在数据库中，独立进程重复查询内容与顺序一致。"""
        lin_id = self.lin_xiao["id"]
        records = [
            self.assert_add_success(
                self.add_feedback(lin_id, text), lin_id, text
            )
            for text in ("第一条", "第二条", "第三条")
        ]
        self.assert_list(lin_id, records)
        self.assert_list(lin_id, records)

    def test_profile_corrections_keep_feedback_attached(self):
        """更正姓名、邮箱、岗位或阶段后评价仍归属原候选人 id。"""
        lin_id = self.lin_xiao["id"]
        first = self.assert_add_success(
            self.add_feedback(lin_id, "甲"), lin_id, "甲"
        )
        self.run_cli("set-name", "--id", str(lin_id), "--name", "林小晓")
        self.run_cli(
            "set-email", "--id", str(lin_id), "--email", "lin.x2@example.test"
        )
        self.run_cli(
            "set-position", "--id", str(lin_id), "--position", "另一岗位"
        )
        self.run_cli("set-stage", "--id", str(lin_id), "--stage", "hired")

        self.assert_list(lin_id, [first])
        # 追加评价不改变阶段或统计
        get_result = self.run_cli("get", "--id", str(lin_id))
        self.assertEqual(json.loads(get_result.stdout)["stage"], "hired")
        self.assert_list(self.zhou_ning["id"], [])


class FeedbackExistingDatabaseTests(unittest.TestCase):
    """只有 candidates 表的旧库直接可用，历史候选人初始评价为空。"""

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
            ("林晓", "lin.xiao@example.test", POSITION, "interviewing"),
        )
        conn.commit()
        conn.close()

        result = self.run_cli("list-feedback", "--id", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])

        result = self.run_cli(
            "add-feedback", "--id", "1", "--text", "旧库评价"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"id": 1, "candidate_id": 1, "text": "旧库评价"},
        )
        result = self.run_cli("list-feedback", "--id", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            [{"id": 1, "candidate_id": 1, "text": "旧库评价"}],
        )


if __name__ == "__main__":
    unittest.main()
