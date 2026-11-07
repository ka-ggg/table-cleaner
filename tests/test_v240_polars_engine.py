"""v2.4.0 Polars 引擎单元/错误路径测试（对齐矩阵之外的补充）。

对齐测试负责「58 组可观察结果」；这里补齐实现层的错误文案、辅助函数与预检边界，
让新模块的覆盖率与维护性达到 CP2 要求。所有断言仍以 pandas 参考实现为准。
"""

from __future__ import annotations

import re
from dataclasses import replace

import cleaning.engine.known_differences as known_differences
import cleaning.rules as rules
import cleaning.transform as transform
import pandas as pd
import pytest
from cleaning.engine import get_engine
from cleaning.engine.known_differences import KNOWN_DIFFERENCES, known_difference_problems
from cleaning.engine.polars_engine import (
    PolarsEngine,
    convert_to_date,
    convert_to_number,
    drop_columns,
    drop_duplicate_rows,
    drop_missing_rows,
    evaluate_abs,
    evaluate_expression,
    evaluate_pow_guard,
    evaluate_round,
    order_columns,
    preflight,
    rename_columns,
)
from cleaning.errors import Deadline, TableError
from cleaning.transform import CleanOptions, ColumnOps


def _assert_frame_equal(expected: pd.DataFrame, actual: pd.DataFrame) -> None:
    pd.testing.assert_frame_equal(expected, actual)


def _ops_result(frame: pd.DataFrame, ops: ColumnOps) -> tuple[pd.DataFrame, list[str]]:
    return transform.apply_transforms(frame, ops)


# ---------------------------------------------------------------- 直接实现入口


def test_convert_to_number_helper_matches_pandas():
    series = pd.Series(["1,000", "20.5", "-3.5", "", None], name="金额")
    expected, expected_failed = transform.convert_to_number(series)
    actual, actual_failed = convert_to_number(series)
    pd.testing.assert_series_equal(expected, actual)
    assert expected_failed == actual_failed == 0


def test_convert_to_date_helper_covers_all_supported_families():
    cases = (
        ["2024-05-01", "2024-5-2", "", None],
        ["2024/5/2", "2024/05/01", None],
        ["2024-05-01 12:30:00"],
        ["2024-05-02T00:00:00"],
        ["2024年5月1日", "2024年5月2日", None],
        [],
    )
    for values in cases:
        series = pd.Series(values, name="日期", dtype="str")
        expected, expected_failed = transform.convert_to_date(series)
        actual, actual_failed = convert_to_date(series)
        # 空列的 datetime64 单位可能不同（s vs us），属于 L1 允许的内部表示差异；
        # 逐值比较由主对齐测试的 L1/L2 负责。
        pd.testing.assert_series_equal(expected, actual, check_dtype=False)
        assert expected_failed == actual_failed


def test_convert_to_date_helper_rejects_mixed_families():
    with pytest.raises(TableError, match="无法由 Polars 统一解析"):
        convert_to_date(pd.Series(["2024-05-01", "2024/5/2"], name="日期"))


def test_structural_helpers_match_pandas_and_ignore_missing_columns():
    frame = pd.DataFrame({"a": [1, 2], "b": [3, 4], "c": [5, 6]})
    _assert_frame_equal(_ops_result(frame, ColumnOps(drop=("b",)))[0], drop_columns(frame, ["b"]))
    _assert_frame_equal(_ops_result(frame, ColumnOps(rename=(("a", "x"),)))[0], rename_columns(frame, [("a", "x")]))
    _assert_frame_equal(_ops_result(frame, ColumnOps(order=("c", "a")))[0], order_columns(frame, ["c", "a"]))
    # 不存在/冲突的列由调用方提示并忽略；辅助函数本身只做存在列的操作。
    _assert_frame_equal(frame, drop_columns(frame, ["z"]))
    _assert_frame_equal(frame, rename_columns(frame, [("z", "y")]))
    _assert_frame_equal(frame, rename_columns(frame, [("a", "b")]))
    _assert_frame_equal(frame, order_columns(frame, ["z"]))
    _assert_frame_equal(frame, drop_duplicate_rows(frame, subset=["a"]))
    _assert_frame_equal(frame, drop_missing_rows(frame, ["a", "b"]))


