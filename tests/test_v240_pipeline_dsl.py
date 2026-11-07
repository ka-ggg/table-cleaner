"""v2.4.0 W4 测试：DSL v1 校验、旧模板迁移、编译、计划描述、往返与版本策略。

覆盖规划 G-10 ~ G-12 的合同部分：9/9 节点、中文错误定位到节点、迁移字段等价与
3 个数据集清洗结果一致、往返稳定、严格整数版本、计划描述与入口隔离。
"""

from __future__ import annotations

import ast
import sys
import tomllib
from dataclasses import replace
from pathlib import Path
from typing import Any

import cleaning
import cleaning.pipeline as pipeline_module
import pandas as pd
import pytest
from cleaning.engine.registry import STATUS_FULL_FALLBACK, node_types
from cleaning.pipeline import (
    COLUMN_COMPUTE,
    COLUMN_DROP,
    COLUMN_ORDER,
    COLUMN_RENAME,
    COLUMN_TO_DATE,
    COLUMN_TO_NUMBER,
    CURRENT_DSL_VERSION,
    DEFAULT_ENGINE_PREFERENCE,
    ROW_DEDUPE_BY,
    ROW_DROP_DUPLICATES,
    ROW_DROP_MISSING,
    SUPPORTED_DSL_VERSIONS,
    Pipeline,
    describe_pipeline,
    dump_pipeline,
    load_pipeline,
    migrate_pipeline,
    pipeline_schema,
    pipeline_to_document,
    pipeline_to_options,
    template_to_pipeline,
    validate_pipeline,
)
from cleaning.templates import build_template, dumps, load_template
from cleaning.transform import CleanOptions

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- 测试夹具


def _node(node_id: str, node_type: str, params: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    node: dict[str, Any] = {"id": node_id, "type": node_type, "params": {} if params is None else params}
    node.update(extra)
    return node


def _document(nodes: list[dict[str, Any]] | None = None, **extra: Any) -> dict[str, Any]:
    document: dict[str, Any] = {"dsl_version": CURRENT_DSL_VERSION, "nodes": [] if nodes is None else nodes}
    document.update(extra)
    return document


def _one(node_type: str, params: dict[str, Any] | None = None, node_id: str = "n1") -> dict[str, Any]:
    return _document([_node(node_id, node_type, params)])


def _full_nodes() -> list[dict[str, Any]]:
    return [
        _node("dedupe-all", ROW_DROP_DUPLICATES),
        _node("dedupe-order", ROW_DEDUPE_BY, {"columns": ["订单号"]}),
        _node("drop-empty-customer", ROW_DROP_MISSING, {"columns": ["客户名称"]}),
        _node("drop-note", COLUMN_DROP, {"columns": ["备注"]}),
        _node("to-number-amount", COLUMN_TO_NUMBER, {"columns": ["金额", "数量"]}),
        _node("to-date-order", COLUMN_TO_DATE, {"columns": ["日期"]}),
        _node("rename-customer", COLUMN_RENAME, {"renames": [{"from": "客户名称", "to": "客户"}]}),
        _node("compute-tax", COLUMN_COMPUTE, {"name": "含税金额", "expression": "金额*1.13"}),
        _node("order-columns", COLUMN_ORDER, {"columns": ["订单号", "客户", "含税金额"]}),
    ]


def _all_switches_rules() -> dict[str, Any]:
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


def _frame_one() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "订单号": ["A001", "A002", "A002", "A003", "A004", None],
            "客户名称": ["晨光", "星语", "星语", "山泉", None, "远航"],
            "金额": ["1,000", "20.5", "20.5", "300", "88", "12"],
            "日期": ["2024-05-01", "2024/5/2", "2024/5/2", "2024-05-03", "2024-05-04", "2024-05-05"],
            "数量": ["1", "2", "2", "3", "4", "5"],
            "单价": ["10", "10.25", "10.25", "100", "22", "2.4"],
            "备注": ["a", "b", "b", "=1+1", "d", "e"],
        }
    )


def _frame_two() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "订单号": ["B001", "B001", "B002", "B003"],
            "客户名称": ["甲", "甲", "乙", "丙"],
            "金额": ["2,000", "2,000", "-3.5", "4000000000"],
            "日期": ["2024年5月1日", "2024年5月1日", "2024-06-02", "2024-06-03"],
            "数量": ["1", "1", "2", "3"],
            "单价": ["100", "100", "1.75", "2"],
            "备注": ["x", "x", "", " 加急 "],
        }
    )


