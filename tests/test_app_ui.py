"""端到端界面测试：真实跑 app.py，覆盖上传→预览→清洗→下载→重新打开。

单独一个文件，因为它比逻辑测试慢（每次启动约 4 秒）。
运行：python -m pytest tests/test_app_ui.py -q
"""

from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest
from cleaning import to_xlsx_bytes

from ui_selectors import _button, _checkbox, _dataframe, _multiselect, _selectbox

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"

# 界面测试要两样东西，缺了哪个都要在输出里说清楚，别让「1 skipped」看不出原因：
# 1) 装了 streamlit 且带 AppTest（requirements.txt 里已固定 >=1.50,<2）；
# 2) AppTest 支持 file_uploader（实测 1.50/1.52/1.55 没有，1.66 有）。
try:
    import streamlit  # noqa: F401
    from streamlit.testing import v1 as streamlit_testing
except ImportError as exc:  # 没装、或装了但依赖坏了都会走到这
    missing = getattr(exc, "name", None) or str(exc)
    pytest.skip(
        f"界面测试需要 streamlit，但导入失败（{missing}）；先 pip install -r requirements.txt 再跑。"
        "逻辑测试（tests/test_app_logic.py）不受影响。",
        allow_module_level=True,
    )

AppTest = streamlit_testing.AppTest

if not hasattr(streamlit_testing.element_tree, "FileUploader"):
    pytest.skip(
        f"当前 Streamlit {streamlit.__version__} 的 AppTest 还不支持 file_uploader，"
        "整个界面测试文件跳过（应用本身仍可运行；逻辑测试不受影响；升到 1.66+ 即可跑）",
        allow_module_level=True,
    )


@pytest.fixture(scope="module")
def app():
    """每个测试都从干净页面开始，避免 session_state 串味。"""

    def _new():
        at = AppTest.from_file(str(APP_PATH), default_timeout=180).run()
        assert not list(at.exception), list(at.exception)
        return at

    return _new


SAMPLE_CSV = (
    "订单号,客户,金额,备注\n"
    "A001,晨光小吃店,128,送货前电话\n"
    "A002,星语文具,0,\n"
    "A002,星语文具,0,\n"
    "A003,山泉花店,56.5,=1+1\n"
).encode("utf-8-sig")


def test_upload_shows_stats_and_preview(app):
    """上传后行数、列数、预览必须和文件一致。"""
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", SAMPLE_CSV, "text/csv").run()
    assert not list(at.exception)

    assert {m.label: m.value for m in at.metric} == {"行数": "4", "列数": "4"}
    preview = _dataframe(at, "订单号", "客户", "金额", "备注").value
    assert len(preview) == 4
    assert preview["订单号"].tolist() == ["A001", "A002", "A002", "A003"]


def test_semicolon_csv_is_split_and_explained(app):
    """分号分隔的 CSV 在页面上要真的分成 4 列，并说明用了哪种分隔符。"""
    csv = SAMPLE_CSV.decode("utf-8-sig").replace(",", ";").encode("utf-8-sig")
    at = app()
    at.get("file_uploader")[0].upload("分号.csv", csv, "text/csv").run()
    assert not list(at.exception)
    assert {m.label: m.value for m in at.metric} == {"行数": "4", "列数": "4"}
    assert any("分号" in info.value for info in at.info)


def test_phone_flow_semicolon_csv_end_to_end(app):
    """等价于手机上的完整操作：分号 CSV → 勾两项清洗 → 选列 → 生成 → 下载 → 重开核对。"""
    csv = (
        "订单号;客户;金额;备注\n"
        "A001;晨光小吃店;128;送货前电话\n"
        "A002;星语文具;0;\n"
        "A002;星语文具;0;\n"
        "A003;山泉花店;56.5;=1+1\n"
    ).encode("utf-8-sig")
    at = app()
    at.get("file_uploader")[0].upload("分号订单.csv", csv, "text/csv").run()
    assert not list(at.exception)
    assert {m.label: m.value for m in at.metric} == {"行数": "4", "列数": "4"}

    _checkbox(at, "删除完全重复的行").check().run()
    _checkbox(at, "删除指定列中有空值的行").check().run()
    _multiselect(at, "选择要检查空值的列").select("备注").run()
    assert not list(at.exception)

    _button(at, "生成下载文件").click().run()
    exports = at.session_state["exports"]
    back = pd.read_csv(BytesIO(exports["csv"]), dtype=str, keep_default_na=False)

    assert back.shape == (2, 4)  # A002 先被当重复行删掉、备注为空也被删
    assert back["订单号"].tolist() == ["A001", "A003"]
    assert back["备注"].tolist() == ["送货前电话", "'=1+1"]  # CSV 防公式前缀确实写进了文件


