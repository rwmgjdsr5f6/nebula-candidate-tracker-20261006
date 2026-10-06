"""get 命令的回归测试。

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
LIN_XIAO = {"name": "林晓", "email": "Lin.Xiao@example.test"}
ZHOU_NING = {"name": "周宁", "email": "zhou.ning@example.test"}


class GetTestCase(unittest.TestCase):
    """每个测试用例都通过 add 入口登记林晓和周宁，并记录返回的完整记录。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-get-test-")
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

    def get_candidate(self, candidate_id):
        return self.run_cli("get", "--id", candidate_id)

    def list_candidates(self):
        result = self.run_cli("list", "--position", POSITION)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    # ---- 断言辅助 ----

    def expected_records(self):
        """按 id 升序的当前期望记录。"""
        return sorted((self.lin_xiao, self.zhou_ning), key=lambda r: r["id"])

    def assert_success(self, result, expected_record):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), expected_record)
        # 成功查询是只读的：两名候选人的全部字段与记录数量不变
        self.assertEqual(self.list_candidates(), self.expected_records())

    def assert_failure(self, result, expected_errors):
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        # 标准错误只有单行 JSON，不出现回溯等多行输出
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertEqual(json.loads(result.stderr), {"errors": expected_errors})
        # 失败查询同样是只读的
        self.assertEqual(self.list_candidates(), self.expected_records())


class GetSuccessTests(GetTestCase):
    def test_get_returns_registered_record(self):
        """按登记返回的 id 查询，得到与登记结果完全相同的对象。"""
        result = self.get_candidate(str(self.lin_xiao["id"]))
        self.assert_success(result, self.lin_xiao)

    def test_get_is_repeatable(self):
        """重复查看同一 id，结果一致。"""
        for _ in range(3):
            result = self.get_candidate(str(self.zhou_ning["id"]))
            self.assert_success(result, self.zhou_ning)

    def test_email_case_and_name_preserved(self):
        """邮箱大小写与中文姓名保持库存原值。"""
        result = self.get_candidate(str(self.lin_xiao["id"]))
        self.assert_success(result, self.lin_xiao)
        record = json.loads(result.stdout)
        self.assertEqual(record["email"], "Lin.Xiao@example.test")
        self.assertEqual(record["name"], "林晓")

    def test_id_with_leading_zeros_succeeds(self):
        """带前导零的 id 命中同一记录。"""
        result = self.get_candidate("0001")
        self.assert_success(result, self.lin_xiao)

    def test_id_with_surrounding_whitespace_succeeds(self):
        """两端带空白的 id 去空白后命中。"""
        result = self.get_candidate("  {}  ".format(self.zhou_ning["id"]))
        self.assert_success(result, self.zhou_ning)

    def test_updates_are_visible_in_next_get(self):
        """邮箱与阶段修改在下一次 get 中可见。"""
        update = self.run_cli(
            "set-email",
            "--id", str(self.lin_xiao["id"]),
            "--email", "lin.xiao@new.example.test",
        )
        self.assertEqual(update.returncode, 0, update.stderr)
        update = self.run_cli(
            "set-stage",
            "--id", str(self.lin_xiao["id"]),
            "--stage", "interviewing",
        )
        self.assertEqual(update.returncode, 0, update.stderr)

        expected = dict(
            self.lin_xiao,
            email="lin.xiao@new.example.test",
            stage="interviewing",
        )
        result = self.get_candidate(str(self.lin_xiao["id"]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), expected)


class GetFailureTests(GetTestCase):
    def test_blank_id_is_invalid(self):
        for blank in ("", "   ", "\t\n"):
            with self.subTest(blank=repr(blank)):
                result = self.get_candidate(blank)
                self.assert_failure(result, {"id": "invalid"})

    def test_zero_and_negative_id_are_invalid(self):
        for bad_id in ("0", "0000", "-1", "-42"):
            with self.subTest(bad_id=bad_id):
                result = self.get_candidate(bad_id)
                self.assert_failure(result, {"id": "invalid"})

    def test_non_numeric_id_is_invalid(self):
        for bad_id in ("abc", "1.5", "1a", "+1", "1 2", "１２"):
            with self.subTest(bad_id=bad_id):
                result = self.get_candidate(bad_id)
                self.assert_failure(result, {"id": "invalid"})

    def test_unknown_id_is_not_found(self):
        missing_id = str(max(self.lin_xiao["id"], self.zhou_ning["id"]) + 1000)
        result = self.get_candidate(missing_id)
        self.assert_failure(result, {"id": "not_found"})

    def test_sqlite_int64_max_id_is_not_found(self):
        """整数上限本身是合法 id，无记录时返回 not_found 而非异常。"""
        result = self.get_candidate("9223372036854775807")
        self.assert_failure(result, {"id": "not_found"})

    def test_id_above_sqlite_int64_max_is_not_found(self):
        """超过 SQLite 有符号整数上限的合法 id 返回 not_found，无回溯。"""
        result = self.get_candidate("9223372036854775808")
        self.assert_failure(result, {"id": "not_found"})

    def test_oversized_id_with_leading_zeros_is_not_found(self):
        """带前导零的越界值含义不变，同样返回 not_found。"""
        result = self.get_candidate("0009223372036854775808")
        self.assert_failure(result, {"id": "not_found"})

    def test_missing_id_option_is_usage_error(self):
        """完全省略 --id 时是参数解析错误：退出码 2，stderr 为用法信息。"""
        result = self.run_cli("get")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage", result.stderr.lower())


class GetEmptyDatabaseTests(unittest.TestCase):
    """空库场景：首次访问自动建库建表，合法 id 返回 not_found。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-get-empty-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "candidates.sqlite3")

    def test_get_on_empty_database_is_not_found(self):
        result = subprocess.run(
            [sys.executable, "-m", "recruiting", "--db", self.db_path,
             "get", "--id", "1"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertEqual(json.loads(result.stderr), {"errors": {"id": "not_found"}})


if __name__ == "__main__":
    unittest.main()
