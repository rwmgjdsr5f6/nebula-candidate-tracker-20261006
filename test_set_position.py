"""set-position 命令的回归测试。

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

OLD_POSITION = "测试工程师"
NEW_POSITION = "产品设计师"
LIN_XIAO = {"name": "林晓", "email": "lin.xiao@example.test"}
ZHOU_NING = {"name": "周宁", "email": "zhou.ning@example.test"}


class SetPositionTestCase(unittest.TestCase):
    """每个测试用例都通过 add 入口登记林晓和周宁，岗位均为测试工程师。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-set-position-test-")
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
            "add", "--name", name, "--email", email, "--position", OLD_POSITION
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def set_position(self, candidate_id, position):
        return self.run_cli(
            "set-position", "--id", candidate_id, "--position", position
        )

    def list_by_position(self, position):
        result = self.run_cli("list", "--position", position)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def get_candidate(self, candidate_id):
        result = self.run_cli("get", "--id", str(candidate_id))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def summary(self, position):
        result = self.run_cli("summary", "--position", position)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    # ---- 断言辅助 ----

    def assert_success(self, result, expected_record):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), expected_record)

    def assert_failure(self, result, expected_errors):
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        # 标准错误只有单行 JSON，不出现回溯等多行输出
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.assertEqual(json.loads(result.stderr), {"errors": expected_errors})
        # 失败后两名候选人仍都在旧岗位，完整记录与操作前相同
        self.assertEqual(
            self.list_by_position(OLD_POSITION),
            [self.lin_xiao, self.zhou_ning],
        )
        self.assertEqual(self.list_by_position(NEW_POSITION), [])


class SetPositionSuccessTests(SetPositionTestCase):
    def test_position_update_persists(self):
        """更正岗位后只有 position 改变，其余字段与另一人记录保持原值。"""
        result = self.set_position(str(self.lin_xiao["id"]), NEW_POSITION)
        expected = dict(self.lin_xiao, position=NEW_POSITION)
        self.assert_success(result, expected)
        # 新岗位只返回林晓，旧岗位只返回周宁
        self.assertEqual(self.list_by_position(NEW_POSITION), [expected])
        self.assertEqual(
            self.list_by_position(OLD_POSITION), [self.zhou_ning]
        )
        # get 能读到新岗位，其他字段不变
        self.assertEqual(self.get_candidate(self.lin_xiao["id"]), expected)
        # summary 按更正后的归属计数
        self.assertEqual(
            self.summary(NEW_POSITION),
            {
                "position": NEW_POSITION,
                "total": 1,
                "counts": {
                    "applied": 1,
                    "interviewing": 0,
                    "hired": 0,
                    "rejected": 0,
                },
            },
        )
        self.assertEqual(
            self.summary(OLD_POSITION),
            {
                "position": OLD_POSITION,
                "total": 1,
                "counts": {
                    "applied": 1,
                    "interviewing": 0,
                    "hired": 0,
                    "rejected": 0,
                },
            },
        )

    def test_position_with_surrounding_whitespace_is_trimmed(self):
        """岗位两端空白被去除，保存与返回值都是去空白后的岗位。"""
        result = self.set_position(
            str(self.lin_xiao["id"]), "  {}  ".format(NEW_POSITION)
        )
        expected = dict(self.lin_xiao, position=NEW_POSITION)
        self.assert_success(result, expected)
        self.assertEqual(self.list_by_position(NEW_POSITION), [expected])

    def test_position_internal_whitespace_and_case_are_preserved(self):
        """岗位内部空白与大小写原样保存，按精确文本匹配。"""
        new_position = "Senior QA 工程师"
        result = self.set_position(str(self.lin_xiao["id"]), new_position)
        expected = dict(self.lin_xiao, position=new_position)
        self.assert_success(result, expected)
        self.assertEqual(self.list_by_position(new_position), [expected])
        # 不同大小写或不同空白不匹配
        self.assertEqual(self.list_by_position("senior qa 工程师"), [])
        self.assertEqual(self.list_by_position("Senior QA工程师"), [])

    def test_setting_current_position_again_succeeds(self):
        """重复设置当前岗位按成功处理。"""
        result = self.set_position(
            str(self.zhou_ning["id"]), "  {}  ".format(OLD_POSITION)
        )
        self.assert_success(result, self.zhou_ning)
        self.assertEqual(
            self.list_by_position(OLD_POSITION),
            [self.lin_xiao, self.zhou_ning],
        )

    def test_id_with_surrounding_whitespace_succeeds(self):
        """带两端空白的 id 与合法岗位仍可成功。"""
        result = self.set_position(
            "  {}  ".format(self.lin_xiao["id"]), NEW_POSITION
        )
        expected = dict(self.lin_xiao, position=NEW_POSITION)
        self.assert_success(result, expected)

    def test_id_with_leading_zeros_succeeds(self):
        """id 允许前导零，数值不变。"""
        result = self.set_position(
            "000{}".format(self.lin_xiao["id"]), NEW_POSITION
        )
        expected = dict(self.lin_xiao, position=NEW_POSITION)
        self.assert_success(result, expected)

    def test_update_does_not_insert_or_touch_other_candidates(self):
        """更正不新增记录，另一候选人的全部字段保持原值。"""
        result = self.set_position(str(self.lin_xiao["id"]), NEW_POSITION)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.list_by_position(NEW_POSITION),
            [dict(self.lin_xiao, position=NEW_POSITION)],
        )
        self.assertEqual(
            self.list_by_position(OLD_POSITION), [self.zhou_ning]
        )
        self.assertEqual(self.get_candidate(self.zhou_ning["id"]), self.zhou_ning)


