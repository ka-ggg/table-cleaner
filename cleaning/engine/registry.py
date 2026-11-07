"""算子能力注册表：17 项用户可见能力的唯一合同清单。

共 17 项：9 个流水线节点（R1–R9）、3 个检查算子（I1–I3）、5 类计算列表达式能力
（X1–X5）。每条记录 pandas 参考实现、Polars 目标状态与回落原因、风险、对齐用例 id，
供 W2/W3 实现与对齐测试核对。**本模块不执行任何数据操作。**

状态取值（与 ``V240_PLAN.md`` 5.3 一致）：

- ``aligned``：常见路径必须由 Polars 原生执行，逐值语义与 pandas 一致；
- ``partial_fallback``：常见路径对齐，已登记的边界输入整条流水线回落 pandas；
- ``full_fallback``：整项能力回落 pandas，必须写清原因与到期版本。

W1 只落地合同；W2（v2.4.0 实施 PR 3）已为 17 项能力填入 Polars 实现路径并置
``polars_implemented=True``。实现路径由 ``tests/test_v240_engine_alignment.py``
逐项调用比较；禁止把未实现的能力标成已完成，否则对齐与发布门禁会失去意义。
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Callable

KIND_NODE = "node"
KIND_INSPECTOR = "inspector"
KIND_EXPRESSION = "expression"

STATUS_ALIGNED = "aligned"
STATUS_PARTIAL_FALLBACK = "partial_fallback"
STATUS_FULL_FALLBACK = "full_fallback"

KINDS = (KIND_NODE, KIND_INSPECTOR, KIND_EXPRESSION)
STATUSES = (STATUS_ALIGNED, STATUS_PARTIAL_FALLBACK, STATUS_FULL_FALLBACK)

# 17 项能力的固定编号与顺序；测试要求注册表不多不少。
CAPABILITY_IDS = (
    "R1",
    "R2",
    "R3",
    "R4",
    "R5",
    "R6",
    "R7",
    "R8",
    "R9",
    "I1",
    "I2",
    "I3",
    "X1",
    "X2",
    "X3",
    "X4",
    "X5",
)

# 计划 5.3 要求高风险节点至少 5 组对齐用例；其余流水线节点至少 3 组。
_MIN_NODE_CASES = 3
_MIN_INSPECTOR_CASES = 2
_MIN_EXPRESSION_CASES = 2
_HIGH_RISK_NODES = frozenset({"R5", "R6", "R8"})
_MIN_HIGH_RISK_CASES = 5


@dataclass(frozen=True)
class Capability:
    """一项算子/检查/表达式能力的合同记录（不可变，注册表是唯一权威）。"""

    id: str
    kind: str  # node / inspector / expression
    title: str  # 中文短名，计划描述与文档引用
    summary: str  # 对应的现行 pandas 行为
    pandas_impl: str  # 参考实现的点号路径，测试会解析并确认可调用
    polars_status: str  # aligned / partial_fallback / full_fallback（v2.4.0 目标）
    polars_reason: str  # 为什么对齐或回落
    risk: str  # 主要差异风险
    case_ids: tuple[str, ...]  # W3 对齐用例的稳定 id
    stage: int | None = None  # 流水线阶段；仅 node 有
    node_type: str | None = None  # DSL 节点类型；仅 node 有
    polars_impl: str | None = None  # W2 填入 Polars 实现路径
    polars_implemented: bool = False  # W2 实现后置 True


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        id="R1",
        kind=KIND_NODE,
        title="全行去重",
        summary="drop_duplicates(keep='first')：所有列完全相同的行只保留第一次出现。",
        pandas_impl="cleaning.rules._drop_duplicate_rows",
        polars_status=STATUS_ALIGNED,
        polars_reason="unique(maintain_order=True) 在 null、空文本、多列组合上保持首次出现，语义可逐值复现。",
        risk="null/空文本相等性、行顺序",
        case_ids=("R1-null-vs-empty-text", "R1-keep-first-order", "R1-multi-column-all-null"),
        stage=10,
        node_type="row.drop_duplicates",
        polars_impl="cleaning.engine.polars_engine.drop_duplicate_rows",
        polars_implemented=True,
    ),
    Capability(
        id="R2",
        kind=KIND_NODE,
        title="按列去重",
        summary="drop_duplicates(subset=列, keep='first')：只按指定列判重，保留第一次出现。",
        pandas_impl="cleaning.rules._drop_duplicate_rows",
        polars_status=STATUS_ALIGNED,
        polars_reason="unique(subset=..., maintain_order=True) 与 pandas 的按列判重、保留首次出现一致。",
        risk="null/空文本键、缺失列过滤、行顺序",
        case_ids=("R2-subset-first", "R2-null-key", "R2-empty-text-key"),
        stage=20,
        node_type="row.dedupe_by",
        polars_impl="cleaning.engine.polars_engine.drop_duplicate_rows",
        polars_implemented=True,
    ),
    Capability(
        id="R3",
        kind=KIND_NODE,
        title="删空值行",
        summary="missing_mask 的「null 或空文本」判定，所选列任一为空即删整行。",
        pandas_impl="cleaning.rules._drop_missing_rows",
        polars_status=STATUS_ALIGNED,
        polars_reason="filter + 「null 或空文本」谓词复现同一判定；分块处理与整表结果一致。",
        risk="空文本语义、分块与整表一致、进度/超时",
        case_ids=("R3-single-column", "R3-all-columns", "R3-chunk-equals-whole"),
        stage=30,
        node_type="row.drop_missing",
        polars_impl="cleaning.engine.polars_engine.drop_missing_rows",
        polars_implemented=True,
    ),
    Capability(
        id="R4",
        kind=KIND_NODE,
        title="删列",
        summary="df.drop(columns=existing)：只删存在的列，不存在的列提示后忽略。",
        pandas_impl="cleaning.transform.apply_transforms",
        polars_status=STATUS_ALIGNED,
        polars_reason="drop(columns) 直接对应；不存在列的提示沿用现行文案。",
        risk="不存在列的提示、列顺序保持不变",
        case_ids=("R4-existing", "R4-missing-column", "R4-drop-multiple"),
        stage=40,
        node_type="column.drop",
        polars_impl="cleaning.engine.polars_engine.drop_columns",
        polars_implemented=True,
    ),
    Capability(
        id="R5",
        kind=KIND_NODE,
        title="转数字",
        summary="numeric_series + Decimal 精度守卫 + _tidy_numbers；失败位置保留原文并计数。",
        pandas_impl="cleaning.transform.convert_to_number",
        polars_status=STATUS_PARTIAL_FALLBACK,
        polars_reason="常见数字/千分位/科学计数路径对齐；货币符号、百分号、长编号、溢出、混合列等边界整节点回落 pandas。",
        risk=">15 位长编号、整数溢出、混合列、显示类型",
        case_ids=(
            "R5-thousands",
            "R5-currency-percent",
            "R5-long-id-15-18",
            "R5-overflow-inf",
            "R5-mixed-column",
        ),
        stage=50,
        node_type="column.to_number",
        polars_impl="cleaning.engine.polars_engine.convert_to_number",
        polars_implemented=True,
    ),
    Capability(
        id="R6",
        kind=KIND_NODE,
        title="转日期",
        summary="pd.to_datetime(format='mixed') + 时区处理；整日只留日期，失败保留原文。",
        pandas_impl="cleaning.transform.convert_to_date",
        polars_status=STATUS_PARTIAL_FALLBACK,
        polars_reason="ISO、/ 等明确格式走 Polars 解析；中文年月日按当前 pandas「保留原文并计数」对齐；混合格式、混时区整节点回落 pandas，不猜格式。",
        risk="格式猜测、时区、日期 vs 日期时间",
        case_ids=(
            "R6-iso",
            "R6-slash",
            "R6-chinese-ymd",
            "R6-mixed-formats",
            "R6-mixed-timezones",
        ),
        stage=60,
        node_type="column.to_date",
        polars_impl="cleaning.engine.polars_engine.convert_to_date",
        polars_implemented=True,
    ),
    Capability(
        id="R7",
        kind=KIND_NODE,
        title="列改名",
        summary="rename(columns=mapping)：源列不存在或目标名冲突时跳过并提示。",
        pandas_impl="cleaning.transform.apply_transforms",
        polars_status=STATUS_ALIGNED,
        polars_reason="rename 映射一次应用；重名冲突与不存在列沿用现行提示，不改变列顺序。",
        risk="重名/冲突提示、改名后列引用",
        case_ids=("R7-basic", "R7-missing-source", "R7-name-conflict"),
        stage=70,
        node_type="column.rename",
        polars_impl="cleaning.engine.polars_engine.rename_columns",
        polars_implemented=True,
    ),
    Capability(
        id="R8",
        kind=KIND_NODE,
        title="计算列",
        summary="AST 白名单 + pandas Series 运算；round/abs 守卫，无穷大留空并提示。",
        pandas_impl="cleaning.transform.add_computed_column",
        polars_status=STATUS_PARTIAL_FALLBACK,
        polars_reason="四则（除零→inf 留空）、一元、round/abs、幂守卫核心路径对齐；//、%、** 与非数字列等边界整节点回落 pandas。",
        risk="取整、除零、溢出、// 与 % 的负数符号",
        case_ids=(
            "R8-precedence",
            "R8-round-half-even",
            "R8-division-by-zero",
            "R8-overflow-inf",
            "R8-pow-guard",
        ),
        stage=80,
        node_type="column.compute",
        polars_impl="cleaning.engine.polars_engine.add_computed_column",
        polars_implemented=True,
    ),
    Capability(
        id="R9",
        kind=KIND_NODE,
        title="列顺序",
        summary="点名前列在前（按给定顺序），其余列按原顺序跟随；不存在的列提示后忽略。",
        pandas_impl="cleaning.transform.apply_transforms",
        polars_status=STATUS_ALIGNED,
        polars_reason="select(wanted + rest) 与现行「点名前、其余原顺序」一致。",
        risk="不存在列、重复点名、列顺序",
        case_ids=("R9-order-front", "R9-missing-column", "R9-rest-preserved"),
        stage=90,
        node_type="column.order",
        polars_impl="cleaning.engine.polars_engine.order_columns",
        polars_implemented=True,
    ),
    Capability(
        id="I1",
        kind=KIND_INSPECTOR,
        title="空值计数",
        summary="missing_mask 的 isna() | eq('') 逐列求和；0 与 '0' 不算缺失。",
        pandas_impl="cleaning.rules.find_missing",
        polars_status=STATUS_ALIGNED,
        polars_reason="null 计数 + 空文本计数，与 missing_mask 同一判定。",
        risk="空文本、数字 0、null 表示",
        case_ids=("I1-null-and-empty-text", "I1-zero-not-missing"),
        polars_impl="cleaning.engine.polars_engine.find_missing",
        polars_implemented=True,
    ),
    Capability(
        id="I2",
        kind=KIND_INSPECTOR,
        title="完全重复行计数",
        summary="duplicated().sum()：所有列值完全相同的行数，不删数据。",
        pandas_impl="cleaning.rules.summarize",
        polars_status=STATUS_ALIGNED,
        polars_reason="height - unique(maintain_order=True).height 与 duplicated().sum() 一致；null 相等性也一致。",
        risk="null 相等性、混合 dtype",
        case_ids=("I2-duplicate-count", "I2-null-rows"),
        polars_impl="cleaning.engine.polars_engine.summarize",
        polars_implemented=True,
    ),
    Capability(
        id="I3",
        kind=KIND_INSPECTOR,
        title="可疑格式检查",
        summary="文本列首尾空格 + 疑似公式/注入开头；判定与 CSV 导出防护共用纯函数。",
        pandas_impl="cleaning.rules.find_suspicious",
        polars_status=STATUS_ALIGNED,
        polars_reason="按逻辑类型识别文本列，首尾空格与公式判定精确优先，可接受逐值回调。",
        risk="Unicode 空白、BOM/零宽字符、逐值性能",
        case_ids=("I3-leading-space", "I3-formula-prefixes", "I3-bom-zero-width"),
        polars_impl="cleaning.engine.polars_engine.find_suspicious",
        polars_implemented=True,
    ),
    Capability(
        id="X1",
        kind=KIND_EXPRESSION,
        title="四则/整除/取模/幂",
        summary="+ - * / // % ** 的 Python/NumPy Series 运算（含负数取模与整除语义）。",
        pandas_impl="cleaning.transform.safe_eval",
        polars_status=STATUS_ALIGNED,
        polars_reason="按同一运算符与负数语义映射到 pl.Expr，逐项差分。",
        risk="负数取模/整除、溢出",
        case_ids=("X1-four-ops", "X1-negative-floor-mod", "X1-overflow"),
        polars_impl="cleaning.engine.polars_engine.evaluate_expression",
        polars_implemented=True,
    ),
    Capability(
        id="X2",
        kind=KIND_EXPRESSION,
        title="一元正负",
        summary="Python unary + / -：对数字列取正负号。",
        pandas_impl="cleaning.transform.safe_eval",
        polars_status=STATUS_ALIGNED,
        polars_reason="一元正负直接对应 Polars 表达式，无额外 dtype 语义。",
        risk="dtype 变化",
        case_ids=("X2-unary-plus", "X2-unary-minus"),
        polars_impl="cleaning.engine.polars_engine.evaluate_expression",
        polars_implemented=True,
    ),
    Capability(
        id="X3",
        kind=KIND_EXPRESSION,
        title="round()",
        summary="Python round(value, ndigits)，保留位数上限 ±30，半偶舍入。",
        pandas_impl="cleaning.transform._safe_round",
        polars_status=STATUS_ALIGNED,
        polars_reason="按同一保留位数上限与半偶舍入对齐；若 W3 差分发现不一致则登记 partial_fallback。",
        risk="半偶舍入 vs NumPy、位数上限",
        case_ids=("X3-round-digits", "X3-round-negative", "X3-round-limit"),
        polars_impl="cleaning.engine.polars_engine.evaluate_round",
        polars_implemented=True,
    ),
    Capability(
        id="X4",
        kind=KIND_EXPRESSION,
        title="abs()",
        summary="abs(value)：对数字或列取绝对值。",
        pandas_impl="cleaning.transform.safe_eval",
        polars_status=STATUS_ALIGNED,
        polars_reason="abs() 对负数与 null 的语义一致。",
        risk="null 传播",
        case_ids=("X4-abs-negative", "X4-abs-null"),
        polars_impl="cleaning.engine.polars_engine.evaluate_abs",
        polars_implemented=True,
    ),
    Capability(
        id="X5",
        kind=KIND_EXPRESSION,
        title="幂运算守卫",
        summary="_safe_pow 常量位数检查；运行期 inf 留空并提示。",
        pandas_impl="cleaning.transform._safe_pow",
        polars_status=STATUS_ALIGNED,
        polars_reason="编译期常量位数检查 + 运行期 inf 处理，共用同一上限。",
        risk="常量折叠、运行期溢出",
        case_ids=("X5-constant-explosion", "X5-runtime-inf"),
        polars_impl="cleaning.engine.polars_engine.evaluate_pow_guard",
        polars_implemented=True,
    ),
)

_BY_ID: dict[str, Capability] = {capability.id: capability for capability in CAPABILITIES}
_BY_NODE_TYPE: dict[str, Capability] = {
    capability.node_type: capability for capability in CAPABILITIES if capability.node_type is not None
}


def get_capability(capability_id: str) -> Capability:
    """按能力 id（如 ``R1``）取合同记录；不存在时抛 ``KeyError``（内部调用）。"""
    return _BY_ID[capability_id]


def capability_for_node_type(node_type: str) -> Capability:
    """按 DSL 节点类型取对应流水线能力；不存在时抛 ``KeyError``。"""
    return _BY_NODE_TYPE[node_type]


def node_capabilities() -> tuple[Capability, ...]:
    """按阶段顺序返回 9 个流水线节点能力。"""
    return tuple(sorted((cap for cap in CAPABILITIES if cap.kind == KIND_NODE), key=_stage_key))


def node_types() -> tuple[str, ...]:
    """按阶段顺序返回 9 个 DSL 节点类型。"""
    return tuple(cap.node_type for cap in node_capabilities() if cap.node_type is not None)


def _stage_key(capability: Capability) -> int:
    return capability.stage if capability.stage is not None else -1


def resolve_callable(path: str) -> Callable[..., object]:
    """解析「模块.属性」路径并确认可调用；坏路径抛 ``ImportError`` / ``AttributeError``。"""
    module_name, _, attribute = path.rpartition(".")
    if not module_name or not attribute:
        raise ImportError(f"实现路径「{path}」缺少模块名或属性名。")
    module = importlib.import_module(module_name)
    resolved = getattr(module, attribute)
    if not callable(resolved):
        raise TypeError(f"实现路径「{path}」指向的对象不可调用。")
    return resolved


def registry_problems() -> tuple[str, ...]:
    """返回注册表自检问题列表；空元组表示合同完整，可直接用于测试与 CI。"""
    problems: list[str] = []
    actual_ids = tuple(capability.id for capability in CAPABILITIES)
    if actual_ids != CAPABILITY_IDS:
        problems.append(f"能力编号应为 {CAPABILITY_IDS}，实际为 {actual_ids}。")
    if len(set(actual_ids)) != len(actual_ids):
        problems.append("能力编号有重复。")

    seen_node_types: set[str] = set()
    seen_stages: set[str] = set()
    for capability in CAPABILITIES:
        label = capability.id
        if capability.kind not in KINDS:
            problems.append(f"{label} 的 kind「{capability.kind}」不合法。")
        if capability.polars_status not in STATUSES:
            problems.append(f"{label} 的 Polars 状态「{capability.polars_status}」不合法。")
        if not capability.title.strip() or not capability.summary.strip():
            problems.append(f"{label} 缺少中文名称或行为说明。")
        if not capability.risk.strip():
            problems.append(f"{label} 缺少风险说明。")
        if not capability.polars_reason.strip():
            problems.append(f"{label} 缺少对齐/回落原因。")
        if not capability.case_ids:
            problems.append(f"{label} 没有对齐用例 id。")
        try:
            resolve_callable(capability.pandas_impl)
        except (ImportError, AttributeError, TypeError) as exc:
            problems.append(f"{label} 的 pandas 实现「{capability.pandas_impl}」不可用：{exc}")
        if capability.polars_implemented and capability.polars_impl is None:
            problems.append(f"{label} 标记为已实现，但缺少 polars_impl 路径。")
        if capability.polars_impl is not None and not capability.polars_implemented:
            problems.append(f"{label} 有 polars_impl 路径，但未标记为已实现。")
        if capability.polars_impl is not None:
            try:
                resolve_callable(capability.polars_impl)
            except (ImportError, AttributeError, TypeError) as exc:
                problems.append(f"{label} 的 Polars 实现「{capability.polars_impl}」不可用：{exc}")

        if capability.kind == KIND_NODE:
            if capability.node_type is None or capability.stage is None:
                problems.append(f"{label} 是流水线节点，但缺少 node_type 或 stage。")
                continue
            if capability.node_type in seen_node_types:
                problems.append(f"节点类型「{capability.node_type}」重复登记。")
            seen_node_types.add(capability.node_type)
            if not 10 <= capability.stage <= 90 or capability.stage % 10:
                problems.append(f"{label} 的阶段 {capability.stage} 不在 10–90 的整十阶段。")
            stage_key = str(capability.stage)
            if stage_key in seen_stages:
                problems.append(f"阶段 {capability.stage} 被多个节点重复使用。")
            seen_stages.add(stage_key)
            minimum = _MIN_HIGH_RISK_CASES if capability.id in _HIGH_RISK_NODES else _MIN_NODE_CASES
            if len(capability.case_ids) < minimum:
                problems.append(f"{label} 只有 {len(capability.case_ids)} 组用例，至少需要 {minimum} 组。")
        elif capability.kind == KIND_INSPECTOR:
            if len(capability.case_ids) < _MIN_INSPECTOR_CASES:
                problems.append(f"{label} 的检查用例少于 {_MIN_INSPECTOR_CASES} 组。")
        elif capability.kind == KIND_EXPRESSION:
            if len(capability.case_ids) < _MIN_EXPRESSION_CASES:
                problems.append(f"{label} 的表达式用例少于 {_MIN_EXPRESSION_CASES} 组。")

    if len(seen_node_types) != 9:
        problems.append(f"流水线节点类型应为 9 个，实际登记 {len(seen_node_types)} 个。")
    if set(seen_stages) != {"10", "20", "30", "40", "50", "60", "70", "80", "90"}:
        problems.append(f"流水线阶段应覆盖 10–90，实际为 {sorted(seen_stages, key=int)}。")
    return tuple(problems)
