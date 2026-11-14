# table-cleaner v2.5.0 规划：交互性能、反馈与发现性

> 本文件是**规划文档，不是实现**：不改变任何代码、清洗语义、默认值、依赖或用户可见行为。
> 基线：`main` @ `83a66ee`（v2.4.0 发布提交，`tag v2.4.0`）。
> 依据：issue #9 正文、当前仓库代码、`ENGINEERING_STANDARDS.md`、v2.4.0 交付记录，以及本文第 2 节记录的实测。
> 目标版本：v2.5.0；上一版：v2.4.0；下一里程碑：v3.0（lazy/streaming、500 万行、行级血缘）。
> 数字口径：`【现行值】` = 已实测或仓库中已存在；`【目标值】` = 尚未达成，须按本文方法判定。

**阅读顺序建议：** 评审先看 1.3 的关键设计判断、1.4 的待确认决策、第 7 节验收和第 12 节的 pillow / demo 结论，再看第 5 节逐项方案。实现者从第 5、6 节开始。

**目录：** 0 文档信息与需求对照 · 1 结论摘要 · 2 基线（实测） · 3 范围与不做清单 · 4 统一验收口径 · 5 技术方案（逐项） · 6 任务拆分、依赖与批次 · 7 量化验收标准 · 8 测试计划 · 9 风险与回退 · 10 工期估算 · 11 发布门禁 · 12 pillow 与在线 demo 的单独结论 · 13 待人工确认的决策 · 14 参考文件。

## 0. 文档信息与需求对照

| 项目 | 内容 |
| --- | --- |
| 文档性质 | 版本规划（只规划，不写实现代码、不改清洗逻辑） |
| 目标版本 | v2.5.0（用户可见的交互与发现性改进版） |
| 代码基线 | `main` @ `83a66ee`，`cleaning.__version__ = "2.4.0"`，`tag v2.4.0` |
| 规划来源 | issue #9；当前仓库代码；本次实测（见第 2 节） |
| 产出形式 | **本文件的 PR**（`docs/V250_PLAN.md`），不在 issue 里贴全文；理由见 1.2 |
| 当前状态 | 待人工评审；评审确认前不进入实施、不合并、不打 tag |
| 与 v2.4.0 关系 | 清洗语义、默认值、上限默认值、公共 API、CLI / HTTP API、双引擎对齐结论全部冻结 |
| 与 v3.0 关系 | 大文件真提速（lazy/streaming、500 万行、行级血缘）不在本版，仍属 v3.0 |

**issue #9 各项与本文章节的对应：**

| issue 项 | 本文位置 | 规划结论摘要 |
| --- | --- | --- |
| P0-1 缓存（解析 + 流水线 + `st.fragment` 评估） | 5.1、7 G-02/G-05 | 解析用 `st.cache_data(scope="session")`；流水线用 session_state 单槽缓存；`st.fragment` 只用于导出区 |
| P0-2 计算反馈（spinner + 行数） | 5.2、7 G-06 | 仅在缓存未命中时显示；大表带行数/列数与用时 |
| P1-3 上传后「套用最近模板」 | 5.3、7 G-07 | 第 1 步显眼位置；走既有对比流程，不自动导出 |
| P1-4 2–3 个场景预设 | 5.4、7 G-08 | 按列名匹配，一行提示；只提示、不自动勾选 |
| P1-5 删行 >0 的对比区提示条 | 5.5、7 G-09 | 顶部「本次共删 N 行」+ 查看前 5 行样例 |
| P2-6 未生效规则导出前汇总 | 5.6、7 G-10 | 导出区集中列出，不阻塞导出 |
| P2-7 上传区上限标注与入口 | 5.7、7 G-11 | 上传区旁标注；高级设置补齐可调项；超限文案补入口 |
| P2-8 转数字/转日期抽样预览 | 5.8、7 G-12 | 前 5 值前→后 + 整列保留原文数量；只预览 |
| 硬项 9 覆盖率可测 | 5.9、7 G-13 | CI/本地可跑；公布 `cleaning/` 与 `app.py` 两个数；不设门禁 |
| 硬项 10 mypy 无新增 | 5.10、7 G-14 | 基线 4 条；建议顺手清零；不阻塞发布 |
| pillow 12.3.0 → 13.0.0 | 12.1、13 DC-7 | 建议纳入 v2.5 附带（独立 commit）；依据见 12.1 |
| 在线 demo | 12.2 | 只评估路线与成本；不占本版开发范围 |

## 1. 结论摘要

### 1.1 建议结论

建议按 issue #9 的定位推进 v2.5.0：**只动 `app.py` 的交互层与发现性，不改 `cleaning/` 的任何清洗语义、默认值、公共 API 或引擎/DSL 结论。** 本版交付六类用户可见改进：

1. **更快**：上传解析、检查结果、清洗结果按「文件内容指纹 + 规则」缓存；5 万行表的规则变更交互目标从约 0.7–0.9 秒压到 0.3–0.5 秒量级；导出按钮点击不再整页重算。
2. **更清楚**：计算中显示 spinner 与「正在重新计算…（5 万行 × 12 列）」，完成后显示用时；大表不再出现“静止空窗”。
3. **更好找**：上传后显眼位置一键套用最近模板；按列名识别的 2–3 个场景预设提示；删行后在对比区顶部提示总删除行数并可看前 5 行样例。
4. **少踩坑**：导出前汇总未生效规则；上传区标注当前上限并指向高级设置；转数字/转日期提供前 5 值预览与整列保留原文数量。
5. **可度量**：CI 与本地都能跑 coverage，TEST_REPORT 公布 `cleaning/` 与 `app.py` 两个语句覆盖率；mypy 保持无新增。
6. **可回归**：新增行为测试、50k 交互计时脚本、与 v2.4.0 的输出一致性金标；既有 557 项测试一项不删。

**本版不碰的东西（硬边界）：** 清洗判定规则、默认值、`Limits` 默认值、导出字节语义、模板 JSON 格式、`cleaning.__all__`、CLI/HTTP API、`cleaning/engine`、`cleaning/pipeline`、M2 双引擎对齐结论。

### 1.2 相对 issue 正文的范围调整建议

issue 的 10 项范围整体成立，建议**不删项**，只做三处边界澄清 + 一项可选建议：

| 建议 | 内容 | 理由 |
| --- | --- | --- |
| 增加前置项 | 先把界面测试的 `at.button[0]` / `at.checkbox[0]` 等位置选择器改为按标签/键定位（test-only） | 本版会在上传区、对比区插入新控件，位置索引会整体错位；不先加固，557 项测试会大面积误指向，无法证明“保护既有行为” |
| 明确 `st.fragment` 的落点 | 只把**导出区**包成 fragment；不把规则区/对比区拆成 fragment | 导出按钮是当前唯一“点一下重算整页”的大开销入口；fragment 隔离后可避免重复解析、检查与清洗 |
| 明确“逐字节一致”的口径 | CSV 比字节；XLSX 比解包后的内容条目（忽略 ZIP 时间戳与 `docProps/core.xml` 时间字段） | `to_xlsx_bytes` 受 openpyxl/zipfile 写入时间影响，同一进程隔 2 秒生成的两个 XLSX 字节就不同；不能把容器时间戳当成语义回归 |
| 建议顺手做 | mypy 存量 4 条清零（可选加分项） | 4 条都在 `cleaning/transform.py` 的窄类型边界；真实类型收窄即可，不改变运行行为，换来 CI 可以硬门禁 0 错误 |

**产出形式选择：** 采用 `docs/V250_PLAN.md` 的 PR，而不是把约 800 行全文贴进 issue。原因：规划需要逐行评审（尤其是第 5、7、12 节），PR 能保留行级评论与修改历史；issue #9 回帖只放结论摘要、PR 链接与待确认决策，避免把一个 issue 变成两份正文。

### 1.3 关键设计判断

1. **缓存分两层，作用域必须对**：解析用 `st.cache_data(scope="session")`（Streamlit 1.66 已支持 session 作用域，会话断开即清理）；流水线中间产物用 `st.session_state` 单槽缓存。这样既满足 issue 对 `st.cache_data` 的要求，又避免全局缓存把不同用户的上传内容留在同一进程里，也避免 `cache_data` 对 DataFrame 的重复拷贝放大内存。
2. **缓存键 = 内容指纹 + 解析参数 + 规则**：解析键含文件名、`sha256(data)`、表头行、工作表、`Limits`、超时；清洗键含指纹、表头行、工作表、`CleanOptions`、样例行数、超时。规则一变，清洗键必变；文件一换，解析与清洗键都变。
3. **进度条不能因缓存消失**：大表（> `CHUNK_THRESHOLD_ROWS`）首次计算仍走现有 `progress` 回调；缓存命中时不再伪造进度条，直接显示“已复用”说明。
4. **导出区用 fragment 是“局部重跑”，不是“局部状态”**：`@st.fragment` 只包生成/下载区；规则、模板、上传仍在主脚本，规则一变必触发整页重跑，旧的 `exports` 仍按现有签名机制失效。
5. **发现性只做提示，不做代选**：场景预设只输出一行文字；模板一键套用只还原规则、必须经过第 5 步对比，绝不自动点“生成下载”；删行提示只提供入口，不改变默认展示。
6. **测试先加固再改 UI**：先做 test-only 的标签定位重构（557 项必须原样通过），再开始插入控件；新增行为测试与 50k 计时脚本分开，计时脚本不进 CI 门禁（共享 runner 抖动大）。
7. **“逐字节一致”按可复现口径验收**：CSV 是字节级；XLSX 是“解包内容级”。这比笼统说“逐字节”更严格可执行，也如实说明容器层不可比的部分。
8. **pillow 是平台变更，不是普通依赖升级**：Streamlit 1.66 明确要求 `pillow<13`；升 pillow 13 必须同时升 Streamlit 1.67。虽然实测 1.67 + pillow 13 全量 557 项通过，但它改变的是 UI 框架版本，必须独立 commit、独立回滚。

