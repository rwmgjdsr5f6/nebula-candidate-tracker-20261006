"""add 登记入口的回归测试：有效输入落盘、无效输入不写入。

从项目根目录执行 `python -m unittest discover` 即可与原有测试一起发现并运行。
每个测试都在独立的临时目录中使用全新的 SQLite 数据库，
通过子进程调用 `python -m recruiting` 公开命令并以 --db 指定该数据库，
结束后清理临时目录；重复运行不依赖前次结果，
不读取也不改动使用者已有的任何数据。所有人名、岗位与邮箱均为合成数据。
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

VALID_NAME = "林晓"
VALID_EMAIL = "Lin.Xiao@example.test"
VALID_POSITION = "合成测试岗"

# 固定成功样例：三个字段两端都带空白，落盘时去除两端空白但保留邮箱大小写
PADDED_NAME = " 林晓 "
PADDED_EMAIL = " Lin.Xiao@example.test "
PADDED_POSITION = " 合成测试岗 "

BLANK_VALUES = ("", "   ", "\t\n")

INVALID_EMAILS = [
    "",  # 空邮箱
    "   ",  # 纯空白邮箱（去空白后为空）
    "Lin Xiao@example.test",  # 本地部分含内部空格
    "Lin.Xiao@example\ttest",  # 域名含内部制表符
    "Lin.Xiaoexample.test",  # 缺少 @
    "Lin@Xiao@example.test",  # 含多个 @
    "@example.test",  # @ 左侧本地部分为空
    "Lin.Xiao@",  # @ 右侧域名为空
    "Lin.Xiao@example",  # 域名无点
    "Lin.Xiao@.example.test",  # 点位于域名首部
    "Lin.Xiao@example.test.",  # 点位于域名尾部
]


class AddRegistrationTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-add-registration-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "candidates.sqlite3")

    # ---- 命令调用辅助 ----

    def run_cli(self, *argv):
        return subprocess.run(
            [sys.executable, "-m", "recruiting", "--db", self.db_path, *argv],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )

    def add(self, name, email, position):
        return self.run_cli(
            "add", "--name", name, "--email", email, "--position", position
        )

    def add_valid_sample(self):
        """登记固定成功样例（字段两端带空白）。"""
        return self.add(PADDED_NAME, PADDED_EMAIL, PADDED_POSITION)

    def list_position(self, position):
        return self.run_cli("list", "--position", position)

    def candidate_count(self):
        """直接读取本用例自己的临时数据库，统计候选人总数。"""
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        finally:
            conn.close()


class AddRegistrationSuccessTests(AddRegistrationTestCase):
    def assert_add_success_record(self, result):
        """成功登记：退出码 0、标准错误为空，标准输出是仅含五个字段的 JSON 对象。"""
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        # 标准输出中只有单个 JSON 对象，没有多余的行
        payload = result.stdout.strip()
        self.assertNotIn("\n", payload)
        record = json.loads(payload)
        self.assertIsInstance(record, dict)
        self.assertEqual(
            set(record.keys()),
            {"id", "name", "email", "position", "stage"},
        )
        self.assertIsInstance(record["id"], int)
        self.assertGreaterEqual(record["id"], 1)
        self.assertEqual(record["name"], VALID_NAME)
        self.assertEqual(record["email"], VALID_EMAIL)
        self.assertEqual(record["position"], VALID_POSITION)
        self.assertEqual(record["stage"], "applied")
        return record

    def test_valid_input_is_persisted_as_applied(self):
        """两端空白被去除、邮箱大小写保留，登记在另一次 list 调用中完整可见。"""
        result = self.add_valid_sample()
        record = self.assert_add_success_record(result)

        # 登记进程结束后的另一次独立命令调用：只返回这一条完整记录
        listed = self.list_position(VALID_POSITION)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(listed.stderr, "")
        self.assertEqual(json.loads(listed.stdout), [record])

        # 其他岗位查不到任何人，库里总共只有一名候选人
        other = self.list_position("合成其他岗位")
        self.assertEqual(other.returncode, 0, other.stderr)
        self.assertEqual(json.loads(other.stdout), [])
        self.assertEqual(self.candidate_count(), 1)

    def test_same_data_can_be_registered_twice_with_distinct_ids(self):
        """相同资料（含相同邮箱）可重复登记，重启后按 id 升序查到两条记录。"""
        first = self.add_valid_sample()
        first_record = self.assert_add_success_record(first)

        second = self.add_valid_sample()
        second_record = self.assert_add_success_record(second)

        self.assertNotEqual(first_record["id"], second_record["id"])
        # 当前允许重复邮箱：两条记录的姓名、邮箱、岗位、阶段完全一致
        for field in ("name", "email", "position", "stage"):
            self.assertEqual(first_record[field], second_record[field])

        # 新的 list 进程读取同一数据库，证明持久化稳定
        listed = self.list_position(VALID_POSITION)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(listed.stderr, "")
        records = json.loads(listed.stdout)
        self.assertEqual(records, [first_record, second_record])
        self.assertEqual(
            [record["id"] for record in records],
            sorted(record["id"] for record in records),
        )
        self.assertEqual(self.candidate_count(), 2)


class AddRegistrationFailureTestCase(AddRegistrationTestCase):
    """失败用例的公共基类：提供“失败不写库”的统一断言。"""

    def snapshot(self):
        """当前岗位的完整记录快照与候选人总数。"""
        listed = self.list_position(VALID_POSITION)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(listed.stderr, "")
        return json.loads(listed.stdout), self.candidate_count()

    def assert_add_failure(self, result, expected_errors):
        """退出码 2、标准输出为空、标准错误为单行 errors JSON，且数据不变。"""
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        payload = result.stderr.strip()
        self.assertNotIn("\n", payload)
        self.assertEqual(json.loads(payload), {"errors": expected_errors})
        # 失败后候选人总数和已存记录的所有字段与失败前一致
        records, count = self.snapshot()
        self.assertEqual(records, self.state_snapshot)
        self.assertEqual(count, self.state_count)


class AddRegistrationValidationTests(AddRegistrationFailureTestCase):
    def setUp(self):
        super().setUp()
        # 先成功登记一名候选人，作为失败操作前后的对照数据
        seeded = self.add_valid_sample()
        self.assertEqual(seeded.returncode, 0, seeded.stderr)
        self.assertEqual(seeded.stderr, "")
        self.seeded_record = json.loads(seeded.stdout)
        self.state_snapshot, self.state_count = self.snapshot()
        self.assertEqual(self.state_snapshot, [self.seeded_record])
        self.assertEqual(self.state_count, 1)

    def test_blank_name_is_required(self):
        for blank in BLANK_VALUES:
            with self.subTest(blank=repr(blank)):
                result = self.add(blank, VALID_EMAIL, VALID_POSITION)
                self.assert_add_failure(result, {"name": "required"})

    def test_blank_position_is_required(self):
        for blank in BLANK_VALUES:
            with self.subTest(blank=repr(blank)):
                result = self.add(VALID_NAME, VALID_EMAIL, blank)
                self.assert_add_failure(result, {"position": "required"})

    def test_invalid_email_is_invalid(self):
        for bad_email in INVALID_EMAILS:
            with self.subTest(email=repr(bad_email)):
                result = self.add(VALID_NAME, bad_email, VALID_POSITION)
                self.assert_add_failure(result, {"email": "invalid"})

    def test_all_three_fields_invalid_return_merged_errors(self):
        """三个字段同时无效：标准错误为单行、固定三项错误的 JSON 对象。"""
        result = self.add("   ", "invalid", "")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr.strip(),
            '{"errors": {"name": "required", "email": "invalid",'
            ' "position": "required"}}',
        )
        self.assert_add_failure(
            result,
            {"name": "required", "email": "invalid", "position": "required"},
        )


class AddRegistrationFailureOnEmptyDatabaseTests(AddRegistrationFailureTestCase):
    def test_failed_adds_leave_empty_database_empty(self):
        """空数据库上的各类失败登记都不建表写入任何候选人。"""
        self.state_snapshot, self.state_count = [], 0

        attempts = []
        for blank in BLANK_VALUES:
            attempts.append(
                ({"name": "required"}, self.add(blank, VALID_EMAIL, VALID_POSITION))
            )
            attempts.append(
                ({"position": "required"}, self.add(VALID_NAME, VALID_EMAIL, blank))
            )
        for bad_email in INVALID_EMAILS:
            attempts.append(
                ({"email": "invalid"}, self.add(VALID_NAME, bad_email, VALID_POSITION))
            )
        attempts.append(
            (
                {"name": "required", "email": "invalid", "position": "required"},
                self.add("   ", "invalid", ""),
            )
        )

        for expected_errors, result in attempts:
            with self.subTest(expected_errors=expected_errors):
                self.assert_add_failure(result, expected_errors)

        # 全部失败结束后，空数据库中仍没有任何候选人
        self.assertEqual(self.candidate_count(), 0)


if __name__ == "__main__":
    unittest.main()
