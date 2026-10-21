"""cleaning.py 的测试：正常/空/损坏/超大文件、重复行、缺失值、导出。

运行：python -m pytest -q
"""

import zipfile
from io import BytesIO

import openpyxl
import pandas as pd
import pytest

import cleaning
from cleaning import (
    EXCEL_MAX_ROWS,
    FORMULA_ISSUE,
    SPACE_ISSUE,
    MAX_UPLOAD_BYTES,
    TableError,
    clean_data,
    clean_report,
    find_missing,
    find_suspicious,
    _is_text_column,
    _looks_like_formula,
    load_table,
    make_sample_df,
    shield_csv_formulas,
    summarize,
    to_csv_bytes,
    to_xlsx_bytes,
)


def csv_bytes(text: str) -> bytes:
    """测试里统一用带 BOM 的 UTF-8，和普通用户从 Excel 导出的文件一致。"""
    return text.encode("utf-8-sig")


# ---------------------------------------------------------------- 读取：正常文件


def test_load_csv_normal_file():
    """正常 CSV：行列、列名、空值、数字 0 都按预期。"""
    raw = csv_bytes("订单号,金额,备注\nA001,0,有货\nA002,128.5,\n")
    loaded = load_table("订单.csv", raw)
    df = loaded.df

    assert df.shape == (2, 3)
    assert list(df.columns) == ["订单号", "金额", "备注"]
    assert df.loc[0, "金额"] == "0"  # CSV 按原文读取，0 不会被当成空
    assert pd.isna(df.loc[1, "备注"])  # 读取后空值统一是 NA
    assert int(find_missing(df)["备注"]) == 1
    assert int(find_missing(df)["金额"]) == 0
    assert loaded.notes == []


def test_load_csv_keeps_leading_zero_code():
    """CSV 按文本读取，00123 这类编号不会被改成 123。"""
    df = load_table("编号.csv", csv_bytes("编号\n00123\n")).df
    assert df.loc[0, "编号"] == "00123"


def test_load_csv_literal_na_is_data_not_missing():
    """文字 NA / null 是有效内容，不能当成空值。"""
    df = load_table("备注.csv", csv_bytes("备注\nNA\nnull\n")).df
    assert df["备注"].tolist() == ["NA", "null"]
    assert int(summarize(df)["空值单元格"]) == 0


def test_load_csv_gb18030_fallback_with_note():
    """不是 UTF-8 的中文 CSV 会按 GB18030 读取，并在页面提示。"""
    raw = "客户名称\n张三小吃店\n".encode("gb18030")
    loaded = load_table("客户.csv", raw)
    assert loaded.df.loc[0, "客户名称"] == "张三小吃店"
    assert loaded.notes and "GB18030" in loaded.notes[0]


@pytest.mark.parametrize("separator,name", [(";", "分号"), ("\t", "制表符"), ("|", "竖线")])
def test_load_csv_other_delimiter_is_detected(separator, name):
    """分号/制表符/竖线分隔的 CSV 能正确分列，并提示用了哪个分隔符。"""
    raw = csv_bytes(f"姓名{separator}金额{separator}备注\n张三{separator}10{separator}首行\n")
    loaded = load_table("其他.csv", raw)
    assert loaded.df.shape == (1, 3)
    assert loaded.df.loc[0, "金额"] == "10"
    assert any(name in note for note in loaded.notes)


def test_load_csv_comma_file_has_no_delimiter_note():
    """逗号分隔是默认情况，不该多嘴提示分隔符。"""
    loaded = load_table("逗号.csv", csv_bytes("姓名,金额\n张三,10\n"))
    assert loaded.df.shape == (1, 2)
    assert loaded.notes == []


def test_load_csv_single_column_with_semicolons_is_not_split():
    """单列文件的值里带分号时不能被硬拆成多列（分隔符得在表头出现过才算数）。"""
    loaded = load_table("单列.csv", csv_bytes("备注\na;b\nc;d\n"))
    assert loaded.df.shape == (2, 1)
    assert loaded.df["备注"].tolist() == ["a;b", "c;d"]
    assert any("只解析出一列" in note for note in loaded.notes)


def test_estimate_csv_shape_uses_detected_delimiter():
    """解析前预检也要按识别到的分隔符算列数，否则列数上限会漏判。"""
    raw = csv_bytes("a;b;c\n1;2;3\n")
    assert cleaning._estimate_csv_shape(raw, "utf-8-sig") == (1, 1)  # 默认逗号
    assert cleaning._estimate_csv_shape(raw, "utf-8-sig", ";") == (1, 3)


def test_load_xlsx_keeps_literal_na_text():
    """XLSX 里的文字 NA / null 也是有效数据，不能按缺失处理。"""
    df = pd.DataFrame({"code": ["NA", "null"]})
    loaded = load_table("x.xlsx", to_xlsx_bytes(df))
    assert loaded.df["code"].tolist() == ["NA", "null"]
    assert int(find_missing(loaded.df)["code"]) == 0


def test_load_xlsx_roundtrip():
    """正常 XLSX：数字 0 保留，空白单元格被识别为缺失。"""
    original = pd.DataFrame(
        {"编号": ["00123", "A002"], "金额": [0, 128.5], "备注": ["", None]}
    )
    loaded = load_table("数据.xlsx", to_xlsx_bytes(original))
    df = loaded.df

    assert df.shape == (2, 3)
    assert df.loc[0, "金额"] == 0
    assert pd.isna(df.loc[0, "备注"])
    assert int(find_missing(df)["备注"]) == 2


def test_load_xlsx_datetime_and_number_roundtrip():
    """XLSX 的日期/数字经过读取和再导出不能变成字符串或丢掉 0。"""
    original = pd.DataFrame(
        {
            "日期": pd.to_datetime(["2024-05-01", None, "2024-05-03"]),
            "金额": [0, 1.5, None],
        }
    )
    loaded = load_table("日期.xlsx", to_xlsx_bytes(original)).df

    assert str(loaded.loc[0, "日期"]).startswith("2024-05-01")
    assert pd.isna(loaded.loc[1, "日期"])
    assert loaded.loc[0, "金额"] == 0
    assert pd.isna(loaded.loc[2, "金额"])


