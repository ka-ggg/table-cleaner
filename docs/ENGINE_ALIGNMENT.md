# 引擎对齐说明（v2.4.0）

> 本文是 v2.4.0 双引擎实现的**摘要**，不是第二份权威表。权威来源始终是
> `cleaning/engine/registry.py`（17 项能力）与 `cleaning/engine/known_differences.py`
> （已知差异登记）；本文的数字与状态由 `tests/test_v240_docs.py` 读取上述模块后逐项校验，
> 文档与代码不一致时测试失败。

## 1. 一句话结论

v2.4.0 的默认引擎仍是 **pandas**，清洗语义、默认值、上限、公共 API、CLI / HTTP API /
Streamlit 默认行为与 v2.3.0 一致。Polars 是**内部预览**的可选引擎：只有显式调用
`clean_table(..., engine="polars")` / `clean_bytes(..., engine="polars")` 才会使用；
CLI、HTTP API、Streamlit 与批量入口都不暴露引擎选项。未安装 Polars 时默认 pandas
路径完整可用。

## 2. 入口接法

| 入口 | v2.3.0 | v2.4.0 | 默认行为 |
| --- | --- | --- | --- |
| `clean_table(df, options, ...)` | 无 `engine` | 新增 keyword-only `engine="pandas"` | 不变；默认直通原 pandas 实现 |
| `clean_bytes(filename, data, options, ...)` | 无 `engine` | 新增 keyword-only `engine="pandas"`，向下传给 `clean_table` | 不变 |
| `run_batch(...)` | 无 `engine` | 不新增 | 仍走 pandas |
| CLI `table-cleaner` | 无引擎选项 | 不新增 | 不变 |
| HTTP API `table-cleaner-api` | 无引擎字段 | 不新增 | 不变 |
| Streamlit `app.py` | 无引擎选项 | 不新增 | 不变 |

- `engine="pandas"`（以及默认值）走 `transform.clean_table` 的现行实现，**不 import
  引擎包**，因此默认安装没有 Polars 也不会多一次导入或依赖检查。
- `engine="polars"` 才惰性 import 可选引擎。未安装 Polars / 实现模块时抛中文
  `TableError`，提示安装可选依赖，**不会静默改用 pandas**。
- 引擎能力表或执行前预检判定必须回落的节点，会让**整条流水线**改走 pandas；
  计划描述标记 `effective_engine=pandas` 与回落节点，不做运行期静默重跑。
- `sample > 0` 时 Polars 统一回落 pandas，保证被删行样例、顺序与「删除原因」完全一致。

`cleaning.__all__` 不因引擎/DSL 增删；`engine` 关键字是内部预览，不是新的公共导出。

## 3. 17 项能力状态

状态与 `cleaning/engine/registry.py` 的 `CAPABILITIES` 一致；`aligned` = 常见路径由
Polars 原生执行，`partial_fallback` = 常见路径对齐、已登记边界整条流水线回落 pandas，
`full_fallback` = 整项回落（本版为 0 项）。

| ID | 类型 | 名称 | Polars 状态 | 说明 |
| --- | --- | --- | --- | --- |
| R1 | 节点 | 全行去重 | aligned | unique(maintain_order=True) 在 null、空文本、多列组合上保持首次出现，语义可逐值复现。 |
| R2 | 节点 | 按列去重 | aligned | unique(subset=..., maintain_order=True) 与 pandas 的按列判重、保留首次出现一致。 |
| R3 | 节点 | 删空值行 | aligned | filter + 「null 或空文本」谓词复现同一判定；分块处理与整表结果一致。 |
| R4 | 节点 | 删列 | aligned | drop(columns) 直接对应；不存在列的提示沿用现行文案。 |
| R5 | 节点 | 转数字 | partial_fallback | 常见数字/千分位/科学计数路径对齐；货币符号、百分号、长编号、溢出、混合列等边界整节点回落 pandas。 |
| R6 | 节点 | 转日期 | partial_fallback | ISO、/ 等明确格式走 Polars 解析；中文年月日按当前 pandas「保留原文并计数」对齐；混合格式、混时区整节点回落 pandas，不猜格式。 |
| R7 | 节点 | 列改名 | aligned | rename 映射一次应用；重名冲突与不存在列沿用现行提示，不改变列顺序。 |
| R8 | 节点 | 计算列 | partial_fallback | 四则（除零→inf 留空）、一元、round/abs、幂守卫核心路径对齐；//、%、** 与非数字列等边界整节点回落 pandas。 |
| R9 | 节点 | 列顺序 | aligned | select(wanted + rest) 与现行「点名前、其余原顺序」一致。 |
| I1 | 检查 | 空值计数 | aligned | null 计数 + 空文本计数，与 missing_mask 同一判定。 |
| I2 | 检查 | 完全重复行计数 | aligned | height - unique(maintain_order=True).height 与 duplicated().sum() 一致；null 相等性也一致。 |
| I3 | 检查 | 可疑格式检查 | aligned | 按逻辑类型识别文本列，首尾空格与公式判定精确优先，可接受逐值回调。 |
| X1 | 表达式 | 四则/整除/取模/幂 | aligned | 按同一运算符与负数语义映射到 pl.Expr，逐项差分。 |
| X2 | 表达式 | 一元正负 | aligned | 一元正负直接对应 Polars 表达式，无额外 dtype 语义。 |
| X3 | 表达式 | round() | aligned | 按同一保留位数上限与半偶舍入对齐；若 W3 差分发现不一致则登记 partial_fallback。 |
| X4 | 表达式 | abs() | aligned | abs() 对负数与 null 的语义一致。 |
| X5 | 表达式 | 幂运算守卫 | aligned | 编译期常量位数检查 + 运行期 inf 处理，共用同一上限。 |

