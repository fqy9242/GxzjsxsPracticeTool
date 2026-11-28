"""Protect credentials with Windows DPAPI or a persistent Linux Fernet key."""

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path


class SecretError(RuntimeError):
    """无法保存或读取本机凭据。"""


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.c_void_p)]


def _crypt(data: bytes, *, protect: bool) -> bytes:
    if sys.platform != "win32":
        raise SecretError("账号管理需要 Windows 用户凭据保护。")

    api = ctypes.WinDLL("crypt32", use_last_error=True)
    function = api.CryptProtectData if protect else api.CryptUnprotectData
    function.argtypes = [
        ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
    ]
    function.restype = wintypes.BOOL
    free = ctypes.WinDLL("kernel32").LocalFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = ctypes.c_void_p

    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.addressof(buffer))
    result = _Blob()
    try:
        if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
            action = "加密" if protect else "解密"
            raise SecretError(f"Windows 无法{action}凭据（错误码 {ctypes.get_last_error()}）。")
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.memset(buffer, 0, len(buffer))
        if result.data:
            if not protect:
                ctypes.memset(result.data, 0, result.size)
            free(result.data)


def seal(data: bytes) -> bytes:
    return _crypt(data, protect=True)


def unseal(data: bytes) -> bytes:
    return _crypt(data, protect=False)


class SecretStore:
    """Windows credentials remain compatible; Linux keys live beside the database."""

    _PREFIX = b"PTF1:"

    def __init__(self, directory: Path):
        self._windows = sys.platform == "win32"
        if self._windows:
            return
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            raise SecretError("Linux 凭据保护需要 cryptography，请运行 pip install -r requirements.txt。") from None
        key_path = directory / "secret.key"
        try:
            try:
                descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(Fernet.generate_key())
                    output.flush()
                    os.fsync(output.fileno())
            self._cipher = Fernet(key_path.read_bytes().strip())
        except (OSError, ValueError):
            raise SecretError("无法读取凭据密钥 secret.key，请检查数据目录权限及密钥文件。") from None

    def seal(self, data: bytes) -> bytes:
        if self._windows:
            return seal(data)
        return self._PREFIX + self._cipher.encrypt(data)

    def unseal(self, data: bytes) -> bytes:
        if self._windows:
            if data.startswith(self._PREFIX):
                raise SecretError("此凭据来自 Linux，请在 Windows 中重新填写账号或 SMTP 密码。")
            return unseal(data)
        if not data.startswith(self._PREFIX):
            raise SecretError("无法读取 Windows DPAPI 凭据，请在 Linux 中重新填写账号或 SMTP 密码。")
        from cryptography.fernet import InvalidToken
        try:
            return self._cipher.decrypt(data[len(self._PREFIX):])
        except InvalidToken:
            raise SecretError("凭据解密失败，请恢复与数据库匹配的 secret.key 或重新填写密码。") from None
