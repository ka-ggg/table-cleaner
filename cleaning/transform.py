"""列级变换：重命名、删列、调顺序、类型转换、计算列（安全表达式求值）。

应用顺序固定（界面和 README 说同一套）：
**删列 → 类型转换 → 重命名 → 计算列 → 列顺序**。
类型转换用**原列名**（下拉框里列的就是原列名），重命名之后的计算列和列顺序用**新列名**；
列顺序最后排，用户点名的列排前面，没点到的列按原顺序跟在后面。
"""

from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Callable

import numpy as np
import pandas as pd

from .errors import Deadline, TableError
from .rules import CleanReport, clean_report

# 合法的千分位写法：只去掉这种逗号，避免把 "1,2" 误当成 12
_THOUSANDS_RE = re.compile(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_MAX_EXPRESSION_CHARS = 200
# 中文输入法打出的全角括号/运算符：手机上是最高频的「语法错误」来源，直接换成半角
_FULLWIDTH = str.maketrans({"（": "(", "）": ")", "＋": "+", "－": "-", "＊": "*", "／": "/", "％": "%", "　": " "})
# 列名里带空格/括号时没法直接写进表达式，允许用 [列名] 或【列名】包起来
_BRACKET_RE = re.compile(r"\[([^\[\]]+)\]|【([^【】]+)】")
_MAX_POW_BITS = 4096  # 计算列里整数结果的上限（约 1233 位十进制），防止 9**9**9 这种爆炸
_MAX_ROUND_DIGITS = 30  # round() 保留位数的上限：再大没有实际意义，还会去算 10**n 卡住
_MAX_SAFE_DIGITS = 15  # float64 保证 15 位有效数字可无损往返（超过就可能悄悄改数值）
_DIGIT_RE = re.compile(r"[0-9]")


def _safe_pow(base: object, exp: object) -> object:
    """幂运算守卫：常数底/指数先估结果位数，10**1e8 这种不必真的算完（会吃光内存）。

    只拦「两个 Python 整数」这种常量表达式；列参与运算时是 numpy 浮点，
    溢出后是 inf，由 add_computed_column 统一留空，不会把内存吃爆。
    """
    if isinstance(base, int) and isinstance(exp, int) and abs(base) > 1 and exp > 0:
        # 结果的二进制位数至少 exp × (bit_length(base) - 1)，超过上限一定爆炸
        if exp * (abs(base).bit_length() - 1) > _MAX_POW_BITS:
            raise TableError(
                f"计算表达式里的 {base}**{exp} 结果太大（超过 {_MAX_POW_BITS} 位整数），"
                "已停止计算以免卡死；请改成实际需要的算式（例如用列名参与计算）。"
            )
    return base**exp


_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: _safe_pow,
}


def _safe_round(value: object, ndigits: int = 0) -> object:
    """round() 守卫：round(1, -10000000) 会去算 10 的 1000 万次方（实测 18 秒），先拦掉。"""
    if isinstance(ndigits, int) and abs(ndigits) > _MAX_ROUND_DIGITS:
        raise TableError(
            f"round() 的保留位数 {ndigits} 太大（支持 -{_MAX_ROUND_DIGITS}~{_MAX_ROUND_DIGITS}），"
            "请改成实际需要的位数，例如 round(金额, 2)。"
        )
    return round(value, ndigits)


_ALLOWED_FUNCS = {"round": _safe_round, "abs": abs}


@dataclass(frozen=True)
class ColumnOps:
    """界面上的「进阶列操作」；全部默认空 = 不做任何列变换。"""

    rename: tuple[tuple[str, str], ...] = ()  # (旧列名, 新列名)
    drop: tuple[str, ...] = ()  # 要删除的列
    order: tuple[str, ...] = ()  # 想排在前面的列（按给定顺序）
    to_number: tuple[str, ...] = ()  # 转成数字的列
    to_date: tuple[str, ...] = ()  # 转成日期的列
    computed: tuple[tuple[str, str], ...] = ()  # (新列名, 表达式)

    def is_empty(self) -> bool:
        return not (
            self.rename
            or self.drop
            or self.order
            or self.to_number
            or self.to_date
            or self.computed
        )


