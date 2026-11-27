"""本地多账号签到管理台和浏览器单次签到入口。"""

import json
import math
import sqlite3
import ssl
import threading
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from .accounts import Accounts, DashboardError, default_data_dir
from .client import PracticeClient, PracticeError
from .location import LocationError
from .secrets import SecretError


_ASSET_DIR = Path(__file__).with_name("web_assets")
_ASSETS = {
    "/simulate-location.js": ("simulate-location.js", "text/javascript; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/dashboard.js": ("dashboard.js", "text/javascript; charset=utf-8"),
    "/dashboard.css": ("dashboard.css", "text/css; charset=utf-8"),
}
_MAIL_SETTINGS = "/api/mail-settings"
_ACCOUNT_ROUTES = {"/api/accounts", "/api/accounts/delete", "/api/accounts/run", _MAIL_SETTINGS}
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost"}
_MAX_BODY_SIZE = 8192


class WebError(RuntimeError):
    """本地 Web 服务无法运行。"""


class _WebServer(ThreadingHTTPServer):
    def __init__(self, address, accounts: Accounts):
        self.check_in_lock = threading.Lock()
        self.accounts = accounts
        super().__init__(address, _RequestHandler)


class _RequestHandler(BaseHTTPRequestHandler):
    def setup(self) -> None:
        self.request.settimeout(15)
        super().setup()

    def do_GET(self) -> None:
        if not self._valid_host():
            self._send_json(HTTPStatus.FORBIDDEN, error="请使用服务启动时显示的地址。")
            return

        path = self.path.partition("?")[0]
        if path in ("/api/dashboard", _MAIL_SETTINGS):
            if self._allow_dashboard():
                try:
                    accounts = self.server.accounts
                    data = accounts.mail_settings() if path == _MAIL_SETTINGS else accounts.overview()
                    self._send_json(HTTPStatus.OK, **data)
                except sqlite3.Error:
                    self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, error="暂时无法读取管理台数据。")
            return
        loopback = self._loopback_request()
        if path == "/":
            filename = "dashboard.html" if loopback else "index.html"
            content_type = "text/html; charset=utf-8"
        elif path == "/simulate-location" or (path == "/schedule" and loopback):
            filename = "dashboard.html"
            content_type = "text/html; charset=utf-8"
        elif path in _ASSETS:
            filename, content_type = _ASSETS[path]
        else:
            self._send_json(HTTPStatus.NOT_FOUND, error="页面不存在。")
            return

        self._send(
            HTTPStatus.OK, content_type, (_ASSET_DIR / filename).read_bytes(),
            static_simulation=path == "/simulate-location" and not loopback,
        )

    def do_POST(self) -> None:
        if not self._same_origin():
            self._send_json(HTTPStatus.FORBIDDEN, error="请从当前服务的页面发起请求。")
            return

        handler = {
            "/api/location": self._receive_location,
            "/api/status": self._account_action,
            "/api/check-in": self._submit_check_in,
        }.get(self.path)
        account_route = self.path in _ACCOUNT_ROUTES
        if account_route and not self._allow_dashboard():
            return
        if handler is None and not account_route:
            self._send_json(HTTPStatus.NOT_FOUND, error="接口不存在。")
            return
        try:
            data = self._read_json()
        except (ValueError, RecursionError, TimeoutError):
            self._send_json(HTTPStatus.BAD_REQUEST, error="请提交有效的 JSON 请求。")
            return

        if account_route:
            self._manage_account(data)
        else:
            handler(data)

    def _manage_account(self, data: dict) -> None:
        accounts = self.server.accounts
        try:
            if self.path == _MAIL_SETTINGS:
                accounts.save_mail_settings(data)
                self._send_json(HTTPStatus.OK, configured=True)
                return
            if self.path == "/api/accounts":
                account_id = accounts.save(data)
                self._send_json(HTTPStatus.OK, id=account_id)
                return
            account_id = data.get("id")
            if type(account_id) is not int or not 0 < account_id < 2**63:
                raise DashboardError("请选择有效的账号。")
            if self.path == "/api/accounts/delete":
                accounts.delete(account_id)
                self._send_json(HTTPStatus.OK, id=account_id)
            else:
                action = data.get("action")
                if action not in ("manual", "status"):
                    raise DashboardError("请选择签到或查询。")
                run_id = accounts.launch(account_id, action)
                self._send_json(HTTPStatus.ACCEPTED, id=run_id)
        except DashboardError as error:
            self._send_json(error.status, error=str(error))
        except SecretError as error:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, error=str(error))
        except sqlite3.Error:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, error="暂时无法更新管理台数据。")

    def _submit_check_in(self, data: dict) -> None:
        latitude, longitude = data.get("latitude"), data.get("longitude")
        if not (_number_in_range(latitude, -90, 90) and _number_in_range(longitude, -180, 180)):
            self._send_json(HTTPStatus.BAD_REQUEST, error="设备位置数据无效。")
            return
        lock = self.server.check_in_lock
        if not lock.acquire(blocking=False):
            self._send_json(HTTPStatus.CONFLICT, error="当前有签到请求正在处理。")
            return
        try:
            self._account_action(data, position=(latitude, longitude))
        finally:
            lock.release()

    def _read_json(self) -> dict[str, Any]:
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Expected JSON")
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= _MAX_BODY_SIZE:
            raise ValueError("Invalid body size")
        data = json.loads(self.rfile.read(length))
        if not isinstance(data, dict):
            raise ValueError("Expected an object")
        return data

    def _receive_location(self, data: dict[str, Any]) -> None:
        latitude = data.get("latitude")
        longitude = data.get("longitude")
        accuracy = data.get("accuracy")
        if (
            not _number_in_range(latitude, -90, 90)
            or not _number_in_range(longitude, -180, 180)
            or type(accuracy) not in (int, float)
            or not 0 <= accuracy < math.inf
        ):
            self._send_json(HTTPStatus.BAD_REQUEST, error="设备位置或精度数据无效。")
            return

        self._send_json(HTTPStatus.OK, accuracy=accuracy)

    def _account_action(self, data: dict[str, Any], position: Optional[tuple[float, float]] = None) -> None:
        username = data.get("username")
        password = data.get("password")
        if (
            not isinstance(username, str) or not username.strip()
            or not isinstance(password, str) or not password
        ):
            self._send_json(HTTPStatus.BAD_REQUEST, error="请输入实习账号和密码。")
            return

        try:
            client = PracticeClient()
            client.login(username, password)
            if position is None:
                result = {"signedToday": client.signed_today()}
            else:
                result = {"alreadySigned": not client.check_in(lambda: position)}
        except LocationError as error:
            self._send_json(HTTPStatus.BAD_REQUEST, error=str(error))
            return
        except PracticeError as error:
            self._send_json(HTTPStatus.BAD_GATEWAY, error=str(error))
            return

        self._send_json(HTTPStatus.OK, **result)

    def _valid_host(self) -> bool:
        host = self.headers.get("Host", "").partition(":")[0].lower()
        return isinstance(self.connection, ssl.SSLSocket) or host in _LOOPBACK_HOSTS

    def _loopback_request(self) -> bool:
        port = self.server.server_port
        hosts = {f"{host}:{port}" for host in _LOOPBACK_HOSTS}
        if port == (443 if isinstance(self.connection, ssl.SSLSocket) else 80):
            hosts.update(_LOOPBACK_HOSTS)
        return self.client_address[0] == "127.0.0.1" and self.headers.get("Host", "").lower() in hosts

    def _allow_dashboard(self) -> bool:
        if self._loopback_request() and self._same_origin() and (
            self.command == "GET" or self.headers.get("Origin") is not None
        ):
            return True
        self._send_json(HTTPStatus.FORBIDDEN, error="请从本机管理台操作。")
        return False

    def _same_origin(self) -> bool:
        scheme = "https" if isinstance(self.connection, ssl.SSLSocket) else "http"
        origin = self.headers.get("Origin")
        fetch_site = self.headers.get("Sec-Fetch-Site")
        return (
            self._valid_host()
            and fetch_site in (None, "same-origin")
            and origin in (None, f"{scheme}://{self.headers.get('Host', '')}")
        )

    def _send_json(self, status: HTTPStatus, **data: Any) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", body)

    def _send(self, status: HTTPStatus, content_type: str, body: bytes, *, static_simulation: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Permissions-Policy", "geolocation=(self)")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
            + ("connect-src 'none'; form-action 'none'" if static_simulation else "form-action 'self'"),
        )
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def _number_in_range(value: Any, minimum: float, maximum: float) -> bool:
    return type(value) in (int, float) and minimum <= value <= maximum


