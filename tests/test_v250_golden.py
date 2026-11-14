"""v2.5.0 W1：结果一致性金标（规划 4.2 / G-03）。

金标由 v2.4.0 基线 ``83a66ee`` 的独立 worktree 用
``benchmarks/capture_golden.py`` 生成，提交在 ``tests/fixtures/v250/golden.json``；
本文件只读金标，不依赖网络或旧工作区。
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from functools import lru_cache
from pathlib import Path

import cleaning
import pandas as pd
import pytest

from ui_selectors import _by_label

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "v250"
GOLDEN = json.loads((FIXTURES / "golden.json").read_text(encoding="utf-8"))

try:
    import streamlit  # noqa: F401
    from streamlit.testing import v1 as streamlit_testing
except ImportError as exc:
    missing = getattr(exc, "name", None) or str(exc)
    pytest.skip(
        f"金标端到端测试需要 streamlit，但导入失败（{missing}）；先 pip install -r requirements.txt 再跑。",
        allow_module_level=True,
    )

AppTest = streamlit_testing.AppTest

if not hasattr(streamlit_testing.element_tree, "FileUploader"):
    pytest.skip(
        "当前 Streamlit 的 AppTest 不支持 file_uploader，端到端金标用例跳过。",
        allow_module_level=True,
    )

SMALL_MIXED = FIXTURES / "small_mixed.csv"


def _options(rules: bool) -> cleaning.CleanOptions:
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


@lru_cache(maxsize=2)
def _report(case: str) -> cleaning.CleanReport:
    loaded = cleaning.load_table("small_mixed.csv", SMALL_MIXED.read_bytes())
    return cleaning.clean_table(loaded.df, _options(rules=case == "rules"), sample=5)


def _records(frame: pd.DataFrame) -> list[dict[str, str]]:
    return frame.fillna("").astype(str).to_dict(orient="records")


def _xlsx_entries(data: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {
            name: hashlib.sha256(archive.read(name)).hexdigest()
            for name in sorted(archive.namelist())
            if name != "docProps/core.xml"
        }


def test_fixture_sha256_matches_golden():
    """fixture 字节固定，金标里的 sha256 才能长期复现。"""
    actual = hashlib.sha256(SMALL_MIXED.read_bytes()).hexdigest()
    assert actual == GOLDEN["fixtures"]["small_mixed.csv"]["sha256"]


@pytest.mark.parametrize("case", ["default", "rules"])
@pytest.mark.parametrize("mode", ["quote", "zwsp", "off"])
def test_csv_bytes_match_golden(case, mode):
    """三种公式防护模式，CSV 必须与 v2.4.0 基线逐字节一致。"""
    actual = hashlib.sha256(cleaning.to_csv_bytes(_report(case).df, neutralize_formulas=mode)).hexdigest()
    assert actual == GOLDEN["cases"][case]["csv_sha256"][mode]


@pytest.mark.parametrize("case", ["default", "rules"])
@pytest.mark.parametrize("mode", ["text", "number"])
def test_xlsx_entries_match_golden(case, mode):
    """XLSX 解包内容一致；忽略 ZIP 时间戳与 docProps/core.xml 的写入时间。"""
    actual = _xlsx_entries(cleaning.to_xlsx_bytes(_report(case).df, number_mode=mode))
    assert actual == GOLDEN["cases"][case]["xlsx_entries_sha256"][mode]


@pytest.mark.parametrize("case", ["default", "rules"])
def test_report_fields_match_golden(case):
    """CleanReport 的 steps / removed / notes 字段级一致。"""
    report = _report(case)
    golden = GOLDEN["cases"][case]["report"]
    assert dict(report.steps) == golden["steps"]
    assert _records(report.removed) == golden["removed"]
    assert list(report.notes) == golden["notes"]
    assert len(report.df) == GOLDEN["cases"][case]["rows_after"]
    assert [str(column) for column in report.df.columns] == GOLDEN["cases"][case]["columns_after"]


def test_app_e2e_csv_hits_golden_hash():
    """AppTest 端到端：上传 fixture、勾规则、点生成，导出的 CSV 命中金标哈希。"""
    import streamlit as st

    st.cache_data.clear()
    at = AppTest.from_file(str(APP_PATH), default_timeout=300).run()
    at.get("file_uploader")[0].upload(SMALL_MIXED.name, SMALL_MIXED.read_bytes(), "text/csv").run()
    _by_label(at.checkbox, "删除完全重复的行").check()
    _by_label(at.checkbox, "按指定列去重").check()
    at.run()
    _by_label(at.multiselect, "选择去重用的列").select("订单号")
    _by_label(at.checkbox, "删除指定列中有空值的行").check()
    at.run()
    _by_label(at.multiselect, "选择要检查空值的列").select("备注")
    at.run()
    _by_label(at.button, "生成下载文件").click()
    at.run()

    assert not list(at.exception), list(at.exception)
    exports = at.session_state["exports"]
    actual = hashlib.sha256(exports["csv"]).hexdigest()
    assert actual == GOLDEN["app_e2e"]["csv_sha256"]
    back = pd.read_csv(io.BytesIO(exports["csv"]), dtype=str, keep_default_na=False)
    assert len(back) == GOLDEN["app_e2e"]["rows"]
    assert [str(column) for column in back.columns] == GOLDEN["app_e2e"]["columns"]


def test_multi_sheet_fixture_is_readable():
    """提交的双表 fixture 能被 load_table 正常读取，内容与生成时一致。"""
    loaded = cleaning.load_table("multi_sheet.xlsx", (FIXTURES / "multi_sheet.xlsx").read_bytes())
    assert set(loaded.sheet_names) == {"订单", "客户"}
    assert loaded.df.shape[0] > 0
