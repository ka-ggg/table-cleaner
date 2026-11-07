"""v2.4.0 W3 对齐测试：58 组用例、L1/L2/L3 三层比较、独立承担率与差异登记。

覆盖规划 5.4 / G-04 ~ G-08：

- 52 组能力用例（与 ``registry.Capability.case_ids`` 一一对应）+ 3 组完整流水线
  + 3 组 benchmark 端到端 = 58 组；
- L1 比较清洗结果的逻辑值（列名/顺序、行数/行序、逐单元格；保留 null 与空文本、
  整数/浮点/文本、日期/日期的区别）；
- L2 比较 CSV 字节与 XLSX 读回后的单元格值/类型；
- L3 比较 steps、notes、removed（sample > 0 时）；
- 回落用例断言预检结论为 pandas、结果与默认路径完全一致，并能在
  ``known_differences`` 找到登记条目；
- Polars 用例在跑之前把 pandas 参考入口替换成会抛错的哨兵，证明没有偷偷回落
  （「独立承担」不是只看注册表有没有实现）。

运行环境必须安装 Polars（见 requirements-dev.txt）；缺失时这里直接 ImportError，
不静默 skip——静默跳过会让 CP2 门禁失去意义。
"""

from __future__ import annotations

import ast
import math
import random
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Mapping

import cleaning
import cleaning.engine.base as engine_base
import cleaning.rules as rules
import cleaning.transform as transform
import pandas as pd
import polars as pl
import pytest
from benchmarks.generate import _NAMES, COLUMNS, SEED, _amount, _date_text
from cleaning.engine import (
    CAPABILITIES,
    KIND_INSPECTOR,
    KIND_NODE,
    STATUS_FULL_FALLBACK,
    get_capability,
    get_engine,
    resolve_callable,
)
from cleaning.engine.known_differences import (
    DECISION_ALIGN,
    DECISION_FALLBACK,
    KNOWN_DIFFERENCES,
    difference_for_case,
    differences_for,
    get_difference,
    known_difference_problems,
)
from cleaning.engine.polars_engine import (
    POLARS_ENGINE,
    PolarsEngine,
    evaluate_abs,
    evaluate_expression,
    evaluate_pow_guard,
    evaluate_round,
    preflight,
)
from cleaning.engine.polars_engine import (
    drop_columns as polars_engine_drop_columns,
)
from cleaning.engine.polars_engine import (
    order_columns as polars_engine_order_columns,
)
from cleaning.engine.polars_engine import (
    rename_columns as polars_engine_rename_columns,
)
from cleaning.errors import Deadline, TableError
from cleaning.export import to_csv_bytes, to_xlsx_bytes
from cleaning.transform import CleanOptions, ColumnOps
from openpyxl import load_workbook

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALIGNED = "polars"
FALLBACK = "pandas"
TOTAL_CASES = 58
MIN_POLARS_RATE = 0.80
BENCHMARK_SIZES = {"small": 300, "medium": 1_200, "large": 3_000}


# ---------------------------------------------------------------- 用例模型


@dataclass(frozen=True)
class Snapshot:
    """一次引擎调用的可比结果；不同能力只填相关字段。"""

    frame: pd.DataFrame | None = None
    steps: Mapping[str, int] | None = None
    notes: tuple[str, ...] | None = None
    removed: pd.DataFrame | None = None
    series: pd.Series | None = None
    mapping: Mapping[str, object] | None = None
    error: str | None = None


@dataclass(frozen=True)
class Case:
    """一组对齐用例；``kind`` 决定预检方式与「禁止回落」哨兵。"""

    case_id: str
    capability: str
    expected: str
    kind: str
    runner: Callable[[str], Snapshot]
    frame: pd.DataFrame
    options: CleanOptions | None
    sample: int = 0
    difference: str | None = None


def _engine(name: str) -> Any:
    return get_engine(name)


def _report_snapshot(report: cleaning.CleanReport) -> Snapshot:
    return Snapshot(
        frame=report.df,
        steps=report.steps,
        notes=report.notes,
        removed=report.removed,
    )


def _row_case(
    case_id: str,
    capability: str,
    frame: pd.DataFrame,
    expected: str,
    *,
    drop_duplicates: bool = False,
    dedupe_on: list[str] | None = None,
    drop_missing_in: list[str] | None = None,
    chunk_rows: int | None = None,
    difference: str | None = None,
) -> Case:
    row_kwargs: dict[str, Any] = {
        "drop_duplicates": drop_duplicates,
        "dedupe_on": dedupe_on,
        "drop_missing_in": drop_missing_in,
        "chunk_rows": chunk_rows,
    }

    def runner(name: str) -> Snapshot:
        return _report_snapshot(_engine(name).clean_report(frame, **row_kwargs))

    options = CleanOptions(
        drop_duplicates=drop_duplicates,
        dedupe_on=tuple(dedupe_on or ()),
        drop_missing_in=tuple(drop_missing_in or ()),
    )
    return Case(case_id, capability, expected, "row", runner, frame, options, difference=difference)


def _ops_case(
    case_id: str,
    capability: str,
    frame: pd.DataFrame,
    ops: ColumnOps,
    expected: str,
    *,
    difference: str | None = None,
) -> Case:
    def runner(name: str) -> Snapshot:
        transformed, notes = _engine(name).apply_transforms(frame, ops)
        return Snapshot(frame=transformed, notes=tuple(notes))

    return Case(case_id, capability, expected, "ops", runner, frame, CleanOptions(columns=ops), difference=difference)


