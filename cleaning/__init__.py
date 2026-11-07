"""表格清洗的核心逻辑（v2.0.0 起是一个包，不再是单个 cleaning.py）。

这里只有「对外接口」和文档：界面、CLI、HTTP API、测试都从 ``cleaning`` 直接 import，
不用关心内部怎么拆。公共 API 清单见 ``cleaning.__all__`` 与仓库根目录 ``API.md``。各模块分工：

- ``limits``      所有 MAX_* 上限与性能参数（环境变量可覆盖），分块/超时默认值
- ``errors``      用户可见的 TableError、协作式超时 Deadline
- ``xlsx_scan``   XLSX 压缩包流式预检：解压体积、真实行列数、工作表名、选表
- ``io``          读写入口：CSV/XLSX 读取、编码/分隔符识别、表头行、表头规范化
- ``rules``       判定与行清洗：缺失值、重复行、可疑格式、公式判断、分块删行
- ``transform``   列变换：重命名、删列、调顺序、类型转换、计算列（安全求值）
- ``export``      CSV/XLSX/ZIP 导出、公式注入三种模式、编号文本/数字开关、文件名
- ``templates``   清洗规则模板的 JSON 读写校验、template_version 版本识别、按当前列过滤
- ``batch``       多张工作表 / 多个文件 → 逐一清洗 → 打包 ZIP
- ``service``     编程式入口：clean_bytes / inspect_bytes / options_from_rules / 报告
- ``engine``      引擎协议、17 项能力注册表与 pandas 参考引擎（v2.4.0 内部预埋，默认仍走 pandas）
- ``pipeline``    Pipeline DSL v1：校验、模板迁移、编译为 CleanOptions、计划描述（内部模块，不进 __all__）
- ``cli``         table-cleaner 命令行（clean / inspect / template / batch）
- ``api.http_api`` table-cleaner-api 本地 HTTP 雏形（独立入口层，标准库，无鉴权）

判定口径（界面、README、测试引用同一份文字）：

- 缺失值 = 空白单元格（NaN/None，或只有空文本）。数字 0 和文本 "0" 是有效数据。
- 重复行 = 所有列的值完全相同的行；清洗时保留第一次出现的那一行。
- 可疑数据只是格式提示，工具不会自动修改这些单元格。

``MAX_*`` 常量从这里导出（真实定义在 ``cleaning.limits``）；测试或界面覆盖时改的是
这里的同名属性，内部通过 ``current_limits()`` 读取，所以覆盖一定生效。
"""

from __future__ import annotations

__version__ = "2.3.0"

from .batch import BatchItem, BatchJob, BatchReport, run_batch
from .errors import Deadline, TableError
from .export import (
    FORMULA_MODE_LABELS,
    FORMULA_MODES,
    NUMBER_MODE_LABELS,
    NUMBER_MODES,
    build_zip,
    entry_name,
    numbers_where_possible,
    safe_stem,
    shield_csv_formulas,
    timestamped_name,
    to_csv_bytes,
    to_xlsx_bytes,
    unique_names,
)
from .io import (
    ALLOWED_EXTS,
    CSV_DELIMITERS,
    LoadedTable,
    load_table,
)

