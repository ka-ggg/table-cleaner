# 测试报告（TEST_REPORT）

- 报告日期：2026-10-20
- 被测版本：**v2.0.1**（修复前基线 v2.0.0）
- 测试环境：Android 16 容器（aarch64，vivo V2403A，16 GB 内存），Python 3.12.3
- 说明：本报告里的数字全部来自实际命令输出；没跑的一律写「未执行」，不编造。

---

## 1. 环境清单

| 环境 | 怎么来的 | 关键版本 |
| --- | --- | --- |
| **A. 开发机** | 容器内已有环境 | Python 3.12.3 / streamlit 1.66.0 / pandas 3.0.6 / openpyxl 3.1.5 / pytest 9.1.1 / numpy 2.5.3 / **lxml 6.1.3** |
| **B. 干净 venv** | `python3 -m venv /tmp/audit/venv-clean` + `pip install -r requirements-dev.txt` | 同上，但 **numpy 2.5.4、没有 lxml**（干净环境的真实形态） |
| **C. 最低允许版本** | 隔离目录 `pip install --target` 装 `streamlit==1.50.0`、`pandas==2.2.3`、`openpyxl==3.1.5`、`pytest>=8,<10` | streamlit 1.50.0 / pandas 2.2.3 / openpyxl 3.1.5 / pytest 9.1.1 |

> 为什么要有 B：`requirements-dev.txt` 不装 lxml，CI 就是这种环境。
> 本轮发现 4 项测试在 B 环境失败（**BUG-007**），修复后才三套全绿——只测 A 会漏掉。

## 2. 全量测试结果

命令：

```bash
cd table-cleaner
python3 -m pytest -q -rs          # 或干净 venv 里的 python
python3 -m pytest --collect-only -q | tail -1
```

| 环境 | 收集 | 结果 | 跳过 |
| --- | --- | --- | --- |
| A 开发机（修复前基线） | 194 | **194 passed**（68.31 s） | 0 |
| A 开发机（修复后，最终） | 214 | **214 passed**（85.09 s） | 0 |
| B 干净 venv（修复后，最终） | 214 | **214 passed**（77.44 s） | 0 |
| C 最低版本（修复后，最终） | 214 | **182 passed, 2 files skipped**（13.70 s） | 2 个界面测试文件（32 项）：Streamlit 1.50 的 `AppTest` 不支持 `file_uploader`。**应用本身可运行**，逻辑测试照跑 |

补充证据（环境 C）：单独用 AppTest 在 streamlit 1.50.0 下跑 `app.py` 初始页面，
`at.exception` 为空、标题「表格清洗小工具」与空状态提示正常显示；
1.50 里文件上传控件是 `UnknownElement`（没有 `.upload()`），所以上传之后的流程无法在 1.50 下自动测，
这是 AppTest 的限制、不是应用报错。另核对了 `st.dataframe/download_button/progress/toggle/multiselect/radio/text_area`
在 1.50 里都已有 `width` 等本项目用到的参数。

### 2.1 各文件数量

| 文件 | 收集数 | 说明 |
| --- | --- | --- |
| `tests/test_app_logic.py` | 89 | v1.x 逻辑（原样保留） |
| `tests/test_v2_features.py` | 75 | v2.0.0 新功能 |
| `tests/test_regressions_v201.py` | 18 | **本轮新增**：11 个代码缺陷的逻辑回归（第一轮 7 个 + 复审补充 4 个；文档问题 BUG-008 无代码可测） |
| `tests/test_app_ui.py` | 18 | v1.x 端到端界面（AppTest） |
| `tests/test_app_ui_v2.py` | 14 | 12 项 v2.0.0 + **2 项本轮新增界面回归** |
| 合计 | **214** | 182 逻辑 + 32 界面 |

## 3. 回归测试有效性验证（关键证据）

把 `tests/test_regressions_v201.py` 原样拷到**修复前的代码快照**（`/tmp/audit/pristine-snapshot`）上跑：

