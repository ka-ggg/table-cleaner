"""Pipeline DSL v1：加载、校验、版本迁移、编译为 ``CleanOptions`` 与计划描述。

本模块是 v2.4.0 的**内部预埋**：不执行清洗、不依赖 Streamlit，也不进入 CLI /
HTTP API / 界面的 import 链。``cleaning.__all__`` 不因此增删，v3.0 再决定是否
提升为公共 API。

校验分三层，任何一层失败都不执行半条流水线：

1. 版本前置检查：``dsl_version`` 缺失、非整数、小于 1、高于当前支持版本；
2. JSON Schema 结构校验：必填字段、类型、枚举、未知字段、节点参数结构；
3. 语义校验：节点 id 唯一、阶段顺序、同阶段合并规则。

错误统一抛中文 ``TableError``，并尽量带节点序号与 id。``jsonschema`` 只在真正
校验时惰性 import：默认 pandas 入口和模板读取不受影响；缺少该开发依赖时只在本
内部功能给中文提示。
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from importlib import resources
from typing import Any, Callable, Iterable, Mapping, cast

from .engine.registry import (
    STATUS_FULL_FALLBACK,
    capability_for_node_type,
    node_capabilities,
)
from .errors import TableError
from .templates import TemplateInfo, load_template, normalize_rules
from .transform import CleanOptions, ColumnOps

CURRENT_DSL_VERSION = 1
SUPPORTED_DSL_VERSIONS: tuple[int, ...] = (1,)
DEFAULT_ENGINE_PREFERENCE = "pandas"
ENGINE_PREFERENCES = ("pandas", "polars")

ROW_DROP_DUPLICATES = "row.drop_duplicates"
ROW_DEDUPE_BY = "row.dedupe_by"
ROW_DROP_MISSING = "row.drop_missing"
COLUMN_DROP = "column.drop"
COLUMN_TO_NUMBER = "column.to_number"
COLUMN_TO_DATE = "column.to_date"
COLUMN_RENAME = "column.rename"
COLUMN_COMPUTE = "column.compute"
COLUMN_ORDER = "column.order"

# 节点类型与阶段以注册表为唯一权威；这里只做展开，避免 DSL 与注册表两处维护。
NODE_STAGES: dict[str, int] = {
    capability.node_type: capability.stage
    for capability in node_capabilities()
    if capability.node_type is not None and capability.stage is not None
}
_SINGLE_NODE_STAGES = frozenset({10, 20, 30, 40, 90})
_ROW_NODE_TYPES = frozenset({ROW_DROP_DUPLICATES, ROW_DEDUPE_BY, ROW_DROP_MISSING})
_SCHEMA_PACKAGE = "cleaning.schema"
_SCHEMA_FILE = "pipeline-v1.json"

_TYPE_NAMES = {
    "object": "对象",
    "array": "数组",
    "string": "字符串",
    "integer": "整数",
    "number": "数字",
    "boolean": "布尔值",
    "null": "空值",
}

# 将来升版本时在这里逐版本登记迁移函数（输入/输出都是当前版本的文档字典）。
_MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}


@dataclass(frozen=True)
class PipelineNode:
    """DSL 节点：id 稳定唯一，type 对应注册表能力，params 只存该节点的参数。"""

    id: str
    type: str
    params: Mapping[str, Any]
    enabled: bool = True
    description: str | None = None


@dataclass(frozen=True)
class Pipeline:
    """一条可序列化的清洗流水线；v1 只承载现有 ``CleanOptions`` 能表达的内容。"""

    dsl_version: int
    nodes: tuple[PipelineNode, ...]
    name: str | None = None
    engine_preference: str = DEFAULT_ENGINE_PREFERENCE
    header_row: int = 1


@dataclass(frozen=True)
class PlanNode:
    """计划中的一个节点：记录输入/输出列、执行引擎与回落原因。"""

    id: str
    type: str
    enabled: bool
    stage: int
    input_columns: tuple[str, ...]
    output_columns: tuple[str, ...]
    engine: str
    fallback_reason: str | None
    lazy_eligible: bool = False


@dataclass(frozen=True)
class FallbackNode:
    """被能力表判定为必须回落 pandas 的节点（id、类型、原因）。"""

    id: str
    type: str
    reason: str


@dataclass(frozen=True)
class PipelinePlan:
    """只读执行计划描述；本版只产出数据，不接 UI，也不读取任何数据行。"""

    dsl_version: int
    name: str | None
    engine: str
    nodes: tuple[PlanNode, ...]
    effective_engine: str
    fallback_nodes: tuple[FallbackNode, ...]
    warnings: tuple[str, ...]
    input_columns: tuple[str, ...]
    output_columns: tuple[str, ...]


# ---------------------------------------------------------------- 加载与校验


def pipeline_schema() -> dict[str, Any]:
    """读取随包发布的 DSL v1 JSON Schema（``importlib.resources``，兼容 wheel）。"""
    try:
        payload = resources.files(_SCHEMA_PACKAGE).joinpath(_SCHEMA_FILE).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError):
        raise TableError(
            "流水线 DSL 的 schema 文件缺失（cleaning/schema/pipeline-v1.json）；请重新安装完整的 table-cleaner 包。"
        ) from None
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise TableError(
            f"流水线 DSL 的 schema 文件不是有效 JSON（第 {exc.lineno} 行附近）；请重新安装工具。"
        ) from None
    if not isinstance(document, dict):
        raise TableError("流水线 DSL 的 schema 文件应是一个 JSON 对象；请重新安装工具。")
    return cast(dict[str, Any], document)


def load_pipeline(data: bytes | str | Mapping[str, Any]) -> Pipeline:
    """从 JSON 字节/文本或已解析对象读入流水线；坏文件与坏字段给中文错误。"""
    if isinstance(data, Mapping):
        document: Mapping[str, Any] = data
    elif isinstance(data, (bytes, str)):
        document = _parse_document(data)
    else:
        raise TableError("流水线应是一个 JSON 对象（或 JSON 文本/字节）；请检查文件类型。")
    return validate_pipeline(document)


def validate_pipeline(document: Mapping[str, Any]) -> Pipeline:
    """按三层规则校验流水线并返回不可变 ``Pipeline``；不执行任何清洗。"""
    if not isinstance(document, Mapping):
        raise TableError('流水线应是一个 JSON 对象，例如 {"dsl_version": 1, "nodes": []}。')
    migrated = migrate_pipeline(document)
    _validate_schema_structure(migrated)
    _validate_semantics(migrated)
    return _build_pipeline(migrated)


def migrate_pipeline(document: Mapping[str, Any]) -> dict[str, Any]:
    """把旧版本 DSL 逐版本迁移到 ``CURRENT_DSL_VERSION``；v1 当前是恒等迁移。

    版本策略（DC-3）：任何格式变化都升整数版本并提供迁移函数；旧读取器拒绝更高
    版本，避免未知字段被静默忽略。这里的循环为 v2 及以后保留了逐版本入口。
    """
    migrated = copy.deepcopy(dict(document))
    version = _check_version(migrated)
    while version < CURRENT_DSL_VERSION:
        step = _MIGRATIONS.get(version)
        if step is None:
            raise TableError(f"DSL 版本 {version} 没有可用的迁移函数，无法读取；请改用旧版工具或升级包。")
        migrated = step(migrated)
        version += 1
        migrated["dsl_version"] = version
    return migrated


def _parse_document(data: bytes | str) -> dict[str, Any]:
    if isinstance(data, bytes):
        if not data:
            raise TableError("流水线文件是空的（0 字节），请重新选择或粘贴 DSL 内容。")
        text = None
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise TableError("流水线文件编码无法识别，请使用 UTF-8 编码的 .json 文件。")
    else:
        text = data
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TableError(
            f"流水线文件不是有效的 JSON（第 {exc.lineno} 行第 {exc.colno} 列附近）；"
            "请检查逗号、引号和括号，或重新生成 DSL。"
        ) from None
    if not isinstance(document, dict):
        raise TableError('流水线文件里应是一个 JSON 对象，例如 {"dsl_version": 1, "nodes": []}。')
    return cast(dict[str, Any], document)


def _check_version(document: Mapping[str, Any]) -> int:
    if "dsl_version" not in document:
        raise TableError(
            f"流水线缺少必填字段「dsl_version」（当前支持整数版本 {CURRENT_DSL_VERSION}）；"
            "旧模板请先用 template_to_pipeline 迁移。"
        )
    raw = document["dsl_version"]
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise TableError(
            "流水线字段「dsl_version」必须是整数（当前支持 "
            f"{CURRENT_DSL_VERSION}）；收到 {raw!r}，整数不是字符串或小数。"
        )
    if raw < 1:
        raise TableError(f"流水线字段「dsl_version」必须大于等于 1；收到 {raw}。")
    if raw > CURRENT_DSL_VERSION:
        raise TableError(
            f"流水线 DSL 版本 {raw} 高于当前工具支持的版本 {CURRENT_DSL_VERSION}；"
            "请升级 table-cleaner 后再打开，或改用旧格式并重新生成 DSL。"
        )
    return raw


def _validate_schema_structure(document: Mapping[str, Any]) -> None:
    try:
        import jsonschema
    except ImportError:
        raise TableError(
            "当前环境缺少 jsonschema，无法按 JSON Schema 校验流水线 DSL"
            "（v2.4.0 的开发依赖）。请安装 jsonschema 后再用；默认 pandas 清洗不受影响。"
        ) from None
    validator = jsonschema.Draft202012Validator(pipeline_schema())
    errors = sorted(
        validator.iter_errors(document),
        key=lambda error: (len(list(error.absolute_path)), [str(part) for part in error.absolute_path]),
    )
    if errors:
        raise TableError(_format_schema_error(errors[0], document))


def _format_schema_error(error: Any, document: Mapping[str, Any]) -> str:
    path = list(error.absolute_path)
    label, rest = _node_label(path, document)
    where = label or "流水线"
    field = _field_path(rest)
    keyword = str(error.validator)

    if keyword == "required":
        target = _missing_field(error, field)
        return f"流水线 DSL 校验失败：{where}缺少必填字段「{target}」。"
    if keyword == "additionalProperties":
        target = _extra_field(error, field)
        return (
            f"流水线 DSL 校验失败：{where}有不认识的字段「{target}」；"
            "v1 采用严格版本策略，未知字段会被拒绝，拼写错误不会被静默忽略。"
        )

    subject = f"{where}字段「{field}」" if field else where
    if keyword == "type":
        expected = _expected_type_names(error.validator_value)
        return f"流水线 DSL 校验失败：{subject}应为{expected}，收到{_json_type_name(error.instance)}。"
    if keyword == "const":
        return (
            f"流水线 DSL 校验失败：{subject}必须是 {_value_text(error.validator_value)}，"
            f"收到 {_value_text(error.instance)}。"
        )
    if keyword == "enum":
        allowed = "、".join(_value_text(value) for value in error.validator_value)
        return f"流水线 DSL 校验失败：{subject}只能是 {allowed} 之一，收到 {_value_text(error.instance)}。"
    if keyword == "pattern":
        return f"流水线 DSL 校验失败：{subject}格式不符合要求（规则：{error.validator_value}），请不要包含空白字符。"
    if keyword == "minItems":
        return f"流水线 DSL 校验失败：{subject}至少要有 {error.validator_value} 项。"
    if keyword == "maxItems":
        return f"流水线 DSL 校验失败：{subject}最多只能有 {error.validator_value} 项。"
    if keyword == "uniqueItems":
        return f"流水线 DSL 校验失败：{subject}不能有重复项。"
    if keyword == "minLength":
        return f"流水线 DSL 校验失败：{subject}不能为空。"
    if keyword == "maxLength":
        return f"流水线 DSL 校验失败：{subject}最多 {error.validator_value} 个字符。"
    if keyword == "minimum":
        return f"流水线 DSL 校验失败：{subject}不能小于 {error.validator_value}。"
    if keyword == "maxProperties":
        return f"流水线 DSL 校验失败：{subject}不允许包含额外参数（当前节点只接受空参数对象）。"
    return f"流水线 DSL 校验失败：{subject}不符合校验规则「{keyword}」。"


def _node_label(path: list[Any], document: Mapping[str, Any]) -> tuple[str, list[Any]]:
    if len(path) >= 2 and path[0] == "nodes" and isinstance(path[1], int):
        index = path[1]
        nodes = document.get("nodes")
        node = nodes[index] if isinstance(nodes, list) and 0 <= index < len(nodes) else None
        node_id = node.get("id") if isinstance(node, Mapping) else None
        label = f"流水线第 {index + 1} 个节点"
        if isinstance(node_id, str) and node_id:
            label += f"（id={node_id}）"
        return label, path[2:]
    return "", path


def _field_path(path: list[Any]) -> str:
    text = ""
    for part in path:
        if isinstance(part, int):
            text += f"[{part}]"
        elif text:
            text += f".{part}"
        else:
            text = str(part)
    return text


def _missing_field(error: Any, field: str) -> str:
    instance = error.instance
    expected = error.validator_value
    missing = [name for name in expected if not isinstance(instance, Mapping) or name not in instance]
    name = str(missing[0]) if missing else "?"
    return f"{field}.{name}" if field else name


def _extra_field(error: Any, field: str) -> str:
    instance = error.instance
    schema = error.schema if isinstance(error.schema, Mapping) else {}
    known = set(schema.get("properties", {}))
    extra = [str(name) for name in instance if isinstance(instance, Mapping) and name not in known]
    name = extra[0] if extra else "?"
    return f"{field}.{name}" if field else name


def _expected_type_names(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(_TYPE_NAMES.get(str(item), str(item)) for item in value)
    return _TYPE_NAMES.get(str(value), str(value))


def _json_type_name(value: Any) -> str:
    if value is None:
        return "空值"
    if isinstance(value, bool):
        return "布尔值"
    if isinstance(value, int):
        return "整数"
    if isinstance(value, float):
        return "数字"
    if isinstance(value, str):
        return "字符串"
    if isinstance(value, Mapping):
        return "对象"
    if isinstance(value, (list, tuple)):
        return "数组"
    return type(value).__name__


def _value_text(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= 80 else text[:77] + "…"


def _validate_semantics(document: Mapping[str, Any]) -> None:
    nodes = document.get("nodes")
    if not isinstance(nodes, list):
        return  # 结构校验已经报过；这里只做能安全执行的语义检查
    seen_ids: dict[str, int] = {}
    enabled: list[tuple[int, Mapping[str, Any]]] = []
    for index, node in enumerate(nodes):
        if not isinstance(node, Mapping):
            continue
        node_id = str(node.get("id", ""))
        if node_id in seen_ids:
            first = seen_ids[node_id] + 1
            raise TableError(
                f"流水线节点 id「{node_id}」重复（第 {first} 个与第 {index + 1} 个）；"
                "每个节点必须有唯一、稳定的 id，将来行级血缘会引用它。"
            )
        seen_ids[node_id] = index
        if bool(node.get("enabled", True)):
            enabled.append((index, node))
    _validate_stage_order(enabled)


def _validate_stage_order(enabled: list[tuple[int, Mapping[str, Any]]]) -> None:
    previous_index: int | None = None
    previous_stage: int | None = None
    previous_id = ""
    single_seen: dict[int, tuple[int, Mapping[str, Any]]] = {}
    for index, node in enabled:
        node_type = str(node.get("type", ""))
        stage = NODE_STAGES.get(node_type)
        if stage is None:
            continue  # schema 会拒绝未知类型
        if previous_stage is not None and stage < previous_stage and previous_index is not None:
            raise TableError(
                f"流水线节点顺序不对：第 {index + 1} 个节点（id={node.get('id')}，{node_type}，阶段 {stage}）"
                f"排在第 {previous_index + 1} 个节点（id={previous_id}，阶段 {previous_stage}）后面；"
                f"阶段必须按 {_stage_sequence_text()} 递增。"
            )
        if stage in _SINGLE_NODE_STAGES:
            if stage in single_seen:
                first_index, first_node = single_seen[stage]
                raise TableError(
                    f"同一阶段最多只能有 1 个节点：第 {first_index + 1} 个节点（id={first_node.get('id')}）"
                    f"与第 {index + 1} 个节点（id={node.get('id')}）都是 {node_type}（阶段 {stage}）；"
                    "请合并或删除重复节点。"
                )
            single_seen[stage] = (index, node)
        previous_index = index
        previous_stage = stage
        previous_id = str(node.get("id", ""))


def _stage_sequence_text() -> str:
    return "行去重 → 按列去重 → 删空值行 → 删列 → 转数字 → 转日期 → 改名 → 计算列 → 列顺序"


def _build_pipeline(document: Mapping[str, Any]) -> Pipeline:
    engine = document.get("engine")
    preference = DEFAULT_ENGINE_PREFERENCE
    if isinstance(engine, Mapping):
        preference = str(engine.get("preference", DEFAULT_ENGINE_PREFERENCE))
    source = document.get("source")
    header_row = 1
    if isinstance(source, Mapping):
        header_row = int(source.get("header_row", 1))
    name = document.get("name")
    nodes: list[PipelineNode] = []
    for item in document.get("nodes", []):
        if not isinstance(item, Mapping):
            continue
        params = item.get("params")
        description = item.get("description")
        nodes.append(
            PipelineNode(
                id=str(item["id"]),
                type=str(item["type"]),
                params=copy.deepcopy(dict(params)) if isinstance(params, Mapping) else {},
                enabled=bool(item.get("enabled", True)),
                description=description if isinstance(description, str) else None,
            )
        )
    return Pipeline(
        dsl_version=int(document.get("dsl_version", CURRENT_DSL_VERSION)),
        nodes=tuple(nodes),
        name=name if isinstance(name, str) and name else None,
        engine_preference=preference,
        header_row=header_row,
    )


# ---------------------------------------------------------------- 序列化与迁移


def pipeline_to_document(pipeline: Pipeline) -> dict[str, Any]:
    """把 ``Pipeline`` 规整成可序列化文档；同一对象的两次输出逐字节稳定。"""
    nodes: list[dict[str, Any]] = []
    for node in pipeline.nodes:
        item: dict[str, Any] = {"id": node.id, "type": node.type, "enabled": node.enabled}
        if node.description:
            item["description"] = node.description
        item["params"] = copy.deepcopy(dict(node.params))
        nodes.append(item)
    document: dict[str, Any] = {"dsl_version": pipeline.dsl_version}
    if pipeline.name:
        document["name"] = pipeline.name
    document["engine"] = {"preference": pipeline.engine_preference}
    document["source"] = {"header_row": pipeline.header_row}
    document["nodes"] = nodes
    return document


def dump_pipeline(pipeline: Pipeline) -> bytes:
    """导出 UTF-8、中文不转义的流水线 JSON 字节，便于人工查看与版本管理。"""
    return (json.dumps(pipeline_to_document(pipeline), ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def template_to_pipeline(template: bytes | str | TemplateInfo | Mapping[str, Any]) -> Pipeline:
    """把旧清洗模板（含无 ``template_version`` 的旧模板）迁移为 DSL v1。

    只承诺规则与清洗结果一致；模板元信息（保存时间）不进入 DSL。重命名、计算列、
    列顺序保持模板中的先后关系，生成稳定可读的节点 id。
    """

    rules, name = _template_rules(template)
    nodes: list[PipelineNode] = []
    if rules["删除完全重复的行"]:
        nodes.append(_migration_node("row-drop-duplicates", ROW_DROP_DUPLICATES, {}))
    if rules["按列去重"]:
        nodes.append(
            _migration_node("row-dedupe-by", ROW_DEDUPE_BY, {"columns": _migration_columns(rules["按列去重"])})
        )
    if rules["删除空值行"]:
        nodes.append(
            _migration_node("row-drop-missing", ROW_DROP_MISSING, {"columns": _migration_columns(rules["删除空值行"])})
        )
    if rules["删除列"]:
        nodes.append(_migration_node("column-drop", COLUMN_DROP, {"columns": _migration_columns(rules["删除列"])}))
    if rules["转数字"]:
        nodes.append(
            _migration_node("column-to-number", COLUMN_TO_NUMBER, {"columns": _migration_columns(rules["转数字"])})
        )
    if rules["转日期"]:
        nodes.append(
            _migration_node("column-to-date", COLUMN_TO_DATE, {"columns": _migration_columns(rules["转日期"])})
        )
    renames = [{"from": old, "to": new} for old, new in rules["列重命名"]]
    if renames:
        nodes.append(_migration_node("column-rename", COLUMN_RENAME, {"renames": renames}))
    for position, (column_name, expression) in enumerate(rules["计算列"], 1):
        nodes.append(
            _migration_node(
                f"column-compute-{position}",
                COLUMN_COMPUTE,
                {"name": column_name, "expression": expression},
            )
        )
    if rules["列顺序"]:
        nodes.append(_migration_node("column-order", COLUMN_ORDER, {"columns": _migration_columns(rules["列顺序"])}))
    return Pipeline(
        dsl_version=CURRENT_DSL_VERSION,
        nodes=tuple(nodes),
        name=name,
        engine_preference=DEFAULT_ENGINE_PREFERENCE,
        header_row=int(rules["表头行"]),
    )


def _template_rules(
    template: bytes | str | TemplateInfo | Mapping[str, Any],
) -> tuple[dict[str, Any], str | None]:
    if isinstance(template, (bytes, str)):
        info = load_template(template)
        return info.rules, info.name
    if isinstance(template, TemplateInfo):
        return template.rules, template.name
    if isinstance(template, Mapping):
        if "规则" in template:
            raw = template["规则"]
            if not isinstance(raw, Mapping):
                raise TableError("模板里的「规则」应为一组设置，请用本工具导出的模板文件。")
            name = template.get("名称")
            return normalize_rules(dict(raw)), name if isinstance(name, str) and name else None
        return normalize_rules(dict(template)), None
    raise TableError("模板应是一个 JSON 对象、JSON 文本或字节；请检查文件类型。")


def _migration_node(node_id: str, node_type: str, params: Mapping[str, Any]) -> PipelineNode:
    return PipelineNode(id=node_id, type=node_type, params=dict(params), enabled=True)


def _migration_columns(values: Iterable[Any]) -> list[str]:
    """迁移时按首次出现去重列名：模板里重复的列名语义相同，DSL schema 要求唯一。"""
    columns: list[str] = []
    for value in values:
        name = str(value)
        if name not in columns:
            columns.append(name)
    return columns


# ---------------------------------------------------------------- 编译


def pipeline_to_options(pipeline: Pipeline) -> CleanOptions:
    """把 DSL 编译成现有唯一规则载体 ``CleanOptions``；不执行清洗。"""
    drop_duplicates = False
    dedupe_on: tuple[str, ...] = ()
    drop_missing_in: tuple[str, ...] = ()
    drop: list[str] = []
    to_number: list[str] = []
    to_date: list[str] = []
    rename: list[tuple[str, str]] = []
    computed: list[tuple[str, str]] = []
    order: tuple[str, ...] = ()

    for node in pipeline.nodes:
        if not node.enabled:
            continue
        params = node.params
        if node.type == ROW_DROP_DUPLICATES:
            drop_duplicates = True
        elif node.type == ROW_DEDUPE_BY:
            dedupe_on = _name_tuple(params.get("columns"))
        elif node.type == ROW_DROP_MISSING:
            drop_missing_in = _name_tuple(params.get("columns"))
        elif node.type == COLUMN_DROP:
            _extend_unique(drop, params.get("columns"))
        elif node.type == COLUMN_TO_NUMBER:
            _extend_unique(to_number, params.get("columns"))
        elif node.type == COLUMN_TO_DATE:
            _extend_unique(to_date, params.get("columns"))
        elif node.type == COLUMN_RENAME:
            for pair in params.get("renames", ()):
                if isinstance(pair, Mapping):
                    rename.append((str(pair["from"]), str(pair["to"])))
        elif node.type == COLUMN_COMPUTE:
            computed.append((str(params["name"]), str(params["expression"])))
        elif node.type == COLUMN_ORDER:
            order = _name_tuple(params.get("columns"))

    return CleanOptions(
        header_row=pipeline.header_row,
        drop_duplicates=drop_duplicates,
        dedupe_on=dedupe_on,
        drop_missing_in=drop_missing_in,
        columns=ColumnOps(
            rename=tuple(rename),
            drop=tuple(drop),
            order=order,
            to_number=tuple(to_number),
            to_date=tuple(to_date),
            computed=tuple(computed),
        ),
    )


def _name_tuple(values: Any) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(str(value) for value in values)


def _extend_unique(target: list[str], values: Any) -> None:
    for value in _name_tuple(values):
        if value not in target:
            target.append(value)


# ---------------------------------------------------------------- 计划描述


def describe_pipeline(pipeline: Pipeline, columns: Iterable[str]) -> PipelinePlan:
    """按 DSL 与当前列名生成只读执行计划；不读取数据行、不执行清洗。

    ``effective_engine`` 是编译期结论：Polars 偏好下，能力表判定整节点回落的节点
    会让整条流水线走 pandas；执行前预检仍可能因数据边界改走 pandas。本版 Polars
    引擎尚未接入（W2），这里按注册表目标状态描述，实际入口在 W5 接线。
    """
    current = tuple(str(column) for column in columns)
    initial = current
    warnings: list[str] = []
    plan_nodes: list[PlanNode] = []
    fallbacks: list[FallbackNode] = []
    requested = (
        pipeline.engine_preference if pipeline.engine_preference in ENGINE_PREFERENCES else DEFAULT_ENGINE_PREFERENCE
    )

    # 第一遍只判能力表：任一启用节点必须整节点回落时，整条流水线改走 pandas。
    fallback_reasons: dict[str, str] = {}
    if requested != "pandas":
        for node in pipeline.nodes:
            if not node.enabled:
                continue
            capability = capability_for_node_type(node.type)
            if capability.polars_status == STATUS_FULL_FALLBACK:
                fallback_reasons[node.id] = capability.polars_reason
                fallbacks.append(FallbackNode(node.id, node.type, capability.polars_reason))
    effective = "pandas" if requested == "pandas" or fallbacks else requested

    for node in pipeline.nodes:
        capability = capability_for_node_type(node.type)
        before = current
        if node.enabled:
            current = _apply_plan_node(node, current, warnings)
        reason = fallback_reasons.get(node.id)
        plan_nodes.append(
            PlanNode(
                id=node.id,
                type=node.type,
                enabled=node.enabled,
                stage=capability.stage or 0,
                input_columns=before,
                output_columns=current,
                engine=effective,
                fallback_reason=reason if node.enabled else None,
                lazy_eligible=False,
            )
        )

    return PipelinePlan(
        dsl_version=pipeline.dsl_version,
        name=pipeline.name,
        engine=requested,
        nodes=tuple(plan_nodes),
        effective_engine=effective,
        fallback_nodes=tuple(fallbacks),
        warnings=tuple(warnings),
        input_columns=initial,
        output_columns=current,
    )


def _apply_plan_node(
    node: PipelineNode,
    columns: tuple[str, ...],
    warnings: list[str],
) -> tuple[str, ...]:
    params = node.params
    if node.type in _ROW_NODE_TYPES:
        return columns
    if node.type == COLUMN_DROP:
        missing = [name for name in _name_tuple(params.get("columns")) if name not in columns]
        if missing:
            _warn(warnings, f"要删除的列不存在，已忽略：{'、'.join(missing)}。")
        dropped = set(_name_tuple(params.get("columns")))
        return tuple(column for column in columns if column not in dropped)
    if node.type in (COLUMN_TO_NUMBER, COLUMN_TO_DATE):
        label = "转数字" if node.type == COLUMN_TO_NUMBER else "转日期"
        missing = [name for name in _name_tuple(params.get("columns")) if name not in columns]
        if missing:
            _warn(warnings, f"要{label}的列不存在，已忽略：{'、'.join(missing)}。")
        return columns
    if node.type == COLUMN_RENAME:
        return _apply_plan_rename(params, columns, warnings)
    if node.type == COLUMN_COMPUTE:
        name = str(params.get("name", ""))
        if name in columns:
            _warn(warnings, f"计算列「{name}」和已有列重名，已跳过；请换个列名。")
            return columns
        return (*columns, name)
    if node.type == COLUMN_ORDER:
        wanted = [name for name in _name_tuple(params.get("columns")) if name in columns]
        missing = [name for name in _name_tuple(params.get("columns")) if name not in columns]
        if missing:
            _warn(warnings, f"要调整顺序的列不存在，已忽略：{'、'.join(missing)}。")
        if not wanted:
            return columns
        return tuple(wanted) + tuple(column for column in columns if column not in wanted)
    return columns


def _apply_plan_rename(
    params: Mapping[str, Any],
    columns: tuple[str, ...],
    warnings: list[str],
) -> tuple[str, ...]:
    mapping: dict[str, str] = {}
    for pair in params.get("renames", ()):
        if not isinstance(pair, Mapping):
            continue
        old, new = str(pair["from"]), str(pair["to"])
        if old not in columns:
            _warn(warnings, f"要重命名的列「{old}」不存在，已忽略。")
            continue
        if new in columns and new != old:
            _warn(warnings, f"列名「{new}」已经存在，没有把「{old}」改成它。")
            continue
        mapping[old] = new
    return tuple(mapping.get(column, column) for column in columns)


def _warn(warnings: list[str], message: str) -> None:
    if message not in warnings:
        warnings.append(message)
