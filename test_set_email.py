"""set-email 命令的回归测试。

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


class SetEmailTestCase(unittest.TestCase):
    """每个测试用例都通过 add 入口登记林晓和周宁，并记录返回的完整记录。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-set-email-test-")
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

    def set_email(self, candidate_id, email):
        return self.run_cli("set-email", "--id", candidate_id, "--email", email)

    def list_candidates(self):
        result = self.run_cli("list", "--position", POSITION)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    # ---- 断言辅助 ----

    def expected_records(self, email_overrides=None):
        """按 id 升序的当前期望记录，email_overrides 按 id 覆盖邮箱。"""
        email_overrides = email_overrides or {}
        records = []
        for record in (self.lin_xiao, self.zhou_ning):
            expected = dict(record)
            if record["id"] in email_overrides:
                expected["email"] = email_overrides[record["id"]]
            records.append(expected)
        records.sort(key=lambda record: record["id"])
        return records

    def assert_success(self, result, expected_record):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), expected_record)

    def assert_failure(self, result, expected_errors):
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        # 标准错误只有单行 JSON，不出现回溯等多行输出
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertEqual(json.loads(result.stderr), {"errors": expected_errors})
        # 失败后两名候选人的全部字段与操作前相同
        self.assertEqual(self.list_candidates(), self.expected_records())


class SetEmailSuccessTests(SetEmailTestCase):
    def test_email_update_persists(self):
        """更正邮箱后只有 email 改变，其余字段与另一人记录保持原值。"""
        new_email = "lin.new@example.test"
        result = self.set_email(str(self.lin_xiao["id"]), new_email)
        expected = dict(self.lin_xiao, email=new_email)
        self.assert_success(result, expected)
        # 独立命令按岗位查询：目标记录显示新邮箱，另一人完整记录不变
        self.assertEqual(
            self.list_candidates(),
            self.expected_records({self.lin_xiao["id"]: new_email}),
        )

    def test_email_with_surrounding_whitespace_is_trimmed(self):
        """邮箱两端空白被去除，保存与返回值都是去空白后的邮箱。"""
        result = self.set_email(str(self.lin_xiao["id"]), " Lin.New@example.test ")
        expected = dict(self.lin_xiao, email="Lin.New@example.test")
        self.assert_success(result, expected)
        self.assertEqual(
            self.list_candidates(),
            self.expected_records({self.lin_xiao["id"]: "Lin.New@example.test"}),
        )

    def test_email_case_is_preserved(self):
        """保存时保留邮箱内部大小写。"""
        new_email = "Lin.New@Example.TEST"
        result = self.set_email(str(self.zhou_ning["id"]), new_email)
        expected = dict(self.zhou_ning, email=new_email)
        self.assert_success(result, expected)
        self.assertEqual(
            self.list_candidates(),
            self.expected_records({self.zhou_ning["id"]: new_email}),
        )

    def test_setting_current_email_again_succeeds(self):
        """重复设置当前邮箱按成功处理。"""
        result = self.set_email(str(self.zhou_ning["id"]), self.zhou_ning["email"])
        self.assert_success(result, self.zhou_ning)
        self.assertEqual(self.list_candidates(), self.expected_records())

    def test_same_email_allowed_for_different_candidates(self):
        """不限制不同候选人使用相同邮箱。"""
        result = self.set_email(str(self.zhou_ning["id"]), self.lin_xiao["email"])
        expected = dict(self.zhou_ning, email=self.lin_xiao["email"])
        self.assert_success(result, expected)
        self.assertEqual(
            self.list_candidates(),
            self.expected_records({self.zhou_ning["id"]: self.lin_xiao["email"]}),
        )

    def test_id_with_surrounding_whitespace_succeeds(self):
        """带两端空白的 id 与合法邮箱仍可成功。"""
        new_email = "lin.new@example.test"
        result = self.set_email("  {}  ".format(self.lin_xiao["id"]), new_email)
        expected = dict(self.lin_xiao, email=new_email)
        self.assert_success(result, expected)
        self.assertEqual(
            self.list_candidates(),
            self.expected_records({self.lin_xiao["id"]: new_email}),
        )

    def test_id_with_leading_zeros_succeeds(self):
        """id 允许前导零，数值不变。"""
        new_email = "lin.new@example.test"
        result = self.set_email(
            "000{}".format(self.lin_xiao["id"]), new_email
        )
        expected = dict(self.lin_xiao, email=new_email)
        self.assert_success(result, expected)


