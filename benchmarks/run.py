"""benchmark 运行器：生成/复用固定数据集，每档跑 3 次取中位数，写 BASELINE.md。

用法（在仓库根目录）：

    python -m benchmarks.run --dataset all --repeat 3
    python -m benchmarks.run --dataset small --repeat 3   # 只复跑小档

如需重新生成数据：``python -m benchmarks.generate --force``。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from benchmarks import generate

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_RESULT_KEYS = ("total_seconds", "read_seconds", "clean_seconds", "export_seconds", "peak_mb")


def _read_first_line(path: Path, needle: str) -> str:
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if needle in line:
                return line.split(":", 1)[1].strip()
    except OSError:
        return ""
    return ""


def _memory_total_mb() -> float:
    text = _read_first_line(Path("/proc/meminfo"), "MemTotal")
    try:
        return int(text.split()[0]) / 1024
    except (IndexError, ValueError):
        return 0.0


def environment_info() -> dict[str, str]:
    """记录协议要求的环境信息：Python、pandas、OS、CPU、内存。"""
    import numpy
    import openpyxl
    import pandas

    return {
        "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Python": platform.python_version(),
        "pandas": pandas.__version__,
        "numpy": numpy.__version__,
        "openpyxl": openpyxl.__version__,
        "操作系统": platform.platform(),
        "CPU": _read_first_line(Path("/proc/cpuinfo"), "model name") or platform.processor() or "未知",
        "CPU 核数": str(os.cpu_count() or 0),
        "内存": f"{_memory_total_mb():.0f} MB",
    }


def _run_worker(dataset: Path, output: Path) -> dict[str, object]:
    command = [
        sys.executable,
        "-m",
        "benchmarks.worker",
        "--dataset",
        str(dataset),
        "--output",
        str(output),
    ]
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"benchmark 子进程失败（{dataset.name}）：{completed.stderr.strip()[:500]}")
    lines = [line for line in completed.stdout.splitlines() if line.strip().startswith("{")]
    if not lines:
        raise RuntimeError(f"benchmark 子进程没有输出结果：{dataset.name}")
    return json.loads(lines[-1])


def _median(values: list[float]) -> float:
    return float(statistics.median(values))


def render_baseline(results: list[dict[str, object]], environment: dict[str, str], repeat: int) -> str:
    """把运行结果渲染成 Markdown；结果表保留版本列，后续版本只追加不覆盖。"""
    lines = [
        "# table-cleaner benchmark 基线",
        "",
        "> 本文件由 `python -m benchmarks.run` 实测生成，数字来自命令输出，不是估算值。",
        "> 本版只建立 pandas 基线，不设提升目标；跨机器比较只比相对值。",
        "",
        "## 环境",
        "",
        "| 项目 | 值 |",
        "| --- | --- |",
    ]
    for key, value in environment.items():
        lines.append(f"| {key} | {value} |")
    lines.extend(
        [
            "",
            "## 协议",
            "",
            "- 数据集：小 1 万行 / 中 10 万行 / 大 50 万行；固定随机种子 `20231106`。",
            "- 每档四列：18 位长编号（含少量完全重复行）、普通文本（含空值）、"
            "脏金额（普通数值 / 千分位 / 货币符号 / 空值）、混合日期（三种写法 + 非法日期）。",
            "- 标准流水线：读取 → 全行去重 → 按订单号去重 → 删除客户名称为空的行 → "
            "金额转数字 → 日期转日期 → 客户名称改名为客户 → 计算含税金额（金额*1.13）→ "
            "列顺序（订单号、客户、含税金额）→ 导出 CSV（quote 公式防护）。",
            "- 测量项：端到端、读取、清洗、导出耗时与进程内存峰值；每项跑 3 次取中位数。",
            "- 内存峰值取子进程 `resource.getrusage(RUSAGE_SELF).ru_maxrss`；计时用 `time.perf_counter()`。",
            "- 50 万行 CSV 必然超过 10 MB 网页上传上限，benchmark 只放宽「上传字节数」一项；"
            "行/列/单元格上限保持现行值（50 万行 × 4 列 = 200 万单元格，正好等于上限）。",
            "- 历史列：v2.0.1 / v2.2.0 未建立 benchmark 套件，无法补测，"
            "对应列为「未执行」；本表是首个基线，后续版本只追加列、不覆盖本列。",
            "",
            f"## 结果（每项 {repeat} 次取中位数）",
            "",
            "| 数据集 | 输入行数 | 清洗后行数 | 输入大小(MB) | 端到端(s) | 读取(s) | 清洗(s) | 导出(s) | 进程内存峰值(MB) | v2.0.1 基线 | v2.2.0 基线 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for result in results:
        runs = result["runs"]
        assert isinstance(runs, list)
        rows_before = int(runs[0]["rows_before"])
        rows_after = int(runs[0]["rows"])
        input_mb = float(runs[0]["input_bytes"]) / 1024 / 1024
        medians = {key: _median([float(run[key]) for run in runs]) for key in _RESULT_KEYS}
        lines.append(
            f"| {result['name']} | {rows_before:,} | {rows_after:,} | {input_mb:.1f} | {medians['total_seconds']:.3f} | "
            f"{medians['read_seconds']:.3f} | {medians['clean_seconds']:.3f} | "
            f"{medians['export_seconds']:.3f} | {medians['peak_mb']:.1f} | 未执行 | 未执行 |"
        )

    lines.extend(
        [
            "",
            "## 每次运行明细",
            "",
            "| 数据集 | 第几次 | 端到端(s) | 读取(s) | 清洗(s) | 导出(s) | 内存峰值(MB) |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for result in results:
        for index, run in enumerate(result["runs"], 1):
            lines.append(
                f"| {result['name']} | {index} | {float(run['total_seconds']):.3f} | "
                f"{float(run['read_seconds']):.3f} | {float(run['clean_seconds']):.3f} | "
                f"{float(run['export_seconds']):.3f} | {float(run['peak_mb']):.1f} |"
            )
    lines.extend(
        [
            "",
            "## 复跑方法",
            "",
            "```bash",
            "# 1. 生成/复用固定数据（小/中/大）",
            "python -m benchmarks.generate",
            "# 2. 每档跑 3 次，写回本文件",
            "python -m benchmarks.run --dataset all --repeat 3",
            "```",
            "",
            f"> 本次重复次数：{repeat}；结果表只保留中位数，逐次明细见上一节。",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行 table-cleaner benchmark 并写 BASELINE.md")
    parser.add_argument(
        "--dataset",
        choices=(*generate.SIZES, "all"),
        default="all",
        help="跑哪一档；默认 all",
    )
    parser.add_argument("--repeat", type=int, default=3, help="每档重复次数，默认 3")
    parser.add_argument("--data-dir", type=Path, default=Path("benchmarks/data"), help="数据集目录")
    parser.add_argument("--out-dir", type=Path, default=Path("benchmarks/out"), help="导出结果目录")
    parser.add_argument("--baseline", type=Path, default=Path("benchmarks/BASELINE.md"), help="基线文件路径")
    parser.add_argument("--force-data", action="store_true", help="重新生成数据集")
    args = parser.parse_args(argv)
    if args.repeat < 1:
        sys.stderr.write("参数错误：--repeat 必须大于等于 1。\n")
        return 2
    names = list(generate.SIZES) if args.dataset == "all" else [args.dataset]
    sizes = {name: generate.SIZES[name] for name in names}
    try:
        paths = generate.ensure_datasets(args.data_dir, sizes, force=args.force_data)
    except OSError as exc:
        sys.stderr.write(f"生成数据集失败：{exc}\n")
        return 1

    results: list[dict[str, object]] = []
    for name in names:
        path = paths[name]
        sys.stdout.write(f"运行 {name}（{generate.SIZES[name]:,} 行，{path.stat().st_size / 1024 / 1024:.1f} MB）...\n")
        sys.stdout.flush()
        runs: list[dict[str, object]] = []
        for index in range(args.repeat):
            output = args.out_dir / f"{name}.csv"
            try:
                run = _run_worker(path, output)
            except RuntimeError as exc:
                sys.stderr.write(f"{exc}\n")
                return 1
            runs.append(run)
            sys.stdout.write(
                f"  第 {index + 1}/{args.repeat} 次：端到端 {float(run['total_seconds']):.3f}s，"
                f"内存峰值 {float(run['peak_mb']):.1f} MB\n"
            )
            sys.stdout.flush()
        results.append({"name": name, "path": str(path), "runs": runs})

    environment = environment_info()
    baseline = render_baseline(results, environment, args.repeat)
    try:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(baseline, encoding="utf-8")
    except OSError as exc:
        sys.stderr.write(f"写入基线失败：{exc}\n")
        return 1
    sys.stdout.write(f"基线已写入：{args.baseline}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
