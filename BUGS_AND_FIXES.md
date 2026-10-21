# 问题清单与修复记录（BUGS_AND_FIXES）

> 审查对象：`table-cleaner` v2.0.0（工作区实际文件，与 `table-cleaner-v2.0.0.zip` 逐字节一致，已核对 sha256）
> 本轮结论：共处理 **12 个问题**（11 个代码缺陷 + 1 个文档表述问题；第一轮 8 个 + 独立复审补充 4 个），
> 代码缺陷全部带回归测试（18 项）。
> 其中 2 个（BUG-002 的纯整数列形态、BUG-009 的 `round()`）是先修过一次、复审时被绕过、
> 又补修的——这类「修了但没修全」的记录保留在原处，不删历史。
> 修复版本：**v2.0.1**。修复后 214 项测试在 3 套环境中全部通过（见 `TEST_REPORT.md`）。

## 严重等级口径

| 等级 | 含义 |
| --- | --- |
| P0 | 会卡死程序、静默改坏数据、或结果不可信，必须发布前修 |
| P1 | 正式交付前必须解决：用户可见崩溃、核心功能缺陷、安装/测试/文档与事实不符 |
| P2 | 建议近期解决：体验、性能、文档补充 |

## 总览

| 编号 | 等级 | 一句话 | 所属文件 | 状态 |
| --- | --- | --- | --- | --- |
| BUG-001 | P0 | 计算列写 `9**9**9` 会把页面算死（CPU/内存耗尽） | `cleaning/transform.py` | 已修复 |
| BUG-002 | P0 | 「转数字」把 16 位以上长编号悄悄改值（int64 截成 9223372036854775807） | `cleaning/transform.py` | 已修复 |
| BUG-003 | P0 | 计算列整数溢出静默回绕（`4e9*4e9` 得负数） | `cleaning/transform.py` | 已修复 |
| BUG-004 | P1 | 日期列混时区时整页变成英文 ValueError 堆栈，导出按钮消失 | `cleaning/transform.py` | 已修复 |
| BUG-005 | P1 | 套用规则模板时「列顺序」引用改名后/计算出的列会被丢掉，规则静默失效 | `cleaning/templates.py` | 已修复 |
| BUG-006 | P1 | 数据区合并单元格读出来是空的，勾「删除空值行」会误删且无提示 | `cleaning/xlsx_scan.py`、`cleaning/io.py` | 已修复 |
| BUG-007 | P1 | 干净环境（无 lxml）下 4 项测试失败，CI 会红 | `tests/test_app_logic.py` | 已修复 |
| BUG-008 | P2 | README 说重复列名会加 `_2`，实际是 pandas 的 `.1` | `README.md` | 已修复（改文档） |
| BUG-009 | P1 | `round(1, -10000000)` 绕过指数守卫，算 10 的 1000 万次方（18 秒） | `cleaning/transform.py` | 已修复（复审补充） |
| BUG-010 | P1 | BUG-002 的修复在「纯整数长编号列」上失效，XLSX 导出仍把两个身份证号并成一个 | `cleaning/transform.py` | 已修复（复审补充） |
| BUG-011 | P2 | `-3%` 被当公式加前缀，工具自身往返一次就改了值 | `cleaning/rules.py` | 已修复（复审补充） |
| BUG-012 | P2 | XLSX 里以文本保存的 `00123` 读进来变成 `123`，前导零丢失 | `cleaning/io.py` | 已修复（复审补充） |

---

## BUG-001（P0）计算列的指数爆炸会把进程算死

**位置**：`cleaning/transform.py` 白名单幂运算（修复前 `_ALLOWED_BINOPS[ast.Pow] = operator.pow`；修复后 `_safe_pow`，第 31–52 行）

**复现**：

```bash
python3 -c "import pandas as pd; from cleaning.transform import safe_eval; print(safe_eval('9**9**9', pd.DataFrame({'a':[1]})))"
# 修复前：命令挂住（8 秒超时仍未返回，exit 124）；9**387420489 约 1.6 GB 的整数
```

界面上：第 4 步计算列填 `爆炸=9**9**9` → 页面卡死、内存飙升，用户只能杀进程。

**影响**：拒绝服务（自己或同实例的其他人）。表达式长度限制（200 字符）挡不住——这条表达式只有 8 个字符。