def test_load_xlsx_empty_first_sheet_uses_next():
    """第一个工作表是空封面时，改用第一个有数据的工作表，并明确说明。"""
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame().to_excel(writer, sheet_name="封面", index=False)
        pd.DataFrame({"值": [1]}).to_excel(writer, sheet_name="数据", index=False)

    loaded = load_table("带封面.xlsx", buffer.getvalue())
    assert loaded.df["值"].tolist() == [1]
    assert any("封面" in note and "数据" in note for note in loaded.notes)


def test_load_xlsx_multiple_sheets_reads_first_and_tells_user():
    """多工作表只读第一个，但必须明确告知，不能悄悄丢数据。"""
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"值": [1]}).to_excel(writer, sheet_name="第一张", index=False)
        pd.DataFrame({"值": [2]}).to_excel(writer, sheet_name="第二张", index=False)

    loaded = load_table("多表.xlsx", buffer.getvalue())
    assert loaded.df["值"].tolist() == [1]
    assert any("2 个工作表" in note for note in loaded.notes)


def test_load_xlsx_select_named_sheet():
    """1.7.0：可以指定读哪张表；df、sheet_name、sheet_names 都要对。"""
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"值": [1, 2]}).to_excel(writer, index=False, sheet_name="第一张")
        pd.DataFrame({"值": [3, 4, 5]}).to_excel(writer, index=False, sheet_name="第二张")

    loaded = load_table("多表.xlsx", buffer.getvalue(), sheet="第二张")
    assert loaded.df["值"].tolist() == [3, 4, 5]
    assert loaded.sheet_name == "第二张"
    assert loaded.sheet_names == ("第一张", "第二张")
    assert any("本次只读取「第二张」" in note for note in loaded.notes)


def test_load_xlsx_unknown_sheet_lists_options():
    """指定的表名不存在：报错并列出可选表名，用户不用猜。"""
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"值": [1]}).to_excel(writer, index=False, sheet_name="订单")
        pd.DataFrame({"值": [2]}).to_excel(writer, index=False, sheet_name="客户")

    with pytest.raises(TableError, match="没有名为「不存在的表」") as excinfo:
        load_table("多表.xlsx", buffer.getvalue(), sheet="不存在的表")

    message = str(excinfo.value)
    assert "可选" in message and "订单" in message and "客户" in message


def test_load_csv_has_no_sheet_fields():
    """CSV 没有工作表概念：sheet_name 是 None，sheet_names 是空元组。"""
    loaded = load_table("普通.csv", csv_bytes("a\n1\n"))
    assert loaded.sheet_name is None
    assert loaded.sheet_names == ()


def _xlsx_with_chartsheet() -> bytes:
    """空 worksheet + chart sheet + 有数据的 worksheet（chart sheet 在目标表前面）。

    chart sheet 必须挂一张图表：没有图表就没有关系文件，openpyxl 自己读不回来。
    """
    from openpyxl.chart import BarChart

    buffer = BytesIO()
    workbook = openpyxl.Workbook()
    workbook.active.title = "封面"
    workbook.create_chartsheet("图表页").add_chart(BarChart())
    sheet = workbook.create_sheet("数据页")
    sheet.append(["a", "b"])
    sheet.append([1, 2])
    workbook.save(buffer)
    return buffer.getvalue()


def test_xlsx_chartsheet_before_data_is_skipped():
    """回归：chart sheet 不能占工作表序号，也不能算进「共 N 个工作表」。"""
    loaded = load_table("图表页.xlsx", _xlsx_with_chartsheet())

    assert loaded.df["a"].tolist() == [1]  # 读到的是数据页，不是图表页
    assert loaded.sheet_name == "数据页"
    assert loaded.sheet_names == ("封面", "数据页")  # 不含 chart sheet
    assert any("2 个工作表" in note for note in loaded.notes)
    assert any("「封面」没有数据" in note for note in loaded.notes)


def test_xlsx_chartsheet_can_be_skipped_by_name():
    """chart sheet 在文件里时，显式指定真实工作表名照样能读，且它不能当工作表选。"""
    raw = _xlsx_with_chartsheet()

    loaded = load_table("图表页.xlsx", raw, sheet="数据页")
    assert loaded.df.shape == (1, 2)
    assert loaded.sheet_name == "数据页"
    assert "图表页" not in loaded.sheet_names

    with pytest.raises(TableError, match="没有名为「图表页」"):
        load_table("图表页.xlsx", raw, sheet="图表页")


def _xlsx_with_formatted_blank_cover() -> bytes:
    """封面页只设边框、不写值：预检按 <row> 标签会以为它有数据。"""
    from openpyxl.styles import Border, Side

    buffer = BytesIO()
    workbook = openpyxl.Workbook()
    cover = workbook.active
    cover.title = "封面"
    for row in range(1, 6):
        for col in range(1, 4):
            cover.cell(row, col).border = Border(left=Side(style="thin"))
    sheet = workbook.create_sheet("数据")
    sheet.append(["订单号", "金额"])
    sheet.append(["A1", 10])
    workbook.save(buffer)
    return buffer.getvalue()


def test_xlsx_formatted_blank_cover_is_skipped():
    """回归：只有格式的空白封面页要跳过，不能把整份文件拒掉（1.6.3 的行为）。"""
    loaded = load_table("封面.xlsx", _xlsx_with_formatted_blank_cover())

    assert loaded.df.to_dict("list") == {"订单号": ["A1"], "金额": [10]}
    assert loaded.sheet_name == "数据"
    assert loaded.sheet_names == ("封面", "数据")
    assert any("「封面」没有数据" in note for note in loaded.notes)  # 跳过了哪张要说清
    assert any("本次只读取「数据」" in note for note in loaded.notes)  # 封面不算「本次读取的表」


def test_xlsx_formatted_blank_cover_explicit_sheet_does_not_switch():
    """显式指定封面页时不偷偷换表，只报中文错。"""
    with pytest.raises(TableError, match="没有"):
        load_table("封面.xlsx", _xlsx_with_formatted_blank_cover(), sheet="封面")


# ---------------------------------------------------------------- 读取：异常输入


def test_load_empty_bytes_raises():
    """0 字节文件：清晰提示，不进入解析。"""
    with pytest.raises(TableError, match="空的"):
        load_table("空.csv", b"")


