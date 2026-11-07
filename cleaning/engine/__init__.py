"""引擎层：协议、能力注册表与 pandas 参考实现。

导入本包只注册 pandas 引擎；Polars 引擎在显式 ``get_engine("polars")`` 时才惰性
import 并注册，默认安装不依赖 Polars。入口层（``app.py``、CLI、HTTP API）不感知
这里，也不暴露引擎开关。
"""

from __future__ import annotations

from .base import (
    DEFAULT_ENGINE,
    Engine,
    EngineResult,
    available_engines,
    get_engine,
    register_engine,
)
from .pandas_engine import PANDAS_ENGINE, PandasEngine
from .registry import (
    CAPABILITIES,
    CAPABILITY_IDS,
    KIND_EXPRESSION,
    KIND_INSPECTOR,
    KIND_NODE,
    STATUS_ALIGNED,
    STATUS_FULL_FALLBACK,
    STATUS_PARTIAL_FALLBACK,
    Capability,
    capability_for_node_type,
    get_capability,
    node_capabilities,
    node_types,
    registry_problems,
    resolve_callable,
)

__all__ = [
    "DEFAULT_ENGINE",
    "Engine",
    "EngineResult",
    "available_engines",
    "get_engine",
    "register_engine",
    "PANDAS_ENGINE",
    "PandasEngine",
    "CAPABILITIES",
    "CAPABILITY_IDS",
    "KIND_EXPRESSION",
    "KIND_INSPECTOR",
    "KIND_NODE",
    "STATUS_ALIGNED",
    "STATUS_FULL_FALLBACK",
    "STATUS_PARTIAL_FALLBACK",
    "Capability",
    "capability_for_node_type",
    "get_capability",
    "node_capabilities",
    "node_types",
    "registry_problems",
    "resolve_callable",
]
