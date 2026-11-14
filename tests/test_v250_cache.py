"""v2.5.0 W1：解析/检查/清洗缓存与下载失效（对应规划 G-02/G-04/G-05）。

全部用 AppTest 跑真实 ``app.py``；用计数探针证明命中时不再调用
``load_table`` / ``find_missing`` / ``find_suspicious`` / ``clean_table``。
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest

from ui_selectors import _button, _by_label, _checkbox, _multiselect, _selectbox

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

SAMPLE_CSV = (
    "订单号,客户,金额,备注\n"
    "A001,晨光小吃店,128,送货前电话\n"
    "A002,星语文具,0,\n"
    "A002,星语文具,0,\n"
    "A003,山泉花店,56.5,=1+1\n"
).encode("utf-8-sig")

TITLED_CSV = "销售报表\n订单号,金额\nA1,10\nA1,10\nA2,20\n".encode("utf-8-sig")


def _two_sheet_xlsx() -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"编号": ["A1", "A2"], "金额": [1, 2]}).to_excel(writer, index=False, sheet_name="订单")
        pd.DataFrame({"编号": ["B1", "B2", "B3"], "名称": ["甲", "乙", "丙"]}).to_excel(
            writer, index=False, sheet_name="客户"
        )
    return buffer.getvalue()


@pytest.fixture()
def app():
    """每个测试从干净页面开始；conftest 负责在用例前后清 st.cache_data。"""

    def _new():
        at = AppTest.from_file(str(APP_PATH), default_timeout=180).run()
        assert not list(at.exception), list(at.exception)
        return at

    return _new


def _counting(monkeypatch, module, name):
    """把 cleaning 里的函数包一层计数器；app.py 每次 rerun 会重新 import。"""
    calls = {"n": 0}
    real = getattr(module, name)

    def wrapper(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, wrapper)
    return calls


# ---------------------------------------------------------------- 缓存命中


def test_second_run_does_not_parse_again(app, monkeypatch):
    """同一文件第二次 rerun 命中解析缓存，不再调用 load_table。"""
    import cleaning

    calls = _counting(monkeypatch, cleaning, "load_table")
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", SAMPLE_CSV, "text/csv").run()
    assert calls["n"] == 1

    at.run()
    assert calls["n"] == 1


def test_second_run_does_not_recheck_data(app, monkeypatch):
    """空值/可疑内容只依赖数据：rerun 与改规则都不应重算检查。"""
    import cleaning

    missing = _counting(monkeypatch, cleaning, "find_missing")
    suspicious = _counting(monkeypatch, cleaning, "find_suspicious")
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", SAMPLE_CSV, "text/csv").run()
    assert missing["n"] == 1
    assert suspicious["n"] == 1

    at.run()
    _checkbox(at, "删除完全重复的行").check().run()

    assert missing["n"] == 1
    assert suspicious["n"] == 1


def test_second_run_reuses_pipeline_and_skips_clean_table(app, monkeypatch):
    """规则没变时命中流水线缓存：不调用 clean_table，并显示「已复用」。"""
    import cleaning

    calls = _counting(monkeypatch, cleaning, "clean_table")
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", SAMPLE_CSV, "text/csv").run()
    assert calls["n"] == 1
    assert any(caption.value.startswith("计算完成：") for caption in at.caption)

    at.run()
    assert calls["n"] == 1
    assert any("已复用本次会话的计算结果（规则未变）" in caption.value for caption in at.caption)


def test_export_click_does_not_reparse_recheck_or_reclean(app, monkeypatch):
    """点「生成下载文件」只复用缓存：解析/检查/清洗的调用次数都不增加。"""
    import cleaning

    load = _counting(monkeypatch, cleaning, "load_table")
    clean = _counting(monkeypatch, cleaning, "clean_table")
    suspicious = _counting(monkeypatch, cleaning, "find_suspicious")
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", SAMPLE_CSV, "text/csv").run()
    before = (load["n"], clean["n"], suspicious["n"])

    _button(at, "生成下载文件").click().run()

    assert (load["n"], clean["n"], suspicious["n"]) == before
    assert at.get("download_button")


# ---------------------------------------------------------------- 缓存失效


def test_rule_change_invalidates_pipeline_cache(app, monkeypatch):
    """规则变了流水线必须重算，不能拿旧结果。"""
    import cleaning

    calls = _counting(monkeypatch, cleaning, "clean_table")
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", SAMPLE_CSV, "text/csv").run()
    assert calls["n"] == 1

    _checkbox(at, "删除完全重复的行").check().run()

    assert calls["n"] == 2
    assert any(caption.value.startswith("计算完成：") for caption in at.caption)


def test_header_row_change_reparses_and_updates_checks(app, monkeypatch):
    """改表头行必须重新解析，并更新行数/列数与检查结果。"""
    import cleaning

    calls = _counting(monkeypatch, cleaning, "load_table")
    at = app()
    at.get("file_uploader")[0].upload("报表.csv", TITLED_CSV, "text/csv").run()
    assert {m.label: m.value for m in at.metric} == {"行数": "4", "列数": "1"}
    assert calls["n"] == 1

    _by_label(at.number_input, "表头在第几行").set_value(2).run()

    assert calls["n"] == 2
    assert {m.label: m.value for m in at.metric} == {"行数": "3", "列数": "2"}
    assert at.session_state["_检查缓存"]["key"][1] == 2


def test_sheet_change_reparses_and_rechecks_new_sheet(app, monkeypatch):
    """换工作表后解析和检查都必须跟着换，不能沿用上一张表的数字。"""
    import cleaning

    calls = _counting(monkeypatch, cleaning, "load_table")
    at = app()
    at.get("file_uploader")[0].upload("多表.xlsx", _two_sheet_xlsx()).run()
    assert {m.label: m.value for m in at.metric} == {"行数": "2", "列数": "2"}
    assert calls["n"] == 1

    _selectbox(at, "选择要读取的工作表").select("客户").run()

    assert calls["n"] == 2
    assert {m.label: m.value for m in at.metric} == {"行数": "3", "列数": "2"}
    assert at.session_state["_检查缓存"]["key"][2] == "客户"


def test_file_change_clears_session_slots(app):
    """换文件后旧 digest 的检查/流水线槽必须作废。"""
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", SAMPLE_CSV, "text/csv").run()
    first = at.session_state["_当前源"]

    other = "编号,备注\nB001,新文件\n".encode("utf-8-sig")
    at.get("file_uploader")[0].upload("另一个.csv", other, "text/csv").run()

    assert at.session_state["_当前源"] != first
    assert at.session_state["_检查缓存"]["key"] != first


def test_large_file_over_parse_cache_threshold_still_parses(app, monkeypatch):
    """>8 MB（但未超上传上限）的文件跳过解析缓存，仍能正常读取。"""
    import cleaning

    calls = _counting(monkeypatch, cleaning, "load_table")
    big = ("编号,备注\n" + "A001,填充内容填充内容填充内容\n" * 220_000).encode("utf-8-sig")
    assert 8 * 1024 * 1024 < len(big) < 10 * 1024 * 1024

    at = app()
    at.get("file_uploader")[0].upload("大文件.csv", big, "text/csv").run()
    assert not list(at.exception)
    assert calls["n"] == 1


# ---------------------------------------------------------------- 下载失效矩阵（4.4）


def _export_downloads(at) -> list:
    """只数单文件导出区的下载按钮；模板区的「下载」JSON 不算。"""
    return [button for button in at.get("download_button") if button.label in {"下载 CSV", "下载 XLSX"}]


def _generate(at) -> None:
    _button(at, "生成下载文件").click().run()
    assert "exports" in at.session_state
    assert _export_downloads(at)


def test_header_row_change_invalidates_downloads(app):
    at = app()
    at.get("file_uploader")[0].upload("报表.csv", TITLED_CSV, "text/csv").run()
    _generate(at)

    _by_label(at.number_input, "表头在第几行").set_value(2).run()

    assert "exports" not in at.session_state
    assert not _export_downloads(at)


def test_sheet_change_invalidates_downloads(app):
    at = app()
    at.get("file_uploader")[0].upload("多表.xlsx", _two_sheet_xlsx()).run()
    _generate(at)

    _selectbox(at, "选择要读取的工作表").select("客户").run()

    assert "exports" not in at.session_state
    assert not _export_downloads(at)


def test_file_change_invalidates_downloads(app):
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", SAMPLE_CSV, "text/csv").run()
    _generate(at)

    at.get("file_uploader")[0].upload("另一个.csv", "编号\nB001\n".encode("utf-8-sig"), "text/csv").run()

    assert "exports" not in at.session_state
    assert not _export_downloads(at)


def test_number_mode_change_invalidates_downloads(app):
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", SAMPLE_CSV, "text/csv").run()
    _generate(at)

    _by_label(at.radio, "导出 XLSX 时编号类字段怎么处理").set_value("number").run()

    assert "exports" not in at.session_state
    assert not _export_downloads(at)


def test_template_apply_invalidates_downloads(app):
    """套用模板导致规则变化时，旧下载必须失效。"""
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", SAMPLE_CSV, "text/csv").run()
    _checkbox(at, "删除完全重复的行").check().run()
    _by_label(at.button, "保存当前规则为模板").click().run()

    _checkbox(at, "删除完全重复的行").uncheck().run()
    _generate(at)

    _by_label(at.button, "套用").click().run()

    assert _checkbox(at, "删除完全重复的行").value is True
    assert "exports" not in at.session_state
    assert not _export_downloads(at)


def test_multiselect_target_change_invalidates_downloads(app):
    """改目标列（空值列）同样要让旧导出失效。"""
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", SAMPLE_CSV, "text/csv").run()
    _checkbox(at, "删除指定列中有空值的行").check().run()
    _multiselect(at, "选择要检查空值的列").select("备注").run()
    _generate(at)

    _multiselect(at, "选择要检查空值的列").unselect("备注").select("金额").run()

    assert "exports" not in at.session_state
    assert not _export_downloads(at)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rename", "金额=总金额"),
        ("drop", "备注"),
        ("order", "备注"),
        ("to_number", "金额"),
        ("to_date", "客户"),
        ("computed", "总额=金额*2"),
    ],
)
def test_column_op_change_invalidates_downloads(app, field, value):
    """下载失效矩阵：改名/删列/顺序/转数字/转日期/计算列任一变化都要作废旧导出。"""
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", SAMPLE_CSV, "text/csv").run()
    _generate(at)

    widgets = {
        "rename": (at.text_area, "列重命名"),
        "drop": (at.multiselect, "删除这些列"),
        "order": (at.text_area, "把哪些列排到最前面"),
        "to_number": (at.multiselect, "转成数字的列"),
        "to_date": (at.multiselect, "转成日期的列"),
        "computed": (at.text_area, "计算列"),
    }
    elements, label = widgets[field]
    control = _by_label(elements, label)
    if field in {"drop", "to_number", "to_date"}:
        control.select(value).run()
    else:
        control.input(value).run()

    assert "exports" not in at.session_state
    assert not _export_downloads(at)


def test_same_content_different_filename_invalidates_downloads(app):
    """同内容不同文件名也要换下载名：签名包含源文件名，不能拿旧名字下载。"""
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", SAMPLE_CSV, "text/csv").run()
    _generate(at)
    assert "订单" in at.session_state["exports"]["csv_name"]

    at.get("file_uploader")[0].upload("订单副本.csv", SAMPLE_CSV, "text/csv").run()
    assert "exports" not in at.session_state
    assert not _export_downloads(at)

    _generate(at)
    assert "订单副本" in at.session_state["exports"]["csv_name"]
