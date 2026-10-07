"""delete-feedback 命令的回归测试。

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


class DeleteFeedbackTests(unittest.TestCase):
    """每个用例都登记林晓（编号 1，评价编号 1、2）与周宁（编号 2，评价编号 3）。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-delete-feedback-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "candidates.sqlite3")
        self.lin_xiao = self.add_candidate(**LIN_XIAO)
        self.zhou_ning = self.add_candidate(**ZHOU_NING)
        lin_id = self.lin_xiao["id"]
        zhou_id = self.zhou_ning["id"]
        self.first = self.assert_add_success(
            self.add_feedback(lin_id, "表达清楚"), lin_id, "表达清楚"
        )
        self.second = self.assert_add_success(
            self.add_feedback(lin_id, "表达清楚"), lin_id, "表达清楚"
        )
        self.third = self.assert_add_success(
            self.add_feedback(zhou_id, "周宁评价"), zhou_id, "周宁评价"
        )

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

    def add_feedback(self, candidate_id, text):
        return self.run_cli(
            "add-feedback", "--id", str(candidate_id), "--text", text
        )

    def delete_feedback(self, feedback_id):
        return self.run_cli("delete-feedback", "--feedback-id", str(feedback_id))

    def list_feedback(self, candidate_id):
        return self.run_cli("list-feedback", "--id", str(candidate_id))

    # ---- 断言辅助 ----

    def assert_add_success(self, result, candidate_id, text):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        record = json.loads(result.stdout)
        self.assertEqual(record["candidate_id"], candidate_id)
        self.assertEqual(record["text"], text)
        return record

    def assert_delete_success(self, result, expected):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        # 单行 JSON，内部换行只以转义形式出现
        self.assertEqual(len(result.stdout.splitlines()), 1, result.stdout)
        record = json.loads(result.stdout)
        self.assertEqual(record, expected)
        self.assertEqual(set(record), {"id", "candidate_id", "text"})
        return record

    def assert_list(self, candidate_id, expected):
        result = self.list_feedback(candidate_id)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), expected)

    def assert_failure(self, result, expected_errors):
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        # 标准错误只有单行 JSON，不出现回溯等多行输出
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertEqual(json.loads(result.stderr), {"errors": expected_errors})

    # ---- 用例 ----

    def test_delete_returns_removed_record(self):
        """删除林晓第二条评价后返回其原对象，其余评价不变。"""
        result = self.delete_feedback(self.second["id"])
        self.assert_delete_success(result, self.second)
        self.assert_list(self.lin_xiao["id"], [self.first])
        self.assert_list(self.zhou_ning["id"], [self.third])

    def test_delete_all_leaves_candidate_with_empty_list(self):
        """删除候选人全部评价后候选人仍存在，评价查询返回 []。"""
        lin_id = self.lin_xiao["id"]
        self.delete_feedback(self.first["id"])
        self.delete_feedback(self.second["id"])
        self.assert_list(lin_id, [])
        get_result = self.run_cli("get", "--id", str(lin_id))
        self.assertEqual(get_result.returncode, 0, get_result.stderr)
        self.assertEqual(json.loads(get_result.stdout)["name"], "林晓")

    def test_deleted_text_kept_verbatim_with_escaped_newline(self):
        """返回文本保持保存时内容，内部换行以 JSON 转义表示。"""
        lin_id = self.lin_xiao["id"]
        text = "第一行\n第二行"
        added = self.assert_add_success(
            self.add_feedback(lin_id, text), lin_id, text
        )
        result = self.delete_feedback(added["id"])
        self.assert_delete_success(result, added)
        self.assertNotIn("\n", result.stdout.strip())
        self.assertIn("\\n", result.stdout)

    def test_feedback_id_not_candidate_id(self):
        """编号取自评价结果：候选人 id 2 存在但评价 id 2 属于林晓。"""
        result = self.delete_feedback(self.zhou_ning["id"])
        self.assert_delete_success(result, self.second)
        self.assert_list(self.lin_xiao["id"], [self.first])
        self.assert_list(self.zhou_ning["id"], [self.third])

    def test_leading_zero_and_surrounding_whitespace_id(self):
        result = self.delete_feedback("  000{}  ".format(self.third["id"]))
        self.assert_delete_success(result, self.third)
        self.assert_list(self.zhou_ning["id"], [])

    def test_invalid_feedback_id_is_reported(self):
        for bad_id in ("", "   ", "abc", "0", "000", "-1", "1.5", "+1", "１２"):
            with self.subTest(bad_id=repr(bad_id)):
                self.assert_failure(
                    self.delete_feedback(bad_id), {"feedback_id": "invalid"}
                )

    def test_unknown_feedback_id_is_not_found(self):
        self.assert_failure(
            self.delete_feedback(999), {"feedback_id": "not_found"}
        )

    def test_id_above_sqlite_int64_max_is_not_found(self):
        self.assert_failure(
            self.delete_feedback("9223372036854775808"),
            {"feedback_id": "not_found"},
        )

    def test_redelete_and_set_after_delete_are_not_found(self):
        """删除后再次删除或用 set-feedback 更正同一编号均按不存在处理。"""
        self.delete_feedback(self.second["id"])
        self.assert_failure(
            self.delete_feedback(self.second["id"]),
            {"feedback_id": "not_found"},
        )
        result = self.run_cli(
            "set-feedback",
            "--feedback-id", str(self.second["id"]),
            "--text", "x",
        )
        self.assert_failure(result, {"feedback_id": "not_found"})

    def test_failure_saves_nothing(self):
        """参数或存在性错误不改动任何评价、候选人、历史或统计。"""
        lin_id = self.lin_xiao["id"]
        profile_before = self.run_cli("get", "--id", str(lin_id))
        summary_before = self.run_cli("summary", "--position", POSITION)
        self.delete_feedback("abc")
        self.delete_feedback("0")
        self.delete_feedback(999)
        self.delete_feedback("9223372036854775808")
        self.assert_list(lin_id, [self.first, self.second])
        self.assert_list(self.zhou_ning["id"], [self.third])
        self.assertEqual(
            self.run_cli("get", "--id", str(lin_id)).stdout,
            profile_before.stdout,
        )
        self.assertEqual(
            self.run_cli("stage-history", "--id", str(lin_id)).stdout, "[]\n"
        )
        self.assertEqual(
            self.run_cli("summary", "--position", POSITION).stdout,
            summary_before.stdout,
        )

    def test_persists_across_reopen(self):
        """删除保存在数据库中，重新打开同一数据库结果一致。"""
        self.delete_feedback(self.second["id"])
        self.assert_list(self.lin_xiao["id"], [self.first])
        self.assert_list(self.lin_xiao["id"], [self.first])
        self.assert_list(self.zhou_ning["id"], [self.third])

    def test_new_feedback_does_not_reuse_deleted_id(self):
        """删除后追加的评价编号不复用已删除编号，仍唯一且递增。"""
        lin_id = self.lin_xiao["id"]
        self.delete_feedback(self.second["id"])
        added = self.assert_add_success(
            self.add_feedback(lin_id, "新评价"), lin_id, "新评价"
        )
        self.assertGreater(added["id"], self.third["id"])
        self.assert_list(lin_id, [self.first, added])

    def test_delete_does_not_change_profile_history_or_summary(self):
        """删除评价不改变候选人资料、阶段历史与岗位统计。"""
        lin_id = self.lin_xiao["id"]
        self.run_cli("set-stage", "--id", str(lin_id), "--stage", "hired")
        profile_before = self.run_cli("get", "--id", str(lin_id))
        history_before = self.run_cli("stage-history", "--id", str(lin_id))
        summary_before = self.run_cli("summary", "--position", POSITION)
        self.delete_feedback(self.second["id"])
        self.assertEqual(
            self.run_cli("get", "--id", str(lin_id)).stdout,
            profile_before.stdout,
        )
        self.assertEqual(
            self.run_cli("stage-history", "--id", str(lin_id)).stdout,
            history_before.stdout,
        )
        self.assertEqual(
            self.run_cli("summary", "--position", POSITION).stdout,
            summary_before.stdout,
        )

    def test_missing_feedback_id_option_is_usage_error(self):
        """缺少 --feedback-id 时标准错误为用法说明，退出码为 2。"""
        result = self.run_cli("delete-feedback")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage", result.stderr)


if __name__ == "__main__":
    unittest.main()
