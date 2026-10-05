# 招聘候选人管理台

计划整理候选人资料、招聘职位和面试进度。面向本地单机使用，采用 Python 3 标准库与 SQLite。

## 使用方式

仅依赖 Python 3 标准库与本地 SQLite，首次操作时自动创建数据库文件与表。

```bash
# 登记候选人（stage 固定为 applied）
python -m recruiting --db demo.sqlite3 add \
  --name "林晓" --email lin.xiao@example.test --position "测试工程师"

# 按岗位精确查询（区分大小写），按 id 升序返回
python -m recruiting --db demo.sqlite3 list --position "测试工程师"

# 按 id 修改阶段（applied/interviewing/hired/rejected，可在任意阶段间修改）
python -m recruiting --db demo.sqlite3 set-stage --id 1 --stage interviewing
```

字段两端空白会被去除；邮箱保留内部大小写。姓名或岗位为空返回 `required`，
邮箱不合规返回 `invalid`：多个字段错误同时输出到标准错误的单个 JSON 对象
（`errors` 映射字段名到错误值），退出码为 2，且不写入记录。成功退出码为 0，
结果 JSON 输出到标准输出。

`set-stage` 的 id 必须为正整数，阶段去空白后须为允许值之一（小写精确匹配）：
id 非法返回 `invalid`，阶段为空返回 `required`，阶段不在允许集合返回
`invalid`，多个参数错误合并到同一个 `errors` 对象，参数校验先于记录查找；
参数合法但记录不存在时 id 返回 `not_found`。以上错误均退出码为 2、标准输出
为空且不改动任何记录。成功时退出码为 0，输出更新后的单个候选人 JSON 对象，
重复设置当前阶段也按成功处理。

