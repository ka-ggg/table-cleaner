# 版本变更记录（CHANGELOG）

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。
版本号规则：修 bug 升末位（2.0.0 → 2.0.1），加功能升中位，不兼容改动升首位。

## [2.3.0] — 2026-11-07

> 只变形态，不动清洗语义：把 v3 路线图「v2.2 过渡版」里 v2.2.0 未完成的 CLI、API 雏形、
> benchmark 基线、模板 `template_version`、limits 配置对象、batch 纯函数化补齐。
> 现有 214 项测试全部保留并通过，新增 92 项，总数 306。

### 新增

- **CLI 预览版**：新增 `table-cleaner` console script 与 `cleaning/cli.py`，含 `clean` / `inspect` /
  `template` / `batch` 四个子命令；退出码 0 成功、1 输入/数据错误、2 参数用法错误、3 内部错误；
  错误统一中文提示，不打印英文堆栈。参数兼容承诺自 v3.0 起算。
- **API 雏形**：新增 `cleaning/service.py` 编程式入口（`clean_bytes` / `inspect_bytes` /
  `options_from_rules` / `render_report`）与 `api/http_api.py` 标准库本地 HTTP 服务
  （`table-cleaner-api`），只做提交清洗、取结果、取报告三件事；无账号、权限、任务队列与持久化。
- **benchmark 套件**：新增 `benchmarks/`（固定随机种子生成脚本、标准流水线 worker、运行器、
  `BASELINE.md`），小 1 万 / 中 10 万 / 大 50 万三档，每项跑 3 次取中位数，记录端到端、读取、
  清洗、导出耗时与进程内存峰值；本版只建基线，不设提升目标。
- **模板格式版本**：模板保存时写入整数 `"template_version": 1`；新增 `load_template` /
  `TemplateInfo` / `TEMPLATE_FORMAT_VERSION` / `LEGACY_TEMPLATE_VERSION`；字段缺失按旧模板
  （版本 0）读入，更高版本给中文提示但不崩溃。
- **公共 API 清单**：新增 `API.md`，明确 `cleaning.__all__` 内公共名字、用途与入参出参；
  新增 `CleanResult` / `InspectResult` 等公共类型。
- **测试**：新增 `tests/test_v23_*.py` 共 92 项，覆盖 CLI 四子命令与坏文件退出码、HTTP API 三件事、
  limits 配置对象、template_version 兼容与逐单元格一致、batch 纯函数边界、公共 API 边界、
  benchmark 冒烟。

### 变更

- **`cleaning/limits.py`**：`Limits` 从只含 5 个字段的 NamedTuple 扩为统一配置对象，
  增加 `timeout_seconds` / `chunk_rows` / `chunk_threshold_rows` 与 `with_overrides()`、`from_env()`；
  前 5 个字段顺序不变，旧 `_replace()` 与 `current_limits()` 用法兼容。默认值与 v2.2.0 逐项一致：
  10 MB / 500,000 行 / 1,000 列 / 2,000,000 单元格 / XLSX 200 MB / 超时 60 秒；环境变量覆盖名不变。
- **`cleaning/batch.py`**：抽取 `_process_job` 纯函数，`run_batch` 只接收纯数据入参；
  行为与现有批量测试一致，不感知 Streamlit 会话。
- **`cleaning/templates.py`**：新增版本识别路径；旧「版本」字段（2）保持不动，新 `template_version`
  单独表示模板格式版本（1）。
- **`app.py`**：确认只通过 `cleaning/__init__.py` 的公共导出调用，不直接 import `cleaning.io` /
  `transform` / `export` 等内部模块；新增自动化边界测试防止回退。
- **版本号**：`pyproject.toml`、`cleaning.__version__`、README、USER_GUIDE 统一升到 **2.3.0**；
  `app.py` 页面版本读 `cleaning.__version__` 自动跟随。