class SetEmailFailureTests(SetEmailTestCase):
    def test_blank_id_is_invalid(self):
        for blank in ("", "   ", "\t\n"):
            with self.subTest(blank=repr(blank)):
                result = self.set_email(blank, "lin.new@example.test")
                self.assert_failure(result, {"id": "invalid"})

    def test_zero_and_negative_id_are_invalid(self):
        for bad_id in ("0", "-1", "-42"):
            with self.subTest(bad_id=bad_id):
                result = self.set_email(bad_id, "lin.new@example.test")
                self.assert_failure(result, {"id": "invalid"})

    def test_non_numeric_id_is_invalid(self):
        for bad_id in ("abc", "1.5", "1a"):
            with self.subTest(bad_id=bad_id):
                result = self.set_email(bad_id, "lin.new@example.test")
                self.assert_failure(result, {"id": "invalid"})

    def test_all_zero_id_is_invalid(self):
        """全零数字数值为零，仍属于 id 的 invalid。"""
        result = self.set_email("0000", "lin.new@example.test")
        self.assert_failure(result, {"id": "invalid"})

    def test_blank_email_is_invalid(self):
        for blank in ("", "   "):
            with self.subTest(blank=repr(blank)):
                result = self.set_email(str(self.lin_xiao["id"]), blank)
                self.assert_failure(result, {"email": "invalid"})

    def test_malformed_email_is_invalid(self):
        for bad_email in (
            "lin.new@example",
            "lin.newexample.test",
            "@example.test",
            "lin.new@",
            "lin.new@.test",
            "lin.new@example.",
            "lin.new@@example.test",
            "lin new@example.test",
        ):
            with self.subTest(bad_email=bad_email):
                result = self.set_email(str(self.lin_xiao["id"]), bad_email)
                self.assert_failure(result, {"email": "invalid"})

    def test_id_and_email_errors_are_merged(self):
        """id 与邮箱同时错误时，同一个 errors 对象保留两项错误。"""
        result = self.set_email("abc", "lin.new@example")
        self.assert_failure(result, {"id": "invalid", "email": "invalid"})

    def test_unknown_id_is_not_found(self):
        missing_id = str(max(self.lin_xiao["id"], self.zhou_ning["id"]) + 1000)
        result = self.set_email(missing_id, "lin.new@example.test")
        self.assert_failure(result, {"id": "not_found"})

    def test_sqlite_int64_max_id_is_not_found(self):
        """整数上限本身是合法 id，无记录时返回 not_found 而非异常。"""
        result = self.set_email("9223372036854775807", "lin.new@example.test")
        self.assert_failure(result, {"id": "not_found"})

    def test_id_above_sqlite_int64_max_is_not_found(self):
        """超过 SQLite 有符号整数上限的合法 id 返回 not_found，无回溯。"""
        result = self.set_email("9223372036854775808", "lin.new@example.test")
        self.assert_failure(result, {"id": "not_found"})

    def test_oversized_id_with_leading_zeros_is_not_found(self):
        """带前导零的越界值含义不变，同样返回 not_found。"""
        result = self.set_email("0009223372036854775808", "lin.new@example.test")
        self.assert_failure(result, {"id": "not_found"})

    def test_oversized_id_with_invalid_email_reports_only_email(self):
        """超大合法 id 与非法邮箱组合：仅返回 email 的 invalid。"""
        result = self.set_email("9223372036854775808", "lin.new@example")
        self.assert_failure(result, {"email": "invalid"})

    def test_validation_happens_before_lookup(self):
        """合法格式但不存在的 id 配上非法邮箱：只返回 email 的 invalid。"""
        missing_id = str(max(self.lin_xiao["id"], self.zhou_ning["id"]) + 1000)
        result = self.set_email(missing_id, "lin.new@example")
        self.assert_failure(result, {"email": "invalid"})

    def test_failed_update_keeps_previous_email(self):
        """先成功更正，再提交非法邮箱：查询仍保留上次成功的值。"""
        new_email = "Lin.New@example.test"
        first = self.set_email(str(self.lin_xiao["id"]), new_email)
        self.assert_success(first, dict(self.lin_xiao, email=new_email))

        second = self.set_email(str(self.lin_xiao["id"]), "lin.new@example")
        self.assertEqual(second.returncode, 2)
        self.assertEqual(second.stdout, "")
        self.assertEqual(json.loads(second.stderr), {"errors": {"email": "invalid"}})
        self.assertEqual(
            self.list_candidates(),
            self.expected_records({self.lin_xiao["id"]: new_email}),
        )


if __name__ == "__main__":
    unittest.main()
