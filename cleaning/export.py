"""导出：CSV / XLSX / ZIP，公式注入的三种防护模式，编号字段的数字开关。

CSV 没有单元格类型，用 Excel/WPS 打开时 `=`、`+`、`-`、`@`、制表符开头的文本
可能被当成公式执行。这里给三种模式，用户自己权衡兼容性和显示效果：
- quote（默认）：加英文单引号，兼容性最好；
- zwsp：加零宽空格，单元格里看不见，但文本比较/搜索会受影响；
- off：不加，按原样导出。
XLSX 不需要开关：`=` 开头的文字一律强制按文本写入。
"""

from __future__ import annotations

import re
import zipfile
from datetime import datetime
from io import BytesIO
from typing import Sequence

import pandas as pd

from .errors import TableError
from .limits import EXCEL_MAX_COLS, EXCEL_MAX_ROWS
from .rules import _is_text_column, _looks_like_formula
from .transform import numeric_series

FORMULA_MODES = ("quote", "zwsp", "off")
# 界面上给用户看的中文说明；键和参数值一致，避免界面/API 两套叫法
FORMULA_MODE_LABELS = {
    "quote": "加英文单引号（兼容性最好）",
    "zwsp": "加零宽空格（显示更干净）",
    "off": "关闭防护（完全按原样）",
}
_PREFIXES = {"quote": "'", "zwsp": "\u200b"}
NUMBER_MODES = ("text", "number")
NUMBER_MODE_LABELS = {
    "text": "强制文本（00123 保持 00123）",
    "number": "尝试转数字（00123 → 123，转不了的保留文本）",
}
_UNSAFE_NAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _normalize_mode(mode: bool | str | None) -> str:
    if mode is True or mode is None:
        return "quote"
    if mode is False:
        return "off"
    text = str(mode).strip().lower()
    if text in FORMULA_MODES:
        return text
    raise TableError(f"导出防护模式「{mode}」不认识；可选：quote（单引号）、zwsp（零宽空格）、off（关闭）。")


def _shield_cell(value: object, mode: str) -> object:
    """给可能被电子表格当公式执行的文本加前缀；纯数字不处理（避免破坏负数）。"""
    if _looks_like_formula(value):
        return _PREFIXES[mode] + str(value)
    return value


def shield_csv_formulas(df: pd.DataFrame, mode: bool | str = "quote") -> pd.DataFrame:
    """返回副本：CSV 导出前给危险开头的文本/列名加前缀，防止公式注入。

    mode：quote（单引号，默认）、zwsp（零宽空格）、off（不处理，返回副本）。
    """
    normalized = _normalize_mode(mode)
    out = df.copy()
    if normalized == "off":
        return out
    out.columns = [_shield_cell(col, normalized) for col in out.columns]
    for col in out.columns:
        series = out[col]
        if not _is_text_column(series):
            continue
        out[col] = series.map(lambda value: _shield_cell(value, normalized), na_action="ignore")
    return out


def to_csv_bytes(df: pd.DataFrame, neutralize_formulas: bool | str = True) -> bytes:
    """导出 CSV；带 BOM，Excel 直接打开中文不乱码。

    neutralize_formulas 兼容旧值：True = quote（加英文单引号）、False = off，
    也可以直接传 "quote" / "zwsp" / "off"。
    """
    out = shield_csv_formulas(df, neutralize_formulas)
    return out.to_csv(index=False).encode("utf-8-sig")


def _has_formula_text(df: pd.DataFrame) -> bool:
    """导出后会不会出现“被 Excel 当成公式”的单元格。"""
    if any(isinstance(col, str) and col.startswith("=") for col in df.columns):
        return True
    for col in df.columns:
        series = df[col]
        if _is_text_column(series) and series.dropna().astype(str).str.startswith("=").any():
            return True
    return False


