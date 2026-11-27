"""获取 Windows 的现场位置，并转换为实习平台使用的坐标。"""

import json
import math
import subprocess
import sys


class LocationError(RuntimeError):
    """无法取得有效的现场定位。"""


_LOCATE_WINDOWS = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Device
$watcher = [System.Device.Location.GeoCoordinateWatcher]::new('High')
try {
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(15)
    if (-not $watcher.TryStart($true, [TimeSpan]::FromSeconds(10))) { exit 1 }
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        $position = $watcher.Position
        if ($position -and -not $position.Location.IsUnknown -and
            $position.Timestamp -ge [DateTimeOffset]::UtcNow.AddSeconds(-30)) {
            @{ latitude = $position.Location.Latitude; longitude = $position.Location.Longitude } |
                ConvertTo-Json -Compress
            exit 0
        }
        if ($watcher.Permission -eq 'Denied') { exit 1 }
        Start-Sleep -Milliseconds 250
    }
    exit 1
} finally {
    $watcher.Dispose()
}
"""


def device_position() -> tuple[float, float]:
    """获取现场电脑当前的 WGS‑84 经纬度。"""
    if sys.platform != "win32":
        raise LocationError("命令行定位需要 Windows。也可以在现场设备的 PracticeTool 网页中签到。")

    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _LOCATE_WINDOWS],
            capture_output=True, check=True, text=True, timeout=22,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        position = json.loads(result.stdout)
        return position["latitude"], position["longitude"]
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
        raise LocationError("Windows 未返回当前位置。请检查系统定位和桌面应用定位权限。") from None


def gcj02(latitude: float, longitude: float) -> tuple[float, float]:
    """把 WGS‑84 现场测量值转换成 GCJ‑02。"""
    if (
        type(latitude) not in (int, float)
        or type(longitude) not in (int, float)
        or not (0 < abs(latitude) <= 90 and 0 < abs(longitude) <= 180)
    ):
        raise LocationError("设备返回的经纬度无效。")
    if not (72.004 <= longitude <= 137.8347 and 0.8293 <= latitude <= 55.8271):
        return latitude, longitude

    x, y = longitude - 105, latitude - 35
    sine = math.sin
    pi = math.pi
    shared = (20 * sine(6 * x * pi) + 20 * sine(2 * x * pi)) * 2 / 3
    lat_delta = -100 + 2 * x + 3 * y + 0.2 * y**2 + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    lat_delta += shared + (20 * sine(y * pi) + 40 * sine(y * pi / 3)) * 2 / 3
    lat_delta += (160 * sine(y * pi / 12) + 320 * sine(y * pi / 30)) * 2 / 3
    lng_delta = 300 + x + 2 * y + 0.1 * x**2 + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    lng_delta += shared + (20 * sine(x * pi) + 40 * sine(x * pi / 3)) * 2 / 3
    lng_delta += (150 * sine(x * pi / 12) + 300 * sine(x * pi / 30)) * 2 / 3

    semi_major_axis = 6378245.0
    eccentricity_squared = 0.006693421622965943
    latitude_radians = math.radians(latitude)
    correction = 1 - eccentricity_squared * sine(latitude_radians)**2
    root_correction = math.sqrt(correction)
    lat_scale = semi_major_axis * (1 - eccentricity_squared) / (correction * root_correction)
    lng_scale = semi_major_axis * math.cos(latitude_radians) / root_correction
    return latitude + math.degrees(lat_delta / lat_scale), longitude + math.degrees(lng_delta / lng_scale)


def distance_metres(first: tuple[float, float], second: tuple[float, float]) -> float:
    first_lat, first_lng = map(math.radians, first)
    second_lat, second_lng = map(math.radians, second)
    chord = (
        math.sin((first_lat - second_lat) / 2)**2
        + math.cos(first_lat) * math.cos(second_lat) * math.sin((first_lng - second_lng) / 2)**2
    )
    return round(2 * 6378137 * math.asin(math.sqrt(min(1, chord))), 4)
