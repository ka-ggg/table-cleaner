"""批量处理：多张工作表 / 多个文件，各自清洗后打成一个 ZIP（v2.0.0）。

流程和单文件完全一致（load → clean_table → export），所以两边的规则不会走偏；
区别只有两个：每项单独 try/except（一张表坏了不影响其他表），以及结果打包成 ZIP。
"""

from __future__ import annotations

from typing import Callable, NamedTuple, Sequence

from .errors import Deadline, TableError
from .export import build_zip, entry_name, to_csv_bytes, to_xlsx_bytes
from .io import load_table
from .limits import Limits
from .transform import CleanOptions, clean_table

ProgressCallback = Callable[[int, int], None]  # (已完成项数, 总项数)


class BatchJob(NamedTuple):
    """一项待处理的数据：文件名 + 工作表名（CSV 为 None）+ 原始字节。"""

    source: str
    sheet: str | None
    data: bytes


class BatchItem(NamedTuple):
    """一项的处理结果；error 非空表示这一项失败（其他项照常）。"""

    entry: str  # ZIP 里的文件名；失败时是空串
    display: str  # 给用户看的来源：「订单.xlsx · 第二张」
    rows_before: int
    rows_after: int
    error: str | None = None
    notes: tuple[str, ...] = ()


class BatchReport(NamedTuple):
    zip_bytes: bytes | None  # 全部失败时是 None
    items: list[BatchItem]
    exported: int  # 成功打包的文件数


def run_batch(
    jobs: Sequence[BatchJob],
    options: CleanOptions,
    *,
    export_format: str = "csv",
    number_mode: str = "text",
    formula_mode: str = "quote",
    timeout: float | None = None,
    limits: Limits | None = None,
    progress: ProgressCallback | None = None,
) -> BatchReport:
    """逐项读取 → 清洗 → 导出 → 打包；单项失败只记原因，不中断整批。

    timeout 是**每一项**的硬超时秒数（None = 不限）；返回的 notes 只在批次层面
    汇总（每项的转换说明在 BatchItem.notes 里）。
    """
    fmt = str(export_format or "csv").strip().lower()
    if fmt not in ("csv", "xlsx"):
        raise TableError(f"批量导出格式「{export_format}」不认识；可选：csv、xlsx。")
    if not jobs:
        return BatchReport(None, [], 0)

    files: list[tuple[str, bytes]] = []
    items: list[BatchItem] = []
    for index, job in enumerate(jobs, 1):
        display = job.source if job.sheet is None else f"{job.source} · {job.sheet}"
        if progress is not None:
            progress(index - 1, len(jobs))
        try:
            loaded = load_table(
                job.source,
                job.data,
                sheet=job.sheet,
                header_row=options.header_row,
                timeout=timeout,
                limits=limits,
            )
            report = clean_table(loaded.df, options)
            deadline = Deadline(timeout)
            deadline.check("导出这一项")
            if fmt == "xlsx":
                content = to_xlsx_bytes(report.df, number_mode=number_mode)
            else:
                content = to_csv_bytes(report.df, neutralize_formulas=formula_mode)
            name = entry_name(job.source, fmt, job.sheet)
            files.append((name, content))
            items.append(
                BatchItem(
                    name,
                    display,
                    len(loaded.df),
                    len(report.df),
                    None,
                    tuple(report.notes),
                )
            )
        except TableError as exc:
            items.append(BatchItem("", display, 0, 0, str(exc)))
        except Exception:  # 单项的意外异常也不能拖垮整批
            items.append(
                BatchItem(
                    "",
                    display,
                    0,
                    0,
                    "这一项处理失败（文件可能过大或格式异常）；请单独上传它再试。",
                )
            )
        if progress is not None:
            progress(index, len(jobs))

    zip_bytes = build_zip(files) if files else None
    return BatchReport(zip_bytes, items, len(files))