def _frame_three() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "订单号": ["C001", "C002", "C003", "C004", "C005"],
            "客户名称": ["东", "南", "西", "北", "中"],
            "金额": ["0", "12.5", "13", "14", "15"],
            "日期": ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04", "2025-01-05"],
            "数量": ["2", "3", "4", "5", "6"],
            "单价": ["1.5", "2", "2.5", "3", "3.5"],
            "备注": ["", "b", "c", "d", "e"],
        }
    )


_DATASETS = pytest.mark.parametrize(
    "frame",
    [_frame_one(), _frame_two(), _frame_three()],
    ids=["dataset-1", "dataset-2", "dataset-3"],
)

# 迁移样本矩阵：覆盖旧模板/新模板、只规则层、缺列、重名冲突、改名后引用等。
_MIGRATION_RULE_SETS: list[dict[str, Any]] = [
    {},
    {"表头行": 3},
    {"删除完全重复的行": True},
    {"按列去重": ["订单号"]},
    {"删除空值行": ["客户名称"]},
    {"删除列": ["备注"]},
    {"转数字": ["金额"]},
    {"转日期": ["日期"]},
    {"列重命名": [["客户名称", "客户"]]},
    {"计算列": [["含税", "金额*1.13"]]},
    {"列顺序": ["订单号", "客户"]},
    _all_switches_rules(),
    {"删除完全重复的行": True, "按列去重": ["订单号"], "删除空值行": ["客户名称"]},
    {"列重命名": [["客户名称", "客户"], ["客户名称", "客户2"]]},
    {"列重命名": [["客户名称", "金额"]]},
    {"计算列": [["含税", "金额*1.13"], ["含税", "金额*2"]]},
    {"计算列": [["合计", "（金额＋数量）"]]},
    {"删除列": ["不存在的列"], "转数字": ["也不存在"], "列顺序": ["还是不存在"]},
    {"列重命名": [["客户名称", "客户"]], "计算列": [["含税", "金额*1.13"]], "列顺序": ["客户", "含税"]},
    {"表头行": 2, "删除空值行": ["不存在的列"]},
    {"按列去重": ["订单号"], "转数字": ["金额"]},
    {"删除完全重复的行": False, "删除空值行": [], "列重命名": []},
]


# ---------------------------------------------------------------- 版本策略


def test_minimal_pipeline_uses_pandas_defaults():
    """空节点数组合法；引擎默认 pandas、表头默认第 1 行。"""
    pipeline = load_pipeline(_document())
    assert pipeline == Pipeline(
        dsl_version=1,
        nodes=(),
        name=None,
        engine_preference=DEFAULT_ENGINE_PREFERENCE,
        header_row=1,
    )
    assert CURRENT_DSL_VERSION == 1
    assert SUPPORTED_DSL_VERSIONS == (1,)


def test_missing_dsl_version_raises_chinese_error():
    with pytest.raises(cleaning.TableError, match="dsl_version"):
        load_pipeline({"nodes": []})


@pytest.mark.parametrize("bad_version", [True, 1.0, "1", None])
def test_non_integer_dsl_version_raises_chinese_error(bad_version: Any):
    with pytest.raises(cleaning.TableError, match="必须是整数"):
        load_pipeline({"dsl_version": bad_version, "nodes": []})


@pytest.mark.parametrize("bad_version", [0, -1, -100])
def test_dsl_version_below_one_raises_chinese_error(bad_version: int):
    with pytest.raises(cleaning.TableError, match="大于等于 1"):
        load_pipeline({"dsl_version": bad_version, "nodes": []})


def test_future_dsl_version_is_rejected_not_silently_parsed():
    """DC-3：旧读取器拒绝更高版本，不静默忽略未知字段。"""
    with pytest.raises(cleaning.TableError, match="高于当前工具支持的版本") as caught:
        load_pipeline({"dsl_version": 2, "nodes": []})
    assert "升级" in str(caught.value)


def test_migrate_v1_is_identity_and_does_not_mutate_input():
    document = _document([_node("n", ROW_DROP_DUPLICATES)])
    migrated = migrate_pipeline(document)
    assert migrated == document
    migrated["nodes"].append("x")
    assert len(document["nodes"]) == 1


