"""list 命令 --name 姓名精确筛选的回归测试。

从项目根目录执行 `python -m unittest discover` 即可发现并运行。
每个测试都在独立的临时目录中创建全新的 SQLite 数据库，通过子进程调用
`python -m recruiting` 公开命令，结束后清理临时目录，不读取也不改动
使用者已有的任何数据。所有人名、岗位与邮箱均为合成数据。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

TEST_POSITION = "合成测试岗"
DEV_POSITION = "合成开发岗"

OMITTED = object()


class ListNameFilterTestCase(unittest.TestCase):
    """固定四名候选人：

    id 1 林晓（合成测试岗，applied），id 2 周宁（合成测试岗，applied），
    id 3 林晓（合成测试岗，interviewing），id 4 林晓（合成开发岗，applied）。
    四个邮箱各不相同。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-list-name-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "candidates.sqlite3")

        self.lin1 = self.add_candidate(
            "林晓", "lin.xiao.one@example.test", TEST_POSITION
        )
        self.zhou = self.add_candidate(
            "周宁", "zhou.ning@example.test", TEST_POSITION
        )
        self.lin2 = self.add_candidate(
            "林晓", "lin.xiao.two@example.test", TEST_POSITION
        )
        self.lin_dev = self.add_candidate(
            "林晓", "lin.xiao.dev@example.test", DEV_POSITION
        )

        result = self.run_cli(
            "set-stage", "--id", str(self.lin2["id"]), "--stage", "interviewing"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.lin2["stage"] = "interviewing"

        self.state_snapshot = self.all_records()

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

    def list_candidates(
        self, position, name=OMITTED, stage=OMITTED, email=OMITTED
    ):
        argv = ["list", "--position", position]
        if name is not OMITTED:
            argv.extend(["--name", name])
        if stage is not OMITTED:
            argv.extend(["--stage", stage])
        if email is not OMITTED:
            argv.extend(["--email", email])
        return self.run_cli(*argv)

    def all_records(self):
        records = []
        for position in (TEST_POSITION, DEV_POSITION):
            result = self.list_candidates(position)
            self.assertEqual(result.returncode, 0, result.stderr)
            records.extend(json.loads(result.stdout))
        records.sort(key=lambda record: record["id"])
        return records

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
        # 查询失败不改变任何候选人资料
        self.assertEqual(self.all_records(), self.state_snapshot)

    def assert_state_unchanged(self):
        self.assertEqual(self.all_records(), self.state_snapshot)

    # ---- 成功路径 ----

    def test_name_filter_returns_all_same_named_at_position(self):
        """岗位内同名的 id 1、id 3 全部返回，按 id 升序，含完整五字段。"""
        result = self.list_candidates(TEST_POSITION, "林晓")
        records = self.assert_success_query(result, [self.lin1, self.lin2])
        self.assertEqual([r["id"] for r in records], [1, 3])

    def test_surrounding_whitespace_in_name_is_trimmed(self):
        """--name 两端空白去除后完整匹配，返回 id 1、id 3 两条记录。"""
        for raw in (" 林晓 ", "\t林晓\t", " 林晓\n"):
            with self.subTest(raw=repr(raw)):
                result = self.list_candidates(TEST_POSITION, raw)
                self.assert_success_query(result, [self.lin1, self.lin2])

    def test_name_and_stage_must_hold_simultaneously(self):
        """姓名与阶段同时成立才算命中：只有 id 3 是 interviewing 的林晓。"""
        result = self.list_candidates(
            TEST_POSITION, " 林晓 ", stage="interviewing"
        )
        self.assert_success_query(result, [self.lin2])

        applied = self.list_candidates(TEST_POSITION, "林晓", stage="applied")
        self.assert_success_query(applied, [self.lin1])

    def test_name_filter_is_scoped_to_position(self):
        """id 4 的林晓在开发岗，不出现在测试岗结果中，反之亦然。"""
        result = self.list_candidates(TEST_POSITION, "林晓")
        records = self.assert_success_query(result, [self.lin1, self.lin2])
        self.assertNotIn(self.lin_dev["id"], [r["id"] for r in records])

        dev_result = self.list_candidates(DEV_POSITION, "林晓")
        self.assert_success_query(dev_result, [self.lin_dev])

    def test_omitting_name_keeps_current_behavior(self):
        """省略 --name 返回该岗位全部记录，与扩展前行为一致。"""
        result = self.list_candidates(TEST_POSITION)
        self.assert_success_query(result, [self.lin1, self.zhou, self.lin2])

    def test_unknown_or_other_position_name_returns_empty_array(self):
        no_such_name = self.list_candidates(TEST_POSITION, "陈禾")
        self.assert_success_query(no_such_name, [])

        no_such_position = self.list_candidates("未登记岗位", "林晓")
        self.assert_success_query(no_such_position, [])

        # 周宁只在测试岗存在；开发岗查周宁为空
        self.assert_success_query(
            self.list_candidates(DEV_POSITION, "周宁"), []
        )

    def test_name_and_email_filters_intersect(self):
        """姓名与邮箱同时成立才命中，可在同名者中锁定其中一条。"""
        result = self.list_candidates(
            TEST_POSITION, "林晓", email="lin.xiao.two@example.test"
        )
        self.assert_success_query(result, [self.lin2])

        # 该邮箱属于测试岗；换开发岗查同名同邮箱为空
        cross = self.list_candidates(
            DEV_POSITION, "林晓", email="lin.xiao.two@example.test"
        )
        self.assert_success_query(cross, [])

    def test_name_filter_reads_corrected_value(self):
        """set-name 更正生效后，姓名筛选按现值匹配。"""
        result = self.run_cli(
            "set-name", "--id", str(self.zhou["id"]), "--name", " 林晓 "
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.zhou["name"] = "林晓"

        records = self.list_candidates(TEST_POSITION, "林晓")
        self.assert_success_query(
            records, [self.lin1, self.zhou, self.lin2]
        )

    def test_results_consistent_across_command_restarts(self):
        """重新启动命令查询同一数据库，结果一致。"""
        first = self.list_candidates(TEST_POSITION, " 林晓 ")
        second = self.list_candidates(TEST_POSITION, " 林晓 ")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(
            [r["id"] for r in json.loads(second.stdout)], [1, 3]
        )

    def test_successful_queries_do_not_modify_data(self):
        self.list_candidates(TEST_POSITION)
        self.list_candidates(TEST_POSITION, "林晓")
        self.list_candidates(TEST_POSITION, " 林晓 ", stage="interviewing")
        self.list_candidates(TEST_POSITION, "陈禾")
        self.list_candidates(DEV_POSITION, "林晓")
        self.assert_state_unchanged()

    # ---- 参数校验 ----

    def test_explicit_empty_or_blank_name_is_required(self):
        for blank in ("", "   ", "\t\n", " "):
            with self.subTest(blank=repr(blank)):
                result = self.list_candidates(TEST_POSITION, blank)
                self.assert_failure_query(result, {"name": "required"})

    def test_blank_name_and_invalid_stage_errors_are_merged(self):
        """题目示例：--name " " 与 --stage offer 一次输出两项错误。"""
        result = self.list_candidates(TEST_POSITION, " ", stage="offer")
        self.assert_failure_query(
            result, {"name": "required", "stage": "invalid"}
        )
        # 字节级约定：标准错误恰好一行紧凑 JSON
        self.assertEqual(
            result.stderr,
            '{"errors":{"name":"required","stage":"invalid"}}\n',
        )

    def test_blank_name_with_blank_position_errors_are_merged(self):
        result = self.list_candidates("  ", " ")
        self.assert_failure_query(
            result, {"position": "required", "name": "required"}
        )

    def test_blank_name_with_empty_stage_and_bad_email_are_merged(self):
        result = self.list_candidates(
            TEST_POSITION, " ", stage="", email="not-an-email"
        )
        self.assert_failure_query(
            result,
            {
                "name": "required",
                "email": "invalid",
                "stage": "required",
            },
        )

    def test_omitted_name_does_not_trigger_required(self):
        """省略 --name 时即使其他条件非法，也不报 name 错误。"""
        result = self.list_candidates(TEST_POSITION, stage="offer")
        self.assert_failure_query(result, {"stage": "invalid"})

    def test_failed_queries_do_not_modify_data(self):
        self.list_candidates(TEST_POSITION, "")
        self.list_candidates(TEST_POSITION, "   ", stage="offer")
        self.list_candidates(" ", " ")
        self.assert_state_unchanged()


class EnglishNameMatchingTests(unittest.TestCase):
    """英文姓名按字节完整匹配：区分大小写、保留内部空白、无子串匹配。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-list-name-edge-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "candidates.sqlite3")

        def add(name, email):
            result = subprocess.run(
                [
                    sys.executable, "-m", "recruiting", "--db", self.db_path,
                    "add", "--name", name, "--email", email,
                    "--position", TEST_POSITION,
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        self.lin_english = add("Lin Xiao", "lin.xiao@example.test")
        self.spaced = add("Lin  Xiao", "lin.spaced@example.test")

    def list_by_name(self, name):
        return subprocess.run(
            [
                sys.executable, "-m", "recruiting", "--db", self.db_path,
                "list", "--position", TEST_POSITION, "--name", name,
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )

    def test_english_name_is_case_sensitive(self):
        exact = self.list_by_name("Lin Xiao")
        self.assertEqual(exact.returncode, 0, exact.stderr)
        self.assertEqual(json.loads(exact.stdout), [self.lin_english])

        for variant in ("lin xiao", "LIN XIAO", "lin Xiao"):
            with self.subTest(variant=variant):
                result = self.list_by_name(variant)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), [])

    def test_internal_whitespace_is_significant_and_no_substring_match(self):
        # 单空格与双空格内部空白不可互换
        self.assertEqual(
            json.loads(self.list_by_name("Lin Xiao").stdout),
            [self.lin_english],
        )
        self.assertEqual(
            json.loads(self.list_by_name("Lin  Xiao").stdout),
            [self.spaced],
        )
        # 去两端空白不等于子串匹配
        self.assertEqual(json.loads(self.list_by_name("Lin").stdout), [])
        self.assertEqual(json.loads(self.list_by_name("Xiao").stdout), [])
        self.assertEqual(json.loads(self.list_by_name("Lin X").stdout), [])


if __name__ == "__main__":
    unittest.main()