def _inspector_case(
    case_id: str,
    capability: str,
    frame: pd.DataFrame,
    function_name: str,
) -> Case:
    def runner(name: str) -> Snapshot:
        engine = _engine(name)
        if function_name == "find_missing":
            return Snapshot(series=engine.find_missing(frame))
        if function_name == "summarize":
            return Snapshot(mapping=engine.summarize(frame))
        return Snapshot(mapping=engine.find_suspicious(frame))

    return Case(case_id, capability, ALIGNED, "inspector", runner, frame, None)


def _pipeline_case(
    case_id: str,
    frame: pd.DataFrame,
    options: CleanOptions,
    expected: str,
    *,
    sample: int = 0,
    difference: str | None = None,
) -> Case:
    def runner(name: str) -> Snapshot:
        return _report_snapshot(_engine(name).clean_table(frame, options, sample=sample))

    return Case(case_id, "PIPE", expected, "pipeline", runner, frame, options, sample, difference)


def _benchmark_case(case_id: str, frame: pd.DataFrame, options: CleanOptions) -> Case:
    def runner(name: str) -> Snapshot:
        return _report_snapshot(_engine(name).clean_table(frame, options))

    return Case(case_id, "BENCH", ALIGNED, "benchmark", runner, frame, options)


def _expression_case(
    case_id: str,
    capability: str,
    frame: pd.DataFrame,
    *,
    mode: str,
    expression: str = "",
    ndigits: int = 0,
) -> Case:
    """表达式能力用例：pandas 侧走 ``safe_eval``，Polars 侧走注册的实现入口。"""

    def runner(name: str) -> Snapshot:
        try:
            if mode == "round":
                value = (
                    transform._safe_round(frame["a"], ndigits)
                    if name == "pandas"
                    else evaluate_round(frame["a"], ndigits)
                )
            elif mode == "abs":
                value = transform.safe_eval("abs(a)", frame) if name == "pandas" else evaluate_abs(frame["a"])
            elif mode == "pow_const":
                value = transform._safe_pow(10, 999_999) if name == "pandas" else evaluate_pow_guard(10, 999_999)
            elif mode == "pow_series":
                value = (
                    transform._safe_pow(frame["a"], 400) if name == "pandas" else evaluate_pow_guard(frame["a"], 400)
                )
            else:
                value = (
                    transform.safe_eval(expression, frame)
                    if name == "pandas"
                    else evaluate_expression(expression, frame)
                )
        except TableError as exc:
            return Snapshot(error=str(exc))
        return Snapshot(series=value)

    return Case(case_id, capability, ALIGNED, "expression", runner, frame, None)


# ---------------------------------------------------------------- 52 组能力用例


def _r1_cases() -> list[Case]:
    return [
        _row_case(
            "R1-null-vs-empty-text",
            "R1",
            pd.DataFrame({"A": ["x", None, "", None, "x"], "B": ["", None, "", "q", None]}),
            ALIGNED,
            drop_duplicates=True,
        ),
        _row_case(
            "R1-keep-first-order",
            "R1",
            pd.DataFrame({"A": ["b", "a", "b", "a", "c"]}),
            ALIGNED,
            drop_duplicates=True,
        ),
        _row_case(
            "R1-multi-column-all-null",
            "R1",
            pd.DataFrame({"A": [None, None, None], "B": [None, None, None]}),
            ALIGNED,
            drop_duplicates=True,
        ),
    ]


def _r2_cases() -> list[Case]:
    return [
        _row_case(
            "R2-subset-first",
            "R2",
            pd.DataFrame({"k": [1, 2, 1, 2, 3], "v": ["a", "b", "c", "d", "e"]}),
            ALIGNED,
            dedupe_on=["k"],
        ),
        _row_case(
            "R2-null-key",
            "R2",
            pd.DataFrame({"k": [None, "", None, "", None], "v": [1, 2, 3, 4, 5]}),
            ALIGNED,
            dedupe_on=["k"],
        ),
        _row_case(
            "R2-empty-text-key",
            "R2",
            pd.DataFrame({"k": ["", "", None, None], "v": [1, 2, 3, 4]}),
            ALIGNED,
            dedupe_on=["k"],
        ),
    ]


def _r3_cases() -> list[Case]:
    return [
        _row_case(
            "R3-single-column",
            "R3",
            pd.DataFrame({"a": ["x", None, "", "0", "0", " "], "b": [1, 2, 3, 4, 5, 6]}),
            ALIGNED,
            drop_missing_in=["a"],
        ),
        _row_case(
            "R3-all-columns",
            "R3",
            pd.DataFrame({"a": ["x", None, "", "0"], "b": [1, 2, None, 4]}),
            ALIGNED,
            drop_missing_in=["a", "b"],
        ),
        _row_case(
            "R3-chunk-equals-whole",
            "R3",
            pd.DataFrame({"a": ["x", None, "", "y", "z", None, "w", "v", "u", "t", "s", "r"], "b": range(12)}),
            ALIGNED,
            drop_missing_in=["a"],
            chunk_rows=3,
        ),
    ]


def _structural_frame() -> pd.DataFrame:
    return pd.DataFrame({"a": [1, 2], "b": [3, 4], "c": [5, 6]})


def _r4_cases() -> list[Case]:
    frame = _structural_frame()
    return [
        _ops_case("R4-existing", "R4", frame.copy(), ColumnOps(drop=("b",)), ALIGNED),
        _ops_case("R4-missing-column", "R4", frame.copy(), ColumnOps(drop=("z",)), ALIGNED),
        _ops_case("R4-drop-multiple", "R4", frame.copy(), ColumnOps(drop=("a", "c")), ALIGNED),
    ]


