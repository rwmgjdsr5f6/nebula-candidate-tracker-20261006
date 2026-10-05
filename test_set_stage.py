"""set-stage 命令的回归测试。

从项目根目录执行 `python -m unittest discover` 即可发现并运行。
每个测试使用独立的临时 SQLite 数据库，测试之间数据互不影响，
结束后自动清理，不读取或改动使用者已有的任何数据。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

POSITION = "测试工程师"
LIN_XIAO = {"name": "林晓", "email": "lin.xiao@example.test"}
ZHOU_NING = {"name": "周宁", "email": "zhou.ning@example.test"}
MISSING_ID = "999999"


class SetStageTestCase(unittest.TestCase):
    """围绕 `python -m recruiting set-stage` 公开入口的行为测试。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "candidates.sqlite3")
        # 固定样例：通过现有 add 入口登记林晓和周宁，同一合成岗位
        self.lin_xiao = self.add_candidate(**LIN_XIAO)
        self.zhou_ning = self.add_candidate(**ZHOU_NING)

    # ---- 命令执行与查询辅助 ----

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "recruiting", "--db", self.db_path, *args],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )

    def add_candidate(self, name, email):
        result = self.run_cli(
            "add", "--name", name, "--email", email, "--position", POSITION
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def set_stage(self, candidate_id, stage):
        return self.run_cli("set-stage", "--id", candidate_id, "--stage", stage)

    def list_candidates(self):
        result = self.run_cli("list", "--position", POSITION)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    # ---- 断言辅助（均按 JSON 内容比较，不依赖键序或空白排版）----

    def expected_record(self, base, stage):
        return {
            "id": base["id"],
            "name": base["name"],
            "email": base["email"],
            "position": POSITION,
            "stage": stage,
        }

    def assert_success(self, result, expected):
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), expected)

    def assert_failure(self, result, errors, snapshot):
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        # 标准错误只有一个可解析的 JSON 错误对象
        self.assertEqual(json.loads(result.stderr), {"errors": errors})
        # 失败后再次查询，两名候选人全部字段与操作前相同
        self.assertEqual(self.list_candidates(), snapshot)

    # ---- 成功场景 ----

    def test_stage_transitions(self):
        stages = ["interviewing", "hired", "rejected", "applied"]
        for stage in stages:
            with self.subTest(stage=stage):
                result = self.set_stage(str(self.lin_xiao["id"]), stage)
                self.assert_success(
                    result, self.expected_record(self.lin_xiao, stage)
                )

    def test_repeat_current_stage_succeeds(self):
        # 重复设置初始阶段 applied
        result = self.set_stage(str(self.lin_xiao["id"]), "applied")
        self.assert_success(result, self.expected_record(self.lin_xiao, "applied"))
        # 修改后再重复设置同一阶段
        self.set_stage(str(self.lin_xiao["id"]), "interviewing")
        result = self.set_stage(str(self.lin_xiao["id"]), "interviewing")
        self.assert_success(
            result, self.expected_record(self.lin_xiao, "interviewing")
        )

    def test_id_and_stage_whitespace_trimmed(self):
        result = self.set_stage(
            "  {}  ".format(self.lin_xiao["id"]), "  interviewing\t"
        )
        self.assert_success(
            result, self.expected_record(self.lin_xiao, "interviewing")
        )

    def test_result_persisted_and_other_candidate_untouched(self):
        self.set_stage(str(self.lin_xiao["id"]), "hired")
        # 以一次独立命令按岗位查询，确认结果已持久化
        records = self.list_candidates()
        self.assertEqual(
            records,
            [
                self.expected_record(self.lin_xiao, "hired"),
                self.expected_record(self.zhou_ning, "applied"),
            ],
        )

    # ---- 失败场景：id 校验 ----

    def test_blank_id_invalid(self):
        snapshot = self.list_candidates()
        result = self.set_stage("   ", "interviewing")
        self.assert_failure(result, {"id": "invalid"}, snapshot)

    def test_zero_id_invalid(self):
        snapshot = self.list_candidates()
        result = self.set_stage("0", "interviewing")
        self.assert_failure(result, {"id": "invalid"}, snapshot)

    def test_negative_id_invalid(self):
        snapshot = self.list_candidates()
        result = self.set_stage("-3", "interviewing")
        self.assert_failure(result, {"id": "invalid"}, snapshot)

    def test_non_numeric_id_invalid(self):
        snapshot = self.list_candidates()
        result = self.set_stage("abc", "interviewing")
        self.assert_failure(result, {"id": "invalid"}, snapshot)

    # ---- 失败场景：stage 校验 ----

    def test_blank_stage_required(self):
        snapshot = self.list_candidates()
        result = self.set_stage(str(self.lin_xiao["id"]), "   ")
        self.assert_failure(result, {"stage": "required"}, snapshot)

    def test_unknown_stage_invalid(self):
        snapshot = self.list_candidates()
        result = self.set_stage(str(self.lin_xiao["id"]), "archived")
        self.assert_failure(result, {"stage": "invalid"}, snapshot)

    def test_uppercase_stage_invalid(self):
        snapshot = self.list_candidates()
        result = self.set_stage(str(self.lin_xiao["id"]), "INTERVIEWING")
        self.assert_failure(result, {"stage": "invalid"}, snapshot)

    # ---- 失败场景：组合与查找 ----

    def test_both_id_and_stage_errors_merged(self):
        snapshot = self.list_candidates()
        result = self.set_stage("abc", "archived")
        self.assert_failure(
            result, {"id": "invalid", "stage": "invalid"}, snapshot
        )

    def test_missing_id_not_found(self):
        snapshot = self.list_candidates()
        result = self.set_stage(MISSING_ID, "interviewing")
        self.assert_failure(result, {"id": "not_found"}, snapshot)

    def test_validation_precedes_lookup(self):
        # 合法但不存在的 id 配上非法阶段：只返回 stage 的 invalid
        snapshot = self.list_candidates()
        result = self.set_stage(MISSING_ID, "archived")
        self.assert_failure(result, {"stage": "invalid"}, snapshot)


if __name__ == "__main__":
    unittest.main()
