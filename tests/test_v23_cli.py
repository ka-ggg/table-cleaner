"""v2.3.0 CLI 测试：四子命令成功路径、坏文件退出码、中文提示与覆盖策略。"""

from __future__ import annotations

import json
import tomllib
import zipfile
from io import BytesIO
from pathlib import Path

import cleaning
import pandas as pd
from cleaning import cli

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _csv(text: str) -> bytes:
    return text.encode("utf-8-sig")


def _write_template(path: Path, rules: dict[str, object]) -> Path:
    path.write_bytes(cleaning.dumps(cleaning.build_template(rules, name="测试模板")))
    return path


def test_cli_console_script_declared_in_pyproject():
    """pyproject 必须注册 table-cleaner console script，指向 CLI 主函数。"""
    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["scripts"]["table-cleaner"] == "cleaning.cli:main"


def test_cli_clean_writes_output_and_report(tmp_path, capsys):
    source = tmp_path / "订单.csv"
    source.write_bytes(_csv("订单号,金额\nA1,10\nA1,10\nA2,20\n"))
    template = _write_template(tmp_path / "模板.json", {"删除完全重复的行": True})
    output = tmp_path / "结果.csv"
    report = tmp_path / "报告.md"
    code = cli.main(
        [
            "clean",
            str(source),
            "--template",
            str(template),
            "-o",
            str(output),
            "--report",
            str(report),
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "已清洗" in captured.out
    assert "行数：3 -> 2" in captured.out
    back = pd.read_csv(output, dtype=str, keep_default_na=False)
    assert back["订单号"].tolist() == ["A1", "A2"]
    assert "# 清洗报告" in report.read_text(encoding="utf-8")


def test_cli_clean_switch_flags_without_template(tmp_path, capsys):
    source = tmp_path / "订单.csv"
    source.write_bytes(_csv("订单号,金额\nA1,10\nA1,10\nA2,20\n"))
    output = tmp_path / "结果.csv"
    code = cli.main(["clean", str(source), "-o", str(output), "--drop-duplicates", "--rename", "金额=总金额"])
    assert code == 0
    back = pd.read_csv(output, dtype=str, keep_default_na=False)
    assert list(back.columns) == ["订单号", "总金额"]
    assert len(back) == 2
    assert "已清洗" in capsys.readouterr().out


def test_cli_clean_missing_input_returns_one_and_chinese_error(tmp_path, capsys):
    code = cli.main(["clean", str(tmp_path / "不存在.csv"), "-o", str(tmp_path / "out.csv")])
    captured = capsys.readouterr()
    assert code == cleaning.cli.EXIT_INPUT == 1
    assert "找不到输入文件" in captured.err
    assert "Traceback" not in captured.err
    assert "FileNotFoundError" not in captured.err


def test_cli_clean_bad_file_returns_one_and_chinese_error(tmp_path, capsys):
    source = tmp_path / "坏文件.csv"
    source.write_bytes(b"")
    code = cli.main(["clean", str(source), "-o", str(tmp_path / "out.csv")])
    captured = capsys.readouterr()
    assert code == 1
    assert "空的" in captured.err
    assert "Traceback" not in captured.err


def test_cli_clean_existing_output_requires_force(tmp_path, capsys):
    source = tmp_path / "订单.csv"
    source.write_bytes(_csv("a\n1\n"))
    output = tmp_path / "结果.csv"
    output.write_text("旧内容", encoding="utf-8")
    assert cli.main(["clean", str(source), "-o", str(output)]) == 2
    assert "已存在" in capsys.readouterr().err
    assert cli.main(["clean", str(source), "-o", str(output), "--force"]) == 0
    assert output.read_text(encoding="utf-8").startswith("\ufeffa")


def test_cli_clean_unsupported_output_extension_is_usage_error(tmp_path, capsys):
    source = tmp_path / "订单.csv"
    source.write_bytes(_csv("a\n1\n"))
    assert cli.main(["clean", str(source), "-o", str(tmp_path / "结果.txt")]) == 2
    assert "扩展名" in capsys.readouterr().err


def test_cli_clean_template_with_higher_version_returns_one(tmp_path, capsys):
    source = tmp_path / "订单.csv"
    source.write_bytes(_csv("a\n1\n"))
    template = json.loads(cleaning.dumps(cleaning.build_template({})))
    template["template_version"] = 9
    template_path = tmp_path / "高版本.json"
    template_path.write_bytes(json.dumps(template, ensure_ascii=False).encode())
    assert cli.main(["clean", str(source), "--template", str(template_path), "-o", str(tmp_path / "o.csv")]) == 1
    captured = capsys.readouterr()
    assert "高于当前工具支持的版本" in captured.err
    assert "Traceback" not in captured.err


def test_cli_inspect_prints_report_and_writes_report_file(tmp_path, capsys):
    source = tmp_path / "检查.csv"
    source.write_bytes(_csv("编号,备注\nA1,=1+1\nA1,=1+1\nA2, 空格\n"))
    report = tmp_path / "检查报告.md"
    assert cli.main(["inspect", str(source), "--report", str(report)]) == 0
    captured = capsys.readouterr()
    assert "# 检查报告" in captured.out
    assert "完全重复行：1 行" in captured.out
    assert report.read_text(encoding="utf-8") == captured.out


def test_cli_template_check_and_view(tmp_path, capsys):
    path = _write_template(tmp_path / "模板.json", {"删除完全重复的行": True})
    assert cli.main(["template", str(path), "--check"]) == 0
    check_out = capsys.readouterr().out
    assert "模板有效" in check_out
    assert "版本 1" in check_out
    assert cli.main(["template", str(path)]) == 0
    view_out = capsys.readouterr().out
    assert "template_version：1" in view_out
    assert '"template_version": 1' in view_out


def test_cli_template_legacy_file_is_reported_as_version_zero(tmp_path, capsys):
    template = cleaning.build_template({"删除完全重复的行": True})
    template.pop("template_version")
    path = tmp_path / "旧模板.json"
    path.write_bytes(json.dumps(template, ensure_ascii=False).encode())
    assert cli.main(["template", str(path), "--check"]) == 0
    assert "旧模板（版本 0" in capsys.readouterr().out


def test_cli_template_bad_json_returns_one(tmp_path, capsys):
    path = tmp_path / "坏模板.json"
    path.write_text("{not json", encoding="utf-8")
    assert cli.main(["template", str(path)]) == 1
    captured = capsys.readouterr()
    assert "JSON" in captured.err
    assert "Traceback" not in captured.err


def test_cli_batch_directory_writes_zip(tmp_path, capsys):
    source_dir = tmp_path / "输入"
    source_dir.mkdir()
    (source_dir / "a.csv").write_bytes(_csv("编号,金额\nA1,10\nA1,10\n"))
    (source_dir / "b.csv").write_bytes(_csv("编号,金额\nB1,20\n"))
    template = _write_template(tmp_path / "模板.json", {"删除完全重复的行": True})
    out_dir = tmp_path / "输出"
    assert cli.main(["batch", str(source_dir), "--template", str(template), "--out-dir", str(out_dir)]) == 0
    captured = capsys.readouterr()
    assert "成功 2/2 项" in captured.out
    zips = list(out_dir.glob("*.zip"))
    assert len(zips) == 1
    with zipfile.ZipFile(zips[0]) as archive:
        assert sorted(archive.namelist()) == ["a.csv", "b.csv"]
        back = pd.read_csv(BytesIO(archive.read("a.csv")), dtype=str, keep_default_na=False)
    assert back["编号"].tolist() == ["A1"]


def test_cli_batch_partial_failure_returns_one_but_keeps_good_file(tmp_path, capsys):
    source_dir = tmp_path / "输入"
    source_dir.mkdir()
    (source_dir / "good.csv").write_bytes(_csv("编号\nA1\n"))
    (source_dir / "bad.csv").write_bytes(b"")
    out_dir = tmp_path / "输出"
    assert cli.main(["batch", str(source_dir), "--out-dir", str(out_dir)]) == 1
    captured = capsys.readouterr()
    assert "失败：bad.csv" in captured.out
    assert "成功 1/2 项" in captured.out


def test_cli_missing_arguments_returns_usage_code(tmp_path, capsys):
    assert cli.main([]) == 2
    captured = capsys.readouterr()
    assert "参数错误" in captured.err
    assert "usage:" not in captured.err
    assert cli.main(["clean", str(tmp_path / "a.csv")]) == 2


def test_cli_invalid_header_row_returns_usage_code(tmp_path, capsys):
    source = tmp_path / "a.csv"
    source.write_bytes(_csv("a\n1\n"))
    assert cli.main(["inspect", str(source), "--header-row", "0"]) == 2
    assert "必须大于等于 1" in capsys.readouterr().err


def test_cli_unknown_argument_returns_usage_code(tmp_path, capsys):
    source = tmp_path / "a.csv"
    source.write_bytes(_csv("a\n1\n"))
    assert cli.main(["inspect", str(source), "--not-a-flag"]) == 2
    assert "不认识的参数" in capsys.readouterr().err


def test_cli_clean_all_rule_flags_and_xlsx_export(tmp_path, capsys):
    """一次带齐所有规则开关，覆盖模板覆盖、列名拆分、计算列与 XLSX 导出分支。"""
    source = tmp_path / "报表.csv"
    source.write_bytes(_csv("标题\n订单号,客户,金额,日期,备注\nA1,晨光,10,2024-05-01,x\nA1,晨光,10,2024-05-01,x\n"))
    output = tmp_path / "结果.xlsx"
    code = cli.main(
        [
            "clean",
            str(source),
            "-o",
            str(output),
            "--header-row",
            "2",
            "--drop-duplicates",
            "--dedupe-on",
            "订单号,客户",
            "--drop-missing-in",
            "备注",
            "--rename",
            "客户=客户名称",
            "--drop-cols",
            "备注",
            "--order",
            "订单号,含税",
            "--to-number",
            "金额",
            "--to-date",
            "日期",
            "--computed",
            "含税=金额*1.13",
            "--formula-mode",
            "zwsp",
            "--number-mode",
            "number",
        ]
    )
    assert code == 0
    back = pd.read_excel(output)
    assert list(back.columns)[:2] == ["订单号", "含税"]
    assert len(back) == 1
    assert "已清洗" in capsys.readouterr().out


def test_cli_clean_pair_flags_without_equals_return_usage(tmp_path, capsys):
    source = tmp_path / "a.csv"
    source.write_bytes(_csv("a\n1\n"))
    assert cli.main(["clean", str(source), "-o", str(tmp_path / "o.csv"), "--rename", "没有等号"]) == 2
    assert "缺少等号" in capsys.readouterr().err
    assert cli.main(["clean", str(source), "-o", str(tmp_path / "o.csv"), "--computed", "=x"]) == 2
    assert "等号两边不能为空" in capsys.readouterr().err


def test_cli_clean_report_same_path_returns_usage(tmp_path, capsys):
    source = tmp_path / "a.csv"
    source.write_bytes(_csv("a\n1\n"))
    target = tmp_path / "same.csv"
    assert cli.main(["clean", str(source), "-o", str(target), "--report", str(target)]) == 2
    assert "--report 不能和输出文件用同一个路径" in capsys.readouterr().err


def test_cli_write_output_missing_parent_returns_usage(tmp_path, capsys):
    source = tmp_path / "a.csv"
    source.write_bytes(_csv("a\n1\n"))
    assert cli.main(["clean", str(source), "-o", str(tmp_path / "不存在" / "o.csv")]) == 2
    assert "所在目录不存在" in capsys.readouterr().err


def test_cli_input_path_is_directory_returns_one(tmp_path, capsys):
    assert cli.main(["inspect", str(tmp_path)]) == 1
    assert "不是文件" in capsys.readouterr().err


def test_cli_batch_empty_dir_and_missing_path_return_one(tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cli.main(["batch", str(empty), "--out-dir", str(tmp_path / "out")]) == 1
    assert "没有可处理" in capsys.readouterr().err
    assert cli.main(["batch", str(tmp_path / "missing"), "--out-dir", str(tmp_path / "out")]) == 1
    assert "找不到输入路径" in capsys.readouterr().err


def test_cli_batch_out_dir_is_file_returns_usage(tmp_path, capsys):
    source = tmp_path / "a.csv"
    source.write_bytes(_csv("a\n1\n"))
    out_dir = tmp_path / "not-a-dir"
    out_dir.write_text("x", encoding="utf-8")
    assert cli.main(["batch", str(source), "--out-dir", str(out_dir)]) == 2
    assert "--out-dir 不是目录" in capsys.readouterr().err


def test_cli_batch_all_fail_reports_no_zip(tmp_path, capsys):
    source_dir = tmp_path / "输入"
    source_dir.mkdir()
    (source_dir / "bad.csv").write_bytes(b"")
    assert cli.main(["batch", str(source_dir), "--out-dir", str(tmp_path / "out")]) == 1
    captured = capsys.readouterr()
    assert "未生成 ZIP" in captured.out
    assert not list((tmp_path / "out").glob("*.zip"))


def test_cli_batch_xlsx_export(tmp_path, capsys):
    source = tmp_path / "a.csv"
    source.write_bytes(_csv("编号\nA1\n"))
    out_dir = tmp_path / "out"
    assert cli.main(["batch", str(source), "--out-dir", str(out_dir), "--export-format", "xlsx"]) == 0
    zips = list(out_dir.glob("*.zip"))
    with zipfile.ZipFile(zips[0]) as archive:
        assert archive.namelist() == ["a.xlsx"]


def test_cli_unexpected_exception_returns_three(monkeypatch, tmp_path, capsys):
    def boom(args):
        raise RuntimeError("模拟内部错误")

    monkeypatch.setattr(cli, "_cmd_clean", boom)
    source = tmp_path / "a.csv"
    source.write_bytes(_csv("a\n1\n"))
    assert cli.main(["clean", str(source), "-o", str(tmp_path / "o.csv")]) == 3
    captured = capsys.readouterr()
    assert "处理失败" in captured.err
    assert "Traceback" not in captured.err


def test_cli_positive_int_non_number_returns_usage(tmp_path, capsys):
    source = tmp_path / "a.csv"
    source.write_bytes(_csv("a\n1\n"))
    assert cli.main(["inspect", str(source), "--header-row", "abc"]) == 2
    assert "必须是整数" in capsys.readouterr().err


def test_cli_invalid_choice_and_missing_value_return_usage(tmp_path, capsys):
    source = tmp_path / "a.csv"
    source.write_bytes(_csv("a\n1\n"))
    assert cli.main(["clean", str(source), "-o", str(tmp_path / "o.csv"), "--formula-mode", "bad"]) == 2
    assert "参数取值不合法" in capsys.readouterr().err
    assert cli.main(["inspect", str(source), "--header-row"]) == 2
    assert "参数缺少取值" in capsys.readouterr().err


def test_cli_version_prints_package_version(capsys):
    assert cli.main(["--version"]) == 0
    assert "table-cleaner 2.3.0" in capsys.readouterr().out
