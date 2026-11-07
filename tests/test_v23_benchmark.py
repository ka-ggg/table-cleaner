"""v2.3.0 benchmark 套件冒烟测试：数据形态、单次测量、基线表格生成。"""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd
from benchmarks import generate, run, verify_engines, worker


def _read_rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.reader(handle))


def test_generate_dataset_contains_three_column_shapes(tmp_path):
    """生成数据必须同时含长编号、普通文本/数值、混合日期与脏金额。"""
    path = generate.generate_dataset(tmp_path / "small.csv", 1200)
    rows = _read_rows(path)
    assert rows[0] == list(generate.COLUMNS)
    assert len(rows) == 1201
    body = rows[1:]
    assert all(len(row) == 4 for row in body)
    # 18 位长编号保持文本，不丢前导零/精度
    assert all(len(row[0]) == 18 and row[0].isdigit() for row in body)
    # 第 1000 行是刻意插入的完全重复行
    assert body[999] == body[1000]
    # 脏金额：至少出现千分位或货币符号
    assert any("," in row[2] or "¥" in row[2] for row in body)
    # 混合日期：三种写法 + 非法日期
    dates = {row[3] for row in body}
    assert any("-" in date for date in dates)
    assert any("/" in date for date in dates)
    assert any("年" in date for date in dates)
    assert "2024-13-40" in dates


def test_generate_dataset_is_deterministic(tmp_path):
    """固定随机种子下，两次生成的数据逐字节一致。"""
    first = generate.generate_dataset(tmp_path / "a.csv", 300, seed=7)
    second = generate.generate_dataset(tmp_path / "b.csv", 300, seed=7)
    assert first.read_bytes() == second.read_bytes()


def test_worker_run_once_returns_all_measurements(tmp_path):
    """单次运行器返回协议要求的五项测量，并真的写出导出文件。"""
    dataset = generate.generate_dataset(tmp_path / "data.csv", 100)
    output = tmp_path / "out.csv"
    result = worker.run_once(dataset, output)
    assert set(result) == {
        "dataset",
        "engine",
        "rows",
        "rows_before",
        "input_bytes",
        "output_bytes",
        "total_seconds",
        "read_seconds",
        "clean_seconds",
        "export_seconds",
        "peak_mb",
    }
    assert result["engine"] == "pandas"
    assert int(result["rows_before"]) == 100
    assert int(result["rows"]) <= 100
    assert float(result["peak_mb"]) > 0
    assert float(result["total_seconds"]) > 0
    assert output.exists() and output.stat().st_size == int(result["output_bytes"])
    back = pd.read_csv(output, dtype=str, keep_default_na=False)
    assert list(back.columns)[:3] == ["订单号", "客户", "含税金额"]


def test_worker_run_once_records_polars_engine(tmp_path):
    """P1 信息性对比：worker 支持显式 polars，并记录 engine 字段。"""
    dataset = generate.generate_dataset(tmp_path / "data.csv", 100)
    pandas_output = tmp_path / "pandas.csv"
    polars_output = tmp_path / "polars.csv"
    pandas_result = worker.run_once(dataset, pandas_output, engine="pandas")
    polars_result = worker.run_once(dataset, polars_output, engine="polars")
    assert pandas_result["engine"] == "pandas"
    assert polars_result["engine"] == "polars"
    assert pandas_output.read_bytes() == polars_output.read_bytes()
    assert int(polars_result["rows"]) == int(pandas_result["rows"])


def test_verify_engines_compares_real_bytes_and_report(tmp_path):
    """G-05 冒烟：真实 CSV 字节上 pandas / Polars 的帧、报告与导出字节一致。"""
    dataset = generate.generate_dataset(tmp_path / "data.csv", 1200)
    result = verify_engines.verify_dataset("data", dataset)
    assert result.rows_before == 1200
    assert result.rows_after <= result.rows_before
    assert result.csv_bytes > 0
    assert result.effective_engine in {"pandas", "polars"}


def test_run_main_writes_reproducible_baseline_table(tmp_path, capsys):
    """运行器能生成数据、跑子进程、写带环境信息与历史列的 BASELINE.md。"""
    data_dir = tmp_path / "data"
    out_dir = tmp_path / "out"
    baseline = tmp_path / "BASELINE.md"
    code = run.main(
        [
            "--dataset",
            "small",
            "--repeat",
            "1",
            "--data-dir",
            str(data_dir),
            "--out-dir",
            str(out_dir),
            "--baseline",
            str(baseline),
            "--engines",
            "pandas",
            "polars",
        ]
    )
    assert code == 0
    text = baseline.read_text(encoding="utf-8")
    assert "# table-cleaner benchmark 基线" in text
    assert "| Python |" in text and "| pandas |" in text and "| polars |" in text
    assert "| small | pandas | 10,000 |" in text
    assert "| small | polars | 10,000 |" in text
    assert "未执行" in text
    assert "每项 1 次取中位数" in text
    assert "第 1/1 次" in capsys.readouterr().out


def test_run_main_rejects_non_positive_repeat(tmp_path, capsys):
    code = run.main(["--repeat", "0", "--data-dir", str(tmp_path / "data")])
    assert code == 2
    assert "必须大于等于 1" in capsys.readouterr().err


def test_worker_main_reports_chinese_error_for_bad_dataset(tmp_path, capsys):
    assert worker.main(["--dataset", str(tmp_path / "missing.csv"), "--output", str(tmp_path / "o.csv")]) == 1
    assert "benchmark 失败" in capsys.readouterr().err