### 1.4 需要人工确认的决策

| 编号 | 决策 | 本文建议 | 不确认的后果 |
| --- | --- | --- | --- |
| DC-1 | 缓存作用域与实现 | 解析 `st.cache_data(scope="session")`；流水线 `st.session_state` 单槽 | 若用全局 `cache_data` 缓存流水线，多用户内容会留在进程内，且内存峰值不可控 |
| DC-2 | 是否采纳 `st.fragment` | 只用于导出区；先做半日 spike，AppTest 通过后保留，否则回退为普通函数 | 不隔离则点“生成下载文件”仍会重算解析/检查/清洗，5 万行以上导出体验无改善 |
| DC-3 | 一键模板的“最近”定义 | 本会话内最近一次保存/套用/上传的模板；只存规则，不跨会话 | 若要求跨会话/服务端持久化，会引入存储与隐私面，超出本版范围 |
| DC-4 | 场景预设的呈现 | 只按列名匹配并输出一行提示，最多同时提示 2 个场景 | 若允许自动勾选，属于默认值/行为变更，违反 issue 的“只提示不自动勾选” |
| DC-5 | 覆盖率范围 | `source = ["cleaning", "app.py"]`，分别公布两个百分比，不设阈值 | 只公布 `cleaning/` 会掩盖本版几乎全在 `app.py` 的事实；并入总数又会和 94%/95% 基线不可比 |
| DC-6 | mypy 处理 | 建议清零 4 条并让 CI 硬门禁 0；若时间不足，则 CI 只断言“≤4 且位置不变” | 不接 CI 则“无新增”只能靠人工；用 `Any`/`ignore` 压掉则违背棘轮原则 |
| DC-7 | pillow 13 是否随 v2.5 | **建议纳入 v2.5 附带（独立 commit/PR），条件见 12.1** | 拆出单独成版会让 39 条漏洞多留一个发布周期；但需要接受 Streamlit 1.66→1.67 的平台回归面 |
| DC-8 | 在线 demo | 本版只评估，不开发；建议 v2.6 再决定 | 若本版要做，至少 +1–3 人日与托管账号/费用，且会扩大隐私与运维面 |
| DC-9 | 文档产出形式 | 本文 PR；issue #9 只回帖摘要与链接 | 全文贴 issue 会失去行级评审与版本历史 |

### 1.5 v2.5.0 的独立价值

**用户可见价值（本版的主体）：** 同样的清洗结果，交互等待更短、计算状态可见、常用模板和场景建议更容易发现、删行与未生效规则不再“悄悄发生”。这些改进不改变任何清洗结果，因此用户不需要重新学习规则。

**工程价值：**

| 交付物 | 解决什么 | 若 v2.5 之后暂停仍有什么用 |
| --- | --- | --- |
| 内容指纹 + 双层缓存 | 5 万行以上反复交互的等待 | 任何后续 UI/大表工作都可复用的缓存边界与失效规则 |
| 50k AppTest 计时脚本 | 性能从“感觉”变成可复测数字 | v3.0 大表改造的对照基线 |
| 金标输出回归 | 证明“只改 UI、不改结果” | 后续任何重构都可复用同一批 fixture 与哈希 |
| 标签定位后的界面测试 | 新控件不再靠位置索引 | 后续 UI 迭代的测试稳定性 |
| 覆盖率/mypy 门禁 | 本版质量可度量 | v3.0 的工程门禁起点 |
| 发现性组件（模板入口/预设/删行提示） | 新用户少点几次、少踩几个坑 | 可独立复用/回退的 UI 小模块 |

**结论：** v2.5.0 不是“性能大版本”，而是**在不承担语义风险的前提下，把现有交互做顺、把发现性补齐、把质量度量补上**的用户可见改进版。大文件真提速仍留 v3.0。

## 2. 基线（本次实测）

### 2.1 代码、测试与 CI 现状【现行值】

实测日期：2026-11-14；环境：Linux 容器（4 核 / 7934 MB）、Python 3.12.3、streamlit 1.66.0、pandas 3.1.1、numpy 2.5.4、openpyxl 3.1.5、pytest 9.2.0、ruff 0.17.4、mypy 2.4.1、coverage 7.17.2。

| 项目 | 现行值 | 说明 |
| --- | --- | --- |
| 发布提交 | `83a66ee` | `main` 当前提交；`tag v2.4.0` 存在 |
| 版本 | `2.4.0` | `pyproject.toml` / `cleaning.__version__` 一致 |
| `app.py` | 810 行 | 单文件 Streamlit 入口，只调 `cleaning` 公共 API |
| `cleaning/` + `engine/` + `api/` | 6,538 行 | 本次规划不改其行为 |
| `tests/` | 7,219 行 | 18 个测试文件 |
| 全量测试 | **557 passed，0 skipped，74.56 s** | `coverage run -m pytest -q -rs` 实测 |
| `cleaning/` 覆盖率 | **95%**（3131 statements / 142 missed） | 与 v2.4.0 CHANGELOG 一致 |
| `app.py` 覆盖率 | **89%**（342 statements / 36 missed） | 仅 `tests/test_app_ui*.py` 32 项、`--concurrency=thread` 实测；CI 目前不测 |
| Ruff | `ruff format --check .`：47 files already formatted；`ruff check .`：All checks passed | 与 v2.4.0 一致 |
| mypy | **Found 4 errors in 1 file（checked 22 source files）** | `cleaning/transform.py` 第 50/71/280/288 行，EX-001~EX-004 存量 |
| CI | 2 个 job：pytest（3.11/3.12 矩阵）+ ruff | 无 coverage、无 mypy job |
| PR 纪律 | PR 模板要求 CI 全绿 + 清单全勾 + CHANGELOG；人工合并 | 本规划 PR 不合并、不打 tag |

**现行默认上限（v2.5.0 不得改变）【现行值】：** 上传 10 MB、500,000 行、1,000 列、2,000,000 单元格、XLSX 解压后 200 MB、单次超时 60 秒（0 = 不限）、分块阈值 100,000 行 / 块 50,000 行。全部定义在 `cleaning/limits.py`，测试 `tests/test_v23_limits.py` 守着。

### 2.2 5 万行交互耗时基线【现行值】

固定 fixture：50,000 行 × 12 列，种子 20261114，CSV 4.3 MB（含重复订单号、空备注、金额/单价/折扣文本列、日期与状态列）。用 `streamlit.testing.v1.AppTest` 真实跑 `app.py`，单次 `run()` 计时（本容器，非 CI）：

| 操作 | 耗时 |
| --- | ---: |
| 首次上传 + 解析 + 渲染 | 0.704 s |
| 无改动的普通 rerun | 0.682 s |
| 勾选「删除完全重复的行」后的 rerun | 0.742 s |
| 再勾选「删除指定列中有空值的行」后的 rerun | 0.896 s |

同一 fixture 的纯函数耗时（3 次取最小）：

| 函数 | 耗时 |
| --- | ---: |
| `load_table`（解析） | 0.192 s |
| `summarize` | 0.022 s |
| `find_missing` | 0.004 s |
| `find_suspicious` | 0.346 s |
| `clean_table`（无规则） | 0.001 s |
| `clean_table`（5 条规则 + 样例 5 行） | 0.122–0.147 s |

**说明：** 这组数字说明 baseline 在 12 列时已经接近 1 秒，离 issue 的“≤1 秒量级”没有余量；耗时大头是 `find_suspicious` 与解析，规则清洗本身不是瓶颈。缓存应优先消掉“每次 rerun 都重复解析 + 重复检查”，而不是优化 `clean_table`。本机（4 核 8 GB）比部分手机快，因此验收同时设“绝对上限 + 相对改进”两条，避免在快机器上“什么都不做也通过”。

### 2.3 pillow / Streamlit 现状【现行值】

- 本仓库**没有直接 import PIL/pillow**（`grep -rn "PIL\|pillow\|Image" --include='*.py'` 为 0）；pillow 只是 Streamlit 的传递依赖。
- `requirements-dev.txt` 固定 `streamlit==1.66.0`；该版本元数据要求 **`pillow>=9.2.0,<13`**，因此当前只能装 pillow 12.3.0。
- `streamlit==1.67.0` 的元数据要求 **`pillow>=9.2.0,<14`**，可装 pillow 13.0.0。
- 兼容性实测：新建干净 venv，先按 `requirements-dev.txt` 安装，再升级到 `streamlit==1.67.0` + `pillow==13.0.0`，`pip check` 无冲突，全量 **557 passed，0 skipped**（55.42 s）。
- 未执行：Python 3.11 本地实跑（本机只有 3.12）、真实浏览器人工点击、pip-audit 复扫（本机未装 pip-audit，按 v2.4.0 记录 39 条漏洞为已知）。

