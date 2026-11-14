"""v2.5.0 交互计时 harness：用 AppTest 真实跑 ``app.py``（不进 CI 门禁）。

规划 4.1 的固定口径：

- fixture：50,000 行 × 12 列、种子 20261114，现场按固定规则生成，不提交大文件；
- 流程：冷启动 → 上传并 run（冷路径）→ 无改动 rerun → 勾选 2 个行规则并选
  2 个空值/去重列 → 在导出区切换公式防护模式 → 点一次「生成下载文件」；
- 判定：每个 warm 交互取 5 次中位数；导出点击单独记录，不计入 1 秒门槛。

同一份脚本可以对着 v2.4.0 的独立 worktree 跑 baseline（``--app`` 指向那边的
``app.py``，并在该目录运行），保证同机、同 fixture、同命令。

用法（仓库根目录）::

    python benchmarks/app_interaction.py --json-out /tmp/app-new.json
    python benchmarks/app_interaction.py --app /tmp/v240/app.py --json-out /tmp/app-base.json
    python benchmarks/app_interaction.py --memory           # 10 MB / 50k 内存峰值

注意：AppTest 每次 ``run()`` 都会完整重跑脚本，不能用来验证 fragment 只重跑
导出区；fragment 的隔离性由真实浏览器行为保证，AppTest 只验证兼容性（不报错、
按钮和下载可用）。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import platform
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

SEED = 20261114
DEFAULT_ROWS = 50_000
REPS = 5
FIXTURE_NAME = "交互测试_50000行.csv"
COLUMNS = (
    "订单号",
    "客户",
    "金额",
    "单价",
    "折扣",
    "数量",
    "下单日期",
    "状态",
    "备注",
    "地址",
    "电话",
    "渠道",
)
_CUSTOMERS = (
    "晨光小吃店",
    "星语文具",
    "山泉花店",
    "远航网店",
    "青禾杂货",
    "麦田烘焙",
    "云朵咖啡",
    "木棉服饰",
)
_CITIES = ("杭州", "上海", "成都", "广州", "武汉", "西安", "南京", "苏州")
_STATUSES = ("已完成", "待付款", "已发货", "已退款")
_CHANNELS = ("天猫", "京东", "抖音", "拼多多")


def _amount(rng: random.Random, index: int) -> str:
    """脏金额：普通小数、千分位、货币符号、空值，和规划 fixture 一致。"""
    if index % 97 == 0:
        return ""
    value = rng.uniform(0.5, 9999.5)
    if index % 20 == 0:
        return f"{value:,.2f}"
    if index % 50 == 0:
        return f"¥{value:,.2f}"
    return f"{value:.2f}"


def _make_row(rng: random.Random, index: int) -> list[str]:
    # 每 997 行复制上一行，制造完全重复行；订单号按 500 行成对，给按列去重留样本。
    order = f"SO{(index - index % 500):06d}"
    return [
        order,
        _CUSTOMERS[index % len(_CUSTOMERS)],
        _amount(rng, index),
        f"{rng.uniform(1, 500):.2f}",
        ("0.9" if index % 3 == 0 else "0.85" if index % 3 == 1 else ""),
        str(1 + index % 5),
        (
            "2024-13-40"
            if index % 101 == 0
            else f"2026-{1 + index % 12:02d}-{1 + index % 28:02d}"
            if index % 2 == 0
            else f"2026/{1 + index % 12:02d}/{1 + index % 28:02d}"
        ),
        _STATUSES[index % len(_STATUSES)],
        "=1+1" if index % 997 == 0 else ("正常备注" if index % 3 else ""),
        f"{_CITIES[index % len(_CITIES)]}市示例路 {index % 300} 号",
        f"138{index % 100_000_000:08d}",
        _CHANNELS[index % len(_CHANNELS)],
    ]


def build_fixture(rows: int = DEFAULT_ROWS, seed: int = SEED) -> tuple[str, bytes]:
    """按固定种子生成 50k×12 的 CSV；同一参数每次得到相同字节。"""
    rng = random.Random(seed)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    previous: list[str] | None = None
    for index in range(rows):
        row = previous if previous is not None and index % 997 == 1 else _make_row(rng, index)
        writer.writerow(row)
        previous = row
    return FIXTURE_NAME, buffer.getvalue().encode("utf-8-sig")


def _by_label(elements: Any, label: str) -> Any:
    """按 label 前缀找唯一控件；找不到/多个都直接报错，避免点错地方当测量。"""
    matches = [element for element in elements if element.label.startswith(label)]
    if len(matches) != 1:
        raise RuntimeError(f"标签以「{label}」开头的控件应唯一，实际 {[e.label for e in matches]}")
    return matches[0]


def _new_app(app_path: Path) -> AppTest:
    """新会话 + 冷缓存，模拟第一次打开页面。"""
    import streamlit as st

    st.cache_data.clear()
    at = AppTest.from_file(str(app_path), default_timeout=300).run()
    if list(at.exception):
        raise RuntimeError(f"页面启动异常：{list(at.exception)}")
    return at


def _timed_run(at: AppTest) -> float:
    started = time.perf_counter()
    at.run()
    elapsed = time.perf_counter() - started
    if list(at.exception):
        raise RuntimeError(f"run() 出现异常：{list(at.exception)}")
    return elapsed


def _stats(samples: list[float]) -> dict[str, Any]:
    ordered = sorted(samples)
    return {
        "samples": [round(value, 4) for value in samples],
        "median": round(statistics.median(samples), 4),
        "p90": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))], 4),
        "min": round(min(samples), 4),
        "max": round(max(samples), 4),
    }


def _toggle_times(at: AppTest, label: str, reps: int) -> list[float]:
    """反复勾选/取消同一个 checkbox；每次都是一次真实的规则变化交互。"""
    samples: list[float] = []
    for _ in range(reps):
        checkbox = _by_label(at.checkbox, label)
        checkbox.set_value(not checkbox.value)
        samples.append(_timed_run(at))
    return samples


def measure(app_path: Path, rows: int = DEFAULT_ROWS, reps: int = REPS) -> dict[str, Any]:
    """跑规划 4.1 的 5 步流程，返回计时 JSON。"""
    name, data = build_fixture(rows)
    result: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "app": str(app_path),
        "fixture": {"name": name, "rows": rows, "columns": len(COLUMNS), "seed": SEED},
        "fixture_bytes": len(data),
        "fixture_sha256": hashlib.sha256(data).hexdigest(),
    }

    at = _new_app(app_path)
    cold_started = time.perf_counter()
    at.get("file_uploader")[0].upload(name, data, "text/csv")
    at.run()
    result["cold_upload_s"] = round(time.perf_counter() - cold_started, 4)
    if list(at.exception):
        raise RuntimeError(f"冷上传异常：{list(at.exception)}")

    # 2. 无改动 rerun
    noop = [_timed_run(at) for _ in range(reps)]
    result["warm_noop"] = _stats(noop)

    # 3. 勾选/取消行规则；选列只做一次（选中状态保留），随后反复勾选才可重复计时
    result["warm_rule_duplicates"] = _stats(_toggle_times(at, "删除完全重复的行", reps))

    missing = _by_label(at.checkbox, "删除指定列中有空值的行")
    missing.check()
    _timed_run(at)
    _by_label(at.multiselect, "选择要检查空值的列").select("备注")
    _timed_run(at)
    result["warm_rule_missing"] = _stats(_toggle_times(at, "删除指定列中有空值的行", reps))

    dedupe = _by_label(at.checkbox, "按指定列去重")
    dedupe.check()
    _timed_run(at)
    _by_label(at.multiselect, "选择去重用的列").select("订单号")
    _timed_run(at)
    result["warm_rule_dedupe"] = _stats(_toggle_times(at, "按指定列去重", reps))

    # 4. 导出区切换公式防护模式（quote ↔ zwsp）
    formula_samples: list[float] = []
    for index in range(reps):
        radio = _by_label(at.radio, "导出 CSV 时的公式防护")
        radio.set_value("zwsp" if index % 2 == 0 else "quote")
        formula_samples.append(_timed_run(at))
    result["warm_formula_mode"] = _stats(formula_samples)

    warm_samples = [
        *noop,
        *result["warm_rule_duplicates"]["samples"],
        *result["warm_rule_missing"]["samples"],
        *result["warm_rule_dedupe"]["samples"],
        *formula_samples,
    ]
    result["warm_all"] = _stats(warm_samples)

    # 5. 点一次生成下载：单独记录，不设 1 秒门槛
    generate = _by_label(at.button, "生成下载文件")
    generate.click()
    result["export_click_s"] = round(_timed_run(at), 4)
    result["download_buttons"] = [button.label for button in at.get("download_button")]
    if not result["download_buttons"]:
        raise RuntimeError("点了生成下载文件但没有出现下载按钮")
    return result


def _rss_mb(field: str = "VmRSS") -> float:
    """读 /proc/self/status 的当前内存或峰值（VmHWM），单位 MB。"""
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{field}:"):
                return int(line.split()[1]) / 1024
    except OSError:
        return 0.0
    return 0.0


def _memory_fixture(case: str) -> tuple[str, bytes]:
    """内存探针 fixture：10 MB 档要 > 8 MB（绕过解析缓存）且 < 10 MB 上传上限。"""
    if case == "10mb":
        # 80k 行约 9.1 MB；再给备注补少量空格推到约 9.7 MB，确保走「>8 MB 跳过缓存」分支。
        rows = 80_000
        target = 9_700_000
        name, data = build_fixture(rows)
        pad = max(0, (target - len(data)) // rows)
        if pad:
            rng = random.Random(SEED)
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\n")
            writer.writerow(COLUMNS)
            previous = None
            for index in range(rows):
                row = previous if previous is not None and index % 997 == 1 else _make_row(rng, index)
                row = list(row)
                row[8] = f"{row[8]}{'x' * pad}"
                writer.writerow(row)
                previous = row
            data = buffer.getvalue().encode("utf-8-sig")
        return name, data
    return build_fixture(DEFAULT_ROWS)


def _memory_child(case: str) -> None:
    """独立子进程里测量峰值，避免多个档位共用同一个进程峰值。"""
    name, data = _memory_fixture(case)
    if case == "10mb":
        assert 8 * 1024 * 1024 < len(data) < 10 * 1024 * 1024, len(data)
    before_rss, before_hwm = _rss_mb(), _rss_mb("VmHWM")
    at = _new_app(PROJECT_ROOT / "app.py")
    started = time.perf_counter()
    at.get("file_uploader")[0].upload(name, data, "text/csv")
    at.run()
    first = time.perf_counter() - started
    if list(at.exception):
        raise RuntimeError(f"内存探针上传异常：{list(at.exception)}")
    if not {metric.label for metric in at.metric} >= {"行数", "列数"}:
        raise RuntimeError("内存探针上传后没有渲染行数/列数，可能被上限拒绝")
    _timed_run(at)
    print(
        json.dumps(
            {
                "case": case,
                "fixture_bytes": len(data),
                "first_render_s": round(first, 4),
                "rss_before_mb": round(before_rss, 1),
                "rss_after_mb": round(_rss_mb(), 1),
                "hwm_before_mb": round(before_hwm, 1),
                "hwm_after_mb": round(_rss_mb("VmHWM"), 1),
                "hwm_delta_mb": round(_rss_mb("VmHWM") - before_hwm, 1),
            }
        )
    )


def measure_memory() -> dict[str, Any]:
    """两个档位各起一个子进程测峰值；返回父进程汇总。"""
    results = []
    for case in ("50k", "10mb"):
        completed = subprocess.run(
            [sys.executable, __file__, "--memory-child", case],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            check=True,
            timeout=600,
        )
        results.append(json.loads(completed.stdout.strip().splitlines()[-1]))
    return {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "cases": results}


def _environment() -> dict[str, str]:
    import openpyxl
    import pandas

    return {
        "python": platform.python_version(),
        "pandas": pandas.__version__,
        "openpyxl": openpyxl.__version__,
        "os": platform.platform(),
        "cpu_count": str(os.cpu_count() or 0),
    }


def render_markdown(report: dict[str, Any]) -> str:
    """把 JSON 渲染成可直接贴进 TEST_REPORT / PR 的 Markdown。"""
    lines = [
        "## AppTest 交互计时",
        "",
        f"- 时间：{report['generated_at']}",
        f"- app：`{report['app']}`",
        f"- fixture：{report['fixture']['rows']:,} 行 × {report['fixture']['columns']} 列，"
        f"{report['fixture_bytes'] / 1024 / 1024:.2f} MB，sha256 `{report['fixture_sha256'][:16]}…`",
        "",
        "| 场景 | 中位数(s) | p90(s) | 样本(s) |",
        "| --- | ---: | ---: | --- |",
        f"| 冷上传 + 首次渲染 | {report['cold_upload_s']:.3f} | — | — |",
        f"| 无改动 rerun | {report['warm_noop']['median']:.3f} | {report['warm_noop']['p90']:.3f} | "
        f"{report['warm_noop']['samples']} |",
        f"| 行规则：删除完全重复的行 | {report['warm_rule_duplicates']['median']:.3f} | "
        f"{report['warm_rule_duplicates']['p90']:.3f} | {report['warm_rule_duplicates']['samples']} |",
        f"| 行规则：删除空值行（已选备注） | {report['warm_rule_missing']['median']:.3f} | "
        f"{report['warm_rule_missing']['p90']:.3f} | {report['warm_rule_missing']['samples']} |",
        f"| 行规则：按列去重（已选订单号） | {report['warm_rule_dedupe']['median']:.3f} | "
        f"{report['warm_rule_dedupe']['p90']:.3f} | {report['warm_rule_dedupe']['samples']} |",
        f"| 切换公式防护模式 | {report['warm_formula_mode']['median']:.3f} | "
        f"{report['warm_formula_mode']['p90']:.3f} | {report['warm_formula_mode']['samples']} |",
        f"| **warm 交互合计** | **{report['warm_all']['median']:.3f}** | "
        f"{report['warm_all']['p90']:.3f} | {report['warm_all']['samples']} |",
        f"| 导出点击（单独记录） | {report['export_click_s']:.3f} | — | — |",
        "",
        f"- 下载按钮：{report['download_buttons']}",
        f"- 环境：{report.get('environment', {})}",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=PROJECT_ROOT / "app.py", help="要测的 app.py")
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    parser.add_argument("--reps", type=int, default=REPS)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None, help="Markdown 输出路径")
    parser.add_argument("--memory", action="store_true", help="只跑 10 MB / 50k 内存峰值探针")
    parser.add_argument("--memory-child", choices=("50k", "10mb"), default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.memory_child:
        _memory_child(args.memory_child)
        return
    if args.memory:
        report = measure_memory()
        text = json.dumps(report, ensure_ascii=False, indent=2)
        print(text)
        if args.json_out:
            args.json_out.write_text(text + "\n", encoding="utf-8")
        return

    report = measure(args.app, rows=args.rows, reps=args.reps)
    report["environment"] = _environment()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n", encoding="utf-8")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(render_markdown(report), encoding="utf-8")


if __name__ == "__main__":
    main()
