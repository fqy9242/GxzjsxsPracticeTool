"""PracticeTool 命令行入口。"""

import argparse
import getpass
import os
import sys
import warnings
from datetime import datetime, time
from pathlib import Path
from time import sleep

from .accounts import BEIJING
from .client import PracticeClient, PracticeError
from .location import LocationError, device_position
from .web import WebError, serve_web


def _read_credentials() -> tuple[str, str]:
    username = os.environ.get("PRACTICE_USERNAME")
    password = os.environ.get("PRACTICE_PASSWORD")
    if (username is None or password is None) and not sys.stdin.isatty():
        raise PracticeError(
            "请在终端交互输入账号密码，"
            "或为当前进程提供 PRACTICE_USERNAME 和 PRACTICE_PASSWORD。"
        )

    if username is None:
        username = input("实习系统账号：")
    if password is None:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            try:
                password = getpass.getpass("实习系统密码：")
            except getpass.GetPassWarning:
                raise PracticeError("当前终端无法隐藏密码输入，请使用支持交互输入的终端。") from None
    return username, password


def _check_in(username: str, password: str) -> None:
    client = PracticeClient()
    client.login(username, password)
    print("签到成功。" if client.check_in(device_position) else "平台显示今天已经签到。", flush=True)


def _time_of_day(value: str) -> time:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        raise argparse.ArgumentTypeError("请用 HH:MM 指定北京时间。") from None


def _check_in_daily(username: str, password: str, at: time, weekdays_only: bool) -> None:
    last_attempt = None
    print(f"定时签到已启动，每天北京时间 {at:%H:%M} 检查。", flush=True)
    while True:
        now = datetime.now(BEIJING)
        today = now.date()
        if today != last_attempt and now.time() >= at and (not weekdays_only or now.weekday() < 5):
            last_attempt = today
            try:
                _check_in(username, password)
            except (PracticeError, LocationError) as error:
                print(f"{today} {error}", file=sys.stderr, flush=True)
        sleep(15)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m practice_tool", description="岗位实习管理系统命令行工具"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("login", help="通过 HTTPS 请求登录实习系统")
    commands.add_parser("status", help="自动登录并查询今日签到状态")
    check_in = commands.add_parser("check-in", help="使用 Windows 现场定位，通过 HTTPS 接口自动签到")
    check_in.add_argument("--at", type=_time_of_day, metavar="HH:MM", help="在指定的北京时间每天签到")
    check_in.add_argument("--weekdays-only", action="store_true", help="定时签到仅周一至周五运行")
    web = commands.add_parser("web", help="启动多账号签到管理台")
    web.add_argument("--host", default="127.0.0.1", help="监听地址，默认仅本机可访问")
    web.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765")
    web.add_argument("--cert", help="HTTPS 证书文件")
    web.add_argument("--key", help="HTTPS 私钥文件")
    web.add_argument("--data-dir", type=Path, help="管理台的数据目录，默认使用当前用户的应用数据目录")
    args = parser.parse_args()
    if args.command == "check-in" and args.weekdays_only and args.at is None:
        check_in.error("--weekdays-only 需要与 --at 一起使用。")

    try:
        if args.command == "web":
            serve_web(args.host, args.port, args.cert, args.key, args.data_dir)
            return 0
        username, password = _read_credentials()
        if args.command == "check-in":
            if args.at is None:
                _check_in(username, password)
            else:
                _check_in_daily(username, password, args.at, args.weekdays_only)
            return 0
        client = PracticeClient()
        client.login(username, password)
        if args.command == "status":
            print("今天已经签到。" if client.signed_today() else "今天尚未签到。")
        else:
            print("登录成功。")
    except (LocationError, PracticeError, WebError) as error:
        print(error, file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("操作已取消。", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
