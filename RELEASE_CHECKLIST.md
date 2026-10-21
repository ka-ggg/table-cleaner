# 正式发布前检查清单（RELEASE_CHECKLIST）

> 用法：每次发版（哪怕只改一行）照这张表从头打勾。**带 🔴 的项没过就不能对外收费交付。**
> 本清单对应 v2.0.1 的实际状态，最后更新：2026-10-20。

---

## A. 代码与测试

- [x] `python -m pytest -q -rs` 全绿（v2.0.1：**214 passed**，0 skipped，约 80 秒）
- [x] 干净 venv（全新 `python -m venv` + `pip install -r requirements-dev.txt`）全绿（**214 passed**）
- [x] 最低允许版本（streamlit 1.50 / pandas 2.2）能跑：**182 passed, 2 skipped**
      （2 个界面测试文件跳过是已知的 AppTest 限制，不是应用不能跑；见 README）
- [x] 新增/修复的每个缺陷都有**在旧代码上会失败**的回归测试
      （`tests/test_regressions_v201.py` 18 项 + `test_app_ui_v2.py` 2 项）
- [x] 测试文件里没有 `pytest.mark.skip` 绕过失败的逻辑（只有「没装 streamlit」时的模块级跳过）
- [x] 没有把断言改松、没有删测试、没有 `except Exception: pass` 吞错
- [x] `python -m pytest --collect-only -q | tail -1` 与文档写的数量一致（214）
- [x] CI 配置（`.github/workflows/tests.yml`）装的依赖与本地一致，且不依赖 lxml

## B. 版本与文档一致性

- [x] `cleaning/__init__.py` 的 `__version__` = **2.0.1**
- [x] 页面标题下方显示的版本 = `cleaning.__version__`（同一来源，不会不一致）
- [x] `README.md` / `CHANGELOG.md` / `USER_GUIDE.md` / `FAQ.md` 的版本号一致
- [x] README 里的测试数量（214 = 182 逻辑 + 32 界面）与实际 `--collect-only` 一致
- [x] README 的「已知限制」覆盖：长编号精度、百分比/货币格式、合并单元格、大 XLSX 重读、超时、时区
- [x] 文档里的每个功能都能在界面上找到（第 1~8 步、三个导出模式、批量、模板）
- [x] 没有把未实现的功能写成已实现（合表、保留公式/样式、抢占式中断都明确写「不支持」）

## C. 数据安全（这一项不过，其他都白搭）

- [x] 原始文件不会被修改或覆盖（只读上传内容，导出用新文件名 + 时间戳）
- [x] 空值/重复行判定口径在界面、导出防护、README 三处一致（同一份常量文字）
- [x] 长编号不会被「转数字」改值（15 位以上保持文本，含「整列都是长整数」的形态）
- [x] `round()` 保留位数受限、`-3%` 不被当公式、XLSX 文本编号保留（复审补充项）
- [x] 计算列不会静默溢出回绕（溢出留空 + 提示）
- [x] 危险表达式不会卡死/吃光内存（指数爆炸被拦截）
- [x] 日期转换失败保留原文（不猜时区、不改日期）
- [x] 数据区合并单元格有提示（避免误删行）
- [x] 导出 CSV 的公式防护默认开启（`= @ + -` 开头加单引号），XLSX 的 `=` 文字按文本写
- [x] 导出前不静默丢行：删了几行、每一步删多少行都显示；可看被删行样例

## D. 隐私与网络

- [x] 代码里没有对外网络请求（`grep` 过 `requests/urllib/socket/http`，只有 `archive.open` 读内存 zip）
- [x] Streamlit 用量统计已关闭（`.streamlit/config.toml` → `gatherUsageStats = false`，`streamlit config show` 实测 false）
- [x] 上传内容不落盘（无 `open(..., 'w')`、无 tempfile、无数据库）
- [x] 日志里没有表格内容（应用不写业务日志；报错只带列名/单元格值样例，不带整表）
- [x] 页面不引用外部 CDN 资源（`curl` 页面 HTML 只有 Apache 许可证注释里的 URL）
- [x] 部署文档写明「公网无登录，需 Basic Auth/内网」，不暗示有用户体系

