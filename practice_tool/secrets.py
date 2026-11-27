"""用 Windows DPAPI 保护当前用户的实习账号凭据和会话。"""

import ctypes
import sys
from ctypes import wintypes


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
