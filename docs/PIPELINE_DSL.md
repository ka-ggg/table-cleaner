# Pipeline DSL v1 说明（v2.4.0，内部预览）

> v2.4.0 的 Pipeline DSL 是**内部预埋**：`cleaning/pipeline.py` 不作为公共 API 导出，
> CLI / HTTP API / Streamlit 都不读取 DSL。它的作用是把现有 `CleanOptions` 规则变成
> 可序列化、可校验、可迁移、可描述计划的 JSON 格式，为 v3.0 做准备。
> 本文与 `cleaning/schema/pipeline-v1.json`、`cleaning/pipeline.py` 保持一致；
> 节点类型与顺序由 `tests/test_v240_docs.py` 读取代码后校验。

## 1. 边界与不承诺

- 不新增 `cleaning.__all__` 导出；`Pipeline` / `load_pipeline` 等只能从
  `cleaning.pipeline` 内部导入。
- 不执行清洗、不 import Streamlit、不 import Polars；执行仍由
  `clean_table(df, options, engine=...)` 完成。
- 不做 DSL 执行 UI、行级血缘、可复现运行记录、lazy / streaming 标记的真实执行。
- 模板格式 `template_version` 不变；DSL 只是模板的迁移目标，不替换模板。

## 2. 顶层结构

| 字段 | 必填 | 类型 | 说明 |
| --- | --- | --- | --- |
| `dsl_version` | 是 | 整数 | 当前固定为 `1`；缺失、非整数、小于 1、高于当前支持版本都报中文错误 |
| `name` | 否 | 字符串 | 流水线名称，仅展示；长度至少 1 |
| `engine` | 否 | 对象 | `{"preference": "pandas"}`；`preference` 只允许 `pandas` / `polars`，默认 `pandas`；v2.4.0 仅内部使用 |
| `source` | 否 | 对象 | `{"header_row": 1}`；默认 1；不包含 sheet 等 IO 选择 |
| `nodes` | 是 | 数组 | 节点数组；空数组合法，表示不做任何清洗 |

`engine.preference` 是**偏好**，不是强制。能力表或执行前预检判定必须回落时，整条
流水线改走 pandas；计划描述用 `effective_engine` 表示实际引擎。

## 3. 节点类型（9/9）

每个节点必须包含 `id`（唯一、稳定、非空且不含空白）、`type`、`params`；可选
`enabled`（默认 `true`）与 `description`（仅展示）。

| 阶段 | 节点类型 | 参数 | 对应现有行为 |
| --- | --- | --- | --- |
| 10 | `row.drop_duplicates` | 无（空对象） | `drop_duplicates=True` |
| 20 | `row.dedupe_by` | `{"columns": ["订单号"]}` | `CleanOptions.dedupe_on` |
| 30 | `row.drop_missing` | `{"columns": ["客户名称"]}` | `CleanOptions.drop_missing_in` |
| 40 | `column.drop` | `{"columns": ["备注"]}` | `ColumnOps.drop` |
| 50 | `column.to_number` | `{"columns": ["金额"]}` | `ColumnOps.to_number` |
| 60 | `column.to_date` | `{"columns": ["下单日期"]}` | `ColumnOps.to_date` |
| 70 | `column.rename` | `{"renames": [{"from": "客户名称", "to": "客户"}]}` | `ColumnOps.rename` |
| 80 | `column.compute` | `{"name": "含税金额", "expression": "金额*1.13"}` | `ColumnOps.computed`，一个节点一列 |
| 90 | `column.order` | `{"columns": ["订单号", "客户", "含税金额"]}` | `ColumnOps.order` |

参数约束：

- 列名数组至少 1 项、不能重复、每项非空字符串；
- 重命名数组至少 1 项，`from` / `to` 都非空；
- 计算列 `expression` 长度 1–200，沿用现有 AST 白名单与安全求值；
- 空参数对象只允许用于 `row.drop_duplicates`；未知参数会被拒绝，不静默忽略。

## 4. 顺序与合并规则

DSL 必须保持与现有 `CleanOptions` / `ColumnOps` 相同的固定阶段顺序，避免「节点随便
排序」导致与模板语义不一致。顺序错误会指出是第几个节点、哪个 id、哪两个阶段颠倒。

| 阶段 | 节点 | 合并规则 |
| --- | --- | --- |
| 10 | `row.drop_duplicates` | 最多 1 个 |
| 20 | `row.dedupe_by` | 最多 1 个 |
| 30 | `row.drop_missing` | 最多 1 个 |
| 40 | `column.drop` | 最多 1 个 |
| 50 | `column.to_number` | 可多个；列名按首次出现去重 |
| 60 | `column.to_date` | 可多个；列名按首次出现去重 |
| 70 | `column.rename` | 可多个；合并为一个 rename 映射 |
| 80 | `column.compute` | 可多个；按节点顺序执行，同名列跳过并提示 |
| 90 | `column.order` | 最多 1 个，且必须是最后一个阶段 |

`enabled: false` 的节点在顺序校验与编译时都视为不存在；但计划描述仍会列出它，
只是不改变输入/输出列。

## 5. 三层校验与中文错误

任何一层失败都不执行半条流水线，也不产生半成品导出。

