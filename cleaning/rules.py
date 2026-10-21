"""判定规则与行清洗：缺失值、重复行、可疑格式、公式判断、分块清洗。

判定口径在界面、导出防护和 README 里引用同一份文字，避免说法不一致：
- 缺失值 = 空白单元格（NaN/None，或只有空文本）。数字 0 和文本 "0" 是有效数据。
- 重复行 = 所有列的值完全相同的行；清洗时保留第一次出现的那一行。
- 可疑数据只是格式提示，工具不会自动修改这些单元格。
"""

from __future__ import annotations

import re
from typing import Callable, NamedTuple

import pandas as pd

from .errors import Deadline
from .limits import CHUNK_ROWS, CHUNK_THRESHOLD_ROWS

DUPLICATE_RULE = "所有列的值都完全相同的行才算重复行，清洗时保留第一次出现的那一行。"
MISSING_RULE = (
    "空白单元格（没有内容，或只有空文本）算缺失值；"
    '数字 0、文本 "0"、"NA" 这类填了内容的单元格都算有效数据；'
    "只含空格的单元格不算缺失，但会在可疑数据里提示。"
)
SPACE_ISSUE = "首尾有空格"
FORMULA_ISSUE = "疑似公式/注入开头"  # 界面/测试/README 引用同一份文案
# 单元格里可能藏着零宽字符/BOM（Excel 导出常见），判断前先剥掉
_INVISIBLE = "\ufeff\u200b\u200c\u200d\u200e\u200f\u2060"
_PLAIN_NUMBER_RE = re.compile(
    r"[+-]?(?:(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]*)?|\.[0-9]+)"
    r"(?:[eE][+-]?[0-9]+)?%?"  # -3%、-3.5% 在 Excel 里是数字，不能当公式加前缀
)
# infer_dtype 明确不是字符串的种类，命中就跳过该列
_NON_TEXT_INFER_KINDS = frozenset(
    {
        "integer",
        "floating",
        "decimal",
        "complex",
        "boolean",
        "datetime",
        "datetime64",
        "date",
        "time",
        "timedelta",
        "timedelta64",
        "empty",
    }
)

ProgressCallback = Callable[[int, int], None]  # (已处理行数, 总行数)


class CleanReport(NamedTuple):
    df: pd.DataFrame
    steps: dict[str, int]  # 已执行的每一步删掉多少行（按执行顺序）
    removed: pd.DataFrame  # 被删掉的行样例（含「删除原因」列，最多 sample 行）
    notes: tuple[str, ...] = ()  # 列变换等步骤的说明（v2.0.0）


# ---------------------------------------------------------------- 检查


def missing_mask(df: pd.DataFrame) -> pd.DataFrame:
    """逐单元格判断是否缺失：空白（NaN/None）或空文本都算缺失。"""
    return df.isna() | df.eq("")


def find_missing(df: pd.DataFrame) -> pd.Series:
    """返回每一列的空值数量。"""
    return missing_mask(df).sum()


def _is_text_column(series: pd.Series) -> bool:
    """是否按文本处理；pandas 2/3 对 object/StringDtype 的判定不一样。

    非文本 dtype（数字/日期/布尔等）直接排除，避免逐个值扫描大表；
    dtype 说不准的（Categorical 等）再按整列的实际值判断。
    """
    if pd.api.types.is_string_dtype(series):
        return True
    if pd.api.types.is_numeric_dtype(series) or pd.api.types.is_datetime64_any_dtype(series):
        return False
    if pd.api.types.is_bool_dtype(series) or pd.api.types.is_timedelta64_dtype(series):
        return False
    if pd.api.types.is_object_dtype(series):
        # object 列可能是数字/日期/混合：infer_dtype 是 C 实现的整列判断（50 万行约 6ms），
        # 比逐值 isinstance 快一个数量级，也不会像抽样那样漏掉列尾的字符串。
        kind = pd.api.types.infer_dtype(series, skipna=True)
        if kind == "string":
            return True
        if kind in _NON_TEXT_INFER_KINDS:
            return False
        return any(isinstance(value, str) for value in series.dropna())
    # Categorical 等扩展 dtype：逐值判断，比维护类型白名单可靠
    return any(isinstance(value, str) for value in series.dropna())