def numbers_where_possible(df: pd.DataFrame) -> pd.DataFrame:
    """把「看起来是数字」的文本单元格变成真正的数字，转不了的保持原文。

    只动文本列；日期列、数字列原样保留。00123 → 123、1,000 → 1000，
    A002 这种保持文本。这样导出的 XLSX 能在 Excel 里直接算，又不会把编号吃成空值。
    """
    out = df.copy()
    for column in df.columns:
        series = df[column]
        if not _is_text_column(series):
            continue
        numeric = numeric_series(series)
        cleaned = series.astype("string").str.strip()
        non_empty = cleaned.notna() & cleaned.str.len().gt(0)
        converted = numeric.notna() & non_empty
        if not bool(converted.any()):
            continue
        tidy = numeric.astype(object).map(
            lambda value: int(value) if isinstance(value, float) and value.is_integer() else value
        )
        out[column] = series.astype(object).where(~converted, tidy)
    return out


def to_xlsx_bytes(df: pd.DataFrame, number_mode: str = "text") -> bytes:
    """导出 XLSX；超出 Excel 上限时给出明确提示，而不是抛原始异常。

    number_mode：
    - "text"（默认）：文本列强制按文本写入，`00123` 这类编号不会被吃掉；
    - "number"：尽量把数字写法的文本转成数字（Excel 里可直接计算），转不了的保留文本。
    """
    mode = str(number_mode or "text").strip().lower()
    if mode not in NUMBER_MODES:
        raise TableError(f"编号导出模式「{number_mode}」不认识；可选：text（强制文本）、number（尝试转数字）。")
    if len(df) > EXCEL_MAX_ROWS - 1 or df.shape[1] > EXCEL_MAX_COLS:
        raise TableError("数据超出了 Excel 的上限（1048576 行 / 16384 列，含表头），请改用 CSV 下载。")
    out = numbers_where_possible(df) if mode == "number" else df
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        out.to_excel(writer, index=False)
        if _has_formula_text(out):
            # openpyxl 默认把 "=" 开头的文字写成公式。这里在保存前改回文本，
            # 否则导出的文件打开后会被 Excel 执行，等于悄悄改了数据。
            for sheet in writer.book.worksheets:
                for row in sheet.iter_rows():
                    for cell in row:
                        if cell.data_type == "f":
                            cell.data_type = "s"
    return buffer.getvalue()


# ---------------------------------------------------------------- 文件名与打包


def safe_stem(name: str) -> str:
    """去掉路径和文件系统不认的字符，留一个能安全放进下载名/ZIP 条目的主名。"""
    stem = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    stem = _UNSAFE_NAME_RE.sub("_", stem).strip(" .")
    return stem[:60] or "表格"


def timestamped_name(original: str, ext: str, prefix: str = "清洗后", now: datetime | None = None) -> str:
    """下载文件名：前缀 + 原文件名 + 时间戳，避免连续导出互相覆盖。"""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}_{safe_stem(original)}_{stamp}.{ext.lstrip('.')}"


def unique_names(names: Sequence[str]) -> list[str]:
    """同名条目加序号（ZIP 里同名会互相覆盖，下载到同一目录也会）。"""
    used: set[str] = set()
    result: list[str] = []
    for name in names:
        stem, dot, ext = name.rpartition(".")
        if not dot:
            stem, ext = name, ""
        candidate = name
        index = 2
        while candidate in used:
            candidate = f"{stem} ({index})" + (f".{ext}" if ext else "")
            index += 1
        used.add(candidate)
        result.append(candidate)
    return result


def entry_name(original: str, ext: str, sheet: str | None = None) -> str:
    """ZIP 里单个文件的条目名：原文件名（可选工作表名）+ 扩展名。"""
    stem = safe_stem(original)
    if sheet:
        sheet_part = _UNSAFE_NAME_RE.sub("_", sheet).strip() or "工作表"
        stem = f"{stem}_{sheet_part}"
    return f"{stem}.{ext.lstrip('.')}"


def build_zip(files: Sequence[tuple[str, bytes]]) -> bytes:
    """把若干（文件名, 内容）打成一个 ZIP；重名自动加序号。"""
    if not files:
        raise TableError("没有可打包的文件。")
    names = unique_names([name for name, _ in files])
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, (_, content) in zip(names, files):
            archive.writestr(name, content)
    return buffer.getvalue()