### 2.4 当前代码对本版的硬约束

- `app.py` 只能通过 `cleaning/__init__.py` 的公共导出调用清洗逻辑（`tests/test_v23_public_api.py` 守着）；新增缓存/预览/预设代码也必须是 UI 层，不新增 `cleaning` 导出。
- `CleanOptions` / `ColumnOps` 是唯一规则载体；缓存键必须包含它们，不能另拼一套规则状态。
- 上传内容不落盘、不发网络请求；`st.cache_data(scope="session")` 与 session_state 都只留在内存，会话结束释放。
- 现有下载失效机制：`show_export` 用 `(len, 列名, dtype, sheet_name, repr(options), formula_mode, number_mode, cleaned 内容哈希)` 做签名；本版新增源文件指纹，不能削弱。
- `st.fragment` 要求 Streamlit ≥1.37；`requirements.txt` 已是 `>=1.50,<2`，1.66/1.67 均支持。
- 测试大量使用位置选择器（`at.button[0]`、`at.checkbox[0]`、`at.multiselect[0]`、`at.dataframe[0]` 等，共约 38 处），插入控件前必须加固。

## 3. 范围与不做清单

### 3.1 本版要做（P0 / P1 / P2 + 硬项）

| 优先级 | 项 | 用户可见结果 |
| --- | --- | --- |
| P0 | 1. 缓存 + `st.fragment` 评估 | 反复交互不再重复解析/检查/清洗；导出点击只重跑导出区 |
| P0 | 2. 计算反馈 | 计算中有 spinner 与行数提示，完成有耗时说明 |
| P1 | 3. 上传后一键套用最近模板 | 同类新表少点几步，但必须看对比后才导出 |
| P1 | 4. 2–3 个场景预设提示 | 按列名给出「像电商订单/快递对账/库存」的建议，不自动勾选 |
| P1 | 5. 删行 >0 的对比区提示条 | 直接看到“共删 N 行”，可看前 5 行样例 |
| P2 | 6. 未生效规则导出前汇总 | 导出前知道哪些规则没生效，避免误以为已应用 |
| P2 | 7. 上传区上限标注与入口 | 看得到上限，知道去高级设置调，默认值不变 |
| P2 | 8. 转数字/转日期抽样预览 | 前 5 个值前→后、整列保留原文数量，只预览不改结果 |
| 硬项 9 | 覆盖率可测 | CI 与本地可跑，TEST_REPORT 公布 `cleaning/` 与 `app.py` 覆盖率 |
| 硬项 10 | mypy 无新增 | 基线 4 条；建议清零，不阻塞发布 |
| 附带 | pillow 13（独立 commit，见 12.1） | 修掉依赖扫描的 39 条命中；由人工拍板 |
| 发布 | 版本 2.5.0、CHANGELOG、TEST_REPORT、README/USER_GUIDE/FAQ 同步 | 发布记录与实际一致 |

### 3.2 明确不做

**清洗与数据能力：**

- 不改 `cleaning/` 的判定规则、默认值、`Limits` 默认值、导出语义、模板/DSL 格式、`__all__`。
- 不做转换语义放宽：货币/百分号/混合日期直转、自动猜时区、自动修数据，全部不做。
- 不做 Polars 转默认、不新开引擎开关、不改 M2 已冻结的对齐/回落结论。
- 不做 lazy/streaming、500 万行、行级血缘、可复现运行记录——仍属 v3.0。
- 不做服务端模板持久化、账号体系、数据库、外部 API、上传落盘。

**交互层边界：**

- 不自动勾选预设、不自动生成导出、不自动套模板、不自动改规则。
- 不重做整体视觉/版式；只加提示、入口、状态与局部容器，保持现有手机优先样式。
- 不改批量区、模板 JSON 格式、CLI/HTTP API；批量若未来也用 fragment，另行评估。

### 3.3 推迟项的理由与影响

| 推迟项 | 理由 | 影响 |
| --- | --- | --- |
| 大文件 lazy/streaming、500 万行 | 需要原生帧/流式 IO，边界与导出要重做；v3.0 范围 | 本版不承诺 500 万行与内存下降；只做 5 万行交互与缓存 |
| 行级血缘 | 与 v3.0 引擎/DSL 血缘一起做，避免两套实现 | 本版仍只有步级计数 + 前 5 行样例 |
| 在线 demo | 涉及托管、隐私、鉴权与运维，不是本版定位 | 本版只评估，见 12.2 |
| 跨会话模板 | 需要服务端存储与隐私设计 | 本版模板仍只在会话内；可下载 JSON 自行保存 |
| 自动场景套用 | 违反“只提示不自动勾选” | 本版只提示，用户自己勾选 |

## 4. 统一验收口径

### 4.1 交互性能口径

- **固定 fixture**：50,000 行 × 12 列，种子 20261114，CSV 4.3 MB；包含重复订单号、空备注、金额/单价/折扣文本列、日期/状态列；默认上限内。
- **固定 harness**：`benchmarks/app_interaction.py`（新增，UI 计时脚本，不进 CI 门禁）。用 `AppTest.from_file` 跑真实 `app.py`，记录：
  1. 冷启动 → 上传并 run（冷路径）；
  2. 无改动 rerun；
  3. 依次勾选 2 个行规则、选择 2 个空值/去重列；
  4. 在导出区切换公式防护模式；
  5. 点一次「生成下载文件」（单独记录，不计入 1 秒门槛）。
- **判定**：每个 warm 交互取 5 次中位数。
  - 【目标值】warm 交互中位数 **≤ 1.0 s**；
  - 【目标值】warm 交互中位数相对 v2.4.0 同 harness **至少改善 25%**（防“快机器上本来就过”）；
  - 【目标值】冷上传 + 首次渲染 **≤ 2.0 s**（本基线 0.704 s，留余量）；
  - 导出点击：只要求“不重复执行解析/检查/清洗”（用计数探针证明），不设 1 秒门槛（XLSX 生成本身可能数秒）。
- **同口径复测**：同一台机器、同一 fixture、同一命令，先在 `83a66ee` worktree 跑 baseline，再在 v2.5 分支跑；结果写入 TEST_REPORT，注明环境、日期、命令。CI 只跑行为测试，不跑 50k 计时（共享 runner 抖动）。

### 4.2 结果一致性口径（“与 v2.4.0 一致”）

- **CSV：字节级一致。** fixture 集 × 3 种公式防护模式（quote/zwsp/off）的 `sha256` 必须相同。
- **XLSX：解包内容级一致。** 忽略 ZIP entry 时间戳与 `docProps/core.xml` 里的创建/修改时间；其余条目字节与 `openpyxl` 读回值/类型必须一致。原因：`to_xlsx_bytes` 不固定写入时间，同一进程隔 2 秒生成的两个文件字节已不同（本次实测确认）；这是容器元数据差异，不是清洗/导出语义差异。
- **报告：字段级一致。** `CleanReport.steps`、`removed`（样例行与删除原因）、`notes` 必须与 v2.4.0 相同。
- **App 级：** 至少 1 个 AppTest 端到端用例上传固定 fixture、设规则、点生成，导出的 CSV 必须命中同一金标哈希。
- **金标生成**：在 `83a66ee` 的独立 worktree 运行 `benchmarks/` 下的采集脚本，生成 `tests/fixtures/v250/golden.json` 并提交；v2.5 测试只读金标，不依赖网络或旧工作区。

### 4.3 交互与发现性口径

| 项 | 判定 |
| --- | --- |
| 模板一键套用 | 从上传到“生成下载文件”可用，操作计数 ≤ 3（上传、套用、生成）；套用后不得自动出现 `exports` / 下载按钮 |
| 场景预设 | 只出现文字提示；提示前后所有 checkbox/multiselect/radio 的值不变；最多 2 条提示 |
| 删行提示 | 删除行数 > 0 时显示「本次共删 N 行」；点击后展示 ≤5 行样例与删除原因；0 行时不显示 |
| 未生效汇总 | 覆盖空目标、缺失列、计算列失败、格式错误等六类；有效规则不得误报；只提示不阻塞导出 |
| 上限标注 | 上传区旁显示当前五个上限；任一超限错误附近出现「高级设置」入口提示；默认值不变 |
| 转换预览 | 前 5 个非空值前→后；整列保留原文数量；只读，不改变 `options` 或导出结果 |

### 4.4 下载失效不变量

以下任一变化后，旧的 `exports` 必须被清除、下载按钮消失，且重新生成的内容对应新状态：

- 勾选/取消任一清洗规则、改任一目标列；
- 改表头行、换工作表、换上传文件；
- 套用模板（规则变化）；
- 改公式防护模式、编号模式；
- 改列操作（改名/删列/顺序/转数字/转日期/计算列）。

### 4.5 覆盖率与 mypy 口径

- `coverage run --concurrency=thread -m pytest -q -rs`（`pyproject.toml` 设 `source = ["cleaning", "app.py"]`）。
- 分别报告：
  - `coverage report --include='cleaning/*'`：基线 95%；
  - `coverage report --include='app.py'`：基线 89%。
- **只公布，不设 fail-under**；CI 与本地命令一致。
- mypy：基线 4 条；发布前 `mypy` 错误数不得 >4；若清零，则 CI 硬门禁 0。

