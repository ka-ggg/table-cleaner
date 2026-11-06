"""XLSX 的流式预检：解压体积、真实行列数、工作表名与选表。

这一层只碰 zip 包和 XML，不 import openpyxl / pandas，所以解析之前就能拒绝
压缩炸弹和超大表——不用先把几百 MB 塞进内存。
"""

from __future__ import annotations

import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO

from .errors import TableError
from .limits import Limits, check_shape_limits, current_limits, raise_shape_limit

XLSX_ONE_SHEET_HINT = (
    "本次只读取这一张工作表，其他工作表不读取（也不限额）；请拆分这张表，或用 Excel 删减行列后再上传。"
)

_COL_LETTERS_RE = re.compile(rb'<c r="([A-Z]+)\d+"')
_ROW_TAG_RE = re.compile(rb"<row[ >/]")  # 真正的行标签；<rowBreaks>（分页符）不算行
_MERGE_RE = re.compile(rb'<mergeCell\b[^>]*\bref="([A-Z]+)(\d+):([A-Z]+)(\d+)"')
_STREAM_CHUNK = 1 << 16
_MERGE_TAIL = 128  # 合并单元格标签不会超过这个长度，够跨块匹配
_REL_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def _col_to_number(letters: str) -> int:
    """A->1, Z->26, AA->27。"""
    cols = 0
    for ch in letters:
        cols = cols * 26 + (ord(ch) - 64)
    return cols


def check_xlsx_uncompressed_size(data: bytes, limits: Limits | None = None) -> None:
    """XLSX 是压缩包：先看解压后的总大小，防止体积不大的压缩炸弹。"""
    lim = limits or current_limits()
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            total = sum(entry.file_size for entry in archive.infolist())
    except (zipfile.BadZipFile, OSError) as exc:
        raise TableError("XLSX 无法打开：文件可能已损坏，或者其实是改了扩展名的其他文件。") from exc
    if total > lim.max_xlsx_uncompressed_bytes:
        total_mb = total / 1024 / 1024
        limit_mb = lim.max_xlsx_uncompressed_mb
        raise TableError(
            f"XLSX 解压后约 {total_mb:.0f} MB，超过上限 {limit_mb} MB；"
            "请拆分后再上传，也可以用环境变量 TABLE_CLEANER_MAX_XLSX_MB 调大上限。"
        )


def scan_xlsx_sheet(
    stream,
    source: str,
    hint: str = "",
    limits: Limits | None = None,
    header_rows: int = 1,
):
    """流式扫工作表 XML：数真实行标签、真实最大列号。

    列数只按真实单元格引用 `<c r="…">` 扫：<dimension> 可以是伪造的（写小能绕过列数上限，
    写大会误拒），所以完全不看它；解析后 _require_data 还会再验一次。
    返回（行标签数，最大列号）；行标签数含表头，判断上限时按 header_rows 减掉表头上面的行。
    """
    lim = limits or current_limits()
    rows = 0
    cols = 1
    widest = b""  # 见过的最宽列字母：先比长度再比字典序，等价于最大列号，省掉每次匹配的 decode
    # 行标签按 5 字节模式 <row[ >/] 匹配：<rowBreaks>（分页符）也以 "<row" 开头，
    # 按子串数会把它算成一行。留 4 字节尾巴：5 字节模式最多跨 4 字节，
    # 且整段落在尾巴里（4 字节）不可能，所以不会重复计数。
    tail_row = b""
    tail_col = b""
    chunk = stream.read(_STREAM_CHUNK)

    while chunk:
        rows += len(_ROW_TAG_RE.findall(tail_row + chunk))
        tail_row = (tail_row + chunk)[-4:]

        # 每块只换算一次「本块最宽的列」：400k 个单元格约 0.11 s，比逐次 decode 快得多。
        # tail 会把上一块末尾的引用再扫一次，取 max 不影响结果。
        window = tail_col + chunk
        chunk_widest = max(
            (m.group(1) for m in _COL_LETTERS_RE.finditer(window)),
            key=lambda letters: (len(letters), letters),
            default=b"",
        )
        if (len(chunk_widest), chunk_widest) > (len(widest), widest):
            widest = chunk_widest
            cols = max(cols, _col_to_number(widest.decode()))
        tail_col = window[-32:]

        # 已足够判定超限，不必读完整个工作表（表头也算进去，免得正好卡上限时误判）
        if rows - header_rows > lim.max_rows or cols > lim.max_cols:
            raise_shape_limit(max(rows - header_rows, 0), cols, source, hint, lim)
        chunk = stream.read(_STREAM_CHUNK)
    return max(rows, 0), cols


