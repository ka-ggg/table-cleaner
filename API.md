# 公共 API 清单（v2.3.0）

> 权威来源：`cleaning/__init__.py` 的 `__all__`。本文件逐项说明用途与入参/出参约定；
> `__all__` 之外的名字一律视为内部实现，不承诺兼容。旧版 `from cleaning import ...`
> 的导入路径保持可用；新增导出只增不删。

## 1. 边界与承诺

- **入口层**：`app.py`（Streamlit）、`table-cleaner`（CLI）、`table-cleaner-api`（本地 HTTP）
  只能调用本清单中的名字。`app.py` 不直接 import `cleaning.io` / `cleaning.transform` /
  `cleaning.export` 等内部模块；该约束由 `tests/test_v23_public_api.py` 自动检查。
- **规则载体**：`CleanOptions` 与 `ColumnOps` 是唯一的规则载体，界面、CLI、API 不各拼一套参数。
- **错误约定**：用户可见错误统一抛 `TableError`，消息为中文；入口层负责收敛为退出码/HTTP 错误，
  不把英文堆栈抛给用户。
- **版本**：`cleaning.__version__` 为包版本；模板格式版本单独用 `template_version` 表示。

## 2. 版本与上限

| 名字 | 用途 |
| --- | --- |
| `__version__` | 包版本字符串，当前 `2.3.0` |
| `MAX_UPLOAD_BYTES` / `MAX_ROWS` / `MAX_COLS` / `MAX_CELLS` / `MAX_XLSX_UNCOMPRESSED_BYTES` | 现行默认上限常量；运行时可被 `TABLE_CLEANER_*` 环境变量覆盖 |
| `API_MAX_JOBS` / `API_MAX_BODY_BYTES` / `API_READ_TIMEOUT_SECONDS` | 本地 HTTP API 雏形的内存任务数、请求体上限、正文读取超时 |
| `EXCEL_MAX_ROWS` / `EXCEL_MAX_COLS` | Excel 格式硬上限，导出时使用 |
| `CHUNK_ROWS` / `CHUNK_THRESHOLD_ROWS` / `DEFAULT_TIMEOUT_SECONDS` | 分块与硬超时默认值 |
| `Limits` | 统一配置对象（NamedTuple）：上传字节、行/列/单元格、XLSX 解压上限、超时、分块；提供 `with_overrides()`、`from_env()` |
| `current_limits(**overrides)` | 读取当前生效配置；优先取 `cleaning` 包上的同名常量，便于测试/界面按会话覆盖 |
| `check_shape_limits(...)` / `raise_shape_limit(...)` | 解析前/解析后按配置拒绝超限，抛中文 `TableError` |
| `Deadline(seconds)` | 协作式硬超时；`check(what)` 超时抛中文 `TableError` |
| `TableError` | 所有可展示给用户的中文错误基类（`ValueError` 子类） |

## 3. 读取

| 名字 | 用途 |
| --- | --- |
| `ALLOWED_EXTS` / `CSV_DELIMITERS` / `XLSX_ONE_SHEET_HINT` | 支持的扩展名、CSV 分隔符表、XLSX 单表提示 |
| `LoadedTable(df, notes, sheet_name, sheet_names)` | 读取结果：数据、非阻断说明、实际工作表名、工作簿全部表名 |
| `load_table(filename, data, sheet=None, header_row=1, timeout=None, limits=None)` | 按扩展名读取 CSV/XLSX；类型/大小/空文件/损坏/超限均抛中文 `TableError` |
| `check_xlsx_uncompressed_size(data, limits=None)` | 解压体积预检，防压缩炸弹 |
| `xlsx_sheet_names(archive)` / `pick_xlsx_sheet(...)` / `scan_part(...)` / `scan_xlsx_sheet(...)` / `limit_reason(exc)` | XLSX 流式预检与选表 |
| `count_data_merges(archive, part, header_rows=1)` | 统计数据区合并单元格数量 |

## 4. 判定、清洗与列变换

| 名字 | 用途 |
| --- | --- |
| `MISSING_RULE` / `DUPLICATE_RULE` / `SPACE_ISSUE` / `FORMULA_ISSUE` | 判定口径唯一文案 |
| `CleanReport(df, steps, removed, notes)` | 清洗结果：数据、每步删行数、被删样例、说明 |
| `find_missing(df)` / `missing_mask(df)` / `find_suspicious(df)` / `summarize(df)` | 检查空值、可疑格式、前后统计 |
| `clean_data(df, ...)` | 只按行规则清洗，返回 DataFrame |
| `clean_report(df, ...)` / `clean_table(df, options, ...)` | 行清洗 + 列变换；`clean_table` 是入口层推荐用法 |
| `make_sample_df()` | 虚构样例数据 |
| `CleanOptions` / `ColumnOps` | 唯一规则载体；默认空 = 不做任何清洗/列变换 |
| `apply_transforms(df, ops)` | 按「删列 → 转换 → 重命名 → 计算列 → 列顺序」应用列变换 |
| `convert_to_number(series)` / `convert_to_date(series)` / `numeric_series(series)` | 类型转换与数字解析（含长编号精度守卫） |
| `parse_names(text)` / `parse_pairs(text, example=...)` | 解析界面/CLI 的「每行一个」文本 |
| `safe_eval(expr, df)` | AST 白名单安全表达式求值 |