## 5. 技术方案（逐项）

> 本版的生产代码改动集中在 `app.py`；`cleaning/` 不改清洗逻辑（唯一例外：发布时 `cleaning/__init__.py` 只改 `__version__`；若人工选择清零 mypy，`cleaning/transform.py` 只做类型收窄，不改运行行为）。依赖变更单独成 commit（12.1）；新增脚本在 `benchmarks/`，新增测试在 `tests/`，新增文档在 `docs/`。

### 5.1 P0-1 缓存与 `st.fragment`

**现状：** `main()` 每次 rerun 都重新 `load_table`、`summarize`、`find_missing`、`find_suspicious`、`clean_table`；点「生成下载文件」也会整页重跑。50k×12 时 `find_suspicious` ≈0.35 s、解析 ≈0.19 s，是主要重复开销。

**对应文件/函数：** `app.py` 的 `get_source`、`show_checks`、`show_comparison`、`show_export`、`main`。

**改法：**

1. **文件指纹**：对上传字节算 `hashlib.sha256(data).hexdigest()`；按 `(upload.file_id, upload.size)` 在 session_state 记住已算指纹，避免每次 rerun 重算（`UploadedFile.file_id` 在 Streamlit 1.66 存在；不存在时退回每轮重算，10 MB 约几十毫秒）。
2. **解析缓存**：
   ```python
   @st.cache_data(scope="session", max_entries=2, show_spinner=False)
   def _parse_cached(filename, digest, _data, header_row, sheet, limits, timeout):
       return cleaning.load_table(filename, _data, sheet=sheet, header_row=header_row,
                                  timeout=timeout, limits=limits)
   ```
   - `_data` 用下划线前缀跳过对 4–10 MB 字节的二次哈希；`digest` 参与键，保证内容变化必 miss。
   - 键包含 `header_row`、`sheet`、`limits`、`timeout`；换表头/换表/改上限/改超时都重新解析。
   - `max_entries=2`：保留当前表与上一张表（多工作表来回切换不重复解析）；会话结束由 Streamlit 清理。
   - 大于 8 MB 的文件跳过解析缓存，直接 `load_table`，避免会话内多份大 DataFrame 叠加（阈值写死常量并在代码注释说明）。
3. **检查缓存**：session_state 单槽 `{"digest": ..., "stats": summarize(df), "missing": find_missing(df), "suspicious": find_suspicious(df)}`；`show_checks` 改为接收这些值。检查结果只依赖数据，不依赖规则。
4. **流水线缓存**：session_state 单槽 `{"key": (digest, header_row, sheet, options, sample, timeout), "report": CleanReport, "stats_after": summarize(cleaned), "elapsed": float}`。
   - 命中：直接使用，不调用 `clean_table`，显示“已复用本次会话的计算结果（规则未变）”。
   - 未命中：调用现有 `clean_table`，保留 >10 万行的 `progress` 回调；完成后写缓存并显示用时。
   - 单槽即“session 级”复用，不保留历史规则结果，内存有界。
5. **导出区 fragment**：
   ```python
   @st.fragment
   def show_export(...):  # 原函数整体加装饰器
       ...
   ```
   - 点击「生成下载文件」只重跑 fragment，不重跑上传/检查/清洗；导出签名沿用现有机制，并**新增源文件 `digest` 与文件名**，避免“同内容不同文件名”时旧下载名残留。
   - 规则/文件/模板/表头任一变化仍触发整页 rerun，签名不一致 → 旧 `exports` 清除。
6. **失效矩阵**：见 4.4；测试逐项覆盖。切换文件时同时清掉旧 digest 的 session 缓存槽，防止拿旧结果。
7. **`st.fragment` 评估结论**：本机 AppTest 小探针确认 fragment 内按钮点击不会重跑主脚本（full-run 计数保持 1）；因此本版**有条件采纳**，仅用于导出区。若实施中 AppTest 出现元素树/下载按钮异常且无法在 1 天内解决，则回退为普通函数，只保留解析/清洗缓存（性能目标仍可由缓存满足），并在 PR 记录评估结论。

**风险与回退：**

| 风险 | 缓解 | 回退 |
| --- | --- | --- |
| 缓存串数据（规则/文件/表/表头变化后仍用旧结果） | 键含全部输入；失效矩阵测试；换文件清槽 | 移除缓存装饰器/单槽，恢复每次重算 |
| 多用户内容留在进程 | `scope="session"` + session_state；不落盘 | 关闭解析缓存，直接 `load_table` |
| 内存峰值上升 | `max_entries=2`、>8 MB 绕过、单槽流水线；PR 记录 10 MB fixture 峰值 | 降 `max_entries=1` 或全绕过 |
| 大表进度条消失 | 仅未命中时走 progress；命中不伪造 | 大表禁用缓存 |
| fragment 与 AppTest/下载按钮不兼容 | 半日 spike，逐项跑现有下载失效测试 | 去掉 `@st.fragment`，保留缓存 |
| 测试跨用例缓存污染 | `conftest.py` autouse `st.cache_data.clear()`（仅测试） | — |

**验收：** 见 G-02/G-04/G-05；5 万行 warm 交互中位数 ≤1.0 s 且相对 baseline ≤−25%；缓存命中不再调用 `load_table`/`clean_table`（计数探针）；所有下载失效用例通过。

### 5.2 P0-2 计算反馈

**对应文件/函数：** `app.py` 的 `show_comparison` / 新增 `_render_compute_status`。

**改法：**

- 缓存未命中且开始清洗时：
  - 行数 ≤ 20,000：`with st.spinner("正在重新计算…")`；
  - 行数 > 20,000：`with st.spinner(f"正在重新计算…（{rows:,} 行 × {cols:,} 列）")`；
  - >100,000 行继续显示现有分块进度条与 `done/total` 文案。
- 缓存命中：不显示 spinner，显示 `st.caption("已复用本次会话的计算结果（规则未变）。")`。
- 计算完成（未命中）：显示 `st.caption(f"计算完成：{rows:,} 行 × {cols:,} 列，用时 {elapsed:.1f} 秒。")`，让“慢/快”有可核对的数字。
- 导出生成沿用现有 spinner，文案改为「正在生成下载文件…（大表 XLSX 可能较慢）」。

**风险与回退：** 额外 caption 可能影响按位置读 `at.caption` 的测试（现有测试很少按位置读 caption）；回退只需删除新增 caption/spinner。文案过长在手机上换行，保持一行短句。

**验收：** G-06；大表首算有 spinner + 行数；缓存命中无 spinner 且出现“已复用”。

### 5.3 P1-3 上传后「套用最近模板」

**对应文件/函数：** `app.py` 的 `show_templates`、`apply_pending_template`、`main`；新增 `render_recent_template_entry(columns)`。

**改法：**

- session_state 新增 `_最近模板 = {"名称": str, "规则": dict}`；在「保存当前规则为模板」「套用」「上传模板 JSON」三处写入。
- 文件载入且列名确定后（第 1 步概览之后、检查结果之前）显示一个 `st.container(border=True)`：
  - 标题「套用最近模板」；
  - 一行摘要：模板名 + `cleaning.summarize_rules` 的规则摘要；
  - `st.button("一键套用「{名称}」", key="一键套用最近模板")`；
  - 文案明确「只还原规则；不会自动生成下载，请在下方第 5 步确认对比」。
- 点击：把模板规则写入既有 `_待应用模板`，`st.rerun()`；下一轮 `apply_pending_template()` 在控件创建前套用并按当前列过滤（复用现有机制）。
- 没有最近模板时不显示该容器；不新增持久化。
- 规则变化会自然触发下载失效与清洗重算。

**风险与回退：** 套用发生在控件创建前（既有排序约束）；模板来自旧文件、列名对不上时用 `filter_for_columns` 的提示，不静默套错；按钮插入位置会移动测试索引（由 3.1 的前置项解决）。回退：删除容器与 `_最近模板` 写入。

**验收：** G-07；模板已保存的前提下，同一会话内“上传同类新表 → 一键套用 → 生成下载”共 3 次操作即可得到可用下载；套用后 `exports` 不存在；对比区显示新规则的效果。

### 5.4 P1-4 场景预设提示

**对应文件/函数：** `app.py` 新增常量 `SCENARIO_PRESETS` 与纯函数 `match_scenario_hints(columns)`；`show_row_rules` 顶部渲染。

**改法：**

- 3 个预设，每个由「场景名 + 关键列同义词 + 建议动作」组成：
  - **电商订单**：订单号/订单编号/单号、金额/实付金额/付款金额、收货人/客户/买家、收货地址/地址、下单时间/支付时间、商品/数量；
  - **快递对账**：运单号/快递单号/物流单号、寄件时间/发货时间、重量、运费/快递费、目的地/收件地址、签收状态；
  - **库存**：SKU/商品编码/货号、仓库/仓位、库存/库存数量/可用库存、安全库存、更新时间/盘点时间。
- 归一化列名（去空白、全角转半角、大小写不敏感、去掉常见后缀如“（元）”）后匹配；命中 ≥2 个关键列才算识别，按命中数排序，最多显示 2 条。
- 每条一行文字，例如：`识别到类似「电商订单」：命中订单号、金额；常见处理是按订单号去重、金额转数字（仅提示，不会自动勾选）。`
- 只读文本，不写任何 widget 状态；放在第 3 步顶部，紧挨规则控件。

