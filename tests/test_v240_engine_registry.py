"""v2.4.0 W1 合同测试：17 项能力注册表、引擎协议与 pandas 参考包装。

这些测试只验证「合同」与「默认路径不变」，不要求 Polars 已实现（PR 3 才实现）：
- 17 项能力必须完整、编号固定、pandas 实现可解析、风险与用例字段齐全；
- pandas 包装必须与直接调用现有函数逐值一致；
- 公共入口签名与返回类型不变，入口层不感知引擎/DSL。
"""

from __future__ import annotations

import ast
import importlib
import inspect
from dataclasses import replace
from pathlib import Path

import cleaning
import cleaning.engine.base as engine_base
import cleaning.engine.registry as engine_registry
import pandas as pd
import pytest
from cleaning import rules, transform
from cleaning.engine import (
    CAPABILITIES,
    CAPABILITY_IDS,
    KIND_EXPRESSION,
    KIND_INSPECTOR,
    KIND_NODE,
    PANDAS_ENGINE,
    STATUS_ALIGNED,
    STATUS_FULL_FALLBACK,
    STATUS_PARTIAL_FALLBACK,
    EngineResult,
    PandasEngine,
    available_engines,
    capability_for_node_type,
    get_capability,
    get_engine,
    node_capabilities,
    register_engine,
    registry_problems,
    resolve_callable,
)
from cleaning.engine.base import DEFAULT_ENGINE

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENGINE_MODULES = (
    "cleaning/engine/base.py",
    "cleaning/engine/registry.py",
    "cleaning/engine/pandas_engine.py",
)

# 计划 5.3 的 v2.4.0 目标：核心结构算子与检查/表达式对齐，R5/R6/R8 边界回落。
EXPECTED_STATUS = {
    "R1": STATUS_ALIGNED,
    "R2": STATUS_ALIGNED,
    "R3": STATUS_ALIGNED,
    "R4": STATUS_ALIGNED,
    "R5": STATUS_PARTIAL_FALLBACK,
    "R6": STATUS_PARTIAL_FALLBACK,
    "R7": STATUS_ALIGNED,
    "R8": STATUS_PARTIAL_FALLBACK,
    "R9": STATUS_ALIGNED,
    "I1": STATUS_ALIGNED,
    "I2": STATUS_ALIGNED,
    "I3": STATUS_ALIGNED,
    "X1": STATUS_ALIGNED,
    "X2": STATUS_ALIGNED,
    "X3": STATUS_ALIGNED,
    "X4": STATUS_ALIGNED,
    "X5": STATUS_ALIGNED,
}


def _sample_options() -> cleaning.CleanOptions:
    return cleaning.CleanOptions(
        header_row=1,
        drop_duplicates=True,
        dedupe_on=("订单号",),
        drop_missing_in=("客户名称",),
        columns=cleaning.ColumnOps(
            rename=(("客户名称", "客户"),),
            drop=("备注",),
            order=("订单号", "客户", "金额"),
            to_number=("金额",),
            to_date=("日期",),
            computed=(("金额2", "金额*2"),),
        ),
    )


def test_registry_has_exactly_the_17_planned_capabilities():
    """17 项能力不多不少、编号固定，三类数量符合规划（9 + 3 + 5）。"""
    assert tuple(capability.id for capability in CAPABILITIES) == CAPABILITY_IDS
    assert len(CAPABILITIES) == 17
    kinds = [capability.kind for capability in CAPABILITIES]
    assert kinds.count(KIND_NODE) == 9
    assert kinds.count(KIND_INSPECTOR) == 3
    assert kinds.count(KIND_EXPRESSION) == 5


def test_registry_covers_nine_pipeline_nodes_in_stage_order():
    """9 个 DSL 节点类型与阶段 10–90 一一对应，且与 cleaning.pipeline 展开一致。"""
    nodes = node_capabilities()
    assert len(nodes) == 9
    assert [capability.stage for capability in nodes] == list(range(10, 100, 10))
    for capability in nodes:
        assert capability.node_type is not None
        assert capability_for_node_type(capability.node_type) is capability
        assert get_capability(capability.id) is capability


def test_every_capability_has_pandas_implementation_risk_and_cases():
    """G-03：17/17 项都有可解析的 pandas 实现、Polars 状态、风险与对齐用例。"""
    for capability in CAPABILITIES:
        implementation = resolve_callable(capability.pandas_impl)
        assert callable(implementation), capability.id
        assert capability.polars_status in (STATUS_ALIGNED, STATUS_PARTIAL_FALLBACK, STATUS_FULL_FALLBACK)
        assert capability.polars_reason.strip(), capability.id
        assert capability.risk.strip(), capability.id
        assert capability.case_ids, capability.id


def test_node_case_matrix_meets_plan_minimums():
    """每个流水线节点 ≥3 组用例，高风险 R5/R6/R8 ≥5 组；检查/表达式 ≥2 组。"""
    for capability in CAPABILITIES:
        if capability.kind == KIND_NODE:
            minimum = 5 if capability.id in {"R5", "R6", "R8"} else 3
            assert len(capability.case_ids) >= minimum, capability.id
        elif capability.kind == KIND_INSPECTOR:
            assert len(capability.case_ids) >= 2, capability.id
        elif capability.kind == KIND_EXPRESSION:
            assert len(capability.case_ids) >= 2, capability.id


