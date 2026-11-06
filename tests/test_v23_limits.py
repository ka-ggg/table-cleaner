"""v2.3.0 的 Limits 统一配置对象测试：默认值、覆盖、环境变量与包属性优先级。"""

from __future__ import annotations

import cleaning
from cleaning.limits import API_MAX_BODY_BYTES, API_MAX_JOBS


def test_limits_defaults_match_frozen_values():
    """配置对象默认值必须与 v2.2.0 的现行上限逐项一致，不许借重构调值。"""
    limits = cleaning.Limits.from_env()
    assert limits.max_upload_bytes == 10 * 1024 * 1024
    assert limits.max_rows == 500_000
    assert limits.max_cols == 1_000
    assert limits.max_cells == 2_000_000
    assert limits.max_xlsx_uncompressed_bytes == 200 * 1024 * 1024
    assert limits.timeout_seconds == 60
    assert limits.chunk_rows == 50_000
    assert limits.chunk_threshold_rows == 100_000
    assert limits.max_upload_mb == 10
    assert limits.max_xlsx_uncompressed_mb == 200


def test_limits_with_overrides_keeps_other_fields():
    """按会话覆盖只改指定字段，其他字段沿用原值，原对象不被修改。"""
    base = cleaning.Limits.from_env()
    strict = base.with_overrides(max_rows=10, max_upload_bytes=4096, timeout_seconds=0)
    assert strict.max_rows == 10
    assert strict.max_upload_bytes == 4096
    assert strict.timeout_seconds == 0
    assert strict.max_cols == base.max_cols
    assert strict.max_cells == base.max_cells
    assert base.max_rows == 500_000
    assert base.timeout_seconds == 60


def test_limits_from_env_reads_all_override_names(monkeypatch):
    """TABLE_CLEANER_* 环境变量覆盖名保持不变，新增字段也走同一套命名。"""
    monkeypatch.setenv("TABLE_CLEANER_MAX_UPLOAD_MB", "7")
    monkeypatch.setenv("TABLE_CLEANER_MAX_ROWS", "12345")
    monkeypatch.setenv("TABLE_CLEANER_MAX_COLS", "88")
    monkeypatch.setenv("TABLE_CLEANER_MAX_CELLS", "99999")
    monkeypatch.setenv("TABLE_CLEANER_MAX_XLSX_MB", "300")
    monkeypatch.setenv("TABLE_CLEANER_TIMEOUT_SECONDS", "15")
    monkeypatch.setenv("TABLE_CLEANER_CHUNK_ROWS", "2000")
    monkeypatch.setenv("TABLE_CLEANER_CHUNK_THRESHOLD", "4000")
    limits = cleaning.Limits.from_env()
    assert limits.max_upload_bytes == 7 * 1024 * 1024
    assert limits.max_rows == 12345
    assert limits.max_cols == 88
    assert limits.max_cells == 99999
    assert limits.max_xlsx_uncompressed_bytes == 300 * 1024 * 1024
    assert limits.timeout_seconds == 15
    assert limits.chunk_rows == 2000
    assert limits.chunk_threshold_rows == 4000


def test_limits_from_env_invalid_and_zero_values(monkeypatch):
    """写错退回默认；超时 0 是合法值（不限时），不能被当成无效值。"""
    monkeypatch.setenv("TABLE_CLEANER_MAX_ROWS", "很多行")
    monkeypatch.setenv("TABLE_CLEANER_TIMEOUT_SECONDS", "0")
    limits = cleaning.Limits.from_env()
    assert limits.max_rows == 500_000
    assert limits.timeout_seconds == 0


def test_current_limits_follows_all_package_constants(monkeypatch):
    """monkeypatch 包属性时，配置对象连超时与分块字段也要跟着变。"""
    monkeypatch.setattr(cleaning, "MAX_ROWS", 3)
    monkeypatch.setattr(cleaning, "DEFAULT_TIMEOUT_SECONDS", 5)
    monkeypatch.setattr(cleaning, "CHUNK_ROWS", 7)
    monkeypatch.setattr(cleaning, "CHUNK_THRESHOLD_ROWS", 9)
    current = cleaning.current_limits()
    assert current.max_rows == 3
    assert current.timeout_seconds == 5
    assert current.chunk_rows == 7
    assert current.chunk_threshold_rows == 9


def test_api_limits_are_bounded_and_positive():
    """API 雏形的内存上限是正数，避免默认值写成 0 导致服务一启动就拒绝所有请求。"""
    assert API_MAX_JOBS >= 1
    assert API_MAX_BODY_BYTES >= 1024 * 1024
