"""summary 命令的回归测试。

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

POSITION_QA = "Synthetic QA"
POSITION_DEV = "Synthetic Dev"

# 登记在 Synthetic QA 的四名候选人及其目标阶段
QA_CANDIDATES = (
    {"name": "林晓", "email": "lin.xiao@example.test", "stage": "applied"},
    {"name": "周宁", "email": "zhou.ning@example.test", "stage": "applied"},
    {"name": "陈禾", "email": "chen.he@example.test", "stage": "interviewing"},
    {"name": "方澄", "email": "fang.cheng@example.test", "stage": "hired"},
)
# 登记在 Synthetic Dev 的候选人及其目标阶段
DEV_CANDIDATES = (
    {"name": "许舟", "email": "xu.zhou@example.test", "stage": "interviewing"},
)

ALL_STAGES = ("applied", "interviewing", "hired", "rejected")


class SummaryTestCase(unittest.TestCase):
    """每个测试用例都通过 add/set-stage 入口准备好两个岗位的样例数据。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-summary-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "candidates.sqlite3")
        self.qa_records = self.seed_candidates(POSITION_QA, QA_CANDIDATES)
        self.dev_records = self.seed_candidates(POSITION_DEV, DEV_CANDIDATES)

    # ---- 命令调用辅助 ----

    def run_cli(self, *argv, db_path=None):
        return subprocess.run(
            [sys.executable, "-m", "recruiting", "--db", db_path or self.db_path, *argv],
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
        result = self.run_cli(
            "set-stage", "--id", str(candidate_id), "--stage", stage
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def seed_candidates(self, position, candidates):
        """登记候选人并按需调整阶段，返回 add 返回的完整记录列表。"""
        records = []
        for candidate in candidates:
            record = self.add_candidate(
                candidate["name"], candidate["email"], position
            )
            if candidate["stage"] != "applied":
                record = self.set_stage(record["id"], candidate["stage"])
            records.append(record)
        return records

    def list_position(self, position):
        result = self.run_cli("list", "--position", position)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def run_summary(self, position, db_path=None):
        return self.run_cli("summary", "--position", position, db_path=db_path)

    # ---- 断言辅助 ----

    def snapshot_candidates(self):
        """两个岗位当前的全部记录，用于核对汇总不改动候选人数据。"""
        return {
            POSITION_QA: self.list_position(POSITION_QA),
            POSITION_DEV: self.list_position(POSITION_DEV),
        }

    def assert_summary_success(self, result, expected_position, expected_counts):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["position"], expected_position)
        self.assertIsInstance(payload["total"], int)
        self.assertEqual(payload["total"], sum(expected_counts.values()))
        self.assertEqual(payload["counts"], expected_counts)
        for stage in ALL_STAGES:
            self.assertIsInstance(payload["counts"][stage], int)
        return payload

    def assert_summary_failure(self, result, expected_errors):
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(result.stderr), {"errors": expected_errors})


class SummaryCountsTests(SummaryTestCase):
    def test_summary_counts_only_requested_position(self):
        """带两端空白的岗位名按去空白后统计，其他岗位不参与。"""
        before = self.snapshot_candidates()

        result = self.run_summary("  {}  ".format(POSITION_QA))
        self.assert_summary_success(
            result,
            POSITION_QA,
            {"applied": 2, "interviewing": 1, "hired": 1, "rejected": 0},
        )

        # 汇总成功后两个岗位的候选人记录与操作前完全一致
        self.assertEqual(self.snapshot_candidates(), before)

    def test_summary_reflects_stage_change_and_is_repeatable(self):
        """林晓改为 rejected 后四个阶段各为 1，重复执行结果一致。"""
        lin_xiao = self.qa_records[0]
        self.set_stage(lin_xiao["id"], "rejected")
        before = self.snapshot_candidates()

        expected_counts = {"applied": 1, "interviewing": 1, "hired": 1, "rejected": 1}
        first = self.run_summary(POSITION_QA)
        first_payload = self.assert_summary_success(
            first, POSITION_QA, expected_counts
        )

        # 再次启动命令读取同一数据库，结果一致
        second = self.run_summary(POSITION_QA)
        second_payload = self.assert_summary_success(
            second, POSITION_QA, expected_counts
        )
        self.assertEqual(second_payload, first_payload)

        self.assertEqual(self.snapshot_candidates(), before)

    def test_summary_for_dev_position(self):
        """Synthetic Dev 只有许舟一人处于 interviewing。"""
        result = self.run_summary(POSITION_DEV)
        self.assert_summary_success(
            result,
            POSITION_DEV,
            {"applied": 0, "interviewing": 1, "hired": 0, "rejected": 0},
        )


class SummaryEmptyResultTests(SummaryTestCase):
    def assert_empty_summary(self, result, expected_position):
        self.assert_summary_success(
            result,
            expected_position,
            {"applied": 0, "interviewing": 0, "hired": 0, "rejected": 0},
        )

    def test_unregistered_position_returns_zero(self):
        before = self.snapshot_candidates()
        result = self.run_summary("Synthetic Marketing")
        self.assert_empty_summary(result, "Synthetic Marketing")
        self.assertEqual(self.snapshot_candidates(), before)

    def test_differently_cased_position_returns_zero(self):
        """岗位名区分大小写，synthetic qa 不匹配 Synthetic QA。"""
        before = self.snapshot_candidates()
        result = self.run_summary("synthetic qa")
        self.assert_empty_summary(result, "synthetic qa")
        self.assertEqual(self.snapshot_candidates(), before)

    def test_brand_new_empty_database_returns_zero(self):
        """全新空数据库上汇总已有岗位也返回总数 0。"""
        empty_db = os.path.join(self.tmpdir, "empty.sqlite3")
        result = self.run_summary(POSITION_QA, db_path=empty_db)
        self.assert_empty_summary(result, POSITION_QA)


class SummaryValidationTests(SummaryTestCase):
    def test_blank_position_is_required(self):
        for blank in ("", "   ", "\t\n"):
            with self.subTest(blank=repr(blank)):
                before = self.snapshot_candidates()
                result = self.run_summary(blank)
                self.assert_summary_failure(result, {"position": "required"})
                # 失败的汇总不改动任何候选人记录
                self.assertEqual(self.snapshot_candidates(), before)


if __name__ == "__main__":
    unittest.main()
