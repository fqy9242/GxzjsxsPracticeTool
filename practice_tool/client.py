"""通过实习平台 HTTPS 接口登录、查询和提交签到。"""

import json
import math
import re
from collections.abc import Callable
from http.client import HTTPException
from http.cookiejar import Cookie, CookieJar
from typing import Any, Optional
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

from .location import distance_metres, gcj02

_PAGE_ORIGIN = "https://gxzjsxs.gxibvc.net"
PLATFORM_LOGIN_URL = f"{_PAGE_ORIGIN}/pages/login"
_API_ROOT = "https://gxzjsx.gxibvc.net/api/studentapp"
_API_HOST = urlsplit(_API_ROOT).hostname
_REQUEST_TIMEOUT = 12
_TOKEN_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9-]*")
_TOKEN_VALUE = re.compile(r"[\x21-\x7e]+")
_COOKIE_FIELDS = (
    "version", "name", "value", "port", "port_specified", "domain", "domain_specified",
    "domain_initial_dot", "path", "path_specified", "secure", "expires", "discard", "comment",
    "comment_url", "rfc2109",
)


class PracticeError(RuntimeError):
    """实习系统请求未能完成。"""


class _PlatformRejection(PracticeError):
    """平台返回了明确的业务错误。"""


class SessionExpired(_PlatformRejection):
    """平台需要重新登录。"""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # 登录请求包含密码，后续请求包含当前账号的会话令牌。
        return None


class PracticeClient:
    """一个账号在当前进程中的会话。"""

    def __init__(self) -> None:
        self._cookies = CookieJar()
        self._opener = build_opener(_NoRedirect(), HTTPCookieProcessor(self._cookies))
        self._authorization = ""

    def _clear_session(self) -> None:
        self._authorization = ""
        self._cookies.clear()

    @property
    def has_session(self) -> bool:
        self._cookies.clear_expired_cookies()
        return bool(self._authorization or len(self._cookies))

    def export_session(self) -> dict:
        self._cookies.clear_expired_cookies()
        return {
            "authorization": self._authorization,
            "cookies": [{field: getattr(cookie, field) for field in _COOKIE_FIELDS} for cookie in self._cookies],
        }

    def restore_session(self, state: dict) -> None:
        self._clear_session()
        authorization = state.get("authorization", "")
        if isinstance(authorization, str):
            scheme, _, value = authorization.partition(" ")
            if _TOKEN_SCHEME.fullmatch(scheme) and _TOKEN_VALUE.fullmatch(value):
                self._authorization = authorization
        cookies = state.get("cookies")
        if not isinstance(cookies, list):
            return
        for values in cookies:
            if not isinstance(values, dict) or not isinstance(values.get("domain"), str):
                continue
            domain = values["domain"].lstrip(".").lower()
            if domain not in (_API_HOST, "gxibvc.net"):
                continue
            try:
                cookie = Cookie(**{field: values[field] for field in _COOKIE_FIELDS}, rest={})
                if not cookie.is_expired():
                    self._cookies.set_cookie(cookie)
            except (KeyError, TypeError, ValueError):
                continue

    def login(self, username: str, password: str) -> None:
        self._clear_session()
        if not username.strip() or not password:
            raise PracticeError("请输入账号和密码。")

        token = self._post(
            "Login", {"UserName": username.strip(), "Password": password}, "登录"
        )
        token = token if isinstance(token, dict) else {}
        token_type = token.get("token_type")
        access_token = token.get("access_token")
        if (
            isinstance(token_type, str) and _TOKEN_SCHEME.fullmatch(token_type)
            and isinstance(access_token, str) and _TOKEN_VALUE.fullmatch(access_token)
        ):
            self._authorization = f"{token_type} {access_token}"
        if not self.has_session:
            raise PracticeError("系统未返回有效的登录会话。")

    def signed_today(self) -> bool:
        if not self.has_session:
            raise SessionExpired("请先登录实习系统。")

        data = self._post("Info", {}, "查询签到状态")
        signed = data.get("SignToday") if isinstance(data, dict) else None
        if type(signed) is bool:
            return signed
        if type(signed) is int and signed in (0, 1):
            return bool(signed)
        raise PracticeError("系统未返回有效的今日签到状态。")

    def check_in(self, locate: Callable[[], tuple[float, float]], *, known_status: Optional[bool] = None) -> bool:
        """提交现场签到。返回 False 表示平台今天已经有签到记录。"""
        signed = self.signed_today() if known_status is None else known_status
        if signed:
            return False

        settings = self._post("Settings", {}, "查询签到设置")
        if not isinstance(settings, dict):
            raise PracticeError("平台未返回有效的签到设置。")
        try:
            company_lat = float(settings["CompanyLat"])
            company_lng = float(settings["CompanyLng"])
            sign_range = float(settings["SignRange"])
        except (ValueError, KeyError, TypeError, OverflowError):
            raise PracticeError("平台的实习单位坐标或签到范围尚未配置。") from None
        if not (0 < abs(company_lat) <= 90 and 0 < abs(company_lng) <= 180 and 0 <= sign_range < math.inf):
            raise PracticeError("平台的实习单位坐标或签到范围无效。")

        current = gcj02(*locate())
        if settings.get("Itinerancy") not in (1, "1"):
            distance = distance_metres(current, (company_lat, company_lng))
            if distance > sign_range:
                raise PracticeError(f"当前位置距签到点约 {distance:.0f} 米，超出平台设置的 {sign_range:g} 米范围。")

        try:
            self._post("Sign", {"Lat": current[0], "Lng": current[1]}, "提交签到")
        except PracticeError as error:
            # 请求超时也可能已经写入。只查记录，避免再次提交。
            try:
                if self.signed_today():
                    return True
            except PracticeError:
                pass
            if isinstance(error, _PlatformRejection):
                raise
            raise PracticeError(f"{error} 请通过今日签到状态确认结果。") from None
        return True

    def _post(self, path: str, payload: dict[str, Any], action: str) -> Any:
        request = Request(
            f"{_API_ROOT}/{path}",
            data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": _PAGE_ORIGIN,
                "Referer": f"{_PAGE_ORIGIN}/",
                "Authorization": self._authorization,
                "Platform": "h5",
                "Wxtoken": "",
            },
            method="POST",
        )

        try:
            with self._opener.open(request, timeout=_REQUEST_TIMEOUT) as response:
                body = json.loads(response.read())
        except HTTPError as error:
            error.close()
            message = f"{action}失败，HTTP 状态码 {error.code}。"
            if error.code in (401, 403):
                self._clear_session()
                raise SessionExpired(message) from None
            raise PracticeError(message) from None
        except (OSError, HTTPException):
            raise PracticeError(f"{action}失败，请检查网络和系统服务。") from None
        except ValueError:
            raise PracticeError(f"{action}失败，系统响应格式异常。") from None

        code = body.get("Code") if isinstance(body, dict) else None
        if type(code) is not int:
            raise PracticeError(f"{action}失败，系统响应缺少有效状态码。")
        if code in (401, 403):
            self._clear_session()
        if code == 9901:
            raise _PlatformRejection("请先在官方页面修改密码，再重新登录。")
        if code != 0:
            message = body.get("Msg")
            if isinstance(message, str):
                message = "".join(char if char.isprintable() else " " for char in message[:500]).strip()
            detail = f"平台提示：{message}" if isinstance(message, str) and message else "平台未返回具体提示。"
            error = SessionExpired if code in (401, 403) else _PlatformRejection
            raise error(f"{action}失败，系统状态码 {code}。{detail}")
        return body.get("Data")