def test_future_version_is_rejected_before_migration():
    with pytest.raises(cleaning.TableError, match="高于当前工具支持"):
        migrate_pipeline({"dsl_version": CURRENT_DSL_VERSION + 1, "nodes": []})


def test_migration_hook_advances_version_step_by_step(monkeypatch: pytest.MonkeyPatch):
    """版本策略预留逐版本迁移钩子；升到 v2 时 v1 文档会被迁移函数改成 v2。"""
    monkeypatch.setattr(pipeline_module, "CURRENT_DSL_VERSION", 2)

    def migrate_v1(document: dict[str, Any]) -> dict[str, Any]:
        migrated = dict(document)
        migrated["name"] = "从 v1 迁移"
        return migrated

    monkeypatch.setitem(pipeline_module._MIGRATIONS, 1, migrate_v1)
    migrated = migrate_pipeline({"dsl_version": 1, "nodes": []})
    assert migrated["dsl_version"] == 2
    assert migrated["name"] == "从 v1 迁移"


def test_missing_migration_hook_is_chinese(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(pipeline_module, "CURRENT_DSL_VERSION", 2)
    with pytest.raises(cleaning.TableError, match="没有可用的迁移函数"):
        migrate_pipeline({"dsl_version": 1, "nodes": []})


# ---------------------------------------------------------------- 加载错误


def test_load_rejects_json_array():
    with pytest.raises(cleaning.TableError, match="JSON 对象"):
        load_pipeline("[]")


def test_load_rejects_broken_json():
    with pytest.raises(cleaning.TableError, match="有效的 JSON"):
        load_pipeline("{")


def test_load_rejects_empty_bytes():
    with pytest.raises(cleaning.TableError, match="空的"):
        load_pipeline(b"")


def test_load_rejects_unsupported_input_type():
    with pytest.raises(cleaning.TableError, match="JSON 对象"):
        load_pipeline(123)  # type: ignore[arg-type]


def test_validate_pipeline_rejects_non_mapping():
    with pytest.raises(cleaning.TableError, match="JSON 对象"):
        validate_pipeline([])  # type: ignore[arg-type]


def test_parse_document_rejects_unknown_encoding():
    with pytest.raises(cleaning.TableError, match="编码无法识别"):
        load_pipeline(b"\x80")


def test_template_to_pipeline_rejects_unsupported_input_type():
    with pytest.raises(cleaning.TableError, match="模板应"):
        template_to_pipeline(123)  # type: ignore[arg-type]


def test_load_accepts_utf8_bom_and_gb18030():
    document = _document([], name="订单清洗")
    utf8 = dump_pipeline(load_pipeline(document))
    assert load_pipeline(b"\xef\xbb\xbf" + utf8).name == "订单清洗"
    gb18030 = dump_pipeline(load_pipeline(document)).decode("utf-8").encode("gb18030")
    assert load_pipeline(gb18030).name == "订单清洗"


# ---------------------------------------------------------------- 结构校验


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        (_document(unknown=1), "不认识的字段"),
        (_document({"bad": "object"}), "应为数组"),
        ({"dsl_version": 1, "nodes": [{"id": "n", "type": ROW_DROP_DUPLICATES}]}, "缺少必填字段「params」"),
        (_document([_node("n", "row.unknown")]), "只能是"),
        (_one(ROW_DEDUPE_BY, {"columns": 1}), "应为数组"),
        (_one(ROW_DEDUPE_BY, {}), "缺少必填字段「params.columns」"),
        (_one(ROW_DEDUPE_BY, {"columns": []}), "至少要有 1 项"),
        (_one(ROW_DEDUPE_BY, {"columns": ["a", "a"]}), "不能有重复项"),
        (_one(COLUMN_COMPUTE, {"name": "含税"}), "params.expression"),
        (_one(COLUMN_COMPUTE, {"name": "含税", "expression": "x" * 201}), "最多 200 个字符"),
        (_one(COLUMN_RENAME, {"renames": [{"from": "a"}]}), "params.renames[0].to"),
        (_document([], engine={"preference": "spark"}), "只能是"),
        (_document([], source={"header_row": 0}), "不能小于 1"),
        (_one(ROW_DROP_DUPLICATES, node_id="bad id"), "空白"),
        (_one(ROW_DROP_DUPLICATES, {"columns": ["x"]}), "params.columns"),
        (_document([dict(_node("n", ROW_DROP_DUPLICATES), foo="bar")]), "不认识的字段"),
        (_document([], name=123), "应为字符串"),
        (_document([dict(_node("n", ROW_DROP_DUPLICATES), enabled="yes")]), "应为布尔值"),
        (_document([dict(_node("n", ROW_DROP_DUPLICATES), description="")]), "不能为空"),
        (_document([], engine={}), "engine.preference"),
    ],
)
def test_schema_errors_are_chinese_and_actionable(document: dict[str, Any], expected: str):
    """G-10：结构错误必须是中文，并指出字段/节点；未知类型在 schema 层拒绝。"""
    with pytest.raises(cleaning.TableError) as caught:
        validate_pipeline(document)
    message = str(caught.value)
    assert expected in message, message
    assert "流水线 DSL 校验失败" in message