def test_changing_options_invalidates_downloads(app):
    """改了清洗选项，之前生成的下载必须失效，不能让人拿到旧文件。"""
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", SAMPLE_CSV, "text/csv").run()
    _button(at, "生成下载文件").click().run()
    assert "exports" in at.session_state

    at.run()
    _checkbox(at, "删除完全重复的行").check().run()
    assert "exports" not in at.session_state
    assert not at.get("download_button")


def test_downloaded_csv_and_xlsx_agree(app):
    """下载的 CSV 与 XLSX 行数、编号、金额必须一致，且能被重新打开。"""
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", SAMPLE_CSV, "text/csv").run()
    _button(at, "生成下载文件").click().run()

    exports = at.session_state["exports"]
    csv_back = pd.read_csv(BytesIO(exports["csv"]), dtype=str, keep_default_na=False)
    xlsx_back = pd.read_excel(BytesIO(exports["xlsx"]), dtype=str, keep_default_na=False)

    assert csv_back.shape == xlsx_back.shape == (4, 4)
    assert csv_back["订单号"].tolist() == xlsx_back["订单号"].tolist()
    assert csv_back.loc[0, "金额"] == xlsx_back.loc[0, "金额"] == "128"


def test_all_empty_column_does_not_crash_page(app):
    """回归：整列空值的 CSV 不能把页面搞崩（find_suspicious 曾在空列上抛 ValueError）。"""
    csv = "编号,备注\nA001,\nA002,\n".encode("utf-8-sig")
    at = app()
    at.get("file_uploader")[0].upload("空列.csv", csv, "text/csv").run()
    assert not list(at.exception)
    assert {m.label: m.value for m in at.metric} == {"行数": "2", "列数": "2"}


def test_xlsx_upload_roundtrip_in_ui(app):
    """上传 XLSX 也能正常预览和导出。"""
    original = pd.DataFrame({"编号": ["00123", "A002"], "金额": [0, 128.5]})
    at = app()
    at.get("file_uploader")[0].upload("数据.xlsx", to_xlsx_bytes(original)).run()
    assert not list(at.exception)
    assert {m.label: m.value for m in at.metric} == {"行数": "2", "列数": "2"}


@pytest.mark.parametrize(
    ("filename", "data", "keyword"),
    [
        ("空.csv", b"", "空的"),
        ("坏.xlsx", b"not an excel file", "无法打开"),
        ("老表格.xls", b"\xd0\xcf\x11\xe0", "只支持"),
    ],
)
def test_bad_files_show_chinese_error(app, filename, data, keyword):
    """异常文件要在页面上给出中文提示，而不是抛异常或卡住。"""
    at = app()
    at.get("file_uploader")[0].upload(filename, data).run()
    assert not list(at.exception)
    errors = [e.value for e in at.error]
    assert errors and keyword in errors[0]


def _big_then_small_xlsx() -> bytes:
    """一张 10 行的「大表」+ 一张 2 行的「小表」，配合 MAX_ROWS=5 用。"""
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame({"值": range(10)}).to_excel(writer, index=False, sheet_name="大表")
        pd.DataFrame({"值": range(2)}).to_excel(writer, index=False, sheet_name="小表")
    return buf.getvalue()


