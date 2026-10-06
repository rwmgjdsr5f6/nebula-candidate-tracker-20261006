"""set-name 命令的回归测试。

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

POSITION = "测试工程师"
LIN_XIAO = {"name": "林晓", "email": "lin.xiao@example.test"}
ZHOU_NING = {"name": "周宁", "email": "zhou.ning@example.test"}
NEW_NAME = "林 晓"


class SetNameTestCase(unittest.TestCase):
    """每个测试用例都通过 add 入口登记林晓和周宁，岗位均为测试工程师。

    林晓的阶段调整为 interviewing，周宁保持 applied 作为对照。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="recruiting-set-name-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "candidates.sqlite3")
        self.lin_xiao = self.add_candidate(**LIN_XIAO)
        self.zhou_ning = self.add_candidate(**ZHOU_NING)
        self.lin_xiao = self.set_stage(self.lin_xiao["id"], "interviewing")

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
        result = self.run_cli(
            "set-stage", "--id", str(candidate_id), "--stage", stage
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def set_name(self, candidate_id, name):
        return self.run_cli("set-name", "--id", candidate_id, "--name", name)

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
        # 失败后两名候选人完整记录与操作前相同
        self.assertEqual(
            self.list_by_position(POSITION),
            [self.lin_xiao, self.zhou_ning],
        )


class SetNameSuccessTests(SetNameTestCase):
    def test_name_update_persists(self):
        """更正姓名后只有 name 改变，其余字段与另一人记录保持原值。"""
        result = self.set_name(str(self.lin_xiao["id"]), "林晓雨")
        expected = dict(self.lin_xiao, name="林晓雨")
        self.assert_success(result, expected)
        # get 能读到新姓名，id、邮箱、岗位、阶段不变
        self.assertEqual(self.get_candidate(self.lin_xiao["id"]), expected)
        self.assertEqual(self.get_candidate(self.zhou_ning["id"]), self.zhou_ning)
        # list 的岗位与阶段筛选、id 排序不受姓名更正影响
        self.assertEqual(
            self.list_by_position(POSITION), [expected, self.zhou_ning]
        )
        interviewing = self.run_cli(
            "list", "--position", POSITION, "--stage", "interviewing"
        )
        self.assertEqual(json.loads(interviewing.stdout), [expected])
        # summary 人数不变
        self.assertEqual(
            self.summary(POSITION),
            {
                "position": POSITION,
                "total": 2,
                "counts": {
                    "applied": 1,
                    "interviewing": 1,
                    "hired": 0,
                    "rejected": 0,
                },
            },
        )

    def test_name_with_surrounding_whitespace_is_trimmed(self):
        """姓名两端空白被去除，保存与返回值都是去空白后的姓名。"""
        result = self.set_name(str(self.lin_xiao["id"]), "  {}  ".format(NEW_NAME))
        expected = dict(self.lin_xiao, name=NEW_NAME)
        self.assert_success(result, expected)
        self.assertEqual(self.get_candidate(self.lin_xiao["id"]), expected)

    def test_name_internal_whitespace_and_case_are_preserved(self):
        """姓名内部空白、中文及英文字母大小写原样保存。"""
        new_name = "Alice 林 Xiao"
        result = self.set_name(str(self.lin_xiao["id"]), new_name)
        self.assert_success(result, dict(self.lin_xiao, name=new_name))
        self.assertEqual(
            self.get_candidate(self.lin_xiao["id"])["name"], new_name
        )

    def test_duplicate_name_is_accepted(self):
        """不因与他人重名拒绝更新。"""
        result = self.set_name(str(self.lin_xiao["id"]), self.zhou_ning["name"])
        self.assert_success(
            result, dict(self.lin_xiao, name=self.zhou_ning["name"])
        )
        self.assertEqual(self.get_candidate(self.zhou_ning["id"]), self.zhou_ning)

    def test_setting_current_name_again_succeeds(self):
        """重复设置当前姓名按成功处理。"""
        result = self.set_name(
            str(self.zhou_ning["id"]), "  {}  ".format(self.zhou_ning["name"])
        )
        self.assert_success(result, self.zhou_ning)
        self.assertEqual(
            self.list_by_position(POSITION),
            [self.lin_xiao, self.zhou_ning],
        )

    def test_id_with_surrounding_whitespace_succeeds(self):
        """带两端空白的 id 与合法姓名仍可成功。"""
        result = self.set_name("  {}  ".format(self.lin_xiao["id"]), NEW_NAME)
        self.assert_success(result, dict(self.lin_xiao, name=NEW_NAME))

    def test_id_with_leading_zeros_succeeds(self):
        """id 允许前导零，数值不变。"""
        result = self.set_name("000{}".format(self.lin_xiao["id"]), NEW_NAME)
        self.assert_success(result, dict(self.lin_xiao, name=NEW_NAME))

    def test_update_does_not_insert_or_touch_other_candidates(self):
        """更正不新增记录，另一候选人的全部字段保持原值。"""
        result = self.set_name(str(self.lin_xiao["id"]), NEW_NAME)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.list_by_position(POSITION),
            [dict(self.lin_xiao, name=NEW_NAME), self.zhou_ning],
        )
        self.assertEqual(self.get_candidate(self.zhou_ning["id"]), self.zhou_ning)


