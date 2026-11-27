"""Offline regression checks for account check-in coordinate selection."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from practice_tool.accounts import Accounts
from practice_tool.client import PracticeClient, SessionExpired
from practice_tool.location import LocationError, gcj02


class AccountLocationTests(unittest.TestCase):
    custom = {"latitude": 22.817, "longitude": 108.366, "radius": 1}
    device = (31.2304, 121.4737)

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for name in ("seal", "unseal"):
            patcher = patch(f"practice_tool.accounts.{name}", side_effect=lambda data: data)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.accounts = Accounts(Path(directory.name))
        self.addCleanup(self.accounts.close)
        self.requests = []
        self.signed_today = False
        self.expire_settings = False
        self.platform_position = gcj02(self.custom["latitude"], self.custom["longitude"])
        patcher = patch.object(PracticeClient, "_post", autospec=True, side_effect=self._post)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("practice_tool.accounts.device_position", side_effect=LocationError("No device fix"))
        self.locate_device = patcher.start()
        self.addCleanup(patcher.stop)

    def _post(self, client, path, payload, action):
        self.requests.append((path, payload))
        if path == "Login":
            return {"token_type": "Bearer", "access_token": "test-session"}
        if path == "Info":
            return {"SignToday": self.signed_today}
        if path == "Settings":
            if self.expire_settings:
                self.expire_settings = False
                raise SessionExpired("Session expired")
            return {"CompanyLat": self.platform_position[0], "CompanyLng": self.platform_position[1],
                    "SignRange": 1, "Itinerancy": 0}
        if path == "Sign":
            return None
        raise AssertionError(f"Unexpected platform request: {path}")

    def _save(self, location, account_id=None):
        return self.accounts.save({
            "id": account_id, "name": "Test account", "username": "test-user", "password": "test-password",
            "enabled": True, "check_time": "00:00", "weekdays_only": False, "check_location": location,
        })

    def _run(self, account_id, source="manual"):
        # Run the queued operation inline so assertions see its final receipt.
        with patch.object(self.accounts._executor, "submit", side_effect=lambda fn, *args: fn(*args)):
            if source == "automatic":
                self.accounts._schedule_due()
            else:
                self.accounts.launch(account_id, source)
        return self.accounts.overview()["runs"][0]

    def _assert_sign(self, position):
        latitude, longitude = gcj02(*position)
        self.assertEqual([payload for path, payload in self.requests if path == "Sign"],
                         [{"Lat": latitude, "Lng": longitude}])

    def test_manual_check_in_submits_saved_custom_position_without_device_fix(self):
        account_id = self._save(self.custom)
        self.assertEqual(self.accounts.overview()["accounts"][0]["check_location"], self.custom)
        run = self._run(account_id)
        self.assertEqual(run["state"], "signed", run["message"])
        self._assert_sign((self.custom["latitude"], self.custom["longitude"]))
        self.locate_device.assert_not_called()

    def test_scheduled_check_in_submits_custom_position_without_device_fix(self):
        run = self._run(self._save(self.custom), "automatic")
        self.assertEqual(run["source"], "automatic")
        self.assertEqual(run["state"], "signed", run["message"])
        self._assert_sign((self.custom["latitude"], self.custom["longitude"]))
        self.locate_device.assert_not_called()

    def test_current_position_mode_uses_device_fix(self):
        self.locate_device.side_effect = None
        self.locate_device.return_value = self.device
        self.platform_position = gcj02(*self.device)
        run = self._run(self._save(None))
        self.assertEqual(run["state"], "signed", run["message"])
        self._assert_sign(self.device)
        self.locate_device.assert_called_once_with()

    def test_switching_from_custom_to_current_clears_saved_position(self):
        account_id = self._save(self.custom)
        self._save(None, account_id)
        self.assertIsNone(self.accounts.overview()["accounts"][0]["check_location"])
        self.locate_device.side_effect = None
        self.locate_device.return_value = self.device
        self.platform_position = gcj02(*self.device)
        run = self._run(account_id)
        self.assertEqual(run["state"], "signed", run["message"])
        self._assert_sign(self.device)
        self.locate_device.assert_called_once_with()

    def test_session_renewal_preserves_custom_position(self):
        account_id = self._save(self.custom)
        with self.accounts._db() as db:
            db.execute("UPDATE accounts SET session = ? WHERE id = ?",
                       (json.dumps({"authorization": "Bearer cached-session"}).encode(), account_id))
        self.expire_settings = True
        run = self._run(account_id)
        self.assertEqual(run["state"], "signed", run["message"])
        self.assertEqual(sum(path == "Settings" for path, _ in self.requests), 2)
        self.assertEqual(sum(path == "Login" for path, _ in self.requests), 1)
        self._assert_sign((self.custom["latitude"], self.custom["longitude"]))
        self.locate_device.assert_not_called()

    def test_current_mode_reports_device_failure_without_submitting(self):
        run = self._run(self._save(None))
        self.assertEqual(run["state"], "failed")
        self.assertEqual(run["message"], "No device fix")
        self.assertFalse(any(path == "Sign" for path, _ in self.requests))

    def test_custom_position_still_obeys_platform_sign_range(self):
        self.platform_position = gcj02(*self.device)
        run = self._run(self._save(self.custom))
        self.assertEqual(run["state"], "failed")
        self.assertIn("超出平台设置", run["message"])
        self.assertFalse(any(path == "Sign" for path, _ in self.requests))
        self.locate_device.assert_not_called()

    def test_status_query_does_not_locate_or_submit(self):
        run = self._run(self._save(self.custom), "status")
        self.assertEqual(run["state"], "unsigned")
        self.assertFalse(any(path in ("Settings", "Sign") for path, _ in self.requests))
        self.locate_device.assert_not_called()

    def test_already_signed_does_not_locate_or_submit(self):
        self.signed_today = True
        run = self._run(self._save(self.custom))
        self.assertEqual(run["state"], "already_signed")
        self.assertFalse(any(path in ("Settings", "Sign") for path, _ in self.requests))
        self.locate_device.assert_not_called()


if __name__ == "__main__":
    unittest.main()
