"""Polars 引擎：把 17 项能力映射为 Polars 执行，并在执行前完成回落判定。

设计边界（与 ``V240_PLAN.md`` 5.2 / 5.3 一致）：

- 协议边界仍是 pandas 帧：本模块把输入帧转成 Polars、执行、再回填 pandas；
  ``CleanReport`` 的类型和入口层的默认行为不变。
- **整条流水线回落**：执行前预检只要发现一个已登记边界（长编号、溢出、混合日期、
  时区、混合列、``sample > 0`` 等），整条流水线改走 pandas，不逐节点混跑。
- **运行期异常不静默换引擎**：预检通过的路径如果运行期抛错，按中文 ``TableError``
  暴露；只有预检结论为回落时才调用 pandas 参考实现。
- **共享判据，不复制语义**：数值整理、计算列收尾、公式判定等继续复用 ``transform`` /
  ``rules`` 里的同一份函数；Polars 只负责数据操作本身，避免两套口径。

符号说明：``polars_impl`` 指向的模块级函数是注册表可解析的实现入口，默认先把输入
pandas 帧转成 Polars；它们假设调用方已经通过 ``preflight``，边界输入由引擎整体回落。
"""

from __future__ import annotations

import ast
import functools
import math
import re
import warnings
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable, Mapping, Sequence

import pandas as pd
import polars as pl

from .. import rules, transform
from ..errors import Deadline, TableError
from ..rules import CleanReport, ProgressCallback, _is_text_column, _looks_like_formula
from ..transform import CleanOptions, ColumnOps
from .base import Engine, register_engine

# 与 pandas 参考实现共用同一组解析/守卫常量，避免两边各写一份后慢慢漂移。
_THOUSANDS_RE = transform._THOUSANDS_RE
_MAX_SAFE_DIGITS = transform._MAX_SAFE_DIGITS
_DIGIT_RE = transform._DIGIT_RE
_MAX_EXPRESSION_CHARS = transform._MAX_EXPRESSION_CHARS
_BRACKET_RE = transform._BRACKET_RE
_FULLWIDTH = transform._FULLWIDTH
_MAX_ROUND_DIGITS = transform._MAX_ROUND_DIGITS

_STRING_DTYPE = pl.Utf8
_MISSING_COLUMN_REASON = "Polars 无法无损映射该列（混合类型或扩展 dtype）"
_SAMPLE_REASON = "sample > 0 需要逐行样例与删除原因，v2.4.0 统一回落 pandas"
_FRAME_PLACEHOLDER = "__polars_result__"


@dataclass(frozen=True)
class FallbackDetail:
    """一条回落原因：属于哪个能力、哪一列、为什么。"""

    capability: str
    column: str | None
    reason: str


@dataclass(frozen=True)
class PreflightResult:
    """执行前预检结论；``engine`` 是整条流水线实际使用的引擎。"""

    engine: str
    details: tuple[FallbackDetail, ...]
    checked_columns: tuple[str, ...] = ()

    @property
    def uses_polars(self) -> bool:
        return self.engine == "polars"

    @property
    def fallback_nodes(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(detail.capability for detail in self.details))

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(
            f"{detail.capability}" + (f"「{detail.column}」" if detail.column else "") + f"：{detail.reason}"
            for detail in self.details
        )

    def reason_for(self, capability: str) -> str | None:
        """返回某项能力的第一条回落原因；没有回落时返回 ``None``。"""
        for detail in self.details:
            if detail.capability == capability:
                return detail.reason
        return None


@dataclass(frozen=True)
class _DateFormat:
    """日期格式族；``polars_formats`` 逐个尝试，全部值匹配的那个用于 Polars 解析。"""

    name: str
    pattern: re.Pattern[str]
    pandas_formats: tuple[str, ...]
    polars_formats: tuple[str, ...]
    sample: str


@dataclass(frozen=True)
class _ResolvedDate:
    """一列日期在预检阶段的结论；``polars_format is None`` 表示按 pandas 行为保留原文。"""

    format: _DateFormat
    values: tuple[str, ...]
    polars_format: str | None


# 常见显式格式：与 pandas ``format="mixed"`` 在当前版本能解析的写法保持一致。
# 中文年月日在 pandas 3.x / 2.x 里都不能解析（实测返回 NaT），因此走「保留原文」的
# 对齐路径而不是硬转；如果未来 pandas 支持，探针会自动改用 Polars 解析。
_DATE_FORMATS: tuple[_DateFormat, ...] = (
    _DateFormat(
        name="iso-date",
        pattern=re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$"),
        pandas_formats=("%Y-%m-%d",),
        polars_formats=("%Y-%m-%d",),
        sample="2024-05-01",
    ),
    _DateFormat(
        name="iso-datetime",
        pattern=re.compile(r"^\d{4}-\d{1,2}-\d{1,2}[ T]\d{1,2}:\d{2}:\d{2}$"),
        pandas_formats=("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"),
        polars_formats=("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"),
        sample="2024-05-01 12:30:00",
    ),
    _DateFormat(
        name="slash-date",
        pattern=re.compile(r"^\d{4}/\d{1,2}/\d{1,2}$"),
        pandas_formats=("%Y/%m/%d",),
        polars_formats=("%Y/%m/%d",),
        sample="2024/05/01",
    ),
    _DateFormat(
        name="chinese-ymd",
        pattern=re.compile(r"^\d{4}年\d{1,2}月\d{1,2}日$"),
        pandas_formats=("%Y年%m月%d日",),
        polars_formats=("%Y年%m月%d日",),
        sample="2024年05月01日",
    ),
)


