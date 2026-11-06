"""v2.3.0 批量纯函数化测试：只依赖入参、结果可重复、不感知界面。"""

from __future__ import annotations

import inspect
import zipfile
from io import BytesIO

import cleaning


def _csv(text: str) -> bytes:
    return text.encode("utf-8-sig")


def _zip_entries(payload: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_batch_same_inputs_produce_same_entries_and_content():
    """同一个 BatchJob 序列 + 同一份 CleanOptions，跑两次的条目名与内容一致。"""
    jobs = [
        cleaning.BatchJob("订单.csv", None, _csv("编号,金额\nA1,10\nA1,10\nA2,20\n")),
        cleaning.BatchJob("客户.csv", None, _csv("编号,名称\nB1,晨光\nB2,星语\n")),
    ]
    options = cleaning.CleanOptions(drop_duplicates=True)
    first = cleaning.run_batch(jobs, options)
    second = cleaning.run_batch(jobs, options)
    assert first.exported == second.exported == 2
    assert [item.entry for item in first.items] == [item.entry for item in second.items]
    assert _zip_entries(first.zip_bytes or b"") == _zip_entries(second.zip_bytes or b"")
    assert [item.rows_after for item in first.items] == [2, 2]


def test_batch_does_not_depend_on_streamlit_or_ui_state():
    """批量模块源码不得出现 Streamlit / session_state，入参里也没有界面对象。"""
    source = inspect.getsource(cleaning.batch)
    assert "streamlit" not in source
    assert "session_state" not in source
    signature = inspect.signature(cleaning.run_batch)
    assert list(signature.parameters) == [
        "jobs",
        "options",
        "export_format",
        "number_mode",
        "formula_mode",
        "timeout",
        "limits",
        "progress",
    ]
    assert signature.parameters["options"].annotation in ("CleanOptions", cleaning.CleanOptions)


def test_batch_options_are_not_mutated_by_run():
    """批量处理不能悄悄改调用方的 CleanOptions（纯函数边界的一部分）。"""
    options = cleaning.CleanOptions(drop_duplicates=True)
    cleaning.run_batch(
        [cleaning.BatchJob("a.csv", None, _csv("a\n1\n1\n"))],
        options,
    )
    assert options == cleaning.CleanOptions(drop_duplicates=True)
