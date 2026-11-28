import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from practice_tool.accounts import Accounts, BEIJING, DashboardError, _schedule_due
from practice_tool.scheduling import CalendarError, HolidayCalendar, minute_of_day
from practice_tool.client import PracticeClient


class CalendarTests(unittest.TestCase):
    def test_holidays_weekends_and_makeup_workdays_are_distinct(self):
        calendar = HolidayCalendar()
        cases = {
            # Ordinary Thursday, ordinary Saturday, public holiday, makeup Sunday.
            date(2026, 9, 24): [True, True, False, True, False, False],
            date(2026, 9, 19): [True, False, True, False, False, True],
            date(2026, 10, 1): [True, True, False, False, True, True],
            date(2026, 9, 20): [True, False, True, True, False, False],
            date(2027, 2, 5): [True, True, False, False, True, True],
            date(2027, 1, 31): [True, False, True, True, False, False],
        }
        modes = ("daily", "weekdays", "weekends", "workdays", "holidays", "days_off")
        for day, expected in cases.items():
            with self.subTest(day=day):
                self.assertEqual([calendar.matches(mode, day) for mode in modes], expected)

    def test_every_announced_holiday_and_makeup_day(self):
        calendar = HolidayCalendar()
        for year, counts in ((2026, (33, 6)), (2027, (29, 5))):
            holidays, makeup = calendar._years[year]
            self.assertEqual((len(holidays), len(makeup)), counts)
            for day in holidays:
                self.assertTrue(calendar.matches("holidays", day))
                self.assertFalse(calendar.matches("workdays", day))
            for day in makeup:
                self.assertTrue(calendar.matches("workdays", day))
                self.assertFalse(calendar.matches("days_off", day))

    def test_unknown_year_never_falls_back_to_weekday_guessing(self):
        calendar = HolidayCalendar()
        for mode in ("workdays", "holidays", "days_off"):
            with self.subTest(mode=mode), self.assertRaisesRegex(CalendarError, "2028"):
                calendar.matches(mode, date(2028, 1, 1))
        self.assertTrue(calendar.matches("daily", date(2028, 1, 1)))

    def test_external_calendar_adds_year_and_validates_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calendar.json"
            path.write_text(json.dumps({"2028": {"holidays": ["2028-01-01"], "workdays": ["2028-01-02"]}}))
            calendar = HolidayCalendar(path)
            self.assertIn(2028, calendar.years)
            self.assertTrue(calendar.matches("holidays", date(2028, 1, 1)))
            self.assertTrue(calendar.matches("workdays", date(2028, 1, 2)))
            for values in ({"holidays": ["2029-01-01"], "workdays": []},
                           {"holidays": ["2028-01-01"], "workdays": ["2028-01-01"]},
                           {"holidays": "2028-01-01", "workdays": []}):
                path.write_text(json.dumps({"2028": values}))
                with self.assertRaises(CalendarError):
                    HolidayCalendar(path)


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.accounts = Accounts(self.directory)
        self.addCleanup(self.accounts.close)
        patcher = patch("practice_tool.accounts.datetime")
        self.clock = patcher.start()
        self.addCleanup(patcher.stop)
        self.now = datetime(2026, 9, 24, 8, 0, tzinfo=BEIJING)
        self.clock.now.return_value = self.now
        patcher = patch("practice_tool.client.PracticeClient._post", side_effect=AssertionError("Offline test"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def save(self, **changes):
        data = {"name": "Schedule test", "username": "test", "password": "password", "enabled": True,
                "schedule_mode": "daily", "time_mode": "random", "check_time": "09:00", "check_time_end": "10:00",
                "check_location": {"latitude": 22.817, "longitude": 108.366, "radius": 100}}
        data.update(changes)
        return self.accounts.save(data)

    def target(self):
        return self.accounts.overview()["accounts"][0]["today_check_time"]

    def tick(self):
        def finish(run_id, account, source):
            self.accounts._finish(run_id, account["id"], "signed", "Offline success", None)
        with patch.object(self.accounts, "_execute", side_effect=finish), patch.object(
                self.accounts._executor, "submit", side_effect=lambda fn, *args: fn(*args)):
            self.accounts._schedule_due()

    def test_daily_random_target_is_persistent_and_not_resampled_by_refresh(self):
        self.save()
        with patch("practice_tool.accounts.secrets.randbelow", return_value=17) as random:
            first = self.target()
            self.assertEqual(first, "09:17")
            self.assertEqual(self.target(), first)
            self.tick()  # Before target; planning also must not resample.
            random.assert_called_once()
        self.accounts.close()
        self.accounts = Accounts(self.directory)
        self.addCleanup(self.accounts.close)
        with patch("practice_tool.accounts.secrets.randbelow", side_effect=AssertionError("Resampled after restart")):
            self.assertEqual(self.target(), first)

    def test_next_day_draw_avoids_previous_plan(self):
        self.save()
        with patch("practice_tool.accounts.secrets.randbelow", return_value=0):
            self.assertEqual(self.target(), "09:00")
            self.clock.now.return_value = self.now + timedelta(days=1)
            self.assertEqual(self.target(), "09:01")
            self.clock.now.return_value = self.now + timedelta(days=2)
            self.assertEqual(self.target(), "09:00")

    def test_random_bounds_include_last_minute_and_account_for_midwindow_creation(self):
        self.save()
        self.clock.now.return_value = self.now.replace(hour=9, minute=45)
        with patch("practice_tool.accounts.secrets.randbelow", side_effect=lambda size: size - 1):
            self.assertEqual(self.target(), "10:00")
        self.save(id=1, check_time="09:30", check_time_end="10:30")
        with patch("practice_tool.accounts.secrets.randbelow", return_value=0):
            self.assertEqual(self.target(), "09:45")

    def test_random_stays_within_window_for_many_days(self):
        self.save()
        previous = None
        for offset in range(40):
            self.clock.now.return_value = self.now + timedelta(days=offset)
            target = self.target()
            self.assertGreaterEqual(minute_of_day(target), 9 * 60)
            self.assertLessEqual(minute_of_day(target), 10 * 60)
            self.assertNotEqual(target, previous)
            previous = target

    def test_scheduler_waits_until_target_and_attempts_only_once(self):
        self.save()
        with patch("practice_tool.accounts.secrets.randbelow", return_value=30):
            self.assertEqual(self.target(), "09:30")
        self.clock.now.return_value = self.now.replace(hour=9, minute=29)
        self.tick()
        self.assertEqual(self.accounts.overview()["runs"], [])
        self.clock.now.return_value = self.now.replace(hour=9, minute=30)
        self.tick()
        self.tick()
        runs = self.accounts.overview()["runs"]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["source"], "automatic")
        self.assertEqual(runs[0]["state"], "signed")

    def test_missed_random_window_is_skipped_even_with_a_saved_target(self):
        account_id = self.save()
        self.assertIsNotNone(self.target())
        self.clock.now.return_value = self.now.replace(hour=10, minute=1)
        self.tick()
        self.assertIsNone(self.accounts.launch(account_id, "automatic"))
        self.assertEqual(self.accounts.overview()["runs"], [])

    def test_first_start_after_random_window_does_not_generate_or_submit(self):
        self.save()
        self.clock.now.return_value = self.now.replace(hour=11)
        with patch("practice_tool.accounts.secrets.randbelow", side_effect=AssertionError("Late draw")):
            self.assertIsNone(self.target())
            self.tick()
        self.assertEqual(self.accounts.overview()["runs"], [])

    def test_task_delayed_past_window_rechecks_time_before_getting_coordinates(self):
        account_id = self.save()
        self.clock.now.return_value = self.now.replace(hour=9, minute=59)
        queued = []
        with patch("practice_tool.accounts.secrets.randbelow", return_value=0), patch.object(
                self.accounts._executor, "submit", side_effect=lambda *args: queued.append(args)):
            self.assertIsNotNone(self.accounts.launch(account_id, "automatic"))
        self.assertEqual(len(queued), 1)
        self.clock.now.return_value = self.now.replace(hour=10, minute=1)
        fn, *args = queued[0]
        with patch.object(PracticeClient, "signed_today", return_value=False), patch.object(
                PracticeClient, "check_in", side_effect=lambda locate, **kwargs: locate()):
            fn(*args)
        run = self.accounts.overview()["runs"][0]
        self.assertEqual(run["state"], "failed")
        self.assertIn("已超出今日随机签到时间段", run["message"])

    def test_fixed_time_retains_startup_catchup(self):
        self.save(time_mode="fixed", check_time_end="")
        self.clock.now.return_value = self.now.replace(hour=11)
        self.tick()
        self.assertEqual(len(self.accounts.overview()["runs"]), 1)

    def test_holiday_only_skips_ordinary_days_then_executes_on_holiday(self):
        self.save(schedule_mode="holidays", time_mode="fixed", check_time="00:00", check_time_end="")
        self.tick()
        self.assertEqual(self.accounts.overview()["runs"], [])
        self.clock.now.return_value = datetime(2026, 10, 1, 9, 0, tzinfo=BEIJING)
        self.tick()
        self.assertEqual(len(self.accounts.overview()["runs"]), 1)

    def test_unknown_calendar_is_visible_and_does_not_consume_daily_attempt(self):
        self.save(schedule_mode="holidays")
        self.clock.now.return_value = datetime(2028, 1, 1, 9, 0, tzinfo=BEIJING)
        with self.assertLogs("practice_tool.accounts", level="WARNING"):
            self.tick()
        overview = self.accounts.overview()
        self.assertIn("2028", overview["accounts"][0]["schedule_error"])
        self.assertEqual(overview["runs"], [])

    def test_invalid_modes_and_cross_midnight_or_empty_windows_are_rejected(self):
        for change in ({"schedule_mode": "unknown"}, {"schedule_mode": []}, {"time_mode": "unknown"},
                       {"check_time_end": ""}, {"check_time_end": "09:00"},
                       {"check_time": "23:00", "check_time_end": "01:00"}, {"check_time_end": "25:00"}):
            with self.subTest(change=change), self.assertRaises(DashboardError):
                self.save(**change)

    def test_old_request_format_remains_supported(self):
        self.save(schedule_mode="holidays", time_mode="fixed")
        self.accounts.save({"id": 1, "name": "Renamed", "username": "test", "password": "", "enabled": True,
                            "check_time": "09:00", "weekdays_only": False})
        self.assertEqual(self.accounts.overview()["accounts"][0]["schedule_mode"], "daily")

    def test_old_database_migrates_daily_and_weekday_choices(self):
        directory = self.directory / "legacy"
        directory.mkdir()
        with sqlite3.connect(directory / "practice.sqlite3") as db:
            db.execute("""CREATE TABLE accounts (id INTEGER PRIMARY KEY, name TEXT, username TEXT UNIQUE,
                       password BLOB, session BLOB, enabled INTEGER, check_time TEXT, weekdays_only INTEGER)""")
            for index, weekdays in enumerate((0, 1), start=1):
                db.execute("INSERT INTO accounts VALUES (?, ?, ?, ?, NULL, 0, '09:00', ?)",
                           (index, "Legacy", f"legacy{index}", b"legacy", weekdays))
        migrated = Accounts(directory)
        self.addCleanup(migrated.close)
        overview = migrated.overview()
        self.assertEqual([item["schedule_mode"] for item in overview["accounts"]], ["daily", "weekdays"])
        self.assertTrue(all(item["time_mode"] == "fixed" for item in overview["accounts"]))
        # Reopening must not replace a newly selected advanced mode.
        with migrated._db() as db:
            db.execute("UPDATE accounts SET schedule_mode = 'holidays' WHERE id = 1")
        reopened = Accounts(directory)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.overview()["accounts"][0]["schedule_mode"], "holidays")

    def test_changed_window_replaces_plan_but_unrelated_edits_do_not(self):
        self.save()
        with patch("practice_tool.accounts.secrets.randbelow", return_value=10):
            self.assertEqual(self.target(), "09:10")
        self.save(id=1, name="Renamed")
        self.assertEqual(self.target(), "09:10")
        self.save(id=1, check_time="11:00", check_time_end="12:00")
        with patch("practice_tool.accounts.secrets.randbelow", return_value=10):
            self.assertEqual(self.target(), "11:10")


if __name__ == "__main__":
    unittest.main()
