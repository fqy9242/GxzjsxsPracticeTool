"""启动器使用的本机服务进程。标准输入关闭时停止调度并等待现有任务。"""

import argparse
import logging
import os
import sys
import threading
from pathlib import Path

from .web import WebError, open_web_server


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        with open_web_server("127.0.0.1", args.port, data_dir=args.data_dir) as server:
            def stop_on_input():
                os.read(sys.stdin.fileno(), 1)
                server.accounts.stop_scheduling()
                server.shutdown()

            threading.Thread(target=stop_on_input, name="launcher-control", daemon=True).start()
            print(f"PRACTICETOOL_READY {server.server_port}")
            server.serve_forever(poll_interval=0.25)
    except WebError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
