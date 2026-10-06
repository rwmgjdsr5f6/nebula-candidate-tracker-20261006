"""set-stage 命令的回归测试。

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


class SetStageTestCase(unittest.TestCase):
    """每个测试用例都通过 add 入口登记林晓和周宁，并记录返回的完整记录。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-set-stage-test-")
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

    def set_stage(self, candidate_id, stage):
        return self.run_cli("set-stage", "--id", candidate_id, "--stage", stage)

    def list_candidates(self):
        result = self.run_cli("list", "--position", POSITION)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    # ---- 断言辅助 ----

    def expected_records(self, stage_overrides=None):
        """按 id 升序的当前期望记录，stage_overrides 按 id 覆盖阶段。"""
        stage_overrides = stage_overrides or {}
        records = []
        for record in (self.lin_xiao, self.zhou_ning):
            expected = dict(record)
            if record["id"] in stage_overrides:
                expected["stage"] = stage_overrides[record["id"]]
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
        self.assertEqual(json.loads(result.stderr), {"errors": expected_errors})
        # 失败后两名候选人的全部字段与操作前相同
        self.assertEqual(self.list_candidates(), self.expected_records())


class SetStageSuccessTests(SetStageTestCase):
    def test_stage_transitions_persist(self):
        """applied/interviewing/hired/rejected 之间互转，结果持久化。"""
        for stage in ("interviewing", "hired", "rejected", "applied"):
            with self.subTest(stage=stage):
                result = self.set_stage(str(self.lin_xiao["id"]), stage)
                expected = dict(self.lin_xiao, stage=stage)
                self.assert_success(result, expected)
                # 独立命令按岗位查询：目标记录显示新阶段，另一人完整记录不变
                self.assertEqual(
                    self.list_candidates(),
                    self.expected_records({self.lin_xiao["id"]: stage}),
                )

    def test_setting_current_stage_again_succeeds(self):
        """重复设置当前阶段按成功处理。"""
        # 初始阶段即为 applied，重复设置
        result = self.set_stage(str(self.zhou_ning["id"]), "applied")
        self.assert_success(result, self.zhou_ning)

        # 转到 interviewing 后再次设置 interviewing
        first = self.set_stage(str(self.zhou_ning["id"]), "interviewing")
        expected = dict(self.zhou_ning, stage="interviewing")
        self.assert_success(first, expected)
        second = self.set_stage(str(self.zhou_ning["id"]), "interviewing")
        self.assert_success(second, expected)

        self.assertEqual(
            self.list_candidates(),
            self.expected_records({self.zhou_ning["id"]: "interviewing"}),
        )

    def test_id_with_surrounding_whitespace_succeeds(self):
        """带两端空白的 id 与合法阶段仍可成功。"""
        result = self.set_stage("  {}  ".format(self.lin_xiao["id"]), "hired")
        expected = dict(self.lin_xiao, stage="hired")
        self.assert_success(result, expected)
        self.assertEqual(
            self.list_candidates(),
            self.expected_records({self.lin_xiao["id"]: "hired"}),
        )

    def test_id_with_leading_zeros_succeeds(self):
        """前导零不改变编号含义，仍定位同一候选人。"""
        result = self.set_stage("00{}".format(self.lin_xiao["id"]), "hired")
        expected = dict(self.lin_xiao, stage="hired")
        self.assert_success(result, expected)
        self.assertEqual(
            self.list_candidates(),
            self.expected_records({self.lin_xiao["id"]: "hired"}),
        )

    def test_stage_with_surrounding_whitespace_returns_trimmed(self):
        """阶段两端空白被去除，返回值是去空白后的阶段。"""
        result = self.set_stage(str(self.lin_xiao["id"]), "  interviewing  ")
        expected = dict(self.lin_xiao, stage="interviewing")
        self.assert_success(result, expected)
        self.assertEqual(
            self.list_candidates(),
            self.expected_records({self.lin_xiao["id"]: "interviewing"}),
        )