@dataclass(frozen=True)
class CleanOptions:
    """一次清洗的全部选择（行规则 + 列变换），界面/批量/测试共用同一份。"""

    header_row: int = 1
    drop_duplicates: bool = False
    dedupe_on: tuple[str, ...] = ()
    drop_missing_in: tuple[str, ...] = ()
    columns: ColumnOps = field(default_factory=ColumnOps)


# ---------------------------------------------------------------- 文本解析


def parse_pairs(text: str, example: str = "旧列名=新列名") -> tuple[tuple[str, str], ...]:
    """把「每行一个 旧=新」的文本解析成有序元组；空行和 # 开头的行忽略。

    中文输入法很容易打出全角「＝」，这里一并接受，不然手机上会莫名其妙报格式错。
    """
    pairs: list[tuple[str, str]] = []
    for lineno, raw in enumerate((text or "").splitlines(), 1):
        line = raw.replace("＝", "=").strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise TableError(
                f"第 {lineno} 行「{line}」缺少等号，格式应为「{example}」。"
            )
        old, new = (part.strip() for part in line.split("=", 1))
        if not old or not new:
            raise TableError(
                f"第 {lineno} 行等号两边有空的，格式应为「{example}」。"
            )
        pairs.append((old, new))
    return tuple(pairs)


def parse_names(text: str) -> tuple[str, ...]:
    """把「每行一个列名」的文本解析成有序、去重后的元组。"""
    names: list[str] = []
    for raw in (text or "").splitlines():
        name = raw.replace("＝", "").strip()
        if name and name not in names and not name.startswith("#"):
            names.append(name)
    return tuple(names)


# ---------------------------------------------------------------- 类型转换


def numeric_series(series: pd.Series) -> pd.Series:
    """尽量把一列变成数字：去首尾空白、去合法千分位逗号，转不了的给 NaN。

    精度守卫：只有「转完以后数值和原文完全一样」的格子才算转换成功。
    超过 15 位有效数字的长编号（订单号、身份证号、条码）在 float64 里会丢精度，
    int64 装不下的还会被截成 9223372036854775807——那等于悄悄改数据。
    这类格子一律保持 NaN（调用方会保留原文并提示），导出时仍是原本的文字。
    """
    cleaned = series.astype("string").str.strip()
    # 只去掉「合法千分位」写法里的逗号，避免把 "1,2" 误当成 12
    with_commas = cleaned.str.fullmatch(_THOUSANDS_RE).fillna(False).astype(bool)
    text = cleaned.where(~with_commas, cleaned.str.replace(",", "", regex=False))
    numeric = pd.to_numeric(text, errors="coerce")
    # 溢出成 inf 的（1E400）和精度不够的都不算转换成功
    keep = np.isfinite(numeric.to_numpy(dtype="float64", na_value=np.nan))
    long_cells = text.fillna("").str.count(_DIGIT_RE).to_numpy() > _MAX_SAFE_DIGITS
    for position in np.flatnonzero(long_cells & keep):
        try:
            # 用 float64（Excel/XLSX 真正存数字用的精度）当判据，而不是 int64 的精确值：
            # 一整列都是整齐整数时 pandas 会给出精确的 Int64，守卫若按 int64 比就会放行，
            # 导出的 XLSX 里 18 位身份证号仍会被四舍五入成同一个值。
            keep[position] = Decimal(text.iloc[position]) == Decimal(
                repr(float(numeric.iloc[position]))
            )
        except (InvalidOperation, ValueError):
            keep[position] = False
    return numeric.where(pd.Series(keep, index=numeric.index))


def _tidy_numbers(numeric: pd.Series) -> pd.Series:
    """整数就写成整数：避免 Excel/CSV 里出现 123.0 这种别扭的编号。"""
    known = numeric.dropna()
    if not len(known) or not bool((known % 1 == 0).all()):
        return numeric
    try:
        too_big = bool((known.abs() > 2**63 - 1).any())  # int64 装不下就别硬转
    except (TypeError, OverflowError):
        too_big = True
    if too_big:
        return numeric
    try:
        return numeric.astype("Int64")
    except (TypeError, ValueError, OverflowError):  # 极端值转不了就保持原样
        return numeric


def _tidy_object_numbers(values: pd.Series) -> pd.Series:
    """混合列（数字 + 原文）里的整数值也写成 int，别显示成 123.0。"""
    return values.map(
        lambda value: int(value)
        if isinstance(value, float) and value.is_integer()
        else value
    )