def _r5_cases() -> list[Case]:
    return [
        _ops_case(
            "R5-thousands",
            "R5",
            pd.DataFrame({"金额": ["1,000", "20.5", "-3.5", "", None]}),
            ColumnOps(to_number=("金额",)),
            ALIGNED,
        ),
        _ops_case(
            "R5-currency-percent",
            "R5",
            pd.DataFrame({"金额": ["$1,200", "50%", "100", "", None]}),
            ColumnOps(to_number=("金额",)),
            FALLBACK,
            difference="KD-0001",
        ),
        _ops_case(
            "R5-long-id-15-18",
            "R5",
            pd.DataFrame({"编号": ["123456789012345678", "12345678901234567", ""]}),
            ColumnOps(to_number=("编号",)),
            FALLBACK,
            difference="KD-0002",
        ),
        _ops_case(
            "R5-overflow-inf",
            "R5",
            pd.DataFrame({"金额": ["1E400", "2.5", ""]}),
            ColumnOps(to_number=("金额",)),
            FALLBACK,
            difference="KD-0003",
        ),
        _ops_case(
            "R5-mixed-column",
            "R5",
            pd.DataFrame({"金额": ["100", "abc", "2.5", ""]}),
            ColumnOps(to_number=("金额",)),
            FALLBACK,
            difference="KD-0004",
        ),
    ]


def _r6_cases() -> list[Case]:
    return [
        _ops_case(
            "R6-iso",
            "R6",
            pd.DataFrame({"日期": ["2024-05-01", "2024-5-2", "", None]}),
            ColumnOps(to_date=("日期",)),
            ALIGNED,
        ),
        _ops_case(
            "R6-slash",
            "R6",
            pd.DataFrame({"日期": ["2024/5/2", "2024/05/01", None]}),
            ColumnOps(to_date=("日期",)),
            ALIGNED,
        ),
        _ops_case(
            "R6-chinese-ymd",
            "R6",
            pd.DataFrame({"日期": ["2024年5月1日", "2024年5月2日", None]}),
            ColumnOps(to_date=("日期",)),
            ALIGNED,
        ),
        _ops_case(
            "R6-mixed-formats",
            "R6",
            pd.DataFrame({"日期": ["2024-05-01", "2024/5/2", "2024年5月3日"]}),
            ColumnOps(to_date=("日期",)),
            FALLBACK,
            difference="KD-0005",
        ),
        _ops_case(
            "R6-mixed-timezones",
            "R6",
            pd.DataFrame({"日期": ["2024-05-01T00:00:00+08:00", "2024-05-02T00:00:00"]}),
            ColumnOps(to_date=("日期",)),
            FALLBACK,
            difference="KD-0006",
        ),
    ]


def _r7_cases() -> list[Case]:
    frame = _structural_frame()
    return [
        _ops_case("R7-basic", "R7", frame.copy(), ColumnOps(rename=(("a", "x"),)), ALIGNED),
        _ops_case("R7-missing-source", "R7", frame.copy(), ColumnOps(rename=(("z", "y"),)), ALIGNED),
        _ops_case("R7-name-conflict", "R7", frame.copy(), ColumnOps(rename=(("a", "b"),)), ALIGNED),
    ]


def _r8_cases() -> list[Case]:
    return [
        _ops_case(
            "R8-precedence",
            "R8",
            pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": [4.0, 5.0, 6.0], "c": [2.0, 2.0, 2.0]}),
            ColumnOps(computed=(("结果", "(a + b) * c"),)),
            ALIGNED,
        ),
        _ops_case(
            "R8-round-half-even",
            "R8",
            pd.DataFrame({"a": [2.5, 3.5, -2.5, -3.5, 2.675]}),
            ColumnOps(computed=(("结果", "round(a, 0)"),)),
            ALIGNED,
        ),
        _ops_case(
            "R8-division-by-zero",
            "R8",
            pd.DataFrame({"a": [1.0, 0.0, -1.0, 2.0], "b": [0.0, 0.0, 0.0, 2.0]}),
            ColumnOps(computed=(("结果", "a / b"),)),
            ALIGNED,
        ),
        _ops_case(
            "R8-overflow-inf",
            "R8",
            pd.DataFrame({"a": [1e200, 2.0, 3.0]}),
            ColumnOps(computed=(("结果", "a * a * a"),)),
            ALIGNED,
        ),
        _ops_case(
            "R8-pow-guard",
            "R8",
            pd.DataFrame({"a": [2.0]}),
            ColumnOps(computed=(("结果", "10 ** 999999"),)),
            FALLBACK,
            difference="KD-0007",
        ),
    ]


def _r9_cases() -> list[Case]:
    frame = _structural_frame()
    return [
        _ops_case("R9-order-front", "R9", frame.copy(), ColumnOps(order=("c", "a")), ALIGNED),
        _ops_case("R9-missing-column", "R9", frame.copy(), ColumnOps(order=("z", "a")), ALIGNED),
        _ops_case("R9-rest-preserved", "R9", frame.copy(), ColumnOps(order=("b",)), ALIGNED),
    ]