第一轮（BUG-001~008）：

```
$ python3 -m pytest tests/test_regressions_v201.py -q -k "not exponent and not freeze and not version"
8 failed, 2 passed, 4 deselected in 6.28s
```

第二轮复审补充的 4 项（BUG-009~012）在**第一轮修复后的代码**上跑：

```
$ python3 -m pytest tests/test_regressions_v201.py -q -k "pure_integer or huge_ndigits or negative_percentage or text_number_cells"
4 failed, 14 deselected in 16.87s
```

四项分别是：纯整数长编号列仍被改值、`round(1,-10000000)` 算 18 秒、`-3%` 被加前缀、
XLSX 文本 `00123` 读成 `123`——全部在修复后通过。

失败项（= 这些测试真的能抓到旧 bug）：

| 测试 | 在旧代码上失败的原因 |
| --- | --- |
| `test_convert_to_number_never_changes_long_ids` | 长编号被改成 int64 上限/丢尾数 |
| `test_number_mode_export_keeps_long_ids_as_text` | XLSX 写出 `1.234567890123457e+23` |
| `test_numeric_series_is_exact_for_15_digit_numbers` | `1E400` 转成 `inf`（导出会写出 `inf`） |
| `test_computed_column_does_not_silently_wrap_on_integer_overflow` | `4e9*4e9` 回绕成负数 |
| `test_mixed_timezone_dates_keep_original_text_instead_of_crashing` | pandas 抛 `ValueError`（3.0）/ `AttributeError`（2.2） |
| `test_single_timezone_dates_drop_tz_but_keep_wall_clock` | 带时区的日期让 XLSX 导出失败 |
| `test_template_keeps_column_order_for_renamed_and_computed_columns` | 列顺序被丢 + 假提示 |
| `test_merged_data_cells_are_reported_before_deleting_empty_rows` | 没有任何提示，测试拿不到说明 |

另有 4 项被 deselect 的是「指数爆炸」相关（旧代码会真去算、挂死测试进程），
其挂死现象单独验证：

```
$ timeout 8 python3 -c "import pandas as pd; from cleaning.transform import safe_eval; safe_eval('9**9**9', pd.DataFrame({'a':[1]}))"
exit=124          # 8 秒仍未返回，说明旧代码会卡死
$ 修复后：同一条命令 0.000 s 返回中文 TableError
```

## 4. 独立探针（不依赖项目测试，修复前做的对抗性检查）

脚本在 `/tmp/audit/`（探针不作为交付物，结论已固化进 `tests/`）：

| 探针 | 覆盖 | 结果 |
| --- | --- | --- |
| `probe_data.py` | CSV：CRLF、引号内换行、emoji/中文、GBK、BOM、制表符/竖线、重复/空列名、坏引号、表头行 2/越界、前导零、`1E5`、长数字 | 读取层无数据损坏 |
| `probe_xlsx.py` | XLSX：合并表头+数据区、隐藏工作表、公式（无缓存）、`00000` 数字格式、布尔/日期/时间、只有表头、单行单列、特殊表名、大整数单元格、空白/空格 | 除「合并单元格无提示」外均符合预期 |
| `probe_export.py` / `probe_export2.py` | 计算列 inf/复数/超大数、CSV 含 inf、公式注入 9 种前缀 × 3 模式、列名注入、XLSX `=` 单元格类型、编号模式、CSV 往返（逗号/引号/换行/emoji） | 注入防护有效；长编号与溢出有问题（已修） |
| `probe_misc.py` | 模板往返/过滤/坏字段、文件名路径穿越、ZIP 重名、批量单项失败、清洗顺序、空表清洗、`parse_*` | 除模板列顺序外无问题（已修） |
| `probe_formats.py` | 只有 CR 的 CSV、空引号、引号内逗号、BOM、全空行、1 MB 单元格、XLSX 尾部空行/空列 | 均符合预期 |
| `probe_ui.py` / `probe_ui2.py` | AppTest 下的混合时区日期崩溃、合并单元格提示 | 修复前崩溃、修复后中文提示 |

