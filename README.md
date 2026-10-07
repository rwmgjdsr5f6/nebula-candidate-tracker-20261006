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

# 在岗位之内再按合成姓名精确查找（去除两端空白，保留内部空白、区分大小写）
python -m recruiting --db demo.sqlite3 list --position "测试工程师" --name "林晓"

# 按 id 修改候选人阶段（applied/interviewing/hired/rejected 均可互转）
python -m recruiting --db demo.sqlite3 set-stage --id 1 --stage interviewing
```

字段两端空白会被去除；邮箱保留内部大小写。姓名或岗位为空返回 `required`，
邮箱不合规返回 `invalid`：多个字段错误同时输出到标准错误的单个 JSON 对象
（`errors` 映射字段名到错误值），退出码为 2，且不写入记录。成功退出码为 0，
结果 JSON 输出到标准输出。

`list` 必传岗位，`--name`、`--stage`、`--email` 均可选；省略时不过滤该条件，
提供时四个条件同时成立才算命中，同名候选人全部返回。`--name` 去除两端空白后
与保存的姓名完整匹配（保留内部空白、区分英文字母大小写，不作子串或通配符
匹配），显式传入空字符串或纯空白返回 `required`；岗位为空返回 `position` 的
`required`，阶段为空返回 `stage` 的 `required`、非法或大写阶段返回
`stage` 的 `invalid`，邮箱不合规返回 `email` 的 `invalid`，多个参数错误合并为
同一个 `errors` 对象。结果为候选人五字段 JSON 数组、按 id 升序，没有匹配记录
时返回 `[]`；查询不改动任何记录。

`set-stage` 的阶段去除两端空白后按小写精确匹配，须为
`applied`、`interviewing`、`hired`、`rejected` 之一：为空返回
`required`，否则不在允许集合返回 `invalid`；id 去除空白后不是正整数返回
`invalid`，参数合法但记录不存在返回 `not_found`。参数错误合并为一个
`errors` 对象，先完成参数校验再查找记录；错误退出码均为 2、标准输出为空，
且不改动任何记录。成功时标准输出为更新后的候选人 JSON 对象，阶段之间允许
任意互转，重复设置当前阶段也按成功处理。

`set-position` 的岗位沿用登记规则：去除两端空白后为空返回 `required`，
内部空白与大小写原样保存；id 规则与 `set-stage` 相同。参数错误合并为一个
`errors` 对象，先完成参数校验再查找记录；不存在的 id 配合空岗位只报告
position 的 `required`，合法但不存在的 id 返回 `not_found`。成功时只替换
目标记录的岗位，保留 id、姓名、邮箱与阶段，不新增记录，重复设置当前岗位也
按成功处理；结果 JSON 输出到标准输出，标准错误为空，退出码为 0。

`set-name` 的姓名沿用登记规则：去除两端空白后为空返回 `required`，内部空白、
中文及英文字母大小写原样保留，重名不拒绝更新；id 规则与 `set-stage` 相同。
参数错误合并为一个 `errors` 对象，先完成参数校验再查找记录；不存在的 id
配合空姓名只报告 name 的 `required`，合法但不存在的 id（含
9223372036854775808）返回 `not_found`。成功时只替换目标记录的姓名，保留 id、
邮箱、岗位与阶段，不新增记录，重复设置当前姓名也按成功处理；结果 JSON 输出
到标准输出，标准错误为空，退出码为 0。

`summary` 的两种模式互斥且必须提供其一：

```bash
# 单岗位汇总：结果为单个 JSON 对象
python -m recruiting --db demo.sqlite3 summary --position "测试工程师"

# 全岗位汇总：结果为 JSON 数组，每个岗位一个元素
python -m recruiting --db demo.sqlite3 summary --all-positions
```

单岗位模式 `--position` 去除两端空白后与保存的岗位精确匹配（区分大小写、
保留内部空白），显式传入空字符串或纯空白时向标准错误输出
`{"errors": {"position": "required"}}`，退出码为 2、标准输出为空。即使岗位
没有任何候选人也成功返回单个对象，`total` 为 0。

全岗位模式 `--all-positions` 无需参数：只包含当前至少有一名候选人的岗位，
每个不同岗位名称（大小写或内部空白不同即视为不同岗位）只出现一次，按名称的
Unicode 码点升序排列；空数据库返回 `[]`。两种模式同时指定或都不指定属于命令
行用法错误：退出码为 2、标准输出为空、标准错误显示用法说明。

两种模式的结果结构相同：单岗位模式为对象，全岗位模式的每个数组元素为对象，
均含 `position`、`total`、`counts`；`counts` 恒包含
`applied`、`interviewing`、`hired`、`rejected` 四项整数计数，没有候选人的
阶段输出 0，`total` 等于四项之和。岗位或阶段更正后再次查询反映当前值，已无
候选人的旧岗位不会出现在全岗位结果中。汇总为只读查询，成功时退出码为 0、
结果单行 JSON 输出到标准输出、标准错误为空，且不改动任何候选人记录。

