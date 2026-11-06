"""v2.0.0 新功能的逻辑测试：表头行、上限覆盖、超时与分块、导出模式、列变换、模板、批量。

老测试（tests/test_app_logic.py）原样保留，证明 107 项旧行为没被改坏；
这里只测新加的东西和拆分后的对外接口。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path

import cleaning
import openpyxl
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def csv_bytes(text: str) -> bytes:
    """测试里统一用带 BOM 的 UTF-8，和普通用户从 Excel 导出的文件一致。"""
    return text.encode("utf-8-sig")


def two_sheet_xlsx() -> bytes:
    """双工作表：订单（3 行，含 1 行重复）、客户（2 行）。"""
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"编号": ["A1", "A1", "A2"], "金额": [1, 1, 2]}).to_excel(writer, index=False, sheet_name="订单")
        pd.DataFrame({"编号": ["B1", "B2"], "金额": [3, 4]}).to_excel(writer, index=False, sheet_name="客户")
    return buffer.getvalue()


# ---------------------------------------------------------------- 模块拆分与旧 API


def test_package_exposes_legacy_public_api():
    """拆成包之后，老的公共名字（含测试用到的私有函数）都还能从 cleaning 直接 import。"""
    for name in (
        "load_table",
        "clean_data",
        "clean_report",
        "find_missing",
        "find_suspicious",
        "summarize",
        "to_csv_bytes",
        "to_xlsx_bytes",
        "shield_csv_formulas",
        "make_sample_df",
        "TableError",
        "LoadedTable",
        "CleanReport",
        "MAX_ROWS",
        "MAX_COLS",
        "MAX_CELLS",
        "MAX_UPLOAD_BYTES",
        "MAX_XLSX_UNCOMPRESSED_BYTES",
        "EXCEL_MAX_ROWS",
        "ALLOWED_EXTS",
        "_is_text_column",
        "_looks_like_formula",
        "_estimate_csv_shape",
    ):
        assert hasattr(cleaning, name), f"cleaning 少了对外接口：{name}"
    assert cleaning.__version__ == "2.0.1"


def test_code_is_split_into_expected_modules():
    """按需求拆成的模块都要真的存在且能被 import。"""
    assert cleaning.__file__.endswith("__init__.py")
    for module in ("io", "xlsx_scan", "rules", "transform", "export", "templates", "batch"):
        assert f"cleaning.{module}" in sys.modules, module


# ---------------------------------------------------------------- 指定表头行


def test_csv_header_row_skips_rows_above():
    """第 3 行才是表头：上面两行标题被忽略，并明确告知。"""
    raw = csv_bytes("销售报表\n生成时间: 2024-05-01\n订单号,金额\nA1,10\nA2,20\n")
    loaded = cleaning.load_table("报表.csv", raw, header_row=3)
    assert list(loaded.df.columns) == ["订单号", "金额"]
    assert loaded.df.shape == (2, 2)
    assert any("第 3 行作为表头" in note for note in loaded.notes)


def test_xlsx_header_row_second_row():
    """XLSX 第 2 行作表头（第 1 行是标题）。"""
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame([["盘点表", None], ["编号", "金额"], ["A1", 10], ["A2", 20]]).to_excel(
            writer, index=False, header=False
        )
    loaded = cleaning.load_table("盘点.xlsx", buffer.getvalue(), header_row=2)
    assert list(loaded.df.columns) == ["编号", "金额"]
    assert loaded.df["编号"].tolist() == ["A1", "A2"]


@pytest.mark.parametrize("header_row", [3, 9])
def test_csv_header_row_beyond_file_raises(header_row):
    """表头行比文件还靠下：中文提示去调小，而不是「格式无法解析」。"""
    with pytest.raises(cleaning.TableError, match="表头"):
        cleaning.load_table("短.csv", csv_bytes("a,b\n1,2\n"), header_row=header_row)


def test_xlsx_header_row_beyond_file_mentions_setting():
    """XLSX 没有数据行时，提示里要提到「表头在第几行」这个设置。"""
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"a": [1]}).to_excel(writer, index=False)
    with pytest.raises(cleaning.TableError, match="表头在第几行"):
        cleaning.load_table("x.xlsx", buffer.getvalue(), header_row=5)


def test_header_row_must_start_at_one():
    with pytest.raises(cleaning.TableError, match="表头"):
        cleaning.load_table("x.csv", csv_bytes("a\n1\n"), header_row=0)


def test_merged_cell_blank_headers_are_auto_named():
    """合并单元格拆开后的空表头自动命名，否则下拉框里没法选这一列。"""
    buffer = BytesIO()
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["订单", "金额", None])
    sheet.merge_cells("B1:C1")
    sheet.append(["A1", 10, 20])
    workbook.save(buffer)

    loaded = cleaning.load_table("合并.xlsx", buffer.getvalue())
    assert list(loaded.df.columns) == ["订单", "金额", "列3"]
    assert any("空白列名" in note and "列3" in note for note in loaded.notes)


def test_normalize_headers_renames_blank_and_duplicate_names():
    df = pd.DataFrame([[1, 2, 3]], columns=["金额", "金额", "Unnamed: 2"])
    out, notes = cleaning._normalize_headers(df)
    assert list(out.columns) == ["金额", "金额_2", "列3"]
    assert notes


# ---------------------------------------------------------------- 上限与环境变量


def test_current_limits_follows_package_constants(monkeypatch):
    """界面/测试覆盖改的是 cleaning 包属性，入口必须读到它（否则限额会静默失效）。"""
    monkeypatch.setattr(cleaning, "MAX_ROWS", 3)
    assert cleaning.current_limits().max_rows == 3
    with pytest.raises(cleaning.TableError, match="行数"):
        cleaning.load_table("x.csv", csv_bytes("a\n1\n2\n3\n4\n"))


def test_sidebar_style_override_only_affects_this_call():
    """按会话传 limits：不改全局，下一次调用恢复默认。"""
    strict = cleaning.current_limits()._replace(max_upload_bytes=4)
    with pytest.raises(cleaning.TableError, match="太大"):
        cleaning.load_table("x.csv", csv_bytes("a\n1\n"), limits=strict)
    assert cleaning.load_table("x.csv", csv_bytes("a\n1\n")).df.shape == (1, 1)


def test_env_variables_override_defaults():
    """部署时用环境变量改默认上限：新进程 import cleaning 就生效。"""
    env = {
        **os.environ,
        "TABLE_CLEANER_MAX_ROWS": "12345",
        "TABLE_CLEANER_MAX_UPLOAD_MB": "7",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import cleaning; print(cleaning.MAX_ROWS, cleaning.MAX_UPLOAD_BYTES)",
        ],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    rows, upload = result.stdout.split()
    assert int(rows) == 12345
    assert int(upload) == 7 * 1024 * 1024


def test_invalid_env_value_falls_back_to_default():
    """环境变量写成文字也不该让服务起不来。"""
    env = {**os.environ, "TABLE_CLEANER_MAX_ROWS": "很多行"}
    result = subprocess.run(
        [sys.executable, "-c", "import cleaning; print(cleaning.MAX_ROWS)"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert int(result.stdout) == 500_000


# ---------------------------------------------------------------- 硬超时与分块


def test_deadline_zero_means_no_timeout():
    assert not cleaning.Deadline(0).expired
    assert cleaning.Deadline(1e-9).expired


def test_clean_reports_timeout_in_chinese():
    df = pd.DataFrame({"a": list("abcdef")})
    with pytest.raises(cleaning.TableError, match="超过"):
        cleaning.clean_report(
            df,
            drop_missing_in=["a"],
            deadline=cleaning.Deadline(1e-9),
            chunk_rows=2,
        )


def test_load_csv_timeout_raises_chinese():
    with pytest.raises(cleaning.TableError, match="超过"):
        cleaning.load_table("x.csv", csv_bytes("a\n" + "1\n" * 10), timeout=1e-9)


def test_chunked_clean_matches_whole_table():
    """分块清洗的结果、步骤计数、被删样例必须和整表一次算完全一致。"""
    df = pd.DataFrame({"订单号": ["A1", "A1", "A2", "A2", "A3"], "金额": [1, 1, 2, 2, 3]})
    whole = cleaning.clean_report(df, drop_duplicates=True, dedupe_on=["订单号"], sample=3)
    chunked = cleaning.clean_report(df, drop_duplicates=True, dedupe_on=["订单号"], sample=3, chunk_rows=2)
    assert whole.df.equals(chunked.df)
    assert whole.steps == chunked.steps == {"删除完全重复的行": 2, "按指定列去重": 0}
    assert whole.removed.equals(chunked.removed)


def test_chunked_dedupe_spans_chunk_boundaries():
    """同一个键出现在不同块里，仍按「第一次出现」保留。"""
    df = pd.DataFrame({"k": ["A", "B", "A", "B"], "v": [1, 2, 3, 4]})
    out = cleaning.clean_report(df, dedupe_on=["k"], chunk_rows=2).df
    assert out["k"].tolist() == ["A", "B"]
    assert out["v"].tolist() == [1, 2]


def test_chunked_missing_filter_reports_progress():
    df = pd.DataFrame({"a": [1, None] * 5})
    seen: list[tuple[int, int]] = []
    report = cleaning.clean_report(
        df,
        drop_missing_in=["a"],
        chunk_rows=3,
        progress=lambda done, total: seen.append((done, total)),
    )
    assert len(report.df) == 5
    assert seen[-1] == (10, 10)
    assert all(total == 10 for _, total in seen)
    assert seen == sorted(seen)


def test_large_frames_auto_chunk_without_asking(monkeypatch):
    """超过阈值默认分块（这里把阈值调小来验证自动开关）。"""
    monkeypatch.setattr(cleaning.rules, "CHUNK_THRESHOLD_ROWS", 1)
    monkeypatch.setattr(cleaning.rules, "CHUNK_ROWS", 2)
    seen: list[tuple[int, int]] = []
    df = pd.DataFrame({"a": [1, None, 2, None]})
    cleaning.clean_report(
        df,
        drop_missing_in=["a"],
        progress=lambda done, total: seen.append((done, total)),
    )
    assert len(seen) >= 3  # 起始 0 + 每一块一次
    assert seen[-1] == (4, 4)


# ---------------------------------------------------------------- 导出：三种防护模式


def test_csv_formula_modes():
    df = pd.DataFrame({"v": ["=1+1", "-5", "正常"]})
    quote = cleaning.load_table("q.csv", cleaning.to_csv_bytes(df)).df
    assert quote["v"].tolist() == ["'=1+1", "-5", "正常"]

    zwsp = cleaning.load_table("z.csv", cleaning.to_csv_bytes(df, "zwsp")).df
    assert zwsp["v"].tolist() == ["\u200b=1+1", "-5", "正常"]

    off = cleaning.load_table("o.csv", cleaning.to_csv_bytes(df, "off")).df
    assert off["v"].tolist() == ["=1+1", "-5", "正常"]


def test_csv_formula_mode_accepts_legacy_bools():
    df = pd.DataFrame({"v": ["=1+1"]})
    assert "'=1+1" in cleaning.to_csv_bytes(df, True).decode("utf-8-sig")
    assert "'=1+1" not in cleaning.to_csv_bytes(df, False).decode("utf-8-sig")


def test_unknown_formula_mode_raises():
    with pytest.raises(cleaning.TableError, match="模式"):
        cleaning.to_csv_bytes(pd.DataFrame({"a": [1]}), "bogus")


def test_zwsp_mode_does_not_touch_numbers_or_normal_text():
    df = pd.DataFrame({"v": ["-5", "1e3", "正常", " 加急"]})
    assert cleaning.shield_csv_formulas(df, "zwsp")["v"].tolist() == df["v"].tolist()


# ---------------------------------------------------------------- 导出：编号文本/数字


def test_xlsx_number_mode_default_keeps_leading_zero():
    raw = cleaning.to_xlsx_bytes(pd.DataFrame({"编号": ["00123"]}))
    sheet = openpyxl.load_workbook(BytesIO(raw)).active
    assert sheet["A2"].value == "00123"


def test_xlsx_number_mode_number_converts_and_keeps_text():
    raw = cleaning.to_xlsx_bytes(
        pd.DataFrame({"编号": ["00123", "A002"], "金额": ["5", "6"]}),
        number_mode="number",
    )
    sheet = openpyxl.load_workbook(BytesIO(raw)).active
    assert sheet["A2"].value == 123
    assert sheet["A3"].value == "A002"
    assert sheet["B2"].value == 5  # 整数写成整数，不是 5.0


def test_xlsx_number_mode_still_shields_formulas():
    raw = cleaning.to_xlsx_bytes(pd.DataFrame({"备注": ["=1+1"]}), number_mode="number")
    sheet = openpyxl.load_workbook(BytesIO(raw)).active
    assert sheet["A2"].value == "=1+1"
    assert sheet["A2"].data_type == "s"


def test_unknown_number_mode_raises():
    with pytest.raises(cleaning.TableError, match="编号"):
        cleaning.to_xlsx_bytes(pd.DataFrame({"a": [1]}), number_mode="bogus")


# ---------------------------------------------------------------- 导出：文件名与 ZIP


def test_timestamped_name_has_original_stem_and_time():
    name = cleaning.timestamped_name("我的订单.xlsx", "csv", now=datetime(2024, 5, 1, 12, 30, 5))
    assert name == "清洗后_我的订单_20240501-123005.csv"


def test_timestamped_name_sanitizes_paths_and_bad_chars():
    name = cleaning.timestamped_name("../../客户:表.xlsx", "xlsx", now=datetime(2024, 1, 2, 3, 4, 5))
    assert "/" not in name and ":" not in name
    assert name.startswith("清洗后_") and name.endswith(".xlsx")


def test_safe_stem_and_entry_name():
    assert cleaning.safe_stem("目录/我的 表.xlsx") == "我的 表"
    assert cleaning.entry_name("订单.xlsx", "csv", "第二张") == "订单_第二张.csv"
    assert cleaning.entry_name("订单.xlsx", "csv") == "订单.csv"


def test_unique_names_appends_suffix_for_duplicates():
    assert cleaning.unique_names(["a.csv", "a.csv", "a.csv"]) == [
        "a.csv",
        "a (2).csv",
        "a (3).csv",
    ]


def test_build_zip_roundtrip_with_duplicate_names():
    raw = cleaning.build_zip([("a.csv", b"1"), ("a.csv", b"2")])
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        assert archive.namelist() == ["a.csv", "a (2).csv"]
        assert archive.read("a (2).csv") == b"2"


# ---------------------------------------------------------------- 列变换


def test_rename_drop_and_reorder():
    df = pd.DataFrame({"金额": [1], "备注": ["x"], "编号": ["A1"]})
    ops = cleaning.ColumnOps(rename=(("金额", "总金额"),), drop=("备注",), order=("编号",))
    out, notes = cleaning.apply_transforms(df, ops)
    assert list(out.columns) == ["编号", "总金额"]
    assert notes == []


def test_type_conversion_keeps_failures_and_notes():
    df = pd.DataFrame({"金额": ["100", "约三百"], "日期": ["2024-05-01", "不是日期"]})
    out, notes = cleaning.apply_transforms(df, cleaning.ColumnOps(to_number=("金额",), to_date=("日期",)))
    assert out["金额"].tolist()[0] == 100
    assert out["金额"].tolist()[1] == "约三百"
    assert str(out["日期"].tolist()[0]).startswith("2024-05-01")
    assert out["日期"].tolist()[1] == "不是日期"
    text = " ".join(notes)
    assert "转数字" in text and "转日期" in text and "约三百" in text


def test_type_conversion_handles_thousands_and_integers():
    df = pd.DataFrame({"金额": ["1,000", "2", "3.5"]})
    out, notes = cleaning.apply_transforms(df, cleaning.ColumnOps(to_number=("金额",)))
    assert out["金额"].tolist() == [1000, 2, 3.5]
    assert notes == []


def test_computed_columns_can_chain():
    df = pd.DataFrame({"单价": ["10", "20"], "数量": ["2", "3"]})
    ops = cleaning.ColumnOps(
        to_number=("单价", "数量"),
        computed=(("总额", "单价*数量"), ("含税", "round(总额*1.13, 2)")),
    )
    out, notes = cleaning.apply_transforms(df, ops)
    assert out["总额"].tolist() == [20, 60]
    assert out["含税"].tolist() == [22.6, 67.8]
    assert notes == []


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('rm -rf /')",
        "[单价].__class__",
        "单价 if 数量 else 0",
        "lambda: 1",
        "open('x')",
        "单价[0]",
    ],
)
def test_safe_eval_rejects_non_arithmetic(expr):
    """计算列不是代码执行入口：白名单之外一律拒绝。"""
    df = pd.DataFrame({"单价": [1], "数量": [2]})
    with pytest.raises(cleaning.TableError):
        cleaning.safe_eval(expr, df)


def test_safe_eval_handles_brackets_and_fullwidth():
    df = pd.DataFrame({"单价（元）": [10.0], "数量": [2]})
    assert cleaning.safe_eval("[单价（元）]*数量", df).tolist() == [20]
    assert cleaning.safe_eval("（数量＋1）*2", df).tolist() == [6]
    assert cleaning.safe_eval("round(数量/3, 2)", df).tolist() == [0.67]


def test_safe_eval_division_by_zero_leaves_blank():
    df = pd.DataFrame({"a": [1.0], "b": [0.0]})
    out, notes = cleaning.apply_transforms(df, cleaning.ColumnOps(computed=(("比值", "a/b"),)))
    assert pd.isna(out.loc[0, "比值"])
    assert any("除以 0" in note for note in notes)


def test_computed_column_with_unknown_column_is_reported():
    df = pd.DataFrame({"a": [1]})
    out, notes = cleaning.apply_transforms(df, cleaning.ColumnOps(computed=(("b", "不存在的列*2"),)))
    assert "b" not in out.columns
    assert any("不存在的列" in note for note in notes)


def test_transform_rename_conflict_is_skipped_with_note():
    df = pd.DataFrame({"金额": [1], "总金额": [2]})
    out, notes = cleaning.apply_transforms(df, cleaning.ColumnOps(rename=(("金额", "总金额"),)))
    assert list(out.columns) == ["金额", "总金额"]
    assert any("已经存在" in note for note in notes)


def test_transforms_ignore_missing_columns_without_failing():
    """批量时各表列名不同：不存在的列只提示，不能让整批失败。"""
    df = pd.DataFrame({"金额": [1]})
    out, notes = cleaning.apply_transforms(
        df,
        cleaning.ColumnOps(
            rename=(("备注", "说明"),),
            drop=("备注",),
            order=("备注",),
            to_number=("备注",),
            to_date=("备注",),
        ),
    )
    assert list(out.columns) == ["金额"]
    assert len(notes) >= 4


def test_parse_pairs_accepts_fullwidth_equals_and_skips_comments():
    assert cleaning.parse_pairs("金额＝总金额\n\n# 注释\n备注=说明") == (
        ("金额", "总金额"),
        ("备注", "说明"),
    )
    with pytest.raises(cleaning.TableError, match="第 1 行"):
        cleaning.parse_pairs("金额 总金额")


def test_parse_names_keeps_order_and_dedupes():
    assert cleaning.parse_names("金额\n备注\n金额\n") == ("金额", "备注")


def test_clean_table_applies_row_rules_then_columns():
    df = pd.DataFrame({"编号": ["A1", "A1", "A2"], "金额": [1, 1, 2]})
    options = cleaning.CleanOptions(drop_duplicates=True, columns=cleaning.ColumnOps(rename=(("金额", "总金额"),)))
    report = cleaning.clean_table(df, options)
    assert report.steps == {"删除完全重复的行": 1}
    assert list(report.df.columns) == ["编号", "总金额"]
    assert len(report.df) == 2


# ---------------------------------------------------------------- 规则模板


def test_template_roundtrip():
    rules = {
        "表头行": 2,
        "删除完全重复的行": True,
        "按列去重": ["订单号"],
        "计算列": [["总额", "单价*数量"]],
        "列重命名": {"金额": "总金额"},
    }
    template = cleaning.build_template(rules, name="订单")
    assert template["工具"] == cleaning.TEMPLATE_TOOL
    assert template["版本"] == cleaning.TEMPLATE_VERSION
    assert template["名称"] == "订单"
    assert cleaning.loads(cleaning.dumps(template)) == cleaning.normalize_rules(rules)


def test_template_accepts_rules_only_json():
    """手写的模板可以只有一个规则对象，不必带「工具/版本」外壳。"""
    raw = json.dumps({"表头行": 3, "删除空值行": ["备注"]}, ensure_ascii=False).encode()
    rules = cleaning.loads(raw)
    assert rules["表头行"] == 3
    assert rules["删除空值行"] == ["备注"]


def test_template_bad_json_raises_chinese_error():
    with pytest.raises(cleaning.TableError, match="JSON"):
        cleaning.loads(b"{not json")


@pytest.mark.parametrize(
    ("payload", "keyword"),
    [
        ({"表头行": "第三行"}, "表头行"),
        ({"表头行": 0}, "表头行"),
        ({"删除完全重复的行": "yes"}, "删除完全重复的行"),
        ({"按列去重": "订单号"}, "按列去重"),
        ({"列重命名": "金额=总金额"}, "列重命名"),
        ({"计算列": [{"列名": "总额"}]}, "计算列"),
    ],
)
def test_template_bad_field_types_raise(payload, keyword):
    with pytest.raises(cleaning.TableError, match=keyword):
        cleaning.loads(json.dumps(payload, ensure_ascii=False))


def test_template_filter_drops_unknown_columns():
    rules = cleaning.normalize_rules(
        {
            "按列去重": ["订单号", "客户"],
            "列重命名": {"金额": "总金额", "备注": "说明"},
            "计算列": [["总额", "单价*数量"]],
        }
    )
    filtered, notes = cleaning.filter_for_columns(rules, ["订单号", "金额", "单价", "数量"])
    assert filtered["按列去重"] == ["订单号"]
    assert filtered["列重命名"] == [["金额", "总金额"]]
    assert filtered["计算列"] == [["总额", "单价*数量"]]  # 表达式在执行阶段再提示
    assert notes and "客户" in notes[0] and "备注" in notes[0]


def test_template_text_helpers():
    rules = cleaning.normalize_rules({"列重命名": [["金额", "总金额"]], "计算列": [["总额", "单价*数量"]]})
    assert cleaning.rules_to_rename_text(rules) == "金额=总金额"
    assert cleaning.rules_to_computed_text(rules) == "总额=单价*数量"


def test_summarize_rules_is_readable_chinese():
    rules = cleaning.normalize_rules({"删除完全重复的行": True, "按列去重": ["订单号"]})
    text = cleaning.summarize_rules(rules)
    assert "全行去重" in text and "订单号" in text
    assert "没有任何规则" in cleaning.summarize_rules(cleaning.normalize_rules({}))


def test_build_template_rejects_bad_rules():
    with pytest.raises(cleaning.TableError, match="表头行"):
        cleaning.build_template({"表头行": -1})


def test_template_empty_bytes_raises():
    with pytest.raises(cleaning.TableError, match="空的"):
        cleaning.loads(b"")


# ---------------------------------------------------------------- 批量


def test_batch_multi_sheet_zip_contains_each_sheet():
    raw = two_sheet_xlsx()
    jobs = [cleaning.BatchJob("多表.xlsx", sheet, raw) for sheet in ("订单", "客户")]
    report = cleaning.run_batch(jobs, cleaning.CleanOptions(drop_duplicates=True))

    assert report.exported == 2
    assert [item.entry for item in report.items] == ["多表_订单.csv", "多表_客户.csv"]
    with zipfile.ZipFile(BytesIO(report.zip_bytes)) as archive:
        back = pd.read_csv(BytesIO(archive.read("多表_订单.csv")), dtype=str, keep_default_na=False)
    assert back.shape == (2, 2)
    assert back["编号"].tolist() == ["A1", "A2"]


def test_batch_isolates_single_item_errors():
    """一个坏文件不能让整批失败，其他项照常打包。"""
    jobs = [
        cleaning.BatchJob("坏.csv", None, b""),
        cleaning.BatchJob("好.csv", None, csv_bytes("a\n1\n")),
    ]
    report = cleaning.run_batch(jobs, cleaning.CleanOptions())
    assert report.exported == 1
    assert report.items[0].error and "空的" in report.items[0].error
    assert report.items[1].error is None


def test_batch_all_failed_returns_no_zip():
    report = cleaning.run_batch([cleaning.BatchJob("坏.csv", None, b"")], cleaning.CleanOptions())
    assert report.zip_bytes is None
    assert report.exported == 0


def test_batch_xlsx_format_and_number_mode():
    jobs = [cleaning.BatchJob("订单.xlsx", "订单", two_sheet_xlsx())]
    report = cleaning.run_batch(jobs, cleaning.CleanOptions(), export_format="xlsx", number_mode="number")
    with zipfile.ZipFile(BytesIO(report.zip_bytes)) as archive:
        assert archive.namelist() == ["订单_订单.xlsx"]
        back = pd.read_excel(BytesIO(archive.read(archive.namelist()[0])))
    assert back["金额"].tolist() == [1, 1, 2]


def test_batch_applies_header_row_and_rules():
    raw = csv_bytes("标题\n订单号,金额\nA1,10\nA1,10\n")
    jobs = [cleaning.BatchJob("报表.csv", None, raw)]
    options = cleaning.CleanOptions(header_row=2, drop_duplicates=True)
    report = cleaning.run_batch(jobs, options)
    with zipfile.ZipFile(BytesIO(report.zip_bytes)) as archive:
        back = pd.read_csv(BytesIO(archive.read("报表.csv")), dtype=str, keep_default_na=False)
    assert back.shape == (1, 2)


def test_batch_unknown_format_raises():
    with pytest.raises(cleaning.TableError, match="格式"):
        cleaning.run_batch(
            [cleaning.BatchJob("a.csv", None, b"")],
            cleaning.CleanOptions(),
            export_format="pdf",
        )


def test_batch_empty_jobs():
    report = cleaning.run_batch([], cleaning.CleanOptions())
    assert report.zip_bytes is None
    assert report.items == []


def test_batch_reports_progress():
    seen: list[tuple[int, int]] = []
    cleaning.run_batch(
        [cleaning.BatchJob("a.csv", None, csv_bytes("a\n1\n"))],
        cleaning.CleanOptions(),
        progress=lambda done, total: seen.append((done, total)),
    )
    assert seen[-1] == (1, 1)


def test_batch_uses_wildcard_display_for_sheets():
    jobs = [cleaning.BatchJob("多表.xlsx", "客户", two_sheet_xlsx())]
    report = cleaning.run_batch(jobs, cleaning.CleanOptions())
    assert report.items[0].display == "多表.xlsx · 客户"
