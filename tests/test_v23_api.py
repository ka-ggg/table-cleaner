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
from api import http_api

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _csv(text: str) -> bytes:
    return text.encode("utf-8-sig")


def test_api_console_script_declared_in_pyproject():
    """pyproject 必须注册 table-cleaner-api console script。"""
    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["scripts"]["table-cleaner-api"] == "api.http_api:main"


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


def _raw_request(
    server: http_api.ApiServer,
    method: str,
    path: str,
    headers: dict[str, str],
    body: bytes = b"",
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", _port(server), timeout=10)
    connection.putrequest(method, path)
    for key, value in headers.items():
        connection.putheader(key, value)
    connection.endheaders(body)
    response = connection.getresponse()
    content = response.read()
    result_headers = {key.lower(): value for key, value in response.getheaders()}
    status = response.status
    connection.close()
    return status, result_headers, content


def test_api_post_wrong_path_returns_chinese_404(server):
    status, _, content = _request(server, "POST", "/api/wrong", {})
    assert status == 404
    assert "提交清洗请用 POST /api/jobs" in json.loads(content.decode("utf-8"))["错误"]


def test_api_put_and_delete_return_405(server):
    for method in ("PUT", "DELETE"):
        status, _, content = _request(server, method, "/api/jobs")
        assert status == 405
        assert "只支持 GET 和 POST" in json.loads(content.decode("utf-8"))["错误"]


def test_api_invalid_json_body_returns_400(server):
    body = b"not json"
    status, _, content = _raw_request(
        server,
        "POST",
        "/api/jobs",
        {"Content-Type": "application/json", "Content-Length": str(len(body))},
        body,
    )
    assert status == 400
    assert "不是合法的 UTF-8 JSON" in json.loads(content.decode("utf-8"))["错误"]


def test_api_json_array_body_returns_400(server):
    body = json.dumps([1, 2, 3]).encode()
    status, _, content = _raw_request(
        server,
        "POST",
        "/api/jobs",
        {"Content-Type": "application/json", "Content-Length": str(len(body))},
        body,
    )
    assert status == 400
    assert "JSON 对象" in json.loads(content.decode("utf-8"))["错误"]


def test_api_incomplete_body_returns_400_not_hang(server, monkeypatch):
    """声明了 Content-Length 却不发够正文：必须快速回中文 400，不能挂住线程。"""
    monkeypatch.setattr(http_api, "API_READ_TIMEOUT_SECONDS", 0.2)
    status, _, content = _raw_request(
        server,
        "POST",
        "/api/jobs",
        {"Content-Type": "application/json", "Content-Length": "9"},
    )
    assert status == 400
    assert "请求体不完整" in json.loads(content.decode("utf-8"))["错误"]


def test_api_hung_bad_request_does_not_block_normal_request(server, monkeypatch):
    """一个挂起的半截请求只占自己的线程，后续正常请求仍能 201。"""
    monkeypatch.setattr(http_api, "API_READ_TIMEOUT_SECONDS", 0.2)
    ready = threading.Event()
    statuses: list[int] = []

    def slow_bad_request() -> None:
        connection = http.client.HTTPConnection("127.0.0.1", _port(server), timeout=10)
        connection.putrequest("POST", "/api/jobs")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", "9")
        connection.endheaders()
        ready.set()
        response = connection.getresponse()
        statuses.append(response.status)
        response.read()
        connection.close()

    thread = threading.Thread(target=slow_bad_request, daemon=True)
    thread.start()
    assert ready.wait(2)
    status, _ = _submit(server, {"filename": "a.csv", "content_base64": _encode(_csv("a\n1\n"))})
    assert status == 201
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert statuses == [400]


def test_api_missing_content_base64_returns_400(server):
    status, payload = _submit(server, {"filename": "a.csv"})
    assert status == 400
    assert "content_base64" in str(payload["错误"])


def test_api_template_wrong_type_returns_400(server):
    status, payload = _submit(
        server,
        {"filename": "a.csv", "content_base64": _encode(_csv("a\n1\n")), "template": "not-an-object"},
    )
    assert status == 400
    assert "template / rules" in str(payload["错误"])


def test_api_empty_job_id_paths_return_404(server):
    for suffix in ("result", "report"):
        status, _, content = _request(server, "GET", f"/api/jobs//{suffix}")
        assert status == 404
        assert "接口不存在" in json.loads(content.decode("utf-8"))["错误"]


def test_api_body_too_large_returns_413(server):
    import cleaning.limits as limits

    status, _, content = _raw_request(
        server,
        "POST",
        "/api/jobs",
        {"Content-Type": "application/json", "Content-Length": str(limits.API_MAX_BODY_BYTES + 1)},
    )
    assert status == 413
    assert "请求体超过" in json.loads(content.decode("utf-8"))["错误"]


def test_job_store_evicts_oldest_when_full():
    store = http_api.JobStore(max_jobs=1)
    first = cleaning.CleanResult("a.csv", "清洗后_a.csv", b"1", "csv", 1, 1, {}, (), (), None)
    second = cleaning.CleanResult("b.csv", "清洗后_b.csv", b"2", "csv", 1, 1, {}, (), (), None)
    first_id = store.put(first)
    second_id = store.put(second)
    assert store.get(first_id) is None
    assert store.get(second_id) is second


def test_api_main_rejects_invalid_port(capsys):
    assert http_api.main(["--port", "-1"]) == 2
    assert "0 到 65535" in capsys.readouterr().err


def test_api_main_startup_oserror_returns_one(monkeypatch, capsys):
    def boom(host, port):
        raise OSError(99, "端口不可用")

    monkeypatch.setattr(http_api, "create_server", boom)
    assert http_api.main(["--port", "18767"]) == 1
    assert "启动失败" in capsys.readouterr().err


def test_api_main_starts_and_stops_on_keyboard_interrupt(monkeypatch, capsys):
    class FakeServer:
        server_address = ("127.0.0.1", 18768)
        closed = False
        shut = False

        def serve_forever(self):
            raise KeyboardInterrupt

        def shutdown(self):
            self.shut = True

        def server_close(self):
            self.closed = True

    fake = FakeServer()
    monkeypatch.setattr(http_api, "create_server", lambda host, port: fake)
    assert http_api.main(["--port", "18768"]) == 0
    assert fake.shut is True and fake.closed is True
    assert "已启动" in capsys.readouterr().out


def test_api_unexpected_error_returns_500(monkeypatch, server):
    def boom(*args, **kwargs):
        raise RuntimeError("模拟内部错误")

    monkeypatch.setattr(cleaning, "clean_bytes", boom)
    status, payload = _submit(
        server,
        {"filename": "a.csv", "content_base64": _encode(_csv("a\n1\n"))},
    )
    assert status == 500
    assert "处理失败" in str(payload["错误"])
