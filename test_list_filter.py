"""list 命令岗位与阶段联合查询的回归测试。

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

TEST_POSITION = "合成测试岗"
DEV_POSITION = "合成开发岗"

LIN_XIAO = {"name": "林晓", "email": "lin.xiao@example.test", "position": TEST_POSITION}
ZHOU_NING = {"name": "周宁", "email": "zhou.ning@example.test", "position": TEST_POSITION}
CHEN_HE = {"name": "陈禾", "email": "chen.he@example.test", "position": TEST_POSITION}
XU_ZHOU = {"name": "许舟", "email": "xu.zhou@example.test", "position": DEV_POSITION}

STAGE_OMITTED = object()


class ListQueryTestCase(unittest.TestCase):
    """四名固定候选人：前三人在合成测试岗，许舟在合成开发岗；
    林晓、陈禾、许舟设为 interviewing，周宁保持 applied。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-list-filter-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "candidates.sqlite3")

        self.lin_xiao = self.add_candidate(**LIN_XIAO)
        self.zhou_ning = self.add_candidate(**ZHOU_NING)
        self.chen_he = self.add_candidate(**CHEN_HE)
        self.xu_zhou = self.add_candidate(**XU_ZHOU)

        for record in (self.lin_xiao, self.chen_he, self.xu_zhou):
            updated = self.set_stage(record["id"], "interviewing")
            self.assertEqual(updated.returncode, 0, updated.stderr)
            record["stage"] = "interviewing"

        # 查询前的完整资料快照，成功和失败查询都不得改变它
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

    def set_stage(self, candidate_id, stage):
        return self.run_cli(
            "set-stage", "--id", str(candidate_id), "--stage", stage
        )

    def list_candidates(self, position, stage=STAGE_OMITTED):
        argv = ["list", "--position", position]
        if stage is not STAGE_OMITTED:
            argv.extend(["--stage", stage])
        return self.run_cli(*argv)

    def all_records(self):
        """两个岗位的全部记录，按 id 升序合并。"""
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
        # 标准错误中只有单个 JSON 对象
        payload = result.stderr.strip()
        self.assertNotIn("\n", payload)
        self.assertEqual(json.loads(payload), {"errors": expected_errors})
        # 查询失败不改变任何候选人的资料或阶段
        self.assertEqual(self.all_records(), self.state_snapshot)

    def assert_state_unchanged(self):
        self.assertEqual(self.all_records(), self.state_snapshot)


class ListFilterSuccessTests(ListQueryTestCase):
    def test_position_and_stage_filter_returns_intersection(self):
        """岗位与阶段联合查询只返回同时满足两者的记录，按 id 升序。"""
        result = self.list_candidates(TEST_POSITION, "interviewing")
        records = self.assert_success_query(
            result, [self.lin_xiao, self.chen_he]
        )
        self.assertEqual(
            [record["id"] for record in records],
            sorted(record["id"] for record in records),
        )

    def test_omitting_stage_returns_everyone_at_position(self):
        """省略 --stage 返回该岗位的全部三名候选人（含各自身处的阶段）。"""
        result = self.list_candidates(TEST_POSITION)
        self.assert_success_query(
            result,
            [self.lin_xiao, self.zhou_ning, self.chen_he],
        )

    def test_applied_filter_returns_only_zhou_ning(self):
        result = self.list_candidates(TEST_POSITION, "applied")
        self.assert_success_query(result, [self.zhou_ning])

    def test_hired_and_rejected_filters_return_empty_arrays(self):
        for stage in ("hired", "rejected"):
            with self.subTest(stage=stage):
                result = self.list_candidates(TEST_POSITION, stage)
                self.assert_success_query(result, [])

    def test_stage_filter_excludes_other_positions(self):
        """许舟虽是 interviewing，但岗位不同，不出现在测试岗结果中。"""
        result = self.list_candidates(TEST_POSITION, "interviewing")
        records = self.assert_success_query(
            result, [self.lin_xiao, self.chen_he]
        )
        self.assertNotIn(self.xu_zhou["id"], [r["id"] for r in records])

        dev_result = self.list_candidates(DEV_POSITION, "interviewing")
        self.assert_success_query(dev_result, [self.xu_zhou])

    def test_whitespace_around_position_and_stage_is_trimmed(self):
        expected = [self.lin_xiao, self.chen_he]
        cases = [
            ("  {}  ".format(TEST_POSITION), "  interviewing  "),
            ("\t{}\t".format(TEST_POSITION), "interviewing"),
            (TEST_POSITION, "  interviewing\t\n"),
            (" {} ".format(TEST_POSITION), STAGE_OMITTED),
        ]
        for position, stage in cases:
            with self.subTest(position=repr(position), stage=repr(stage)):
                result = self.list_candidates(position, stage)
                self.assert_success_query(
                    result,
                    [self.lin_xiao, self.zhou_ning, self.chen_he]
                    if stage is STAGE_OMITTED
                    else expected,
                )

    def test_unregistered_position_returns_empty_array(self):
        for position in ("未登记岗位", "Unknown Position"):
            with self.subTest(position=position):
                result = self.list_candidates(position, "interviewing")
                self.assert_success_query(result, [])

                result_without_stage = self.list_candidates(position)
                self.assert_success_query(result_without_stage, [])

    def test_results_consistent_across_command_restarts(self):
        """每次调用都是重新启动的新命令，同一数据库的查询结果一致。"""
        first = self.list_candidates(TEST_POSITION, "interviewing")
        second = self.list_candidates(TEST_POSITION, "interviewing")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(
            json.loads(second.stdout), [self.lin_xiao, self.chen_he]
        )

    def test_successful_queries_do_not_modify_data(self):
        """所有成功查询结束后，四名候选人的资料与阶段保持原样。"""
        self.list_candidates(TEST_POSITION)
        self.list_candidates(TEST_POSITION, "interviewing")
        self.list_candidates(TEST_POSITION, "applied")
        self.list_candidates(TEST_POSITION, "hired")
        self.list_candidates(TEST_POSITION, "rejected")
        self.list_candidates(DEV_POSITION)
        self.list_candidates("未登记岗位", "rejected")
        self.assert_state_unchanged()