1. **版本前置检查**：`dsl_version` 缺失、非整数（布尔值也算非整数）、小于 1、
   大于当前支持版本；不进入 schema 校验。
2. **JSON Schema 结构校验**：必填字段、类型、枚举、未知字段、节点 id 格式、
   `params` 结构；错误映射成中文并定位到节点序号与 id，例如
   「流水线第 2 个节点（id=to-number-amount）字段「params.columns」应为字符串数组」。
3. **语义校验**：节点 id 唯一、阶段顺序、同阶段合并规则。

错误类别（统一 `TableError`，中文且尽量带节点位置）：

- DSL 文件 / 输入类型 / 编码错误（空字节、坏 JSON、JSON 不是对象、不支持的类型）；
- `dsl_version` 缺失 / 类型错误 / 小于 1 / 高于当前版本；
- schema 结构错误（缺必填字段、未知字段、类型不符、枚举不符、空字符串、长度/数量越界）；
- 节点 id 重复；
- 阶段顺序颠倒；
- 同阶段节点重复；
- 迁移函数缺失；
- `jsonschema` 未安装（只影响内部 DSL 校验，默认 pandas 清洗不受影响）。

## 6. 版本策略

- `dsl_version` 是整数，当前支持 `1`；读取器支持 `1..CURRENT_DSL_VERSION`。
- 任何格式变化（新增可选字段、删除/改名字段、节点类型或参数变化、语义变化）都必须
  升整数版本，并提供从旧版本到新版本的迁移函数；迁移逐版本可测。
- 高于当前版本：报中文「版本过高」，提示升级工具或改用旧格式，不静默解析。
- 低于 1 或非整数：报中文错误，不做猜测。
- 版本迁移当前是恒等迁移（v1 没有旧版本）；`_MIGRATIONS` 为 v2 及以后保留逐版本入口。

## 7. 模板 → DSL 迁移

`template_to_pipeline(template)` 接受模板字节 / JSON 文本 / `TemplateInfo` / 规则字典，
把旧模板迁移为 DSL v1：

- `表头行` → `source.header_row`；
- 行规则布尔值 → 对应节点（仅 `true` 时生成）；
- 列操作列表 → 对应节点；重命名对合并为一个 `column.rename`；
- 计算列按原顺序生成多个 `column.compute`（id 为 `column-compute-1` 等）；
- 列顺序 → `column.order`；
- 模板名称 → `name`；不继承 `保存时间` 等元信息。

迁移承诺「规则与清洗结果一致」，不承诺模板元信息往返。`pipeline_to_options` 编译出的
`CleanOptions` 与 `options_from_rules` 字段逐项相等；对齐测试还验证 3 个数据集清洗结果
一致。

## 8. 计划描述

`describe_pipeline(pipeline, columns)` 返回只读 `PipelinePlan`，不读取数据行、不执行清洗：

| 字段 | 说明 |
| --- | --- |
| `dsl_version` / `name` | 基本信息 |
| `engine` | 偏好引擎 |
| `nodes` | 顺序节点：id、type、enabled、阶段、输入列、输出列、引擎、回落原因、`lazy_eligible`（本版恒 false） |
| `effective_engine` | 编译期实际引擎；能力表判定回落时是 pandas |
| `fallback_nodes` | 整节点回落 id 与原因 |
| `warnings` | 列不存在、重名等非阻断说明 |
| `input_columns` / `output_columns` | 按列集合模拟得到的输入/输出列 |

计划只做**编译期能力表判定**；执行前数据预检（混合 dtype、时区等）由引擎在
`clean_table(engine="polars")` 内完成，可能再次改走 pandas，但不会产生半成品。

## 9. 示例

```json
{
  "dsl_version": 1,
  "name": "订单清洗",
  "engine": {"preference": "pandas"},
  "source": {"header_row": 1},
  "nodes": [
    {"id": "dedupe-all", "type": "row.drop_duplicates", "params": {}},
    {"id": "dedupe-order", "type": "row.dedupe_by", "params": {"columns": ["订单号"]}},
    {"id": "drop-empty-customer", "type": "row.drop_missing", "params": {"columns": ["客户名称"]}},
    {"id": "to-number-amount", "type": "column.to_number", "params": {"columns": ["金额"]}},
    {"id": "to-date-order", "type": "column.to_date", "params": {"columns": ["下单日期"]}},
    {"id": "rename-customer", "type": "column.rename", "params": {"renames": [{"from": "客户名称", "to": "客户"}]}},
    {"id": "compute-tax", "type": "column.compute", "params": {"name": "含税金额", "expression": "金额*1.13"}},
    {"id": "order-columns", "type": "column.order", "params": {"columns": ["订单号", "客户", "含税金额"]}}
  ]
}
```

内部用法：

```python
from cleaning.pipeline import describe_pipeline, load_pipeline, pipeline_to_options

pipeline = load_pipeline(document_text_or_bytes)
options = pipeline_to_options(pipeline)
plan = describe_pipeline(pipeline, columns=["订单号", "客户名称", "金额"])
```

v2.4.0 不把以上函数放进 `cleaning.__all__`，也不在 CLI / HTTP API / Streamlit 中暴露。
