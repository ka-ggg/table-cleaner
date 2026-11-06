"""``table-cleaner`` 命令行入口（预览版）。

四个子命令：``clean`` / ``inspect`` / ``template`` / ``batch``。所有清洗、模板和批量
逻辑都调用 ``cleaning`` 的公共 API，CLI 只做参数解析、文件读写和中文错误收敛。

退出码（与 v2.1.0 规划一致）：

- 0：成功；
- 1：输入/数据错误（文件不存在、格式不支持、模板损坏或版本过高、超过上限）；
- 2：参数用法错误（缺参数、组合不合法、输出路径不可写/已存在）；
- 3：内部错误（已收敛为中文提示，不打印英文堆栈）。

预览声明：参数兼容承诺自 v3.0 起算；v2.3.0 期间允许调整参数，调整会记入 CHANGELOG。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import NoReturn

import cleaning

EXIT_OK = 0
EXIT_INPUT = 1
EXIT_USAGE = 2
EXIT_INTERNAL = 3

PROG = "table-cleaner"


class UsageError(Exception):
    """参数组合或输出路径问题：收敛为退出码 2 和中文提示。"""


class _Parser(argparse.ArgumentParser):
    """把 argparse 的英文错误统一翻成中文，且不打印英文 usage/堆栈。"""

    def error(self, message: str) -> NoReturn:
        self.exit(EXIT_USAGE, f"参数错误：{_translate_usage_error(message)}\n用 {self.prog} --help 查看用法。\n")


def _translate_usage_error(message: str) -> str:
    """覆盖 argparse 最常见的英文错误；未知错误保留技术细节但不带堆栈。"""
    text = message.strip()
    if text.startswith("the following arguments are required:"):
        fields = text.split(":", 1)[1].strip()
        return f"缺少必填参数：{fields}"
    if text.startswith("unrecognized arguments:"):
        return f"不认识的参数：{text.split(':', 1)[1].strip()}"
    if text.startswith("invalid choice:"):
        return f"参数取值不合法：{text.split(':', 1)[1].strip()}"
    if text.startswith("argument ") and ": " in text:
        argument, detail = text.split(": ", 1)
        return f"参数 {argument[9:]}：{detail}"
    if "expected one argument" in text:
        return "参数缺少取值"
    return text


def _say(text: str) -> None:
    """CLI 正常输出（源码禁止 print，统一走 stdout）。"""
    sys.stdout.write(text + "\n")


def _error(text: str) -> None:
    """CLI 错误输出：只写中文提示，不写英文堆栈。"""
    sys.stderr.write(f"错误：{text}\n")


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError("必须是整数") from None
    if value < 1:
        raise argparse.ArgumentTypeError("必须大于等于 1")
    return value


def _add_rule_options(parser: argparse.ArgumentParser) -> None:
    """clean 与 batch 共用的规则参数；不传就沿用模板或默认值。"""
    parser.add_argument("--template", type=Path, help="清洗规则模板（.json，推荐）")
    parser.add_argument("--header-row", type=_positive_int, help="表头在第几行（从 1 开始，默认 1）")
    parser.add_argument("--drop-duplicates", action="store_true", help="删除所有列都完全相同的行")
    parser.add_argument("--dedupe-on", action="append", metavar="列名", help="按这些列去重；逗号分隔，可重复")
    parser.add_argument("--drop-missing-in", action="append", metavar="列名", help="这些列有空的整行删除；逗号分隔")
    parser.add_argument("--rename", action="append", metavar="旧=新", help="列重命名；可重复")
    parser.add_argument("--drop-cols", action="append", metavar="列名", help="要删除的列；逗号分隔")
    parser.add_argument("--order", action="append", metavar="列名", help="排在最前面的列；逗号分隔")
    parser.add_argument("--to-number", action="append", metavar="列名", help="转成数字的列；逗号分隔")
    parser.add_argument("--to-date", action="append", metavar="列名", help="转成日期的列；逗号分隔")
    parser.add_argument("--computed", action="append", metavar="新列=表达式", help="计算列；可重复")
    parser.add_argument(
        "--formula-mode",
        choices=cleaning.FORMULA_MODES,
        default="quote",
        help="CSV 公式防护：quote（单引号，默认）/ zwsp（零宽空格）/ off（关闭）",
    )
    parser.add_argument(
        "--number-mode",
        choices=cleaning.NUMBER_MODES,
        default="text",
        help="XLSX 编号导出：text（强制文本，默认）/ number（尝试转数字）",
    )
    parser.add_argument("--force", action="store_true", help="输出文件已存在时覆盖（默认报错不覆盖）")


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog=PROG, description="表格清洗小工具命令行（预览版；参数兼容承诺自 v3.0 起）")
    parser.add_argument("--version", action="version", version=f"{PROG} {cleaning.__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="子命令")

    clean = sub.add_parser("clean", help="按模板/开关清洗单个文件并导出")
    clean.add_argument("input", type=Path, help="输入 .csv / .xlsx 文件")
    clean.add_argument("-o", "--output", type=Path, required=True, help="输出文件（扩展名决定格式：.csv / .xlsx）")
    clean.add_argument("--report", type=Path, help="可选：把中文清洗报告写到这个文件")
    _add_rule_options(clean)
    clean.set_defaults(handler=_cmd_clean)

    inspect = sub.add_parser("inspect", help="只检查不清洗，输出问题报告")
    inspect.add_argument("input", type=Path, help="输入 .csv / .xlsx 文件")
    inspect.add_argument("--header-row", type=_positive_int, help="表头在第几行（从 1 开始，默认 1）")
    inspect.add_argument("--report", type=Path, help="可选：把中文检查报告写到这个文件")
    inspect.add_argument("--force", action="store_true", help="报告文件已存在时覆盖")
    inspect.set_defaults(handler=_cmd_inspect)

    template = sub.add_parser("template", help="查看与校验模板文件")
    template.add_argument("template", type=Path, help="模板 .json 文件")
    template.add_argument("--check", action="store_true", help="只校验，不输出完整内容")
    template.set_defaults(handler=_cmd_template)

    batch = sub.add_parser("batch", help="按同一模板批量清洗目录/多个文件")
    batch.add_argument("input", nargs="+", type=Path, help="输入目录，或一个/多个 .csv/.xlsx 文件")
    batch.add_argument("--out-dir", type=Path, required=True, help="输出目录（不存在会自动创建）")
    batch.add_argument(
        "--export-format",
        choices=("csv", "xlsx"),
        default="csv",
        help="每个文件的导出格式：csv（默认）/ xlsx",
    )
    _add_rule_options(batch)
    batch.set_defaults(handler=_cmd_batch)
    return parser


def _read_file(path: Path, label: str) -> bytes:
    if not path.exists():
        raise cleaning.TableError(f"找不到{label}：{path}")
    if not path.is_file():
        raise cleaning.TableError(f"{label}不是文件：{path}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise cleaning.TableError(f"读取{label}失败：{path}（{exc.strerror or '无法读取'}）") from None


def _load_template(path: Path) -> cleaning.TemplateInfo:
    data = _read_file(path, "模板文件")
    return cleaning.load_template(data)


def _split_columns(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    names: list[str] = []
    for value in values:
        for part in value.split(","):
            name = part.strip()
            if name and name not in names:
                names.append(name)
    return names


def _split_pairs(values: list[str] | None, label: str) -> list[list[str]] | None:
    if values is None:
        return None
    pairs: list[list[str]] = []
    for index, value in enumerate(values, 1):
        text = value.replace("＝", "=")
        if "=" not in text:
            raise UsageError(f"{label} 第 {index} 项「{value}」缺少等号，格式应为「旧=新」。")
        left, right = (part.strip() for part in text.split("=", 1))
        if not left or not right:
            raise UsageError(f"{label} 第 {index} 项「{value}」等号两边不能为空。")
        pairs.append([left, right])
    return pairs


def _build_rules(args: argparse.Namespace) -> dict[str, object]:
    """模板打底，命令行显式参数覆盖同名规则；不传的规则保持模板值。"""
    rules: dict[str, object] = {}
    if getattr(args, "template", None) is not None:
        rules.update(_load_template(args.template).rules)
    if args.header_row is not None:
        rules["表头行"] = args.header_row
    if args.drop_duplicates:
        rules["删除完全重复的行"] = True
    columns = {
        "按列去重": _split_columns(args.dedupe_on),
        "删除空值行": _split_columns(args.drop_missing_in),
        "删除列": _split_columns(args.drop_cols),
        "列顺序": _split_columns(args.order),
        "转数字": _split_columns(args.to_number),
        "转日期": _split_columns(args.to_date),
    }
    for key, value in columns.items():
        if value is not None:
            rules[key] = value
    rename = _split_pairs(args.rename, "--rename")
    if rename is not None:
        rules["列重命名"] = rename
    computed = _split_pairs(args.computed, "--computed")
    if computed is not None:
        rules["计算列"] = computed
    return rules


def _options(args: argparse.Namespace) -> cleaning.CleanOptions:
    return cleaning.options_from_rules(_build_rules(args))


def _output_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix not in cleaning.ALLOWED_EXTS:
        raise UsageError(f"输出文件扩展名「{path.suffix or '无'}」不支持；只支持 .csv 和 .xlsx。")
    return suffix.lstrip(".")


def _write_output(path: Path, data: bytes, *, force: bool, label: str) -> None:
    if path.exists() and not force:
        raise UsageError(f"{label}已存在：{path}；如需覆盖请加 --force。")
    parent = path.parent if path.parent != Path("") else Path(".")
    if not parent.exists():
        raise UsageError(f"{label}所在目录不存在：{parent}")
    if not parent.is_dir():
        raise UsageError(f"{label}所在路径不是目录：{parent}")
    try:
        path.write_bytes(data)
    except OSError as exc:
        raise UsageError(f"写入{label}失败：{path}（{exc.strerror or '无法写入'}）") from None


def _cmd_clean(args: argparse.Namespace) -> int:
    data = _read_file(args.input, "输入文件")
    options = _options(args)
    export_format = _output_format(args.output)
    result = cleaning.clean_bytes(
        args.input.name,
        data,
        options,
        export_format=export_format,
        formula_mode=args.formula_mode,
        number_mode=args.number_mode,
        timeout=cleaning.current_limits().timeout_seconds or None,
    )
    if args.report is not None and args.report.resolve() == args.output.resolve():
        raise UsageError("--report 不能和输出文件用同一个路径。")
    _write_output(args.output, result.data, force=args.force, label="输出文件")
    if args.report is not None:
        _write_output(args.report, result.report_text.encode("utf-8"), force=args.force, label="报告文件")
    _say(f"已清洗：{args.input} -> {args.output}")
    _say(f"行数：{result.rows_before:,} -> {result.rows_after:,}；格式：{result.export_format}")
    if args.report is not None:
        _say(f"报告：{args.report}")
    return EXIT_OK


def _cmd_inspect(args: argparse.Namespace) -> int:
    data = _read_file(args.input, "输入文件")
    result = cleaning.inspect_bytes(
        args.input.name,
        data,
        header_row=args.header_row or 1,
        timeout=cleaning.current_limits().timeout_seconds or None,
    )
    if args.report is not None:
        _write_output(args.report, result.report_text.encode("utf-8"), force=args.force, label="报告文件")
    _say(result.report_text.rstrip("\n"))
    return EXIT_OK


def _template_version_label(info: cleaning.TemplateInfo) -> str:
    if info.is_legacy:
        return "旧模板（版本 0，无 template_version 字段）"
    return f"版本 {info.template_version}"


def _cmd_template(args: argparse.Namespace) -> int:
    info = _load_template(args.template)
    version = _template_version_label(info)
    summary = cleaning.summarize_rules(info.rules)
    if args.check:
        _say(f"模板有效：{args.template}")
        _say(f"格式：{version}；规则：{summary}")
        return EXIT_OK
    _say(f"模板文件：{args.template}")
    _say(f"工具：{info.tool or '（未填写）'}")
    _say(f"名称：{info.name or '（未填写）'}")
    _say(f"保存时间：{info.saved_at or '（未填写）'}")
    _say(f"template_version：{info.template_version}（{version}）")
    _say(f"规则摘要：{summary}")
    _say("")
    _say(json.dumps(info.document, ensure_ascii=False, indent=2))
    return EXIT_OK


def _collect_batch_files(inputs: Sequence[Path]) -> list[Path]:
    files: list[Path] = []
    for path in inputs:
        if path.is_dir():
            found = sorted(
                item for item in path.iterdir() if item.is_file() and item.suffix.lower() in cleaning.ALLOWED_EXTS
            )
            if not found:
                raise cleaning.TableError(f"目录里没有可处理的 .csv/.xlsx 文件：{path}")
            files.extend(found)
        elif path.is_file():
            files.append(path)
        else:
            raise cleaning.TableError(f"找不到输入路径：{path}")
    # 去重但保持用户给的顺序
    return list(dict.fromkeys(files))


def _cmd_batch(args: argparse.Namespace) -> int:
    options = _options(args)
    paths = _collect_batch_files(args.input)
    jobs = [cleaning.BatchJob(path.name, None, _read_file(path, "输入文件")) for path in paths]
    if args.out_dir.exists() and not args.out_dir.is_dir():
        raise UsageError(f"--out-dir 不是目录：{args.out_dir}")
    try:
        args.out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UsageError(f"创建输出目录失败：{args.out_dir}（{exc.strerror or '无法创建'}）") from None
    report = cleaning.run_batch(
        jobs,
        options,
        export_format=args.export_format,
        number_mode=args.number_mode,
        formula_mode=args.formula_mode,
        timeout=cleaning.current_limits().timeout_seconds or None,
    )
    for item in report.items:
        if item.error:
            _say(f"失败：{item.display} —— {item.error}")
        else:
            _say(f"成功：{item.display} -> {item.entry}（{item.rows_before:,} -> {item.rows_after:,} 行）")
    if report.zip_bytes is not None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = args.out_dir / f"批量清洗结果_{stamp}.zip"
        _write_output(target, report.zip_bytes, force=args.force, label="批量结果")
        _say(f"打包：{target}（成功 {report.exported}/{len(report.items)} 项）")
    else:
        _say("这一批没有任何一项成功，未生成 ZIP。")
    return EXIT_OK if report.exported == len(report.items) else EXIT_INPUT


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 主入口：返回退出码，不向用户抛英文堆栈。"""
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE
    try:
        return int(args.handler(args))
    except UsageError as exc:
        _error(str(exc))
        return EXIT_USAGE
    except cleaning.TableError as exc:
        _error(str(exc))
        return EXIT_INPUT
    except Exception:
        _error("处理失败：程序遇到未预期的问题；请把输入文件信息和这条提示反馈给维护者。")
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