def test_load_header_only_csv_raises():
    """只有表头没有数据行：明确报错。"""
    with pytest.raises(TableError, match="没有数据行"):
        load_table("只有表头.csv", csv_bytes("姓名,金额\n"))


def test_load_unsupported_extension_raises():
    """不支持的格式（.txt / .xls）在读取前被拒绝。"""
    with pytest.raises(TableError, match="只支持"):
        load_table("数据.txt", csv_bytes("a\n1\n"))
    with pytest.raises(TableError, match="只支持"):
        load_table("老表格.xls", b"\xd0\xcf\x11\xe0")


def test_load_corrupt_xlsx_raises():
    """损坏/伪装成 xlsx 的文件：给中文提示，不抛原始异常。"""
    with pytest.raises(TableError, match="无法打开"):
        load_table("坏文件.xlsx", b"this is not an excel file")


def test_binary_disguised_as_csv_raises():
    """把 xlsx 改名成 .csv：不能当成文本硬读，给出编码/类型提示。"""
    with pytest.raises(TableError, match="二进制"):
        load_table("伪装.csv", b"PK\x03\x04\x00\x00")


def test_load_too_large_raises():
    """超过大小上限：在解析前拒绝。"""
    with pytest.raises(TableError, match="太大"):
        load_table("大文件.csv", b"0" * (MAX_UPLOAD_BYTES + 1))


# ---------------------------------------------------------------- 资源限制


def test_row_limit_rejects(monkeypatch):
    """超过行数上限时给中文提示，不进入后续处理。"""
    monkeypatch.setattr(cleaning, "MAX_ROWS", 2)
    with pytest.raises(TableError, match="行数"):
        load_table("rows.csv", csv_bytes("值\n1\n2\n3\n"))


def test_quoted_newline_counts_as_one_record():
    """引号内的换行属于同一个单元格，不能算成多行（否则会误拒合法文件）。"""
    raw = '姓名,备注\n小明,"第一行\n第二行"\n'.encode("utf-8-sig")
    assert cleaning._estimate_csv_shape(raw, "utf-8-sig") == (1, 2)

    loaded = load_table("换行.csv", raw)
    assert loaded.df.shape == (1, 2)
    assert loaded.df.loc[0, "备注"] == "第一行\n第二行"


def test_quoted_newline_file_near_row_limit_is_not_rejected(monkeypatch):
    """行数刚好等于上限、但含大量跨行字段的文件必须放行。"""
    monkeypatch.setattr(cleaning, "MAX_ROWS", 2)
    raw = 'a,b\n1,"x\ny"\n2,"p\nq\nr"\n'.encode("utf-8-sig")
    assert cleaning._estimate_csv_shape(raw, "utf-8-sig") == (2, 2)
    assert load_table("多行.csv", raw).df.shape == (2, 2)


def test_csv_row_limit_checked_before_parsing(monkeypatch):
    """超限 CSV 不能先进 read_csv 占内存：解析前按行数预检就拒绝。"""
    monkeypatch.setattr(cleaning, "MAX_ROWS", 5)
    monkeypatch.setattr(
        pd, "read_csv", lambda *a, **k: pytest.fail("read_csv 不该被调用")
    )
    with pytest.raises(TableError, match="行数"):
        load_table("rows.csv", csv_bytes("值\n" + "1\n" * 10))


def _tamper_dimension(raw: bytes, new_ref: bytes, sheet: str = "sheet1.xml") -> bytes:
    """把工作表 XML 里的 <dimension ref="..."/> 改成指定值，返回新的 xlsx 字节。"""
    entry = f"xl/worksheets/{sheet}"
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        content = archive.read(entry)
    import re as _re

    # 不同 XML 序列化器（lxml / stdlib）写的自闭合标签空格不同：
    # lxml 是 <dimension ref="A1"/>，stdlib 是 <dimension ref="A1" />，两种都要认。
    tampered = _re.sub(rb'<dimension\b[^>]*?/>', b'<dimension ref="' + new_ref + b'"/>', content, count=1)
    assert tampered != content, "没找到 dimension，测试本身失效"

    out = BytesIO()
    with zipfile.ZipFile(BytesIO(raw)) as source, zipfile.ZipFile(
        out, "w", zipfile.ZIP_DEFLATED
    ) as target:
        for item in source.infolist():
            target.writestr(item.filename, tampered if item.filename == entry else source.read(item.filename))
    return out.getvalue()


def test_xlsx_real_rows_checked_before_parsing(monkeypatch):
    """按真实行数预检：行数超限时在 openpyxl 解析前就拒绝。"""
    buffer = BytesIO()
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["值"])
    for i in range(20):
        sheet.append([i])
    workbook.save(buffer)

    monkeypatch.setattr(cleaning, "MAX_ROWS", 5)
    with pytest.raises(TableError, match="行数"):
        load_table("行多.xlsx", buffer.getvalue())


def _xlsx_with_row_breaks(rows: int, breaks: int = 2) -> bytes:
    """造一个带手动分页符的 XLSX：Excel 里插过分页符的工作表会写 <rowBreaks>。"""
    from openpyxl.worksheet.pagebreak import Break

    buffer = BytesIO()
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["值"])
    for i in range(rows):
        sheet.append([i])
    for i in range(breaks):
        sheet.row_breaks.append(Break(id=10 * (i + 1)))
    workbook.save(buffer)
    return buffer.getvalue()


def test_xlsx_page_breaks_not_counted_as_rows(monkeypatch):
    """分页符 <rowBreaks> 不是数据行：正好等于行数上限的带分页符文件必须放行。

    回归：<rowBreaks> 也以 "<row" 开头，按子串数会多算 1 行，把合法文件误拒。
    """
    monkeypatch.setattr(cleaning, "MAX_ROWS", 20)
    raw = _xlsx_with_row_breaks(20)
    assert b"<rowBreaks" in zipfile.ZipFile(BytesIO(raw)).read("xl/worksheets/sheet1.xml")
    loaded = load_table("分页.xlsx", raw)
    assert loaded.df.shape == (20, 1)