def find_suspicious(df: pd.DataFrame) -> dict[str, dict[str, int]]:
    """检查常见格式问题：首尾空格、疑似公式/注入开头（判定规则同导出防护）。

    只做提示，不修改数据。返回 {列名: {问题: 单元格数}}，没有问题的列不出现。
    """
    found: dict[str, dict[str, int]] = {}
    for col in df.columns:
        series = df[col]
        if not _is_text_column(series):
            continue
        text = series.dropna().astype(str)
        issues: dict[str, int] = {}
        spaced = int((text != text.str.strip()).sum())
        # 与导出防护同一个判断：= @ + - 开头（纯数字除外）、含前置空白/BOM/零宽字符都算
        # 空列 map 出来是 str dtype、sum() 返回 ''，必须显式转 bool 再计数
        formulas = int(text.map(_looks_like_formula).astype("bool").sum())
        if spaced:
            issues[SPACE_ISSUE] = spaced
        if formulas:
            issues[FORMULA_ISSUE] = formulas
        if issues:
            found[str(col)] = issues
    return found


def summarize(df: pd.DataFrame) -> dict[str, int]:
    """清洗前后对比用的统计。"""
    return {
        "行数": int(df.shape[0]),
        "列数": int(df.shape[1]),
        "空值单元格": int(missing_mask(df).to_numpy().sum()),
        "完全重复行": int(df.duplicated().sum()),
    }


# ---------------------------------------------------------------- 清洗


def clean_data(
    df: pd.DataFrame,
    drop_duplicates: bool = False,
    dedupe_on: list[str] | None = None,
    drop_missing_in: list[str] | None = None,
) -> pd.DataFrame:
    """按用户的选择清洗；所有选项默认都不做，不修改传入的 df。

    顺序固定：先去重复 → 再按列去重 → 再删空值行（界面和 README 说的是同一套顺序）。
    drop_duplicates=True：删除所有列都完全相同的行，保留第一次出现的。
    dedupe_on=[列名...]：只按这些列判重，保留第一次出现的那行；不存在的列名忽略。
    drop_missing_in=[列名...]：所选列里任意一列是空白的行，整行删除。
    """
    return clean_report(df, drop_duplicates, dedupe_on, drop_missing_in).df


def _drop_duplicate_rows(
    frame: pd.DataFrame,
    subset: list[str] | None,
    label: str,
    progress: ProgressCallback | None,
    deadline: Deadline | None,
) -> pd.DataFrame:
    """去重：pandas 一次精确比较。

    分块模式在这里不分块——跨块的「第一次出现」要求把所有键留在内存里，
    比 pandas 的哈希实现更占内存也更慢；去重本身是 C 实现，几十万行约 1 秒。
    分块真正省的是「按列删空值行」那种逐行扫描（见 _drop_missing_rows）。
    """
    if deadline is not None:
        deadline.check(label)
    out = frame.drop_duplicates(subset=subset, keep="first")
    if progress is not None:
        progress(len(frame), len(frame))
    if deadline is not None:
        deadline.check(label)
    return out


def _drop_missing_rows(
    frame: pd.DataFrame,
    columns: list[str],
    progress: ProgressCallback | None,
    deadline: Deadline | None,
    chunk_rows: int,
) -> pd.DataFrame:
    """删除所选列中有空值的行；大表按块处理，块之间报进度、查超时。"""
    mask = missing_mask(frame[columns]).any(axis=1)
    total = len(frame)
    if chunk_rows <= 0 or total <= chunk_rows:
        if deadline is not None:
            deadline.check("删除空值行")
        return frame.loc[~mask]
    values = mask.to_numpy(dtype=bool)
    kept: list[pd.DataFrame] = []
    for start in range(0, total, chunk_rows):
        if deadline is not None:
            deadline.check("分块删除空值行")
        part = values[start : start + chunk_rows]
        # 用 iloc 按位置切、按位置的布尔数组筛，避免 pandas 去对齐两边不同的索引
        kept.append(frame.iloc[start : start + chunk_rows].iloc[~part])
        if progress is not None:
            progress(min(start + chunk_rows, total), total)
    return pd.concat(kept) if kept else frame.head(0)