def test_scalar_expression_helpers_match_python_semantics():
    assert evaluate_round(2.675, 2) == round(2.675, 2)
    assert evaluate_abs(-3.5) == 3.5
    assert evaluate_pow_guard(2, 10) == 2**10
    with pytest.raises(TableError, match="保留位数"):
        evaluate_round(2.5, 1000)
    with pytest.raises(TableError, match="结果太大"):
        evaluate_pow_guard(10, 999_999)


# ---------------------------------------------------------------- 表达式错误路径


@pytest.mark.parametrize(
    ("expression", "fragment"),
    [
        ("", "空的"),
        ("a +", "语法有误"),
        ("missing + 1", "不是当前的列名"),
        ("'x' + 1", "只能用数字"),
        ("a & b", "只支持 + - * / // % **"),
        ("a and b", "不支持的写法"),
        ("eval(a)", "只支持 round() 和 abs()"),
        ("round(a, b)", "保留位数"),
        ("round(a, n=2)", "不支持关键字参数"),
        ("abs(a, b)", "只接受一个参数"),
        ("10 ** 999999", "结果太大"),
    ],
)
def test_expression_error_messages_are_chinese(expression: str, fragment: str):
    frame = pd.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]})
    with pytest.raises(TableError, match=re.escape(fragment)):
        evaluate_expression(expression, frame)


def test_expression_error_messages_match_pandas_for_shared_paths():
    frame = pd.DataFrame({"a": [1.0], "b": [2.0]})
    for expression in ("", "a +", "missing + 1", "'x' + 1", "a & b", "eval(a)", "round(a, n=2)"):
        with pytest.raises(TableError) as expected:
            transform.safe_eval(expression, frame)
        with pytest.raises(TableError) as actual:
            evaluate_expression(expression, frame)
        assert str(expected.value) == str(actual.value), expression


def test_bracket_aliases_and_fullwidth_operators_are_supported():
    frame = pd.DataFrame({"单价（元）": [10.0, 20.0], "数量": [2.0, 3.0]})
    expected = transform.safe_eval("[单价（元）] * 数量", frame)
    actual = evaluate_expression("[单价（元）] * 数量", frame)
    pd.testing.assert_series_equal(expected, actual, check_names=False)
    expected_fullwidth = transform.safe_eval("（[单价（元）]＋1）＊数量", frame)
    actual_fullwidth = evaluate_expression("（[单价（元）]＋1）＊数量", frame)
    pd.testing.assert_series_equal(expected_fullwidth, actual_fullwidth, check_names=False)


def test_constant_expression_returns_broadcast_series():
    frame = pd.DataFrame({"a": [1.0, 2.0, 3.0]})
    expected = transform.safe_eval("2 + 3", frame)
    actual = evaluate_expression("2 + 3", frame)
    pd.testing.assert_series_equal(expected, actual)


# ---------------------------------------------------------------- 预检的列状态模拟


def test_preflight_rename_then_compute_uses_the_new_column_name():
    frame = pd.DataFrame({"金额": ["1,000", "20.5"]})
    ops = ColumnOps(rename=(("金额", "金额2"),), computed=(("税额", "金额2 * 1.13"),))
    decision = preflight(frame, CleanOptions(columns=ops))
    assert decision.uses_polars
    expected = transform.apply_transforms(frame, ops)
    actual = get_engine("polars").apply_transforms(frame, ops)
    _assert_frame_equal(expected[0], actual[0])
    assert expected[1] == actual[1]


def test_preflight_dropped_conversion_column_is_ignored():
    frame = pd.DataFrame({"a": ["1,000"], "b": ["x"]})
    ops = ColumnOps(drop=("a",), to_number=("a",))
    decision = preflight(frame, CleanOptions(columns=ops))
    assert decision.uses_polars
    expected = transform.apply_transforms(frame, ops)
    actual = get_engine("polars").apply_transforms(frame, ops)
    _assert_frame_equal(expected[0], actual[0])
    assert expected[1] == actual[1]


