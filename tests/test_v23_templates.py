"""v2.3.0 模板格式版本测试：新字段、旧模板兼容、未知版本与逐单元格一致性。"""

from __future__ import annotations

import json

import cleaning
import pandas as pd
import pytest


def _input_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "订单号": ["A001", "A002", "A002", "A003", "A004", "A005"],
            "客户名称": ["晨光", "星语", "星语", None, "远航", "山泉"],
            "金额": ["1,000", "20.5", "20.5", "300", "88", "12"],
            "日期": ["2024-05-01", "2024/5/2", "2024/5/2", "2024-05-03", "2024-05-04", "2024-05-05"],
            "数量": ["1", "2", "2", "3", "4", "5"],
            "单价": ["10", "10.25", "10.25", "100", "22", "2.4"],
            "备注": ["a", "b", "b", "c", "d", "e"],
        }
    )


def _rules_with_all_switches() -> dict[str, object]:
    return {
        "表头行": 1,
        "删除完全重复的行": True,
        "按列去重": ["订单号"],
        "删除空值行": ["客户名称"],
        "列重命名": [["客户名称", "客户"], ["金额", "总金额"]],
        "删除列": ["备注"],
        "列顺序": ["订单号", "客户", "含税金额"],
        "转数字": ["金额", "数量"],
        "转日期": ["日期"],
        "计算列": [["含税金额", "round(总金额*1.13, 2)"]],
    }


def _rules_without_all_switches() -> dict[str, object]:
    return {
        "表头行": 1,
        "删除完全重复的行": True,
        "按列去重": ["订单号"],
        "删除空值行": ["客户名称"],
        "列重命名": [["客户名称", "客户"]],
        "计算列": [["金额合计", "数量*单价"]],
        "列顺序": ["客户", "金额合计"],
    }


def test_build_template_writes_integer_format_version_one():
    """保存模板时写入整数 template_version = 1；旧「版本」字段仍保持 2。"""
    template = cleaning.build_template(_rules_with_all_switches(), name="订单模板")
    assert template["template_version"] == 1
    assert isinstance(template["template_version"], int)
    assert not isinstance(template["template_version"], bool)
    assert template["版本"] == cleaning.TEMPLATE_VERSION == 2
    assert template["名称"] == "订单模板"
    assert template["工具"] == cleaning.TEMPLATE_TOOL


def test_legacy_template_without_version_is_read_as_zero():
    """字段缺失按旧模板（版本 0）正常读入，规则一项不少。"""
    template = cleaning.build_template(_rules_with_all_switches())
    legacy = dict(template)
    legacy.pop("template_version")
    info = cleaning.load_template(cleaning.dumps(legacy))
    assert info.template_version == cleaning.LEGACY_TEMPLATE_VERSION == 0
    assert info.is_legacy is True
    assert info.rules == cleaning.normalize_rules(_rules_with_all_switches())
    assert info.tool == cleaning.TEMPLATE_TOOL
    assert info.name == "我的清洗规则"
    assert info.saved_at


def test_template_with_unknown_higher_version_raises_chinese_error():
    """更高版本只给中文提示，不崩溃、不静默按低版本解析。"""
    template = cleaning.build_template(_rules_with_all_switches())
    template["template_version"] = 2
    with pytest.raises(cleaning.TableError, match="高于当前工具支持的版本"):
        cleaning.load_template(cleaning.dumps(template))


@pytest.mark.parametrize("bad_version", ["1", 1.5, True, None, -1])
def test_template_with_invalid_version_raises_chinese_error(bad_version):
    """字段类型损坏或负数：明确指出字段问题，不猜版本。"""
    template = cleaning.build_template({})
    template["template_version"] = bad_version
    with pytest.raises(cleaning.TableError, match="template_version"):
        cleaning.load_template(cleaning.dumps(template))


def test_rules_only_json_still_loads_as_legacy():
    """只写规则层的手写旧模板仍可用，不带元信息时按版本 0。"""
    raw = json.dumps({"表头行": 3, "删除空值行": ["备注"]}, ensure_ascii=False).encode()
    info = cleaning.load_template(raw)
    assert info.template_version == 0
    assert info.is_legacy is True
    assert info.tool is None
    assert info.rules["表头行"] == 3
    assert info.rules["删除空值行"] == ["备注"]


@pytest.mark.parametrize(
    "rules",
    [
        _rules_with_all_switches(),
        _rules_without_all_switches(),
        {"删除完全重复的行": True},
        {"表头行": 2, "删除空值行": ["客户名称"], "列顺序": ["客户名称", "订单号"]},
    ],
)
def test_old_and_new_templates_clean_to_identical_cells(rules):
    """同一输入、同一开关组合，旧模板（无字段）与新模板输出逐单元格一致。"""
    frame = _input_frame()
    new_template = cleaning.build_template(rules)
    old_template = dict(new_template)
    old_template.pop("template_version")

    new_info = cleaning.load_template(cleaning.dumps(new_template))
    old_info = cleaning.load_template(cleaning.dumps(old_template))
    assert new_info.template_version == 1
    assert old_info.template_version == 0
    assert new_info.rules == old_info.rules

    raw = frame.to_csv(index=False).encode("utf-8-sig")
    header_row = int(str(rules.get("表头行", 1)))
    if header_row > 1:
        raw = ("标题行\n" * (header_row - 1)).encode("utf-8-sig") + raw
    new_options = cleaning.options_from_rules(new_info.rules)
    old_options = cleaning.options_from_rules(old_info.rules)
    new_result = cleaning.clean_bytes("输入.csv", raw, new_options)
    old_result = cleaning.clean_bytes("输入.csv", raw, old_options)
    assert new_result.rows_before == old_result.rows_before
    assert new_result.rows_after == old_result.rows_after
    assert new_result.steps == old_result.steps
    assert new_result.notes == old_result.notes
    assert new_result.load_notes == old_result.load_notes
    assert new_result.data == old_result.data

    # 再直接比一次 DataFrame，失败时能看清是哪一格不同（比只比字节更好定位）。
    new_frame = pd.read_csv(pd.io.common.BytesIO(new_result.data), dtype=str, keep_default_na=False)
    old_frame = pd.read_csv(pd.io.common.BytesIO(old_result.data), dtype=str, keep_default_na=False)
    pd.testing.assert_frame_equal(new_frame, old_frame, check_exact=True)
