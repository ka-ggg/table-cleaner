"""编程式清洗入口：把「读文件 → 清洗 → 导出 → 生成报告」收敛成纯函数。

界面、CLI 和 HTTP API 都只需要拼好 ``CleanOptions`` 再调用这里，不用各写一套
读取/清洗/导出流程。所有函数只依赖入参，不读界面状态、不写全局变量。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .errors import Deadline, TableError
from .export import timestamped_name, to_csv_bytes, to_xlsx_bytes
from .io import load_table
from .limits import Limits
from .rules import find_missing, find_suspicious, summarize
from .templates import normalize_rules
from .transform import CleanOptions, ColumnOps, clean_table


@dataclass(frozen=True)
class CleanResult:
    """一次完整清洗的结果，供 CLI/API/调用方写文件和展示报告。"""

    source: str  # 输入文件名（报告里给用户看）
    filename: str  # 建议的输出文件名（带原文件名与时间戳）
    data: bytes  # 导出的文件内容
    export_format: str  # csv / xlsx
    rows_before: int
    rows_after: int
    steps: dict[str, int]  # 每一步删掉多少行
    notes: tuple[str, ...]  # 列变换说明
    load_notes: tuple[str, ...]  # 读取阶段的非阻断说明
    sheet_name: str | None = None

    @property
    def report_text(self) -> str:
        """中文报告文本（终端/Markdown 均可读）。"""
        return render_report(self)


@dataclass(frozen=True)
class InspectResult:
    """只检查不清洗的结果。"""

    filename: str
    rows: int
    cols: int
    columns: tuple[str, ...]
    missing: dict[str, int]
    duplicate_rows: int
    suspicious: dict[str, dict[str, int]]
    load_notes: tuple[str, ...]
    sheet_name: str | None = None

    @property
    def report_text(self) -> str:
        """中文检查报告文本（终端/Markdown 均可读）。"""
        return render_report(self)


def options_from_rules(rules: Mapping[str, object]) -> CleanOptions:
    """把模板/规则字典转成唯一的规则载体 ``CleanOptions``。

    规则先经 ``normalize_rules`` 做与原模板读取相同的字段校验，因此 CLI、API 和界面
    不会出现「模板能存但入口读法不同」的两套语义。
    """
    normalized = normalize_rules(dict(rules))
    return CleanOptions(
        header_row=normalized["表头行"],
        drop_duplicates=normalized["删除完全重复的行"],
        dedupe_on=tuple(normalized["按列去重"]),
        drop_missing_in=tuple(normalized["删除空值行"]),
        columns=ColumnOps(
            rename=tuple(tuple(pair) for pair in normalized["列重命名"]),
            drop=tuple(normalized["删除列"]),
            order=tuple(normalized["列顺序"]),
            to_number=tuple(normalized["转数字"]),
            to_date=tuple(normalized["转日期"]),
            computed=tuple(tuple(pair) for pair in normalized["计算列"]),
        ),
    )


def clean_bytes(
    filename: str,
    data: bytes,
    options: CleanOptions,
    *,
    export_format: str = "csv",
    formula_mode: str = "quote",
    number_mode: str = "text",
    timeout: float | None = None,
    limits: Limits | None = None,
    engine: str = "pandas",
) -> CleanResult:
    """清洗内存中的文件字节并导出，返回结果字节与报告素材。

    参数与界面/批量共用同一套语义：``options`` 是唯一规则载体，``limits`` 是统一
    上限配置；读取、清洗、导出任一步的用户可预见错误都以中文 ``TableError`` 抛出。
    ``engine`` 是 v2.4.0 的内部预览关键字：默认 ``"pandas"``，显式 ``"polars"``
    才走可选引擎；CLI / HTTP API / 界面不传该参数。
    """
    fmt = str(export_format or "csv").strip().lower()
    if fmt not in ("csv", "xlsx"):
        raise TableError(f"导出格式「{export_format}」不认识；可选：csv、xlsx。")
    loaded = load_table(
        filename,
        data,
        header_row=options.header_row,
        timeout=timeout,
        limits=limits,
    )
    report = clean_table(loaded.df, options, deadline=Deadline(timeout), engine=engine)
    if fmt == "xlsx":
        content = to_xlsx_bytes(report.df, number_mode=number_mode)
    else:
        content = to_csv_bytes(report.df, neutralize_formulas=formula_mode)
    return CleanResult(
        source=filename,
        filename=timestamped_name(filename, fmt),
        data=content,
        export_format=fmt,
        rows_before=len(loaded.df),
        rows_after=len(report.df),
        steps=dict(report.steps),
        notes=tuple(report.notes),
        load_notes=tuple(loaded.notes),
        sheet_name=loaded.sheet_name,
    )


def inspect_bytes(
    filename: str,
    data: bytes,
    *,
    header_row: int = 1,
    timeout: float | None = None,
    limits: Limits | None = None,
) -> InspectResult:
    """只检查不清洗：返回行列、空值、重复行与可疑格式，不改动任何数据。"""
    loaded = load_table(filename, data, header_row=header_row, timeout=timeout, limits=limits)
    counts = summarize(loaded.df)
    missing = {str(column): int(count) for column, count in find_missing(loaded.df).items()}
    suspicious = {
        str(column): {str(issue): int(count) for issue, count in issues.items()}
        for column, issues in find_suspicious(loaded.df).items()
    }
    return InspectResult(
        filename=filename,
        rows=int(counts["行数"]),
        cols=int(counts["列数"]),
        columns=tuple(str(column) for column in loaded.df.columns),
        missing=missing,
        duplicate_rows=int(counts["完全重复行"]),
        suspicious=suspicious,
        load_notes=tuple(loaded.notes),
        sheet_name=loaded.sheet_name,
    )


def _render_clean_report(result: CleanResult) -> str:
    lines = [
        "# 清洗报告",
        "",
        f"- 来源：{result.source}",
        f"- 建议输出文件名：{result.filename}",
        f"- 导出格式：{result.export_format}",
    ]
    if result.sheet_name:
        lines.append(f"- 工作表：{result.sheet_name}")
    lines.extend(
        [
            f"- 清洗前：{result.rows_before:,} 行",
            f"- 清洗后：{result.rows_after:,} 行",
            f"- 共删除：{max(result.rows_before - result.rows_after, 0):,} 行",
        ]
    )
    lines.extend(["", "## 清洗步骤"])
    if result.steps:
        for label, removed in result.steps.items():
            lines.append(f"- {label}：{removed:,} 行")
    else:
        lines.append("- 没有执行删行规则")
    if result.load_notes:
        lines.extend(["", "## 读取提示"])
        lines.extend(f"- {note}" for note in result.load_notes)
    if result.notes:
        lines.extend(["", "## 列变换提示"])
        lines.extend(f"- {note}" for note in result.notes)
    return "\n".join(lines) + "\n"


def _render_inspect_report(result: InspectResult) -> str:
    lines = [
        "# 检查报告",
        "",
        f"- 文件：{result.filename}",
    ]
    if result.sheet_name:
        lines.append(f"- 工作表：{result.sheet_name}")
    lines.extend(
        [
            f"- 行数：{result.rows:,}",
            f"- 列数：{result.cols:,}",
            f"- 列名：{'、'.join(result.columns)}",
            "",
            "## 空值",
        ]
    )
    missing_items = [(column, count) for column, count in result.missing.items() if count]
    if missing_items:
        lines.extend(f"- {column}：{count:,} 个空值单元格" for column, count in missing_items)
    else:
        lines.append("- 没有空值单元格")
    lines.extend(["", "## 重复行", f"- 完全重复行：{result.duplicate_rows:,} 行"])
    lines.extend(["", "## 可疑格式"])
    if result.suspicious:
        for column, issues in result.suspicious.items():
            detail = "；".join(f"{issue} {count:,} 个" for issue, count in issues.items())
            lines.append(f"- {column}：{detail}")
    else:
        lines.append("- 没有发现首尾空格或疑似公式开头")
    if result.load_notes:
        lines.extend(["", "## 读取提示"])
        lines.extend(f"- {note}" for note in result.load_notes)
    return "\n".join(lines) + "\n"


def render_report(result: CleanResult | InspectResult) -> str:
    """把清洗/检查结果渲染成中文报告文本。"""
    if isinstance(result, CleanResult):
        return _render_clean_report(result)
    return _render_inspect_report(result)
