"""AppTest 界面控件的定位 helper：只按 label / 列名查找，不用位置索引。

页面插入新控件后 ``at.button[0]`` 会悄悄指向另一个按钮，测试还在跑、其实点错了地方。
这里找不到控件、或命中多个，都直接抛 ``AssertionError`` 让测试失败，不允许静默跳过。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _describe(element: Any) -> str:
    """给失败信息一个人能看懂的名字：控件用 label/key，dataframe 用列名。"""
    if hasattr(element, "label"):
        return element.label
    if getattr(element, "key", None):
        return str(element.key)
    if hasattr(element, "value") and hasattr(element.value, "columns"):
        return f"列={[str(column) for column in element.value.columns]}"
    return str(getattr(element, "type", "?"))


def _unique(matches: Iterable[Any], description: str) -> Any:
    """要求恰好命中一个元素，否则报出实际命中数量与标签。"""
    found = list(matches)
    if len(found) != 1:
        raise AssertionError(
            f"{description}：期望唯一命中，实际找到 {len(found)} 个 {[_describe(element) for element in found]}"
        )
    return found[0]


def _by_label(elements: Iterable[Any], label: str) -> Any:
    """按标签前缀定位一个控件；前缀必须唯一命中。"""
    return _unique(
        (element for element in elements if element.label.startswith(label)),
        f"标签以「{label}」开头的控件",
    )


def _button(at: Any, label: str) -> Any:
    return _by_label(at.button, label)


def _checkbox(at: Any, label: str) -> Any:
    return _by_label(at.checkbox, label)


def _multiselect(at: Any, label: str) -> Any:
    return _by_label(at.multiselect, label)


def _selectbox(at: Any, label: str) -> Any:
    return _by_label(at.selectbox, label)


def _dataframe(at: Any, *columns: str) -> Any:
    """AppTest 的 dataframe 没有 label/key，改按列名定位；同样要求唯一命中。"""
    wanted = set(columns)
    return _unique(
        (frame for frame in at.dataframe if wanted <= {str(column) for column in frame.value.columns}),
        f"包含列 {sorted(wanted)} 的 dataframe",
    )