def count_data_merges(archive: zipfile.ZipFile, part: str, header_rows: int = 1) -> int:
    """数「压在数据行上」的合并单元格区域个数（只扫 XML，不开 openpyxl）。

    合并区域只有左上角单元格有值，其余格子 pandas 读出来是空的——用户勾「删除指定列中
    有空值的行」时，这些行会被当空值行删掉。表头行及以上的合并（标题、合并表头）
    不算，因为那部分本来就说明过了。读不了就返回 0，不因为提示功能让文件打不开。
    """
    count = 0
    try:
        with archive.open(part) as stream:
            tail = b""
            while True:
                chunk = stream.read(_STREAM_CHUNK)
                if not chunk:
                    break
                window = tail + chunk
                for match in _MERGE_RE.finditer(window):
                    # 只数「整条标签在上一块里还没数完」的，避免跨块重复计数
                    if match.end() > len(tail) and int(match.group(4)) > header_rows:
                        count += 1
                tail = window[-_MERGE_TAIL:]
    except (KeyError, OSError):
        return 0
    return count


def xlsx_sheet_names(archive: zipfile.ZipFile) -> dict[str, str]:
    """工作表 XML 路径 → 用户在 Excel 里看到的工作表名。

    用 ElementTree 解析而不是正则：表名里的 `& < > " '` 在 XML 里是实体
    （`Sales &amp; Marketing`），正则拿到的是没解码的原文，和 openpyxl/pandas 读出来的名字对不上，
    结果默认读表、按名选表全都会失败。
    关系路径按 OOXML 规则解析：以 `/` 开头是包内绝对路径，否则相对 `xl/`，允许 `..`。
    只读 workbook.xml 和它的 rels 两个小文件；拿不到/解析失败就返回空字典，退回目录筛选。
    """
    try:
        rels_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
    except (KeyError, ET.ParseError):
        return {}

    targets: dict[str, str] = {}
    for rel in rels_root.iter():
        if not rel.tag.endswith(("}Relationship", "Relationship")):
            continue
        rid, target = rel.get("Id"), rel.get("Target")
        if not (rid and target) or rel.get("TargetMode") == "External":
            continue
        if target.startswith("/"):
            targets[rid] = posixpath.normpath(target.lstrip("/"))
        else:
            targets[rid] = posixpath.normpath(posixpath.join("xl", target))

    names: dict[str, str] = {}
    for element in workbook_root.iter():
        if not element.tag.endswith(("}sheet", "sheet")):
            continue
        sheet_name = element.get("name")
        rid = element.get(_REL_ID) or next(
            (v for k, v in element.attrib.items() if k.rsplit("}", 1)[-1] == "id" and v), None
        )
        part = targets.get(rid) if rid else None
        if sheet_name is not None and part:
            names[part] = sheet_name
    return names


def scan_part(
    archive: zipfile.ZipFile,
    part: str,
    label: str,
    total: int,
    limits: Limits | None = None,
    header_rows: int = 1,
) -> tuple[int, int]:
    """扫一张工作表并做行列/单元格限额（预检与「跳过空白表」共用同一段逻辑）。

    返回的 rows 含表头行；数据行数由调用方按用户选的 header_rows 计算。
    """
    lim = limits or current_limits()
    source = f"这份 XLSX 的工作表「{label}」（{part.rsplit('/', 1)[-1]}，文件共 {total} 个工作表）"
    with archive.open(part) as stream:  # 只读需要的字节，不整个读进内存
        rows, cols = scan_xlsx_sheet(stream, source, XLSX_ONE_SHEET_HINT, lim, header_rows)
    data_rows = max(rows - header_rows, 0)
    check_shape_limits(data_rows, cols, source, XLSX_ONE_SHEET_HINT, lim)
    return rows, cols


