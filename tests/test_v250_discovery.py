"""v2.5.0 W2：最近模板入口、场景预设提示、删行提示条（规划 5.3 / 5.4 / 5.5）。

全部用 AppTest 跑真实 ``app.py``，只验证界面行为：一键套用不自动导出、
场景提示不写控件状态、删行数量与样例随规则变化。
"""

from __future__ import annotations

from pathlib import Path

import cleaning
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


@pytest.fixture()
def app():
    def _new():
        at = AppTest.from_file(str(APP_PATH), default_timeout=180).run()
        assert not list(at.exception), list(at.exception)
        return at

    return _new


ECOMMERCE_CSV = (
    "订单编号,实付金额（元）,买家,收货地址,下单时间,商品\n"
    "A001,128,张三,杭州,2024-05-01,苹果\n"
    "A002,56,李四,上海,2024-05-02,梨\n"
).encode("utf-8-sig")

EXPRESS_CSV = (
    "运单号,寄件时间,重量(kg),快递费,收件地址,签收状态\n"
    "S001,2024-05-01,1.2,8,杭州,已签收\n"
    "S002,2024-05-02,2.5,12,上海,运输中\n"
).encode("utf-8-sig")

INVENTORY_CSV = (
    "SKU,仓库,可用库存,安全库存,更新时间\nSKU-1,杭州仓,10,3,2024-05-01\nSKU-2,上海仓,4,5,2024-05-02\n"
).encode("utf-8-sig")

ALL_SCENARIOS_CSV = (
    "订单号,金额,收货人,运单号,重量,运费,SKU,仓库,库存\nA001,128,张三,S001,1.2,8,SKU-1,杭州仓,10\n"
).encode("utf-8-sig")

NO_MATCH_CSV = "颜色,尺码\n红,L\n".encode("utf-8-sig")

PRODUCT_CSV = "商品,单价,数量\n苹果,10,2\n梨,20,3\n".encode("utf-8-sig")

PRODUCT_NEW_CSV = "商品,单价,数量\n苹果,10,2\n苹果,10,2\n梨,20,3\n".encode("utf-8-sig")

DUPLICATES_CSV = "编号,备注\nA1,一\nA1,一\nA2,二\nA2,二\n".encode("utf-8-sig")

MIXED_DELETES_CSV = "编号,备注\nA1,一\nA1,一\nA2,\nA3,三\n".encode("utf-8-sig")

MANY_DUPLICATES_CSV = ("编号,备注\n" + "".join(f"A{index},内容\nA{index},内容\n" for index in range(6))).encode(
    "utf-8-sig"
)


def _captions(at) -> list[str]:
    return [str(caption.value) for caption in at.caption]


def _warnings(at) -> list[str]:
    return [str(warning.value) for warning in at.warning]


def _buttons(at) -> list[str]:
    return [str(button.label) for button in at.button]


def _save_template(at, name: str) -> None:
    _by_label(at.text_input, "模板名称").input(name).run()
    _by_label(at.button, "保存当前规则为模板").click().run()
    assert not list(at.exception)


def _click_template_apply(at, name: str) -> None:
    button = next(button for button in at.button if button.key == f"模板_套用_{name}")
    button.click().run()
    assert not list(at.exception)


# ---------------------------------------------------------------- P1-4 场景预设


def test_scenario_hint_matches_ecommerce_fixture(app):
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", ECOMMERCE_CSV, "text/csv").run()

    hints = [text for text in _captions(at) if text.startswith("识别到类似")]
    assert len(hints) == 1
    assert hints[0].startswith("识别到类似「电商订单」")
    assert "命中订单号、金额" in hints[0]
    assert "仅提示，不会自动勾选" in hints[0]


def test_scenario_hint_matches_express_fixture(app):
    at = app()
    at.get("file_uploader")[0].upload("对账.csv", EXPRESS_CSV, "text/csv").run()

    hints = [text for text in _captions(at) if text.startswith("识别到类似")]
    assert len(hints) == 1
    assert hints[0].startswith("识别到类似「快递对账」")
    assert "命中运单号、寄件时间、重量、运费、目的地、签收状态" in hints[0]


def test_scenario_hint_matches_inventory_fixture(app):
    at = app()
    at.get("file_uploader")[0].upload("库存.csv", INVENTORY_CSV, "text/csv").run()

    hints = [text for text in _captions(at) if text.startswith("识别到类似")]
    assert len(hints) == 1
    assert hints[0].startswith("识别到类似「库存」")
    assert "命中SKU、仓库、库存、安全库存" in hints[0]


