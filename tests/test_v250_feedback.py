"""v2.5.0 W1：计算反馈（规划 5.2 / G-06）。

spinner 是 transient 元素，AppTest 不保留它；这里把 ``st.spinner`` 包一层记录
文案，再交给真实的 spinner，验证的是实际传给界面的字符串。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ui_selectors import _button

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"

try:
    import streamlit  # noqa: F401
    from streamlit.testing import v1 as streamlit_testing
except ImportError as exc:
    missing = getattr(exc, "name", None) or str(exc)
    pytest.skip(
        f"界面测试需要 streamlit，但导入失败（{missing}）；先 pip install -r requirements.txt 再跑。",
        allow_module_level=True,
    )

AppTest = streamlit_testing.AppTest

if not hasattr(streamlit_testing.element_tree, "FileUploader"):
    pytest.skip(
        "当前 Streamlit 的 AppTest 不支持 file_uploader，界面测试整体跳过。",
        allow_module_level=True,
    )

SAMPLE_CSV = (
    "订单号,客户,金额,备注\n"
    "A001,晨光小吃店,128,送货前电话\n"
    "A002,星语文具,0,\n"
    "A002,星语文具,0,\n"
    "A003,山泉花店,56.5,=1+1\n"
).encode("utf-8-sig")


def _csv(rows: int) -> bytes:
    """两列固定数据，用来跨过 2 万 / 10 万阈值，不依赖清洗逻辑细节。"""
    body = "".join(f"A{index:06d},填充内容\n" for index in range(rows))
    return ("编号,备注\n" + body).encode("utf-8-sig")


def _spy_spinner(monkeypatch) -> list[str]:
    import streamlit as st

    real = st.spinner
    seen: list[str] = []

    def spy(text: str, *args, **kwargs):
        seen.append(text)
        return real(text, *args, **kwargs)

    monkeypatch.setattr(st, "spinner", spy)
    return seen


@pytest.fixture()
def app():
    def _new():
        at = AppTest.from_file(str(APP_PATH), default_timeout=180).run()
        assert not list(at.exception), list(at.exception)
        return at

    return _new


def test_small_table_shows_spinner_and_done_caption(app, monkeypatch):
    """小表：清洗时有 spinner，完成后 caption 写明行×列与用时。"""
    seen = _spy_spinner(monkeypatch)
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", SAMPLE_CSV, "text/csv").run()
    assert not list(at.exception)

    assert "正在重新计算…" in seen
    done = [caption.value for caption in at.caption if caption.value.startswith("计算完成：")]
    assert done, "没有看到「计算完成」caption"
    assert "4 行 × 4 列" in done[0]
    assert "用时" in done[0] and "秒" in done[0]


def test_large_table_spinner_includes_dimensions(app, monkeypatch):
    """>2 万行：spinner 带行数×列数，等待时知道自己在等什么。"""
    seen = _spy_spinner(monkeypatch)
    at = app()
    at.get("file_uploader")[0].upload("两万行.csv", _csv(20_001), "text/csv").run()
    assert not list(at.exception)

    assert "正在重新计算…（20,001 行 × 2 列）" in seen


def test_cache_hit_has_no_spinner_and_shows_reuse_caption(app, monkeypatch):
    """命中：不显示 spinner，只显示「已复用」caption。"""
    seen = _spy_spinner(monkeypatch)
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", SAMPLE_CSV, "text/csv").run()
    seen.clear()

    at.run()

    assert "正在重新计算…" not in seen
    assert any("已复用本次会话的计算结果（规则未变）" in caption.value for caption in at.caption)


def test_over_100k_rows_keeps_progress_bar(app, monkeypatch):
    """>10 万行仍走分块清洗与进度条，不能静默地等。"""
    import streamlit as st

    real = st.progress
    texts: list[str] = []

    def spy(*args, **kwargs):
        texts.append(kwargs.get("text", "") or "")
        return real(*args, **kwargs)

    monkeypatch.setattr(st, "progress", spy)
    at = app()
    at.get("file_uploader")[0].upload("十万行.csv", _csv(100_001), "text/csv").run()

    assert not list(at.exception)
    assert any("正在分块清洗" in text for text in texts)


def test_export_spinner_text_mentions_xlsx(app, monkeypatch):
    """导出 spinner 文案提示大表 XLSX 可能较慢。"""
    seen = _spy_spinner(monkeypatch)
    at = app()
    at.get("file_uploader")[0].upload("订单.csv", SAMPLE_CSV, "text/csv").run()
    seen.clear()

    _button(at, "生成下载文件").click().run()

    assert "正在生成下载文件…（大表 XLSX 可能较慢）" in seen