统计：17 项能力中 14 项 `aligned`、3 项 `partial_fallback`（R5 / R6 / R8）、0 项
`full_fallback`。9 个流水线节点中，有整节点回落路径的是 3/9（R5 / R6 / R8），
其余 6/9 节点无整节点回落；核心结构算子 R1 / R2 / R3 / R4 / R7 / R9 为 6/6 对齐。

## 4. 对齐验证口径

`tests/test_v240_engine_alignment.py` 共 58 组用例（52 组能力用例 + 3 组完整流水线 +
3 组 benchmark 端到端），按 L1 逻辑值、L2 CSV/XLSX 字节或读回值、L3 报告三层比较：

- 独立承担率 **50 / 58 = 86.2%**，高于 CP2 门禁 ≥80%；
- 核心结构算子 **6/6** 对齐；
- 整节点回落 **0/9**（R5 / R6 / R8 只有已登记边界回落，不是整节点回落）；
- 每个 Polars 用例执行前把 pandas 参考入口替换成会抛错的哨兵，证明没有偷偷回落；
- 回落用例断言预检结论为 pandas、结果与默认路径完全一致，并能在差异登记表找到条目。

## 5. 已知差异与回落清单

共 10 条登记（9 条回落、1 条已对齐），无「待定 / 未知」项，无 `accepted_difference`。
字段含义：`fallback` = 边界输入整条流水线回落 pandas；`align` = 已规范化消除差异。

| 差异 ID | 能力 | 触发用例 | 决定 | 范围 | 计划解决版本 |
| --- | --- | --- | --- | --- | --- |
| KD-0001 | R5 | R5-currency-percent | fallback | boundary | v3.0 |
| KD-0002 | R5 | R5-long-id-15-18 | fallback | boundary | v3.0 |
| KD-0003 | R5 | R5-overflow-inf | fallback | boundary | v3.0 |
| KD-0004 | R5 | R5-mixed-column | fallback | boundary | v3.0 |
| KD-0005 | R6 | R6-mixed-formats | fallback | boundary | v3.0 |
| KD-0006 | R6 | R6-mixed-timezones | fallback | boundary | v3.0 |
| KD-0007 | R8 | R8-pow-guard | fallback | boundary | v3.0 |
| KD-0009 | engine | PIPE-sample-fallback | fallback | boundary | v3.0 |
| KD-0010 | engine | PIPE-full-aligned | align | common_path | v2.4.0 |
| KD-0011 | engine | engine-dtype-fallback | fallback | boundary | v3.0 |

摘要：

- 转数字（R5）：货币符号、百分号、超过 15 位有效数字的长编号、溢出、混合列边界回落；
- 转日期（R6）：混合格式、混时区边界回落；ISO、`/`、中文年月日明确格式路径由 Polars 执行；
- 计算列（R8）：`//`、`%`、`**` 的边界与常量幂守卫回落；
- 引擎级：`sample > 0` 回落；混合 object 列、带时区 datetime 等无法无损映射的 dtype 预检回落；
- 已对齐（KD-0010）：Polars 执行后按原 dtype 回填 pandas，L1/L2/L3 完全一致，仅内部
  null/NaN 表示不同且不可见。

## 6. 回退演练（未安装 Polars 时默认路径完整可用）

v2.4.0 发布前按下面步骤演练，并用 `tests/test_v240_entry_compat.py` 自动重复执行。
演练用 `sys.modules["polars"] = None` 模拟未安装 Polars，不依赖真实卸载：

```bash
python - <<'PY'
import sys
sys.modules["polars"] = None  # 模拟未安装可选依赖

import cleaning

options = cleaning.options_from_rules({"删除完全重复的行": True})
data = "订单号\nA1\nA1\n".encode("utf-8-sig")
result = cleaning.clean_bytes("订单.csv", data, options)
assert result.rows_before == 2 and result.rows_after == 1

try:
    cleaning.clean_bytes("订单.csv", data, options, engine="polars")
except cleaning.TableError as exc:
    print("默认 pandas 路径正常；显式 Polars 报中文错误：", exc)
else:
    raise SystemExit("显式 Polars 应报错但没有")
PY
```

演练结果（v2.4.0 实现期实跑）：

- 默认 `clean_bytes` / `clean_table` / `run_batch` / CLI / HTTP API 全部可用，结果与安装
  Polars 时逐字节一致；
- 显式 `engine="polars"` 抛中文 `TableError`，提示缺少可选依赖，并说明默认引擎仍是 pandas；
- 默认入口没有 import `cleaning.engine` 或 `polars`，未安装 extra 的安装体积与导入耗时不受影响。

## 7. 性能记录（P1，仅信息性）

v2.4.0 **不设性能门禁**，不承诺 500 万行、不承诺内存下降。P1 benchmark 只记录
pandas / Polars 小、中、大三档（固定种子 `20231106`、每档 3 次中位数），结果见
`benchmarks/ENGINE_BENCHMARK.md`。Polars 数字包含 pandas↔Polars 转换开销，不能解释为
v3.0 性能；大档在 8 GB 环境下可能吃紧，失败不阻塞本版。

## 8. 本版明确未做

- 不做 lazy / streaming，不做行级血缘，不做性能目标（留 v3.0）；
- 不把 Polars 暴露为 CLI / HTTP API / UI 选项，不新增公共导出；
- 不做运行期静默切换引擎；回落必须在执行前显式判定并登记；
- 不处理 pillow 既有漏洞（单独评审，不在本 PR）。