def test_scenario_hints_show_at_most_two(app):
    at = app()
    at.get("file_uploader")[0].upload("全部.csv", ALL_SCENARIOS_CSV, "text/csv").run()

    hints = [text for text in _captions(at) if text.startswith("识别到类似")]
    assert len(hints) == 2
    assert hints[0].startswith("识别到类似「电商订单」")
    assert hints[1].startswith("识别到类似「快递对账」")


def test_no_scenario_hint_without_enough_matching_columns(app):
    at = app()
    at.get("file_uploader")[0].upload("颜色.csv", NO_MATCH_CSV, "text/csv").run()

    assert not [text for text in _captions(at) if text.startswith("识别到类似")]


def test_scenario_hints_do_not_change_widget_state(app):
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", ECOMMERCE_CSV, "text/csv").run()

    assert all(checkbox.value is False for checkbox in at.checkbox)
    assert all(multiselect.value == [] for multiselect in at.multiselect)
    assert [text for text in _captions(at) if text.startswith("识别到类似")]

    at.run()

    assert all(checkbox.value is False for checkbox in at.checkbox)
    assert all(multiselect.value == [] for multiselect in at.multiselect)


# ---------------------------------------------------------------- P1-3 最近模板


def test_saved_template_shows_recent_entry_after_new_upload(app):
    at = app()
    at.get("file_uploader")[0].upload("商品.csv", PRODUCT_CSV, "text/csv").run()
    _checkbox(at, "删除完全重复的行").check().run()
    _save_template(at, "商品模板")

    assert at.session_state["_最近模板"]["名称"] == "商品模板"
    assert at.session_state["_最近模板"]["规则"]["删除完全重复的行"] is True

    at.get("file_uploader")[0].upload("商品新.csv", PRODUCT_NEW_CSV, "text/csv").run()

    assert "一键套用「商品模板」" in _buttons(at)
    captions = _captions(at)
    assert any(text.startswith("规则摘要：") and "全行去重" in text for text in captions)
    assert any("只还原规则；不会自动生成下载" in text for text in captions)


def test_recent_entry_hidden_without_recent_template(app):
    at = app()
    at.get("file_uploader")[0].upload("商品.csv", PRODUCT_CSV, "text/csv").run()

    assert not [label for label in _buttons(at) if label.startswith("一键套用")]


def test_one_click_template_restores_rules_and_invalidates_export(app):
    at = app()
    at.get("file_uploader")[0].upload("商品.csv", PRODUCT_CSV, "text/csv").run()
    _checkbox(at, "删除完全重复的行").check().run()
    _by_label(at.text_area, "列重命名").input("数量=件数").run()
    _save_template(at, "商品模板")

    at.get("file_uploader")[0].upload("商品新.csv", PRODUCT_NEW_CSV, "text/csv").run()
    _checkbox(at, "删除完全重复的行").uncheck().run()
    _by_label(at.text_area, "列重命名").input("").run()
    _button(at, "生成下载文件").click().run()
    assert "exports" in at.session_state

    _button(at, "一键套用「商品模板」").click().run()
    assert not list(at.exception)

    assert _checkbox(at, "删除完全重复的行").value is True
    assert _by_label(at.text_area, "列重命名").value == "数量=件数"
    assert "exports" not in at.session_state
    assert not [button for button in at.get("download_button") if button.label == "下载 CSV"]
    steps = [frame.value for frame in at.dataframe if "步骤" in getattr(frame.value, "columns", [])]
    assert steps and steps[0]["步骤"].tolist() == ["删除完全重复的行"]
    assert steps[0]["删掉行数"].tolist() == [1]

    # 上传新表 → 一键套用 → 生成下载：3 次操作后拿到可用下载。
    _button(at, "生成下载文件").click().run()
    assert "csv" in at.session_state["exports"]


def test_recent_template_column_mismatch_is_reported_and_filtered(app):
    at = app()
    at.get("file_uploader")[0].upload("商品.csv", PRODUCT_CSV, "text/csv").run()
    _checkbox(at, "按指定列去重").check().run()
    _multiselect(at, "选择去重用的列").select("数量").run()
    _by_label(at.text_area, "列重命名").input("商品=品名").run()
    _save_template(at, "数量模板")

    at.get("file_uploader")[0].upload("另一个.csv", "名称,金额\n甲,1\n".encode("utf-8-sig"), "text/csv").run()
    assert any("模板里有当前文件没有的列" in text for text in _captions(at))

    _button(at, "一键套用「数量模板」").click().run()
    assert not list(at.exception)

    infos = [str(info.value) for info in at.info]
    assert any("模板里有当前文件没有的列，已忽略" in text for text in infos)
    assert any("数量" in text for text in infos)
    assert _checkbox(at, "按指定列去重").value is False
    assert _multiselect(at, "选择去重用的列").value == []


