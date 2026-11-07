"""双引擎端到端一致性校验（G-05）：真实 benchmark 数据上比较 pandas / Polars。

标准流水线（读取 → 去重 → 删空值 → 转数字 → 转日期 → 改名 → 计算列 → 列顺序 → 导出）
在真实小/中/大三档数据上分别跑 pandas 与 ``engine="polars"``，要求：

- 清洗后的 DataFrame、steps、notes、removed 完全一致；
- 导出的 CSV 字节完全一致。

本脚本只作发布门禁证据，不设性能门禁；Polars 在混合格式/脏金额等边界会按登记表
整条流水线回落 pandas，脚本同时打印实际引擎与回落节点，便于核对不是「静默切换」。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import cleaning
import pandas as pd
from cleaning.engine import get_engine

from benchmarks import generate
from benchmarks.worker import standard_options

_BENCH_UPLOAD_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True)
class Verification:
    """一档数据的双引擎对照结果。"""

    dataset: str
    rows_before: int
    rows_after: int
    effective_engine: str
    fallback_nodes: tuple[str, ...]
    csv_bytes: int


def _limits() -> cleaning.Limits:
    return cleaning.Limits.from_env().with_overrides(max_upload_bytes=_BENCH_UPLOAD_BYTES)


def compare_engines(filename: str, raw: bytes, options: cleaning.CleanOptions | None = None) -> Verification:
    """在给定文件字节上比较 pandas 与 Polars 的清洗结果与 CSV 导出字节。"""
    options = options or standard_options()
    loaded = cleaning.load_table(filename, raw, limits=_limits())

    pandas_report = cleaning.clean_table(loaded.df, options, engine="pandas")
    polars_report = cleaning.clean_table(loaded.df, options, engine="polars")

    pd.testing.assert_frame_equal(polars_report.df, pandas_report.df)
    pd.testing.assert_frame_equal(polars_report.removed, pandas_report.removed)
    assert polars_report.steps == pandas_report.steps
    assert polars_report.notes == pandas_report.notes

    pandas_csv = cleaning.to_csv_bytes(pandas_report.df, neutralize_formulas="quote")
    polars_csv = cleaning.to_csv_bytes(polars_report.df, neutralize_formulas="quote")
    assert pandas_csv == polars_csv

    decision = get_engine("polars").preflight(loaded.df, options)
    return Verification(
        dataset=filename,
        rows_before=int(len(loaded.df)),
        rows_after=int(len(polars_report.df)),
        effective_engine=decision.engine,
        fallback_nodes=decision.fallback_nodes,
        csv_bytes=len(polars_csv),
    )


def verify_dataset(name: str, path: Path) -> Verification:
    """读取一档 CSV 并调用 :func:`compare_engines`。"""
    return compare_engines(path.name, path.read_bytes())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="比较 pandas / Polars 在 benchmark 数据上的端到端一致性")
    parser.add_argument(
        "--sizes",
        nargs="+",
        choices=tuple(generate.SIZES),
        default=list(generate.SIZES),
        help="校验哪些档位；默认小/中/大全部",
    )
    parser.add_argument("--data-dir", type=Path, default=Path("benchmarks/data"), help="数据集目录")
    args = parser.parse_args(argv)

    paths = generate.ensure_datasets(args.data_dir, {name: generate.SIZES[name] for name in args.sizes})
    for name in args.sizes:
        path = paths[name]
        try:
            result = verify_dataset(name, path)
        except (AssertionError, cleaning.TableError, OSError) as exc:
            sys.stderr.write(f"{name} 双引擎校验失败：{exc}\n")
            return 1
        extent = f"，回落节点 {'、'.join(result.fallback_nodes)}" if result.fallback_nodes else "，无整节点回落"
        sys.stdout.write(
            f"{name}: {result.rows_before:,} → {result.rows_after:,} 行，CSV {result.csv_bytes:,} 字节，"
            f"实际引擎 {result.effective_engine}{extent}；结果与报告一致。\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