def test_polars_status_matches_the_approved_contract():
    """状态字段来自计划 5.3，不在实现期悄悄改目标（变更需评审）。"""
    assert {capability.id: capability.polars_status for capability in CAPABILITIES} == EXPECTED_STATUS


def test_polars_implementation_flag_is_consistent():
    """未实现的能力不能填实现路径；实现路径存在时必须标记为已实现。"""
    for capability in CAPABILITIES:
        assert capability.polars_implemented == (capability.polars_impl is not None), capability.id


def test_registry_self_check_is_clean():
    """注册表自检为空，可直接作为 CI 门禁。"""
    assert registry_problems() == ()


def test_registry_self_check_catches_every_broken_field(monkeypatch: pytest.MonkeyPatch):
    """自检能发现编号、状态、风险、实现路径、节点阶段与用例数量问题。"""
    capabilities = list(CAPABILITIES)
    capabilities[0] = replace(capabilities[0], id="R99", kind="unknown")
    capabilities[1] = replace(capabilities[1], id="R99", polars_status="unknown")
    capabilities[2] = replace(capabilities[2], title="", summary="")
    capabilities[3] = replace(capabilities[3], risk="", polars_reason="", case_ids=())
    capabilities[4] = replace(capabilities[4], pandas_impl="cleaning.rules.no_such_attr")
    capabilities[5] = replace(capabilities[5], polars_implemented=True, polars_impl=None)
    capabilities[6] = replace(
        capabilities[6],
        stage=11,
        polars_impl="cleaning.rules.no_such_attr",
        polars_implemented=False,
    )
    capabilities[7] = replace(capabilities[7], node_type=None, stage=None)
    capabilities[8] = replace(
        capabilities[8],
        node_type=capabilities[0].node_type,
        stage=capabilities[0].stage,
    )
    capabilities[9] = replace(capabilities[9], case_ids=())
    capabilities[12] = replace(capabilities[12], case_ids=())
    monkeypatch.setattr(engine_registry, "CAPABILITIES", tuple(capabilities))

    joined = "\n".join(engine_registry.registry_problems())
    expected_fragments = [
        "能力编号应为",
        "能力编号有重复",
        "kind",
        "Polars 状态",
        "缺少中文名称或行为说明",
        "缺少风险说明",
        "缺少对齐/回落原因",
        "没有对齐用例 id",
        "pandas 实现",
        "标记为已实现，但缺少 polars_impl",
        "有 polars_impl 路径，但未标记为已实现",
        "Polars 实现",
        "缺少 node_type 或 stage",
        "节点类型",
        "不在 10–90",
        "阶段",
        "至少需要",
        "检查用例少于",
        "表达式用例少于",
        "流水线节点类型应为 9 个",
        "流水线阶段应覆盖",
    ]
    for fragment in expected_fragments:
        assert fragment in joined, fragment


def test_resolve_callable_rejects_bad_paths():
    """实现路径必须同时有模块、属性且可调用。"""
    with pytest.raises(ImportError):
        resolve_callable("nodots")
    with pytest.raises(ImportError):
        resolve_callable("cleaning.no_such_module.some_function")
    with pytest.raises(TypeError):
        resolve_callable("cleaning.rules.DUPLICATE_RULE")


def test_default_engine_is_pandas_and_registered():
    """默认引擎恒为 pandas；不传参数时返回同一个参考实现，不静默切换。"""
    assert DEFAULT_ENGINE == "pandas"
    assert isinstance(get_engine(), PandasEngine)
    assert get_engine("pandas") is PANDAS_ENGINE
    assert "pandas" in available_engines()


def test_unknown_engine_raises_chinese_error():
    """未知引擎只给中文错误，并提示默认仍是 pandas。"""
    with pytest.raises(cleaning.TableError, match="不存在") as caught:
        get_engine("duckdb")
    assert "pandas" in str(caught.value)


def test_unavailable_engine_raises_and_never_silently_falls_back(monkeypatch: pytest.MonkeyPatch):
    """显式选择不可用引擎时报中文错误，不会静默改用 pandas。"""
    # W2 之后 registry 会解析 polars_impl，polars 可能已经注册；先摘掉注册项，
    # 才能测到「模块 import 失败」的惰性加载分支。
    monkeypatch.delitem(engine_base._ENGINES, "polars", raising=False)
    monkeypatch.setitem(engine_base._LAZY_ENGINE_MODULES, "polars", "cleaning.engine.no_such_engine")
    with pytest.raises(cleaning.TableError, match="不可用") as caught:
        get_engine("polars")
    message = str(caught.value)
    assert "pandas" in message
    assert "no_such_engine" in message


