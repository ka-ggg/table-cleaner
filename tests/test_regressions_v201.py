"""v2.0.1 回归测试：商业化审查发现并修复的 5 个问题。

每项都对应一条真实可复现的缺陷（旧版会失败），改动只在 cleaning/ 里：
1. 计算列 `9**9**9` 这类指数爆炸会把页面卡死（内存/CPU 耗尽）；
2. 「尝试转数字」/「转数字」会把超过 15 位的长编号悄悄改值（int64 截断、float64 丢精度）；
3. 日期列里混着带时区和不带时区的写法时，pandas 抛 ValueError，页面整页变成英文堆栈；
4. 模板套用时「列顺序」里引用改名后/计算出来的列名会被当成不存在的列丢掉，规则静默失效；
5. 数据区的合并单元格读出来是空的，勾「删除空值行」会误删，页面没有任何提示。
"""

from __future__ import annotations

import io

import cleaning
import openpyxl
import pandas as pd
import pytest

# ---------------------------------------------------------------- 1. 计算列指数爆炸


def test_huge_exponent_is_rejected_instead_of_hanging():
    """9**9**9 是 9 的 3.8 亿次方：旧版会真的去算（吃光内存），必须提前拦住。"""
    df = pd.DataFrame({"a": [1, 2]})
    for expr in ("9**9**9", "10**100000000", "2**2**64"):
        with pytest.raises(cleaning.TableError, match="结果太大"):
            cleaning.safe_eval(expr, df)


def test_reasonable_exponents_still_work():
    """守卫不能误伤正常算式：2**10、链式小指数、列参与的幂运算照常。"""
    df = pd.DataFrame({"a": ["2", "3"]})
    assert cleaning.safe_eval("2**10", df).tolist() == [1024, 1024]
    assert str(cleaning.safe_eval("(9**9)**9", df).iloc[0]) == str((9**9) ** 9)
    assert cleaning.safe_eval("a**2", df).tolist() == [4.0, 9.0]
    # 列参与的幂运算走浮点，溢出成 inf 由 add_computed_column 留空，不是「结果太大」
    assert cleaning.safe_eval("a**100000", df).map(lambda v: v == float("inf")).all()


def test_pow_blowup_does_not_freeze_clean_table():
    """走完整 clean_table 也不挂死：算不出来就记一条说明、跳过这一列。"""
    df = pd.DataFrame({"a": [1, 2]})
    report = cleaning.clean_table(df, cleaning.CleanOptions(columns=cleaning.ColumnOps(computed=(("b", "9**9**9"),))))
    assert "b" not in report.df.columns
    assert any("结果太大" in note for note in report.notes)


# ---------------------------------------------------------------- 2. 长编号精度


LONG_IDS = [
    "123456789012345678901234",  # 24 位：超过 int64，旧版会截成 9223372036854775807
    "1234567890123456789",  # 19 位：float64 丢精度（旧版变成 …6768）
    "9007199254740993",  # 2**53+1：float64 表示不了（旧版变成 …992）
    "99999999999999999999",  # 20 位
]


def test_convert_to_number_never_changes_long_ids():
    series = pd.Series(LONG_IDS + ["00123", "1,000", "A002"])
    converted, failed = cleaning.convert_to_number(series)
    # 4 个长编号 + 「A002」都算没转成功，原文保留
    assert failed == len(LONG_IDS) + 1
    assert converted.tolist()[: len(LONG_IDS)] == LONG_IDS
    # 短编号/千分位照常转（这是用户选「转数字」本来就要的效果）
    assert converted.tolist()[len(LONG_IDS) :] == [123, 1000, "A002"]


def test_number_mode_export_keeps_long_ids_as_text():
    """XLSX「尝试转数字」模式：长编号必须还是原文，不能写成 1.234567890123457e+23。"""
    df = pd.DataFrame({"id": LONG_IDS})
    out = cleaning.numbers_where_possible(df)
    assert out["id"].tolist() == LONG_IDS

    sheet = openpyxl.load_workbook(io.BytesIO(cleaning.to_xlsx_bytes(df, number_mode="number"))).active
    values = [row[0].value for row in sheet.iter_rows(min_row=2)]
    assert values == LONG_IDS  # 读回来还是完整字符串


