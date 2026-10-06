"""list 命令岗位与阶段联合查询的回归测试。

从项目根目录执行 `python -m unittest discover` 即可发现并运行。
每个测试都在独立的临时目录中创建全新的 SQLite 数据库，
通过子进程调用 `python -m recruiting` 公开命令，结束后清理临时目录，
不读取也不改动使用者已有的任何数据。

固定场景：依次登记林晓、周宁、陈禾（岗位均为合成测试岗）和许舟
（岗位为合成开发岗），再将林晓、陈禾和许舟设为 interviewing，
周宁保持 applied。
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

LIN_XIAO = {"name": "林晓", "email": "lin.xiao@example.test"}
ZHOU_NING = {"name": "周宁", "email": "zhou.ning@example.test"}
CHEN_HE = {"name": "陈禾", "email": "chen.he@example.test"}
XU_ZHOU = {"name": "许舟", "email": "xu.zhou@example.test"}


class ListPositionStageTestCase(unittest.TestCase):
    """每个用例独立登记四名候选人并设置阶段，结束后随临时目录一并清理。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-list-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "candidates.sqlite3")

        # 严格按固定顺序登记，id 随登记顺序递增
        self.lin_xiao = self.add_candidate(LIN_XIAO["name"], LIN_XIAO["email"], TEST_POSITION)
        self.zhou_ning = self.add_candidate(ZHOU_NING["name"], ZHOU_NING["email"], TEST_POSITION)
        self.chen_he = self.add_candidate(CHEN_HE["name"], CHEN_HE["email"], TEST_POSITION)
        self.xu_zhou = self.add_candidate(XU_ZHOU["name"], XU_ZHOU["email"], DEV_POSITION)

        # 林晓、陈禾、许舟转为 interviewing；周宁保持 applied
        for candidate in (self.lin_xiao, self.chen_he, self.xu_zhou):
            candidate["stage"] = "interviewing"
            result = self.run_cli(
                "set-stage", "--id", str(candidate["id"]), "--stage", "interviewing"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")

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

    def list_candidates(self, position, stage=None):
        argv = ["list", "--position", position]
        if stage is not None:
            argv.extend(["--stage", stage])
        return self.run_cli(*argv)

    def snapshot_all(self):
        """按两个岗位查询全部候选人的当前完整资料，用于核对查询无副作用。"""
        records = []
        for position in (TEST_POSITION, DEV_POSITION):
            result = self.list_candidates(position)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            records.extend(json.loads(result.stdout))
        records.sort(key=lambda record: record["id"])
        return records

    def expected_snapshot(self):
        return sorted(
            (
                dict(self.lin_xiao),
                dict(self.zhou_ning),
                dict(self.chen_he),
                dict(self.xu_zhou),
            ),
            key=lambda record: record["id"],
        )

    # ---- 断言辅助 ----

    def assert_success_list(self, result, expected_records):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        records = json.loads(result.stdout)
        self.assertIsInstance(records, list)
        self.assertEqual(records, expected_records)
        for record in records:
            # 每条记录保留完整候选人字段
            self.assertEqual(
                set(record.keys()), {"id", "name", "email", "position", "stage"}
            )
        # id 升序
        ids = [record["id"] for record in records]
        self.assertEqual(ids, sorted(ids))
        return records

    def assert_failure_list(self, result, expected_errors, before_snapshot):
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        # 标准错误仅含单个 JSON 对象
        error_payload = json.loads(result.stderr)
        self.assertEqual(error_payload, {"errors": expected_errors})
        # 失败查询不改变任何候选人的资料或阶段
        self.assertEqual(self.snapshot_all(), before_snapshot)


class ListPositionStageSuccessTests(ListPositionStageTestCase):
    def test_position_and_interviewing_stage_returns_two(self):
        """合成测试岗 + interviewing 只返回林晓和陈禾，按 id 升序。"""
        before = self.snapshot_all()
        result = self.list_candidates(TEST_POSITION, "interviewing")
        self.assert_success_list(result, [self.lin_xiao, self.chen_he])
        # 成功查询不改变任何候选人的资料或阶段
        self.assertEqual(self.snapshot_all(), before)

    def test_position_without_stage_returns_all_three(self):
        """省略 --stage 返回该岗位的前三人，按登记 id 升序。"""
        before = self.snapshot_all()
        result = self.list_candidates(TEST_POSITION)
        self.assert_success_list(
            result, [self.lin_xiao, self.zhou_ning, self.chen_he]
        )
        self.assertEqual(self.snapshot_all(), before)

    def test_applied_stage_returns_only_zhou_ning(self):
        """applied 筛选只返回仍处于 applied 的周宁。"""
        result = self.list_candidates(TEST_POSITION, "applied")
        self.assert_success_list(result, [self.zhou_ning])

    def test_hired_and_rejected_stages_return_empty(self):
        """hired 和 rejected 筛选均返回空数组。"""
        for stage in ("hired", "rejected"):
            with self.subTest(stage=stage):
                result = self.list_candidates(TEST_POSITION, stage)
                self.assert_success_list(result, [])

    def test_other_position_is_excluded_by_stage_filter(self):
        """联合筛选不会串岗：合成开发岗的 interviewing 只有许舟。"""
        result = self.list_candidates(DEV_POSITION, "interviewing")
        self.assert_success_list(result, [self.xu_zhou])

    def test_surrounding_whitespace_does_not_change_result(self):
        """岗位与阶段两端空白不影响结果。"""
        padded = self.list_candidates("  {}  ".format(TEST_POSITION), "  interviewing  ")
        self.assert_success_list(padded, [self.lin_xiao, self.chen_he])

        plain = self.list_candidates(TEST_POSITION, "interviewing")
        self.assert_success_list(plain, [self.lin_xiao, self.chen_he])

        # 省略阶段时岗位两端空白同样被去除
        padded_position = self.list_candidates("  {}  ".format(TEST_POSITION))
        self.assert_success_list(
            padded_position, [self.lin_xiao, self.zhou_ning, self.chen_he]
        )

    def test_unregistered_position_returns_empty(self):
        """未登记的岗位返回空数组。"""
        result = self.list_candidates("不存在的合成岗位", "interviewing")
        self.assert_success_list(result, [])
        result_without_stage = self.list_candidates("不存在的合成岗位")
        self.assert_success_list(result_without_stage, [])

    def test_english_position_is_case_sensitive(self):
        """英文岗位区分大小写，大小写不一致返回空数组。"""
        english = self.add_candidate("安途", "an.tu@example.test", "QA Engineer")
        english["stage"] = "applied"

        exact = self.list_candidates("QA Engineer", "applied")
        self.assert_success_list(exact, [english])

        different_case = self.list_candidates("qa engineer", "applied")
        self.assert_success_list(different_case, [])

        upper_case = self.list_candidates("QA ENGINEER")
        self.assert_success_list(upper_case, [])

    def test_results_persist_across_separate_invocations(self):
        """重新启动命令查询同一数据库，结果仍一致。"""
        first = self.list_candidates(TEST_POSITION, "interviewing")
        expected = [self.lin_xiao, self.chen_he]
        self.assert_success_list(first, expected)

        # 全新子进程重新查询
        second = self.list_candidates(TEST_POSITION, "interviewing")
        self.assert_success_list(second, expected)

        third = self.list_candidates(TEST_POSITION)
        self.assert_success_list(
            third, [self.lin_xiao, self.zhou_ning, self.chen_he]
        )


class ListPositionStageFailureTests(ListPositionStageTestCase):
    def test_empty_stage_is_required(self):
        """空字符串或纯空白阶段返回 stage 的 required。"""
        before = self.snapshot_all()
        for blank in ("", "   ", "\t\n"):
            with self.subTest(blank=repr(blank)):
                result = self.list_candidates(TEST_POSITION, blank)
                self.assert_failure_list(result, {"stage": "required"}, before)

    def test_unknown_and_uppercase_stage_are_invalid(self):
        """offer 或 INTERVIEWING 返回 stage 的 invalid。"""
        before = self.snapshot_all()
        for stage in ("offer", "INTERVIEWING"):
            with self.subTest(stage=stage):
                result = self.list_candidates(TEST_POSITION, stage)
                self.assert_failure_list(result, {"stage": "invalid"}, before)

    def test_blank_position_is_required(self):
        """空白岗位返回 position 的 required。"""
        before = self.snapshot_all()
        for blank in ("", "   ", "\t\n"):
            with self.subTest(blank=repr(blank)):
                result = self.list_candidates(blank, "interviewing")
                self.assert_failure_list(result, {"position": "required"}, before)

    def test_blank_position_without_stage_is_required(self):
        """空白岗位且省略阶段时，只返回 position 的 required。"""
        before = self.snapshot_all()
        result = self.list_candidates("   ")
        self.assert_failure_list(result, {"position": "required"}, before)

    def test_blank_position_and_invalid_stage_errors_are_merged(self):
        """空白岗位与 offer 同时输入，同一 errors 对象返回两项错误。"""
        before = self.snapshot_all()
        result = self.list_candidates("   ", "offer")
        self.assert_failure_list(
            result, {"position": "required", "stage": "invalid"}, before
        )


if __name__ == "__main__":
    unittest.main()
