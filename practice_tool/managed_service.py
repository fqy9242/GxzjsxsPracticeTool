"""由 Windows 启动器管理的后台服务。"""

import argparse
import logging
import os
import re
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .web import WebError, open_web_server


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{32}", args.instance):
        parser.error("无效的服务实例标识。")

    control = args.data_dir / "launcher-runtime" / "service"
    control.mkdir(parents=True, exist_ok=True)
    state = control / "active"
    pending = control / f"{args.instance}.tmp"
    stop_file = control / f"{args.instance}.stop"
    log = RotatingFileHandler(control / "service.log", maxBytes=256_000, backupCount=1, encoding="utf-8")
    log.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger = logging.getLogger()
    logger.addHandler(log)
    logger.setLevel(logging.INFO)
    stop_wait = threading.Event()
    worker = None
    published = None

    try:
        with open_web_server("127.0.0.1", args.port, data_dir=args.data_dir) as server:
            instance = f"{args.instance} {os.getpid()} {int(time.time())}\n"
            pending.write_text(instance, encoding="ascii")
            pending.replace(state)
            published = instance
            logger.info("签到服务已启动。")

            def wait_for_stop():
                while not stop_wait.wait(0.25):
                    if stop_file.is_file():
                        logger.info("正在停止服务，等待当前任务完成。")
                        server.accounts.stop_scheduling()
                        server.shutdown()
                        return

            worker = threading.Thread(target=wait_for_stop, name="launcher-control", daemon=True)
            worker.start()
            server.serve_forever(poll_interval=0.25)
        logger.info("签到服务已停止。")
        return 0
    except (OSError, WebError) as error:
        logger.error("%s", error)
        return 1
    finally:
        stop_wait.set()
        if worker is not None:
            worker.join(timeout=1)
        if published and state.exists() and state.read_text(encoding="ascii") == published:
            state.unlink(missing_ok=True)
        pending.unlink(missing_ok=True)
        stop_file.unlink(missing_ok=True)
        logger.removeHandler(log)
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
