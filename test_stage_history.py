"""stage-history 命令与 set-stage 历史记录的回归测试。

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


class StageHistoryTestCase(unittest.TestCase):
    """每个测试用例都通过 add 入口登记林晓和周宁，并记录返回的完整记录。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-stage-history-test-")
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

    def stage_history(self, candidate_id):
        return self.run_cli("stage-history", "--id", candidate_id)

    def list_candidates(self):
        result = self.run_cli("list", "--position", POSITION)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    # ---- 断言辅助 ----

    def assert_history(self, candidate_id, expected):
        result = self.stage_history(str(candidate_id))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), expected)

    def assert_failure(self, result, expected_errors):
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        # 标准错误只有单行 JSON，不出现回溯等多行输出
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertEqual(json.loads(result.stderr), {"errors": expected_errors})


class StageHistoryRecordTests(StageHistoryTestCase):
    def test_transitions_accumulate_in_order(self):
        """每次成功改成不同阶段按发生顺序累积 from/to。"""
        lin_id = self.lin_xiao["id"]
        self.set_stage(str(lin_id), "interviewing")
        self.set_stage(str(lin_id), "hired")
        self.assert_history(
            lin_id,
            [
                {"from_stage": "applied", "to_stage": "interviewing"},
                {"from_stage": "interviewing", "to_stage": "hired"},
            ],
        )

    def test_registration_does_not_create_history(self):
        """登记时的 applied 不算变更，未变更者历史为空。"""
        self.assert_history(self.lin_xiao["id"], [])
        self.assert_history(self.zhou_ning["id"], [])

    def test_setting_current_stage_again_adds_no_history(self):
        """重复设置当前阶段仍成功，但不增加历史。"""
        lin_id = self.lin_xiao["id"]
        # 初始阶段 applied 上重复设置 applied
        result = self.set_stage(str(lin_id), "applied")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_history(lin_id, [])

        self.set_stage(str(lin_id), "interviewing")
        result = self.set_stage(str(lin_id), "interviewing")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_history(
            lin_id,
            [{"from_stage": "applied", "to_stage": "interviewing"}],
        )

    def test_any_direction_transition_is_recorded(self):
        """阶段允许任意互转，包括回到此前阶段，每次都记录。"""
        lin_id = self.lin_xiao["id"]
        for stage in ("interviewing", "rejected", "applied", "hired"):
            self.set_stage(str(lin_id), stage)
        self.assert_history(
            lin_id,
            [
                {"from_stage": "applied", "to_stage": "interviewing"},
                {"from_stage": "interviewing", "to_stage": "rejected"},
                {"from_stage": "rejected", "to_stage": "applied"},
                {"from_stage": "applied", "to_stage": "hired"},
            ],
        )

    def test_failed_set_stage_adds_no_history(self):
        """set-stage 失败时当前阶段和历史均不变。"""
        lin_id = self.lin_xiao["id"]
        self.set_stage(str(lin_id), "interviewing")
        before = self.list_candidates()

        for bad_stage in ("", "offer", "INTERVIEWING"):
            with self.subTest(bad_stage=repr(bad_stage)):
                result = self.set_stage(str(lin_id), bad_stage)
                self.assertEqual(result.returncode, 2)
        result = self.set_stage(str(lin_id + 1000), "hired")
        self.assertEqual(result.returncode, 2)

        self.assert_history(
            lin_id,
            [{"from_stage": "applied", "to_stage": "interviewing"}],
        )
        self.assertEqual(self.list_candidates(), before)

    def test_profile_corrections_do_not_touch_history(self):
        """姓名、邮箱或岗位更正不改变历史归属，也不增加历史。"""
        lin_id = self.lin_xiao["id"]
        self.set_stage(str(lin_id), "interviewing")
        expected = [{"from_stage": "applied", "to_stage": "interviewing"}]

        result = self.run_cli(
            "set-name", "--id", str(lin_id), "--name", "林小晓"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli(
            "set-email", "--id", str(lin_id), "--email", "lin.x2@example.test"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli(
            "set-position", "--id", str(lin_id), "--position", "另一岗位"
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assert_history(lin_id, expected)
        self.assert_history(self.zhou_ning["id"], [])

    def test_histories_are_tracked_per_candidate(self):
        """两名候选人的历史互不影响。"""
        lin_id = self.lin_xiao["id"]
        zhou_id = self.zhou_ning["id"]
        self.set_stage(str(lin_id), "interviewing")
        self.set_stage(str(zhou_id), "rejected")
        self.set_stage(str(lin_id), "hired")

        self.assert_history(
            lin_id,
            [
                {"from_stage": "applied", "to_stage": "interviewing"},
                {"from_stage": "interviewing", "to_stage": "hired"},
            ],
        )
        self.assert_history(
            zhou_id,
            [{"from_stage": "applied", "to_stage": "rejected"}],
        )

    def test_query_does_not_change_records_or_summary(self):
        """查询历史不改动候选人资料或汇总统计。"""
        lin_id = self.lin_xiao["id"]
        self.set_stage(str(lin_id), "interviewing")
        before_list = self.list_candidates()
        before_summary = self.run_cli("summary", "--position", POSITION)

        self.assert_history(lin_id, [{"from_stage": "applied", "to_stage": "interviewing"}])
        self.assert_history(self.zhou_ning["id"], [])

        self.assertEqual(self.list_candidates(), before_list)
        after_summary = self.run_cli("summary", "--position", POSITION)
        self.assertEqual(after_summary.stdout, before_summary.stdout)

    def test_history_persists_across_restarts(self):
        """历史保存在数据库中，独立进程重复查询结果一致。"""
        lin_id = self.lin_xiao["id"]
        self.set_stage(str(lin_id), "interviewing")
        self.set_stage(str(lin_id), "hired")
        expected = [
            {"from_stage": "applied", "to_stage": "interviewing"},
            {"from_stage": "interviewing", "to_stage": "hired"},
        ]
        self.assert_history(lin_id, expected)
        self.assert_history(lin_id, expected)


class StageHistoryIdRuleTests(StageHistoryTestCase):
    def test_id_with_surrounding_whitespace_succeeds(self):
        result = self.stage_history("  {}  ".format(self.lin_xiao["id"]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), [])

    def test_id_with_leading_zeros_succeeds(self):
        result = self.stage_history("000{}".format(self.lin_xiao["id"]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])

    def test_blank_id_is_invalid(self):
        for blank in ("", "   ", "\t\n"):
            with self.subTest(blank=repr(blank)):
                self.assert_failure(
                    self.stage_history(blank), {"id": "invalid"}
                )

    def test_zero_and_negative_id_are_invalid(self):
        for bad_id in ("0", "0000", "-1", "-42"):
            with self.subTest(bad_id=bad_id):
                self.assert_failure(
                    self.stage_history(bad_id), {"id": "invalid"}
                )

    def test_non_numeric_id_is_invalid(self):
        for bad_id in ("abc", "1.5", "1a", "+1", "１２"):
            with self.subTest(bad_id=bad_id):
                self.assert_failure(
                    self.stage_history(bad_id), {"id": "invalid"}
                )

    def test_unknown_id_is_not_found(self):
        missing_id = str(max(self.lin_xiao["id"], self.zhou_ning["id"]) + 1000)
        self.assert_failure(self.stage_history(missing_id), {"id": "not_found"})

    def test_id_above_sqlite_int64_max_is_not_found(self):
        self.assert_failure(
            self.stage_history("9223372036854775808"), {"id": "not_found"}
        )

    def test_missing_id_option_is_usage_error(self):
        """缺少 --id 时标准错误为用法说明，标准输出为空，退出码为 2。"""
        result = self.run_cli("stage-history")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage", result.stderr)


class StageHistoryExistingDatabaseTests(unittest.TestCase):
    """已有数据库（无 stage_history 表）直接可用，不补造过去历史。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-stage-history-legacy-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "candidates.sqlite3")

    def run_cli(self, *argv):
        return subprocess.run(
            [sys.executable, "-m", "recruiting", "--db", self.db_path, *argv],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )

    def test_legacy_database_without_history_table(self):
        """手工建立只有 candidates 表的旧库：首次变更以当时阶段为起点。"""
        import sqlite3

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE candidates ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " name TEXT NOT NULL, email TEXT NOT NULL,"
            " position TEXT NOT NULL, stage TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO candidates (name, email, position, stage)"
            " VALUES (?, ?, ?, ?)",
            ("林晓", "lin.xiao@example.test", POSITION, "interviewing"),
        )
        conn.commit()
        conn.close()

        # 旧库首次查询：历史为空，不补造过去历史
        result = self.run_cli("stage-history", "--id", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])

        # 首次变更以当时保存的阶段 interviewing 为起点
        result = self.run_cli("set-stage", "--id", "1", "--stage", "hired")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli("stage-history", "--id", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            [{"from_stage": "interviewing", "to_stage": "hired"}],
        )


if __name__ == "__main__":
    unittest.main()