**风险与回退：** 列名同义词有限，可能漏报/误报；误报只是提示，不改变结果；同义词表放模块常量，后续可加。回退：删除提示渲染与常量。

**验收：** G-08；3 个 fixture 各命中对应场景；无匹配时不显示；提示前后 checkbox/multiselect 值完全不变。

### 5.5 P1-5 删行 >0 的对比区提示条

**对应文件/函数：** `app.py` 的 `show_comparison`。

**改法：**

- 现有 `show_comparison` 顶部（“5. 清洗前后对比”之后）计算 `total_deleted = sum(report.steps.values())`（与 `len(df) - len(cleaned)` 相等；列变换不删行）。
- `total_deleted > 0` 时：
  - `st.warning(f"本次共删 {total_deleted:,} 行。")`；
  - 同一区域提供「查看被删样例（前 5 行）」按钮；点击设置 `规则_样例 = True` 并 rerun，下一轮以 `sample=5` 计算（缓存键含 sample）；
  - 样例表沿用现有“被删掉的行（最多 5 行，含删除原因）”表格与说明文案。
- `total_deleted == 0` 时不显示提示条；用户已打开样例且无删除时保留现有 info。
- 删除数用 `report.steps` 汇总，保留每一步的明细表。

**风险与回退：** sample=5 会多算 removed 样例（50k 行实测清洗 0.12–0.15 s，样例开销很小）；缓存键区分 sample；`规则_样例` 与旧 checkbox 语义兼容。回退：恢复原 checkbox。

**验收：** G-09；删 2 行显示「本次共删 2 行」；点击后样例 ≤5 行、删除原因正确；0 删除不显示；改规则后数字更新。

### 5.6 P2-6 未生效规则导出前汇总

**对应文件/函数：** `app.py` 新增纯函数 `collect_ineffective_rules(options, columns, problems, report_notes, template_notes)`；`show_export` 顶部渲染。

**改法：**

- “未生效”严格定义为**配置层面没有作用对象/被跳过**，不是“规则有效但本次删了 0 行”。覆盖：
  1. 勾了「删除空值行」但没选列；
  2. 勾了「按列去重」但没选列；
  3. 删列/列顺序/转数字/转日期/重命名引用了不存在的列；
  4. 计算列未生成（重名、表达式错误、非数字列等，从 `report.notes` 识别）；
  5. 文本格式错误导致整段规则未应用（`problems`）；
  6. 套用模板时因列名不存在被过滤掉的条目（`filter_for_columns` 的 notes）。
- 在「6. 生成并下载清洗结果」标题下、生成按钮之前显示一条 `st.warning("以下规则本次没有生效：…；导出的结果不会包含它们。")`；生成按钮与结果区都保留这条提示。
- 只提示，不阻塞；有效规则不得误报。`report.notes` 用固定前缀匹配，避免把“工作表跳过”等无关说明算进来。

**风险与回退：** 误报会烦人，测试必须覆盖“有效规则无 warning”；`report.notes` 文案若未来改动，匹配需集中在一处。回退：删除提示块。

**验收：** G-10；6 类各一条触发、有效规则零误报；改好配置后提示消失；导出结果不变。

### 5.7 P2-7 上传区上限标注与入口

**对应文件/函数：** `app.py` 的 `sidebar_settings`、`get_source`。

**改法：**

- 上传区下方 caption：`当前上限：单文件 {mb} MB · {rows:,} 行 · {cols:,} 列 · {cells:,} 单元格 · XLSX 解压后 {xlsx_mb} MB。可在左侧「高级设置」临时调大（只影响本次会话）。`
- 高级设置 expander 在现有“上传上限 / 行数 / 超时”之外补齐：列数上限、单元格总数、XLSX 解压上限（MB）；全部走 `Limits._replace(...)`，不写全局、不改默认值。
- 超限文案入口：`get_source` 捕获 `TableError` 后，若消息包含“上限”，追加 `st.info("可在左侧「高级设置」里临时调大上限（只影响本次会话）。")`。这样无需改 `cleaning/` 的文案（包括只提环境变量的 XLSX 解压提示），也满足“超限文案补入口”。
- 同步更新“使用说明与判定规则”里的资源限制段落，写清可调项；默认值文案保持与 `Limits` 一致。

**风险与回退：** 新增 sidebar 控件可能影响按位置取 `number_input` 的测试（现有测试按标签取表头行，风险低）；上限调大后内存风险由用户承担，保留现有提醒。回退：删除新增输入与 caption/hint。

**验收：** G-11；默认 caption 数字与 `Limits` 一致；改 sidebar 后上传/清洗使用新值；超限错误附近出现高级设置提示；`test_v23_limits` 全过。

### 5.8 P2-8 转数字/转日期抽样预览

**对应文件/函数：** `app.py` 的 `show_column_rules`（改为接收 `df` 与 `digest`）；新增 `render_conversion_preview(df, ops, digest)`。

**改法：**

- 当「转成数字的列」或「转成日期的列」非空时，在对应 multiselect 下方渲染预览面板（`st.expander("转换预览（只预览，不改结果）", expanded=False)` 或直接小块）。
- 对每个选中列：
  - 用 `cleaning.convert_to_number` / `cleaning.convert_to_date` 在**副本**上算（不写回 df/options）；
  - 取样：该列前 5 个非空值；展示「原值 → 转换后 / 保留原文」；
  - 统计：整列非空值中失败（保留原文）的数量；
  - 一行 caption：`转数字「金额」：前 5 个中 4 个可转换；整列 1 个值将保留原文。`
- 结果按 `(digest, 列名, 类型)` 缓存在 session_state，规则不变不重算；混时区日期整列失败时显示“整列保留原文”而不是抛错。
- 计算列预览已有（现有预览），本项只补转数字/日期，不改清洗结果。

**风险与回退：** 预览统计与真实清洗必须用同一函数（`convert_to_*`），避免两套口径；超长文本截断显示；大表只多算选中列。回退：删除预览面板。

**验收：** G-12；混合列 `00123`/`A002` 显示 123 与保留原文；整列失败计数正确；预览前后 `CleanOptions`/导出结果不变；混时区不崩。

### 5.9 硬项 9：覆盖率可测

**对应文件：** `pyproject.toml`、`.github/workflows/tests.yml`、`TEST_REPORT.md`。

**改法：**

- `[tool.coverage.run]`：`source = ["cleaning", "app.py"]`，`concurrency = ["thread"]`（AppTest 在脚本线程里跑 `app.py`；不加 thread 时 app.py 记不到）。
- CI 的 pytest job 用：
  ```text
  coverage run -m pytest -q -rs
  coverage report --include='cleaning/*'
  coverage report --include='app.py'
  ```
  两个 report 只打印，不设 `fail_under`；job summary 里贴两个百分比。
- 本地同一命令写入 TEST_REPORT；v2.5 发布记录公布实测值（基线：cleaning 95%、app.py 89%）。
- 不把覆盖率并入一个总数（会与历史 94%/95% 不可比）；不把 app.py 排除在外（本版主要改它）。

**风险与回退：** coverage 让测试慢约 10–20%；`concurrency=thread` 可能带来少量噪声；两个 report 步骤任一失败只影响可见性，不影响合并（但命令本身要能跑通）。回退：移除 coverage 步骤，保留原 pytest。

**验收：** G-13；CI 与本地命令都能跑；TEST_REPORT 有日期/环境/两个百分比；无 fail-under。

### 5.10 硬项 10：mypy 无新增（建议清零）

**对应文件：** `cleaning/transform.py`（可选）、`.github/workflows/tests.yml`、`TEST_REPORT.md`。

**现状 4 条：**

```text
transform.py:50: Unsupported left operand type for ** ("object")        [operator]
transform.py:71: No overload variant of "round" matches ("object","int") [call-overload]
transform.py:280: Unsupported operand type for unary - ("object")      [operator]
transform.py:288: Cannot call function of unknown type                 [operator]
```

**改法（推荐）：**

- 用真实类型收窄解决：`_safe_pow` 在确认 `int` 后 `cast`；`_safe_round` 用 `SupportsRound`/重载或 `cast`；一元负号用 `cast` 到支持 `__neg__` 的类型；`_ALLOWED_FUNCS` 标注为 `dict[str, Callable[..., object]]`。不引入 `Any`、不加 `# type: ignore` 掩盖。
- CI 增加 `mypy` 步骤，目标 0 错误；运行行为由现有 557 项 + 计算列测试保护。
- 若实施时发现类型收窄会牵动语义（预期不会），退回基线方案：CI 脚本断言错误数 ≤4 且错误位置/代码与上述 4 条完全一致，出现第 5 条即失败。

**风险与回退：** 类型改动只影响静态检查，不改运行时；若 mypy 2.4.1 对 `cast` 仍报错，按基线方案保留 4 条。**该项不阻塞 v2.5 发布。**

**验收：** G-14；`mypy` 0 或 =4 且无新增；Ruff/557 全过。

### 5.11 文档、版本与发布准备

**对应文件：** `CHANGELOG.md`、`TEST_REPORT.md`、`README.md`、`USER_GUIDE.md`、`FAQ.md`、`pyproject.toml`、`cleaning/__init__.py`。

