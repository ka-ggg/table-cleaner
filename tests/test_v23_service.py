"""v2.3.0 编程式入口测试：CleanOptions 转换、清洗、检查与报告。"""

from __future__ import annotations

from io import BytesIO

import cleaning
import pandas as pd
import pytest


def _csv(text: str) -> bytes:
    return text.encode("utf-8-sig")


def test_options_from_rules_maps_every_template_switch():
    """模板规则 → CleanOptions 时每个开关都要落到唯一规则载体上。"""
    rules = {
        "表头行": 2,
        "删除完全重复的行": True,
        "按列去重": ["订单号"],
        "删除空值行": ["客户名称"],
        "列重命名": [["客户名称", "客户"]],
        "删除列": ["备注"],
        "列顺序": ["客户", "总额"],
        "转数字": ["金额"],
        "转日期": ["日期"],
        "计算列": [["总额", "金额*2"]],
    }
    options = cleaning.options_from_rules(rules)
    assert options.header_row == 2
    assert options.drop_duplicates is True
    assert options.dedupe_on == ("订单号",)
    assert options.drop_missing_in == ("客户名称",)
    assert options.columns.rename == (("客户名称", "客户"),)
    assert options.columns.drop == ("备注",)
    assert options.columns.order == ("客户", "总额")
    assert options.columns.to_number == ("金额",)
    assert options.columns.to_date == ("日期",)
    assert options.columns.computed == (("总额", "金额*2"),)


def test_clean_bytes_end_to_end_and_report():
    """一次完整清洗返回导出字节、前后行数和中文报告。"""
    data = _csv("订单号,客户名称,金额,备注\nA001,晨光,10,x\nA001,晨光,10,x\nA002,星语,20,y\n")
    options = cleaning.options_from_rules({"删除完全重复的行": True, "列重命名": [["金额", "总金额"]]})
    result = cleaning.clean_bytes("订单.csv", data, options)
    assert result.export_format == "csv"
    assert result.rows_before == 3
    assert result.rows_after == 2
    assert result.steps == {"删除完全重复的行": 1}
    back = pd.read_csv(BytesIO(result.data), dtype=str, keep_default_na=False)
    assert list(back.columns) == ["订单号", "客户名称", "总金额", "备注"]
    report = result.report_text
    assert "# 清洗报告" in report
    assert "清洗前：3 行" in report and "清洗后：2 行" in report
    assert "删除完全重复的行：1 行" in report


def test_clean_bytes_unknown_export_format_raises_chinese_error():
    with pytest.raises(cleaning.TableError, match="导出格式"):
        cleaning.clean_bytes("a.csv", _csv("a\n1\n"), cleaning.CleanOptions(), export_format="pdf")


def test_inspect_bytes_reports_missing_duplicate_and_suspicious():
    """只检查不改数据：空值、重复行、可疑格式都有结构化结果和中文报告。"""
    data = _csv("编号,备注\nA1,=1+1\nA1,=1+1\nA2, 空格\n")
    result = cleaning.inspect_bytes("检查.csv", data)
    assert result.rows == 3
    assert result.cols == 2
    assert result.columns == ("编号", "备注")
    assert result.duplicate_rows == 1
    assert result.missing == {"编号": 0, "备注": 0}
    assert result.suspicious["备注"][cleaning.FORMULA_ISSUE] == 2
    assert result.suspicious["备注"][cleaning.SPACE_ISSUE] == 1
    report = result.report_text
    assert "# 检查报告" in report
    assert "完全重复行：1 行" in report
    assert "疑似公式" in report


def test_inspect_bytes_does_not_clean_or_modify_source():
    """检查入口不得删行：同一个字节串进来，行数原样返回。"""
    data = _csv("编号\nA1\nA1\nA2\n")
    result = cleaning.inspect_bytes("检查.csv", data)
    assert result.rows == 3
    assert result.duplicate_rows == 1
    assert cleaning.clean_bytes("检查.csv", data, cleaning.CleanOptions()).rows_after == 3


def test_render_report_dispatches_both_result_types():
    clean_result = cleaning.CleanResult("a.csv", b"", "csv", 1, 1, {}, (), (), None)
    inspect_result = cleaning.InspectResult("a.csv", 1, 1, ("a",), {"a": 0}, 0, {}, (), None)
    assert cleaning.render_report(clean_result).startswith("# 清洗报告")
    assert cleaning.render_report(inspect_result).startswith("# 检查报告")