@contextmanager
def open_web_server(
    host: str, port: int, cert: Optional[str] = None, key: Optional[str] = None,
    data_dir: Optional[Path] = None,
):
    if not 0 <= port <= 65535:
        raise WebError("端口应在 0 到 65535 之间。")
    if bool(cert) != bool(key):
        raise WebError("请同时提供 HTTPS 证书和私钥。")
    if not cert and host not in _LOOPBACK_HOSTS:
        raise WebError("从其他设备访问时请使用 HTTPS 证书和私钥。")

    try:
        context = None
        if cert and key:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)

        accounts = Accounts(data_dir or default_data_dir())
        try:
            with _WebServer((host, port), accounts) as server:
                if context is not None:
                    server.socket = context.wrap_socket(server.socket, server_side=True)
                accounts.start()
                yield server
        finally:
            accounts.close()
    except (OSError, sqlite3.Error) as error:
        raise WebError(f"Web 服务启动或运行失败（{error}）。") from None


def serve_web(
    host: str, port: int, cert: Optional[str] = None, key: Optional[str] = None,
    data_dir: Optional[Path] = None,
) -> None:
    with open_web_server(host, port, cert, key, data_dir) as server:
        protocol = "https" if cert else "http"
        print(f"PracticeTool 已监听 {protocol}://{host}:{server.server_port}/", flush=True)
        server.serve_forever()