def test_computed_name_conflict_falls_back_to_pandas_note():
    frame = pd.DataFrame({"a": [1.0], "b": [2.0]})
    ops = ColumnOps(computed=(("b", "a + 1"),))
    decision = preflight(frame, CleanOptions(columns=ops))
    assert not decision.uses_polars
    expected = transform.apply_transforms(frame, ops)
    actual = get_engine("polars").apply_transforms(frame, ops)
    _assert_frame_equal(expected[0], actual[0])
    assert expected[1] == actual[1]
    assert "重名" in expected[1][0]


def test_computed_referencing_prior_computed_column_falls_back():
    frame = pd.DataFrame({"a": [1.0]})
    ops = ColumnOps(computed=(("r1", "a + 1"), ("r2", "r1 * 2")))
    decision = preflight(frame, CleanOptions(columns=ops))
    assert not decision.uses_polars
    expected = transform.apply_transforms(frame, ops)
    actual = get_engine("polars").apply_transforms(frame, ops)
    _assert_frame_equal(expected[0], actual[0])
    assert expected[1] == actual[1]


# ---------------------------------------------------------------- 回落分支


def test_inspectors_fall_back_on_unmappable_frames():
    frame = pd.DataFrame({"a": [1, "x", None], "b": [1.0, 2.0, 3.0]})
    engine = PolarsEngine()
    pd.testing.assert_series_equal(engine.find_missing(frame), rules.find_missing(frame))
    assert engine.find_suspicious(frame) == rules.find_suspicious(frame)
    assert engine.summarize(frame) == rules.summarize(frame)


def test_clean_report_and_clean_table_fall_back_on_mixed_dtype():
    frame = pd.DataFrame({"a": [1, "x", None], "b": [1, 1, 2]})
    engine = PolarsEngine()
    options = CleanOptions(drop_duplicates=True, drop_missing_in=("a",))
    expected = transform.clean_table(frame, options)
    actual = engine.clean_table(frame, options)
    pd.testing.assert_frame_equal(expected.df, actual.df)
    pd.testing.assert_frame_equal(expected.removed, actual.removed)
    assert expected.steps == actual.steps
    assert expected.notes == actual.notes


# ---------------------------------------------------------------- 差异登记自检


def test_known_difference_self_check_catches_broken_entries(monkeypatch: pytest.MonkeyPatch):
    base = KNOWN_DIFFERENCES[0]
    broken = (
        replace(base, id="KD-1"),
        replace(base, id="KD-0001"),
        replace(base, operator=""),
        replace(base, case=""),
        replace(base, pandas=""),
        replace(base, polars=""),
        replace(base, impact=""),
        replace(base, reason=""),
        replace(base, decision="pending"),
        replace(base, scope="everywhere"),
        replace(base, status="done"),
        replace(base, target="v9.9"),
        replace(base, decision="align", status="open"),
        replace(base, decision="align", status="open", scope="boundary"),
        replace(base, decision="align", status="open", target="v2.4.0"),
        replace(base, decision="fallback", scope="common_path"),
        replace(base, decision="fallback", target="v2.4.0"),
        replace(base, decision="accepted_difference"),
        replace(base, reason="待定"),
    )
    monkeypatch.setattr(known_differences, "KNOWN_DIFFERENCES", broken)
    problems = "\n".join(known_difference_problems())
    for fragment in (
        "编号格式",
        "重复",
        "缺少 operator",
        "缺少触发用例",
        "缺少 pandas",
        "缺少 polars",
        "缺少 impact",
        "缺少 reason",
        "decision",
        "scope",
        "status",
        "target",
        "必须是 resolved",
        "只能是 fallback",
        "scope 不能是 common_path",
        "target 应写明",
        "accepted_difference",
        "未决措辞",
    ):
        assert fragment in problems, fragment
    monkeypatch.setattr(known_differences, "KNOWN_DIFFERENCES", ())
    assert "登记表为空" in "\n".join(known_difference_problems())