def _i1_cases() -> list[Case]:
    return [
        _inspector_case(
            "I1-null-and-empty-text",
            "I1",
            pd.DataFrame({"a": [None, "", "x", "0"], "b": ["", "0", None, " "]}),
            "find_missing",
        ),
        _inspector_case(
            "I1-zero-not-missing",
            "I1",
            pd.DataFrame({"数字": [0, 0.0, None], "文本": ["0", "", None]}),
            "find_missing",
        ),
    ]


def _i2_cases() -> list[Case]:
    return [
        _inspector_case(
            "I2-duplicate-count",
            "I2",
            pd.DataFrame({"a": [1, 1, 2, 2], "b": ["x", "x", "y", "z"]}),
            "summarize",
        ),
        _inspector_case(
            "I2-null-rows",
            "I2",
            pd.DataFrame({"a": [None, None, None, None], "b": [None, None, None, None]}),
            "summarize",
        ),
    ]


def _i3_cases() -> list[Case]:
    return [
        _inspector_case(
            "I3-leading-space",
            "I3",
            pd.DataFrame({"文本": [" 前空格", "后空格 ", "正常", "  双  ", None], "数值": [1, 2, 3, 4, 5]}),
            "find_suspicious",
        ),
        _inspector_case(
            "I3-formula-prefixes",
            "I3",
            pd.DataFrame({"文本": ["=1+1", "@cmd", "+5", "-3.5", "50%", "safe", None]}),
            "find_suspicious",
        ),
        _inspector_case(
            "I3-bom-zero-width",
            "I3",
            pd.DataFrame({"文本": ["\ufeff=cmd", "\u200b  x", "\ufeff@x", "  pad  ", "\u200e-sheet"]}),
            "find_suspicious",
        ),
    ]


def _x_cases() -> list[Case]:
    return [
        _expression_case(
            "X1-four-ops",
            "X1",
            pd.DataFrame({"a": [2.0, 5.0, 9.0], "b": [4.0, 2.0, 3.0]}),
            mode="expr",
            expression="a + b - a * b / a",
        ),
        _expression_case(
            "X1-negative-floor-mod",
            "X1",
            pd.DataFrame({"a": [-7.0, 7.0, -7.0], "b": [3.0, 3.0, -3.0]}),
            mode="expr",
            expression="a // b + a % b",
        ),
        _expression_case(
            "X1-overflow",
            "X1",
            pd.DataFrame({"a": [10.0, -10.0]}),
            mode="expr",
            expression="a ** 400",
        ),
        _expression_case(
            "X2-unary-plus",
            "X2",
            pd.DataFrame({"a": [1.5, -2.5, 0.0]}),
            mode="expr",
            expression="+a",
        ),
        _expression_case(
            "X2-unary-minus",
            "X2",
            pd.DataFrame({"a": [1.5, -2.5, 0.0]}),
            mode="expr",
            expression="-a",
        ),
        _expression_case(
            "X3-round-digits",
            "X3",
            pd.DataFrame({"a": [2.675, -2.675, 12345.678]}),
            mode="round",
            ndigits=2,
        ),
        _expression_case(
            "X3-round-negative",
            "X3",
            pd.DataFrame({"a": [15.0, 25.0, -25.0, 12345.678]}),
            mode="round",
            ndigits=-1,
        ),
        _expression_case(
            "X3-round-limit",
            "X3",
            pd.DataFrame({"a": [1.0]}),
            mode="round",
            ndigits=1000,
        ),
        _expression_case(
            "X4-abs-negative",
            "X4",
            pd.DataFrame({"a": [-1.0, 2.0, -3.5]}),
            mode="abs",
        ),
        _expression_case(
            "X4-abs-null",
            "X4",
            pd.DataFrame({"a": [-1.0, None]}),
            mode="abs",
        ),
        _expression_case(
            "X5-constant-explosion",
            "X5",
            pd.DataFrame({"a": [2.0]}),
            mode="pow_const",
        ),
        _expression_case(
            "X5-runtime-inf",
            "X5",
            pd.DataFrame({"a": [10.0, 2.0]}),
            mode="pow_series",
        ),
    ]


# ---------------------------------------------------------------- 完整流水线与 benchmark


def _full_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "订单号": ["A001", "A002", "A002", "A003", None, "A005"],
            "客户名称": ["晨光", None, "晨光", "星语", "远航", "山泉"],
            "金额": ["1,000", "20.5", "20.5", "300", "88", "12"],
            "日期": ["2024-05-01", "2024-05-02", "2024-05-02", "2024-05-03", "2024-05-04", "2024-05-05"],
            "备注": ["a", "b", "b", "=1+1", "d", "e"],
        }
    )


def _full_options() -> CleanOptions:
    return CleanOptions(
        drop_duplicates=True,
        dedupe_on=("订单号",),
        drop_missing_in=("客户名称",),
        columns=ColumnOps(
            rename=(("客户名称", "客户"),),
            drop=("备注",),
            to_number=("金额",),
            to_date=("日期",),
            computed=(("含税金额", "金额*1.13"),),
            order=("订单号", "客户", "金额", "日期", "含税金额"),
        ),
    )


def _structural_options() -> CleanOptions:
    return CleanOptions(
        drop_duplicates=True,
        columns=ColumnOps(
            drop=("c",),
            rename=(("a", "a2"), ("z", "z2")),
            order=("b", "a2"),
        ),
    )


def _benchmark_options() -> CleanOptions:
    return CleanOptions(
        drop_duplicates=True,
        drop_missing_in=("客户名称",),
        columns=ColumnOps(
            drop=("金额", "下单日期"),
            rename=(("客户名称", "客户"),),
            order=("订单号", "客户"),
        ),
    )


