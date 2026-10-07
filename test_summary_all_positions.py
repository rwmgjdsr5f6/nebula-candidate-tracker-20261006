"""summary --all-positions 全岗位汇总的回归测试。

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

POSITION_QA = "合成测试岗"
POSITION_DEV = "合成开发岗"

LIN_XIAO = {"name": "林晓", "email": "lin.xiao@example.test"}
ZHOU_NING = {"name": "周宁", "email": "zhou.ning@example.test"}
CHEN_HE = {"name": "陈禾", "email": "chen.he@example.test"}

STAGES = ("applied", "interviewing", "hired", "rejected")


class SummaryAllPositionsTestCase(unittest.TestCase):
    """固定合成数据：合成测试岗有林晓(applied)、周宁(interviewing)；
    合成开发岗有陈禾(hired)。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-summary-all-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "candidates.sqlite3")

        self.add_candidate(POSITION_QA, **LIN_XIAO)
        zhou = self.add_candidate(POSITION_QA, **ZHOU_NING)
        chen = self.add_candidate(POSITION_DEV, **CHEN_HE)
        self.set_stage(zhou, "interviewing")
        self.set_stage(chen, "hired")

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

    def set_position(self, record, position):
        result = self.run_cli(
            "set-position", "--id", str(record["id"]), "--position", position
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def run_all_positions(self):
        return self.run_cli("summary", "--all-positions")

    def run_single(self, position):
        return self.run_cli("summary", "--position", position)

    def list_candidates(self, position):
        result = self.run_cli("list", "--position", position)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def all_candidates(self):
        return {
            POSITION_QA: self.list_candidates(POSITION_QA),
            POSITION_DEV: self.list_candidates(POSITION_DEV),
        }

    # ---- 断言辅助 ----

    def assert_position_summary(self, element, position, total, counts):
        self.assertEqual(set(element.keys()), {"position", "total", "counts"})
        self.assertEqual(element["position"], position)
        self.assertIsInstance(element["total"], int)
        self.assertEqual(element["total"], total)
        self.assertEqual(element["counts"], counts)
        self.assertEqual(
            set(element["counts"].keys()),
            {"applied", "interviewing", "hired", "rejected"},
        )
        for stage in STAGES:
            self.assertIsInstance(element["counts"][stage], int)
        self.assertEqual(element["total"], sum(element["counts"].values()))


class SummaryAllPositionsSuccessTests(SummaryAllPositionsTestCase):
    def test_all_positions_returns_fixed_synthetic_results(self):
        """先返回合成开发岗(total 1, hired 1)，再返回合成测试岗(total 2)。"""
        before = self.all_candidates()
        result = self.run_all_positions()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")

        payload = json.loads(result.stdout)
        self.assertIsInstance(payload, list)
        self.assertEqual([item["position"] for item in payload], [
            POSITION_DEV,
            POSITION_QA,
        ])

        self.assert_position_summary(
            payload[0],
            POSITION_DEV,
            1,
            {"applied": 0, "interviewing": 0, "hired": 1, "rejected": 0},
        )
        self.assert_position_summary(
            payload[1],
            POSITION_QA,
            2,
            {"applied": 1, "interviewing": 1, "hired": 0, "rejected": 0},
        )
        # 汇总不改动任何候选人记录
        self.assertEqual(self.all_candidates(), before)

    def test_results_are_repeatable_across_processes(self):
        """同一数据库未修改时，重复查询与退出进程后重查结果逐字一致。"""
        first = self.run_all_positions()
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.run_all_positions()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(first.stderr, "")
        self.assertEqual(second.stderr, "")

    def test_each_element_matches_single_position_summary(self):
        """全岗位数组中每个元素与对应 --position 单岗位查询内容相同。"""
        all_payload = json.loads(self.run_all_positions().stdout)
        for element in all_payload:
            single = self.run_single(element["position"])
            self.assertEqual(single.returncode, 0, single.stderr)
            self.assertEqual(single.stderr, "")
            self.assertEqual(json.loads(single.stdout), element)

    def test_positions_sorted_by_unicode_codepoint(self):
        """按名称 Unicode 码点升序：ASCII 大写先于小写，小写先于中文。"""
        self.add_candidate("qa", "甲", "jia@example.test")
        self.add_candidate("QA", "乙", "yi@example.test")
        payload = json.loads(self.run_all_positions().stdout)
        names = [item["position"] for item in payload]
        self.assertEqual(
            names,
            sorted(names),
            "岗位名应按 Unicode 码点（Python 字符串序）升序排列",
        )
        self.assertIn("QA", names)
        self.assertIn("qa", names)

    def test_case_and_inner_whitespace_positions_counted_separately(self):
        """大小写或内部空白不同的岗位分别统计，且内部空白保留在结果中。"""
        self.add_candidate("qa", "甲", "jia@example.test")
        self.add_candidate("qa x", "乙", "yi@example.test")

        by_name = {
            item["position"]: item
            for item in json.loads(self.run_all_positions().stdout)
        }
        self.assertEqual(by_name["qa"]["total"], 1)
        self.assertEqual(by_name["qa"]["counts"]["applied"], 1)
        self.assertEqual(by_name["qa x"]["total"], 1)
        self.assertEqual(by_name["qa x"]["counts"]["applied"], 1)
        self.assertEqual(by_name[POSITION_QA]["total"], 2)

    def test_stage_change_is_reflected(self):
        """阶段更正后全岗位汇总反映当前阶段值。"""
        lin = next(
            r for r in self.list_candidates(POSITION_QA) if r["name"] == "林晓"
        )
        self.set_stage(lin, "rejected")
        by_name = {
            item["position"]: item
            for item in json.loads(self.run_all_positions().stdout)
        }
        self.assertEqual(
            by_name[POSITION_QA]["counts"],
            {"applied": 0, "interviewing": 1, "hired": 0, "rejected": 1},
        )
        self.assertEqual(by_name[POSITION_QA]["total"], 2)

    def test_position_change_removes_empty_old_position(self):
        """唯一候选人改到新岗位后，已无候选人的旧岗位不再出现。"""
        chen = next(r for r in self.list_candidates(POSITION_DEV) if r["name"] == "陈禾")
        self.set_position(chen, POSITION_QA)

        names = [
            item["position"]
            for item in json.loads(self.run_all_positions().stdout)
        ]
        self.assertNotIn(POSITION_DEV, names)
        self.assertIn(POSITION_QA, names)
        by_name = {
            item["position"]: item
            for item in json.loads(self.run_all_positions().stdout)
        }
        self.assertEqual(by_name[POSITION_QA]["total"], 3)


class SummaryAllPositionsEmptyDatabaseTests(unittest.TestCase):
    """全新空数据库上的全岗位汇总：不预先登记任何候选人。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-summary-all-empty-test-")
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
        result = self.run_cli("summary", "--all-positions")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "[]\n")
        self.assertEqual(json.loads(result.stdout), [])
        # 查询后数据库中仍没有任何候选人
        listed = self.run_cli("list", "--position", POSITION_QA)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(json.loads(listed.stdout), [])


class SummaryModeConflictTests(SummaryAllPositionsTestCase):
    def test_both_modes_is_usage_error(self):
        """同时指定两种模式：退出码 2，stdout 为空，stderr 显示用法说明。"""
        before = self.all_candidates()
        result = self.run_cli(
            "summary", "--position", POSITION_QA, "--all-positions"
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage", result.stderr)
        self.assertIn("--all-positions", result.stderr)
        self.assertIn("--position", result.stderr)
        # 失败查询不改动任何候选人记录
        self.assertEqual(self.all_candidates(), before)

    def test_neither_mode_is_usage_error(self):
        """两种模式都未提供：退出码 2，stdout 为空，stderr 显示用法说明。"""
        before = self.all_candidates()
        result = self.run_cli("summary")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage", result.stderr)
        self.assertIn("--all-positions", result.stderr)
        self.assertIn("--position", result.stderr)
        self.assertEqual(self.all_candidates(), before)

    def test_blank_single_position_still_reports_required(self):
        """单岗位模式显式传入空串或纯空白仍返回 position required。"""
        for blank in ("", "   ", "\t\n"):
            with self.subTest(blank=repr(blank)):
                before = self.all_candidates()
                result = self.run_cli("summary", "--position", blank)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertEqual(
                    json.loads(result.stderr),
                    {"errors": {"position": "required"}},
                )
                self.assertEqual(self.all_candidates(), before)


if __name__ == "__main__":
    unittest.main()