# ---------------------------------------------------------------- 覆盖补充


def test_preflight_result_accessors_and_reasons():
    frame = pd.DataFrame({"n": ["$1,200"]})
    result = preflight(frame, CleanOptions(columns=ColumnOps(to_number=("n",))))
    assert not result.uses_polars
    assert result.reason_for("R5")
    assert result.reason_for("R8") is None
    assert result.fallback_nodes == ("R5",)
    assert any("R5" in reason for reason in result.reasons)


def test_preflight_reports_non_finite_and_overflow_values():
    for value in ("nan", "1e999", "1E400"):
        frame = pd.DataFrame({"n": [value]})
        result = preflight(frame, CleanOptions(columns=ColumnOps(to_number=("n",))))
        assert not result.uses_polars, value
        assert result.reason_for("R5"), value


def test_preflight_reports_expression_boundaries_and_bad_columns():
    frame = pd.DataFrame({"a": [1.0], "b": [2.0], "t": ["abc"], "d": [pd.Timestamp("2024-01-01")]})
    expressions = {
        "a % b": "取模",
        "a // b": "整除",
        "round(a)": "缺少保留位数",
        "round(a, b)": "保留位数",
        "round(a, 1000)": "保留位数超过",
        "a + missing": "不存在",
        "t + 1": "含无法转换的数字",
        "d + 1": "不是数字",
        "a +": "语法有误",
    }
    for expression, fragment in expressions.items():
        result = preflight(frame, CleanOptions(columns=ColumnOps(computed=(("r", expression),))))
        assert not result.uses_polars, expression
        reason = result.reason_for("R8") or ""
        assert re.search(re.escape(fragment), reason), (expression, reason)


def test_expression_errors_for_non_numeric_and_long_inputs():
    frame = pd.DataFrame({"d": [pd.Timestamp("2024-01-01")]})
    with pytest.raises(TableError, match="不是数字"):
        evaluate_expression("d + 1", frame)
    with pytest.raises(TableError, match="太长"):
        evaluate_expression("a" * 300, pd.DataFrame({"a": [1.0]}))
    with pytest.raises(TableError, match="不支持位运算"):
        evaluate_expression("1 & 2", pd.DataFrame({"a": [1.0]}))
    with pytest.raises(TableError, match="只接受一个参数"):
        evaluate_expression("abs(1, 2)", pd.DataFrame({"a": [1.0]}))


def test_constant_unary_and_function_paths_are_implemented():
    frame = pd.DataFrame({"a": [1.0]})
    for expression in ("+2", "-2", "round(2.5, 0)", "round(a, -1)", "abs(-3)", "abs(a)"):
        expected = transform.safe_eval(expression, frame)
        actual = evaluate_expression(expression, frame)
        pd.testing.assert_series_equal(expected, actual, check_names=False)


def test_pandas_format_probe_handles_pandas_exceptions(monkeypatch: pytest.MonkeyPatch):
    from cleaning.engine import polars_engine

    def boom(*args: object, **kwargs: object) -> None:
        raise ValueError("boom")

    monkeypatch.setattr(pd, "to_datetime", boom)
    polars_engine._pandas_parses_format.cache_clear()
    try:
        assert polars_engine._pandas_parses_format("iso-date") is False
    finally:
        polars_engine._pandas_parses_format.cache_clear()