def test_schema_error_points_at_node_index_and_id():
    """错误信息带第几个节点与 id，用户能直接定位到坏节点。"""
    document = _document(
        [
            _node("ok-node", ROW_DROP_DUPLICATES),
            _node("bad-node", COLUMN_COMPUTE, {"name": "含税"}),
        ]
    )
    with pytest.raises(cleaning.TableError) as caught:
        validate_pipeline(document)
    message = str(caught.value)
    assert "第 2 个节点" in message
    assert "bad-node" in message
    assert "params.expression" in message


# ---------------------------------------------------------------- 语义校验


def test_duplicate_node_ids_are_rejected():
    document = _document(
        [
            _node("same", ROW_DROP_DUPLICATES),
            _node("same", ROW_DEDUPE_BY, {"columns": ["订单号"]}),
        ]
    )
    with pytest.raises(cleaning.TableError, match="重复") as caught:
        validate_pipeline(document)
    assert "same" in str(caught.value)


def test_reversed_stage_order_is_rejected_with_both_nodes():
    document = _document(
        [
            _node("to-number", COLUMN_TO_NUMBER, {"columns": ["金额"]}),
            _node("drop-note", COLUMN_DROP, {"columns": ["备注"]}),
        ]
    )
    with pytest.raises(cleaning.TableError, match="顺序") as caught:
        validate_pipeline(document)
    message = str(caught.value)
    assert "to-number" in message
    assert "drop-note" in message
    assert "阶段" in message


def test_order_node_must_be_last():
    document = _document(
        [
            _node("order", COLUMN_ORDER, {"columns": ["订单号"]}),
            _node("compute", COLUMN_COMPUTE, {"name": "x", "expression": "金额*2"}),
        ]
    )
    with pytest.raises(cleaning.TableError, match="顺序"):
        validate_pipeline(document)


def test_single_stage_nodes_cannot_repeat():
    document = _document(
        [
            _node("first", ROW_DROP_DUPLICATES),
            _node("second", ROW_DROP_DUPLICATES),
        ]
    )
    with pytest.raises(cleaning.TableError, match="最多只能有 1 个") as caught:
        validate_pipeline(document)
    message = str(caught.value)
    assert "first" in message and "second" in message


def test_multiple_to_number_and_compute_nodes_are_allowed():
    """转数字/转日期/改名/计算列允许多节点；列名按首次出现去重，计算列按顺序执行。"""
    document = _document(
        [
            _node("num-1", COLUMN_TO_NUMBER, {"columns": ["金额"]}),
            _node("num-2", COLUMN_TO_NUMBER, {"columns": ["金额", "数量"]}),
            _node("rename-1", COLUMN_RENAME, {"renames": [{"from": "客户名称", "to": "客户"}]}),
            _node("rename-2", COLUMN_RENAME, {"renames": [{"from": "金额", "to": "总金额"}]}),
            _node("calc-1", COLUMN_COMPUTE, {"name": "含税", "expression": "总金额*1.13"}),
            _node("calc-2", COLUMN_COMPUTE, {"name": "折后", "expression": "含税*0.9"}),
        ]
    )
    options = pipeline_to_options(validate_pipeline(document))
    assert options.columns.to_number == ("金额", "数量")
    assert options.columns.rename == (("客户名称", "客户"), ("金额", "总金额"))
    assert options.columns.computed == (("含税", "总金额*1.13"), ("折后", "含税*0.9"))


