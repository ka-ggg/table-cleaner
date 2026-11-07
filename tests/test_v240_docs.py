"""v2.4.0 W5 文档一致性测试：文档摘要必须与代码注册表/DSL 合同逐项一致。

规划 5.9 / G-14 要求文档由测试校验、不手写第二份完整表。这里直接解析两份专题文档
中的 Markdown 表格，与 ``cleaning.engine.registry``、``known_differences`` 和
``cleaning.pipeline`` 的权威数据比对；代码更新而文档没跟上时测试失败。
"""

from __future__ import annotations

import re
from pathlib import Path

from cleaning.engine.known_differences import KNOWN_DIFFERENCES
from cleaning.engine.registry import (
    CAPABILITIES,
    KIND_EXPRESSION,
    KIND_INSPECTOR,
    KIND_NODE,
    node_capabilities,
    node_types,
)
from cleaning.pipeline import CURRENT_DSL_VERSION, NODE_STAGES

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENGINE_DOC = PROJECT_ROOT / "docs" / "ENGINE_ALIGNMENT.md"
DSL_DOC = PROJECT_ROOT / "docs" / "PIPELINE_DSL.md"

_KIND_LABELS = {KIND_NODE: "节点", KIND_INSPECTOR: "检查", KIND_EXPRESSION: "表达式"}


def _engine_doc() -> str:
    assert ENGINE_DOC.is_file(), "缺少 docs/ENGINE_ALIGNMENT.md"
    return ENGINE_DOC.read_text(encoding="utf-8")


def _dsl_doc() -> str:
    assert DSL_DOC.is_file(), "缺少 docs/PIPELINE_DSL.md"
    return DSL_DOC.read_text(encoding="utf-8")


def test_engine_doc_capability_table_matches_registry():
    """17 项能力的 id、类型、名称、Polars 状态与注册表逐项一致。"""
    rows = re.findall(
        r"^\| ([RIX]\d+) \| (节点|检查|表达式) \| ([^|]+?) \| ([a-z_]+) \|",
        _engine_doc(),
        re.MULTILINE,
    )
    documented = [(capability_id, label, title.strip(), status) for capability_id, label, title, status in rows]
    expected = [
        (capability.id, _KIND_LABELS[capability.kind], capability.title, capability.polars_status)
        for capability in CAPABILITIES
    ]
    assert documented == expected


def test_engine_doc_known_difference_table_matches_registry():
    """10 条已知差异的 id、能力、用例、决定、范围、目标版本与登记表一致。"""
    rows = re.findall(
        r"^\| (KD-\d{4}) \| (\S+) \| (\S+) \| (fallback|align) \| ([a-z_]+) \| (v\d+\.\d+(?:\.\d+)?) \|",
        _engine_doc(),
        re.MULTILINE,
    )
    expected = [
        (item.id, item.operator, item.case, item.decision, item.scope, item.target) for item in KNOWN_DIFFERENCES
    ]
    assert rows == expected


def test_engine_doc_summary_numbers_match_contract():
    """摘要里的 17/14/3/0、6/6、3/9、86.2% 与能力注册表/对齐口径一致。"""
    text = _engine_doc()
    aligned = [capability for capability in CAPABILITIES if capability.polars_status == "aligned"]
    partial = [capability for capability in CAPABILITIES if capability.polars_status == "partial_fallback"]
    full = [capability for capability in CAPABILITIES if capability.polars_status == "full_fallback"]
    fallback_nodes = [capability for capability in node_capabilities() if capability.polars_status != "aligned"]
    assert len(CAPABILITIES) == 17
    assert (len(aligned), len(partial), len(full)) == (14, 3, 0)
    assert len(fallback_nodes) == 3
    assert "17 项能力中 14 项 `aligned`、3 项 `partial_fallback`" in text
    assert re.search(r"0 项\s+`full_fallback`", text)
    assert "3/9（R5 / R6 / R8）" in text
    assert "50 / 58 = 86.2%" in text
    assert "核心结构算子 **6/6** 对齐" in text
    assert "整节点回落 **0/9**" in text
    assert "无「待定 / 未知」项" in text


def test_engine_doc_states_default_pandas_and_no_lazy_streaming():
    """文档必须写明默认 pandas、Polars 内部预览、未做 lazy/streaming/血缘/性能目标。"""
    text = _engine_doc()
    for phrase in (
        "默认引擎仍是 **pandas**",
        "Polars 是**内部预览**",
        "不做 lazy / streaming",
        "不做行级血缘",
        "不做性能目标",
    ):
        assert phrase in text, phrase


def test_dsl_doc_node_table_matches_pipeline_contract():
    """DSL 文档的 9 个节点类型与阶段、当前版本、引擎偏好与代码一致。"""
    text = _dsl_doc()
    section = text.split("## 3. 节点类型", 1)[1].split("## 4.", 1)[0]
    rows = re.findall(r"^\| (\d+) \| `([a-z_.]+)` \|", section, re.MULTILINE)
    expected = [(str(NODE_STAGES[node_type]), node_type) for node_type in node_types()]
    assert rows == expected
    assert f"当前固定为 `{CURRENT_DSL_VERSION}`" in text
    assert "`preference` 只允许 `pandas` / `polars`" in text
    assert "最多 1 个，且必须是最后一个阶段" in text
    assert "`cleaning/pipeline.py` 不作为公共 API 导出" in text


def test_dsl_doc_mentions_three_layer_validation_and_version_policy():
    """文档必须覆盖三层校验、严格整数版本与逐版本迁移，便于 v3.0 接手。"""
    text = _dsl_doc()
    for phrase in (
        "版本前置检查",
        "JSON Schema 结构校验",
        "语义校验",
        "任何格式变化",
        "迁移函数",
        "高于当前版本",
        "`jsonschema` 未安装",
    ):
        assert phrase in text, phrase