- 所有功能 PR 合并后，单独一个发布准备 PR：版本升 `2.5.0`（四处一致：`pyproject.toml`、`cleaning.__version__`、CHANGELOG、git tag），更新 README 的版本段、USER_GUIDE/FAQ 的操作说明、TEST_REPORT 的实测数字（含 50k 计时、覆盖率、mypy）。
- CHANGELOG 写清：本版只改交互与发现性；清洗语义/默认值/API/引擎未变；pillow 决定；已知限制。
- **不自动合并、不自动打 tag**：PR 由人工评审合并，tag 由人工在合并后创建。

**验收：** G-15/G-16；版本四处一致；文档数字来自实际命令；PR 模板逐项勾选。

### 5.12 测试选择器加固（前置项，非 issue 直接条目）

**对应文件：** `tests/test_app_ui.py`、`tests/test_app_ui_v2.py`（必要时 `tests/test_app_logic.py` 不动）。

**改法：**

- 把 `at.button[0]`、`at.checkbox[0]`、`at.multiselect[0]`、`at.dataframe[0]`、`at.selectbox[0]` 等位置访问改为按 label/key 查找的 helper（如 `_button(at, "生成下载文件")`、`_checkbox(at, "删除完全重复的行")`），helper 内部断言唯一命中。
- 先做 test-only commit，557 项原样通过；再开始插控件。
- 新增 `conftest.py` autouse fixture：仅对使用 AppTest 的测试在前后 `st.cache_data.clear()`，防止 session 缓存跨用例串味（本次实测：新 `AppTest` 实例会命中上一个实例的 session 缓存，必须显式清）。

**风险与回退：** 纯测试重构，不改生产代码；若某个 helper 找不到控件，测试失败而不是静默跳过。回退：单 commit 回退。

**验收：** 加固后 557 项全过；随后每个 UI PR 不得因插入控件而改无关测试的索引。

## 6. 任务拆分、依赖与批次

### 6.1 工作流

| W | 工作内容 | 主要文件 | 依赖 | 量级 |
| --- | --- | --- | --- | --- |
| W0 | 测试选择器加固 + 缓存清理 fixture | `tests/*`、`conftest.py` | 无 | S（4–6 h） |
| W1 | P0-1 缓存 + fragment；P0-2 反馈 | `app.py` | W0 | M–L（12–18 h） |
| W2 | P1-3 模板入口；P1-4 预设；P1-5 删行提示 | `app.py` | W1（复用缓存结构） | M（11–17 h） |
| W3 | P2-6 未生效；P2-7 上限；P2-8 转换预览 | `app.py` | W1 | M（12–18 h） |
| W4 | 覆盖率 CI、mypy（建议清零）、TEST_REPORT | `pyproject.toml`、workflow、`TEST_REPORT.md`、`transform.py`（可选） | 可与 W1–W3 并行 | S–M（6–10 h） |
| W5 | pillow 13 + Streamlit 1.67（独立 commit/PR） | `requirements*.txt`、`pyproject.toml` | W1–W3 合并后 | S（3–5 h） |
| W6 | 版本/CHANGELOG/README/发布准备 | 文档与版本文件 | W4、W5 | S（4–6 h） |
| P1 | 50k 计时 harness 与金标采集脚本 | `benchmarks/app_interaction.py`、`tests/fixtures/v250/` | W1（可先用 baseline 写） | S（4–6 h） |

量级：S ≈ 0.5–1 天，M ≈ 1–2 天，L ≈ 2–3 天（按 6 小时有效投入/天）。

### 6.2 依赖图与关键路径

```text
W0 ──> W1 ──> W2 ──┐
          └──> W3 ──┼──> W5 ──> W6
W4（覆盖率/mypy）───┘
P1（harness/金标）可与 W1 并行
```

- 关键路径：W0 → W1 → W2/W3 → W5 → W6。
- W2 与 W3 都改 `app.py`，建议顺序合并（W2 先），避免同一文件长期冲突；分支可并行开发。
- W4 可与 W1–W3 并行；W5 必须最后（依赖版本稳定后跑全量）；W6 收尾。

### 6.3 检查点（不写日历日期）

| 检查点 | 通过条件 | 不通过的处理 |
| --- | --- | --- |
| CP1 基线 | W0 完成；557 项全过；50k baseline 计时与金标采集完成 | 不进入 UI 改动 |
| CP2 P0 | W1 完成；warm 交互 ≤1.0 s 且相对 baseline 改善 ≥25%；下载失效全过；fragment spike 有结论 | 回退 fragment，只保留缓存；仍不达标则暂停评审 |
| CP3 P1/P2 | W2/W3 完成；G-07~G-12 全过；新增测试 ≥60；0 skipped | 逐项回退未达标项，不带病发布 |
| CP4 发布 | W4/W5/W6 完成；G-01~G-16 全过；CI 3.11/3.12 绿；人工确认 | 不打 tag；保留分支，报告未完成项 |

### 6.4 PR 批次建议

| 顺序 | PR 主题 | 内容 | 必须 |
| ---: | --- | --- | --- |
| 1 | `docs: 新增 v2.5.0 规划` | 本文件 | 人工评审通过（本 PR） |
| 2 | `test: 界面测试改用标签定位` | W0 | 557 项原样通过 |
| 3 | `perf(ui): 缓存解析/清洗并隔离导出重跑` | W1 | CP2；计时证据 |
| 4 | `feat(ui): 模板入口、场景提示与删行提示` | W2 | G-07~G-09；下载失效 |
| 5 | `feat(ui): 未生效提示、上限入口与转换预览` | W3 | G-10~G-12 |
| 6 | `ci: 覆盖率与 mypy 门禁` | W4 | G-13/G-14；命令实测 |
| 7 | `deps: 升级 streamlit 1.67 与 pillow 13` | W5（独立 commit/PR） | 557 通过；pip check；人工拍板 |
| 8 | `release: v2.5.0 发布准备` | W6 | G-15/G-16；版本/CHANGELOG/TEST_REPORT |

- PR 3–5 都改 `app.py`，每个 PR 内部按“行为 + 测试 + 文案”拆提交；不要为每个小项单独开 PR，避免评审碎片化。
- pillow 必须独立 PR/commit：它改的是平台版本，回滚不能牵连 UI 代码。
- 每个 PR 描述写：改动归类、默认行为是否变化、跑过的命令与结果、未执行项、UI 是否有 Before/After（真改 UI 的 PR 要截图；纯测试/文档 PR 写“不适用”）。
- 人工合并，不开启 auto-merge。

## 7. 量化验收标准

| G | 验收组 | 目标值 / 判定方法 |
| --- | --- | --- |
| G-01 | 既有行为与公共边界 | 557 项既有测试全过、0 skipped；默认值/`Limits`/公共 API/CLI/API/引擎/DSL 无行为 diff；`tests/test_v23_public_api.py` 仍过【目标值】 |
| G-02 | 5 万行交互性能 | 50k×12 fixture，AppTest 同 harness；warm 交互中位数 ≤1.0 s；相对 v2.4.0 改善 ≥25%；冷上传+首渲染 ≤2.0 s；导出点击不重跑解析/检查/清洗【目标值】 |
| G-03 | 结果一致性 | CSV 3 种防护模式 sha256 与 v2.4.0 金标逐字节一致；XLSX 解包内容一致；报告 steps/removed/notes 一致；App 级端到端 CSV 命中金标【目标值】 |
| G-04 | 下载失效 | 规则/列/表头/工作表/文件/模板/导出模式任一变化后旧 `exports` 清除、下载按钮消失；复测通过【目标值】 |
| G-05 | 缓存正确性与隔离 | 命中不调用 `load_table`/`clean_table`；规则/文件/表头变化必 miss；session 结束/测试清缓存后不复用；多文件不串数据【目标值】 |
| G-06 | 计算反馈 | 首算有 spinner（大表带行数/列数）；完成显示用时；命中显示“已复用”；大表无静止空窗（录屏/计时说明）【目标值】 |
| G-07 | 模板一键入口 | 上传后可见；点击还原规则并触发对比；不自动生成导出；同会话同类新表 ≤3 次点击出可用下载【目标值】 |
| G-08 | 场景预设 | 3 个预设可命中；最多 2 条；只提示；提示前后控件值不变【目标值】 |
| G-09 | 删行提示 | 删行 >0 显示总数；可查看 ≤5 行样例与原因；0 行不显示；规则变更后数字更新【目标值】 |
| G-10 | 未生效汇总 | 6 类未生效各覆盖；有效规则零误报；只提示不阻塞；改好后消失【目标值】 |
| G-11 | 上限标注 | 上传区显示当前上限；高级设置可调列/单元格/XLSX 解压；超限错误附近有入口；默认值不变【目标值】 |
| G-12 | 转换预览 | 前 5 值前→后；整列保留原文数；混时区不崩；只读不改结果【目标值】 |
| G-13 | 覆盖率 | CI 与本地可跑 `coverage`；公布 `cleaning/`（基线 95%）与 `app.py`（基线 89%）两个数；无 fail-under【目标值】 |
| G-14 | mypy | 错误数 ≤4 且无新增；若清零则 0；CI 可判定【目标值】 |
| G-15 | 测试与 CI | 新增 ≥60 项，总数 ≥617，0 skipped；pytest 3.11/3.12 全绿；ruff format/check 全绿【目标值】 |
| G-16 | 文档与发布 | CHANGELOG/TEST_REPORT/README/USER_GUIDE/FAQ 与实测一致；版本四处一致；PR 不自动合并；人工确认后合并/tag【目标值】 |

