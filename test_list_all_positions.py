"""list 命令 --all-positions 跨岗位查询的回归测试。

从项目根目录执行 `python -m unittest discover` 即可发现并运行。
每个测试都在独立的临时目录中创建全新的 SQLite 数据库，
通过子进程调用 `python -m recruiting` 公开命令，结束后清理临时目录，
不读取也不改动使用者已有的任何数据。所有人名、岗位与邮箱均为合成数据。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

QA_POSITION = "测试工程师"
BE_POSITION = "后端工程师"

OMITTED = object()


class ListAllPositionsTestCase(unittest.TestCase):
    """固定三名候选人（与验收数据一致）：

    id 1 林晓（测试工程师，interviewing），
    id 2 林晓（后端工程师，applied），
    id 3 周宁（测试工程师，applied）。
    三个邮箱各不相同。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-list-all-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "candidates.sqlite3")

        self.lin_qa = self.add_candidate(
            "林晓", "lin.xiao.qa@example.test", QA_POSITION
        )
        self.lin_be = self.add_candidate(
            "林晓", "lin.xiao.be@example.test", BE_POSITION
        )
        self.zhou_qa = self.add_candidate(
            "周宁", "zhou.ning.qa@example.test", QA_POSITION
        )

        result = self.run_cli(
            "set-stage", "--id", str(self.lin_qa["id"]), "--stage",
            "interviewing",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.lin_qa["stage"] = "interviewing"

        snapshot = self.list_all()
        self.assertEqual(snapshot.returncode, 0, snapshot.stderr)
        self.state_snapshot = json.loads(snapshot.stdout)
        self.assertEqual(
            self.state_snapshot,
            [self.lin_qa, self.lin_be, self.zhou_qa],
        )

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
        return json.loads(result.stdout)

    def list_all(self, name=OMITTED, stage=OMITTED, email=OMITTED):
        argv = ["list", "--all-positions"]
        if name is not OMITTED:
            argv.extend(["--name", name])
        if stage is not OMITTED:
            argv.extend(["--stage", stage])
        if email is not OMITTED:
            argv.extend(["--email", email])
        return self.run_cli(*argv)

    # ---- 断言辅助 ----

    def assert_success_query(self, result, expected_records):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        payload = result.stdout.rstrip("\n")
        self.assertNotIn("\n", payload)
        records = json.loads(result.stdout)
        self.assertIsInstance(records, list)
        self.assertEqual(records, expected_records)
        for record in records:
            self.assertEqual(
                set(record.keys()),
                {"id", "name", "email", "position", "stage"},
            )
        return records

    def assert_failure_query(self, result, expected_errors):
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        payload = result.stderr.rstrip("\n")
        self.assertNotIn("\n", payload)
        self.assertEqual(json.loads(payload), {"errors": expected_errors})
        # 查询失败不改变任何候选人资料或阶段
        self.assertEqual(self.list_all().returncode, 0)
        self.assertEqual(
            json.loads(self.list_all().stdout), self.state_snapshot
        )

    def assert_state_unchanged(self):
        result = self.list_all()
        self.assertEqual(json.loads(result.stdout), self.state_snapshot)

    # ---- 成功路径 ----

    def test_no_filters_returns_everyone_sorted_by_global_id(self):
        """无筛选时跨岗位返回全部三人，按 id 全局升序、不按岗位分组。"""
        result = self.list_all()
        records = self.assert_success_query(
            result, [self.lin_qa, self.lin_be, self.zhou_qa]
        )
        self.assertEqual(
            [record["id"] for record in records],
            sorted(record["id"] for record in records),
        )

    def test_name_filter_returns_same_name_across_positions(self):
        """题目验收：--name 林晓 跨岗位依次返回编号 1、2 的完整对象。"""
        result = self.list_all(name="林晓")
        records = self.assert_success_query(result, [self.lin_qa, self.lin_be])
        self.assertEqual(
            [record["position"] for record in records],
            [QA_POSITION, BE_POSITION],
        )

    def test_name_and_stage_filter_returns_only_id_two(self):
        """题目验收：再加 --stage applied 仅返回编号 2。"""
        result = self.list_all(name="林晓", stage="applied")
        self.assert_success_query(result, [self.lin_be])

    def test_stage_filter_spans_positions(self):
        result = self.list_all(stage="applied")
        self.assert_success_query(result, [self.lin_be, self.zhou_qa])

    def test_name_stage_email_must_hold_simultaneously(self):
        result = self.list_all(
            name="林晓",
            stage="interviewing",
            email="lin.xiao.qa@example.test",
        )
        self.assert_success_query(result, [self.lin_qa])

        # 任一条件不满足（邮箱属于编号 2 但阶段不匹配）都不返回
        mismatch = self.list_all(
            name="林晓",
            stage="interviewing",
            email="lin.xiao.be@example.test",
        )
        self.assert_success_query(mismatch, [])

    def test_email_filter_is_exact_case_sensitive(self):
        result = self.list_all(email="LIN.XIAO.QA@EXAMPLE.TEST")
        self.assert_success_query(result, [])

        exact = self.list_all(email="lin.xiao.qa@example.test")
        self.assert_success_query(exact, [self.lin_qa])

    def test_percent_and_underscore_are_literal_characters(self):
        # 参数化查询下 LIKE 通配符按普通字符处理，不会匹配任何记录
        self.assert_success_query(self.list_all(name="林_"), [])
        self.assert_success_query(self.list_all(name="林%"), [])
        self.assert_success_query(
            self.list_all(email="%@example.test"),
            [],
        )

    def test_surrounding_whitespace_in_filters_is_trimmed(self):
        for raw in (" 林晓 ", "\t林晓\t", " 林晓\n"):
            with self.subTest(raw=repr(raw)):
                self.assert_success_query(
                    self.list_all(name=raw), [self.lin_qa, self.lin_be]
                )
        self.assert_success_query(
            self.list_all(stage="  applied\t\n"),
            [self.lin_be, self.zhou_qa],
        )

    def test_no_match_returns_empty_array(self):
        self.assert_success_query(self.list_all(name="陈禾"), [])
        self.assert_success_query(self.list_all(stage="hired"), [])
        self.assert_success_query(
            self.list_all(email="nobody@example.test"), []
        )

    def test_results_consistent_across_command_restarts(self):
        first = self.list_all(name="林晓")
        second = self.list_all(name="林晓")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(
            [r["id"] for r in json.loads(second.stdout)], [1, 2]
        )

    def test_successful_queries_do_not_modify_data(self):
        self.list_all()
        self.list_all(name="林晓")
        self.list_all(name="林晓", stage="applied")
        self.list_all(stage="interviewing")
        self.list_all(email="lin.xiao.be@example.test")
        self.list_all(name="陈禾")
        self.assert_state_unchanged()

    # ---- 参数校验 ----

    def test_blank_name_is_required(self):
        for blank in ("", "   ", "\t\n"):
            with self.subTest(blank=repr(blank)):
                self.assert_failure_query(
                    self.list_all(name=blank), {"name": "required"}
                )

    def test_blank_stage_is_required_and_unknown_stage_is_invalid(self):
        for blank in ("", "  ", "\t\n"):
            with self.subTest(blank=repr(blank)):
                self.assert_failure_query(
                    self.list_all(stage=blank), {"stage": "required"}
                )
        for bad in ("offer", "APPLIED", "Applied"):
            with self.subTest(bad=bad):
                self.assert_failure_query(
                    self.list_all(stage=bad), {"stage": "invalid"}
                )

    def test_invalid_email(self):
        for bad in ("not-an-email", "@example.test", "a@", "a@b"):
            with self.subTest(bad=bad):
                self.assert_failure_query(
                    self.list_all(email=bad), {"email": "invalid"}
                )

    def test_field_errors_are_merged_into_one_object(self):
        result = self.list_all(name=" ", stage="x", email="bad")
        self.assert_failure_query(
            result,
            {"name": "required", "email": "invalid", "stage": "invalid"},
        )
        # 字节级约定：标准错误恰好一行紧凑 JSON
        self.assertEqual(
            result.stderr,
            '{"errors":{"name":"required","email":"invalid","stage":"invalid"}}\n',
        )

    def test_failed_queries_do_not_modify_data(self):
        self.list_all(name=" ")
        self.list_all(stage="offer")
        self.list_all(email="bad")
        self.list_all(name=" ", stage="x", email="bad")
        self.assert_state_unchanged()

    # ---- 范围选项用法 ----

    def test_scope_options_are_mutually_exclusive(self):
        result = self.run_cli(
            "list", "--all-positions", "--position", QA_POSITION
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage:", result.stderr)

        # 空岗位值与全岗位开关并用同样按用法错误处理
        result = self.run_cli(
            "list", "--all-positions", "--position", ""
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage:", result.stderr)

    def test_scope_option_is_required(self):
        result = self.run_cli("list", "--name", "林晓")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage:", result.stderr)

        result = self.run_cli("list")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage:", result.stderr)


class ListAllPositionsEmptyDatabaseTests(unittest.TestCase):
    """空数据库直接返回 []，不要求先有任何记录。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-list-all-empty-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "candidates.sqlite3")

    def run_cli(self, *argv):
        return subprocess.run(
            [sys.executable, "-m", "recruiting", "--db", self.db_path, *argv],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )

    def test_empty_database_returns_empty_array(self):
        result = self.run_cli("list", "--all-positions")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "[]\n")

    def test_empty_database_with_filters_returns_empty_array(self):
        result = self.run_cli(
            "list", "--all-positions", "--name", "林晓",
            "--stage", "applied", "--email", "a@b.test",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "[]\n")

    def test_existing_database_without_migration_keeps_working(self):
        """旧数据库（由既有命令创建）可直接用于全岗位查询，无需迁移。"""
        add = self.run_cli(
            "add", "--name", "林晓",
            "--email", "lin.xiao.qa@example.test",
            "--position", QA_POSITION,
        )
        self.assertEqual(add.returncode, 0, add.stderr)
        result = self.run_cli("list", "--all-positions")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [json.loads(add.stdout)])


if __name__ == "__main__":
    unittest.main()
