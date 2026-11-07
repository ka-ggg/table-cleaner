"""pandas 参考引擎：把现有 ``rules`` / ``transform`` 包装成统一引擎接口。

包装层只做转发，不复制、不修改任何清洗语义；默认入口（``clean_table`` /
``clean_bytes`` 等）在本版仍直接走这些函数，行为与 v2.3.0 完全一致。
Polars 引擎（W2）以后按同一协议实现，再用对齐测试证明语义等价。
"""

from __future__ import annotations

import pandas as pd

from .. import rules, transform
from ..errors import Deadline
from ..rules import CleanReport, ProgressCallback
from ..transform import CleanOptions, ColumnOps
from .base import Engine, register_engine


class PandasEngine:
    """现有 pandas 实现的参考包装；默认引擎，语义即 v2.3.0 的现行行为。"""

    name = "pandas"

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
    ) -> CleanReport:
        return rules.clean_report(
            df,
            drop_duplicates=drop_duplicates,
            dedupe_on=dedupe_on,
            drop_missing_in=drop_missing_in,
            sample=sample,
            progress=progress,
            deadline=deadline,
            chunk_rows=chunk_rows,
        )

    def apply_transforms(self, df: pd.DataFrame, ops: ColumnOps) -> tuple[pd.DataFrame, list[str]]:
        return transform.apply_transforms(df, ops)

    def clean_table(
        self,
        df: pd.DataFrame,
        options: CleanOptions,
        *,
        sample: int = 0,
        progress: ProgressCallback | None = None,
        deadline: Deadline | None = None,
    ) -> CleanReport:
        return transform.clean_table(
            df,
            options,
            sample=sample,
            progress=progress,
            deadline=deadline,
        )

    def find_missing(self, df: pd.DataFrame) -> pd.Series:
        return rules.find_missing(df)

    def find_suspicious(self, df: pd.DataFrame) -> dict[str, dict[str, int]]:
        return rules.find_suspicious(df)

    def summarize(self, df: pd.DataFrame) -> dict[str, int]:
        return rules.summarize(df)


PANDAS_ENGINE = PandasEngine()
register_engine(PANDAS_ENGINE)

# 让 mypy 按协议检查包装类；运行时一致性由 tests/test_v240_engine_registry.py 核对。
_PANDAS_ENGINE_CONTRACT: Engine = PANDAS_ENGINE
