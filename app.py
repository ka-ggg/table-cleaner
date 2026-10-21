"""表格清洗小工具 —— Streamlit 界面（v2.0.0，手机优先）。

界面只负责展示和收集选择，清洗规则全部在 ``cleaning`` 包里，方便单独测试。
新增的批量、模板、进阶列操作都复用同一份 ``CleanOptions``，页面和测试走同一条路。
"""

from __future__ import annotations

import hashlib
from datetime import datetime

import pandas as pd
import streamlit as st

import cleaning
from cleaning import (
    DUPLICATE_RULE,
    MISSING_RULE,
    CleanOptions,
    ColumnOps,
    LoadedTable,
    TableError,
    find_missing,
    find_suspicious,
    load_table,
    make_sample_df,
    summarize,
    to_csv_bytes,
    to_xlsx_bytes,
)

APP_VERSION = cleaning.__version__
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
JSON_MIME = "application/json"

st.set_page_config(page_title="表格清洗小工具", page_icon="🧹", layout="centered")

# 手机上收窄留白、放大一点字号；只改样式，不引入组件库
st.markdown(
    """
    <style>
    .block-container {max-width: 720px; padding: 1.2rem 0.9rem 2rem;}
    h1 {font-size: 1.55rem;}
    h2 {font-size: 1.2rem; margin-top: 1.4rem;}
    div[data-testid="stMetricValue"] {font-size: 1.4rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------- 小工具


def sidebar_settings() -> tuple[cleaning.Limits, float | None]:
    """左侧高级设置：按会话临时覆盖上限和超时（不写全局，避免影响别人的会话）。"""
    defaults = cleaning.current_limits()
    with st.sidebar:
        st.subheader("高级设置")
        with st.expander("资源上限与处理超时", expanded=False):
            upload_mb = st.number_input(
                "上传文件上限（MB）",
                min_value=1,
                max_value=None,
                value=int(defaults.max_upload_mb),
                step=1,
                key="上限_上传MB",
            )
            max_rows = st.number_input(
                "单表行数上限",
                min_value=1,
                max_value=None,
                value=int(defaults.max_rows),
                step=10_000,
                key="上限_行数",
            )
            timeout = st.number_input(
                "处理超时（秒，0 = 不限）",
                min_value=0,
                max_value=86_400,
                value=int(cleaning.DEFAULT_TIMEOUT_SECONDS),
                step=10,
                key="上限_超时",
            )
        st.caption(
            "只影响本次会话。部署时可用环境变量 "
            "`TABLE_CLEANER_MAX_UPLOAD_MB`、`TABLE_CLEANER_MAX_ROWS`、"
            "`TABLE_CLEANER_MAX_COLS`、`TABLE_CLEANER_MAX_CELLS`、"
            "`TABLE_CLEANER_MAX_XLSX_MB`、`TABLE_CLEANER_TIMEOUT_SECONDS` 改默认值（见 README）。"
        )
        st.caption(
            f"当前：单文件 {int(upload_mb)} MB · {int(max_rows):,} 行 · "
            f"超时 {'不限' if not timeout else f'{int(timeout)} 秒'}"
        )
    limits = defaults._replace(
        max_upload_bytes=int(upload_mb) * 1024 * 1024, max_rows=int(max_rows)
    )
    return limits, (float(timeout) if timeout else None)


def _sanitize_multiselect(key: str, options: list[str]) -> None:
    """清掉会话里已经不在当前列中的选中项（换文件/换表/套用模板后可能残留）。"""
    current = st.session_state.get(key)
    if not current:
        return
    kept = [value for value in current if value in options]
    if kept != list(current):
        st.session_state[key] = kept


def apply_pending_template() -> list[str]:
    """把「待套用模板」写进控件状态；必须在本轮任何控件创建之前调用。

    Streamlit 不允许控件创建之后再改它的 session_state，所以按钮里只放一个
    「待套用」标记 + st.rerun()，真正的赋值留到下一轮开头做。
    """
    pending = st.session_state.pop("_待应用模板", None)
    if not pending:
        return []
    columns = list(st.session_state.get("_当前列", []))
    rules, notes = cleaning.filter_for_columns(pending, columns)
    st.session_state.update(
        {
            "表头行": int(rules["表头行"]),
            "规则_删除重复行": bool(rules["删除完全重复的行"]),
            "规则_按列去重": bool(rules["按列去重"]),
            "规则_去重列": list(rules["按列去重"]),
            "规则_删除空值行": bool(rules["删除空值行"]),
            "规则_空值列": list(rules["删除空值行"]),
            "规则_列重命名": cleaning.rules_to_rename_text(rules),
            "规则_删除列": list(rules["删除列"]),
            "规则_列顺序": "\n".join(rules["列顺序"]),
            "规则_转数字": list(rules["转数字"]),
            "规则_转日期": list(rules["转日期"]),
            "规则_计算列": cleaning.rules_to_computed_text(rules),
        }
    )
    st.session_state["_模板提示"] = notes
    return notes


def read_column_ops() -> tuple[ColumnOps, list[str]]:
    """读第 4 步的控件；文字格式写错时收集成提示，不让整页崩掉。"""
    problems: list[str] = []

    def pairs(key: str, example: str) -> tuple[tuple[str, str], ...]:
        try:
            return cleaning.parse_pairs(st.session_state.get(key, ""), example)
        except TableError as exc:
            problems.append(str(exc))
            return ()

    ops = ColumnOps(
        rename=pairs("规则_列重命名", "旧列名=新列名"),
        drop=tuple(st.session_state.get("规则_删除列", [])),
        order=cleaning.parse_names(st.session_state.get("规则_列顺序", "")),
        to_number=tuple(st.session_state.get("规则_转数字", [])),
        to_date=tuple(st.session_state.get("规则_转日期", [])),
        computed=pairs("规则_计算列", "新列名=表达式"),
    )
    return ops, problems


def rules_from_state(ops: ColumnOps) -> dict:
    """把当前控件状态整理成模板字典（保存模板时用，和套用模板的键一一对应）。"""
    return {
        "表头行": int(st.session_state.get("表头行", 1)),
        "删除完全重复的行": bool(st.session_state.get("规则_删除重复行", False)),
        "按列去重": list(st.session_state.get("规则_去重列", []))
        if st.session_state.get("规则_按列去重")
        else [],
        "删除空值行": list(st.session_state.get("规则_空值列", []))
        if st.session_state.get("规则_删除空值行")
        else [],
        "列重命名": [list(pair) for pair in ops.rename],
        "删除列": list(ops.drop),
        "列顺序": list(ops.order),
        "转数字": list(ops.to_number),
        "转日期": list(ops.to_date),
        "计算列": [list(pair) for pair in ops.computed],
    }


# ---------------------------------------------------------------- 读取文件


def get_source(
    limits: cleaning.Limits, timeout: float | None
) -> tuple[str, bytes | None, LoadedTable] | None:
    """读取上传文件或样例数据；出错时展示原因并返回 None。"""
    # 不限制选择器类型：让 .txt/.xls 也能选中，由 load_table 给中文提示，
    # 否则只会看到组件自带的英文报错。
    upload = st.file_uploader(
        f"上传 CSV 或 XLSX 文件（最大 {limits.max_upload_mb} MB）", key="主文件"
    )
    use_demo = st.toggle("没有文件？用虚构样例数据演示", value=False, key="样例开关")
    # 不传 value：初始值由 session_state 决定（模板可以预设），避免 Streamlit 警告
    header_row = st.number_input(
        "表头在第几行（默认第 1 行）",
        min_value=1,
        max_value=200,
        step=1,
        key="表头行",
    )
    if header_row > 1:
        st.caption(
            f"第 {header_row} 行作为列名，上面 {header_row - 1} 行会被忽略；"
            "合并单元格造成的空列名会自动命名为「列3」这类名字。"
        )

    if upload is not None:
        name = upload.name
        data = upload.getvalue()
        try:
            # 大文件在手机上最容易「感觉卡住」，这里明确告诉用户正在干什么
            with st.spinner("正在读取并预检文件…（大文件需要几秒）"):
                loaded = load_table(
                    name,
                    data,
                    header_row=int(header_row),
                    timeout=timeout,
                    limits=limits,
                )
            default = loaded  # 换表失败时退回它，保证页面不会只剩一句报错
            # 多工作表：先按默认表读一次拿到表名清单，用户换表后按所选表重读一次。
            # 不缓存「表名→数据」：文件本来就在内存里，重读最多几秒，比维护缓存状态简单，
            # 也不会把多张表同时留在内存里。换文件时 key 跟着文件名变，选择器回到默认值。
            if len(loaded.sheet_names) > 1:
                names = list(loaded.sheet_names)
                current = loaded.sheet_name if loaded.sheet_name in names else names[0]
                chosen = st.selectbox(
                    "选择要读取的工作表",
                    names,
                    index=names.index(current),
                    key=f"选择工作表_{name}",
                )
                if chosen != current:
                    try:
                        with st.spinner(f"正在读取工作表「{chosen}」…"):
                            loaded = load_table(
                                name,
                                data,
                                sheet=chosen,
                                header_row=int(header_row),
                                timeout=timeout,
                                limits=limits,
                            )
                    except TableError as exc:
                        # 换到的表超限/读不了时不能把页面变成「只剩一句报错」：
                        # 退回默认表并说清楚，用户还能继续换别的表。
                        st.error(f"工作表「{chosen}」读不了：{exc}")
                        loaded = default
                        loaded.notes.append(
                            f"当前显示的是「{loaded.sheet_name}」；可以换一张表，"
                            "或把这张表拆分后再上传。"
                        )
            return name, data, loaded
        except TableError as exc:
            st.error(f"无法读取文件：{exc}")
            return None
        except Exception:
            # 兜底：内存不足等意外情况也不要直接把 traceback 甩给用户
            st.error("读取失败：文件可能过大或格式异常，请换个小文件或拆分后再试。")
            return None
    if use_demo:
        return (
            "样例数据",
            None,
            LoadedTable(make_sample_df(), ["这是虚构样例数据，不含真实个人信息。"]),
        )
    st.info("请先上传文件，或打开上面的「样例数据」开关。")
    return None


def show_checks(df: pd.DataFrame, stats: dict[str, int]) -> None:
    """展示空值、重复行、可疑数据三类检查结果。"""
    missing = find_missing(df)
    missing_rows = [
        {"列名": str(col), "空值数量": int(count)}
        for col, count in missing.items()
        if count > 0
    ]
    if missing_rows:
        st.write("各列空值数量")
        st.dataframe(pd.DataFrame(missing_rows), width="stretch", hide_index=True)
    else:
        st.success("没有发现空白单元格。")

    st.write(f"完全重复的行：**{stats['完全重复行']}** 行")
    st.caption(DUPLICATE_RULE)

    suspicious = find_suspicious(df)
    if suspicious:
        rows = [
            {"列名": col, "问题": issue, "单元格数": count}
            for col, issues in suspicious.items()
            for issue, count in issues.items()
        ]
        st.write("可疑格式（只提示，不自动修改）")
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        st.caption(
            "只是格式提示，需要你人工确认；工具不会自动改这些单元格。"
            "导出的 CSV 默认已开启公式防护，导出的 XLSX 也按文本写入；"
            "防护不能覆盖所有软件和宏场景，来源不明的文件请先核对内容再打开。"
        )
    else:
        st.success("没有发现首尾空格或公式开头的可疑内容。")


# ---------------------------------------------------------------- 各步骤界面


def show_row_rules(columns: list[str]) -> None:
    """第 3 步：行清洗选项（默认都不做）。"""
    st.subheader("3. 选择清洗操作（默认都不执行）")
    st.checkbox("删除完全重复的行", key="规则_删除重复行")
    drop_na = st.checkbox("删除指定列中有空值的行", key="规则_删除空值行")
    target_cols = st.multiselect(
        "选择要检查空值的列", options=columns, disabled=not drop_na, key="规则_空值列"
    )
    if drop_na and not target_cols:
        st.warning("已勾选删除空值行，但还没有选择列，这一步暂时不会删除任何行。")

    dedupe = st.checkbox("按指定列去重（保留第一次出现的行）", key="规则_按列去重")
    dedupe_cols = st.multiselect(
        "选择去重用的列", options=columns, disabled=not dedupe, key="规则_去重列"
    )
    if dedupe and not dedupe_cols:
        st.warning("已勾选按列去重，但还没有选择列，这一步暂时不会去重。")


def show_column_rules(columns: list[str]) -> None:
    """第 4 步：进阶列操作（全部可选、默认关闭）。"""
    st.subheader("4. 进阶列操作（全部可选，默认关闭）")
    with st.expander("展开设置：改名 / 删列 / 调顺序 / 类型转换 / 计算列", expanded=False):
        st.caption("应用顺序：删列 → 类型转换（用原列名）→ 重命名 → 计算列 → 列顺序（后两步用新列名）。")
        st.text_area(
            "列重命名（每行一个：旧列名=新列名）",
            placeholder="金额=总金额\n备注=说明",
            height=100,
            key="规则_列重命名",
        )
        st.multiselect("删除这些列", options=columns, key="规则_删除列")
        st.text_area(
            "把哪些列排到最前面（每行一个列名，留空=保持原顺序）",
            height=80,
            key="规则_列顺序",
        )
        st.multiselect("转成数字的列（转不了保留原文并提示）", options=columns, key="规则_转数字")
        st.multiselect("转成日期的列（转不了保留原文并提示）", options=columns, key="规则_转日期")
        st.text_area(
            "计算列（每行一个：新列名=表达式）",
            placeholder="总额=单价*数量\n含税=round(总额*1.13, 2)",
            height=100,
            key="规则_计算列",
        )
        st.caption(
            "计算表达式只支持列名、数字、`+ - * / // % **`、括号和 `round()`/`abs()`；"
            "不会执行任何代码。列名写错、除以 0、文本参与计算都会提示并跳过这一列。"
        )


def show_comparison(
    df: pd.DataFrame,
    options: CleanOptions,
    timeout: float | None,
) -> tuple[cleaning.CleanReport, dict[str, int]] | None:
    """第 5 步：算清洗结果并展示前后对比；超时/失败时给中文提示并停止。"""
    st.subheader("5. 清洗前后对比")
    show_removed = st.checkbox("查看被删掉的行样例（最多 5 行）", key="规则_样例")
    stats = summarize(df)
    big = len(df) > cleaning.CHUNK_THRESHOLD_ROWS
    bar = st.progress(0.0, text="正在分块清洗…") if big else None

    def on_progress(done: int, total: int) -> None:
        if bar is not None and total:
            bar.progress(min(done / total, 1.0), text=f"正在分块清洗… {done:,}/{total:,} 行")

    try:
        report = cleaning.clean_table(
            df,
            options,
            sample=5 if show_removed else 0,
            progress=on_progress if big else None,
            deadline=cleaning.Deadline(timeout),
        )
    except TableError as exc:
        if bar is not None:
            bar.empty()
        st.error(f"清洗没有完成：{exc}")
        st.info(
            "本次没有生成任何下载文件。可以把文件拆小、少勾几张工作表，"
            "或在左侧「高级设置 → 处理超时」里调大秒数后再试。"
        )
        st.stop()
    if bar is not None:
        bar.empty()

    for note in report.notes:
        st.warning(note)
    cleaned = report.df
    st.dataframe(
        pd.DataFrame({"清洗前": stats, "清洗后": summarize(cleaned)}),
        width="stretch",
    )
    if report.steps:
        st.write("每一步删掉多少行")
        st.dataframe(
            pd.DataFrame(
                [{"步骤": label, "删掉行数": n} for label, n in report.steps.items()]
            ),
            width="stretch",
            hide_index=True,
        )
        st.caption("列变换不删行，所以不在这张表里；转换失败的单元格会有上面的提示。")
        if show_removed:
            if len(report.removed):
                st.write("被删掉的行（最多 5 行，含删除原因）")
                st.dataframe(report.removed, width="stretch", hide_index=True)
                st.caption(
                    "只做样例展示：原始行号已重置，同一行可能先因重复被判、也可能因空值被判，"
                    "「删除原因」写的是它实际被删在哪一步；列名是清洗时的原始列名。"
                )
            else:
                st.info("没有删掉任何行，所以没有样例。")
    if not cleaned.equals(df):
        st.write("清洗后预览（前 10 行）")
        st.dataframe(cleaned.head(10), width="stretch", hide_index=True)
    else:
        st.info(
            "当前没有实际删除任何行，也没有改列。CSV 若开启防公式，危险开头的文本会加前缀；"
            "关闭后与原数据一致。"
        )
    return report, stats


def show_export(
    name: str,
    loaded: LoadedTable,
    cleaned: pd.DataFrame,
    options: CleanOptions,
    timeout: float | None,
) -> None:
    """第 6 步：导出选项 + 生成下载（带时间戳文件名，不覆盖原文件）。"""
    st.subheader("6. 生成并下载清洗结果")
    st.caption(
        "下载不会覆盖你上传的原文件；文件只在内存中处理，不保存到服务器。"
        "数据多时生成要几秒，所以点按钮才生成，避免每改一次选项都卡一下。"
    )
    formula_mode = st.radio(
        "导出 CSV 时的公式防护",
        options=list(cleaning.FORMULA_MODES),
        format_func=lambda mode: cleaning.FORMULA_MODE_LABELS[mode],
        key="导出_防护模式",
    )
    st.caption(
        "单引号兼容性最好但可能被看见；零宽空格看不见，但用文本工具搜索/比较会受影响；"
        "关闭则完全按原样导出（风险自负）。纯数字（如 -5）在任何模式下都不加前缀。"
        "XLSX 不需要这个开关：`=` 开头的文字一律按文本写入。"
    )
    number_mode = st.radio(
        "导出 XLSX 时编号类字段怎么处理",
        options=list(cleaning.NUMBER_MODES),
        format_func=lambda mode: cleaning.NUMBER_MODE_LABELS[mode],
        key="导出_编号模式",
    )
    st.caption("CSV 导出不受这个开关影响（CSV 没有类型，编号始终原样写出）。")

    # 用清洗结果的内容做签名：选项没变就复用已生成的文件，避免重复生成；
    # 选项一变（数据/防护模式/列变换/编号模式变了）就丢弃旧文件，防止下载到上一次的结果。
    row_hashes = pd.util.hash_pandas_object(cleaned, index=True).to_numpy()
    content_digest = hashlib.sha256(row_hashes.tobytes()).hexdigest()
    signature = (
        len(cleaned),
        tuple(str(col) for col in cleaned.columns),
        tuple(str(dtype) for dtype in cleaned.dtypes),
        loaded.sheet_name,  # 换工作表后旧导出必须失效
        repr(options),
        formula_mode,
        number_mode,
        content_digest,
    )
    exports = st.session_state.get("exports")
    if exports is not None and exports["signature"] != signature:
        st.session_state.pop("exports")
        exports = None

    if st.button("生成下载文件", key="生成下载"):
        with st.spinner("正在生成，数据多时需要几秒…"):
            deadline = cleaning.Deadline(timeout)
            try:
                deadline.check("生成 XLSX")
                xlsx_bytes = to_xlsx_bytes(cleaned, number_mode=number_mode)
                xlsx_error = None
            except TableError as exc:
                xlsx_bytes, xlsx_error = None, str(exc)
            except Exception:
                xlsx_bytes, xlsx_error = None, (
                    "生成 XLSX 失败，请改用 CSV 下载；数据里可能有 Excel 不支持的格式。"
                )
            try:
                deadline.check("生成 CSV")
                csv_bytes = to_csv_bytes(cleaned, neutralize_formulas=formula_mode)
                csv_error = None
            except TableError as exc:
                csv_bytes, csv_error = None, str(exc)
            st.session_state["exports"] = {
                "signature": signature,
                "csv": csv_bytes,
                "csv_name": cleaning.timestamped_name(name, "csv"),
                "csv_error": csv_error,
                "xlsx": xlsx_bytes,
                "xlsx_name": cleaning.timestamped_name(name, "xlsx"),
                "xlsx_error": xlsx_error,
            }
            if timeout and deadline.expired:
                # 单次 pandas/openpyxl 调用没法从中间打断，只能在结束时如实提示
                st.warning(
                    f"这次生成已经超过设定的 {timeout:g} 秒：大文件的 XLSX 导出很慢，"
                    "手机端建议改用 CSV 下载，或在左侧「高级设置」调大「处理超时」。"
                )
        exports = st.session_state["exports"]

    if exports is not None:
        csv_col, xlsx_col = st.columns(2)
        if exports["csv"] is not None:
            csv_col.download_button(
                "下载 CSV",
                data=exports["csv"],
                file_name=exports["csv_name"],
                mime="text/csv",
                width="stretch",
            )
        else:
            csv_col.warning(exports["csv_error"])
        if exports["xlsx"] is not None:
            xlsx_col.download_button(
                "下载 XLSX",
                data=exports["xlsx"],
                file_name=exports["xlsx_name"],
                mime=XLSX_MIME,
                width="stretch",
            )
        else:
            xlsx_col.warning(exports["xlsx_error"])
        st.caption(
            "文件名带原文件名和时间戳，连续导出不会互相覆盖。"
            f"（CSV：{cleaning.FORMULA_MODE_LABELS[formula_mode]}；"
            f"XLSX：{cleaning.NUMBER_MODE_LABELS[number_mode]}）"
        )
    else:
        st.info("先点「生成下载文件」，再选择格式下载。")


def show_batch(
    name: str,
    data: bytes | None,
    loaded: LoadedTable,
    options: CleanOptions,
    limits: cleaning.Limits,
    timeout: float | None,
) -> None:
    """第 7 步：多张工作表 / 多个文件 → 各自清洗 → 一个 ZIP。"""
    st.subheader("7. 批量处理（多张工作表 / 多个文件）")
    on = st.toggle(
        "开启批量：把多张表或多个文件分别清洗后打成一个 ZIP 下载",
        key="批量_开关",
    )
    if not on:
        st.caption("不开也不影响上面的单个文件流程；需要一次处理多张表/多个文件时再打开。")
        return

    fmt_label = st.radio(
        "批量文件格式", ["CSV", "XLSX"], index=0, horizontal=True, key="批量_格式"
    )
    jobs: list[cleaning.BatchJob] = []

    sheet_names = list(loaded.sheet_names)
    if len(sheet_names) > 1 and data is not None:
        chosen_sheets = st.multiselect(
            "一起处理当前文件的这些工作表（各生成一个文件）",
            options=sheet_names,
            default=sheet_names,
            key=f"批量_工作表_{name}",
        )
        jobs.extend(
            cleaning.BatchJob(name, sheet, data) for sheet in chosen_sheets
        )
    elif data is not None:
        jobs.append(cleaning.BatchJob(name, None, data))

    batch_files = st.file_uploader(
        "一次上传多个 CSV/XLSX（可多选；每个文件取第一张有数据的表）",
        accept_multiple_files=True,
        key="批量_文件",
    )
    for item in batch_files or []:
        jobs.append(cleaning.BatchJob(item.name, None, item.getvalue()))
    st.caption(
        "同一个 XLSX 里的多张表请用上面的工作表多选；这里按「每个文件一个结果」处理，"
        "所以一个多表 XLSX 只会取第一张有数据的表。"
    )
    if not jobs:
        st.info("还没有可处理的内容：勾选上面的工作表，或一次上传多个文件。")
        return

    signature = (
        tuple((job.source, job.sheet, len(job.data)) for job in jobs),
        repr(options),
        fmt_label,
        st.session_state.get("导出_防护模式"),
        st.session_state.get("导出_编号模式"),
    )
    stored = st.session_state.get("批量_结果")
    if stored is not None and stored["signature"] != signature:
        st.session_state.pop("批量_结果")
        stored = None

    st.caption(f"待处理 {len(jobs)} 项；每项都用上面第 3、4 步当前的规则。")
    if st.button("生成批量 ZIP", key="批量_生成"):
        bar = st.progress(0.0, text="正在批量处理…")

        def on_progress(done: int, total: int) -> None:
            bar.progress(min(done / total, 1.0), text=f"已处理 {done}/{total} 项…")

        with st.spinner("正在逐项清洗并打包…"):
            report = cleaning.run_batch(
                jobs,
                options,
                export_format=fmt_label.lower(),
                number_mode=st.session_state.get("导出_编号模式", "text"),
                formula_mode=st.session_state.get("导出_防护模式", "quote"),
                timeout=timeout,
                limits=limits,
                progress=on_progress,
            )
        bar.empty()
        st.session_state["批量_结果"] = {
            "signature": signature,
            "report": report,
            "name": cleaning.timestamped_name(
                f"批量{len(jobs)}项", "zip", prefix="清洗结果"
            ),
        }

    stored = st.session_state.get("批量_结果")
    if stored is None:
        st.info("点「生成批量 ZIP」后，这里会出现下载按钮和每项的处理情况。")
        return
    report = stored["report"]
    rows = [
        {
            "来源": item.display,
            "清洗前行数": item.rows_before,
            "清洗后行数": item.rows_after,
            "结果": item.error or "已打包",
        }
        for item in report.items
    ]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    for item in report.items:
        for note in item.notes:
            st.caption(f"{item.display}：{note}")
    if report.zip_bytes is not None:
        st.download_button(
            "下载批量 ZIP",
            data=report.zip_bytes,
            file_name=stored["name"],
            mime="application/zip",
            width="stretch",
        )
        st.caption(f"成功打包 {report.exported} 个文件；失败项的原因见上表。")
    else:
        st.error(
            "这一批没有任何一项成功：请看上面的原因。可以单独上传其中一张表试试，"
            "或按提示调整「表头在第几行」/拆分文件。"
        )


def show_templates(
    columns: list[str], ops: ColumnOps, problems: list[str]
) -> None:
    """第 8 步：清洗规则模板（纯 session_state + 可下载 JSON，不依赖服务器）。"""
    st.subheader("8. 清洗规则模板（可选）")
    st.caption(
        "把当前这套规则存成 JSON 文件：下次上传同类表格，套用模板就能一键还原勾选、"
        "列名和计算列。模板只存规则、不含数据，也不会离开这台服务器。"
    )
    saved: dict[str, dict] = st.session_state.setdefault("规则模板", {})

    uploaded = st.file_uploader(
        "上传之前保存的模板（.json）", type=["json"], key="模板_上传"
    )
    if uploaded is not None:
        token = f"{uploaded.name}:{uploaded.size}"
        if st.session_state.get("_模板已读") != token:
            try:
                rules = cleaning.loads(uploaded.getvalue())
            except TableError as exc:
                st.error(f"模板读不了：{exc}")
            else:
                st.session_state["_待应用模板"] = rules
                st.session_state["_模板已读"] = token
                st.rerun()

    tpl_name = st.text_input("模板名称", value="我的清洗规则", key="模板_名称")
    if st.button("保存当前规则为模板", key="模板_保存"):
        if problems:
            st.error("进阶设置有格式问题，先把下面这些改好再保存：" + "；".join(problems))
        else:
            try:
                template = cleaning.build_template(
                    rules_from_state(ops), name=tpl_name
                )
            except TableError as exc:
                st.error(f"这套规则存不下来：{exc}")
            else:
                saved[template["名称"]] = template
                st.success(
                    f"已保存模板「{template['名称']}」；下面可以「下载」存到手机或「套用」。"
                )

    if not saved:
        st.info("还没有保存过模板。勾好规则后点「保存当前规则为模板」。")
        return
    for template_name, template in list(saved.items()):
        title_col, apply_col, download_col = st.columns([2, 1, 1])
        title_col.markdown(f"**{template_name}**")
        title_col.caption(cleaning.summarize_rules(template["规则"]))
        if apply_col.button("套用", key=f"模板_套用_{template_name}"):
            st.session_state["_待应用模板"] = template["规则"]
            st.rerun()
        download_col.download_button(
            "下载",
            data=cleaning.dumps(template),
            file_name=f"清洗模板_{template_name}_{datetime.now():%Y%m%d}.json",
            mime=JSON_MIME,
            key=f"模板_下载_{template_name}",
        )


# ---------------------------------------------------------------- 主流程


def main() -> None:
    st.title("表格清洗小工具")
    st.caption(
        f"v{APP_VERSION} · 上传 → 检查 → 勾选清洗操作 → 生成并下载。原始文件不会被修改。"
    )
    st.caption("工具只能帮你发现常见问题，不保证所有数据都正确，导出前请自己核对。")

    apply_pending_template()
    limits, timeout = sidebar_settings()

    source = get_source(limits, timeout)
    if source is None:
        return
    name, data, loaded = source
    df = loaded.df
    columns = [str(column) for column in df.columns]
    st.session_state["_当前列"] = columns

    for note in st.session_state.pop("_模板提示", []):
        st.info(note)
    for note in loaded.notes:
        st.info(note)

    st.subheader("1. 文件概览")
    stats = summarize(df)
    left, right = st.columns(2)
    left.metric("行数", stats["行数"])
    right.metric("列数", stats["列数"])
    st.caption("列名：" + " ｜ ".join(columns))
    st.write("数据预览（前 10 行）")
    st.dataframe(df.head(10), width="stretch", hide_index=True)

    st.subheader("2. 检查结果")
    st.caption(MISSING_RULE)
    show_checks(df, stats)

    for key in ("规则_空值列", "规则_去重列", "规则_删除列", "规则_转数字", "规则_转日期"):
        _sanitize_multiselect(key, columns)
    show_row_rules(columns)
    show_column_rules(columns)

    ops, problems = read_column_ops()
    for problem in problems:
        st.error(f"进阶设置有格式问题：{problem}")
    options = CleanOptions(
        header_row=int(st.session_state.get("表头行", 1)),
        drop_duplicates=bool(st.session_state.get("规则_删除重复行", False)),
        dedupe_on=tuple(st.session_state.get("规则_去重列", []))
        if st.session_state.get("规则_按列去重")
        else (),
        drop_missing_in=tuple(st.session_state.get("规则_空值列", []))
        if st.session_state.get("规则_删除空值行")
        else (),
        columns=ops,
    )

    result = show_comparison(df, options, timeout)
    if result is None:
        return
    report, stats = result
    cleaned = report.df

    show_export(name, loaded, cleaned, options, timeout)
    show_batch(name, data, loaded, options, limits, timeout)
    show_templates(columns, ops, problems)

    with st.expander("使用说明与判定规则"):
        st.markdown(
            f"""
- **重复行**：{DUPLICATE_RULE}
- **缺失值**：{MISSING_RULE}
- **可疑数据**：只提示首尾空格和疑似公式/注入开头（`= @ + -`、前置空白/BOM/零宽字符），不会自动修改。
- **表头行**：可选第 N 行作表头（默认第 1 行）；合并单元格造成的空列名自动命名成「列3」，可在第 4 步改名。
- **CSV 按文本读取**：保留 `00123` 这类编号；XLSX 里以**文本**保存的编号也原样保留（v2.0.1）。
  导出的 XLSX 默认也按文本写入，需要计算时可在第 6 步选「尝试转数字」。
- **XLSX 多工作表**：文件有多张表时，页面顶部会出现「选择要读取的工作表」，换表会重新读取选中的那张；
  只有一张表时不显示选择器。其他工作表不解析，行列/单元格上限也只对选中的这张表生效。
  要**一次处理多张表**，用第 7 步的批量处理，选多张表后下载一个 ZIP。
- **批量上传多个文件**：第 7 步可一次选多个 CSV/XLSX，每个文件各自清洗，打成一个 ZIP。
- **规则模板**：第 8 步把当前规则存成 JSON 下载；下次上传同类表格时上传模板即可一键还原。
  模板只存规则，不存数据。
- **按列去重**：第 3 步可勾选「按指定列去重」，再选具体列（如订单号），同列值相同的行只保留第一次出现的行。
  它可以和「删除完全重复的行」同时开：顺序是先删完全重复的行、再按列去重、最后删空值行。
- **进阶列操作**（第 4 步，全部可选）：改名、删列、把指定列排前面、文本转数字/日期（失败保留原文并提示）、
  计算列（如 `总额=单价*数量`，只允许四则运算和 `round()/abs()`，不会执行任意代码）。
- **转数字的精度边界**：超过 15 位有效数字的长编号（订单号、身份证号、条码）会保持文本，
  不会被改成另一个数字；需要时请用文本形式保存这类编号。
- **转日期的时区**：同一列里带时区和不带时区混写时，整列保留原文并提示（不猜时区、不改日期）；
  同一时区的日期按当地时间的日期处理，时区标记不保留（Excel 不支持时区）。
- **合并单元格**：数据区里的合并单元格会在页面上提示——合并区域只有左上角有值，
  勾「删除指定列中有空值的行」时请留意，别把这类行当空值行删掉。
- **公式**：XLSX 里的公式读取的是 Excel 缓存的计算结果，导出为数值；没有缓存结果的公式会显示为空。
  导出的 XLSX 中 `=` 开头的文字会按文本写入，不会被 Excel 当公式执行。
- **CSV 的分隔符**：自动识别逗号、分号、制表符、竖线；页面加载时会说明这次按哪种分隔符解析。
- **CSV 公式防护**：三种模式（英文单引号 / 零宽空格 / 关闭），默认单引号；在第 6 步可切换。
- **防护的边界**：无法覆盖所有电子表格版本和宏场景；来源不明的文件仍需先核对内容再打开。
- **资源限制**：单文件 {limits.max_upload_mb} MB、行数 {limits.max_rows:,}、列数
  {limits.max_cols:,}、单元格 {limits.max_cells:,} 个；XLSX 解压后 {limits.max_xlsx_uncompressed_mb} MB；
  单次处理默认超时 {int(cleaning.DEFAULT_TIMEOUT_SECONDS)} 秒（左侧可调）。超过 10 万行默认分块清洗并显示进度。
- **测试**：本版 214 项自动化测试（逻辑 182 + 界面 32）覆盖主要流程，
  但工具只做通用格式检查，不保证业务数据完全正确——重要数据请导出前人工核对。
"""
        )

    st.caption(
        f"版本 {APP_VERSION}。样例数据全部虚构。上传内容仅在本次运行的内存中处理；"
        "页面会话结束后释放，不写入磁盘或数据库。"
    )


main()