def test_xlsx_page_breaks_do_not_push_file_over_limit(monkeypatch):
    """带分页符也不能少拦一行：21 行超过 20 行上限，提示里要给出真实行数。"""
    monkeypatch.setattr(cleaning, "MAX_ROWS", 20)
    with pytest.raises(TableError, match="21"):
        load_table("分页.xlsx", _xlsx_with_row_breaks(21))


def test_xlsx_limit_only_checks_the_sheet_being_read(monkeypatch):
    """1.7.0 预检策略：只对将被解析的那张表限额。

    默认读第一张（小表）→ 不扫后面的「大表」，放行；
    显式指定「大表」→ 被拒，提示里说清是哪张表、文件共几张。
    """
    monkeypatch.setattr(cleaning, "MAX_ROWS", 5)
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"值": range(3)}).to_excel(writer, index=False, sheet_name="小表")
        pd.DataFrame({"值": range(10)}).to_excel(writer, index=False, sheet_name="大表")
    raw = buffer.getvalue()

    loaded = load_table("两个表.xlsx", raw)  # 默认读第一张，不因为大表被拒
    assert loaded.df.shape == (3, 1)
    assert loaded.sheet_name == "小表"

    with pytest.raises(TableError, match="行数") as excinfo:
        load_table("两个表.xlsx", raw, sheet="大表")

    message = str(excinfo.value)
    assert "「大表」" in message  # 报 Excel 里看到的名字，不是 sheet2.xml 这种内部文件名
    assert "共 2 个工作表" in message
    assert "其他工作表不读取" in message  # 说清策略：只限额这一张


def _xlsx_with_chart(rows: int = 3) -> bytes:
    """带图表的工作簿：包里会多出 xl/drawings/ 和 xl/worksheets/_rels/ 文件。"""
    from openpyxl.chart import BarChart, Reference

    buffer = BytesIO()
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["月", "额"])
    for i in range(rows):
        sheet.append([i, i * 10])
    chart = BarChart()
    chart.add_data(
        Reference(sheet, min_col=2, min_row=1, max_row=rows + 1), titles_from_data=True
    )
    sheet.add_chart(chart, "D2")
    workbook.save(buffer)
    return buffer.getvalue()


def test_chart_parts_are_not_counted_as_worksheets(monkeypatch):
    """回归：带图表的工作簿有 xl/worksheets/_rels/ 文件，它不是工作表，表数不能多算。

    以前按 "xl/worksheets/" 前缀筛，会把关系文件当第二张表，提示里的表数和序号都错。
    """
    raw = _xlsx_with_chart(3)
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        assert "xl/worksheets/_rels/sheet1.xml.rels" in archive.namelist(), "测试前置条件失效"

    assert load_table("图表.xlsx", raw).df.shape == (3, 2)  # 正常文件照常读出

    monkeypatch.setattr(cleaning, "MAX_ROWS", 1)
    with pytest.raises(TableError) as excinfo:
        load_table("图表.xlsx", raw)
    message = str(excinfo.value)
    # 唯一一张表超限 → 走「每张表都超限」的报错，名字仍是真实工作表名
    assert "每张工作表都超过" in message
    assert "「Sheet」" in message
    assert message.count("「Sheet」") == 1  # 关系文件没被当成第二张表


def test_worksheet_rels_entry_does_not_inflate_sheet_count(monkeypatch):
    """回归：包里混进 xl/worksheets/_rels/sheet1.xml.rels 时，表数仍是真实工作表数。"""
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"值": range(3)}).to_excel(writer, index=False, sheet_name="小表")
        pd.DataFrame({"值": range(10)}).to_excel(writer, index=False, sheet_name="大表")

    source = zipfile.ZipFile(BytesIO(buffer.getvalue()))
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            target.writestr(item.filename, source.read(item.filename))
        target.writestr("xl/worksheets/_rels/sheet1.xml.rels", b"<Relationships/>")

    monkeypatch.setattr(cleaning, "MAX_ROWS", 5)
    with pytest.raises(TableError) as excinfo:
        load_table("关系.xlsx", out.getvalue(), sheet="大表")  # 大表才超限，点名读它
    message = str(excinfo.value)
    assert "共 2 个工作表" in message  # 不是 3
    assert "「大表」" in message


def _xlsx_with_special_sheet_names() -> bytes:
    """表名里带 XML 实体字符（& < > "）：Excel 会存成 &amp; &lt; &gt; &quot;。"""
    buffer = BytesIO()
    workbook = openpyxl.Workbook()
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


def _rewrite_entry(raw: bytes, name: str, *replacements: tuple[bytes, bytes]) -> bytes:
    """替换 zip 里某个文本条目的内容，返回新字节（测试构造异常包用）。"""
    content = zipfile.ZipFile(BytesIO(raw)).read(name)
    for old, new in replacements:
        assert old in content, f"{name} 里没有 {old!r}，测试前置条件失效"
        content = content.replace(old, new)
    out = BytesIO()
    with zipfile.ZipFile(BytesIO(raw)) as source, zipfile.ZipFile(
        out, "w", zipfile.ZIP_DEFLATED
    ) as target:
        for item in source.infolist():
            target.writestr(item.filename, content if item.filename == name else source.read(item.filename))
    return out.getvalue()


def test_xlsx_special_chars_in_sheet_names_are_decoded():
    """回归：表名里的 &amp; 等实体必须解码，否则和 openpyxl 读出来的名字对不上。

    以前用正则取 name 属性，拿到「Sales &amp; Marketing」，默认读表直接报「找不到要读取的工作表」。
    """
    raw = _xlsx_with_special_sheet_names()
    loaded = load_table("特殊表名.xlsx", raw)
    assert loaded.sheet_names == ("Sales & Marketing", "A<B>C", '他说"好" & 报价')
    assert loaded.sheet_name == "Sales & Marketing"  # 默认读第一张，且名字是解码后的
    assert loaded.df.shape == (1, 2)


@pytest.mark.parametrize(
    ("name", "rows"), [("Sales & Marketing", 1), ("A<B>C", 1), ('他说"好" & 报价', 1)]
)
def test_xlsx_special_chars_sheet_can_be_selected_by_name(name, rows):
    """显式选表要能按解码后的名字选中（多工作表切换的底层能力）。"""
    loaded = load_table("特殊表名.xlsx", _xlsx_with_special_sheet_names(), sheet=name)
    assert loaded.sheet_name == name
    assert loaded.df.shape == (rows, 1) or loaded.df.shape == (rows, 2)