def test_disabled_nodes_are_ignored_by_order_and_compile():
    """enabled=false 等价于节点不存在：不参与阶段顺序，也不进入 CleanOptions。"""
    document = _document(
        [
            _node("to-number", COLUMN_TO_NUMBER, {"columns": ["金额"]}, enabled=False),
            _node("drop-note", COLUMN_DROP, {"columns": ["备注"]}),
            _node("first-dedupe", ROW_DROP_DUPLICATES, enabled=False),
        ]
    )
    pipeline = validate_pipeline(document)
    assert pipeline.nodes[0].enabled is False
    options = pipeline_to_options(pipeline)
    assert options.columns.to_number == ()
    assert options.columns.drop == ("备注",)
    assert options.drop_duplicates is False


def test_pipeline_to_options_matches_options_from_rules_after_migration():
    """迁移后的 CleanOptions 与现有 options_from_rules 字段逐项相等。"""
    rules = _all_switches_rules()
    template = build_template(rules)
    info = load_template(dumps(template))
    assert pipeline_to_options(template_to_pipeline(template)) == cleaning.options_from_rules(info.rules)


# ---------------------------------------------------------------- 迁移与往返


@pytest.mark.parametrize("rules", _MIGRATION_RULE_SETS)
def test_template_migration_is_field_equivalent_for_new_and_legacy_templates(rules: dict[str, Any]):
    """≥20 个模板样本：旧/新模板迁移后 CleanOptions 与现有读取逐项相等，且可往返。"""
    template = build_template(rules)
    legacy = dict(template)
    legacy.pop("template_version")
    for payload in (dumps(template), dumps(legacy)):
        info = load_template(payload)
        pipeline = template_to_pipeline(payload)
        assert pipeline_to_options(pipeline) == cleaning.options_from_rules(info.rules)
        assert load_pipeline(dump_pipeline(pipeline)) == pipeline
        assert dump_pipeline(load_pipeline(dump_pipeline(pipeline))) == dump_pipeline(pipeline)


def test_template_to_pipeline_handles_filter_for_columns_output():
    """经 filter_for_columns 过滤后的规则也能迁移，字段与清洗结果保持一致。"""
    rules = _all_switches_rules()
    filtered, notes = cleaning.filter_for_columns(rules, ["订单号", "客户名称", "金额", "日期"])
    assert notes
    pipeline = template_to_pipeline(filtered)
    assert pipeline_to_options(pipeline) == cleaning.options_from_rules(filtered)
    assert load_pipeline(dump_pipeline(pipeline)) == pipeline


def test_template_to_pipeline_accepts_rules_only_mapping_and_template_info():
    """只规则层的 JSON、TemplateInfo、完整模板对象三种输入都支持。"""
    rules = {"表头行": 2, "删除空值行": ["客户名称"]}
    expected_options = cleaning.options_from_rules(rules)
    assert pipeline_to_options(template_to_pipeline(rules)) == expected_options
    info = load_template(dumps(build_template(rules)))
    assert pipeline_to_options(template_to_pipeline(info)) == expected_options
    document = build_template(rules)
    assert pipeline_to_options(template_to_pipeline(document)) == expected_options
    assert template_to_pipeline(document).name == document["名称"]
    assert template_to_pipeline(document).header_row == 2


def test_template_to_pipeline_rejects_broken_rules_layer():
    with pytest.raises(cleaning.TableError, match="规则"):
        template_to_pipeline({"规则": "not-a-mapping"})


@_DATASETS
def test_migration_cleans_three_datasets_identically(frame: pd.DataFrame):
    """3 个数据集：迁移前后 L1 数据、L2 CSV 字节、L3 报告/说明完全一致。"""
    rules = _all_switches_rules()
    payload = dumps(build_template(rules))
    info = load_template(payload)
    direct_options = cleaning.options_from_rules(info.rules)
    migrated_options = pipeline_to_options(template_to_pipeline(payload))
    assert migrated_options == direct_options

    direct = cleaning.clean_table(frame, direct_options, sample=2)
    migrated = cleaning.clean_table(frame, migrated_options, sample=2)
    pd.testing.assert_frame_equal(migrated.df, direct.df)
    pd.testing.assert_frame_equal(migrated.removed, direct.removed)
    assert migrated.steps == direct.steps
    assert migrated.notes == direct.notes
    assert cleaning.to_csv_bytes(migrated.df) == cleaning.to_csv_bytes(direct.df)

    raw = frame.to_csv(index=False).encode("utf-8-sig")
    direct_result = cleaning.clean_bytes("输入.csv", raw, direct_options)
    migrated_result = cleaning.clean_bytes("输入.csv", raw, migrated_options)
    assert migrated_result.data == direct_result.data
    assert migrated_result.steps == direct_result.steps
    assert migrated_result.notes == direct_result.notes
    assert migrated_result.rows_before == direct_result.rows_before
    assert migrated_result.rows_after == direct_result.rows_after


