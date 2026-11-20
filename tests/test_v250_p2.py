"""v2.5.0 W3：未生效规则汇总、上限入口、转换预览（规划 5.6 / 5.7 / 5.8）。

G-10 的纯函数部分直接 import ``app`` 验证六类触发与零误报；界面部分用 AppTest
跑真实页面。G-11/G-12 全部走 AppTest，避免只测到内部函数而没测到渲染位置。
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import cleaning
import pandas as pd
import pytest

from ui_selectors import _button, _by_label, _checkbox, _multiselect

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"

try:
    import streamlit  # noqa: F401
    from streamlit.testing import v1 as streamlit_testing
except ImportError as exc:
    missing = getattr(exc, "name", None) or str(exc)
    pytest.skip(
        f"界面测试需要 streamlit，但导入失败（{missing}）；先 pip install -r requirements.txt 再跑。",
        allow_module_level=True,
    )

AppTest = streamlit_testing.AppTest

if not hasattr(streamlit_testing.element_tree, "FileUploader"):
    pytest.skip(
        "当前 Streamlit 的 AppTest 不支持 file_uploader，界面测试整体跳过。",
        allow_module_level=True,
    )

# 验证纯函数需要 import app；底部已有 __main__ 守卫，导入时不会启动页面。
import app as app_module  # noqa: E402

MIXED_CSV = (
    "编号,金额,日期\nA1,00123,2024-05-01\nA2,A002,2024-05-02\nA3,45,2024-05-03\nA4,6,2024-05-04\nA5,7,2024-05-05\n"
).encode("utf-8-sig")

MANY_BAD_CSV = "编号,金额\nA1,00123\nA2,A002\nA3,B003\nA4,4\n".encode("utf-8-sig")

TIMEZONE_CSV = ("编号,时间\nA1,2024-05-01T00:00:00+08:00\nA2,2024-05-02\nA3,2024-05-03T00:00:00+08:00\n").encode(
    "utf-8-sig"
)

LONG_TEXT_CSV = ("编号,金额\nA1," + "9" * 40 + "\nA2,1\n").encode("utf-8-sig")

PRODUCT_CSV = "商品,单价,数量\n苹果,10,2\n梨,20,3\n".encode("utf-8-sig")

MISMATCH_CSV = "名称,金额\n甲,1\n乙,2\n".encode("utf-8-sig")

TWO_COL_CSV = "编号,金额\nA1,1\nA2,2\n".encode("utf-8-sig")

VALID_CSV = ("编号,金额,日期,备注\nA1,10,2024-05-01,首行\nA2,20,2024-05-02,\n").encode("utf-8-sig")


def _captions(at) -> list[str]:
    return [str(caption.value) for caption in at.caption]


def _warnings(at) -> list[str]:
    return [str(warning.value) for warning in at.warning]


def _ineffective_warnings(at) -> list[str]:
    return [text for text in _warnings(at) if text.startswith("以下规则本次没有生效：")]


def _preview_captions(at) -> list[str]:
    return [text for text in _captions(at) if text.startswith(("转数字「", "转日期「"))]


def _preview_frames(at) -> list[pd.DataFrame]:
    return [frame.value for frame in at.dataframe if list(frame.value.columns) == ["原值", "转换后"]]


def _message_of_interest(at) -> list[str]:
    return _ineffective_warnings(at)


def _two_sheet_xlsx() -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"编号": ["A1", "A2"]}).to_excel(writer, index=False, sheet_name="订单")
        pd.DataFrame({"编号": ["B1", "B2"]}).to_excel(writer, index=False, sheet_name="客户")
    return buffer.getvalue()


def _large_xlsx() -> bytes:
    """解压后约 4 MB、压缩后很小的 XLSX，用来验证会话内的 XLSX 解压上限。"""
    buffer = BytesIO()
    pd.DataFrame({"内容": ["x" * 100] * 25_000}).to_excel(buffer, index=False, engine="openpyxl")
    return buffer.getvalue()


@pytest.fixture()
def app():
    def _new():
        at = AppTest.from_file(str(APP_PATH), default_timeout=180).run()
        assert not list(at.exception), list(at.exception)
        return at

    return _new


# ---------------------------------------------------------------- G-10 纯函数


def test_collect_returns_empty_without_rules():
    assert app_module.collect_ineffective_rules(cleaning.CleanOptions(), ["编号"], [], (), ()) == []


def test_collect_flags_drop_missing_without_columns():
    issues = app_module.collect_ineffective_rules(
        cleaning.CleanOptions(), ["编号"], [], (), (), is_drop_missing_checked=True
    )
    assert len(issues) == 1
    assert "删除指定列中有空值的行" in issues[0]


def test_collect_flags_dedupe_without_columns():
    issues = app_module.collect_ineffective_rules(cleaning.CleanOptions(), ["编号"], [], (), (), is_dedupe_checked=True)
    assert len(issues) == 1
    assert "按指定列去重" in issues[0]


@pytest.mark.parametrize(
    ("ops", "label"),
    [
        ({"drop": ("缺列",)}, "删列"),
        ({"to_number": ("缺列",)}, "转数字"),
        ({"to_date": ("缺列",)}, "转日期"),
        ({"rename": (("缺列", "新名"),)}, "重命名"),
    ],
)
def test_collect_missing_columns(ops, label):
    options = cleaning.CleanOptions(columns=cleaning.ColumnOps(**ops))
    issues = app_module.collect_ineffective_rules(options, ["编号", "金额"], [], (), ())
    assert any(label in issue and "缺列" in issue for issue in issues), issues


def test_collect_missing_order_from_report():
    """列顺序用执行后的列名，只能从 report.notes 判断；配置阶段不直接比对。"""
    notes = ("要调整顺序的列不存在，已忽略：缺列。",)
    issues = app_module.collect_ineffective_rules(cleaning.CleanOptions(), ["编号"], [], notes, ())
    assert any("列顺序" in issue and "缺列" in issue for issue in issues), issues


def test_collect_valid_rules_and_noisy_notes_do_not_warn():
    options = cleaning.CleanOptions(
        dedupe_on=("编号",),
        drop_missing_in=("金额",),
        columns=cleaning.ColumnOps(
            drop=("备注",),
            to_number=("金额",),
            to_date=("日期",),
            rename=(("编号", "单号"),),
            computed=(("总额", "金额*2"),),
            order=("单号", "总额"),
        ),
    )
    notes = (
        "「金额」转数字时有 1 个单元格没转成功，已保留原文（例如「¥」）；需要的话请手工修正这些格子。",
        "计算列「总额」有 2 个单元格的结果是无穷大（除以 0 或数字太大），已留空。",
        "该文件有 2 个工作表，本次只读取「Sheet1」；其他表不读取。",
        "文件包含合并单元格，已按左上角的值读取。",
    )
    assert app_module.collect_ineffective_rules(options, ["编号", "金额", "日期", "备注"], [], notes, ()) == []


def test_collect_order_with_future_name_is_not_false_positive():
    """列顺序用改名后的新列名；拿原始列名比对会误报，这里必须保持安静。"""
    options = cleaning.CleanOptions(
        columns=cleaning.ColumnOps(rename=(("编号", "单号"),), order=("单号",)),
    )
    assert app_module.collect_ineffective_rules(options, ["编号", "金额"], [], (), ()) == []


def test_collect_supplements_missing_from_report_after_drop():
    options = cleaning.CleanOptions(columns=cleaning.ColumnOps(rename=(("金额", "总金额"),)))
    notes = ("要重命名的列「金额」不存在，已忽略。",)
    issues = app_module.collect_ineffective_rules(options, ["编号", "金额"], [], notes, ())
    assert any("重命名规则引用了不存在的列：金额" in issue for issue in issues), issues


def test_collect_reports_rename_collision():
    notes = ("列名「金额」已经存在，没有把「日期」改成它。",)
    issues = app_module.collect_ineffective_rules(cleaning.CleanOptions(), ["编号", "金额"], [], notes, ())
    assert any("重命名「日期」没有生效" in issue and "金额" in issue for issue in issues), issues


@pytest.mark.parametrize(
    "note",
    [
        "计算列「总额」没有生成：表达式「缺失」算不出来。",
        "计算列「总额」和已有列重名，已跳过；请换个列名。",
        "计算列里有空列名，已跳过（表达式「x」）。",
    ],
)
def test_collect_computed_column_not_generated(note):
    issues = app_module.collect_ineffective_rules(cleaning.CleanOptions(), ["编号"], [], (note,), ())
    assert any("计算列" in issue and issue.startswith("计算列") for issue in issues), issues


@pytest.mark.parametrize(
    "problem",
    [
        "第 1 行「数量 件数」缺少等号，格式应为「旧列名=新列名」。",
        "第 2 行等号两边有空的，格式应为「新列名=表达式」。",
    ],
)
def test_collect_text_format_problems(problem):
    issues = app_module.collect_ineffective_rules(cleaning.CleanOptions(), ["编号"], [problem], (), ())
    assert len(issues) == 1
    assert "文本格式有误" in issues[0] and problem in issues[0]


def test_collect_template_notes_are_reported():
    notes = ("模板里有当前文件没有的列，已忽略：数量。",)
    issues = app_module.collect_ineffective_rules(cleaning.CleanOptions(), ["编号"], [], (), notes)
    assert issues == ["模板里有当前文件没有的列，已忽略：数量"]


def test_collect_deduplicates_direct_and_report_notes():
    options = cleaning.CleanOptions(columns=cleaning.ColumnOps(to_number=("缺列",)))
    notes = ("要转数字的列「缺列」不存在，已忽略。",)
    issues = app_module.collect_ineffective_rules(options, ["编号"], [], notes, ())
    assert sum("转数字规则" in issue for issue in issues) == 1, issues


def test_collect_ignores_conversion_failures_and_infinity_notes():
    notes = (
        "「金额」转日期时有 2 个单元格没转成功，已保留原文（例如「下月」）；常见写法如 2024-05-01、2024/5/1。",
        "计算列「总额」有 1 个单元格的结果是无穷大（除以 0 或数字太大），已留空。",
    )
    assert app_module.collect_ineffective_rules(cleaning.CleanOptions(), ["金额"], [], notes, ()) == []


def test_collect_ignores_worksheet_skip_note():
    notes = ("这些工作表超出单表上限，已跳过：客户。", "该文件有 3 个工作表，本次只读取「订单」；其他表不读取。")
    assert app_module.collect_ineffective_rules(cleaning.CleanOptions(), ["编号"], [], notes, ()) == []


# ---------------------------------------------------------------- G-10 界面


def test_export_warning_for_empty_drop_target_and_disappears_after_select(app):
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", TWO_COL_CSV, "text/csv").run()
    _checkbox(at, "删除指定列中有空值的行").check().run()

    warnings = _message_of_interest(at)
    assert len(warnings) == 1
    assert "已勾选「删除指定列中有空值的行」，但没有选择列" in warnings[0]
    assert "导出的结果不会包含它们" in warnings[0]

    _multiselect(at, "选择要检查空值的列").select("编号").run()
    assert _message_of_interest(at) == []


def test_export_warning_for_bad_rename_and_disappears_after_fix(app):
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", TWO_COL_CSV, "text/csv").run()
    _by_label(at.text_area, "列重命名").input("缺列=新名").run()

    warnings = _message_of_interest(at)
    assert len(warnings) == 1
    assert "重命名规则引用了不存在的列：缺列" in warnings[0]

    _by_label(at.text_area, "列重命名").input("编号=单号").run()
    assert _message_of_interest(at) == []


def test_export_warning_for_failed_computed_column(app):
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", TWO_COL_CSV, "text/csv").run()
    _by_label(at.text_area, "计算列").input("总额=金额*缺列").run()

    warnings = _message_of_interest(at)
    assert len(warnings) == 1
    assert "计算列「总额」没有生成" in warnings[0]


def test_export_warning_for_template_filtered_entries(app):
    at = app()
    at.get("file_uploader")[0].upload("商品.csv", PRODUCT_CSV, "text/csv").run()
    _checkbox(at, "按指定列去重").check().run()
    _multiselect(at, "选择去重用的列").select("数量").run()
    _by_label(at.text_area, "列重命名").input("商品=品名").run()
    _by_label(at.text_input, "模板名称").input("商品模板").run()
    _button(at, "保存当前规则为模板").click().run()

    at.get("file_uploader")[0].upload("另一个.csv", MISMATCH_CSV, "text/csv").run()
    _button(at, "一键套用「商品模板」").click().run()
    assert not list(at.exception)

    warnings = _message_of_interest(at)
    assert len(warnings) == 1
    assert "模板里有当前文件没有的列" in warnings[0]


def test_export_warning_does_not_block_download_and_persists(app):
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", TWO_COL_CSV, "text/csv").run()
    _checkbox(at, "删除指定列中有空值的行").check().run()
    assert _message_of_interest(at)

    _button(at, "生成下载文件").click().run()
    assert not list(at.exception)
    assert "exports" in at.session_state
    assert "下载 CSV" in [button.label for button in at.get("download_button")]
    assert _message_of_interest(at), "生成按钮之后提示不能消失"


def test_export_no_warning_for_valid_rules(app):
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", VALID_CSV, "text/csv").run()
    _checkbox(at, "删除完全重复的行").check().run()
    _checkbox(at, "删除指定列中有空值的行").check().run()
    _multiselect(at, "选择要检查空值的列").select("备注").run()
    _by_label(at.text_area, "列重命名").input("编号=单号").run()
    _by_label(at.text_area, "计算列").input("总额=金额*2").run()

    assert _message_of_interest(at) == []
    assert not list(at.exception)


def test_export_no_warning_for_worksheet_skip(app):
    at = app()
    at.get("file_uploader")[0].upload("两表.xlsx", _two_sheet_xlsx()).run()
    assert any("本次只读取" in str(info.value) for info in at.info)
    assert _message_of_interest(at) == []


# ---------------------------------------------------------------- G-11 上限


def test_upload_caption_matches_default_limits(app):
    at = app()
    limits = cleaning.current_limits()
    caption = next(text for text in _captions(at) if text.startswith("当前上限："))
    assert f"单文件 {limits.max_upload_mb} MB" in caption
    assert f"{limits.max_rows:,} 行" in caption
    assert f"{limits.max_cols:,} 列" in caption
    assert f"{limits.max_cells:,} 单元格" in caption
    assert f"XLSX 解压后 {limits.max_xlsx_uncompressed_mb} MB" in caption
    assert "可在左侧「高级设置」临时调大（只影响本次会话）" in caption


def test_sidebar_has_new_limit_inputs(app):
    at = app()
    labels = {widget.label for widget in at.number_input}
    assert {"单表列数上限", "单元格总数上限", "XLSX 解压后上限（MB）"} <= labels


def test_sidebar_max_rows_override_rejects_upload_with_hint(app):
    at = app()
    _by_label(at.number_input, "单表行数上限").set_value(1).run()
    at.get("file_uploader")[0].upload("订单.csv", TWO_COL_CSV, "text/csv").run()

    errors = [str(error.value) for error in at.error]
    assert any("行数 2 超过上限 1 行" in text for text in errors), errors
    assert any("高级设置" in str(info.value) for info in at.info)


def test_sidebar_max_cols_override_rejects_upload_with_hint(app):
    at = app()
    _by_label(at.number_input, "单表列数上限").set_value(2).run()
    at.get("file_uploader")[0].upload("三列.csv", "a,b,c\n1,2,3\n".encode("utf-8-sig"), "text/csv").run()

    errors = [str(error.value) for error in at.error]
    assert any("列数 3 超过上限 2 列" in text for text in errors), errors
    assert any("高级设置" in str(info.value) for info in at.info)


def test_sidebar_max_cells_override_rejects_upload_with_hint(app):
    at = app()
    _by_label(at.number_input, "单表行数上限").set_value(100).run()
    _by_label(at.number_input, "单表列数上限").set_value(100).run()
    _by_label(at.number_input, "单元格总数上限").set_value(2).run()
    at.get("file_uploader")[0].upload("订单.csv", TWO_COL_CSV, "text/csv").run()

    errors = [str(error.value) for error in at.error]
    assert any("单元格" in text and "超过上限" in text for text in errors), errors
    assert any("高级设置" in str(info.value) for info in at.info)


def test_sidebar_xlsx_override_rejects_upload_with_hint(app):
    at = app()
    _by_label(at.number_input, "XLSX 解压后上限（MB）").set_value(1).run()
    at.get("file_uploader")[0].upload("大表.xlsx", _large_xlsx()).run()

    errors = [str(error.value) for error in at.error]
    assert any("XLSX 解压后" in text and "超过上限 1 MB" in text for text in errors), errors
    assert any("高级设置" in str(info.value) for info in at.info)


def test_non_limit_read_error_has_no_limit_hint(app):
    at = app()
    at.get("file_uploader")[0].upload("说明.txt", b"hello", "text/plain").run()

    assert list(at.error)
    assert not any("高级设置" in str(info.value) for info in at.info)


def test_sidebar_override_updates_upload_caption(app):
    at = app()
    _by_label(at.number_input, "单表行数上限").set_value(123).run()
    caption = next(text for text in _captions(at) if text.startswith("当前上限："))
    assert "123 行" in caption


def test_sidebar_xlsx_override_updates_caption(app):
    at = app()
    _by_label(at.number_input, "XLSX 解压后上限（MB）").set_value(321).run()
    caption = next(text for text in _captions(at) if text.startswith("当前上限："))
    assert "XLSX 解压后 321 MB" in caption


def test_sidebar_override_does_not_change_defaults(app):
    at = app()
    _by_label(at.number_input, "单表行数上限").set_value(7).run()
    _by_label(at.number_input, "单表列数上限").set_value(8).run()
    assert cleaning.current_limits().max_rows == 500_000
    assert cleaning.current_limits().max_cols == 1_000
    assert at.session_state["上限_行数"] == 7
    assert at.session_state["上限_列数"] == 8


# ---------------------------------------------------------------- G-12 预览


def test_preview_hidden_without_conversion_selection(app):
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", MIXED_CSV, "text/csv").run()
    assert _preview_captions(at) == []
    assert _preview_frames(at) == []


def test_preview_number_mixed_shows_conversion_and_preserve(app):
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", MIXED_CSV, "text/csv").run()
    _multiselect(at, "转成数字的列").select("金额").run()

    captions = _preview_captions(at)
    assert len(captions) == 1
    assert captions[0] == "转数字「金额」：前 5 个中 4 个可转换；整列 1 个值将保留原文。"
    frames = _preview_frames(at)
    assert len(frames) == 1
    rows = frames[0].to_dict("records")
    assert rows[0] == {"原值": "00123", "转换后": "123"}
    assert rows[1] == {"原值": "A002", "转换后": "保留原文"}


def test_preview_caption_counts_all_failures(app):
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", MANY_BAD_CSV, "text/csv").run()
    _multiselect(at, "转成数字的列").select("金额").run()

    caption = _preview_captions(at)[0]
    assert "前 4 个中 2 个可转换" in caption
    assert "整列 2 个值将保留原文" in caption


def test_preview_date_mixed_timezone_keeps_original(app):
    at = app()
    at.get("file_uploader")[0].upload("时间.csv", TIMEZONE_CSV, "text/csv").run()
    _multiselect(at, "转成日期的列").select("时间").run()

    assert not list(at.exception)
    caption = _preview_captions(at)[0]
    assert caption.startswith("转日期「时间」")
    assert "3 个值将保留原文" in caption
    assert "整列保留原文" in caption


def test_preview_only_selected_columns_are_converted(app, monkeypatch):
    calls: list[str] = []
    real_number = cleaning.convert_to_number
    real_date = cleaning.convert_to_date

    def spy_number(series):
        calls.append(str(series.name))
        return real_number(series)

    def spy_date(series):
        calls.append(str(series.name))
        return real_date(series)

    monkeypatch.setattr(cleaning, "convert_to_number", spy_number)
    monkeypatch.setattr(cleaning, "convert_to_date", spy_date)

    at = app()
    at.get("file_uploader")[0].upload("订单.csv", MIXED_CSV, "text/csv").run()
    _multiselect(at, "转成数字的列").select("金额").run()

    assert set(calls) == {"金额"}


def test_preview_cache_avoids_recompute(app, monkeypatch):
    calls: list[str] = []
    real_number = cleaning.convert_to_number

    def spy_number(series):
        calls.append(str(series.name))
        return real_number(series)

    monkeypatch.setattr(cleaning, "convert_to_number", spy_number)

    at = app()
    at.get("file_uploader")[0].upload("订单.csv", MIXED_CSV, "text/csv").run()
    _multiselect(at, "转成数字的列").select("金额").run()
    assert calls

    calls.clear()
    at.run()
    assert calls == []
    assert _preview_captions(at)


def test_preview_cache_invalidated_after_source_change(app, monkeypatch):
    calls: list[str] = []
    real_number = cleaning.convert_to_number

    def spy_number(series):
        calls.append(str(series.name))
        return real_number(series)

    monkeypatch.setattr(cleaning, "convert_to_number", spy_number)

    at = app()
    at.get("file_uploader")[0].upload("订单.csv", MIXED_CSV, "text/csv").run()
    _multiselect(at, "转成数字的列").select("金额").run()
    calls.clear()

    at.get("file_uploader")[0].upload("另一批.csv", MANY_BAD_CSV, "text/csv").run()
    assert calls, "换文件后预览缓存必须失效并重算"


def test_preview_truncates_long_text(app):
    at = app()
    at.get("file_uploader")[0].upload("长文本.csv", LONG_TEXT_CSV, "text/csv").run()
    _multiselect(at, "转成数字的列").select("金额").run()

    rows = _preview_frames(at)[0].to_dict("records")
    long_value = rows[0]["原值"]
    assert long_value.endswith("…")
    assert len(long_value) <= app_module._PREVIEW_MAX_CHARS + 1


def test_preview_does_not_change_export_result(app):
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", MIXED_CSV, "text/csv").run()
    _multiselect(at, "转成数字的列").select("金额").run()
    _button(at, "生成下载文件").click().run()

    loaded = cleaning.load_table("订单.csv", MIXED_CSV)
    expected = cleaning.clean_table(
        loaded.df,
        cleaning.CleanOptions(columns=cleaning.ColumnOps(to_number=("金额",))),
    ).df
    assert at.session_state["exports"]["csv"] == cleaning.to_csv_bytes(expected)


def test_preview_keeps_widget_state(app):
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", MIXED_CSV, "text/csv").run()
    _multiselect(at, "转成数字的列").select("金额").run()

    assert _multiselect(at, "转成数字的列").value == ["金额"]
    assert _multiselect(at, "转成日期的列").value == []
    assert at.session_state["规则_转数字"] == ["金额"]
    assert at.session_state["规则_转日期"] == []


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        (
            {"kind": "number", "column": "金额", "sample_count": 0, "convertible": 0, "failed": 0, "non_empty": 0},
            "转数字「金额」：该列没有非空值。",
        ),
        (
            {"kind": "number", "column": "金额", "sample_count": 5, "convertible": 5, "failed": 0, "non_empty": 5},
            "转数字「金额」：前 5 个中 5 个可转换；整列没有值会保留原文。",
        ),
        (
            {"kind": "date", "column": "日期", "sample_count": 3, "convertible": 0, "failed": 3, "non_empty": 3},
            "转日期「日期」：前 3 个中 0 个可转换；整列 3 个值将保留原文（整列保留原文）。",
        ),
        (
            {"kind": "date", "column": "日期", "sample_count": 2, "convertible": 1, "failed": 1, "non_empty": 4},
            "转日期「日期」：前 2 个中 1 个可转换；整列 1 个值将保留原文。",
        ),
    ],
)
def test_preview_caption_variants(info, expected):
    assert app_module._preview_caption(info) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("  多行\n文本  ", "多行 文本"),
        ("x" * 40, "x" * app_module._PREVIEW_MAX_CHARS + "…"),
    ],
)
def test_preview_text_truncation(value, expected):
    assert app_module._preview_text(value) == expected