def test_uploaded_template_json_records_recent_template(app):
    template = cleaning.build_template({"删除完全重复的行": True}, name="上传模板")
    at = app()
    at.get("file_uploader")[0].upload("商品.csv", PRODUCT_CSV, "text/csv").run()

    _by_label(at.get("file_uploader"), "上传之前保存的模板").set_value(
        ("模板.json", cleaning.dumps(template), "application/json")
    ).run()
    assert not list(at.exception)

    assert at.session_state["_最近模板"]["名称"] == "上传模板"
    assert "一键套用「上传模板」" in _buttons(at)


def test_applying_template_from_list_updates_recent_template(app):
    at = app()
    at.get("file_uploader")[0].upload("商品.csv", PRODUCT_CSV, "text/csv").run()
    _checkbox(at, "删除完全重复的行").check().run()
    _save_template(at, "模板A")

    _checkbox(at, "删除完全重复的行").uncheck().run()
    _by_label(at.text_area, "列重命名").input("数量=件数").run()
    _save_template(at, "模板B")
    assert at.session_state["_最近模板"]["名称"] == "模板B"

    _click_template_apply(at, "模板A")

    assert at.session_state["_最近模板"]["名称"] == "模板A"
    assert at.session_state["_最近模板"]["规则"]["删除完全重复的行"] is True


# ---------------------------------------------------------------- P1-5 删行提示


def test_delete_count_warning_and_sample_rows(app):
    at = app()
    at.get("file_uploader")[0].upload("重复.csv", DUPLICATES_CSV, "text/csv").run()
    _checkbox(at, "删除完全重复的行").check().run()

    assert "本次共删 2 行。" in _warnings(at)
    assert "查看被删样例（前 5 行）" in _buttons(at)
    assert "exports" not in at.session_state

    _button(at, "查看被删样例").click().run()
    assert not list(at.exception)

    samples = [frame.value for frame in at.dataframe if "删除原因" in getattr(frame.value, "columns", [])]
    assert samples, "没有渲染出被删行样例表"
    assert len(samples[0]) == 2
    assert samples[0]["删除原因"].tolist() == ["删除完全重复的行", "删除完全重复的行"]


def test_zero_deletions_hide_warning(app):
    at = app()
    at.get("file_uploader")[0].upload("干净.csv", "编号,备注\nA1,一\nA2,二\n".encode("utf-8-sig"), "text/csv").run()
    _checkbox(at, "删除完全重复的行").check().run()

    assert not [text for text in _warnings(at) if text.startswith("本次共删")]
    assert "查看被删样例（前 5 行）" not in _buttons(at)


def test_delete_count_updates_when_rules_change(app):
    at = app()
    at.get("file_uploader")[0].upload("混合.csv", MIXED_DELETES_CSV, "text/csv").run()
    _checkbox(at, "删除完全重复的行").check().run()
    assert "本次共删 1 行。" in _warnings(at)

    _checkbox(at, "删除指定列中有空值的行").check().run()
    _multiselect(at, "选择要检查空值的列").select("备注").run()
    assert "本次共删 2 行。" in _warnings(at)

    _checkbox(at, "删除完全重复的行").uncheck().run()
    assert "本次共删 1 行。" in _warnings(at)


def test_delete_sample_shows_each_reason(app):
    at = app()
    at.get("file_uploader")[0].upload("混合.csv", MIXED_DELETES_CSV, "text/csv").run()
    _checkbox(at, "删除完全重复的行").check().run()
    _checkbox(at, "删除指定列中有空值的行").check().run()
    _multiselect(at, "选择要检查空值的列").select("备注").run()
    _button(at, "查看被删样例").click().run()
    assert not list(at.exception)

    samples = [frame.value for frame in at.dataframe if "删除原因" in getattr(frame.value, "columns", [])]
    assert samples, "没有渲染出被删行样例表"
    assert samples[0]["删除原因"].tolist() == ["删除完全重复的行", "删除指定列中有空值的行"]


def test_delete_sample_limited_to_five_rows(app):
    at = app()
    at.get("file_uploader")[0].upload("很多重复.csv", MANY_DUPLICATES_CSV, "text/csv").run()
    _checkbox(at, "删除完全重复的行").check().run()
    assert "本次共删 6 行。" in _warnings(at)

    _button(at, "查看被删样例").click().run()
    assert not list(at.exception)

    samples = [frame.value for frame in at.dataframe if "删除原因" in getattr(frame.value, "columns", [])]
    assert samples, "没有渲染出被删行样例表"
    assert len(samples[0]) == 5