def convert_to_number(series: pd.Series) -> tuple[pd.Series, int]:
    """转数字：失败的位置保留原文；返回（新列, 失败个数）。"""
    numeric = numeric_series(series)
    cleaned = series.astype("string").str.strip()
    non_empty = cleaned.notna() & cleaned.str.len().gt(0)
    ok = numeric.notna() & non_empty
    failed = non_empty & ~ok
    failed_count = int(failed.sum())
    if not failed_count:
        return _tidy_numbers(numeric), 0
    out = series.astype(object).copy()
    out[ok] = _tidy_object_numbers(numeric.astype(object))[ok]
    return out, failed_count


def convert_to_date(series: pd.Series) -> tuple[pd.Series, int]:
    """转日期：能认出来的变成日期，认不出的保留原文；返回（新列, 失败个数）。"""
    cleaned = series.astype("string").str.strip()
    try:
        parsed = pd.to_datetime(cleaned, errors="coerce", format="mixed")
    except (ValueError, TypeError, OverflowError):
        # 同一列里带时区和不带时区混着写时，pandas 3 会直接抛 ValueError（不猜时区，
        # 避免把日期算错）。这种情况整列保留原文，让用户自己改，别让页面崩掉。
        non_empty = cleaned.notna() & cleaned.str.len().gt(0)
        return series.copy(), int(non_empty.sum())
    if not pd.api.types.is_datetime64_any_dtype(parsed):
        # pandas 2.2 对混合时区不抛错，而是返回 object 列（未来版本才改成抛错）。
        # 同样当「整列没转成功、保留原文」处理，否则后面 .dt 访问器会抛原生 AttributeError。
        non_empty = cleaned.notna() & cleaned.str.len().gt(0)
        return series.copy(), int(non_empty.sum())
    if parsed.dt.tz is not None:
        # Excel 不支持带时区的日期；保留「当地时间的墙上时间」，只去掉时区标记
        parsed = parsed.dt.tz_localize(None)
    known = parsed.dropna()
    if len(known) and bool((known == known.dt.normalize()).all()):
        parsed = parsed.dt.date  # 全是整日的日期就只留日期，不显示 00:00:00
    non_empty = cleaned.notna() & cleaned.str.len().gt(0)
    ok = parsed.notna() & non_empty
    failed = non_empty & ~ok
    failed_count = int(failed.sum())
    if not failed_count:
        return parsed, 0
    out = series.astype(object).copy()
    out[ok] = parsed[ok]
    return out, failed_count


def _examples(series: pd.Series, mask: pd.Series, limit: int = 3) -> str:
    values = [str(value) for value in series[mask].head(limit).tolist()]
    return "、".join(f"「{value}」" for value in values)


# ---------------------------------------------------------------- 计算列


def _numeric_column(series: pd.Series, name: str) -> pd.Series:
    """计算列里引用到的列：一律按 float64 参与运算（和 Excel 一样用双精度）。

    为什么不用整数：numpy 的 int64 乘/幂遇到溢出会**静默回绕**（3**100000 得到
    -3665183052406099839），用户拿到的是个看不出错的假数字。float64 溢出是 inf，
    add_computed_column 会把 inf 留空并提示，错误可见、数据不被悄悄改。
    """
    from .rules import _is_text_column  # 局部导入：rules 不依赖 transform，避免循环

    if pd.api.types.is_numeric_dtype(series):
        return series.astype("float64")
    if _is_text_column(series):
        return numeric_series(series).astype("float64")
    raise TableError(f"列「{name}」不是数字（可能是日期或布尔值），不能参与计算。")


