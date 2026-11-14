"""pytest 全局夹层：AppTest 用例前后清空 Streamlit 的 data cache。

实测新的 AppTest 实例会命中上一个实例留在进程里的 ``st.cache_data``，导致用例之间串数据。
只对真正使用 AppTest 的测试模块生效；逻辑测试不导入 streamlit，也不会被牵连。
"""

from __future__ import annotations

import pytest


def _uses_apptest(request: pytest.FixtureRequest) -> bool:
    """界面测试模块在文件顶部把 ``streamlit.testing`` 的 AppTest 绑定为模块级名字。"""
    return hasattr(request.module, "AppTest")


@pytest.fixture(autouse=True)
def clear_apptest_cache(request: pytest.FixtureRequest):
    """AppTest 用例前后各清一次缓存；其余测试原样放行。"""
    if not _uses_apptest(request):
        yield
        return

    import streamlit as st

    st.cache_data.clear()
    try:
        yield
    finally:
        st.cache_data.clear()
