"""v2.4.0 已知差异登记表：Polars 与 pandas 双引擎之间「为什么可以不同」的唯一权威。

本模块只存数据与自检，不执行任何数据操作，也不 import Polars。对齐测试、计划描述和
实现期文档都从这里读取结论；没有登记的差异一律视为回归，测试必须失败。

登记字段与 ``V240_PLAN.md`` 5.4 一致：

- ``decision``：``align``（对齐，差异不存在或已被规范化消除）/ ``fallback``（回落
  pandas）/ ``accepted_difference``（人工评审接受的可见差异，v3.0 前重新评审）；
- ``scope``：``common_path``（常见路径对齐）/ ``boundary``（边界输入回落）/
  ``whole_operator``（整算子回落）；
- ``status``：``open``（回落仍存在）/ ``resolved``（已对齐，不需要再回落）。

门禁（规划 5.4 / G-06）：

- ``decision=align`` 必须是 ``resolved``；
- ``status=open`` 只能是 ``fallback``，且必须写清触发用例、原因和计划版本；
- 不允许 ``accepted_difference`` 在 v2.4.0 出现——它需要人工评审批准；
- 不允许出现「待定」「未知」这类空白决定。
"""

from __future__ import annotations

from dataclasses import dataclass

DECISION_ALIGN = "align"
DECISION_FALLBACK = "fallback"
DECISION_ACCEPTED_DIFFERENCE = "accepted_difference"

SCOPE_COMMON_PATH = "common_path"
SCOPE_BOUNDARY = "boundary"
SCOPE_WHOLE_OPERATOR = "whole_operator"

STATUS_OPEN = "open"
STATUS_RESOLVED = "resolved"

DECISIONS = (DECISION_ALIGN, DECISION_FALLBACK, DECISION_ACCEPTED_DIFFERENCE)
SCOPES = (SCOPE_COMMON_PATH, SCOPE_BOUNDARY, SCOPE_WHOLE_OPERATOR)
STATUSES = (STATUS_OPEN, STATUS_RESOLVED)

# v2.4.0 允许的回落目标版本；整算子回落必须显式写到期版本（本版没有整算子回落）。
_KNOWN_TARGETS = ("v2.4.0", "v3.0", "v3.1")


@dataclass(frozen=True)
class KnownDifference:
    """一条已登记的引擎差异；字段含义见模块文档与规划 5.4。"""

    id: str
    operator: str  # 能力 id（R1–R9 / I1–I3 / X1–X5）或 engine
    case: str  # 触发/覆盖该结论的对齐用例 id
    pandas: str  # 可复现的 pandas 结果
    polars: str  # 可复现的 Polars 结果
    impact: str  # 是否用户可见；影响 L1/L2/L3/L4 哪一层
    decision: str  # align / fallback / accepted_difference
    scope: str  # common_path / boundary / whole_operator
    reason: str  # 为什么这样决定
    target: str  # 计划解决版本
    status: str  # open / resolved


