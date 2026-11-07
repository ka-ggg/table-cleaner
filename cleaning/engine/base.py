"""引擎合同：协议、结果类型与引擎选择接口。

v2.4.0 的引擎边界刻意停在 pandas 帧进出：``CleanReport`` 的 ``df`` / ``removed``
仍然是 pandas 对象，入口层和报告类型都不感知具体引擎，也不暴露引擎开关。
本模块只定义合同，不实现任何清洗逻辑，也不 import Polars。

依赖方向：``cleaning.engine`` → ``rules`` / ``transform`` / ``errors``；
入口层（``app.py``、CLI、HTTP API）不 import 本模块。
"""

from __future__ import annotations

import importlib
from typing import Protocol

import pandas as pd

from ..errors import Deadline, TableError
from ..rules import CleanReport, ProgressCallback
from ..transform import CleanOptions, ColumnOps

DEFAULT_ENGINE = "pandas"

# 引擎能力清单与回落状态见 ``cleaning.engine.registry``；本模块只管「选哪个引擎」。
# 结果类型直接复用 ``CleanReport``：引擎边界不另造一套报告，避免入口层出现两种结果。
EngineResult = CleanReport

# 延迟导入的引擎实现：pandas 之外的引擎必须在显式选择时才会 import 可选依赖，
# 默认 pandas 路径不受安装体积和导入耗时影响。
_LAZY_ENGINE_MODULES: dict[str, str] = {
    "pandas": "cleaning.engine.pandas_engine",
    "polars": "cleaning.engine.polars_engine",
}


class Engine(Protocol):
    """引擎协议：以 pandas 帧为输入输出，结果必须与 pandas 参考实现语义等价。

    实现类必须提供 ``name`` 与下面这些方法；方法签名与现有 ``rules`` /
    ``transform`` 公共函数保持一致，默认参数和返回类型不得放宽。
    """

    name: str

    def clean_report(
        self,
        df: pd.DataFrame,
        drop_duplicates: bool = False,
        dedupe_on: list[str] | None = None,
        drop_missing_in: list[str] | None = None,
        sample: int = 0,
        *,
        progress: ProgressCallback | None = None,
        deadline: Deadline | None = None,
        chunk_rows: int | None = None,
    ) -> EngineResult:
        """按行规则清洗，返回与 ``rules.clean_report`` 相同的结果。"""
        ...  # pragma: no cover

    def apply_transforms(self, df: pd.DataFrame, ops: ColumnOps) -> tuple[pd.DataFrame, list[str]]:
        """应用列变换，返回（新帧, 中文说明），语义同 ``transform.apply_transforms``。"""
        ...  # pragma: no cover

    def clean_table(
        self,
        df: pd.DataFrame,
        options: CleanOptions,
        *,
        sample: int = 0,
        progress: ProgressCallback | None = None,
        deadline: Deadline | None = None,
    ) -> EngineResult:
        """行清洗 + 列变换一次做完，语义同 ``transform.clean_table``。"""
        ...  # pragma: no cover

    def find_missing(self, df: pd.DataFrame) -> pd.Series:
        """返回每列空值数量，语义同 ``rules.find_missing``。"""
        ...  # pragma: no cover

    def find_suspicious(self, df: pd.DataFrame) -> dict[str, dict[str, int]]:
        """返回可疑格式统计，语义同 ``rules.find_suspicious``。"""
        ...  # pragma: no cover

    def summarize(self, df: pd.DataFrame) -> dict[str, int]:
        """返回清洗前后统计，语义同 ``rules.summarize``。"""
        ...  # pragma: no cover


_ENGINES: dict[str, Engine] = {}


def register_engine(engine: Engine) -> None:
    """注册一个引擎实现；同名注册后出现的覆盖先出现的（测试可替换参考实现）。"""
    name = str(getattr(engine, "name", "") or "").strip()
    if not name:
        raise TableError("引擎实现缺少 name，不能注册。")
    _ENGINES[name] = engine


def available_engines() -> tuple[str, ...]:
    """返回当前已注册的引擎名；默认 pandas 在导入 ``cleaning.engine`` 后即可用。"""
    return tuple(sorted(_ENGINES))


def get_engine(name: str = DEFAULT_ENGINE) -> Engine:
    """按名字取引擎实例；未知引擎或可选依赖缺失时抛中文 ``TableError``。

    默认返回 pandas 参考实现。选择 ``polars`` 但环境没有安装 Polars 或实现模块时
    明确报错，**不会静默改用 pandas**；回落只能由计划期能力表显式决定。
    """
    requested = str(name or "").strip().lower()
    if requested in _ENGINES:
        return _ENGINES[requested]
    module_name = _LAZY_ENGINE_MODULES.get(requested)
    if module_name is None:
        options = "、".join(available_engines()) or DEFAULT_ENGINE
        raise TableError(f"引擎「{name}」不存在；当前可选引擎：{options}。默认引擎是 pandas。")
    try:
        importlib.import_module(module_name)
    except ImportError as exc:
        # 可选依赖（例如 polars）未安装时只影响显式选择该引擎的调用；
        # 默认 pandas 路径完全不经过这里。
        raise TableError(
            f"引擎「{requested}」当前不可用：缺少可选依赖或实现（{exc.name or exc}）。"
            "默认引擎仍是 pandas；如需 Polars 预览，请安装对应可选依赖后再试。"
        ) from None
    engine = _ENGINES.get(requested)
    if engine is None:
        raise TableError(f"引擎模块「{module_name}」没有注册「{requested}」实现；默认引擎仍是 pandas。")
    return engine
