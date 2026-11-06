"""读取 CSV / XLSX：格式与大小校验、编码与分隔符识别、表头行、预检、表头规范化。"""

from __future__ import annotations

import csv
import re
import zipfile
from io import BytesIO, StringIO
from typing import NamedTuple

import pandas as pd

from .errors import Deadline, TableError
from .limits import Limits, check_shape_limits, current_limits
from .xlsx_scan import (
    check_xlsx_uncompressed_size,
    count_data_merges,
    pick_xlsx_sheet,
    scan_part,
)

ALLOWED_EXTS = (".csv", ".xlsx")
# 支持的 CSV 分隔符（Excel 另存为「CSV UTF-8」永远是逗号，其他软件/导出工具常用另外三种）
CSV_DELIMITERS = {",": "逗号", ";": "分号", "\t": "制表符", "|": "竖线"}
# 合并单元格/空表头：pandas 会给缺失表头起 "Unnamed: 3" 这类名字，两种都当空表头处理
_UNNAMED_RE = re.compile(r"Unnamed: \d+")


class LoadedTable(NamedTuple):
    df: pd.DataFrame
    notes: list[str]  # 需要告诉用户、但不阻断流程的说明
    sheet_name: str | None = None  # 实际读取的工作表名；CSV 没有工作表，是 None
    sheet_names: tuple[str, ...] = ()  # 工作簿里的全部工作表名（工作簿顺序）；CSV 是空元组


# ---------------------------------------------------------------- 入口


def load_table(
    filename: str,
    data: bytes,
    sheet: str | None = None,
    header_row: int = 1,
    timeout: float | None = None,
    limits: Limits | None = None,
) -> LoadedTable:
    """按扩展名读取 CSV/XLSX。

    sheet 只在 XLSX 里有用：给表名就读这张，不给就自动取第一张有数据的表。
    header_row 是「表头在第几行」（从 1 开始），默认第 1 行。
    timeout 是协作式硬超时秒数（None = 不限），超时抛中文 TableError。
    文件类型、大小、空文件、损坏文件都在这里拦截，错误信息可以直接展示给用户。
    """
    lim = limits or current_limits()
    name = (filename or "").lower()
    if not name.endswith(ALLOWED_EXTS):
        raise TableError("只支持 .csv 和 .xlsx 文件，请先用 Excel 另存为这两种格式。")
    if not data:
        raise TableError("文件是空的（0 字节），请确认有没有选错文件。")
    if len(data) > lim.max_upload_bytes:
        size_mb = len(data) / 1024 / 1024
        raise TableError(
            f"文件太大（{size_mb:.1f} MB），当前上限是 {lim.max_upload_mb} MB。"
            "请拆分成多个小文件再上传，也可以在左侧「高级设置」里临时调大上传上限。"
        )
    if header_row < 1:
        raise TableError("「表头在第几行」必须从第 1 行起算，请填 1 或更大的数字。")

    deadline = Deadline(timeout)
    if name.endswith(".csv"):
        return _read_csv(data, header_row, deadline, lim)
    return _read_xlsx(data, sheet, header_row, deadline, lim)


# ---------------------------------------------------------------- CSV


def _detect_delimiter(text: str) -> str:
    """猜 CSV 分隔符：交给 stdlib 的 csv.Sniffer，认不出或表头切不开就按逗号。

    只在「表头真能被这个分隔符切成多列」时才采用：单列文件的值里偶尔带分号，
    Sniffer 会误判成多列，这个检查能挡住（否则会把一列数据硬拆成两列）。
    """
    sample = text[:8192]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters="".join(CSV_DELIMITERS)).delimiter
        header = next(csv.reader(StringIO(sample), delimiter=delimiter))
    except (csv.Error, StopIteration):
        return ","
    return delimiter if len(header) > 1 else ","


