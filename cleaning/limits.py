"""所有上限与性能参数集中在这里（v2.0.0 起）。

三件事：
1. 部署时可以用环境变量覆盖，不用改代码（见 README「资源限制」）；
2. 界面左侧「高级设置」可以按会话临时覆盖——那走 ``current_limits(max_rows=…)``，
   不写回模块常量，避免一个用户的设置影响别人的会话；
3. ``MAX_*`` 会被 ``cleaning/__init__.py`` 原样导出，所以
   ``monkeypatch.setattr(cleaning, "MAX_ROWS", 2)`` 依旧生效：
   内部一律通过 ``current_limits()`` 读包属性，而不是本模块的常量副本。
"""

from __future__ import annotations

import os
from typing import NamedTuple

from .errors import TableError


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    """读整数环境变量；没设、写错或太小都退回默认值（部署时不该因拼错变量就崩）。"""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return value if value >= minimum else default


# 文件与数据规模上限
MAX_UPLOAD_BYTES = _env_int("TABLE_CLEANER_MAX_UPLOAD_MB", 10) * 1024 * 1024
MAX_ROWS = _env_int("TABLE_CLEANER_MAX_ROWS", 500_000)
MAX_COLS = _env_int("TABLE_CLEANER_MAX_COLS", 1_000)
MAX_CELLS = _env_int("TABLE_CLEANER_MAX_CELLS", 2_000_000)
MAX_XLSX_UNCOMPRESSED_BYTES = _env_int("TABLE_CLEANER_MAX_XLSX_MB", 200) * 1024 * 1024

# Excel 格式本身的硬上限（不是本工具的策略，算导出时用）
EXCEL_MAX_ROWS = 1_048_576
EXCEL_MAX_COLS = 16_384

# 分块清洗：超过 CHUNK_THRESHOLD_ROWS 行默认按 CHUNK_ROWS 一块处理并报进度
CHUNK_ROWS = _env_int("TABLE_CLEANER_CHUNK_ROWS", 50_000)
CHUNK_THRESHOLD_ROWS = _env_int("TABLE_CLEANER_CHUNK_THRESHOLD", 100_000)

# 单次处理的硬超时（秒）；0 = 不限
DEFAULT_TIMEOUT_SECONDS = _env_int("TABLE_CLEANER_TIMEOUT_SECONDS", 60, minimum=0)


class Limits(NamedTuple):
    """一次处理实际生效的上限。"""

    max_upload_bytes: int
    max_rows: int
    max_cols: int
    max_cells: int
    max_xlsx_uncompressed_bytes: int

    @property
    def max_upload_mb(self) -> int:
        return max(1, self.max_upload_bytes // 1024 // 1024)

    @property
    def max_xlsx_uncompressed_mb(self) -> int:
        return max(1, self.max_xlsx_uncompressed_bytes // 1024 // 1024)


def current_limits(**overrides: int) -> Limits:
    """当前生效的上限：默认取 ``cleaning`` 包上的同名常量，参数可临时覆盖。

    为什么从包属性读：``cleaning/__init__.py`` 会把 ``MAX_*`` 再导出一份，
    测试的 monkeypatch、界面高级设置的覆盖改的都是那份属性；如果这里读本模块的
    常量副本，覆盖就会静默失效（v1.x 的限额测试会全绿但实际没生效）。
    """
    limits_globals = globals()
    pkg = __import__(__package__ or "cleaning")
    values: dict[str, int] = {}
    for field, const in (
        ("max_upload_bytes", "MAX_UPLOAD_BYTES"),
        ("max_rows", "MAX_ROWS"),
        ("max_cols", "MAX_COLS"),
        ("max_cells", "MAX_CELLS"),
        ("max_xlsx_uncompressed_bytes", "MAX_XLSX_UNCOMPRESSED_BYTES"),
    ):
        fallback = limits_globals[const]
        value = getattr(pkg, const, fallback)
        values[field] = value if isinstance(value, int) and value > 0 else fallback
    values.update({key: int(value) for key, value in overrides.items() if value})
    return Limits(**values)


def raise_shape_limit(rows: int, cols: int, source: str, hint: str = "", limits: Limits | None = None) -> None:
    """超限时的统一中文提示；解析前的估算和解析后的实际值共用同一套话术。"""
    lim = limits or current_limits()
    tail = f" {hint}" if hint else ""
    tune = "也可以在左侧「高级设置」里临时调大上限。"
    if rows > lim.max_rows:
        raise TableError(
            f"{source}的行数 {rows:,} 超过上限 {lim.max_rows:,} 行；"
            f"请拆分成多个小文件再上传，或删掉用不到的行。{tune}{tail}"
        )
    if cols > lim.max_cols:
        raise TableError(
            f"{source}的列数 {cols:,} 超过上限 {lim.max_cols:,} 列；请删掉不需要的列或拆分后再上传。{tune}{tail}"
        )
    if rows * cols > lim.max_cells:
        raise TableError(
            f"{source}有 {rows:,} 行 × {cols:,} 列，共 {rows * cols:,} 个单元格，"
            f"单元格总数超过上限 {lim.max_cells:,} 个；请拆分后再上传。{tune}{tail}"
        )


def check_shape_limits(rows: int, cols: int, source: str, hint: str = "", limits: Limits | None = None) -> None:
    """解析前先按估算规模拒绝，避免超限文件已经占满内存。"""
    lim = limits or current_limits()
    if rows > lim.max_rows or cols > lim.max_cols or rows * cols > lim.max_cells:
        raise_shape_limit(rows, cols, source, hint, lim)