def test_pipeline_roundtrip_preserves_optional_fields_and_is_byte_stable():
    """name/engine/header/description/enabled 往返保留；两次 dump 完全一致。"""
    document = _document(
        [
            _node("drop", COLUMN_DROP, {"columns": ["备注"]}, description="删掉备注列"),
            _node("calc", COLUMN_COMPUTE, {"name": "含税", "expression": "金额*1.13"}, enabled=False),
        ],
        name="订单清洗",
        engine={"preference": "polars"},
        source={"header_row": 2},
    )
    pipeline = load_pipeline(document)
    assert pipeline.engine_preference == "polars"
    assert pipeline.header_row == 2
    assert pipeline.nodes[0].description == "删掉备注列"
    assert pipeline.nodes[1].enabled is False
    dumped = dump_pipeline(pipeline)
    assert dumped == dump_pipeline(load_pipeline(dumped))
    assert pipeline_to_document(pipeline) == pipeline_to_document(load_pipeline(dumped))


def test_roundtrip_document_validates_against_schema():
    pipeline = template_to_pipeline(build_template(_all_switches_rules()))
    document = pipeline_to_document(pipeline)
    assert load_pipeline(document) == pipeline


# ---------------------------------------------------------------- 计划描述


def test_plan_describes_standard_pipeline_columns_and_engine():
    """标准流水线的输入/输出列、节点顺序、lazy=false 与 pandas 引擎全部可核对。"""
    pipeline = load_pipeline(_document(_full_nodes(), name="订单清洗"))
    columns = ["订单号", "客户名称", "金额", "日期", "数量", "单价", "备注"]
    plan = describe_pipeline(pipeline, columns)

    assert plan.dsl_version == 1
    assert plan.name == "订单清洗"
    assert plan.engine == "pandas"
    assert plan.effective_engine == "pandas"
    assert plan.fallback_nodes == ()
    assert plan.warnings == ()
    assert plan.input_columns == tuple(columns)
    assert [node.stage for node in plan.nodes] == list(range(10, 100, 10))
    assert all(node.lazy_eligible is False for node in plan.nodes)
    assert all(node.engine == "pandas" for node in plan.nodes)

    drop_node = next(node for node in plan.nodes if node.id == "drop-note")
    assert drop_node.input_columns == tuple(columns)
    assert drop_node.output_columns == ("订单号", "客户名称", "金额", "日期", "数量", "单价")

    rename_node = next(node for node in plan.nodes if node.id == "rename-customer")
    assert "客户" in rename_node.output_columns
    assert "客户名称" not in rename_node.output_columns

    compute_node = next(node for node in plan.nodes if node.id == "compute-tax")
    assert compute_node.output_columns[-1] == "含税金额"
    assert "含税金额" not in compute_node.input_columns

    assert plan.output_columns == ("订单号", "客户", "含税金额", "金额", "日期", "数量", "单价")


def test_plan_reports_missing_columns_as_non_blocking_warnings():
    """列不存在只进入 warnings，不阻断计划；输出列按实际存在的列模拟。"""
    pipeline = load_pipeline(_document(_full_nodes()))
    plan = describe_pipeline(pipeline, ["订单号", "客户名称"])
    assert plan.effective_engine == "pandas"
    assert any("不存在" in warning for warning in plan.warnings)
    assert plan.output_columns == ("订单号", "客户", "含税金额")


