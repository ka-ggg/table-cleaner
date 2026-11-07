"""v2.4.0 W5 测试：入口 ``engine`` 关键字、默认行为回归与未装 Polars 回退演练。

覆盖规划 5.2 / 5.6 与 G-01、G-08、G-13：

- ``clean_table`` / ``clean_bytes`` 只新增 keyword-only ``engine="pandas"``；
- 默认路径直通 v2.3.0 的 pandas 实现，不 import 引擎包，不依赖 Polars；
- 显式 ``engine="pandas"`` 与默认逐字节一致；``engine="polars"`` 在支持数据集上
  结果与 pandas 完全一致，并且确实由 Polars 独立执行（预检 uses_polars）；
- CLI / HTTP API / Streamlit / 批量入口不传 ``engine``；
- 用子进程模拟「未安装 Polars」：默认 pandas 路径（公共 API、CLI、批量）完整可用，
  显式请求 Polars 给中文 ``TableError``，不静默回落。
"""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

import cleaning
import pandas as pd
import pytest
from cleaning.engine import PANDAS_ENGINE, get_engine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENTRY_FILES = (
    PROJECT_ROOT / "app.py",
    PROJECT_ROOT / "cleaning" / "cli.py",
    PROJECT_ROOT / "api" / "http_api.py",
    PROJECT_ROOT / "cleaning" / "batch.py",
)


def _csv(text: str) -> bytes:
    return text.encode("utf-8-sig")


def _sample_options() -> cleaning.CleanOptions:
    return cleaning.CleanOptions(
        header_row=1,
        drop_duplicates=True,
        dedupe_on=("订单号",),
        drop_missing_in=("客户名称",),
        columns=cleaning.ColumnOps(
            rename=(("客户名称", "客户"),),
            order=("订单号", "客户", "含税金额"),
            to_number=("金额",),
            to_date=("下单日期",),
            computed=(("含税金额", "金额*1.13"),),
        ),
    )


def _sample_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "订单号": ["A1", "A2", "A2", "A3", "A4", "A5"],
            "客户名称": ["晨光", "星语", "星语", "", "云朵", "木棉"],
            "金额": ["10.5", "20", "20", "30.25", "40", "50"],
            "下单日期": ["2024-01-02", "2024-02-03", "2024-02-03", "2024-03-04", "2024-04-05", "2024-05-06"],
        }
    )


def test_default_path_bypasses_engine_selector(monkeypatch: pytest.MonkeyPatch):
    """默认 pandas 路径不是「先选引擎再转发」：即使选择器报错，默认调用仍成功。"""
    import cleaning.engine as engine_package

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("默认路径不应调用 get_engine")

    monkeypatch.setattr(engine_package, "get_engine", _boom)
    frame = _sample_frame()
    options = _sample_options()

    report = cleaning.clean_table(frame, options)
    direct = PANDAS_ENGINE.clean_table(frame, options)
    pd.testing.assert_frame_equal(report.df, direct.df)
    pd.testing.assert_frame_equal(report.removed, direct.removed)
    assert report.steps == direct.steps
    assert report.notes == direct.notes

    result = cleaning.clean_bytes(
        "订单.csv", _csv("订单号,金额\nA1,10\nA1,10\n"), cleaning.CleanOptions(drop_duplicates=True)
    )
    assert result.rows_before == 2 and result.rows_after == 1


def test_explicit_pandas_matches_default_bytes_and_report():
    """显式 engine="pandas" 与省略参数完全一致，不改变导出字节或报告。"""
    data = _csv("订单号,客户名称,金额,下单日期\nA1,晨光,10.5,2024-01-02\nA2,星语,20,2024-02-03\n")
    options = _sample_options()
    default = cleaning.clean_bytes("订单.csv", data, options)
    explicit = cleaning.clean_bytes("订单.csv", data, options, engine="pandas")
    assert default.data == explicit.data
    assert default.report_text == explicit.report_text
    assert default.steps == explicit.steps


def test_polars_engine_matches_pandas_and_really_executes(monkeypatch: pytest.MonkeyPatch):
    """支持数据集上 Polars 与 pandas 结果一致，且预检确认整条流水线由 Polars 执行。"""
    frame = _sample_frame()
    options = _sample_options()

    preflight = get_engine("polars").preflight(frame, options)
    assert preflight.engine == "polars"
    assert preflight.details == ()

    expected = cleaning.clean_table(frame, options, engine="pandas")
    actual = cleaning.clean_table(frame, options, engine="polars")
    pd.testing.assert_frame_equal(actual.df, expected.df)
    pd.testing.assert_frame_equal(actual.removed, expected.removed)
    assert actual.steps == expected.steps
    assert actual.notes == expected.notes

    data = _csv("订单号,客户名称,金额,下单日期\nA1,晨光,10.5,2024-01-02\nA2,星语,20,2024-02-03\n")
    pandas_result = cleaning.clean_bytes("订单.csv", data, options, engine="pandas")
    polars_result = cleaning.clean_bytes("订单.csv", data, options, engine="polars")
    assert polars_result.data == pandas_result.data
    assert polars_result.report_text == pandas_result.report_text