## 5. 资源与性能（实测，环境 A）

| 场景 | 耗时 | 峰值内存 (`ru_maxrss`) |
| --- | --- | --- |
| 9.8 MB CSV，30 万行 × 4 列：`load_table` | 0.68 s | 212 MB |
| 同一文件 `clean_table`（全行去重 + 删空值行） | 0.17 s（300000 → 294000 行） | 231 MB |
| 同一文件 `to_csv_bytes` | 1.27 s（9.7 MB） | 323 MB |
| 同一文件 `to_xlsx_bytes`（30 万行） | **29.3 s** | **782 MB** |
| XLSX 20 万行 × 4 列：解压体积检查 | 0.004 s | — |
| 同上：流式预检 `pick_xlsx_sheet` | 0.67 s | — |
| 同上：新增的合并单元格扫描 | 0.10 s | — |
| 同上：完整 `load_table`（openpyxl 解析） | 8.38 s | 782 MB（累计峰值） |
| 超行数拦截（上限 1000，文件 30 万行） | 0.03 s（预检早停，不解析） | — |
| 协作式超时（`timeout=0.0001`） | 0.07 s 抛中文 `TableError` | — |
| AppTest 界面全量（32 项） | 约 36 s | — |

> 30 万行 XLSX 导出 29 秒、782 MB 是**手机端瓶颈**，README/FAQ 已建议大表用 CSV；
> 部署容器内存建议 ≥1 GB。

## 6. 独立复审记录（第二轮，新上下文）

由另起的新上下文审查代理执行，**只读项目文件、不复用第一轮结论**。它自己重跑了三套环境、
做了变异测试（删掉守卫/前缀后相关测试立即失败，证明断言有效）、并做了对抗性探测。

### 6.1 它确认的（与第一轮结论一致）

- 三套环境测试结果与本文档记录一致（A：214 passed；B 干净 venv：全绿；C 最低版本：182 + 2 skipped）；
- 发布包解压后测试通过、代码与仓库一致；
- 公式注入 12 种危险前缀 × 3 模式未发现漏网；合并单元格计数在 64 KB 块边界上正确；
  时区日期在 pandas 3.0 与 2.2 下行为一致；数据完整性抽查除下面 4 项外无问题。

### 6.2 它新发现的 4 个问题（已全部修复）

| 编号 | 等级 | 问题 | 修复后验证 |
| --- | --- | --- | --- |
| BUG-010 | P1 | 纯整数长编号列：第一轮精度守卫失效，XLSX 导出把两个身份证号并成一个值 | 回归测试通过；4 个长编号保持文本 |
| BUG-009 | P1 | `round(1, -10000000)` 绕过指数守卫，算 18.3 秒 | 现在 0.000 s 中文报错；`round(x,2)` 正常 |
| BUG-011 | P2 | `-3%` 被当公式加前缀，往返改值 | 现在不加前缀；`-3%+1` 仍算可疑 |
| BUG-012 | P2 | XLSX 文本编号 `00123` 读成 `123` | 现在保留文本；数字/日期列行为不变 |

另外两条记入已知限制、未改代码：计算列算出的超大整数导出 XLSX 会被 Excel 按 15 位精度显示；
极老日期（如 1677 年）在不同 pandas 版本下解析结果可能不同。

### 6.3 它的独立判断

> 「P1 是静默改数据且正好落在核心卖点上，交付一个『文档承诺已修、实际仍会改数据』的版本
> 售后风险高于收益；先修 P1、补 LICENSE，再考虑 B 级交付。」
> 本轮已按其建议修复 P1 与另外 3 项，LICENSE 仍列为发布阻塞项。

### 6.4 本轮修复后的复测

见第 2 节最终数字（修复后 A/B/C 三套环境全部重跑）。

## 7. 安全与依赖扫描

