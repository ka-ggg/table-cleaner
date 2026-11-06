"""v2.3.0 公共 API 边界测试：app.py 只能走 cleaning/__init__.py 的导出。"""

from __future__ import annotations

import ast
from pathlib import Path

import cleaning

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_PATH = PROJECT_ROOT / "app.py"


def _app_cleaning_names() -> tuple[set[str], list[str]]:
    """返回 app.py 引用的 cleaning 公共名字，以及所有直接 import 子模块的语句。"""
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    names: set[str] = set()
    internal: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("cleaning."):
                    internal.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("cleaning."):
            internal.append(node.module)
        elif isinstance(node, ast.ImportFrom) and node.module == "cleaning":
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "cleaning":
            names.add(node.attr)
    return names, internal


def test_app_imports_no_cleaning_internal_module():
    """app.py 不许直连 io/transform/export 等内部模块，避免拆包边界被开后门。"""
    _, internal = _app_cleaning_names()
    assert internal == []


def test_app_cleaning_names_are_all_in_public_api():
    """app.py 用到的每个 cleaning 名字都必须在 __all__ 里，缺了就正式补导出。"""
    names, _ = _app_cleaning_names()
    missing = sorted(name for name in names if name != "__version__" and name not in cleaning.__all__)
    assert missing == []
    assert len(names) >= 30


def test_public_api_names_exist_and_functions_have_docstrings():
    """__all__ 里的名字必须真实存在；公共函数和类必须有 docstring。"""
    for name in cleaning.__all__:
        assert hasattr(cleaning, name), name
        if name.startswith("__"):
            continue
        obj = getattr(cleaning, name)
        if callable(obj) and not isinstance(obj, type):
            assert (getattr(obj, "__doc__", None) or "").strip(), name
        elif isinstance(obj, type):
            assert (obj.__doc__ or "").strip(), name