@functools.lru_cache(maxsize=None)
def _pandas_parses_format(format_name: str) -> bool:
    """探针：当前 pandas 是否能解析该格式族的样例。

    参考实现是 pandas 本身，所以「Polars 该怎么对齐」也必须以它为准。中文年月日在
    当前 pandas 版本解析不了；此时引擎保留原文并计数，与 pandas 逐值一致。用
    ``lru_cache`` 固定一次探针结果，避免逐列重复判断。
    """
    date_format = next(item for item in _DATE_FORMATS if item.name == format_name)
    series = pd.Series([date_format.sample])
    try:
        parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    except (ValueError, TypeError, OverflowError):
        return False
    return bool(pd.api.types.is_datetime64_any_dtype(parsed) and parsed.notna().all())


# ---------------------------------------------------------------- 帧转换


def _to_polars(df: pd.DataFrame) -> pl.DataFrame:
    """pandas → Polars；索引不进入数据，行顺序保持。"""
    return pl.from_pandas(df.reset_index(drop=True))


def _timestamp_to_date_or_none(value: object) -> object:
    if isinstance(value, pd.Timestamp):
        return value.date()
    if value is pd.NaT or (isinstance(value, float) and math.isnan(value)):
        return None
    return value


def _is_object_date_column(series: pd.Series) -> bool:
    """object 列是否整列都是 ``datetime.date``（不含 datetime）。"""
    found = False
    for value in series:
        if value is None or value is pd.NA or (isinstance(value, float) and math.isnan(value)):
            continue
        if isinstance(value, datetime):
            return False
        if not isinstance(value, date):
            return False
        found = True
    return found


def _contains_float_nan(series: pd.Series) -> bool:
    """object 列里是否真的存在 float('nan')；用来区分 None 与 NaN 的原始表示。"""
    for value in series:
        if isinstance(value, float) and math.isnan(value):
            return True
    return False


def _restore_dtype(series: pd.Series, original: pd.Series) -> pd.Series:
    """把 Polars 回填的列恢复成原始 pandas dtype；失败时保留回填结果。

    结构算子（去重/过滤/删列/改名/换序）不改变单元格，恢复 dtype 后 L1/L2 才能与
    pandas 参考实现逐字节一致。转换算子产生的列不经过这里，由各自的收尾逻辑决定。
    """
    target = original.dtype
    try:
        if pd.api.types.is_object_dtype(target):
            restored = series.astype(object)
            if _is_object_date_column(original):
                # Polars Date 回填 pandas 会变成 Timestamp；原列是 datetime.date 时转回去。
                restored = restored.map(_timestamp_to_date_or_none)
            elif not _contains_float_nan(original):
                restored = restored.where(restored.notna(), None)
            return restored
        if pd.api.types.is_string_dtype(target):
            return series.astype(target)
        if pd.api.types.is_bool_dtype(target):
            return series.astype(target)
        if pd.api.types.is_integer_dtype(target):
            return series.astype(target)
        if pd.api.types.is_float_dtype(target):
            return series.astype(target)
        if pd.api.types.is_datetime64_any_dtype(target):
            return pd.to_datetime(series).astype(target)
        return series.astype(target)
    except (TypeError, ValueError, OverflowError):
        return series


def _from_polars(frame: pl.DataFrame, original: pd.DataFrame | None = None) -> pd.DataFrame:
    """Polars → pandas；可选地恢复原始 dtype，并重置为 0 起的行索引。"""
    out = frame.to_pandas()
    if original is not None:
        for column in out.columns:
            if column in original.columns:
                out[column] = _restore_dtype(out[column], original[column])
    return out.reset_index(drop=True)


# ---------------------------------------------------------------- 预检


def _frame_mapping_problem(df: pd.DataFrame) -> str | None:
    """帧能否无损进入 Polars；返回中文原因或 ``None``。"""
    for column in df.columns:
        dtype = df[column].dtype
        if isinstance(dtype, pd.DatetimeTZDtype):
            return f"列「{column}」带时区；v2.4.0 的引擎边界不处理时区列"
    try:
        _to_polars(df)
    except Exception as exc:  # ArrowInvalid / TypeError / ValueError 等第三方异常
        return f"{_MISSING_COLUMN_REASON}（{type(exc).__name__}: {exc}）"
    return None


def _numeric_cell_problem(value: object) -> str | None:
    """逐值判断一个单元格能否被 pandas ``numeric_series`` 无损转换。"""
    text = str(value).strip()
    if not text:
        return None
    if _THOUSANDS_RE.fullmatch(text):
        text = text.replace(",", "")
    try:
        candidate = Decimal(text)
    except (InvalidOperation, ValueError):
        return f"「{value}」不是可识别的数字"
    if not candidate.is_finite():
        return f"「{value}」不是有限数字"
    try:
        number = float(text)
    except (ValueError, OverflowError):
        return f"「{value}」超出双精度范围"
    if not math.isfinite(number):
        return f"「{value}」超出双精度范围（会变成 inf）"
    if len(_DIGIT_RE.findall(text)) > _MAX_SAFE_DIGITS:
        # 与 pandas 相同的精度守卫：只有 Decimal 与 float64 完全等值才允许转换。
        if candidate != Decimal(repr(number)):
            return f"「{value}」有效数字超过 {_MAX_SAFE_DIGITS} 位，转成 float64 会改值"
    return None


def _numeric_column_problem(series: pd.Series) -> str | None:
    """扫描一列，返回第一个无法无损转换的单元格原因。"""
    text = series.astype("string")
    for value in text:
        if pd.isna(value):
            continue
        problem = _numeric_cell_problem(value)
        if problem is not None:
            return problem
    return None


def _date_format_for(series: pd.Series) -> _ResolvedDate | None:
    """识别一列的显式日期格式；混合、非法或无法识别时返回 ``None``（回落）。"""
    values: list[str] = []
    for value in series.astype("string"):
        if pd.isna(value):
            continue
        text = str(value).strip()
        if text:
            values.append(text)
    if not values:
        return _ResolvedDate(_DATE_FORMATS[0], (), _DATE_FORMATS[0].polars_formats[0])
    for date_format in _DATE_FORMATS:
        if not all(date_format.pattern.match(text) for text in values):
            continue
        if date_format.name == "chinese-ymd" and not _pandas_parses_format(date_format.name):
            # pandas 解析不了中文年月日：保留原文并计数才是对齐行为，不硬转。
            return _ResolvedDate(date_format, tuple(values), None)
        for polars_format in date_format.polars_formats:
            if all(_matches_pandas_format(text, (polars_format,)) for text in values):
                return _ResolvedDate(date_format, tuple(values), polars_format)
        return None
    return None