- **`pyproject.toml`**：新增 console scripts `table-cleaner = "cleaning.cli:main"`、
  `table-cleaner-api = "api.http_api:main"`。
- **文档**：README 增加 v2.3.0 改动、CLI、API 雏形、benchmark 与已知限制；
  USER_GUIDE 增加 CLI 简要说明；新增 `API.md`。

### 修复

- **HTTP API 半截请求挂起**：`api/http_api.py` 的 `_read_payload` 原用阻塞式 `read(length)` 且无超时，客户端声明
  `Content-Length` 却不发够正文时 handler 线程会一直挂住、永远不回 400。现改为带读取超时的
  分块读取，正文不完整/超时返回中文 400「请求体不完整或读取超时」，并补「坏请求不阻塞后续
  正常请求」的并发回归用例。
- `CleanResult` 区分来源文件名（`source`）与建议输出文件名（`filename`），避免报告把建议输出名
  误标成输入文件。
- benchmark worker 读不到数据集时补中文错误（原会抛 `FileNotFoundError`）。
- 本版无语义修复项；未改任何清洗判定规则、默认值或导出行为。

### 测试与质量基线（2026-11-07 实跑）

- `python -m pytest -q -rs`：**306 passed，0 skipped**（全量跑两遍，结果一致；第二遍结果见交付报告）。
- `python -m pytest --collect-only -q`：**306 tests collected**。
- `ruff format --check .`：通过；`ruff check .`：`All checks passed!`。
- `mypy`（`cleaning/` + `api/`，非严格）：`Found 4 errors in 1 file (checked 14 source files)`，
  仍是 EX-001~EX-004 的存量 transform.py 错误，本版未新增。
- `coverage report --include='cleaning/*'`：**94%**（1595 statements / 99 missed），
  高于 v2.2.0 的 93% 基线；本版只公布，CI 不设覆盖率门禁。
- CLI 实跑：`clean`（模板 + 报告）、`inspect`（双表 XLSX）、`template --check`、
  `batch`（samples 目录）均退出 0；坏文件/坏模板/缺参数分别退出 1/1/2，只输出中文提示。
- API 实跑：`POST /api/jobs` 返回 201，`GET .../result` 返回 CSV，`GET .../report` 返回中文报告；
  结果与公共 API `clean_bytes` 逐字节一致；半截正文请求在读取超时后返回中文 400，不挂住线程。
- benchmark 首版基线（median）：小档端到端 **0.281 s** / 峰值 **133.2 MB**；
  中档 **2.408 s** / **212.6 MB**；大档 **11.826 s** / **532.7 MB**。
  环境：Python 3.12.3、pandas 3.1.1、numpy 2.5.4、openpyxl 3.1.5、Linux、4 核 / 7934 MB。

### 已知限制与未完成项

- **CLI 是预览版**：参数兼容承诺自 v3.0 起算，v2.3.0 期间可能调整；调整会记入本文件。
- **HTTP API 没有鉴权**：默认只监听 127.0.0.1，仅供本机/可信内网；任务只存内存、最多 20 个，
  服务重启即丢；不做账号、权限、队列、并发调度和结果持久化。不要直接暴露公网。
- **在线 Demo 未提供**：本版没有可用的托管环境，未部署、未编造链接；只能用本地 Streamlit / CLI / API。
- **真机/手机浏览器人工验收未执行**：容器读屏、截图与端口可达性限制，未写「通过」，
  也未用桌面端自动化测试替代；清单见 README/USER_GUIDE。
- **干净环境 `pip install .` 未完整执行**：本机无网络，构建隔离所需的 `setuptools` 无法从 PyPI 拉取；
  已在现有 `.venv` 用 `--no-build-isolation` 完成 editable 安装并验证 console script 可用，
  未把它写成「干净环境安装通过」。