**不可删的底线：**

- 557 项既有测试全过（允许改 1 项删行样例测试以匹配新 UI 契约，但不删断言、不降标准）。
- 清洗语义、默认值、公共 API、引擎/DSL 结论不变。
- 5 万行 warm 交互 ≤1.0 s 且相对 baseline 有 ≥25% 改善。
- CSV 金标逐字节一致。
- 不自动合并、不自动打 tag。

## 8. 测试计划

### 8.1 既有 557 项的保护方式

1. **先加固选择器**（W0）：把约 38 处 `at.*[...]` 位置访问（其中 button/checkbox/multiselect 共 23 处）改成 label/key 查找；断言唯一命中；557 项原样通过后再改 UI。
2. **不删用例、不放宽断言**：唯一需要调整的是 `test_removed_rows_sample_toggle_shows_reason`（删行样例从 checkbox 改为按钮/提示条），改为按新 label 点击并保留全部断言；其余 556 项不改语义。
3. **缓存隔离**：`conftest.py` 在 AppTest 前后 `st.cache_data.clear()`；避免新实例命中旧 session 缓存。
4. **金标回归**：新增 G-03 测试，保证“UI 改动没有碰结果”。
5. **全量两遍**：每个 PR 本地 `pytest -q -rs` 跑两遍；发布前再跑一遍，记录数字。
6. **不用 skip/deselect/放宽断言让 CI 变绿**；界面测试的模块级跳过条件保持现状。

### 8.2 新增行为测试清单

| 测试文件 | 预计数量 | 覆盖 |
| --- | ---: | --- |
| `tests/test_v250_cache.py` | 12–16 | 解析/检查/清洗命中与 miss；换文件/表/表头/规则失效；命中不调用底层函数；跨会话清缓存；大文件绕过；fragment 导出不重跑主流程；下载失效 |
| `tests/test_v250_feedback.py` | 4–6 | spinner 文案与行数提示；完成用时；命中“已复用”；大表进度条仍出现 |
| `tests/test_v250_discovery.py` | 14–18 | 最近模板出现/套用/不自动导出/≤3 次点击；3 个预设命中、无匹配、零自动勾选；删行提示数量、样例 ≤5、0 行不显示、规则变更更新 |
| `tests/test_v250_p2.py` | 12–16 | 未生效 6 类 + 无误报；上限 caption/高级设置生效/默认值不变/超限入口；转换预览前→后、保留数量、混时区、只读 |
| `tests/test_v250_golden.py` | 10–14（参数化） | CSV 3 模式 × 用例；XLSX 2 模式；报告字段；App 级端到端 |
| `tests/test_v250_release.py` | 3–5 | 版本四处一致；CHANGELOG/TEST_REPORT 含关键数字；CI 配置含 coverage/mypy 步骤；PR 未开启 auto-merge（静态检查） |
| **合计** | **约 60–80** | 6 个文件的最小值合计为 55；参数化用例会超过下限，目标新增 ≥60、总数 ≥617 |

### 8.3 fixture 与 harness

- `tests/fixtures/v250/`：小体积 `small_mixed.csv`（空值/重复/公式/长编号/混时区）、`multi_sheet.xlsx`、模板 JSON 可提交；50k×12 的 4.3 MB CSV 由 harness 按固定种子现场生成，不提交大文件，只提交生成参数与金标哈希。
- `benchmarks/app_interaction.py`：AppTest 计时 + JSON/Markdown 输出；可与 v2.4.0 worktree 对跑；不加入 CI。
- `tests/fixtures/v250/golden.json`：由 `83a66ee` worktree 生成的 sha256/报告快照；生成脚本与命令写入文件头注释。
- 所有 fixture 为虚构数据，不含真实个人信息。

### 8.4 需要记录的证据

- 全量测试：`pytest -q -rs` 两遍（数量、耗时、0 skipped）；
- 50k 计时：v2.4.0 与 v2.5 各一组（中位数、p90、环境）；
- 覆盖率：cleaning / app.py 两个数（命令与日期）；
- mypy：错误数/位置；
- 金标：CSV sha256 清单、XLSX 解包比对结论；
- CI：两个 Python 版本的结果截图/日志；
- 未执行项如实写“未执行”（如 Python 3.11 本地、真实浏览器、pip-audit 复扫）。

## 9. 风险与回退

| 编号 | 风险 | 概率/影响 | 缓解 | 回退 |
| --- | --- | --- | --- | --- |
| RK-01 | 缓存串数据/旧结果 | 中/高 | 键含全部输入；失效矩阵测试；换文件清槽 | 移除缓存 |
| RK-02 | 内存峰值上升（大文件） | 中/中 | `max_entries=2`；>8 MB 绕过；单槽；记录峰值 | 降为 1 或不缓存 |
| RK-03 | `st.fragment` 与 AppTest/下载按钮不兼容 | 中/中 | 半日 spike；先跑下载失效用例 | 去 fragment，保留缓存 |
| RK-04 | 插入控件导致 557 项测试错位 | 高/中 | W0 先加固选择器；helper 断言唯一命中 | 回退 UI commit，保留 W0 |
| RK-05 | 场景预设误报/漏报 | 中/低 | 只提示；阈值 ≥2 列；同义词集中维护 | 删除提示 |
| RK-06 | 未生效汇总误报 | 中/中 | 严格定义 + 6 类测试 + 有效规则零误报 | 删除提示 |
| RK-07 | 转换预览与真实清洗口径不一致 | 中/高 | 预览复用 `convert_to_*`；测试对比导出 | 删除预览 |
| RK-08 | 上限调大导致内存风险 | 低/中 | 保留现有提醒；只影响会话；默认值不变 | 删除新增可调项 |
| RK-09 | Streamlit 1.67 + pillow 13 平台回归 | 中/高 | 独立 commit；全量 557 + CI 双版本；AppTest 已实测 | 单 commit 回滚到 1.66 + 12.3 |
| RK-10 | 覆盖率/mypy CI 抖动 | 中/低 | 不设覆盖率门槛；mypy 固定 2.4.1；基线方案 | 移除该 CI 步骤 |
| RK-11 | 评审等待/范围膨胀 | 中/中 | 分批小 PR；不做超出 10 项的范围 | 暂停新增，按第 3 节拒绝 |
| RK-12 | 快机器上性能门禁“假通过” | 中/中 | 绝对上限 + 相对 baseline ≥25% 双指标 | 以相对指标为准 |

**回退总原则：** 每个功能独立 commit；缓存/fragment/模板/预设/提示/预览都可单独移除；pillow 单独 commit；任何回退都必须重跑 557 + 新增测试 + 金标。

## 10. 工期估算

### 10.1 估算口径

- 1 人日 = 6 小时有效投入；不含评审等待、环境折腾。
- 业余投入按每周 10–15 小时估算；不写日历日期承诺。
- 估算含实现、测试、文档、自查与一次返工；不含人工评审。

### 10.2 估算表

| 工作流 | 内容 | 小时（估算，不是承诺） |
| --- | --- | ---: |
| W0 | 测试选择器加固 + 缓存清理 fixture | 4–6 |
| W1 | 缓存 + fragment + 计算反馈 | 12–18 |
| W2 | 模板入口 + 场景预设 + 删行提示 | 11–17 |
| W3 | 未生效汇总 + 上限入口 + 转换预览 | 12–18 |
| W4 | 覆盖率 CI + mypy（建议清零） | 6–10 |
| W5 | streamlit 1.67 + pillow 13（独立） | 3–5 |
| W6 | 版本/CHANGELOG/README/TEST_REPORT/发布 | 4–6 |
| P1 | 50k 计时 harness + 金标采集 | 4–6 |
| **合计** |  | **56–86 h** |

**量级结论：** 约 **56–86 小时**；按每周 15 小时约 **4–6 周业余**，按每周 10 小时约 **6–9 周**。比 v2.4.0（60–95 h）略低，主要因为不改清洗语义、不写新引擎；但测试加固与 UI 测试使下限不低。

### 10.3 可压缩范围

- 先压缩 P2-6/P2-7/P2-8（可拆到下一个小版本），但 P0/P1 与硬项 9/10 不建议压。
- 可压缩 mypy 清零为“基线 4 条不新增”（省 3–5 h）。
- 可压缩 50k harness 为“只用现有 AppTest 手测记录”（省 2–4 h，但证据变弱）。
- **不能压缩**：557 项回归、金标一致、下载失效、5 万行计时证据、CI 双版本、人工确认/不自动合并。

### 10.4 批次与节奏建议

- 第 1 批：本规划 PR（评审 1 次）。
- 第 2 批：W0（test-only，快）。
- 第 3 批：W1（P0，核心风险）。
- 第 4–5 批：W2、W3（可分两周）。
- 第 6 批：W4（可与 W1–W3 并行）。
- 第 7 批：W5（依赖升级，最后）。
- 第 8 批：W6（发布准备，人工合并 + tag）。

## 11. 发布门禁

v2.5.0 发布前必须同时满足：