def test_polars_engine_registers_and_unknown_engine_is_chinese(monkeypatch: pytest.MonkeyPatch):
    """只允许 pandas / polars；未知引擎抛中文 TableError，默认调用仍可用。"""
    frame = _sample_frame()
    options = _sample_options()
    with pytest.raises(cleaning.TableError, match="引擎"):
        cleaning.clean_table(frame, options, engine="no_such_engine")
    with pytest.raises(cleaning.TableError, match="引擎"):
        cleaning.clean_bytes("订单.csv", _csv("a\n1\n"), cleaning.CleanOptions(), engine="no_such_engine")
    assert cleaning.clean_table(frame, options).steps


def _calls_with_engine_keyword(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = ""
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name in {"clean_table", "clean_bytes"} and any(keyword.arg == "engine" for keyword in node.keywords):
            lines.append(node.lineno)
    return lines


def test_entry_layers_never_pass_engine_keyword():
    """CLI / HTTP API / Streamlit / 批量不暴露引擎选项，默认行为与 v2.3.0 一致。"""
    offenders = {str(path.relative_to(PROJECT_ROOT)): _calls_with_engine_keyword(path) for path in ENTRY_FILES}
    assert {path: lines for path, lines in offenders.items() if lines} == {}


def test_limits_defaults_and_environment_names_unchanged():
    """G-01：默认值与环境变量名与 v2.3.0 完全一致，Polars 路径不得私自放宽。"""
    expected = {
        "max_upload_bytes": 10 * 1024 * 1024,
        "max_rows": 500_000,
        "max_cols": 1_000,
        "max_cells": 2_000_000,
        "max_xlsx_uncompressed_bytes": 200 * 1024 * 1024,
        "timeout_seconds": 60,
        "chunk_rows": 50_000,
        "chunk_threshold_rows": 100_000,
    }
    defaults = cleaning.current_limits()
    for field, value in expected.items():
        assert getattr(defaults, field) == value, field
    source = (PROJECT_ROOT / "cleaning" / "limits.py").read_text(encoding="utf-8")
    for name in (
        "TABLE_CLEANER_MAX_UPLOAD_MB",
        "TABLE_CLEANER_MAX_ROWS",
        "TABLE_CLEANER_MAX_COLS",
        "TABLE_CLEANER_MAX_CELLS",
        "TABLE_CLEANER_MAX_XLSX_MB",
        "TABLE_CLEANER_TIMEOUT_SECONDS",
        "TABLE_CLEANER_CHUNK_ROWS",
        "TABLE_CLEANER_CHUNK_THRESHOLD",
    ):
        assert name in source


_DRILL = r"""
import pathlib
import sys
import tempfile

sys.modules["polars"] = None  # 模拟未安装 Polars

import cleaning
import cleaning.cli

options = cleaning.CleanOptions(drop_duplicates=True)
frame = cleaning.make_sample_df()

report = cleaning.clean_table(frame, options)
assert report.steps, "默认 clean_table 应完成清洗"
assert cleaning.clean_table(frame, options, engine="pandas").steps == report.steps

data = "编号,金额\nA1,10\nA1,10\nA2,20\n".encode("utf-8-sig")
result = cleaning.clean_bytes("a.csv", data, options)
assert result.rows_before == 3 and result.rows_after == 2, result

batch = cleaning.run_batch([cleaning.BatchJob("a.csv", None, data)], options)
assert batch.exported == 1 and batch.items[0].error is None, batch

with tempfile.TemporaryDirectory() as tmp:
    source = pathlib.Path(tmp) / "in.csv"
    source.write_bytes(data)
    output = pathlib.Path(tmp) / "out.csv"
    assert cleaning.cli.main(["clean", str(source), "-o", str(output)]) == 0
    assert output.exists()

assert "cleaning.engine" not in sys.modules, "默认路径不应 import 引擎包"
assert "polars" not in sys.modules or sys.modules["polars"] is None

for call in (
    lambda: cleaning.clean_table(frame, options, engine="polars"),
    lambda: cleaning.clean_bytes("a.csv", data, options, engine="polars"),
):
    try:
        call()
    except cleaning.TableError as exc:
        message = str(exc)
        assert "可选依赖" in message, message
        assert "默认引擎仍是 pandas" in message, message
    else:
        raise SystemExit("显式请求 Polars 应报错，不能静默回落")

assert "polars" not in sys.modules or sys.modules["polars"] is None
print("fallback drill ok")
"""


def test_fallback_drill_without_polars_default_path_is_complete():
    """回退演练：未安装 Polars 时默认 pandas 公共 API / CLI / 批量完整可用。"""
    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(_DRILL)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "fallback drill ok" in completed.stdout