def test_plan_rename_matches_pandas_one_pass_and_conflict_semantics():
    """rename 映射一次应用，且目标名已存在时跳过，与现有 apply_transforms 一致。"""
    document = _document(
        [
            _node(
                "rename",
                COLUMN_RENAME,
                {"renames": [{"from": "A", "to": "B"}, {"from": "B", "to": "C"}]},
            )
        ]
    )
    pipeline = load_pipeline(document)
    # 一次映射：A→B，但新列 B 不会被同一批的 B→C 再改名。
    assert describe_pipeline(pipeline, ["A", "D"]).output_columns == ("B", "D")
    # 目标名已存在：A→B 按现行行为跳过，B→C 仍生效，得到 A、C、D。
    plan = describe_pipeline(pipeline, ["A", "B", "D"])
    assert plan.output_columns == ("A", "C", "D")
    assert any("已经存在" in warning for warning in plan.warnings)


def test_plan_compute_name_conflict_is_skipped_with_warning():
    document = _document([_node("calc", COLUMN_COMPUTE, {"name": "金额", "expression": "金额*2"})])
    plan = describe_pipeline(load_pipeline(document), ["金额", "数量"])
    assert plan.output_columns == ("金额", "数量")
    assert any("重名" in warning for warning in plan.warnings)


def test_plan_includes_disabled_nodes_without_changing_columns():
    document = _document(
        [
            _node("drop-note", COLUMN_DROP, {"columns": ["备注"]}, enabled=False),
            _node("calc", COLUMN_COMPUTE, {"name": "含税", "expression": "金额*1.13"}),
        ]
    )
    plan = describe_pipeline(load_pipeline(document), ["金额", "备注"])
    assert plan.nodes[0].enabled is False
    assert plan.nodes[0].input_columns == plan.nodes[0].output_columns == ("金额", "备注")
    assert plan.nodes[1].output_columns == ("金额", "备注", "含税")


def test_plan_polars_preference_uses_registry_contract():
    """Polars 偏好下，按注册表目标状态描述执行引擎；本 PR 尚未实现，实际执行在 W2/W5。"""
    pipeline = load_pipeline(_document(_full_nodes(), engine={"preference": "polars"}))
    plan = describe_pipeline(pipeline, ["订单号", "客户名称", "金额", "日期", "数量", "单价", "备注"])
    assert plan.engine == "polars"
    assert plan.effective_engine == "polars"
    assert plan.fallback_nodes == ()
    assert all(node.engine == "polars" for node in plan.nodes)


def test_plan_full_fallback_moves_whole_pipeline_to_pandas(monkeypatch: pytest.MonkeyPatch):
    """capability 判定整节点回落时，effective_engine=pandas 且所有节点都按 pandas 描述。"""
    original = pipeline_module.capability_for_node_type

    def fake_capability(node_type: str) -> Any:
        capability = original(node_type)
        if node_type == COLUMN_TO_DATE:
            return replace(
                capability,
                polars_status=STATUS_FULL_FALLBACK,
                polars_reason="测试：混合日期整节点回落",
            )
        return capability

    monkeypatch.setattr(pipeline_module, "capability_for_node_type", fake_capability)
    pipeline = load_pipeline(_document(_full_nodes(), engine={"preference": "polars"}))
    plan = describe_pipeline(pipeline, ["订单号", "客户名称", "金额", "日期", "数量", "单价", "备注"])
    assert plan.engine == "polars"
    assert plan.effective_engine == "pandas"
    assert [fallback.id for fallback in plan.fallback_nodes] == ["to-date-order"]
    assert all(node.engine == "pandas" for node in plan.nodes)
    date_node = next(node for node in plan.nodes if node.id == "to-date-order")
    assert "混合日期" in (date_node.fallback_reason or "")


def test_plan_without_nodes_keeps_columns():
    plan = describe_pipeline(load_pipeline(_document()), ["A", "B"])
    assert plan.nodes == ()
    assert plan.input_columns == plan.output_columns == ("A", "B")


def test_plan_warnings_are_deduplicated_across_nodes():
    """多个节点产生同一条说明时只保留一份，计划输出不刷屏。"""
    document = _document(
        [
            _node("num-1", COLUMN_TO_NUMBER, {"columns": ["金额"]}),
            _node("num-2", COLUMN_TO_NUMBER, {"columns": ["金额"]}),
        ]
    )
    plan = describe_pipeline(load_pipeline(document), ["订单号"])
    assert plan.warnings == ("要转数字的列不存在，已忽略：金额。",)


# ---------------------------------------------------------------- schema 与打包


