"""本地 HTTP API 雏形（仅标准库，无第三方 Web 框架）。

只做三件事：

- ``POST /api/jobs``：提交一次清洗（输入文件 base64 + 可选模板/规则）；
- ``GET /api/jobs/{id}/result``：取清洗后的结果文件；
- ``GET /api/jobs/{id}/report``：取本次运行的中文检查/清洗报告。

明确不做：账号、登录、权限、任务队列、并发调度、结果持久化。任务只存在进程内存里，
服务重启即丢；默认只监听 127.0.0.1，**没有鉴权**，只适合本机或可信内网调用。
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import sys
import threading
import uuid
from collections import OrderedDict
from collections.abc import Sequence
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote, urlparse

import cleaning
from cleaning import API_MAX_BODY_BYTES, API_MAX_JOBS, API_READ_TIMEOUT_SECONDS

PROG = "table-cleaner-api"
_CSV_MIME = "text/csv; charset=utf-8"
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ApiError(Exception):
    """带 HTTP 状态码的中文接口错误。"""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class JobStore:
    """进程内任务表：有上限、先进先出淘汰；加锁保证多线程下不串。"""

    def __init__(self, max_jobs: int = API_MAX_JOBS) -> None:
        self._max_jobs = max(1, int(max_jobs))
        self._jobs: OrderedDict[str, cleaning.CleanResult] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, result: cleaning.CleanResult) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = result
            while len(self._jobs) > self._max_jobs:
                self._jobs.popitem(last=False)
        return job_id

    def get(self, job_id: str) -> cleaning.CleanResult | None:
        with self._lock:
            return self._jobs.get(job_id)


def _payload_text(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def _payload_options(payload: dict[str, object]) -> cleaning.CleanOptions:
    template = payload.get("template")
    rules = payload.get("rules")
    if isinstance(template, dict):
        info = cleaning.load_template(json.dumps(template, ensure_ascii=False).encode("utf-8"))
        return cleaning.options_from_rules(info.rules)
    if isinstance(rules, dict):
        return cleaning.options_from_rules(rules)
    if template is None and rules is None:
        return cleaning.CleanOptions()
    raise ApiError(HTTPStatus.BAD_REQUEST, "template / rules 应为 JSON 对象。")


class ApiHandler(BaseHTTPRequestHandler):
    """三个接口的请求处理；所有错误都返回中文 JSON，不泄露英文堆栈。"""

    server_version = f"table-cleaner-api/{cleaning.__version__}"

    @property
    def _store(self) -> JobStore:
        server = self.server
        if not isinstance(server, ApiServer):
            raise RuntimeError("API 服务未初始化任务表")
        return server.store

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - 父类签名固定
        """关掉默认访问日志，避免把用户文件名写到 stderr。"""

    def _send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, content: bytes, filename: str, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(filename)}")
        self.end_headers()
        self.wfile.write(content)

    def _error(self, status: int, message: str) -> None:
        self._send_json(int(status), {"错误": message})

    def _read_body(self, length: int) -> bytes:
        """读满声明的正文；半截正文/连接不动时按中文 400 返回，不能挂住线程。"""
        chunks: list[bytes] = []
        remaining = length
        self.connection.settimeout(API_READ_TIMEOUT_SECONDS)
        try:
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    raise ApiError(
                        HTTPStatus.BAD_REQUEST,
                        "请求体不完整：连接在正文读完前就结束了，请重新提交。",
                    )
                chunks.append(chunk)
                remaining -= len(chunk)
        except (TimeoutError, OSError):
            raise ApiError(
                HTTPStatus.BAD_REQUEST,
                "请求体不完整或读取超时；请确认网络后重新提交。",
            ) from None
        finally:
            self.connection.settimeout(None)
        return b"".join(chunks)

    def _read_payload(self) -> dict[str, object]:
        length_raw = self.headers.get("Content-Length")
        try:
            length = int(length_raw or "0")
        except ValueError:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Content-Length 不是整数。") from None
        if length <= 0:
            raise ApiError(HTTPStatus.BAD_REQUEST, "请求体为空；请提交 JSON。")
        if length > API_MAX_BODY_BYTES:
            raise ApiError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                f"请求体超过 {API_MAX_BODY_BYTES // 1024 // 1024} MB 上限；请拆分文件或改用 CLI。",
            )
        raw = self._read_body(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(HTTPStatus.BAD_REQUEST, "请求体不是合法的 UTF-8 JSON。") from None
        if not isinstance(payload, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "请求体应是一个 JSON 对象。")
        return payload

    def _submit(self) -> None:
        payload = self._read_payload()
        filename = _payload_text(payload, "filename")
        if not filename:
            raise ApiError(HTTPStatus.BAD_REQUEST, "缺少 filename（输入文件名，用于判断 CSV/XLSX）。")
        encoded = _payload_text(payload, "content_base64")
        if not encoded:
            raise ApiError(HTTPStatus.BAD_REQUEST, "缺少 content_base64（输入文件内容的 Base64）。")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise ApiError(HTTPStatus.BAD_REQUEST, "content_base64 不是合法的 Base64。") from None
        options = _payload_options(payload)
        result = cleaning.clean_bytes(
            filename,
            content,
            options,
            export_format=str(payload.get("export_format", "csv")),
            formula_mode=str(payload.get("formula_mode", "quote")),
            number_mode=str(payload.get("number_mode", "text")),
            timeout=cleaning.current_limits().timeout_seconds or None,
        )
        job_id = self._store.put(result)
        self._send_json(
            HTTPStatus.CREATED,
            {
                "job_id": job_id,
                "status": "已完成",
                "source": result.source,
                "filename": result.filename,
                "rows_before": result.rows_before,
                "rows_after": result.rows_after,
                "download_url": f"/api/jobs/{job_id}/result",
                "report_url": f"/api/jobs/{job_id}/report",
            },
        )

    def _job_id_from_path(self, action: str) -> str | None:
        prefix = "/api/jobs/"
        suffix = f"/{action}"
        path = urlparse(self.path).path
        if not path.startswith(prefix) or not path.endswith(suffix):
            return None
        job_id = unquote(path[len(prefix) : -len(suffix)])
        return job_id or None

    def _result(self) -> None:
        job_id = self._job_id_from_path("result")
        if job_id is None:
            self._error(
                HTTPStatus.NOT_FOUND,
                "接口不存在；可用：POST /api/jobs、GET /api/jobs/{id}/result、GET /api/jobs/{id}/report。",
            )
            return
        result = self._store.get(job_id)
        if result is None:
            self._error(HTTPStatus.NOT_FOUND, "找不到这个任务；任务只存在内存里，服务重启后会丢失。")
            return
        content_type = _XLSX_MIME if result.export_format == "xlsx" else _CSV_MIME
        self._send_bytes(result.data, result.filename, content_type)

    def _report(self) -> None:
        job_id = self._job_id_from_path("report")
        if job_id is None:
            self._error(
                HTTPStatus.NOT_FOUND,
                "接口不存在；可用：POST /api/jobs、GET /api/jobs/{id}/result、GET /api/jobs/{id}/report。",
            )
            return
        result = self._store.get(job_id)
        if result is None:
            self._error(HTTPStatus.NOT_FOUND, "找不到这个任务；任务只存在内存里，服务重启后会丢失。")
            return
        self._send_json(
            HTTPStatus.OK,
            {
                "job_id": job_id,
                "source": result.source,
                "rows_before": result.rows_before,
                "rows_after": result.rows_after,
                "steps": result.steps,
                "notes": list(result.notes),
                "load_notes": list(result.load_notes),
                "report": result.report_text,
            },
        )

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 用 do_* 命名
        if urlparse(self.path).path != "/api/jobs":
            self._error(HTTPStatus.NOT_FOUND, "接口不存在；提交清洗请用 POST /api/jobs。")
            return
        try:
            self._submit()
        except ApiError as exc:
            self._error(exc.status, exc.message)
        except cleaning.TableError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "处理失败：程序遇到未预期的问题，请检查输入文件后重试。")

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 用 do_* 命名
        path = urlparse(self.path).path
        if path.startswith("/api/jobs/") and path.endswith("/result"):
            self._result()
            return
        if path.startswith("/api/jobs/") and path.endswith("/report"):
            self._report()
            return
        self._error(
            HTTPStatus.NOT_FOUND,
            "接口不存在；可用：POST /api/jobs、GET /api/jobs/{id}/result、GET /api/jobs/{id}/report。",
        )

    def do_PUT(self) -> None:  # noqa: N802
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "只支持 GET 和 POST。")

    def do_DELETE(self) -> None:  # noqa: N802
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "只支持 GET 和 POST。")


class ApiServer(ThreadingHTTPServer):
    """带任务表的本地 HTTP 服务。"""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], store: JobStore) -> None:
        super().__init__(server_address, ApiHandler)
        self.store = store


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    store: JobStore | None = None,
) -> ApiServer:
    """创建服务实例；``port=0`` 时由系统分配端口（测试用）。"""
    return ApiServer((host, int(port)), store or JobStore())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="table-cleaner 本地 HTTP API 雏形（无鉴权，仅本机/可信内网；任务只存内存）",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """启动 HTTP 服务；Ctrl+C 正常退出。"""
    args = _parser().parse_args(argv)
    if args.port < 0 or args.port > 65535:
        sys.stderr.write("参数错误：--port 必须在 0 到 65535 之间。\n")
        return 2
    try:
        server = create_server(args.host, args.port)
    except OSError as exc:
        sys.stderr.write(f"启动失败：无法监听 {args.host}:{args.port}（{exc.strerror or '端口不可用'}）。\n")
        return 1
    address = server.server_address
    raw_host, raw_port = address[0], address[1]
    host = raw_host.decode("utf-8", "replace") if isinstance(raw_host, bytes) else str(raw_host)
    port = int(raw_port)
    sys.stdout.write(f"{PROG} 已启动：http://{host}:{port}\n")
    sys.stdout.write("仅本机/可信内网使用，无鉴权；任务只存在内存里，重启即丢。按 Ctrl+C 停止。\n")
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