def _benchmark_frame(rows: int) -> pd.DataFrame:
    """按 benchmarks/generate.py 的固定种子生成小号数据；大档留给 CI 外的 benchmark。"""
    rng = random.Random(SEED)
    records: list[tuple[str, str, str, str]] = []
    previous: tuple[str, str, str, str] | None = None
    for index in range(rows):
        if index and index % 1000 == 0 and previous is not None:
            row = previous
        else:
            identifier = f"{100_000_000_000_000_000 + index:018d}"
            name = "" if index % 5000 == 0 else _NAMES[rng.randrange(len(_NAMES))]
            row = (identifier, name, _amount(rng, index), _date_text(index))
            previous = row
        records.append(row)
    return pd.DataFrame(records, columns=list(COLUMNS))


def _pipeline_cases() -> list[Case]:
    return [
        _pipeline_case("PIPE-full-aligned", _full_frame(), _full_options(), ALIGNED, difference="KD-0010"),
        _pipeline_case(
            "PIPE-structural-aligned",
            pd.DataFrame({"a": ["x", "x", None, ""], "b": [1, 1, 2, 3], "c": ["p", "q", "r", "s"]}),
            _structural_options(),
            ALIGNED,
        ),
        _pipeline_case(
            "PIPE-sample-fallback",
            _full_frame(),
            _full_options(),
            FALLBACK,
            sample=3,
            difference="KD-0009",
        ),
    ]


def _benchmark_cases() -> list[Case]:
    options = _benchmark_options()
    return [_benchmark_case(f"BENCH-{name}", _benchmark_frame(rows), options) for name, rows in BENCHMARK_SIZES.items()]


CASES: tuple[Case, ...] = tuple(
    _r1_cases()
    + _r2_cases()
    + _r3_cases()
    + _r4_cases()
    + _r5_cases()
    + _r6_cases()
    + _r7_cases()
    + _r8_cases()
    + _r9_cases()
    + _i1_cases()
    + _i2_cases()
    + _i3_cases()
    + _x_cases()
    + _pipeline_cases()
    + _benchmark_cases()
)
CASE_BY_ID: dict[str, Case] = {case.case_id: case for case in CASES}


# ---------------------------------------------------------------- 比较器


def _canonical_cell(value: object) -> tuple[str, object]:
    """把单元格规范成可比较的逻辑值；保留 null 与空文本、整数/浮点/文本的区别。"""
    if value is None or value is pd.NA or value is pd.NaT:
        return ("null", None)
    if isinstance(value, float) and math.isnan(value):
        return ("null", None)
    if isinstance(value, pd.Timestamp):
        return ("datetime", value.isoformat())
    if isinstance(value, datetime):
        return ("datetime", value.isoformat())
    if isinstance(value, date):
        return ("date", value.isoformat())
    if isinstance(value, str):
        return ("text", value)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, float):
        return ("float", value)
    return (type(value).__name__, repr(value))


def _assert_l1_equal(expected: pd.DataFrame, actual: pd.DataFrame) -> None:
    assert list(expected.columns) == list(actual.columns), "列名或列顺序不一致"
    assert expected.shape == actual.shape, f"形状不一致：{expected.shape} != {actual.shape}"
    for row in range(expected.shape[0]):
        for column in range(expected.shape[1]):
            left = _canonical_cell(expected.iat[row, column])
            right = _canonical_cell(actual.iat[row, column])
            assert left == right, f"第 {row} 行第 {column} 列不一致：pandas={left!r} polars={right!r}"


def _assert_series_logical_equal(expected: pd.Series, actual: pd.Series) -> None:
    assert len(expected) == len(actual)
    for position, (left, right) in enumerate(zip(expected.tolist(), actual.tolist(), strict=True)):
        assert _canonical_cell(left) == _canonical_cell(right), (
            f"第 {position} 个值不一致：pandas={left!r} polars={right!r}"
        )


def _xlsx_cells(data: bytes) -> list[list[tuple[object, str]]]:
    """XLSX 不按文件字节比较（ZIP 时间戳不稳定），按读回的单元格值与类型比较。"""
    sheet = load_workbook(BytesIO(data)).active
    return [[(cell.value, cell.data_type) for cell in row] for row in sheet.iter_rows()]


def _assert_l2_equal(expected: pd.DataFrame, actual: pd.DataFrame) -> None:
    assert to_csv_bytes(expected, neutralize_formulas="quote") == to_csv_bytes(actual, neutralize_formulas="quote"), (
        "L2 CSV 字节不一致"
    )
    assert _xlsx_cells(to_xlsx_bytes(expected)) == _xlsx_cells(to_xlsx_bytes(actual)), "L2 XLSX 单元格值/类型不一致"


def _assert_snapshot_equal(expected: Snapshot, actual: Snapshot) -> None:
    if expected.error is not None or actual.error is not None:
        assert expected.error == actual.error
        return
    if expected.frame is not None:
        assert actual.frame is not None
        _assert_l1_equal(expected.frame, actual.frame)
        _assert_l2_equal(expected.frame, actual.frame)
    if expected.steps is not None:
        assert expected.steps == actual.steps
    if expected.notes is not None:
        assert expected.notes == actual.notes
    if expected.removed is not None:
        assert actual.removed is not None
        _assert_l1_equal(expected.removed, actual.removed)
    if expected.series is not None:
        assert actual.series is not None
        _assert_series_logical_equal(expected.series, actual.series)
    if expected.mapping is not None:
        assert expected.mapping == actual.mapping


