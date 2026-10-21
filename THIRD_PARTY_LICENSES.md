# 第三方依赖许可证说明（THIRD_PARTY_LICENSES）

> 本文件是**事实记录**，不是法律意见。下表由实际安装后的包元数据生成
> （`pip install -r requirements.txt` 后的 `importlib.metadata`），版本会随依赖升级变化，
> **每次发布前请重新生成一次**。

## 1. 本项目自身的许可证

**当前没有 LICENSE 文件。** 这意味着：默认版权归作者所有，但买家/客户在法律上不清楚自己
获得了什么权利（能不能商用、能不能改、能不能转发）。

- 商业交付前**必须**补一个 LICENSE / 最终用户协议（EULA），写清授权范围、是否可转售、
  是否提供升级、免责条款。见 `RELEASE_CHECKLIST.md` 的阻塞项。
- 本审查方不能替你决定授权条款，也不会替你编造一个「看起来像法律文件」的 LICENSE。

## 2. 直接运行依赖（`requirements.txt`）

| 包 | 版本约束 | 许可证 | 说明 |
| --- | --- | --- | --- |
| `streamlit` | `>=1.50,<2` | Apache-2.0 | 界面框架 |
| `pandas` | `>=2.2,<4` | BSD-3-Clause | 表格读写与清洗 |
| `openpyxl` | `>=3.1,<4` | MIT | XLSX 读写 |

三者都是宽松许可证，允许商业使用、修改、再分发；再分发时要保留版权与许可证声明。

## 3. 运行时的传递依赖（实测 36 个包，含上面 3 个）

| 包 | 实测版本 | 许可证（元数据） |
| --- | --- | --- |
| `Jinja2` | 3.1.6 | BSD-3-Clause |
| `MarkupSafe` | 3.0.4 | BSD-3-Clause |
| `altair` | 6.3.0 | BSD-3-Clause |
| `anyio` | 4.15.1 | MIT |
| `attrs` | 26.1.0 | MIT |
| `certifi` | 2026.10.18 | MPL-2.0 |
| `charset-normalizer` | 3.5.2 | MIT |
| `click` | 8.5.0 | BSD-3-Clause |
| `et_xmlfile` | 2.0.0 | MIT |
| `h11` | 0.16.0 | MIT |
| `idna` | 3.20 | BSD-3-Clause |
| `itsdangerous` | 2.2.0 | BSD-3-Clause |
| `jsonschema` | 4.26.0 | MIT |
| `jsonschema-specifications` | 2025.9.1 | MIT |
| `narwhals` | 2.27.1 | MIT |
| `numpy` | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| `openpyxl` | 3.1.5 | MIT |
| `packaging` | 26.3 | Apache-2.0 OR BSD-2-Clause |
| `pandas` | 3.0.6 | BSD-3-Clause |
| `pillow` | 12.3.0 | MIT-CMU |
| `protobuf` | 7.36.2 | BSD-3-Clause |
| `pyarrow` | 26.0.0 | Apache-2.0 |
| `pydeck` | 0.9.3 | Apache-2.0 |
| `python-dateutil` | 2.9.0.post0 | BSD-3-Clause / Apache-2.0（双许可） |
| `python-multipart` | 0.0.32 | Apache-2.0 |
| `referencing` | 0.37.0 | MIT |
| `requests` | 2.34.2 | Apache-2.0 |
| `rpds-py` | 2026.9.1 | MIT |
| `six` | 1.17.0 | MIT |
| `starlette` | 1.9.0 | BSD-3-Clause |
| `streamlit` | 1.66.0 | Apache-2.0 |
| `typing_extensions` | 4.16.0 | PSF-2.0 |
| `urllib3` | 2.8.0 | MIT |
| `uvicorn` | 0.54.0 | BSD-3-Clause |
| `watchdog` | 6.0.0 | Apache-2.0 |
| `websockets` | 17.3 | BSD-3-Clause |

注意事项：

- **`certifi` 是 MPL-2.0**：只在文件层面要求开源修改过的 MPL 文件，不影响你的闭源应用；
  再分发时保留其许可证与声明即可。
- **没有 GPL/AGPL 依赖**（这是可以闭源商业交付的前提）。重新生成清单时请核对这一条：
  一旦出现 `GPL`、`AGPL`、`SSPL` 等强传染性许可证，就要停下来评估。
- `pyarrow` / `pillow` / `protobuf` 体积大，但都允许商用。

## 4. 开发/测试依赖（`requirements-dev.txt`，不随产品交付）

| 包 | 许可证 | 说明 |
| --- | --- | --- |
| `pytest` | MIT | 测试框架 |
| `streamlit==1.66.0` | Apache-2.0 | 固定版本以支持 `AppTest` 的 file_uploader |

## 5. 分发时要做的事

**只把服务部署在服务器上、不给客户源码/压缩包**：
- BSD/MIT 不要求对「网络使用」做声明（没有 AGPL 那种网络条款）；
- 但客户问起时，你有义务说明用了哪些开源组件；建议随产品页附上本文件。

**把源码 zip 或打包好的程序发给客户（再分发）**：
- 必须随包附上本文件，以及各依赖自带的 LICENSE/版权声明（pip 包内一般都有）；
- 不能删除依赖包里的版权头；
- Apache-2.0 的包如果带 `NOTICE` 文件，要一起保留。

## 6. 重新生成本清单

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python - <<'PY'
from importlib.metadata import distributions
for d in sorted(distributions(), key=lambda d: d.metadata["Name"].lower()):
    lic = d.metadata.get("License-Expression") or ""
    cls = [c.split("::")[-1].strip() for c in (d.metadata.get_all("Classifier") or []) if c.startswith("License ::")]
    print(d.metadata["Name"], d.version, lic or (cls[0] if cls else "?"))
PY
```

（本文件第 3 节的表就是这样生成的。）