- **Python 3.11 本地未实跑**：本机只有 3.12；CI 配置覆盖 3.11，本地实测记为「未执行」。
- **mypy 仍有 4 条存量错误**：未通过改逻辑或 `Any` 压掉，属 EX-001~EX-004 棘轮还债项。
- **coverage 只公布不设门禁**：本版实测 `cleaning/` 行覆盖率 **94%**（1595 statements / 99 missed），高于 v2.2.0 的 93% 基线；CI 仍不按覆盖率拦截。
- **pip-audit 未接入 CI**：规范列为目标，本版未安装、未加入 workflow。
- **benchmark 只有单机首版基线**：v2.0.1 / v2.2.0 没有 benchmark 套件，历史列无法补测，标「未执行」；
  本版不设 10% 回退门禁，跨机器只比相对值。
- **未 push**：tag `v2.3.0` 打在本地最终提交上，推送由需求方验收后执行。

## [2.2.0] — 2026-11-06

> 只变形态，不动清洗语义：把工程规范、打包、静态检查、类型检查、覆盖率测量、
> CI 矩阵、许可证与 PR 流程补齐。清洗行为、判定规则、公共 API 导出均未改变。

### 新增

- **`ENGINEERING_STANDARDS.md`**：常设《工程标准与细节要求》，14 节正文 + 40 条细节总清单；
  每条标准五要素齐全（编号/级别/条文/检查方法/违反后果），含豁免登记表、规范变更记录、
  棘轮还债节奏、6 类改动的边缘清单与 M1–M4 落地安排。
- **`pyproject.toml`**：`name = "table-cleaner"`、`version = "2.2.0"`、Python `>=3.11`；
  setuptools 构建配置使 `pip install .` 可安装 `cleaning` 包；同时写入 Ruff（format + check）、
  mypy（非严格）、coverage 配置。公共 API 仍以 `cleaning/__init__.py` 的 `__all__` 为唯一清单。
- **Ruff 0.17.4**：全仓 `ruff format` 与 `ruff check --fix`，只做格式化与导入排序，无语义变更；
  `ruff format --check .` 与 `ruff check .` 均通过，无 `per-file-ignores`。
- **mypy 2.4.1**：对 `cleaning/` 启用非严格检查（`ignore_missing_imports`、
  `follow_imports = "silent"` 等），基线错误 **4 条**，全部在 `cleaning/transform.py`，
  登记为棘轮还债项（未改逻辑换通过）。
- **coverage 7.17.2**：实测 `cleaning/` 行覆盖率 **93%**（1178→格式化后 1183 statements，83 missed），
  仅测量公布，本版不设门禁。
- **`.github/pull_request_template.md`**：按 ENGINEERING_STANDARDS 第 11 节写 7 组自检清单
  （改动归类、边缘清单、回归测试与旧代码失败验证、文档同步、benchmark 影响、豁免登记、CHANGELOG）。
- **`.github/workflows/tests.yml`**：测试矩阵扩到 Python **3.11 + 3.12**（测试 job 仍是门禁）；
  新增 Ruff 检查 job，`continue-on-error: true` 警告期运行，注释写明切换条件。
- **`LICENSE`**：MIT，`Copyright (c) 2026 ka-ggg`。
- `requirements-dev.txt` 补 `ruff==0.17.4`、`mypy==2.4.1`、`coverage==7.17.2`（本机实测可装版本）。

### 变更

- 现行版本统一升到 **2.2.0**：`pyproject.toml`、`cleaning/__init__.py` 的 `__version__`、
  页面标题下方版本（`app.py` 读 `cleaning.__version__`，自动跟随）、README 首屏与版本核对命令。
- README 项目结构、CI 说明同步新增文件与 Python 3.11/3.12 矩阵；
  `USER_GUIDE.md` 的现行版本号同步为 v2.2.0。历史文档中叙述旧版本行为的文字未改。
- `tests/test_v2_features.py` 与 `tests/test_regressions_v201.py` 各 1 处版本断言
  从 `2.0.1` 更新为 `2.2.0`；只改版本期望值，测试逻辑与数量不变（仍为 214 项）。

