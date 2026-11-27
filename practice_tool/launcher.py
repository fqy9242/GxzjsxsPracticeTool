"""Windows 图形启动器。"""

import ctypes
import logging
import queue
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import scrolledtext, ttk

from practice_tool.accounts import BEIJING
from practice_tool.web import WebError, open_web_server


HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}/"
BG = "#f5f7fb"
PANEL = "#ffffff"
TEXT = "#17243a"
MUTED = "#596980"
BLUE = "#2457d5"


class _LogHandler(logging.Handler):
    def __init__(self, events):
        super().__init__(logging.INFO)
        self.events = events

    def emit(self, record) -> None:
        self.events.put(("log", (record.created, record.getMessage(), record.levelno >= logging.WARNING)))


class Launcher:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.server = None
        self.events = queue.Queue()
        self.phase = "stopped"
        self.closing = False

        root.title("PracticeTool · 签到服务")
        root.configure(background=BG)
        root.minsize(530, 420)
        root.protocol("WM_DELETE_WINDOW", self._close)
        self._style()

        container = tk.Frame(root, background=BG, padx=25, pady=24)
        container.pack(fill="both", expand=True)
        header = tk.Frame(container, background=BG)
        header.pack(fill="x", pady=(0, 20))
        mark = tk.Label(header, text="P", width=3, background=BLUE, foreground="white", font=("Segoe UI", 19, "bold"))
        mark.pack(side="left", ipady=4, padx=(0, 12))
        brand = tk.Frame(header, background=BG)
        brand.pack(side="left")
        self._label(brand, "PracticeTool", ("Segoe UI", 17, "bold"), BG).pack(anchor="w")
        self._label(brand, "实习签到服务", ("Segoe UI", 10), BG, MUTED).pack(anchor="w")

        card = tk.Frame(container, background=PANEL, padx=18, pady=16,
                        highlightbackground="#dce5f1", highlightthickness=1)
        card.pack(fill="x")
        card_head = tk.Frame(card, background=PANEL)
        card_head.pack(fill="x")
        self._label(card_head, "服务状态", ("Segoe UI", 11, "bold"), PANEL).pack(side="left")
        self.status = self._label(card_head, "", ("Segoe UI", 10, "bold"), PANEL)
        self.status.pack(side="right")
        self.description = self._label(card, "", ("Segoe UI", 10), PANEL, MUTED)
        self.description.configure(wraplength=385, justify="left")
        self.description.pack(anchor="w", pady=(8, 0))
        self._label(card, URL, ("Consolas", 10), PANEL, BLUE).pack(anchor="w", pady=(9, 0))

        actions = ttk.Frame(container, style="Launcher.TFrame")
        actions.pack(fill="x", pady=(17, 0))
        actions.columnconfigure((0, 1), weight=1)
        self.toggle = ttk.Button(actions, style="Primary.TButton", command=self._toggle)
        self.toggle.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.open = ttk.Button(actions, text="打开 Web 首页", style="Secondary.TButton", command=self._open)
        self.open.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self._label(container, "自动签到在服务运行期间执行。", ("Segoe UI", 9), BG, MUTED).pack(anchor="w", pady=(14, 0))

        log_header = ttk.Frame(container, style="Launcher.TFrame")
        log_header.pack(fill="x", pady=(14, 6))
        self._label(log_header, "运行日志 · 北京时间", ("Segoe UI", 11, "bold"), BG).pack(side="left")
        ttk.Button(log_header, text="复制日志", command=self._copy_logs).pack(side="right")
        self.logs = scrolledtext.ScrolledText(
            container, width=45, height=10, wrap="word", font=("Consolas", 10),
            background=PANEL, foreground=TEXT, borderwidth=0, padx=11, pady=9,
            highlightthickness=1, highlightbackground="#dce5f1", state="disabled",
        )
        self.logs.tag_configure("time", foreground=MUTED)
        self.logs.tag_configure("warning", foreground="#a33d37")
        self.logs.pack(fill="both", expand=True)
        self.logger = logging.getLogger("practice_tool.accounts")
        self.previous_log_level = self.logger.level
        self.logger.setLevel(logging.INFO)
        self.log_handler = _LogHandler(self.events)
        self.logger.addHandler(self.log_handler)
        self._write_log("启动器已就绪。")

        self._set_phase("stopped")
        root.update_idletasks()
        width = max(root.winfo_reqwidth(), 580)
        height = max(root.winfo_reqheight(), 525)
        x = max(0, (root.winfo_screenwidth() - width) // 2)
        y = max(0, (root.winfo_screenheight() - height) // 2)
        root.geometry(f"{width}x{height}+{x}+{y}")
        root.after(100, self._poll)

    @staticmethod
    def _label(parent, text, font, background, foreground=TEXT):
        return tk.Label(parent, text=text, font=font, background=background, foreground=foreground)

    @staticmethod
    def _style() -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Launcher.TFrame", background=BG)
        style.configure("Primary.TButton", background=BLUE, foreground="#ffffff", borderwidth=0,
                        focusthickness=1, padding=(15, 10), font=("Segoe UI", 10, "bold"))
        style.map("Primary.TButton", background=[("disabled", "#a0b2e3"), ("active", "#1947bc")])
        style.configure("Secondary.TButton", background=PANEL, foreground=TEXT, borderwidth=1,
                        bordercolor="#d4deed", padding=(15, 10), font=("Segoe UI", 10, "bold"))
        style.map("Secondary.TButton", background=[("active", "#eaf0fc")], foreground=[("disabled", "#97a4b9")])

    def _set_phase(self, phase: str, error: str = "") -> None:
        self.phase = phase
        phases = {
            "stopped": ("已停止", MUTED, "启动服务后，即可打开 Web 首页管理签到账号。"),
            "starting": ("正在启动", BLUE, "正在启动本机签到服务。"),
            "running": ("运行中", "#167258", "服务可用，自动签到计划正在运行。"),
            "stopping": ("正在停止", BLUE, "正在等待执行中的任务结束。"),
            "failed": ("启动失败", "#a33d37", error),
        }
        label, color, description = phases[phase]
        self.status.configure(text=f"●  {label}", foreground=color)
        self.description.configure(text=description or "服务未能启动。")
        self.toggle.configure(
            text="停止服务" if phase in ("running", "stopping") else "启动服务",
            state="disabled" if phase in ("starting", "stopping") else "normal",
        )
        self.open.configure(state="normal" if phase == "running" else "disabled")

    def _toggle(self) -> None:
        if self.phase == "running":
            self._stop()
        elif self.phase in ("stopped", "failed"):
            self._set_phase("starting")
            self._write_log("正在启动签到服务。")
            threading.Thread(target=self._serve, name="practice-web").start()

    def _serve(self) -> None:
        try:
            with open_web_server(HOST, PORT) as server:
                self.events.put(("running", server))
                server.serve_forever(poll_interval=0.25)
        except WebError as error:
            description = str(error)
            if "10048" in description:
                description = "端口 8765 已占用，请先关闭之前启动的服务。"
            self.events.put(("failed", description))
        except Exception:
            self.events.put(("failed", "启动时发生内部错误。"))
        else:
            self.events.put(("stopped", None))

    def _stop(self) -> None:
        self._set_phase("stopping")
        self._write_log("正在停止服务，等待执行中的任务结束。")
        self.server.accounts.stop_scheduling()
        threading.Thread(target=self.server.shutdown, name="practice-web-stop").start()

    def _poll(self) -> None:
        try:
            while True:
                phase, value = self.events.get_nowait()
                if phase == "log":
                    timestamp, message, warning = value
                    self._write_log(message, warning=warning, timestamp=timestamp)
                elif phase == "running":
                    self.server = value
                    self._write_log(f"服务已启动。{URL}")
                    if self.closing:
                        self._stop()
                    else:
                        self._set_phase(phase)
                else:
                    self.server = None
                    self._write_log(value or "服务已停止。", warning=phase == "failed")
                    if self.closing:
                        self._destroy()
                        return
                    self._set_phase(phase, value or "")
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _open(self) -> None:
        if webbrowser.open_new_tab(URL):
            self._write_log("已打开 Web 首页。")
        else:
            self.description.configure(text=f"请在浏览器地址栏打开 {URL}")
            self._write_log("无法自动打开浏览器。", warning=True)

    def _write_log(self, message: str, *, warning: bool = False, timestamp=None) -> None:
        at = datetime.now(BEIJING) if timestamp is None else datetime.fromtimestamp(timestamp, BEIJING)
        at_bottom = self.logs.yview()[1] >= 0.99
        self.logs.configure(state="normal")
        self.logs.insert("end", f"[{at:%H:%M:%S}]  ", "time")
        self.logs.insert("end", " ".join(message.splitlines()) + "\n", "warning" if warning else "")
        line_count = int(self.logs.index("end-1c").split(".")[0]) - 1
        if line_count > 300:
            self.logs.delete("1.0", f"{line_count - 299}.0")
        self.logs.configure(state="disabled")
        if at_bottom:
            self.logs.see("end")

    def _copy_logs(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.logs.get("1.0", "end-1c"))
        self._write_log("已复制日志。")

    def _destroy(self) -> None:
        self.logger.removeHandler(self.log_handler)
        self.logger.setLevel(self.previous_log_level)
        self.root.destroy()

    def _close(self) -> None:
        if self.phase in ("stopped", "failed"):
            self._destroy()
            return
        self.closing = True
        if self.phase == "running":
            self._stop()


def main() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    Launcher(root)
    root.mainloop()


if __name__ == "__main__":
    main()
