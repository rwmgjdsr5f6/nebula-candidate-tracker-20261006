"""add 登记入口的独立回归测试：有效输入落盘、无效输入不写入。

从项目根目录执行 `python -m unittest discover` 即可与原有筛选、阶段修改
测试一起被发现并运行。每个用例都在独立的临时目录中创建全新的 SQLite
数据库，通过子进程调用 `python -m recruiting` 公开命令并以 --db 指向
临时库，结束后清理临时目录；重复运行互不依赖，也不读取或改动使用者
已有的任何数据。所有人名、岗位与邮箱均为合成数据。

本文件只覆盖 add 命令：不改变三个现有命令的参数、输出、邮箱规则或
SQLite 数据格式，也不涉及数据库权限、损坏或外部服务。
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

# 固定成功样例：三个字段两端均带空白，邮箱保留内部大小写
NAME_INPUT = " 林晓 "
EMAIL_INPUT = " Lin.Xiao@example.test "
POSITION_INPUT = " 合成测试岗 "

NAME = "林晓"
EMAIL = "Lin.Xiao@example.test"
POSITION = "合成测试岗"
STAGE_APPLIED = "applied"

FIELDS = {"id", "name", "email", "position", "stage"}

BLANK_VALUES = ("", "   ", "\t\n")


class AddRegistrationTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-add-test-")
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

    def add(self, name=NAME_INPUT, email=EMAIL_INPUT, position=POSITION_INPUT):
        # 所有错误用例都显式提供三个必填选项：校验针对的是已提供的字段值，
        # 而非遗漏必填选项（后者属于参数解析，不在本文件范围）。
        return self.run_cli(
            "add", "--name", name, "--email", email, "--position", position
        )

    def list_position(self, position=POSITION):
        result = self.run_cli("list", "--position", position)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def candidate_count(self):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute("SELECT count(*) FROM candidates").fetchone()[0]
        finally:
            conn.close()

    def all_rows(self):
        """直接读盘取得全部记录（id, name, email, position, stage），按 id 升序。"""
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute(
                "SELECT id, name, email, position, stage FROM candidates"
                " ORDER BY id ASC"
            ).fetchall()
        finally:
            conn.close()

    # ---- 断言辅助 ----

    def seed_one_candidate(self):
        """先登记一条有效候选人，返回其完整记录。"""
        result = self.add()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def assert_add_failure(self, result, expected_errors):
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        payload = result.stderr.strip()
        # 标准错误是单行单个 JSON 对象
        self.assertNotIn("\n", payload)
        self.assertEqual(json.loads(payload), {"errors": expected_errors})

    def assert_state_unchanged(self, count_before, rows_before, existing_record):
        """候选人总数与已存记录的所有字段都与失败前一致。"""
        self.assertEqual(self.candidate_count(), count_before)
        self.assertEqual(self.all_rows(), rows_before)
        # 经另一个命令进程查询，已存记录仍完整可读
        self.assertEqual(self.list_position(), [existing_record])


class AddRegistrationSuccessTests(AddRegistrationTestCase):
    def test_valid_input_is_persisted_with_trimmed_fields(self):
        """有效输入：两端空白被去除、邮箱保留内部大小写，记录完整落盘。"""
        result = self.add()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        # 标准输出为单个 JSON 对象，且只有一行
        payload = result.stdout.strip()
        self.assertNotIn("\n", payload)
        record = json.loads(payload)
        self.assertIsInstance(record, dict)
        self.assertEqual(set(record.keys()), FIELDS)

        self.assertEqual(record["name"], NAME)
        self.assertEqual(record["position"], POSITION)
        self.assertEqual(record["email"], EMAIL)
        self.assertEqual(record["stage"], STAGE_APPLIED)
        self.assertIsInstance(record["id"], int)
        self.assertGreaterEqual(record["id"], 1)

        # 登记进程结束后，另一次 list 调用返回只含这条完整记录的数组
        self.assertEqual(self.candidate_count(), 1)
        self.assertEqual(self.list_position(), [record])

    def test_duplicate_registration_creates_distinct_id_and_both_persist(self):
        """相同资料可重复登记：生成不同 id，重启进程后两条记录按 id 升序共存。"""
        first_result = self.add()
        second_result = self.add()

        self.assertEqual(first_result.returncode, 0, first_result.stderr)
        self.assertEqual(second_result.returncode, 0, second_result.stderr)
        self.assertEqual(first_result.stderr, "")
        self.assertEqual(second_result.stderr, "")

        first = json.loads(first_result.stdout)
        second = json.loads(second_result.stdout)
        self.assertIsInstance(first["id"], int)
        self.assertIsInstance(second["id"], int)
        self.assertNotEqual(first["id"], second["id"])
        # 除 id 外的所有字段完全一致（保留当前允许重复邮箱的行为）
        for field in ("name", "email", "position", "stage"):
            self.assertEqual(first[field], second[field])
        self.assertEqual(first["name"], NAME)
        self.assertEqual(first["email"], EMAIL)
        self.assertEqual(first["position"], POSITION)
        self.assertEqual(first["stage"], STAGE_APPLIED)

        # 全新命令进程查询：按 id 升序的两条完整记录
        records = self.list_position()
        self.assertEqual([record["id"] for record in records],
                         sorted(record["id"] for record in records))
        self.assertEqual(records, [first, second])
        self.assertEqual(self.candidate_count(), 2)

        # 再次重启进程查询，结果保持稳定
        self.assertEqual(self.list_position(), records)


class AddRegistrationFailureTests(AddRegistrationTestCase):
    def seed_with_snapshot(self):
        """先登记一条有效候选人，并固化失败前的总数与全量行快照。

        每个测试方法只播种一次；同一方法内的多个 subTest 共享该快照，
        因为每次失败的 add 都不应写入，快照在各次失败间保持不变。
        """
        record = self.seed_one_candidate()
        return record, self.candidate_count(), self.all_rows()

    def test_blank_name_is_required(self):
        record, count_before, rows_before = self.seed_with_snapshot()
        for blank in BLANK_VALUES:
            with self.subTest(blank=repr(blank)):
                result = self.add(name=blank)
                self.assert_add_failure(result, {"name": "required"})
                self.assert_state_unchanged(count_before, rows_before, record)

    def test_blank_position_is_required(self):
        record, count_before, rows_before = self.seed_with_snapshot()
        for blank in BLANK_VALUES:
            with self.subTest(blank=repr(blank)):
                result = self.add(position=blank)
                self.assert_add_failure(result, {"position": "required"})
                self.assert_state_unchanged(count_before, rows_before, record)

    def test_empty_email_is_invalid(self):
        record, count_before, rows_before = self.seed_with_snapshot()
        for blank in BLANK_VALUES:
            with self.subTest(blank=repr(blank)):
                result = self.add(email=blank)
                self.assert_add_failure(result, {"email": "invalid"})
                self.assert_state_unchanged(count_before, rows_before, record)

    def test_email_with_internal_whitespace_is_invalid(self):
        record, count_before, rows_before = self.seed_with_snapshot()
        for bad_email in ("Lin Xiao@example.test", "lin.xiao@\texample.test"):
            with self.subTest(bad_email=repr(bad_email)):
                result = self.add(email=bad_email)
                self.assert_add_failure(result, {"email": "invalid"})
                self.assert_state_unchanged(count_before, rows_before, record)

    def test_email_without_or_with_multiple_at_is_invalid(self):
        record, count_before, rows_before = self.seed_with_snapshot()
        for bad_email in (
            "lin.xiaoexample.test",   # 缺少 @
            "lin@xiao@example.test",  # 含多个 @
        ):
            with self.subTest(bad_email=bad_email):
                result = self.add(email=bad_email)
                self.assert_add_failure(result, {"email": "invalid"})
                self.assert_state_unchanged(count_before, rows_before, record)

    def test_email_with_empty_local_or_domain_part_is_invalid(self):
        record, count_before, rows_before = self.seed_with_snapshot()
        for bad_email in ("@example.test", "lin.xiao@"):
            with self.subTest(bad_email=bad_email):
                result = self.add(email=bad_email)
                self.assert_add_failure(result, {"email": "invalid"})
                self.assert_state_unchanged(count_before, rows_before, record)

    def test_email_domain_without_dot_or_dangling_dot_is_invalid(self):
        record, count_before, rows_before = self.seed_with_snapshot()
        for bad_email in (
            "lin.xiao@example",          # 域名无点
            "lin.xiao@.example.test",    # 点位于域名首部
            "lin.xiao@example.test.",    # 点位于域名尾部
        ):
            with self.subTest(bad_email=bad_email):
                result = self.add(email=bad_email)
                self.assert_add_failure(result, {"email": "invalid"})
                self.assert_state_unchanged(count_before, rows_before, record)

    def test_all_three_fields_invalid_emit_single_line_errors(self):
        """三字段同时无效：标准错误为固定单行 JSON，空数据库仍无候选人。"""
        result = self.add(name="  ", email="bad", position="")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr.strip(),
            '{"errors": {"name": "required", "email": "invalid",'
            ' "position": "required"}}',
        )
        self.assertNotIn("\n", result.stderr.strip())
        self.assertEqual(
            json.loads(result.stderr),
            {"errors": {"name": "required", "email": "invalid",
                        "position": "required"}},
        )

        # 未写入任何候选人：直接读盘计数为 0，list 也返回空数组
        self.assertEqual(self.candidate_count(), 0)
        self.assertEqual(self.all_rows(), [])
        self.assertEqual(self.list_position(), [])


if __name__ == "__main__":
    unittest.main()