def test_numeric_series_is_exact_for_15_digit_numbers():
    """15 位及以内的数字必须照常转换，精度守卫不能把正常数据也挡掉。"""
    series = pd.Series(["123456789012345", "0.1", "1E5", "1,234,567.89"])
    assert cleaning.numeric_series(series).tolist() == [123456789012345.0, 0.1, 100000.0, 1234567.89]
    # 溢出成 inf 的也不算转换成功，否则 CSV 里会出现 "inf"
    assert pd.isna(cleaning.numeric_series(pd.Series(["1E400"])).iloc[0])


def test_computed_column_does_not_silently_wrap_on_integer_overflow():
    """整数溢出：旧版 numpy int64 会静默回绕（4e9*4e9 变成负数，3**100000 变成怪数）。"""
    df = pd.DataFrame({"数量": ["4000000000"], "单价": ["4000000000"]})
    report = cleaning.clean_table(
        df, cleaning.CleanOptions(columns=cleaning.ColumnOps(computed=(("总额", "数量*单价"),)))
    )
    assert report.df["总额"].iloc[0] == pytest.approx(1.6e19, rel=1e-12)

    report = cleaning.clean_table(
        pd.DataFrame({"a": ["3"]}),
        cleaning.CleanOptions(columns=cleaning.ColumnOps(computed=(("b", "a**100000"),))),
    )
    assert pd.isna(report.df["b"].iloc[0])  # 溢出留空，不是回绕的假数字
    assert any("无穷大" in note for note in report.notes)


# ---------------------------------------------------------------- 3. 混合时区日期


def test_mixed_timezone_dates_keep_original_text_instead_of_crashing():
    series = pd.Series(["2024-05-01T10:00:00+08:00", "2024-05-02T10:00:00+09:00"])
    converted, failed = cleaning.convert_to_date(series)
    assert failed == 2
    assert converted.tolist() == series.tolist()  # 原文保留，不猜时区

    report = cleaning.clean_table(
        pd.DataFrame({"日期": series, "金额": [1, 2]}),
        cleaning.CleanOptions(columns=cleaning.ColumnOps(to_date=("日期",))),
    )
    assert any("没转成功" in note for note in report.notes)


def test_single_timezone_dates_drop_tz_but_keep_wall_clock():
    """同一时区的日期：保留当地时间的日期，不带时区（Excel 不支持时区）。"""
    series = pd.Series(["2024-05-01T23:00:00-08:00"])
    converted, failed = cleaning.convert_to_date(series)
    assert failed == 0
    assert converted.iloc[0] == pd.Timestamp("2024-05-01 23:00:00")
    assert converted.iloc[0].tzinfo is None


# ---------------------------------------------------------------- 4. 模板列顺序


def test_template_keeps_column_order_for_renamed_and_computed_columns():
    rules = {
        "列重命名": [["金额", "总金额"]],
        "列顺序": ["总金额", "订单号", "含税"],
        "计算列": [["含税", "总金额*1.13"]],
    }
    filtered, notes = cleaning.filter_for_columns(rules, ["订单号", "金额"])
    assert filtered["列顺序"] == ["总金额", "订单号", "含税"]
    assert filtered["列重命名"] == [["金额", "总金额"]]
    assert notes == []  # 改名后的列不是「当前文件没有的列」，不能瞎提示


def test_template_still_drops_columns_the_file_really_lacks():
    rules = {"列顺序": ["订单号", "备注"], "删除列": ["备注"]}
    filtered, notes = cleaning.filter_for_columns(rules, ["订单号", "金额"])
    assert filtered["列顺序"] == ["订单号"]
    assert filtered["删除列"] == []
    assert notes == ["模板里有当前文件没有的列，已忽略：备注。"]


# ---------------------------------------------------------------- 5. 合并单元格提示


def _merged_workbook() -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "报表"
    sheet.append(["分类", "商品", "金额"])
    sheet.append(["水果", "苹果", 10])
    sheet.append(["水果", "梨", 20])
    sheet.append(["蔬菜", "白菜", 5])
    sheet.merge_cells("A2:A3")  # 数据区合并：第 2 行「分类」读出来是空的
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_merged_data_cells_are_reported_before_deleting_empty_rows():
    loaded = cleaning.load_table("报表.xlsx", _merged_workbook())
    assert any("合并单元格压在数据行上" in note for note in loaded.notes)
    # 这个提示不是空话：直接删空值行确实会删掉合并区域里的那一行
    assert len(cleaning.clean_data(loaded.df, drop_missing_in=["分类"])) == 2


