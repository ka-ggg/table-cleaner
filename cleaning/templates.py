"""清洗规则模板：存成 JSON，浏览器里下载/上传，不依赖服务器和数据库。

模板里只有「规则」，没有数据。字段名都是中文，用户用记事本打开也能改：
``规则`` 下可选 ``表头行``、``删除完全重复的行``、``按列去重``、``删除空值行``、
``列重命名``、``删除列``、``列顺序``、``转数字``、``转日期``、``计算列``。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable, NamedTuple

from .errors import TableError

TEMPLATE_TOOL = "表格清洗小工具"
TEMPLATE_VERSION = 2  # 旧「版本」字段：v2.x 的模板包版本，保持不动
TEMPLATE_FORMAT_VERSION = 1  # 新 template_version 字段：模板格式版本，只在格式变化时递增
LEGACY_TEMPLATE_VERSION = 0  # 没有 template_version 字段的旧模板
_MAX_TEMPLATE_BYTES = 1024 * 1024  # 1 MB：规则模板不该这么大，多半是选错了文件

_LIST_KEYS = ("按列去重", "删除空值行", "删除列", "列顺序", "转数字", "转日期")


def _as_name_list(value: Any, key: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TableError(f'模板里的「{key}」应为列名列表，例如 ["订单号"]；请检查模板文件。')
    return [item for item in value if item.strip()]


def _as_pairs(value: Any, key: str, example: str) -> list[tuple[str, str]]:
    """接受 {"旧": "新"} 或 [["旧", "新"], …] 两种写法。"""
    if value is None:
        return []
    pairs: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for old, new in value.items():
            if not isinstance(old, str) or not isinstance(new, str):
                raise TableError(f"模板里的「{key}」应为「旧=新」的文字，{example}。")
            pairs.append((old, new))
        return pairs
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, list) or len(item) != 2 or not all(isinstance(part, str) for part in item):
                raise TableError(f"模板里的「{key}」应为「旧=新」的文字，{example}。")
            pairs.append((item[0], item[1]))
        return pairs
    raise TableError(f"模板里的「{key}」应为「旧=新」的文字，{example}。")


def _as_computed(value: Any) -> list[tuple[str, str]]:
    if value is None:
        return []
    pairs: list[tuple[str, str]] = []
    if not isinstance(value, list):
        raise TableError('模板里的「计算列」应为列表，例如 [{"列名": "总额", "表达式": "单价*数量"}]。')
    for item in value:
        if isinstance(item, dict):
            name = item.get("列名") or item.get("name")
            expr = item.get("表达式") or item.get("expr")
        elif isinstance(item, list) and len(item) == 2:
            name, expr = item
        else:
            name = expr = None
        if not isinstance(name, str) or not isinstance(expr, str) or not name.strip():
            raise TableError(
                '模板里的「计算列」每一项都要有「列名」和「表达式」，例如 {"列名": "总额", "表达式": "单价*数量"}。'
            )
        pairs.append((name.strip(), expr.strip()))
    return pairs


def _as_header_row(value: Any) -> int:
    if value is None:
        return 1
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TableError(f"模板里的「表头行」应为大于 0 的整数，收到 {value!r}；请检查模板文件。")
    number = int(value)
    if number < 1:
        raise TableError("模板里的「表头行」应为大于 0 的整数（第 1 行起算）。")
    return number


def _as_bool(value: Any, key: str) -> bool:
    if value is None:
        return False
    if not isinstance(value, bool):
        raise TableError(f"模板里的「{key}」应为 true 或 false，收到 {value!r}。")
    return value


def normalize_rules(rules: Any) -> dict[str, Any]:
    """校验并规整规则字典；不认识的字段忽略（旧版模板/手写模板都能用）。"""
    if not isinstance(rules, dict):
        raise TableError("模板里的「规则」应为一组设置，请用本工具导出的模板文件。")
    rename = _as_pairs(rules.get("列重命名"), "列重命名", '例如 {"金额": "总金额"}')
    return {
        "表头行": _as_header_row(rules.get("表头行")),
        "删除完全重复的行": _as_bool(rules.get("删除完全重复的行"), "删除完全重复的行"),
        "按列去重": _as_name_list(rules.get("按列去重"), "按列去重"),
        "删除空值行": _as_name_list(rules.get("删除空值行"), "删除空值行"),
        "列重命名": [list(pair) for pair in rename],
        "删除列": _as_name_list(rules.get("删除列"), "删除列"),
        "列顺序": _as_name_list(rules.get("列顺序"), "列顺序"),
        "转数字": _as_name_list(rules.get("转数字"), "转数字"),
        "转日期": _as_name_list(rules.get("转日期"), "转日期"),
        "计算列": [list(pair) for pair in _as_computed(rules.get("计算列"))],
    }


class TemplateInfo(NamedTuple):
    """模板文件的元信息 + 规整后的规则；CLI/API 用它显示版本与字段。"""

    template_version: int
    is_legacy: bool
    tool: str | None
    name: str | None
    saved_at: str | None
    rules: dict[str, object]
    document: dict[str, object]


def _optional_text(document: dict[str, object], key: str) -> str | None:
    """元信息字段只在是字符串时采用；手写模板里写错类型时不影响规则读取。"""
    value = document.get(key)
    return value if isinstance(value, str) else None


def _template_version(document: dict[str, object]) -> tuple[int, bool]:
    """识别模板格式版本；缺失按旧模板 0，未知更高版本给中文错误而不是崩溃。"""
    if "template_version" not in document:
        return LEGACY_TEMPLATE_VERSION, True
    raw = document["template_version"]
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise TableError(
            "模板里的「template_version」应为整数（当前支持 1）；"
            f"收到 {raw!r}，请用本工具导出的模板，或删掉这个字段按旧模板使用。"
        )
    if raw < LEGACY_TEMPLATE_VERSION:
        raise TableError(f"模板里的「template_version」不能是负数（收到 {raw}）；请检查模板文件。")
    if raw > TEMPLATE_FORMAT_VERSION:
        raise TableError(
            f"模板格式版本 {raw} 高于当前工具支持的版本 {TEMPLATE_FORMAT_VERSION}；"
            "这个模板可能来自更新版本，请升级 table-cleaner 后再打开，或改用旧模板。"
        )
    return raw, raw == LEGACY_TEMPLATE_VERSION


def _parse_document(data: bytes | str) -> dict[str, Any]:
    """把模板字节/文本解析成 JSON 对象；坏文件给可操作的中文提示。"""
    if isinstance(data, bytes):
        if not data:
            raise TableError("模板文件是空的（0 字节），请重新选择。")
        if len(data) > _MAX_TEMPLATE_BYTES:
            raise TableError("模板文件超过 1 MB，多半是选错了文件；模板里只存规则，正常只有几 KB。")
        text = None
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise TableError("模板文件编码无法识别，请用本工具导出的模板（UTF-8 编码的 .json）。")
    else:
        text = data
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TableError(
            f"模板文件不是有效的 JSON（第 {exc.lineno} 行第 {exc.colno} 列附近）；"
            "请重新下载模板，或用文本编辑器检查逗号和引号。"
        ) from None
    if not isinstance(document, dict):
        raise TableError("模板文件里应是一个 JSON 对象，请用本工具导出的模板。")
    return document


def build_template(rules: dict[str, Any], name: str = "", now: datetime | None = None) -> dict[str, Any]:
    """生成可下载的模板对象；rules 不合法时直接抛中文错误（不让用户存下坏模板）。"""
    normalized = normalize_rules(rules)
    return {
        "工具": TEMPLATE_TOOL,
        "版本": TEMPLATE_VERSION,
        "template_version": TEMPLATE_FORMAT_VERSION,
        "名称": (name or "我的清洗规则").strip()[:40],
        "保存时间": (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
        "规则": normalized,
    }


def dumps(template: dict[str, Any]) -> bytes:
    """导出模板 JSON 字节（UTF-8，中文不转义，方便用记事本查看和改）。"""
    return (json.dumps(template, ensure_ascii=False, indent=2, sort_keys=False) + "\n").encode("utf-8")


def load_template(data: bytes | str) -> TemplateInfo:
    """读取模板并返回元信息 + 规则；兼容无 ``template_version`` 的旧模板。

    版本规则：字段缺失 = 0（旧模板，正常读入）；当前支持 1；
    更高的整数或非整数类型一律抛中文 ``TableError``，不静默按低版本解析。
    """
    document = _parse_document(data)
    version, is_legacy = _template_version(document)
    rules = document.get("规则", document)
    normalized = normalize_rules(rules)
    return TemplateInfo(
        template_version=version,
        is_legacy=is_legacy,
        tool=_optional_text(document, "工具"),
        name=_optional_text(document, "名称"),
        saved_at=_optional_text(document, "保存时间"),
        rules=normalized,
        document=document,
    )


def loads(data: bytes | str) -> dict[str, Any]:
    """读模板文件，返回规整后的规则字典；坏文件给可操作的中文提示。"""
    return load_template(data).rules


def filter_for_columns(rules: dict[str, Any], columns: Iterable[str]) -> tuple[dict[str, Any], list[str]]:
    """把模板里当前文件没有的列剔掉，返回（可用规则, 中文说明）。

    模板是给「同类表格」复用的，列名可能略有出入；直接套用会静默忽略不存在的列，
    这里先说清楚忽略了什么，用户才知道为什么某一步没生效。
    注意「列顺序」是最后一步、用的是改完名/算出来的**新列名**，所以重命名后的目标列名
    和计算列名也算「当前文件有的列」，否则模板里“把总金额排最前面”会莫名失效。
    """
    known = {str(column) for column in columns}
    filtered = normalize_rules(rules)
    ignored: list[str] = []
    rename: list[list[str]] = []
    for old, new in filtered["列重命名"]:
        if old in known:
            rename.append([old, new])
        elif old not in ignored:
            ignored.append(old)
    filtered["列重命名"] = rename
    # 列顺序在最后一步执行：改名后的列名、计算列名那时已经存在
    future = {new for _, new in rename} | {name for name, _ in filtered["计算列"]}

    for key in _LIST_KEYS:
        allowed = known | (future if key == "列顺序" else set())
        kept: list[str] = []
        for name in filtered[key]:
            if name in allowed:
                if name not in kept:  # 多选控件不接受重复值
                    kept.append(name)
            elif name not in ignored:
                ignored.append(name)
        filtered[key] = kept

    # 计算列保留原样：表达式里引用的列不存在时，执行阶段会给出具体提示，
    # 这里提前过滤反而会让「先计算、再重命名」这类模板莫名消失。
    notes: list[str] = []
    if ignored:
        preview = "、".join(ignored[:6]) + ("等" if len(ignored) > 6 else "")
        notes.append(f"模板里有当前文件没有的列，已忽略：{preview}。")
    return filtered, notes


def rules_to_rename_text(rules: dict[str, Any]) -> str:
    """模板里的重命名 → 界面文本框内容（每行「旧=新」）。"""
    return "\n".join(f"{old}={new}" for old, new in normalize_rules(rules)["列重命名"])


def rules_to_computed_text(rules: dict[str, Any]) -> str:
    """模板里的计算列 → 界面文本框内容（每行「新列名=表达式」）。"""
    return "\n".join(f"{name}={expr}" for name, expr in normalize_rules(rules)["计算列"])


def summarize_rules(rules: dict[str, Any]) -> str:
    """一行中文摘要，用来在模板列表里显示这套规则做了什么。"""
    normalized = normalize_rules(rules)
    parts: list[str] = []
    if normalized["删除完全重复的行"]:
        parts.append("全行去重")
    if normalized["按列去重"]:
        parts.append(f"按列去重（{'、'.join(normalized['按列去重'])}）")
    if normalized["删除空值行"]:
        parts.append(f"删空值行（{'、'.join(normalized['删除空值行'])}）")
    if normalized["列重命名"]:
        parts.append(f"改名 {len(normalized['列重命名'])} 列")
    if normalized["删除列"]:
        parts.append(f"删 {len(normalized['删除列'])} 列")
    if normalized["转数字"]:
        parts.append(f"转数字 {len(normalized['转数字'])} 列")
    if normalized["转日期"]:
        parts.append(f"转日期 {len(normalized['转日期'])} 列")
    if normalized["计算列"]:
        parts.append(f"计算列 {len(normalized['计算列'])} 个")
    if normalized["表头行"] != 1:
        parts.append(f"表头在第 {normalized['表头行']} 行")
    return "；".join(parts) if parts else "没有任何规则（相当于原样导出）"