def _eval_node(node: ast.AST, df: pd.DataFrame, aliases: dict[str, str]) -> object:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise TableError(
            "计算表达式里只能用数字，不能用文字或其他常量；请只写列名、数字和四则运算。"
        )
    if isinstance(node, ast.Name):
        name = aliases.get(node.id, node.id)
        if name not in df.columns:
            options = "、".join(str(col) for col in df.columns[:8])
            raise TableError(
                f"表达式里的「{name}」不是当前的列名。"
                f"可用的列有：{options}{'…' if len(df.columns) > 8 else ''}"
                "；列名里有空格或括号时，用方括号包起来，例如 [单价（元）]*数量。"
            )
        return _numeric_column(df[name], name)
    if isinstance(node, ast.BinOp):
        func = _ALLOWED_BINOPS.get(type(node.op))
        if func is None:
            raise TableError(
                "计算表达式只支持 + - * / // % ** 这几种运算，"
                "不支持位运算、比较或字符串拼接。"
            )
        return func(
            _eval_node(node.left, df, aliases), _eval_node(node.right, df, aliases)
        )
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _eval_node(node.operand, df, aliases)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
            raise TableError(
                "计算表达式里只支持 round() 和 abs() 两个函数；"
                "其他函数、属性访问（点号）和下标都不允许执行。"
            )
        if node.keywords:
            raise TableError("计算表达式里的函数不支持关键字参数，例如 round(金额, 2) 这样写。")
        return _ALLOWED_FUNCS[node.func.id](
            *[_eval_node(arg, df, aliases) for arg in node.args]
        )
    raise TableError(
        "计算表达式里有不支持的写法；只允许列名、数字、+ - * / // % ** 和 round()/abs()，"
        "不能写代码、函数定义或访问属性。"
    )


def safe_eval(expr: str, df: pd.DataFrame) -> pd.Series:
    """安全求值一段算术表达式，返回一列结果（不执行任意代码）。

    只接受：数字、列名、+ - * / // % **、括号、round()/abs()。
    属性访问（`列.__class__`）、下标、lambda、推导式、赋值等一律拒绝——
    这是「简单计算列」而不是公式引擎，白名单比黑名单可靠。
    """
    text = (expr or "").strip()
    if not text:
        raise TableError("计算表达式是空的，请写成例如「金额=单价*数量」。")
    if len(text) > _MAX_EXPRESSION_CHARS:
        raise TableError(
            f"计算表达式太长（超过 {_MAX_EXPRESSION_CHARS} 个字符），请拆成多列计算。"
        )
    # [列名] /【列名】先换成安全的占位符，这样带空格、括号的列名也能参与计算
    aliases: dict[str, str] = {}

    def _alias(match: re.Match) -> str:
        name = (match.group(1) or match.group(2)).strip()
        placeholder = f"_列{len(aliases)}"
        aliases[placeholder] = name
        return placeholder

    prepared = _BRACKET_RE.sub(_alias, text)
    tree = None
    for candidate in (prepared, prepared.translate(_FULLWIDTH)):
        try:
            tree = ast.parse(candidate, mode="eval")
            break
        except SyntaxError:
            continue
    if tree is None:
        raise TableError(
            f"计算表达式「{text}」语法有误，请检查括号、运算符和列名；"
            "列名里有空格或括号时，用方括号包起来，例如 [单价（元）]*数量。"
        )
    value = _eval_node(tree.body, df, aliases)
    if isinstance(value, pd.Series):
        return value.reindex(df.index)
    return pd.Series(value, index=df.index)


def add_computed_column(
    df: pd.DataFrame, name: str, expr: str, notes: list[str]
) -> pd.DataFrame:
    """加一列计算结果；算不出来就记一条说明并跳过这一列，不影响其他列。"""
    if not name:
        notes.append(f"计算列里有空列名，已跳过（表达式「{expr}」）。")
        return df
    if name in df.columns:
        notes.append(f"计算列「{name}」和已有列重名，已跳过；请换个列名。")
        return df
    try:
        result = safe_eval(expr, df)
    except TableError as exc:
        notes.append(f"计算列「{name}」没有生成：{exc}")
        return df
    except Exception as exc:  # 除以 0、文本参与乘法等
        notes.append(
            f"计算列「{name}」没有生成：表达式「{expr}」算不出来（{exc}）；"
            "请确认参与计算的列都是数字。"
        )
        return df
    infinities = int((result == float("inf")).sum() + (result == float("-inf")).sum())
    if infinities:
        result = result.replace([float("inf"), float("-inf")], pd.NA)
        notes.append(
            f"计算列「{name}」有 {infinities} 个单元格的结果是无穷大（除以 0 或数字太大），"
            "已留空。"
        )
    result = _tidy_numbers(result)  # 4.0 → 4：浮点算完把整数值显示回整数
    out = df.copy()
    out[name] = result
    return out