def test_structural_helpers_restore_native_dtypes():
    frame = pd.DataFrame(
        {
            "i": pd.Series([1, 2], dtype="int64"),
            "f": pd.Series([1.5, 2.5], dtype="float64"),
            "b": pd.Series([True, False], dtype=bool),
            "s": pd.Series(["x", "y"], dtype="string"),
            "d": pd.Series(pd.to_datetime(["2024-01-01", "2024-01-02"])),
            "c": pd.Series(["a", "b"], dtype="category"),
            "o": pd.Series([pd.Timestamp("2024-01-01").date(), None], dtype=object),
        }
    )
    expected = frame[["f", "i", "b", "s", "d", "c", "o"]]
    actual = order_columns(frame, ["f", "i"])
    pd.testing.assert_frame_equal(expected, actual)
    # object 列里是 datetime（不是 date）时不能误伤成 date。
    datetimes = pd.DataFrame({"o": pd.Series([pd.Timestamp("2024-01-01"), None], dtype=object)})
    pd.testing.assert_frame_equal(datetimes, order_columns(datetimes, ["o"]))
    numbers = pd.DataFrame({"o": pd.Series([1, None], dtype=object)})
    pd.testing.assert_frame_equal(numbers, order_columns(numbers, ["o"]))


def test_row_helpers_check_deadline_and_report_progress():
    calls: list[tuple[int, int]] = []
    frame = pd.DataFrame({"a": ["x", None, "x"], "b": [1, 2, 3]})
    deadline = Deadline(None)
    drop_duplicate_rows(frame, progress=lambda done, total: calls.append((done, total)), deadline=deadline)
    drop_duplicate_rows(
        frame, subset=["a"], progress=lambda done, total: calls.append((done, total)), deadline=deadline
    )
    drop_missing_rows(frame, ["a"], deadline=deadline)
    drop_missing_rows(frame, ["z"], deadline=deadline)
    assert calls == [(3, 3), (3, 3)]
    engine = PolarsEngine()
    engine.clean_report(
        frame,
        dedupe_on=["a"],
        drop_missing_in=["a"],
        deadline=deadline,
    )


def test_clean_report_and_clean_table_fallback_branches():
    mixed = pd.DataFrame({"a": [1, "x", None], "b": [1, 1, 2]})
    engine = PolarsEngine()
    expected = rules.clean_report(mixed, drop_duplicates=True)
    actual = engine.clean_report(mixed, drop_duplicates=True)
    pd.testing.assert_frame_equal(expected.df, actual.df)
    assert expected.steps == actual.steps
    # 无列变换时 clean_table 直接返回行清洗报告。
    plain = engine.clean_table(pd.DataFrame({"a": ["x", "x", "y"]}), CleanOptions(drop_duplicates=True))
    assert plain.steps == {"删除完全重复的行": 1}
    assert engine.preflight(pd.DataFrame({"a": [1, 2]})).uses_polars


def test_transform_helpers_cover_missing_columns_and_errors():
    from cleaning.engine.polars_engine import _apply_transforms_polars, _engine_error, add_computed_column

    frame = pd.DataFrame({"a": [1.0]})
    notes: list[str] = []
    add_computed_column(frame, "", "a + 1", notes)
    add_computed_column(frame, "a", "a + 1", notes)
    add_computed_column(frame, "r", "missing + 1", notes)
    add_computed_column(frame, "r", "a & 1", notes)
    assert len(notes) == 4
    transformed, transform_notes = _apply_transforms_polars(
        frame,
        ColumnOps(to_date=("missing",), rename=(("z", "y"),), order=("z",), computed=(("a", "a + 1"),)),
    )
    assert "missing" not in transformed.columns
    assert any("要转日期的列" in note for note in transform_notes)
    assert "没有自动改用 pandas" in str(_engine_error("测试", RuntimeError("boom")))


