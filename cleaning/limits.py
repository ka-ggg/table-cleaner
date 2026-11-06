"""所有上限与性能参数集中在这里（v2.0.0 起）。

四件事：

1. ``Limits`` 是界面、CLI、HTTP API 共用的唯一配置对象；
2. 部署时可以用环境变量覆盖，不用改代码（见 README「资源限制」）；
3. 界面左侧「高级设置」可以按会话临时覆盖——那走 ``current_limits(...)``
   或 ``Limits.with_overrides()``，不写回模块常量，避免一个用户的设置影响别人；
4. ``MAX_*`` 会被 ``cleaning/__init__.py`` 原样导出，所以
   ``monkeypatch.setattr(cleaning, "MAX_ROWS", 2)`` 依旧生效：
   内部一律通过 ``current_limits()`` 读包属性，而不是本模块的常量副本。
"""

from __future__ import annotations

import os
from typing import NamedTuple

from .errors import TableError

_MB = 1024 * 1024


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


def _env_limit_values() -> dict[str, int]:
    """按当前环境变量算出一组上限；导入时和 ``Limits.from_env()`` 共用同一套口径。"""
    return {
        "max_upload_bytes": _env_int("TABLE_CLEANER_MAX_UPLOAD_MB", 10) * _MB,
        "max_rows": _env_int("TABLE_CLEANER_MAX_ROWS", 500_000),
        "max_cols": _env_int("TABLE_CLEANER_MAX_COLS", 1_000),
        "max_cells": _env_int("TABLE_CLEANER_MAX_CELLS", 2_000_000),
        "max_xlsx_uncompressed_bytes": _env_int("TABLE_CLEANER_MAX_XLSX_MB", 200) * _MB,
        "timeout_seconds": _env_int("TABLE_CLEANER_TIMEOUT_SECONDS", 60, minimum=0),
        "chunk_rows": _env_int("TABLE_CLEANER_CHUNK_ROWS", 50_000),
        "chunk_threshold_rows": _env_int("TABLE_CLEANER_CHUNK_THRESHOLD", 100_000),
    }


_ENV_DEFAULTS = _env_limit_values()

# 文件与数据规模上限（模块常量保留给旧代码和 monkeypatch 读取）
MAX_UPLOAD_BYTES = _ENV_DEFAULTS["max_upload_bytes"]
MAX_ROWS = _ENV_DEFAULTS["max_rows"]
MAX_COLS = _ENV_DEFAULTS["max_cols"]
MAX_CELLS = _ENV_DEFAULTS["max_cells"]
MAX_XLSX_UNCOMPRESSED_BYTES = _ENV_DEFAULTS["max_xlsx_uncompressed_bytes"]

# Excel 格式本身的硬上限（不是本工具的策略，算导出时用）
EXCEL_MAX_ROWS = 1_048_576
EXCEL_MAX_COLS = 16_384

# 分块清洗：超过 CHUNK_THRESHOLD_ROWS 行默认按 CHUNK_ROWS 一块处理并报进度
CHUNK_ROWS = _ENV_DEFAULTS["chunk_rows"]
CHUNK_THRESHOLD_ROWS = _ENV_DEFAULTS["chunk_threshold_rows"]

# 单次处理的硬超时（秒）；0 = 不限
DEFAULT_TIMEOUT_SECONDS = _ENV_DEFAULTS["timeout_seconds"]

# HTTP API 雏形的进程内限制：任务只留在内存，不落盘、不排队。
API_MAX_JOBS = _env_int("TABLE_CLEANER_API_MAX_JOBS", 20)
API_MAX_BODY_BYTES = _env_int("TABLE_CLEANER_API_MAX_BODY_MB", 32) * _MB


class Limits(NamedTuple):
    """一次处理实际生效的上限配置。

    这是界面、CLI、HTTP API 共用的唯一规则载体。字段顺序保持 v2.2.0 的
    前五项不变，新增的超时与分块字段都有默认值，因此旧代码按位置构造、
    ``_replace()`` 和 ``current_limits().max_rows`` 都继续可用。
    """

    max_upload_bytes: int
    max_rows: int
    max_cols: int
    max_cells: int
    max_xlsx_uncompressed_bytes: int
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    chunk_rows: int = CHUNK_ROWS
    chunk_threshold_rows: int = CHUNK_THRESHOLD_ROWS

    @property
    def max_upload_mb(self) -> int:
        return max(1, self.max_upload_bytes // _MB)

    @property
    def max_xlsx_uncompressed_mb(self) -> int:
        return max(1, self.max_xlsx_uncompressed_bytes // _MB)

    def with_overrides(self, **overrides: int) -> Limits:
        """返回替换若干字段后的新配置；不修改原对象，便于按请求/会话隔离。"""
        return self._replace(**overrides)

    @classmethod
    def from_env(cls) -> Limits:
        """按当前环境变量重新构造配置（部署启动时用，不读包属性覆盖）。"""
        return cls(
            max_upload_bytes=_env_int("TABLE_CLEANER_MAX_UPLOAD_MB", 10) * _MB,
            max_rows=_env_int("TABLE_CLEANER_MAX_ROWS", 500_000),
            max_cols=_env_int("TABLE_CLEANER_MAX_COLS", 1_000),
            max_cells=_env_int("TABLE_CLEANER_MAX_CELLS", 2_000_000),
            max_xlsx_uncompressed_bytes=_env_int("TABLE_CLEANER_MAX_XLSX_MB", 200) * _MB,
            timeout_seconds=_env_int("TABLE_CLEANER_TIMEOUT_SECONDS", 60, minimum=0),
            chunk_rows=_env_int("TABLE_CLEANER_CHUNK_ROWS", 50_000),
            chunk_threshold_rows=_env_int("TABLE_CLEANER_CHUNK_THRESHOLD", 100_000),
        )


_LIMIT_FIELDS = (
    ("max_upload_bytes", "MAX_UPLOAD_BYTES"),
    ("max_rows", "MAX_ROWS"),
    ("max_cols", "MAX_COLS"),
    ("max_cells", "MAX_CELLS"),
    ("max_xlsx_uncompressed_bytes", "MAX_XLSX_UNCOMPRESSED_BYTES"),
    ("timeout_seconds", "DEFAULT_TIMEOUT_SECONDS"),
    ("chunk_rows", "CHUNK_ROWS"),
    ("chunk_threshold_rows", "CHUNK_THRESHOLD_ROWS"),
)


def current_limits(**overrides: int) -> Limits:
    """当前生效的上限：默认取 ``cleaning`` 包上的同名常量，参数可临时覆盖。

    为什么从包属性读：``cleaning/__init__.py`` 会把 ``MAX_*`` 再导出一份，
    测试的 monkeypatch、界面高级设置的覆盖改的都是那份属性；如果这里读本模块的
    常量副本，覆盖就会静默失效（v1.x 的限额测试会全绿但实际没生效）。
    """
    limits_globals = globals()
    pkg = __import__(__package__ or "cleaning")
    values: dict[str, int] = {}
    for field, const in _LIMIT_FIELDS:
        fallback = int(limits_globals[const])
        value = getattr(pkg, const, fallback)
        minimum = 0 if field == "timeout_seconds" else 1
        values[field] = value if isinstance(value, int) and value >= minimum else fallback
    values.update({key: int(value) for key, value in overrides.items() if value is not None})
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