def test_lazy_engine_module_must_register_its_name(monkeypatch: pytest.MonkeyPatch):
    """模块能 import 但没注册引擎时明确报错，不返回空实现。"""
    monkeypatch.setitem(engine_base._LAZY_ENGINE_MODULES, "fake", "cleaning.errors")
    with pytest.raises(cleaning.TableError, match="没有注册"):
        get_engine("fake")


def test_register_engine_requires_a_name():
    """没有 name 的引擎实现不能注册，避免选择时拿到无名对象。"""

    class Nameless:
        pass

    with pytest.raises(cleaning.TableError, match="name"):
        register_engine(Nameless())  # type: ignore[arg-type]


def test_engine_result_type_is_clean_report():
    """引擎边界直接复用 CleanReport，不再造一套报告类型。"""
    assert EngineResult is cleaning.CleanReport


def test_pandas_engine_exposes_the_contract_methods():
    """协议要求的 6 个方法都存在且可调用。"""
    engine = PandasEngine()
    for name in ("clean_report", "apply_transforms", "clean_table", "find_missing", "find_suspicious", "summarize"):
        assert callable(getattr(engine, name)), name
    assert engine.name == "pandas"


def test_pandas_engine_clean_table_matches_direct_transform():
    """包装层的行清洗 + 列变换与直接调用 transform.clean_table 逐值一致。"""
    frame = cleaning.make_sample_df()
    options = _sample_options()
    expected = transform.clean_table(frame, options, sample=3)
    actual = PANDAS_ENGINE.clean_table(frame, options, sample=3)
    assert isinstance(actual, cleaning.CleanReport)
    pd.testing.assert_frame_equal(actual.df, expected.df)
    pd.testing.assert_frame_equal(actual.removed, expected.removed)
    assert actual.steps == expected.steps
    assert actual.notes == expected.notes


def test_pandas_engine_clean_report_matches_direct_rules_with_chunking():
    """行清洗（含样例与分块）与 rules.clean_report 结果一致。"""
    frame = cleaning.make_sample_df()
    expected = rules.clean_report(
        frame,
        drop_duplicates=True,
        dedupe_on=["订单号"],
        drop_missing_in=["客户名称"],
        sample=2,
        chunk_rows=2,
    )
    actual = PANDAS_ENGINE.clean_report(
        frame,
        drop_duplicates=True,
        dedupe_on=["订单号"],
        drop_missing_in=["客户名称"],
        sample=2,
        chunk_rows=2,
    )
    pd.testing.assert_frame_equal(actual.df, expected.df)
    pd.testing.assert_frame_equal(actual.removed, expected.removed)
    assert actual.steps == expected.steps


def test_pandas_engine_inspectors_match_direct_functions():
    """空值、可疑格式、统计三个检查算子与现行函数一致。"""
    frame = cleaning.make_sample_df()
    pd.testing.assert_series_equal(PANDAS_ENGINE.find_missing(frame), rules.find_missing(frame))
    assert PANDAS_ENGINE.find_suspicious(frame) == rules.find_suspicious(frame)
    assert PANDAS_ENGINE.summarize(frame) == rules.summarize(frame)


def test_pandas_engine_apply_transforms_matches_direct_transform():
    """列变换包装与 transform.apply_transforms 的帧、说明完全一致。"""
    frame = cleaning.make_sample_df()
    options = _sample_options()
    expected_frame, expected_notes = transform.apply_transforms(frame, options.columns)
    actual_frame, actual_notes = PANDAS_ENGINE.apply_transforms(frame, options.columns)
    pd.testing.assert_frame_equal(actual_frame, expected_frame)
    assert actual_notes == expected_notes


def test_public_entry_signatures_have_no_engine_keyword_in_this_pr():
    """W5 才接 engine 关键字；本 PR 的公共入口签名必须保持原样。"""
    clean_table_parameters = inspect.signature(cleaning.clean_table).parameters
    assert list(clean_table_parameters) == ["df", "options", "sample", "progress", "deadline"]
    assert clean_table_parameters["sample"].kind is inspect.Parameter.KEYWORD_ONLY
    assert "engine" not in clean_table_parameters

    clean_bytes_parameters = inspect.signature(cleaning.clean_bytes).parameters
    assert "engine" not in clean_bytes_parameters
    assert "engine" not in inspect.signature(cleaning.clean_report).parameters
    assert "engine" not in inspect.signature(cleaning.run_batch).parameters


def test_public_api_does_not_export_engine_or_dsl_names():
    """cleaning.__all__ 不因引擎/DSL 增删；两个新模块只能作为内部模块导入。"""
    forbidden = {"engine", "pipeline", "get_engine", "Pipeline", "PipelineNode", "describe_pipeline", "load_pipeline"}
    assert forbidden.isdisjoint(cleaning.__all__)
    assert importlib.import_module("cleaning.engine") is not None


def test_engine_and_dsl_modules_do_not_import_polars():
    """W1/W4 的合同模块不 import Polars；默认安装没有该依赖也能用。"""
    for relative in ENGINE_MODULES:
        source = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert not any(name == "polars" or name.startswith("polars.") for name in imported), relative