# 兼容旧路径：老脚本/旧测试里直接调用的私有函数
from .io import (
    _detect_delimiter as _detect_delimiter,
)
from .io import (
    _estimate_csv_shape as _estimate_csv_shape,
)
from .io import (
    _normalize_headers as _normalize_headers,
)
from .limits import (
    API_MAX_BODY_BYTES,
    API_MAX_JOBS,
    API_READ_TIMEOUT_SECONDS,
    CHUNK_ROWS,
    CHUNK_THRESHOLD_ROWS,
    DEFAULT_TIMEOUT_SECONDS,
    EXCEL_MAX_COLS,
    EXCEL_MAX_ROWS,
    MAX_CELLS,
    MAX_COLS,
    MAX_ROWS,
    MAX_UPLOAD_BYTES,
    MAX_XLSX_UNCOMPRESSED_BYTES,
    Limits,
    check_shape_limits,
    current_limits,
    raise_shape_limit,
)
from .rules import (
    _PLAIN_NUMBER_RE as _PLAIN_NUMBER_RE,
)
from .rules import (
    DUPLICATE_RULE,
    FORMULA_ISSUE,
    MISSING_RULE,
    SPACE_ISSUE,
    CleanReport,
    clean_data,
    clean_report,
    find_missing,
    find_suspicious,
    make_sample_df,
    missing_mask,
    summarize,
)
from .rules import (
    _is_text_column as _is_text_column,  # 旧脚本直接调用这些私有函数，保留重导出
)
from .rules import (
    _looks_like_formula as _looks_like_formula,
)
from .service import (
    CleanResult,
    InspectResult,
    clean_bytes,
    inspect_bytes,
    options_from_rules,
    render_report,
)
from .templates import (
    LEGACY_TEMPLATE_VERSION,
    TEMPLATE_FORMAT_VERSION,
    TEMPLATE_TOOL,
    TEMPLATE_VERSION,
    TemplateInfo,
    build_template,
    dumps,
    filter_for_columns,
    load_template,
    loads,
    normalize_rules,
    rules_to_computed_text,
    rules_to_rename_text,
    summarize_rules,
)
from .transform import (
    CleanOptions,
    ColumnOps,
    apply_transforms,
    clean_table,
    convert_to_date,
    convert_to_number,
    numeric_series,
    parse_names,
    parse_pairs,
    safe_eval,
)
from .xlsx_scan import (
    XLSX_ONE_SHEET_HINT,
    check_xlsx_uncompressed_size,
    count_data_merges,
    limit_reason,
    pick_xlsx_sheet,
    scan_part,
    scan_xlsx_sheet,
    xlsx_sheet_names,
)

__all__ = [
    "__version__",
    # 上限与超时
    "MAX_UPLOAD_BYTES",
    "MAX_ROWS",
    "MAX_COLS",
    "MAX_CELLS",
    "MAX_XLSX_UNCOMPRESSED_BYTES",
    "API_MAX_JOBS",
    "API_MAX_BODY_BYTES",
    "API_READ_TIMEOUT_SECONDS",
    "EXCEL_MAX_ROWS",
    "EXCEL_MAX_COLS",
    "CHUNK_ROWS",
    "CHUNK_THRESHOLD_ROWS",
    "DEFAULT_TIMEOUT_SECONDS",
    "Limits",
    "current_limits",
    "check_shape_limits",
    "raise_shape_limit",
    "Deadline",
    "TableError",
    # 读取
    "ALLOWED_EXTS",
    "CSV_DELIMITERS",
    "XLSX_ONE_SHEET_HINT",
    "LoadedTable",
    "load_table",
    "check_xlsx_uncompressed_size",
    "count_data_merges",
    "pick_xlsx_sheet",
    "scan_part",
    "scan_xlsx_sheet",
    "xlsx_sheet_names",
    "limit_reason",
    # 判定与清洗
    "DUPLICATE_RULE",
    "MISSING_RULE",
    "SPACE_ISSUE",
    "FORMULA_ISSUE",
    "CleanReport",
    "clean_data",
    "clean_report",
    "clean_table",
    "find_missing",
    "find_suspicious",
    "missing_mask",
    "summarize",
    "make_sample_df",
    # 列变换
    "CleanOptions",
    "ColumnOps",
    "apply_transforms",
    "convert_to_date",
    "convert_to_number",
    "numeric_series",
    "parse_names",
    "parse_pairs",
    "safe_eval",
    # 导出
    "FORMULA_MODES",
    "FORMULA_MODE_LABELS",
    "NUMBER_MODES",
    "NUMBER_MODE_LABELS",
    "shield_csv_formulas",
    "to_csv_bytes",
    "to_xlsx_bytes",
    "numbers_where_possible",
    "timestamped_name",
    "safe_stem",
    "unique_names",
    "entry_name",
    "build_zip",
    # 编程式入口
    "CleanResult",
    "InspectResult",
    "clean_bytes",
    "inspect_bytes",
    "options_from_rules",
    "render_report",
    # 模板
    "TEMPLATE_TOOL",
    "TEMPLATE_VERSION",
    "TEMPLATE_FORMAT_VERSION",
    "LEGACY_TEMPLATE_VERSION",
    "TemplateInfo",
    "build_template",
    "dumps",
    "load_template",
    "loads",
    "normalize_rules",
    "filter_for_columns",
    "rules_to_rename_text",
    "rules_to_computed_text",
    "summarize_rules",
    # 批量
    "BatchJob",
    "BatchItem",
    "BatchReport",
    "run_batch",
]