def test_schema_resource_matches_registry_contract():
    """schema 可从包内读取，9 个节点类型与注册表阶段顺序一致，未知字段拒绝。"""
    assert tuple(pipeline_module.NODE_STAGES) == node_types()
    schema = pipeline_schema()
    node_schema = schema["$defs"]["node"]
    assert node_schema["properties"]["type"]["enum"] == list(node_types())
    assert node_schema["additionalProperties"] is False
    assert set(node_schema["required"]) == {"id", "type", "params"}
    assert schema["properties"]["dsl_version"]["const"] == 1
    assert schema["properties"]["source"]["properties"]["header_row"]["default"] == 1
    assert schema["properties"]["engine"]["properties"]["preference"]["enum"] == ["pandas", "polars"]
    assert schema["additionalProperties"] is False


def test_schema_is_declared_as_wheel_package_data():
    """schema 必须随 wheel 发布，否则 importlib.resources 在安装后读不到。"""
    metadata = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = metadata["tool"]["setuptools"]["package-data"]
    assert package_data["cleaning.schema"] == ["*.json"]


class _FakeSchemaResource:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    def joinpath(self, _name: str) -> "_FakeSchemaResource":
        return self

    def read_text(self, encoding: str) -> str:
        assert encoding == "utf-8"
        return self.payload


def test_schema_missing_or_broken_file_is_chinese(monkeypatch: pytest.MonkeyPatch):
    """包数据缺失/损坏时只给中文错误，不把英文异常抛给用户。"""

    def missing_files(_package: str) -> Any:
        raise FileNotFoundError

    monkeypatch.setattr(pipeline_module.resources, "files", missing_files)
    with pytest.raises(cleaning.TableError, match="schema 文件缺失"):
        pipeline_schema()

    monkeypatch.setattr(pipeline_module.resources, "files", lambda _package: _FakeSchemaResource("{"))
    with pytest.raises(cleaning.TableError, match="不是有效 JSON"):
        pipeline_schema()

    monkeypatch.setattr(pipeline_module.resources, "files", lambda _package: _FakeSchemaResource("[]"))
    with pytest.raises(cleaning.TableError, match="JSON 对象"):
        pipeline_schema()


def test_jsonschema_is_imported_lazily_and_missing_dependency_is_chinese(monkeypatch: pytest.MonkeyPatch):
    """默认 pandas 入口不依赖 jsonschema；缺失时只在本内部功能给中文错误。"""
    source = (PROJECT_ROOT / "cleaning" / "pipeline.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    top_level_imports: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level_imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level_imports.append(node.module)
    assert "jsonschema" not in top_level_imports

    monkeypatch.setitem(sys.modules, "jsonschema", None)
    with pytest.raises(cleaning.TableError, match="jsonschema"):
        validate_pipeline(_document())


# ---------------------------------------------------------------- 入口隔离


def test_entry_layers_do_not_import_engine_or_pipeline_modules():
    """CLI/HTTP API/Streamlit 入口不读取引擎或 DSL，保持「入口不感知引擎」。"""
    for relative in ("app.py", "cleaning/cli.py", "api/http_api.py"):
        tree = ast.parse((PROJECT_ROOT / relative).read_text(encoding="utf-8"))
        modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
        forbidden = [
            module
            for module in modules
            if module == "cleaning.pipeline"
            or module.startswith("cleaning.pipeline.")
            or module in {"cleaning.engine", "cleaning.engine.base", "cleaning.engine.registry"}
            or module.startswith("cleaning.engine.")
        ]
        assert forbidden == [], f"{relative} 不应 import {forbidden}"


def test_pipeline_module_does_not_import_streamlit_or_polars():
    """DSL 是纯数据/校验模块，不依赖界面框架或 Polars 引擎。"""
    tree = ast.parse((PROJECT_ROOT / "cleaning" / "pipeline.py").read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    assert not any(module == "streamlit" or module.startswith("streamlit.") for module in modules)
    assert not any(module == "polars" or module.startswith("polars.") for module in modules)


def test_dsl_types_are_not_exported_from_public_api():
    """DSL 仍是内部模块：公共 __all__ 不出现 Pipeline 相关名字。"""
    assert {"pipeline", "Pipeline", "PipelineNode", "describe_pipeline", "load_pipeline"}.isdisjoint(cleaning.__all__)
    assert issubclass(cleaning.CleanOptions, object)
    assert isinstance(pipeline_to_options(load_pipeline(_document())), CleanOptions)
