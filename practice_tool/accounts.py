"""管理签到账号、平台会话、每日计划和执行记录。"""

import json
import logging
import math
import os
import re
import secrets
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from pathlib import Path
from typing import Optional

from .client import PracticeClient, PracticeError, SessionExpired
from .location import LocationError, device_position
from .mail import mailbox, send_reminder
from .scheduling import CalendarError, HolidayCalendar, SCHEDULE_MODES, TIME_MODES, minute_of_day, minute_text
from .secrets import SecretError, SecretStore


BEIJING = timezone(timedelta(hours=8))
logger = logging.getLogger(__name__)
_TIME = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d", re.ASCII)
_WORKERS = 3
_SOURCE_NAMES = {"manual": "手动签到", "automatic": "自动签到", "status": "查询状态"}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    username TEXT NOT NULL UNIQUE,
    password BLOB NOT NULL,
    session BLOB,
    enabled INTEGER NOT NULL,
    check_time TEXT NOT NULL,
    weekdays_only INTEGER NOT NULL,
    schedule_mode TEXT NOT NULL DEFAULT 'weekdays',
    time_mode TEXT NOT NULL DEFAULT 'fixed',
    check_time_end TEXT NOT NULL DEFAULT '',
    check_location TEXT,
    reminder_enabled INTEGER NOT NULL DEFAULT 0,
    reminder_email TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    day TEXT NOT NULL,
    state TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    mail_state TEXT
);
CREATE TABLE IF NOT EXISTS mail_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    host TEXT NOT NULL,
    port INTEGER NOT NULL,
    security TEXT NOT NULL,
    sender TEXT NOT NULL,
    password BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS schedule_targets (
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    day TEXT NOT NULL,
    signature TEXT NOT NULL,
    target_time TEXT NOT NULL,
    PRIMARY KEY (account_id, day)
);
CREATE UNIQUE INDEX IF NOT EXISTS daily_automatic ON runs(account_id, day) WHERE source = 'automatic';
CREATE UNIQUE INDEX IF NOT EXISTS active_account ON runs(account_id) WHERE state = 'running';
CREATE INDEX IF NOT EXISTS account_history ON runs(account_id, id DESC);
"""


class DashboardError(RuntimeError):
    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


def default_data_dir() -> Path:
    configured = os.environ.get("PRACTICE_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    local = os.environ.get("LOCALAPPDATA")
    return (Path(local) if local else Path.home() / ".local" / "share") / "PracticeTool"


def _timestamp(now: datetime) -> str:
    return now.isoformat(timespec="seconds")


def _schedule_due(account: sqlite3.Row, now: datetime, calendar: Optional[HolidayCalendar] = None,
                  target: Optional[str] = None) -> bool:
    mode = account["schedule_mode"]
    random_time = account["time_mode"] == "random"
    if random_time and (target is None or now.strftime("%H:%M") > account["check_time_end"]):
        return False
    return bool(
        account["enabled"] and now.strftime("%H:%M") >= (target if random_time else account["check_time"])
        and (calendar or HolidayCalendar()).matches(mode, now.date())
    )


def _encode_location(location: object) -> Optional[str]:
    if location is None:
        return None
    if not isinstance(location, dict):
        raise DashboardError("请填写有效的实习地点。")
    latitude, longitude, radius = (location.get(key) for key in ("latitude", "longitude", "radius"))
    if (
        type(latitude) not in (int, float) or not -90 <= latitude <= 90
        or type(longitude) not in (int, float) or not -180 <= longitude <= 180
        or type(radius) not in (int, float) or not 0 < radius < math.inf
    ):
        raise DashboardError("请输入有效的 WGS-84 经纬度和大于 0 米的范围。")
    return json.dumps({"latitude": latitude, "longitude": longitude, "radius": radius})


class Accounts:
    def __init__(self, directory: Path):
        self._path = directory / "practice.sqlite3"
        directory.mkdir(parents=True, exist_ok=True)
        self._secrets = SecretStore(directory)
        self._calendar = HolidayCalendar(directory / "calendar.json")
        self._calendar_warnings = set()
        with self._db() as db:
            db.executescript(_SCHEMA)
            db.execute("BEGIN IMMEDIATE")
            columns = {column["name"] for column in db.execute("PRAGMA table_info(accounts)")}
            if "schedule_mode" not in columns:
                db.execute("ALTER TABLE accounts ADD COLUMN schedule_mode TEXT NOT NULL DEFAULT 'weekdays'")
                db.execute("UPDATE accounts SET schedule_mode = CASE WHEN weekdays_only THEN 'weekdays' ELSE 'daily' END")
            if "time_mode" not in columns:
                db.execute("ALTER TABLE accounts ADD COLUMN time_mode TEXT NOT NULL DEFAULT 'fixed'")
            if "check_time_end" not in columns:
                db.execute("ALTER TABLE accounts ADD COLUMN check_time_end TEXT NOT NULL DEFAULT ''")
            if "check_location" not in columns:
                db.execute("ALTER TABLE accounts ADD COLUMN check_location TEXT")
            if "reminder_enabled" not in columns:
                db.execute("ALTER TABLE accounts ADD COLUMN reminder_enabled INTEGER NOT NULL DEFAULT 0")
            if "reminder_email" not in columns:
                db.execute("ALTER TABLE accounts ADD COLUMN reminder_email TEXT NOT NULL DEFAULT ''")
            if not any(column["name"] == "mail_state" for column in db.execute("PRAGMA table_info(runs)")):
                db.execute("ALTER TABLE runs ADD COLUMN mail_state TEXT")
        self._stop = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="practice-check-in")
        self._scheduler = threading.Thread(target=self._schedule, name="practice-scheduler", daemon=True)

    @contextmanager
    def _db(self):
        with closing(sqlite3.connect(self._path, timeout=5)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            yield db

    def start(self) -> None:
        self._scheduler.start()

    def stop_scheduling(self) -> None:
        self._stop.set()

    def close(self) -> None:
        self.stop_scheduling()
        if self._scheduler.is_alive():
            self._scheduler.join()
        self._executor.shutdown()

    @staticmethod
    def _expire_runs(db: sqlite3.Connection, now: datetime) -> None:
        db.execute(
            """UPDATE runs SET state = 'interrupted', message = ?, finished_at = ?
               WHERE state = 'running' AND started_at < ?""",
            ("执行中断，请查询平台状态。", _timestamp(now), _timestamp(now - timedelta(minutes=5))),
        )

    def overview(self) -> dict:
        now = datetime.now(BEIJING)
        day = now.date().isoformat()
        with self._db() as db:
            self._expire_runs(db, now)
            accounts = db.execute(
                """SELECT a.id, a.name, a.username, a.enabled, a.check_time, a.weekdays_only, a.check_location,
                          a.schedule_mode, a.time_mode, a.check_time_end,
                          a.reminder_enabled, a.reminder_email,
                          a.session IS NOT NULL AS has_session, r.state, r.message, r.started_at, r.finished_at,
                          EXISTS(SELECT 1 FROM runs AS t WHERE t.account_id = a.id AND t.day = ?
                                 AND t.state IN ('signed', 'already_signed')) AS signed_today
                   FROM accounts AS a
                   LEFT JOIN runs AS r ON r.id = (SELECT id FROM runs WHERE account_id = a.id ORDER BY id DESC LIMIT 1)
                   ORDER BY a.id""", (day,),
            ).fetchall()
            runs = db.execute(
                """SELECT r.id, r.account_id, a.name, r.source, r.state, r.message, r.started_at, r.finished_at, r.mail_state
                   FROM runs AS r JOIN accounts AS a ON a.id = r.account_id
                   ORDER BY r.id DESC LIMIT 100"""
            ).fetchall()
        account_details = [dict(row) for row in accounts]
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            for account in account_details:
                if account["check_location"] is not None:
                    account["check_location"] = json.loads(account["check_location"])
                account["today_check_time"] = None
                account["schedule_error"] = ""
                if account["enabled"]:
                    try:
                        if self._calendar.matches(account["schedule_mode"], now.date()):
                            account["today_check_time"] = self._target_time(db, account, now)
                    except CalendarError as error:
                        account["schedule_error"] = str(error)
        return {"accounts": account_details, "runs": [dict(row) for row in runs],
                "today": day, "scheduler": self._scheduler.is_alive(),
                "schedule_modes": SCHEDULE_MODES, "time_modes": TIME_MODES,
                "calendar_years": self._calendar.years, "device_location_supported": sys.platform == "win32"}

    @staticmethod
    def _target_time(db: sqlite3.Connection, account, now: datetime) -> Optional[str]:
        if account["time_mode"] == "fixed":
            return account["check_time"]
        day = now.date().isoformat()
        signature = f"random:{account['check_time']}:{account['check_time_end']}"
        planned = db.execute("SELECT signature, target_time FROM schedule_targets WHERE account_id = ? AND day = ?",
                             (account["id"], day)).fetchone()
        if planned and planned["signature"] == signature:
            return planned["target_time"]
        if now.strftime("%H:%M") > account["check_time_end"]:
            return None
        first = max(minute_of_day(account["check_time"]), now.hour * 60 + now.minute)
        last = minute_of_day(account["check_time_end"])
        previous = db.execute("SELECT target_time FROM schedule_targets WHERE account_id = ? AND day < ? ORDER BY day DESC LIMIT 1",
                              (account["id"], day)).fetchone()
        excluded = minute_of_day(previous["target_time"]) if previous else -1
        size = last - first + 1
        avoid_previous = size > 1 and first <= excluded <= last
        chosen = first + secrets.randbelow(size - int(avoid_previous))
        if avoid_previous and chosen >= excluded:
            chosen += 1
        target = minute_text(chosen)
        db.execute("""INSERT INTO schedule_targets (account_id, day, signature, target_time) VALUES (?, ?, ?, ?)
                      ON CONFLICT(account_id, day) DO UPDATE SET signature = excluded.signature, target_time = excluded.target_time""",
                   (account["id"], day, signature, target))
        return target

    @staticmethod
    def _account(db: sqlite3.Connection, account_id: int) -> sqlite3.Row:
        row = db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row is None:
            raise DashboardError("未找到此账号。", HTTPStatus.NOT_FOUND)
        return row

    @staticmethod
    def _require_idle(db: sqlite3.Connection, account_id: int) -> None:
        if db.execute("SELECT 1 FROM runs WHERE account_id = ? AND state = 'running'", (account_id,)).fetchone():
            raise DashboardError("此账号正在执行，请等待本次结果。", HTTPStatus.CONFLICT)

    def save(self, data: dict) -> int:
        account_id = data.get("id")
        name, username, password = (data.get(key, "") for key in ("name", "username", "password"))
        check_time = data.get("check_time")
        enabled = data.get("enabled")
        if (
            (account_id is not None and (type(account_id) is not int or not 0 < account_id < 2**63))
            or not isinstance(name, str) or not 0 < len(name.strip()) <= 50
            or not isinstance(username, str) or not 0 < len(username.strip()) <= 100
            or not isinstance(password, str) or len(password) > 500
            or (account_id is None and not password)
        ):
            raise DashboardError("请检查账号名称、实习账号和密码。")
        if not isinstance(check_time, str) or not _TIME.fullmatch(check_time) or type(enabled) is not bool:
            raise DashboardError("请填写有效的签到时间和执行频率。")

        location = _encode_location(data.get("check_location"))
        username = username.strip()
        name = name.strip()
        secret = self._secrets.seal(password.encode()) if password else None
        try:
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                account = self._account(db, account_id) if account_id is not None else None
                schedule_mode = data.get("schedule_mode")
                if "schedule_mode" not in data:
                    if "weekdays_only" in data:
                        if type(data["weekdays_only"]) is not bool:
                            raise DashboardError("请选择有效的签到频率。")
                        schedule_mode = "weekdays" if data["weekdays_only"] else "daily"
                    else:
                        schedule_mode = account["schedule_mode"] if account else "weekdays"
                time_mode = data.get("time_mode", account["time_mode"] if account else "fixed")
                check_time_end = data.get("check_time_end", account["check_time_end"] if account else "")
                if not isinstance(schedule_mode, str) or schedule_mode not in SCHEDULE_MODES:
                    raise DashboardError("请选择有效的签到频率。")
                if not isinstance(time_mode, str) or time_mode not in TIME_MODES:
                    raise DashboardError("请选择固定时间或随机时间。")
                if not isinstance(check_time_end, str) or (check_time_end and not _TIME.fullmatch(check_time_end)):
                    raise DashboardError("请输入有效的结束时间。")
                if time_mode == "random" and (not _TIME.fullmatch(check_time_end) or check_time >= check_time_end):
                    raise DashboardError("随机时间段的结束时间须晚于开始时间，且应位于同一天。")
                weekdays_only = schedule_mode == "weekdays"
                if account is not None and "check_location" not in data:
                    location = account["check_location"]
                if sys.platform != "win32" and enabled and location is None:
                    raise DashboardError("Linux 自动签到需要配置自定义位置。")
                if account_id is None:
                    reminder_enabled, reminder_email = self._reminder(data)
                    return db.execute(
                        """INSERT INTO accounts (name, username, password, enabled, check_time, weekdays_only,
                           check_location, reminder_enabled, reminder_email, schedule_mode, time_mode, check_time_end)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (name, username, secret, enabled, check_time, weekdays_only, location, reminder_enabled, reminder_email,
                         schedule_mode, time_mode, check_time_end),
                    ).lastrowid
                self._expire_runs(db, datetime.now(BEIJING))
                self._require_idle(db, account_id)
                if username != account["username"]:
                    raise DashboardError("已有账号的登录名固定。请添加另一个实习账号。")
                reminder_enabled, reminder_email = self._reminder(data, account)
                db.execute(
                    """UPDATE accounts SET name = ?, username = ?, password = ?, session = ?,
                           enabled = ?, check_time = ?, weekdays_only = ?, check_location = ?,
                           reminder_enabled = ?, reminder_email = ?, schedule_mode = ?, time_mode = ?, check_time_end = ? WHERE id = ?""",
                    (name, username, secret or account["password"], None if secret else account["session"],
                     enabled, check_time, weekdays_only, location, reminder_enabled, reminder_email,
                     schedule_mode, time_mode, check_time_end, account_id),
                )
        except sqlite3.IntegrityError:
            raise DashboardError("这个实习账号已经添加。", HTTPStatus.CONFLICT) from None
        return account_id

    @staticmethod
    def _reminder(data: dict, account: Optional[sqlite3.Row] = None) -> tuple[bool, str]:
        enabled = data.get("reminder_enabled", bool(account["reminder_enabled"]) if account else False)
        email = data.get("reminder_email", account["reminder_email"] if account else "")
        if type(enabled) is not bool:
            raise DashboardError("请选择是否启用邮件提醒。")
        if not isinstance(email, str):
            raise DashboardError("请填写有效的收件邮箱。")
        try:
            return enabled, mailbox(email) if enabled or email.strip() else ""
        except ValueError:
            raise DashboardError("请填写有效的收件邮箱。") from None

    def mail_settings(self) -> dict:
        with self._db() as db:
            config = db.execute("SELECT host, port, security, sender FROM mail_settings WHERE id = 1").fetchone()
        if config is None:
            return {"configured": False, "host": "", "port": 465, "security": "ssl", "sender": ""}
        return {"configured": True, **dict(config)}

    def save_mail_settings(self, data: dict) -> None:
        host, port = data.get("host"), data.get("port")
        security, password = data.get("security"), data.get("password", "")
        if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", host.strip()):
            raise DashboardError("请填写有效的 SMTP 服务器地址。")
        if type(port) is not int or not 1 <= port <= 65535 or security not in ("ssl", "starttls"):
            raise DashboardError("请检查 SMTP 端口和加密方式。")
        if not isinstance(password, str) or len(password) > 500:
            raise DashboardError("请检查 SMTP 授权码或密码。")
        try:
            sender = mailbox(data.get("sender"))
        except ValueError:
            raise DashboardError("请填写有效的发件邮箱。") from None
        secret = self._secrets.seal(password.encode()) if password else None
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT host, sender, password FROM mail_settings WHERE id = 1").fetchone()
            if secret is None:
                if previous is None or (host.strip(), sender) != (previous["host"], previous["sender"]):
                    raise DashboardError("请填写发件邮箱的 SMTP 授权码或密码。")
                secret = previous["password"]
            db.execute(
                """INSERT INTO mail_settings (id, host, port, security, sender, password) VALUES (1, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET host = excluded.host, port = excluded.port,
                   security = excluded.security, sender = excluded.sender, password = excluded.password""",
                (host.strip(), port, security, sender, secret),
            )

    def delete(self, account_id: int) -> None:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire_runs(db, datetime.now(BEIJING))
            self._account(db, account_id)
            self._require_idle(db, account_id)
            db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))

    def launch(self, account_id: int, source: str) -> Optional[int]:
        now = datetime.now(BEIJING)
        day = now.date().isoformat()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire_runs(db, now)
            account = self._account(db, account_id)
            if source == "automatic":
                try:
                    eligible = bool(account["enabled"]) and self._calendar.matches(account["schedule_mode"], now.date())
                    target = self._target_time(db, account, now) if eligible else None
                    due = eligible and _schedule_due(account, now, self._calendar, target)
                except CalendarError as error:
                    raise DashboardError(str(error)) from None
                if not due or db.execute(
                    "SELECT 1 FROM runs WHERE account_id = ? AND day = ? AND source = 'automatic'",
                    (account_id, day),
                ).fetchone():
                    return None
            self._require_idle(db, account_id)
            if db.execute("SELECT count(*) FROM runs WHERE state = 'running'").fetchone()[0] >= _WORKERS:
                raise DashboardError("执行队列暂时已满，请稍后重试。", HTTPStatus.CONFLICT)
            run_id = db.execute(
                "INSERT INTO runs (account_id, source, day, state, started_at) VALUES (?, ?, ?, 'running', ?)",
                (account_id, source, day, _timestamp(now)),
            ).lastrowid

        try:
            self._executor.submit(self._execute, run_id, account, source)
        except RuntimeError:
            self._finish(run_id, account_id, "interrupted", "服务已停止。", account["session"])
            raise DashboardError("服务已停止。", HTTPStatus.SERVICE_UNAVAILABLE) from None
        return run_id

    def _schedule(self) -> None:
        while not self._stop.is_set():
            try:
                self._schedule_due()
            except sqlite3.Error:
                logger.exception("无法读取签到计划。")
            self._stop.wait(10)

    def _schedule_due(self) -> None:
        now = datetime.now(BEIJING)
        day = now.date().isoformat()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            due = db.execute(
                """SELECT a.* FROM accounts a WHERE a.enabled = 1 AND NOT EXISTS
                   (SELECT 1 FROM runs r WHERE r.account_id = a.id AND r.day = ? AND r.source = 'automatic')
                   ORDER BY a.check_time, a.id""", (day,),
            ).fetchall()
            ready = []
            for account in due:
                try:
                    if self._calendar.matches(account["schedule_mode"], now.date()):
                        target = self._target_time(db, account, now)
                        if _schedule_due(account, now, self._calendar, target):
                            ready.append(account["id"])
                except CalendarError as error:
                    marker = (account["id"], day)
                    if marker not in self._calendar_warnings:
                        logger.warning("账号 %s · %s", account["id"], error)
                        self._calendar_warnings.add(marker)
        for account_id in ready:
            if self._stop.is_set():
                return
            try:
                self.launch(account_id, "automatic")
            except DashboardError:
                continue

    def _execute(self, run_id: int, account: sqlite3.Row, source: str) -> None:
        heading = f"{' '.join(account['name'].split())} · {_SOURCE_NAMES[source]}"
        logger.info("%s · 开始执行", heading)
        client = PracticeClient()
        password = ""
        session = None

        def renew() -> bool:
            nonlocal password
            password = self._secrets.unseal(account["password"]).decode()
            client.login(account["username"], password)
            return client.signed_today()

        def locate() -> tuple[float, float]:
            if source == "automatic" and account["time_mode"] == "random":
                current_time = datetime.now(BEIJING)
                with self._db() as db:
                    run_day = db.execute("SELECT day FROM runs WHERE id = ?", (run_id,)).fetchone()["day"]
                if current_time.date().isoformat() != run_day or current_time.strftime("%H:%M") > account["check_time_end"]:
                    raise LocationError("已超出今日随机签到时间段，本次不再提交。")
            if account["check_location"] is not None:
                site = json.loads(account["check_location"])
                return site["latitude"], site["longitude"]
            return device_position()

        try:
            if account["session"]:
                try:
                    state = json.loads(self._secrets.unseal(account["session"]))
                    if isinstance(state, dict):
                        client.restore_session(state)
                except (SecretError, ValueError):
                    pass
            try:
                signed = client.signed_today()
            except SessionExpired:
                signed = renew()

            if signed:
                state, message = "already_signed", "平台显示今天已经签到。"
            elif source == "status":
                state, message = "unsigned", "平台显示今天尚未签到。"
            else:
                try:
                    client.check_in(locate, known_status=signed)
                    state, message = "signed", "平台已确认签到成功。"
                except SessionExpired:
                    if password:
                        raise
                    if renew():
                        state, message = "already_signed", "平台显示今天已经签到。"
                    else:
                        client.check_in(locate, known_status=False)
                        state, message = "signed", "平台已确认签到成功。"
        except (PracticeError, LocationError, SecretError, ValueError) as error:
            state, message = "failed", str(error)
            if password:
                message = message.replace(password, "••••")
        except Exception:
            state, message = "failed", "执行时发生内部错误。"
            logger.exception("账号签到执行异常。")

        try:
            if client.has_session:
                session = self._secrets.seal(json.dumps(client.export_session(), ensure_ascii=False).encode())
        except SecretError:
            message += " 登录会话未能保存。"
        try:
            finished = self._finish(run_id, account["id"], state, message, session)
        except sqlite3.Error:
            logger.exception("%s · 无法保存执行记录。", heading)
        else:
            level = logging.WARNING if state == "failed" else logging.INFO
            logger.log(level, "%s · %s", heading, message)
            if finished and source != "status" and account["reminder_enabled"]:
                self._send_reminder(run_id, account, source, state, finished)

    def _finish(self, run_id: int, account_id: int, state: str, message: str, session: Optional[bytes]) -> Optional[str]:
        finished = _timestamp(datetime.now(BEIJING))
        with self._db() as db:
            updated = db.execute(
                "UPDATE runs SET state = ?, message = ?, finished_at = ? WHERE id = ? AND state = 'running'",
                (state, message, finished, run_id),
            )
            if updated.rowcount:
                db.execute("UPDATE accounts SET session = ? WHERE id = ?", (session, account_id))
                return finished
        return None

    def _send_reminder(self, run_id: int, account: sqlite3.Row, source: str, state: str, finished: str) -> None:
        try:
            with self._db() as db:
                config = db.execute("SELECT * FROM mail_settings WHERE id = 1").fetchone()
            mail_state = "not_configured"
            if config is not None:
                try:
                    send_reminder(config, self._secrets.unseal(config["password"]).decode(), account["reminder_email"],
                                  account["name"], _SOURCE_NAMES[source], state, finished)
                    mail_state = "sent"
                except (OSError, ValueError, SecretError) as error:
                    mail_state = "failed"
                    logger.warning("执行记录 %s · 邮件发送失败（%s）。", run_id, type(error).__name__)
            with self._db() as db:
                db.execute("UPDATE runs SET mail_state = ? WHERE id = ?", (mail_state, run_id))
        except sqlite3.Error:
            logger.exception("执行记录 %s · 无法读取发件配置或保存发送结果。", run_id)