| 项 | 方法 | 结果 |
| --- | --- | --- |
| 网络外发 | `grep` 源码 `requests/urllib/socket/http/open(` | 只有 `archive.open(part)`（读内存 zip），无网络调用 |
| 页面外部资源 | `curl http://127.0.0.1:8501/ \| grep -oE 'https?://…'` | 只有 Apache 许可证注释里的 URL |
| 用量统计 | `streamlit config show` | `gatherUsageStats = false` |
| 文件写入 | `grep open(...,'w')/tempfile` | 无 |
| 硬编码路径 | `grep /root /tmp /home C:\\` | 无 |
| 运行依赖漏洞 | `pip-audit`（干净 venv） | streamlit/pandas/openpyxl 及其依赖 **0 条**；仅 venv 自带 `pip 24.0` 有升级项（pip 不随产品交付） |
| 测试告警 | `pytest -q`（pandas 2.2 环境） | 2 条 `FutureWarning`：混合时区解析（测试故意触发，pandas 3 会变成错误，代码已处理两种行为）。第一轮出现过的 `replace` 向下转型告警已通过改用 `mask` 消除 |
| 许可证 | 安装元数据 | 无 GPL/AGPL 依赖（见 `THIRD_PARTY_LICENSES.md`） |
| 系统环境顺带扫描 | `pip-audit --local` | 发现 `aiohttp 3.14.3`、`mcp 1.29.0` 有已知漏洞——这两个是 **DSH 宿主环境**的包，不属于本产品，也不随产品交付 |

## 7.1 客户视角安装验证（发布包）

模拟客户拿到发布包后的完整流程（全新目录 + 全新 venv）：

```bash
cd /tmp/audit/customer
python3 -c "import zipfile; zipfile.ZipFile('/root/工作室/table-cleaner-v2.0.1.zip').extractall('.')"
cd table-cleaner && python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m streamlit run app.py --server.headless true --server.port 8601
```

实际结果：

| 步骤 | 结果 |
| --- | --- |
| 从 zip 解压 | 34 个文件，无缓存/临时文件 |
| `pip install -r requirements.txt` | 成功（streamlit 1.66.0 / pandas 3.0.6 / openpyxl 3.1.5） |
| 启动 | 成功，`/_stcore/health` 返回 **200**，页面 HTML 正常返回 |
| 说明：浏览器标签页的静态 `<title>` 是 `Streamlit`，应用标题由 `st.set_page_config` 在页面加载后设置 | 不是缺陷，但首次加载前标签页会显示 Streamlit |

## 8. 未执行 / 无法执行

| 项 | 状态 | 原因 |
| --- | --- | --- |
| 真实浏览器人工点击（电脑） | **未执行** | 容器内无浏览器；AppTest 覆盖了点击逻辑但不是真实渲染 |
| 手机浏览器人工点击 | **未执行** | 读屏/截图 `SCREEN_TARGET_UNAVAILABLE`、虚拟屏 `VSCREEN_NOT_RUNNING`；`/app/open` 打开 `127.0.0.1:8501` 后容器请求日志无手机请求（端口不可达） |
| Windows / macOS 运行 | **未执行** | 环境不允许 |
| 多用户并发 | **未执行** | 未做并发压测 |
| 打包 exe/app | **未执行** | 环境不允许 |
| 真实客户数据 | **未执行** | 涉及隐私，不应在审查中接触 |

## 9. 结论

- 修复前基线 194 项通过，但存在 3 个 P0（数据损坏/卡死）与 4 个 P1；
- 独立复审又在第一轮修复上找到 2 个 P1 + 2 个 P2，已修复；
- 修复后 214 项在三套环境全部通过（最低版本环境有 2 个界面文件因 AppTest 限制跳过，已说明）；
- 每项修复都有「在旧代码上失败、在新代码上通过」的回归测试；
- 干净 venv 安装与测试可复现，依赖无已知漏洞；
- 未完成项集中在「真实浏览器/真机人工验收」「跨系统」「并发」，这些直接影响能否按 B 级交付。
