# PracticeTool

岗位实习管理系统工具，支持通过 HTTPS 登录、查询今日签到状态和提交现场签到。需要 Python 3.9 或更新版本，Python 部分使用标准库。

## Windows 启动器

双击 `PracticeTool.exe`，点击「启动服务」和「打开 Web 首页」。启动器包含 Python 运行环境，也可以从项目目录运行 `python -m practice_tool web`，然后打开 [本机管理台](http://127.0.0.1:8765/)。

1. 点击「添加账号」，填写显示名称、实习账号和密码。需要自动签到时，开启计划并选择执行时间与频率。
2. 点击账号旁的「查询」核对今日状态，或在现场点击「签到」发起一次签到。
3. 在「执行记录」查看平台回执。账号设置、会话和执行记录会保留在本机。

计划按北京时间执行，可选每天或周一至周五。服务启动后会执行当天已到时间的计划，每账号每天自动尝试一次。停止服务或关闭启动器后，定时任务暂停。自动签到使用运行服务的 Windows 电脑定位，需要在实习地点启用系统定位和桌面应用定位权限。

平台返回的 Cookie 和 Token 按账号缓存。会话失效后，服务使用已保存的密码重登。密码与会话由 Windows 当前用户的 DPAPI 加密，默认数据位于 `%LOCALAPPDATA%\PracticeTool`。多账号管理台仅在本机开放。

如需从源码构建 Windows 启动器，安装 PyInstaller 6 后，在项目目录运行 `.\tools\build-launcher.ps1`。输出位于 `dist\PracticeTool.exe`。

## 登录与查询

在项目目录运行以下命令。

```powershell
python -m practice_tool login
python -m practice_tool status
```

命令会读取账号和密码，输入密码时隐藏字符。也可以为当前进程提供 `PRACTICE_USERNAME` 和 `PRACTICE_PASSWORD`，实现免交互登录。每次运行使用独立的内存会话。

## 命令行现场签到

在实习地点的 Windows 电脑上开启系统定位和桌面应用定位权限，然后在项目目录运行。

```powershell
python -m practice_tool check-in
```

命令先登录并查询今日记录。需要签到时，读取平台的签到设置和设备当前位置，检查签到范围，再直接发送 `POST /api/studentapp/Sign`。平台确认签到或已经签到时退出码为 `0`，请求失败或无法确认结果时退出码为 `1`。

需要定时执行时，可指定北京时间。下面的命令在工作日 09:00 签到，程序需要保持运行。

```powershell
python -m practice_tool check-in --at 09:00 --weekdays-only
```

也可用 Windows 任务计划程序在同一个现场用户会话中运行单次签到命令。将起始位置设为项目目录，并为该进程配置上面的登录环境变量。

## 网页签到与定位

服务运行后，点击管理台底部的「单次签到」，输入实习账号和密码，点击「使用当前位置签到」并允许定位。位置和登录凭据仅用于处理这一次请求。[单次签到页面](http://127.0.0.1:8765/quick)

如果用手机访问电脑上的服务，启动时可用 `--host` 指定局域网监听地址，并用 `--cert` 和 `--key` 指定 HTTPS 证书和私钥。请在手机浏览器中通过与受信任的证书匹配的地址和端口访问。

## Python 客户端

`practice_tool.client.PracticeClient` 提供 `login()`、`signed_today()` 和 `check_in(locate)`。签到时传入提供现场 WGS‑84 经纬度的函数，客户端会转换成平台所需的 GCJ‑02。每个账号操作可创建独立客户端。