def test_over_limit_first_sheet_falls_back_in_ui(app, monkeypatch):
    """回归：第一张表超限不应把整份文件打死——自动读下一张可用表并说明跳过了谁。"""
    import cleaning

    monkeypatch.setattr(cleaning, "MAX_ROWS", 5)
    at = app()
    at.get("file_uploader")[0].upload("两表.xlsx", _big_then_small_xlsx()).run()
    assert not list(at.exception)
    assert {m.label: m.value for m in at.metric} == {"行数": "2", "列数": "1"}
    assert any("「大表」" in info.value and "跳过" in info.value for info in at.info)
    assert [s.label for s in at.selectbox] == ["选择要读取的工作表"]


def test_switch_to_over_limit_sheet_keeps_page_usable(app, monkeypatch):
    """换到超限的表时给中文错误，但页面不能只剩报错：默认表照常显示、选表框还在。"""
    import cleaning

    monkeypatch.setattr(cleaning, "MAX_ROWS", 5)
    at = app()
    at.get("file_uploader")[0].upload("两表.xlsx", _big_then_small_xlsx()).run()
    _selectbox(at, "选择要读取的工作表").select("大表").run()

    assert not list(at.exception)
    errors = [e.value for e in at.error]
    assert errors and "大表" in errors[0]
    assert {m.label: m.value for m in at.metric} == {"行数": "2", "列数": "1"}  # 仍是默认的小表
    assert any("当前显示的是" in info.value for info in at.info)
    assert [s.label for s in at.selectbox] == ["选择要读取的工作表"]


def test_removed_rows_sample_toggle_shows_reason(app):
    """勾「查看被删掉的行样例」后，页面列出被删的行和删除原因。"""
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", SAMPLE_CSV, "text/csv").run()
    _checkbox(at, "删除完全重复的行").check().run()

    toggle = _checkbox(at, "查看被删掉的行样例")
    toggle.check().run()
    assert not list(at.exception)

    samples = [f.value for f in at.dataframe if "删除原因" in getattr(f.value, "columns", [])]
    assert samples, "没有渲染出被删行样例表"
    assert samples[0]["删除原因"].tolist() == ["删除完全重复的行"]


def test_row_limit_shows_error_not_crash(app, monkeypatch):
    """超限文件被拒绝时，页面显示中文错误且不崩溃。"""
    import cleaning

    monkeypatch.setattr(cleaning, "MAX_ROWS", 2)
    at = app()
    at.get("file_uploader")[0].upload("大.csv", ("值\n" + "1\n" * 10).encode("utf-8-sig")).run()
    assert not list(at.exception)
    errors = [e.value for e in at.error]
    assert errors and "行数" in errors[0]


# ---------------------------------------------------------------- 1.7.0 新功能

DEDUPE_CSV = (
    "订单号,客户,金额,备注\nA001,晨光小吃店,128,送货前电话\nA001,晨光小吃店,200,改单后重下一单\nA002,星语文具,0,\n"
).encode("utf-8-sig")


def _step_counts(at) -> dict[str, int]:
    """把页面上的「每一步删掉多少行」表格读成 {步骤: 行数}；没有就返回空字典。"""
    for frame in at.dataframe:
        value = frame.value
        if "步骤" in getattr(value, "columns", []):
            return dict(zip(value["步骤"], value["删掉行数"]))
    return {}


def _multi_sheet_xlsx() -> bytes:
    """造一个双工作表 XLSX：两张表行列数不同，才能验证真的换了表。"""
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame({"编号": ["001", "002"], "金额": [1.5, 2.5]}).to_excel(writer, sheet_name="第一张", index=False)
        pd.DataFrame({"编号": ["A1", "A2", "A3"], "名称": ["甲", "乙", "丙"], "金额": [1, 2, 3]}).to_excel(
            writer, sheet_name="第二张", index=False
        )
    return buf.getvalue()


def test_multi_sheet_selectbox_reads_chosen_sheet(app):
    """双表 XLSX：出现工作表选择器，换表后行列数和预览都变成第二张的。"""
    at = app()
    at.get("file_uploader")[0].upload("多表.xlsx", _multi_sheet_xlsx()).run()
    assert not list(at.exception)
    assert [s.label for s in at.selectbox] == ["选择要读取的工作表"]
    assert {m.label: m.value for m in at.metric} == {"行数": "2", "列数": "2"}

    _selectbox(at, "选择要读取的工作表").select("第二张").run()
    assert not list(at.exception)
    assert {m.label: m.value for m in at.metric} == {"行数": "3", "列数": "3"}
    assert _dataframe(at, "名称").value["名称"].tolist() == ["甲", "乙", "丙"]


