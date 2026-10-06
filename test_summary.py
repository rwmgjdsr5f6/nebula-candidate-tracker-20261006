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

LIN_XIAO = {"name": "林晓", "email": "lin.xiao@example.test"}
ZHOU_NING = {"name": "周宁", "email": "zhou.ning@example.test"}
CHEN_HE = {"name": "陈禾", "email": "chen.he@example.test"}
FANG_CHENG = {"name": "方澄", "email": "fang.cheng@example.test"}
XU_ZHOU = {"name": "许舟", "email": "xu.zhou@example.test"}

STAGES = ("applied", "interviewing", "hired", "rejected")


class SummaryTestCase(unittest.TestCase):
    """登记 Synthetic QA 四名候选人与 Synthetic Dev 一名候选人作为样例数据。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-summary-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "candidates.sqlite3")

        self.lin_xiao = self.add_candidate(POSITION_QA, **LIN_XIAO)
        self.zhou_ning = self.add_candidate(POSITION_QA, **ZHOU_NING)
        self.chen_he = self.add_candidate(POSITION_QA, **CHEN_HE)
        self.fang_cheng = self.add_candidate(POSITION_QA, **FANG_CHENG)
        self.xu_zhou = self.add_candidate(POSITION_DEV, **XU_ZHOU)

        # Synthetic QA: applied、applied、interviewing、hired
        self.chen_he = self.set_stage(self.chen_he, "interviewing")
        self.fang_cheng = self.set_stage(self.fang_cheng, "hired")
        # Synthetic Dev: interviewing
        self.xu_zhou = self.set_stage(self.xu_zhou, "interviewing")

    # ---- 命令调用辅助 ----

    def run_cli(self, *argv):
        return subprocess.run(
            [sys.executable, "-m", "recruiting", "--db", self.db_path, *argv],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )

    def add_candidate(self, position, name, email):
        result = self.run_cli(
            "add", "--name", name, "--email", email, "--position", position
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def set_stage(self, record, stage):
        result = self.run_cli(
            "set-stage", "--id", str(record["id"]), "--stage", stage
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def run_summary(self, position):
        return self.run_cli("summary", "--position", position)

    def list_candidates(self, position):
        result = self.run_cli("list", "--position", position)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def all_candidates(self):
        """两个岗位的完整候选人记录，用于核对汇总不改变数据。"""
        return {
            POSITION_QA: self.list_candidates(POSITION_QA),
            POSITION_DEV: self.list_candidates(POSITION_DEV),
        }

    # ---- 断言辅助 ----

    def expected_counts(self, **overrides):
        counts = {stage: 0 for stage in STAGES}
        counts.update(overrides)
        return counts

    def assert_summary_success(self, result, position, total, counts):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["position"], position)
        self.assertIsInstance(payload["total"], int)
        self.assertEqual(payload["total"], total)
        self.assertEqual(payload["counts"], counts)
        for stage in STAGES:
            self.assertIsInstance(payload["counts"][stage], int)


class SummarySuccessTests(SummaryTestCase):
    def test_summary_counts_each_stage(self):
        """带两端空白的岗位被去除空白后汇总，其他岗位不参与统计。"""
        before = self.all_candidates()
        result = self.run_summary("  Synthetic QA  ")
        self.assert_summary_success(
            result,
            POSITION_QA,
            4,
            self.expected_counts(applied=2, interviewing=1, hired=1),
        )
        # 汇总不改动任何候选人记录
        self.assertEqual(self.all_candidates(), before)

    def test_summary_reflects_stage_change_and_is_repeatable(self):
        """林晓改为 rejected 后四个阶段各 1 人，重复执行结果一致。"""
        self.lin_xiao = self.set_stage(self.lin_xiao, "rejected")

        before = self.all_candidates()
        expected_counts = self.expected_counts(
            applied=1, interviewing=1, hired=1, rejected=1
        )
        first = self.run_summary(POSITION_QA)
        self.assert_summary_success(first, POSITION_QA, 4, expected_counts)
        # 再次启动命令读取同一数据库，结果一致
        second = self.run_summary(POSITION_QA)
        self.assert_summary_success(second, POSITION_QA, 4, expected_counts)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(self.all_candidates(), before)

    def test_unregistered_position_returns_zero_counts(self):
        """从未登记过的岗位总数为 0，四个阶段均保留且为整数 0。"""
        before = self.all_candidates()
        result = self.run_summary("Synthetic Ops")
        self.assert_summary_success(
            result, "Synthetic Ops", 0, self.expected_counts()
        )
        self.assertEqual(self.all_candidates(), before)

    def test_position_matching_is_case_sensitive(self):
        """大小写不同的 synthetic qa 不匹配已有岗位，总数为 0。"""
        before = self.all_candidates()
        result = self.run_summary("synthetic qa")
        self.assert_summary_success(
            result, "synthetic qa", 0, self.expected_counts()
        )
        self.assertEqual(self.all_candidates(), before)


class SummaryEmptyDatabaseTests(unittest.TestCase):
    """全新空数据库上的汇总：不预先登记任何候选人。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-summary-empty-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "candidates.sqlite3")

    def run_cli(self, *argv):
        return subprocess.run(
            [sys.executable, "-m", "recruiting", "--db", self.db_path, *argv],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )

    def test_empty_database_returns_zero_counts(self):
        result = self.run_cli("summary", "--position", POSITION_QA)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["position"], POSITION_QA)
        self.assertEqual(payload["total"], 0)
        self.assertEqual(payload["counts"], {stage: 0 for stage in STAGES})
        for stage in STAGES:
            self.assertIsInstance(payload["counts"][stage], int)
        # 汇总后数据库中仍没有任何候选人
        listed = self.run_cli("list", "--position", POSITION_QA)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(json.loads(listed.stdout), [])


class SummaryFailureTests(SummaryTestCase):
    def test_blank_position_is_required(self):
        """空串或纯空白岗位：退出码 2，stdout 为空，stderr 为单个 JSON 错误。"""
        for blank in ("", "   ", "\t\n"):
            with self.subTest(blank=repr(blank)):
                before = self.all_candidates()
                result = self.run_summary(blank)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertEqual(
                    json.loads(result.stderr),
                    {"errors": {"position": "required"}},
                )
                # 失败的汇总不改动任何候选人记录
                self.assertEqual(self.all_candidates(), before)


if __name__ == "__main__":
    unittest.main()