class SetPositionFailureTests(SetPositionTestCase):
    def test_blank_id_is_invalid(self):
        for blank in ("", "   ", "\t\n"):
            with self.subTest(blank=repr(blank)):
                result = self.set_position(blank, NEW_POSITION)
                self.assert_failure(result, {"id": "invalid"})

    def test_zero_and_negative_id_are_invalid(self):
        for bad_id in ("0", "-1", "-42", "+1"):
            with self.subTest(bad_id=bad_id):
                result = self.set_position(bad_id, NEW_POSITION)
                self.assert_failure(result, {"id": "invalid"})

    def test_non_numeric_id_is_invalid(self):
        for bad_id in ("abc", "1.5", "1a", "１"):
            with self.subTest(bad_id=bad_id):
                result = self.set_position(bad_id, NEW_POSITION)
                self.assert_failure(result, {"id": "invalid"})

    def test_all_zero_id_is_invalid(self):
        """全零数字数值为零，仍属于 id 的 invalid。"""
        result = self.set_position("0000", NEW_POSITION)
        self.assert_failure(result, {"id": "invalid"})

    def test_blank_position_is_required(self):
        for blank in ("", "   ", "\t\n "):
            with self.subTest(blank=repr(blank)):
                result = self.set_position(str(self.lin_xiao["id"]), blank)
                self.assert_failure(result, {"position": "required"})

    def test_id_and_position_errors_are_merged(self):
        """id 与岗位同时错误时，同一个 errors 对象保留两项错误。"""
        result = self.set_position("abc", "   ")
        self.assert_failure(result, {"id": "invalid", "position": "required"})

    def test_unknown_id_is_not_found(self):
        missing_id = str(max(self.lin_xiao["id"], self.zhou_ning["id"]) + 1000)
        result = self.set_position(missing_id, NEW_POSITION)
        self.assert_failure(result, {"id": "not_found"})

    def test_sqlite_int64_max_id_is_not_found(self):
        """整数上限本身是合法 id，无记录时返回 not_found 而非异常。"""
        result = self.set_position("9223372036854775807", NEW_POSITION)
        self.assert_failure(result, {"id": "not_found"})

    def test_id_above_sqlite_int64_max_is_not_found(self):
        """超过 SQLite 有符号整数上限的合法 id 返回 not_found，无回溯。"""
        result = self.set_position("9223372036854775808", NEW_POSITION)
        self.assert_failure(result, {"id": "not_found"})

    def test_oversized_id_with_leading_zeros_is_not_found(self):
        """带前导零的越界值含义不变，同样返回 not_found。"""
        result = self.set_position("0009223372036854775808", NEW_POSITION)
        self.assert_failure(result, {"id": "not_found"})

    def test_not_found_id_with_blank_position_reports_only_position(self):
        """不存在的 id 配合空岗位：只返回 position 的 required。"""
        missing_id = str(max(self.lin_xiao["id"], self.zhou_ning["id"]) + 1000)
        result = self.set_position(missing_id, "   ")
        self.assert_failure(result, {"position": "required"})

    def test_oversized_id_with_blank_position_reports_only_position(self):
        """超大合法 id 与空岗位组合：仅返回 position 的 required。"""
        result = self.set_position("9223372036854775808", "   ")
        self.assert_failure(result, {"position": "required"})

    def test_invalid_id_with_blank_position_reports_both(self):
        """非法 id 与空岗位组合：两项错误合并报告。"""
        result = self.set_position("-1", "   ")
        self.assert_failure(result, {"id": "invalid", "position": "required"})

    def test_failed_update_keeps_previous_position(self):
        """先成功更正，再提交空岗位：查询仍保留上次成功的值。"""
        first = self.set_position(str(self.lin_xiao["id"]), NEW_POSITION)
        self.assert_success(first, dict(self.lin_xiao, position=NEW_POSITION))

        second = self.set_position(str(self.lin_xiao["id"]), "   ")
        self.assertEqual(second.returncode, 2)
        self.assertEqual(second.stdout, "")
        self.assertEqual(
            json.loads(second.stderr), {"errors": {"position": "required"}}
        )
        self.assertEqual(
            self.list_by_position(NEW_POSITION),
            [dict(self.lin_xiao, position=NEW_POSITION)],
        )
        self.assertEqual(
            self.list_by_position(OLD_POSITION), [self.zhou_ning]
        )


class SetPositionUsageTests(SetPositionTestCase):
    def test_missing_position_option_exits_2_with_usage(self):
        """缺少必填的 --position 时显示用法并以 2 退出，不改动记录。"""
        result = self.run_cli(
            "set-position", "--id", str(self.lin_xiao["id"])
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage:", result.stderr)
        self.assertIn("--position", result.stderr)
        self.assertEqual(
            self.list_by_position(OLD_POSITION),
            [self.lin_xiao, self.zhou_ning],
        )

    def test_missing_id_option_exits_2_with_usage(self):
        """缺少必填的 --id 时显示用法并以 2 退出，不改动记录。"""
        result = self.run_cli("set-position", "--position", NEW_POSITION)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage:", result.stderr)
        self.assertIn("--id", result.stderr)
        self.assertEqual(
            self.list_by_position(OLD_POSITION),
            [self.lin_xiao, self.zhou_ning],
        )


if __name__ == "__main__":
    unittest.main()
