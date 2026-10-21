"""v2.0.0 新功能的端到端界面测试：表头行、进阶列操作、批量 ZIP、模板、导出模式。

真实跑 app.py（不 mock 界面），和 tests/test_app_ui.py 一样用 Streamlit 的 AppTest。
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

import cleaning

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"

try:
    import streamlit  # noqa: F401
    from streamlit.testing import v1 as streamlit_testing
except ImportError as exc:  # 没装、或装了但依赖坏了都会走到这
    missing = getattr(exc, "name", None) or str(exc)
    pytest.skip(
        f"界面测试需要 streamlit，但导入失败（{missing}）；先 pip install -r requirements.txt 再跑。",
        allow_module_level=True,
    )

AppTest = streamlit_testing.AppTest

if not hasattr(streamlit_testing.element_tree, "FileUploader"):
    pytest.skip(
        "当前 Streamlit 的 AppTest 不支持 file_uploader，界面测试整体跳过（应用本身仍可运行）",
        allow_module_level=True,
    )


@pytest.fixture()
def app():
    """每个测试都从干净页面开始，避免 session_state 串味。"""

    def _new():
        at = AppTest.from_file(str(APP_PATH), default_timeout=180).run()
        assert not list(at.exception), list(at.exception)
        return at

    return _new


TITLED_CSV = "销售报表\n订单号,金额\nA1,10\nA1,10\nA2,20\n".encode("utf-8-sig")

OPS_CSV = "商品,单价,数量\n苹果,10,2\n梨,20,3\n".encode("utf-8-sig")


def two_sheet_xlsx() -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"编号": ["A1", "A1", "A2"], "金额": [1, 1, 2]}).to_excel(
            writer, index=False, sheet_name="订单"
        )
        pd.DataFrame({"编号": ["B1", "B2"], "金额": [3, 4]}).to_excel(
            writer, index=False, sheet_name="客户"
        )
    return buffer.getvalue()


def _by_label(elements, prefix: str):
    return next(element for element in elements if element.label.startswith(prefix))


# ---------------------------------------------------------------- 表头行


def test_header_row_can_be_changed_in_ui(app):
    """上传带标题行的报表：把「表头在第几行」改成 2，行数列数跟着变。"""
    at = app()
    at.get("file_uploader")[0].upload("报表.csv", TITLED_CSV, "text/csv").run()
    assert not list(at.exception)
    assert {m.label: m.value for m in at.metric} == {"行数": "4", "列数": "1"}

    _by_label(at.number_input, "表头在第几行").set_value(2).run()
    assert not list(at.exception)
    assert {m.label: m.value for m in at.metric} == {"行数": "3", "列数": "2"}
    assert at.dataframe[0].value["订单号"].tolist() == ["A1", "A1", "A2"]


# ---------------------------------------------------------------- 进阶列操作


def test_advanced_column_ops_reach_the_download(app):
    """改名 + 转数字 + 计算列：下载的 CSV 里列名和算出来的值都要对。"""
    at = app()
    at.get("file_uploader")[0].upload("商品.csv", OPS_CSV, "text/csv").run()
    _by_label(at.text_area, "列重命名").input("数量=件数").run()
    _by_label(at.multiselect, "转成数字的列").select("单价").select("件数").run()
    _by_label(at.text_area, "计算列").input("总额=单价*件数").run()
    assert not list(at.exception)

    at.button[0].click().run()
    assert not list(at.exception)
    exports = at.session_state["exports"]
    back = pd.read_csv(BytesIO(exports["csv"]), dtype=str, keep_default_na=False)

    assert list(back.columns) == ["商品", "单价", "件数", "总额"]
    assert back["总额"].tolist() == ["20", "60"]
    assert exports["csv_name"].startswith("清洗后_商品_")


def test_advanced_bad_expression_shows_chinese_error_but_page_works(app):
    """表达式写错只提示、不崩：页面照常可导出，提示里说清哪一行错。"""
    at = app()
    at.get("file_uploader")[0].upload("商品.csv", OPS_CSV, "text/csv").run()
    _by_label(at.text_area, "列重命名").input("数量 件数").run()

    assert not list(at.exception)
    errors = [error.value for error in at.error]
    assert errors and "第 1 行" in errors[0]
    at.button[0].click().run()
    assert not list(at.exception)
    assert "csv" in at.session_state["exports"]


def test_computed_column_preview_visible_in_page(app):
    """计算列真的进了页面预览（不只是下载文件里）。"""
    at = app()
    at.get("file_uploader")[0].upload("商品.csv", OPS_CSV, "text/csv").run()
    _by_label(at.multiselect, "转成数字的列").select("单价").select("数量").run()
    _by_label(at.text_area, "计算列").input("总额=单价*数量").run()
    assert not list(at.exception)

    frames = [
        frame.value
        for frame in at.dataframe
        if "总额" in getattr(frame.value, "columns", [])
    ]
    assert frames, "页面上没有出现带计算列的预览"
    assert frames[0]["总额"].tolist() == [20, 60]


# ---------------------------------------------------------------- 批量 ZIP


def test_batch_multiple_sheets_in_one_zip(app):
    """双表 XLSX：打开批量、勾两张表、生成一个 ZIP，里面每张表一个文件。"""
    at = app()
    at.get("file_uploader")[0].upload("多表.xlsx", two_sheet_xlsx()).run()
    _by_label(at.toggle, "开启批量").set_value(True).run()
    assert not list(at.exception)

    sheets = _by_label(at.multiselect, "一起处理当前文件的这些工作表")
    assert sheets.value == ["订单", "客户"]

    _by_label(at.button, "生成批量 ZIP").click().run()
    assert not list(at.exception)
    report = at.session_state["批量_结果"]["report"]
    assert report.exported == 2
    assert [item.entry for item in report.items] == ["多表_订单.csv", "多表_客户.csv"]
    assert [item.rows_after for item in report.items] == [3, 2]
    assert any(button.label == "下载批量 ZIP" for button in at.get("download_button"))


def test_batch_multiple_uploaded_files_in_one_zip(app):
    """一次上传多个文件（含当前主文件）：每个文件一个结果，打成一个 ZIP。"""
    at = app()
    at.get("file_uploader")[0].upload("主.csv", "a\n1\n".encode("utf-8-sig"), "text/csv").run()
    _by_label(at.toggle, "开启批量").set_value(True).run()

    batch_uploader = _by_label(at.get("file_uploader"), "一次上传多个")
    batch_uploader.set_value(
        [
            ("甲.csv", "x,y\n1,2\n".encode("utf-8-sig"), "text/csv"),
            ("乙.csv", "x,y\n3,4\n".encode("utf-8-sig"), "text/csv"),
        ]
    ).run()
    assert not list(at.exception)

    _by_label(at.button, "生成批量 ZIP").click().run()
    assert not list(at.exception)
    report = at.session_state["批量_结果"]["report"]
    entries = [item.entry for item in report.items]
    assert entries == ["主.csv", "甲.csv", "乙.csv"]


def test_batch_bad_file_is_reported_per_item(app):
    """批量里有一个坏文件：其他项照常打包，页面表格写明哪一项失败和原因。"""
    at = app()
    at.get("file_uploader")[0].upload("主.csv", "a\n1\n".encode("utf-8-sig"), "text/csv").run()
    _by_label(at.toggle, "开启批量").set_value(True).run()
    _by_label(at.get("file_uploader"), "一次上传多个").set_value(
        [("坏.xlsx", b"not excel", "application/octet-stream")]
    ).run()

    _by_label(at.button, "生成批量 ZIP").click().run()
    assert not list(at.exception)
    report = at.session_state["批量_结果"]["report"]
    assert report.exported == 1
    failed = [item for item in report.items if item.error]
    assert failed and "无法打开" in failed[0].error
    table = next(frame.value for frame in at.dataframe if "来源" in frame.value.columns)
    assert "已打包" in table["结果"].tolist()
    assert any("无法打开" in text for text in table["结果"].tolist())


# ---------------------------------------------------------------- 规则模板


def test_template_save_and_apply_restores_widgets(app):
    """保存当前规则为模板 → 改乱控件 → 点「套用」后全部恢复。"""
    at = app()
    at.get("file_uploader")[0].upload("商品.csv", OPS_CSV, "text/csv").run()
    at.checkbox[0].check().run()  # 删除完全重复的行
    _by_label(at.text_area, "列重命名").input("数量=件数").run()
    _by_label(at.text_area, "计算列").input("总额=单价*数量").run()
    _by_label(at.text_input, "模板名称").input("商品模板").run()
    _by_label(at.button, "保存当前规则为模板").click().run()
    assert not list(at.exception)

    saved = at.session_state["规则模板"]["商品模板"]["规则"]
    assert saved["删除完全重复的行"] is True
    assert saved["列重命名"] == [["数量", "件数"]]
    assert saved["计算列"] == [["总额", "单价*数量"]]

    # 改乱
    at.checkbox[0].uncheck().run()
    _by_label(at.text_area, "列重命名").input("").run()
    _by_label(at.text_area, "计算列").input("").run()
    assert at.checkbox[0].value is False

    _by_label(at.button, "套用").click().run()
    assert not list(at.exception)
    assert at.checkbox[0].value is True
    assert _by_label(at.text_area, "列重命名").value == "数量=件数"
    assert _by_label(at.text_area, "计算列").value == "总额=单价*数量"


def test_template_file_upload_applies_rules(app):
    """上传之前下载的模板 JSON：自动套用（表头行、去重勾选都恢复）。"""
    template = cleaning.build_template(
        {"表头行": 2, "删除完全重复的行": True}, name="报表模板"
    )
    at = app()
    at.get("file_uploader")[0].upload("报表.csv", TITLED_CSV, "text/csv").run()
    assert {m.label: m.value for m in at.metric} == {"行数": "4", "列数": "1"}

    template_uploader = _by_label(at.get("file_uploader"), "上传之前保存的模板")
    template_uploader.set_value(
        ("模板.json", cleaning.dumps(template), "application/json")
    ).run()
    assert not list(at.exception)
    assert _by_label(at.number_input, "表头在第几行").value == 2
    assert at.checkbox[0].value is True
    assert {m.label: m.value for m in at.metric} == {"行数": "3", "列数": "2"}


# ---------------------------------------------------------------- 导出模式


def test_formula_mode_radio_controls_csv_export(app):
    """三种公式防护模式在真实页面上切换后，导出的 CSV 内容跟着变，旧文件自动失效。"""
    at = app()
    at.get("file_uploader")[0].upload(
        "订单.csv", "备注\n=1+1\n正常\n".encode("utf-8-sig"), "text/csv"
    ).run()
    at.button[0].click().run()
    assert "'=1+1" in at.session_state["exports"]["csv"].decode("utf-8-sig")

    _by_label(at.radio, "导出 CSV 时的公式防护").set_value("off").run()
    assert "exports" not in at.session_state  # 换模式后旧导出必须失效
    at.button[0].click().run()
    assert "'=1+1" not in at.session_state["exports"]["csv"].decode("utf-8-sig")

    _by_label(at.radio, "导出 CSV 时的公式防护").set_value("zwsp").run()
    at.button[0].click().run()
    assert "\u200b=1+1" in at.session_state["exports"]["csv"].decode("utf-8-sig")


def test_xlsx_number_mode_radio_changes_cell_types(app):
    """编号 00123：默认强制文本；切到「尝试转数字」后 XLSX 里是真数字。"""
    at = app()
    at.get("file_uploader")[0].upload(
        "编号.csv", "编号\n00123\nA002\n".encode("utf-8-sig"), "text/csv"
    ).run()
    at.button[0].click().run()
    sheet = openpyxl.load_workbook(BytesIO(at.session_state["exports"]["xlsx"])).active
    assert sheet["A2"].value == "00123"

    _by_label(at.radio, "导出 XLSX 时编号类字段怎么处理").set_value("number").run()
    at.button[0].click().run()
    sheet = openpyxl.load_workbook(BytesIO(at.session_state["exports"]["xlsx"])).active
    assert sheet["A2"].value == 123
    assert sheet["A3"].value == "A002"


def test_advanced_ops_change_invalidates_downloads(app):
    """列变换改了以后，之前生成的下载必须失效（和清洗选项同一套机制）。"""
    at = app()
    at.get("file_uploader")[0].upload("商品.csv", OPS_CSV, "text/csv").run()
    at.button[0].click().run()
    assert "exports" in at.session_state

    _by_label(at.text_area, "列重命名").input("数量=件数").run()
    assert "exports" not in at.session_state
    assert not [button for button in at.get("download_button") if button.label == "下载 CSV"]


# ---------------------------------------------------------------- v2.0.1 回归（界面层）


def test_mixed_timezone_dates_show_warning_instead_of_traceback(app):
    """同一列混着带/不带时区的日期：旧版整页变成英文 ValueError 堆栈，导出按钮全消失。"""
    at = app()
    at.get("file_uploader")[0].upload(
        "日期.csv",
        "日期,金额\n2024-05-01T10:00:00+08:00,1\n2024-05-02T10:00:00+09:00,2\n".encode("utf-8-sig"),
        "text/csv",
    ).run()
    _by_label(at.multiselect, "转成日期的列").select("日期").run()

    assert not list(at.exception)
    assert any("没转成功" in str(warning.value) for warning in at.warning)
    at.button[0].click().run()  # 「生成下载文件」照常可用
    assert "csv" in at.session_state["exports"]


def test_exploding_expression_does_not_freeze_the_page(app):
    """9**9**9：旧版会真去算（3.8 亿次方）把页面卡死；现在立刻给出中文提示。"""
    at = app()
    at.get("file_uploader")[0].upload("商品.csv", OPS_CSV, "text/csv").run()
    _by_label(at.multiselect, "转成数字的列").select("单价").run()
    _by_label(at.text_area, "计算列").input("爆炸=9**9**9").run()

    assert not list(at.exception)
    assert any("结果太大" in str(warning.value) for warning in at.warning)
    assert "爆炸" not in at.dataframe[0].value.columns