## E. 依赖与许可证

- [x] `requirements.txt` 的版本约束能被解析安装（干净 venv 实测）
- [x] `pip-audit` 扫运行依赖：streamlit / pandas / openpyxl 及其依赖**无已知漏洞**
      （只报 venv 自带 pip 的升级项，pip 不随产品交付）
- [x] `THIRD_PARTY_LICENSES.md` 由实际安装的元数据生成，**没有 GPL/AGPL/SSPL 依赖**
- [ ] 🔴 **补 `LICENSE`（或 EULA）**：当前项目**没有许可证文件**，商业交付前必须写清
      授权范围/能否转售/是否含升级/免责。**这一项没打勾前不要收款。**
- [ ] 🔴 若以「源码 zip / 安装包」形式交付：zip 里要带 `THIRD_PARTY_LICENSES.md`
      和各依赖的 LICENSE 声明（见该文件第 5 节）

## F. 安装与可交付性

- [x] 安装步骤在干净环境里照做能成功（`pip install -r requirements.txt` → `streamlit run app.py`）
- [x] 启动后 `/_stcore/health` 返回 200，页面 HTML 正常
- [x] README 写明 Python 版本要求（3.11+，实测 3.12.3）和系统依赖（无额外系统库）
- [x] 没有硬编码本机路径（`grep /root`、`/tmp`、绝对路径：无）
- [x] 退出/重启/重复使用正常（无状态累积在磁盘；批量/导出都存在会话内存里）
- [ ] 🔴 **非技术客户的安装方式**：目前要装 Python + 敲命令。要么由你部署好给网址，
      要么做免安装包。做不到就别承诺「下载即用」。
- [ ] ⚠️ 打包成 exe/app（可选）：需要 Windows/macOS 实机与签名，本环境（Android 容器）做不到；
      真要做，必须分别实测启动、体积、杀毒误报、依赖兼容。

## G. 发布包（zip）卫生

- [x] 排除 `__pycache__/`、`.pytest_cache/`、`.venv/`、`.DS_Store`、`*.pyc`
- [x] 包含：`app.py`、`cleaning/`、`tests/`、`samples/`、`.streamlit/`、`.github/`、
      `requirements*.txt`、`pytest.ini`、`README.md` 与所有交付文档
- [x] 不含客户数据、真实表格、密钥/token/密码、本机日志
- [x] 解压到新目录后能直接 `pip install -r requirements.txt` + 跑测试（发布前实测，见 TEST_REPORT）
- [x] 包内没有绝对路径、没有指向作者机器的符号链接

## H. 上线前最后 5 件事（人工，约 10 分钟）

- [ ] 🔴 **人工在真实浏览器里走一遍**「README 真机验收清单」的 9 步（电脑浏览器即可；
      手机浏览器更佳）。本环境无法代替：读屏/截图不可用、手机访问不到容器端口。
- [ ] 用客户会给的真实类型文件（脱敏）各跑一次：分号 CSV、双表 XLSX、带标题行、含长编号
- [ ] 确认导出文件能被客户用的软件打开（Excel / WPS / 对方系统导入）
- [ ] 保存一份旧版本（回滚用）：当前原始包 `table-cleaner-v2.0.0.zip` 在项目上级目录
- [ ] 把「已知限制」「售后话术」「不要承诺的事」过一遍（`SELLER_SUPPORT_GUIDE.md`）

## I. 回滚与备份

- [x] 修改前有原始快照：`table-cleaner-v2.0.0.zip`（与开工时工作区逐字节一致，sha256 已核对）
- [x] 本轮改动全部在 `cleaning/`、`tests/`、`app.py` 文案与新文档里，可按文件回退
- [ ] 建议：正式发布后给仓库打 tag（`v2.0.1`），下一版从 tag 拉分支

---

## 一句话判定

- A~D 全绿 + F 的第一条（干净环境能装）→ 可以**有限试用**；
- 再补上 E 的 LICENSE、F 的非技术安装方式、H 的人工验收 → 才谈得上**有限商业交付**；
- 任何一条 🔴 没过就收款 → 退款和纠纷风险由你承担。
