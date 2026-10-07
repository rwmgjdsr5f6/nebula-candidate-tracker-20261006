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

# 按 id 查看候选人阶段变更历史（JSON 数组，按发生顺序）
python -m recruiting --db demo.sqlite3 stage-history --id 1

# 为已登记候选人追加一条合成评价
python -m recruiting --db demo.sqlite3 add-feedback --id 1 --text "表达清楚"

# 按 id 查看候选人的全部合成评价（JSON 数组，按评价 id 升序）
python -m recruiting --db demo.sqlite3 list-feedback --id 1

# 按评价编号更正一条评价的文字（保留评价 id 与归属的候选人 id）
python -m recruiting --db demo.sqlite3 set-feedback --feedback-id 2 --text "表达清楚"

# 按岗位汇总各阶段人数（单个对象）
python -m recruiting --db demo.sqlite3 summary --position "测试工程师"

# 一次查看全部已有岗位的招聘进度（JSON 数组）
python -m recruiting --db demo.sqlite3 summary --all-positions
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

每次成功改成不同阶段时，变更前后的阶段会按发生顺序追加到该候选人的阶段
历史；重复设置当前阶段不增加历史，登记时的 `applied` 也不算变更。历史保存
在数据库中，重启后结果一致；姓名、邮箱或岗位更正不改变历史归属，也不增加
历史。已有数据库直接可用：首次变更以当时保存的阶段为起点，不补造过去历史，
未变更过的候选人历史为空。

`stage-history` 按 id 查询阶段变更历史，`--id` 必填，id 规则与 `set-stage`
相同（去除两端空白，仅接受 ASCII 数字组成的正整数，允许前导零）。成功时
标准输出为单行 JSON 数组，每项只含 `from_stage` 和 `to_stage` 两个字符串
字段，按发生顺序排列，标准错误为空，退出码为 0；查询不改动任何记录或统计。
非法 id 返回 `{"errors": {"id": "invalid"}}`，合法但不存在的 id（含
9223372036854775808）返回 `{"errors": {"id": "not_found"}}`：错误仅向标准
错误输出单行 JSON，标准输出为空，退出码为 2；缺少 `--id` 时标准错误为用法
说明，标准输出为空，退出码为 2。

`add-feedback` 接收必填的 `--id` 与 `--text`：评价文本去除两端空白，内部
空白、换行、中文和大小写原样保留，空文本或纯空白返回 `text` 的 `required`；
id 规则与 `set-stage` 相同（去除两端空白，仅接受 ASCII 数字组成的正整数，
允许前导零）。参数错误先合并为同一个 `errors` 对象，存在错误时直接返回，不
查询候选人；非法 id 与空文本同时出现时在一个 `errors` 对象中报告
`{"id": "invalid", "text": "required"}` 两项。参数合法但候选人不存在（含
9223372036854775808）时只返回 `id` 的 `not_found`，不保存评价或改动候选人。
成功时标准输出为单行 JSON 对象，仅含评价的正整数 `id`、整数 `candidate_id`
和字符串 `text`，标准错误为空，退出码为 0；重复提交相同文本也新增独立评价，
评价 id 唯一且随追加递增，追加评价不改变候选人的阶段或统计。

`list-feedback` 的 `--id` 必填且规则与 `stage-history` 相同。成功时标准
输出为单行 JSON 数组，每项结构与追加结果一致（仅含 `id`、`candidate_id`、
`text`），只包含目标候选人的评价，按评价 id 升序排列；已存在但没有评价的
候选人返回 `[]`。评价保存在同一个数据库中，重启后内容与顺序一致；更正姓名、
邮箱、岗位或阶段后仍归属原候选人 id。已有数据库无需手工初始化，历史候选人的
初始评价为空，查询不改动任何记录、阶段或统计。非法 id 与不存在 id 的错误
格式同 `stage-history`（仅向标准错误输出单行 JSON、标准输出为空、退出码 2）；
缺少 `--id` 时标准错误为用法说明，标准输出为空，退出码为 2。

`set-feedback` 接收必填的 `--feedback-id` 与 `--text`，按评价编号（取自
已有评价结果，不是候选人编号）更正一条评价的文字。文本规则与
`add-feedback` 相同：去除两端空白，内部空白、换行、中文和大小写原样保留，
空文本或纯空白返回 `text` 的 `required`；编号去除两端空白后须为 ASCII
数字组成的正整数（允许前导零），否则返回 `feedback_id` 的 `invalid`。参数
错误先合并为同一个 `errors` 对象再判断记录是否存在：非法编号配合空文本时
在一个 `errors` 对象中报告
`{"feedback_id": "invalid", "text": "required"}` 两项，不存在的编号配合
空文本时仅报告 `text` 的 `required`。参数合法但评价不存在（含
9223372036854775808）时返回 `feedback_id` 的 `not_found`。字段错误只向
标准错误输出单行 errors JSON，标准输出为空，退出码为 2，且不改动任何记录。
成功时只替换目标评价的 `text`，保留 `id` 和 `candidate_id`，标准输出为仅
含这三个字段的单行 JSON 对象，标准错误为空，退出码为 0；不新增评价，
`list-feedback` 显示新文字，评价数量及按评价 id 升序的顺序不变，重复更正
为当前文字也按成功处理，不改变候选人资料、阶段历史或岗位统计。缺少
`--feedback-id` 或 `--text` 时标准错误为用法说明，标准输出为空，退出码为
2。

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

`summary` 的两种模式互斥且必须提供其一：`--position 岗位` 或
`--all-positions`，同时指定或都未指定按命令行用法错误结束（退出码 2、
标准输出为空、标准错误为用法说明）。

`--position` 模式将岗位去除两端空白后精确匹配（区分大小写、保留内部空白），
标准输出为单个对象 `{"position", "total", "counts"}`；显式传入空字符串或
纯空白时向标准错误输出 `{"errors": {"position": "required"}}`，退出码为 2、
标准输出为空。即使该岗位没有任何候选人也成功返回，`total` 为 0。

`--all-positions` 模式在标准输出返回单行 JSON 数组，只包含当前至少有一名
候选人的岗位，每个不同岗位名称只出现一次，并按名称的 Unicode 码点升序
排列；大小写或内部空白不同的岗位分别统计。每个元素沿用单岗位模式的
`position`、`total`、`counts` 结构，`counts` 始终包含
`applied`、`interviewing`、`hired`、`rejected` 四项整数，没有候选人的阶段
输出 0，`total` 等于四项之和；空数据库返回 `[]`。两种模式都只读取数据，
不修改候选人；岗位或阶段更正后查询即反映当前值，已无候选人的旧岗位不会
出现在全岗位结果中。成功退出码为 0、标准错误为空。