def test_merged_header_only_is_not_reported_as_data_merge():
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["标题"])
    sheet.append(["a", "b"])
    sheet.append([1, 2])
    sheet.merge_cells("A1:B1")  # 只在表头上方的合并，不该报「压在数据行上」
    buffer = io.BytesIO()
    book.save(buffer)
    loaded = cleaning.load_table("标题.xlsx", buffer.getvalue())
    assert not any("压在数据行上" in note for note in loaded.notes)


# ---------------------------------------------------------------- 文档口径


def test_version_is_bumped_after_fixes():
    assert cleaning.__version__ == "2.4.0"


# ---------------------------------------------------------------- 第二轮复审补充


def test_pure_integer_id_column_keeps_text_after_number_conversion():
    """整列都是整齐的 18 位整数时，pandas 会给出精确的 Int64——
    守卫若按 int64 比较就会失效；导出的 XLSX 里两个不同的身份证号会并成同一个值。
    第一版修复漏了这条路径（第一轮测试的长编号里混了 24 位，迫使整列走 float64）。
    """
    ids = ["110101199003071234", "110101199003071235", "9999999999999999"]
    series = pd.Series(ids)

    assert cleaning.numeric_series(series).isna().all()
    converted, failed = cleaning.convert_to_number(series)
    assert failed == 3
    assert converted.tolist() == ids
    assert cleaning.numbers_where_possible(pd.DataFrame({"id": series}))["id"].tolist() == ids

    # 15 位以内、且在 float64 里能精确表示的数字仍照常转换（不能把正常数据全挡住）
    exact = pd.Series(["9007199254740992", "1234567890123456", "00123"])
    assert cleaning.numeric_series(exact).tolist() == [9007199254740992, 1234567890123456, 123]


def test_round_with_huge_ndigits_is_rejected():
    """round(1, -10000000) 会去算 10 的 1000 万次方（实测 18 秒），必须提前拦掉。"""
    df = pd.DataFrame({"金额": [1.5]})
    for expr in ("round(1, -10000000)", "round(1, -31)", "round(金额, 100)"):
        with pytest.raises(cleaning.TableError, match="保留位数"):
            cleaning.safe_eval(expr, df)
    assert cleaning.safe_eval("round(1.2345, 2)", df).iloc[0] == 1.23
    assert cleaning.safe_eval("round(金额, 0)", df).iloc[0] == 2


def test_negative_percentage_is_number_not_formula():
    """-3% 在 Excel 里是数字，不能被当成公式加前缀（否则导出一趟就变成 '-3%）。"""
    assert not cleaning._looks_like_formula("-3%")
    assert not cleaning._looks_like_formula("+12.5%")
    assert cleaning._looks_like_formula("-3%+1")  # 不是纯数字，仍然算可疑

    out = cleaning.to_csv_bytes(pd.DataFrame({"m": ["-3%", "-1,000", "=1+1"]}))
    back = pd.read_csv(io.BytesIO(out), dtype=str, keep_default_na=False)
    assert back["m"].tolist() == ["-3%", "-1,000", "'=1+1"]


def test_xlsx_text_number_cells_keep_leading_zeros():
    """XLSX 里以文本保存的 00123，读进来必须还是 "00123"（否则再导出就丢前导零）。"""
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["编号", "金额", "日期"])
    sheet.append(["00123", 100, pd.Timestamp("2024-05-01")])
    sheet.append(["00000045", 200, None])
    buffer = io.BytesIO()
    book.save(buffer)

    loaded = cleaning.load_table("编号.xlsx", buffer.getvalue())
    assert loaded.df["编号"].tolist() == ["00123", "00000045"]
    # 数字/日期列仍按正常类型读，不因为「保文本」把整张表变成字符串
    assert loaded.df["金额"].tolist() == [100, 200]
    assert str(loaded.df["日期"].dtype).startswith("datetime64")
    assert cleaning.to_csv_bytes(loaded.df).decode("utf-8-sig").splitlines()[1] == "00123,100,2024-05-01"