# ---------------------------------------------------------------- 一起应用


def _conversion_failures(
    series: pd.Series, label: str, failed: int, hint: str
) -> str:
    """转换失败时的提示：给出个数和最多两个原文样例，用户知道该改哪一格。"""
    sample = _examples(series, series.map(lambda value: isinstance(value, str)), 2)
    return (
        f"「{series.name}」{label}时有 {failed} 个单元格没转成功，已保留原文"
        + (f"（例如{sample}）" if sample else "")
        + hint
    )


def apply_transforms(df: pd.DataFrame, ops: ColumnOps) -> tuple[pd.DataFrame, list[str]]:
    """按固定顺序应用列变换；不存在的列名只提示、不报错（批量时各表列名可能不同）。

    顺序：删列 → 类型转换（用**原列名**）→ 重命名 → 计算列（用**新列名**）→ 列顺序（新列名）。
    这样界面上「要转换的列」下拉框列出的就是原列名，不会因为刚改过名而对不上。
    """
    if ops.is_empty():
        return df, []
    notes: list[str] = []
    out = df

    # 1. 删列（原列名）
    if ops.drop:
        existing = [name for name in ops.drop if name in out.columns]
        missing = [name for name in ops.drop if name not in out.columns]
        if existing:
            out = out.drop(columns=existing)
        if missing:
            notes.append(f"要删除的列不存在，已忽略：{'、'.join(missing)}。")

    # 2. 类型转换（原列名；失败的位置保留原文并提示）
    for column in ops.to_number:
        if column not in out.columns:
            notes.append(f"要转数字的列「{column}」不存在，已忽略。")
            continue
        converted, failed = convert_to_number(out[column])
        out = out.copy()
        out[column] = converted
        if failed:
            notes.append(
                _conversion_failures(
                    out[column], "转数字", failed, "；需要的话请手工修正这些格子。"
                )
            )
    for column in ops.to_date:
        if column not in out.columns:
            notes.append(f"要转日期的列「{column}」不存在，已忽略。")
            continue
        converted, failed = convert_to_date(out[column])
        out = out.copy()
        out[column] = converted
        if failed:
            notes.append(
                _conversion_failures(
                    out[column],
                    "转日期",
                    failed,
                    "；常见写法如 2024-05-01、2024/5/1。",
                )
            )

    # 3. 重命名（重名或撞已有列名时跳过，避免两列同名）
    if ops.rename:
        mapping: dict[str, str] = {}
        for old, new in ops.rename:
            if old not in out.columns:
                notes.append(f"要重命名的列「{old}」不存在，已忽略。")
                continue
            if new in out.columns and new != old:
                notes.append(f"列名「{new}」已经存在，没有把「{old}」改成它。")
                continue
            mapping[old] = new
        if mapping:
            out = out.rename(columns=mapping)

    # 4. 计算列（用重命名后的列名）
    for name, expr in ops.computed:
        out = add_computed_column(out, name, expr, notes)

    # 5. 列顺序：点名的列在前（保持用户给的顺序），其余按原顺序跟在后面
    if ops.order:
        wanted = [name for name in ops.order if name in out.columns]
        missing = [name for name in ops.order if name not in out.columns]
        if wanted:
            rest = [column for column in out.columns if column not in wanted]
            out = out[wanted + rest]
        if missing:
            notes.append(f"要调整顺序的列不存在，已忽略：{'、'.join(missing)}。")
    return out, notes


def clean_table(
    df: pd.DataFrame,
    options: CleanOptions,
    *,
    sample: int = 0,
    progress: Callable[[int, int], None] | None = None,
    deadline: Deadline | None = None,
) -> CleanReport:
    """行清洗 + 列变换一次做完，返回带说明的 CleanReport（界面和批量共用）。"""
    report = clean_report(
        df,
        drop_duplicates=options.drop_duplicates,
        dedupe_on=list(options.dedupe_on) or None,
        drop_missing_in=list(options.drop_missing_in) or None,
        sample=sample,
        progress=progress,
        deadline=deadline,
    )
    if options.columns.is_empty():
        return report
    transformed, notes = apply_transforms(report.df, options.columns)
    if deadline is not None:
        deadline.check("列变换")
    return CleanReport(transformed, report.steps, report.removed, tuple(notes))