def _matches_pandas_format(text: str, formats: Sequence[str]) -> bool:
    for fmt in formats:
        try:
            datetime.strptime(text, fmt)
        except ValueError:
            continue
        return True
    return False


def _is_boundary_expression(tree: ast.AST) -> str | None:
    """R8 节点需要回落的边界运算；返回原因或 ``None``。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.FloorDiv):
                return "整除（//）"
            if isinstance(node.op, ast.Mod):
                return "取模（%）"
            if isinstance(node.op, ast.Pow):
                return "幂运算（**）"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "round":
            if len(node.args) < 2:
                return "round() 缺少保留位数"
            constant, value = _constant_number(node.args[1])
            if not constant or not isinstance(value, int) or isinstance(value, bool):
                return "round() 的保留位数不是数字常量"
            if abs(value) > _MAX_ROUND_DIGITS:
                return f"round() 保留位数超过 ±{_MAX_ROUND_DIGITS}"
    return None


def _constant_number(node: ast.AST) -> tuple[bool, float | int | None]:
    """把常量表达式（含正负号）折叠成一个数字；不是常量时返回 ``(False, None)``。"""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return True, node.value
        return False, None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        ok, value = _constant_number(node.operand)
        if not ok or value is None:
            return False, None
        return True, value if isinstance(node.op, ast.UAdd) else -value
    return False, None


def _expression_columns(tree: ast.AST, aliases: Mapping[str, str]) -> tuple[str, ...]:
    """表达式真正引用的列名；``round``/``abs`` 是函数名，不能当列名。"""
    names: list[str] = []

    class _Names(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            for argument in node.args:
                self.visit(argument)

        def visit_Name(self, node: ast.Name) -> None:
            name = aliases.get(node.id, node.id)
            if name not in names:
                names.append(name)

        def generic_visit(self, node: ast.AST) -> None:
            if not isinstance(node, (ast.Call, ast.Name)):
                super().generic_visit(node)

    _Names().visit(tree)
    return tuple(names)


def _prepare_expression(expr: str) -> tuple[ast.Expression, dict[str, str]]:
    """与 ``transform.safe_eval`` 相同的预处理：方括号列名占位、全角符号兼容。"""
    text = (expr or "").strip()
    if not text:
        raise TableError("计算表达式是空的，请写成例如「金额=单价*数量」。")
    if len(text) > _MAX_EXPRESSION_CHARS:
        raise TableError(f"计算表达式太长（超过 {_MAX_EXPRESSION_CHARS} 个字符），请拆成多列计算。")
    aliases: dict[str, str] = {}

    def _alias(match: re.Match[str]) -> str:
        name = (match.group(1) or match.group(2)).strip()
        placeholder = f"_列{len(aliases)}"
        aliases[placeholder] = name
        return placeholder

    prepared = _BRACKET_RE.sub(_alias, text)
    for candidate in (prepared, prepared.translate(_FULLWIDTH)):
        try:
            return ast.parse(candidate, mode="eval"), aliases
        except SyntaxError:
            continue
    raise TableError(
        f"计算表达式「{text}」语法有误，请检查括号、运算符和列名；"
        "列名里有空格或括号时，用方括号包起来，例如 [单价（元）]*数量。"
    )


def _expression_problem(
    expr: str,
    df: pd.DataFrame,
    sources: Mapping[str, str],
    computed_names: set[str],
) -> str | None:
    """R8 计算列的预检：解析、边界运算、引用列的数值可转换性。

    ``sources`` 是「当前列名 → 原始数据列名」的映射：前面已经改过名的列也能按新
    名字找到数据，避免把合法流水线误判成回落。
    """
    try:
        tree, aliases = _prepare_expression(expr)
    except TableError as exc:
        return str(exc)
    boundary = _is_boundary_expression(tree.body)
    if boundary is not None:
        return f"计算列含 {boundary}，属于 v2.4.0 登记的边界回落"
    for name in _expression_columns(tree.body, aliases):
        if name in computed_names:
            return f"表达式引用了前面新建的计算列「{name}」，属于 v2.4.0 登记边界"
        source = sources.get(name)
        if source is None:
            return f"表达式引用的列「{name}」不存在"
        series = df[source]
        if pd.api.types.is_numeric_dtype(series):
            continue
        if _is_text_column(series):
            problem = _numeric_column_problem(series)
            if problem is not None:
                return f"列「{name}」含无法转换的数字：{problem}"
            continue
        return f"列「{name}」不是数字（可能是日期或布尔值），不能参与计算"
    return None


def _rename_mapping(pairs: Iterable[tuple[str, str]], sources: Mapping[str, str]) -> dict[str, str]:
    """与 pandas 应用顺序一致的改名映射：不存在/冲突的列跳过并提示。"""
    mapping: dict[str, str] = {}
    for old, new in pairs:
        if old not in sources:
            continue
        if new in sources and new != old:
            continue
        mapping[old] = new
    return mapping


def _pandas_unique_missing(df: pd.DataFrame, columns: Iterable[str]) -> list[str]:
    """保持给定顺序、只取存在的列。"""
    return [column for column in columns if column in df.columns]


def preflight(
    df: pd.DataFrame,
    options: CleanOptions | None = None,
    *,
    sample: int = 0,
    columns: Iterable[str] | None = None,
) -> PreflightResult:
    """执行前预检：决定整条流水线走 Polars 还是回落 pandas。

    只读取必要的目标列；不写数据、不改帧。``columns`` 用于检查算子只关心部分列
    （例如 ``clean_report`` 的删空值列）；不传时检查所有会被转换/计算的列。
    """
    details: list[FallbackDetail] = []
    checked: list[str] = []
    if sample > 0:
        details.append(FallbackDetail("engine", None, _SAMPLE_REASON))
    mapping_problem = _frame_mapping_problem(df)
    if mapping_problem is not None:
        details.append(FallbackDetail("engine", None, mapping_problem))
        return PreflightResult("pandas", tuple(details), tuple(checked))

    scope = set(columns) if columns is not None else None
    if options is not None:
        ops = options.columns
        # 按 apply_transforms 的固定顺序模拟列状态：删列 → 转换 → 改名 → 计算列。
        # 这样「先改名、再用新名字计算」的合法流水线不会因为预检看错列名而回落。
        sources: dict[str, str] = {column: column for column in df.columns}
        computed_names: set[str] = set()
        for column in ops.drop:
            sources.pop(column, None)
        for column in ops.to_number:
            source = sources.get(column)
            if source is None or (scope is not None and column not in scope):
                continue
            checked.append(column)
            problem = _numeric_column_problem(df[source])
            if problem is not None:
                details.append(FallbackDetail("R5", column, problem))
        for column in ops.to_date:
            source = sources.get(column)
            if source is None or (scope is not None and column not in scope):
                continue
            checked.append(column)
            resolved = _date_format_for(df[source])
            if resolved is None:
                details.append(FallbackDetail("R6", column, "日期格式混合、非法或无法识别，不猜格式"))
        mapping = _rename_mapping(ops.rename, sources)
        if mapping:
            sources = {mapping.get(name, name): source for name, source in sources.items()}
        for name, expr in ops.computed:
            checked.append(name)
            if name in sources:
                # pandas 会跳过并给「和已有列重名」提示；回落保证提示逐字一致。
                details.append(FallbackDetail("R8", name or None, f"计算列「{name}」和已有列重名"))
                continue
            problem = _expression_problem(expr, df, sources, computed_names)
            if problem is not None:
                details.append(FallbackDetail("R8", name or None, problem))
            computed_names.add(name)

    if details:
        return PreflightResult("pandas", tuple(details), tuple(dict.fromkeys(checked)))
    return PreflightResult("polars", (), tuple(dict.fromkeys(checked)))


# ---------------------------------------------------------------- 行清洗


def _missing_expr(column: str) -> pl.Expr:
    """与 ``rules.missing_mask`` 同义：null 或空文本都算缺失。"""
    text = pl.col(column).cast(_STRING_DTYPE, strict=False)
    return pl.col(column).is_null() | (text == "")


def drop_duplicate_rows(
    df: pd.DataFrame,
    subset: list[str] | None = None,
    progress: ProgressCallback | None = None,
    deadline: Deadline | None = None,
    label: str = "删除完全重复的行",
) -> pd.DataFrame:
    """R1/R2：Polars 去重，保留第一次出现并保持原行序。"""
    if deadline is not None:
        deadline.check(label)
    frame = _to_polars(df)
    out = frame.unique(subset=subset, keep="first", maintain_order=True)
    if progress is not None:
        progress(frame.height, frame.height)
    if deadline is not None:
        deadline.check(label)
    return _from_polars(out, original=df)


def drop_missing_rows(
    df: pd.DataFrame,
    columns: list[str],
    deadline: Deadline | None = None,
) -> pd.DataFrame:
    """R3：所选列任一为 null/空文本就删整行。"""
    if deadline is not None:
        deadline.check("删除指定列中有空值的行")
    frame = _to_polars(df)
    existing = _pandas_unique_missing(df, columns)
    if not existing:
        return _from_polars(frame, original=df)
    predicate = pl.any_horizontal([_missing_expr(column) for column in existing])
    out = frame.filter(~predicate)
    if deadline is not None:
        deadline.check("删除指定列中有空值的行")
    return _from_polars(out, original=df)


def _clean_report_polars(
    df: pd.DataFrame,
    *,
    drop_duplicates: bool,
    dedupe_on: list[str] | None,
    drop_missing_in: list[str] | None,
    progress: ProgressCallback | None,
    deadline: Deadline | None,
) -> CleanReport:
    """Polars 行清洗；报告 steps 与 pandas 参考实现逐项一致。"""
    out = df.reset_index(drop=True)
    frame = _to_polars(out)
    total = frame.height
    steps: dict[str, int] = {}
    if progress is not None:
        progress(0, total)

    def step(label: str, new_frame: pl.DataFrame) -> None:
        nonlocal frame
        steps[label] = frame.height - new_frame.height
        frame = new_frame

    if drop_duplicates:
        if deadline is not None:
            deadline.check("删除完全重复的行")
        step(
            "删除完全重复的行",
            frame.unique(keep="first", maintain_order=True),
        )
        if progress is not None:
            progress(total, total)
    dedupe_cols = _pandas_unique_missing(out, dedupe_on or [])
    if dedupe_cols:
        if deadline is not None:
            deadline.check("按指定列去重")
        step(
            "按指定列去重",
            frame.unique(subset=dedupe_cols, keep="first", maintain_order=True),
        )
        if progress is not None:
            progress(total, total)
    missing_cols = _pandas_unique_missing(out, drop_missing_in or [])
    if missing_cols:
        if deadline is not None:
            deadline.check("删除指定列中有空值的行")
        predicate = pl.any_horizontal([_missing_expr(column) for column in missing_cols])
        step("删除指定列中有空值的行", frame.filter(~predicate))
        if progress is not None:
            progress(total, total)
    if progress is not None and not any(steps):
        progress(total, total)
    if deadline is not None:
        deadline.check("行清洗")

    removed = df.head(0).assign(删除原因="")
    return CleanReport(_from_polars(frame, original=out), steps, removed)


# ---------------------------------------------------------------- 类型转换


def _numeric_expression(column: str) -> pl.Expr:
    """与 ``numeric_series`` 同义：去空白、只去合法千分位逗号、转 Float64。"""
    text = pl.col(column).cast(_STRING_DTYPE, strict=False).str.strip_chars()
    thousands = text.str.contains(_THOUSANDS_RE.pattern)
    text = pl.when(thousands).then(text.str.replace_all(",", "")).otherwise(text)
    return text.cast(pl.Float64, strict=False)


def _series_from_frame(frame: pl.DataFrame, column: str) -> pd.Series:
    values = frame.select(pl.col(column).alias(_FRAME_PLACEHOLDER)).to_series().to_pandas()
    # pandas 参考实现从 StringDtype 调 pd.to_numeric，得到可空 Float64；这里显式对齐，
    # 否则 Polars 回填的 numpy float64 会让 L1 dtype 与 L2 导出出现差异。
    return pd.Series(values, name=column, dtype="Float64")


def convert_to_number(series: pd.Series) -> tuple[pd.Series, int]:
    """R5：Polars 数值转换；失败位置保留原文并计数（与 pandas 同签名）。

    边界输入由引擎预检整体回落，本函数只处理预检通过、可无损转换的列。
    """
    name = series.name if series.name is not None else "值"
    frame = _to_polars(series.rename(name).to_frame(name=name))
    numeric = _series_from_frame(frame.with_columns(_numeric_expression(name).alias(name)), name)
    tidied = transform._tidy_numbers(numeric)
    return tidied.rename(series.name), 0


def convert_to_date(series: pd.Series) -> tuple[pd.Series, int]:
    """R6：Polars 日期转换；只走显式格式，解析失败与 pandas 一样保留原文。"""
    name = series.name if series.name is not None else "值"
    resolved = _date_format_for(series)
    if resolved is None:
        raise TableError(f"列「{name}」的日期格式无法由 Polars 统一解析。")
    if resolved.polars_format is None:
        # pandas 解析不了该格式（当前是中文年月日）：对齐行为是整列保留原文并计数。
        cleaned = series.astype("string").str.strip()
        non_empty = cleaned.notna() & cleaned.str.len().gt(0)
        out = series.astype(object).copy()
        return out.rename(series.name), int(non_empty.sum())
    frame = _to_polars(series.rename(name).to_frame(name=name))
    text = pl.col(name).cast(_STRING_DTYPE, strict=False).str.strip_chars()
    text = pl.when(text == "").then(pl.lit(None)).otherwise(text)
    parsed = frame.select(text.str.to_datetime(format=resolved.polars_format, strict=True).alias(name))
    values = parsed.to_series().to_pandas()
    parsed_series = pd.Series(values, name=series.name)
    known = parsed_series.dropna()
    if len(known) and bool((known == known.dt.normalize()).all()):
        parsed_series = parsed_series.dt.date
    cleaned = series.astype("string").str.strip()
    non_empty = cleaned.notna() & cleaned.str.len().gt(0)
    failed = int((non_empty & parsed_series.isna()).sum())
    if failed:
        # 与 pandas 相同：成功位置转日期，失败/空文本位置保留原文，整体回到 object。
        out = series.astype(object).copy()
        ok = parsed_series.notna() & non_empty
        out[ok] = parsed_series[ok]
        return out, failed
    return parsed_series, 0


# ---------------------------------------------------------------- 结构变换


def drop_columns(df: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    """R4：删除存在的列（不存在的列由调用方提示并忽略）。"""
    frame = _to_polars(df)
    existing = _pandas_unique_missing(df, columns)
    if not existing:
        return _from_polars(frame, original=df)
    return _from_polars(frame.drop(existing), original=df)


def rename_columns(df: pd.DataFrame, pairs: Iterable[tuple[str, str]]) -> pd.DataFrame:
    """R7：按映射一次改名；调用方负责冲突/不存在列的提示。"""
    mapping: dict[str, str] = {}
    for old, new in pairs:
        if old in df.columns and (new == old or new not in df.columns):
            mapping[old] = new
    frame = _to_polars(df)
    if not mapping:
        return _from_polars(frame, original=df)
    renamed = frame.rename(mapping)
    original = df.rename(columns=mapping)
    return _from_polars(renamed, original=original)


def order_columns(df: pd.DataFrame, order: Iterable[str]) -> pd.DataFrame:
    """R9：点名在前（按给定顺序），其余列按原顺序跟随。"""
    wanted = _pandas_unique_missing(df, order)
    if not wanted:
        return _from_polars(_to_polars(df), original=df)
    rest = [column for column in df.columns if column not in wanted]
    frame = _to_polars(df)
    return _from_polars(frame.select(wanted + rest), original=df[wanted + rest])


# ---------------------------------------------------------------- 计算列表达式


@dataclass(frozen=True)
class _CompiledExpr:
    """编译后的表达式：常量标量直接求值，列表达式交给 Polars。"""

    expr: pl.Expr | None
    constant: bool
    value: float | int | None = None


def _column_expression(column: str, series: pd.Series) -> pl.Expr:
    if pd.api.types.is_numeric_dtype(series):
        return pl.col(column).cast(pl.Float64)
    if _is_text_column(series):
        return _numeric_expression(column)
    raise TableError(f"列「{column}」不是数字（可能是日期或布尔值），不能参与计算。")


def _compile_node(node: ast.AST, df: pd.DataFrame, aliases: Mapping[str, str]) -> _CompiledExpr:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return _CompiledExpr(None, True, node.value)
        raise TableError("计算表达式里只能用数字，不能用文字或其他常量；请只写列名、数字和四则运算。")
    if isinstance(node, ast.Name):
        name = aliases.get(node.id, node.id)
        if name not in df.columns:
            options = "、".join(str(column) for column in df.columns[:8])
            raise TableError(
                f"表达式里的「{name}」不是当前的列名。"
                f"可用的列有：{options}{'…' if len(df.columns) > 8 else ''}"
                "；列名里有空格或括号时，用方括号包起来，例如 [单价（元）]*数量。"
            )
        return _CompiledExpr(_column_expression(name, df[name]), False)
    if isinstance(node, ast.BinOp):
        left = _compile_node(node.left, df, aliases)
        right = _compile_node(node.right, df, aliases)
        if left.constant and right.constant:
            return _CompiledExpr(None, True, _constant_binop(node.op, left.value, right.value))
        left_expr = left.expr if left.expr is not None else pl.lit(left.value)
        right_expr = right.expr if right.expr is not None else pl.lit(right.value)
        if isinstance(node.op, ast.Add):
            return _CompiledExpr(left_expr + right_expr, False)
        if isinstance(node.op, ast.Sub):
            return _CompiledExpr(left_expr - right_expr, False)
        if isinstance(node.op, ast.Mult):
            return _CompiledExpr(left_expr * right_expr, False)
        if isinstance(node.op, ast.Div):
            return _CompiledExpr(left_expr / right_expr, False)
        if isinstance(node.op, ast.FloorDiv):
            return _CompiledExpr(left_expr // right_expr, False)
        if isinstance(node.op, ast.Mod):
            return _CompiledExpr(left_expr % right_expr, False)
        if isinstance(node.op, ast.Pow):
            return _CompiledExpr(left_expr**right_expr, False)
        raise TableError("计算表达式只支持 + - * / // % ** 这几种运算，不支持位运算、比较或字符串拼接。")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        operand = _compile_node(node.operand, df, aliases)
        if operand.constant:
            value = operand.value
            assert value is not None
            return _CompiledExpr(None, True, value if isinstance(node.op, ast.UAdd) else -value)
        assert operand.expr is not None
        if isinstance(node.op, ast.UAdd):
            return _CompiledExpr(operand.expr, False)
        return _CompiledExpr(-operand.expr, False)
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in transform._ALLOWED_FUNCS:
            raise TableError(
                "计算表达式里只支持 round() 和 abs() 两个函数；其他函数、属性访问（点号）和下标都不允许执行。"
            )
        if node.keywords:
            raise TableError("计算表达式里的函数不支持关键字参数，例如 round(金额, 2) 这样写。")
        args = [_compile_node(arg, df, aliases) for arg in node.args]
        if node.func.id == "round":
            if len(args) < 2 or not args[1].constant or not isinstance(args[1].value, int):
                raise TableError("round() 的保留位数必须是数字常量；请写成 round(金额, 2) 这种形式。")
            ndigits = int(args[1].value)
            transform._safe_round(0.0, ndigits)  # 触发与 pandas 相同的位数上限守卫
            if args[0].constant:
                value = args[0].value
                assert value is not None
                return _CompiledExpr(None, True, round(float(value), ndigits))
            assert args[0].expr is not None
            if ndigits >= 0:
                return _CompiledExpr(args[0].expr.round(ndigits), False)
            # Polars 的 round 不支持负位数；按 10 的幂缩放后再做半偶舍入。
            factor = float(10**-ndigits)
            return _CompiledExpr((args[0].expr / factor).round(0) * factor, False)
        if len(args) != 1:
            raise TableError("abs() 只接受一个参数，例如 abs(金额)。")
        if args[0].constant:
            value = args[0].value
            assert value is not None
            return _CompiledExpr(None, True, abs(value))
        assert args[0].expr is not None
        return _CompiledExpr(args[0].expr.abs(), False)
    raise TableError(
        "计算表达式里有不支持的写法；只允许列名、数字、+ - * / // % ** 和 round()/abs()，"
        "不能写代码、函数定义或访问属性。"
    )


def _constant_binop(op: ast.operator, left: Any, right: Any) -> Any:
    if isinstance(op, ast.Pow):
        return transform._safe_pow(left, right)
    func = transform._ALLOWED_BINOPS.get(type(op))
    if func is None:
        raise TableError("计算表达式只支持 + - * / // % ** 这几种运算，不支持位运算、比较或字符串拼接。")
    return func(left, right)


def evaluate_expression(expr: str, df: pd.DataFrame) -> pd.Series:
    """X1/X2/X4：用 Polars 求值一段白名单算术表达式，返回与 ``safe_eval`` 同语义的列。"""
    tree, aliases = _prepare_expression(expr)
    compiled = _compile_node(tree.body, df, aliases)
    if compiled.constant:
        return pd.Series(compiled.value, index=df.index)
    assert compiled.expr is not None
    frame = _to_polars(df)
    result = frame.select(compiled.expr.alias(_FRAME_PLACEHOLDER)).to_series().to_pandas()
    return pd.Series(result, index=df.index)


def evaluate_round(value: pd.Series | float | int, ndigits: int = 0) -> pd.Series | float | int:
    """X3：round() 的 Polars 实现与共享位数守卫；标量走 Python round。"""
    transform._safe_round(0.0, ndigits)
    if not isinstance(value, pd.Series):
        return round(value, ndigits)
    name = value.name if value.name is not None else "值"
    frame = _to_polars(value.rename(name).to_frame(name=name))
    expression = pl.col(name).cast(pl.Float64)
    if ndigits >= 0:
        expression = expression.round(ndigits)
    else:
        factor = float(10**-ndigits)
        expression = (expression / factor).round(0) * factor
    rounded = frame.select(expression.alias(name)).to_series().to_pandas()
    return pd.Series(rounded, name=value.name)


def evaluate_abs(value: pd.Series | float | int) -> pd.Series | float | int:
    """X4：abs() 的 Polars 实现；标量走 Python abs。"""
    if not isinstance(value, pd.Series):
        return abs(value)
    name = value.name if value.name is not None else "值"
    frame = _to_polars(value.rename(name).to_frame(name=name))
    absolute = frame.select(pl.col(name).abs().alias(name)).to_series().to_pandas()
    return pd.Series(absolute, name=value.name)


def evaluate_pow_guard(base: pd.Series | float | int, exp: float | int) -> pd.Series | float | int:
    """X5：常量幂做位数守卫；列幂交给 Polars（运行期溢出与 pandas 一样保留 inf）。"""
    if isinstance(base, (int, float)) and not isinstance(base, bool):
        return transform._safe_pow(base, exp)
    name = base.name if base.name is not None else "值"
    frame = _to_polars(base.rename(name).to_frame(name=name))
    result = frame.select((pl.col(name).cast(pl.Float64) ** float(exp)).alias(name)).to_series().to_pandas()
    return pd.Series(result, name=base.name)


def add_computed_column(df: pd.DataFrame, name: str, expr: str, notes: list[str]) -> pd.DataFrame:
    """R8：Polars 计算列；收尾（无穷大留空、整数化、说明）复用 pandas 同一份函数。"""
    if not name:
        notes.append(f"计算列里有空列名，已跳过（表达式「{expr}」）。")
        return df
    if name in df.columns:
        notes.append(f"计算列「{name}」和已有列重名，已跳过；请换个列名。")
        return df
    try:
        result = evaluate_expression(expr, df)
    except TableError as exc:
        notes.append(f"计算列「{name}」没有生成：{exc}")
        return df
    except Exception as exc:
        notes.append(f"计算列「{name}」没有生成：表达式「{expr}」算不出来（{exc}）；请确认参与计算的列都是数字。")
        return df
    return transform._finalize_computed_column(df, name, result, notes)


# ---------------------------------------------------------------- 列变换应用


def _apply_transforms_polars(
    df: pd.DataFrame,
    ops: ColumnOps,
    *,
    deadline: Deadline | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Polars 路径的列变换；步骤顺序与 ``transform.apply_transforms`` 完全一致。"""
    notes: list[str] = []
    if deadline is not None:
        deadline.check("列变换")
    frame = _to_polars(df)
    converted: dict[str, pd.Series] = {}

    if ops.drop:
        existing = _pandas_unique_missing(df, ops.drop)
        missing = [name for name in ops.drop if name not in df.columns]
        if existing:
            frame = frame.drop(existing)
        if missing:
            notes.append(f"要删除的列不存在，已忽略：{'、'.join(missing)}。")

    for column in ops.to_number:
        if deadline is not None:
            deadline.check("列变换-转数字")
        if column not in frame.columns:
            notes.append(f"要转数字的列「{column}」不存在，已忽略。")
            continue
        converted_series, failed = convert_to_number(pd.Series(frame[column].to_pandas(), name=column))
        converted[column] = converted_series
        frame = frame.with_columns(pl.from_pandas(converted_series.rename(column).to_frame(name=column))[column])
        if failed:
            notes.append(
                transform._conversion_failures(converted_series, "转数字", failed, "；需要的话请手工修正这些格子。")
            )
    for column in ops.to_date:
        if deadline is not None:
            deadline.check("列变换-转日期")
        if column not in frame.columns:
            notes.append(f"要转日期的列「{column}」不存在，已忽略。")
            continue
        converted_series, failed = convert_to_date(pd.Series(frame[column].to_pandas(), name=column))
        converted[column] = converted_series
        frame = frame.with_columns(pl.from_pandas(converted_series.rename(column).to_frame(name=column))[column])
        if failed:
            notes.append(
                transform._conversion_failures(
                    converted_series, "转日期", failed, "；常见写法如 2024-05-01、2024/5/1。"
                )
            )

    mapping: dict[str, str] = {}
    for old, new in ops.rename:
        if old not in frame.columns:
            notes.append(f"要重命名的列「{old}」不存在，已忽略。")
            continue
        if new in frame.columns and new != old:
            notes.append(f"列名「{new}」已经存在，没有把「{old}」改成它。")
            continue
        mapping[old] = new
    if mapping:
        frame = frame.rename(mapping)
        converted = {mapping.get(name, name): series for name, series in converted.items()}

    for name, expr in ops.computed:
        if deadline is not None:
            deadline.check("列变换-计算列")
        updated = add_computed_column(_from_polars(frame), name, expr, notes)
        if name not in updated.columns:
            continue
        converted[name] = updated[name]
        if name not in frame.columns:
            frame = frame.with_columns(pl.lit(None).alias(name))

    if ops.order:
        if deadline is not None:
            deadline.check("列变换-列顺序")
        wanted = [name for name in ops.order if name in frame.columns]
        missing = [name for name in ops.order if name not in frame.columns]
        if wanted:
            rest = [column for column in frame.columns if column not in wanted]
            frame = frame.select(wanted + rest)
        if missing:
            notes.append(f"要调整顺序的列不存在，已忽略：{'、'.join(missing)}。")

    original = df if not mapping else df.rename(columns=mapping)
    out = _from_polars(frame, original=original)
    for column, series in converted.items():
        out[column] = series.set_axis(out.index)
    return out, notes