class SetNameFailureTests(SetNameTestCase):
    def test_blank_id_is_invalid(self):
        for blank in ("", "   ", "\t\n"):
            with self.subTest(blank=repr(blank)):
                result = self.set_name(blank, NEW_NAME)
                self.assert_failure(result, {"id": "invalid"})

    def test_zero_and_negative_id_are_invalid(self):
        for bad_id in ("0", "-1", "-42", "+1"):
            with self.subTest(bad_id=bad_id):
                result = self.set_name(bad_id, NEW_NAME)
                self.assert_failure(result, {"id": "invalid"})

    def test_non_numeric_id_is_invalid(self):
        for bad_id in ("abc", "1.5", "1a", "１"):
            with self.subTest(bad_id=bad_id):
                result = self.set_name(bad_id, NEW_NAME)
                self.assert_failure(result, {"id": "invalid"})

    def test_all_zero_id_is_invalid(self):
        """全零数字数值为零，仍属于 id 的 invalid。"""
        result = self.set_name("0000", NEW_NAME)
        self.assert_failure(result, {"id": "invalid"})

    def test_blank_name_is_required(self):
        for blank in ("", "   ", "\t\n "):
            with self.subTest(blank=repr(blank)):
                result = self.set_name(str(self.lin_xiao["id"]), blank)
                self.assert_failure(result, {"name": "required"})

    def test_id_and_name_errors_are_merged(self):
        """id 与姓名同时错误时，同一个 errors 对象保留两项错误。"""
        result = self.set_name("abc", "   ")
        self.assert_failure(result, {"id": "invalid", "name": "required"})

    def test_unknown_id_is_not_found(self):
        missing_id = str(max(self.lin_xiao["id"], self.zhou_ning["id"]) + 1000)
        result = self.set_name(missing_id, NEW_NAME)
        self.assert_failure(result, {"id": "not_found"})

    def test_sqlite_int64_max_id_is_not_found(self):
        """整数上限本身是合法 id，无记录时返回 not_found 而非异常。"""
        result = self.set_name("9223372036854775807", NEW_NAME)
        self.assert_failure(result, {"id": "not_found"})

    def test_id_above_sqlite_int64_max_is_not_found(self):
        """超过 SQLite 有符号整数上限的合法 id 返回 not_found，无回溯。"""
        result = self.set_name("9223372036854775808", NEW_NAME)
        self.assert_failure(result, {"id": "not_found"})

    def test_oversized_id_with_leading_zeros_is_not_found(self):
        """带前导零的越界值含义不变，同样返回 not_found。"""
        result = self.set_name("0009223372036854775808", NEW_NAME)
        self.assert_failure(result, {"id": "not_found"})

    def test_not_found_id_with_blank_name_reports_only_name(self):
        """不存在的 id 配合空姓名：只返回 name 的 required。"""
        missing_id = str(max(self.lin_xiao["id"], self.zhou_ning["id"]) + 1000)
        result = self.set_name(missing_id, "   ")
        self.assert_failure(result, {"name": "required"})

    def test_oversized_id_with_blank_name_reports_only_name(self):
        """超大合法 id 与空姓名组合：仅返回 name 的 required。"""
        result = self.set_name("9223372036854775808", "   ")
        self.assert_failure(result, {"name": "required"})

    def test_invalid_id_with_blank_name_reports_both(self):
        """非法 id 与空姓名组合：两项错误合并报告。"""
        result = self.set_name("-1", "   ")
        self.assert_failure(result, {"id": "invalid", "name": "required"})

    def test_failed_update_keeps_previous_name(self):
        """先成功更正，再提交空姓名：查询仍保留上次成功的值。"""
        first = self.set_name(str(self.lin_xiao["id"]), NEW_NAME)
        self.assert_success(first, dict(self.lin_xiao, name=NEW_NAME))

        second = self.set_name(str(self.lin_xiao["id"]), "   ")
        self.assertEqual(second.returncode, 2)
        self.assertEqual(second.stdout, "")
        self.assertEqual(
            json.loads(second.stderr), {"errors": {"name": "required"}}
        )
        self.assertEqual(
            self.get_candidate(self.lin_xiao["id"]),
            dict(self.lin_xiao, name=NEW_NAME),
        )


class SetNameUsageTests(SetNameTestCase):
    def test_missing_name_option_exits_2_with_usage(self):
        """缺少必填的 --name 时显示用法并以 2 退出，不改动记录。"""
        result = self.run_cli("set-name", "--id", str(self.lin_xiao["id"]))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage:", result.stderr)
        self.assertIn("--name", result.stderr)
        self.assertEqual(
            self.list_by_position(POSITION),
            [self.lin_xiao, self.zhou_ning],
        )

    def test_missing_id_option_exits_2_with_usage(self):
        """缺少必填的 --id 时显示用法并以 2 退出，不改动记录。"""
        result = self.run_cli("set-name", "--name", NEW_NAME)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage:", result.stderr)
        self.assertIn("--id", result.stderr)
        self.assertEqual(
            self.list_by_position(POSITION),
            [self.lin_xiao, self.zhou_ning],
        )


if __name__ == "__main__":
    unittest.main()