def test_xlsx_raw_entity_name_is_not_accepted():
    """用没解码的原文选表必须报错，并列出解码后的可选名字（说明我们没在两种写法间含糊）。"""
    raw = _xlsx_with_special_sheet_names()
    with pytest.raises(TableError) as excinfo:
        load_table("特殊表名.xlsx", raw, sheet="Sales &amp; Marketing")
    message = str(excinfo.value)
    assert "可选：Sales & Marketing、A<B>C" in message  # 列的是解码后的真名


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (b'Target="/xl/worksheets/sheet1.xml"', b'Target="worksheets/sheet1.xml"'),  # 相对 xl/
        (b'Target="/xl/worksheets/sheet1.xml"', b'Target="../xl/worksheets/sheet1.xml"'),  # 带 ..
    ],
)
def test_xlsx_relationship_target_paths_are_resolved(old, new):
    """关系文件里的工作表路径：绝对路径和带 .. 的相对路径都要能解析到包内真实条目。"""
    raw = _rewrite_entry(
        _xlsx_with_special_sheet_names(), "xl/_rels/workbook.xml.rels", (old, new)
    )
    loaded = load_table("路径变体.xlsx", raw)
    assert loaded.sheet_name == "Sales & Marketing"
    assert loaded.sheet_names[0] == "Sales & Marketing"
    assert loaded.df.shape == (1, 2)


def test_xlsx_without_dimension_still_counts_columns():
    """老式/精简工作簿没有 <dimension> 时，列数要退回逐单元格扫描算对。"""
    import re

    buffer = BytesIO()
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["a", "b", "c"])
    for i in range(3):
        sheet.append([i, i, i])
    workbook.save(buffer)

    entry = "xl/worksheets/sheet1.xml"
    content = zipfile.ZipFile(BytesIO(buffer.getvalue())).read(entry)
    stripped = re.sub(rb'<dimension\b[^>]*?/>', b"", content, count=1)
    assert stripped != content

    out = BytesIO()
    with zipfile.ZipFile(BytesIO(buffer.getvalue())) as source, zipfile.ZipFile(
        out, "w", zipfile.ZIP_DEFLATED
    ) as target:
        for item in source.infolist():
            target.writestr(item.filename, stripped if item.filename == entry else source.read(item.filename))

    assert load_table("无dimension.xlsx", out.getvalue()).df.shape == (3, 3)


def test_xlsx_tampered_dimension_does_not_hide_rows():
    """回归：dimension 被伪造成很小时，仍按真实行数处理，不能静默丢行。"""
    buffer = BytesIO()
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["值"])
    for i in range(10):
        sheet.append([i])
    workbook.save(buffer)

    tampered = _tamper_dimension(buffer.getvalue(), b"A1:A2")
    loaded = load_table("伪造.xlsx", tampered)
    assert loaded.df.shape == (10, 1)  # 10 条数据一行不少
    assert loaded.df["值"].tolist() == list(range(10))


def test_clean_report_counts_each_step():
    """每一步删了多少行要分开算：全行去重 1、按列去重 1、空值行 1（顺序固定）。"""
    df = pd.DataFrame(
        {
            "订单号": ["A1", "A1", "A1", "A2", "A3"],
            "金额": ["10", "10", "20", "30", ""],
        }
    )
    report = clean_report(
        df,
        drop_duplicates=True,
        dedupe_on=["订单号"],
        drop_missing_in=["金额"],
    )
    assert report.df["订单号"].tolist() == ["A1", "A2"]
    assert report.steps == {"删除完全重复的行": 1, "按指定列去重": 1, "删除指定列中有空值的行": 1}
    assert len(report.df) == len(df) - sum(report.steps.values())


def test_clean_report_sample_rows_and_reason():
    """勾了样例才返回被删的行，且行里带「删除原因」、最多 sample 行。"""
    df = pd.DataFrame({"订单号": ["A1", "A1", "A2"], "金额": ["10", "10", "20"]})
    report = clean_report(df, dedupe_on=["订单号"], sample=5)
    assert report.steps == {"按指定列去重": 1}
    assert report.removed["订单号"].tolist() == ["A1"]
    assert report.removed["删除原因"].tolist() == ["按指定列去重"]

    no_sample = clean_report(df, dedupe_on=["订单号"])
    assert no_sample.removed.empty
    assert "删除原因" in no_sample.removed.columns  # 空表也有列，界面直接渲染不报错
    assert no_sample.df.equals(report.df)


def test_xlsx_over_limit_sheet_is_skipped_by_default(monkeypatch):
    """默认读表时，超限的表跳过、接着读下一张，并把跳过原因写进提示。

    以前会让整份文件直接打不开，用户连选表框都看不到。
    """
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"值": range(10)}).to_excel(writer, index=False, sheet_name="大表")
        pd.DataFrame({"值": range(2)}).to_excel(writer, index=False, sheet_name="小表")
    monkeypatch.setattr(cleaning, "MAX_ROWS", 5)

    loaded = load_table("两表.xlsx", buffer.getvalue())
    assert loaded.sheet_name == "小表"
    assert loaded.df.shape == (2, 1)
    assert any("「大表」" in note and "跳过" in note for note in loaded.notes)


def test_xlsx_over_limit_sheet_still_refuses_when_explicit(monkeypatch):
    """用户点名要那张超限的表时，仍要照实报错（不能悄悄换成别的表）。"""
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"值": range(10)}).to_excel(writer, index=False, sheet_name="大表")
        pd.DataFrame({"值": range(2)}).to_excel(writer, index=False, sheet_name="小表")
    monkeypatch.setattr(cleaning, "MAX_ROWS", 5)

    with pytest.raises(TableError) as excinfo:
        load_table("两表.xlsx", buffer.getvalue(), sheet="大表")
    message = str(excinfo.value)
    assert "「大表」" in message and "10" in message


