"""v2.3.0 HTTP API 雏形测试：提交、取结果、取报告三件事与中文错误。"""

from __future__ import annotations

import base64
import http.client
import json
import threading
import tomllib
from collections.abc import Iterator
from pathlib import Path

import cleaning
import pandas as pd
import pytest
from cleaning import http_api

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _csv(text: str) -> bytes:
    return text.encode("utf-8-sig")


def test_api_console_script_declared_in_pyproject():
    """pyproject 必须注册 table-cleaner-api console script。"""
    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["scripts"]["table-cleaner-api"] == "cleaning.http_api:main"


def _encode(content: bytes) -> str:
    return base64.b64encode(content).decode("ascii")


@pytest.fixture()
def server() -> Iterator[http_api.ApiServer]:
    instance = http_api.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


def _port(server: http_api.ApiServer) -> int:
    return int(server.server_address[1])


def _request(
    server: http_api.ApiServer,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", _port(server), timeout=10)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    content = response.read()
    result_headers = {key.lower(): value for key, value in response.getheaders()}
    status = response.status
    connection.close()
    return status, result_headers, content


def _submit(server: http_api.ApiServer, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
    status, _, content = _request(server, "POST", "/api/jobs", payload)
    return status, json.loads(content.decode("utf-8"))


def test_api_submit_result_and_report_round_trip(server):
    """提交 → 取结果 → 取报告三件事完整往返，结果与公共 API 逐字节一致。"""
    content = _csv("订单号,金额\nA1,10\nA1,10\nA2,20\n")
    template = cleaning.build_template({"删除完全重复的行": True})
    status, submitted = _submit(
        server,
        {
            "filename": "订单.csv",
            "content_base64": _encode(content),
            "template": template,
            "export_format": "csv",
        },
    )
    assert status == 201
    assert submitted["status"] == "已完成"
    assert submitted["rows_before"] == 3
    assert submitted["rows_after"] == 2
    job_id = str(submitted["job_id"])

    expected = cleaning.clean_bytes(
        "订单.csv",
        content,
        cleaning.options_from_rules(template["规则"]),
    )
    status, headers, result = _request(server, "GET", f"/api/jobs/{job_id}/result")
    assert status == 200
    assert "text/csv" in headers["content-type"]
    assert result == expected.data
    back = pd.read_csv(pd.io.common.BytesIO(result), dtype=str, keep_default_na=False)
    assert back["订单号"].tolist() == ["A1", "A2"]

    status, headers, report_raw = _request(server, "GET", f"/api/jobs/{job_id}/report")
    assert status == 200
    assert "application/json" in headers["content-type"]
    report = json.loads(report_raw.decode("utf-8"))
    assert report["job_id"] == job_id
    assert report["steps"] == {"删除完全重复的行": 1}
    assert "# 清洗报告" in report["report"]
    assert report["rows_before"] == 3 and report["rows_after"] == 2


def test_api_accepts_rules_without_template_and_xlsx_export(server):
    """也可以直接传规则对象；XLSX 导出返回正确 MIME 与文件内容。"""
    content = _csv("编号,金额\nA1,10\n")
    status, submitted = _submit(
        server,
        {
            "filename": "订单.csv",
            "content_base64": _encode(content),
            "rules": {"列重命名": [["金额", "总金额"]]},
            "export_format": "xlsx",
        },
    )
    assert status == 201
    job_id = str(submitted["job_id"])
    status, headers, result = _request(server, "GET", f"/api/jobs/{job_id}/result")
    assert status == 200
    assert "spreadsheetml" in headers["content-type"]
    back = pd.read_excel(pd.io.common.BytesIO(result))
    assert list(back.columns) == ["编号", "总金额"]


def test_api_bad_base64_returns_chinese_400(server):
    status, payload = _submit(server, {"filename": "a.csv", "content_base64": "不是 base64"})
    assert status == 400
    assert "Base64" in str(payload["错误"])


def test_api_missing_filename_returns_chinese_400(server):
    status, payload = _submit(server, {"content_base64": _encode(_csv("a\n1\n"))})
    assert status == 400
    assert "filename" in str(payload["错误"])


def test_api_bad_template_version_returns_chinese_400(server):
    template = cleaning.build_template({})
    template["template_version"] = 99
    status, payload = _submit(
        server,
        {
            "filename": "a.csv",
            "content_base64": _encode(_csv("a\n1\n")),
            "template": template,
        },
    )
    assert status == 400
    assert "高于当前工具支持的版本" in str(payload["错误"])


def test_api_unknown_job_returns_chinese_404(server):
    status, _, content = _request(server, "GET", "/api/jobs/not-found/result")
    assert status == 404
    assert "找不到这个任务" in json.loads(content.decode("utf-8"))["错误"]


def test_api_unknown_path_returns_chinese_404(server):
    status, _, content = _request(server, "GET", "/nope")
    assert status == 404
    assert "接口不存在" in json.loads(content.decode("utf-8"))["错误"]


def test_api_empty_body_returns_chinese_400(server):
    status, _, content = _request(server, "POST", "/api/jobs")
    assert status == 400
    assert "请求体为空" in json.loads(content.decode("utf-8"))["错误"]


def test_api_has_no_auth_and_no_persistent_files(server, tmp_path):
    """无鉴权是本版明示的限制；请求不需要任何 token，也不在仓库/tmp 里落文件。"""
    before = set(PROJECT_ROOT.rglob("*.csv"))
    status, _ = _submit(
        server,
        {"filename": "a.csv", "content_base64": _encode(_csv("a\n1\n"))},
    )
    assert status == 201
    assert set(PROJECT_ROOT.rglob("*.csv")) == before
    assert not list(tmp_path.rglob("*"))