**修复**：`_safe_pow` 在真正求值前估算结果的二进制位数（`exp × (bit_length(base)-1)`），超过 4096 位直接抛中文 `TableError`：「计算结果太大（超过 4096 位整数），已停止计算以免卡死」。列参与的幂运算仍是浮点，溢出成 `inf` 后由计算列统一留空并提示，不受影响。

**对应测试**：
- `tests/test_regressions_v201.py::test_huge_exponent_is_rejected_instead_of_hanging`
- `::test_reasonable_exponents_still_work`（`2**10`、`(9**9)**9`、`a**2`、`a**100000` 不误伤）
- `::test_pow_blowup_does_not_freeze_clean_table`
- `tests/test_app_ui_v2.py::test_exploding_expression_does_not_freeze_the_page`

**修复后**：`9**9**9`、`10**100000000`、`2**2**64` 均在 **0.000 s** 内给出中文提示；`2**1000` 照常算出 302 位整数。

**残余风险**：`**` 之外没有能爆内存的运算符；`round()` 的 ndigits 上限未单独限制，但不会造成大整数（实测无风险）。

---

## BUG-002（P0）长编号「转数字」被悄悄改值

**位置**：`cleaning/transform.py::numeric_series`（第 136–160 行）

**复现**：

```python
import pandas as pd, cleaning
s = pd.Series(["123456789012345678901234", "9007199254740993"])
print(cleaning.convert_to_number(s)[0].tolist())
# 修复前： [9223372036854775807, 9007199254740992]   ← 两个值都被改了
# 修复后： ['123456789012345678901234', '9007199254740993']（保留原文）+ failed=2
```

XLSX「尝试转数字」模式同样命中：

```python
cleaning.to_xlsx_bytes(pd.DataFrame({"id": ["123456789012345678901234"]}), number_mode="number")
# 修复前：单元格是 1.234567890123457e+23
# 修复后：单元格是文本 '123456789012345678901234'
```

**影响**：订单号、身份证号、条码、运单号这类长编号在导出文件里变成**另一个数字**，而且没有任何提示。这是最危险的「静默数据损坏」：文件能打开、程序不报错、用户不看不知道。

**根因**：`pandas.to_numeric` 对超出 int64 的整数会截到 `9223372036854775807`；落在 int64 内但超过 2^53 的整数会经过 float64 丢精度。

**修复**：转换后做精度守卫——超过 15 个数字字符的格子逐个用 `Decimal` 核对「转完的数值是否与原文相等」；不相等（或转成 `inf`）就不算转换成功，`convert_to_number` 保留原文并在页面提示「N 个单元格没转成功（例如…）」，`numbers_where_possible` 则保持文本导出。`00123 → 123`、`1,000 → 1000` 这类预期效果不变。

**对应测试**：
- `::test_convert_to_number_never_changes_long_ids`
- `::test_number_mode_export_keeps_long_ids_as_text`
- `::test_numeric_series_is_exact_for_15_digit_numbers`（15 位以内、千分位、`1E5`、`1E400` 边界）

**修复后**：4 类长编号全部保持原文；`123456789012345`（15 位）、`0.1`、`1,234,567.89` 照常转数字。

**残余风险**：XLSX 里**已经是数字**的长编号（Excel 保存时就已经丢精度）工具无法恢复，只能提示用户改文本格式后重新录入——已写进 `FAQ.md`。

**第二轮复审补充（重要）**：上面的修复在「整列都是整齐整数」时**失效**——pandas 对纯整数列给出
精确的 `Int64`，守卫用 `Decimal(str(Int64))` 比较必然相等，于是 18 位身份证号被判定"转换成功"，
导出 XLSX 时仍被四舍五入（`110101199003071234` 与 `110101199003071235` 并成同一个值）。
第一轮的测试用列里混了 24 位（迫使整列走 float64），恰好绕开了这条路径。
最终修复：守卫改用 **float64 的精度**（Excel/XLSX 真正存数字的精度）比较，
并补 `test_pure_integer_id_column_keeps_text_after_number_conversion`。

---

## BUG-003（P0）计算列整数溢出静默回绕

**位置**：`cleaning/transform.py::_numeric_column`（第 243–256 行）、`add_computed_column`（第 348 行起）

**复现**：

```python
import pandas as pd, cleaning
df = pd.DataFrame({"数量": ["4000000000"], "单价": ["4000000000"]})
r = cleaning.clean_table(df, cleaning.CleanOptions(
    columns=cleaning.ColumnOps(computed=(("总额", "数量*单价"),))))
print(r.df["总额"].tolist())
# 修复前：[-2446744073709551616]（numpy int64 回绕，负数！且没有任何提示）
# 修复后：[1.6e+19]
```

