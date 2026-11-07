"""benchmark 单次运行器：在一个独立进程里跑固定流水线并输出 JSON。

由 ``benchmarks.run`` 启动；每个进程只跑一次，进程退出前用
``resource.getrusage(RUSAGE_SELF).ru_maxrss`` 记录进程内存峰值。
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from pathlib import Path

import cleaning
from cleaning.limits import Limits

# 只放宽「上传文件大小」这一项：50 万行 CSV 必然超过 10 MB 的网页上传上限，
# 但行/列/单元格上限保持现行值（50 万行 × 4 列 = 200 万单元格，正好等于上限）。
_BENCH_UPLOAD_BYTES = 512 * 1024 * 1024


def standard_options() -> cleaning.CleanOptions:
    """固定标准流水线：覆盖去重、删空值、类型转换、重命名、计算列、列顺序。"""
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


def run_once(dataset: Path, output: Path, *, engine: str = "pandas") -> dict[str, object]:
    """跑一次读取→清洗→导出，返回各项耗时（秒）与进程内存峰值（MB）。

    ``engine`` 只用于 P1 信息性对比（pandas / polars）；不设性能门禁，默认仍是 pandas。
    """
    limits = Limits.from_env().with_overrides(max_upload_bytes=_BENCH_UPLOAD_BYTES)
    started = time.perf_counter()
    read_started = started
    raw = dataset.read_bytes()
    loaded = cleaning.load_table(dataset.name, raw, limits=limits)
    read_seconds = time.perf_counter() - read_started

    clean_started = time.perf_counter()
    report = cleaning.clean_table(loaded.df, standard_options(), engine=engine)
    clean_seconds = time.perf_counter() - clean_started

    export_started = time.perf_counter()
    content = cleaning.to_csv_bytes(report.df, neutralize_formulas="quote")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(content)
    export_seconds = time.perf_counter() - export_started
    total_seconds = time.perf_counter() - started

    peak_kb = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return {
        "dataset": dataset.stem,
        "engine": engine,
        "rows": int(len(report.df)),
        "rows_before": int(len(loaded.df)),
        "input_bytes": len(raw),
        "output_bytes": len(content),
        "total_seconds": total_seconds,
        "read_seconds": read_seconds,
        "clean_seconds": clean_seconds,
        "export_seconds": export_seconds,
        "peak_mb": peak_kb / 1024,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="benchmark 单次运行器（一般由 benchmarks.run 调用）")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--engine",
        choices=("pandas", "polars"),
        default="pandas",
        help="清洁引擎；默认 pandas。Polars 结果只作信息性对比，不设性能门禁。",
    )
    args = parser.parse_args(argv)
    try:
        result = run_once(args.dataset, args.output, engine=args.engine)
    except (cleaning.TableError, OSError) as exc:
        sys.stderr.write(f"benchmark 失败：{exc}\n")
        return 1
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
