"""按路线图附录 B 生成固定 benchmark 数据。

三档行数：小 1 万、中 10 万、大 50 万。固定随机种子，生成脚本进仓库，
任何人可复现同一批 CSV；数据文件不提交（体积大），用脚本随时重建。

每档列形态固定为四列，覆盖三类要求：

- ``订单号``：18 位长编号（文本），含少量完全重复行；
- ``客户名称``：普通文本，含少量空值；
- ``金额``：普通数值 + 千分位 + 货币符号等脏金额；
- ``下单日期``：多种日期写法 + 少量非法日期。

50 万行 × 4 列 = 200 万单元格，正好等于现行 ``MAX_CELLS``，不靠放宽单元格上限。
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

SIZES = {"small": 10_000, "medium": 100_000, "large": 500_000}
SEED = 20231106
COLUMNS = ("订单号", "客户名称", "金额", "下单日期")
_NAMES = (
    "晨光小吃店",
    "星语文具",
    "山泉花店",
    "远航网店",
    "青禾杂货",
    "麦田烘焙",
    "云朵咖啡",
    "木棉服饰",
)
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日")


def _amount(rng: random.Random, index: int) -> str:
    """金额列故意混入脏格式：千分位、人民币符号、空值，测试转换失败保留原文。"""
    value = rng.uniform(0.5, 9999.5)
    if index % 97 == 0:
        return ""
    if index % 20 == 0:
        return f"{value:,.2f}"
    if index % 50 == 0:
        return f"¥{value:,.2f}"
    return f"{value:.2f}"


def _date_text(index: int) -> str:
    if index % 101 == 0:
        return "2024-13-40"  # 非法日期：转换失败，保留原文
    day = datetime(2024, 1, 1) + timedelta(days=index % 730)
    return day.strftime(_DATE_FORMATS[index % len(_DATE_FORMATS)])


def generate_dataset(path: Path, rows: int, *, seed: int = SEED) -> Path:
    """流式写一个固定数据集；返回写入路径。"""
    rng = random.Random(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous: tuple[str, str, str, str] | None = None
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(COLUMNS)
        for index in range(rows):
            if index and index % 1000 == 0 and previous is not None:
                row = previous  # 每 1000 行插入一条完全重复行，测全行去重
            else:
                identifier = f"{100_000_000_000_000_000 + index:018d}"
                name = "" if index % 5000 == 0 else _NAMES[rng.randrange(len(_NAMES))]
                row = (identifier, name, _amount(rng, index), _date_text(index))
                previous = row
            writer.writerow(row)
    return path


def ensure_datasets(
    data_dir: Path,
    sizes: dict[str, int] | None = None,
    *,
    force: bool = False,
) -> dict[str, Path]:
    """确保指定档位的数据文件存在；返回 {档位: 路径}。"""
    chosen = sizes or SIZES
    result: dict[str, Path] = {}
    for name, rows in chosen.items():
        path = data_dir / f"{name}.csv"
        if force or not path.exists():
            generate_dataset(path, rows)
        result[name] = path
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 table-cleaner benchmark 固定数据集")
    parser.add_argument("--data-dir", type=Path, default=Path("benchmarks/data"), help="数据输出目录")
    parser.add_argument("--sizes", nargs="+", choices=tuple(SIZES), default=list(SIZES), help="生成哪些档位")
    parser.add_argument("--force", action="store_true", help="覆盖已有文件")
    args = parser.parse_args(argv)
    paths = ensure_datasets(args.data_dir, {name: SIZES[name] for name in args.sizes}, force=args.force)
    for name, path in paths.items():
        sys.stdout.write(f"{name}: {path}（{path.stat().st_size / 1024 / 1024:.1f} MB）\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