def test_xlsx_all_sheets_over_limit_lists_each_reason(monkeypatch):
    """每张表都超限时，错误里要列出每张表的名字和它卡在哪。"""
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"值": range(10)}).to_excel(writer, index=False, sheet_name="大表一")
        pd.DataFrame({"值": range(12)}).to_excel(writer, index=False, sheet_name="大表二")
    monkeypatch.setattr(cleaning, "MAX_ROWS", 5)

    with pytest.raises(TableError) as excinfo:
        load_table("全超.xlsx", buffer.getvalue())
    message = str(excinfo.value)
    assert "「大表一」" in message and "「大表二」" in message
    assert "每张工作表都超过" in message


def test_xlsx_forged_small_dimension_cannot_hide_columns(monkeypatch):
    """回归：dimension 被伪造成 1 列、实际 3 列时，列数上限必须在解析前拦住。

    以前有 dimension 就跳过真实单元格扫描，伪造小 dimension 能绕过列数预检，
    等于先把解析资源花掉再被拒。这里连 ExcelFile 都设成「不许被调用」。
    """
    original = pd.DataFrame({"a": [1, 2], "b": [3, 4], "c": [5, 6]})
    tampered = _tamper_dimension(to_xlsx_bytes(original), b"A1")

    monkeypatch.setattr(cleaning, "MAX_COLS", 2)
    monkeypatch.setattr(
        pd, "ExcelFile", lambda *a, **k: pytest.fail("解析前就该拒绝，ExcelFile 不该被调用")
    )
    with pytest.raises(TableError, match="列数") as excinfo:
        load_table("伪造列.xlsx", tampered)
    assert "3" in str(excinfo.value)  # 报的是真实列数，不是伪造的 1


def test_xlsx_oversized_dimension_does_not_reject():
    """回归：dimension 写大（A1:XFD2）不能误拒真实只有 3 列的文件。

    列数只按真实 <c r="…"> 扫，dimension 完全不参与判断。
    """
    original = pd.DataFrame({"a": [1, 2], "b": [3, 4], "c": [5, 6]})
    tampered = _tamper_dimension(to_xlsx_bytes(original), b"A1:XFD2")

    loaded = load_table("dim写大.xlsx", tampered)
    assert loaded.df.shape == (2, 3)


def test_column_limit_rejects(monkeypatch):
    """超过列数上限时给中文提示。"""
    monkeypatch.setattr(cleaning, "MAX_COLS", 2)
    with pytest.raises(TableError, match="列数"):
        load_table("cols.csv", csv_bytes("a,b,c\n1,2,3\n"))


def test_cell_limit_rejects(monkeypatch):
    """行列都没超，但单元格总数超限也要拒绝。"""
    monkeypatch.setattr(cleaning, "MAX_CELLS", 3)
    with pytest.raises(TableError, match="单元格"):
        load_table("cells.csv", csv_bytes("a,b\n1,2\n3,4\n"))


def test_xlsx_uncompressed_limit_rejects(monkeypatch):
    """XLSX 压缩包解压后过大时拒绝（防压缩炸弹）。"""
    monkeypatch.setattr(cleaning, "MAX_XLSX_UNCOMPRESSED_BYTES", 1)
    with pytest.raises(TableError, match="解压"):
        load_table("x.xlsx", to_xlsx_bytes(pd.DataFrame({"a": [1]})))


def test_xlsx_zip_bomb_rejected_before_parsing():
    """真实压缩炸弹：压缩后 250KB、解压后 250MB，必须在解析前拒绝。"""
    import zipfile

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", b"0" * (250 * 1024 * 1024))
    raw = buffer.getvalue()
    assert len(raw) < MAX_UPLOAD_BYTES  # 体积检查拦不住，必须靠解压检查

    with pytest.raises(TableError, match="解压"):
        load_table("炸弹.xlsx", raw)


# ---------------------------------------------------------------- 检查与清洗


def test_duplicate_rule_requires_all_columns_equal():
    """重复行判定：所有列都相同才算重复，保留第一次出现的那一行。"""
    df = pd.DataFrame({"编号": ["A1", "A1", "A2"], "数量": [1, 1, 1]})
    assert summarize(df)["完全重复行"] == 1

    cleaned = clean_data(df, drop_duplicates=True)
    assert cleaned["编号"].tolist() == ["A1", "A2"]
    assert len(df) == 3  # 原数据不能被改动


def test_missing_only_uses_selected_columns():
    """只按所选列判空：未选中列的空值行保留，数字 0 不算空。"""
    df = pd.DataFrame(
        {"订单号": ["A1", "A2", "A3"], "金额": [0, None, 10], "备注": ["", "x", "y"]}
    )
    cleaned = clean_data(df, drop_missing_in=["金额"])
    assert cleaned["订单号"].tolist() == ["A1", "A3"]
    assert len(df) == 3


def test_zero_and_empty_string_are_different():
    """0、"0" 都是有效数据；空文本和 None 才算缺失。"""
    df = pd.DataFrame({"a": [0, "", None, "0"]})
    assert summarize(df)["空值单元格"] == 2

    cleaned = clean_data(df, drop_missing_in=["a"])
    assert cleaned["a"].tolist() == [0, "0"]


def test_clean_without_options_is_a_copy():
    """默认不删除任何数据，且不修改传入的 DataFrame。"""
    df = pd.DataFrame({"a": [1, 1], "b": ["x", None]})
    cleaned = clean_data(df, drop_missing_in=["不存在的列"])

    assert cleaned.equals(df.reset_index(drop=True))
    cleaned.loc[0, "a"] = 999
    assert df.loc[0, "a"] == 1


def test_dedupe_on_keeps_first_row_per_key():
    """按列去重：同一订单号的多行只留第一次出现的那行（金额可以不同）。"""
    df = pd.DataFrame(
        {"订单号": ["A1", "A1", "A2", "A1"], "金额": [10, 20, 30, 40]}
    )
    cleaned = clean_data(df, dedupe_on=["订单号"])

    assert cleaned["订单号"].tolist() == ["A1", "A2"]
    assert cleaned["金额"].tolist() == [10, 30]  # 留的是第一条
    assert len(df) == 4  # 原数据不能被改动


def test_dedupe_on_and_drop_duplicates_can_be_combined():
    """全行去重和按列去重可同时开：先删完全重复行，再按列留第一条。"""
    df = pd.DataFrame(
        {"订单号": ["A1", "A1", "A1", "A2"], "金额": [10, 10, 20, 30]}
    )
    cleaned = clean_data(df, drop_duplicates=True, dedupe_on=["订单号"])

    assert cleaned["订单号"].tolist() == ["A1", "A2"]
    assert cleaned["金额"].tolist() == [10, 30]