# ---------------------------------------------------------------- 检查算子


def find_missing(df: pd.DataFrame) -> pd.Series:
    """I1：空值计数，null 与空文本都算，数字 0 / 文本 "0" 不算。"""
    frame = _to_polars(df)
    counts: list[int] = []
    for column in frame.columns:
        value = frame.select(_missing_expr(column).sum()).item()
        counts.append(int(value or 0))
    return pd.Series(counts, index=list(df.columns), dtype="int64")


def summarize(df: pd.DataFrame) -> dict[str, int]:
    """I2：清洗前后统计；完全重复行按整行判等（null 相等）。"""
    frame = _to_polars(df)
    missing = find_missing(df)
    return {
        "行数": int(frame.height),
        "列数": int(frame.width),
        "空值单元格": int(missing.sum()),
        # 与 pandas duplicated() 一致：每组重复行只把「第一次出现」之后的行计为重复。
        "完全重复行": int(frame.height - frame.unique(maintain_order=True).height),
    }


def _count_boolean_map(values: pl.Series, predicate: Callable[[str], bool]) -> int:
    """逐值回调计数；精确优先（Unicode 空白/公式判定与 pandas 完全一致）。

    Polars 会提示 map_elements 比原生表达式慢；本版没有性能目标，且 ``strip_chars``
    的空白集合与 Python ``str.strip`` 不完全一致，因此保留逐值实现并局部静音提示。
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        total = values.map_elements(predicate, return_dtype=pl.Boolean).sum()
    return int(total or 0)


def find_suspicious(df: pd.DataFrame) -> dict[str, dict[str, int]]:
    """I3：可疑格式检查；文本列识别与公式判定复用 pandas 侧的同一份纯函数。"""
    frame = _to_polars(df)
    found: dict[str, dict[str, int]] = {}
    for column in df.columns:
        if not _is_text_column(df[column]):
            continue
        values = frame.select(pl.col(column).drop_nulls()).to_series()
        spaced = _count_boolean_map(values, lambda value: value != value.strip())
        formulas = _count_boolean_map(values, _looks_like_formula)
        issues: dict[str, int] = {}
        if spaced:
            issues[rules.SPACE_ISSUE] = spaced
        if formulas:
            issues[rules.FORMULA_ISSUE] = formulas
        if issues:
            found[str(column)] = issues
    return found


# ---------------------------------------------------------------- 引擎实现


def _engine_error(stage: str, exc: Exception) -> TableError:
    """把预检通过后的意外异常变成中文错误；不静默改用 pandas 重跑。"""
    return TableError(
        f"Polars 引擎在{stage}时出错（{type(exc).__name__}：{exc}）。"
        "为避免掩盖问题，没有自动改用 pandas；请把这条提示反馈给维护者。"
    )


class PolarsEngine:
    """17 项能力的 Polars 引擎；默认不启用，显式 ``get_engine("polars")`` 才 import。"""

    name = "polars"

    def preflight(
        self,
        df: pd.DataFrame,
        options: CleanOptions | None = None,
        *,
        sample: int = 0,
        columns: Iterable[str] | None = None,
    ) -> PreflightResult:
        return preflight(df, options, sample=sample, columns=columns)

    def clean_report(
        self,
        df: pd.DataFrame,
        drop_duplicates: bool = False,
        dedupe_on: list[str] | None = None,
        drop_missing_in: list[str] | None = None,
        sample: int = 0,
        *,
        progress: ProgressCallback | None = None,
        deadline: Deadline | None = None,
        chunk_rows: int | None = None,
    ) -> CleanReport:
        decision = preflight(df, None, sample=sample)
        if not decision.uses_polars:
            return rules.clean_report(
                df,
                drop_duplicates=drop_duplicates,
                dedupe_on=dedupe_on,
                drop_missing_in=drop_missing_in,
                sample=sample,
                progress=progress,
                deadline=deadline,
                chunk_rows=chunk_rows,
            )
        try:
            return _clean_report_polars(
                df,
                drop_duplicates=drop_duplicates,
                dedupe_on=dedupe_on,
                drop_missing_in=drop_missing_in,
                progress=progress,
                deadline=deadline,
            )
        except TableError:
            raise
        except Exception as exc:
            raise _engine_error("行清洗", exc) from exc

    def apply_transforms(self, df: pd.DataFrame, ops: ColumnOps) -> tuple[pd.DataFrame, list[str]]:
        decision = preflight(df, CleanOptions(columns=ops))
        if not decision.uses_polars:
            return transform.apply_transforms(df, ops)
        try:
            return _apply_transforms_polars(df, ops)
        except TableError:
            raise
        except Exception as exc:
            raise _engine_error("列变换", exc) from exc

    def clean_table(
        self,
        df: pd.DataFrame,
        options: CleanOptions,
        *,
        sample: int = 0,
        progress: ProgressCallback | None = None,
        deadline: Deadline | None = None,
    ) -> CleanReport:
        decision = preflight(df, options, sample=sample)
        if not decision.uses_polars:
            return transform.clean_table(
                df,
                options,
                sample=sample,
                progress=progress,
                deadline=deadline,
            )
        try:
            report = _clean_report_polars(
                df,
                drop_duplicates=options.drop_duplicates,
                dedupe_on=list(options.dedupe_on) or None,
                drop_missing_in=list(options.drop_missing_in) or None,
                progress=progress,
                deadline=deadline,
            )
            if options.columns.is_empty():
                return report
            transformed, notes = _apply_transforms_polars(report.df, options.columns, deadline=deadline)
            if deadline is not None:
                deadline.check("列变换")
            return CleanReport(transformed, report.steps, report.removed, tuple(notes))
        except TableError:
            raise
        except Exception as exc:
            raise _engine_error("清洗流水线", exc) from exc

    def find_missing(self, df: pd.DataFrame) -> pd.Series:
        if not preflight(df).uses_polars:
            return rules.find_missing(df)
        return find_missing(df)

    def find_suspicious(self, df: pd.DataFrame) -> dict[str, dict[str, int]]:
        if not preflight(df).uses_polars:
            return rules.find_suspicious(df)
        return find_suspicious(df)

    def summarize(self, df: pd.DataFrame) -> dict[str, int]:
        if not preflight(df).uses_polars:
            return rules.summarize(df)
        return summarize(df)


POLARS_ENGINE = PolarsEngine()
register_engine(POLARS_ENGINE)

# 让 mypy 按协议检查实现；运行时一致性由对齐测试核对。
_POLARS_ENGINE_CONTRACT: Engine = POLARS_ENGINE


__all__ = [
    "FallbackDetail",
    "POLARS_ENGINE",
    "PolarsEngine",
    "PreflightResult",
    "add_computed_column",
    "convert_to_date",
    "convert_to_number",
    "drop_columns",
    "drop_duplicate_rows",
    "drop_missing_rows",
    "evaluate_abs",
    "evaluate_expression",
    "evaluate_pow_guard",
    "evaluate_round",
    "find_missing",
    "find_suspicious",
    "order_columns",
    "preflight",
    "rename_columns",
    "summarize",
]