class EnglishPositionCaseSensitivityTests(unittest.TestCase):
    """英文岗位按大小写精确匹配：单独用例准备独立数据。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-list-case-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "candidates.sqlite3")

        result = subprocess.run(
            [
                sys.executable, "-m", "recruiting", "--db", self.db_path,
                "add", "--name", "林晓",
                "--email", "lin.xiao@example.test",
                "--position", "Backend Engineer",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.record = json.loads(result.stdout)

    def list_candidates(self, position):
        return subprocess.run(
            [
                sys.executable, "-m", "recruiting", "--db", self.db_path,
                "list", "--position", position,
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )

    def test_english_position_is_case_sensitive(self):
        exact = self.list_candidates("Backend Engineer")
        self.assertEqual(exact.returncode, 0, exact.stderr)
        self.assertEqual(exact.stderr, "")
        self.assertEqual(json.loads(exact.stdout), [self.record])

        for variant in ("backend engineer", "BACKEND ENGINEER", "Backend Eng"):
            with self.subTest(variant=variant):
                result = self.list_candidates(variant)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertEqual(json.loads(result.stdout), [])


class ListFilterValidationTests(ListQueryTestCase):
    def test_omitted_stage_differs_from_explicit_empty_stage(self):
        """省略 --stage 正常返回；显式空值是参数错误。"""
        omitted = self.list_candidates(TEST_POSITION)
        self.assert_success_query(
            omitted,
            [self.lin_xiao, self.zhou_ning, self.chen_he],
        )

        for blank in ("", "   ", "\t\n"):
            with self.subTest(blank=repr(blank)):
                result = self.list_candidates(TEST_POSITION, blank)
                self.assert_failure_query(result, {"stage": "required"})

    def test_unknown_stage_is_invalid(self):
        result = self.list_candidates(TEST_POSITION, "offer")
        self.assert_failure_query(result, {"stage": "invalid"})

    def test_uppercase_stage_is_invalid(self):
        result = self.list_candidates(TEST_POSITION, "INTERVIEWING")
        self.assert_failure_query(result, {"stage": "invalid"})

    def test_blank_position_is_required(self):
        for blank in ("", "   ", "\t\n"):
            with self.subTest(blank=repr(blank)):
                result = self.list_candidates(blank, "interviewing")
                self.assert_failure_query(result, {"position": "required"})

                omitted_stage = self.list_candidates(blank)
                self.assert_failure_query(
                    omitted_stage, {"position": "required"}
                )

    def test_blank_position_and_invalid_stage_errors_are_merged(self):
        """空白岗位与 offer 同时输入时，同一个 errors 对象返回两项错误。"""
        result = self.list_candidates("   ", "offer")
        self.assert_failure_query(
            result,
            {"position": "required", "stage": "invalid"},
        )

    def test_failed_queries_do_not_modify_data(self):
        """全部失败查询结束后，四名候选人的资料与阶段保持原样。"""
        self.list_candidates(TEST_POSITION, "")
        self.list_candidates(TEST_POSITION, "offer")
        self.list_candidates(TEST_POSITION, "INTERVIEWING")
        self.list_candidates("", "interviewing")
        self.list_candidates("   ", "offer")
        self.assert_state_unchanged()


if __name__ == "__main__":
    unittest.main()
