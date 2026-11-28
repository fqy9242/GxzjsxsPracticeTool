"""Explicit authenticated access for server deployments."""

import base64
import binascii
import hashlib
import hmac
import os
from pathlib import Path
from urllib.parse import urlsplit


class AccessError(ValueError):
    pass


class ServerAccess:
    def __init__(self):
        username = os.environ.get("PRACTICE_ADMIN_USERNAME", "admin")
        password = os.environ.get("PRACTICE_ADMIN_PASSWORD", "")
        password_file = os.environ.get("PRACTICE_ADMIN_PASSWORD_FILE")
        if password_file:
            if password:
                raise AccessError("PRACTICE_ADMIN_PASSWORD 和 PRACTICE_ADMIN_PASSWORD_FILE 只能设置一项。")
            try:
                password = Path(password_file).read_text(encoding="utf-8").rstrip("\r\n")
            except (OSError, UnicodeError):
                raise AccessError("无法读取管理台密码文件。") from None
        if not username or ":" in username or len(username) > 100 or len(password) < 12:
            raise AccessError("服务器模式需要设置 PRACTICE_ADMIN_PASSWORD（至少 12 个字符）及有效的管理用户名。")
        self._username = hashlib.sha256(username.encode()).digest()
        self._password = hashlib.sha256(password.encode()).digest()
        self.origin = os.environ.get("PRACTICE_PUBLIC_ORIGIN", "").rstrip("/")
        if self.origin:
            try:
                parsed = urlsplit(self.origin)
                parsed.port
            except ValueError:
                raise AccessError("PRACTICE_PUBLIC_ORIGIN 的地址或端口无效。") from None
            if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None
                    or parsed.password is not None or parsed.path or parsed.query or parsed.fragment):
                raise AccessError("PRACTICE_PUBLIC_ORIGIN 必须为完整站点源，例如 https://practice.example.com。")
            self.host = parsed.netloc.lower()
            self.origin = f"{parsed.scheme}://{self.host}"

    def accepts(self, authorization: str) -> bool:
        scheme, _, encoded = authorization.partition(" ")
        if scheme.lower() != "basic" or len(encoded) > 8192:
            return False
        try:
            username, password = base64.b64decode(encoded, validate=True).decode("utf-8").split(":", 1)
        except (binascii.Error, UnicodeError, ValueError):
            return False
        correct_user = hmac.compare_digest(hashlib.sha256(username.encode()).digest(), self._username)
        correct_password = hmac.compare_digest(hashlib.sha256(password.encode()).digest(), self._password)
        return correct_user & correct_password