def clean_report(
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
    """清洗 + 每一步删了多少行；sample>0 时再给出被删行的样例（最多 sample 行）。

    清洗只删行、不改单元格（公式防护只在导出时加前缀），所以「被删掉的行」可以用
    原始行号精确定位；sample=0 时只算计数，不多占内存。

    progress/ deadline 用于大文件：超过 CHUNK_THRESHOLD_ROWS 行时自动按 CHUNK_ROWS
    分块处理「删空值行」，每块回调一次 progress(已处理, 总数) 并检查超时。
    chunk_rows 显式传入时强制分块（测试用小块验证分块结果与整表一致）。
    """
    out = df.reset_index(drop=True)  # 原始行号唯一，后面用它定位被删掉的行
    steps: dict[str, int] = {}
    samples: list[pd.DataFrame] = []
    left = sample
    if chunk_rows is None:
        chunk_rows = CHUNK_ROWS if len(out) > CHUNK_THRESHOLD_ROWS else 0
    if progress is not None:
        progress(0, len(out))

    def step(label: str, new_out: pd.DataFrame) -> None:
        nonlocal out, left
        removed_n = len(out) - len(new_out)
        steps[label] = removed_n
        if left > 0 and removed_n:
            gone = out.loc[~out.index.isin(new_out.index)].head(left)
            samples.append(gone.assign(删除原因=label))
            left -= len(samples[-1])
        out = new_out

    if drop_duplicates:
        step(
            "删除完全重复的行",
            _drop_duplicate_rows(out, None, "删除完全重复的行", progress, deadline),
        )
    dedupe_cols = [col for col in (dedupe_on or []) if col in out.columns]
    if dedupe_cols:
        step(
            "按指定列去重",
            _drop_duplicate_rows(out, dedupe_cols, "按指定列去重", progress, deadline),
        )
    cols = [col for col in (drop_missing_in or []) if col in out.columns]
    if cols:
        step(
            "删除指定列中有空值的行",
            _drop_missing_rows(out, cols, progress, deadline, chunk_rows),
        )
    if progress is not None and not any(steps):
        progress(len(out), len(out))

    removed = pd.concat(samples) if samples else df.head(0).assign(删除原因="")
    return CleanReport(out.reset_index(drop=True), steps, removed)


# ---------------------------------------------------------------- 公式判断
# 「可疑数据」提示和 CSV 导出防护共用这一个判断，保证口径一致。


def _looks_like_formula(value: object) -> bool:
    """是否可能被电子表格当公式执行。

    先剥掉 BOM/零宽字符和前置空白，再看开头字符；纯数字（含负数、科学计数法、
    千分位）不算公式，避免把 -5 误判。
    """
    if not isinstance(value, str) or not value:
        return False
    probe = value.lstrip(_INVISIBLE + " \t\r\n")
    if not probe:
        return False
    if probe[0] in "=@":
        return True
    if probe[0] in "+-":
        return not _PLAIN_NUMBER_RE.fullmatch(probe.strip())
    return False


# ---------------------------------------------------------------- 样例数据


def make_sample_df() -> pd.DataFrame:
    """虚构样例数据：含 1 行完全重复、3 个空值、1 个 0、2 处可疑格式。"""
    return pd.DataFrame(
        {
            "订单号": ["A001", "A002", "A003", "A003", "A005", "A006"],
            "客户名称": ["晨光小吃店", "星语文具", "山泉花店", "山泉花店", "远航网店", None],
            "金额": [128.0, 0.0, 56.5, 56.5, 300.0, 88.0],
            "备注": ["送货前电话", None, "=A1+B1", "=A1+B1", " 加急", None],
            "日期": [
                "2024-05-01",
                "2024-05-02",
                "2024-05-03",
                "2024-05-03",
                "2024-05-04",
                "2024-05-05",
            ],
        }
    )