`3**100000` 更直观：修复前得到 `-3665183052406099839`，修复后留空并提示「结果是无穷大（除以 0 或数字太大）」。

**影响**：计算列可能给出**看起来正常但完全错误**的数字（负数、怪数），属于「输出结果不可信」。

**根因**：列是文本数字 → `pd.to_numeric` 得到 int64 → numpy 的整数乘/幂溢出时不报错，直接按 2^64 回绕。

**修复**：计算列里的操作数一律转成 `float64`（和 Excel 一样用双精度），溢出变 `inf` → 统一留空 + 中文提示；算完再用 `_tidy_numbers` 把整数值显示回整数（`4.0 → 4`，界面/下载的观感与修复前一致）。

**对应测试**：`::test_computed_column_does_not_silently_wrap_on_integer_overflow`

**残余风险**：按 Excel 口径，超过 15 位有效数字的计算结果仍会有浮点误差（Excel 本身也是这个精度），已在 README 与 FAQ 说明。

---

## BUG-004（P1）日期列混时区导致整页英文堆栈

**位置**：`cleaning/transform.py::convert_to_date`（第 203–241 行）

**复现**：上传 CSV `日期 / 2024-05-01T10:00:00+08:00 / 2024-05-02T10:00:00+09:00`，在第 4 步把「日期」勾成「转成日期」。
修复前：整个页面变成 `ValueError: Mixed timezones detected. Pass utc=True …` 的英文堆栈，第 5/6/7/8 步全部消失，导出按钮没了。
修复后：页面正常，提示「「日期」转日期时有 2 个单元格没转成功，已保留原文（例如…）」。

**影响**：用户操作直接让页面不可用；提示是英文且无法执行（用户不知道 `utc=True` 是什么）。

**根因**：pandas 3.0 对「同列混合时区」直接抛 `ValueError`，pandas 2.2 则返回 object 列（之后 `.dt` 访问器抛 `AttributeError`）——两个受支持的版本行为不同，旧代码都没处理。

**修复**：整段解析包在异常处理里；解析结果不是 datatime 类型时也按「整列没转成功、保留原文」处理（不猜时区，避免把日期算错）。同一时区的日期保留「当地时间的墙上时间」并去掉时区标记（Excel 不支持时区），导出 XLSX 不再失败。

**对应测试**：
- `::test_mixed_timezone_dates_keep_original_text_instead_of_crashing`
- `::test_single_timezone_dates_drop_tz_but_keep_wall_clock`
- `tests/test_app_ui_v2.py::test_mixed_timezone_dates_show_warning_instead_of_traceback`
- 在 pandas 2.2.3 与 3.0.6 两套环境中都跑（否则会漏掉 2.2 的 `AttributeError` 变体）

**残余风险**：带时区的日期会丢掉时区标记（只保留墙上时间），已在 README 说明。

---

## BUG-005（P1）模板「列顺序」里引用新列名会静默失效

**位置**：`cleaning/templates.py::filter_for_columns`（第 179 行起）

**复现**：

```python
rules = {"列重命名": [["金额","总金额"]], "列顺序": ["总金额","订单号"]}
print(cleaning.filter_for_columns(rules, ["订单号","金额"]))
# 修复前： 列顺序 -> ['订单号']，并提示「模板里有当前文件没有的列，已忽略：总金额」 ← 假提示
# 修复后： 列顺序 -> ['总金额','订单号']，无提示
```

界面上的后果：保存一套「金额→总金额 并把总金额排最前」的模板，下次套用时改名生效、排序丢失，还骗用户说「总金额」这个列不存在。

**根因**：「列顺序」是**最后一步**执行的，用的是改名后/计算出的新列名；过滤时却只拿原始列名核对。

**修复**：先过滤重命名，把「改名目标列名 + 计算列名」加入「列顺序」的可用集合；真正不存在的列照旧剔除并提示。计算列本身仍保留原样（执行阶段给具体报错）。

**对应测试**：
- `::test_template_keeps_column_order_for_renamed_and_computed_columns`
- `::test_template_still_drops_columns_the_file_really_lacks`（不能把真缺列也放过）

**残余风险**：模板里 `删除列`/`转数字`/`转日期` 仍按原列名匹配（这两步本来就在重命名之前执行），行为正确。

---