def test_clean_table_deadline_and_transform_error_wrapper(monkeypatch: pytest.MonkeyPatch):
    from cleaning.engine import polars_engine

    class _Stop(Deadline):
        def __init__(self, label: str) -> None:
            super().__init__(None)
            self.label = label

        def check(self, what: str = "处理") -> None:
            if what == self.label:
                raise TableError(f"stop:{what}")

    numbers = pd.DataFrame({"a": ["1,000", "2"]})
    dates = pd.DataFrame({"a": ["2024-05-01", "2024-05-02"]})
    with pytest.raises(TableError, match="stop:列变换"):
        PolarsEngine().clean_table(numbers, CleanOptions(columns=ColumnOps(to_number=("a",))), deadline=_Stop("列变换"))
    with pytest.raises(TableError, match="stop:列变换-转日期"):
        PolarsEngine().clean_table(
            dates,
            CleanOptions(columns=ColumnOps(to_date=("a",))),
            deadline=_Stop("列变换-转日期"),
        )
    with pytest.raises(TableError, match="stop:列变换-计算列"):
        PolarsEngine().clean_table(
            numbers,
            CleanOptions(columns=ColumnOps(computed=(("r", "2 * 3"),))),
            deadline=_Stop("列变换-计算列"),
        )
    with pytest.raises(TableError, match="stop:列变换-列顺序"):
        PolarsEngine().clean_table(
            numbers,
            CleanOptions(columns=ColumnOps(order=("a",))),
            deadline=_Stop("列变换-列顺序"),
        )
    frame = numbers

    def boom_transforms(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom transforms")

    monkeypatch.setattr(polars_engine, "_apply_transforms_polars", boom_transforms)
    with pytest.raises(TableError, match="没有自动改用 pandas"):
        PolarsEngine().apply_transforms(frame, ColumnOps(to_number=("a",)))
    with pytest.raises(TableError, match="没有自动改用 pandas"):
        PolarsEngine().clean_table(frame, CleanOptions(columns=ColumnOps(to_number=("a",))))


def test_nan_and_numeric_zero_semantics_match_pandas():
    import numpy as np

    duplicates = pd.DataFrame({"a": [np.nan, np.nan, 1.0], "b": ["x", "x", "y"]})
    expected = rules.clean_report(duplicates, drop_duplicates=True)
    actual = PolarsEngine().clean_report(duplicates, drop_duplicates=True)
    pd.testing.assert_frame_equal(expected.df, actual.df)
    missing = pd.DataFrame({"a": [0.0, np.nan, 1.0], "b": [1, 2, 3]})
    expected_missing = rules.clean_report(missing, drop_missing_in=["a"])
    actual_missing = PolarsEngine().clean_report(missing, drop_missing_in=["a"])
    pd.testing.assert_frame_equal(expected_missing.df, actual_missing.df)
    assert len(actual_missing.df) == 2


def test_leading_zero_and_fullwidth_numbers_follow_pandas():
    leading = pd.DataFrame({"n": ["00123", "000", ""]})
    ops = ColumnOps(to_number=("n",))
    assert preflight(leading, CleanOptions(columns=ops)).uses_polars
    expected = transform.apply_transforms(leading, ops)
    actual = PolarsEngine().apply_transforms(leading, ops)
    _assert_frame_equal(expected[0], actual[0])
    assert expected[1] == actual[1]
    assert expected[0]["n"].tolist()[:2] == [123, 0]
    # 全角数字不是 pandas 能直接识别的数字：整条流水线回落并保留原文。
    fullwidth = pd.DataFrame({"n": ["１２３", "４５"]})
    assert not preflight(fullwidth, CleanOptions(columns=ops)).uses_polars
    expected_fullwidth = transform.apply_transforms(fullwidth, ops)
    actual_fullwidth = PolarsEngine().apply_transforms(fullwidth, ops)
    _assert_frame_equal(expected_fullwidth[0], actual_fullwidth[0])
    assert expected_fullwidth[1] == actual_fullwidth[1]
    # Python 的 float/Decimal 接受下划线，但 pandas / Polars 都不接受；不能悄悄转。
    underscore = pd.DataFrame({"n": ["1_000", "1_0.5"]})
    assert not preflight(underscore, CleanOptions(columns=ops)).uses_polars
    expected_underscore = transform.apply_transforms(underscore, ops)
    actual_underscore = PolarsEngine().apply_transforms(underscore, ops)
    _assert_frame_equal(expected_underscore[0], actual_underscore[0])
    assert expected_underscore[1] == actual_underscore[1]
    assert expected_underscore[0]["n"].tolist() == ["1_000", "1_0.5"]