# 每条回落都对应一个对齐用例；用例 id 与 ``registry.Capability.case_ids`` 保持一致。
KNOWN_DIFFERENCES: tuple[KnownDifference, ...] = (
    KnownDifference(
        id="KD-0001",
        operator="R5",
        case="R5-currency-percent",
        pandas="「$1,200」「50%」不是 pandas 能直接转换的数字，保留原文并计入失败个数。",
        polars="Polars 快速路径无法在不猜格式的前提下复现「保留原文并计数」，整条流水线回落 pandas。",
        impact="用户可见；影响 L3 报告（失败计数与原文样例），不做回落会改变数据。",
        decision=DECISION_FALLBACK,
        scope=SCOPE_BOUNDARY,
        reason="货币符号/百分号属于未支持的边界写法；规划 5.3 要求边界输入整节点回落 pandas。",
        target="v3.0",
        status=STATUS_OPEN,
    ),
    KnownDifference(
        id="KD-0002",
        operator="R5",
        case="R5-long-id-15-18",
        pandas="超过 15 位有效数字的长编号（订单号/身份证号）在 float64 里会丢精度，pandas 保留原文并计数。",
        polars="Polars cast 到 Float64 同样会改值；Decimal 逐值复核成本与整节点回落在 v2.4.0 等价。",
        impact="用户可见；影响 L1 数据值与 L2 导出，静默转换会悄悄改数据。",
        decision=DECISION_FALLBACK,
        scope=SCOPE_BOUNDARY,
        reason="长编号精度守卫是 v2.1 已修复的回归项，不能为性能牺牲正确性。",
        target="v3.0",
        status=STATUS_OPEN,
    ),
    KnownDifference(
        id="KD-0003",
        operator="R5",
        case="R5-overflow-inf",
        pandas="「1E400」转换溢出为 inf，pandas 视为失败、保留原文并计数。",
        polars="Polars 文本 cast 得到 inf；保证不写出 inf 需要与 pandas 相同的有限值守卫。",
        impact="用户可见；影响 L1 数据值与 L2 导出。",
        decision=DECISION_FALLBACK,
        scope=SCOPE_BOUNDARY,
        reason="溢出值不是合法数字，保留原文是现行合同；边界情形统一回落 pandas。",
        target="v3.0",
        status=STATUS_OPEN,
    ),
    KnownDifference(
        id="KD-0004",
        operator="R5",
        case="R5-mixed-column",
        pandas="同一列里能转换与不能转换的值混排时，成功位置转数字、失败位置保留原文并计数。",
        polars="Polars 单次 cast 会把整列失败值变成 null；逐值回填与 pandas 的 object 混合 dtype 难以在 v2.4.0 无损复现。",
        impact="用户可见；影响 L1 数据值与 L3 报告。",
        decision=DECISION_FALLBACK,
        scope=SCOPE_BOUNDARY,
        reason="混合列是规划 5.3 明确登记的回落边界；失败位置必须保留原文。",
        target="v3.0",
        status=STATUS_OPEN,
    ),
    KnownDifference(
        id="KD-0005",
        operator="R6",
        case="R6-mixed-formats",
        pandas='同一列混用多种日期写法时由 pandas format="mixed" 逐值推断；非法日期保留原文并计数。',
        polars="Polars 日期解析必须给单一格式；v2.4.0 不猜格式，混合/非法输入整条流水线回落 pandas。",
        impact="用户可见；影响 L1 数据值与 L3 报告。",
        decision=DECISION_FALLBACK,
        scope=SCOPE_BOUNDARY,
        reason="规划 5.3 要求混合格式登记回落，禁止在 Polars 侧猜格式。",
        target="v3.0",
        status=STATUS_OPEN,
    ),
    KnownDifference(
        id="KD-0006",
        operator="R6",
        case="R6-mixed-timezones",
        pandas="同一列混有带时区与不带时区的写法时，pandas 不猜时区，整列保留原文并提示。",
        polars="Polars 解析会按单一规则处理时区；为保持现行「不猜时区」行为，整条流水线回落 pandas。",
        impact="用户可见；影响 L1 数据值与 L3 报告。",
        decision=DECISION_FALLBACK,
        scope=SCOPE_BOUNDARY,
        reason="时区猜测会把日期算错；现行合同是保留原文，边界情形回落。",
        target="v3.0",
        status=STATUS_OPEN,
    ),
    KnownDifference(
        id="KD-0007",
        operator="R8",
        case="R8-pow-guard",
        pandas="// % ** 属于 R8 的边界运算：幂含常量位数检查（10**1e8 直接抛中文 TableError），负数整除/取模按 Python 语义。",
        polars="Polars 表达式在运行期才展开；取整、负数整除/取模与常量幂守卫的边界组合在 v2.4.0 整条流水线回落 pandas。",
        impact="用户可见；影响 L3 报告（中文错误说明）与边界数据的取整结果，回落保证完全一致。",
        decision=DECISION_FALLBACK,
        scope=SCOPE_BOUNDARY,
        reason="规划 5.3 要求 R8 的取整/负数整除/取模/幂边界回落；表达式能力 X1/X5 单独对齐并另行验证。",
        target="v3.0",
        status=STATUS_OPEN,
    ),
    KnownDifference(
        id="KD-0009",
        operator="engine",
        case="PIPE-sample-fallback",
        pandas="sample > 0 时返回被删行样例与「删除原因」，行号、顺序和计数都来自 pandas。",
        polars="v2.4.0 的 Polars 路径不实现行级样例/血缘，sample > 0 统一整条流水线回落 pandas。",
        impact="不影响清洗结果；L3 报告（removed/删除原因）完全一致。",
        decision=DECISION_FALLBACK,
        scope=SCOPE_BOUNDARY,
        reason="规划 5.2 明确规定 sample > 0 的能力回落不是隐藏差异。",
        target="v3.0",
        status=STATUS_OPEN,
    ),
    KnownDifference(
        id="KD-0010",
        operator="engine",
        case="PIPE-full-aligned",
        pandas="清洗结果是 pandas 帧，导出 CSV/XLSX 的字节/单元格类型以 pandas dtype 为准。",
        polars="Polars 执行后回填 pandas 时按原 dtype 与共享整理函数恢复，L1/L2/L3 完全一致；仅内部空值表示不同。",
        impact="不可见；内部 null/NaN 表示不同，L1 逻辑值、L2 导出、L3 报告均已验证一致。",
        decision=DECISION_ALIGN,
        scope=SCOPE_COMMON_PATH,
        reason="回填时恢复原 dtype，避免把「都是数字」当成放过差异的理由。",
        target="v2.4.0",
        status=STATUS_RESOLVED,
    ),
    KnownDifference(
        id="KD-0011",
        operator="engine",
        case="engine-dtype-fallback",
        pandas="pandas 可以承载混合 object 列（同一列既有数字又有文本）以及带时区的 datetime 列。",
        polars="Polars 的强类型列无法无损表示混合列；时区列也不在 v2.4.0 的引擎边界内，执行前预检整条流水线回落 pandas。",
        impact="不可见；回落路径的输出与默认 pandas 完全一致，不会改变任何单元格。",
        decision=DECISION_FALLBACK,
        scope=SCOPE_BOUNDARY,
        reason="规划 5.2 要求对无法映射的 dtype 做执行前预检回落，禁止运行到一半再换引擎。",
        target="v3.0",
        status=STATUS_OPEN,
    ),
)