- 本规划 PR 经人工确认；实施 PR 不自动合并、不开启 auto-merge。
- `pytest -q -rs` 在本地两遍、CI Python 3.11/3.12 全绿，0 skipped；新增 ≥60，总数 ≥617。
- `ruff format --check .` 与 `ruff check .` 全绿。
- mypy 错误数 ≤4 且无新增；若清零则 CI 硬门禁 0。
- coverage 在 CI 与本地可跑，TEST_REPORT 公布 `cleaning/` 与 `app.py` 两个百分比（只公布，不设门槛）。
- 5 万行 warm 交互中位数 ≤1.0 s，且相对 v2.4.0 baseline 改善 ≥25%；计时记录写入 TEST_REPORT。
- CSV 金标逐字节一致；XLSX 解包内容一致；下载失效不变量全过。
- 版本四处一致（`pyproject.toml`、`cleaning.__version__`、CHANGELOG、git tag），CHANGELOG/README/USER_GUIDE/FAQ/TEST_REPORT 与实测一致。
- 默认值、上限、公共 API、CLI/HTTP API、引擎/DSL 结论无变化。
- pillow 决定已由人工拍板；若纳入，独立 commit 且 CI 双版本通过；若拆出，CHANGELOG“已知限制”如实写明。
- 以上任一不满足：不合并、不打 tag，按第 9 节回退或重估。

## 12. pillow 与在线 demo 的单独结论

### 12.1 pillow 12.3.0 → 13.0.0

**结论：建议纳入 v2.5.0 附带，但必须作为独立 commit / 独立 PR，且以人工拍板为准。**

**依据：**

| 证据 | 内容 |
| --- | --- |
| 依赖约束 | 当前 `streamlit==1.66.0` 元数据要求 `pillow>=9.2.0,<13`，**不能**只升 pillow；`streamlit==1.67.0` 要求 `pillow<14`，可装 13.0.0 |
| 直接使用 | 仓库无 `PIL`/`pillow`/`st.image` 相关代码；pillow 只经 Streamlit 图片能力传递，本应用只处理 CSV/XLSX |
| 兼容实测 | 干净 venv：先按 `requirements-dev.txt` 安装，再升级到 `streamlit==1.67.0` + `pillow==13.0.0`；`pip check` 无冲突；`pytest -q -rs`：**557 passed，0 skipped**（55.42 s） |
| 回归面 | Streamlit 1.66→1.67 是 UI 框架 minor bump；AppTest 界面测试 32 项通过；但真实浏览器未验、Python 3.11 未本地验；CI 矩阵可补 |
| 安全收益 | v2.4.0 记录 pip-audit 命中 pillow 12.3.0 **39 条**漏洞（13.0.0 修复）；升级后应复扫确认归零/减少 |

**若纳入（推荐路径）：**

1. 独立 commit：`requirements-dev.txt` 改 `streamlit==1.67.0`；`requirements.txt` 与 `pyproject.toml` 增加显式 `pillow>=13.0.0,<14`（确保解析器不会回退到 12.x）。
2. 条件：CI 3.11/3.12 全绿；`pip check` 通过；`pytest` 557；AppTest 文件上传/下载/模板/批量全过；pip-audit 复扫记录；PR 描述写明“平台版本变更，可独立回滚”。
3. 回滚：一个 commit 同时回退 Streamlit pin 与 pillow floor，回到 1.66 + 12.3；不影响 UI 代码。

**若人工认为“v2.5 不引入平台变更”：** 拆出单独成版（建议 v2.5.1 安全依赖版），v2.5.0 在 CHANGELOG「已知限制」明确写“pillow 12.3.0 的 39 条扫描命中未在本版修复，计划 v2.5.1 处理”，不得写成已解决。

**不推荐“只升 pillow 不升 Streamlit”：** 1.66 的 `<13` 约束会让 `pip install` 直接冲突；若用 `--no-deps` 强装属于绕过依赖约束，CI/部署不可复现。

### 12.2 在线 demo（只评估，不占本版开发范围）

**结论：v2.5.0 不开发、不部署、不编造链接；建议 v2.6 再决定。**

| 路线 | 月成本 | 开发/运维 | 优点 | 风险 |
| --- | ---: | --- | --- | --- |
| Streamlit Community Cloud | $0 | 0.5–1 人日 | 与现有 app 直接兼容；免费 | 公开仓库/公开应用；无鉴权；休眠；1 GB 内存；上传内容进第三方云；国内访问可能不稳定 |
| Hugging Face Spaces（Streamlit SDK） | $0 | 0.5–1 人日 | 免费、部署简单 | 公开；无鉴权；休眠；第三方托管；资源/网络限制 |
| Render / Fly.io / Railway 容器 | $5–10 | 1–2 人日 + 运维 | 可控 HTTPS、可加 Basic Auth、资源较稳 | 需 Dockerfile/健康检查；512 MB–1 GB 内存；持续费用；仍需隐私说明 |
| 自建 VPS + Caddy | $4–10 | 1–2 人日 + 长期运维 | 完全可控；可内网/鉴权 | 需自己打补丁/监控/备份；公网暴露风险 |
| Cloudflare Tunnel + 自有机器 | $0 + 电费 | 0.5 人日 | 不买云主机 | 需常开机器；URL/稳定性差；不适合对外 demo |
| GitHub Codespaces | 免费额度 | 0.5 人日 | 临时演示 | 临时环境；不适合作为产品入口 |

**成本结论：** 最小可用 demo 约 **1–3 人日**开发 + **$0–10/月**托管；若要求登录/限流/隐私合规，另加 2–5 人日。

**推荐路线（若做）：** 只展示**虚构样例数据**的只读 demo（不开放上传），部署在 Hugging Face Spaces 或 Streamlit Community Cloud；在页面顶部写明“演示数据、勿上传真实数据”。这样避免第三方托管用户上传内容的隐私与滥用风险。若必须开放上传，则选 Render/Fly + Basic Auth + 限流，预算 $5–10/月，并单独做隐私/合规评审。

**本版处理：** 不写部署代码、不加 Dockerfile、不改 `.streamlit`、不创建 demo 链接；只在本文记录评估，供 v2.6 决策。

## 13. 待人工确认的决策

| 编号 | 问题 | 建议 | 需要人工给什么 |
| --- | --- | --- | --- |
| DC-1 | 缓存作用域 | 解析 `st.cache_data(scope="session")`；流水线 session_state 单槽 | 确认“会话级、不跨用户”的实现口径 |
| DC-2 | `st.fragment` 是否用于导出区 | 是，半日 spike 后保留或回退 | 确认可接受“局部重跑”的测试/实现复杂度 |
| DC-3 | “最近模板”是否只在本会话 | 是；不引入服务端存储 | 确认不要求跨会话/账号持久化 |
| DC-4 | 场景预设是否允许自动勾选 | 否，只提示 | 确认“只提示”是产品决定 |
| DC-5 | 覆盖率公布范围 | `cleaning/` + `app.py` 两个数，不设门槛 | 确认可接受两个百分比而非一个总数 |
| DC-6 | mypy 是否顺手清零 | 建议清零（独立 commit） | 若不同意，接受“≤4 且不新增”的 CI 方案 |
| DC-7 | pillow 13 是否随 v2.5 | 建议纳入（独立 commit），条件见 12.1 | 在“v2.5 附带”与“拆出 v2.5.1”之间拍板 |
| DC-8 | 在线 demo 是否本版做 | 不做，v2.6 再评估 | 确认 demo 的产品形态与隐私边界 |
| DC-9 | 本文产出形式 | PR `docs/V250_PLAN.md` + issue 回帖摘要 | 确认后进入实施 |

**人工确认前，本 ticket 不进入实施。** 若评审要求调整范围，先改本文件并重新确认，不在实施 PR 里悄悄扩项。

## 14. 参考文件

- issue #9 正文（v2.5 任务书）与本次触发说明。
- `app.py`（810 行）：`get_source`、`show_checks`、`show_comparison`、`show_export`、`show_templates`、`main`。
- `cleaning/limits.py`、`cleaning/io.py`、`cleaning/rules.py`、`cleaning/transform.py`、`cleaning/export.py`、`cleaning/templates.py`、`cleaning/batch.py`；`cleaning/__init__.py` 的 `__all__`。
- `tests/test_app_ui.py`、`tests/test_app_ui_v2.py`、`tests/test_app_logic.py`、`tests/test_v23_public_api.py`、`tests/test_v23_limits.py`、`tests/test_v240_docs.py`。
- `ENGINEERING_STANDARDS.md` 第 2、6、7、8、9、14 节；`RELEASE_CHECKLIST.md`；`.github/workflows/tests.yml`；`.github/pull_request_template.md`。
- `V240_PLAN.md`（体例参照）；`CHANGELOG.md` v2.4.0 节；`TEST_REPORT.md`；`benchmarks/BASELINE.md`。
- 本次实测命令（可复现）：
  - `coverage run -m pytest -q -rs` → 557 passed；`coverage report --include='cleaning/*'` → 95%；
  - `coverage run --source=. --concurrency=thread -m pytest -q tests/test_app_ui.py tests/test_app_ui_v2.py`，再 `coverage report --include='*/app.py'` → app.py 89%；
  - `ruff format --check .` / `ruff check .` / `mypy` → 47 formatted / All checks passed / 4 errors；
  - 50k×12 AppTest 计时脚本（见 2.2 与 8.3）；
  - 干净 venv `streamlit==1.67.0` + `pillow==13.0.0` → 557 passed、`pip check` 通过。