### 测试与质量基线（2026-11-06 实跑）

- `python -m pytest -q -rs`：**214 passed，0 skipped**（Ruff 格式化前后各跑一次，均全过）。
- `ruff format --check .`：通过；`ruff check .`：`All checks passed!`。
- `mypy`（`cleaning/`，非严格）：`Found 4 errors in 1 file (checked 10 source files)`。
- `coverage run -m pytest -q` + `coverage report --include='cleaning/*'`：**93%**。
- `pip install .`：在已有 `.venv` 中安装成功，元数据版本 `2.2.0`，`import cleaning` 正常。

### 已知限制与未完成项

- **覆盖率仅基线、未设门禁**：93% 只是实测公布值，CI 不按覆盖率拦截；门禁切换按规范第 14 节排期。
- **mypy 非严格**：仍有 4 条 transform.py 错误，未通过改逻辑或 `Any` 绕过；属棘轮还债项。
- **Ruff 仍处警告期**：CI 的 Ruff job `continue-on-error: true`，不阻断合并；切换条件已写入规范。
- **Python 3.11 矩阵未在本地实跑**：本机只有 3.12，3.11 由 CI 配置覆盖，本地实测记为「未执行」。
- **真机/手机浏览器人工验收未执行**：容器读屏、截图与端口可达性限制，详见 README 与 TEST_REPORT 的未执行项。
- **pip-audit 未接入 CI**：规范列为目标，本版未安装、未加入 workflow。
- **benchmark 基线未建立**：性能回退 10% 阈值只是目标值，本版未测、未设门禁。
- **CLI、引擎替换、模板 version 字段、Benchmark 套件**：不在本版范围（见任务简报「明确不做」）。
- 本次未执行 push；tag `v2.2.0` 打在本地最终提交上，推送由需求方验收后执行。

## [2.0.1] — 2026-10-20

> 只修 bug，不加功能。来自一次从零开始的商业化交付审查
> （明细见 `BUGS_AND_FIXES.md`，测试证据见 `TEST_REPORT.md`）。

### 修复（会改坏数据 / 卡死程序）

- **计算列指数爆炸会卡死页面**：`9**9**9`、`10**100000000` 这类常量指数运算在求值前就被拦截，
  给中文提示「结果太大…已停止计算」。修复前会真的去算 9 的 3.8 亿次方（约 1.6 GB 整数），
  页面卡死、内存飙升。
- **「转数字」把长编号悄悄改值**：超过 15 位有效数字的整数在 int64 里会被截成
  `9223372036854775807`、在 float64 里会丢尾数。现在这类格子保持文本，
  并在页面提示「N 个单元格没转成功」；XLSX「尝试转数字」模式同样保持文本。
  `00123 → 123`、`1,000 → 1000` 的预期效果不变。
- **计算列整数溢出静默回绕**：`4000000000*4000000000` 修复前得到
  `-2446744073709551616`（且无提示）。现在按 float64（与 Excel 一致）计算，
  溢出留空并提示「结果是无穷大（除以 0 或数字太大）」。
- **`round()` 不再能卡住页面**（复审补充）：`round(1, -10000000)` 修复前要 18.3 秒
  （C 层算 10 的 1000 万次方），现在保留位数限制 ±30，立刻中文提示。
- **纯整数长编号列**（复审补充，BUG-002 的补丁不完整）：整列都是 18 位整数时，
  pandas 给出精确的 int64，守卫失效 → 导出的 XLSX 里两个不同身份证号并成同一个值。
  现在守卫以 float64 精度为准，并补了专门的回归测试。

### 修复（用户可见故障）

- **日期列混时区整页崩溃**：同一列既有带时区又有不带时区的日期时，修复前整页变成
  `ValueError: Mixed timezones detected…` 的英文堆栈，导出按钮全部消失。
  现在整列保留原文 + 中文提示，页面照常可用；同一时区的日期按「当地时间的日期」处理
  （Excel 不支持时区，时区标记不保留）。