def limit_reason(exc: TableError) -> str:
    """把「行数/列数超限」的长提示压成一句短的，用来写「这张表被跳过」的说明。

    超限提示的前半句是「这份 XLSX 的工作表「X」（sheet1.xml，文件共 N 个工作表）的」，
    表名已经单独写在提示里了，这里只留「行数 10 超过上限 5 行」那半句。
    """
    text = str(exc).split("；")[0].rstrip("。")
    if "）的" in text:
        return text.split("）的", 1)[1]
    return text


def pick_xlsx_sheet(
    data: bytes,
    sheet: str | None = None,
    limits: Limits | None = None,
    header_rows: int = 1,
) -> tuple[tuple[str, ...], tuple[str, ...], int, tuple[tuple[str, str], ...]]:
    """流式扫工作表，挑出起始解析位置，并只对它做行列/单元格上限检查。

    返回（全部工作表名, 各表路径, 起始序号, 被跳过的表及原因）。策略：
    - 给了 sheet：只扫这一张，名字不存在直接报错（提示里列出可选表名），超限照实拒；
    - 没给 sheet：按工作簿顺序扫，遇到第一张有行标签且没超限的表就作为起点，**后面的表不再扫**；
      超限的表**跳过而不是让整份文件打不开**——用户没法选表时，这是唯一的出路；
    - 跳过哪些表会写进 notes，用户看得到；全都超限才报错，并列出每张表的原因。
    - header_rows 是用户选的「表头在第几行」：表头以上都不算数据，所以一张表
      至少要 header_rows + 1 个行标签才算有数据。
    """
    lim = limits or current_limits()
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            # 只认真正的工作表：优先按 workbook.xml 的关系映射取（chart sheet 不算，
            # xl/worksheets/_rels/ 下的关系文件也不算——带图表/绘图的工作簿会有这种文件，
            # 按目录前缀筛会把它当工作表，表数和序号都会错）；拿不到映射再退回目录筛选。
            names = xlsx_sheet_names(archive)
            present = set(archive.namelist())
            parts = [n for n in names if n.startswith("xl/worksheets/") and n in present] or sorted(
                n for n in present if n.startswith("xl/worksheets/") and "/_rels/" not in n and n.endswith(".xml")
            )
            if not parts:
                return (), (), 0, ()  # 包结构不像 XLSX：交给后面的解析报「无法打开」
            labels = tuple(names.get(part) or f"第 {index} 个工作表" for index, part in enumerate(parts, 1))
            total = len(parts)
            if sheet is not None:
                if sheet not in labels:
                    raise TableError(f"工作簿里没有名为「{sheet}」的工作表，可选：{'、'.join(labels)}。")
                targets: range | list[int] = [labels.index(sheet)]
            else:
                targets = range(total)
            skipped: list[tuple[str, str]] = []
            for index in targets:
                try:
                    rows, _ = scan_part(archive, parts[index], labels[index], total, lim, header_rows)
                except TableError as exc:
                    if sheet is not None:
                        raise  # 用户点名要的表，超限必须照实报错
                    skipped.append((labels[index], limit_reason(exc)))
                    continue
                # 指定的表即使为空也选它，让 _require_data 报空表
                if rows > header_rows or sheet is not None:
                    return labels, tuple(parts), index, tuple(skipped)
            if skipped:
                detail = "；".join(f"「{label}」{reason}" for label, reason in skipped)
                raise TableError(
                    f"这个文件的每张工作表都超过了单表上限：{detail}。请拆分文件，或先用 Excel 删掉用不到的表再上传。"
                )
            hint = (
                f"当前「表头在第几行」是第 {header_rows} 行，如果表头没这么靠下，把它调小再试。"
                if header_rows > 1
                else ""
            )
            raise TableError("工作簿里没有数据行（所有工作表都为空，或只有表头）。" + hint)
    except zipfile.BadZipFile:
        return (), (), 0, ()  # 损坏文件交给后面的解析报「无法打开」
