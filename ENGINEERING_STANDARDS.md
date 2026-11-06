# table-cleaner 工程标准与细节要求

| 项目 | 内容 |
| --- | --- |
| 文档性质 | 常设规范，不是版本规划；长期有效，直到本文升版替换 |
| 规范版本 | 1.0.0 |
| 适用仓库 | `ka-ggg/table-cleaner`（本目录 `/home/hatch/workspace/table-cleaner-work`） |
| 生效版本 | 自 v2.1.0 起生效；首个实际执行版本为 v2.2.0（编号冲突见第 0.1 节） |
| 核查与制定日期 | 2026-11-06 |
| 正文章节 | 14 节（第 1–14 节） |
| 细节总清单 | 52 条（第 13 节） |
| 标准条文 | 88 条（门禁 22 / 要求 62 / 建议 4） |
| 数字口径 | 每个量化指标均标注【现行值】或【目标值】；【目标值】不是已实现 |

## 目录

- [0 文档信息、关系与冲突裁决](#0-文档信息关系与冲突裁决)
- [1 总则与分级、豁免机制](#1-总则与分级豁免机制)
- [2 棘轮原则](#2-棘轮原则)
- [3 代码标准](#3-代码标准)
- [4 类型标准](#4-类型标准)
- [5 架构与分层](#5-架构与分层)
- [6 正确性细节要求](#6-正确性细节要求)
- [7 测试标准](#7-测试标准)
- [8 性能标准](#8-性能标准)
- [9 安全与隐私](#9-安全与隐私)
- [10 文档标准](#10-文档标准)
- [11 Git 与评审](#11-git-与评审)
- [12 发布标准](#12-发布标准)
- [13 细节总清单](#13-细节总清单)
- [14 落地安排](#14-落地安排)
- [附录 A 豁免登记表模板与在册清单](#附录-a-豁免登记表模板与在册清单)
- [附录 B 规范变更记录表](#附录-b-规范变更记录表)
- [附录 C 核查记录与实测基线](#附录-c-核查记录与实测基线)

## 0 文档信息、关系与冲突裁决

### 0.1 与路线图、v2.1.0 规划的关系

本文是常设规范，管“什么样的改动算合格”。《table-cleaner v3 升级路线图》管方向（v3.0 引擎版、v3.1 算法版、v3.2 工程版）；《table-cleaner v2.1.0 版本规划》管单个版本的落地拆解（P0/P1、M1–M4、CLI 与 benchmark）。本文不替代这两份文档，只给它们约束质量底线。

冲突裁决按以下顺序执行，均以本文为准：

| 冲突点 | 裁决 |
| --- | --- |
| 本文与《v2.1.0 版本规划》第 8 节发布门禁不一致 | 以本文为准。规划第 8 节允许的“未完成但明示即可发布”，不能放宽本文的测试、文档、版本一致性门禁 |
| 本文与《v3 升级路线图》的阶段划分不一致 | 方向以路线图为准，质量门禁以本文为准；路线图不得降低本文的现行门禁 |
| 规范写“自 v2.1.0 起生效”，但本仓库本次发布号是 v2.2.0 | 发布号以本次任务简报为准：v2.2.0 是首个执行本文的版本，v2.1.0 未发布、不追认；本文的“自 v2.1.0 起生效”只表示生效基线，不表示存在 v2.1.0 发版记录 |
| 《v2.1.0 版本规划》把路线图的 v2.2 过渡版改号为 v2.1.0，本次又发布 v2.2.0 | 编号冲突不在本文裁决范围；本文只按实际发布号 v2.2.0 核查。v2.2.0 只完成“打包 + 工程工具链 + 呈现补齐”子集，CLI、模板 version 字段、benchmark 套件仍按规划留给后续版本，未完成项在 CHANGELOG 明示 |
| 要求书写于 v2.0.1 时点，称“Ruff/mypy/coverage 当前不在仓库、由 v2.1.0 的 pyproject.toml 引入” | 该表述描述的是起草时点，不是 v2.2.0 发布时点。v2.2.0 已用 `pyproject.toml` 实际引入 Ruff、mypy、coverage；本文以实测配置为准。仍未引入的 `pip-audit`、benchmark 套件按“当前不在仓库、目标版本引入”表述，不冒充已存在 |
| 要求书称“本任务只写文档、不改代码”，本次任务简报要求交付代码与工具链 | 交付范围以任务简报为准；本文仍是标准条文，不能因为本次改了工具链就放宽条文 |

### 0.2 现状基线（2026-11-06 实测）

标准从真实起点出发。下表左边是规范起草时的 v2.0.1 状态，右边是本次 Ruff 格式化后、v2.2.0 发布前的实际状态。所有数字均来自附录 C 的命令输出。

| 项目 | v2.0.1（Ruff 前）【现行值】 | v2.2.0 发布前（Ruff 后）【现行值】 |
| --- | --- | --- |
| `app.py` 行数 | 855 | 810 |
| `cleaning/` 10 个文件总行数 | 2462 | 2352 |
| `cleaning/` 除 `__init__.py` 外 9 个模块行数 | 2249 | 2128 |
| 自动化测试 | 214 项 = 182 逻辑 + 32 界面 | 214 项 = 182 逻辑 + 32 界面 |
| 测试文件分布 | 89 / 75 / 18 / 18 / 14 | 89 / 75 / 18 / 18 / 14 |
| CI | 单 job，ubuntu + Python 3.12 | 测试矩阵 3.11 + 3.12；另 1 个 Ruff 警告 job |
| Ruff / mypy / coverage 配置 | 0 | 3 项均写入 `pyproject.toml` |
| Ruff 检查结果 | 无此检查 | `ruff format --check .` 通过；`ruff check .` 全部通过，0 条 `per-file-ignores` |
| mypy 基线错误 | 无此检查 | 4 条，均在 `cleaning/transform.py` |
| `cleaning/` 行覆盖率 | 未测量 | 93%（1183 statements，83 missed） |
| `pyproject.toml` | 无 | 有，`name = "table-cleaner"`、`version = "2.2.0"` |
| 提交历史 | 1 个提交（`0affbff`） | 交付前 1 个提交；本次按小步提交拆分 |
| PR 模板 / LICENSE | 无 / 无 | 有 / MIT |

`cleaning/limits.py` 的现行上限（全部为【现行值】，默认值，可由环境变量覆盖）：

| 项目 | 现行值 | 定义位置 |
| --- | --- | --- |
| 上传文件大小 | 10 MB（`10 * 1024 * 1024` 字节） | `cleaning/limits.py` `MAX_UPLOAD_BYTES` |
| 行数 | 500,000 | `MAX_ROWS` |
| 列数 | 1,000 | `MAX_COLS` |
| 单元格总数 | 2,000,000 | `MAX_CELLS` |
| XLSX 解压后总大小 | 200 MB | `MAX_XLSX_UNCOMPRESSED_BYTES` |
| 分块阈值 / 块大小 | 100,000 行 / 50,000 行 | `CHUNK_THRESHOLD_ROWS` / `CHUNK_ROWS` |
| 默认硬超时 | 60 秒（0 = 不限） | `DEFAULT_TIMEOUT_SECONDS` |

### 0.3 本文的写法约定

- 每条标准一行，固定五列：编号、级别、条文、检查方法、违反后果。缺任何一列，该条视为未完成。
- 条文用命令式短句，先给判据，再给解释；解释不超过 3 句。
- “门禁”只给能自动检查的条文；“要求”给人工按清单核对的条文；“建议”不阻断。
- 数字后的【现行值】表示 2026-11-06 实测或当前配置；【目标值】表示尚未达成、必须写测量方法与达标判定。

## 1 总则与分级、豁免机制

### 1.1 级别定义（写死，不得改写）

| 级别 | 定义 | 不达标的后果 |
| --- | --- | --- |
| 门禁 | 可由 CI 命令、脚本退出码或测试断言自动检查 | CI 红了禁止合并与发版；没有例外通道，只有按本节登记的豁免 |
| 要求 | 无法完全自动判定，由人工评审按清单逐项核对并留记录 | 评审记录缺项即视为评审未完成，PR 不得合并 |
| 建议 | 推荐做法，不强制 | 不阻断合并；评审可指出，作者回应一句理由即可 |

不能自动检查的条文禁止标为门禁。判据只有一条：说得出具体命令、脚本或测试断言，并能给出非零退出码；说不出的，只能标“要求”或“建议”。

### 1.2 标准条文

| 编号 | 级别 | 条文 | 检查方法 | 违反后果 |
| --- | --- | --- | --- | --- |
| GOV-01 | 要求 | 每条标准必须写全编号、级别、条文、检查方法、违反后果五要素 | 评审抽查任意 10 条；缺一项即失败 | 该条视为未完成，文档退回重写 |
| GOV-02 | 要求 | 门禁级条文必须附可执行检查命令或测试断言 | 逐条运行“检查方法”列的命令；命令不存在或永远返回 0 即失败 | 降级为“要求”，并在规范变更记录写明 |
| GOV-03 | 要求 | 豁免必须登记条目编号、理由、批准人、到期版本四项 | 对照附录 A 在册清单逐项核对；缺项即失败 | 该豁免无效，按违规处理 |
| GOV-04 | 要求 | 豁免到期自动失效，禁止永久豁免 | 发版前用到期版本与本次版本比对；到期条目仍在册即失败 | 阻断发版，先还债或重新评审续期 |
| GOV-05 | 要求 | 每个版本豁免清单净减少 | 对比上一版与本次在册条数；本次不少于上次即失败 | 阻断发版，新增豁免必须同时还掉更多旧豁免 |
| GOV-06 | 要求 | 规范升版必须记录改了哪条、为什么改、级别升或降 | 检查附录 B 是否有对应行 | 规范变更不生效，按旧版执行 |
| GOV-07 | 要求 | 同一事实只设一个权威出处，其他文档只引用不复制数字 | 检索重复数字，抽查 README、CHANGELOG、TEST_REPORT 是否各自维护同一组数字 | 记一条文档缺陷，限期改为引用 |
| GOV-08 | 建议 | 标准条文优先引用本仓库文件、模块、命令，不写无法落点的通用套话 | 评审抽查条文是否指向具体路径或命令 | 评审指出，作者补落点或删除该条 |

## 2 棘轮原则

存量代码不搞一次性全量整改。新标准对新增和改动代码立即全严；存量不达标项登记豁免，逐版还债。棘轮只进不退：豁免条数每版必须净减少，标准级别不得悄悄降级。

### 2.1 标准条文

| 编号 | 级别 | 条文 | 检查方法 | 违反后果 |
| --- | --- | --- | --- | --- |
| RAT-01 | 要求 | 新增代码和被改动代码按本文全部标准执行，无过渡期 | CI 跑 Ruff 与 pytest；评审对本次 diff 核对 mypy 与其余条文 | 未达标代码评审打回 |
| RAT-02 | 要求 | 存量不达标项在本文生效时一次性盘点并全部登记豁免，逐项写到期版本 | 对照附录 A 与代码实际状态；有未登记存量即失败 | 按违规处理，补登记后才能发版 |
| RAT-03 | 要求 | 每个版本还掉的豁免不少于“存量 20% 与 3 条取较高者”【目标值】 | 发版时统计在册条数变化；未达标必须在 CHANGELOG 写明原因 | 不阻断发版，但必须在发版说明标注未达标 |
| RAT-04 | 要求 | 新增豁免条数不得大于本次还掉的旧豁免条数 | 发版前比对附录 A 的新增与删除记录 | 阻断发版 |
| RAT-05 | 要求 | 还债不得靠删测试、删功能、放宽断言或降标准完成 | 对比上一版测试收集数与断言；减少即失败 | 阻断发版，恢复测试或功能 |
| RAT-06 | 要求 | 标准收紧只在次版本或大版本节点生效，放宽必须写明理由并过评审 | 检查附录 B 的生效版本与理由 | 变更不生效 |
| RAT-07 | 要求 | 每项新检查先以警告模式进 CI 跑满一个里程碑，再切门禁；切换条件写进第 14 节 | 对照第 14 节切换条件与 CI 配置 | 无预告直接变红视为流程违规 |

### 2.2 从警告切换为门禁的触发条件

| 检查 | 当前状态【现行值】 | 切换条件【目标值】 |
| --- | --- | --- |
| Ruff | CI 警告 job，`continue-on-error: true` | 该 job 连续 2 个发布版本全绿，且 `per-file-ignores` 条数为 0；满足后删除 `continue-on-error` |
| mypy | 非严格，4 条错误 | `cleaning/` 错误数连续 2 个版本为 0；满足后切 `disallow_untyped_defs = true` |
| coverage | 仅测量，93% | 基线已公布（本文附录 C）；M4 起设门禁，`cleaning/` 不低于 90% 且不低于已公布基线 |
| pip-audit | 未引入 | 连续 1 个版本无 high/critical 漏洞后进 CI 门禁 |

## 3 代码标准

### 3.1 标准条文

| 编号 | 级别 | 条文 | 检查方法 | 违反后果 |
| --- | --- | --- | --- | --- |
| CODE-01 | 门禁 | 格式化与 lint 只用 Ruff 一套工具，配置写在 `pyproject.toml` 的 `[tool.ruff]`，禁止命令行临时拼参数 | `grep -n "\[tool.ruff" pyproject.toml`；CI 执行 `ruff check .` | CI 红，禁止合并 |
| CODE-02 | 门禁 | 提交前 `ruff format --check .` 与 `ruff check .` 必须全过 | CI 的 `ruff` job 两条命令退出码均为 0 | CI 红，禁止合并 |
| CODE-03 | 要求 | 模块、函数、变量用 snake_case；类用 PascalCase；常量用全大写下划线；布尔值用 `is_`、`has_`、`should_` 前缀 | 评审抽查 `cleaning/`、`app.py` 新增符号命名 | 评审打回改名 |
| CODE-04 | 门禁 | 导入按标准库、第三方、本包三组排序；禁止 `import *`；`cleaning/__init__.py` 的显式导出除外且必须写全名单 | `ruff check .` 的 I001 规则；`grep -rn "import \*" app.py cleaning` 应为空 | CI 红，禁止合并 |
| CODE-05 | 要求 | 单个函数不超过 60 行（不含 docstring 与空行）；圈复杂度不超过 10；嵌套不超过 4 层【目标值】 | 用 `ruff check --select C901` 与人工计数；超限新增函数先拆 | 新增代码超限必须拆函数，拆不了登记豁免 |
| CODE-06 | 门禁 | 禁止裸 `except:`；禁止 `print` 进源码；禁止 TODO/FIXME 与注释掉的旧代码 | `grep -rn "except:" cleaning app.py`；`grep -rn "print(" cleaning app.py`；`grep -rn "TODO\|FIXME" cleaning app.py`；三者均应为空 | CI 红，禁止合并 |
| CODE-07 | 门禁 | 一切上限、阈值、超时、分块大小只许定义在 `cleaning/limits.py`，业务代码引用常量名，不许再写 `500000`、`60` 这类字面量 | `grep -rn "500000\|2000000\|1048576" cleaning --include="*.py"` 只应命中 `limits.py` | CI 红或评审打回 |
| CODE-08 | 要求 | 界面文案中的判定口径必须引用 `cleaning` 的同一来源，禁止界面与核心各写一份同义文案 | 检索重复文案；对照 `cleaning/rules.py` 的 `MISSING_RULE`、`DUPLICATE_RULE`、`FORMULA_ISSUE` | 评审打回，改为一处定义 |
| CODE-09 | 要求 | 公共 API（`cleaning/__init__.py` 的 `__all__` 内）必须有 docstring，写清参数、返回、会抛 `TableError` 的情形 | 逐项抽查 `__all__` 内的函数与类 | 评审打回补 docstring |
| CODE-10 | 建议 | 注释只写“为什么”，不写“是什么”；特殊写法必须说明原因并带缺陷编号或豁免编号 | 评审抽查新增注释 | 评审指出，作者改写或删除 |
| CODE-11 | 要求 | 禁止静默吞异常：捕获后既不提示用户、不写报告、也不重新抛出的，一律违规；降级行为必须对用户可见 | 评审逐处检查新增 `except` 分支的后续动作 | 评审打回，补提示或重新抛出 |
| CODE-12 | 门禁 | 用户可见错误一律走 `TableError` 中文提示，禁止把英文堆栈抛给用户；入口层捕获未知异常也必须转成中文提示 | 跑 `python -m pytest tests -q -k "error or invalid or broken"`；`grep -n "st.error" app.py` 逐条确认中文文案 | CI 红或评审打回 |

### 3.2 现行代码的落点说明

- `app.py` 与 `cleaning/` 中现有 `except Exception` 均在捕获后给用户中文提示或记录到批量结果（如 `cleaning/batch.py` 单项失败、`app.py` 页面错误提示），不属于静默吞异常；新增代码不得借“已有先例”扩大捕获范围。
- `cleaning/templates.py` 的 `Any` 只出现在 JSON 解析边界（见 TYPE-04 与附录 A 的 EX-005），不得扩散到业务函数返回类型。
- 现有源码 `print`、TODO、FIXME 检查结果均为 0 条【现行值】，本文将其固化为门禁。

## 4 类型标准

### 4.1 标准条文

| 编号 | 级别 | 条文 | 检查方法 | 违反后果 |
| --- | --- | --- | --- | --- |
| TYPE-01 | 要求 | 新增和改动代码的参数、返回值类型标注覆盖率 100%【目标值】；mypy 警告期先测量，M4 起升为门禁 | 对 diff 跑 mypy；未标注函数计入 `disallow_untyped_defs` | 新增代码未标注禁止合并 |
| TYPE-02 | 门禁 | `cleaning/` 启用 mypy 非严格模式，配置写在 `pyproject.toml` 的 `[tool.mypy]`；存量错误只许减少不许增加 | 执行 `mypy`；本次基线 4 条【现行值】，超过基线即失败 | 错误数增加禁止合并 |
| TYPE-03 | 要求 | `app.py` 只要求新增和改动代码标注，不要求存量补齐，也不进 mypy 强制范围 | 评审抽查本次 diff 的新增函数 | 新增未标注评审打回 |
| TYPE-04 | 门禁 | 禁止用 `Any` 绕过检查；确需使用必须登记豁免并写理由 | `grep -rn "Any" cleaning`；对照附录 A 的 EX-005 | 未登记即 CI 红或评审打回 |
| TYPE-05 | 要求 | 标注写真实结构：容器标到元素类型，能用 `NamedTuple`/dataclass 的不另拼裸 `dict`；`CleanOptions`、`ColumnOps` 改字段时标注与 docstring 同步改 | 评审抽查公共 API 与 `transform.py` 的载体类 | 评审打回 |
| TYPE-06 | 要求 | mypy 收紧顺序固定：非严格（当前）→ `disallow_untyped_defs = true` → `disallow_incomplete_defs = true` → 严格模式；每步至少跨 1 个版本 | 对照 `pyproject.toml` 与附录 B 的变更记录 | 跳步收紧视为流程违规 |

### 4.2 mypy 基线【现行值】

命令与完整输出见附录 C。基线为 **4 条错误，均在 `cleaning/transform.py`**：

| 行 | 错误码 | 现象 | 处理 |
| --- | --- | --- | --- |
| 50 | `operator` | `base**exp`，`base/exp` 标为 `object` | 登记 EX-001，棘轮还债 |
| 71 | `call-overload` | `round(value, ndigits)` 的 `value` 为 `object` | 登记 EX-002 |
| 280 | `operator` | 一元负号作用于 `object` | 登记 EX-003 |
| 288 | `call-overload` | 白名单函数分发时调用类型未知 | 登记 EX-004 |

没有为了消错改逻辑，也没有用 `Any` 或 `# type: ignore` 压掉。目标是把 `_eval_node` 的操作数类型收敛为可判定的联合类型后再清零【目标值】。

## 5 架构与分层

### 5.1 标准条文

| 编号 | 级别 | 条文 | 检查方法 | 违反后果 |
| --- | --- | --- | --- | --- |
| ARCH-01 | 门禁 | 依赖方向只能是入口层（`app.py`、将来的 CLI/API）→ `cleaning/`；禁止反方向 | `grep -rn "import app" cleaning --include="*.py"` 应为空 | CI 红，禁止合并 |
| ARCH-02 | 门禁 | `cleaning/` 禁止 import Streamlit、禁止读 UI 会话状态、禁止感知调用入口 | `grep -rn "streamlit\|session_state" cleaning --include="*.py"` 应为空 | CI 红，禁止合并 |
| ARCH-03 | 要求 | 新逻辑先归入现有模块：`io` 读取、`rules` 判定、`transform` 列变换、`export` 导出、`limits` 上限、`templates` 模板、`batch` 批量、`xlsx_scan` 预检、`errors` 错误类型、`__init__` 导出；放不下才新建模块且必须写明职责与边界 | 评审对照模块 docstring 与改动落点 | 评审打回或要求补模块说明 |
| ARCH-04 | 门禁 | `CleanOptions` / `ColumnOps` 是界面、批量、将来 CLI 的唯一规则载体；禁止入口各自定义一套选项再翻译 | `grep -rn "class .*Options\|class .*Ops" app.py cleaning` 只应命中 `transform.py` | CI 红或评审打回 |
| ARCH-05 | 要求 | 公共 API 以 `cleaning/__init__.py` 的 `__all__` 为唯一清单；本版不增删导出；清单外一律内部 | `python -c "import cleaning; print(cleaning.__all__)"` 与上一版比对 | 导出变化即阻断发版 |
| ARCH-06 | 要求 | 内部函数改名、拆分不算破坏兼容，但必须在 CHANGELOG 的“变更”类目注明 | 检查 CHANGELOG 对应版本条目 | 评审打回补记录 |
| ARCH-07 | 门禁 | `cleaning/` 的配置只走 `limits.py` 的统一来源；入口改上限只能改配置，不许在入口私自放宽后把大表喂进来 | `grep -rn "MAX_ROWS\s*=\|MAX_COLS\s*=\|MAX_CELLS\s*=" cleaning app.py` 只应命中 `limits.py` 与测试 | CI 红或评审打回 |

## 6 正确性细节要求

正确性是本项目的命门。本章标准不允许降级为“建议”执行。

### 6.1 标准条文

| 编号 | 级别 | 条文 | 检查方法 | 违反后果 |
| --- | --- | --- | --- | --- |
| COR-01 | 要求 | 每个缺陷修复必须同时交付回归测试，以及该测试在旧代码上确实失败的验证记录；只交“新代码通过”不算完成 | 检查 PR 是否附旧代码失败输出；对照 `tests/test_regressions_v201.py` 的做法 | 修复不算完成，禁止合并 |
| COR-02 | 要求 | 修复 diff 中每处行为变化必须对应缺陷本身或边缘清单的一项；对不上的拆成独立提交 | 逐行读 diff，与缺陷编号/边缘清单比对 | 评审打回拆分 |
| COR-03 | 要求 | 改动前按第 6.2 节“改动类型 → 必查边缘清单”逐项核对，结果写进 PR 自检 | 对照 `.github/pull_request_template.md` 第 2 组勾选 | 漏核视为评审未完成 |
| COR-04 | 要求 | 缺失值、重复行、可疑格式的判定口径只以 `cleaning/rules.py` 的常量与 README 判定规则节为来源，两处必须一致 | 对比 `MISSING_RULE`、`DUPLICATE_RULE`、`FORMULA_ISSUE` 与 README 文案 | 不一致即阻断发版，记缺陷 |
| COR-05 | 门禁 | 分块清洗结果必须与整表清洗结果逐单元格一致 | `python -m pytest tests/test_v2_features.py -q -k "chunk"` 必须通过 | CI 红，禁止合并 |
| COR-06 | 门禁 | 禁止静默改数据：长编号保持文本、整数溢出留空并提示、混时区保留原文并提示、合并单元格提示数量 | 跑 `tests/test_regressions_v201.py` 18 项与相关界面用例 | CI 红，禁止合并 |
| COR-07 | 要求 | 文档与代码冲突时以代码为准，立即修文档并同时记一条缺陷；不许只改文档掩盖行为 | 检查缺陷记录与文档 diff 是否成对出现 | 评审打回补缺陷记录 |

### 6.2 改动类型 → 必查边缘清单

| 改动类型 | 必查边缘 | 对应现有测试/命令 |
| --- | --- | --- |
| IO（`cleaning/io.py`、`cleaning/xlsx_scan.py`） | 编码（UTF-8、BOM、GBK）、分隔符与引号、表头行、合并单元格、XML 实体解码、空表与超限早停 | `tests/test_app_logic.py`、`tests/test_v2_features.py -k "header or delimiter or limit"` |
| transform（`cleaning/transform.py`、`cleaning/rules.py`） | 长编号 >15 位精度、整数溢出、指数爆炸、`round` 位数、全角字符、混时区日期、转换失败保留原文 | `tests/test_regressions_v201.py` 18 项、`tests/test_v2_features.py -k "computed or convert"` |
| export（`cleaning/export.py`） | 公式注入三模式、`=` 开头文本、`00123` 前导零、文件名时间戳与重名、ZIP 同名条目 | `tests/test_v2_features.py -k "formula or number_mode or zip or timestamp"` |
| templates（`cleaning/templates.py`） | 改名后列名引用、无 `version` 字段旧模板、迁移逐单元格一致判据 | `tests/test_v2_features.py -k "template"`、`tests/test_app_ui_v2.py` |
| limits/rules（`cleaning/limits.py`、`cleaning/rules.py`） | 上限早停与中文提示、分块 = 整表不变量、缺失值/重复行/可疑格式判定口径唯一出处 | `tests/test_v2_features.py -k "limit or chunk or missing"` |
| 界面/批量（`app.py`、`cleaning/batch.py`） | 入口共用 `CleanOptions`/`ColumnOps`、批量与单文件结果一致、选项变化旧下载失效、进度与超时、中文错误 | `tests/test_app_ui.py`、`tests/test_app_ui_v2.py`、`tests/test_v2_features.py -k "batch"` |

逐类细则（每类改动都必须逐条核对，不能只看表头）：

1. **改 IO**
   - 编码：UTF-8、带 BOM、GBK/GB18030 各读一遍，乱码与崩溃都算失败；`cleaning/io.py` 的两段解码逻辑必须都有用例。
   - 分隔符与引号：分隔符出现在引号内、行尾多分隔符、空列名、重复列名（pandas 的 `金额.1` 形式）。
   - 表头行：表头不在第 1 行、表头有空值、合并单元格展开后的列名、越界表头行给中文提示。
   - 合并单元格：只提示不填充；数据区合并单元格数量与影响要提示，表头合并不误报。
   - XML 实体解码：XLSX 里的 `&amp;` 等实体只解一层，不多解、不少解；`cleaning/xlsx_scan.py` 的流式扫描不受实体长度影响。
   - 空表与超限早停：空文件、只有表头、超行/列/单元格上限，必须在重活开始前给中文 `TableError`，不产生半成品。

2. **改 transform**
   - 长编号：>15 位有效数字的编号全程保持文本，包括“整列都是 18 位整数”的纯整数列形态；不得被 int64 截断或 float64 丢尾数。
   - 整数溢出：`4000000000*4000000000` 按 float64 计算，溢出留空并提示，不得静默回绕成负数。
   - 指数爆炸：`9**9**9`、`10**100000000` 在求值前按位数估算拦截，不能真的去算。
   - `round` 位数：`round(1, -10000000)` 必须被 ±30 的位数上限拦截并给中文提示。
   - 全角字符：全角括号/运算符/等号归一化后口径一致，归一化不得改动列名与数据内容。
   - 混时区日期：同列既有带时区又有不带时区时整列保留原文并提示；同一时区按当地时间的日期处理，时区标记不保留。
   - 转换失败保留原文：任何一格转换失败，该格保留原值并计数提示，不许整列清空或整表失败。

3. **改 export**
   - 公式注入三模式：`quote`（单引号）、`zero-width`（零宽空格）、`off`（关闭）三模式行为固定；模式不因界面、批量、将来 CLI 入口不同而变化。
   - `=` 开头文本：CSV 三模式按选择处理，XLSX 始终按文本写入，不被 Excel 当公式执行。
   - `00123` 前导零：CSV 与 XLSX 两种导出都不丢失；XLSX「尝试转数字」模式下 `00123 → 123`，`A002` 保留文本。
   - 文件名：带原文件名与时间戳，连续导出不覆盖；路径分隔符、非法字符、超长名要清理。
   - ZIP 同名条目：批量 ZIP 内同名文件自动加 ` (2)`，不互相覆盖、不丢文件。

4. **改 templates**
   - 改名后列名引用：模板里的「列顺序」「计算列」引用改名后的新列名时必须保留，不得误报“当前文件没有这一列”。
   - 无 `version` 字段旧模板：按现行 `TEMPLATE_VERSION = 2` 的默认规则读入；旧字段缺失走默认值，不报错。
   - 迁移逐单元格一致：迁移/往返前后，对同一输入清洗输出的每个单元格必须相同；该判据要有自动化用例，不靠人工抽查。
   - 坏模板：坏 JSON、字段类型错误、列名不存在，只给中文提示，不整页崩溃。

5. **改 limits/rules**
   - 上限早停：超限在解析/清洗/导出之前拦下，提示写清超了哪一项、现值多少、上限多少，不写含糊的“文件过大”。
   - 分块 = 整表：`chunk_rows` 强制小块与整表处理结果逐单元格一致，含跨块去重、进度回调、超时检查。
   - 判定口径唯一：缺失值、重复行、可疑格式只在 `rules.py` 定义一次；界面与 README 引用，不复制。
   - 上限不绕过：业务代码不得私自调大或跳过 `current_limits()`；测试覆盖时必须走 monkeypatch 或参数覆盖。

6. **改界面/批量（补充类）**
   - 同一规则载体：界面与批量都传 `CleanOptions`/`ColumnOps`，不得各拼一套参数。
   - 批量 = 单文件：同一文件单跑与进批量结果一致，失败项只记原因不拖垮整批。
   - 旧下载失效：任何选项变化后旧导出必须失效，下载按钮消失，避免用户拿到旧规则文件。
   - 进度与超时：>10 万行有分块进度；超时给中文提示，不产出半成品。
   - 中文错误：页面与批量结果不出现英文堆栈，异常统一收敛为 `TableError` 文案。

## 7 测试标准

### 7.1 标准条文

| 编号 | 级别 | 条文 | 检查方法 | 违反后果 |
| --- | --- | --- | --- | --- |
| TEST-01 | 要求 | 覆盖率执行顺序写死：先测量、公布基线，再设门禁；本版只测量公布，不拦截 | 检查 `pyproject.toml` 无 `fail_under`、附录 C 已公布基线；顺序颠倒即失败 | 顺序颠倒即阻断发版 |
| TEST-02 | 要求 | `cleaning/` 行覆盖率不低于 90%【目标值】；新增代码行覆盖率不低于 95%【目标值】；不得低于已公布基线 93%【现行值】；M4 起升为门禁 | CI coverage 报告；低于阈值即失败 | M4 前评审打回，M4 起 CI 红禁止合并 |
| TEST-03 | 要求 | 测试名必须含“被测对象 + 条件 + 期望”；禁止 `test_1`、`test_ok` 这类无信息名 | 评审抽查测试函数名 | 评审打回改名 |
| TEST-04 | 要求 | 每个测试按准备、执行、断言三段组织；断言到具体值与结构，禁止只断“不报错”“结果非空” | 评审抽查断言 | 评审打回补断言 |
| TEST-05 | 门禁 | 禁止 flaky：禁止 `sleep` 等时序、禁止靠固定随机种子蒙混；失败重跑仍红按真失败处理 | `grep -rn "sleep(" tests` 应为空；CI 失败即红 | CI 红，禁止合并 |
| TEST-06 | 要求 | 界面测试禁止静默跳过；每处跳过必须在 TEST_REPORT 逐项解释原因与恢复条件 | `pytest -q -rs` 输出跳过原因；对照 TEST_REPORT | 未解释的跳过按测试缺失处理，阻断发版 |
| TEST-07 | 要求 | 三环境验证固化为 CI 矩阵：Python 3.11 与 3.12【目标值】；干净 venv 与最低依赖版本为后续目标 | `.github/workflows/tests.yml` 的 matrix；本机实测记录 | 缺的矩阵未补且未人工补验，阻断发版 |
| TEST-08 | 要求 | 测试数据只许虚构样例，禁止真实用户数据、真实订单、真实个人信息进仓库（改名的也算） | 评审检查 `samples/` 与测试 fixture；检索真实姓名/手机号/身份证模式 | 阻断合并并清理历史 |
| TEST-09 | 门禁 | 214 项既有测试只增不减；删改既有用例必须在 PR 逐项说明理由 | `pytest --collect-only -q` 收集数与本版记录比对 | 收集数减少且无说明即阻断发版 |

### 7.2 测试数量与覆盖率口径

- 现行测试数 214【现行值】：`test_app_logic.py` 89、`test_v2_features.py` 75、`test_regressions_v201.py` 18、`test_app_ui.py` 18、`test_app_ui_v2.py` 14；182 逻辑 + 32 界面。
- 现行覆盖率 93%【现行值】：`cleaning/` 1183 statements、83 missed，2026-11-06 用 `coverage 7.17.2` 实测；命令与完整输出见附录 C。
- 覆盖率目标【目标值】：`cleaning/` ≥90%，新增代码 ≥95%；本版不设门禁，M4 起按第 14 节切换。
- 跳过口径：只有“没装 Streamlit”或“Streamlit < 1.66 的 `AppTest` 不支持 `file_uploader`”允许整文件跳过；跳过必须在 TEST_REPORT 写明原因与恢复条件。本次 214 项 0 skipped【现行值】。

## 8 性能标准

### 8.1 标准条文

| 编号 | 级别 | 条文 | 检查方法 | 违反后果 |
| --- | --- | --- | --- | --- |
| PERF-01 | 要求 | 建立 benchmark 基线后才设性能门禁；基线必须用固定数据集、固定脚本实测，写明环境与日期【目标值】 | 检查仓库是否有 benchmark 脚本与结果表；当前未建立【现行值】 | 未建立前只报告，不设门禁 |
| PERF-02 | 要求 | 同一数据集耗时或内存峰值回退超过 10%【目标值】必须在 PR 解释并获确认，禁止静默合并 | PR 附测量数据；评审对照基线表 | 未解释即评审打回 |
| PERF-03 | 门禁 | 大文件路径必须有超时与进度反馈：默认 60 秒、100,000 行分块阈值、50,000 行块大小【现行值】 | `grep -n "DEFAULT_TIMEOUT_SECONDS\|CHUNK_THRESHOLD_ROWS\|CHUNK_ROWS" cleaning/limits.py`；跑超时与分块用例 | CI 红，禁止合并 |
| PERF-04 | 要求 | 性能测量固定方法：同一机器、同一数据集跑 3 次取中位数；耗时用 `time.perf_counter()`，Python 内存峰值用 `tracemalloc`，进程内存用 `/usr/bin/time -v` 的 `Maximum resident set size` | 检查 benchmark 脚本是否按此法实现；复跑记录 | 方法不固定则数据不可比，评审打回 |
| PERF-05 | 建议 | 禁止无上限地把整表复制多份；导出 XLSX 这类高内存路径在文档写明峰值与部署内存下限 | 评审检查新增路径的内存拷贝；对照 README 性能参考 | 评审指出，作者补说明或优化 |

### 8.2 现行性能事实与未完成项

- 现有 README 的耗时/内存表是 v2.0.1 在开发机上的实测【现行值】，不是本版重测；本版未重跑，报告写“未执行”。
- benchmark 套件（固定数据集 + 脚本 + 结果入库）尚未建立【现行值】；10% 回退阈值只是目标值，没有基线就不能判定。
- 大文件保护已有实现：默认硬超时 60 秒、>100,000 行自动分块、每块 50,000 行、块间检查 `Deadline` 并回调进度【现行值】。
- 复跑方法（基线建立后）：固定 `samples/` 之外的合成数据集 → `python -m benchmark.run --dataset <name> --repeat 3`（脚本随 baseline 一起提交）→ 记录中位数与 `ru_maxrss`；该命令当前不存在，属目标值。

## 9 安全与隐私

### 9.1 标准条文

| 编号 | 级别 | 条文 | 检查方法 | 违反后果 |
| --- | --- | --- | --- | --- |
| SEC-01 | 门禁 | 代码不得发起对外网络请求 | `grep -rn "requests\|urllib\|socket\|http://\|https://" cleaning app.py --include="*.py"` 只允许注释/许可证 URL；实际调用为 0 | CI 红，禁止合并 |
| SEC-02 | 门禁 | 上传内容不得落盘、不得写数据库 | `grep -rn "open(.*[\"']w\|tempfile\|sqlite3" cleaning app.py` 应为空 | CI 红，禁止合并 |
| SEC-03 | 门禁 | Streamlit 用量上报必须关闭 | `grep -n "gatherUsageStats" .streamlit/config.toml` 应为 `false` | CI 红，禁止合并 |
| SEC-04 | 要求 | 计算列 AST 白名单任何扩充必须单独评审并补攻击样例；属性访问、下标、`__import__`、lambda、推导式等拒绝用例禁止删除 | 对照 `cleaning/transform.py` 的 `_ALLOWED_BINOPS`/`_ALLOWED_FUNCS` 与测试用例；攻击样例缺失即失败 | 评审打回，补评审记录与攻击样例 |
| SEC-05 | 门禁 | `limits.py` 的上限禁止在业务代码中绕过或私自调大 | `grep -rn "MAX_ROWS\s*=\|MAX_COLS\s*=\|MAX_CELLS\s*=" cleaning app.py` 只应命中 `limits.py` 与测试 | CI 红，禁止合并 |
| SEC-06 | 要求 | 依赖变更必须过 `pip-audit`；新增依赖必须写理由与维护状态评估，禁止为一句话功能引入大包 | CI 跑 `pip-audit`；PR 描述写依赖理由 | 未过扫描或未写理由，评审打回 |
| SEC-07 | 要求 | 禁止把密钥、token、真实数据、本机绝对路径写进仓库 | `grep -rn "/root\|/home/\|token\|password" --include="*.py" .` 抽查；发布包卫生检查 | 阻断发版并清理历史 |

### 9.2 工具现状与落点

- `pip-audit` 当前不在 `requirements-dev.txt`、不在 CI【现行值】；目标是在 M3 加入 CI，先报告后门禁【目标值】。
- 现有防护：`clean_data` 只删行不改单元格；CSV 三模式公式防护；XLSX 的 `=` 开头文字按文本写入；计算列白名单求值；上限集中在 `limits.py`。
- 现有检查命令与结果见附录 C；README 的“数据与隐私”节是用户可见口径，代码变更后必须同步。

## 10 文档标准

### 10.1 标准条文

| 编号 | 级别 | 条文 | 检查方法 | 违反后果 |
| --- | --- | --- | --- | --- |
| DOC-01 | 要求 | 行为变更必须同步文档：README 判定规则、USER_GUIDE、FAQ、CHANGELOG 按影响面勾选；未同步视为未完成 | 对照 PR 模板第 4 组；检索变更点是否在对应文档出现 | 阻断合并 |
| DOC-02 | 要求 | 文档中一切数字必须来自实际命令输出，并注明环境与日期口径；没跑的写“未执行”，禁止估算或照抄模板 | 抽查数字能否在 TEST_REPORT/附录 C 找到命令与输出 | 阻断发版，改为实测或“未执行” |
| DOC-03 | 要求 | 同一事实只设一个权威出处，其他文档只引用不复制数字 | 检索重复数字与重复口径段落 | 记文档缺陷，限期改为引用 |
| DOC-04 | 要求 | 文档与代码冲突以代码为准，立即修文档并同时记一条缺陷 | 检查缺陷记录与文档 diff 是否成对 | 评审打回 |
| DOC-05 | 门禁 | 版本号在 `pyproject.toml`、`cleaning/__init__.py`、`CHANGELOG.md`、git tag 四处一致（SemVer） | 发版前逐处 grep；tag 指向最终提交 | 不一致即阻断发版 |
| DOC-06 | 要求 | CHANGELOG 顶部必须有本次版本条目，并单列“已知限制与未完成项”；未执行项写“未执行” | 检查 CHANGELOG 对应版本条目 | 缺失即阻断发版 |
| DOC-07 | 建议 | 公共 API 文档与内部实现分离；内部改名只在 CHANGELOG 注明，不写进用户指南 | 评审检查文档受众与内容 | 评审指出，作者调整 |

### 10.2 本项目文档权威出处

| 事实 | 权威出处 | 其他文档的处理 |
| --- | --- | --- |
| 判定规则（缺失值、重复行、可疑格式、清洗顺序） | `cleaning/rules.py` 常量 + `README.md` 判定规则节 | USER_GUIDE、FAQ 只引用，不另写一套数字 |
| 资源上限默认值 | `cleaning/limits.py` | README 资源限制表、FAQ 只引用 |
| 测试数量与结果 | `TEST_REPORT.md` 与 CI 输出 | README 引用总数，不复制逐项命令输出 |
| 工程标准 | 本文 | 其他文档只写条目编号 |
| 版本变更 | `CHANGELOG.md` | README 首屏只写当前版本号 |

## 11 Git 与评审

### 11.1 标准条文

| 编号 | 级别 | 条文 | 检查方法 | 违反后果 |
| --- | --- | --- | --- | --- |
| GIT-01 | 要求 | 小步提交、一个提交一件事；标题写清“动词 + 对象”，例如 `docs: 新增 ENGINEERING_STANDARDS 工程规范` | `git log --oneline` 逐条检查；一个提交混多件事即失败 | 评审打回重做提交 |
| GIT-02 | 要求 | 禁止把整版压成一个大提交；提交历史必须能按任务区分 | 统计提交数与改动文件；单提交包含互不相关模块即失败 | 阻断合并 |
| GIT-03 | 要求 | PR 必须使用 `.github/pull_request_template.md`，7 组自检清单全部勾选或有豁免编号 | 检查 PR 描述；缺组即失败 | 禁止合并 |
| GIT-04 | 要求 | 合并门禁 = CI 全绿 + 清单全勾 + CHANGELOG 已更新，缺一不可 | 合并前检查 CI 状态、PR 清单、CHANGELOG diff | 禁止合并 |
| GIT-05 | 要求 | 版本号与 git tag 与 CHANGELOG 一一对应，遵循 SemVer | `git tag --list` 与版本三处比对 | 阻断发版 |
| GIT-06 | 要求 | 提交信息用中文或英文均可，但同一仓库保持一种主语言；正文写“为什么”而不是文件清单 | 抽查最近 10 条提交 | 评审指出，作者改写 |

### 11.2 PR 自检清单的 7 组条目

1. 改动归类（代码 / IO / transform / export / templates / limits / 界面 / 测试 / 文档 / CI / 发布）。
2. 边缘清单核对（第 6.2 节六类逐项）。
3. 回归测试及旧代码失败验证。
4. 文档同步（README / USER_GUIDE / FAQ / CHANGELOG）。
5. benchmark 影响（未建立基线时写“未测量”并说明）。
6. 豁免登记（条目编号、理由、批准人、到期版本）。
7. CHANGELOG 更新与版本一致性。

## 12 发布标准

### 12.1 标准条文

| 编号 | 级别 | 条文 | 检查方法 | 违反后果 |
| --- | --- | --- | --- | --- |
| REL-01 | 要求 | 在 `RELEASE_CHECKLIST.md` 之上追加：发布产物留 sha256 校验和并记录 | `sha256sum <产物>` 写入发布记录；无记录即失败 | 阻断发版 |
| REL-02 | 要求 | 发版说明必须含“已知限制与未完成项”；真机验收等未执行项必须明示，禁止冒充通过 | 检查 CHANGELOG/发版说明对应段落 | 阻断发版 |
| REL-03 | 要求 | 发布后在干净环境做冒烟：`pip install .` 成功 + 用 `samples/` 样例跑通全流程 | 新建 venv 执行安装、导入、样例清洗；记录命令与结果 | 冒烟失败即回滚 |
| REL-04 | 要求 | 版本号、tag、CHANGELOG、发布说明一一对应；tag 打在最终提交上 | `git describe --tags`、`git log --oneline` 对照 | 阻断发版 |
| REL-05 | 要求 | 发布包排除 `__pycache__/`、`.pytest_cache/`、`.venv/`、`.coverage`、`*.egg-info/`、本机日志与客户数据 | 解压发布包后 `find` 抽查 | 评审打回重打包 |
| REL-06 | 要求 | 保留上一版可回滚产物与 sha256；发版前确认回滚路径可用 | 检查发布记录中的旧版产物与校验和 | 无回滚产物不得对外发布 |
| REL-07 | 要求 | 不得把未实现功能写成已实现；不得用含糊结论替代实测结论 | 全文检索含糊结论词；对照功能清单与测试报告 | 阻断发版并修文档 |

### 12.2 发布前最小动作

1. 跑 `python -m pytest -q -rs`，记录通过数/总数与耗时。
2. 跑 `ruff format --check .`、`ruff check .`，记录输出。
3. 跑 `mypy`，记录错误数；有新增错误先还债。
4. 跑覆盖率命令，记录 `cleaning/` 百分比；与上一版比对。
5. 在干净 venv 执行 `pip install .`，再用 `samples/验收样例-分号.csv` 与 `samples/验收样例-双表.xlsx` 走一遍。
6. 更新 CHANGELOG 的“已知限制与未完成项”，打 tag，记录 sha256。

## 13 细节总清单

以下 52 条为逐项可核对的判据，正文负责解释，本清单只写判据。发布或合并前逐条勾选。

### 13.1 规范与豁免

- [ ] D-01 每条标准五要素齐全（编号/级别/条文/检查方法/违反后果）
- [ ] D-02 门禁条文都有可执行命令或测试断言
- [ ] D-03 豁免登记条目编号、理由、批准人、到期版本
- [ ] D-04 到期豁免已失效或已重新评审
- [ ] D-05 本版豁免条数少于上一版
- [ ] D-06 规范变更已记入附录 B

### 13.2 代码与类型

- [ ] D-07 新增/改动代码按全部标准执行，无过渡期
- [ ] D-08 `ruff format --check .` 通过
- [ ] D-09 `ruff check .` 通过
- [ ] D-10 无未注明理由的 `per-file-ignores`
- [ ] D-11 命名符合 snake_case / PascalCase / 全大写常量
- [ ] D-12 导入三组排序，无 `import *`
- [ ] D-13 新增函数 ≤60 行【目标值】
- [ ] D-14 新增函数圈复杂度 ≤10、嵌套 ≤4 层【目标值】
- [ ] D-15 无裸 `except:`、无 `print`、无静默吞异常
- [ ] D-16 无 TODO/FIXME 与注释掉的旧代码
- [ ] D-17 上限/阈值/超时只定义在 `cleaning/limits.py`
- [ ] D-18 判定规则文案只有一处来源
- [ ] D-19 公共 API 有 docstring，写清参数/返回/TableError
- [ ] D-20 注释写“为什么”，特殊写法带编号
- [ ] D-21 新增/改动代码类型标注 100%【目标值】
- [ ] D-22 mypy 错误数不高于基线 4 条
- [ ] D-23 `Any` 使用已登记豁免
- [ ] D-24 `cleaning/` 不 import Streamlit、不读 UI 状态
- [ ] D-25 依赖方向只有入口 → `cleaning/`
- [ ] D-26 新模块写明职责、边界、调用方
- [ ] D-27 `CleanOptions`/`ColumnOps` 是唯一规则载体
- [ ] D-28 公共 API 与 `__all__` 一致，无擅自增删

### 13.3 正确性与测试

- [ ] D-29 每个缺陷修复有回归测试
- [ ] D-30 回归测试在旧代码上失败、新代码上通过，有记录
- [ ] D-31 六类改动边缘清单逐项核对
- [ ] D-32 分块清洗结果等于整表
- [ ] D-33 长编号、溢出、混时区、合并单元格不静默改数据
- [ ] D-34 覆盖率已测量并公布基线（93%）
- [ ] D-35 `cleaning/` 覆盖率 ≥90%【目标值】
- [ ] D-36 新增代码覆盖率 ≥95%【目标值】
- [ ] D-37 测试名含被测对象、条件、期望
- [ ] D-38 断言到具体值，不只断“不报错”
- [ ] D-39 无 `sleep(`、无靠固定种子蒙混
- [ ] D-40 界面测试跳过有逐项解释
- [ ] D-41 CI 矩阵覆盖 Python 3.11 与 3.12
- [ ] D-42 测试数据全部虚构
- [ ] D-43 214 项既有测试只增不减

### 13.4 性能、安全、文档、Git、发布

- [ ] D-44 benchmark 回退 >10% 已解释并确认【目标值】
- [ ] D-45 大文件有超时与进度反馈
- [ ] D-46 `pip-audit` 已过，新增依赖写了理由
- [ ] D-47 无网络请求、无落盘、用量上报关闭
- [ ] D-48 AST 白名单拒绝用例未删，扩充有评审与攻击样例
- [ ] D-49 文档数字有命令、环境、日期出处
- [ ] D-50 版本号在 pyproject / `__init__` / CHANGELOG / tag 四处一致
- [ ] D-51 CHANGELOG 有“已知限制与未完成项”，未执行写“未执行”
- [ ] D-52 PR 清单全勾、CI 全绿、发布产物有 sha256

## 14 落地安排

### 14.1 工具链引入与切换

| 工具链 | 引入节点 | 本版状态【现行值】 | 警告 → 门禁切换条件【目标值】 |
| --- | --- | --- | --- |
| Ruff（format + check） | v2.2.0（对应规划 M1 的工程化部分） | 已落地；本地与 CI 均通过；CI job `continue-on-error: true` | 连续 2 个发布版本全绿且 `per-file-ignores` 为 0，删除 `continue-on-error` |
| mypy（非严格） | v2.2.0（M1） | 已落地；4 条错误，全部登记豁免 | 连续 2 个版本错误数 0，切 `disallow_untyped_defs = true` |
| coverage | v2.2.0（M1） | 已测量 93%，仅公布，不设门禁 | 基线已公布；M4 起 `cleaning/` ≥90% 且不低于基线 |
| CI 矩阵（3.11/3.12） | v2.2.0（M1） | 已配置；本地仅 3.12 实跑，3.11 未执行 | 3.11 在 CI 连续 2 个版本全绿后视为稳定 |
| pip-audit | 目标 M3 | 未引入；`requirements-dev.txt` 无此包 | 连续 1 个版本无 high/critical，进 CI 门禁 |
| benchmark 套件 | 目标 M3 | 未建立；10% 阈值无基线可判 | 基线入库后才启用 10% 回退门禁 |

### 14.2 对照 M1–M4 的落地位置

| 里程碑 | 规划内容 | 本文要求的工具链动作 | 本版实际 |
| --- | --- | --- | --- |
| M1 | 打包与 API 固化 | 引入 `pyproject.toml`、Ruff、mypy、coverage；公共 API 清单成文 | `pyproject.toml`、Ruff、mypy、coverage 已完成；API 未增删；CLI 未做（本版范围外） |
| M2 | CLI 与模板预埋 | CLI 入口必须共用 `CleanOptions`/`ColumnOps`；模板 version 字段迁移用例 | 未执行（本版明确不做 CLI 与模板 schema） |
| M3 | benchmark 与呈现补齐 | benchmark 基线入库；LICENSE、README、CHANGELOG 齐备 | LICENSE、README、CHANGELOG 已完成；benchmark 未建立 |
| M4 | 验收与发版 | 全量回归、覆盖率门禁、真机验收、tag 与 sha256 | 214 项全过、tag 与本地提交已完成；覆盖率门禁与真机验收未执行 |

### 14.3 v3 还要再升的标准方向

只列方向，具体拆解以《table-cleaner v3 升级路线图》为准：

- **v3.0 引擎版**：Polars 引擎与 Pipeline DSL 的双引擎结果一致性、行级血缘、懒执行下的内存上限；本文的正确性边缘清单与分块=整表不变量需扩展到新引擎。
- **v3.1 算法版**：模糊去重与智能类型推断的评估方法、误判率与可解释性标准；本文的类型标注与判定口径唯一性需覆盖新推断层。
- **v3.2 工程版**：属性测试与 Fuzzing、mypy 严格模式、性能回归 CI、覆盖率徽章；本文的目标值在该阶段转为门禁。

## 附录 A 豁免登记表模板与在册清单

### A.1 模板

| 字段 | 填写要求 |
| --- | --- |
| 豁免编号 | `EX-NNN`，全仓库唯一，不复用 |
| 条目编号 | 违反的本文标准编号，如 `TYPE-02` |
| 文件/范围 | 具体文件、函数或行号范围，禁止写“全仓” |
| 理由 | 写清为什么当前不能达标、代价是什么；禁止写“历史原因”“没时间” |
| 批准人 | 项目负责人或需求方指定人员 |
| 到期版本 | 具体版本号，如 `v2.3.0`；禁止写“永久” |
| 还债计划 | 下一次尝试的做法与验收判据 |
| 状态 | `在册` / `已还` / `已续期` |

### A.2 在册清单【现行值】

| 编号 | 条目 | 文件/范围 | 理由 | 批准人 | 到期版本 | 还债计划 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| EX-001 | TYPE-02 | `cleaning/transform.py:50` `base**exp` | 操作数可能是 int、float、numpy 标量或 Series，当前签名 `object`；直接改联合类型需先梳理 `_eval_node` 全路径 | ka-ggg | v2.3.0 | 把 `_eval_node` 操作数收敛为 `int \| float \| pd.Series`，补类型用例后清零 | 在册 |
| EX-002 | TYPE-02 | `cleaning/transform.py:71` `round(value, ndigits)` | 同上，`round` 可接收标量或 Series；mypy 的 `SupportsRound` 不能覆盖 Series 重载 | ka-ggg | v2.3.0 | 为标量与 Series 分流或加精确重载，保持 `round(金额,2)` 行为不变 | 在册 |
| EX-003 | TYPE-02 | `cleaning/transform.py:280` 一元负号 | `value` 来自 AST 求值结果，类型为 `object`；负号对 Series 与标量行为不同 | ka-ggg | v2.3.0 | 与 EX-001 同一轮类型收敛后消除 | 在册 |
| EX-004 | TYPE-02 | `cleaning/transform.py:288` 白名单函数分发 | `_ALLOWED_FUNCS` 的值类型是异构可调用对象，mypy 无法推断调用签名 | ka-ggg | v2.3.0 | 为白名单函数定义统一 `Callable` 协议并逐项标注 | 在册 |
| EX-005 | TYPE-04 | `cleaning/templates.py` 的 `Any` | JSON 反序列化边界，值类型在运行时校验（`_as_name_list` 等）；用具体类型会在解析前撒谎 | ka-ggg | v2.3.0 | 引入 `TypedDict`/`JsonValue` 别名，保留运行时校验 | 在册 |

- 现行在册 5 条【现行值】；其中 mypy 4 条、`Any` 1 条。
- v2.3.0 还债目标【目标值】：还掉至少 3 条；优先 EX-001~EX-004（同一轮类型收敛）。
- 新增豁免必须同时还掉更多旧豁免，否则不许发版（RAT-04）。

## 附录 B 规范变更记录表

| 规范版本 | 日期 | 变更条目 | 变更内容 | 原因 | 级别变化 |
| --- | --- | --- | --- | --- | --- |
| 1.0.0 | 2026-11-06 | 全文 | 首次制定，14 节正文 + 52 条细节总清单 + 附录 A/B/C | 任务简报要求建立常设工程规范并结合 v2.2.0 实测基线 | 初始 |

> 后续升版在此表追加；收紧与放宽都要写，放宽必须附评审结论。

## 附录 C 核查记录与实测基线

### C.1 环境

| 项目 | 值 |
| --- | --- |
| 日期 | 2026-11-06 |
| 操作系统 | Linux（容器） |
| Python | 3.12.3（`python3 --version`） |
| pytest | 9.2.0 |
| streamlit | 1.66.0 |
| pandas | 3.1.1 |
| openpyxl | 3.1.5 |
| Ruff | 0.17.4 |
| mypy | 2.4.1 |
| coverage | 7.17.2 |

### C.2 核查命令与输出

```text
$ wc -l app.py cleaning/*.py
   810 app.py
   224 cleaning/__init__.py
   117 cleaning/batch.py
    36 cleaning/errors.py
   204 cleaning/export.py
   376 cleaning/io.py
   118 cleaning/limits.py
   308 cleaning/rules.py
   231 cleaning/templates.py
   474 cleaning/transform.py
   264 cleaning/xlsx_scan.py
  3162 total
```

> Ruff 格式化前同一命令为 `855 app.py`、`2462 cleaning/` 总计；行数变化只来自 `ruff format`，无语义改动。

```text
$ python3 -m pytest --collect-only -q | tail -1
214 tests collected in 1.98s

$ python3 -m pytest -q -rs | tail -1
214 passed, 52 warnings in 20.11s
```

```text
$ ruff format --check .
16 files already formatted

$ ruff check .
All checks passed!
```

```text
$ mypy
cleaning/transform.py:50: error: Unsupported left operand type for ** ("object")  [operator]
cleaning/transform.py:71: error: No overload variant of "round" matches argument types "object", "int"  [call-overload]
cleaning/transform.py:280: error: Unsupported operand type for unary - ("object")  [operator]
cleaning/transform.py:288: error: Cannot call function of unknown type  [operator]
Found 4 errors in 1 file (checked 10 source files)
```

```text
$ coverage run -m pytest -q
214 passed, 52 warnings in 34.23s

$ coverage report --include='cleaning/*' -m
Name                    Stmts   Miss  Cover   Missing
-----------------------------------------------------
cleaning/__init__.py       18      0   100%
cleaning/batch.py          54      2    96%   103-104
cleaning/errors.py         14      0   100%
cleaning/export.py        124      2    98%   175, 198
cleaning/io.py            213     22    90%   112-115, 124-126, 162-164, 168, 195, 213, 220, 231-232, 240, 254-255, 276, 297, 352
cleaning/limits.py         59      5    92%   25-29
cleaning/rules.py         127      5    96%   85, 91, 96, 170, 260
cleaning/templates.py     151     15    90%   39, 45, 56, 64, 95, 134, 140-141, 143, 153, 218, 222, 224, 226, 230
cleaning/transform.py     281     24    91%   120, 160-161, 172-173, 178-179, 215-216, 255, 262, 276, 287, 304, 306, 325, 338-339, 341-342, 348-350, 381
cleaning/xlsx_scan.py     142      8    94%   124-125, 141-142, 150, 200, 231, 264
-----------------------------------------------------
TOTAL                    1183     83    93%
```

```text
$ pip install --no-cache-dir --no-deps -q .     # 在已有 .venv 中验证构建与包内容
$ python -c "import importlib.metadata as m; print(m.version('table-cleaner'))"
2.2.0
$ python -c "import cleaning; print(cleaning.__version__)"
2.2.0
```

### C.3 与要求书基线的差异

| 基线项 | 要求书基线 | 本次实测 | 差异说明 |
| --- | --- | --- | --- |
| `app.py` 行数 | 855 | 855（Ruff 前）/ 810（Ruff 后） | 格式化重排导致减少 45 行，无语义变更 |
| `cleaning/` 行数 | 约 2462 | 2462（Ruff 前）/ 2352（Ruff 后） | 同上；10 个文件含 `__init__.py` |
| 测试数 | 214 | 214 | 无差异；0 skipped |
| CI | 单 job，Python 3.12 | 测试矩阵 3.11/3.12 + Ruff 警告 job | 本版按简报扩矩阵，属计划变更 |
| 规范工具 | 零配置 | Ruff/mypy/coverage 已写入 `pyproject.toml` | 本版实际引入，不再处于“零配置” |
| 类型标注 | 部分存在 | mypy 基线 4 条错误 | 首次量化；未改逻辑消错 |
| 提交历史 | 1 个大提交 | 交付前 1 个；本次拆小步提交 | 按简报要求拆分 |
| 上限默认值 | 10 MB / 500,000 / 1,000 / 2,000,000 / 200 MB / 100,000 / 50,000 / 60 秒 | 同左 | 无差异，未改 `limits.py` |
| LICENSE | 无 | MIT | 本版新增 |
| PR 模板 | 无 | 有，7 组清单 | 本版新增 |

### C.4 核查范围

- 仓库根目录全部 Markdown：README、AUDIT_REPORT、TEST_REPORT、BUGS_AND_FIXES、CHANGELOG、RELEASE_CHECKLIST、FAQ、USER_GUIDE、SELLER_SUPPORT_GUIDE、THIRD_PARTY_LICENSES。
- 源码：`app.py` 与 `cleaning/` 的 10 个文件（`__init__`、`io`、`rules`、`transform`、`export`、`templates`、`batch`、`limits`、`errors`、`xlsx_scan`）。
- 测试：`tests/` 5 个文件；`.github/workflows/tests.yml`；`pytest.ini`；`requirements.txt`；`requirements-dev.txt`。
- 规划文档：`/home/hatch/workspace/your_files/table-cleaner-v210-plan/`、`/home/hatch/workspace/your_files/table-cleaner-v3-roadmap/`、`/home/hatch/workspace/your_files/engineering-standards-requirements/`。