- **模板「列顺序」静默失效**：模板里把「改名后的列」「计算列」排在最前面时，
  套用后排序被丢掉，还错误提示「当前文件没有这一列」。已修复（过滤时把新列名算进可用列）。
- **数据区合并单元格没有提示**：合并区域只有左上角有值，修复前勾「删除空值行」会把
  合并区域里的行当空行删掉。现在读取时给出提示（数量 + 影响），表头合并不会误报。
- **计算列引用日期/布尔列**：原来抛原生英文异常，现在中文提示「列「X」不是数字…」。

### 修复（复审补充的数据完整性/兼容性）

- **`-3%` 被当成公式**：导出变成 `'-3%`，工具自身往返一次就改了值。现在带 `%` 的纯数字不算公式。
- **XLSX 里以文本保存的编号**：`00123` 不再被读成 `123`（数字/日期列仍按正常类型读），
  自己导出的文本编号文件再传回来也不会丢前导零。

### 修复（测试与文档）

- **干净环境（无 lxml）4 项测试失败**：测试用精确正则匹配 `<dimension ref="…"/>`，
  openpyxl 在无 lxml 时写成 `<dimension ref="…" />`（多一个空格）导致断言失败。
  也就是说 CI 在干净 runner 上会红。已改成两种序列化都认。
- 文档：重复列名的实际命名（`金额.1`，不是 `_2`）、百分比/货币格式不保留、
  XLSX 长编号精度限制、大 XLSX 每次操作重读、时区处理——写进 README「已知限制」与 `FAQ.md`。
- 新增 `AUDIT_REPORT.md`、`BUGS_AND_FIXES.md`、`TEST_REPORT.md`、`USER_GUIDE.md`、
  `FAQ.md`、`SELLER_SUPPORT_GUIDE.md`、`RELEASE_CHECKLIST.md`、`THIRD_PARTY_LICENSES.md`。
- 第二轮独立复审又发现 4 个问题（BUG-009~012），已全部修复并补测试；
  已知限制补充「计算列大整数导出 XLSX 会按 15 位精度显示」「极老日期的 pandas 版本差异」。

### 测试

- 从 **194 项增加到 214 项**：新增 `tests/test_regressions_v201.py`（18 项逻辑回归）
  + `tests/test_app_ui_v2.py` 加 2 项界面回归。
- 每项回归测试都先在**修复前的代码**上跑出失败，再在修复后通过（见 `TEST_REPORT.md`）。
- 三套环境验证：开发机、全新 venv（无 lxml）、最低允许版本（streamlit 1.50 + pandas 2.2）。

### 升级注意

- 旧版导出的、包含 16 位以上编号的文件**建议重新导出并核对**（旧版可能已经改值）。
- 计算列结果现在按浮点计算再显示回整数，界面观感不变；极端大数会显示为空 + 提示，
  不再出现负数/怪数。
- 版本号变成 `2.0.1`（页面标题下、`cleaning/__init__.py`）。

## [2.0.0] — 2026-10-20

- 批量处理（多工作表 / 多文件 → 一个 ZIP）、清洗规则模板（JSON）、指定表头行、
  进阶列操作（改名/删列/排序/转数字/转日期/安全计算列）、三种 CSV 公式防护模式、
  XLSX 编号文本/数字开关、可配置上限与协作式超时、分块清洗。
- `cleaning.py` 拆成 `cleaning/` 包（limits/errors/xlsx_scan/io/rules/transform/export/templates/batch）。
- 测试 107 → 194。

## 更早版本

v1.5.0 → v1.8.0 的变更记录保留在 `README.md` 的「版本历史」一节（多工作表、按列去重、
超限表跳过、CSV 分隔符识别、压缩炸弹防护、XLSX 预检加固等）。