def test_dedupe_on_runs_before_drop_missing():
    """顺序：按列去重（留第一条）→ 再删空值行，所以留下的空值行会被删掉。"""
    df = pd.DataFrame({"订单号": ["A1", "A1", "A2"], "金额": [None, 10, 20]})
    cleaned = clean_data(df, dedupe_on=["订单号"], drop_missing_in=["金额"])

    # 先去重留下 A1(空)/A2，再按「金额」删空 → 只剩 A2；顺序倒过来则会留下 A1=10
    assert cleaned["订单号"].tolist() == ["A2"]


def test_dedupe_on_ignores_unknown_columns():
    """和 drop_missing_in 一致：不存在的列名直接忽略，不报错也不乱删。"""
    df = pd.DataFrame({"a": [1, 1, 2]})
    assert clean_data(df, dedupe_on=["不存在的列"]).equals(df.reset_index(drop=True))
    assert clean_data(df, dedupe_on=["不存在的列", "a"])["a"].tolist() == [1, 2]


def test_find_suspicious_reports_spaces_and_formula_prefix():
    """可疑数据：首尾空格、= + @ 开头，只统计字符串单元格。"""
    df = pd.DataFrame({"备注": [" 加急", "=1+1", "正常", "", None], "金额": [1, 2, 3, 4, 5]})
    assert find_suspicious(df) == {
        "备注": {SPACE_ISSUE: 1, FORMULA_ISSUE: 1}
    }


@pytest.mark.parametrize("dtype", [object, "string", "category"])
def test_find_suspicious_scans_text_columns(dtype):
    """回归：object/StringDtype/Categorical 都不能漏检（pandas 2/3 判定不同）。"""
    df = pd.DataFrame({"备注": pd.Series([" 加急", "=1+1", "正常"], dtype=dtype)})
    assert find_suspicious(df) == {
        "备注": {SPACE_ISSUE: 1, FORMULA_ISSUE: 1}
    }


def test_find_suspicious_catches_strings_beyond_sample_window():
    """回归：字符串出现在列尾时也不能漏（之前只看前 100 个值）。"""
    df = pd.DataFrame({"列": pd.Series([None] * 200 + [" 加急", "=1+1"]).astype("category")})
    assert find_suspicious(df) == {"列": {SPACE_ISSUE: 1, FORMULA_ISSUE: 1}}


def test_find_suspicious_flags_obfuscated_formulas():
    """回归：前置空格/制表符/BOM/零宽字符开头的公式，检查与导出防护口径必须一致。"""
    tricky = [" =1+1", "\t=1+1", "\ufeff=1+1", "\u200b@cmd"]
    df = pd.DataFrame({"v": tricky})

    # 四项都要被报为疑似公式（旧实现只报「首尾空格」）
    assert find_suspicious(df)["v"][FORMULA_ISSUE] == 4
    # 导出防护也必须覆盖同样四项
    assert all(v.startswith("'") for v in shield_csv_formulas(df)["v"].tolist())


def test_find_suspicious_and_export_agree_on_every_value():
    """同一批值，检查结果与导出防护不能出现一边报一边漏。"""
    values = ["=1+1", " =1+1", "\t=1+1", "@x", "+cmd", "-cmd", "-5", "+5", "正常", " 加急", ""]
    df = pd.DataFrame({"v": values})
    flagged = [value for value in values if _looks_like_formula(value)]
    shielded = [
        value for value, out in zip(values, shield_csv_formulas(df)["v"].tolist())
        if isinstance(out, str) and out.startswith("'")
    ]
    assert flagged == shielded


def test_find_suspicious_handles_all_empty_text_column():
    """回归：整列都是空值时 map 出来是 str dtype，直接 sum() 会得到 '' 并崩溃。"""
    for dtype in ("string", "str", object):
        df = pd.DataFrame(
            {"备注": pd.Series([None, None], dtype=dtype), "金额": [1, 2]}
        )
        assert find_suspicious(df) == {}


def test_find_suspicious_skips_object_numeric_columns():
    """object 列装的其实是数字时，不能被当成文本列逐值扫描。"""
    numbers = pd.Series([float(i) for i in range(1000)], dtype=object)
    assert _is_text_column(numbers) is False

    mixed = pd.Series([1.0] * 1000 + [" =1+1"], dtype=object)
    assert _is_text_column(mixed) is True
    assert find_suspicious(pd.DataFrame({"v": mixed})) == {
        "v": {SPACE_ISSUE: 1, FORMULA_ISSUE: 1}
    }


def test_find_suspicious_skips_non_text_columns_quickly():
    """数字/日期列不扫值：百万行数字列的判定必须是常数时间。"""
    df = pd.DataFrame({"n": range(1_000_000), "d": pd.date_range("2024-01-01", periods=1_000_000, freq="s")})
    assert find_suspicious(df) == {}


def test_whitespace_only_is_not_missing_but_is_suspicious():
    """只含空格不算缺失（不会删行），但会被提示。"""
    df = pd.DataFrame({"备注": [" ", "正常"]})
    assert summarize(df)["空值单元格"] == 0
    assert find_suspicious(df) == {"备注": {SPACE_ISSUE: 1}}


def test_summarize_empty_dataframe():
    """空表统计不报错。"""
    assert summarize(pd.DataFrame()) == {
        "行数": 0,
        "列数": 0,
        "空值单元格": 0,
        "完全重复行": 0,
    }


# ---------------------------------------------------------------- 导出


def test_export_csv_roundtrip_preserves_rows_and_values():
    """导出 CSV 后再读回：行数、00123、0 和空值都不变。"""
    df = pd.DataFrame({"编号": ["00123", "A2"], "金额": [0, None], "备注": ["", "x"]})
    back = load_table("导出.csv", to_csv_bytes(df)).df

    assert back.shape == df.shape
    assert back.loc[0, "编号"] == "00123"
    assert float(back.loc[0, "金额"]) == 0  # 0 没有变成空值
    assert int(find_missing(back)["金额"]) == 1