# ---------------------------------------------------------------- pandas 哨兵


def _boom(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("Polars 用例回落到 pandas 参考实现；独立承担率断言不成立。")


def _forbid_pandas(case: Case, monkeypatch: pytest.MonkeyPatch) -> None:
    """把该能力的 pandas 入口换成哨兵；Polars 路径一旦调用就会失败。"""
    targets: list[tuple[Any, str]] = []
    if case.kind in ("row", "pipeline", "benchmark"):
        targets.extend([(rules, "clean_report"), (rules, "_drop_duplicate_rows"), (rules, "_drop_missing_rows")])
    if case.kind in ("row",):
        targets.extend([(transform, "clean_table")])
    if case.kind in ("ops", "pipeline", "benchmark"):
        targets.append((transform, "apply_transforms"))
    if case.kind == "pipeline":
        targets.append((transform, "clean_table"))
    if case.capability == "R5":
        targets.extend([(transform, "convert_to_number"), (transform, "numeric_series")])
    if case.capability == "R6":
        targets.append((transform, "convert_to_date"))
    if case.capability == "R8":
        targets.extend([(transform, "add_computed_column"), (transform, "safe_eval")])
    if case.capability == "I1":
        targets.append((rules, "find_missing"))
    if case.capability == "I2":
        targets.append((rules, "summarize"))
    if case.capability == "I3":
        targets.append((rules, "find_suspicious"))
    for module, attribute in targets:
        monkeypatch.setattr(module, attribute, _boom)


# ---------------------------------------------------------------- 门禁：矩阵与差异登记


def test_alignment_matrix_is_exactly_58_cases_with_unique_ids():
    """58 组用例、id 唯一，且 52 个注册表用例 id 全部有落地测试。"""
    assert len(CASES) == TOTAL_CASES
    assert len(CASE_BY_ID) == TOTAL_CASES
    registry_ids = {case_id for capability in CAPABILITIES for case_id in capability.case_ids}
    assert len(registry_ids) == 52
    assert registry_ids <= set(CASE_BY_ID)
    assert {case.case_id for case in CASES if case.kind == "pipeline"} == {
        "PIPE-full-aligned",
        "PIPE-structural-aligned",
        "PIPE-sample-fallback",
    }
    assert {case.case_id for case in CASES if case.kind == "benchmark"} == {f"BENCH-{name}" for name in BENCHMARK_SIZES}
    assert [case.case_id for case in CASES if case.kind == "benchmark"] == [f"BENCH-{name}" for name in BENCHMARK_SIZES]


def test_each_capability_meets_plan_case_minimums():
    counts = Counter(case.capability for case in CASES)
    for capability in CAPABILITIES:
        if capability.kind == KIND_NODE:
            minimum = 5 if capability.id in {"R5", "R6", "R8"} else 3
        elif capability.kind == KIND_INSPECTOR:
            minimum = 2
        else:
            minimum = 2
        assert counts[capability.id] >= minimum, capability.id
        assert counts[capability.id] == len(capability.case_ids), capability.id


def test_independent_execution_rate_meets_cp2_gate():
    """G-07：≥80% 用例全程 Polars；回落只出现在已登记的 R5/R6/R8 与 sample 模式。"""
    polars_cases = [case for case in CASES if case.expected == ALIGNED]
    fallback_cases = [case for case in CASES if case.expected == FALLBACK]
    assert len(polars_cases) + len(fallback_cases) == TOTAL_CASES
    assert len(polars_cases) / TOTAL_CASES >= MIN_POLARS_RATE
    fallback_capabilities = {case.capability for case in fallback_cases}
    assert fallback_capabilities == {"R5", "R6", "R8", "PIPE"}
    # 核心结构算子 6/6 对齐，且没有整节点回落。
    core = {"R1", "R2", "R3", "R4", "R7", "R9"}
    assert all(case.expected == ALIGNED for case in CASES if case.capability in core)
    assert not [capability for capability in CAPABILITIES if capability.polars_status == STATUS_FULL_FALLBACK]
    # R6 的明确格式（ISO、/、以及 pandas 可解析的中文年月日路径）必须由 Polars 执行。
    for case_id in ("R6-iso", "R6-slash", "R6-chinese-ymd"):
        assert CASE_BY_ID[case_id].expected == ALIGNED, case_id


def test_every_fallback_case_has_a_registered_decision():
    assert known_difference_problems() == ()
    for case in CASES:
        if case.expected == FALLBACK:
            difference = difference_for_case(case.case_id)
            assert difference is not None, case.case_id
            assert difference.decision == DECISION_FALLBACK, case.case_id
        if case.difference is not None:
            difference = difference_for_case(case.case_id)
            assert difference is not None, case.case_id
            assert difference.id == case.difference, case.case_id
    # 所有 open 的回落都能在 58 组用例里找到触发场景，不允许「登记了但没测」。
    for difference in KNOWN_DIFFERENCES:
        if difference.decision == DECISION_FALLBACK:
            assert difference.case in CASE_BY_ID or difference.case in BOUNDARY_GUARD_CASES, difference.id


# ---------------------------------------------------------------- 主对齐测试


@pytest.mark.parametrize("case", CASES, ids=[case.case_id for case in CASES])
def test_alignment_case(case: Case, monkeypatch: pytest.MonkeyPatch):
    """一组用例的 L1/L2/L3 三层比较；Polars 用例先拆掉 pandas 参考入口。"""
    if case.kind != "expression":
        decision = preflight(case.frame, case.options, sample=case.sample)
        if case.expected == ALIGNED:
            assert decision.uses_polars, (case.case_id, decision.reasons)
            assert case.capability not in decision.fallback_nodes or case.kind in ("pipeline", "benchmark")
        else:
            assert not decision.uses_polars, (case.case_id, decision.reasons)
            if case.capability != "PIPE":
                assert decision.reason_for(case.capability) is not None, case.case_id

    expected = case.runner("pandas")
    if case.expected == ALIGNED:
        _forbid_pandas(case, monkeypatch)
    actual = case.runner("polars")
    _assert_snapshot_equal(expected, actual)


# ---------------------------------------------------------------- 引擎行为


def test_registry_resolves_all_17_polars_implementations():
    """注册表 17/17 都标记已实现，且实现路径可解析为可调用对象。"""
    for capability in CAPABILITIES:
        assert capability.polars_implemented is True, capability.id
        assert capability.polars_impl is not None, capability.id
        assert callable(resolve_callable(capability.polars_impl)), capability.id
    assert get_capability("R5").polars_impl.endswith("convert_to_number")
    assert get_capability("R6").polars_impl.endswith("convert_to_date")
    assert get_capability("R8").polars_impl.endswith("add_computed_column")


def test_polars_engine_registers_and_default_stays_pandas():
    assert engine_base.DEFAULT_ENGINE == "pandas"
    assert isinstance(get_engine(), cleaning.engine.PandasEngine)
    assert isinstance(get_engine("polars"), PolarsEngine)
    assert get_engine("polars") is POLARS_ENGINE
    assert {"pandas", "polars"} <= set(engine_base.available_engines())


def test_preflight_is_read_only_and_deterministic():
    frame = _full_frame()
    before = frame.copy(deep=True)
    first = preflight(frame, _full_options())
    second = preflight(frame, _full_options())
    assert first == second
    pd.testing.assert_frame_equal(frame, before)


def test_sample_positive_falls_back_and_keeps_removed_samples_exact():
    frame = _full_frame()
    options = _full_options()
    decision = preflight(frame, options, sample=3)
    assert not decision.uses_polars
    assert decision.reason_for("engine") is not None
    expected = get_engine("pandas").clean_table(frame, options, sample=3)
    actual = POLARS_ENGINE.clean_table(frame, options, sample=3)
    pd.testing.assert_frame_equal(expected.df, actual.df)
    pd.testing.assert_frame_equal(expected.removed, actual.removed)
    assert expected.steps == actual.steps
    assert expected.notes == actual.notes


def test_deadline_is_checked_at_node_boundaries():
    class _Stop(Deadline):
        def __init__(self, label: str) -> None:
            super().__init__(None)
            self.label = label

        def check(self, what: str = "处理") -> None:
            if what == self.label:
                raise TableError(f"stop:{what}")

    frame = pd.DataFrame({"a": ["x", None, "x"], "b": ["1", "2", "3"]})
    with pytest.raises(TableError, match="stop:删除完全重复的行"):
        POLARS_ENGINE.clean_report(frame, drop_duplicates=True, deadline=_Stop("删除完全重复的行"))
    with pytest.raises(TableError, match="stop:列变换-转数字"):
        POLARS_ENGINE.clean_table(
            frame,
            CleanOptions(columns=ColumnOps(to_number=("b",))),
            deadline=_Stop("列变换-转数字"),
        )


def test_progress_reports_start_and_end_without_changing_results():
    calls: list[tuple[int, int]] = []
    frame = _full_frame()
    report = POLARS_ENGINE.clean_table(frame, _full_options(), progress=lambda done, total: calls.append((done, total)))
    assert calls
    assert calls[0] == (0, len(frame))
    assert calls[-1] == (len(frame), len(frame))
    assert report.steps


def test_runtime_exception_is_not_silently_swapped_for_pandas(monkeypatch: pytest.MonkeyPatch):
    def boom(self: pl.DataFrame, *args: Any, **kwargs: Any) -> pl.DataFrame:
        raise RuntimeError("pl boom")

    monkeypatch.setattr(pl.DataFrame, "unique", boom)
    frame = pd.DataFrame({"a": ["x", "x", "y"]})
    with pytest.raises(TableError, match="没有自动改用 pandas"):
        POLARS_ENGINE.clean_report(frame, drop_duplicates=True)


def test_default_install_does_not_import_polars():
    """默认安装（不带 polars extra）导入 cleaning/engine 也不应加载 Polars。"""
    code = (
        "import sys, cleaning, cleaning.engine\n"
        "assert 'polars' not in sys.modules, sorted(name for name in sys.modules if 'polars' in name)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_alignment_tests_do_not_hide_failures():
    """对齐测试不允许用 skip/xfail 把未对齐藏起来。"""
    source = (PROJECT_ROOT / "tests/test_v240_engine_alignment.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not any(isinstance(node, ast.Attribute) and node.attr in {"skip", "xfail"} for node in ast.walk(tree))


# ---------------------------------------------------------------- 低频边界与登记表


# 这些不是 58 组对齐矩阵的一部分，而是「预检必须显式回落」的守卫用例：
# 输入形态在真实 XLSX/接口调用里可能出现，但没有独立的算子能力编号。
BOUNDARY_GUARD_CASES = {"engine-dtype-fallback"}


def test_mixed_dtype_and_timezone_frames_fall_back_explicitly():
    """混合 object 列 / 带时区列无法映射 Polars：预检必须整条流水线回落 pandas。"""
    mixed = pd.DataFrame({"a": [1, "x", None], "b": [1.0, 2.0, 3.0]})
    timezone = pd.DataFrame({"d": pd.Series(pd.to_datetime(["2024-01-01", "2024-01-02"]).tz_localize("UTC"))})
    for frame in (mixed, timezone):
        decision = preflight(frame, CleanOptions(drop_duplicates=True))
        assert not decision.uses_polars
        assert decision.reason_for("engine") is not None
        expected = get_engine("pandas").clean_table(frame, CleanOptions(drop_duplicates=True))
        actual = POLARS_ENGINE.clean_table(frame, CleanOptions(drop_duplicates=True))
        # 带时区的帧 XLSX 本身不支持导出，这里只验证 L1/L3 与显式回落结论。
        _assert_l1_equal(expected.df, actual.df)
        pd.testing.assert_frame_equal(expected.removed, actual.removed)
        assert expected.steps == actual.steps
        assert expected.notes == actual.notes


def test_r8_floor_mod_expression_falls_back_without_changing_result():
    """R8 的 // % ** 边界按 KD-0007 回落；表达式能力 X1/X5 仍单独对齐。"""
    frame = pd.DataFrame({"a": [-7.0, 7.0], "b": [3.0, -3.0]})
    options = CleanOptions(columns=ColumnOps(computed=(("结果", "a // b + a % b"),)))
    decision = preflight(frame, options)
    assert not decision.uses_polars
    assert decision.reason_for("R8") is not None
    expected = get_engine("pandas").apply_transforms(frame, options.columns)
    actual = POLARS_ENGINE.apply_transforms(frame, options.columns)
    _assert_snapshot_equal(
        Snapshot(frame=expected[0], notes=tuple(expected[1])), Snapshot(frame=actual[0], notes=tuple(actual[1]))
    )
    assert difference_for_case("R8-pow-guard").id == "KD-0007"


def test_invalid_dates_fall_back_and_preserve_original():
    """非法日期（2024-02-30）不能被 Polars 猜成别的日期；回落 pandas 保留原文。"""
    frame = pd.DataFrame({"d": ["2024-02-30", "2024-05-01", ""]})
    options = CleanOptions(columns=ColumnOps(to_date=("d",)))
    decision = preflight(frame, options)
    assert not decision.uses_polars
    expected = get_engine("pandas").apply_transforms(frame, options.columns)
    actual = POLARS_ENGINE.apply_transforms(frame, options.columns)
    _assert_snapshot_equal(
        Snapshot(frame=expected[0], notes=tuple(expected[1])), Snapshot(frame=actual[0], notes=tuple(actual[1]))
    )
    assert "2024-02-30" in expected[0]["d"].tolist()


def test_malformed_thousands_separator_is_not_rewritten():
    """1,2 不是合法千分位，不能悄悄变成 12；需要回落 pandas 保留原文。"""
    frame = pd.DataFrame({"n": ["1,2", "100", ""]})
    options = CleanOptions(columns=ColumnOps(to_number=("n",)))
    assert not preflight(frame, options).uses_polars
    expected = get_engine("pandas").apply_transforms(frame, options.columns)
    actual = POLARS_ENGINE.apply_transforms(frame, options.columns)
    _assert_snapshot_equal(
        Snapshot(frame=expected[0], notes=tuple(expected[1])), Snapshot(frame=actual[0], notes=tuple(actual[1]))
    )
    assert expected[0]["n"].tolist()[0] == "1,2"


def test_fullwidth_expression_operators_use_the_same_translation():
    """全角括号/运算符先在两侧都归一为半角，再走同一白名单。"""
    frame = pd.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]})
    expression = "（a＋b）＊2"
    expected = transform.safe_eval(expression, frame)
    actual = evaluate_expression(expression, frame)
    _assert_series_logical_equal(expected, actual)