## BUG-006（P1）数据区合并单元格读成空值，删空值行会误删

**位置**：`cleaning/xlsx_scan.py::count_data_merges`（第 106 行起）、`cleaning/io.py::_read_xlsx`（第 249、269 行）

**复现**：

```
分类,商品,金额
水果,苹果,10
水果,梨,20      ← A2:A3 合并，读出来第 3 行「分类」是空的
```

勾「删除指定列中有空值的行 → 分类」→ 修复前直接删掉「梨」这一行，页面没有任何提示。
修复后页面先提示：「这张工作表里有 1 处合并单元格压在数据行上……别把这类行当成空值行删掉。」

**影响**：静默少数据（用户以为「分类」那行本来就是空的）。合并单元格在中文报表里非常常见。

**修复**：读表时用流式 XML 扫描（不开 openpyxl、不占额外内存）数出「结束行落在表头以下」的合并区域，作为一条说明返回；表头及以上的合并（标题、合并表头）不重复提示。20 万行 × 4 列的 XLSX 实测只多花 **0.10 s**。

**对应测试**：
- `::test_merged_data_cells_are_reported_before_deleting_empty_rows`（同时断言「不提示的话确实会删掉 1 行」，证明提示不是空话）
- `::test_merged_header_only_is_not_reported_as_data_merge`

**残余风险**：只是提示，工具不会替用户填充合并值（那会改变数据含义）。

---

## BUG-007（P1）干净环境（无 lxml）下 4 项测试失败

**位置**：`tests/test_app_logic.py::_tamper_dimension` 与 `test_xlsx_without_dimension_still_counts_columns`

**复现**：`python3 -m venv 新环境 && pip install -r requirements-dev.txt && pytest`
修复前：`4 failed, 206 passed`，失败原因是测试里用 `re.sub(rb'<dimension ref="[^"]*"/>', …)` 精确匹配 XML；有 lxml 时 openpyxl 写成 `<dimension ref="A1:B2"/>`，没有 lxml（干净环境就是）时写成 `<dimension ref="A1:B2" />`，多一个空格，断言「测试本身失效」。

**影响**：`requirements-dev.txt` 不装 lxml，所以 `.github/workflows/tests.yml` 的 CI 在干净 runner 上会红；README 宣称的「194 项全通过」只在装了 lxml 的开发机上成立。

**修复**：正则改为 `<dimension\b[^>]*?/>`，两种序列化都认；断言逻辑不变。修复后在「无 lxml 的干净 venv」和「有 lxml 的系统环境」都 89 项全通过。

**对应测试**：就是这 4 项本身（修复后它们才是真的回归测试）。

**残余风险**：无（测试只放宽了序列化空格，没有放宽业务断言）。

---

## BUG-008（P2）文档与实际的列名去重规则不一致

**位置**：`README.md`「判定规则」「表头」段落

**问题**：文档写「重名自动加 `_2`」，实际是 pandas 先改名成 `金额.1`（`金额` + `.1`）。用户按文档去下拉框里找 `金额_2` 会找不到。

**修复**：文档改成「重复列名自动加序号区分（pandas 先改成的形式是 `金额.1`）」；并在 FAQ 补了一条。

**残余风险**：无（只是文案）。

---

## 已确认但未修的项（明确披露，不是遗漏）

| 项 | 为什么没修 | 影响 | 已写入 |
| --- | --- | --- | --- |
| XLSX 里已经是数字的长编号（>15 位）读进来就已经丢精度 | Excel 保存时信息已丢失，工具无法恢复；强行改文本只能掩盖问题 | 用户看到的编号可能是错的 | FAQ、README「已知限制」 |
| XLSX 的百分比/货币/自定义数字格式不保留 | 要保留格式得实现整套单元格样式读写，属于新功能而不是修 bug | 15% 打开后显示 0.15 | FAQ |
| 大 XLSX（20 万行）每次点按都会重新读取（约 8.4 s） | 需要缓存整张表（内存翻倍）或改架构；目前有「正在读取」提示 | 手机上操作偏慢，但不丢数据 | README「已知限制」、FAQ |
| 超时是协作式的（不能打断单次 pandas/openpyxl 调用） | 真要抢占需子进程 + 数据拷贝，手机内存更紧 | 极端大文件可能超过设定秒数才停 | README 已有说明，保留 |
| 没有 LICENSE 文件 | 需要销售主体名称与授权条款，不能由审查方代填 | **商业交付前必须补** | `RELEASE_CHECKLIST.md` 标为阻塞项 |
| 没有免安装的可执行包 | 打包需要 Windows/macOS 实机与签名，本环境（Android 容器）做不到 | 非技术客户需要人协助安装 Python | 最终判定按 A（有限试用）而不是 B |