def test_export_xlsx_refuses_too_many_rows():
    """超过 Excel 行数上限时给明确提示，而不是崩溃。"""
    oversized = pd.DataFrame({"a": range(EXCEL_MAX_ROWS)})
    with pytest.raises(TableError, match="Excel"):
        to_xlsx_bytes(oversized)


def test_shield_csv_formulas_protects_dangerous_prefixes():
    """= + - @、制表符、回车开头的文本都要加单引号，且不修改原数据。"""
    values = ["=1+1", "+cmd", "-cmd", "@cmd", "\t=1", "\r=1", " =1"]
    df = pd.DataFrame({"值": values})

    shielded = shield_csv_formulas(df)
    assert shielded["值"].tolist() == [
        "'=1+1",
        "'+cmd",
        "'-cmd",
        "'@cmd",
        "'\t=1",
        "'\r=1",
        "' =1",
    ]
    assert df["值"].tolist() == values  # 原 DataFrame 不能被改

    # 列名也会被写进 CSV 第一行，同样要防护
    header = shield_csv_formulas(pd.DataFrame({"=表头": [1]}))
    assert header.columns.tolist() == ["'=表头"]


def test_shield_csv_formulas_handles_invisible_prefixes():
    """BOM/零宽字符开头的公式也要防护；带千分位的负数不能被误伤。"""
    df = pd.DataFrame({"v": ["\ufeff=1+1", "\u200b@x", "-1,000", "-1,234.56", "-5"]})
    assert shield_csv_formulas(df)["v"].tolist() == [
        "'\ufeff=1+1",
        "'\u200b@x",
        "-1,000",
        "-1,234.56",
        "-5",
    ]


def test_shield_csv_formulas_keeps_numbers_and_normal_text():
    """纯数字（含负数）和正常文本不能被误伤。"""
    df = pd.DataFrame(
        {"值": ["-5", "+5", "1e3", "-1.5", "-5 ", "00123", "正常", "-1e3", "-1,000", "-.5"]}
    )
    assert shield_csv_formulas(df)["值"].tolist() == df["值"].tolist()

    numbers = pd.DataFrame({"n": [0, -5, 1.5, None]})
    shielded = shield_csv_formulas(numbers)
    assert shielded["n"].tolist()[:3] == [0, -5, 1.5]
    assert pd.isna(shielded["n"].iloc[3])


def test_shield_does_not_mutate_normal_data():
    """数据完整性：正常编号、负数、科学计数法、千分位、普通文本都不能被防护改掉。"""
    normal = pd.DataFrame(
        {
            "编号": ["00123", "A002"],
            "金额": [-5, 0],
            "科学": ["-1e3", "+1.5e-3"],
            "文本": ["正常", "带空格 "],
        }
    )
    assert shield_csv_formulas(normal).equals(normal)

    back = load_table("往返.csv", to_csv_bytes(normal)).df
    assert back["编号"].tolist() == ["00123", "A002"]
    assert back["科学"].tolist() == ["-1e3", "+1.5e-3"]
    assert float(back.loc[0, "金额"]) == -5
    assert back.loc[1, "文本"] == "带空格 "


def test_export_csv_shields_formulas_by_default():
    """CSV 默认加单引号；传 False 时完全按原样导出。"""
    df = pd.DataFrame({"备注": ["=1+1", "-5", "正常"]})

    shielded = load_table("防护.csv", to_csv_bytes(df)).df
    assert shielded["备注"].tolist() == ["'=1+1", "-5", "正常"]

    raw = load_table("原样.csv", to_csv_bytes(df, neutralize_formulas=False)).df
    assert raw["备注"].tolist() == ["=1+1", "-5", "正常"]


def test_export_xlsx_keeps_other_formula_prefixes_as_text():
    """XLSX 里 + - @、制表符开头的文字也必须保持文本，不能变成公式。"""
    values = ["=1+1", "+1+1", "-1+1", "@cmd", "\t=1+1"]
    raw = to_xlsx_bytes(pd.DataFrame({"值": values}))

    workbook = openpyxl.load_workbook(BytesIO(raw), data_only=False)
    cells = [workbook.active.cell(row=i + 2, column=1) for i in range(len(values))]
    assert [cell.value for cell in cells] == values
    assert all(cell.data_type == "s" for cell in cells)


def test_export_xlsx_keeps_formula_like_text_as_text():
    """= 开头的文字导出后仍是文本，不会被 Excel 当公式执行。"""
    df = pd.DataFrame({"备注": ["=1+1", "正常"], "=编号": [1, 2]})
    raw = to_xlsx_bytes(df)

    workbook = openpyxl.load_workbook(BytesIO(raw), data_only=False)
    sheet = workbook.active
    assert sheet["A2"].value == "=1+1"
    assert sheet["A2"].data_type == "s"
    assert sheet["B1"].value == "=编号"
    assert sheet["B1"].data_type == "s"

    back = load_table("导出.xlsx", raw).df
    assert back.loc[0, "备注"] == "=1+1"


def test_export_xlsx_does_not_mutate_normal_data():
    """数据完整性：XLSX 往返后编号、负数、文本（含尾随空格）都不变。"""
    normal = pd.DataFrame(
        {"编号": ["00123", "A002"], "金额": [-5, 0], "文本": ["正常", "带空格 "]}
    )
    raw = to_xlsx_bytes(normal)

    workbook = openpyxl.load_workbook(BytesIO(raw), data_only=False)
    values = [cell.value for row in workbook.active.iter_rows() for cell in row]
    assert values == ["编号", "金额", "文本", "00123", -5, "正常", "A002", 0, "带空格 "]

    back = load_table("往返.xlsx", raw).df
    assert back["编号"].tolist() == ["00123", "A002"]
    assert back["金额"].tolist() == [-5, 0]
    assert back.loc[1, "文本"] == "带空格 "


def test_sample_data_contains_demo_problems():
    """样例数据是虚构的，并且能演示空值、重复行、0 和可疑格式。"""
    df = make_sample_df()
    stats = summarize(df)

    assert stats["行数"] == 6
    assert stats["完全重复行"] == 1
    assert stats["空值单元格"] == 3
    assert 0 in df["金额"].tolist()

    suspicious = find_suspicious(df)
    assert suspicious["备注"][SPACE_ISSUE] == 1
    assert suspicious["备注"][FORMULA_ISSUE] == 2