_BY_ID: dict[str, KnownDifference] = {difference.id: difference for difference in KNOWN_DIFFERENCES}


def get_difference(difference_id: str) -> KnownDifference:
    """按 id 取一条差异登记；不存在时抛 ``KeyError``（内部调用）。"""
    return _BY_ID[difference_id]


def differences_for(operator: str) -> tuple[KnownDifference, ...]:
    """返回某个能力/引擎的全部登记条目（按 id 排序）。"""
    return tuple(sorted((item for item in KNOWN_DIFFERENCES if item.operator == operator), key=lambda item: item.id))


def difference_for_case(case_id: str) -> KnownDifference | None:
    """按对齐用例 id 找登记条目；没有登记时返回 ``None``。"""
    for item in KNOWN_DIFFERENCES:
        if item.case == case_id:
            return item
    return None


def known_difference_problems() -> tuple[str, ...]:
    """自检差异登记表；空元组表示可以用于 CP2/G-06 门禁。"""
    problems: list[str] = []
    seen_ids: set[str] = set()
    seen_cases: dict[str, str] = {}
    for item in KNOWN_DIFFERENCES:
        label = item.id
        if not label.startswith("KD-") or len(label) != 7 or not label[3:].isdigit():
            problems.append(f"{label} 的编号格式应为 KD-0000 四位数字。")
        if label in seen_ids:
            problems.append(f"差异编号「{label}」重复。")
        seen_ids.add(label)
        if not item.operator.strip():
            problems.append(f"{label} 缺少 operator。")
        if not item.case.strip():
            problems.append(f"{label} 缺少触发用例 id。")
        if item.case in seen_cases and seen_cases[item.case] != label:
            problems.append(f"用例「{item.case}」被 {seen_cases[item.case]} 与 {label} 重复登记。")
        seen_cases[item.case] = label
        for field_name in ("pandas", "polars", "impact", "reason"):
            if not str(getattr(item, field_name)).strip():
                problems.append(f"{label} 缺少 {field_name} 说明。")
        if item.decision not in DECISIONS:
            problems.append(f"{label} 的 decision「{item.decision}」不合法。")
        if item.scope not in SCOPES:
            problems.append(f"{label} 的 scope「{item.scope}」不合法。")
        if item.status not in STATUSES:
            problems.append(f"{label} 的 status「{item.status}」不合法。")
        if item.target not in _KNOWN_TARGETS:
            problems.append(f"{label} 的 target「{item.target}」不在已知版本 {_KNOWN_TARGETS}。")
        if item.decision == DECISION_ALIGN and item.status != STATUS_RESOLVED:
            problems.append(f"{label} 决定为 align，状态必须是 resolved。")
        if item.status == STATUS_OPEN and item.decision != DECISION_FALLBACK:
            problems.append(f"{label} 处于 open，只能是 fallback（不能把未对齐写成已解决）。")
        if item.decision == DECISION_FALLBACK and item.scope == SCOPE_COMMON_PATH:
            problems.append(f"{label} 是回落，scope 不能是 common_path。")
        if item.decision == DECISION_FALLBACK and not item.target.startswith("v3."):
            problems.append(f"{label} 是回落，target 应写明后续版本（v3.x）。")
        if item.decision == DECISION_ACCEPTED_DIFFERENCE:
            problems.append(f"{label} 是 accepted_difference；v2.4.0 未获人工评审批准，不能登记。")
        for pending in ("待定", "未知", "TBD"):
            if pending in f"{item.pandas}{item.polars}{item.impact}{item.reason}":
                problems.append(f"{label} 含未决措辞「{pending}」，CP2 不允许。")
    if not KNOWN_DIFFERENCES:
        problems.append("已知差异登记表为空；回落项必须逐条登记。")
    return tuple(problems)
