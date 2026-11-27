"""管理签到账号、平台会话、每日计划和执行记录。"""

import json
import logging
import math
import os
import re
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from pathlib import Path
from typing import Optional

from .client import PracticeClient, PracticeError, SessionExpired
from .location import LocationError, device_position, distance_metres
from .secrets import SecretError, seal, unseal


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
    check_location TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    day TEXT NOT NULL,
    state TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT
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
    local = os.environ.get("LOCALAPPDATA")
    return (Path(local) if local else Path.home() / ".local" / "share") / "PracticeTool"


def _timestamp(now: datetime) -> str:
    return now.isoformat(timespec="seconds")


def _schedule_due(account: sqlite3.Row, now: datetime) -> bool:
    return bool(
        account["enabled"] and now.strftime("%H:%M") >= account["check_time"]
        and (not account["weekdays_only"] or now.weekday() < 5)
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
        with self._db() as db:
            db.executescript(_SCHEMA)
            db.execute("BEGIN IMMEDIATE")
            columns = {column["name"] for column in db.execute("PRAGMA table_info(accounts)")}
            if "check_location" not in columns:
                db.execute("ALTER TABLE accounts ADD COLUMN check_location TEXT")
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
                          a.session IS NOT NULL AS has_session, r.state, r.message, r.started_at, r.finished_at,
                          EXISTS(SELECT 1 FROM runs AS t WHERE t.account_id = a.id AND t.day = ?
                                 AND t.state IN ('signed', 'already_signed')) AS signed_today
                   FROM accounts AS a
                   LEFT JOIN runs AS r ON r.id = (SELECT id FROM runs WHERE account_id = a.id ORDER BY id DESC LIMIT 1)
                   ORDER BY a.id""", (day,),
            ).fetchall()
            runs = db.execute(
                """SELECT r.id, r.account_id, a.name, r.source, r.state, r.message, r.started_at, r.finished_at
                   FROM runs AS r JOIN accounts AS a ON a.id = r.account_id
                   ORDER BY r.id DESC LIMIT 100"""
            ).fetchall()
        account_details = [dict(row) for row in accounts]
        for account in account_details:
            if account["check_location"] is not None:
                account["check_location"] = json.loads(account["check_location"])
        return {"accounts": account_details, "runs": [dict(row) for row in runs],
                "today": day, "scheduler": self._scheduler.is_alive()}

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
        enabled, weekdays_only = data.get("enabled"), data.get("weekdays_only")
        if (
            (account_id is not None and (type(account_id) is not int or not 0 < account_id < 2**63))
            or not isinstance(name, str) or not 0 < len(name.strip()) <= 50
            or not isinstance(username, str) or not 0 < len(username.strip()) <= 100
            or not isinstance(password, str) or len(password) > 500
            or (account_id is None and not password)
        ):
            raise DashboardError("请检查账号名称、实习账号和密码。")
        if not isinstance(check_time, str) or not _TIME.fullmatch(check_time) or type(enabled) is not bool or type(weekdays_only) is not bool:
            raise DashboardError("请填写有效的签到时间和执行频率。")

        location = _encode_location(data.get("check_location"))
        username = username.strip()
        name = name.strip()
        secret = seal(password.encode()) if password else None
        try:
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                if account_id is None:
                    return db.execute(
                        """INSERT INTO accounts (name, username, password, enabled, check_time, weekdays_only, check_location)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""", (name, username, secret, enabled, check_time, weekdays_only, location),
                    ).lastrowid
                self._expire_runs(db, datetime.now(BEIJING))
                account = self._account(db, account_id)
                self._require_idle(db, account_id)
                if username != account["username"]:
                    raise DashboardError("已有账号的登录名固定。请添加另一个实习账号。")
                if "check_location" not in data:
                    location = account["check_location"]
                db.execute(
                    """UPDATE accounts SET name = ?, username = ?, password = ?, session = ?,
                           enabled = ?, check_time = ?, weekdays_only = ?, check_location = ? WHERE id = ?""",
                    (name, username, secret or account["password"], None if secret else account["session"],
                     enabled, check_time, weekdays_only, location, account_id),
                )
        except sqlite3.IntegrityError:
            raise DashboardError("这个实习账号已经添加。", HTTPStatus.CONFLICT) from None
        return account_id

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
            if source == "automatic" and (
                not _schedule_due(account, now) or db.execute(
                    "SELECT 1 FROM runs WHERE account_id = ? AND day = ? AND source = 'automatic'",
                    (account_id, day),
                ).fetchone()
            ):
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
            due = db.execute(
                """SELECT a.* FROM accounts a WHERE a.enabled = 1 AND a.check_time <= ? AND NOT EXISTS
                   (SELECT 1 FROM runs r WHERE r.account_id = a.id AND r.day = ? AND r.source = 'automatic')
                   ORDER BY a.check_time, a.id""", (now.strftime("%H:%M"), day),
            ).fetchall()
        for account in due:
            if self._stop.is_set():
                return
            if _schedule_due(account, now):
                try:
                    self.launch(account["id"], "automatic")
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
            password = unseal(account["password"]).decode()
            client.login(account["username"], password)
            return client.signed_today()

        def locate() -> tuple[float, float]:
            current = device_position()
            if account["check_location"] is not None:
                site = json.loads(account["check_location"])
                distance = distance_metres(current, (site["latitude"], site["longitude"]))
                if distance > site["radius"]:
                    raise LocationError("设备当前位置超出此账号设置的实习地点范围。")
            return current

        try:
            if account["session"]:
                try:
                    state = json.loads(unseal(account["session"]))
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
                session = seal(json.dumps(client.export_session(), ensure_ascii=False).encode())
        except SecretError:
            message += " 登录会话未能保存。"
        try:
            self._finish(run_id, account["id"], state, message, session)
        except sqlite3.Error:
            logger.exception("%s · 无法保存执行记录。", heading)
        else:
            level = logging.WARNING if state == "failed" else logging.INFO
            logger.log(level, "%s · %s", heading, message)

    def _finish(self, run_id: int, account_id: int, state: str, message: str, session: Optional[bytes]) -> None:
        finished = _timestamp(datetime.now(BEIJING))
        with self._db() as db:
            updated = db.execute(
                "UPDATE runs SET state = ?, message = ?, finished_at = ? WHERE id = ? AND state = 'running'",
                (state, message, finished, run_id),
            )
            if updated.rowcount:
                db.execute("UPDATE accounts SET session = ? WHERE id = ?", (session, account_id))