def test_structural_impl_helpers_match_pandas_reference():
    """注册表指向的结构算子实现入口可直接调用，且与 pandas 参考实现一致。"""
    frame = pd.DataFrame({"a": [1, 2], "b": [3, 4], "c": [5, 6]})
    for columns, expected in ((("b",), transform.apply_transforms(frame, ColumnOps(drop=("b",)))[0]),):
        _assert_l1_equal(expected, polars_engine_drop_columns(frame, list(columns)))
    _assert_l1_equal(
        transform.apply_transforms(frame, ColumnOps(rename=(("a", "x"),)))[0],
        polars_engine_rename_columns(frame, [("a", "x")]),
    )
    _assert_l1_equal(
        transform.apply_transforms(frame, ColumnOps(order=("c", "a")))[0],
        polars_engine_order_columns(frame, ["c", "a"]),
    )


def test_difference_lookup_helpers_and_table_shape():
    """登记表可被测试/计划描述按 id、能力、用例三种方式查询。"""
    assert get_difference("KD-0001").operator == "R5"
    assert {item.id for item in differences_for("R5")} == {"KD-0001", "KD-0002", "KD-0003", "KD-0004"}
    assert difference_for_case("no-such-case") is None
    assert len(KNOWN_DIFFERENCES) == 10
    assert {item.decision for item in KNOWN_DIFFERENCES} == {DECISION_ALIGN, DECISION_FALLBACK}
    assert all(item.status == "resolved" for item in KNOWN_DIFFERENCES if item.decision == DECISION_ALIGN)
    assert all(item.target == "v3.0" for item in KNOWN_DIFFERENCES if item.decision == DECISION_FALLBACK)
    assert known_difference_problems() == ()
