"""Beijing-date schedule rules and the published mainland China holiday calendar."""

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Optional


SCHEDULE_MODES = {
    "daily": "每天",
    "weekdays": "周一至周五",
    "weekends": "仅周末",
    "workdays": "仅工作日（含调休）",
    "holidays": "仅节假日（不含普通周末）",
    "days_off": "节假日和周末（排除调休上班）",
}
TIME_MODES = {"fixed": "固定时间", "random": "时间段内随机"}
CALENDAR_MODES = {"workdays", "holidays", "days_off"}

# State Council notices, including all days in each announced holiday period:
# https://www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm
# https://www.gov.cn/zhengce/content/202611/content_7085101.htm
_PUBLISHED = {
    2026: {
        "holidays": [("01-01", "01-03"), ("02-15", "02-23"), ("04-04", "04-06"),
                     ("05-01", "05-05"), ("06-19", "06-21"), ("09-25", "09-27"), ("10-01", "10-07")],
        "workdays": ["01-04", "02-14", "02-28", "05-09", "09-20", "10-10"],
    },
    2027: {
        "holidays": [("01-01", "01-03"), ("02-05", "02-13"), ("04-03", "04-05"),
                     ("05-01", "05-05"), ("06-09", "06-09"), ("09-15", "09-15"), ("10-01", "10-07")],
        "workdays": ["01-31", "02-14", "05-08", "09-26", "10-09"],
    },
}


class CalendarError(ValueError):
    """The requested date has no complete, valid holiday calendar."""


class HolidayCalendar:
    def __init__(self, override: Optional[Path] = None):
        self._years = {}
        for year, values in _PUBLISHED.items():
            holidays = set()
            for first, last in values["holidays"]:
                current, end = date.fromisoformat(f"{year}-{first}"), date.fromisoformat(f"{year}-{last}")
                while current <= end:
                    holidays.add(current)
                    current += timedelta(days=1)
            workdays = {date.fromisoformat(f"{year}-{value}") for value in values["workdays"]}
            self._years[year] = holidays, workdays
        if override is not None and override.exists():
            try:
                values = json.loads(override.read_text(encoding="utf-8"))
                if not isinstance(values, dict):
                    raise ValueError
                for year_text, days in values.items():
                    year = int(year_text)
                    if len(year_text) != 4 or str(year) != year_text or not 1 <= year <= 9999 or not isinstance(days, dict):
                        raise ValueError
                    sets = []
                    for field in ("holidays", "workdays"):
                        entries = days[field]
                        if not isinstance(entries, list) or any(not isinstance(item, str) for item in entries):
                            raise ValueError
                        parsed = {date.fromisoformat(item) for item in entries}
                        if any(item.year != year for item in parsed) or any(date.fromisoformat(item).isoformat() != item for item in entries):
                            raise ValueError
                        sets.append(parsed)
                    if sets[0] & sets[1]:
                        raise ValueError
                    self._years[year] = tuple(sets)
            except (OSError, ValueError, TypeError, KeyError):
                raise CalendarError("calendar.json 格式无效，请检查年份、holidays 和 workdays 日期列表。") from None

    @property
    def years(self) -> list[int]:
        return sorted(self._years)

    def matches(self, mode: str, day: date) -> bool:
        if mode == "daily":
            return True
        if mode == "weekdays":
            return day.weekday() < 5
        if mode == "weekends":
            return day.weekday() >= 5
        if mode not in CALENDAR_MODES:
            raise CalendarError("未知的自动签到频率。")
        if day.year not in self._years:
            raise CalendarError(f"缺少 {day.year} 年节假日日历，请更新程序或在数据目录提供 calendar.json。")
        holidays, workdays = self._years[day.year]
        if mode == "holidays":
            return day in holidays
        working = day in workdays or (day.weekday() < 5 and day not in holidays)
        return working if mode == "workdays" else not working


def minute_of_day(value: str) -> int:
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def minute_text(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"