def _estimate_csv_shape(
    data: bytes,
    encoding: str,
    delimiter: str = ",",
    header_row: int = 1,
    limits: Limits | None = None,
    deadline: Deadline | None = None,
) -> tuple[int, int] | None:
    """解析前估算 CSV 的行列数（行数已扣掉表头上面的行）。

    用 csv.reader 逐条读记录，所以引号内的换行算同一个单元格，
    不会把一条记录当成多行（这是 splitlines 会犯的错）。
    超过行数上限就早停，不必读完整个文件。
    """
    lim = limits or current_limits()
    text = data.decode(encoding, errors="ignore")
    reader = csv.reader(StringIO(text), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration:
        return None
    except csv.Error:
        return None  # 引号坏掉的文件交给 read_csv 报「格式无法解析」
    cols = max(len(header), 1)
    total = 1
    try:
        for index, _ in enumerate(reader, 1):
            total += 1
            if total > lim.max_rows + header_row:
                break  # 早停：已足够判定超限
            if deadline is not None and index % 20000 == 0:
                deadline.check("预检 CSV 行列数")
    except csv.Error:
        pass  # 能数多少算多少，真正的报错留给 read_csv
    return max(total - header_row, 0), cols


def _csv_header_too_large(header_row: int) -> TableError:
    return TableError(
        f"CSV 里找不到第 {header_row} 行可作表头（文件可能比这更短）；请把「表头在第几行」改小，或确认选对了文件。"
    )


def _read_csv(data: bytes, header_row: int, deadline: Deadline, limits: Limits) -> LoadedTable:
    if b"\x00" in data:
        raise TableError(
            "文件里有二进制内容或使用了不支持的编码，无法按文本读取；请用 Excel 另存为「CSV UTF-8」后重试。"
        )
    notes: list[str] = []
    for encoding in ("utf-8-sig", "gb18030"):
        text = data.decode(encoding, errors="ignore")
        delimiter = _detect_delimiter(text)
        # 先按行数和表头列数预检，超限的文件不进入 read_csv，避免先占满内存
        estimate = _estimate_csv_shape(data, encoding, delimiter, header_row, limits, deadline)
        if estimate is not None:
            check_shape_limits(*estimate, "文件", limits=limits)
        deadline.check("读取 CSV")
        try:
            df = pd.read_csv(
                BytesIO(data),
                encoding=encoding,
                sep=delimiter,
                dtype=str,  # 按原文读取：保留 00123 这类编号，0 不会被当成空
                keep_default_na=False,  # "NA"、"null" 这类文字不当缺失值
                header=header_row - 1,
            )
        except UnicodeDecodeError:
            continue
        except pd.errors.EmptyDataError:
            if header_row > 1:
                raise _csv_header_too_large(header_row) from None
            raise TableError("CSV 里没有任何数据（文件为空，或只有空白字符）。") from None
        except pd.errors.ParserError as exc:
            if "Passed header=" in str(exc):  # 表头行比文件还靠下
                raise _csv_header_too_large(header_row) from None
            raise TableError(f"CSV 格式无法解析，请检查是否用{CSV_DELIMITERS[delimiter]}分隔、引号是否成对。") from exc
        if header_row > 1:
            notes.append(
                f"已把第 {header_row} 行作为表头，上面 {header_row - 1} 行已忽略；"
                "如果数据行被少吃掉了，请把「表头在第几行」调小。"
            )
        if delimiter != ",":
            name = CSV_DELIMITERS[delimiter]
            notes.append(
                f"文件是{name}分隔的（不是逗号），已自动按{name}解析；"
                "如果分列不对，请用 Excel 另存为「CSV UTF-8（逗号分隔）」再上传。"
            )
        if encoding == "gb18030":
            notes.append(
                "文件不是 UTF-8 编码，已按 GB18030（兼容 GBK）读取；"
                "如果看到乱码，请用 Excel 另存为「CSV UTF-8」再上传。"
            )
        df = _require_data(df, "文件", limits)  # 解析后再确认一次实际行列/单元格
        df = _blank_to_na(df)
        if df.shape[1] == 1:
            notes.append(
                "这个文件只解析出一列：没有识别到逗号、分号、制表符或竖线分隔符。"
                "如果内容本来应该分列，请确认原文件格式，或用 Excel 另存为"
                "「CSV UTF-8（逗号分隔）」再上传。"
            )
        df, header_notes = _normalize_headers(df)
        return LoadedTable(df, notes + header_notes)
    raise TableError("CSV 编码无法识别，请用 Excel 另存为「CSV UTF-8」后重试。")


# ---------------------------------------------------------------- XLSX


def _read_xlsx(
    data: bytes,
    sheet: str | None,
    header_row: int,
    deadline: Deadline,
    limits: Limits,
) -> LoadedTable:
    try:
        check_xlsx_uncompressed_size(data, limits)
        deadline.check("预检 XLSX")
        labels, parts, index, skipped = pick_xlsx_sheet(data, sheet, limits, header_rows=header_row)
        if not labels:
            raise TableError("XLSX 无法打开：文件里没有可读取的工作表，请用 Excel 另存为 .xlsx 后重试。")
        book = pd.ExcelFile(BytesIO(data), engine="openpyxl")
        merged = 0
        with zipfile.ZipFile(BytesIO(data)) as archive:
            while True:
                chosen = labels[index]  # 按名字定位：序号不可靠（工作簿里可能有 chart sheet）
                if chosen not in book.sheet_names:  # 包结构异常时 pandas 看不到这张表
                    raise TableError(f"XLSX 无法打开：找不到要读取的工作表「{chosen}」，请用 Excel 另存后重试。")
                deadline.check(f"读取工作表「{chosen}」")
                # keep_default_na=False：单元格里写的 "NA" 是文字，不能当成空值；
                # dtype=object：先原样收下，否则像 "00123" 这种文本单元格会被推断成数字、前导零丢掉。
                try:
                    df = book.parse(
                        chosen,
                        keep_default_na=False,
                        header=header_row - 1,
                        dtype=object,
                    )
                except pd.errors.EmptyDataError:
                    raise _xlsx_header_too_large(chosen, header_row) from None
                df = _restore_value_types(df)
                if sheet is not None or (df.shape[0] > 0 and df.shape[1] > 0):
                    break  # 显式指定的表就是它，空表也交给 _require_data 报错
                # 只设格式的空白封面页也有行标签，预检会选中它；解析后才发现是空的就往后跳，
                # 下一张要先扫+限额，保证「只对真正要解析的表花资源」。
                index += 1
                if index >= len(labels):
                    raise TableError("工作簿里没有数据行（所有工作表都为空，或只有表头）。")
                scan_part(archive, parts[index], labels[index], len(labels), limits, header_row)
            merged = count_data_merges(archive, parts[index], header_row)
        df = _require_data(df, f"工作表「{chosen}」", limits)  # 上限检查
        df = _blank_to_na(df)
        df, header_notes = _normalize_headers(df)
        if header_row > 1:
            header_notes.insert(
                0,
                f"已把第 {header_row} 行作为表头，上面 {header_row - 1} 行已忽略；"
                "如果数据行被少吃掉了，请把「表头在第几行」调小。",
            )
    except TableError:
        raise
    except Exception as exc:  # 损坏文件的异常类型很杂，统一转成用户能看懂的话
        raise TableError("XLSX 无法打开：文件可能已损坏，或者其实是改了扩展名的其他文件。") from exc

    notes: list[str] = []
    if merged:
        notes.append(
            f"这张工作表里有 {merged} 处合并单元格压在数据行上：合并区域只有左上角有值，"
            "其余格子读出来是空的。用第 3 步「删除指定列中有空值的行」时请留意，"
            "别把这类行当成空值行删掉。"
        )
    if skipped:
        detail = "；".join(f"「{label}」{reason}" for label, reason in skipped)
        notes.append(f"这些工作表超出单表上限，已跳过：{detail}。")
    if len(labels) > 1:
        if sheet is None and index:
            skipped_names = "、".join(f"「{name}」" for name in labels[:index])
            notes.append(f"前面的工作表{skipped_names}没有数据，已读取「{chosen}」。")
        notes.append(f"该文件有 {len(labels)} 个工作表，本次只读取「{chosen}」；其他表不读取。")
    return LoadedTable(df, notes + header_notes, chosen, labels)


def _xlsx_header_too_large(sheet: str, header_row: int) -> TableError:
    return TableError(
        f"工作表「{sheet}」里找不到第 {header_row} 行可作表头（这张表可能比这更短）；"
        "请把「表头在第几行」改小，或换一张有数据的表。"
    )


# ---------------------------------------------------------------- 共用


def _restore_value_types(df: pd.DataFrame) -> pd.DataFrame:
    """把「本来就是数字/日期」的列还原成正常 dtype，文本单元格保持文本。

    XLSX 用 dtype=object 读，是为了不让 pandas 把文本形式的编号（"00123"）推断成数字、
    悄悄吃掉前导零；但数字/日期列如果一直是 object，计算列和导出的 dtype 判断会跟着变。
    这里只在「整列的值本来就是数字/日期对象（不是字符串）」时还原：
    文本列不动，所以 "00123" 仍是 "00123"。
    """
    out = None
    for column in df.columns:
        series = df[column]
        if series.dtype != object:
            continue
        values = series.dropna()
        # 空文本单元格（keep_default_na=False 读出来是 ""）不算有效值，别让它把日期列判成 mixed
        values = values[values.astype("string") != ""]
        if values.empty:
            continue
        kind = pd.api.types.infer_dtype(values, skipna=True)
        converted = None
        # 只还原「整列本来就是数字」的列；混着文本的列（mixed-integer 等）保持原样，
        # 否则像 "001" 和 2 混在一起的列里，字符串 "001" 又会被转成 1。
        if kind in ("integer", "floating", "decimal", "mixed-integer-float"):
            converted = pd.to_numeric(series, errors="coerce")
        elif kind in ("datetime", "date"):
            converted = pd.to_datetime(series, errors="coerce")
        if converted is None:
            continue
        if out is None:
            out = df.copy()
        out[column] = converted
    return df if out is None else out


def _normalize_headers(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """把列名统一成字符串，并补上合并单元格造成的空表头。

    Excel 里合并单元格拆开后，被合并掉的格子读出来是空的（pandas 会给
    「Unnamed: 3」这类名字），这种列没法在下拉框里选，必须给个能叫的名字。
    规则：空白/Unnamed 列 → 「列N」（N 是列序号），重名 → 后面加「_2」「_3」。
    """
    notes: list[str] = []
    auto_named: list[str] = []
    seen: set[str] = set()
    columns: list[str] = []
    for position, column in enumerate(df.columns, 1):
        raw = str(column)
        text = raw.strip()
        if not text or _UNNAMED_RE.fullmatch(text):
            text = f"列{position}"
            auto_named.append(text)
        base = text
        suffix = 2
        while text in seen:
            text = f"{base}_{suffix}"
            suffix += 1
        seen.add(text)
        columns.append(text)
    if auto_named:
        notes.append(
            f"表头里有 {len(auto_named)} 个空白列名（合并单元格常见），"
            f"已自动命名为「{'、'.join(auto_named[:5])}」"
            + ("等" if len(auto_named) > 5 else "")
            + "；可以在第 4 步「列重命名」里改名。"
        )
    renamed = [str(old) for old, new in zip(df.columns, columns) if str(old) != new]
    if renamed and not auto_named:
        notes.append("有重复的列名，已自动加序号区分（如「金额_2」）；需要的话可在「列重命名」里改。")
    if columns == [str(column) for column in df.columns]:
        return df, notes
    out = df.copy()
    out.columns = columns
    return out, notes


def _blank_to_na(df: pd.DataFrame) -> pd.DataFrame:
    """统一缺失值表示：空文本也换成 NA，避免空文本和 NaN 两种写法混进统计/导出。

    用 mask 而不是 replace：pandas 2.2 的 replace 会触发「静默向下转型」的
    FutureWarning（未来版本行为会变），这里只做「等于空文本的格子换成 NA」，语义更窄也更稳。
    """
    return df.mask(df.eq(""), pd.NA)


def _require_data(df: pd.DataFrame, source: str, limits: Limits | None = None) -> pd.DataFrame:
    rows, cols = df.shape
    if cols == 0:
        raise TableError(f"{source}里没有可用的列。")
    if rows == 0:
        raise TableError(f"{source}里没有数据行（只有表头，或内容为空）。")
    check_shape_limits(rows, cols, source, limits=limits)
    return df
