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

# 全岗位汇总验收使用的固定合成数据（中文岗位）。
POSITION_QA_CN = "合成测试岗"
POSITION_DEV_CN = "合成开发岗"

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


class SummaryAllPositionsTestCase(unittest.TestCase):
    """全岗位汇总的固定合成数据：合成测试岗两人、合成开发岗一人。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-summary-all-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "candidates.sqlite3")

        self.lin_xiao = self.add_candidate(POSITION_QA_CN, **LIN_XIAO)
        self.zhou_ning = self.add_candidate(POSITION_QA_CN, **ZHOU_NING)
        self.chen_he = self.add_candidate(POSITION_DEV_CN, **CHEN_HE)

        # 合成测试岗：林晓 applied、周宁 interviewing；合成开发岗：陈禾 hired
        self.zhou_ning = self.set_stage(self.zhou_ning, "interviewing")
        self.chen_he = self.set_stage(self.chen_he, "hired")

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

    def run_summary(self, position):
        return self.run_cli("summary", "--position", position)

    def expected_summary(self, position, **overrides):
        counts = {stage: 0 for stage in STAGES}
        counts.update(overrides)
        return {"position": position, "total": sum(counts.values()), "counts": counts}

    def assert_integer_counts(self, element):
        self.assertEqual(set(element["counts"]), set(STAGES))
        self.assertIsInstance(element["total"], int)
        for stage in STAGES:
            self.assertIsInstance(element["counts"][stage], int)
        self.assertEqual(element["total"], sum(element["counts"].values()))


class SummaryAllPositionsTests(SummaryAllPositionsTestCase):
    EXPECTED = [
        {
            "position": POSITION_DEV_CN,
            "total": 1,
            "counts": {"applied": 0, "interviewing": 0, "hired": 1, "rejected": 0},
        },
        {
            "position": POSITION_QA_CN,
            "total": 2,
            "counts": {"applied": 1, "interviewing": 1, "hired": 0, "rejected": 0},
        },
    ]

    def test_all_positions_returns_ordered_array(self):
        """单行 JSON 数组：先合成开发岗后合成测试岗，结构与单岗位汇总一致。"""
        result = self.run_all_positions()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        # 标准输出为单行 JSON（以换行结尾，无多余行）
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload, self.EXPECTED)
        positions = [element["position"] for element in payload]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(len(positions), len(set(positions)))
        for element in payload:
            self.assert_integer_counts(element)

    def test_repeated_queries_are_identical(self):
        """不修改记录时，重复查询（每次都是全新进程）结果逐字节一致。"""
        first = self.run_all_positions()
        second = self.run_all_positions()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(json.loads(first.stdout), self.EXPECTED)

    def test_single_position_matches_array_element(self):
        """原单岗位查询仍返回单个对象，内容与数组中对应元素相同。"""
        payload = json.loads(self.run_all_positions().stdout)
        for position in (POSITION_DEV_CN, POSITION_QA_CN):
            with self.subTest(position=position):
                result = self.run_summary(position)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                single = json.loads(result.stdout)
                [element] = [e for e in payload if e["position"] == position]
                self.assertEqual(single, element)

    def test_trimming_position_does_not_create_extra_group(self):
        """带两端空白的单岗位查询精确匹配去空白后的岗位，不影响全岗位分组。"""
        result = self.run_summary("  " + POSITION_QA_CN + "\t")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), self.EXPECTED[1])
        self.assertEqual(json.loads(self.run_all_positions().stdout), self.EXPECTED)

    def test_case_and_inner_whitespace_positions_are_separate(self):
        """大小写或内部空白不同的岗位分别统计，按名称码点升序排列。"""
        self.add_candidate("Synthetic Ops", FANG_CHENG["name"], FANG_CHENG["email"])
        self.add_candidate("synthetic ops", XU_ZHOU["name"], XU_ZHOU["email"])
        self.add_candidate("Synthetic  Ops", LIN_XIAO["name"], "lin2@example.test")

        result = self.run_all_positions()
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        positions = [element["position"] for element in payload]
        self.assertEqual(
            positions,
            sorted(
                [
                    POSITION_DEV_CN,
                    POSITION_QA_CN,
                    "Synthetic Ops",
                    "Synthetic  Ops",
                    "synthetic ops",
                ]
            ),
        )
        extra = {
            element["position"]: element
            for element in payload
            if "Ops" in element["position"] or "ops" in element["position"]
        }
        self.assertEqual(extra["Synthetic Ops"]["total"], 1)
        self.assertEqual(extra["Synthetic Ops"]["counts"]["applied"], 1)
        self.assertEqual(extra["Synthetic  Ops"]["total"], 1)
        self.assertEqual(extra["synthetic ops"]["total"], 1)
        self.assertEqual(len(extra), 3)

    def test_stage_correction_is_reflected(self):
        """阶段更正后全岗位与单岗位结果都反映当前值。"""
        self.zhou_ning = self.set_stage(self.zhou_ning, "hired")
        payload = json.loads(self.run_all_positions().stdout)
        self.assertEqual(
            payload,
            [
                self.expected_summary(POSITION_DEV_CN, hired=1),
                self.expected_summary(POSITION_QA_CN, applied=1, hired=1),
            ],
        )
        single = json.loads(self.run_summary(POSITION_QA_CN).stdout)
        self.assertEqual(single, self.expected_summary(POSITION_QA_CN, applied=1, hired=1))

    def test_position_correction_removes_emptied_old_position(self):
        """岗位更正后已无候选人的旧岗位不再出现，新岗位出现。"""
        # 合成测试岗两人都改走后，该岗位从全岗位结果中消失
        self.set_position(self.lin_xiao, POSITION_DEV_CN)
        self.set_position(self.zhou_ning, POSITION_DEV_CN)

        payload = json.loads(self.run_all_positions().stdout)
        self.assertEqual(
            payload,
            [
                {
                    "position": POSITION_DEV_CN,
                    "total": 3,
                    "counts": {
                        "applied": 1,
                        "interviewing": 1,
                        "hired": 1,
                        "rejected": 0,
                    },
                }
            ],
        )
        # 旧岗位单岗位查询语义不变：仍返回 total 0 的对象
        old = self.run_summary(POSITION_QA_CN)
        self.assertEqual(old.returncode, 0, old.stderr)
        self.assertEqual(
            json.loads(old.stdout), self.expected_summary(POSITION_QA_CN)
        )

    def test_all_positions_does_not_modify_records(self):
        """全岗位查询前后，三个候选人记录保持不变。"""
        def records():
            rows = []
            for position in (POSITION_QA_CN, POSITION_DEV_CN):
                listed = self.run_cli("list", "--position", position)
                self.assertEqual(listed.returncode, 0, listed.stderr)
                rows.extend(json.loads(listed.stdout))
            return sorted(rows, key=lambda row: row["id"])

        before = records()
        self.run_all_positions()
        self.run_all_positions()
        self.assertEqual(records(), before)


class SummaryAllPositionsEmptyDatabaseTests(unittest.TestCase):
    """全新空数据库的全岗位汇总返回 []。"""

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
        self.assertEqual(result.stdout.splitlines(), ["[]"])
        self.assertEqual(json.loads(result.stdout), [])
        # 查询后数据库仍为空
        single = self.run_cli("summary", "--position", POSITION_QA)
        self.assertEqual(single.returncode, 0, single.stderr)
        self.assertEqual(json.loads(single.stdout)["total"], 0)


class SummaryModeSelectionTests(SummaryAllPositionsTestCase):
    """两种模式互斥：同时指定或都未指定按命令行用法错误处理。"""

    def test_both_modes_is_usage_error(self):
        result = self.run_cli(
            "summary", "--all-positions", "--position", POSITION_QA_CN
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage:", result.stderr)

    def test_neither_mode_is_usage_error(self):
        result = self.run_cli("summary")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage:", result.stderr)

    def test_blank_position_remains_validation_error(self):
        """单岗位模式显式空串或纯空白仍返回 errors JSON，而非用法错误。"""
        for blank in ("", "   ", "\t\n"):
            with self.subTest(blank=repr(blank)):
                result = self.run_cli("summary", "--position", blank)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertEqual(
                    json.loads(result.stderr),
                    {"errors": {"position": "required"}},
                )

    def test_failed_queries_do_not_modify_records(self):
        """所有失败的模式/参数组合都不改动候选人记录。"""
        def all_records():
            payload = self.run_all_positions()
            self.assertEqual(payload.returncode, 0, payload.stderr)
            return payload.stdout

        before = all_records()
        self.run_cli("summary", "--all-positions", "--position", POSITION_QA_CN)
        self.run_cli("summary")
        self.run_cli("summary", "--position", "")
        self.run_cli("summary", "--position", "   ")
        self.assertEqual(all_records(), before)


if __name__ == "__main__":
    unittest.main()