## 修复过程中发现、已顺带处理的边界

- 「转数字」遇到 `1E400` 会转成 `inf`，导出 CSV 会写出 `inf`：现在按「没转成功」保留原文。
- `3**100000` 溢出提示原来是「有 N 个单元格除以 0」，措辞不准确：改为「结果是无穷大（除以 0 或数字太大）」。
- `_tidy_numbers` 遇到超出 int64 的整数值会抛 `OverflowError`：现在先判断再转，保持浮点。
- 计算列引用日期/布尔列时，原来会抛原生英文异常：现在给中文提示「列「X」不是数字…不能参与计算」。


---

## 第二轮独立复审补充的问题（BUG-009 ~ BUG-012）

> 来源：另起新上下文的复审代理（2026-10-20）。它先在**已修复的 v2.0.1** 上做对抗探测，
> 下面 4 个问题都是第一轮漏掉的；修复后同样补了「旧代码上失败、新代码上通过」的回归测试。

### BUG-009（P1）`round()` 可绕过指数守卫，卡住十几秒

**位置**：`cleaning/transform.py` 的 `_ALLOWED_FUNCS`（修复后为 `_safe_round`）

**复现**：

```bash
python3 -c "import pandas as pd, time; from cleaning.transform import safe_eval; t=time.time(); print(safe_eval('round(1, -10000000)', pd.DataFrame({'a':[1]}))); print(time.time()-t)"
# 修复前：18.3 秒才返回 0（C 层算 10**10000000，SIGTERM 也打不断）
# 修复后：0.000 秒抛中文 TableError「round() 的保留位数 -10000000 太大（支持 -30~30）」
```

**影响**：与 BUG-001 同类（页面卡住、CPU 占满），而且一次只算一个数，用户完全不知道原因。

**修复**：`_safe_round` 限制保留位数在 ±30（Excel 只需 15），超出给中文提示；正常 `round(x, 2)` 不受影响。

**对应测试**：`::test_round_with_huge_ndigits_is_rejected`

### BUG-010（P1）纯整数长编号列：BUG-002 的修复失效

见上面 BUG-002 的「第二轮复审补充」。**对应测试**：
`::test_pure_integer_id_column_keeps_text_after_number_conversion`

### BUG-011（P2）负百分比被当成公式

**位置**：`cleaning/rules.py::_PLAIN_NUMBER_RE`（「纯数字不算公式」的白名单）

**复现**：

```python
cleaning.to_csv_bytes(pd.DataFrame({"m": ["-3%"]}))
# 修复前： '-3%（多了一个单引号；重新导入就是 '-3%，工具自身改了值）
# 修复后： -3%
```

**影响**：财务表里的负百分比列导出后多出可见单引号，再导入就对不上。

**修复**：纯数字判定允许结尾的 `%`（`-3%`、`+12.5%` 在 Excel 里是数值）；`-3%+1` 这类仍算可疑。

**对应测试**：`::test_negative_percentage_is_number_not_formula`

### BUG-012（P2）XLSX 文本编号读入丢前导零

**位置**：`cleaning/io.py::_read_xlsx`（`book.parse` 未指定 dtype）

**复现**：openpyxl 写文本单元格 `"00123"` → 上传 → 页面显示 `123` → 导出还是 `123`。

**影响**：XLSX 里以文本保存的编号（用户特意设成文本防止 Excel 吃零）被静默改值；
也和自己「导出的 XLSX 默认按文本写入」的行为对不上（自己导出的文件再传回来会丢零）。

**修复**：XLSX 用 `dtype=object` 读入（保留文本单元格原样），再用 `_restore_value_types`
把「整列本来就是数字/日期对象」的列还原成正常 dtype——文本列不动，数字/日期列行为不变。
实测 20 万行 × 4 列 XLSX 读取耗时没有明显变化（新旧各测 3 次，新旧差异在测量噪声范围内）。

**对应测试**：`::test_xlsx_text_number_cells_keep_leading_zeros`

**残余风险**：混着文本和数字的列（`"001"` 和 `2` 同一列）保持原样（不猜测），
逐值类型会不同——这是最保守的选择。