## 5. 导出

| 名字 | 用途 |
| --- | --- |
| `FORMULA_MODES` / `FORMULA_MODE_LABELS` | CSV 公式注入防护三模式及中文文案 |
| `NUMBER_MODES` / `NUMBER_MODE_LABELS` | XLSX 编号导出模式及中文文案 |
| `shield_csv_formulas(df, mode)` | 返回加防护前缀的副本 |
| `to_csv_bytes(df, neutralize_formulas=True)` | 导出 CSV 字节（UTF-8 BOM） |
| `to_xlsx_bytes(df, number_mode="text")` | 导出 XLSX 字节；超 Excel 上限抛中文 `TableError` |
| `numbers_where_possible(df)` | XLSX「尝试转数字」模式的转换 |
| `safe_stem` / `timestamped_name` / `unique_names` / `entry_name` / `build_zip` | 文件名清理、时间戳命名、ZIP 重名处理 |

## 6. 模板

| 名字 | 用途 |
| --- | --- |
| `TEMPLATE_TOOL` | 模板「工具」字段固定值 |
| `TEMPLATE_VERSION` | 旧「版本」字段值（保持 2，兼容既有模板） |
| `TEMPLATE_FORMAT_VERSION` / `LEGACY_TEMPLATE_VERSION` | 新 `template_version` 的当前值 1 / 旧模板 0 |
| `TemplateInfo` / `load_template(data)` | 模板元信息（工具、名称、保存时间、版本、规则、原始对象）与读取入口 |
| `build_template(rules, name="", now=None)` | 校验规则并生成模板对象；写入 `template_version: 1` |
| `dumps(template)` | 模板对象 → UTF-8 JSON 字节 |
| `loads(data)` | 读模板 → 规整后的规则字典（兼容旧模板与只有规则层的 JSON） |
| `normalize_rules(rules)` | 校验并规整规则 |
| `filter_for_columns(rules, columns)` | 剔除当前文件不存在的列，返回（规则, 中文说明） |
| `rules_to_rename_text` / `rules_to_computed_text` / `summarize_rules` | 规则 → 界面文本 / 一行摘要 |

模板格式版本规则见 `cleaning/templates.py` 与 README「规则模板」：

- 保存时写 `"template_version": 1`；
- 字段缺失 = 旧模板（版本 0），正常读入；
- 字段为大于 1 的整数或类型错误：中文 `TableError`，不崩溃、不猜测版本。

## 7. 批量

| 名字 | 用途 |
| --- | --- |
| `BatchJob(source, sheet, data)` | 一项待处理数据：文件名、工作表名（CSV 为 None）、原始字节 |
| `BatchItem` / `BatchReport` | 单项结果 / 批次结果（ZIP 字节、逐项结果、成功数） |
| `run_batch(jobs, options, *, export_format="csv", number_mode="text", formula_mode="quote", timeout=None, limits=None, progress=None)` | 纯函数批量处理：逐项读取→清洗→导出→打包；单项失败不拖垮整批 |

## 8. 编程式与 HTTP 入口（v2.3.0 新增）

| 名字 | 用途 |
| --- | --- |
| `CleanResult` | 编程式清洗结果：来源文件名、建议输出名、文件字节、前后行数、步骤、说明、报告文本 |
| `InspectResult` | 只检查结果：行列、列名、空值、重复行、可疑格式、读取说明、报告文本 |
| `clean_bytes(filename, data, options, ...)` | 对内存中的文件字节执行一次完整清洗并导出 |
| `inspect_bytes(filename, data, ...)` | 只检查不清洗，返回结构化结果与中文报告 |
| `render_report(result)` | 把 `CleanResult` / `InspectResult` 渲染成终端/Markdown 可读文本 |
| `options_from_rules(rules)` | 模板规则字典 → `CleanOptions` |

HTTP 入口不在 `__all__` 内，通过 `python -m api.http_api` 或 console script
`table-cleaner-api` 启动；接口说明见 `README.md` 的「API 雏形」章节。

## 9. HTTP API 雏形（无鉴权，仅本机/内网）

| 方法与路径 | 作用 |
| --- | --- |
| `POST /api/jobs` | 提交一次清洗；JSON 字段：`filename`、`content_base64`、可选 `template`（模板对象）或 `rules`（规则对象）、`export_format`、`formula_mode`、`number_mode` |
| `GET /api/jobs/{id}/result` | 取清洗后的结果文件 |
| `GET /api/jobs/{id}/report` | 取中文清洗报告与步骤/说明（JSON） |

- 默认只监听 `127.0.0.1:8765`，**没有鉴权**，不要直接暴露到公网。
- 任务只存在进程内存，最多保留 20 个（FIFO），服务重启即丢；不做队列/持久化。
- 所有错误返回中文 JSON：`{"错误": "..."}`；请求体上限默认 32 MB（`TABLE_CLEANER_API_MAX_BODY_MB`）。
- 正文读取默认 5 秒超时（`TABLE_CLEANER_API_READ_TIMEOUT_SECONDS`）；半截正文/超时返回中文 400，不会挂住线程。
