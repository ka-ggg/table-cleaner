"""v2.5.0 金标采集脚本：在 v2.4.0 基线（``83a66ee``）的独立 worktree 运行。

规划 4.2 的口径：

- CSV：fixture × 3 种公式防护模式的 ``sha256`` 必须逐字节一致；
- XLSX：解包后除 ``docProps/core.xml``（写入时间）外，每个条目的字节必须一致；
- CleanReport：``steps`` / ``removed`` / ``notes`` 字段级一致；
- App 级：至少 1 个 AppTest 端到端用例导出的 CSV 命中同一金标哈希。

用法（在基线 worktree 根目录）::

    python benchmarks/capture_golden.py \
        --out tests/fixtures/v250/golden.json \
        --fixtures-dir tests/fixtures/v250

生成后把 ``golden.json`` 与 fixture 一起提交；v2.5 的测试只读金标，不再依赖
网络或旧工作区。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import platform
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import cleaning  # noqa: E402
import openpyxl  # noqa: E402
import pandas as pd  # noqa: E402

SMALL_MIXED_NAME = "small_mixed.csv"
MULTI_SHEET_NAME = "multi_sheet.xlsx"

# 12 列 × 9 行：空值、完全重复、公式开头、18 位长编号、混时区日期都在里面。
_SMALL_MIXED_ROWS: tuple[tuple[str, ...], ...] = (
    (
        "订单号",
        "客户",
        "金额",
        "单价",
        "折扣",
        "数量",
        "下单日期",
        "状态",
        "备注",
        "长编号",
        "电话",
        "渠道",
    ),
    (
        "A001",
        "晨光小吃店",
        "128.00",
        "10.5",
        "0.9",
        "2",
        "2026-01-02",
        "已完成",
        "送货前电话",
        "123456789012345678",
        "13800001111",
        "天猫",
    ),
    (
        "A001",
        "晨光小吃店",
        "128.00",
        "10.5",
        "0.9",
        "2",
        "2026-01-02",
        "已完成",
        "送货前电话",
        "123456789012345678",
        "13800001111",
        "天猫",
    ),
    (
        "A002",
        "星语文具",
        "0",
        "20",
        "九折",
        "1",
        "2026/01/03",
        "待付款",
        "",
        "123456789012345679",
        "13800002222",
        "京东",
    ),
    (
        "A003",
        "山泉花店",
        "¥99.00",
        "5.5",
        "",
        "3",
        "2026年01月04日",
        "已发货",
        "=1+1",
        "00123",
        "13800003333",
        "抖音",
    ),
    (
        "A004",
        "远航网店",
        "",
        "",
        "abc",
        "",
        "2026-13-40",
        "未知",
        " 前后空格 ",
        "123456789012345680",
        "",
        "拼多多",
    ),
    (
        "A005",
        "青禾杂货",
        "1,280.00",
        "0.85",
        "0.85",
        "4",
        "2026-01-05T10:00:00+08:00",
        "已完成",
        "@SUM(A1)",
        "123456789012345681",
        "13800004444",
        "天猫",
    ),
    (
        "A006",
        "麦田烘焙",
        "66.60",
        "3.3",
        "0.9",
        "2",
        "2026-01-06T10:00:00Z",
        "已退款",
        "",
        "123456789012345682",
        "13800005555",
        "京东",
    ),
    (
        "A007",
        "云朵咖啡",
        "88",
        "4.4",
        "",
        "1",
        "2026/02/30",
        "已完成",
        "",
        "123456789012345683",
        "13800006666",
        "抖音",
    ),
    (
        "A008",
        "木棉服饰",
        "12.30",
        "1.23",
        "0.5",
        "10",
        "2026-01-07",
        "+1",
        '=HYPERLINK("x")',
        "123456789012345684",
        "13800007777",
        "拼多多",
    ),
)

_MULTI_SHEET_FRAMES = {
    "订单": pd.DataFrame(
        {
            "订单号": ["A001", "A001", "A002"],
            "金额": ["128.00", "128.00", "0"],
            "备注": ["有货", "有货", ""],
        }
    ),
    "客户": pd.DataFrame({"客户编号": ["C1", "C2"], "客户名称": ["晨光小吃店", "星语文具"]}),
}


def small_mixed_bytes() -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows(_SMALL_MIXED_ROWS)
    return buffer.getvalue().encode("utf-8-sig")


def multi_sheet_bytes() -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for sheet, frame in _MULTI_SHEET_FRAMES.items():
            frame.to_excel(writer, sheet_name=sheet, index=False)
    return buffer.getvalue()


def _records(frame: pd.DataFrame) -> list[dict[str, str]]:
    """把 DataFrame 转成可 JSON 序列化、跨 pandas 版本稳定的字符串记录。"""
    return frame.fillna("").astype(str).to_dict(orient="records")


def report_snapshot(report: cleaning.CleanReport) -> dict[str, Any]:
    return {
        "steps": dict(report.steps),
        "removed": _records(report.removed),
        "notes": list(report.notes),
    }


def _xlsx_entries(data: bytes) -> dict[str, str]:
    """按条目算 sha256；忽略 ZIP 时间戳与 docProps/core.xml 的写入时间。"""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {
            name: hashlib.sha256(archive.read(name)).hexdigest()
            for name in sorted(archive.namelist())
            if name != "docProps/core.xml"
        }


def _options(*, rules: bool) -> cleaning.CleanOptions:
    if not rules:
        return cleaning.CleanOptions()
    return cleaning.CleanOptions(
        header_row=1,
        drop_duplicates=True,
        dedupe_on=("订单号",),
        drop_missing_in=("备注",),
        columns=cleaning.ColumnOps(
            rename=(("金额", "总金额"),),
            drop=("渠道",),
            order=("订单号", "客户", "总金额"),
            to_number=("金额", "单价"),
            to_date=("下单日期",),
            computed=(("含税金额", "总金额*1.13"),),
        ),
    )


def cleaning_cases(fixture: bytes) -> dict[str, Any]:
    loaded = cleaning.load_table(SMALL_MIXED_NAME, fixture)
    cases: dict[str, Any] = {}
    for case_name, rules in (("default", False), ("rules", True)):
        report = cleaning.clean_table(loaded.df, _options(rules=rules), sample=5)
        cleaned = report.df
        cases[case_name] = {
            "csv_sha256": {
                mode: hashlib.sha256(cleaning.to_csv_bytes(cleaned, neutralize_formulas=mode)).hexdigest()
                for mode in cleaning.FORMULA_MODES
            },
            "xlsx_entries_sha256": {
                mode: _xlsx_entries(cleaning.to_xlsx_bytes(cleaned, number_mode=mode)) for mode in cleaning.NUMBER_MODES
            },
            "report": report_snapshot(report),
            "rows_after": len(cleaned),
            "columns_after": [str(column) for column in cleaned.columns],
        }
    return cases


def _by_label(elements: Any, label: str) -> Any:
    matches = [element for element in elements if element.label.startswith(label)]
    if len(matches) != 1:
        raise RuntimeError(f"标签以「{label}」开头的控件应唯一，实际 {[e.label for e in matches]}")
    return matches[0]


def app_e2e(app_path: Path, fixture: bytes) -> dict[str, Any]:
    """AppTest 端到端：上传 fixture → 勾规则 → 生成下载 → CSV 哈希。"""
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    st.cache_data.clear()
    at = AppTest.from_file(str(app_path), default_timeout=300).run()
    at.get("file_uploader")[0].upload(SMALL_MIXED_NAME, fixture, "text/csv").run()
    _by_label(at.checkbox, "删除完全重复的行").check()
    _by_label(at.checkbox, "按指定列去重").check()
    at.run()
    _by_label(at.multiselect, "选择去重用的列").select("订单号")
    _by_label(at.checkbox, "删除指定列中有空值的行").check()
    at.run()
    _by_label(at.multiselect, "选择要检查空值的列").select("备注")
    at.run()
    if list(at.exception):
        raise RuntimeError(f"AppTest 运行异常：{list(at.exception)}")
    _by_label(at.button, "生成下载文件").click()
    at.run()
    if list(at.exception):
        raise RuntimeError(f"AppTest 生成下载异常：{list(at.exception)}")
    exports = at.session_state["exports"]
    csv_bytes = exports["csv"]
    back = pd.read_csv(io.BytesIO(csv_bytes), dtype=str, keep_default_na=False)
    return {
        "fixture": SMALL_MIXED_NAME,
        "rules": "删除完全重复的行 + 按订单号去重 + 删除备注空值行",
        "formula_mode": at.session_state["导出_防护模式"],
        "csv_sha256": hashlib.sha256(csv_bytes).hexdigest(),
        "rows": len(back),
        "columns": [str(column) for column in back.columns],
    }


def _source_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=PROJECT_ROOT / "app.py")
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "tests/fixtures/v250/golden.json")
    parser.add_argument("--fixtures-dir", type=Path, default=PROJECT_ROOT / "tests/fixtures/v250")
    args = parser.parse_args()

    args.fixtures_dir.mkdir(parents=True, exist_ok=True)
    small = small_mixed_bytes()
    multi = multi_sheet_bytes()
    (args.fixtures_dir / SMALL_MIXED_NAME).write_bytes(small)
    (args.fixtures_dir / MULTI_SHEET_NAME).write_bytes(multi)

    golden = {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source_commit": _source_commit(),
            "python": platform.python_version(),
            "pandas": pd.__version__,
            "openpyxl": openpyxl.__version__,
            "command": "python benchmarks/capture_golden.py --out tests/fixtures/v250/golden.json",
            "generated_from": "v2.4.0 基线 worktree",
            "notes": "CSV 字节级；XLSX 忽略 docProps/core.xml 与 ZIP 时间戳；报告字段级。",
        },
        "fixtures": {
            SMALL_MIXED_NAME: {
                "sha256": hashlib.sha256(small).hexdigest(),
                "rows": len(_SMALL_MIXED_ROWS) - 1,
                "columns": len(_SMALL_MIXED_ROWS[0]),
            },
            MULTI_SHEET_NAME: {
                "sheets": list(_MULTI_SHEET_FRAMES),
                "sha256_content": hashlib.sha256(
                    repr({name: _records(frame) for name, frame in _MULTI_SHEET_FRAMES.items()}).encode()
                ).hexdigest(),
            },
        },
        "cases": cleaning_cases(small),
        "app_e2e": app_e2e(args.app, small),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(golden, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    print(f"app_e2e csv sha256: {golden['app_e2e']['csv_sha256']}")


if __name__ == "__main__":
    main()