class SetStageFailureTests(SetStageTestCase):
    def test_blank_id_is_invalid(self):
        for blank in ("", "   ", "\t\n"):
            with self.subTest(blank=repr(blank)):
                result = self.set_stage(blank, "interviewing")
                self.assert_failure(result, {"id": "invalid"})

    def test_zero_and_negative_id_are_invalid(self):
        for bad_id in ("0", "-1", "-42"):
            with self.subTest(bad_id=bad_id):
                result = self.set_stage(bad_id, "interviewing")
                self.assert_failure(result, {"id": "invalid"})

    def test_non_numeric_id_is_invalid(self):
        for bad_id in ("abc", "1.5", "1a"):
            with self.subTest(bad_id=bad_id):
                result = self.set_stage(bad_id, "interviewing")
                self.assert_failure(result, {"id": "invalid"})

    def test_blank_id_and_stage_errors_are_merged(self):
        """id 与阶段同时错误时，同一个 errors 对象保留两项错误。"""
        result = self.set_stage("abc", "on-site")
        self.assert_failure(result, {"id": "invalid", "stage": "invalid"})

    def test_blank_stage_is_required(self):
        for blank in ("", "   "):
            with self.subTest(blank=repr(blank)):
                result = self.set_stage(str(self.lin_xiao["id"]), blank)
                self.assert_failure(result, {"stage": "required"})

    def test_unknown_stage_is_invalid(self):
        result = self.set_stage(str(self.lin_xiao["id"]), "offer")
        self.assert_failure(result, {"stage": "invalid"})

    def test_uppercase_stage_is_invalid(self):
        result = self.set_stage(str(self.lin_xiao["id"]), "INTERVIEWING")
        self.assert_failure(result, {"stage": "invalid"})

    def test_unknown_id_is_not_found(self):
        missing_id = str(max(self.lin_xiao["id"], self.zhou_ning["id"]) + 1000)
        result = self.set_stage(missing_id, "hired")
        self.assert_failure(result, {"id": "not_found"})

    def test_validation_happens_before_lookup(self):
        """合法格式但不存在的 id 配上非法阶段：只返回 stage 的 invalid。"""
        missing_id = str(max(self.lin_xiao["id"], self.zhou_ning["id"]) + 1000)
        result = self.set_stage(missing_id, "offer")
        self.assert_failure(result, {"stage": "invalid"})


class SetStageOverflowIdTests(SetStageTestCase):
    """超过 SQLite 有符号整数上限（9223372036854775807）的合法 id。"""

    SQLITE_MAX_INT = 9223372036854775807

    def assert_not_found(self, result):
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        # 标准错误只有单行 JSON，无异常回溯
        self.assertEqual(result.stderr.count("\n"), 1)
        self.assertEqual(json.loads(result.stderr), {"errors": {"id": "not_found"}})
        self.assertEqual(self.list_candidates(), self.expected_records())

    def test_max_int_id_is_not_found(self):
        """整数上限本身：格式合法但无记录，返回 not_found。"""
        result = self.set_stage(str(self.SQLITE_MAX_INT), "interviewing")
        self.assert_not_found(result)

    def test_max_int_plus_one_is_not_found(self):
        """上限加一：不得报 invalid，也不得出现异常回溯。"""
        result = self.set_stage(str(self.SQLITE_MAX_INT + 1), "interviewing")
        self.assert_not_found(result)

    def test_overflow_id_with_leading_zeros_is_not_found(self):
        """带前导零的越界值仍按同一编号处理，返回 not_found。"""
        result = self.set_stage("00" + str(self.SQLITE_MAX_INT + 1), "hired")
        self.assert_not_found(result)

    def test_far_overflow_id_is_not_found(self):
        """远超上限的合法正整数同样返回 not_found。"""
        result = self.set_stage("9" * 30, "interviewing")
        self.assert_not_found(result)

    def test_overflow_id_with_blank_stage_reports_stage_required(self):
        """超大合法 id 与空阶段组合：仅返回 stage 的 required。"""
        result = self.set_stage(str(self.SQLITE_MAX_INT + 1), "   ")
        self.assert_failure(result, {"stage": "required"})

    def test_overflow_id_with_unknown_stage_reports_stage_invalid(self):
        """超大合法 id 与未知阶段组合：仅返回 stage 的 invalid。"""
        result = self.set_stage(str(self.SQLITE_MAX_INT + 1), "offer")
        self.assert_failure(result, {"stage": "invalid"})

    def test_overflow_id_with_uppercase_stage_reports_stage_invalid(self):
        """超大合法 id 与大写阶段组合：仅返回 stage 的 invalid。"""
        result = self.set_stage(str(self.SQLITE_MAX_INT + 1), "INTERVIEWING")
        self.assert_failure(result, {"stage": "invalid"})


if __name__ == "__main__":
    unittest.main()