def _special_name_xlsx() -> bytes:
    """三张表，名字里带 & < > " —— Excel 会把它们存成 XML 实体。"""
    from openpyxl import Workbook

    buffer = BytesIO()
    workbook = Workbook()
    first = workbook.active
    first.title = "Sales & Marketing"
    first.append(["订单号", "金额"])
    first.append(["A1", 10])
    second = workbook.create_sheet("A<B>C")
    second.append(["编号"])
    second.append(["S1"])
    third = workbook.create_sheet('他说"好" & 报价')
    third.append(["号"])
    third.append(["Q1"])
    workbook.save(buffer)
    return buffer.getvalue()


def test_special_char_sheet_names_in_ui(app):
    """回归：表名含 & < > " 时，下拉框显示解码后的真名，换表能读到对应数据。"""
    at = app()
    at.get("file_uploader")[0].upload("特殊表名.xlsx", _special_name_xlsx()).run()
    assert not list(at.exception)
    assert _selectbox(at, "选择要读取的工作表").options == ["Sales & Marketing", "A<B>C", '他说"好" & 报价']
    assert {m.label: m.value for m in at.metric} == {"行数": "1", "列数": "2"}

    _selectbox(at, "选择要读取的工作表").select('他说"好" & 报价').run()
    assert not list(at.exception)
    assert {m.label: m.value for m in at.metric} == {"行数": "1", "列数": "1"}

    _selectbox(at, "选择要读取的工作表").select("A<B>C").run()
    assert {m.label: m.value for m in at.metric} == {"行数": "1", "列数": "1"}
    assert _dataframe(at, "编号").value["编号"].tolist() == ["S1"]


def test_dedupe_by_column_ui_end_to_end(app):
    """勾选按列去重并选订单号后，同一订单号只留第一条；导出的 CSV 也要对。"""
    at = app()
    at.get("file_uploader")[0].upload("重复订单.csv", DEDUPE_CSV, "text/csv").run()
    assert {m.label: m.value for m in at.metric} == {"行数": "3", "列数": "4"}

    _checkbox(at, "按指定列去重").check().run()
    _multiselect(at, "选择去重用的列").select("订单号").run()
    assert not list(at.exception)
    # 别让用户盲选：第 4 步的「每一步删掉多少行」要写明按列去重删了 1 行
    assert _step_counts(at).get("按指定列去重") == 1

    _button(at, "生成下载文件").click().run()
    back = pd.read_csv(BytesIO(at.session_state["exports"]["csv"]), dtype=str, keep_default_na=False)
    assert back.shape == (2, 4)
    assert back["订单号"].tolist() == ["A001", "A002"]
    assert back["金额"].tolist() == ["128", "0"]  # 保留第一次出现的 A001

    # 两个去重同时开：A002 的完全重复行先被删掉，再按订单号看就没有重复了。
    # 步骤计数必须写出「删完全重复 1 行、按列去重 0 行」，不能把两步混成一个数。
    at2 = app()
    at2.get("file_uploader")[0].upload("订单.csv", SAMPLE_CSV, "text/csv").run()
    at2.checkbox[0].check().run()  # 删除完全重复的行
    at2.checkbox[2].check().run()  # 按指定列去重
    at2.multiselect[1].select("订单号").run()
    assert not list(at2.exception)
    counts = _step_counts(at2)
    assert counts["删除完全重复的行"] == 1
    assert counts["按指定列去重"] == 0


def test_new_dedupe_option_invalidates_downloads(app):
    """改了去重列（新选项）后，之前生成的旧导出必须失效。"""
    at = app()
    at.get("file_uploader")[0].upload("重复订单.csv", DEDUPE_CSV, "text/csv").run()
    _button(at, "生成下载文件").click().run()
    assert "exports" in at.session_state

    _checkbox(at, "按指定列去重").check().run()
    _multiselect(at, "选择去重用的列").select("订单号").run()
    assert "exports" not in at.session_state
    assert not at.get("download_button")
